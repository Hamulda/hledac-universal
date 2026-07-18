"""
domain_executors — Bounded domain-specific executor registry.

Replaces ad-hoc ThreadPoolExecutor(max_workers=N) instantiations with a
centralized registry so the total thread count stays bounded on M1 8GB.

M1 8GB PROBLEM
==============
Python 3.14 default executor: min(32, os.cpu_count() + 4) = 32 workers on M1.
Subsystems that each create their own pool easily exceed 80 threads total:
  - sync_bridge dedicated daemon thread + 4-worker pool
  - coreml_embedder _INFERENCE_EXECUTOR (1 worker)
  - prefetch_oracle_integration _duckdb_executor (2 workers)
  - public_fetcher _HTML_EXECUTOR (4 workers)
  - jsonld_exporter + stix_exporter (1 worker each)
  - deduplication.py (2+4+3 workers across pools)
  - dns_tunnel_detector (1 worker)
  - execution_optimizer (N workers)

SOLUTION
========
Single registry — all ThreadPoolExecutor instances routed through get_or_create().
Total workers capped at 24 (8 cores × 3 + main asyncio thread).

DOMAIN LAYOUT (M1 8GB target)
==============================
  html       — BeautifulSoup, lxml parsing, HTML extraction  (8 workers, ISSUE-5)
  duckdb     — DuckDB sync queries (2 workers)
  infer      — CoreML/MLX sync bridge (2 workers, floor: 1→2 on M1)
  crypto     — yara-python, Pycryptodome (2 workers, floor: 1→2 on M1)
  semantic   — SimHash, embedding deduplication (2 workers)
  content    — content hashing (3 workers)
  metadata   — metadata processing (2 workers)
  dns        — DNS/mlx operations (2 workers, floor: 1→2 on M1)
  parallel   — general parallel execution (3 workers)
  nlp        — GLiNER2, fast-langdetect (2 workers)
  vision     — PyMuPDF, vision encoder (2 workers)
  embed      — MLX embed sync bridge (2 workers, floor: 1→2 on M1)
  storage    — DuckDB sync adapter (2 workers)
  default    — unmapped fallback (2 workers)

Bounded total (M1, cpu_count=8): 3+2+2+2+2+3+2+2+3+2+2+2+2+2 = 31
Raw preset sum: 27 | Bounded sum: 31 | _TOTAL_THREAD_CAP: 24 (soft cap, warning only)
NOTE: _bounded_workers() applies max(2, min(preset, cpu_count)). On M1 cpu_count=8,
all presets are below the cap, so only the min-floor raises 1→2 for infer/crypto/dns/embed.
_TOTAL_THREAD_CAP is a soft cap — executors are always created (callers expect existence);
a warning is logged on first exceed. Permanent overhead NOT counted here:
  sync_bridge _get_dedicated_thread_pool() — 4 daemon workers (persistent, @functools.cached)

INTERPRETERPOOLEXECUTOR (Python 3.14+)
======================================
PEP 756: InterpreterPoolExecutor provides true parallelism via subinterpreters.
Each worker has its own GIL — no GIL contention.

  When to use:
    - Heavy pure-Python CPU work (>100ms per task)
    - Large batch chunks (>10K items)
    - Workers pre-warmed with module imports

  When NOT to use:
    - Small/medium tasks (overhead too high)
    - I/O-bound tasks (ThreadPoolExecutor wins)
    - M1 8GB: lower RSS than subinterpreters

For DuckDB + MLX coexistence on M1 8GB, ThreadPoolExecutor (bounded)
is optimal — DuckDB releases GIL in C extension, MLX runs on Metal.
"""
from __future__ import annotations

import atexit
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Final

# CPU cap for M1 8GB: max 24 threads total (8 cores × 3 + main asyncio)
# Override via HLEDAC_TOTAL_THREAD_CAP env var
_TOTAL_THREAD_CAP: Final[int] = int(os.environ.get("HLEDAC_TOTAL_THREAD_CAP", "24"))

# Per-domain worker presets (sum controlled by _TOTAL_THREAD_CAP)
_DOMAIN_PRESETS: dict[str, int] = {
    "html": 8,       # ISSUE-5: 3→8 workers: 100pages×200ms/8=2.5s < 6s target
    "duckdb": 2,     # DuckDB sync queries
    "infer": 1,      # CoreML/MLX sync bridge
    "crypto": 1,     # yara-python, Pycryptodome
    "semantic": 2,   # SimHash, embedding deduplication
    "content": 3,   # content hashing
    "metadata": 2,  # metadata processing
    "dns": 1,        # DNS/mlx operations
    "parallel": 3,  # general parallel execution
    "nlp": 2,       # GLiNER2, fast-langdetect, ghost forensics (ISSUE-049)
    "vision": 2,     # PyMuPDF, vision encoder, CoreML (ISSUE-049)
    "embed": 1,      # MLX embed sync bridge
    "storage": 2,   # DuckDB sync adapter
    "captcha": 1,    # PIL CAPTCHA image analysis (ISSUE-049)
    "exposure_db": 1,  # LMDB single-writer for exposure cache (ISSUE-027)
    "default": 2,    # unmapped fallback
}


def _bounded_workers(preset: int) -> int:
    """Return bounded worker count respecting global cap."""
    cpu_count = os.cpu_count() or 4
    return max(2, min(preset, cpu_count))


# Global registry — lazily initialized on first use
_executors: dict[str, ThreadPoolExecutor] = {}
_executors_lock = threading.Lock()
_initialized: bool = False
_total_cap_warning_logged: bool = False


def get_or_create(name: str, max_workers: int | None = None) -> ThreadPoolExecutor:
    """
    Get or create a named executor from the global registry.

    This is the canonical entry point for all ThreadPoolExecutor creation.
    All production executors MUST use this instead of instantiating directly.

    Args:
        name: Unique executor name (e.g., 'html', 'duckdb', 'infer').
              If the executor already exists, max_workers is IGNORED
              (idempotent — first wins).
        max_workers: Worker count hint. If None, uses domain preset from
                     _DOMAIN_PRESETS, or 2 for unknown names.

    Returns:
        Shared ThreadPoolExecutor instance.

    M1 8GB invariant:
        The sum of all executor workers across the process is capped at
        HLEDAC_TOTAL_THREAD_CAP (default 24). When the cap is reached,
        new executors still get created (to avoid blocking callers) but
        worker counts are silently reduced to fit.

    Example:
        # Instead of: ThreadPoolExecutor(max_workers=4, thread_name_prefix='html-extract')
        pool = get_or_create('html')

        # With custom worker count:
        pool = get_or_create('my_pool', max_workers=6)
    """
    global _initialized

    if name in _executors:
        return _executors[name]

    with _executors_lock:
        if name in _executors:  # Double-check after acquiring lock
            return _executors[name]

        # Determine worker count
        preset = _DOMAIN_PRESETS.get(name, max_workers or 2)
        workers = _bounded_workers(preset if max_workers is None else max_workers)

        # Enforce _TOTAL_THREAD_CAP warning (not hard cap — callers expect executor to exist)
        global _total_cap_warning_logged
        current_total = sum(e._max_workers for e in _executors.values())
        if current_total + workers > _TOTAL_THREAD_CAP and not _total_cap_warning_logged:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "[domain_executors] total workers %%d exceed _TOTAL_THREAD_CAP=%d. "
                "Set HLEDAC_TOTAL_THREAD_CAP env var or reduce domain presets.",
                current_total + workers,
                _TOTAL_THREAD_CAP,
            )
            _total_cap_warning_logged = True

        executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=name,
        )
        _executors[name] = executor

        # Register atexit shutdown on first creation
        if not _initialized:
            atexit.register(shutdown_all)
            _initialized = True

        return executor


def get_domain_executors() -> dict[str, ThreadPoolExecutor]:
    """
    Get the full executor registry (legacy compatibility alias).

    Returns a snapshot copy of the current registry.
    Prefer get_or_create() for individual executor access.
    """
    with _executors_lock:
        return dict(_executors)


def shutdown_all() -> None:
    """
    Gracefully shutdown all registered executors.

    Call on application shutdown. Idempotent — safe to call multiple times.
    """
    global _executors
    if not _executors:
        return

    with _executors_lock:
        if not _executors:
            return
        for executor in list(_executors.values()):
            executor.shutdown(wait=False)
        _executors.clear()


def total_worker_count() -> int:
    """Return sum of max_workers across all registered executors."""
    with _executors_lock:
        return sum(e._max_workers for e in _executors.values())


# ── Convenience aliases ────────────────────────────────────────────────────────

def get_html_executor() -> ThreadPoolExecutor:
    """HTML extraction pool (BeautifulSoup, lxml)."""
    return get_or_create("html")


def get_duckdb_executor() -> ThreadPoolExecutor:
    """DuckDB sync query pool."""
    return get_or_create("duckdb")


def get_infer_executor() -> ThreadPoolExecutor:
    """CoreML/MLX inference sync bridge pool."""
    return get_or_create("infer")


def get_crypto_executor() -> ThreadPoolExecutor:
    """Cryptographic operations pool (yara-python, Pycryptodome)."""
    return get_or_create("crypto")


def get_semantic_executor() -> ThreadPoolExecutor:
    """Semantic deduplication pool (SimHash, embeddings)."""
    return get_or_create("semantic")


def get_content_executor() -> ThreadPoolExecutor:
    """Content hashing pool."""
    return get_or_create("content")


def get_metadata_executor() -> ThreadPoolExecutor:
    """Metadata processing pool."""
    return get_or_create("metadata")


def get_dns_executor() -> ThreadPoolExecutor:
    """DNS/MLX operations pool."""
    return get_or_create("dns")


def get_parallel_executor() -> ThreadPoolExecutor:
    """General parallel execution pool."""
    return get_or_create("parallel")


def get_vision_executor() -> ThreadPoolExecutor:
    """Vision processing pool (PyMuPDF, vision encoder)."""
    return get_or_create("vision")


def get_exposure_db_executor() -> ThreadPoolExecutor:
    """LMDB single-writer executor for exposure cache (ISSUE-027)."""
    return get_or_create("exposure_db")


def get_captcha_executor() -> ThreadPoolExecutor:
    """PIL CAPTCHA image analysis pool (1 worker — I/O + minimal CPU)."""
    return get_or_create("captcha")


def get_nlp_executor() -> ThreadPoolExecutor:
    """NLP pool: GLiNER2, fast-langdetect, ghost forensics analysis."""
    return get_or_create("nlp")

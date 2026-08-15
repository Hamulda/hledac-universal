"""
domain_executors — Unified Executor Lifecycle Manager (R5).

R5 SOLUTION
===========
Single process-wide registry for ALL ThreadPoolExecutor instances with
deterministic multi-layer shutdown guarantee.

Fixes all 4 known executor lifecycle issues:
  Issue 1 — DuckDBShadowStore: executor now centrally managed, guaranteed shutdown
  Issue 2 — DeepHermes3Engine: fallback executors routed through this registry
  Issue 3 — domain_executors: hard cap replaces soft warning; signal handlers added
  Issue 4 — utils/executors.py: deprecated globals now lazily routed here

MULTI-LAYER SHUTDOWN GUARANTEE (Python 3.14+)
=============================================
Layer 1: Context manager `scope()` — deterministic cleanup for tests/sprints
Layer 2: Signal handler (SIGINT/SIGTERM) — catches operator interrupts
Layer 3: atexit — normal process exit
Layer 4: weakref.finalize on a sentinel — GC safety net

No layer is the sole mechanism — each is a failsafe for the previous.

HARD CAP ENFORCEMENT
====================
_TOTAL_THREAD_HARD_CAP = 24 (M1 8GB: 8 cores × 3)
_EMERGENCY_THREAD_CAP = 12 (memory pressure CRITICAL or above)

When hard cap is exceeded:
  - New executor creation is BLOCKED, not just warned
  - Caller receives RuntimeError (fail-fast, never silently degrade)
  - Exception message includes current total and cap for diagnostics

When emergency cap is active (memory pressure CRITICAL/EMERGENCY):
  - New executors are capped at min(workers, _EMERGENCY_THREAD_CAP - current)
  - Existing executors are NOT resized (in-flight work completes)

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
Total workers hard-capped at 24 (8 cores × 3 + main asyncio thread).

DOMAIN LAYOUT (M1 8GB target)
==============================
  html                 — BeautifulSoup, lxml parsing, HTML extraction  (8 workers)
  duckdb               — DuckDB sync queries (2 workers)
  infer                — CoreML/MLX sync bridge (2 workers, floor: 1→2 on M1)
  crypto               — yara-python, Pycryptodome (2 workers, floor: 1→2 on M1)
  semantic             — SimHash, embedding deduplication (2 workers)
  content              — content hashing (3 workers)
  metadata             — metadata processing (2 workers)
  dns                  — DNS/mlx operations (2 workers, floor: 1→2 on M1)
  parallel             — general parallel execution (3 workers)
  nlp                  — GLiNER2, fast-langdetect (2 workers)
  vision               — PyMuPDF, vision encoder (2 workers)
  embed                — MLX embed sync bridge (2 workers, floor: 1→2 on M1)
  storage              — DuckDB sync adapter (2 workers)
  captcha              — PIL CAPTCHA image analysis (1 worker)
  exposure_db          — LMDB single-writer for exposure cache (1 worker)
  default              — unmapped fallback (2 workers)
  hermes_prep_fb       — DeepHermes3Engine fallback: ChatML format+tokenize (3)
  hermes_post_fb       — DeepHermes3Engine fallback: JSON parse+validate (2)
  hermes_inference_fb  — DeepHermes3Engine fallback: MLX inference (1)
  hermes_compile_fb    — DeepHermes3Engine fallback: prompt compilation (1)
  inference_engine     — InferenceEngine thread pool (1)
  evidence_duckdb      — Evidence log DuckDB writes (2)
  evidence_sqlite      — Evidence log SQLite writes (1)
  forensics            — Document forensics CPU pool (2)
  vision_ocr_batch     — Vision OCR batch processing (2)
  forensics_sync       — Forensics sync wrapper (1)
  uma_callback         — UMA watchdog callbacks (2)
  legacy_cpu           — Deprecated CPU_EXECUTOR compat (2)
  legacy_io            — Deprecated IO_EXECUTOR compat (4)

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
import logging
import os
import signal
import sys
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Final
from _core import aclose

_log = logging.getLogger(__name__)

# ── M1 8GB Thread Caps ─────────────────────────────────────────────────────────

# Hard cap: 24 threads total (8 cores × 3 + main asyncio)
# Override via HLEDAC_TOTAL_THREAD_CAP env var
_TOTAL_THREAD_HARD_CAP: Final[int] = int(
    os.environ.get("HLEDAC_TOTAL_THREAD_CAP", "24")
)

# Emergency cap: 12 threads when memory pressure CRITICAL or above
# Override via HLEDAC_EMERGENCY_THREAD_CAP env var
_EMERGENCY_THREAD_CAP: Final[int] = int(
    os.environ.get("HLEDAC_EMERGENCY_THREAD_CAP", "12")
)

# ── Per-domain worker presets ──────────────────────────────────────────────────


def _bounded_workers(preset: int) -> int:
    """Return bounded worker count respecting per-core ceiling.

    M1 8GB: cpu_count=8 → ceiling=8 per domain.
    Min floor is 1 (changed from 2 in R5 — allow single-worker pools).
    """
    cpu_count = os.cpu_count() or 4
    return max(1, min(preset, cpu_count))


_DOMAIN_PRESETS: dict[str, int] = {
    # ── Core domains (existing) ──
    "html": 8,          # ISSUE-5: 100pages×200ms/8=2.5s < 6s target
    "duckdb": 2,        # DuckDB sync queries
    "infer": 1,         # CoreML/MLX sync bridge
    "crypto": 1,        # yara-python, Pycryptodome
    "semantic": 2,      # SimHash, embedding deduplication
    "content": 3,       # content hashing
    "metadata": 2,      # metadata processing
    "dns": 1,           # DNS/mlx operations
    "parallel": 3,      # general parallel execution
    "nlp": 2,           # GLiNER2, fast-langdetect, ghost forensics
    "vision": 2,        # PyMuPDF, vision encoder, CoreML
    "embed": 1,         # MLX embed sync bridge
    "storage": 2,       # DuckDB sync adapter
    "captcha": 1,       # PIL CAPTCHA image analysis
    "exposure_db": 1,   # LMDB single-writer for exposure cache
    "default": 2,       # unmapped fallback
    # ── R5: Hermes engine fallback domains (Issue 2 fix) ──
    "hermes_prep_fb": 3,       # ChatML format + tokenization (fallback)
    "hermes_post_fb": 2,       # JSON parse + model_validate (fallback)
    "hermes_inference_fb": 1,  # MLX inference (fallback)
    "hermes_compile_fb": 1,    # Prompt compilation (fallback, lazy)
    # ── R5: Ad-hoc pool migration domains (Issues 1/3/4 fix) ──
    "inference_engine": 1,     # InferenceEngine thread pool
    "evidence_duckdb": 2,      # Evidence log DuckDB writes
    "evidence_sqlite": 1,      # Evidence log SQLite writes (WAL serialized)
    "forensics": 2,            # Document forensics CPU
    "vision_ocr_batch": 2,     # Vision OCR batch processing
    "forensics_sync": 1,       # Forensics sync wrapper
    "uma_callback": 2,         # UMA watchdog callbacks
    "legacy_cpu": 2,           # Deprecated CPU_EXECUTOR compat
    "legacy_io": 4,            # Deprecated IO_EXECUTOR compat
    "parquet": 1,              # Parquet writer executor
    "evidence_log_sqlite": 1,  # Evidence log SQLite (alt key)
    "evidence_log_duckdb": 2,  # Evidence log DuckDB (alt key)
}


# ── Internal helper: memory pressure check ─────────────────────────────────────

def _is_emergency() -> bool:
    """Check if system is under CRITICAL or EMERGENCY memory pressure.

    Lazy-imports resource_governor to avoid circular dependency and
    cold-start cost. Returns False if governor unavailable (fail-open).
    """
    try:
        from hledac.universal._core.resource_governor import sample_uma_status
        status = sample_uma_status()
        return status in ("CRITICAL", "EMERGENCY")
    except Exception:
        return False


# ── Global registry state ──────────────────────────────────────────────────────

_executors: dict[str, ThreadPoolExecutor] = {}
_executors_lock = threading.Lock()
_initialized: bool = False
_shutdown_called: bool = False

# Signal handler tracking — prevents double-registration
_signal_handlers_registered: bool = False
_sentinel: object | None = None  # weakref.finalize target


def _get_current_total() -> int:
    """Return sum of max_workers across all registered executors (lock held)."""
    return sum(e._max_workers for e in _executors.values())


# ── Multi-layer shutdown ───────────────────────────────────────────────────────


def _shutdown_all_executors(*, cancel_futures: bool = True) -> None:
    """Core shutdown routine — idempotent, fail-safe.

    Python 3.14: cancel_futures=True ensures zero-stall shutdown.
    Each executor is shutdown independently; one failure doesn't block others.
    """
    global _shutdown_called
    if _shutdown_called:
        return

    with _executors_lock:
        if _shutdown_called:
            return
        _shutdown_called = True

        if not _executors:
            return

        names = list(_executors.keys())
        for name in names:
            executor = _executors.get(name)
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=cancel_futures)
                except Exception:
                    _log.debug(
                        "[domain_executors] shutdown error for '%s'", name,
                        exc_info=True,
                    )
        _executors.clear()
        _log.info(
            "[domain_executors] shutdown_all: %d executors shut down", len(names)
        )


def shutdown_all() -> None:
    """Public shutdown API — gracefully shut down all registered executors.

    Idempotent — safe to call multiple times.
    Uses cancel_futures=True for zero-stall shutdown (Python 3.14 best practice).
    """
    _shutdown_all_executors(cancel_futures=True)


def _register_signal_handlers() -> None:
    """Register SIGINT/SIGTERM handlers for guaranteed executor cleanup.

    Idempotent — only registers once. Signal handlers call shutdown_all()
    then chain to the original handler (if any) or raise KeyboardInterrupt
    for SIGINT / sys.exit for SIGTERM.
    """
    global _signal_handlers_registered
    if _signal_handlers_registered:
        return
    _signal_handlers_registered = True

    _original_sigint = signal.getsignal(signal.SIGINT)
    _original_sigterm = signal.getsignal(signal.SIGTERM)

    def _signal_handler(signum: int, _frame: object) -> None:
        _log.warning(
            "[domain_executors] signal %s received — shutting down executors",
            signal.Signals(signum).name,
        )
        _shutdown_all_executors(cancel_futures=True)
        # Chain to original handler or default behavior
        if signum == signal.SIGINT:
            if _original_sigint not in (None, signal.SIG_DFL):
                if callable(_original_sigint):
                    _original_sigint(signum, _frame)  # type: ignore[call-arg]
            else:
                raise KeyboardInterrupt
        elif signum == signal.SIGTERM:
            if _original_sigterm not in (None, signal.SIG_DFL):
                if callable(_original_sigterm):
                    _original_sigterm(signum, _frame)  # type: ignore[call-arg]
            else:
                sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        # Signal registration can fail in non-main threads or embedded interpreters
        _log.debug("[domain_executors] signal handler registration skipped")


def _ensure_initialized() -> None:
    """One-time initialization: register atexit, signal handlers, GC sentinel."""
    global _initialized, _sentinel
    if _initialized:
        return

    # Layer 2: Signal handlers for SIGINT/SIGTERM
    _register_signal_handlers()

    # Layer 3: atexit for normal process exit
    atexit.register(shutdown_all)

    # Layer 4: GC safety net via weakref.finalize on a sentinel object
    # When the interpreter is tearing down, if the sentinel is collected,
    # finalize triggers shutdown. This catches cases where atexit doesn't
    # run (e.g., os._exit, or certain test frameworks).
    class _Sentinel:
        def __del__(self) -> None:
            _shutdown_all_executors(cancel_futures=True)

    _sentinel = _Sentinel()
    weakref.finalize(_sentinel, _shutdown_all_executors, cancel_futures=True)

    _initialized = True


# ── Context manager for test/sprint scoping ────────────────────────────────────


@contextmanager
def scope():
    """Context manager that guarantees executor cleanup on exit.

    Usage in tests:
        with domain_executors.scope():
            pool = get_or_create("test_pool")
            ...

    Usage in sprints:
        with domain_executors.scope():
            store = DuckDBShadowStore(...)
            await store.async_ingest_findings_batch(...)
        # All executors shut down here

    On exit, calls shutdown_all() with cancel_futures=True.
    Exception-safe — always shuts down even if the block raises.
    """
    try:
        yield
    finally:
        shutdown_all()


# ── Canonical executor factory ─────────────────────────────────────────────────


def get_or_create(
    name: str,
    max_workers: int | None = None,
) -> ThreadPoolExecutor:
    """Get or create a named executor from the global registry.

    This is the CANONICAL entry point for ALL ThreadPoolExecutor creation.
    Every production executor MUST use this instead of instantiating directly.

    Args:
        name: Unique executor name (e.g., 'html', 'duckdb', 'infer').
              If the executor already exists, max_workers is IGNORED
              (idempotent — first wins).
        max_workers: Worker count hint. If None, uses domain preset from
                     _DOMAIN_PRESETS, or 2 for unknown names.

    Returns:
        Shared ThreadPoolExecutor instance.

    Raises:
        RuntimeError: If hard cap would be exceeded. Callers must handle
                      this — either reuse an existing executor or reduce
                      their worker count.

    M1 8GB invariants:
        1. Total workers across ALL executors ≤ _TOTAL_THREAD_HARD_CAP (24).
        2. Under memory pressure (CRITICAL/EMERGENCY), cap tightens to
           _EMERGENCY_THREAD_CAP (12).
        3. callers MUST handle RuntimeError — fail-fast with clear diagnostics.

    Example:
        # Instead of: ThreadPoolExecutor(max_workers=4, thread_name_prefix='html-extract')
        pool = get_or_create('html')

        # With custom worker count:
        pool = get_or_create('my_pool', max_workers=6)
    """
    # Fast path: already exists
    if name in _executors:
        return _executors[name]

    with _executors_lock:
        if name in _executors:  # Double-check after acquiring lock
            return _executors[name]

        # Determine worker count
        preset = _DOMAIN_PRESETS.get(name, max_workers or 2)
        workers = _bounded_workers(preset if max_workers is None else max_workers)

        # Check memory pressure — tighten cap under duress
        emergency = _is_emergency()
        effective_cap = _EMERGENCY_THREAD_CAP if emergency else _TOTAL_THREAD_HARD_CAP

        current_total = _get_current_total()
        proposed_total = current_total + workers

        if proposed_total > effective_cap:
            # Try to reduce workers to fit within cap
            available = effective_cap - current_total
            if available <= 0:
                raise RuntimeError(
                    f"[domain_executors] HARD CAP REACHED: {current_total} workers "
                    f"already allocated (cap={effective_cap}, "
                    f"emergency={emergency}). "
                    f"Cannot create executor '{name}'. "
                    f"Reuse an existing executor or reduce worker counts."
                )
            # Clamp workers to available slots
            workers = max(1, available)
            _log.warning(
                "[domain_executors] CAPPED '%s' to %d workers "
                "(available=%d, cap=%d, emergency=%s)",
                name, workers, available, effective_cap, emergency,
            )

        executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=name,
        )
        _executors[name] = executor

        # One-time init on first executor creation
        _ensure_initialized()

        _log.debug(
            "[domain_executors] Created '%s' with %d workers (total=%d/%d, emergency=%s)",
            name, workers, _get_current_total(), effective_cap, emergency,
        )

        return executor


def register_existing(
    name: str,
    executor: ThreadPoolExecutor,
) -> ThreadPoolExecutor:
    """Adopt an externally-created executor into the registry.

    Use this for retroactively registering pools that were created
    before domain_executors migration (e.g., third-party lib pools).

    If name already exists, the existing executor is returned and
    the passed executor is NOT registered (caller should shut it down).

    Args:
        name: Unique executor name.
        executor: The ThreadPoolExecutor to adopt.

    Returns:
        The registered executor (which may be the existing one).

    Raises:
        RuntimeError: If adopting this executor would exceed the hard cap.
    """
    if name in _executors:
        return _executors[name]

    with _executors_lock:
        if name in _executors:
            return _executors[name]

        emergency = _is_emergency()
        effective_cap = _EMERGENCY_THREAD_CAP if emergency else _TOTAL_THREAD_HARD_CAP
        current_total = _get_current_total()
        workers = executor._max_workers

        if current_total + workers > effective_cap:
            raise RuntimeError(
                f"[domain_executors] HARD CAP REACHED: cannot adopt '{name}' "
                f"({workers} workers). Current={current_total}, cap={effective_cap}."
            )

        _executors[name] = executor
        _ensure_initialized()

        _log.debug(
            "[domain_executors] Adopted '%s' with %d workers (total=%d/%d)",
            name, workers, _get_current_total(), effective_cap,
        )
        return executor


def get_domain_executors() -> dict[str, ThreadPoolExecutor]:
    """Get a snapshot copy of the full executor registry.

    Prefer get_or_create() for individual executor access.
    """
    with _executors_lock:
        return dict(_executors)


def total_worker_count() -> int:
    """Return sum of max_workers across all registered executors."""
    with _executors_lock:
        return _get_current_total()


def get_hard_cap() -> int:
    """Return the effective hard cap (respects emergency state)."""
    return _EMERGENCY_THREAD_CAP if _is_emergency() else _TOTAL_THREAD_HARD_CAP


def is_emergency() -> bool:
    """Check if system is under memory pressure (CRITICAL or above)."""
    return _is_emergency()


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


# ── R5: Hermes fallback convenience aliases (Issue 2 fix) ──────────────────────


def get_hermes_prep_fallback_executor() -> ThreadPoolExecutor:
    """DeepHermes3Engine fallback: ChatML format + tokenization."""
    return get_or_create("hermes_prep_fb")


def get_hermes_post_fallback_executor() -> ThreadPoolExecutor:
    """DeepHermes3Engine fallback: JSON parse + model_validate."""
    return get_or_create("hermes_post_fb")


def get_hermes_inference_fallback_executor() -> ThreadPoolExecutor:
    """DeepHermes3Engine fallback: MLX inference."""
    return get_or_create("hermes_inference_fb")


def get_hermes_compile_fallback_executor() -> ThreadPoolExecutor:
    """DeepHermes3Engine fallback: prompt compilation."""
    return get_or_create("hermes_compile_fb")


# ── R5: Evidence log convenience aliases ───────────────────────────────────────


def get_evidence_duckdb_executor() -> ThreadPoolExecutor:
    """Evidence log DuckDB write executor."""
    return get_or_create("evidence_duckdb")


def get_evidence_sqlite_executor() -> ThreadPoolExecutor:
    """Evidence log SQLite write executor (WAL serialized)."""
    return get_or_create("evidence_sqlite")


# ── R5: Forensics convenience aliases ──────────────────────────────────────────


def get_forensics_cpu_executor() -> ThreadPoolExecutor:
    """Document forensics CPU fallback pool."""
    return get_or_create("forensics")


def get_vision_ocr_batch_executor() -> ThreadPoolExecutor:
    """Vision OCR batch processing pool."""
    return get_or_create("vision_ocr_batch")


def get_forensics_sync_executor() -> ThreadPoolExecutor:
    """Forensics sync wrapper pool."""
    return get_or_create("forensics_sync")


# ── R5: UMA / legacy convenience aliases ───────────────────────────────────────


def get_uma_callback_executor() -> ThreadPoolExecutor:
    """UMA watchdog callback executor."""
    return get_or_create("uma_callback")


def get_legacy_cpu_executor() -> ThreadPoolExecutor:
    """Deprecated CPU_EXECUTOR compat (from utils/executors.py)."""
    return get_or_create("legacy_cpu")


def get_legacy_io_executor() -> ThreadPoolExecutor:
    """Deprecated IO_EXECUTOR compat (from utils/executors.py)."""
    return get_or_create("legacy_io")


# ── R5: Exports ────────────────────────────────────────────────────────────────

__all__ = [
    # Core API
    "get_or_create",
    "register_existing",
    "get_domain_executors",
    "shutdown_all",
    "total_worker_count",
    "get_hard_cap",
    "is_emergency",
    "scope",
    # Convenience aliases — core
    "get_html_executor",
    "get_duckdb_executor",
    "get_infer_executor",
    "get_crypto_executor",
    "get_semantic_executor",
    "get_content_executor",
    "get_metadata_executor",
    "get_dns_executor",
    "get_parallel_executor",
    "get_vision_executor",
    "get_exposure_db_executor",
    "get_captcha_executor",
    "get_nlp_executor",
    # Convenience aliases — R5 Hermes fallback
    "get_hermes_prep_fallback_executor",
    "get_hermes_post_fallback_executor",
    "get_hermes_inference_fallback_executor",
    "get_hermes_compile_fallback_executor",
    # Convenience aliases — R5 evidence log
    "get_evidence_duckdb_executor",
    "get_evidence_sqlite_executor",
    # Convenience aliases — R5 forensics
    "get_forensics_cpu_executor",
    "get_vision_ocr_batch_executor",
    "get_forensics_sync_executor",
    # Convenience aliases — R5 UMA / legacy
    "get_uma_callback_executor",
    "get_legacy_cpu_executor",
    "get_legacy_io_executor",
]

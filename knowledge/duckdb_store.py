"""DuckDB Shadow Analytics -- canonical sprint facts store.

ROLE: Canonical store for sprint-level facts and derived analytics.

⚠️  "Shadow" in the class name refers to historical naming (Sprint 8AO/8AS).
    This store IS the canonical sprint facts authority for the analytics
    subsystem, not a shadow of anything.

FACTS HIERARCHY (3 tiers):

TIER 1 -- SPRINT FACTS (DuckDB, durable):
    sprint_delta       -- per-sprint metrics: query, duration, new_findings, dedup_hits, ioc_nodes
    sprint_scorecard   -- per-sprint aggregated scores: fpm, ioc_density, synthesis_confidence
    source_hit_log     -- per-sprint source attribution: source_type, hit_rate

TIER 2 -- SHADOW FINDINGS (DuckDB, durable):
    canonical_findings    -- finding-level records forwarded from EvidenceLog.append()
    shadow_runs        -- run-level metadata
    -- F272: DuckDB ioc_graph table removed; IOC storage via DuckPGQGraph (graph/quantum_pathfinder.py)

TIER 3 -- CROSS-SPRINT (DuckDB, append-only, pruneable):
    temporal_events    -- time-indexed events for temporal archaeology
"""


import asyncio
import atexit
import sys
import weakref

# Sprint T1: OpenTelemetry instrumentation (always-on, M1 EIGHTGB safe, fail-soft)
try:
    from otel import (  # type: ignore[import]
        instrumented as _otel_instrumented,
    )
except ImportError:
    from hledac.universal.otel import (  # type: ignore[import]
        instrumented as _otel_instrumented,
    )
import datetime as _dt
import logging
import os
import re
import time as _time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

# F26X: @deprecated with Python 3.11+ safe fallback (see utils/_deprecated.py)

# Sprint F262OBS: canonical source_type centralization - guard at ingest seam
try:
    from hledac.universal.utils.source_types import SourceType, canonical_source_type  # type: ignore[import]
except ImportError:
    SourceType = cast(Any, None)
    canonical_source_type = cast(Any, None)

# F3.3: BoundedTaskSet for bounded background task tracking (K11 fix)
try:
    from hledac.universal.utils.async_utils import BoundedTaskSet  # type: ignore[import]
except ImportError:
    BoundedTaskSet = None  # type: ignore[assignment,misc]

import msgspec

# Sprint F26X: orjson for fast JSON path (3-11x vs stdlib json)
try:
    import orjson as _orjson
    _HAS_ORJSON = True
except ImportError:
    _orjson = cast(Any, None)
    _HAS_ORJSON = False


# Sprint F26X: Module-level reusable encoders/decoders - avoid per-call instantiation.
# msgspec is ~5-7x faster than stdlib json for write-heavy hot paths (already canonical
# in this module per Sprint 8R). orjson is ~3-5x faster than stdlib json for the
# duckdb-varchar-bridge paths where DuckDB parameters must be `str`, not `bytes`.
if _HAS_ORJSON:
    _ORJSON_DECODER = _orjson.loads
else:
    import json as _stdjson

    def _ORJSON_DECODER(b: Any) -> Any:  # noqa: N802
        return _stdjson.loads(b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else b)


# ---------------------------------------------------------------------------
# TYPE_CHECKING: Dynamic attribute stubs for type checker satisfaction.
# These names are set via object.__setattr__() at runtime and are NOT visible
# to type checkers without explicit annotation.
# ---------------------------------------------------------------------------
if TYPE_CHECKING:
    class _DuckDBQueryExecutor:
        """Stubs for dynamic attributes set via object.__setattr__ in _DuckDBQueryExecutor."""
        _store: DuckDBShadowStore
        _stmt_insert_finding: Any
        _stmt_insert_finding_conn_id: int | None

    class _DuckDBShadowStore:
        """Stubs for dynamic attributes set via object.__setattr__ in DuckDBShadowStore."""
        _query_executor: _DuckDBQueryExecutor
        _graph_store: Any


def _json_dumps_str(value: Any) -> str:
    """Sprint F26X: Fast str-returning JSON encoder for DuckDB VARCHAR parameters.

    Default behaviour: ``orjson.dumps(...).decode("utf-8")`` - single allocation,
    explicit codec (avoids BOM detection). Fallback to stdlib json. Used at the
    DuckDB boundary (parameterized INSERT/UPDATE) where DuckDB requires `str`
    but most other call sites already feed `bytes` and stay zero-copy.
    """
    if value is None:
        return "{}"
    if _HAS_ORJSON:
        return _orjson.dumps(value).decode("utf-8")
    import json as _stdjson
    return _stdjson.dumps(value, separators=(",", ":"))


def _provenance_to_arrow_native(provenance: tuple[str, ...]) -> bytes | str | None:
    """
    Sprint Thread2b + F290-1: Zero-copy provenance → bytes for Arrow.

    Converts CanonicalFinding.provenance (tuple[str,...]) to bytes via msgspec.json.encode
    so pa.array(bytes) can ingest it zero-copy (no intermediate Python str decode).

    F290-1 FIX: Previously returned bytes then re-decoded in Arrow path (2x serialization).
    Now returns bytes directly — pa.array accepts bytes natively for string arrays.

    Returns:
        - bytes: msgspec-encoded provenance list (Arrow-compatible, zero-copy)
        - str fallback: for stdlib path (no copy saving, but compatible)
        - None: for empty provenance (NULL in SQL)

    Zero-copy principle: pa.array(bytes) reads the C buffer directly from the bytes
    object returned here — no Python str intermediate, no tolist() overhead.
    """
    if not provenance:
        return None
    try:
        import msgspec
        # F290-1 FIX: Return bytes directly. Arrow pa.array accepts bytes for utf8 string type.
        # Previously: encode → decode (wasteful round-trip)
        return msgspec.json.encode(list(provenance))
    except Exception:
        pass
    # F290-1 FIX: fallback returns tuple (original type) - convert to JSON str for Arrow compatibility
    return _msgspec_encode(provenance).decode("utf-8")


def _json_loads_flexible(raw: Any) -> Any:
    """Sprint F26X: Single-shot JSON decode that handles str | bytes | None | empty.

    Replaces the 6 hand-rolled ``orjson.loads(r[N]) if r[N] else {}`` patterns
    that previously littered this module (lines 3038-3041, 4452-4455).
    """
    if raw is None or raw == b"" or raw == "":
        return {}
    if isinstance(raw, (bytes, bytearray)):
        return _ORJSON_DECODER(raw)
    if isinstance(raw, str):
        if _HAS_ORJSON:
            return _orjson.loads(raw.encode("utf-8"))
        import json as _stdjson
        return _stdjson.loads(raw)
    return raw


# Sprint F202K: TargetProfileSummary import with inline fallback
try:
    from hledac.universal.knowledge.sprint_diff_engine import TargetProfileSummary
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class TargetProfileSummary:
        target_id: str = ""
        first_seen: float = 0.0
        last_seen: float = 0.0
        cumulative_finding_count: int = 0
        entity_summary_json: str = "{}"

# Sprint F204D: TargetMemoryUpdate import
try:
    from hledac.universal.knowledge.target_memory import (  # noqa: F401
        TargetMemory,  # hledac.universal.knowledge.target_memory.TargetMemory
        TargetMemoryUpdate,  # hledac.universal.knowledge.target_memory.TargetMemoryUpdate
    )
except ImportError:
    pass

__all__ = [
    "DuckDBShadowStore",
    "ActivationResult",
    "ReplayResult",
    "CanonicalFinding",
    "FindingQualityDecision",
    "QualityRejectionRecord",  # backward compat - real def moved to quality_assessment
    "_normalize_osint_url",  # re-exported from quality_assessment for backward compat
]

# Import QualityRejectionRecord from quality_assessment (moved in Sprint F216G refactor)
# F273F: MADV_FREE_REUSABLE + F_NOCACHE for DuckDB file-backed mmap regions
from hledac.universal.tools.file_cache import apply_nocache_to_path, madv_free_reusable_on_path  # noqa: E402
from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget, safe_gather_return_exceptions  # noqa: E402

from .dedup import DedupManager  # noqa: E402

# Also import DEDUP_HOT_CACHE_MAX since it's used in get_dedup_runtime_status
from .quality_assessment import _DEDUP_HOT_CACHE_MAX as _DEDUP_HOT_CACHE_MAX  # noqa: E402
from .quality_assessment import (  # noqa: E402
    _HIGH_CONF_IOC_RE,  # Sprint P1-2: batch quality gate
    _QUALITY_ENTROPY_THRESHOLD,
    _QUALITY_MIN_ENTROPY_LEN,
    QualityAssessmentState,
    QualityRejectionRecord,
    _compute_dedup_fingerprint,
    _compute_entropy,
    _compute_url_fingerprint,
    _normalize_for_quality,
    _normalize_osint_url,  # re-exported for backward compat with tests
)

# Sprint P1-2: Batch Rust quality gate — rayon-parallel, M1 8GB safe
# F265C: Use centralized rust backend
_QUALITY_GATE_BATCH_AVAILABLE = False
try:
    from core.rust_backend import rust as _rust_backend

    if _rust_backend.is_available and _rust_backend.quality is not None:
        _QUALITY_GATE_BATCH_AVAILABLE = True
except ImportError:
    pass

# Provide fallbacks for when Rust is not available
if not _QUALITY_GATE_BATCH_AVAILABLE:
    _rust_batch_entropy = None  # type: ignore[assignment]
    _rust_batch_dedup_fingerprints = None  # type: ignore[assignment]
    _rust_batch_normalize_quality_text = None  # type: ignore[assignment]
    _rust_batch_url_fingerprints = None  # type: ignore[assignment]
    _rust_dedup_fingerprint = None  # type: ignore[assignment]
    _rust_url_fingerprint_b2b = None  # type: ignore[assignment]
    _rust_normalize_quality_text = None  # type: ignore[assignment]

# F275-3: Rust Arrow batch builder — single-pass IPC bytes, rayon-parallel column build.
# build_arrow_batch_from_findings returns IPC bytes (zero-copy vs Python loops).
_RUST_ARROW_AVAILABLE = False
_rust_build_arrow_batch = None
try:
    from hledac_rust_extensions import (
        build_arrow_batch_from_findings,  # type: ignore[import,unresolved-import]  # noqa: E402
    )

    _rust_build_arrow_batch = build_arrow_batch_from_findings
    _RUST_ARROW_AVAILABLE = True
except Exception:  # noqa: BLE001
    pass

# Sprint PAR-1 P2 / F266-2.3: Batch Rust IOC extraction — rayon-parallel, 1000 text limit
# F266-2.3: zero-copy tier via batch_ioc_extract_unified_python (Python heap direct)
try:
    from hledac_rust_extensions import batch_ioc_extract_unified as _rust_batch_ioc_extract
    from hledac_rust_extensions import batch_ioc_extract_unified_python as _rust_batch_ioc_extract_python
    _IOC_EXTRACT_BATCH_AVAILABLE = True
    _IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE = True
except ImportError:
    _IOC_EXTRACT_BATCH_AVAILABLE = False
    _IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE = False
    _rust_batch_ioc_extract: Any = None  # type: ignore[assignment]
    _rust_batch_ioc_extract_python: Any = None  # type: ignore[assignment]


def extract_iocs_from_texts(texts: list[str]):
    """
    Extract IOCs from a list of texts using Rust's batch_ioc_extract_unified.
    Yields (ioc_value, ioc_type) tuples lazily — no intermediate flat list allocated.

    PAR-1 P2 / F266-2.3: Three-tier fallback chain for zero-copy memory:

        Tier 1 — batch_ioc_extract_unified_python (F266-2.3):
            Zero-copy path. Rust writes results directly into Python heap
            via PyList::append / PyTuple::new — no intermediate Rust
            Vec<(String,String)> that Python must copy.

        Tier 2 — batch_ioc_extract_unified (rayon Vec return):
            Original path. Rust collects results in Vec<Vec<…>> then
            PyO3 auto-converts to Python list.  Tuples are copied by
            PyO3 at the GIL boundary (PyTuple::new for each element).

        Tier 3 — pure Python ioc_qs.extract_iocs_from_text:
            Slowest; used only when Rust is unavailable.

    Args:
        texts: List of text strings to scan for IOCs.

    Yields:
        Tuples of (ioc_value, ioc_type) from all texts combined.
        IOC types: ipv4, ipv6, domain, md5, sha1, sha256, email, cve.
    """
    if not texts:
        return

    # Tier 1: zero-copy Python heap path (F266-2.3)
    # F266-2.3: yield from nested iteration — zero intermediate list allocation.
    if _IOC_EXTRACT_PYTHON_ZERO_COPY_AVAILABLE and _rust_batch_ioc_extract_python is not None:
        try:
            batch_results: list[list[tuple[str, str]]] = _rust_batch_ioc_extract_python(texts)
            for text_result in batch_results:
                yield from text_result
            return
        except Exception:
            pass  # Tier 2 fallback

    # Tier 2: rayon Vec return (legacy fallback)
    if _IOC_EXTRACT_BATCH_AVAILABLE and _rust_batch_ioc_extract is not None:
        try:
            batch_results: list[list[tuple[str, str]]] = _rust_batch_ioc_extract(texts)
            for text_result in batch_results:
                yield from text_result
            return
        except Exception:
            pass  # Tier 3 fallback

    # Tier 3: pure Python (final fallback)
    try:
        from intelligence import ioc_qs
        for text in texts:
            yield from ioc_qs.extract_iocs_from_text(text)
    except Exception:
        return


# Sprint F216G: WAL Manager and Dedup Manager (extracted from this file)
from .wal import WALManager  # noqa: E402

logger = logging.getLogger(__name__)
# Sprint F222: Semantic buffering (extracted from this file)


# Sprint F217: Ingest Pipeline Interface
# --------------------------------------------------------------------------

class ActivationResult(msgspec.Struct, gc=False):
    """
    Sprint F300: msgspec.Struct for activation record operations.

    Fields:
        finding_id:     Unique identifier of the finding
        lmdb_success:   True if LMDB WAL write succeeded
        duckdb_success: True if DuckDB write succeeded, False if it failed,
                        None if not yet attempted
        lmdb_key:       "finding:{id}" - LMDB key used
        desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)
        error:          Error message if there was an exception, None otherwise
        accepted:       True when finding passed quality gate and was stored
    """

    finding_id: str
    lmdb_success: bool | list[bool]
    duckdb_success: bool | None
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool = False


class ReplayResult(msgspec.Struct, gc=False):
    """
    Sprint F300: msgspec.Struct for pending-sync replay operations.

    Fields:
        finding_id:           Unique identifier of the finding
        marker_found:         True if pending marker existed before replay attempt
        wal_truth_found:      True if finding:{id} WAL truth was found in LMDB
        duckdb_written:       True if DuckDB write succeeded during replay
        marker_cleared:       True if pending marker was cleared after success
        read_back_verified:   True if fresh read-back confirmed the DuckDB record
        deadlettered:         True if marker was moved to dead-letter namespace
        retry_count:          Number of retry attempts made
        error:                Error message if there was an exception, None otherwise
    """

    finding_id: str
    marker_found: bool
    wal_truth_found: bool
    duckdb_written: bool
    marker_cleared: bool
    read_back_verified: bool
    deadlettered: bool
    retry_count: int
    error: str | None



class CanonicalFinding(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint 8P: Canonical internal finding DTO.

    Minimální povinná pole:
      - finding_id: str       - unique identifier
      - query: str             - research query text
      - source_type: str       - source type (e.g., "web", "document", "synthetic")
      - confidence: float       - confidence score [0.0, 1.0]
      - ts: float              - Unix timestamp
      - provenance: tuple[str, ...] - tvrdý invariant, nesmí být None, default = ()

    Volitelná pole:
      - payload_text: str | None - supplementary text payload

    DTO invariants:
      - frozen=True  - immutabilní instance
      - gc=False     - zakázán garbage collector tracking (výkon)
      - msgspec.Struct - zero-copy decode/encode

    NOTE 8Q/8R: CanonicalFinding je používán napříč celým projektem jako univerzální
        typ pro všechny findingy. Přesun do sdíleného DTO modulu by vyžadoval
        extra import cyklus break (storage → DTO → callers). Aktuálně jeadržován
        in-process přes async_ingest_findings_batch(), což je dostatečné.
    """

    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()

    # Volitelné doplňkové pole - jde do LMDB WAL payloadu, ne do DuckDB INSERT
    payload_text: str | None = None


class FindingQualityDecision(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint 8W: Quality decision contract for CanonicalFinding ingest.

    Fields:
        accepted:        True if finding passed quality gate
        reason:          Human-readable reason for reject/accept, or None
        entropy:         Computed entropy in bits per character
        normalized_hash: BLAKE2b fingerprint of normalized text (hex, 32 chars)
        duplicate:       True if exact-content duplicate detected
    """

    accepted: bool
    reason: str | None
    entropy: float
    normalized_hash: str | None
    duplicate: bool


# Sprint F216G: QualityRejectionRecord moved to quality_assessment.py


# ---------------------------------------------------------------------------
# Graph injection helpers
# ---------------------------------------------------------------------------


def _check_graph_capability(graph: Any, slot_name: str) -> None:
    """
    Runtime type safety for graph injection slots.

    Validates that the graph has the required buffer_ioc/flush_buffers methods.
    Raises TypeError if the graph lacks required capabilities.

    This prevents DuckPGQGraph (which lacks buffered writes) from being
    accidentally injected into truth-write-only slots.
    """
    if not (
        callable(getattr(graph, "buffer_ioc", None))
        and callable(getattr(graph, "flush_buffers", None))
    ):
        raise TypeError(
            f"{slot_name}: graph must implement buffer_ioc() and flush_buffers(). "
            f"Got {graph.__class__.__name__} which lacks buffered write capability. "
            f"Use IOCGraph (Kuzu) for truth-write slots."
        )


# Quality helper constants and functions moved to quality_assessment.py


# ---------------------------------------------------------------------------
# Package-level guard: duckdb is imported only inside initialize()
# ---------------------------------------------------------------------------

_DuckDBModule: Any | None = None


def _get_duckdb() -> Any:
    """Lazy import of duckdb - only loaded when sidecar is actually used."""
    global _DuckDBModule
    if _DuckDBModule is None:
        import duckdb

        _DuckDBModule = duckdb
    return _DuckDBModule


# ---------------------------------------------------------------------------
# Env-configurable limits
# ---------------------------------------------------------------------------

_DUCKDB_MEMORY_LIMIT: str = os.environ.get("GHOST_DUCKDB_MEMORY", "2GB")  # P3.4: 400MB→2GB for M1 Air 8GB; matches _resolve_duckdb_runtime_settings default
_DUCKDB_MAX_TEMP: str = os.environ.get("GHOST_DUCKDB_MAX_TEMP", "1GB")

# Sprint P0-4: Arrow zero-copy ingest (default ON - M1 EIGHTGB optimized, 1.5-2* faster than executemany).
# Disabled via HLEDAC_ARROW_INGEST=0 when Arrow path causes issues.
# Off -> async_record_canonical_findings_batch_arrow silently falls back to legacy executemany.
# On  -> Arrow path for batches >= ARROW_MIN_BATCH (below threshold still falls back to executemany
#       because per-row executemany call overhead is lower than Arrow table build for tiny N).
_ARROW_INGEST_ENABLED: bool = os.environ.get("HLEDAC_ARROW_INGEST", "1") != "0"

# Sprint P1-1: RAM disk temp directory for :memory: mode DuckDB.
# HLEDAC_DUCKDB_RAMDISK_TEMP=/Volumes/hledac_ram -> SET temp_directory for :memory: connections.
# This enables :memory: speed with temp spills to RAM disk instead of SSD.
_DUCKDB_RAMDISK_TEMP: str | None = os.environ.get("HLEDAC_DUCKDB_RAMDISK_TEMP")
# Sprint P0-4: Arrow path break-even vs executemany is roughly N=5-10 on M1 EIGHTGB.
# Below 5, executemany wins on per-call overhead; above, Arrow register+INSERT dominates.
# F265C: Lowered from 50->20. F5.2: Lowered from 20->5 - sprints produce 0-30 findings
#        per cycle; with quality gate filtering, many accepted batches are 1-10 items.
#        Arrow table build overhead (~0.5ms) is amortized across any batch >= 5.
# Telemetry: _ARROW_PATH_SELECTED counter tracks path selection.
_ARROW_MIN_BATCH: int = int(os.environ.get("HLEDAC_ARROW_MIN_BATCH", "5"))  # M1: Arrow amortized from ~5 rows

# Sprint P1-3 + Variant B (F265B): Arrow path telemetry - instance-level, bounded.
# F265B: REMOVED module-level _ARROW_METRICS — moved to DuckDBShadowStore._arrow_metrics
#        (per-instance, reset on aclose(), prevents cross-sprint unbounded growth).
# Metrics KEYS preserved for get_arrow_metrics() backward compat.


def _check_pyarrow_available() -> bool:
    """
    Sprint F265C: Cache-aware pyarrow availability check.

    Called from tight loops (executor overhead path) so we optimize for the
    common case: pyarrow already imported -> O(1) sys.modules lookup, zero I/O.
    Only falls back to find_spec when pyarrow is not yet loaded.

    Caches result in module-level _PYARROW_AVAILABLE so repeated calls in the
    same process are always O(1).
    """
    # Fast path: already imported in this process
    if "pyarrow" in sys.modules:
        return True
    # Cache miss: check via find_spec (no I/O, just importer machinery)
    cached = getattr(_check_pyarrow_available, "_cached", None)
    if cached is not None:
        return cached
    import importlib.util
    spec = importlib.util.find_spec("pyarrow")
    result = spec is not None
    object.__setattr__(_check_pyarrow_available, "_cached", result)
    return result


def get_arrow_metrics() -> dict[str, int]:
    """
    Sprint F265C: Expose Arrow ingest metrics for sprint telemetry.
    Sprint F265B Variant B: DEPRECATED — _ARROW_METRICS moved to instance-level
    DuckDBShadowStore._arrow_metrics (per-sprint reset, prevents cross-sprint growth).

    Returns empty dict — instance metrics are accessed via store._arrow_metrics
    in __main__.py L2664 (preferred path) or sprint_scheduler L8949.
    Kept for backward compat with external callers that import this directly.
    """
    return {}


# ---------------------------------------------------------------------------
# UMA-aware DuckDB runtime settings
# ---------------------------------------------------------------------------

def _resolve_duckdb_runtime_settings(
    uma_state: str | None = None,
    swap_detected: bool = False,
) -> dict[str, str | int]:
    """
    Resolve DuckDB runtime settings based on UMA memory pressure state.

    DuckDB store receives explicit uma_state from resource_governor/scheduler
    - it MUST NOT import heavy runtime schedulers to determine this internally.

    Args:
        uma_state: One of "WARN", "CRITICAL", "EMERGENCY", or None for normal.
        swap_detected: True if system-level swap pressure is detected.

    Returns:
        dict with keys: memory_limit (str), max_temp (str),
                        threads (int), preserve_insertion_order (bool),
                        safe_mode (bool), write_buffer_limit (str),
                        allocator_flush_threshold (str),
                        allocator_bulk_dealloc_threshold (str),
                        enable_fsst_vectors (bool),
                        temp_file_encryption (bool).
    """
    base_mem = os.environ.get("GHOST_DUCKDB_MEMORY", "2GB")  # P3.4: 400MB→2GB for M1 Air 8GB, better performance
    base_threads = int(os.environ.get("HLEDAC_DUCKDB_THREADS", 4))  # M1 8GB: 4 cores optimal

    settings: dict[str, str | int | bool] = {
        "memory_limit": base_mem,
        "max_temp": _DUCKDB_MAX_TEMP,
        "threads": base_threads,
        "preserve_insertion_order": False,
        "safe_mode": False,
        # F314-4: Adaptive chunk_size for batch processing — scaled by UMA state
        "chunk_size": 1024,  # default, overridden per-state below
        # F314-4b: Adaptive pipeline_maxsize — scaled by UMA state
        "pipeline_maxsize": 4,  # default, overridden per-state below
        # DuckDB 1.5.4 columnar compression & allocator tuning for M1 8GB:
        # - write_buffer_row_group_memory_limit: row group flush threshold
        #   Default 145MiB → 64MiB (M1 8GB: faster flush, less memory held)
        "write_buffer_limit": "64MiB",
        # - allocator_flush_threshold: peak allocation to flush after completing task
        #   Default 128MiB → 64MiB (M1 8GB: more frequent implicit cleanup)
        "allocator_flush_threshold": "64MiB",
        # - allocator_bulk_deallocation_flush_threshold: bulk dealloc trigger
        #   Default 512MiB → 256MiB (M1 8GB: faster bulk memory return)
        "allocator_bulk_dealloc_threshold": "256MiB",
        # - enable_fsst_vectors: FSST compression for string/JSON columns
        #   Default false → true (M1 8GB: ~2-4× text compression, moderate CPU cost)
        "enable_fsst_vectors": True,
        # - temp_file_encryption: encrypt temp files (not needed, adds overhead)
        #   Default false (already safe, but explicit for clarity)
        "temp_file_encryption": False,
    }

    # F314-4: Adaptive chunk sizing — dynamically scale batch chunk based on memory pressure
    # Smaller chunks = lower RAM spike, larger chunks = better throughput at low pressure
    # M1 8GB: 256×5KB=1.3MB, 512×5KB=2.5MB, 1536×5KB=7.5MB peak
    if swap_detected:
        # EMERGENCY: minimize memory footprint
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True
        # Disable FSST in emergency (CPU cost not worth compression gain)
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "32MiB"
        settings["allocator_flush_threshold"] = "32MiB"
        settings["allocator_bulk_dealloc_threshold"] = "128MiB"
        settings["chunk_size"] = 256  # F314-4: minimal for emergency
        settings["pipeline_maxsize"] = 2  # F314-4b: minimal buffer for emergency

    elif uma_state == "EMERGENCY":
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "32MiB"
        settings["allocator_flush_threshold"] = "32MiB"
        settings["allocator_bulk_dealloc_threshold"] = "128MiB"
        settings["chunk_size"] = 256  # F314-4: minimal for emergency
        settings["pipeline_maxsize"] = 2  # F314-4b: minimal buffer for emergency

    elif uma_state == "CRITICAL":
        settings["memory_limit"] = "250MB"
        settings["threads"] = 1
        settings["enable_fsst_vectors"] = False
        settings["write_buffer_limit"] = "48MiB"
        settings["allocator_flush_threshold"] = "48MiB"
        settings["allocator_bulk_dealloc_threshold"] = "192MiB"
        settings["chunk_size"] = 512  # F314-4: conservative for critical
        settings["pipeline_maxsize"] = 2  # F314-4b: conservative for critical

    elif uma_state == "WARN":
        # Conservative but still usable
        settings["memory_limit"] = "250MB"
        settings["threads"] = 2
        settings["write_buffer_limit"] = "64MiB"
        settings["allocator_flush_threshold"] = "64MiB"
        settings["allocator_bulk_dealloc_threshold"] = "256MiB"
        settings["chunk_size"] = 768  # F314-4: moderate for warn
        settings["pipeline_maxsize"] = 3  # F314-4b: moderate for warn

    else:
        # F314-4: Normal — larger chunks for better throughput
        settings["chunk_size"] = 1536  # F314-4: 1.5× larger than old 1024, M1-safe
        settings["pipeline_maxsize"] = 6  # F314-4b: max overlap for normal

    return settings


def _validate_duckdb_setting(value: str, setting_name: str) -> str:
    """
    Validate DuckDB setting value to prevent SQL injection.

    P1-3: Replaces f-string interpolation in SET commands.
    Only allows alphanumeric, GB/MB/KB/TB/MiB/GiB/KiB suffixes, and basic punctuation.
    """
    import re

    # Allow: numbers, GB/MB/KB/TB/MiB/GiB/KiB suffixes, decimal point, spaces
    if not re.match(r"^[\d.]+\s*(GB|MB|KB|TB|MiB|GiB|KiB)?\s*$", value.strip(), re.IGNORECASE):
        raise ValueError(f"Invalid DuckDB setting {setting_name}: {value!r}")
    return value.strip()


def _validate_path_setting(value: Path, setting_name: str) -> str:
    """
    Validate Path setting for DuckDB SET commands.

    P1-3: Ensures path is absolute and contains no shell metacharacters.
    """
    value_str = str(value)
    if not value.is_absolute():
        raise ValueError(f"Invalid DuckDB {setting_name}: must be absolute path, got {value_str!r}")
    # Block shell metacharacters that could escape the string
    dangerous = ["'", '"', ";", "$", "`", "\\", "\n", "\r"]
    for char in dangerous:
        if char in value_str:
            raise ValueError(f"Invalid DuckDB {setting_name}: dangerous char {char!r} in {value_str!r}")
    return value_str


def _validate_duckdb_threads(value: str | int, setting_name: str = "threads") -> int:
    """
    Validate threads is a safe positive integer in DuckDB-supported range.

    P1-3: Replaces f-string interpolation in PRAGMA threads=...
    DuckDB PRAGMA does not support ? prepared params, so we validate
    the integer at write time rather than use parameterized syntax.
    """
    try:
        int_val = int(value)
    except (ValueError, TypeError) as err:
        raise ValueError(f"Invalid DuckDB {setting_name}: cannot convert {value!r} to int") from err
    max_threads = min(os.cpu_count() or 4, 8)
    if not (1 <= int_val <= max_threads):
        raise ValueError(f"Invalid DuckDB {setting_name}: {int_val} out of safe range [1, {max_threads}]")
    return int_val

# Sprint 8AG §6.17: Persistent dedup config
_DEDUP_LMDB_MAP_SIZE: int = 64 * 1024 * 1024  # 64MB dedicated dedup LMDB
# _DEDUP_HOT_CACHE_MAX moved to quality_assessment.py (Sprint F216G refactor)


# ---------------------------------------------------------------------------
# Schema SQL (defined once, used in both modes)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS canonical_findings (
        id              VARCHAR PRIMARY KEY,
        query           VARCHAR,
        source_type     VARCHAR,
        confidence      DOUBLE,
        ts              DOUBLE,
        provenance_json TEXT,
        payload_text    TEXT,
        UNIQUE (id),
        UNIQUE (query, source_type)
    );
    -- Sprint STORAGE-FIX-1: time-range + per-query lookups
    -- canonical_findings is queried with WHERE query LIKE ? ORDER BY ts DESC LIMIT N (6+ sites).
    -- time-range + per-query lookups, indexes for performance
    CREATE INDEX IF NOT EXISTS idx_canonical_findings_ts ON canonical_findings(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_canonical_findings_query ON canonical_findings(query);
    CREATE TABLE IF NOT EXISTS shadow_runs (
        run_id      VARCHAR PRIMARY KEY,
        started_at  TIMESTAMP,
        ended_at    TIMESTAMP,
        total_fds   INTEGER,
        rss_mb      INTEGER
    );
    CREATE TABLE IF NOT EXISTS sprint_delta (
        sprint_id TEXT PRIMARY KEY,
        ts DOUBLE NOT NULL,
        query TEXT,
        duration_s REAL DEFAULT 0,
        new_findings INT DEFAULT 0,
        dedup_hits INT DEFAULT 0,
        ioc_nodes INT DEFAULT 0,
        ioc_new_this_sprint INT DEFAULT 0,
        uma_peak_gib REAL DEFAULT 0,
        synthesis_success BOOL DEFAULT false,
        findings_per_minute REAL DEFAULT 0,
        top_source_type TEXT,
        synthesis_confidence REAL DEFAULT 0
    );
    -- Index for ORDER BY ts DESC queries (scoreboard, recent sprints)
    CREATE INDEX IF NOT EXISTS idx_sprint_delta_ts ON sprint_delta(ts DESC);
    CREATE TABLE IF NOT EXISTS source_hit_log (
        sprint_id TEXT,
        ts DOUBLE,
        source_type TEXT,
        findings_count INT,
        ioc_count INT,
        hit_rate REAL
    );
    -- Sprint F-B: indexes for per-sprint + time-range source_hit_log lookups
    CREATE INDEX IF NOT EXISTS idx_source_hit_log_sprint_ts
        ON source_hit_log(sprint_id, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_source_hit_log_ts
        ON source_hit_log(ts DESC);
    CREATE TABLE IF NOT EXISTS sprint_scorecard (
        sprint_id TEXT PRIMARY KEY,
        ts DOUBLE NOT NULL,
        findings_per_minute REAL,
        ioc_density REAL,
        semantic_novelty REAL,
        source_yield_json TEXT,
        phase_timings_json TEXT,
        outlines_used BOOL,
        accepted_findings INT,
        ioc_nodes INT
    );
    CREATE INDEX IF NOT EXISTS idx_sprint_scorecard_ts
        ON sprint_scorecard(ts DESC);
    CREATE TABLE IF NOT EXISTS research_episodes (
        episode_id   TEXT PRIMARY KEY,
        sprint_id    TEXT NOT NULL,
        query        TEXT NOT NULL,
        summary      TEXT,
        top_findings JSON,
        ioc_clusters JSON,
        source_yield JSON,
        synthesis_engine TEXT,
        duration_s   REAL,
        ts           DOUBLE NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_episodes_ts ON research_episodes(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_episodes_sprint
        ON research_episodes(sprint_id);
    CREATE TABLE IF NOT EXISTS target_profiles (
        target_id TEXT PRIMARY KEY,
        first_seen DOUBLE,
        last_seen DOUBLE,
        cumulative_finding_count INTEGER,
        entity_summary_json TEXT
    );
    -- Sprint F-B: target_profiles queried by last_seen DESC for recent targets
    CREATE INDEX IF NOT EXISTS idx_target_profiles_last_seen
        ON target_profiles(last_seen DESC);
    CREATE TABLE IF NOT EXISTS hypothesis_feedback (
        id TEXT PRIMARY KEY,
        target_id TEXT,
        pivot_type TEXT,
        ioc_type TEXT,
        produced_count INTEGER,
        accepted_count INTEGER,
        signal_value DOUBLE,
        ts DOUBLE
    );
    -- Sprint F-B: hypothesis_feedback target_id is the primary filter
    -- for per-target pivot analytics. Index avoids scan.
    CREATE INDEX IF NOT EXISTS idx_hypothesis_feedback_target_ts
        ON hypothesis_feedback(target_id, ts DESC);
    CREATE TABLE IF NOT EXISTS hypothesis_tracking (
        hypothesis_id TEXT PRIMARY KEY,
        sprint_id TEXT,
        hypothesis_text TEXT,
        status TEXT,
        confidence REAL,
        falsification_result TEXT,
        disproved_by_sprint_id TEXT,
        ts DOUBLE
    );
    -- Sprint F-B: hypothesis_tracking is queried by sprint_id and status
    -- in the windup_engine hypothesis summarizer.
    CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_sprint
        ON hypothesis_tracking(sprint_id);
    CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_status_ts
        ON hypothesis_tracking(status, ts DESC);
    CREATE TABLE IF NOT EXISTS target_memory (
        target_id TEXT PRIMARY KEY,
        first_seen_ts DOUBLE NOT NULL,
        last_seen_ts DOUBLE NOT NULL,
        sprint_count INTEGER NOT NULL,
        cumulative_finding_count INTEGER NOT NULL,
        entity_facets_json TEXT NOT NULL,
        exposure_facets_json TEXT NOT NULL,
        pivot_facets_json TEXT NOT NULL,
        confidence_drift_json TEXT NOT NULL,
        updated_by_sprint_id TEXT NOT NULL
    );
    -- Sprint F-B: target_memory last_seen_ts is the primary sort key
    -- for "recent targets" queries in F204D.
    CREATE INDEX IF NOT EXISTS idx_target_memory_last_seen
        ON target_memory(last_seen_ts DESC);
    -- Sprint F224A: DHT metadata table for torrent content discovery
    CREATE TABLE IF NOT EXISTS dht_metadata (
        infohash TEXT PRIMARY KEY,
        name TEXT,
        files_json TEXT,
        size_bytes BIGINT,
        first_seen DOUBLE,
        last_seen DOUBLE,
        peer_count INT,
        sources_json TEXT
    );
    -- Sprint F-B: dht_metadata is queried by last_seen DESC and peer_count
    -- for "recent active torrents" and "popular torrents" lookups.
    CREATE INDEX IF NOT EXISTS idx_dht_metadata_last_seen
        ON dht_metadata(last_seen DESC);
    CREATE INDEX IF NOT EXISTS idx_dht_metadata_peer_count
        ON dht_metadata(peer_count DESC);
"""


# Sprint 8R / F265: msgspec_json.encode() for CanonicalFinding provenance.
# encode() uses msgspec's built-in thread-safe Encoder with per-thread pooling
# (bounded at _POOL_MAX=8 per thread via threading.local). This is the canonical
# write path for DuckDB; per-call Encoder.encode() is safe because each call
# pops from the pool, encodes, and returns to the pool (no concurrent access).
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode  # noqa: E402

# Sprint F-CLEAN: Max concurrent in-flight graph update tasks (advisory only).
# Bounds the `_bg_tasks` set under bursty accepted-write load. Discard callback
# on each task ensures steady-state count returns near 0 after the burst.
# M1 EIGHTGB safe - work runs on the default ThreadPoolExecutor, never a subprocess.
# 16 concurrent DuckPGQ upserts is well under the 1-worker DuckDB executor.
_MAX_INFLIGHT_GRAPH_UPDATES: int = 16

# ─── Module-level cleanup callback for weakref.finalize ──────────────

def _duckdb_at_exit_shutdown(instance: DuckDBShadowStore) -> None:
    """Called by weakref.finalize at interpreter exit if explicit aclose() was not called.

    DuckDBShadowStore keeps _shared_executor alive per Sprint 8L contract
    (for re-init safety after aclose()), but we add finalizer to ensure
    atexit cleanup if aclose() was never called.

    This is synchronous (runs in main thread at shutdown):
      1. Signal worker thread to stop via _executor.shutdown()
      2. Best-effort — DuckDB connections are complex to clean up safely
    """
    try:
        if instance._shared_executor is not None:
            instance._shared_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass

class DuckDBShadowStore:
    # MEM-1: __slots__ for memory optimization on M1 8GB
    # All 30 instance attributes declared - ~1.4 KB per-instance savings vs __dict__
    __slots__ = (
        # Core state
        '_initialized', '_closed', '_lazy', '_db_path', '_temp_dir', '_uma_state',
        '_memory_limit', '_max_temp', '_startup_ready', '_quality_state',
        # DuckDB connection
        '_duckdb_module', '_duckdb_settings', '_persistent_conn', '_file_conn',
        # WAL/Dedup
        '_wal_manager', '_wal_lmdb', '_dedup_lmdb', '_dedup_lmdb_path',
        '_dedup_lmdb_boot_error', '_dedup_lmdb_last_error', '_dedup_manager',
        # Semantic store
        '_semantic_store', '_semantic_buffer',
        # Replay
        '_replay_lock', '_startup_replay_done',
        # Background tasks
        '_bg_tasks', '_checkpoint_task',
        # Executor (for async ops) — F285-U1: all 4 pools unified to _shared_executor
        '_shared_executor', '_executor_semaphore',
        '_write_executor', '_read_executor', '_wal_executor', '_duckdb_arrow_executor', '_executor',
        # F300S-FIX: _query_executor set via object.__setattr__ in _qe() lazy init
        '_query_executor',
        # Temporal anonymizer
        '_temporal_anonymizer',
        # Sprint F265B Variant B: Arrow path telemetry — per-instance, reset on aclose()
        '_arrow_metrics',
        # Lazy graph store (set via object.__setattr__ for name mangling)
        '_DuckDBShadowStore__graph_store',
        # Prepared statement cache (set via object.__setattr__ in nested _DuckDBQueryExecutor)
        '_stmt_insert_finding', '_stmt_insert_finding_conn_id',
        # Constant-like class attribute (assigned via self.)
        'DEAD_LETTER_PREFIX',
        # F300S-FIX: weakref support for __slots__ (required for weakref.finalize)
        '__weakref__',
        # F300S-FIX: weakref finalizer — registered in __init__, detached in aclose()
        # Also needs '__weakref__' in slots to support weakref.finalize()
        '_finalizer',
    )

    """DuckDB sidecar with RAMDISK-first / OPSEC-safe degraded mode."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        uma_state: str | None = None,
        lazy: bool = True,
    ) -> None:
        """
        Initialize DuckDBShadowStore.

        Args:
            db_path:  Optional explicit DB path. If None, resolved via _resolve_path().
                     Passing a Path enables file-mode (MODE A) without requiring paths.py.
            temp_dir: Optional explicit temp directory for DuckDB scratch space.
                     Required when db_path is set; ignored for :memory: mode.
            uma_state: Optional UMA memory pressure state ("WARN", "CRITICAL", "EMERGENCY").
                     Set by resource_governor or scheduler at startup to adjust DuckDB
                     settings for memory-constrained environments (M1 EIGHTGB UMA only).
            lazy:     If True (default), DuckDB connection is deferred to first actual
                     query via ensure_connected(). __aenter__ returns immediately without
                     connecting. If False, connects eagerly in async_initialize() (legacy
                     behavior). M1 8GB: lazy=True saves ~1-2s from sprint boot.
        """
        # Sprint DuckDB Lazy Init (F265X): lazy=True defers DuckDB connection to first query.
        # M1 8GB: saves ~1-2s from sprint boot when no findings are produced.
        self._lazy: bool = lazy
        self._initialized: bool = False
        self._closed: bool = False
        # Sprint 8D: test-friendly seam - allow db_path/temp_dir injection
        self._db_path: Path | None = Path(db_path) if db_path is not None else None
        self._temp_dir: Path | None = Path(temp_dir) if temp_dir is not None else None
        self._memory_limit: str = _DUCKDB_MEMORY_LIMIT
        self._max_temp: str = _DUCKDB_MAX_TEMP
        self._duckdb_module: Any | None = None
        # Sprint F231: UMA-aware settings - explicit injection from resource_governor/scheduler
        self._uma_state: str | None = uma_state
        self._duckdb_settings: dict[str, str | int] = {}  # resolved at connection init

        # Sprint F285-U1: Unified executor — replaces 4 separate pools with 1 adaptive pool.
        #
        # BEFORE (F285): 4 pools × up to 6 threads = ~150 MB overhead
        #   _write_executor: 2 workers  (legacy insert + analytics)
        #   _read_executor:  1 worker   (never used, confirmed by grep audit)
        #   _wal_executor:   1 worker   (WAL LMDB put/get)
        #   _duckdb_arrow_executor: 2 workers (Arrow batch insert)
        #
        # AFTER (F285-U1): 1 pool × 3 workers = ~75 MB overhead (~50% reduction)
        #   - WAL I/O (LMDB putmulti) runs on shared pool via asyncio.gather overlap
        #   - DuckDB Arrow INSERT (CPU-bound SIMD memcpy) runs on shared pool
        #   - asyncio.Semaphore(3) bounds concurrent executor calls (WAL + DuckDB + legacy)
        #   - DuckDB PRAGMA threads=2: internal parallelism within each call
        #   - M1 8GB: 3 workers × ~25MB = ~75MB, safe within ~6.25GB total budget
        #
        # Backward compat: _executor alias preserved for sync submit() in tests/wrappers.
        # _wal_executor / _duckdb_arrow_executor preserved as aliases for Arrow path.
        # F300S: Reduced from 3→2 for M1 8GB UMA. 3 workers = ~50 MB extra RAM overhead.
        # DuckDB I/O is I/O-bound (mmap WAL, Arrow INSERT), not CPU-bound.
        # Quality gate uses Rust rayon (offloaded to process), not these threads.
        # _adjust_executor_pool still allows 3 workers in "ok" state if needed.
        _max_workers = min(2, max(1, (os.cpu_count() or 2) - 1))
        self._shared_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_max_workers,
            thread_name_prefix="duckdb_unified",
        )
        # Backward-compat aliases (Arrow path references these by name)
        self._write_executor: ThreadPoolExecutor = self._shared_executor
        self._read_executor: ThreadPoolExecutor = self._shared_executor
        self._wal_executor: ThreadPoolExecutor = self._shared_executor
        self._duckdb_arrow_executor: ThreadPoolExecutor = self._shared_executor
        # Concurrency bound — prevents unbounded parallel executor calls
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        self._executor_semaphore: asyncio.Semaphore = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)

        # Persistent connection for :memory: mode; None for file mode
        self._persistent_conn: Any | None = None

        # Sprint 7H: Persistent file-backed connection for file mode
        # THREAD SAFETY: _file_conn is worker-thread-only. All _sync_* methods
        # using _file_conn are explicitly documented with "MUST be called on worker thread".
        # DuckDB connections are NOT thread-safe; this design ensures single-threaded access.
        self._file_conn: Any | None = None

        # Async queue for batch scheduling (optional, deferred to future sprint)
        # For 8AS: direct run_in_executor for each call

        # Sprint 8H: Per-instance replay guard - prevents concurrent replay of same markers
        # NOTE: _replay_lock is lazy; initialize it lazily on first async use
        self._replay_lock: asyncio.Lock | None = None

        # Sprint 8L: Boot barrier - startup replay must complete before writes are accepted
        self._startup_ready: asyncio.Event = asyncio.Event()  # set after init + optional replay
        self._startup_replay_done: bool = False  # True once startup replay has run

        # Sprint F216G: Quality assessment state - delegated to QualityAssessmentState
        # (extracted from duckdb_store.py in Sprint F216G refactor)
        # Counters: _quality_rejected_count, _quality_duplicate_count, _quality_fail_open_count,
        #           _persistent_duplicate_count, _accepted_count
        # Ledger:   _quality_rejection_ledger (bounded, max 200 entries)
        self._quality_state: QualityAssessmentState = QualityAssessmentState()

        # Sprint F265B Variant B: Arrow path telemetry — per-instance, reset on aclose()
        # Prevents cross-sprint unbounded growth that module-level _ARROW_METRICS had.
        self._arrow_metrics: dict[str, int] = {
            "arrow_selected": 0,
            "arrow_fallback_env": 0,
            "arrow_fallback_batch": 0,
            "arrow_fallback_pyarrow": 0,
            "arrow_fallback_init": 0,
            "arrow_fallback_executor": 0,
            "arrow_fallback_empty": 0,
            "arrow_fallback_all_fail": 0,
            "arrow_success_count": 0,
            "arrow_success_lmdb_count": 0,
            "arrow_success_duckdb_count": 0,
            "arrow_error_table_build": 0,
            "arrow_error_duckdb_insert": 0,
            "arrow_error_partial": 0,
            # BUG-11: Tracks partial inserts where ON CONFLICT silently deduplicated some rows
            "arrow_partial_duplicates": 0,
        }

        # Sprint F216G: WAL Manager - owns LMDB for pending sync markers, deadletters, WAL replay
        # (extracted from duckdb_store.py in Sprint F216G refactor)
        self._wal_manager: WALManager | None = None

        # Sprint F216G: Dedup Manager - owns persistent dedup LMDB, hot cache, semantic dedup
        # (extracted from duckdb_store.py in Sprint F216G refactor)
        self._dedup_manager: DedupManager | None = None

        # Sprint F216G: Backward-compat aliases - code that reads _wal_lmdb / _dedup_lmdb directly
        # gets None so hasattr checks return False (store uses manager methods now).
        # Remove after all callers migrated to WALManager/DedupManager methods.
        self._wal_lmdb: Any | None = None
        self._dedup_lmdb: Any | None = None

        # Sprint 8AV: Dead-letter namespace for ingested-but-rejected findings
        self.DEAD_LETTER_PREFIX: str = "deadletter_ingest:"

        # Sprint 8AG §6.17: Persistent dedup LMDB - now managed by DedupManager
        # (legacy aliases kept for backward compat during migration)
        self._dedup_lmdb_path: Path | None = None
        self._dedup_lmdb_last_error: str | None = None
        self._dedup_lmdb_boot_error: str | None = None

        # Sprint 8W: In-memory dedup set - REMOVED (Sprint F222)
        # Now delegated to DedupManager._dedup_hot_cache (owned there since F216G)

        # Sprint 8QA: Background task tracking for graph ingest
        # F3.3: BoundedTaskSet replaces unbound set[asyncio.Task] — K11 fix
        self._bg_tasks: BoundedTaskSet = (
            BoundedTaskSet(maxsize=_MAX_INFLIGHT_GRAPH_UPDATES)
            if BoundedTaskSet is not None
            else cast(Any, None)
        )

        # Sprint 8SB: Semantic store (FastEmbed + LanceDB)
        self._semantic_store: Any | None = None

        # Sprint 1.2: Backward-compat alias — _executor kept so that
        # synchronous submit() calls in tests / compat wrappers still resolve.
        # New async code uses _write_executor / _read_executor explicitly.
        self._executor = self._write_executor

        # Sprint 8SB: Semantic buffer - fail-open, no-op if no store injected
        from hledac.universal.knowledge.semantic_store_buffer import SemanticStoreBuffer
        self._semantic_buffer: SemanticStoreBuffer = SemanticStoreBuffer()

        # P3-2: Background DuckDB checkpoint task for native WAL.
        # Only active for file mode (None for :memory:).
        self._checkpoint_task: asyncio.Task | None = None

        # Sprint F265B Variant B: M1 RAM-adaptive executor pool sizing.
        # Scales _duckdb_arrow_executor workers down on memory pressure to conserve RAM.
        # CRITICAL/EMERGENCY state: max_workers=1; SOFT_WARN/WARN: max_workers=2; OK: max_workers=2 (default).
        self._adjust_executor_pool()

        # F289: weakref.finalize for interpreter-exit cleanup guarantee.
        # DuckDBShadowStore keeps _shared_executor alive per Sprint 8L contract
        # (for re-init safety), but we add finalizer to ensure atexit cleanup
        # if aclose() was never called.
        # F300S-FIX: wrapped in try/except — weakref.finalize can fail if __slots__
        # lacks '__weakref__' (TypeError: "cannot create weak reference").
        try:
            self._finalizer = weakref.finalize(
                self,
                _duckdb_at_exit_shutdown,
                self,
            )
            atexit.register(self._finalizer)
        except Exception:
            # Fail-open: if weakref registration fails, explicit aclose() still works.
            # The executor will be leaked at interpreter exit, but aclose() handles it.
            self._finalizer = None

    # -- M1 RAM-adaptive executor sizing ----------------------------------------
    # Sprint F265B Variant B: Dynamic thread pool scaling based on UMA pressure.

    def _adjust_executor_pool(self) -> None:
        """
        Adjust _shared_executor worker count based on M1 UMA memory pressure.

        F300S: Reduced defaults for M1 8GB UMA:
          CRITICAL/EMERGENCY: 1 worker (~50 MB saved vs 2 workers baseline)
          SOFT_WARN: 1 worker (conservative, leaves headroom for MLX)
          OK: 2 workers (baseline, set at __init__)

        F285-U1: Unified executor — all 4 former pools are now _shared_executor aliases.
        This method adjusts the single shared pool's max_workers.

        This is a best-effort advisory — executor is NOT restarted, only the
        reference to max_workers is capped for future task submissions.
        Thread count change takes effect on the NEXT submit() call.

        Lazy import of resource_governor to avoid circular deps and cold-start cost.
        """
        try:
            from hledac.universal.core.resource_governor import sample_uma_status

            snap = sample_uma_status()
            state = snap.state if snap else "ok"
        except Exception:
            state = "ok"

        if state in ("critical", "emergency", "soft_warn"):
            target_workers = 1  # F300S: reduced from 2, M1 8GB needs headroom for MLX
        else:
            target_workers = 2  # F300S: baseline for ok / warn (was 3)

        # Only update if already constructed (not on first call before __init__ completes)
        if hasattr(self, "_shared_executor") and self._shared_executor is not None:
            try:
                # Get current max_workers
                current = self._shared_executor._max_workers  # type: ignore[attr-defined]
                if current != target_workers:
                    self._shared_executor._max_workers = target_workers  # type: ignore[attr-defined]
                    _dbg = logging.getLogger(__name__)
                    _dbg.debug(
                        "[DuckDB] executor workers: %d -> %d (uma_state=%s)",
                        current,
                        target_workers,
                        state,
                    )
            except Exception:
                pass

    # -- Backward-compat property aliases ----------------------------------------
    # Sprint F216G refactor moved counters to QualityAssessmentState._quality_state.
    # Tests (probe_f223g) access store._persistent_duplicate_count directly.
    # These properties maintain the pre-F216G store.xxx interface for compat.
    @property
    def _accepted_count(self) -> int:
        return self._quality_state._accepted_count

    @property
    def _quality_duplicate_count(self) -> int:
        return self._quality_state._quality_duplicate_count

    @property
    def _quality_rejected_count(self) -> int:
        return self._quality_state._quality_rejected_count

    @property
    def _persistent_duplicate_count(self) -> int:
        return self._quality_state._persistent_duplicate_count

    # -- Test factory -----------------------------------------------------------

    @classmethod
    def for_testing(
        cls,
        *,
        name: str = "test",
        temp_dir: Path | str | None = None,
    ) -> DuckDBShadowStore:
        """
        Create a DuckDB store for test isolation.

        Not for production use - provides a predictable temp path that is
        cleaned up by the caller after the test.

        Args:
            name:  Identifier used in the temp path (default "test").
                  Pass unique names per test to avoid collisions.
            temp_dir:  Optional temp directory. If None, a temp dir is created
                      via tempfile.mkdtemp and the caller is responsible for
                      cleaning it up.
        """
        import tempfile

        td = temp_dir or Path(tempfile.mkdtemp(prefix="hledac_test_duckdb_"))
        if isinstance(td, str):
            td = Path(td)
        td.mkdir(parents=True, exist_ok=True)
        store_path = td / f"{name}.duckdb"
        return cls(db_path=str(store_path))


    def set_uma_state(self, uma_state: str | None, swap_detected: bool = False) -> None:
        """
        Set or update UMA memory pressure state at runtime.

        Can be called while the store is open to adjust DuckDB settings.
        Resolves new settings and applies to all connections immediately.

        Args:
            uma_state: "WARN", "CRITICAL", "EMERGENCY", or None for normal.
            swap_detected: True if system-level swap is active.
        """
        self._uma_state = uma_state
        self._duckdb_settings = _resolve_duckdb_runtime_settings(uma_state, swap_detected)

    def get_uma_state(self) -> str | None:
        """Return currently configured UMA state."""
        return self._uma_state

    def get_stats(self) -> dict[str, Any]:
        """
        Sprint P2-B: Return store statistics for sprint report.

        Returns duckdb_stats section: findings count, graph stats,UMA state.
        """
        try:
            graph_stats = self.get_graph_stats() if hasattr(self, "_DuckDBShadowStore__graph_store") else {}
        except Exception:
            graph_stats = {}
        # F300-GRAPH: total_iocs sourced from DuckPGQGraph via graph_stats (DuckDB ioc_graph table removed in F272).
        # The DuckDB ioc_graph table was replaced by DuckPGQGraph as the canonical IOC store.
        total_iocs = graph_stats.get("nodes", 0) if isinstance(graph_stats, dict) else 0
        try:
            conn = self._qe()._conn if hasattr(self, "_qe") else None
            total_findings = conn.execute("SELECT COUNT(*) FROM canonical_findings").fetchone()[0] if conn else 0
        except Exception:
            total_findings = 0
        return {
            "total_findings": total_findings,
            "total_iocs": total_iocs,
            "graph_stats": graph_stats,
            "uma_state": self._uma_state or "unknown",
            "duckdb_mode": getattr(self, "_duckdb_mode", "unknown"),
        }
    # ---------------------------------------------------------------------------
    # Sprint F222: Graph slots - DEPRECATED, delegated to GraphAttachmentStore
    # ---------------------------------------------------------------------------

    def _graph_store(self) -> Any:
        """Lazy-init GraphAttachmentStore."""
        # Use getattr to access name-mangled attribute set via object.__setattr__
        _attr = "_DuckDBShadowStore__graph_store"
        if not hasattr(self, _attr):
            object.__setattr__(self, _attr, None)
        _store = object.__getattribute__(self, _attr)
        if _store is None:
            from hledac.universal.knowledge.graph_attachment import GraphAttachmentStore
            _store = GraphAttachmentStore()
            object.__setattr__(self, _attr, _store)
        return _store

    def inject_graph(self, graph: Any) -> None:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_graph()."""
        self._graph_store().inject_graph(graph)

    def get_graph_attachment_kind(self) -> str | None:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_attachment_kind()."""
        return self._graph_store().get_graph_attachment_kind()

    def graph_supports_buffered_writes(self) -> bool:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.graph_supports_buffered_writes()."""
        return self._graph_store().graph_supports_buffered_writes()

    def inject_stix_graph(self, graph: Any) -> None:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_stix_graph()."""
        self._graph_store().inject_stix_graph(graph)

    def get_stix_graph(self) -> Any:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_stix_graph()."""
        return self._graph_store().get_stix_graph()

    def inject_truth_write_graph(self, graph: Any) -> None:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.inject_truth_write_graph()."""
        self._graph_store().inject_truth_write_graph(graph)

    def get_truth_write_graph(self) -> Any:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_truth_write_graph()."""
        return self._graph_store().get_truth_write_graph()

    def truth_write_graph_supports_buffered_writes(self) -> bool:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.truth_write_graph_supports_buffered_writes()."""
        return self._graph_store().truth_write_graph_supports_buffered_writes()

    def get_top_seed_nodes(self, n: int = 5) -> list[dict]:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_seed_nodes()."""
        return self._graph_store().get_top_seed_nodes(n=n)

    def get_graph_stats(self) -> dict:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_graph_stats()."""
        return self._graph_store().get_graph_stats()

    def get_connected_iocs(self, ioc_value: str, max_hops: int = 2) -> list:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs()."""
        return self._graph_store().get_connected_iocs(ioc_value, max_hops=max_hops)

    def get_connected_iocs_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list]:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_connected_iocs_batch()."""
        return self._graph_store().get_connected_iocs_batch(values, max_hops=max_hops)

    def annotate_findings_with_graph_context(
        self,
        findings: list[dict],
        max_hops: int = 2,
        max_annotations: int = 50,
    ) -> list[dict]:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.annotate_findings_with_graph_context()."""
        return self._graph_store().annotate_findings_with_graph_context(
            findings, max_hops=max_hops, max_annotations=max_annotations
        )

    def get_analytics_graph_for_synthesis(self) -> Any:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_analytics_graph_for_synthesis()."""
        return self._graph_store().get_analytics_graph_for_synthesis()

    def get_top_entities_for_ghost_global(
        self,
        n: int = 100,
    ) -> list[tuple[str, str, float]]:
        """DEPRECATED (Sprint F222): Delegates to GraphAttachmentStore.get_top_entities_for_ghost_global()."""
        return self._graph_store().get_top_entities_for_ghost_global(n=n)

    async def get_top_findings(self, limit: int = 10) -> list[dict]:
        """
        Sprint 8VE B.4: Return top findings by confidence for IOC graph display.

        Queries canonical_findings ordered by confidence DESC, returns dicts
        with ioc, source_type, query, and confidence fields.
        """
        try:
            conn = self._qe()._conn if hasattr(self, "_qe") else None
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT id, query, source_type, confidence, ts, payload_text
                FROM canonical_findings
                ORDER BY confidence DESC, ts DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
            return [
                {
                    "ioc": row[0],
                    "query": row[1],
                    "source_type": row[2],
                    "confidence": row[3],
                    "ts": row[4],
                    "summary": (row[5] or "")[:200],
                }
                for row in rows
            ]
        except Exception:
            return []

    def inject_semantic_store(self, store: Any) -> None:
        """
        Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.

        The store is used to buffer findings for FastEmbed embedding + LanceDB
        indexing during WINDUP flush.
        """
        self._semantic_store = store
        self._semantic_buffer.inject(store)

    def _semantic_buffer_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Sprint 8SB: Buffer findings into SemanticStore for batch embedding.

        Runs as a background task (not awaited). Fail-open: any exception
        is caught and logged - semantic buffering failure never blocks storage.
        Delegated to SemanticStoreBuffer.
        """
        self._semantic_buffer.buffer_findings(findings)

    async def _graph_ingest_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Background task: ingest findings into IOC graph.

        Called via _bg_tasks tracking after async_ingest_findings_batch succeeds.
        Fail-open: any exception is caught and logged.

        Architecture (P0 Batch IOC):
            1. Batch extract IOCs from all findings in parallel (4-thread pool)
            2. Collect all (ioc_type, value) tuples → batch buffer_ioc calls
            3. Collect all observations → batch buffer_observation calls
            4. O(n) per-finding extraction → O(1) batched graph writes
        """
        # Sprint 8WA: Use dedicated truth-write graph, not analytics _ioc_graph.
        # Sprint F222: Now routed through GraphAttachmentStore
        truth_graph = self._graph_store().get_truth_write_graph()
        if truth_graph is None:
            return

        async def _run() -> None:
            try:
                import xxhash

                from hledac.universal.utils.ioc_extract import (
                    extract_iocs_batch,
                )

                # Step 1: Build extraction items (text + pattern_matches per finding)
                extraction_items: list[tuple[str, list[tuple[str, str]]]] = []
                for finding in findings:
                    text = finding.payload_text or ""
                    pm = getattr(finding, "pattern_matches", None)
                    matches: list[tuple[str, str]] = []
                    if pm and isinstance(pm, list):
                        for item in pm:
                            if isinstance(item, tuple) and len(item) == 2:
                                matches.append((str(item[0]), str(item[1])))
                            elif isinstance(item, dict):
                                v = item.get("value") or item.get("pattern") or ""
                                label = item.get("label") or ""
                                matches.append((str(v), str(label)))
                    extraction_items.append((text, matches))

                # Step 2: Parallel batch extraction — O(n) parallel regex scans
                all_ioc_results = extract_iocs_batch(extraction_items)
                if not any(r for r in all_ioc_results):
                    return

                # Step 3: Collect all IOCs and observations from parallel results
                all_iocs: list[tuple[str, str]] = []  # (ioc_type, value)
                finding_observations: list[tuple[int, str, str, float, str]] = (
                    []
                )  # (finding_idx, ioc_id_a, ioc_id_b, ts, src)

                for finding_idx, (finding, iocs) in enumerate(
                    zip(findings, all_ioc_results, strict=False)
                ):
                    if not iocs:
                        continue
                    ts = finding.ts
                    src = finding.source_type
                    fid = str(finding.finding_id)

                    # Buffer all IOCs and build id_map
                    id_map: dict[str, str] = {}
                    for value, ioc_type in iocs:
                        all_iocs.append((ioc_type, value))
                        ioc_id = f"{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}"
                        id_map[value] = ioc_id

                    # Collect observation pairs for this finding
                    values = list(id_map.keys())
                    for i, v_a in enumerate(values):
                        id_a = id_map[v_a]
                        for v_b in values[i + 1:]:
                            id_b = id_map[v_b]
                            finding_observations.append((finding_idx, id_a, id_b, ts, src))

                # Step 4: Batch buffer all IOCs — single call per unique IOC
                seen_iocs: set[tuple[str, str]] = set()
                for ioc_type, value in all_iocs:
                    ioc_key = (ioc_type, value)
                    if ioc_key not in seen_iocs:
                        await truth_graph.buffer_ioc(ioc_type, value, 1.0)
                        seen_iocs.add(ioc_key)

                # Step 5: Batch buffer all observations
                for _, id_a, id_b, ts, src in finding_observations:
                    await truth_graph.buffer_observation(id_a, id_b, fid, ts, src)

            except Exception as e:
                import logging
                _logger2 = logging.getLogger(__name__)
                _logger2.warning(f"[F206AC] truth_write_graph buffer failed: {e}")

        # F3.3: BoundedTaskSet.spawn() — async, bounded, auto-cleanup
        await self._bg_tasks.spawn(_run(), name="duckdb:truth_write_graph")

    # ---------------------------------------------------------------------------
    # Replay constants (Sprint 8H)
    # ---------------------------------------------------------------------------

    REPLAY_CHUNK_SIZE: int = 100  # markers per chunk; yields event loop between chunks
    MAX_RETRY_COUNT: int = 3      # max retries before dead-lettering a marker
    DEADLETTER_PREFIX: str = "deadletter_duckdb_sync:"  # dead-letter namespace
    # P0-9 fix: bound pending sync markers to prevent unbounded LMDB growth
    MAX_PENDING_SYNC_MARKERS: int = 10000  # max pending markers before oldest eviction

    # ------------------------------------------------------------------
    # Internal sync helpers - ALL run on the worker thread
    # ------------------------------------------------------------------

    def _init_connection(self) -> None:
        """
        Initialize the DuckDB connection. Must be called from the worker thread.
        Sets up file or :memory: mode, applies PRAGMAs and schema.
        For file mode, creates persistent _file_conn (Sprint 7H).

        F231: Uses _resolve_duckdb_runtime_settings() for UMA-aware configuration.
        Applies memory_limit, threads, and preserve_insertion_order based on
        _uma_state (set via __init__ or set_uma_state()).
        """
        duckdb = _get_duckdb()

        # F231: Resolve UMA-aware settings once per connection init
        runtime = _resolve_duckdb_runtime_settings(self._uma_state, swap_detected=False)
        self._duckdb_settings = runtime
        resolved_memory = runtime["memory_limit"]
        resolved_threads = runtime["threads"]
        runtime["preserve_insertion_order"]
        runtime["safe_mode"]

        # F266-U5: Process-level singleton lock — prevents concurrent sprint processes
        # from fighting over the same DuckDB file. Uses PID-file locking with
        # READ-ONLY and :memory: fallbacks for graceful degradation.
        if self._db_path and str(self._db_path) != ':memory:':
            _lock_mode, _lock_msg = self._acquire_process_lock()
            if _lock_mode == 'excl':
                logger.debug(f"[duckdb_init] Exclusive lock acquired: {_lock_msg}")
                _read_only_flag = False
            elif _lock_mode == 'ro':
                logger.warning(f"[duckdb_init] {_lock_msg} — operating in READ-ONLY mode")
                _read_only_flag = True
            else:
                logger.warning(f"[duckdb_init] {_lock_msg} — falling back to :memory: mode")
                self._db_path = None  # force :memory: mode below
                _read_only_flag = False
        else:
            _read_only_flag = False

        if self._db_path:
            # MODE A: RAMDISK active - persistent file DB + temp on RAMDISK
            # F265X FIX: _db_path can be Path(':memory:') (from _resolve_path)
            # or the string ':memory:'. Only set temp_dir for real file paths.
            # For :memory: mode (Path(':memory:')), skip temp_dir setup.
            _is_memory_mode = str(self._db_path) == ':memory:'
            if not _is_memory_mode:
                if self._temp_dir is None:
                    # F266-U5-3 FIX: expanduser() is REQUIRED — pathlib.Path does NOT expand ~
                    self._temp_dir = self._db_path.expanduser().parent / "duckdb_tmp"
                self._temp_dir.mkdir(parents=True, exist_ok=True)
            # Sprint F265X / F314-4: HLEDAC_DUCKDB_INPROCESS is a no-op on DuckDB 1.5.4.
            # DuckDB 1.5.4 always runs in-process (no subprocess mode).
            # DuckDB 2.0+ has in_process=True for spawning a separate subprocess.
            # On DuckDB 1.5.4 we are always in-process regardless of this flag.
            conn = duckdb.connect(str(self._db_path), read_only=_read_only_flag)
            try:
                # F273F: mark DuckDB mmap pages as reusable - reclaimable without writeback
                if not _is_memory_mode:
                    madv_free_reusable_on_path(self._db_path)
                    apply_nocache_to_path(self._db_path)
                # F231: Use resolved settings instead of hardcoded class attrs
                memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
                max_temp_val = _validate_duckdb_setting(self._max_temp, 'max_temp')
                conn.execute("SET memory_limit = ?", [memory_limit_val])
                conn.execute("SET max_temp_directory_size = ?", [max_temp_val])
                # F265X FIX: Only set temp_directory for file-backed DBs, not for :memory:
                # F266-U5-3 FIX: skip temp_directory in READ-ONLY mode (no writes needed)
                if self._temp_dir is not None and not _read_only_flag:
                    temp_dir_val = _validate_path_setting(self._temp_dir, 'temp_directory')
                    conn.execute("SET temp_directory = ?", [temp_dir_val])
                conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
                conn.execute("PRAGMA enable_progress_bar=false")
                conn.execute("PRAGMA enable_object_cache=false")
                # F265B: Enable DuckDB native WAL for concurrent read access + busy_timeout
                # O3: Explicit synchronous=NORMAL (DuckDB default, documented for clarity).
                #     NORMAL = WAL synced to disk on each transaction commit; SAFE on M1 SSD
                #     (no power-loss concern) — avoids per-commit fsync(2) overhead of FULL.
                # O3: wal_autocheckpoint=262144 (256MB in KB) — DuckDB auto-checkpoints when
                #     WAL exceeds this size, in addition to the 300s periodic _checkpoint_loop.
                #     256MB is well within M1 8GB RAM budget; keeps WAL bounded.
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=5000")  # 5s - fast fail, avoids 30s hangs on cold start
                    conn.execute("PRAGMA synchronous=NORMAL")  # O3: explicit WAL flush policy
                    conn.execute("PRAGMA wal_autocheckpoint=262144")  # O3: 256MB auto-checkpoint threshold
                except Exception as e:
                    logger.debug(f"[DUCKDB] WAL/busy_timeout config failed: {e!r}")
                # DuckDB 1.5.4 columnar compression & allocator tuning (M1 8GB):
                try:
                    conn.execute("SET write_buffer_row_group_memory_limit = ?",
                        [str(runtime.get("write_buffer_limit", "64MiB"))])
                    conn.execute("SET allocator_flush_threshold = ?",
                        [str(runtime.get("allocator_flush_threshold", "64MiB"))])
                    conn.execute("SET allocator_bulk_deallocation_flush_threshold = ?",
                        [str(runtime.get("allocator_bulk_dealloc_threshold", "256MiB"))])
                    conn.execute("SET enable_fsst_vectors = ?",
                        [str(runtime.get("enable_fsst_vectors", "true")).lower()])
                    conn.execute("SET temp_file_encryption = ?",
                        [str(runtime.get("temp_file_encryption", "false")).lower()])
                except Exception as e:
                    logger.debug(f"[DUCKDB] columnar/allocator tuning failed: {e}")
                # Sprint 5.6: DuckDB 1.x uses OS mmap automatically for file-backed DBs.
                # enable_object_cache=false skips DuckDB's internal cache, relying on OS page cache
                # + F_NOCACHE/MADV_FREE_REUSABLE (applied at init) for zero-copy reads.
                # DuckDB 2.x has explicit enable_mmap/mmap_size pragmas (not in 1.x).
                # F231 B: preserve_insertion_order = false applied to self._file_conn (persistent, line 1612).
                # F265D: DuckDB's conn.sql() and extract_statements() both fail on this multi-statement
                # schema string (Python source leaks into error messages).  Use regex-based statement splitting
                # to split the schema into individual SQL statements and execute them one by one.
                # F265E fix: strip SQL -- comments (may contain '"' chars that break DuckDB parser),
                # strip trailing triple-quotes, skip remaining docstring residue.
                # F266-U5-3 FIX: skip schema creation in READ-ONLY mode (DB already has schema)
                if not _read_only_flag:
                    _sql_clean = re.sub(r'^\s*--.*$', '', _SCHEMA_SQL, flags=re.MULTILINE)  # strip -- comments
                    _sql_clean = re.sub(r'^\s*#.*$', '', _sql_clean, flags=re.MULTILINE)  # F265X: also strip # comments
                    for _s in re.split(r';\s*(?=\w)', _sql_clean):
                        _s = _s.strip().rstrip('"')
                        if _s and '"' not in _s:
                            conn.execute(_s)
            finally:
                # F-LEAK-FIX: guarantee conn.close() on both normal exit and exception.
                # Mirrors the F285 pattern used in :memory: mode (L1736-1785).
                # Without this, any exception between L1608 and L1672 would leak the conn.
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            # Sprint 8RC: ALTER TABLE for retrokompatibilita (B.2)
            # Sprint 7H: Persistent file-backed connection for reuse across writes
            # F266-U5-3 FIX: use read_only flag for _file_conn too
            self._file_conn = duckdb.connect(str(self._db_path), read_only=_read_only_flag)
            # F273F: mark DuckDB mmap pages as reusable - reclaimable without writeback
            madv_free_reusable_on_path(self._db_path)
            apply_nocache_to_path(self._db_path)
            memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
            max_temp_val = _validate_duckdb_setting(self._max_temp, 'max_temp')
            self._file_conn.execute("SET memory_limit = ?", [memory_limit_val])
            self._file_conn.execute("SET max_temp_directory_size = ?", [max_temp_val])
            # F265X FIX: Only set temp_directory for file-backed DBs
            # F266-U5-3 FIX: skip temp_directory in READ-ONLY mode (no writes needed)
            if self._temp_dir is not None and not _read_only_flag:
                temp_dir_val = _validate_path_setting(self._temp_dir, 'temp_directory')
                self._file_conn.execute("SET temp_directory = ?", [temp_dir_val])
            self._file_conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
            self._file_conn.execute("PRAGMA enable_progress_bar=false")
            self._file_conn.execute("PRAGMA enable_object_cache=false")
            # F265B: Enable DuckDB native WAL for concurrent read access + busy_timeout
            # O3: Explicit synchronous=NORMAL + wal_autocheckpoint=262144 (see above).
            try:
                self._file_conn.execute("PRAGMA journal_mode=WAL")
                self._file_conn.execute("PRAGMA busy_timeout=5000")  # 5s - fast fail, prevents 30s hangs on cold start
                self._file_conn.execute("PRAGMA synchronous=NORMAL")  # O3: explicit WAL flush policy
                self._file_conn.execute("PRAGMA wal_autocheckpoint=262144")  # O3: 256MB auto-checkpoint threshold
            except Exception as e:
                logger.debug(f"[DUCKDB] WAL/busy_timeout config failed: {e!r}")
            # DuckDB 1.5.4 columnar compression & allocator tuning (M1 8GB):
            try:
                self._file_conn.execute("SET write_buffer_row_group_memory_limit = ?",
                    [str(runtime.get("write_buffer_limit", "64MiB"))])
                self._file_conn.execute("SET allocator_flush_threshold = ?",
                    [str(runtime.get("allocator_flush_threshold", "64MiB"))])
                self._file_conn.execute("SET allocator_bulk_deallocation_flush_threshold = ?",
                    [str(runtime.get("allocator_bulk_dealloc_threshold", "256MiB"))])
                self._file_conn.execute("SET enable_fsst_vectors = ?",
                    [str(runtime.get("enable_fsst_vectors", "true")).lower()])
                self._file_conn.execute("SET temp_file_encryption = ?",
                    [str(runtime.get("temp_file_encryption", "false")).lower()])
            except Exception as e:
                logger.debug(f"[DUCKDB] columnar/allocator tuning failed: {e}")
            # Sprint 5.6: DuckDB 1.x uses OS mmap automatically for file-backed DBs.
            # enable_object_cache=false skips DuckDB's internal cache, relying on OS page cache
            # + F_NOCACHE/MADV_FREE_REUSABLE (applied at init) for zero-copy reads.
            # DuckDB 2.x has explicit enable_mmap/mmap_size pragmas (not in 1.x).
            try:
                self._file_conn.execute("SET preserve_insertion_order = false")
            except Exception as e:
                logger.debug(f"[DUCKDB] preserve_insertion_order config failed: {e}")
            # P3-2: DuckDB uses force_checkpoint for crash safety.
            # DuckDB has built-in crash recovery; no WAL pragmas like PostgreSQL.
            # We run periodic force_checkpoint to ensure durability.
            try:
                self._file_conn.execute("PRAGMA force_checkpoint")
            except Exception as e:
                logger.debug(f"[DUCKDB] force_checkpoint failed: {e}")
        else:
            # MODE B: :memory: with PERSISTENT single connection
            # Sprint P1-1: HLEDAC_DUCKDB_RAMDISK_TEMP enables temp spill to RAM disk
            # for :memory: mode (faster than SSD temp, persists across queries).
            # F285: Wrap setup in try/except — on any error, close the orphaned conn
            # to prevent connection leak before re-raising.
            _conn = duckdb.connect(":memory:")
            try:
                memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
                _conn.execute("SET memory_limit = ?", [memory_limit_val])
                if _DUCKDB_RAMDISK_TEMP:
                    # RAM disk temp for :memory: mode — temp spills to RAM disk, not SSD
                    temp_dir_val = _validate_path_setting(Path(_DUCKDB_RAMDISK_TEMP), 'temp_directory')
                    _conn.execute("SET temp_directory = ?", [temp_dir_val])
                    _conn.execute("SET max_temp_directory_size = '4GB'")
                else:
                    _conn.execute("SET max_temp_directory_size = '0GB'")
                _conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
                _conn.execute("PRAGMA enable_progress_bar=false")
                _conn.execute("PRAGMA enable_object_cache=false")
                # DuckDB 1.5.4 columnar compression & allocator tuning (M1 8GB, :memory: mode):
                # Note: write_buffer/WAL/temp_encryption N/A for :memory: — skip silently.
                try:
                    _conn.execute("SET allocator_flush_threshold = ?",
                        [str(runtime.get("allocator_flush_threshold", "64MiB"))])
                    _conn.execute("SET allocator_bulk_deallocation_flush_threshold = ?",
                        [str(runtime.get("allocator_bulk_dealloc_threshold", "256MiB"))])
                    _conn.execute("SET enable_fsst_vectors = ?",
                        [str(runtime.get("enable_fsst_vectors", "true")).lower()])
                except Exception as e:
                    logger.debug(f"[DUCKDB] columnar/allocator tuning failed: {e}")
                # Sprint 5.6: DuckDB 1.x uses OS mmap automatically for file-backed DBs.
                # enable_object_cache=false skips DuckDB's internal cache, relying on OS page cache
                # + F_NOCACHE/MADV_FREE_REUSABLE for zero-copy reads.
                # DuckDB 2.x has explicit enable_mmap/mmap_size pragmas (not in 1.x).
                try:
                    _conn.execute("SET preserve_insertion_order = false")
                except Exception:
                    pass
                # F265D: Same schema-splitting approach for :memory: mode.
                # F265E fix: strip SQL -- comments (may contain '"' chars that break DuckDB parser),
                # strip trailing triple-quotes, skip remaining docstring residue.
                _sql_clean = re.sub(r'^\s*--.*$', '', _SCHEMA_SQL, flags=re.MULTILINE)  # strip -- comments
                _sql_clean = re.sub(r'^\s*#.*$', '', _sql_clean, flags=re.MULTILINE)  # F265X: also strip # comments
                for _s in re.split(r';\s*(?=\w)', _sql_clean):
                    _s = _s.strip().rstrip('"')
                    if _s and '"' not in _s:
                        _conn.execute(_s)
                # F-LEAK-FIX: assign ONLY after all setup succeeds — prevents a closed-conn
                # being left in self._persistent_conn if schema execution throws. The caller
                # checks _initialized and can re-initialize on AttributeError, but a
                # closed-conn in _persistent_conn would silently corrupt future queries.
                self._persistent_conn = _conn
            except Exception:
                _conn.close()
                raise

    # Sprint 8RC: Retrokompatibilita - add missing columns to old DB files (B.2)
    def _apply_schema_migrations(self) -> None:
        """
        ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.
        DuckDB does not have IF NOT EXISTS for ALTER, so we catch and ignore errors.

        Sprint F192F §2: findings_per_min -> findings_per_minute rename.
        Migration order matters - add new column first, then handle legacy column:
          1. Add findings_per_minute (new canonical name, matches sprint_scorecard)
          2. Add top_source_type (may already exist on very old DBs)
          3. Add synthesis_confidence (may already exist on very old DBs)
        Legacy findings_per_min column is retained but not written to (inserts use
        findings_per_minute). Queries read findings_per_minute which is populated
        by current insert logic.
        """
        if self._db_path is None:
            return  # :memory: mode - nothing to migrate
        duckdb = _get_duckdb()
        conn = duckdb.connect(str(self._db_path))
        try:
            # F273F: mark DuckDB mmap pages as reusable
            madv_free_reusable_on_path(self._db_path)
            apply_nocache_to_path(self._db_path)
            try:
                conn.execute(
                    "ALTER TABLE sprint_delta ADD COLUMN findings_per_minute REAL DEFAULT 0"
                )
            except Exception:
                pass  # noqa: BLE001  # column already exists (new schema via CREATE, or prior migration)
            try:
                conn.execute(
                    "ALTER TABLE sprint_delta ADD COLUMN top_source_type TEXT"
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "ALTER TABLE sprint_delta ADD COLUMN synthesis_confidence REAL DEFAULT 0"
                )
            except Exception:
                pass
        finally:
            # F-LEAK-FIX: guarantee conn.close() on both normal exit and exception.
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def ensure_target_profiles_schema(self) -> None:
        """
        Sprint F202K: Ensure target_profiles table exists in DuckDB.
        Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.
        Must be called after _init_connection (connection must exist).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_profiles (
                    target_id TEXT PRIMARY KEY,
                    first_seen DOUBLE,
                    last_seen DOUBLE,
                    cumulative_finding_count INTEGER,
                    entity_summary_json TEXT
                )
                """
            )
        except Exception:
            pass  # noqa: BLE001  # table already exists or connection not ready

    def ensure_target_memory_schema(self) -> None:
        """
        Sprint F204D: Ensure target_memory table exists in DuckDB.
        Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.
        Must be called after _init_connection (connection must exist).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_memory (
                    target_id TEXT PRIMARY KEY,
                    first_seen_ts DOUBLE,
                    last_seen_ts DOUBLE,
                    sprint_count INTEGER,
                    cumulative_finding_count INTEGER,
                    entity_facets_json TEXT,
                    exposure_facets_json TEXT,
                    pivot_facets_json TEXT,
                    confidence_drift_json TEXT,
                    updated_by_sprint_id TEXT,
                    updated_ts DOUBLE
                )
                """
            )
        except Exception:
            pass  # noqa: BLE001  # table already exists or connection not ready

    # --------------------------------------------------------------------------
    # Sprint F224A: DHT metadata ingestion
    # --------------------------------------------------------------------------

    async def async_ingest_dht_metadata(self, metadata: list[dict[str, Any]]) -> int:
        """
        Sprint F224A: Ingest DHT metadata from torrent discovery.

        Args:
            metadata: List of DHT metadata dicts with keys:
                - infohash: str (required, primary key)
                - name: str (optional)
                - files: list[str] (optional, stored as JSON)
                - size_bytes: int (optional)
                - first_seen: float (optional, defaults to now)
                - last_seen: float (optional, defaults to now)
                - peer_count: int (optional)
                - sources: list[str] (optional, stored as JSON)

        Returns:
            Number of records ingested
        """
        if not metadata:
            return 0

        if not self._initialized or self._closed: return 0  # noqa: E701

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()


        _ = asyncio.get_running_loop()  # ensure we're in async context
        now = _time.time()

        def _sync_ingest() -> int:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return 0

            conn.execute("""
                CREATE TABLE IF NOT EXISTS dht_metadata (
                    infohash TEXT PRIMARY KEY,
                    name TEXT,
                    files_json TEXT,
                    size_bytes BIGINT,
                    first_seen DOUBLE,
                    last_seen DOUBLE,
                    peer_count INT,
                    sources_json TEXT
                )
            """)

            count = 0
            for m in metadata:
                infohash = m.get("infohash", "")
                if not infohash:
                    continue

                files_json = _json_dumps_str(m.get("files")) if m.get("files") else None
                sources_json = _json_dumps_str(m.get("sources")) if m.get("sources") else None

                conn.execute("""
                    INSERT INTO dht_metadata (
                        infohash, name, files_json, size_bytes,
                        first_seen, last_seen, peer_count, sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(infohash) DO UPDATE SET
                        name = COALESCE(excluded.name, dht_metadata.name),
                        files_json = COALESCE(excluded.files_json, dht_metadata.files_json),
                        size_bytes = COALESCE(excluded.size_bytes, dht_metadata.size_bytes),
                        last_seen = excluded.last_seen,
                        peer_count = COALESCE(excluded.peer_count, dht_metadata.peer_count),
                        sources_json = COALESCE(excluded.sources_json, dht_metadata.sources_json)
                """, (
                    infohash,
                    m.get("name"),
                    files_json,
                    m.get("size_bytes"),
                    m.get("first_seen", now),
                    m.get("last_seen", now),
                    m.get("peer_count"),
                    sources_json,
                ))
                count += 1

            conn.commit()
            return count

        from utils.rayon_pool import run_in_io_pool_async

        return await run_in_io_pool_async(_sync_ingest)

    # --------------------------------------------------------------------------
    # Sprint F214: DuckDB Query Executor - SQL template & transaction consolidation
    # Consolidates SQL strings and transaction framing that were duplicated across
    # 38 _sync_* methods. One place to change a SQL template; all callers benefit.
    # --------------------------------------------------------------------------

    class _DuckDBQueryExecutor:
        """
        Private SQL construction and execution engine for DuckDBShadowStore.

        NOT part of the public API - exists solely to concentrate SQL string
        templates and transaction patterns that were previously copy-pasted
        across 38 _sync_* methods.

        Design:
        - All SQL templates are class-level string constants
        - Transaction framing (_begin/_commit/_rollback) is shared
        - Connection routing (MODE A file conn vs MODE B persistent conn) is shared
        - Arrow->dict conversion helpers are shared
        """

        # NOTE: __slots__ removed — DuckDBQueryExecutor is a per-store helper (not thousands
        # of instances). Slots + name-mangling caused mypy "no attribute" errors on
        # self._store / self._stmt_insert_finding since Python mangles these to
        # _DuckDBQueryExecutor__store etc. Direct attribute access without __slots__
        # resolves this without changing any runtime behavior.

        # -- SQL Templates --------------------------------------------------

        _SQL_INSERT_SHADOW_FINDING = (
            "INSERT INTO canonical_findings "
            "(id, query, source_type, confidence, ts, provenance_json) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO NOTHING"
        )
        _SQL_INSERT_SHADOW_RUN = (
            "INSERT INTO shadow_runs "
            "(run_id, started_at, ended_at, total_fds, rss_mb) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        _SQL_INSERT_SPRINT_DELTA = (
            "INSERT INTO sprint_delta VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        _SQL_INSERT_SOURCE_HIT = (
            "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)"
        )
        _SQL_INSERT_HYPOTHESIS_FEEDBACK = "INSERT INTO hypothesis_feedback"
        _SQL_INSERT_HYPOTHESIS_TRACKING = "INSERT OR REPLACE INTO hypothesis_tracking"
        _SQL_UPSERT_TARGET_PROFILE = "INSERT OR REPLACE INTO target_profiles"
        _SQL_SELECT_TARGET_PROFILE = "SELECT target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json"  # noqa: E501
        _SQL_SELECT_HYPOTHESIS_FEEDBACK = "SELECT id, target_id, pivot_type, ioc_type"
        _SQL_SELECT_SHADOW_FINDINGS = "SELECT id, query, source_type, confidence, ts, provenance_json"

        def __init__(self, store: DuckDBShadowStore) -> None:
            object.__setattr__(self, "_store", store)
            # Sprint F264: prepared-statement cache for the hot INSERT loop.
            # DuckDB PreparedStatement is connection-bound; we key the cache
            # by id(conn) so a reconnect transparently re-prepares.
            object.__setattr__(self, "_stmt_insert_finding", None)
            object.__setattr__(self, "_stmt_insert_finding_conn_id", None)

        # -- Connection routing ---------------------------------------------

        def _conn(self):
            """Return the active write connection (MODE A file or MODE B persistent).

            F265X-LAZY-FIX: triggers ensure_connected() if connection is not yet
            established in lazy mode. In lazy mode, __aenter__ sets _initialized=True
            but leaves _file_conn=None and _persistent_conn=None. First actual use
            via this property establishes the connection on-demand.
            """
            s = self._store  # type: ignore[attr-defined,assignment]
            if s._db_path:  # type: ignore[attr-defined]
                if s._file_conn is None:
                    s.ensure_connected()
                else:
                    s._prewarm_file_conn()
                return s._file_conn
            if s._persistent_conn is None:
                s.ensure_connected()
            return s._persistent_conn

        # -- Prepared statement cache (Sprint F264) -------------------------

        def _get_insert_stmt(self, conn: Any) -> Any:
            """
            Sprint F264: Lazy-init prepared INSERT statement for canonical_findings.

            Returns the cached prepared statement for `_SQL_INSERT_SHADOW_FINDING`
            if the underlying connection is unchanged. On reconnect the conn
            identity differs and the statement is transparently re-prepared.

            Fail-safe: if conn.prepare() raises, returns None and emits a
            one-shot warning. The caller MUST fall back to
            `conn.execute(self._SQL_INSERT_SHADOW_FINDING, params)` on None
            so the canonical write path stays alive (CLAUDE.md invariant #5).

            MUST be called on the worker thread (DuckDB conn is thread-affine).
            """
            conn_id = id(conn)
            cached = self._stmt_insert_finding  # type: ignore[attr-defined]
            if cached is not None and self._stmt_insert_finding_conn_id == conn_id:  # type: ignore[attr-defined]
                return cached
            try:
                stmt = conn.prepare(self._SQL_INSERT_SHADOW_FINDING)
                object.__setattr__(self, "_stmt_insert_finding", stmt)
                object.__setattr__(self, "_stmt_insert_finding_conn_id", conn_id)
                return stmt
            except Exception as e:
                # Fail-safe: prepared statement not available, caller falls back
                try:
                    logger.debug(f"[DUCKDB] prepare() failed, falling back to execute(): {e}")
                except Exception:
                    pass
                object.__setattr__(self, "_stmt_insert_finding", None)
                object.__setattr__(self, "_stmt_insert_finding_conn_id", None)
                return None

        def _invalidate_insert_stmt(self) -> None:
            """
            Sprint F264: Drop cached prepared statement. Call on close / reconnect.

            Safe to call from any thread; sets the cache to None so the next
            `_get_insert_stmt(conn)` re-prepares on the (possibly new) conn.
            """
            try:
                object.__setattr__(self, "_stmt_insert_finding", None)
                object.__setattr__(self, "_stmt_insert_finding_conn_id", None)
            except Exception:
                pass

        # -- Transaction framing ---------------------------------------------

        @staticmethod
        def _begin(conn) -> None:
            conn.execute("BEGIN TRANSACTION")

        @staticmethod
        def _commit(conn) -> None:
            conn.execute("COMMIT")

        @staticmethod
        def _rollback(conn) -> None:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass

        def _with_transaction(self, conn, fn):
            """
            Run fn(conn) inside an explicit transaction.
            Commits on success, rolls back on any exception.
            Returns fn's return value.
            """
            self._begin(conn)
            try:
                result = fn(conn)
                self._commit(conn)
                return result
            except Exception:
                self._rollback(conn)
                raise

        # -- Query methods - one method per _sync_* operation ---------------

        def insert_finding(
            self,
            finding_id: str,
            query: str,
            source_type: str,
            confidence: float,
            ts: float | None,
            provenance_json: str | None,
        ) -> bool:
            """Insert a single shadow finding. Returns True on success."""
            conn = self._conn()
            if conn is None:
                return False
            params = [finding_id, query, source_type, confidence, ts, provenance_json]
            try:
                # Sprint F264: prepared statement hot path; execute() fallback on prepare() failure
                stmt = self._get_insert_stmt(conn)
                def _do(c: Any) -> None:
                    if stmt is not None:
                        stmt.execute(params)
                    else:
                        c.execute(self._SQL_INSERT_SHADOW_FINDING, params)
                self._with_transaction(conn, _do)
                return True
            except Exception:
                return False

        def insert_findings_bulk(self, findings: list[dict[str, Any]]) -> int:
            """
            Bulk insert shadow findings. Returns number of successfully inserted records.
            MUST be called on the worker thread.
            """
            import logging as _logging

            _logger = _logging.getLogger(__name__)

            if not findings:
                return 0
            rows = [
                [r["id"], r["query"], r["source_type"], r["confidence"],
                 r.get("ts"), r.get("provenance_json")]
                for r in findings
            ]
            conn = self._conn()
            if conn is None:
                return 0
            try:
                # Sprint F264: prepared statement hot path; executemany() bulk path
                stmt = self._get_insert_stmt(conn)
                def _do(c: Any) -> None:
                    if stmt is not None:
                        stmt.executemany(rows)
                    else:
                        c.executemany(self._SQL_INSERT_SHADOW_FINDING, rows)
                self._with_transaction(conn, _do)
                return len(rows)
            except Exception as e:
                _logger.error(f"[D7] DuckDB bulk insert failed: {type(e).__name__}: {e}")
                return 0

        def insert_findings_bulk_as_tuples(self, rows: list[list]) -> int:
            """
            Bulk insert shadow findings from pre-built tuple rows.
            MUST be called on the worker thread.
            Returns number of successfully inserted records.
            """
            import logging as _logging

            _logger = _logging.getLogger(__name__)

            if not rows:
                return 0
            conn = self._conn()
            if conn is None:
                return 0
            try:
                # Sprint F264: prepared statement hot path; executemany() bulk path
                stmt = self._get_insert_stmt(conn)
                def _do(c: Any) -> None:
                    if stmt is not None:
                        stmt.executemany(rows)
                    else:
                        c.executemany(self._SQL_INSERT_SHADOW_FINDING, rows)
                self._with_transaction(conn, _do)
                return len(rows)
            except Exception as e:
                _logger.error(f"[D7] DuckDB bulk-as-tuples insert failed: {type(e).__name__}: {e}")
                return 0

        @contextmanager
        def _wal_delete_mode(self):
            """
            F275-2: Context manager — WAL→DELETE journal mode switch for bulk inserts.

            For bulk inserts (≥CHUNK_SIZE=2048), temporarily switch from WAL to DELETE
            journal mode. WAL mode costs 2× fsync per write (WAL write + DB write);
            DELETE costs 1× fsync. M1 SSD is safe for DELETE — single write is sufficient.

            The LMDB WAL layer is unaffected (separate journal).

            Restores WAL on exit regardless of success/failure.
            Fail-soft: any error is logged and swallowed — caller continues.
            """
            import logging as _logging

            _logger = _logging.getLogger(__name__)
            conn = self._conn()
            if conn is None:
                yield
                return

            original_mode: str | None = None
            try:
                result = conn.execute("PRAGMA journal_mode").fetchone()
                if result:
                    original_mode = str(result[0]).upper()
                # Switch to DELETE for bulk insert — faster for large batches
                if original_mode == "WAL":
                    conn.execute("PRAGMA journal_mode=DELETE")
                yield
            except Exception as _e:
                # Fail-soft: log and continue — bulk insert should still proceed
                _logger.debug(f"[F275-2] WAL→DELETE switch failed: {_e}")
                yield
            finally:
                # Always restore WAL on exit
                if original_mode == "WAL":
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                    except Exception as _e2:
                        _logger.debug(f"[F275-2] WAL restore failed: {_e2}")

        def insert_findings_bulk_arrow(self, table: Any) -> tuple[int, str | None]:
            """
            Sprint P0-4: Zero-copy Arrow bulk insert via DuckDB register() + INSERT...SELECT.

            MUST be called on the worker thread (thread-affine connection).
            Returns (row_count, error_type) on success: (n_rows, None).
            On any failure returns (0, error_type) where error_type is one of:
              "table_none"    - table is None
              "num_rows_err"  - failed to read num_rows
              "zero_rows"     - table has 0 rows
              "no_conn"       - could not acquire connection
              "pyarrow_build" - pa.Table.from_arrays failed (inside DuckDB register)
              "duckdb_error"  - DuckDB register/execute/unregister failed

            Why: executemany with N prepared stmt.execute() Python calls has ~3-5x the
            per-row Python overhead of one Arrow register() + one INSERT...SELECT.
            Provenance is already serialized in `table` (caller builds pa.array of JSON strs),
            so this method does no Python-level encoding.

            ON CONFLICT (id) DO NOTHING handles primary-key collisions silently.
            The secondary UNIQUE(query, source_type) constraint is NOT protected here;
            caller is expected to pre-dedupe or accept the failure (logged + return 0).
            """
            import logging as _logging

            _logger = _logging.getLogger(__name__)

            if table is None:
                return (0, "table_none")
            try:
                n_rows = int(table.num_rows)
            except Exception:
                return (0, "num_rows_err")
            if n_rows == 0:
                return (0, "zero_rows")
            conn = self._conn()
            if conn is None:
                return (0, "no_conn")
            # register() + INSERT...SELECT is the canonical zero-copy path.
            # DuckDB reads Arrow buffers via C++ Arrow C Data Interface - no Python copies.
            # F275-2: WAL→DELETE context manager reduces fsync overhead for bulk inserts.
            try:
                with self._wal_delete_mode():
                    # Use a per-call unique name to avoid collisions if two threads raced
                    # (single-worker executor makes that impossible, but defensive anyway).
                    import uuid as _uuid
                    reg_name = f"finding_arrow_batch_{_uuid.uuid4().hex[:12]}"
                    conn.register(reg_name, table)
                    try:
                        # F280: Handle UNIQUE constraints with INSERT...ON CONFLICT DO NOTHING.
                        # PRIMARY KEY (id) - silently ignore duplicates
                        # UNIQUE (query, source_type) - ISSUE-2 FIX: also handle this constraint.
                        # Arrow path was missing secondary UNIQUE protection, causing 50+ errors/sprint.
                        # Use two-phase insert: first try PK conflict, then try query+source_type conflict.
                        conn.execute(
                            f"INSERT INTO canonical_findings "
                            f"(id, query, source_type, confidence, ts, provenance_json) "
                            f"SELECT id, query, source_type, confidence, ts, provenance_json "
                            f"FROM {reg_name} "
                            f"ON CONFLICT (id) DO NOTHING"
                        )
                        # ISSUE-2 FIX: Also handle UNIQUE (query, source_type) constraint.
                        # This catches findings with same query+source_type but different IDs.
                        conn.execute(
                            f"INSERT INTO canonical_findings "
                            f"(id, query, source_type, confidence, ts, provenance_json) "
                            f"SELECT id, query, source_type, confidence, ts, provenance_json "
                            f"FROM {reg_name} "
                            f"ON CONFLICT (query, source_type) DO NOTHING"
                        )
                    finally:
                        # Always unregister - Arrow buffer ref-counted by DuckDB until then.
                        # Fail-soft unregister: leak is bounded by next register() overwriting
                        # the entry in DuckDB's catalog.
                        try:
                            conn.unregister(reg_name)
                        except Exception:
                            pass
                    return (n_rows, None)
            except Exception as e:
                _logger.error(
                    f"[P0-4 Arrow] DuckDB Arrow bulk insert failed: "
                    f"{type(e).__name__}: {e}"
                )
                return (0, "duckdb_error")

        def insert_run(
            self,
            run_id: str,
            started_at: float | None,
            ended_at: float | None,
            total_fds: int,
            rss_mb: int,
        ) -> bool:
            conn = self._conn()
            if conn is None:
                return False
            # DuckDB accepts ISO timestamp strings via CAST(? AS TIMESTAMP)
            started_iso = _dt.datetime.fromtimestamp(started_at).isoformat() if started_at is not None else None  # noqa: DTZ006
            ended_iso = _dt.datetime.fromtimestamp(ended_at).isoformat() if ended_at is not None else None  # noqa: DTZ006
            params = [run_id, started_iso, ended_iso, total_fds, rss_mb]
            cast_sql = (
                "INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb) "
                "VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?)"
            )
            try:
                self._with_transaction(conn, lambda c: c.execute(cast_sql, params))
                return True
            except Exception:
                return False

        def upsert_target_profile(self, profile) -> None:
            """Upsert target profile. Silently returns on failure."""
            conn = self._conn()
            if conn is None:
                return
            sql = (
                "INSERT OR REPLACE INTO target_profiles "
                "(target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json) "
                "VALUES (?, ?, ?, ?, ?)"
            )
            params = [
                profile.target_id,
                profile.first_seen,
                profile.last_seen,
                profile.cumulative_finding_count,
                profile.entity_summary_json,
            ]
            try:
                conn.execute(sql, params)
            except Exception:
                pass

        def get_target_profile(self, target_id: str):
            """Get target profile. Returns row tuple or None."""
            conn = self._conn()
            if conn is None:
                return None
            sql = (
                "SELECT target_id, first_seen, last_seen, cumulative_finding_count, entity_summary_json "
                "FROM target_profiles WHERE target_id = ?"
            )
            try:
                return conn.execute(sql, [target_id]).fetchone()
            except Exception:
                return None

        def query_findings(self, limit: int) -> list[dict]:
            """Select recent shadow findings. Returns list of dicts."""
            conn = self._conn()
            if conn is None:
                return []
            sql = (
                "SELECT id, query, source_type, confidence, ts, provenance_json "
                "FROM canonical_findings ORDER BY ts DESC LIMIT ?"
            )
            try:
                result = list(self._store.arrow_fetch_batch(conn, sql, [limit]))  # type: ignore[attr-defined]
                return [
                    {
                        "id": row[0],
                        "query": row[1],
                        "source_type": row[2],
                        "confidence": row[3],
                        "ts": row[4],
                        "provenance_json": row[5],
                    }
                    for row in result
                ]
            except Exception:
                return []

    # -- Instantiate executor lazily; _sync_* methods use self._qe when available --

    def _qe(self):
        """Lazy executor - created on first _sync_* access, shared for instance lifetime."""
        if not hasattr(self, "_query_executor"):
            object.__setattr__(self, "_query_executor", self._DuckDBQueryExecutor(self))
        return self._query_executor

    def _sync_insert_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        ts: float | None = None,
        provenance_json: str | None = None,
    ) -> bool:
        """Sync insert - MUST be called on the worker thread."""
        return self._qe().insert_finding(finding_id, query, source_type, confidence, ts, provenance_json)

    def _sync_insert_findings_bulk(
        self,
        findings: list[dict[str, Any]],
    ) -> int:
        """
        Sprint 7H: True bulk insert using executemany in explicit transaction.
        MUST be called on the worker thread.
        Returns number of successfully inserted records.
        """
        return self._qe().insert_findings_bulk(findings)

    def _sync_insert_run(
        self,
        run_id: str,
        started_at: float | None,
        ended_at: float | None,
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """Sync insert run - MUST be called on the worker thread."""
        return self._qe().insert_run(run_id, started_at, ended_at, total_fds, rss_mb)

    def _sync_query_findings(self, limit: int) -> list[dict[str, Any]]:
        """Sync query - MUST be called on the worker thread."""
        return self._qe().query_findings(limit)

    # -- Sprint F202K: target_profiles sync helpers ---------------------------

    def _sync_upsert_target_profile(self, profile: TargetProfileSummary) -> None:
        """Sync upsert - MUST be called on the worker thread."""
        self._qe().upsert_target_profile(profile)

    def _sync_get_target_profile(self, target_id: str) -> TargetProfileSummary | None:
        """Sync get - MUST be called on the worker thread. Returns None if not found."""
        result = self._qe().get_target_profile(target_id)
        if result is None:
            return None
        try:
            return TargetProfileSummary(
                target_id=result[0],
                first_seen=result[1],
                last_seen=result[2],
                cumulative_finding_count=result[3],
                entity_summary_json=result[4],
            )
        except Exception:
            return None

    # -- Sprint F203G: hypothesis_feedback sync helpers ------------------------

    def _sync_record_hypothesis_feedback(self, record: Any) -> bool:
        """
        Sprint F203G: Insert a single hypothesis_feedback record.

        Thread-safe: MUST be called on the duckdb_worker thread.
        Silently fails if store is closed or uninitialized.

        Returns True if inserted, False otherwise.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                """
                INSERT INTO hypothesis_feedback
                (id, target_id, pivot_type, ioc_type, produced_count, accepted_count, signal_value, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    record.id,
                    record.target_id,
                    record.pivot_type,
                    record.ioc_type,
                    record.produced_count,
                    record.accepted_count,
                    record.signal_value,
                    record.ts,
                ],
            )
            return True
        except Exception as e:
            logger.warning(f"[F206L] _sync_record_hypothesis_feedback failed for {record.id}: {e}")
            return False

    def _sync_get_hypothesis_feedback(
        self,
        target_id: str | None,
        limit: int,
    ) -> list[dict]:
        """
        Sprint F203G: Fetch hypothesis_feedback records ordered by ts DESC.

        Thread-safe: MUST be called on the duckdb_worker thread.

        Args:
            target_id: If provided, filter by target_id. If None, returns all.
            limit: Maximum number of records to return.

        Returns:
            List of dicts with keys: id, target_id, pivot_type, ioc_type,
            produced_count, accepted_count, signal_value, ts.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            if target_id:
                sql =                     """
                    SELECT id, target_id, pivot_type, ioc_type,
                           produced_count, accepted_count, signal_value, ts
                    FROM hypothesis_feedback
                    WHERE target_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = conn.execute(sql, [target_id, limit])
                result = list(self.arrow_fetch_batch(conn, sql, [target_id, limit]))
            else:
                sql =                     """
                    SELECT id, target_id, pivot_type, ioc_type,
                           produced_count, accepted_count, signal_value, ts
                    FROM hypothesis_feedback
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = conn.execute(sql, [limit])
                result = list(self.arrow_fetch_batch(conn, sql, [limit]))
            return [
                {
                    "id": r[0],
                    "target_id": r[1],
                    "pivot_type": r[2],
                    "ioc_type": r[3],
                    "produced_count": r[4],
                    "accepted_count": r[5],
                    "signal_value": r[6],
                    "ts": r[7],
                }
                for r in result
            ]
        except Exception:
            return []

    def _sync_get_previous_findings_for_target(
        self, target_id: str, before_sprint_id: str | None, limit: int
    ) -> list[dict]:
        """
        Sync query - MUST be called on the worker thread.
        Returns raw dict rows from canonical_findings filtered by target_id metadata.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            # Try to filter by target_id in payload_text JSON, or fall back to all findings
            # Sprint F202K: canonical_findings may have target_id in payload_text JSON
            if before_sprint_id:
                sql =                     """
                    SELECT finding_id, query, source_type, confidence, ts, provenance_json, payload_text
                    FROM canonical_findings
                    WHERE payload_text LIKE ?
                    AND sprint_id < ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = conn.execute(sql, [f'%"{target_id}"%', before_sprint_id, limit])
                result = list(self.arrow_fetch_batch(conn, sql, [f'%"{target_id}"%', before_sprint_id, limit]))
            else:
                sql =                     """
                    SELECT finding_id, query, source_type, confidence, ts, provenance_json, payload_text
                    FROM canonical_findings
                    WHERE payload_text LIKE ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = conn.execute(sql, [f'%"{target_id}"%', limit])
                result = list(self.arrow_fetch_batch(conn, sql, [f'%"{target_id}"%', limit]))
            return [
                {
                    "finding_id": row[0],
                    "query": row[1],
                    "source_type": row[2],
                    "confidence": row[3],
                    "ts": row[4],
                    "provenance_json": row[5],
                    "payload_text": row[6],
                }
                for row in result
            ]
        except Exception:
            # Fall back: try querying canonical_findings if canonical_findings fails
            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return []
                # canonical_findings doesn't have sprint_id or payload_text - return empty for target filter
                return []
            except Exception:
                return []

    # -- Sprint 8RC: sync helpers ---------------------------------------------

    def _sync_insert_sprint_delta(self, row: dict) -> bool:
        """
        Sync insert - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            if self._db_path:
                # MODE A: Use persistent _file_conn (always initialized before writes)
                self._prewarm_file_conn()
                self._file_conn.execute(
                    """
                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        row["sprint_id"], row["ts"], row.get("query"),
                        row.get("duration_s", 0), row.get("new_findings", 0),
                        row.get("dedup_hits", 0), row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0), row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_minute", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
                        row.get("findings_per_minute", 0),
                    ],
                )
            else:
                # MODE B: :memory: - use persistent single connection
                self._persistent_conn.execute(
                    """
                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        row["sprint_id"], row["ts"], row.get("query"),
                        row.get("duration_s", 0), row.get("new_findings", 0),
                        row.get("dedup_hits", 0), row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0), row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_minute", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
                        row.get("findings_per_minute", 0),
                    ],
                )
            return True
        except Exception:
            return False

    def _sync_insert_source_hit(
        self,
        sprint_id: str,
        ts: float,
        source_type: str,
        findings_count: int,
        ioc_count: int,
        hit_rate: float,
    ) -> bool:
        """Sync insert source hit - MUST be called on the worker thread."""
        try:
            if self._db_path:
                # MODE A: Use persistent _file_conn (always initialized before writes)
                self._prewarm_file_conn()
                self._file_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            else:
                # MODE B: :memory: - use persistent single connection
                self._persistent_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            return True
        except Exception:
            return False

    def _sync_query_sprint_trend(self, last_n: int) -> list[dict]:
        """Sync query - MUST be called on the worker thread. Uses persistent _file_conn."""
        try:
            if self._db_path:
                # MODE A: Use persistent _file_conn (always initialized before queries)
                self._prewarm_file_conn()
                sql =                     """
                    SELECT sprint_id, ts, new_findings, ioc_nodes,
                           findings_per_minute, synthesis_success, uma_peak_gib
                    FROM sprint_delta
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = self._file_conn.execute(sql, [last_n])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [last_n]))
            else:
                # MODE B: :memory: - use persistent single connection
                sql =                     """
                    SELECT sprint_id, ts, new_findings, ioc_nodes,
                           findings_per_minute, synthesis_success, uma_peak_gib
                    FROM sprint_delta
                    ORDER BY ts DESC
                    LIMIT ?
                    """
                result = self._persistent_conn.execute(sql, [last_n])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0], "ts": r[1],
                    "new_findings": r[2], "ioc_nodes": r[3],
                    "findings_per_minute": r[4],
                    "synthesis_success": bool(r[5]) if r[5] is not None else False,
                    "uma_peak_gib": r[6] or 0.0,
                }
                for r in result
            ]
        except Exception:
            return []

    def _sync_query_source_leaderboard(self, since_ts: float) -> list[dict]:
        """Sync query - MUST be called on the worker thread. Uses persistent _file_conn."""
        try:
            if self._db_path:
                # MODE A: Use persistent _file_conn (always initialized before queries)
                self._prewarm_file_conn()
                sql =                     """
                    SELECT source_type,
                           SUM(findings_count) as total_findings,
                           AVG(hit_rate) as avg_hit_rate,
                           COUNT(*) as sprint_appearances
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    LIMIT 10000
                    ORDER BY total_findings DESC
                    """
                result = self._file_conn.execute(sql, [since_ts])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [since_ts]))
            else:
                # MODE B: :memory: - use persistent single connection
                sql =                     """
                    SELECT source_type,
                           SUM(findings_count) as total_findings,
                           AVG(hit_rate) as avg_hit_rate,
                           COUNT(*) as sprint_appearances
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    LIMIT 10000
                    ORDER BY total_findings DESC
                    """
                result = self._persistent_conn.execute(sql, [since_ts])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [since_ts]))
            return [
                {
                    "source_type": r[0],
                    "total_findings": r[1] or 0,
                    "avg_hit_rate": r[2] or 0.0,
                    "sprint_appearances": r[3] or 0,
                }
                for r in result
            ]
        except Exception:
            return []

    def _sync_query_sprint_source_stats(self) -> list[dict]:
        """
        Sprint 8RC: Query source_type hit-rate stats for weight loading.
        Returns avg_hit_rate per source_type over the last 5 days.
        MUST be called on the worker thread.
        """
        cutoff = _time.time() - 5 * 86400
        try:
            if self._db_path:
                # MODE A: Use persistent _file_conn (always initialized before queries)
                self._prewarm_file_conn()
                sql =                     """
                    SELECT source_type, AVG(hit_rate) as avg_hit_rate
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    LIMIT 10000
                    """
                result = self._file_conn.execute(sql, [cutoff])
                result = list(self.arrow_fetch_batch(self._file_conn, sql, [cutoff]))
            else:
                # MODE B: :memory: - use persistent single connection
                sql =                     """
                    SELECT source_type, AVG(hit_rate) as avg_hit_rate
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    LIMIT 10000
                    """
                result = self._persistent_conn.execute(sql, [cutoff])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [cutoff]))
            return [
                {"source_type": r[0], "avg_hit_rate": r[1] or 0.0}
                for r in result
            ]
        except Exception:
            return []

    def _prewarm_file_conn(self) -> bool:
        """
        Sprint 7H: Amortize cold connect by issuing a no-op query.
        Called on first write to warm up _file_conn.
        Returns True if prewarm succeeded.
        """
        if self._file_conn is None:
            return False
        try:
            self._file_conn.execute("SELECT 1").fetchall()
            return True
        except Exception:
            return False

    def _sync_close_on_worker(self) -> None:
        """Close all connections - MUST be called on the worker thread."""
        # Sprint F264: invalidate prepared statement cache before closing conns.
        # DuckDB PreparedStatement is connection-bound; closing the conn makes
        # the cached statement unusable. Drop it so the next _qe() rebuilds.
        try:
            if hasattr(self, "_query_executor"):
                qe = self._qe()
                if qe is not None:
                    qe._invalidate_insert_stmt()
        except Exception:
            pass
        # Close persistent :memory: connection
        if self._persistent_conn is not None:
            try:
                self._persistent_conn.close()
            except Exception:
                pass
            self._persistent_conn = None
        # Close persistent file connection
        if self._file_conn is not None:
            try:
                self._file_conn.close()
            except Exception:
                pass
            self._file_conn = None
        # Sprint 8L + F216G: Close WALManager to release lock files for re-init
        if self._wal_manager is not None:
            try:
                self._wal_manager.close()
            except Exception:
                pass
            self._wal_manager = None

    # ------------------------------------------------------------------
    # Public sync API (from 8AO, kept for backward compat)
    # ------------------------------------------------------------------

    def _resolve_path(self) -> None:
        """
        Resolve _db_path and _temp_dir based on RAMDISK availability.

        RAMDISK_ACTIVE=True:  DUCKDB_STORE_ROOT / "shadow_analytics.duckdb", temp = RAMDISK_ROOT / "duckdb_tmp"
        RAMDISK_ACTIVE=False: DUCKDB_STORE_ROOT / "analytics.duckdb",     temp = None (no spill to SSD)

        Sprint F265B: All hot DuckDB data now uses DUCKDB_STORE_ROOT (co-located with LMDB_STORE_ROOT
        for atomic WAL operations). DUCKDB_STORE_ROOT defaults to SPRINT_STORE_ROOT.parent / "duckdb_store"
        which is ~/.hledac/duckdb_store — or RAMDISK-backed when HLEDAC_RAMDISK/HLEDAC_DUCKDB_STORE is set.
        """
        try:
            from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT
            if RAMDISK_ACTIVE:
                self._db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
                self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            else:
                self._db_path = DUCKDB_STORE_ROOT / "analytics.duckdb"
                self._temp_dir = None
        except Exception:
            # Degraded fallback - :memory: (session-only, no durability)
            self._db_path = None
            self._temp_dir = None

    def initialize(self) -> bool:
        """
        Initialize DuckDB connection synchronously (backward compat wrapper).

        For async code prefer async_initialize().
        """
        if self._closed:
            return False
        if self._initialized:
            return True

        # Sprint F259: Embedding dimension assertion - canonical MRL = 256d
        # Use _shims path (same as lancedb_store.py:351) — hledac.universal.core._mlx_embeddings does not exist
        from _shims.core_mlx_embeddings import MLXEmbeddingManager
        _EMBEDDING_DIM = getattr(MLXEmbeddingManager, 'EMBEDDING_DIM', 256)  # noqa: N806
        assert _EMBEDDING_DIM == 256, (
            f"Embedding dimension mismatch: MLXEmbeddingManager.EMBEDDING_DIM={_EMBEDDING_DIM}, expected 256 (MRL canonical)"  # noqa: E501
        )

        # Sprint 8D: Only resolve path if not already injected via __init__
        if self._db_path is None:
            self._resolve_path()

        try:
            # Run connection init on the worker thread
            fut = self._executor.submit(self._init_connection)
            fut.result()
            self._duckdb_module = _get_duckdb()
            self._initialized = True
            # Sprint 8L: sync initialize has no replay, so store is immediately ready
            self._startup_ready.set()
            return True
        except Exception:
            self._initialized = False
            return False

    def insert_shadow_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """Sync insert - backward compat. For async use async_record_shadow_finding()."""
        if not self._initialized or self._closed:
            return False
        try:
            fut = self._executor.submit(
                self._sync_insert_finding,
                finding_id, query, source_type, confidence,
            )
            return fut.result()
        except Exception:
            return False

    def insert_shadow_run(
        self,
        run_id: str,
        started_at: float,
        ended_at: float | None,
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """Sync insert - backward compat. For async use async_record_shadow_run()."""
        if not self._initialized or self._closed:
            return False
        try:
            fut = self._executor.submit(
                self._sync_insert_run,
                run_id, started_at, ended_at, total_fds, rss_mb,
            )
            return fut.result()
        except Exception:
            return False

    def query_recent_findings(self, limit: int = 10) -> list[dict[str, Any]]:
        """Sync query - backward compat. For async use async_query_recent_findings()."""
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_findings, limit)
            return fut.result()
        except Exception:
            return []

    def close(self) -> None:
        """
        Synchronous close — full cleanup without any event loop manipulation.

        F300S-FIX: close() now performs the FULL cleanup inline synchronously.
        No run_until_complete() on a running loop — which fails on Python 3.10+
        with RuntimeError: "cannot close event loop while running".
        close() IS the synchronous cleanup path — no event loop manipulation needed.

        Idempotent: safe to call multiple times.
        """
        # P3-2 / F300S-FIX: Cancel _checkpoint_task BEFORE _do_sync_close.
        # asyncio.Task.cancel() is thread-safe — safe to call from sync close
        # even if no explicit loop is running; the task's bound loop handles it.
        _ct = getattr(self, "_checkpoint_task", None)
        if _ct is not None:
            _ct.cancel()
            self._checkpoint_task = None

        self._do_sync_close(emergency=True)

    async def _do_async_close(self) -> None:
        """
        Async graph/semantic store close — properly awaits coroutines.

        Called only from aclose() path where an event loop is guaranteed to exist.
        Extracts and awaits all async close() calls that _do_sync_close skips
        when emergency=True.
        """
        gs = self._graph_store() if hasattr(self, "_DuckDBShadowStore__graph_store") else None
        if gs is not None:
            truth_graph = getattr(gs, "_truth_write_graph", None)
            if truth_graph is not None:
                try:
                    if callable(getattr(truth_graph, "close", None)):
                        result = truth_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:
                    pass
            ioc_graph = getattr(gs, "_ioc_graph", None)
            if ioc_graph is not None:
                try:
                    if callable(getattr(ioc_graph, "close", None)):
                        result = ioc_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:
                    pass
            stix_graph = getattr(gs, "_stix_graph", None)
            if stix_graph is not None:
                try:
                    if callable(getattr(stix_graph, "close", None)):
                        result = stix_graph.close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception:
                    pass

        if self._semantic_store is not None:
            try:
                result = self._semantic_store.close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

        # Sprint F272-U7: WALManager async cleanup — releases LMDB lock files
        # Registered via atexit in WALManager._ensure_atexit() as Python 3.14
        # weakref.finalize compat safety net.
        if self._wal_manager is not None:
            try:
                await self._wal_manager.aclose()
            except Exception:
                pass
            self._wal_manager = None

    def _do_sync_close(self, emergency: bool = False) -> None:
        """
        Synchronous full cleanup — called by both close() and aclose().

        Args:
            emergency: If True (close() path), skips async graph/semantic closes
                       since no event loop is guaranteed to be running.
                       Async cleanup is handled by _do_async_close() in aclose() path.
        """
        if self._closed:
            return

        # Detach finalizer
        if self._finalizer is not None:
            try:
                self._finalizer.detach()
            except Exception:
                pass

        self._closed = True
        self._initialized = False

        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:
            pass

        # Close DuckDB connections via worker thread
        try:
            f = self._executor.submit(self._sync_close_on_worker)
            f.result(timeout=5)
        except Exception:
            pass

        # Close graph slots — async methods handled in _do_async_close (aclose path)
        # Only flush buffers in sync path; skip async close() calls
        gs = self._graph_store() if hasattr(self, "_DuckDBShadowStore__graph_store") else None
        if gs is not None:
            truth_graph = getattr(gs, "_truth_write_graph", None)
            if truth_graph is not None:
                try:
                    if callable(getattr(truth_graph, "flush_buffers", None)):
                        truth_graph.flush_buffers()
                except Exception:
                    pass
            # Note: async graph.close() calls are skipped here — handled by _do_async_close

        # Sprint 8SB: close semantic store (sync part only)
        if self._semantic_store is not None:
            try:
                result = self._semantic_store.close()
                # Skip await in sync path — _do_async_close handles it
                if not asyncio.iscoroutine(result):
                    pass  # sync close, nothing to do
            except Exception:
                pass
            self._semantic_store = None

        # Sprint 8L: close WAL LMDB
        _wal = getattr(self, "_wal_lmdb", None)
        if _wal is not None:
            try:
                _wal.close()
            except Exception:
                pass
            self._wal_lmdb = None

        # Sprint 8AG: close DedupManager FIRST (on main thread), then its LMDB.
        # DedupManager.close() properly shuts down all sub-components:
        #   - BloomFilter (sync + clear reference)
        #   - _ioc_dedup_store (Rust mmap-backed, msync before None)
        #   - _dedup_lmdb (proper close with error guard)
        # This must run on the main thread BEFORE _sync_close_on_worker() finishes,
        # and BEFORE _shared_executor.shutdown(wait=False) potentially kills the
        # worker thread that would otherwise run it (F300S-FIX ordering invariant).
        if self._dedup_manager is not None:
            try:
                self._dedup_manager.close()
            except Exception:
                pass
            self._dedup_manager = None

        # Sprint 8AG: close dedup LMDB (only the store ref; DedupManager already closed its own)
        _dedup = getattr(self, "_dedup_lmdb", None)
        if _dedup is not None:
            try:
                _dedup.close()
            except Exception:
                pass
            self._dedup_lmdb = None

        # F11C-2: DuckDB WAL lock orphan recovery — enhanced
        # Clean up stale lock files from killed/crashed DuckDB/graph processes.
        # DuckDB creates: db_path + ".lock" (e.g. ioc_graph.duckdb.lock)
        try:
            from hledac.universal.graph.lock_manager import _is_lock_stale
            # F310-FIX: IOC_DB_PATH is not defined in this scope — use self._db_path
            _lock_db_path = str(self._db_path) if self._db_path else "memory"
            duckdb_lock_path = pathlib.Path(_lock_db_path + ".lock")
            if duckdb_lock_path.exists():
                is_stale, reason = _is_lock_stale(duckdb_lock_path, _lock_db_path)
                if is_stale:
                    duckdb_lock_path.unlink(missing_ok=True)
                    _logger.debug(f"[DUCKDB] Removed stale lock {duckdb_lock_path}: {reason}")
        except Exception:
            pass

        # Arrow metrics clear
        if hasattr(self, "_arrow_metrics") and self._arrow_metrics is not None:
            self._arrow_metrics.clear()

        # Adjust executor pool
        self._adjust_executor_pool()

    # ------------------------------------------------------------------
    # Public async API (new in 8AS)
    # ------------------------------------------------------------------

    async def async_initialize(
        self,
        replay_pending_limit: int | None = None,
        replay_timeout_s: float = 5.0,
    ) -> bool:
        """
        Async initialize - creates connection on the worker thread.

        Optional bounded startup replay runs after connection init, before the store
        accepts new activation writes. This integrates the Sprint 8H recovery API
        into the real init/startup path.

        Args:
            replay_pending_limit: Max number of pending markers to replay at startup.
                                 None or 0 = no startup replay.
            replay_timeout_s:    Wall-time budget for startup replay in seconds.
                                 If exceeded, replay is stopped and remaining
                                 markers are left for a future recovery run.

        Returns:
            True if initialization succeeded, False otherwise.
            Sidecar is safe to use even if this returns False.

        Boot barrier semantics (Sprint 8L):
            While startup replay is running, _startup_ready is NOT set.
            All async activation write methods check this and refuse to proceed
            until the barrier is lifted (or the store is closed).
            After bounded replay completes (success, limit, or timeout),
            _startup_ready is set and writes are accepted.

        NOTE: after aclose(), _closed is True and _initialized is False.
        We allow re-initialization by clearing _closed here.
        """
        if self._closed:
            # Sprint 8L: allow re-initialization after aclose()
            self._closed = False
        if self._initialized:
            return True

        # F11C-2: Proactive lock cleanup at startup — remove stale locks before
        # connecting. Handles DuckDB WAL locks (*.duckdb.lock) from crashed processes.
        try:
            self._cleanup_orphaned_locks()
        except Exception:
            pass

        # Sprint DuckDB Lazy Init (F265X): lazy mode defers actual connection.
        # When lazy=True, we skip connection init here — it happens on first query
        # via ensure_connected(). This saves ~1-2s from sprint boot when no
        # findings are produced (empty sprints are common in dev/test).
        if self._lazy:
            # Sprint 8D: Still resolve path so ensure_connected() has it
            if self._db_path is None:
                self._resolve_path()
            self._initialized = True
            self._startup_ready.set()
            return True

        # Legacy eager path (lazy=False): connect immediately
        # Sprint 8D: Only resolve path if not already injected via __init__
        if self._db_path is None:
            self._resolve_path()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init_connection)
            self._duckdb_module = _get_duckdb()
            self._initialized = True
        except Exception:
            self._initialized = False
            return False

        # Sprint 8L: Initialize WALManager (replaces direct _wal_lmdb creation)
        if self._wal_manager is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is not None:
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

        # Sprint F272: Lazy DedupManager init — load on first finding, not at sprint start.
        # Saves ~2s from sprint boot when dedup LMDB mmap files are cold.
        # DedupManager lazily initializes its sub-systems (_dedup_lmdb, BloomFilter,
        # mmap_ioc_dedup_store, semantic_cache) on first access in _ensure_dedup_manager().
        if self._dedup_manager is None:
            self._dedup_manager = DedupManager()

        # Sprint 8L: Bounded startup replay - only when limit is set and positive
        if replay_pending_limit:
            await self._bounded_startup_replay(
                replay_pending_limit=replay_pending_limit,
                replay_timeout_s=replay_timeout_s,
            )
            self._startup_replay_done = True

        # Sprint F202K: Ensure target_profiles schema exists
        self.ensure_target_profiles_schema()

        # P3-2: Start background checkpoint task for DuckDB native WAL.
        # Only active for file mode (_db_path is not None).
        if self._db_path is not None:
            self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())

        self._startup_ready.set()

        # F300S-FIX: Register weakref.finalize AFTER successful initialization.
        # Previously registered in aclose() which could fire before async_initialize
        # completed, causing "cannot create weak reference" errors. Now finalizer
        # is registered here at the END of successful init — self is fully
        # constructed and safe to reference.
        try:
            if hasattr(self, "_arrow_metrics") and self._arrow_metrics is not None:
                _finalizer = weakref.finalize(
                    self,
                    lambda _metrics: _metrics.clear() if _metrics is not None else None,
                    self._arrow_metrics,
                )
                _finalizer.atexit = False
        except (TypeError, AttributeError, Exception):
            pass

        return True

    async def async_initialize_schema(self) -> bool:
        """
        F275: Explicit schema initialization - creates/touches the DB file and
        runs CREATE TABLE IF NOT EXISTS for all canonical tables.

        Safe to call multiple times (idempotent). Does NOT run full
        async_initialize() - no WAL replay, no DedupManager init.
        This is the minimal init path for the zero-findings case.

        Returns True if schema is ready, False on error.
        """
        if self._closed:
            return False
        try:
            # Ensure path is resolved
            if self._db_path is None:
                self._resolve_path()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._init_connection)
            self._initialized = True
            self._startup_ready.set()
            return True
        except Exception:
            return False

    async def async_record_shadow_run(
        self,
        run_id: str,
        started_at: float,
        ended_at: float | None,
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """
        Insert a run record into the shadow analytics store.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_insert_run,
                run_id, started_at, ended_at, total_fds, rss_mb,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Sprint DuckDB Lazy Init (F265X): ensure_connected()
    # ------------------------------------------------------------------

    def ensure_connected(self) -> None:
        """
        Lazy connection init — called on first actual query.

        When lazy=True (default): defers actual DuckDB connection to this method.
        When lazy=False: no-op (already connected via async_initialize).

        This is the on-demand bootstrap that enables ~0s sprint boot with no findings.
        All async write methods call ensure_connected() before their run_in_executor.

        Barrier semantics (Sprint DuckDB Lazy Init F265X):
            In lazy mode, _startup_ready is cleared here BEFORE connecting, then
            set again AFTER connecting. This ensures writes always wait for the
            connection to be ready (no spurious proceeds before connection exists).
        """
        if not self._lazy:
            return  # Legacy eager mode — already connected via async_initialize
        if self._persistent_conn is not None or self._file_conn is not None:
            return  # Already connected

        # Sprint 8D: resolve path if not already injected
        if self._db_path is None:
            self._resolve_path()

        # Barrier: clear _startup_ready BEFORE connecting so writes block
        # during connection init (prevents spurious proceed when _initialized=True
        # but _file_conn/_persistent_conn are still None in lazy mode)
        self._startup_ready.clear()

        # Synchronous connect on worker thread
        self._init_connection()
        self._duckdb_module = _get_duckdb()

        # Barrier: connection is ready — allow writes to proceed
        self._startup_ready.set()

    # ------------------------------------------------------------------
    # Async Context Manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> DuckDBShadowStore:
        """
        Async context manager entry - initializes the store.

        Usage:
            async with DuckDBShadowStore() as store:
                await store.async_insert_finding(...)
            # aclose() called automatically on exit

        Sprint DuckDB Lazy Init (F265X): when lazy=True (default), this returns
        immediately without connecting. Connection is deferred to the first actual
        query via ensure_connected(). This saves ~1-2s from sprint boot.
        """
        if self._lazy:
            # Lazy mode: skip async_initialize(), just ensure basic setup
            if self._db_path is None:
                self._resolve_path()
            self._initialized = True
            self._startup_ready.set()
            return self
        # Legacy eager path
        await self.async_initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Async context manager exit - cleans up the store.
        Idempotent: safe to call even if already closed.
        """
        await self.aclose()

    async def async_record_shadow_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Insert a single finding into the shadow analytics store.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_insert_finding,
                finding_id, query, source_type, confidence,
            )
            return True
        except Exception:
            return False

    async def async_record_shadow_findings_batch(
        self,
        findings: list[dict[str, Any]],
        max_batch_size: int = 500,
    ) -> int:
        """
        Sprint 7H: True bulk insert for shadow findings using executemany in explicit transaction.
        Each chunk is at most max_batch_size records.
        Returns the number of successfully inserted records.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.

        Used by analytics_hook.shadow_record_finding() for batched shadow analytics writes.
        """
        if not self._initialized or self._closed:
            return 0

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        total_inserted = 0

        for i in range(0, len(findings), max_batch_size):
            chunk = findings[i : i + max_batch_size]
            try:
                count = await loop.run_in_executor(
                    self._executor,
                    self._sync_insert_findings_bulk,
                    chunk,
                )
                total_inserted += count
            except Exception:
                break  # stop on first chunk failure

        return total_inserted

    async def async_query_recent_findings(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Query recent findings ordered by timestamp descending.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_findings,
                limit,
            )
        except Exception:
            return []

    async def aiter_recent_findings(
        self,
        batch_size: int = 500,
        sprint_id_filter: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        STORAGE-FIX-3: Streaming iterator for recent findings.

        M1 EIGHTGB memory benefit: yields Arrow batches via async_query_arrow_batches
        instead of loading all rows into a list. For N=10K rows: -300-400 MB peak
        RAM vs async_query_recent_findings() (which uses .fetchall()).

        Default order: ts DESC. WHERE clause optionally scoped to a sprint query.

        Args:
            batch_size: rows per Arrow batch (default 500).
            sprint_id_filter: optional LIKE pattern on query column.

        Yields:
            dict per row, ts DESC, batched via Arrow.
        """
        if not self._initialized or self._closed:
            return
        if sprint_id_filter is None:
            sql = (
                "SELECT id, query, source_type, confidence, ts, provenance_json "
                "FROM canonical_findings "
                "ORDER BY ts DESC"
            )
            params: list[Any] | None = None
        else:
            sql = (
                "SELECT id, query, source_type, confidence, ts, provenance_json "
                "FROM canonical_findings "
                "WHERE query LIKE ? "
                "ORDER BY ts DESC"
            )
            params = [f"%{sprint_id_filter}%"]
        try:
            async for batch in self.async_query_arrow_batches(
                sql, params, batch_size=batch_size
            ):
                # M5: Zero-copy Arrow→Python via Polars iter_rows (5-10× faster than to_pylist).
                # Polars native ARM64: .iter_rows(named=True) is zero-copy from Arrow buffers.
                try:
                    import polars as _pl
                    pdf = _pl.from_arrow(batch)
                    rows_iter = pdf.iter_rows(named=True)
                except ImportError:
                    # Fallback: pyarrow zero-copy batch iteration (no pandas conversion)
                    cols = batch.columns
                    names = batch.schema.names
                    rows_iter = (dict(zip(names, (cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i] for j in range(len(cols))), strict=False)) for i in range(batch.num_rows))
                for row in rows_iter:
                    yield row
        except Exception:
            return

    async def async_query_arrow_batches(
        self,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 2048,
    ) -> AsyncIterator[Any]:
        """
        F231 C / Thread2b: Streaming Arrow batch query - yields batches without loading full result.

        Uses DuckDB's `fetch_record_batch()` when available (DuckDB 1.2+ with Arrow
        extension), falls back to `to_arrow_reader()`, and finally to a warn-telemetry
        chunked fetch if neither is available.

        Thread2b: batch_size increased from 500 → 2048 for better Arrow throughput
        (~16 MB peak per batch on payload_text-heavy queries, M1 8GB safe).

        IMPORTANT: The DuckDB connection and all iteration stays on the worker thread.
        The async generator bridge ensures no live reader crosses into the event loop.
        Caller must consume the generator fully or cancel to avoid resource leaks.

        Args:
            sql: SQL query to execute.
            params: Optional query parameters (parameterized for SQL injection safety).
            batch_size: Rows per batch (default 500, aligned with batch chunking invariant).

        Yields:
            pyarrow.RecordBatch (or dict rows in fallback mode) - each batch is bounded.
        """
        if not self._initialized or self._closed:
            return

        # F265X-LAZY-FIX: ensure connection before nested _sync_fetch_batches.
        # In lazy mode, __aenter__ sets _initialized=True but connections are None.
        # Nested _sync_fetch_batches accesses _file_conn directly (bypasses _qe()._conn).
        self.ensure_connected()

        def _sync_fetch_batches() -> Iterator[Any]:
            # O(1) cached check - pyarrow not required for DuckDB basic operations
            if not _check_pyarrow_available():
                return  # pyarrow not available - fallback path below

            # Resolve which connection to use (worker-thread-only)
            if self._db_path:
                conn = self._file_conn
            else:
                conn = self._persistent_conn

            if conn is None:
                return

            try:
                # Try fetch_record_batch first (DuckDB 1.2+ Arrow extension)
                result = conn.execute(sql, params or [])
                if hasattr(result, 'fetch_record_batch'):
                    reader = result.fetch_record_batch(batch_size)
                    while True:
                        try:
                            batch = reader.read_next_batch()
                            if batch is None:
                                break
                            yield batch
                        except StopIteration:
                            break
                    return

                # Try to_arrow_reader as second option
                if hasattr(result, 'to_arrow_reader'):
                    reader = result.to_arrow_reader(rows_per_batch=batch_size)
                    while True:
                        try:
                            batch = reader.read_next_batch()
                            if batch is None:
                                break
                            yield batch
                        except StopIteration:
                            break
                    return

            except Exception:
                pass

            # FALLBACK: chunked fetch with warning telemetry
            # Use duckdb's native fetch - does NOT stream full result into memory
            # if using fetchmany-style iteration, but loses Arrow benefits
            import os as _os
            _os.environ.setdefault("HLEDAC_WARN_ARROW_FALLBACK", "1")
            result = conn.execute(sql, params or [])
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                # Convert to dict rows as fallback representation
                yield rows

        def _sync_iter_wrapper() -> Iterator[Any]:
            yield from _sync_fetch_batches()

        loop = asyncio.get_running_loop()
        iterator = await loop.run_in_executor(self._executor, _sync_iter_wrapper)

        # Async bridge: pull from worker-thread iterator without blocking event loop
        while True:
            try:
                batch = await loop.run_in_executor(self._executor, next, iterator)
                yield batch
            except StopIteration:
                break
            except Exception:
                break

    def arrow_fetch_batch(
        self,
        conn: Any,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 2048,
    ) -> Iterator[list[tuple]]:
        """
        Sync streaming fetch - bounded memory alternative to `fetchall()`.

        Yields lists of row tuples, each chunk bounded by `batch_size` (default
        2048, tuned for M1 EIGHTGB UMA - ~16 MB peak per batch on payload_text-heavy
        queries). Replaces `conn.execute(sql, params).fetchall()` patterns so
        the full result set never materializes in RAM at once.

        Two paths, fail-soft throughout:
          1. Arrow zero-copy (DuckDB 1.2+ + pyarrow) - `result.fetch_record_batch(n)`.
          2. fetchmany fallback (universal, no extra deps) - `result.fetchmany(n)`.

        MUST be called on the duckdb worker thread (i.e. from inside `_sync_*`
        methods that run via `self._executor.submit`). Generator stays on the
        worker thread; caller materializes into the final list only after
        full consumption.

        Yields:
            list[tuple] - each chunk bounded; empty generator if conn is None
            or execute fails.
        """
        if conn is None:
            return
        try:
            result = conn.execute(sql, params or [])
        except Exception:
            return

        # Path 1: Arrow zero-copy (DuckDB 1.2+ with pyarrow installed)
        if hasattr(result, "fetch_record_batch"):
            try:
                reader = result.fetch_record_batch(batch_size)
                while True:
                    try:
                        batch = reader.read_next_batch()
                    except StopIteration:
                        break
                    if batch is None:
                        break
                    try:
                        # M5: Zero-copy Arrow→Python via Polars iter_rows (5-10× faster).
                        # Polars ARM64 native: .from_arrow() is zero-copy, .iter_rows() is 5-10× faster than to_pylist().
                        try:
                            import polars as _pl
                            pdf = _pl.from_arrow(batch)
                            yield from pdf.iter_rows(named=False)
                        except ImportError:
                            # Fallback: Arrow batch → zero-copy tuples without to_pylist()
                            cols = batch.columns
                            nrows = batch.num_rows
                            ncols = len(cols)
                            yield [
                                tuple(
                                    cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                    for j in range(ncols)
                                )
                                for i in range(nrows)
                            ]
                    except Exception:
                        # Fallback: columnar unpickling for exotic types
                        cols = batch.columns
                        nrows = batch.num_rows
                        ncols = len(cols)
                        yield [
                            tuple(
                                cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                for j in range(ncols)
                            )
                            for i in range(nrows)
                        ]
                return
            except Exception:
                pass  # noqa: BLE001  # fall through to fetchmany

        # Path 2: fetchmany fallback (no pyarrow required)
        try:
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                yield list(rows)
        except Exception:
            return

    async def async_healthcheck(self) -> bool:
        """
        Quick health check - attempts a zero-cost query.

        Returns True if the store is healthy and responsive.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()


        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_query_findings,
                1,
            )
            return True  # query succeeded
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Sprint 8RC: sprint_delta + source_hit_log async API
    # ------------------------------------------------------------------

    async def async_record_sprint_delta(self, row: dict) -> bool:
        """
        Insert a sprint_delta record.

        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_insert_sprint_delta,
                row,
            )
        except Exception:
            return False

    async def async_record_source_hit(
        self,
        sprint_id: str,
        ts: float,
        source_type: str,
        findings_count: int,
        ioc_count: int,
        hit_rate: float,
    ) -> bool:
        """
        Insert a source_hit_log record.

        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_insert_source_hit,
                sprint_id, ts, source_type, findings_count, ioc_count, hit_rate,
            )
        except Exception:
            return False

    async def async_query_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        Return trend data for the last N sprints, ordered by ts DESC.
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_sprint_trend,
                last_n,
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Practical read seam: sprint-scoped findings
    # ------------------------------------------------------------------

    async def async_query_recent_findings_by_sprint(
        self,
        sprint_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Return the most recent accepted findings for a given sprint,
        ordered by ts DESC. Bounded, read-only, fail-soft.

        Use for: export synthesis input, sprint retrospektivu,
        scheduler priority scoring.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_recent_findings_by_sprint,
                sprint_id,
                limit,
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # F251A: Offline text query memory seed extraction
    # ------------------------------------------------------------------

    async def async_query_findings_by_text(
        self,
        like_pattern: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        F251A: Read canonical_findings rows matching a text/keyword pattern.
        Used by run_runtime_pivot_prelude() for offline memory seed extraction
        when a text query has no direct IOC seeds.

        Args:
            like_pattern: Keyword to search in query/title/payload_text.
            limit: Max rows to return (default 1000).

        Returns:
            list[dict] with keys: id, query, source_type, title, payload_text, ts.
            Fail-soft: returns [] on any error.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_findings_by_text,
                like_pattern,
                limit,
            )
        except Exception:
            return []

    async def async_query_findings_by_keywords(
        self,
        keywords: list[str],
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        P1-2: Read canonical_findings rows matching ANY of the given keywords.
        Uses OR across keywords so "ransomware breach" matches findings
        containing either "ransomware" OR "breach".
        Used by run_runtime_pivot_prelude() for cross-sprint seed extraction
        when the full query string has no direct match.

        Args:
            keywords: List of keywords to search in query/title/payload_text.
            limit: Max rows to return (default 1000).

        Returns:
            list[dict] with keys: id, query, source_type, title, payload_text, ts.
            Fail-soft: returns [] on any error.
        """
        if not self._initialized or self._closed:
            return []
        if not keywords:
            return []
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_findings_by_keywords,
                keywords,
                limit,
            )
        except Exception:
            return []

    def _sync_query_findings_by_keywords(
        self,
        keywords: list[str],
        limit: int,
    ) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            # Build OR conditions for each keyword
            conditions = " OR ".join(["(query LIKE ? OR title LIKE ? OR payload_text LIKE ?)"] * len(keywords))
            pattern = [f"%{kw}%" for kw in keywords for _ in range(3)]
            sql = f"""
                SELECT id, query, source_type, title, payload_text, ts
                FROM canonical_findings
                WHERE {conditions}
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = list(self.arrow_fetch_batch(conn, sql, pattern + [limit]))
            if not rows:
                return []
            return [
                {
                    "id": r[0],
                    "query": r[1],
                    "source_type": r[2],
                    "title": r[3] or "",
                    "payload_text": r[4] or "",
                    "ts": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []

    def _sync_query_findings_by_text(
        self,
        like_pattern: str,
        limit: int,
    ) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            pattern = f"%{like_pattern}%"
            sql =                 """
                SELECT id, query, source_type, title, payload_text, ts
                FROM canonical_findings
                WHERE query LIKE ?
                   OR title LIKE ?
                   OR payload_text LIKE ?
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [pattern, pattern, pattern, limit])
            rows = list(self.arrow_fetch_batch(conn, sql, [pattern, pattern, pattern, limit]))
            if not rows:
                return []
            return [
                {
                    "id": r[0],
                    "query": r[1],
                    "source_type": r[2],
                    "title": r[3] or "",
                    "payload_text": r[4] or "",
                    "ts": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []

    def _sync_query_recent_findings_by_sprint(
        self,
        sprint_id: str,
        limit: int,
    ) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT id, query, source_type, confidence, ts
                FROM canonical_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [sprint_id, sprint_id, limit])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit]))
            if not rows:
                return []
            return [
                {
                    "id": r[0],
                    "query": r[1],
                    "source_type": r[2],
                    "confidence": r[3],
                    "ts": r[4],
                }
                for r in rows
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Practical read seam: IOC-ish pivot candidates from sprint findings
    # ------------------------------------------------------------------

    async def async_query_top_entities_by_sprint(
        self,
        sprint_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Return entity-like pivot candidates extracted from finding queries
        and provenance for the given sprint. Looks for domain/IP/url-like
        tokens in query text. Bounded, read-only, fail-soft.

        Use for: synthesis pivot hints, entity correlation candidates,
        export enrichment. Does NOT require global_entities table.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_top_entities_by_sprint,
                sprint_id,
                limit,
            )
        except Exception:
            return []

    def _sync_query_top_entities_by_sprint(
        self,
        sprint_id: str,
        limit: int,
    ) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        import re

        DOMAIN_RE = re.compile(  # noqa: N806
            r"(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
        )
        IP_RE = re.compile(  # noqa: N806
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        )

        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT id, query, source_type, ts
                FROM canonical_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [sprint_id, sprint_id, limit * 4])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit * 4]))
            if not rows:
                return []

            candidates: dict[str, dict] = {}
            for row in rows:
                text = f"{row[1]} {row[0]}"
                for m in DOMAIN_RE.finditer(text):
                    domain = m.group().lower()
                    if domain not in candidates:
                        candidates[domain] = {
                            "entity_value": domain,
                            "entity_type": "domain",
                            "occurrences": 0,
                            "last_seen_ts": 0.0,
                        }
                    candidates[domain]["occurrences"] += 1
                    candidates[domain]["last_seen_ts"] = max(
                        candidates[domain]["last_seen_ts"], row[3]
                    )
                for m in IP_RE.finditer(text):
                    ip = m.group()
                    if ip not in candidates:
                        candidates[ip] = {
                            "entity_value": ip,
                            "entity_type": "ip",
                            "occurrences": 0,
                            "last_seen_ts": 0.0,
                        }
                    candidates[ip]["occurrences"] += 1
                    candidates[ip]["last_seen_ts"] = max(
                        candidates[ip]["last_seen_ts"], row[3]
                    )

            sorted_candidates = sorted(
                candidates.values(),
                key=lambda x: (x["occurrences"], x["last_seen_ts"]),
                reverse=True,
            )
            return sorted_candidates[:limit]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Practical read seam: sprint IOC summary
    # ------------------------------------------------------------------

    async def async_query_sprint_ioc_summary(
        self,
        sprint_id: str,
    ) -> dict:
        """
        Return a lightweight IOC summary for a sprint:
        total findings, unique source_types, avg confidence,
        time span (first->last ts). Bounded, read-only, fail-soft.

        Use for: scheduler decision support, synthesis quality signals,
        sprint retrospektivu.
        """
        if not self._initialized or self._closed:
            return {}

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_sprint_ioc_summary,
                sprint_id,
            )
        except Exception:
            return {}

    def _sync_query_sprint_ioc_summary(
        self,
        sprint_id: str,
    ) -> dict:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_findings,
                    COUNT(DISTINCT source_type) as unique_sources,
                    AVG(confidence) as avg_confidence,
                    MIN(ts) as first_ts,
                    MAX(ts) as last_ts
                FROM canonical_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                """,
                [sprint_id, sprint_id],
            ).fetchone()
            if row is None or row[0] == 0:
                return {}
            return {
                "sprint_id": sprint_id,
                "total_findings": row[0] or 0,
                "unique_sources": row[1] or 0,
                "avg_confidence": round(row[2] or 0.0, 3),
                "first_ts": row[3] or 0.0,
                "last_ts": row[4] or 0.0,
                "span_seconds": (row[4] or 0.0) - (row[3] or 0.0),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Practical read seam: top sources by findings count for a sprint
    # ------------------------------------------------------------------

    async def async_query_top_sources_by_sprint(
        self,
        sprint_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """
        Return source_type breakdown (findings count, avg confidence)
        for a given sprint. Bounded, read-only, fail-soft.

        Use for: sprint retrospektivu, source yield analysis,
        scheduler source weighting decisions.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_top_sources_by_sprint,
                sprint_id,
                limit,
            )
        except Exception:
            return []

    def _sync_query_top_sources_by_sprint(
        self,
        sprint_id: str,
        limit: int,
    ) -> list[dict]:
        """Sync - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT
                    source_type,
                    COUNT(*) as findings_count,
                    AVG(confidence) as avg_confidence
                FROM canonical_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                GROUP BY source_type
                ORDER BY findings_count DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [sprint_id, sprint_id, limit])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id, sprint_id, limit]))
            return [
                {
                    "source_type": r[0],
                    "findings_count": r[1] or 0,
                    "avg_confidence": round(r[2] or 0.0, 3),
                }
                for r in rows
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint 8TA B.3: Research Scorecard
    # ------------------------------------------------------------------

    async def upsert_scorecard(self, data: dict) -> bool:
        """
        Sprint 8TA B.3: Insert or replace a sprint_scorecard record.

        data contains: sprint_id, ts, findings_per_minute, ioc_density,
        semantic_novelty, source_yield_json (orjson), phase_timings_json (orjson),
        outlines_used, accepted_findings, ioc_nodes
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_upsert_scorecard,
                data,
            )
        except Exception:
            return False

    def _sync_upsert_scorecard(self, data: dict) -> bool:
        """Sync upsert scorecard - MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                """
                INSERT OR REPLACE INTO sprint_scorecard VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    data["sprint_id"],
                    data["ts"],
                    data.get("findings_per_minute", 0),
                    data.get("ioc_density", 0),
                    data.get("semantic_novelty", 1.0),
                    data.get("source_yield_json", "{}"),
                    data.get("phase_timings_json", "{}"),
                    data.get("outlines_used", False),
                    data.get("accepted_findings", 0),
                    data.get("ioc_nodes", 0),
                ],
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Sprint 8UC B.2: research_episodes - sprint episode recall
    # ------------------------------------------------------------------

    async def submit_findings(
        self,
        findings: list[CanonicalFinding],
    ) -> None:
        """
        Fire-and-forget async write — delegates directly to async_ingest_findings_batch().

        async_ingest_findings_batch() has its own built-in Arrow pipeline batching
        (1024-item chunks, 4-slot pipeline queue, concurrent WAL+DuckDB via asyncio.gather),
        so no separate coalescer layer is needed.

        NOTE: findings list must not be mutated after this call returns.
        Caller is responsible for ensuring this.

        Returns: None (fire-and-forget async write).
        """
        if not findings:
            return
        asyncio.create_task(self._submit_findings_bg(findings))

    async def _submit_findings_bg(self, findings: list[CanonicalFinding]) -> None:
        """Background task — runs submit_findings() logic without blocking the caller."""
        try:
            await self.async_ingest_findings_batch(findings)
        except Exception:
            pass

    async def drain_and_get_accepted(
        self,
        findings: list[CanonicalFinding],
    ) -> list[Any]:
        """
        Direct ingest — calls async_ingest_findings_batch() and returns results.

        This is the canonical write path for call sites that need the
        accepted/stored counts from async_ingest_findings_batch().

        Args:
            findings: findings to ingest.

        Returns:
            List of FindingQualityDecision/ActivationResult objects,
            one per finding submitted. Empty list on failure.
        """
        if not findings:
            return []
        try:
            return await self.async_ingest_findings_batch(findings)
        except Exception:
            return []

    # --------------------------------------------------------------------------
    # Sprint F202A: Evidence Envelope helpers
    # --------------------------------------------------------------------------

    def _execute_in_thread_sync(self, fn) -> Any:
        """
        Execute synchronous function on the duckdb executor and return its result.

        MUST be called from the main thread. The callable fn runs on the
        single-worker ThreadPoolExecutor and blocks until complete.

        Returns:
            The return value of fn(), or None if the executor raised an exception.

        NOTE: This is a synchronous helper. Async callers MUST await the result:
            result = await loop.run_in_executor(self._executor, self._execute_in_thread_sync, fn)
        For direct async wrappers, prefer loop.run_in_executor() directly.
        """
        try:
            f = self._executor.submit(fn)
            return f.result()
        except Exception:
            return None

    async def upsert_episode(self, data: dict) -> None:
        """Sprint 8UC B.2: Zapsat sprint epizodu pro budoucí recall."""
        import time as _t


        def _sync():
            conn = self._persistent_conn
            if conn is None:
                return
            conn.execute(
                """INSERT OR REPLACE INTO research_episodes
                   (episode_id, sprint_id, query, summary, top_findings,
                    ioc_clusters, source_yield, synthesis_engine, duration_s, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    data.get("sprint_id", ""),
                    data.get("sprint_id", ""),
                    data.get("query", ""),
                    data.get("summary", "")[:300] if data.get("summary") else "",
                    _json_dumps_str(data.get("top_findings", [])),
                    _json_dumps_str(data.get("ioc_clusters", [])),
                    _json_dumps_str(data.get("source_yield", {})),
                    data.get("synthesis_engine", "unknown"),
                    float(data.get("duration_s", 0.0)),
                    float(data.get("ts", _t.time())),
                ],
            )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _sync)

    async def recall_episodes(
        self,
        query_embedding: list[float] | None,
        limit: int = 5,
    ) -> list[dict]:
        """Sprint 8UC B.2: Načíst posledních `limit` epizod (recency-based)."""
        def _sync():
            conn = self._persistent_conn
            if conn is None:
                return []
            try:
                sql =                     """SELECT sprint_id, query, summary, top_findings, source_yield, ts
                       FROM research_episodes
                       ORDER BY ts DESC
                       LIMIT ?"""
                rows = conn.execute(sql, [limit])
                rows = list(self.arrow_fetch_batch(conn, sql, [limit]))
                if not rows:
                    return []
                cols = ["sprint_id", "query", "summary", "top_findings", "source_yield", "ts"]
                return [dict(zip(cols, r, strict=False)) for r in rows]
            except Exception:
                return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, _sync)

    # ------------------------------------------------------------------
    # Sprint F204D: target_memory - cross-sprint target state accumulation
    # ------------------------------------------------------------------

    async def upsert_target_memory(self, memory: TargetMemory) -> bool:
        """
        Sprint F204D: Upsert a TargetMemory record into DuckDB.

        Serializes facets as JSON TEXT columns. Uses INSERT OR REPLACE.
        GHOST_INVARIANT: runs on duckdb executor via run_in_executor.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_upsert_target_memory,
                memory,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    def _sync_upsert_target_memory(self, memory: TargetMemory) -> bool:
        """Sync upsert target memory - MUST be called on worker thread."""
        import logging as _logging

        _logger = _logging.getLogger(__name__)

        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return False
            conn.execute(
                """
                INSERT OR REPLACE INTO target_memory
                (target_id, first_seen_ts, last_seen_ts, sprint_count,
                 cumulative_finding_count, entity_facets_json, exposure_facets_json,
                 pivot_facets_json, confidence_drift_json, updated_by_sprint_id,
                 updated_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    memory.target_id,
                    memory.first_seen_ts,
                    memory.last_seen_ts,
                    memory.sprint_count,
                    memory.cumulative_finding_count,
                    _json_dumps_str(memory.entity_facets),
                    _json_dumps_str(memory.exposure_facets),
                    _json_dumps_str(memory.pivot_facets),
                    _json_dumps_str(memory.confidence_drift),
                    memory.updated_by_sprint_id,
                    _time.time(),
                ],
            )
            return True
        except Exception as _exc:
            _logger.warning(f"_sync_upsert_target_memory failed: {_exc}")
            return False

    async def read_target_memory(self, target_id: str) -> TargetMemory | None:
        """
        Sprint F204D: Read a TargetMemory record by target_id.
        Returns None if not found. Deserializes JSON TEXT columns.
        """
        if not self._initialized or self._closed:
            return None

        def _sync() -> TargetMemory | None:

            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return None
                sql =                     """
                    SELECT target_id, first_seen_ts, last_seen_ts, sprint_count,
                           cumulative_finding_count, entity_facets_json,
                           exposure_facets_json, pivot_facets_json,
                           confidence_drift_json, updated_by_sprint_id, updated_ts
                    FROM target_memory
                    WHERE target_id = ?
                    """
                rows = conn.execute(sql, [target_id])
                rows = list(self.arrow_fetch_batch(conn, sql, [target_id]))
                if not rows:
                    return None
                r = rows[0]
                return TargetMemory(
                    target_id=r[0],
                    first_seen_ts=r[1],
                    last_seen_ts=r[2],
                    sprint_count=r[3],
                    cumulative_finding_count=r[4],
                    entity_facets=_json_loads_flexible(r[5]),
                    exposure_facets=_json_loads_flexible(r[6]),
                    pivot_facets=_json_loads_flexible(r[7]),
                    confidence_drift=_json_loads_flexible(r[8]),
                    updated_by_sprint_id=r[9],
                )
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, _sync)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Sprint 8TA B.4: ghost_global cross-sprint entity accumulation (SQLite-backed)
    # ------------------------------------------------------------------

    async def upsert_global_entities(
        self,
        entities: list[tuple[str, str, float]],
    ) -> int:
        """
        Sprint F4.1 fix: Upsert entities into ghost_global store using DuckDB.

        Path: ~/.hledac/ghost_global.duckdb (DuckDB with native WAL mode)
        Engine: DuckDB with access_mode='automatic' (native file locking)
        Schema: global_entities(entity_value TEXT PK, entity_type TEXT,
                sprint_count INT, last_seen DOUBLE, confidence_cumulative REAL)
        INSERT OR REPLACE with MAX(confidence) semantics.
        Returns: int (count of upserted entities).
        """
        if not entities:
            return 0
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_upsert_global_entities,
                entities,
            )
        except Exception:
            return 0

    def _sync_upsert_global_entities(
        self,
        entities: list[tuple[str, str, float]],
    ) -> int:
        """Sync upsert global entities - MUST be called on worker thread.

        Uses DuckDB's built-in file locking via access_mode='automatic'.
        DuckDB handles crash-safety internally - no external lock file needed.
        """
        from pathlib import Path

        import duckdb

        ghost_home = Path.home() / ".hledac"
        ghost_home.mkdir(parents=True, exist_ok=True)
        db_path = ghost_home / "ghost_global.duckdb"

        # F4.1 fix: Use DuckDB with native file locking instead of sqlite3 + fcntl.
        # DuckDB's WAL mode handles crash recovery internally.
        # No external lock file needed - DuckDB manages locking itself.
        conn = duckdb.connect(str(db_path), access_mode="automatic")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS global_entities (
                    entity_value TEXT PRIMARY KEY,
                    entity_type TEXT,
                    sprint_count INT DEFAULT 0,
                    last_seen DOUBLE,
                    confidence_cumulative REAL DEFAULT 0
                )
                """
            )
            now = _time.time()

            # Sprint 8TA B.4: Batch upsert - eliminates N+1 query pattern
            # Use INSERT ... ON CONFLICT DO UPDATE for atomic increment/max semantics
            conn.execute(
                """
                INSERT INTO global_entities
                    (entity_value, entity_type, sprint_count, last_seen, confidence_cumulative)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(entity_value) DO UPDATE SET
                    sprint_count = sprint_count + 1,
                    confidence_cumulative = MAX(confidence_cumulative, excluded.confidence_cumulative),
                    last_seen = excluded.last_seen
                """,
                [(e, t, now, c) for e, t, c in entities],
            )
            conn.commit()
            return len(entities)
        finally:
            conn.close()

    async def async_query_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        Return top sources by hit rate for the last N days.
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_source_leaderboard,
                _time.time() - days * 86400,
            )
        except Exception:
            return []

    async def async_query_sprint_source_stats(self) -> list[dict]:
        """
        Return per-source-type avg_hit_rate over the last 5 days.
        Used by SprintScheduler.load_source_weights().
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_query_sprint_source_stats,
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint 8RC: sync convenience wrappers (called from SprintScheduler)
    # ------------------------------------------------------------------

    # Sprint F183D §2: These sync wrappers are DEPRECATED.
    # export_sprint() now calls async_query_sprint_trend() / async_query_source_leaderboard()
    # directly (primary path). These wrappers remain as COMPAT fallback for:
    #   - tests/probe_8rc/ (intentional sync test consumers)
    #   - Any sync-only callers not yet migrated to async context
    # REMOVAL CONDITION: all callers migrated to async read seams

    def get_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        DEPRECATED (Sprint F183D) - use async_query_sprint_trend() instead.

        Convenience sync wrapper - returns last N sprints ordered by ts DESC.
        For use in sync contexts (e.g., report printing).

        REMOVAL CONDITION: all callers migrated to async read seams.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_sprint_trend, last_n)
            return fut.result()
        except Exception:
            return []

    def get_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        DEPRECATED (Sprint F183D) - use async_query_source_leaderboard() instead.

        Convenience sync wrapper - returns top sources by hit rate.
        For use in sync contexts (e.g., report printing).

        REMOVAL CONDITION: all callers migrated to async read seams.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(
                self._sync_query_source_leaderboard,
                _time.time() - days * 86400,
            )
            return fut.result()
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint Delta Read Seams - output / delta truth (Sprint F150H)
    # ------------------------------------------------------------------

    def get_sprint_scorecard_trend(self, last_n: int = 6) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper - returns last N scorecards
        ordered by ts DESC. Covers ioc_density, semantic_novelty, accepted_findings,
        findings_per_minute, and outlines_used. Fail-soft, bounded.

        Use for: yield trend reporting, retrospektiva, sprint-to-sprint
        quality comparison without ad-hoc SQL.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_scorecard_trend, last_n)
            return fut.result()
        except Exception:
            return []

    def _sync_query_scorecard_trend(self, last_n: int) -> list[dict]:
        """Sync - MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT sprint_id, ts, findings_per_minute, ioc_density,
                       semantic_novelty, outlines_used, accepted_findings, ioc_nodes
                FROM sprint_scorecard
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "findings_per_minute": r[2] or 0.0,
                    "ioc_density": r[3] or 0.0,
                    "semantic_novelty": r[4] or 1.0,
                    "outlines_used": bool(r[5]) if r[5] is not None else False,
                    "accepted_findings": r[6] or 0,
                    "ioc_nodes": r[7] or 0,
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_sprint_delta_comparison(self, current_sprint_id: str, lookback: int = 4) -> dict:
        """
        Sprint F150H: Compare current sprint against the average of the last
        `lookback` sprints. Returns a delta dict with absolute values of
        current sprint and the delta vs the rolling mean of prior sprints.

        Covers: new_findings, ioc_new_this_sprint, dedup_hits, findings_per_minute,
        uma_peak_gib, synthesis_confidence.

        Use for: "how is this sprint tracking vs history" without ad-hoc SQL.
        Fail-soft - returns empty/near-zero fields on any error.
        """
        if not self._initialized or self._closed:
            return {}
        try:
            fut = self._executor.submit(
                self._sync_query_delta_comparison, current_sprint_id, lookback
            )
            return fut.result()
        except Exception:
            return {}

    def _sync_query_delta_comparison(
        self, current_sprint_id: str, lookback: int
    ) -> dict:
        """
        Sync - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}

            sql =                 """
                SELECT new_findings, ioc_new_this_sprint, dedup_hits,
                       findings_per_minute, uma_peak_gib, synthesis_confidence
                FROM sprint_delta
                WHERE sprint_id = ?
                """
            current_rows = conn.execute(sql, [current_sprint_id])
            current_rows = list(self.arrow_fetch_batch(conn, sql, [current_sprint_id]))

            if not current_rows:
                return {}

            sql =                 """
                SELECT new_findings, ioc_new_this_sprint, dedup_hits,
                       findings_per_minute, uma_peak_gib, synthesis_confidence
                FROM sprint_delta
                WHERE sprint_id != ?
                ORDER BY ts DESC
                LIMIT ?
                """
            prior_rows = conn.execute(sql, [current_sprint_id, lookback])
            prior_rows = list(self.arrow_fetch_batch(conn, sql, [current_sprint_id, lookback]))

            cur = current_rows[0]
            fields = [
                "new_findings", "ioc_new_this_sprint", "dedup_hits",
                "findings_per_minute", "uma_peak_gib", "synthesis_confidence",
            ]
            cur_vals = [cur[0] or 0, cur[1] or 0, cur[2] or 0,
                        cur[3] or 0.0, cur[4] or 0.0, cur[5] or 0.0]

            if prior_rows:
                prior_avg = [0.0] * len(fields)
                for pr in prior_rows:
                    for idx, _f in enumerate(fields):
                        v = pr[idx] or (0 if idx < 3 else 0.0)
                        prior_avg[idx] += v / len(prior_rows)
                deltas = {
                    f: round(cur_vals[i] - prior_avg[i], 4)
                    for i, f in enumerate(fields)
                }
            else:
                deltas = dict.fromkeys(fields, 0.0)

            return {
                "sprint_id": current_sprint_id,
                "current": {f: cur_vals[i] for i, f in enumerate(fields)},
                "vs_prior_mean": deltas,
            }
        except Exception:
            return {}

    def get_source_mix_trend(self, days: int = 14) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper - returns source_type distribution
        broken down by sprint for the last `days`. Each row contains
        source_type, sprint_id, total_findings, and hit_rate.

        Use for: source mix reporting - is web growing vs feed vs document,
        and is each source getting more productive over time.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(
                self._sync_query_source_mix_trend,
                _time.time() - days * 86400,
            )
            return fut.result()
        except Exception:
            return []

    def _sync_query_source_mix_trend(self, since_ts: float) -> list[dict]:
        """Sync - MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT source_type, sprint_id,
                       SUM(findings_count) as total_findings,
                       AVG(hit_rate) as avg_hit_rate,
                       SUM(ioc_count) as total_iocs
                FROM source_hit_log
                WHERE ts > ?
                GROUP BY source_type, sprint_id
                LIMIT 10000
                ORDER BY sprint_id DESC, total_findings DESC
                """
            rows = conn.execute(sql, [since_ts])
            rows = list(self.arrow_fetch_batch(conn, sql, [since_ts]))
            return [
                {
                    "source_type": r[0],
                    "sprint_id": r[1],
                    "total_findings": r[2] or 0,
                    "avg_hit_rate": r[3] or 0.0,
                    "total_iocs": r[4] or 0,
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_yield_trend(self, last_n: int = 8) -> list[dict]:
        """
        Sprint F150H: Derived yield metrics per sprint - new_findings / duration_s,
        dedup_hits ratio (dedup_hits / new_findings), and ioc_rate
        (ioc_new_this_sprint / new_findings). Returns last N sprints.

        Use for: "are we getting better at extracting unique findings from sources"
        - track yield improvement or degradation across sprints.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_yield_trend, last_n)
            return fut.result()
        except Exception:
            return []

    def _sync_query_yield_trend(self, last_n: int) -> list[dict]:
        """Sync - MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT sprint_id, ts, new_findings, duration_s,
                       dedup_hits, ioc_new_this_sprint
                FROM sprint_delta
                ORDER BY ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            result = []
            for r in rows:
                new_findings = r[2] or 0
                duration_s = r[3] or 0.0
                dedup_hits = r[4] or 0
                ioc_new = r[5] or 0
                result.append({
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": new_findings,
                    "duration_s": duration_s,
                    "yield_per_min": round(new_findings / max(duration_s / 60, 0.001), 4),
                    "dedup_ratio": round(dedup_hits / max(new_findings, 1), 4),
                    "ioc_rate": round(ioc_new / max(new_findings, 1), 4),
                })
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint F150I: Variant B - high-value sprint ranking seams
    # Reads: sprint_delta + sprint_scorecard (NO new tables, NO write-back)
    # ------------------------------------------------------------------

    def get_high_value_sprint_ranking(self, last_n: int = 8) -> list[dict]:
        """
        Sprint F150I: Rank last N sprints by a composite value score.
        Composite = accepted_findings * semantic_novelty / max(duration_s, 1).
        Higher is better. Returns sprint_id, composite_score, and component fields.

        Use for: "which sprints delivered the most value per second".
        Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(
                self._sync_query_high_value_ranking, last_n
            )
            return fut.result()
        except Exception:
            return []

    def _sync_query_high_value_ranking(self, last_n: int) -> list[dict]:
        """Sync - MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT
                    d.sprint_id,
                    d.ts,
                    d.new_findings,
                    d.duration_s,
                    c.accepted_findings,
                    c.semantic_novelty,
                    d.synthesis_confidence,
                    ROUND(
                        CAST(c.accepted_findings AS REAL)
                        * COALESCE(c.semantic_novelty, 1.0)
                        / MAX(d.duration_s, 1.0),
                        4
                    ) AS composite_score
                FROM sprint_delta d
                LEFT JOIN sprint_scorecard c ON d.sprint_id = c.sprint_id
                ORDER BY d.ts DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "accepted_findings": r[4] or 0,
                    "semantic_novelty": r[5] or 1.0,
                    "synthesis_confidence": r[6] or 0.0,
                    "composite_score": r[7] or 0.0,
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_scorecard_consistency_check(self, sprint_id: str) -> dict:
        """
        Sprint F150I: Compare findings_per_minute from sprint_scorecard vs
        findings_per_minute from sprint_delta for the same sprint.
        Returns ratio and warns if divergence > 2x.

        Use for: detecting scorecard / delta sync issues.
        Fail-soft - returns empty dict on any error.

        NOTE: As of Sprint F192F, both tables use findings_per_minute (renamed from
        findings_per_min in sprint_delta). The JOIN now compares two same-named columns.
        """
        if not self._initialized or self._closed:
            return {}
        try:
            fut = self._executor.submit(
                self._sync_query_consistency_check, sprint_id
            )
            return fut.result()
        except Exception:
            return {}

    def _sync_query_consistency_check(self, sprint_id: str) -> dict:
        """
        Sync - MUST be called on the worker thread.

        Sprint F192F §2: both sprint_scorecard and sprint_delta now use findings_per_minute.
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}
            # LIMIT 1000: consistency check only needs recent rows.
            # ORDERED BY rowid DESC: get most recent entries.
            sql =                 """
                SELECT
                    c.sprint_id,
                    c.findings_per_minute,
                    COALESCE(d.findings_per_minute, 0) AS delta_fpm,
                    d.new_findings,
                    d.duration_s
                FROM sprint_scorecard c
                LEFT JOIN sprint_delta d ON c.sprint_id = d.sprint_id
                WHERE c.sprint_id = ?
                ORDER BY rowid DESC
                LIMIT 1000
                """
            rows = conn.execute(sql, [sprint_id])
            rows = list(self.arrow_fetch_batch(conn, sql, [sprint_id]))
            if not rows:
                return {}
            r = rows[0]
            scorecard_fpm = r[1] or 0.0
            delta_fpm = r[2] or 0.0
            ratio = round(delta_fpm / max(scorecard_fpm, 0.001), 4)
            return {
                "sprint_id": r[0],
                "scorecard_fpm": scorecard_fpm,
                "delta_fpm": delta_fpm,
                "ratio": ratio,
                "diverges": ratio > 2.0 or ratio < 0.5,
                "new_findings": r[3] or 0,
                "duration_s": r[4] or 0.0,
            }
        except Exception:
            return {}

    def get_recent_best_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Sprint F150I: Return the top N sprints by yield (new_findings / duration_s).
        Reads from sprint_delta. Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(
                self._sync_query_best_sprints, last_n
            )
            return fut.result()
        except Exception:
            return []

    def _sync_query_best_sprints(self, last_n: int) -> list[dict]:
        """
        Sync - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT sprint_id, ts, new_findings, duration_s,
                       findings_per_minute, synthesis_confidence,
                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)
                       AS yield_per_min
                FROM sprint_delta
                WHERE new_findings > 0 AND duration_s > 0
                ORDER BY yield_per_min DESC
                LIMIT ?
                """
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "findings_per_minute": r[4] or 0.0,
                    "synthesis_confidence": r[5] or 0.0,
                    "yield_per_min": r[6] or 0.0,
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_recent_worst_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Sprint F150I: Return the bottom N sprints by yield (new_findings / duration_s).
        Only sprints with new_findings > 0 are included (exclude zero-yield noise).
        Reads from sprint_delta. Fail-soft, bounded.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(
                self._sync_query_worst_sprints, last_n
            )
            return fut.result()
        except Exception:
            return []

    def _sync_query_worst_sprints(self, last_n: int) -> list[dict]:
        """
        Sync - MUST be called on the worker thread.

        Sprint F192F §2: uses findings_per_minute (matches sprint_scorecard naming).
        """
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            sql =                 """
                SELECT sprint_id, ts, new_findings, duration_s,
                       findings_per_minute, synthesis_confidence,
                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)
                       AS yield_per_min
                FROM sprint_delta
                WHERE new_findings > 0 AND duration_s > 0
                ORDER BY yield_per_min ASC
                LIMIT ?
                """
            rows = conn.execute(sql, [last_n])
            rows = list(self.arrow_fetch_batch(conn, sql, [last_n]))
            return [
                {
                    "sprint_id": r[0],
                    "ts": r[1],
                    "new_findings": r[2] or 0,
                    "duration_s": r[3] or 0.0,
                    "findings_per_min": r[4] or 0.0,
                    "synthesis_confidence": r[5] or 0.0,
                    "yield_per_min": r[6] or 0.0,
                }
                for r in rows
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Public Activation API - WAL-first async wrappers (Sprint 8B)
    # ------------------------------------------------------------------

    async def async_record_activation(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> ActivationResult:
        """
        Record a single finding with WAL-first semantics.

        Order: LMDB WAL first -> DuckDB second.
        If LMDB OK but DuckDB FAIL -> desync=True, LMDB record preserved.
        Caller always receives an ActivationResult.

        Args:
            finding_id:  Unique finding identifier
            query:       Research query text
            source_type: Source type (e.g., "web", "document", "synthetic")
            confidence:  Confidence score [0.0, 1.0]

        Returns:
            ActivationResult with typed fields (never a raw dict)
        """
        if not self._initialized or self._closed:
            return ActivationResult(
                finding_id=finding_id,
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding_id}",
                desync=False,
                error="store closed or not initialized",
                accepted=False,
            )

        # Sprint 8L: Boot barrier - wait for startup replay to complete before accepting writes
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return ActivationResult(
                    finding_id=finding_id,
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{finding_id}",
                    desync=False,
                    error="startup replay timeout",
                    accepted=False,
                )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._activation_record_finding,
                finding_id, query, source_type, confidence,
            )
            # result is a dict from _activation_record_finding - normalize to ActivationResult
            desync = bool(result.get("lmdb_success") and result.get("duckdb_success") is False)
            return ActivationResult(
                finding_id=str(finding_id),
                lmdb_success=bool(result.get("lmdb_success")),
                duckdb_success=result.get("duckdb_success"),
                lmdb_key=f"finding:{finding_id}",
                desync=desync,
                error=None,
                accepted=True,
            )
        except Exception as e:
            return ActivationResult(
                finding_id=str(finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding_id}",
                desync=False,
                error=str(e),
                accepted=False,
            )

    async def async_record_activation_batch(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ActivationResult]:
        """
        Record multiple findings with WAL-first semantics.

        Order: LMDB WAL first (via put_many) -> DuckDB second (chunked batch).
        Returns one ActivationResult per finding in input order.
        Partial failure: if LMDB OK but DuckDB fails for some/all,
        those entries get desync=True.

        Args:
            findings: List of dicts, each must contain:
                      id, query, source_type, confidence

        Returns:
            list[ActivationResult] - one per finding
        """
        if not self._initialized or self._closed:
            return [
                ActivationResult(
                    finding_id=str(f.get("id", "")),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.get('id', '')}",
                    desync=False,
                    error="store closed or not initialized",
                )
                for f in findings
            ]

        # Sprint 8L: Boot barrier - wait for startup replay to complete before accepting writes
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return [
                    ActivationResult(
                        finding_id=str(f.get("id", "")),
                        lmdb_success=False,
                        duckdb_success=None,
                        lmdb_key=f"finding:{f.get('id', '')}",
                        desync=False,
                        error="startup replay timeout",
                    )
                    for f in findings
                ]

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._activation_record_findings_batch,
                findings,
            )
            # Map batch result back to per-finding ActivationResults
            lmdb_ok = result.get("lmdb_success", False)
            duckdb_ok = result.get("duckdb_success", False)
            failed_ids = set(result.get("failed_ids", []))
            [f.get("id") for f in findings if f.get("id")]

            results: list[ActivationResult] = []
            for f in findings:
                fid = f.get("id", "")
                lmdb_success = lmdb_ok and fid not in failed_ids
                duckdb_success = None
                if lmdb_ok:
                    duckdb_success = duckdb_ok and fid not in failed_ids
                desync = bool(lmdb_ok and duckdb_success is False)
                results.append(ActivationResult(
                    finding_id=str(fid),
                    lmdb_success=lmdb_success,
                    duckdb_success=duckdb_success,
                    lmdb_key=f"finding:{fid}",
                    desync=desync,
                    error=None,
                ))
            return results
        except Exception as e:
            return [
                ActivationResult(
                    finding_id=str(f.get("id", "")),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.get('id', '')}",
                    desync=False,
                    error=str(e),
                )
                for f in findings
            ]


    # ------------------------------------------------------------------
    # Sprint 8P: CanonicalFinding DTO - typed ingest API
    # ------------------------------------------------------------------

    async def async_record_canonical_finding(
        self,
        finding: CanonicalFinding,
    ) -> ActivationResult:
        """
        Sprint 8P: Typed ingest API for CanonicalFinding DTO.

        Adapts DTO -> existing WAL-first activation path.
        Používá stejný single-thread write executor jako stávající API.

        DTO -> storage contract mapping:
          finding.finding_id  -> id
          finding.query       -> query
          finding.source_type -> source_type
          finding.confidence  -> confidence
          finding.ts          -> ts (in WAL only)
          finding.provenance  -> LMDB WAL payload (DuckDB nemá provenance sloupec)
          finding.payload_text -> LMDB WAL payload (DuckDB nemá payload_text sloupec)

        Returns ActivationResult with same contract as async_record_activation.

        Provenance: tvrdý invariant - stored in LMDB WAL payload only
        (DuckDB schema nemá provenance_sloupec; backward-compatible,
         probe_8l/probe_8h/probe_8f/probe_8b zůstávají kompatibilní)
        """
        # Sprint F262OBS: normalize source_type at ingest seam - unknown / legacy
        # strings are routed via canonical_source_type() so DuckDB never sees
        # an unregistered value. Forward-compat: unknown values are passed
        # through unchanged (not dropped) so a finding recorded today still
        # resolves on a future schema bump.
        if canonical_source_type is not None and finding.source_type:
            try:
                _raw = (
                    finding.source_type.value
                    if isinstance(finding.source_type, SourceType)
                    else str(finding.source_type)
                )
                if SourceType is not None and _raw not in SourceType._value2member_map_:
                    finding.source_type = canonical_source_type(_raw)  # type: ignore[assignment]
            except Exception:
                # Fail-soft: never block ingest on a bad source_type string.
                pass

        if not self._initialized or self._closed:
            return ActivationResult(
                finding_id=finding.finding_id,
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding.finding_id}",
                desync=False,
                error="store closed or not initialized",
                accepted=False,
            )

        # Boot barrier (Sprint 8L)
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return ActivationResult(
                    finding_id=finding.finding_id,
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{finding.finding_id}",
                    desync=False,
                    error="startup replay timeout",
                    accepted=False,
                )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._canonical_finding_to_activation_result,
                finding,
            )
            desync = bool(result.get("lmdb_success") and result.get("duckdb_success") is False)
            lmdb_ok = bool(result.get("lmdb_success"))
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=lmdb_ok,
                duckdb_success=result.get("duckdb_success"),
                lmdb_key=f"finding:{finding.finding_id}",
                desync=desync,
                error=result.get("error"),
                accepted=lmdb_ok,
            )
        except Exception as e:
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding.finding_id}",
                desync=False,
                error=str(e),
                accepted=False,
            )

    def _canonical_finding_to_activation_result(
        self,
        finding: CanonicalFinding,
    ) -> dict:
        """
        Sync wrapper: CanonicalFinding DTO -> ActivationResult dict.

        Sprint 8R: DTO -> storage contract mapping:
          finding.finding_id  -> id
          finding.query       -> query
          finding.source_type -> source_type
          finding.confidence  -> confidence
          finding.ts          -> ts (DOUBLE in DuckDB)
          finding.provenance  -> provenance_json (JSON TEXT in DuckDB via msgspec)
          finding.payload_text -> LMDB WAL payload only

        LMDB WAL uses msgspec.json.encode for consistent serialization.
        DuckDB insert uses tuple row (efficient, not dict list).
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {
            "lmdb_success": False,
            "duckdb_success": None,
            "error": None,
        }

        # Step 1: LMDB WAL first - msgspec serialization
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    result["error"] = "no wal root"
                    return result
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

            key = f"finding:{finding.finding_id}"
            wal_payload = {
                "id": finding.finding_id,
                "query": finding.query,
                "source_type": finding.source_type,
                "confidence": finding.confidence,
                "ts": finding.ts,
                "provenance": finding.provenance,
                "payload_text": finding.payload_text,
            }
            lmdb_ok = self._wal_manager.wal_put(key, wal_payload)
            result["lmdb_success"] = lmdb_ok
            if not lmdb_ok:
                _logger.warning(f"[Sprint 8P] WAL failed for {finding.finding_id}")
                return result
        except Exception as e:
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] WAL exception for {finding.finding_id}: {e}")
            return result

        # Step 2: DuckDB second - serialize provenance to JSON and pass ts + provenance_json
        try:
            # Serialize provenance tuple to JSON for DuckDB storage
            provenance_json = _msgspec_encode(finding.provenance).decode()
            duckdb_ok = self._sync_insert_finding(
                finding.finding_id,
                finding.query,
                finding.source_type,
                finding.confidence,
                ts=finding.ts,
                provenance_json=provenance_json,
            )
            result["duckdb_success"] = duckdb_ok
            if not duckdb_ok:
                _logger.error(f"[Sprint 8P] DuckDB failed for {finding.finding_id}, LMDB preserved")
                self._wal_manager.wal_write_pending_sync_marker(
                    finding.finding_id, finding.query, finding.source_type, finding.confidence,
                )
        except Exception as e:
            result["duckdb_success"] = False
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] DuckDB exception for {finding.finding_id}: {e}, LMDB preserved")
            self._wal_manager.wal_write_pending_sync_marker(
                finding.finding_id, finding.query, finding.source_type, finding.confidence,
            )

        return result

    async def _record_fail_open_batch(
        self,
        findings: list[CanonicalFinding],
        results: list,
        indices: list[int],
    ) -> list[dict]:
        """
        Sprint D7: Batch fail-open path - process N findings whose quality gate threw.

        Replaces N * async_record_canonical_finding() calls with one batch call.
        Order: LMDB WAL first (per finding via wal_put_many) -> DuckDB second (single executemany).

        Returns list[dict] - one per finding in input order, indexed into results by indices.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)

        if not findings:
            return []

        ret: list[dict] = []

        # Step 1: LMDB WAL - batch via wal_put_many per finding
        lmdb_ok = False
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    for f in findings:  # noqa: B007
                        ret.append({
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "no wal root",
                        })
                    return ret
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

            items = []
            for f in findings:
                key = f"finding:{f.finding_id}"
                wal_payload = {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                }
                items.append((key, wal_payload))

            if items:
                lmdb_ok = self._wal_manager.wal_put_many(items) if hasattr(
                    self._wal_manager, "wal_put_many"
                ) else False
                if not lmdb_ok:
                    _logger.warning(f"[D7] Batch WAL failed for {len(items)} items")
                    for f in findings:  # noqa: B007
                        ret.append({
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "lmdb batch failed",
                        })
                    return ret
        except Exception as e:
            _logger.error(f"[D7] Batch WAL exception: {e}")
            for f in findings:  # noqa: B007
                ret.append({
                    "lmdb_success": False,
                    "duckdb_success": None,
                    "error": str(e),
                })
            return ret

        # Step 2: DuckDB - Arrow zero-copy via C Data Interface.
        # Replaces tuple-based executemany path with register() + INSERT...SELECT.
        duckdb_all_ok = False
        try:
            duckdb_count, duckdb_err = self._sync_record_canonical_findings_batch_arrow(findings)
            if duckdb_err is not None:
                _logger.error(f"[D7-arrow] DuckDB Arrow failed: {duckdb_err}")
                duckdb_all_ok = False
            elif duckdb_count < len(findings):
                # Partial insert = duplicates (ON CONFLICT DO NOTHING), NOT an error.
                # All rows reached DuckDB; duplicates were silently ignored.
                duckdb_all_ok = True
            else:
                duckdb_all_ok = True
        except Exception as e:
            _logger.error(f"[D7] Batch DuckDB exception: {e}, LMDB preserved")
            duckdb_all_ok = False

        # Build per-finding results
        accepted_total = 0
        for f in findings:  # noqa: B007
            lmdb_success = lmdb_ok
            if lmdb_success:
                accepted_total += 1
            ret.append({
                "lmdb_success": lmdb_success,
                "duckdb_success": duckdb_all_ok,
                "error": None,
            })

        if accepted_total:
            self._quality_state._accepted_count += accepted_total

        return ret

    async def async_record_canonical_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[ActivationResult]:
        """
        Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.

        Adapts DTO list -> existing WAL-first batch activation path.
        Používá stejný single-thread write executor jako stávající API.

        Returns list[ActivationResult] - 1:1 mapping, len(results) == len(findings).
        Partial failure: pokud nějaký finding selže, ostatní jsou still processed.
        Celý batch neshodí kvůli jednomu vadnému findingu.
        """
        if not findings:
            return []

        if not self._initialized or self._closed:
            return [
                ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error="store closed or not initialized",
                    accepted=False,
                )
                for f in findings
            ]

        # Boot barrier (Sprint 8L)
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                return [
                    ActivationResult(
                        finding_id=str(f.finding_id),
                        lmdb_success=False,
                        duckdb_success=None,
                        lmdb_key=f"finding:{f.finding_id}",
                        desync=False,
                        error="startup replay timeout",
                        accepted=False,
                    )
                    for f in findings
                ]

        # Sprint 7.2: Use Arrow path for zero-copy batch ingest (P0-4).
        # Falls back to legacy path only if Arrow is unavailable.
        loop = asyncio.get_running_loop()
        try:
            # Arrow zero-copy: WAL + DuckDB Arrow INSERT via C Data Interface.
            # Replaces deleted _sync_record_canonical_findings_batch_arrow_full.
            results = await loop.run_in_executor(
                self._executor,
                self._sync_record_canonical_findings_batch_arrow_standalone,
                findings,
            )
            # results is list[dict] - normalize to list[ActivationResult]
            # Sprint 8QA/8TF: trigger graph ingest in background (fire-and-forget via _bg_tasks)
            # GUARD: check capability before triggering - DuckPGQGraph does not have
            # buffer_ioc/flush_buffers. Silent no-op would hide a miswired attachment.
            if (
                results
                and any(r.get("lmdb_success") for r in results)
                and self.truth_write_graph_supports_buffered_writes()
            ):
                await self._graph_ingest_findings(findings)

            # Sprint 8SB: trigger semantic buffer in background
            if results and any(r.get("lmdb_success") for r in results):
                self._semantic_buffer_findings(findings)

            # Count accepted (lmdb_success) findings for _accepted_count
            accepted_total = sum(1 for r in results if r.get("lmdb_success"))
            self._quality_state._accepted_count += accepted_total

            return [
                ActivationResult(
                    finding_id=str(r.get("finding_id", "")),
                    lmdb_success=bool(r.get("lmdb_success")),
                    duckdb_success=r.get("duckdb_success"),
                    lmdb_key=f"finding:{r.get('finding_id', '')}",
                    desync=bool(r.get("lmdb_success") and r.get("duckdb_success") is False),
                    error=r.get("error"),
                    accepted=bool(r.get("lmdb_success")),
                )
                for r in results
            ]
        except Exception as e:
            return [
                ActivationResult(
                    finding_id=str(f.finding_id),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{f.finding_id}",
                    desync=False,
                    error=str(e),
                    accepted=False,
                )
                for f in findings
            ]

    async def async_record_canonical_findings_batch_arrow(
        self,
        findings: list[CanonicalFinding],
    ) -> list[ActivationResult]:
        """
        Sprint P0-4: Arrow zero-copy batch ingest for CanonicalFinding DTO list.

        10-stupňový fallback na legacy `async_record_canonical_findings_batch`:
          1. `HLEDAC_ARROW_INGEST == "0"` (env gate, default ON, opt-out) -> legacy
          2. `len(findings) < _ARROW_MIN_BATCH` (default 5) -> legacy
          3. pyarrow není dostupný (cached O(1) check) -> legacy
          4. store not initialized or closed -> legacy
          5. startup barrier timeout (30 s) -> legacy
          6. asyncio.gather executor error -> legacy
          7. WAL phase failed (wal_ok is False) -> legacy
          8. DuckDB executor threw exception -> legacy
          9. sync helper returned empty results -> legacy
          10. all duckdb_success=False despite non-empty results -> legacy

        Při úspěchu: WAL first (LMDB put_many) + DuckDB Arrow register+INSERT (one roundtrip).
        Zero-copy: pa.array() drží C++ buffery, DuckDB čte přes Arrow C Data Interface.

        Invarianty (shodné s legacy):
          - 1:1 result mapping (len(results) == len(findings))
          - `self._quality_state._accepted_count` += count of lmdb_success
          - graph + semantic buffer triggered for accepted findings
          - thread-affine execution on `self._executor`
          - LMDB WAL precedes DuckDB (recovery invariant)

        Returns list[ActivationResult]. Empty list for empty findings input.
        """
        if not findings:
            return []

        # Fallback gate 1: env opt-in
        if not _ARROW_INGEST_ENABLED:
            self._arrow_metrics["arrow_fallback_env"] += len(findings)
            logger.debug(
                "[D7-arrow-fallback] HLEDAC_ARROW_INGEST=0, using legacy path "
                f"for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Fallback gate 2: batch size - executemany is faster for small N
        if len(findings) < _ARROW_MIN_BATCH:
            self._arrow_metrics["arrow_fallback_batch"] += len(findings)
            logger.debug(
                f"[D7-arrow-fallback] batch size {len(findings)} < "
                f"_ARROW_MIN_BATCH({_ARROW_MIN_BATCH}), using legacy path"
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Fallback gate 3: pyarrow availability (O(1) cached check)
        if not _check_pyarrow_available():
            self._arrow_metrics["arrow_fallback_pyarrow"] += len(findings)
            logger.debug(
                "[D7-arrow-fallback] pyarrow not available, using legacy path "
                f"for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Init / closed guards - mirror legacy semantics (mark all as failed)
        if not self._initialized or self._closed:
            return await self.async_record_canonical_findings_batch(findings)

        # Boot barrier - same as legacy (30s timeout, fail-soft)
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                self._arrow_metrics["arrow_fallback_init"] += len(findings)
                return await self.async_record_canonical_findings_batch(findings)

        loop = asyncio.get_running_loop()

        # Sprint SEQUENTIAL-2 Level 1: Concurrent WAL + DuckDB via asyncio.gather.
        # WAL-first invariant preserved: WAL MUST succeed before DuckDB result is used.
        # Architecture: submit BOTH futures simultaneously, then await together.
        # - WAL I/O overlaps with DuckDB CPU/INSERT (true parallel execution)
        # - DuckDB Arrow INSERT (CPU-bound SIMD memcpy) runs while WAL LMDB write completes
        # - ~1ms wall-clock overlap on typical 500-item batch (WAL=0.5ms, DuckDB=4-8ms)
        # - WAL-first recovery invariant: DuckDB result is ONLY used if wal_ok is True
        wal_future = loop.run_in_executor(
            self._wal_executor,
            self._wal_put_many_sync,
            findings,
        )
        duckdb_future = loop.run_in_executor(
            self._duckdb_arrow_executor,
            self._duckdb_arrow_sync,
            findings,
        )
        wal_ok: bool
        duckdb_result: tuple[int, str | None] | Exception
        # F262-FIX: asyncio.gather MUST use return_exceptions=True.
        # Without it, if either task raises, CancelledError (BaseException, not Exception)
        # propagates and bypasses the fallback handler entirely — silent data loss.
        # With return_exceptions=True, exceptions arrive as objects in results,
        # both tasks complete, and we handle them explicitly below.
        # F314: migrated asyncio.gather -> safe_gather_return_exceptions (GHOST invariants + raw exceptions preserved)
        gather_results: tuple[object, ...] = await safe_gather_return_exceptions(wal_future, duckdb_future, label="duckdb_store:wal_duckdb")
        wal_ok_or_exc, duckdb_result = gather_results[0], gather_results[1]

        # Handle exceptions from gather return_exceptions path.
        if isinstance(wal_ok_or_exc, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] WAL executor error ({wal_ok_or_exc}), using legacy path "
                f"for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)
        if isinstance(duckdb_result, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] DuckDB executor error ({duckdb_result}), using legacy path "
                f"for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)
        wal_ok = wal_ok_or_exc

        # WAL-first gate: DuckDB result is only valid if WAL succeeded.
        # If WAL failed, DuckDB may have partially run — its results are discarded.
        if not wal_ok:
            self._arrow_metrics["arrow_fallback_empty"] += len(findings)
            _logger.error(
                "[D7] Arrow WAL phase failed for %d findings - "
                "falling back to legacy executemany.",
                len(findings),
            )
            return await self.async_record_canonical_findings_batch(findings)

        logger.debug(f"[D7-arrow] WAL ok (concurrent), DuckDB result={duckdb_result!r} batch={len(findings)}")

        # Build per-finding result dicts from WAL-ok + DuckDB outcome.
        # Same shape as _sync_record_canonical_findings_batch_arrow_standalone returns.
        if isinstance(duckdb_result, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] DuckDB executor exception ({duckdb_result}), "
                f"using legacy path for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)

        duckdb_count, duckdb_err = duckdb_result
        # BUG-11 FIX: Distinguish INSERT failure from duplicate suppression.
        # - duckdb_err is not None  → real INSERT error (hard failure)
        # - duckdb_count == len(findings) → all rows INSERTED (clean)
        # - duckdb_count < len(findings)  → some duplicates (ON CONFLICT ignored, NOT error)
        #   The data IS in DuckDB for all rows; duplicates are normal dedup behavior.
        if duckdb_err is not None:
            _logger.error(f"[D7] DuckDB Arrow bulk failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            # BUG-11: Partial insert = duplicates (ON CONFLICT), NOT an error.
            # All rows reached DuckDB; duplicates were silently ignored.
            # Do NOT fallback — re-processing would waste work and return the same result.
            # Mark all as duckdb_success=True (they ARE in DuckDB).
            self._arrow_metrics["arrow_partial_duplicates"] += 1
            _logger.debug(
                f"[D7-arrow] Duplicate suppression: {duckdb_count}/{len(findings)} "
                f"inserted (rest were deduplicated by ON CONFLICT)"
            )
            duckdb_all_ok = True
        else:
            duckdb_all_ok = True

        results = [
            {
                "finding_id": f.finding_id,
                "lmdb_success": wal_ok,
                "duckdb_success": duckdb_all_ok,
                "error": duckdb_err,
            }
            for f in findings
        ]

        # Fallback gate 4: empty results from sync helper = failure (table build
        # error, register error, INSERT error). Empty list = legacy returns
        # [] only for empty input, which we already filtered above.
        if not results:
            self._arrow_metrics["arrow_fallback_empty"] += len(findings)
            _logger.error(
                "[D7] Arrow path returned 0 results for %d findings - "
                "falling back to legacy executemany. "
                "Enable HLEDAC_ARROW_INGEST=0 to use legacy path only.",
                len(findings),
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Fallback gate 5: complete DuckDB failure (all duckdb_success=False)
        # despite non-empty results -> fall back so canonical write is guaranteed.
        if results and all(r.get("duckdb_success") is False for r in results):
            self._arrow_metrics["arrow_fallback_all_fail"] += len(results)
            # Typed error telemetry - distinguish DuckDB insert failure from
            # partial-write (ON CONFLICT DO NOTHING silently skipped rows).
            errors_in_results = [r.get("error") for r in results if r.get("error")]
            if errors_in_results and errors_in_results[0] == "duckdb_error":
                self._arrow_metrics["arrow_error_duckdb_insert"] += len(results)
            elif errors_in_results and errors_in_results[0] == "table_build":
                self._arrow_metrics["arrow_error_table_build"] += len(results)
            else:
                # Partial write: some inserted, some silently skipped (ON CONFLICT)
                self._arrow_metrics["arrow_error_partial"] += len(results)
            logger.error(
                "[D7] Arrow path: all %d findings failed DuckDB write - "
                "falling back to legacy executemany.",
                len(results),
            )
            return await self.async_record_canonical_findings_batch(findings)

        # Post-processing - identical to async_record_canonical_findings_batch
        # (graph trigger, semantic buffer, _accepted_count, ActivationResult build).
        if results and any(r.get("lmdb_success") for r in results):
            if self.truth_write_graph_supports_buffered_writes():
                await self._graph_ingest_findings(findings)
            self._semantic_buffer_findings(findings)

        accepted_total = sum(1 for r in results if r.get("lmdb_success"))
        self._quality_state._accepted_count += accepted_total

        # Sprint P1-3: Arrow path success telemetry
        self._arrow_metrics["arrow_selected"] += len(findings)
        self._arrow_metrics["arrow_success_count"] += len(findings)
        lmdb_ok = sum(1 for r in results if r.get("lmdb_success"))
        duckdb_ok = sum(1 for r in results if r.get("duckdb_success"))
        self._arrow_metrics["arrow_success_lmdb_count"] += lmdb_ok
        self._arrow_metrics["arrow_success_duckdb_count"] += duckdb_ok
        logger.info(
            f"[D7-arrow] path=arrow batch={len(findings)} "
            f"lmdb_ok={lmdb_ok} duckdb_ok={duckdb_ok}"
        )

        return [
            ActivationResult(
                finding_id=str(r.get("finding_id", "")),
                lmdb_success=bool(r.get("lmdb_success")),
                duckdb_success=r.get("duckdb_success"),
                lmdb_key=f"finding:{r.get('finding_id', '')}",
                desync=bool(r.get("lmdb_success") and r.get("duckdb_success") is False),
                error=r.get("error"),
                accepted=bool(r.get("lmdb_success")),
            )
            for r in results
        ]

    # ------------------------------------------------------------------
    # Sprint F800A: Controller-facing async seam - thin adapters
    # ------------------------------------------------------------------

    async def async_get_recent_findings(
        self,
        limit: int = 10,
    ) -> list[CanonicalFinding]:
        """
        Sprint F800A: Controller-facing async adapter for recent findings.

        Thin wrapper around async_query_recent_findings - converts raw dict rows
        to CanonicalFinding instances so callers receive typed DTOs.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns empty list if store is closed or uninitialized.

        Args:
            limit: Maximum number of findings to return (ordered by ts DESC).

        Returns:
            list[CanonicalFinding] - ordered by ts descending, most recent first.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()


        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(
                self._executor,
                self._sync_query_findings,
                limit,
            )
        except Exception:
            return []

        findings: list[CanonicalFinding] = []
        for row in rows:
            try:
                # Reconstruct provenance tuple from stored JSON string
                provenance: tuple[str, ...] = ()
                raw_prov = row.get("provenance_json") or row.get("provenance")
                if raw_prov:
                    if isinstance(raw_prov, str):
                        try:
                            decoded = msgspec.json.decode(raw_prov.encode())
                            if isinstance(decoded, list):
                                provenance = tuple(str(v) for v in decoded)
                        except Exception:
                            provenance = ()
                    elif isinstance(raw_prov, list):
                        provenance = tuple(str(v) for v in raw_prov)

                finding = CanonicalFinding(
                    finding_id=str(row.get("id", row.get("finding_id", ""))),
                    query=str(row.get("query", "")),
                    source_type=str(row.get("source_type", "")),
                    confidence=float(row.get("confidence", 0.0)),
                    ts=float(row.get("ts", 0.0)),
                    provenance=provenance,
                    payload_text=row.get("payload_text"),
                )
                findings.append(finding)
            except Exception:
                # Malformed row - skip, do not crash
                continue

        return findings

    # -- Sprint F202K: target_profiles async API -------------------------------

    async def async_upsert_target_profile(self, profile: TargetProfileSummary) -> None:
        """
        Sprint F202K: Insert or update a target profile.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.
        """
        if not self._initialized or self._closed:
            return

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_upsert_target_profile,
                profile,
            )
        except Exception:
            pass

    async def async_get_target_profile(self, target_id: str) -> TargetProfileSummary | None:
        """
        Sprint F202K: Get a target profile by target_id.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns None if not found or on error.
        """
        if not self._initialized or self._closed:
            return None

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_get_target_profile,
                target_id,
            )
        except Exception:
            return None

    # -- Sprint F204D: target_memory async API ---------------------------------

    async def async_upsert_target_memory(self, memory: TargetMemory) -> None:
        """
        Sprint F204D: Insert or update target memory from a TargetMemory.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.

        F206H FIX: Previously accepted TargetMemoryUpdate and silently failed
        (type mismatch with _sync_upsert_target_memory which expects TargetMemory).
        Now accepts TargetMemory directly - caller (SprintScheduler) passes
        the already-merged memory from TargetMemoryService.merge_update().
        """
        if not self._initialized or self._closed:
            return

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_upsert_target_memory,
                memory,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def async_get_target_memory(self, target_id: str) -> TargetMemory | None:
        """
        Sprint F204D: Get target memory by target_id.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns None if not found or on error.
        """
        if not self._initialized or self._closed:
            return None

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            from hledac.universal.knowledge.target_memory import TargetMemory

            def _sync_get():
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return None
                result = conn.execute(
                    "SELECT * FROM target_memory WHERE target_id = ?", [target_id]
                ).fetchone()
                if result is None:
                    return None
                return TargetMemory(
                    target_id=result[0],
                    first_seen_ts=result[1],
                    last_seen_ts=result[2],
                    sprint_count=result[3],
                    cumulative_finding_count=result[4],
                    entity_facets=_json_loads_flexible(result[5]),
                    exposure_facets=_json_loads_flexible(result[6]),
                    pivot_facets=_json_loads_flexible(result[7]),
                    confidence_drift=_json_loads_flexible(result[8]),
                    updated_by_sprint_id=result[9],
                )

            return await loop.run_in_executor(self._executor, _sync_get)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def async_get_previous_findings_for_target(
        self,
        target_id: str,
        before_sprint_id: str | None = None,
        limit: int = 1000,
    ) -> list[CanonicalFinding]:
        """
        Sprint F202K: Get previous findings for a target, optionally before a specific sprint.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Returns canonical findings ordered by ts DESC.

        Args:
            target_id: The target identifier to filter findings by.
            before_sprint_id: Optional sprint ID to filter findings before this sprint.
            limit: Maximum number of findings to return (default 1000).

        Returns:
            list[CanonicalFinding] - ordered by ts descending, most recent first.
            Returns empty list if store is closed, uninitialized, or query fails.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(
                self._executor,
                self._sync_get_previous_findings_for_target,
                target_id,
                before_sprint_id,
                limit,
            )
        except Exception:
            return []

        findings: list[CanonicalFinding] = []
        for row in rows:
            try:
                provenance: tuple[str, ...] = ()
                raw_prov = row.get("provenance_json") or row.get("provenance")
                if raw_prov:
                    if isinstance(raw_prov, str):
                        try:
                            decoded = msgspec.json.decode(raw_prov.encode())
                            if isinstance(decoded, list):
                                provenance = tuple(str(v) for v in decoded)
                        except Exception:
                            provenance = ()
                    elif isinstance(raw_prov, list):
                        provenance = tuple(str(v) for v in raw_prov)

                finding = CanonicalFinding(
                    finding_id=str(row.get("finding_id", row.get("id", ""))),
                    query=str(row.get("query", "")),
                    source_type=str(row.get("source_type", "")),
                    confidence=float(row.get("confidence", 0.0)),
                    ts=float(row.get("ts", 0.0)),
                    provenance=provenance,
                    payload_text=row.get("payload_text"),
                )
                findings.append(finding)
            except Exception:
                continue
        return findings

    # -- Sprint F203G: hypothesis_feedback async API ---------------------------

    async def async_record_hypothesis_feedback(
        self,
        record: Any,
    ) -> bool:
        """
        Sprint F203G: Record a single hypothesis_feedback entry.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Silently fails if store is closed or uninitialized.

        Args:
            record: HypothesisFeedbackRecord (frozen dataclass) with fields:
                id, target_id, pivot_type, ioc_type, produced_count,
                accepted_count, signal_value, ts.

        Returns:
            True if recorded, False otherwise.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._sync_record_hypothesis_feedback,
                record,
            )
        except Exception:
            return False

    async def async_get_hypothesis_feedback(
        self,
        target_id: str | None = None,
        limit: int = 1000,
    ) -> list[Any]:
        """
        Sprint F203G: Fetch aggregated hypothesis_feedback records.

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.

        Args:
            target_id: If provided, filter by target_id. If None, returns all.
            limit: Maximum records to return (default 1000).

        Returns:
            List of HypothesisFeedbackRecord instances ordered by ts DESC.
            Returns empty list if store is closed or uninitialized.
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            rows: list[dict] = await loop.run_in_executor(
                self._executor,
                self._sync_get_hypothesis_feedback,
                target_id,
                limit,
            )
        except Exception:
            return []

        from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord
        records: list[Any] = []
        for row in rows:
            try:
                records.append(HypothesisFeedbackRecord(
                    id=str(row["id"]),
                    target_id=str(row["target_id"]),
                    pivot_type=str(row["pivot_type"]),
                    ioc_type=str(row["ioc_type"]),
                    produced_count=int(row["produced_count"] or 0),
                    accepted_count=int(row["accepted_count"] or 0),
                    signal_value=float(row["signal_value"] or 0.0),
                    ts=float(row["ts"] or 0.0),
                ))
            except Exception:
                continue
        return records

    # -- F214: Hypothesis tracking (cross-sprint persistence) ---------------

    async def async_record_hypothesis_tracking(
        self,
        hypothesis_id: str,
        sprint_id: str,
        hypothesis_text: str,
        status: str,
        confidence: float,
        falsification_result: str | None = None,
        disproved_by_sprint_id: str | None = None,
    ) -> bool:
        """
        F214: Persist a hypothesis tracking record for cross-sprint lineage.
        Supports: "we hypothesized X in sprint N, disproved by sprint M."

        Thread-safe, non-blocking - runs on duckdb_worker via run_in_executor.
        Fail-soft: returns False silently on error.
        """
        if not self._initialized or self._closed:
            return False

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._sync_record_hypothesis_tracking,
                hypothesis_id,
                sprint_id,
                hypothesis_text,
                status,
                confidence,
                falsification_result,
                disproved_by_sprint_id,
            )
            return True
        except Exception:
            return False

    def _sync_record_hypothesis_tracking(
        self,
        hypothesis_id: str,
        sprint_id: str,
        hypothesis_text: str,
        status: str,
        confidence: float,
        falsification_result: str | None,
        disproved_by_sprint_id: str | None,
    ) -> None:
        """Sync writer for hypothesis_tracking."""
        conn = self._get_conn()
        if conn is None:
            return
        try:
            conn.execute(
                self._statements._SQL_INSERT_HYPOTHESIS_TRACKING,
                (
                    hypothesis_id,
                    sprint_id,
                    hypothesis_text,
                    status,
                    confidence,
                    falsification_result,
                    disproved_by_sprint_id,
                    time.time(),
                ),
            )
        except Exception:
            pass

    def _finding_id_of(self, f: CanonicalFinding | dict) -> str:
        """Extract finding_id from CanonicalFinding or dict, safely."""
        if isinstance(f, CanonicalFinding):
            return f.finding_id
        return str(f.get("finding_id", f.get("id", "")))

    async def async_bulk_insert_findings(
        self,
        findings: list[CanonicalFinding | dict],
    ) -> list[ActivationResult]:
        """
        Sprint F800A: Controller-facing async adapter for bulk findings insert.

        Accepts CanonicalFinding instances OR plain dicts (controller dict format).
        Dicts are converted to CanonicalFinding before delegating to the existing
        async_record_canonical_findings_batch truth path.

        Thread-safe, non-blocking - delegates to async_record_canonical_findings_batch
        which uses the single-worker executor.

        Args:
            findings: List of CanonicalFinding or dict with keys:
                      finding_id, query, source_type, confidence, ts, provenance.

        Returns:
            list[ActivationResult] - 1:1 mapping, len(results) == len(findings).
            Empty list if input is empty or store is closed.
        """
        if not findings:
            return []

        if not self._initialized or self._closed:
            return [
                ActivationResult(
                    finding_id=self._finding_id_of(f),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{self._finding_id_of(f)}",
                    desync=False,
                    error="store closed or not initialized",
                    accepted=False,
                )
                for f in findings
            ]

        # Normalize dicts -> CanonicalFinding
        canonical_findings: list[CanonicalFinding] = []
        for f in findings:
            if isinstance(f, CanonicalFinding):
                canonical_findings.append(f)
            elif isinstance(f, dict):
                try:
                    provenance: tuple[str, ...] = ()
                    raw_prov = f.get("provenance")
                    if raw_prov:
                        if isinstance(raw_prov, (list, tuple)):
                            provenance = tuple(str(v) for v in raw_prov)
                        elif isinstance(raw_prov, str):
                            try:
                                decoded = msgspec.json.decode(raw_prov.encode())
                                if isinstance(decoded, list):
                                    provenance = tuple(str(v) for v in decoded)
                            except Exception:
                                provenance = ()
                    canonical_findings.append(CanonicalFinding(
                        finding_id=str(f.get("finding_id", f.get("id", ""))),
                        query=str(f.get("query", "")),
                        source_type=str(f.get("source_type", "")),
                        confidence=float(f.get("confidence", 0.0)),
                        ts=float(f.get("ts", 0.0)),
                        provenance=provenance,
                        payload_text=f.get("payload_text"),
                    ))
                except Exception:
                    continue
            else:
                continue

        if not canonical_findings:
            return [
                ActivationResult(
                    finding_id=self._finding_id_of(f),
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{self._finding_id_of(f)}",
                    desync=False,
                    error="unconvertible input",
                    accepted=False,
                )
                for f in findings
            ]

        # Delegate to truth path - returns list[ActivationResult]
        raw_results = await self.async_record_canonical_findings_batch(canonical_findings)

        # Normalize: async_record_canonical_findings_batch returns list[ActivationResult]
        # async_record_canonical_findings_batch returns list[ActivationResult] at type level,
        # but TypedDict is erased to dict at runtime. Normalize all results.
        normalized: list[ActivationResult] = []
        for r in raw_results:
            # r is dict at runtime (TypedDict erasure); convert if not already ActivResult
            if isinstance(r, dict) and "finding_id" in r:
                # Already a properly-shaped ActivationResult dict - pass through
                normalized.append(ActivationResult(
                    finding_id=str(r.get("finding_id", "")),
                    lmdb_success=bool(r.get("lmdb_success")),
                    duckdb_success=r.get("duckdb_success"),
                    lmdb_key=str(r.get("lmdb_key", "")),
                    desync=bool(r.get("desync")),
                    error=r.get("error"),
                    accepted=bool(r.get("accepted", r.get("lmdb_success", False))),
                ))
            else:
                # Fallback - shouldn't happen with truth path
                normalized.append(ActivationResult(
                    finding_id="",
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key="",
                    desync=False,
                    error="unexpected result type",
                    accepted=False,
                ))
        return normalized

    def _canonical_findings_batch_to_activation_results(
        self,
        findings: list[CanonicalFinding],
    ) -> list[dict]:
        """
        Sync batch: CanonicalFinding list -> list[dict] (not ActivationResult, avoid circular import).

        Returns one dict per finding in input order.
        LMDB WAL uses msgspec.json.encode for provenance serialization.
        DuckDB insert uses tuple rows (list of lists).
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        results: list[dict] = []

        if not findings:
            return results

        # F276: Diagnostic instrumentation - log at entry to diagnose silent-0-write path.
        # Previous versions swallowed all errors fail-soft with no telemetry, making it
        # impossible to distinguish: (a) store not initialized, (b) boot barrier timeout,
        # (c) LMDB WAL failure, (d) DuckDB insert failure, (e) ON CONFLICT dedup.
        logger.debug(
            "[F276-INGEST] entry n=%d  _initialized=%s  _closed=%s  _startup_ready=%s",
            len(findings),
            getattr(self, "_initialized", None),
            getattr(self, "_closed", None),
            getattr(self, "_startup_ready", None) and (
                getattr(self._startup_ready, "is_set", None)()
                if callable(getattr(self._startup_ready, "is_set", None))
                else False
            ),
        )

        # Step 1: LMDB WAL first - msgspec serialization
        lmdb_ok = False
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    _logger.error("[F276-INGEST] _db_path is None - WAL root unavailable, cannot persist findings")
                    for f in findings:
                        results.append({
                            "finding_id": f.finding_id,
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "no wal root",
                        })
                    return results
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

            items = []
            for f in findings:
                key = f"finding:{f.finding_id}"
                wal_payload = {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                }
                items.append((key, wal_payload))

            if items:
                lmdb_ok = self._wal_manager.wal_put_many(items) if hasattr(self._wal_manager, 'wal_put_many') else False
                if not lmdb_ok:
                    _logger.warning(f"[Sprint 8P] Batch WAL failed for {len(items)} items")
                    for f in findings:
                        results.append({
                            "finding_id": f.finding_id,
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "lmdb batch failed",
                        })
                    return results
        except Exception as e:
            _logger.error(f"[Sprint 8P] Batch WAL exception: {e}")
            for f in findings:
                results.append({
                    "finding_id": f.finding_id,
                    "lmdb_success": False,
                    "duckdb_success": None,
                    "error": str(e),
                })
            return results

        # Step 2: DuckDB second - tuple rows with ts and provenance_json (Sprint 8R)
        try:
            rows: list[list] = []
            for f in findings:
                provenance_json = _msgspec_encode(f.provenance).decode()
                rows.append([
                    f.finding_id, f.query, f.source_type, f.confidence,
                    f.ts, provenance_json,
                ])
            inserted = self._sync_insert_findings_bulk_as_tuples(rows)
            duckdb_all_ok = inserted >= len(findings)
            if inserted < len(findings):
                _logger.error(f"[Sprint 8P] Partial DuckDB batch: {inserted}/{len(findings)}")
        except Exception as e:
            _logger.error(f"[Sprint 8P] Batch DuckDB exception: {e}, LMDB preserved")
            duckdb_all_ok = False

        # Build per-finding results
        for _i, f in enumerate(findings):
            duckdb_success = duckdb_all_ok  # simplified per-item model
            results.append({
                "finding_id": f.finding_id,
                "lmdb_success": lmdb_ok,
                "duckdb_success": duckdb_success,
                "error": None,
            })

        # F276: Exit instrumentation - diagnose entry > 0 but exit = 0 pattern.
        logger.debug(
            "[F276-INGEST] exit n=%d  lmdb_ok=%s  duckdb_ok=%s",
            len(results),
            lmdb_ok,
            duckdb_all_ok,
        )
        return results

    def _extract_url_from_provenance(self, provenance: tuple[str, ...]) -> str:
        """
        Sprint 8AK: Extract the first HTTP(S) URL from a provenance tuple.

        Source-agnostic: scans all positions regardless of source type.
        Returns empty string if no URL is found.
        """
        if not provenance:
            return ""
        for item in provenance:
            if isinstance(item, str) and item.startswith("http"):
                return item
        return ""

    # -- Sprint F216G: Quality Rejection Ledger -------------------------------

    def _record_quality_rejection(
        self,
        finding: CanonicalFinding,
        decision: FindingQualityDecision,
    ) -> None:
        """
        Sprint F216G: Record a quality gate rejection to the bounded ledger.

        Delegates to QualityAssessmentState.record_rejection().
        """
        self._quality_state.record_rejection(finding, decision)

    def get_quality_rejection_ledger(self) -> tuple[QualityRejectionRecord, ...]:
        """
        Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).

        Returns a tuple (immutable view) of all recorded rejection records.
        Delegates to QualityAssessmentState for backward compat.
        """
        return self._quality_state.get_rejection_history()

    def _assess_finding_quality(self, finding: CanonicalFinding) -> FindingQualityDecision:
        """
        Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.

        Sprint 8AK: URL-first fingerprint - if a canonical URL is present in
        provenance, use it (normalized) as the primary dedup signal, independent
        of source_type or payload position. Falls back to payload_text.

        Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.
        Lookup order: hot cache -> persistent LMDB -> store if miss.
        LMDB is the authority; hot cache is a bounded read-through cache.

        Returns FindingQualityDecision (frozen, immutable).
        Fail-open: any exception -> accept with reason="quality_check_error".

        Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.
        If both are empty, falls back to query (may accept trivially).
        """
        import logging as _logging
        _logger = _logging.getLogger(__name__)

        # Sprint 8AK: URL-first fingerprint
        url_from_provenance = self._extract_url_from_provenance(finding.provenance)
        url_fingerprint = _compute_url_fingerprint(url_from_provenance) if url_from_provenance else ""

        # Map text for quality checks (only needed for entropy when no URL)
        if url_fingerprint:
            # URL-first: use URL fingerprint for dedup, skip entropy (URL = identity)
            fingerprint = url_fingerprint
            entropy = 0.0  # not meaningful when URL is identity
        else:
            # Fallback: payload-based fingerprint
            text = finding.payload_text if finding.payload_text else finding.query
            if not text or not text.strip():
                text = finding.query
            normalized = _normalize_for_quality(text)
            entropy = _compute_entropy(normalized)
            fingerprint = _compute_dedup_fingerprint(normalized)

        # P0-2: Feed source bypass for hot cache write.
        # P1-2: Detailed instrumentation - log source_type + entropy + fp_len for diagnostics
        _is_feed_source: bool = finding.source_type == "rss_atom_pipeline"
        logger.debug(
            "[QUALITY-GATE] assessing source_type=%s is_feed=%s url_fp=%s entropy=%.3f fp_len=%d finding_id=%s",
            finding.source_type,
            _is_feed_source,
            bool(url_fingerprint),
            entropy,
            len(fingerprint),
            finding.finding_id[:16] if finding.finding_id else "",
        )
        # Feed findings (source_type="rss_atom_pipeline") are high-volume, high-overlap:
        # - Hot cache fills rapidly with feed fingerprints -> LRU eviction loses cross-run dedup
        # - Feed items with same IOC but different entry URLs produce legitimate unique findings
        # - Hot cache false positives block legitimate new feed content
        # Fix: feed findings skip hot cache write; LMDB remains authority for cross-run dedup.
        # Hot cache lookup (Tier 1) still applies - existing feed duplicates within same run
        # are correctly rejected by hot cache; only the hot cache WRITE is bypassed.

        # Sprint 8AK: Separate counter semantics for persistent vs in-memory duplicates
        # - Hot cache hit (in-memory, same process) -> _quality_duplicate_count
        # - Persistent LMDB hit (cross-source, survives restarts) -> _persistent_duplicate_count

        # Tier 1: hot cache (fast path, bounded)
        duplicate = self._hot_cache_lookup(fingerprint) is not None
        if duplicate:
            self._quality_state._quality_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            logger.debug(
                "[QUALITY-GATE] rejected=hot_cache_duplicate fp=%s finding_id=%s",
                fingerprint[:16] if fingerprint else "",
                finding.finding_id[:16] if finding.finding_id else "",
            )
            return FindingQualityDecision(
                accepted=False,
                reason=reason,
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=True,
            )

        # Tier 2: persistent LMDB (authority)
        stored_finding_id = self._lookup_persistent_dedup(fingerprint)
        if stored_finding_id is not None:
            # Miss in hot cache but hit in LMDB - populate hot cache (non-feed only), reject
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, stored_finding_id)
            self._quality_state._persistent_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            logger.debug(
                "[QUALITY-GATE] rejected=lmdb_duplicate fp=%s finding_id=%s stored_id=%s",
                fingerprint[:16] if fingerprint else "",
                finding.finding_id[:16] if finding.finding_id else "",
                stored_finding_id[:16] if stored_finding_id else "",
            )
            return FindingQualityDecision(
                accepted=False,
                reason=reason,
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=True,
            )

        # URL-first path: short-circuit to store (no entropy check needed)
        # _accepted_count is incremented by async_record_canonical_findings_batch - NOT here
        if url_fingerprint:
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            # P0-2: feed bypass - hot cache write skipped for feed sources
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True,
                reason=None,
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Short strings (< 8 chars) skip entropy filter - accept immediately
        # WITHOUT storing to LMDB/hotcache. Storage deferred to after semantic dedup pass.
        # _accepted_count is incremented by async_record_canonical_findings_batch - NOT here
        if len(fingerprint) < _QUALITY_MIN_ENTROPY_LEN:
            # Sprint F197B: Check semantic dedup before storing short strings too
            # Sprint F222: Now uses DedupManager's semantic dedup cache
            dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
            if dedup_cache is not None:
                try:
                    text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                    if text_for_embed and len(text_for_embed) >= 16:
                        # Threshold tier based on source type:
                        # F290: Lowered from 0.80/0.85 to 0.75/0.80 for better early-cycle recall.
                        # Feed=0.75 (was 0.80): feed sources need looser dedup, similar phrasing
                        # Non-feed=0.80 (was 0.85): standard OSINT findings
                        # Rationale: early sprint cycles get rejected by LMDB persistent dedup
                        # (findings from prior sprints), so semantic dedup should be permissive
                        # enough to let new findings through.
                        _semantic_thresh = 0.75 if _is_feed_source else 0.80
                        is_dup = dedup_cache.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                        if is_dup:
                            self._quality_state._quality_duplicate_count += 1
                            return FindingQualityDecision(
                                accepted=False,
                                reason="semantic_duplicate",
                                entropy=entropy,
                                normalized_hash=fingerprint,
                                duplicate=True,
                            )
                except Exception as e:
                    # F196A: Quality gate error must not be silent - log warning.
                    _logger.warning(f"Quality gate error (short_string path): {e}")
            # Short string + no semantic duplicate -> store and accept
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            # P0-2: feed bypass - hot cache write skipped for feed sources
            if not _is_feed_source:
                self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True,
                reason="short_string_skip",
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Entropy threshold check
        # P1-2: Feed-adaptive threshold - feed sources generate short, IOC-heavy
        # text with structurally low entropy (URLs, patterns, hashes). Use a lower
        # threshold for feed sources to avoid rejecting legitimate findings.
        _effective_threshold = (
            0.3 if _is_feed_source else _QUALITY_ENTROPY_THRESHOLD
        )
        if entropy < _effective_threshold:
            self._quality_state._quality_rejected_count += 1
            logger.debug(
                "[QUALITY-GATE] rejected=low_entropy threshold=%.2f entropy=%.2f finding_id=%s is_feed=%s",
                _effective_threshold,
                entropy,
                finding.finding_id[:16] if finding.finding_id else "",
                _is_feed_source,
            )
            return FindingQualityDecision(
                accepted=False,
                reason="low_entropy_rejected",
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Sprint F197B: Semantic dedup BEFORE storing - check before committing LMDB write
        # Sprint F222: Now uses DedupManager's semantic dedup cache
        # Skip if no semantic dedup cache (memory pressure or init failed)
        # Fail-soft: returns duplicate=False on any error (finding accepted even on error)
        dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
        if dedup_cache is not None:
            try:
                dedup_cache_ref = dedup_cache
                text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                if text_for_embed and len(text_for_embed) >= 16:
                    # Threshold tier based on source type: feed=0.80, non-feed=0.85
                    _semantic_thresh = 0.80 if _is_feed_source else 0.85
                    is_dup = dedup_cache_ref.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                    if is_dup:
                        self._quality_state._quality_duplicate_count += 1
                        logger.debug(
                            "[QUALITY-GATE] rejected=semantic_duplicate finding_id=%s",
                            finding.finding_id[:16] if finding.finding_id else "",
                        )
                        return FindingQualityDecision(
                            accepted=False,
                            reason="semantic_duplicate",
                            entropy=entropy,
                            normalized_hash=fingerprint,
                            duplicate=True,
                        )
            except Exception as e:
                # F196A: Quality gate error must not be silent - log warning.
                # Fail-open: embedder/LMDB error -> accept the finding anyway
                _logger.warning(f"Quality gate error (entropy path): {e}")

        # Only reach here if semantic dedup passed or was skipped (fail-open)
        # Now safe to commit to LMDB + hot cache (hot cache skipped for feed sources)
        self._store_persistent_dedup(fingerprint, finding.finding_id)
        # P0-2: feed bypass - hot cache write skipped for feed sources
        if not _is_feed_source:
            self._add_to_hot_cache(fingerprint, finding.finding_id)


        return FindingQualityDecision(
            accepted=True,
            reason=None,
            entropy=entropy,
            normalized_hash=fingerprint,
            duplicate=False,
        )

    # ---------------------------------------------------------------------------
    # Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs
    # ---------------------------------------------------------------------------

    def _assess_finding_quality_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision]:
        """
        Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.

        Identical decision logic to _assess_finding_quality() but pre-computes
        fingerprints and entropies in a single Rust rayon batch call per chunk,
        then walks findings in original order applying URL-first → hot_cache →
        LMDB → short_string → entropy → semantic_dedup.

        Bounded: caller should chunk at 4096 max (Rust BATCH_HARD_CAP).
        Below 100 items falls through to sequential (avoids rayon dispatch overhead).
        Returns list[FindingQualityDecision] in same order as findings.
        Fail-soft: any exception propagates to caller for per-row fallback.
        """
        n = len(findings)
        results: list[FindingQualityDecision | None] = [None] * n

        # Phase 1: pre-compute fingerprints + entropies via Rust batch
        url_fingerprints: list[str] = [''] * n
        entropies: list[float] = [0.0] * n
        fingerprints: list[str] = [''] * n
        url_indices: list[int] = []
        payload_indices: list[int] = []
        texts: list[str] = []

        for idx, f in enumerate(findings):
            url = self._extract_url_from_provenance(f.provenance) if f.provenance else ''
            if url:
                url_fingerprints[idx] = url
                url_indices.append(idx)
                texts.append('')
            else:
                payload_text = f.payload_text if f.payload_text else f.query
                if not (payload_text and payload_text.strip()):
                    payload_text = f.query
                texts.append(payload_text)
                payload_indices.append(idx)

        # Batch URL fingerprints
        if url_indices:
            url_texts = [url_fingerprints[i] for i in url_indices]
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_url_fingerprints is not None:
                try:
                    batch_urls: list[str] = _rust_batch_url_fingerprints(url_texts)
                    for j, idx in enumerate(url_indices):
                        url_fingerprints[idx] = batch_urls[j]
                except Exception:
                    for j, idx in enumerate(url_indices):
                        url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])
            else:
                for j, idx in enumerate(url_indices):
                    url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])

        # Batch payload fingerprints + entropies
        if payload_indices:
            payload_texts = [texts[i] for i in payload_indices]
            # Normalize (rayon batch — not list comprehension)
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_normalize_quality_text is not None:
                try:
                    normalized_batch: list[str] = _rust_batch_normalize_quality_text(payload_texts)
                except Exception:
                    normalized_batch = [_normalize_for_quality(t) for t in payload_texts]
            else:
                normalized_batch = [_normalize_for_quality(t) for t in payload_texts]

            # Batch entropy
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_entropy is not None:
                try:
                    entropies_batch: list[float] = _rust_batch_entropy(normalized_batch)
                except Exception:
                    entropies_batch = [_compute_entropy(t) for t in normalized_batch]
            else:
                entropies_batch = [_compute_entropy(t) for t in normalized_batch]

            # Batch dedup fingerprints
            if _QUALITY_GATE_BATCH_AVAILABLE and _rust_batch_dedup_fingerprints is not None:
                try:
                    fps_batch: list[str] = _rust_batch_dedup_fingerprints(normalized_batch)
                except Exception:
                    try:
                        fps_batch = [_rust_dedup_fingerprint(t) for t in normalized_batch]
                    except Exception:
                        fps_batch = [_compute_dedup_fingerprint(t) for t in normalized_batch]
            else:
                fps_batch = [_compute_dedup_fingerprint(t) for t in normalized_batch]

            for j, idx in enumerate(payload_indices):
                entropies[idx] = entropies_batch[j]
                fingerprints[idx] = fps_batch[j]

        # Phase 2: apply decision logic per finding (same as _assess_finding_quality)
        for idx, f in enumerate(findings):
            url_fp = url_fingerprints[idx]
            fp = fingerprints[idx]
            entropy = entropies[idx]
            is_feed_source = f.source_type == "rss_atom_pipeline"
            text_for_embed = url_fp or (f.payload_text or f.query)
            is_high_conf_ioc = bool(
                text_for_embed and _HIGH_CONF_IOC_RE.match(text_for_embed.strip())
            )

            # Tier 1: hot cache
            if self._hot_cache_lookup(fp) is not None:
                self._quality_state._quality_duplicate_count += 1
                reason = "persistent_duplicate" if url_fp else "duplicate_detected"
                results[idx] = FindingQualityDecision(
                    accepted=False, reason=reason, entropy=entropy,
                    normalized_hash=fp, duplicate=True,
                )
                continue

            # Tier 2: LMDB
            stored_id = self._lookup_persistent_dedup(fp)
            if stored_id is not None:
                self._add_to_hot_cache(fp, stored_id)
                self._quality_state._persistent_duplicate_count += 1
                reason = "persistent_duplicate" if url_fp else "duplicate_detected"
                results[idx] = FindingQualityDecision(
                    accepted=False, reason=reason, entropy=entropy,
                    normalized_hash=fp, duplicate=True,
                )
                continue

            # URL-first: store and accept
            if url_fp:
                self._store_persistent_dedup(fp, f.finding_id)
                if not is_feed_source:
                    self._add_to_hot_cache(fp, f.finding_id)
                results[idx] = FindingQualityDecision(
                    accepted=True, reason=None, entropy=entropy,
                    normalized_hash=fp, duplicate=False,
                )
                continue

            # Short strings: semantic dedup
            if len(fp) < _QUALITY_MIN_ENTROPY_LEN:
                dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
                if dedup_cache is not None and not is_high_conf_ioc:
                    try:
                        if text_for_embed and len(text_for_embed) >= 16:
                            _semantic_thresh = 0.80 if is_feed_source else 0.85
                            is_dup = dedup_cache.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                            if is_dup:
                                self._quality_state._quality_duplicate_count += 1
                                results[idx] = FindingQualityDecision(
                                    accepted=False, reason="semantic_duplicate",
                                    entropy=entropy, normalized_hash=fp, duplicate=True,
                                )
                                continue
                    except Exception:
                        pass
                self._store_persistent_dedup(fp, f.finding_id)
                if not is_feed_source:
                    self._add_to_hot_cache(fp, f.finding_id)
                results[idx] = FindingQualityDecision(
                    accepted=True, reason="short_string_skip", entropy=entropy,
                    normalized_hash=fp, duplicate=False,
                )
                continue

            # Entropy threshold
            _threshold = 0.3 if is_feed_source else _QUALITY_ENTROPY_THRESHOLD
            if entropy < _threshold:
                self._quality_state._quality_rejected_count += 1
                results[idx] = FindingQualityDecision(
                    accepted=False, reason="low_entropy_rejected",
                    entropy=entropy, normalized_hash=fp, duplicate=False,
                )
                continue

            # Semantic dedup
            dedup_cache = self._dedup_manager.semantic_dedup_cache if self._dedup_manager else None
            if dedup_cache is not None and not is_high_conf_ioc:
                try:
                    if text_for_embed and len(text_for_embed) >= 16:
                        _semantic_thresh = 0.80 if is_feed_source else 0.85
                        is_dup = dedup_cache.check_and_cache(text_for_embed, threshold=_semantic_thresh)
                        if is_dup:
                            self._quality_state._quality_duplicate_count += 1
                            results[idx] = FindingQualityDecision(
                                accepted=False, reason="semantic_duplicate",
                                entropy=entropy, normalized_hash=fp, duplicate=True,
                            )
                            continue
                except Exception:
                    pass

            # All passed — store and accept
            self._store_persistent_dedup(fp, f.finding_id)
            if not is_feed_source:
                self._add_to_hot_cache(fp, f.finding_id)
            results[idx] = FindingQualityDecision(
                accepted=True, reason=None, entropy=entropy,
                normalized_hash=fp, duplicate=False,
            )

        assert None not in results, "_assess_finding_quality_batch: 1:1 invariant violated"
        return results  # type: ignore[return-value]

    async def async_ingest_finding(
        self,
        finding: CanonicalFinding,
    ) -> FindingQualityDecision | ActivationResult:
        """
        Sprint 8W: Quality-gated single-finding ingest.

        Layer ABOVE async_record_canonical_finding - applies quality gate first,
        then delegates to legacy storage path on accept.

        Quality gate is CPU-only, deterministic, and cheap.
        Fail-open: if quality helpers raise, the finding is stored via legacy path.

        Returns FindingQualityDecision when rejected/duplicate.
        Returns ActivationResult on accept or fail-open.
        """
        # Phase 1: quality check (fail-open on exception)
        try:
            decision = self._assess_finding_quality(finding)
        except Exception:
            # Fail-open: quality gate failed, but store anyway
            # _accepted_count is NOT incremented here - async_record_canonical_finding
            # handles it on success, and we don't double-count on the fail-open path
            self._quality_state._quality_fail_open_count += 1
            result = await self.async_record_canonical_finding(finding)
            return result

        if not decision.accepted:
            # F216G: record quality gate rejection to bounded ledger
            self._record_quality_rejection(finding, decision)
            return decision

        # Phase 2: legacy storage path (WAL-first)
        result = await self.async_record_canonical_finding(finding)
        # Increment _accepted_count if LMDB write succeeded
        if isinstance(result, dict):
            lmdb_ok = result.get("lmdb_success", False)
        else:
            lmdb_ok = bool(result.lmdb_success)
        if lmdb_ok:
            self._quality_state._accepted_count += 1
        return result

    @_otel_instrumented("duckdb.ingest_batch", component="storage")
    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Sprint 8W: Quality-gated batch ingest.

        Layer ABOVE async_record_canonical_findings_batch - applies quality gate to each
        finding, then delegates acceptable ones to legacy batch storage.

        Quality gate is CPU-only, deterministic, and cheap.
        Fail-open: if quality helpers raise for any finding, that finding is stored
        via legacy path.

        Returns list with len(results) == len(findings) - 1:1 invariant.
        Each entry is FindingQualityDecision (rejected/duplicate) or ActivationResult (accepted).
        """
        if not findings:
            # F275: Even with zero findings, ensure schema is initialized so the
            # DB file is created/touched. This fixes the "DuckDB empty after sprint"
            # bug where 0-findings sprints never touched the schema, leaving an
            # empty .duckdb file (or :memory: with no writes).
            # Schema init is cheap (CREATE TABLE IF NOT EXISTS * 10 tables).
            await self.async_initialize_schema()
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()

        n = len(findings)
        # P1-2: Entry instrumentation - log at entry to diagnose silent-0-write path
        logger.debug(
            "[INGEST-BATCH] entry len(findings)=%d  quality_state=_accepted:%d _rejected:%d _dup:%d _persist_dup:%d",
            n,
            self._quality_state._accepted_count,
            self._quality_state._quality_rejected_count,
            self._quality_state._quality_duplicate_count,
            self._quality_state._persistent_duplicate_count,
        )
        results: list[FindingQualityDecision | ActivationResult | None] = [None] * n
        _accepted_findings: list[CanonicalFinding] = []
        _accepted_indices: list[int] = []

        # F223: Strict chunking to prevent OOM on M1 EIGHTGB
        # F314-4: Use dynamic chunk_size from runtime settings (scaled by UMA state)
        _chunk_size = self._duckdb_settings.get("chunk_size", 1024)  # noqa: N806
        CHUNK_SIZE: int = _chunk_size  # type: ignore[assignment]  # for readability in loop

        # A8HIGH FIX: Removed asyncio.Queue pipeline layer.
        # Previously: asyncio.Queue(adaptive maxsize) + backpressure via q.full() + q.join()
        # serialized chunks unnecessarily — each _piped_storage already calls
        # asyncio.gather(wal, duckdb) internally for concurrent WAL+DuckDB per chunk.
        # Queue added only overhead (queue put/get + task_done) without parallelism benefit.
        #
        # New architecture:
        #   - Quality gate for chunk N fires WAL+DuckDB and immediately continues
        #     to quality gate for chunk N+1 (true pipeline: CPU gate overlaps I/O)
        #   - Bounded concurrency: _duckdb_arrow_executor and _wal_executor are
        #     thread-pool bounded (self._shared_executor) — backpressure is
        #     implicit via thread pool saturation, no queue needed
        #   - WAL-first invariant: async_record_canonical_findings_batch_arrow runs
        #     wal_future and duckdb_future on separate executors via asyncio.gather
        #     inside the Arrow helper — same WAL-first guarantee as before
        #
        # A8HIGH: Removed _pipeline_queue, _get_pipeline_queue, q.full()/q.join(),
        # _piped_storage wrapper with queue task_done(). Storage now directly
        # schedules Arrow batch via create_task; all chunk storages run concurrently.

        pending_tasks: list[tuple[list[int], asyncio.Task[list[ActivationResult]]]] = []  # (accepted_indices, storage_task) per chunk

        for chunk_start in range(0, n, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, n)
            chunk_findings = findings[chunk_start:chunk_end]

            # Sprint P1-2: Batch quality gate via assess_batch (Rust rayon-parallel).
            # Falls back to per-row _assess_finding_quality on any exception.
            fail_open_chunk_findings: list[CanonicalFinding] = []
            fail_open_chunk_indices: list[int] = []
            chunk_accepted_findings: list[CanonicalFinding] = []
            chunk_accepted_indices: list[int] = []

            # BUG-14 FIX: Per-row exception isolation in batch quality gate.
            # Previously: except Exception wrapped entire chunk (up to 1024 items).
            # Now: try/except only around run_in_executor (Rust call).
            # Per-row TemporalAnonymizer exceptions are isolated per-item.
            _batch_rust_ok = False
            try:
                # G-10: Offload CPU-bound quality assessment to thread pool to avoid blocking event loop.
                # _assess_finding_quality_batch is deterministic and thread-safe (no shared mutable state).
                # F300S: Use self._shared_executor instead of None (system default) to:
                #   - Keep all DuckDB I/O threads under unified UMA-aware pool management
                #   - Avoid creating extra threads on M1 8GB (system default = unbounded)
                #   - Enable _adjust_executor_pool dynamic worker count to control ALL pool threads
                loop = asyncio.get_running_loop()
                chunk_decisions: list[FindingQualityDecision] = await loop.run_in_executor(
                    self._shared_executor, lambda cf=chunk_findings: self._assess_finding_quality_batch(cf)
                )
                _batch_rust_ok = True
            except Exception:
                # Sprint P1-2: Rust batch failed — fall back to per-row with isolated exceptions.
                self._quality_state._quality_fail_open_count += 1

            for i_offset, f in enumerate(chunk_findings):
                i = chunk_start + i_offset
                try:
                    if _batch_rust_ok:
                        # P1-2: Validate index BEFORE access to avoid spurious IndexError
                        # masking a real batch-internal failure (e.g. partial result).
                        # If the Rust batch returned fewer decisions than items, treat
                        # the un-assessed items as rejections (not fail-open).
                        if i_offset >= len(chunk_decisions):
                            decision = FindingQualityDecision(
                                accepted=False,
                                reason="batch_incomplete",
                                rejection_reason="quality_gate_batch_incomplete",
                                confidence=0.0,
                                source_quality=0.0,
                            )
                        else:
                            decision = chunk_decisions[i_offset]
                    else:
                        decision = self._assess_finding_quality(f)
                except Exception:
                    # Per-row: if assess fails, fail-open (store anyway).
                    fail_open_chunk_findings.append(f)
                    fail_open_chunk_indices.append(i)
                    continue
                if not decision.accepted:
                    self._record_quality_rejection(f, decision)
                    results[i] = decision
                else:
                    # Sprint F216K §1: TemporalAnonymizer - pre-write timestamp anonymization.
                    # P2-3: except Exception (not bare except) prevents masking SIGINT/SystemExit.
                    if os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1":
                        try:
                            from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
                            if not hasattr(self, "_temporal_anonymizer"):
                                self._temporal_anonymizer = TemporalAnonymizer()
                            f.timestamp = self._temporal_anonymizer.anonymize_timestamp(f.timestamp)
                        except Exception:
                            pass  # Non-fatal: anonymizer failure does not block storage.
                    chunk_accepted_findings.append(f)
                    chunk_accepted_indices.append(i)
            # Sprint D7: batch the fail-open chunk
            if fail_open_chunk_findings:
                batch_results = await self._record_fail_open_batch(
                    fail_open_chunk_findings, results, fail_open_chunk_indices,
                )
                for idx, br in zip(fail_open_chunk_indices, batch_results, strict=False):
                    if br is not None:
                        results[idx] = br

            # A8HIGH: Direct Arrow storage — no queue wrapper.
            # async_record_canonical_findings_batch_arrow handles WAL+DuckDB concurrency
            # internally via asyncio.gather on separate threadpool executors.
            # Each chunk's WAL+DuckDB runs in parallel with the next chunk's quality gate.
            if chunk_accepted_findings:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self.async_record_canonical_findings_batch_arrow(chunk_accepted_findings)
                )
                pending_tasks.append((chunk_accepted_indices, task))

            if chunk_end < n:
                await asyncio.sleep(0)

        # A8HIGH: Wait for all storage tasks concurrently via asyncio.gather.
        # Previously: sequential for-loop over pending_tasks (serial per-chunk wait).
        # Now: gather all at once — wall-clock = max(chunk times), not sum.
        # WAL-first invariant preserved: async_record_canonical_findings_batch_arrow
        # runs wal_future before duckdb_future inside the Arrow helper.
        all_accepted_findings: list[CanonicalFinding] = []
        if pending_tasks:
            tasks_only = [t for _, t in pending_tasks]
            storage_results_all: tuple[list[ActivationResult] | Exception, ...] = await safe_gather_return_exceptions(
                *tasks_only, label="duckdb_store:storage_pipeline"
            )
            # Merge results: zip indices with their corresponding task results.
            # Strict zip ensures index alignment is preserved.
            for (chunk_indices, task), task_result in zip(pending_tasks, storage_results_all, strict=True):
                if isinstance(task_result, Exception):
                    logger.warning("[A8HIGH] storage task failed: %s", task_result)
                    for idx in chunk_indices:
                        if results[idx] is None:
                            results[idx] = ActivationResult(
                                finding_id=str(findings[idx].finding_id),
                                lmdb_success=False,
                                duckdb_success=None,
                                lmdb_key=f"finding:{findings[idx].finding_id}",
                                desync=False,
                                error="pipeline_storage_failed",
                                accepted=False,
                            )
                    continue
                for idx, sr in zip(chunk_indices, task_result, strict=False):
                    results[idx] = sr
                    if getattr(sr, "accepted", False):
                        all_accepted_findings.append(findings[idx])

        # Sprint F241: Graph real-time wire - fire graph update async after accepted write.
        # Graph is ADVISORY ONLY - write path never blocks on graph success/failure.
        # F290: Always-on (no env gate) — bounded, fail-safe, already gated by
        # duckdb_store init (graph is None if DuckPGQGraph fails to init).
        # F310-FIX: use _graph_store() accessor instead of undefined self._graph
        truth_graph = self._graph_store().get_truth_write_graph() if all_accepted_findings else None
        if truth_graph is not None:
            # Sprint 5.4: Wire batch Rust IOC extraction into the graph write path.
            # batch_ioc_extract_unified (rayon-parallel, 2 workers) extracts IOCs from
            # payload_texts in one Rust call instead of slower Python per-finding path.
            # Fail-soft: graph update never blocks ingest on IOC extraction errors.
            if _IOC_EXTRACT_BATCH_AVAILABLE and _rust_batch_ioc_extract is not None:
                try:
                    ioc_texts = [f.payload_text or f.query or "" for f in all_accepted_findings]
                    ioc_results: list[list[tuple[str, str]]] = _rust_batch_ioc_extract(ioc_texts)
                    if ioc_results and truth_graph is not None:
                        buffer_ioc = getattr(truth_graph, "buffer_ioc", None)
                        flush_buffers = getattr(truth_graph, "flush_buffers", None)
                        if callable(buffer_ioc) and callable(flush_buffers):
                            import xxhash
                            for finding_idx, _ in enumerate(all_accepted_findings):
                                for ioc_value, ioc_type in ioc_results[finding_idx]:
                                    _ioc_id = f"{ioc_type}:{xxhash.xxh64(ioc_value.encode()).hexdigest()}"
                                    buffer_ioc(ioc_type, ioc_value, 1.0)
                            flush_buffers()
                except Exception:
                    pass  # fail-soft: graph update is advisory only
            self._schedule_graph_update(all_accepted_findings)

        assert None not in results, "Internal error: 1:1 invariant violated"

        # P1-2: Exit instrumentation - log len(results) + accepted count to diagnose
        # entry > 0 but exit = 0 pattern (write path broken)
        accepted_total = sum(
            1 for r in results
            if getattr(r, "accepted", None) is True
            or (isinstance(r, dict) and r.get("accepted") is True)
        )
        logger.debug(
            "[INGEST-BATCH] exit len(results)=%d  accepted=%d  _accepted_count=%d",
            len(results),
            accepted_total,
            self._quality_state._accepted_count,
        )
        # P1-2: Operator-level confirmation - DuckDB canonical write confirmed
        if accepted_total > 0:
            logger.info(
                "[DuckDB] written %d records (sprint F265-P1-2 canonical write verification)",
                accepted_total,
            )
        # F285: WAL compaction — interval-checked inside wal_manager.compact()
        try:
            if self._wal_manager is not None:
                compact_result = self._wal_manager.compact()
                if compact_result is not None:
                    logger.debug(
                        "[WAL] compact pages_reclaimed=%d pages_free=%d",
                        compact_result.get("pages_reclaimed", -1),
                        compact_result.get("pages_free", -1),
                    )
        except Exception:
            pass  # fail-soft: compaction must never block ingest
        return results  # type: ignore[annotation-unchecked]

    # --------------------------------------------------------------------------
    # Sprint F202A: Evidence Envelope helpers
    # --------------------------------------------------------------------------

    def _envelope_to_payload(self, envelope: FindingEnvelope) -> str | None:
        """
        Sprint F202A §2: Serialize FindingEnvelope to payload_text string.

        Fail-soft: returns None if serialization fails or size exceeds limit.
        Caller degrades to plain finding when None is returned.
        """
        from hledac.universal.knowledge.finding_envelope import (
            FindingEnvelope,
            envelope_size_guard,
            serialize_envelope,
        )
        if not isinstance(envelope, FindingEnvelope):
            return None
        if not envelope_size_guard(envelope):
            return None
        return serialize_envelope(envelope)

    def _payload_to_envelope(self, payload_text: str | None) -> FindingEnvelope | None:
        """
        Sprint F202A §2: Deserialize FindingEnvelope from payload_text string.

        Fail-soft: returns None if payload_text is None/empty, parsing fails,
        or required audit_reason field is missing.
        """
        from hledac.universal.knowledge.finding_envelope import deserialize_envelope
        return deserialize_envelope(payload_text)

    def _store_envelope_payload(self, finding_id: str, payload_text: str) -> bool:
        """
        Sprint F202A §2: Update LMDB WAL entry with envelope payload_text.

        Called after initial ingest when envelope is attached post-hoc.
        Returns True if LMDB update succeeded.
        """
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore
            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return False
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
            key = f"finding:{finding_id}"
            # Read existing WAL entry and update payload_text
            existing = self._wal_lmdb.get(key)
            if existing is None:
                return False
            if isinstance(existing, dict):
                existing["payload_text"] = payload_text
            else:
                return False
            return self._wal_lmdb.put(key, existing)
        except Exception:
            return False

    async def async_ingest_findings_with_envelope(
        self,
        findings: list[CanonicalFinding],
        envelopes: list[FindingEnvelope],
    ) -> list[ActivationResult | FindingQualityDecision]:
        """
        Sprint F202A §2: Ingest findings WITH pre-built evidence envelopes.

        Envelope is serialized and stored in payload_text field of LMDB WAL.
        Size guard: oversized envelope -> degraded to plain finding (no crash).
        Returns same 1:1 result list as async_ingest_findings_batch.

        Args:
            findings:  List of CanonicalFinding instances
            envelopes: List of FindingEnvelope instances (same length as findings)
        """
        from hledac.universal.knowledge.finding_envelope import FindingEnvelope

        if not findings:
            return []
        if len(envelopes) != len(findings):
            # Fall back to plain ingest if length mismatch
            return await self.async_ingest_findings_batch(findings)

        # Build payload_text overrides using size guard
        payload_overrides: dict[int, str | None] = {}
        for i, env in enumerate(envelopes):
            if not isinstance(env, FindingEnvelope):
                payload_overrides[i] = None
                continue
            from hledac.universal.knowledge.finding_envelope import envelope_size_guard
            if not envelope_size_guard(env):
                payload_overrides[i] = None
            else:
                from hledac.universal.knowledge.finding_envelope import serialize_envelope
                payload_overrides[i] = serialize_envelope(env)

        # Run standard ingest
        results = await self.async_ingest_findings_batch(findings)

        # Patch LMDB payload_text for findings that got an envelope
        for i, override in payload_overrides.items():
            if override is None:
                continue
            if results[i].get("lmdb_success") if isinstance(results[i], dict) else False:
                fid = results[i].get("finding_id", "")
                if fid:
                    self._store_envelope_payload(fid, override)

        return results

    def _sync_read_envelope(self, finding_id: str) -> FindingEnvelope | None:
        """
        Sprint F202A §3: Read and deserialize envelope from LMDB WAL entry.

        Returns None if finding doesn't exist or has no valid envelope.
        Fail-soft: does not raise.
        """
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore
            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return None
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
            key = f"finding:{finding_id}"
            entry = self._wal_lmdb.get(key)
            if entry is None or not isinstance(entry, dict):
                return None
            payload_text = entry.get("payload_text")
            return self._payload_to_envelope(payload_text)
        except Exception:
            return None

    async def async_get_findings_with_envelope(
        self,
        limit: int = 20,
    ) -> list[dict]:
        """
        Sprint F202A §3: Read recent findings with deserialized envelopes.

        Returns list of dicts with envelope fields attached:
          {finding_id, query, source_type, confidence, ts, provenance,
           payload_text, envelope: FindingEnvelope | None}
        Fail-soft: any finding without valid envelope has envelope=None.
        """
        raw_findings = await self.async_query_recent_findings(limit=limit)
        result = []
        for f in raw_findings:
            fid = f.get("id", f.get("finding_id", ""))
            env = self._sync_read_envelope(fid) if fid else None
            item = dict(f)
            item["envelope"] = env
            result.append(item)
        return result

    def _sync_insert_findings_bulk_as_tuples(
        self,
        rows: list[list],
    ) -> int:
        """
        Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).  # noqa: E501
        MUST be called on the worker thread.
        Returns number of successfully inserted records.
        """
        return self._qe().insert_findings_bulk_as_tuples(rows)

    def _sync_record_canonical_findings_batch_arrow(
        self,
        findings: list[CanonicalFinding],
    ) -> tuple[int, str | None]:
        """
        Sprint P0-4: Arrow zero-copy bulk insert for CanonicalFinding list.

        MUST be called on the worker thread (thread-affine connection).
        Returns (inserted_count, error_type):
          - (n, None) on success where n = number of rows in input table
          - (0, error_type) on any failure, where error_type is one of:
              "pyarrow_not_installed" - pyarrow import failed
              "table_build_failed"    - pa.Table.from_arrays failed
              "duckdb_insert_failed" - QueryExecutor.insert_findings_bulk_arrow failed

        Distinct from the legacy `_canonical_findings_batch_to_activation_results`
        in three ways:
          1. Builds a single pyarrow.Table with columnar zero-copy arrays.
          2. Calls QueryExecutor.insert_findings_bulk_arrow (register + INSERT...SELECT).
          3. Does NOT touch LMDB WAL - caller is responsible for that half (or falls
             back to the legacy path which does both). This split keeps the Arrow
             path optional and side-effect-free at the WAL layer.

        Fail-soft: any error returns (0, error_type) and the async wrapper falls back
        to legacy. The error_type is used for typed telemetry.
        """
        if not findings:
            return (0, None)
        # Hot-path: O(1) sys.modules check cached at module level.
        if not _check_pyarrow_available():
            return (0, "pyarrow_not_installed")

        # F275-3: Try Rust fast path first (single-pass IPC, rayon-parallel columns).
        # Falls back to Python pa.Table.from_arrays on error or if Rust unavailable.
        import pyarrow as _pa

        if _RUST_ARROW_AVAILABLE and _rust_build_arrow_batch is not None:
            try:
                # Build dicts for Rust (matches duckdb_subprocess_writer pattern).
                findings_dicts = [
                    {
                        "id": f.finding_id,
                        "query": f.query,
                        "source_type": f.source_type,
                        "confidence": f.confidence,
                        "ts": f.ts,
                        "provenance_json": _provenance_to_arrow_native(f.provenance),
                    }
                    for f in findings
                ]
                ipc_bytes = _rust_build_arrow_batch(findings_dicts)
                if ipc_bytes and len(ipc_bytes) > 8:
                    reader = _pa.ipc.open_record_batch_reader(ipc_bytes)
                    table = reader.read_next_batch()
                    # Delegate to QueryExecutor - keeps SQL + register/unregister in one place.
                    duckdb_count, duckdb_err = self._qe().insert_findings_bulk_arrow(table)
                    if duckdb_err is not None:
                        return (0, "duckdb_insert_failed")
                    return (duckdb_count, None)
            except Exception:  # noqa: BLE001
                pass  # Fall through to Python path

        # Python fallback: 6× list-comprehension loops
        try:
            # Build columnar arrays - zero-copy for str/float, single alloc per column.
            # Thread2b + F290-1: msgspec.json.encode bytes → pa.array reads C buffer zero-copy.
            # F290-1 FIX: provenance is already bytes from _provenance_to_arrow_native (no decode needed).
            provenance_raw = [_provenance_to_arrow_native(f.provenance) for f in findings]
            provenance_arr = _pa.array(
                provenance_raw,
                type=_pa.string(),
            )
            id_arr = _pa.array(
                [f.finding_id for f in findings], type=_pa.string()
            )
            query_arr = _pa.array(
                [f.query for f in findings], type=_pa.string()
            )
            src_arr = _pa.array(
                [f.source_type for f in findings], type=_pa.string()
            )
            conf_arr = _pa.array(
                [f.confidence for f in findings], type=_pa.float64()
            )
            ts_arr = _pa.array(
                [f.ts for f in findings], type=_pa.float64()
            )
            table = _pa.Table.from_arrays(
                [id_arr, query_arr, src_arr, conf_arr, ts_arr, provenance_arr],
                names=[
                    "id", "query", "source_type", "confidence", "ts", "provenance_json",
                ],
            )
        except Exception as e:
            # Build failure (e.g. exotic provenance type) - caller falls back.
            import logging as _logging
            _logging.getLogger(__name__).error(
                f"[P0-4 Arrow] Table build failed: {type(e).__name__}: {e}"
            )
            return (0, "table_build_failed")

        # Delegate to QueryExecutor - keeps SQL + register/unregister in one place.
        duckdb_count, duckdb_err = self._qe().insert_findings_bulk_arrow(table)
        if duckdb_err is not None:
            return (0, "duckdb_insert_failed")
        return (duckdb_count, None)

    # ----------------------------------------------------------------------
    # Sprint P1-2: DuckDB Single-Writer Variant 2 - dedicated WAL + DuckDB executors
    # ----------------------------------------------------------------------

    def _wal_put_many_sync(
        self,
        findings: list[CanonicalFinding],
    ) -> bool:
        """
        Sprint P1-2: WAL-only sync helper - DuckDB Single-Writer Variant 2.

        Runs on _wal_executor. LMDB WAL is pure I/O so executor occupancy is brief.
        Caller is responsible for DuckDB step (separate executor, sequential invariant).

        Returns True if WAL succeeded for all findings.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    _logger.warning("[P1-2 WAL] no wal_root")
                    return False
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

            items = []
            for f in findings:
                key = f"finding:{f.finding_id}"
                wal_payload = {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                }
                items.append((key, wal_payload))

            if items:
                lmdb_ok = self._wal_manager.wal_put_many(items) if hasattr(
                    self._wal_manager, "wal_put_many"
                ) else False
                if not lmdb_ok:
                    _logger.warning(f"[P1-2 WAL] Batch WAL failed for {len(items)} items")
                    return False
            return True
        except Exception as e:
            _logger.error(f"[P1-2 WAL] Batch WAL exception: {e}")
            return False

    def _duckdb_arrow_sync(
        self,
        findings: list[CanonicalFinding],
    ) -> tuple[int, str | None]:
        """
        Sprint P1-2: DuckDB Arrow-only sync helper - DuckDB Single-Writer Variant 2.

        Runs on _duckdb_arrow_executor. Caller is responsible for WAL step
        (separate executor, sequential WAL-first invariant).

        Returns (inserted_count, error_type) - same shape as
        _sync_record_canonical_findings_batch_arrow.
        """
        return self._sync_record_canonical_findings_batch_arrow(findings)

    def _sync_record_canonical_findings_batch_arrow_standalone(
        self,
        findings: list[CanonicalFinding],
    ) -> list[dict]:
        """
        Arrow zero-copy fallback for legacy batch path (async_record_canonical_findings_batch).

        Combines WAL + DuckDB Arrow into a single sync helper so the legacy fallback
        path also benefits from zero-copy Arrow INSERT. Replaces the tuple-based
        _canonical_findings_batch_to_activation_results path entirely.

        MUST be called on the worker thread.
        Returns list[dict] with 1:1 mapping.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        if not findings:
            return []

        ret: list[dict] = []

        # Step 1: LMDB WAL first (WAL-first invariant).
        lmdb_ok = False
        try:
            if not hasattr(self, "_wal_manager") or self._wal_manager is None:
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    for _ in findings:
                        ret.append({
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "no wal root",
                        })
                    return ret
                self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
                self._wal_manager.initialize()

            items = []
            for f in findings:
                key = f"finding:{f.finding_id}"
                wal_payload = {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                }
                items.append((key, wal_payload))

            if items:
                lmdb_ok = self._wal_manager.wal_put_many(items) if hasattr(
                    self._wal_manager, "wal_put_many"
                ) else False
                if not lmdb_ok:
                    _logger.warning(
                        f"[Arrow-standalone] WAL failed for {len(items)} items"
                    )
                    for _ in findings:
                        ret.append({
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "lmdb batch failed",
                        })
                    return ret
        except Exception as e:
            _logger.error(f"[Arrow-standalone] WAL exception: {e}")
            for _ in findings:
                ret.append({
                    "lmdb_success": False,
                    "duckdb_success": None,
                    "error": str(e),
                })
            return ret

        # Step 2: DuckDB Arrow bulk - zero-copy via C Data Interface.
        duckdb_count, duckdb_err = self._sync_record_canonical_findings_batch_arrow(findings)
        if duckdb_err is not None:
            _logger.error(f"[Arrow-standalone] DuckDB Arrow failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            # Partial insert = duplicates (ON CONFLICT DO NOTHING), NOT an error.
            # All rows reached DuckDB; duplicates were silently ignored.
            duckdb_all_ok = True
        else:
            duckdb_all_ok = True

        # Step 3: Build per-finding results (1:1).
        for f in findings:
            ret.append({
                "finding_id": f.finding_id,
                "lmdb_success": lmdb_ok,
                "duckdb_success": duckdb_all_ok,
                "error": duckdb_err,
            })
        return ret

    # ------------------------------------------------------------------
    # Async shutdown (new in 8AS)
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """
        Async idempotent shutdown - canonical async cleanup path.

        Delegates to _do_sync_close(emergency=False) for shared synchronous cleanup,
        then performs async-only operations (bg task cancellation).

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return

        # P3-2 / F300S-FIX: Cancel _checkpoint_task BEFORE _do_sync_close.
        # _checkpoint_loop uses self._file_conn which is closed in _do_sync_close.
        # Must cancel first so the loop exits cleanly via CancelledError before
        # its connection reference becomes invalid.
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            self._checkpoint_task = None

        # Shared synchronous cleanup (same as close() but skips async graph closes)
        self._do_sync_close(emergency=False)

        # Async-only: await async graph/semantic store closes
        await self._do_async_close()

        # Async-only: cancel background tasks (requires running loop)
        _bg = getattr(self, "_bg_tasks", None)
        if _bg:
            for t in _bg:
                t.cancel()
            await safe_gather_fire_and_forget(*_bg, label="duckdb_store:5746")
            _bg.clear()

    # ------------------------------------------------------------------
    # Sprint 8TC: RRF Fusion - Reciprocal Rank Fusion přes 4 signály
    # ------------------------------------------------------------------

    async def rrf_rank_findings(self, query: str, k: int = 30) -> list[dict]:
        """
        Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.

        Signály:
          1. semantic_score  - z LanceDB ANN (pokud dostupný)
          2. pattern_count   - počet pattern matche
          3. ioc_degree      - počet navázaných IOC uzlů
          4. recency_score   - inverzní age (novější = vyšší)

        SQL RRF: SUM(1.0 / (k + rank_i)) přes všechny signály.
        Chybějící sloupce se přidávají dynamicky přes ALTER TABLE.

        Args:
            query: Search query string to filter canonical_findings
            k: RRF constant (default 30 - snižuje vliv nízkých ranků)

        Returns:
            list[dict] s keys: finding_id, content, rrf_score, semantic_score,
            pattern_count, ioc_degree, ts
        """
        if not self._initialized or self._closed:
            return []

        # Sprint DuckDB Lazy Init (F265X): ensure connection on first actual query
        self.ensure_connected()


        # Dynamicky přidat chybějící sloupce do canonical_findings tabulky
        # (pokud existuje, jinak používáme canonical_findings)
        loop = asyncio.get_running_loop()

        def _sync_rrf_rank() -> list[dict]:
            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return []

                # RRF SQL - bulk VALUES approach: 3 signály v jednom VALUES clause
                # namísto 3* CTE + UNION ALL - O(1) SQL length místo O(N)
                # s1=confidence, s2=ts, s3=source_type ordinal
                rrf_sql = """
                WITH
                  ranked AS (
                      SELECT id AS finding_id,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(confidence, 0) DESC) AS r1,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(ts, 0) DESC) AS r2,
                             ROW_NUMBER() OVER (ORDER BY source_type ASC) AS r3
                        FROM canonical_findings
                       WHERE query = ?1
                  ),
                  rrf AS (
                      SELECT finding_id, r1 AS r FROM ranked
                      UNION ALL
                      SELECT finding_id, r2 AS r FROM ranked
                      UNION ALL
                      SELECT finding_id, r3 AS r FROM ranked
                  )
                SELECT f.id AS finding_id,
                       f.query AS content,
                       f.ts,
                       f.confidence AS semantic_score,
                       f.source_type,
                       f.confidence,
                       SUM(1.0 / (?2 + rrf.r)) AS rrf_score
                  FROM rrf
                  JOIN canonical_findings f ON f.id = rrf.finding_id
                 WHERE f.query = ?1
                 GROUP BY f.id, f.query, f.ts, f.confidence, f.source_type
                 ORDER BY rrf_score DESC
                 LIMIT ?2
                """

                rows = list(self.arrow_fetch_batch(conn, rrf_sql, [query, k]))
                return [
                    {
                        "finding_id": str(r[0]),
                        "content": r[1] or "",
                        "ts": r[2] or 0.0,
                        "semantic_score": r[3] or 0.0,
                        "source_type": r[4] or "",
                        "confidence": r[5] or 0.0,
                        "rrf_score": r[6] or 0.0,
                    }
                    for r in rows
                ]
            except Exception:
                return []

        return await loop.run_in_executor(self._executor, _sync_rrf_rank)

    # ------------------------------------------------------------------
    # Sprint 8L: Bounded Startup Replay
    # ------------------------------------------------------------------

    async def _bounded_startup_replay(
        self,
        replay_pending_limit: int,
        replay_timeout_s: float,
    ) -> None:
        """
        Sprint 8L: Time-boxed startup replay integrated into async_initialize.

        Scans pending_duckdb_sync:* markers, replays up to replay_pending_limit
        of them, and respects replay_timeout_s wall-time budget.

        Boot barrier: _startup_ready is NOT set during replay, so activation
        writes are held off until replay completes or times out.

        Kooperativní yield: asyncio.sleep(0) between chunks to avoid
        starving the event loop during long replay runs.

        Args:
            replay_pending_limit: Maximum markers to replay
            replay_timeout_s:    Wall-time budget in seconds
        """
        import time as _time

        lock = self._ensure_replay_lock()
        deadline = _time.monotonic() + replay_timeout_s

        # Eager scan - not lazy, not over closed txn
        all_markers = self._wal_scan_pending_sync_markers()
        if not all_markers:
            return

        # Deduplicate
        seen_ids: set = set()
        unique_markers: list[dict[str, Any]] = []
        for m in all_markers:
            fid = m.get("id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_markers.append(m)
        del seen_ids

        # Apply limit
        markers_to_replay = unique_markers[:replay_pending_limit]
        del unique_markers  # free memory early

        async with lock:
            for i, marker in enumerate(markers_to_replay):
                # Time-box check
                if _time.monotonic() > deadline:
                    break
                fid = marker.get("id", "")
                if not fid:
                    continue
                # Kooperativní yield between chunks (REPLAY_CHUNK_SIZE markers each)
                if i > 0 and i % self.REPLAY_CHUNK_SIZE == 0:
                    await asyncio.sleep(0)
                # Individual marker replay with timeout via the event loop
                try:
                    async with asyncio.timeout(max(deadline - _time.monotonic(), 0.1)):
                        await self.async_replay_single_pending_marker(fid)
                except TimeoutError:
                    # Timeout on single marker - stop replay, leave remaining pending
                    break

    # ------------------------------------------------------------------
    # Sprint 8H: Pending-Sync Recovery API
    # ------------------------------------------------------------------

    def _ensure_replay_lock(self) -> asyncio.Lock:
        """Lazily initialize the replay lock on the current event loop."""
        if self._replay_lock is None:
            self._replay_lock = asyncio.Lock()
        return self._replay_lock

    async def async_replay_single_pending_marker(
        self,
        finding_id: str,
    ) -> ReplayResult:
        """
        Sprint 8H: Replay a single pending marker by finding_id.

        Recovery semantics per marker:
          1. Marker exists? -> marker_found
          2. WAL finding:{id} truth exists? -> wal_truth_found
          3. If truth missing -> failure (can't recover)
          4. DuckDB write via same safe path as activation
          5. Fresh read-back from new connection confirms durability
          6. Success -> clear pending marker
          7. Failure -> bump retry count; if >= MAX_RETRY_COUNT -> dead-letter

        Idempotency: if DuckDB already has the record, consider it a success.

        Args:
            finding_id: The finding identifier to replay.

        Returns:
            ReplayResult with all fields populated.
        """
        # Lazy init of replay lock
        self._ensure_replay_lock()

        result: ReplayResult = ReplayResult(
            finding_id=finding_id,
            marker_found=False,
            wal_truth_found=False,
            duckdb_written=False,
            marker_cleared=False,
            read_back_verified=False,
            deadlettered=False,
            retry_count=0,
            error=None,
        )

        if self._closed:
            result["error"] = "store closed"
            return result

        # Step 1: Get marker
        marker = self._wal_get_pending_marker(finding_id)
        if marker is None:
            # No marker -> check if DuckDB already has it (idempotent success)
            try:
                loop = asyncio.get_running_loop()
                already_there = await loop.run_in_executor(
                    self._executor,
                    self._sync_verify_duckdb_record,
                    finding_id,
                )
                if already_there:
                    result["marker_found"] = False
                    result["wal_truth_found"] = False
                    result["duckdb_written"] = True
                    result["read_back_verified"] = True
                    result["error"] = None
                    return result
            except Exception:
                pass
            result["marker_found"] = False
            result["error"] = f"no pending marker found for {finding_id}"
            return result

        result["marker_found"] = True

        # Step 2: Check WAL truth
        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                result["wal_truth_found"] = False
                result["error"] = "WAL not initialized"
                return result
            wal_key = f"finding:{finding_id}"
            wal_record = self._wal_lmdb.get(wal_key)
            if wal_record is None:
                result["wal_truth_found"] = False
                result["error"] = f"WAL truth missing for {finding_id}"
                return result
            result["wal_truth_found"] = True
        except Exception as e:
            result["wal_truth_found"] = False
            result["error"] = f"WAL lookup failed: {e}"
            return result

        # Step 3: Get current retry count
        retry_count = marker.get("_retry_count", 0)
        result["retry_count"] = retry_count

        # Step 4: DuckDB write
        loop = asyncio.get_running_loop()
        try:
            db_written = await loop.run_in_executor(
                self._executor,
                self._sync_replay_single_marker,
                finding_id,
                marker,
            )
            result["duckdb_written"] = db_written
        except Exception as e:
            result["duckdb_written"] = False
            result["error"] = f"DuckDB write exception: {e}"

        if not result["duckdb_written"]:
            # Failure: bump retry count
            new_retry = self._get_and_bump_retry_count(finding_id)
            result["retry_count"] = new_retry
            if new_retry >= self.MAX_RETRY_COUNT:
                # Dead-letter: move to dead-letter namespace, clear pending
                dl_ok = self._wal_write_deadletter_marker(
                    finding_id=finding_id,
                    query=marker.get("query", ""),
                    source_type=marker.get("source_type", "unknown"),
                    confidence=marker.get("confidence", 1.0),
                    error=result["error"] or "max retries exceeded",
                    retry_count=new_retry,
                )
                if dl_ok:
                    self._wal_clear_pending_sync_marker(finding_id)
                    result["deadlettered"] = True
                    result["marker_cleared"] = True
            return result

        # Step 5: Fresh read-back from new connection
        try:
            read_back_ok = await loop.run_in_executor(
                self._executor,
                self._sync_verify_duckdb_record,
                finding_id,
            )
            result["read_back_verified"] = read_back_ok
        except Exception as e:
            result["read_back_verified"] = False
            result["error"] = f"read-back failed: {e}"
            return result

        # Step 6: Only clear marker after verified success
        if result["read_back_verified"]:
            cleared = self._wal_clear_pending_sync_marker(finding_id)
            result["marker_cleared"] = cleared

        return result

    async def async_replay_all_pending_duckdb_sync(
        self,
        limit: int | None = None,
    ) -> list[ReplayResult]:
        """
        Sprint 8H: Replay all pending markers with chunking and event-loop yields.

        Uses per-instance replay lock to prevent concurrent replay of same markers.
        Processes markers in chunks of REPLAY_CHUNK_SIZE, yielding to event loop
        between chunks to avoid starving live operations.

        Idempotency: markers that already exist in DuckDB are treated as success.

        Args:
            limit: Optional maximum number of markers to replay. None = all.

        Returns:
            list[ReplayResult], one per processed marker.
        """
        if self._closed:
            return []

        lock = self._ensure_replay_lock()

        # Scan all pending markers (eager list, not lazy)
        all_markers = self._wal_scan_pending_sync_markers()
        if not all_markers:
            return []

        # Deduplicate by id (scan may return same id if multiple markers exist)
        seen_ids: set = set()
        unique_markers: list[dict[str, Any]] = []
        for m in all_markers:
            fid = m.get("id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_markers.append(m)
        del seen_ids

        # Apply limit
        if limit is not None:
            unique_markers = unique_markers[:limit]

        results: list[ReplayResult] = []
        chunk_size = self.REPLAY_CHUNK_SIZE

        async with lock:
            for i in range(0, len(unique_markers), chunk_size):
                chunk = unique_markers[i : i + chunk_size]
                for marker in chunk:
                    fid = marker.get("id", "")
                    if not fid:
                        continue
                    result = await self.async_replay_single_pending_marker(fid)
                    results.append(result)
                # Yield to event loop between chunks
                if i + chunk_size < len(unique_markers):
                    await asyncio.sleep(0)

        return results

    # ------------------------------------------------------------------
    # Diagnostic properties (for tests)
    # ------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Return True if sidecar was successfully initialized."""
        return self._initialized

    @property
    def is_closed(self) -> bool:
        """Return True if sidecar has been shut down."""
        return self._closed

    @property
    def db_path(self) -> Path | None:
        """Return the database path (None for :memory: mode)."""
        return self._db_path

    @property
    def temp_dir(self) -> Path | None:
        """Return the temp directory path (None if not using RAMDISK)."""
        return self._temp_dir

    @property
    def memory_limit(self) -> str:
        """Return the configured memory limit string."""
        return self._memory_limit

    @property
    def max_temp(self) -> str:
        """Return the configured max temp size string."""
        return self._max_temp

    @property
    def is_ramdisk_mode(self) -> bool:
        """Return True if running in RAMDISK-active mode."""
        return self._temp_dir is not None

    def size_bytes(self) -> int | None:
        """Return the database file size in bytes, or None for :memory: mode."""
        if self._db_path is None:
            return None
        try:
            return self._db_path.stat().st_size
        except OSError:
            return None

    async def vacuum_async(self) -> bool:
        """
        Execute VACUUM ANALYZE on the DuckDB file to reclaim space after deletions.

        Only available for file mode (_db_path is not None). Returns True on success.
        Fail-safe: any error is logged and False is returned.
        """
        if self._db_path is None:
            return False
        try:
            import psutil
            total_ram = psutil.virtual_memory().total
            size = self.size_bytes()
            if size is not None and size > 3 * (1024**3) and total_ram < 10 * (1024**3):
                logger.warning(
                    "[duckdb_vacuum] CRITICAL: DuckDB %.1fGB on %.1fGB RAM system — vacuum recommended",
                    size / (1024**3),
                    total_ram / (1024**3),
                )
        except Exception:
            pass  # noqa: BLE001  # psutil unavailable, skip RAM check

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._vacuum_sync)
            return True
        except Exception as e:
            logger.warning(f"[duckdb_vacuum] VACUUM failed: {e}")
            return False

    def _vacuum_sync(self) -> None:
        """Execute VACUUM ANALYZE synchronously on worker thread."""
        if self._db_path is None:
            return
        # Use a separate connection for VACUUM (cannot run on the same conn used for writes)
        duckdb = _get_duckdb()
        tmp_conn = duckdb.connect(str(self._db_path), read_only=False)
        try:
            tmp_conn.execute("VACUUM")
        finally:
            tmp_conn.close()

    async def async_vacuum_if_needed(self, threshold_bytes: int = 2 * (1024**3)) -> bool:
        """
        Conditionally vacuum if the DB file exceeds threshold_bytes.

        Args:
            threshold_bytes: size above which vacuum is triggered (default 2GB)

        Returns True if vacuum was triggered and succeeded, False otherwise.
        """
        size = self.size_bytes()
        if size is None:
            return False
        if size > threshold_bytes:
            logger.info(f"[duckdb_vacuum] DB size {size / (1024**3):.1f}GB > threshold, running VACUUM")
            return await self.vacuum_async()
        return False

    @property
    def executor(self) -> ThreadPoolExecutor:
        """Return the internal executor (for test introspection)."""
        return self._executor

    # ------------------------------------------------------------------
    # Sprint 8L: Telemetry / Operability Helpers (LMDB-only, for observability)
    # ------------------------------------------------------------------

    def pending_marker_count(self) -> int:
        """
        Sprint 8L: Return the number of pending_duckdb_sync:* markers in WAL LMDB.

        Cheap O(n) prefix scan - bounded by REPLAY_CHUNK_SIZE scan.
        Used for observability and benchmarking.
        """
        markers = self._wal_scan_pending_sync_markers()
        return len(markers)

    def deadletter_marker_count(self) -> int:
        """
        Sprint 8L: Return the number of deadletter_duckdb_sync:* markers in WAL LMDB.

        Cheap O(n) prefix scan.
        Used for observability and monitoring.
        """
        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                return 0
            env = self._wal_lmdb._env
            if env is None:
                return 0
            count = 0
            prefix = self.DEADLETTER_PREFIX.encode("utf-8")
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix):
                    for key_bytes, _ in cursor.iternext():
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")  # noqa: E501
                        if not key.startswith(self.DEADLETTER_PREFIX):
                            break
                        count += 1
            return count
        except Exception:
            return 0

    @property
    def startup_ready(self) -> bool:
        """Sprint 8L: True if boot barrier has been lifted (store accepts writes)."""
        return self._startup_ready.is_set()

    @property
    def startup_replay_done(self) -> bool:
        """Sprint 8L: True if startup replay has run (regardless of outcome)."""
        return self._startup_replay_done

    # ------------------------------------------------------------------
    # Hardening invariants (Sprint 1B)
    # ------------------------------------------------------------------

    @property
    def invariant_memory_limit(self) -> str:
        """Return configured memory_limit string."""
        return self._memory_limit

    @property
    def invariant_max_temp(self) -> str:
        """Return configured max_temp_directory_size string."""
        return self._max_temp

    @property
    def invariant_temp_dir(self) -> Path | None:
        """Return configured temp_directory path (None if :memory: mode)."""
        return self._temp_dir

    def invariant_validate(self) -> dict:
        """
        Validate hardening invariants.

        Returns dict with keys:
            - has_no_gpu_pragma: bool
            - memory_limit_ok: bool (1GB or less)
            - temp_size_ok: bool (1GB or 0GB for :memory:)
            - temp_dir_on_ramdisk: bool (temp_dir under RAMDISK_ROOT if set)
        """
        results = {
            "has_no_gpu_pragma": True,
            "memory_limit_ok": False,
            "temp_size_ok": False,
            "temp_dir_on_ramdisk": False,
        }

        # Memory limit: 1GB or less
        try:
            mem_val = self._memory_limit.strip().upper()
            if mem_val.endswith("GB"):
                mem_gb = float(mem_val[:-2])
                results["memory_limit_ok"] = mem_gb <= 1.0
            elif mem_val.endswith("MB"):
                mem_mb = float(mem_val[:-2])
                results["memory_limit_ok"] = mem_mb <= 1024
            else:
                results["memory_limit_ok"] = True  # permissive
        except Exception:
            results["memory_limit_ok"] = False

        # Temp size: 1GB or 0GB for :memory: fallback
        try:
            temp_val = self._max_temp.strip().upper()
            if temp_val in ("0GB", "0", "0MB"):
                results["temp_size_ok"] = self._temp_dir is None  # :memory: mode
            elif temp_val.endswith("GB"):
                temp_gb = float(temp_val[:-2])
                results["temp_size_ok"] = temp_gb <= 1.0
            elif temp_val.endswith("MB"):
                temp_mb = float(temp_val[:-2])
                results["temp_size_ok"] = temp_mb <= 1024
            else:
                results["temp_size_ok"] = True
        except Exception:
            results["temp_size_ok"] = False

        # Temp dir on RAMDISK: check if temp_dir is under RAMDISK_ROOT
        if self._temp_dir is not None:
            try:
                from hledac.universal.paths import RAMDISK_ROOT

                results["temp_dir_on_ramdisk"] = str(self._temp_dir).startswith(str(RAMDISK_ROOT))
            except Exception:
                results["temp_dir_on_ramdisk"] = False
        else:
            # No temp_dir means :memory: mode - this is OK
            results["temp_dir_on_ramdisk"] = True

        return results

    # ------------------------------------------------------------------
    # Internal helper - shared close logic
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sprint 8A: Activation helper - structured result -> LMDB WAL -> DuckDB
    # ------------------------------------------------------------------

    def _wal_write_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Sprint 8A: Write a single finding to LMDB WAL (sync, no await).

        LMDB key format:  finding:{id}
        Value: serialized dict with id, query, source_type, confidence, ts

        Returns True if LMDB write succeeded.

        Delegation: Sprint F233A micro-cleanup - routes through WALManager
        to eliminate the residual direct LMDB WAL path.
        """
        # Sprint F233A: Delegate to WALManager (removes residual _wal_lmdb seam)
        if self._wal_manager is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is None:
                return False
            self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
            self._wal_manager.initialize()

        return self._wal_manager.wal_write_finding(
            finding_id=finding_id,
            query=query,
            source_type=source_type,
            confidence=confidence,
        )

    def _activation_record_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> dict:
        """
        Sprint 8A: Record a structured finding - LMDB WAL first, DuckDB second.

        Mapping:
          result.id or uuid4() -> id
          context.query or "" -> query
          source_type from schema/type name -> source_type
          result.confidence or 1.0 -> confidence
          time.time() -> ts

        Partial failure semantics:
          - LMDB OK + DuckDB FAIL -> LMDB remains truth, log desync, return duckdb_success=False
          - LMDB FAIL + DuckDB SKIP -> return lmdb_success=False, duckdb_success=None

        Returns dict with keys: lmdb_success, duckdb_success, finding_id, query
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {
            "lmdb_success": False,
            "duckdb_success": None,
            "finding_id": finding_id,
            "query": query,
        }

        # Step 1: LMDB WAL first
        lmdb_ok = self._wal_write_finding(finding_id, query, source_type, confidence)
        result["lmdb_success"] = lmdb_ok

        if not lmdb_ok:
            _logger.warning(f"[Sprint 8A] WAL-DuckDB desync: LMDB write failed for {finding_id}")
            return result

        # Step 2: DuckDB second (only if LMDB succeeded)
        try:
            # _sync_insert_finding uses persistent _file_conn or _persistent_conn
            db_ok = self._sync_insert_finding(finding_id, query, source_type, confidence)
            result["duckdb_success"] = db_ok
            if not db_ok:
                _logger.error(f"[Sprint 8A] WAL-DuckDB desync: DuckDB write failed for {finding_id}, LMDB preserved")
                # Sprint 8F: Write pending-sync marker for future recovery
                self._wal_write_pending_sync_marker(finding_id, query, source_type, confidence)
        except Exception as e:
            result["duckdb_success"] = False
            _logger.error(f"[Sprint 8A] WAL-DuckDB desync: DuckDB exception for {finding_id}: {e}, LMDB preserved")
            # Sprint 8F: Write pending-sync marker for future recovery
            self._wal_write_pending_sync_marker(finding_id, query, source_type, confidence)

        return result

    def _wal_evict_oldest_pending_markers(self, keep_count: int) -> int:
        """
        P0-9 fix: Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.

        Removes (total_count - keep_count) oldest markers by timestamp.
        Returns number of markers evicted.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)

        try:
            if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
                return 0
            env = self._wal_lmdb._env
            if env is None:
                return 0

            # Collect all pending markers with their timestamps
            prefix = "pending_duckdb_sync:"
            markers: list[tuple[float, str]] = []  # (timestamp, key)

            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix.encode("utf-8")):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")  # noqa: E501
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = value_bytes if isinstance(value_bytes, memoryview) else value_bytes
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = _ORJSON_DECODER(vb)
                            ts = value.get("ts", 0.0)
                            markers.append((ts, key))
                        except Exception:
                            continue

            if len(markers) <= keep_count:
                return 0

            # Sort by timestamp ascending (oldest first)
            markers.sort(key=lambda x: x[0])
            # Evict oldest (len - keep_count) markers
            evict_count = len(markers) - keep_count
            evicted = 0
            for i in range(evict_count):
                _, key = markers[i]
                if self._wal_lmdb.delete(key):
                    evicted += 1

            if evicted > 0:
                _logger.warning(f"[P0-9] Evicted {evicted} oldest pending sync markers (limit={keep_count})")
            return evicted
        except Exception:
            return 0

    def _wal_write_pending_sync_marker(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Sprint 8F: Write a pending-sync recovery marker to LMDB.
        P0-9 fix: Enforces MAX_PENDING_SYNC_MARKERS bound via oldest eviction.

        Marker key:  pending_duckdb_sync:{id}
        Value:       same structure as WAL finding (id, query, source_type, confidence, ts)

        This marker is written ONLY when LMDB succeeded but DuckDB failed.
        A future recovery sprint can find it via prefix scan and retry the DuckDB write.
        """
        try:
            import time as _time

            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            # Sprint 8F: Ensure _wal_lmdb is initialized (lazy init)
            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return False
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
                # Initialize schema on first access
                try:
                    self._wal_lmdb.put("_schema_init", {"_": "ok"})
                    self._wal_lmdb.delete("_schema_init")
                except Exception:
                    pass

            # P0-9 fix: Evict oldest markers if we're at or above the bound
            self._wal_evict_oldest_pending_markers(self.MAX_PENDING_SYNC_MARKERS - 1)

            key = f"pending_duckdb_sync:{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
            }
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def _wal_scan_pending_sync_markers(self) -> list[dict[str, Any]]:
        """
        Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.

        Returns list of marker values (dicts with id, query, source_type, confidence, ts).
        Uses LMDB cursor with prefix iteration - O(n) where n = number of pending markers,
        NOT O(N) full database scan.
        """
        try:

            if not hasattr(self, "_wal_lmdb"):
                return []
            env = self._wal_lmdb._env
            if env is None:
                return []
            results = []
            prefix = "pending_duckdb_sync:"
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix.encode("utf-8")):
                    for key_bytes, value_bytes in cursor.iternext():
                        # buffers=True returns memoryview; convert to bytes for decoding/parsing
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")  # noqa: E501
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = value_bytes if isinstance(value_bytes, memoryview) else value_bytes
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = _ORJSON_DECODER(vb)
                            results.append(value)
                        except Exception:
                            continue
            return results
        except Exception:
            return []

    def _wal_clear_pending_sync_marker(self, finding_id: str) -> bool:
        """
        Sprint 8F: Clear a pending-sync marker after successful recovery.

        Called by a future recovery sprint after the DuckDB write succeeds.
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:
            return False

    def _wal_write_deadletter_marker(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        error: str,
        retry_count: int,
    ) -> bool:
        """
        Sprint 8H: Write a marker to the dead-letter namespace after max retries exceeded.

        Dead-letter key:  deadletter_duckdb_sync:{id}
        Value:            id, query, source_type, confidence, ts, error, retry_count
        """
        try:
            import time as _time

            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"{self.DEADLETTER_PREFIX}{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
                "error": error,
                "retry_count": retry_count,
            }
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def _wal_get_pending_marker(self, finding_id: str) -> dict[str, Any] | None:
        """
        Sprint 8H: Get a single pending marker value by finding_id.

        Returns the marker dict or None if not found.
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return None
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.get(key)
        except Exception:
            return None

    def _wal_delete_deadletter_marker(self, finding_id: str) -> bool:
        """
        Sprint 8H: Delete a dead-letter marker (used when replay succeeds later).
        """
        try:
            if not hasattr(self, "_wal_lmdb"):
                return False
            key = f"{self.DEADLETTER_PREFIX}{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:
            return False

    def _sync_replay_single_marker(
        self,
        finding_id: str,
        marker: dict[str, Any],
    ) -> bool:
        """
        Sprint 8H: Synchronous single-marker replay - MUST be called on the worker thread.

        Uses the same _sync_insert_finding path as normal activation.
        Returns True if DuckDB write succeeded.
        """
        try:
            db_ok = self._sync_insert_finding(
                finding_id=marker.get("id", finding_id),
                query=marker.get("query", ""),
                source_type=marker.get("source_type", "unknown"),
                confidence=marker.get("confidence", 1.0),
            )
            return db_ok
        except Exception:
            return False

    def _sync_verify_duckdb_record(self, finding_id: str) -> bool:
        """
        Sprint 8H: Fresh read-back verification from a NEW DuckDB connection.

        Called after write commit to confirm the record is durable.
        Uses a non-read-only fresh connection so the WAL is flushed.
        MUST be called on the worker thread.
        """
        try:
            if self._db_path:
                duckdb = _get_duckdb()
                # Fresh connection per read-back (Sprint 8H invariant 1.E)
                # Note: read_only=False so WAL is flushed and visible
                conn = duckdb.connect(str(self._db_path))
                try:
                    sql = "SELECT 1 FROM canonical_findings WHERE id = ? LIMIT 1"
                    result = list(self.arrow_fetch_batch(conn, sql, [finding_id]))
                    return bool(result)
                finally:
                    conn.close()
            else:
                # :memory: mode - use persistent connection
                sql = "SELECT 1 FROM canonical_findings WHERE id = ? LIMIT 1"
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [finding_id]))
                return bool(result)
        except Exception:
            return False

    def _get_and_bump_retry_count(self, finding_id: str) -> int:
        """
        Sprint 8H: Get current retry count from marker metadata and bump it.

        Stores retry count in the marker value under "_retry_count" key.
        Returns the new retry count after bump.
        """
        try:
            marker = self._wal_get_pending_marker(finding_id)
            if marker is None:
                return 0
            current = marker.get("_retry_count", 0)
            new_count = current + 1
            marker["_retry_count"] = new_count
            key = f"pending_duckdb_sync:{finding_id}"
            self._wal_lmdb.put(key, marker)
            return new_count
        except Exception:
            return 0

    def _activation_record_findings_batch(
        self,
        findings: list[dict[str, Any]],
    ) -> dict:
        """
        Sprint 8A: Batch activation - LMDB WAL first, DuckDB second.

        Each finding dict must contain: id, query, source_type, confidence
        (id is generated by caller if not present)

        Returns dict with keys: lmdb_success, duckdb_success, count,
                                failed_ids (list of ids that failed)
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        result = {
            "lmdb_success": False,
            "duckdb_success": False,
            "count": 0,
            "failed_ids": [],
        }

        if not findings:
            return result

        # Step 1: LMDB WAL first - use put_many
        try:
            import time as _time

            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return result
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))

            items = []
            for f in findings:
                fid = f.get("id")
                if not fid:
                    continue
                key = f"finding:{fid}"
                value = {
                    "id": fid,
                    "query": f.get("query", ""),
                    "source_type": f.get("source_type", "unknown"),
                    "confidence": f.get("confidence", 1.0),
                    "ts": _time.time(),
                }
                items.append((key, value))

            if items:
                lmdb_ok = self._wal_lmdb.put_many(items)
                result["lmdb_success"] = lmdb_ok
                if not lmdb_ok:
                    _logger.warning(f"[Sprint 8A] Batch WAL failed for {len(items)} items")
                    return result
        except Exception as e:
            _logger.error(f"[Sprint 8A] Batch WAL exception: {e}")
            return result

        # Step 2: DuckDB second
        try:
            # Map to DuckDB format (list of dicts with id, query, source_type, confidence)
            db_findings = [
                {
                    "id": f.get("id"),
                    "query": f.get("query", ""),
                    "source_type": f.get("source_type", "unknown"),
                    "confidence": f.get("confidence", 1.0),
                }
                for f in findings
                if f.get("id")
            ]
            if db_findings:
                inserted = self._sync_insert_findings_bulk(db_findings)
                result["duckdb_success"] = inserted >= len(db_findings)
                result["count"] = inserted
                if inserted < len(db_findings):
                    _logger.error(f"[Sprint 8A] Partial DuckDB batch: {inserted}/{len(db_findings)}, LMDB preserved")
        except Exception as e:
            _logger.error(f"[Sprint 8A] Batch DuckDB exception: {e}, LMDB preserved")
            # Note: we don't rollback LMDB - it remains truth

        return result

    # ------------------------------------------------------------------
    # Internal helper - shared close logic
    # ------------------------------------------------------------------

    def _acquire_process_lock(self) -> tuple[str, str]:
        """
        F269: Process-level lock using GraphLockManager (consolidated from F266-U5).

        Uses GraphLockManager singleton per db_path — same fcntl.flock-based locking
        as DuckPGQGraph. This unifies the 3 independent locking strategies into one.

        Three-tier locking strategy:
        1. 'excl' — we are the exclusive writer (lock acquired)
        2. 'ro'  — another process holds the lock, open READ-ONLY
        3. None  — lock unavailable, fall back to :memory:

        Returns:
            tuple: (lock_mode: str, message: str)
        """
        from hledac.universal.graph.lock_manager import GraphLockManager

        my_pid = os.getpid()

        # GraphLockManager is a singleton per db_path — same instance is shared
        # across DuckDBShadowStore and DuckPGQGraph for the same file.
        lock_mgr = GraphLockManager(str(self._db_path))
        if lock_mgr.acquire(timeout_s=2.0):
            # F-Alert: Record successful lock acquisition for contention tracking
            try:
                from hledac.universal.monitoring.alert_manager import (
                    get_lock_contention_tracker,
                )
                get_lock_contention_tracker().record_attempt(acquired=True)
            except Exception:
                pass
            return ("excl", f"PID {my_pid} acquired exclusive lock via GraphLockManager")

        # Lock denied — GraphLockManager.acquire() already ran stale detection
        # and fcntl.flock(). Check denial reason to determine READ-ONLY vs :memory:.
        denial = lock_mgr.denial_reason
        holder = lock_mgr.holder_pid

        # Try READ-ONLY mode — verify the DB file is readable
        test_conn = None
        try:
            test_conn = _get_duckdb().connect(str(self._db_path), read_only=True)
            test_conn.close()
            test_conn = None  # mark as closed
            # F-Alert: Record lock contention (denied, falling back to RO)
            try:
                from hledac.universal.monitoring.alert_manager import (
                    get_lock_contention_tracker,
                )
                get_lock_contention_tracker().record_attempt(acquired=False)
            except Exception:
                pass
            msg = f"PID {my_pid} opening READ-ONLY (GraphLockManager denied: {denial})"
            if holder:
                msg = f"PID {my_pid} opening READ-ONLY (holder PID {holder}: {denial})"
            return ("ro", msg)
        except Exception as e:
            if test_conn is not None:
                try:
                    test_conn.close()
                except Exception:
                    pass
            return (
                None,
                f"PID {my_pid} GraphLockManager denied ({denial}), READ-ONLY failed ({e}) — using :memory: fallback",
            )

    def _cleanup_orphaned_locks(self) -> None:
        """
        F11C-2: Remove orphaned DuckDB and GraphLockManager lock files at startup.

        Called from async_initialize() before connecting. Uses the same stale
        detection as GraphLockManager to avoid removing locks held by live processes.

        DuckDB WAL lock path is: str(db_path) + ".lock"
        GraphLockManager lock path is: db_path.with_suffix(".lock") — same as DuckDB!
        """
        if self._db_path is None:
            try:
                self._resolve_path()
            except Exception:
                return
        if self._db_path is None:
            return

        import pathlib

        from hledac.universal.graph.lock_manager import _is_lock_stale
        # DuckDB WAL and GraphLockManager both use: db_path + ".lock"
        try:
            lock_path = pathlib.Path(str(self._db_path) + ".lock")
            if lock_path.exists():
                is_stale, reason = _is_lock_stale(lock_path, self._db_path)
                if is_stale:
                    lock_path.unlink(missing_ok=True)
                    logger.debug(f"[DUCKDB] Removed stale lock {lock_path}: {reason}")
        except Exception as e:
            logger.warning(f"[DUCKDB] Lock cleanup failed: {e}")

    def _do_close(self) -> None:
        """
        Synchronous close helper - idempotent.

        Note: _closed guard removed - close() and _do_close() are always called
        together in the same call chain; close() sets _closed=True first and
        guards against re-entry. _do_close() always runs its cleanup.
        """
        self._closed = True
        self._initialized = False
        # Sprint 8L: reset boot barrier for re-initialize safety
        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:
            pass
        # F266-U5: Release process lock if we hold it
        # F266-U5-3 FIX: delete the lock file we created
        try:
            if hasattr(self, '_db_path') and self._db_path is not None:
                _db_str = str(self._db_path)
                if _db_str != ':memory:' and _db_str != 'None':
                    import pathlib
                    _lock_path = pathlib.Path(_db_str).expanduser()
                    _lock_file = _lock_path.parent / (_lock_path.name + ".lock")
                    try:
                        _lock_file.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            # F285-U1: Unified executor — all 4 former pools are aliases to _shared_executor.
            # Shut down only once to avoid "shutdown called multiple times" errors.
            if hasattr(self, "_shared_executor") and self._shared_executor is not None:
                self._shared_executor.shutdown(wait=False)
        except Exception:
            pass
        # Sprint 8AG + F216G: DedupManager is now closed in _do_sync_close() on the
        # main thread BEFORE _sync_close_on_worker is submitted (see F300S-FIX ordering
        # invariant). This worker path skips it to avoid double-close: the main thread
        # sets _dedup_manager=None before submitting the worker, so the guard here is
        # always False — keeping the block for defensive completeness only.
        # (WAL already closed via _sync_close_on_worker)

    # =============================================================================
    # Sprint 8AG §6.17 + F216G: Dedup Manager delegate
    # (persistence methods moved to DedupManager; DuckDBShadowStore is thin orchestrator)
    # =============================================================================

    DEDUP_NAMESPACE: str = "dedup:"

    def _dedup_key_from_fingerprint(self, fp: str) -> bytes:
        """Build dedup namespace key from BLAKE2b fingerprint."""
        return f"{self.DEDUP_NAMESPACE}{fp}".encode()

    def _dedup_lmdb_key_to_fingerprint(self, key: bytes) -> str:
        """Extract fingerprint from dedup namespace key."""
        return key.decode("utf-8")[len(self.DEDUP_NAMESPACE):]

    def _init_persistent_dedup_lmdb(self) -> None:
        """Deprecated: initialization moved to DedupManager.initialize()."""
        try:
            from hledac.universal.paths import LMDB_STORE_ROOT
            dedup_path = LMDB_STORE_ROOT / "dedup.lmdb"
            dedup_path.mkdir(parents=True, exist_ok=True)

            from hledac.universal.tools.lmdb_kv import LMDBKVStore
            self._dedup_lmdb = LMDBKVStore(
                path=str(dedup_path),
                map_size=_DEDUP_LMDB_MAP_SIZE,
                max_keys=1_000_000,
            )
            self._dedup_lmdb_path = dedup_path
            self._dedup_lmdb_last_error = None
            self._dedup_lmdb_boot_error = None
        except Exception as e:
            self._dedup_lmdb = None
            self._dedup_lmdb_path = None
            self._dedup_lmdb_boot_error = str(e)
            self._dedup_lmdb_last_error = str(e)

    def _init_semantic_dedup_cache(self) -> None:
        """
        Initialize semantic dedup cache.

        DEPRECATED (Sprint F222): Semantic dedup is now initialized by DedupManager.initialize().
        This stub exists only for backward compat - calls are no longer emitted.
        """
        # Sprint F222: DedupManager.initialize() now handles semantic dedup init.
        # This method is never called in the F222 canonical path.
        pass

    def _lookup_persistent_dedup(self, fp: str) -> str | None:
        """
        Lookup a fingerprint in the persistent dedup LMDB.

        DEPRECATED (Sprint F222): Delegates to DedupManager.lookup_persistent_dedup().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is None:
            return None
        return self._dedup_manager.lookup_persistent_dedup(fp)

    def _store_persistent_dedup(self, fp: str, finding_id: str) -> None:
        """
        Store a fingerprint -> finding_id mapping in persistent dedup LMDB.

        DEPRECATED (Sprint F222): Delegates to DedupManager.store_persistent_dedup().
        Kept for backward compat during migration - remove when all callers migrated.

        P1-4: Also update Bloom filter in DedupManager when available.
        Falls back to store._dedup_lmdb directly for backward compat with tests
        that mock store._dedup_lmdb without going through DedupManager.
        """
        # P1-4: Try DedupManager first (Bloom + LMDB write)
        if self._dedup_manager is not None:
            self._dedup_manager.store_persistent_dedup(fp, finding_id)
            return
        # Fallback: direct LMDB write (backward compat with tests that mock store._dedup_lmdb)
        _dedup_lmdb = getattr(self, "_dedup_lmdb", None)
        if _dedup_lmdb is None:
            return
        try:
            key = f"dedup:{fp}".encode()
            value_bytes = finding_id.encode("utf-8")
            with _dedup_lmdb._env.begin(write=True) as txn:
                txn.put(key, value_bytes)
        except Exception:
            pass

    def _add_to_hot_cache(self, fp: str, finding_id: str) -> None:
        """
        Add entry to bounded hot cache with FIFO eviction.

        DEPRECATED (Sprint F222): Delegates to DedupManager.add_to_hot_cache().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is not None:
            self._dedup_manager.add_to_hot_cache(fp, finding_id)
            return
        # Fallback: simple in-memory hot cache for tests that mock _dedup_lmdb directly
        if not hasattr(self, "_hot_cache_fallback"):
            self._hot_cache_fallback: dict[str, str] = {}
        self._hot_cache_fallback[fp] = finding_id

    def _hot_cache_lookup(self, fp: str) -> str | None:
        """
        Bounded hot cache lookup.

        DEPRECATED (Sprint F222): Delegates to DedupManager.hot_cache_lookup().
        Kept for backward compat during migration - remove when all callers migrated.
        """
        if self._dedup_manager is not None:
            return self._dedup_manager.hot_cache_lookup(fp)
        # Fallback: check in-memory hot cache for tests that mock _dedup_lmdb directly
        return getattr(self, "_hot_cache_fallback", {}).get(fp)

    def get_dedup_runtime_status(self) -> dict:
        """
        Sprint 8AG §6.17 + 8AK + 8AV + F222: Typed/cheap status surface for dedup subsystem.

        Sprint F222: Now delegates to DedupManager.get_runtime_status() for dedup-specific
        fields. QualityAssessmentState fields still pulled from _quality_state.
        """
        if self._dedup_manager is not None:
            dedup_status = self._dedup_manager.get_runtime_status(self._quality_state)
            # Merge dedup status with quality state counters (which live in store)
            return {
                **dedup_status,
                "in_memory_duplicate_count": self._quality_state._quality_duplicate_count,
                "persistent_duplicate_count": self._quality_state._persistent_duplicate_count,
                "accepted_count": self._quality_state._accepted_count,
                "low_information_rejected_count": self._quality_state._quality_rejected_count,
                "in_memory_duplicate_rejected_count": self._quality_state._quality_duplicate_count,
                "persistent_duplicate_rejected_count": self._quality_state._persistent_duplicate_count,
                "other_rejected_count": self._quality_state._quality_fail_open_count,
            }
        # Fallback for pre-F222 stores without dedup_manager
        # Also: if _dedup_manager._dedup_lmdb_boot_error was set directly (not via DedupManager),
        # bridge it through so tests that set store._dedup_lmdb_boot_error directly still pass.
        fallback_error = getattr(self, "_dedup_lmdb_boot_error", None)
        # Persistent dedup is enabled if _dedup_lmdb is set (哪怕是 FakeLMDB for tests)
        _dedup_lmdb = getattr(self, "_dedup_lmdb", None)
        _dedup_enabled = _dedup_lmdb is not None and fallback_error is None
        return {
            "persistent_dedup_enabled": _dedup_enabled,
            "bloom_filter_enabled": False,
            "bloom_filter_error": None,
            "last_boot_cleanup_error": fallback_error or getattr(self._dedup_manager, "_dedup_lmdb_boot_error", None) if self._dedup_manager else fallback_error,
            "last_dedup_error": None,
            "dedup_lmdb_path": str(getattr(self, "_dedup_lmdb_path", "") or ""),
            "dedup_namespace": "dedup:",
            "hot_cache_size": len(getattr(self, "_hot_cache_fallback", {})),
            "hot_cache_capacity": 1000,
            "in_memory_duplicate_count": self._quality_state._quality_duplicate_count,
            "persistent_duplicate_count": self._quality_state._persistent_duplicate_count,
            "accepted_count": self._quality_state._accepted_count,
            "low_information_rejected_count": self._quality_state._quality_rejected_count,
            "in_memory_duplicate_rejected_count": self._quality_state._quality_duplicate_count,
            "persistent_duplicate_rejected_count": self._quality_state._persistent_duplicate_count,
            "other_rejected_count": self._quality_state._quality_fail_open_count,
        }

    def reset_ingest_reason_counters(self) -> None:
        """
        Sprint 8AV: Reset all ingest outcome counters to zero.

        Side-effect free, test-safe, can be called any time.
        Resets all counters on QualityAssessmentState.
        """
        self._quality_state._accepted_count = 0
        self._quality_state._quality_rejected_count = 0
        self._quality_state._quality_duplicate_count = 0
        self._quality_state._persistent_duplicate_count = 0
        self._quality_state._quality_fail_open_count = 0

    def classify_ingest_outcome(
        self,
        decision: FindingQualityDecision | ActivationResult,
    ) -> str:
        """
        Sprint 8AV: Classify the canonical reason string for an ingest outcome.

        Internal use - maps internal FindingQualityDecision or ActivationResult
        to a human-readable reason string.

        Returns one of:
          - "accepted"                          - finding passed quality gate
          - "low_information_rejected"         - entropy below threshold
          - "in_memory_duplicate_rejected"     - hot-cache duplicate
          - "persistent_duplicate_rejected"   - LMDB cross-source duplicate
          - "other_rejected"                   - fail-open or unknown
          - "error_rejected"                   - store/LMDB error
        """
        # FindingQualityDecision is a msgspec.Struct (has 'reason' field).
        # ActivationResult is a TypedDict (use item access: decision["key"]).
        if isinstance(decision, FindingQualityDecision):
            # FindingQualityDecision path - msgspec.Struct supports attribute access
            if decision.accepted:
                return "accepted"
            reason = decision.reason
            if reason == "low_entropy_rejected":
                return "low_information_rejected"
            if reason == "persistent_duplicate":
                return "persistent_duplicate_rejected"
            if reason == "duplicate_detected":
                return "in_memory_duplicate_rejected"
            return "other_rejected"

        # ActivationResult path (TypedDict - use item access)
        if decision["accepted"]:
            return "accepted"
        error = decision.get("error")
        if error:
            return "error_rejected"
        return "other_rejected"

    def _schedule_graph_update(self, accepted_findings: list[CanonicalFinding]) -> None:
        """
        Fire graph update as non-blocking asyncio task (Python 3.10+ safe).

        Sprint F241: Writes accepted findings to DuckPGQGraph for cross-sprint
        entity accumulation. Graph is ADVISORY ONLY - failures are silently
        swallowed.

        Sprint F-CLEAN fix: replaced `asyncio.coroutine(_graph_update_task)()`
        (removed in Python 3.11) with the modern `async def` +
        `loop.run_in_executor()` pattern. M1 EIGHTGB safe - DuckDB sync ops run
        in the default ThreadPoolExecutor, not a separate process. Bounded
        by `_MAX_INFLIGHT_GRAPH_UPDATES` via the existing `self._bg_tasks`
        set (Sprint 8QA), auto-drained on completion.

        Sync context (no running event loop - tests / sync CLI / F8H worker
        threads) is a no-op; the graph update is advisory and not required
        for correctness.

        LAZY IMPORT: graph_store accessed here to avoid circular deps
        with duckdb_store.
        """
        try:

            def _sync_graph_update() -> None:
                try:
                    truth_graph = self._graph_store().get_truth_write_graph()
                    if truth_graph is None:
                        return
                    rows = []
                    for f in accepted_findings:
                        ioc_value = getattr(f, "ioc_value", "")
                        ioc_type = getattr(f, "ioc_type", "")
                        if ioc_value:
                            rows.append((ioc_value, ioc_type, float(f.confidence), f.source_type or ""))
                    if rows:
                        truth_graph.upsert_ioc_batch(rows)
                except Exception:
                    pass  # noqa: BLE001  # fail-safe: graph is advisory, never propagates

            # Bounded in-flight cap: reuse Sprint 8QA self._bg_tasks set.
            # getattr fallback covers F233A test fixtures that bypass __init__.
            tasks = getattr(self, "_bg_tasks", None)
            if tasks is None:
                return  # F3.3: BoundedTaskSet not available — skip advisory

            # F3.3 K11 fix: BoundedTaskSet.spawn() handles semaphore bound
            # (maxsize=_MAX_INFLIGHT_GRAPH_UPDATES), auto-cleanup, and
            # cancel() for all tasks. Semaphore acquire blocks if cap reached
            # but the write path is fire-and-forget so we drop instead.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No event loop - sync context (test/CLI); skip advisory.

            async def _graph_update_coro() -> None:
                # DuckDB is NOT thread-safe; route sync upsert via the
                # default ThreadPoolExecutor (in-process, M1 EIGHTGB friendly).
                await asyncio.to_thread(_sync_graph_update)

            # F3.3: spawn() is fire-and-forget with bounded semaphore.
            # If maxsize reached, semaphore.acquire() would block → drop
            # the excess advisory (same policy as old code). We check count
            # first to avoid blocking.
            if tasks.count >= _MAX_INFLIGHT_GRAPH_UPDATES:
                return  # advisory: drop excess, never block write path
            tasks.spawn(_graph_update_coro(), name="duckdb:_schedule_graph_update")
        except Exception:
            pass  # noqa: BLE001  # fail-safe: feature-gated, never blocks write path

    # P3-2: DuckDB native WAL checkpoint loop
    async def _checkpoint_loop(self) -> None:
        """
        Background checkpoint task for DuckDB native WAL.

        Runs every 300s (O3) to flush WAL to main database file, bounding WAL growth.
        duckdb_autocheckpoint=262144 (256MB) provides a secondary safety valve between
        runs. Fail-safe: any error is silently caught and logged.
        Only active for file mode; _checkpoint_task is None for :memory: mode.
        """
        _logger = logging.getLogger(__name__)
        while True:
            try:
                await asyncio.sleep(300)  # O3: 60s -> 300s (duckdb_autocheckpoint is primary bound)
                if self._closed:
                    break
                if self._file_conn is None:
                    continue
                try:
                    self._file_conn.execute("PRAGMA checkpoint")
                    # O3-3: ANALYZE refreshes table statistics → optimal query plans.
                    # Runs every checkpoint (~5 min avg) to keep stats fresh for DuckDB optimizer.
                    self._file_conn.execute("ANALYZE")
                except Exception as e:
                    _logger.debug(f"[P3-2] checkpoint/ANALYZE error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.debug(f"[P3-2] checkpoint loop error: {e}")


# =============================================================================
# Sprint 8AM C.3.a: Factory helper for owned store creation
# =============================================================================

def create_owned_store() -> DuckDBShadowStore:
    """
    Sprint 8AM C.3.a: Create an owned DuckDBShadowStore instance.

    Uses paths.py SSOT for RAMDisk-aware path resolution.
    RAMDISK_ACTIVE=True: db at DUCKDB_STORE_ROOT, temp at RAMDISK_ROOT/duckdb_tmp
    RAMDISK_ACTIVE=False: degraded :memory: fallback

    This is the ONE place in main.py where DuckDBShadowStore is instantiated
    for the owned runtime path. Avoids coupling __main__.py to DuckDBShadowStore
    internals.

    Returns:
        DuckDBShadowStore: initialized store ready for async_initialize()
    """
    try:
        from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

        if RAMDISK_ACTIVE:
            db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
            temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            return DuckDBShadowStore(db_path=db_path, temp_dir=temp_dir)
        else:
            # Degraded mode: :memory: (no durability)
            return DuckDBShadowStore()
    except Exception:
        # Fallback: :memory: even if paths.py import fails
        return DuckDBShadowStore()


"""
DuckDB Shadow Analytics Sidecar - CANONICAL SPRINT FACTS STORE
===============================================================

ROLE: Canonical store for sprint-level facts and derived analytics.

⚠️  "Shadow" in the class name refers to historical naming (Sprint 8AO/8AS).
    This store is NOT a shadow of anything - it IS the canonical sprint facts
    authority for the analytics subsystem.

FACTS HIERARCHY (3 tiers):
--------------------------
TIER 1 - SPRINT FACTS (DuckDB, durable):
    sprint_delta       - per-sprint metrics: query, duration, new_findings, dedup_hits, ioc_nodes
    sprint_scorecard   - per-sprint aggregated scores: fpm, ioc_density, synthesis_confidence
    source_hit_log     - per-sprint source attribution: source_type, hit_rate

TIER 2 - SHADOW FINDINGS (DuckDB, durable):
    canonical_findings    - finding-level records forwarded from EvidenceLog.append()
    shadow_runs        - run-level metadata

TIER 3 - GRAPH (Kuzu/LanceDB, injected):
    IOCGraph           - truth graph for IOC storage (buffered writes)
    SemanticStore      - FastEmbed+ LanceDB for ANN semantic search

LEDGER vs FACTS boundary:
    EvidenceLog (ledger)  ->  analytics_hook (shadow path)  ->  DuckDBShadowStore (sprint facts)
    ResearchContext (carrier)  ->  ContextHandoffMetadata (handoff descriptor)

DESIGN PRINCIPLES:
------------------
- DuckDB is NOT imported at module level of any boot-path file
- DuckDB import is deferred to first actual use inside initialize()
- When RAMDISK_ACTIVE=True: persistent DB under DB_ROOT, temp under RAMDISK_ROOT
- When RAMDISK_ACTIVE=False: :memory: mode with persistent single connection
- All DB operations run on a dedicated single-worker ThreadPoolExecutor
- All async public methods use run_in_executor to avoid event-loop blocking
- Connection is created INSIDE the worker thread (thread-affine)
- PRAGMA threads=2 applied after connection init (M1 EIGHTGB UMA: conservative for memory budget)
- Batch methods enforce chunking: max_batch_size=500
- aclose() is idempotent with _closed flag

SCHEMA (per tier):
------------------
Tier 1:  sprint_delta, sprint_scorecard, source_hit_log
Tier 2:  canonical_findings, shadow_runs

ASYNC API SURFACE
----------------
- async_initialize()       - async init wrapper
- async_record_shadow_run(...)   - insert run record
- async_record_shadow_finding(...)  - insert single finding
- async_record_canonical_findings_batch(..., max_batch_size=500) - chunked batch
- async_query_recent_findings(limit=10)  - query findings
- async_healthcheck()      - returns True if healthy
- aclose()            - async idempotent shutdown

    DELEGATE BOUNDARIES (Sprint F216G-F233A extraction)
    ==================================================
    DuckDBShadowStore is the canonical write core. Persistence concerns have been
    extracted into specialized managers that DuckDBShadowStore orchestrates. The store
    itself owns NO LMDB handles - all such handles are owned by the delegate managers.

    WAL Boundary:
        Pending sync markers, deadletters, WAL replay state -> WALManager (wal.py)

    Dedup Boundary:
        Persistent LMDB, hot cache, semantic dedup cache -> DedupManager (dedup.py)

    Semantic Buffering Boundary:
        FastEmbed + LanceDB batch embedding pipeline -> SemanticStoreBuffer (buffer.py)

    Graph Attachment Boundary:
        IOCGraph injection, STIX, truth-write, graph queries -> GraphAttachmentStore (graph_attachment.py)

    Quality Assessment Boundary:
        Entropy, dedup fingerprint, rejection ledger -> QualityAssessmentState (quality_assessment.py)

    CANONICAL WRITE PATH (unchanged since Sprint F216G):
        async_ingest_findings_batch()
          -> quality gate (per-finding: entropy, dedup fp, URL normalization)
          -> accept/reject decision (QualityAssessmentState)
          -> async_record_canonical_findings_batch()
              -> WALManager.append()          [write-ahead log, crash safety]
              -> DedupManager.check()         [duplicate detection]
              -> DuckDB insert (sprint_delta, canonical_findings)
              -> SemanticStoreBuffer.buffer()  [async FastEmbed + LanceDB index]
              -> GraphAttachmentStore (optional, post-accumulation)
              -> WALManager.flush()            [sync marker, allows replay]

    READ / QUERY METHODS:
        async_query_recent_findings(), async_query_sprint_deltas(),
        async_query_source_hit_log(), get_runtime_status(),
        get_dedup_runtime_status(), get_wal_runtime_status(),
        get_semantic_buffer_status(), get_graph_stats()

    WHY NO StoreProtocol:
        Only one real adapter (DuckDBShadowStore) existed across all sprints.
        The delegate managers each have their own interfaces; no abstraction layer
        was needed since DuckDBShadowStore is the sole canonical store. If a second
        adapter is added in the future (e.g., SQLite fallback), a protocol can be
        introduced at that time. Until then, a protocol would add indirection
        without benefit.
    """

from __future__ import annotations

import asyncio
import sys

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

# F26X: @deprecated with Python 3.11+ safe fallback (see utils/_deprecated.py)

# Sprint F262OBS: canonical source_type centralization - guard at ingest seam
try:
    from hledac.universal.utils.source_types import SourceType, canonical_source_type  # type: ignore[import]
except ImportError:
    SourceType = None  # type: ignore[assignment]
    canonical_source_type = None  # type: ignore[assignment]

import msgspec

# Sprint F26X: orjson for fast JSON path (3-11x vs stdlib json)
try:
    import orjson as _orjson
    _HAS_ORJSON = True
except ImportError:
    _orjson = None  # type: ignore[assignment]
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
    from hledac.universal.knowledge.target_memory import (  # noqa: F401  # hledac.universal.knowledge.target_memory.TargetMemoryUpdate
        TargetMemory,
        TargetMemoryUpdate,
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
from utils.async_helpers import safe_gather_fire_and_forget  # noqa: E402

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
try:
    from hledac_rust_extensions import batch_dedup_fingerprints as _rust_batch_dedup_fingerprints
    from hledac_rust_extensions import batch_entropy as _rust_batch_entropy
    from hledac_rust_extensions import batch_normalize_quality_text as _rust_batch_normalize_quality_text
    from hledac_rust_extensions import batch_url_fingerprints as _rust_batch_url_fingerprints
    from hledac_rust_extensions import dedup_fingerprint as _rust_dedup_fingerprint
    from hledac_rust_extensions import normalize_quality_text as _rust_normalize_quality_text
    from hledac_rust_extensions import url_fingerprint as _rust_url_fingerprint_b2b

    _QUALITY_GATE_BATCH_AVAILABLE = True
except ImportError:
    _QUALITY_GATE_BATCH_AVAILABLE = False
    _rust_batch_entropy = None  # type: ignore[assignment]
    _rust_batch_dedup_fingerprints = None  # type: ignore[assignment]
    _rust_batch_normalize_quality_text = None  # type: ignore[assignment]
    _rust_batch_url_fingerprints = None  # type: ignore[assignment]
    _rust_dedup_fingerprint = None  # type: ignore[assignment]
    _rust_url_fingerprint_b2b = None  # type: ignore[assignment]
    _rust_normalize_quality_text = None  # type: ignore[assignment]

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
    _rust_batch_ioc_extract = None
    _rust_batch_ioc_extract_python = None


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

class ActivationResult(TypedDict):
    """
    Typed result contract for activation record operations.

    Fields:
        finding_id:     Unique identifier of the finding
        lmdb_success:   True if LMDB WAL write succeeded
        duckdb_success: True if DuckDB write succeeded, False if it failed,
                        None if not yet attempted
        lmdb_key:       "finding:{id}" - LMDB key used
        desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)
        error:          Error message if there was an exception, None otherwise
    """

    finding_id: str
    lmdb_success: bool
    duckdb_success: bool | None
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool  # True when finding passed quality gate and was stored


class ReplayResult(TypedDict):
    """
    Typed result contract for pending-sync replay operations (Sprint 8H).

    Fields:
        finding_id:           Unique identifier of the finding
        marker_found:         True if pending marker existed before replay attempt
        wal_truth_found:      True if finding:{id} WAL truth was found in LMDB
        duckdb_written:        True if DuckDB write succeeded during replay
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

    TODO 8Q/8R: zvážit přesun CanonicalFinding do sdíleného DTO modulu,
                pokud bude používán mimo storage vrstvu
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

_DUCKDB_MEMORY_LIMIT: str = os.environ.get("GHOST_DUCKDB_MEMORY", "400MB")
_DUCKDB_MAX_TEMP: str = os.environ.get("GHOST_DUCKDB_MAX_TEMP", "1GB")

# Sprint P0-4: Arrow zero-copy ingest (default ON - M1 EIGHTGB optimized, 1.5-2* faster than executemany).
# Disabled via HLEDAC_ARROW_INGEST=0 when Arrow path causes issues.
# Off -> async_record_canonical_findings_batch_arrow silently falls back to legacy executemany.
# On  -> Arrow path for batches >= ARROW_MIN_BATCH (below threshold still falls back to executemany
#       because per-row executemany call overhead is lower than Arrow table build for tiny N).
_ARROW_INGEST_ENABLED: bool = os.environ.get("HLEDAC_ARROW_INGEST", "1") != "0"
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
                        safe_mode (bool).
    """
    base_mem = os.environ.get("GHOST_DUCKDB_MEMORY", "400MB")
    base_threads = 2

    settings: dict[str, str | int] = {
        "memory_limit": base_mem,
        "max_temp": _DUCKDB_MAX_TEMP,
        "threads": base_threads,
        "preserve_insertion_order": False,
        "safe_mode": False,
    }

    if swap_detected:
        # EMERGENCY: minimize memory footprint
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True

    elif uma_state == "EMERGENCY":
        settings["memory_limit"] = "200MB"
        settings["threads"] = 1
        settings["safe_mode"] = True

    elif uma_state == "CRITICAL":
        settings["memory_limit"] = "250MB"
        settings["threads"] = 1

    elif uma_state == "WARN":
        # Conservative but still usable
        settings["memory_limit"] = "250MB"
        settings["threads"] = 2

    # else: normal - use base env values (already set in defaults)

    return settings


def _validate_duckdb_setting(value: str, setting_name: str) -> str:
    """
    Validate DuckDB setting value to prevent SQL injection.

    P1-3: Replaces f-string interpolation in SET commands.
    Only allows alphanumeric, GB/MB/KB suffixes, and basic punctuation.
    """
    import re

    # Allow: numbers, GB/MB/KB/TB suffixes, decimal point, spaces
    if not re.match(r"^[\d.]+\s*(GB|MB|KB|TB)?\s*$", value.strip(), re.IGNORECASE):
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
    );
    -- Sprint F-B: target_memory last_seen_ts is the primary sort key
    -- for "recent targets" queries in F204D.
    CREATE INDEX IF NOT EXISTS idx_target_memory_last_seen
        ON target_memory(last_seen_ts DESC);
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

    -- P2-1: Domain candidates materialized view for fast domain lookup
    -- Pre-extracts domains from canonical_findings.payload_text with aggregation.
    -- Refreshed incrementally on new findings - avoids full table scan per query.
    CREATE TABLE IF NOT EXISTS domain_candidates (
        domain          TEXT PRIMARY KEY,
        first_seen_ts   DOUBLE,
        last_seen_ts    DOUBLE,
        hit_count       INTEGER DEFAULT 1,
        avg_confidence  REAL DEFAULT 0.0,
        source_types    TEXT,  -- JSON array of source_type values
        finding_ids     TEXT   -- JSON array of canonical_findings.id values
    );
    CREATE INDEX IF NOT EXISTS idx_domain_candidates_last_seen
        ON domain_candidates(last_seen_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_domain_candidates_hit_count
        ON domain_candidates(hit_count DESC);

    -- P2-1: FTS keyword index - replaces LIKE '%pattern%' queries on payload_text
    -- Stores tokenized keywords per finding for fast exact-match lookups.
    CREATE TABLE IF NOT EXISTS finding_keywords (
        finding_id      TEXT PRIMARY KEY,
        keywords        TEXT,  -- space-separated lowercase tokens
        ts              DOUBLE,
        query           TEXT,
        source_type     TEXT,
        FOREIGN KEY (finding_id) REFERENCES canonical_findings(id)
    );
    CREATE INDEX IF NOT EXISTS idx_finding_keywords_ts
        ON finding_keywords(ts DESC);
"

# Sprint 8R: Thread-local encoder for CanonicalFinding serialization.
# msgspec.json.Encoder is NOT safe for concurrent encode() calls across threads
# (mutable internal byte buffer). Each thread gets its own Encoder instance
# via thread-local storage, lazily initialized on first use.
import threading

_local = threading.local()


def _get_canonical_encoder():
    # type: () -> msgspec.json.Encoder
    encoder = getattr(_local, "encoder", None)
    if encoder is None:
        encoder = msgspec.json.Encoder()
        _local.encoder = encoder
    return encoder

# Sprint F-CLEAN: Max concurrent in-flight graph update tasks (advisory only).
# Bounds the `_bg_tasks` set under bursty accepted-write load. Discard callback
# on each task ensures steady-state count returns near 0 after the burst.
# M1 EIGHTGB safe - work runs on the default ThreadPoolExecutor, never a subprocess.
# 16 concurrent DuckPGQ upserts is well under the 1-worker DuckDB executor.
_MAX_INFLIGHT_GRAPH_UPDATES: int = 16

# Module-level docstring (closed at line 1)
"""

class DuckDBShadowStore:
    # MEM-1: __slots__ for memory optimization on M1 8GB
    # All 30 instance attributes declared - ~1.4 KB per-instance savings vs __dict__
    __slots__ = (
        # Core state
        '_initialized', '_closed', '_db_path', '_temp_dir', '_uma_state',
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
        '_bg_tasks', '_checkpoint_task', '_coalescer',
        # Executor (for async ops)
        '_write_executor', '_read_executor', '_wal_executor', '_duckdb_arrow_executor', '_executor',
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
    )

    """DuckDB sidecar with RAMDISK-first / OPSEC-safe degraded mode."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        uma_state: str | None = None,
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
                     DuckDB store does NOT import schedulers - receives this explicitly.
        """
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

        # Sprint 1.2: Split executor — write pool + read pool
        # M1 8GB: 3 write + 2 read workers ≈ +90 MB total, safe (~6.25GB max).
        #
        # NOTE on Arrow path (P0-4 / F285): async_record_canonical_findings_batch_arrow
        # bypasses _write_executor entirely — WAL on _wal_executor and DuckDB INSERT
        # on _duckdb_arrow_executor concurrently via asyncio.gather (preferred path).
        # _write_executor handles: (1) legacy fallback when Arrow falls back to executemany,
        # (2) analytics queries (trend, scorecard, yield), (3) VACUUM, (4) init/close.
        # 2 workers (reduced from 3) — still eliminates single-threaded bottleneck on
        # legacy + analytics paths. _read_executor reduced to 1 (was 2) since it's
        # not used in any run_in_executor call (confirmed by grep audit).
        self._write_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="duckdb_writer",
        )
        self._read_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="duckdb_reader",
        )

        # Sprint F285: WAL + DuckDB split executor — WAL and DuckDB Arrow ingest
        # run on separate thread pools to enable I/O overlap (LMDB putmulti +
        # DuckDB register+INSERT can now proceed in parallel rather than
        # serializing through a single _write_executor thread).
        # M1 8GB: +1 thread ≈ +30 MB, safe within budget.
        self._wal_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="wal_writer",
        )
        # P3-2: DuckDB Arrow executor - 2 workers for parallel DuckDB writes.
        # M1 8GB: 2 workers ≈ +30 MB, safe within budget (~6.25GB total).
        self._duckdb_arrow_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="duckdb_arrow_writer",
        )

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
            # F5.2: Coalescer synergy — counts accepted chunks that were >= _ARROW_MIN_BATCH
            # (Arrow-eligible via coalescer 1024 flush). High ratio = Arrow well-utilized.
            "arrow_coalescer_potential": 0,
            # F5.2: Tracks chunks below Arrow threshold that went via coalescer
            "arrow_coalescer_small_chunk": 0,
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
        self._bg_tasks: set[asyncio.Task] = set()

        # Sprint 8SB: Semantic store (FastEmbed + LanceDB)
        self._semantic_store: Any | None = None

        # Sprint 1.2: Backward-compat alias — _executor kept so that
        # synchronous submit() calls in tests / compat wrappers still resolve.
        # New async code uses _write_executor / _read_executor explicitly.
        self._executor = self._write_executor

        # Sprint 8SB: Semantic buffer - fail-open, no-op if no store injected
        from hledac.universal.knowledge.semantic_store_buffer import SemanticStoreBuffer
        self._semantic_buffer: SemanticStoreBuffer = SemanticStoreBuffer()

        # Sprint DuckDB Write Coalescer: batches findings from N concurrent lanes
        # into a single async_ingest_findings_batch call, reducing call frequency.
        # Pure asyncio Task — no threads. Initialized in async_initialize().
        self._coalescer: Any | None = None

        # P3-2: Background DuckDB checkpoint task for native WAL.
        # Only active for file mode (None for :memory:).
        self._checkpoint_task: asyncio.Task | None = None

        # Sprint F265B Variant B: M1 RAM-adaptive executor pool sizing.
        # Scales _duckdb_arrow_executor workers down on memory pressure to conserve RAM.
        # CRITICAL/EMERGENCY state: max_workers=1; SOFT_WARN/WARN: max_workers=2; OK: max_workers=2 (default).
        self._adjust_executor_pool()

    # -- M1 RAM-adaptive executor sizing ----------------------------------------
    # Sprint F265B Variant B: Dynamic thread pool scaling based on UMA pressure.

    def _adjust_executor_pool(self) -> None:
        """
        Adjust _duckdb_arrow_executor worker count based on M1 UMA memory pressure.

        CRITICAL/EMERGENCY: 1 worker (~15 MB saved vs 2 workers)
        SOFT_WARN/WARN: 2 workers (default)
        OK: 2 workers (default)

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

        if state in ("critical", "emergency"):
            target_workers = 1
        else:
            target_workers = 2  # default for ok / soft_warn / warn

        # Only update if already constructed (not on first call before __init__ completes)
        if hasattr(self, "_duckdb_arrow_executor") and self._duckdb_arrow_executor is not None:
            try:
                # Get current max_workers
                current = self._duckdb_arrow_executor._max_workers  # type: ignore[attr-defined]
                if current != target_workers:
                    self._duckdb_arrow_executor._max_workers = target_workers  # type: ignore[attr-defined]
                    _logger.debug(
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
    # ---------------------------------------------------------------------------
    # Sprint F222: Graph slots - DEPRECATED, delegated to GraphAttachmentStore
    # ---------------------------------------------------------------------------

    def _graph_store(self) -> Any:
        """Lazy-init GraphAttachmentStore."""
        if not hasattr(self, "_DuckDBShadowStore__graph_store"):
            object.__setattr__(self, "_DuckDBShadowStore__graph_store", None)
        if self._DuckDBShadowStore__graph_store is None:
            from hledac.universal.knowledge.graph_attachment import GraphAttachmentStore
            object.__setattr__(self, "_DuckDBShadowStore__graph_store", GraphAttachmentStore())
        return self._DuckDBShadowStore__graph_store

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

    def _graph_ingest_findings(self, findings: list[CanonicalFinding]) -> None:
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

        asyncio.get_running_loop()

        async def _run() -> None:
            try:
                import xxhash

                from hledac.universal.knowledge.ioc_graph import (
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

        t = asyncio.create_task(_run())
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

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

        # Sprint F265-U5: Startup size guard — warn and switch to read-only if DB > 3GB on <10GB RAM
        if self._db_path:
            try:

                import psutil
                size_bytes = self._db_path.stat().st_size
                total_ram = psutil.virtual_memory().total
                if size_bytes > 3 * (1024**3) and total_ram < 10 * (1024**3):
                    logger.warning(
                        "[duckdb_init] CRITICAL: DuckDB %.1fGB on %.1fGB RAM system — vacuum recommended. "
                        "Setting read_only=True until vacuum is run.",
                        size_bytes / (1024**3),
                        total_ram / (1024**3),
                    )
                    _READ_ONLY_FLAG = True  # will be applied after connect below
                else:
                    _READ_ONLY_FLAG = False
            except Exception:
                _READ_ONLY_FLAG = False
        else:
            _READ_ONLY_FLAG = False

        if self._db_path:
            # MODE A: RAMDISK active - persistent file DB + temp on RAMDISK
            if self._temp_dir is None:
                self._temp_dir = self._db_path.parent / "duckdb_tmp"
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(self._db_path), read_only=_READ_ONLY_FLAG)
            # F273F: mark DuckDB mmap pages as reusable - reclaimable without writeback
            madv_free_reusable_on_path(self._db_path)
            apply_nocache_to_path(self._db_path)
            # F231: Use resolved settings instead of hardcoded class attrs
            memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
            max_temp_val = _validate_duckdb_setting(self._max_temp, 'max_temp')
            temp_dir_val = _validate_path_setting(self._temp_dir, 'temp_directory')
            conn.execute("SET memory_limit = ?", [memory_limit_val])
            conn.execute("SET max_temp_directory_size = ?", [max_temp_val])
            conn.execute("SET temp_directory = ?", [temp_dir_val])
            conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
            conn.execute("PRAGMA enable_progress_bar=false")
            conn.execute("PRAGMA enable_object_cache=false")
            # Sprint 5.6: DuckDB 1.x uses OS mmap automatically for file-backed DBs.
            # enable_object_cache=false skips DuckDB's internal cache, relying on OS page cache
            # + F_NOCACHE/MADV_FREE_REUSABLE (applied at init) for zero-copy reads.
            # DuckDB 2.x has explicit enable_mmap/mmap_size pragmas (not in 1.x).
            # F231 B: preserve_insertion_order - fail-soft
            try:
                conn.execute("SET preserve_insertion_order = false")
            except Exception:
                pass  # noqa: BARE-EXCEPT  # older DuckDB version without this setting
            # F265D: DuckDB's conn.sql() and extract_statements() both fail on this multi-statement
            # schema string (Python source leaks into error messages).  Use regex-based statement splitting
            # to split the schema into individual SQL statements and execute them one by one.
            # F265E fix: strip SQL -- comments (may contain '"' chars that break DuckDB parser),
            # strip trailing triple-quotes, skip remaining docstring residue.
            _sql_clean = re.sub(r'^\s*--.*$', '', _SCHEMA_SQL, flags=re.MULTILINE)
            for _s in re.split(r';\s*(?=\w)', _sql_clean):
                _s = _s.strip().rstrip('"')
                if _s and '"' not in _s:
                    conn.execute(_s)
            conn.close()
            # Sprint 8RC: ALTER TABLE for retrokompatibilita (B.2)
            # Sprint 7H: Persistent file-backed connection for reuse across writes
            self._file_conn = duckdb.connect(str(self._db_path))
            # F273F: mark DuckDB mmap pages as reusable - reclaimable without writeback
            madv_free_reusable_on_path(self._db_path)
            apply_nocache_to_path(self._db_path)
            memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
            max_temp_val = _validate_duckdb_setting(self._max_temp, 'max_temp')
            temp_dir_val = _validate_path_setting(self._temp_dir, 'temp_directory')
            self._file_conn.execute("SET memory_limit = ?", [memory_limit_val])
            self._file_conn.execute("SET max_temp_directory_size = ?", [max_temp_val])
            self._file_conn.execute("SET temp_directory = ?", [temp_dir_val])
            self._file_conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
            self._file_conn.execute("PRAGMA enable_progress_bar=false")
            self._file_conn.execute("PRAGMA enable_object_cache=false")
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
            # MODE B: RAMDISK inactive - :memory: with PERSISTENT single connection
            self._persistent_conn = duckdb.connect(":memory:")
            memory_limit_val = _validate_duckdb_setting(str(resolved_memory), 'memory_limit')
            self._persistent_conn.execute("SET memory_limit = ?", [memory_limit_val])
            self._persistent_conn.execute("SET max_temp_directory_size = '0GB'")
            self._persistent_conn.execute(f"PRAGMA threads={_validate_duckdb_threads(resolved_threads)}")
            self._persistent_conn.execute("PRAGMA enable_progress_bar=false")
            self._persistent_conn.execute("PRAGMA enable_object_cache=false")
            # Sprint 5.6: DuckDB 1.x uses OS mmap automatically for file-backed DBs.
            # enable_object_cache=false skips DuckDB's internal cache, relying on OS page cache
            # + F_NOCACHE/MADV_FREE_REUSABLE for zero-copy reads.
            # DuckDB 2.x has explicit enable_mmap/mmap_size pragmas (not in 1.x).
            try:
                self._persistent_conn.execute("SET preserve_insertion_order = false")
            except Exception:
                pass
            # F265D: Same schema-splitting approach for :memory: mode.
            # F265E fix: strip SQL -- comments (may contain '"' chars that break DuckDB parser),
            # strip trailing triple-quotes, skip remaining docstring residue.
            _sql_clean = re.sub(r'^\s*--.*$', '', _SCHEMA_SQL, flags=re.MULTILINE)
            for _s in re.split(r';\s*(?=\w)', _sql_clean):
                _s = _s.strip().rstrip('"')
                if _s and '"' not in _s:
                    self._persistent_conn.execute(_s)

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
        # F273F: mark DuckDB mmap pages as reusable
        madv_free_reusable_on_path(self._db_path)
        apply_nocache_to_path(self._db_path)
        try:
            conn.execute(
                "ALTER TABLE sprint_delta ADD COLUMN findings_per_minute REAL DEFAULT 0"
            )
        except Exception:
            pass  # noqa: BARE-EXCEPT  # column already exists (new schema via CREATE, or prior migration)
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
        conn.close()

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
            pass  # noqa: BARE-EXCEPT  # table already exists or connection not ready

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
            pass  # noqa: BARE-EXCEPT  # table already exists or connection not ready

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

        loop = asyncio.get_running_loop()
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

        return await loop.run_in_executor(None, _sync_ingest)

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

        __slots__ = ("_store", "_stmt_insert_finding", "_stmt_insert_finding_conn_id")

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
            """Return the active write connection (MODE A file or MODE B persistent)."""
            s = self._store
            if s._db_path:
                s._prewarm_file_conn()
                return s._file_conn
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
            cached = self._stmt_insert_finding
            if cached is not None and self._stmt_insert_finding_conn_id == conn_id:
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
                # Sprint F264: prepared statement hot path (loop); executemany() fallback
                stmt = self._get_insert_stmt(conn)
                def _do(c: Any) -> None:
                    if stmt is not None:
                        for row in rows:
                            stmt.execute(row)
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
                # Sprint F264: prepared statement hot path (loop); executemany() fallback
                stmt = self._get_insert_stmt(conn)
                def _do(c: Any) -> None:
                    if stmt is not None:
                        for row in rows:
                            stmt.execute(row)
                    else:
                        c.executemany(self._SQL_INSERT_SHADOW_FINDING, rows)
                self._with_transaction(conn, _do)
                return len(rows)
            except Exception as e:
                _logger.error(f"[D7] DuckDB bulk-as-tuples insert failed: {type(e).__name__}: {e}")
                return 0

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
            try:
                # Use a per-call unique name to avoid collisions if two threads raced
                # (single-worker executor makes that impossible, but defensive anyway).
                import uuid as _uuid
                reg_name = f"finding_arrow_batch_{_uuid.uuid4().hex[:12]}"
                conn.register(reg_name, table)
                try:
                    conn.execute(
                        "INSERT INTO canonical_findings "
                        "(id, query, source_type, confidence, ts, provenance_json) "
                        f"SELECT id, query, source_type, confidence, ts, provenance_json "
                        f"FROM {reg_name} "
                        "ON CONFLICT (id) DO NOTHING"
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
                result = list(self.arrow_fetch_batch(conn, sql, [limit]))
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

        RAMDISK_ACTIVE=True:  DB_ROOT / "shadow_analytics.duckdb", temp = RAMDISK_ROOT / "duckdb_tmp"
        RAMDISK_ACTIVE=False: DB_ROOT / "analytics.duckdb",     temp = None (no spill to SSD)
        """
        try:
            from hledac.universal.paths import DB_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT
            if RAMDISK_ACTIVE:
                self._db_path = DB_ROOT / "shadow_analytics.duckdb"
                self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            else:
                self._db_path = DB_ROOT / "analytics.duckdb"
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
        Synchronous close - canonical sync cleanup path.

        Explicit divergence from aclose():
          - executor is shut down (re-init NOT supported)
          - graph slots (_truth_write_graph, _ioc_graph, _stix_graph) are NOT closed
          - semantic_store is NOT closed
          - bg_tasks are NOT cancelled (sync path has no bg task infrastructure)

        Cleanup ordering:
          1. _sync_close_on_worker()  - closes DuckDB connections + WAL LMDB
          2. _do_close()              - executor.shutdown + dedup LMDB close

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return
        self._closed = True
        self._initialized = False
        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:
            pass
        # Sprint 8L: close DuckDB connections via the worker thread.
        # Must submit because connections are thread-affine to the duckdb_worker.
        try:
            f = self._executor.submit(self._sync_close_on_worker)
            f.result(timeout=5)
        except Exception:
            pass
        self._do_close()

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

        # Sprint 8AG §6.17 + F216G: Initialize DedupManager
        # Uses PERSISTENT LMDB root (LMDB_ROOT), not sprint LMDB
        if self._dedup_manager is None:
            self._dedup_manager = DedupManager()
            self._dedup_manager.initialize()

        # Sprint 8L: Bounded startup replay - only when limit is set and positive
        if replay_pending_limit:
            await self._bounded_startup_replay(
                replay_pending_limit=replay_pending_limit,
                replay_timeout_s=replay_timeout_s,
            )
            self._startup_replay_done = True

        # Sprint F202K: Ensure target_profiles schema exists
        self.ensure_target_profiles_schema()

        # Sprint DuckDB Write Coalescer: start the coalescer loop task
        # Coalescer batches findings from N concurrent lanes into large batches
        # before passing them to async_ingest_findings_batch (reducing call frequency)
        if self._coalescer is None:
            try:
                from hledac.universal.storage.write_coalescer import CoalescerConfig, WriteCoalescer
                cfg = CoalescerConfig.from_env()
                self._coalescer = WriteCoalescer(
                    flush_fn=self.async_ingest_findings_batch,
                    config=cfg,
                )
                await self._coalescer.start()
                logger.debug(
                    "write_coalescer: started coalescer "
                    "(max_batch=%d, flush_interval=%.3fs)",
                    cfg.max_batch_size,
                    cfg.flush_interval_s,
                )
            except Exception:
                # Fail-open: if coalescer fails to start, direct calls still work
                self._coalescer = None

        # P3-2: Start background checkpoint task for DuckDB native WAL.
        # Only active for file mode (_db_path is not None).
        if self._db_path is not None:
            self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())

        self._startup_ready.set()
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
    # Async Context Manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> DuckDBShadowStore:
        """
        Async context manager entry - initializes the store.

        Usage:
            async with DuckDBShadowStore() as store:
                await store.async_insert_finding(...)
            # aclose() called automatically on exit
        """
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
                try:
                    rows = batch.to_pylist()
                except Exception:
                    cols = [batch.column(c).to_pylist() for c in range(batch.num_columns)]
                    names = batch.schema.names
                    rows = [dict(zip(names, row, strict=False)) for row in zip(*cols, strict=False)]
                for row in rows:
                    yield row
        except Exception:
            return

    async def async_query_arrow_batches(
        self,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[Any]:
        """
        F231 C: Streaming Arrow batch query - yields batches without loading full result.

        Uses DuckDB's `fetch_record_batch()` when available (DuckDB 1.2+ with Arrow
        extension), falls back to `to_arrow_reader()`, and finally to a warn-telemetry
        chunked fetch if neither is available.

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
                # result.description is stable - read once, reuse across all batches
                columns = [col[0] for col in result.description]
                while True:
                    try:
                        batch = reader.read_next_batch()
                    except StopIteration:
                        break
                    if batch is None:
                        break
                    try:
                        yield [
                            tuple(row[c] for c in columns)
                            for row in batch.to_pylist()
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
                pass  # noqa: BARE-EXCEPT  # fall through to fetchmany

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
        Sprint DuckDB Write Coalescer: submit findings to the coalescer for batched write.

        This is the preferred write path from concurrent lanes — findings are coalesced
        into large batches before being passed to async_ingest_findings_batch().

        NOTE: findings list must not be mutated after this call returns.
        Caller is responsible for ensuring this.

        Fail-safe: if coalescer is not available (not initialized, or failed to start),
        falls back to direct async_ingest_findings_batch() call.

        Returns: None (fire-and-forget async write through coalescer).
        """
        if not findings:
            return
        if self._coalescer is not None:
            await self._coalescer.submit(findings)
        else:
            # Coalescer not available — direct write (fail-safe fallback)
            try:
                await self.async_ingest_findings_batch(findings)
            except Exception:
                pass

    async def drain_and_get_accepted(
        self,
        findings: list[CanonicalFinding],
    ) -> list[Any]:
        """
        Flush pending coalescer items and ingest new findings, returning merged results.

        This is the merge-path alternative to submit_findings() for call sites
        that need the accepted/stored counts from async_ingest_findings_batch().

        Args:
            findings: new findings to submit alongside any pending items in the queue.

        Returns:
            Merged list of FindingQualityDecision/ActivationResult objects,
            one per finding submitted. Empty list on failure or if coalescer
            is not running.
        """
        if not findings:
            return []
        if self._coalescer is not None:
            return await self._coalescer.drain_and_get_accepted(findings)
        # Coalescer not available — direct write (fail-safe fallback)
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
            _logger.warning("_sync_upsert_target_memory failed: %s", _exc)
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
        Sprint 8TA B.4: Upsert entities into ghost_global store.

        Path: ~/.hledac/ghost_global.duckdb  (file named .duckdb but backed by SQLite)
        filelock: ~/.hledac/ghost_global.lock
        Engine: sqlite3 (NOT DuckDB) - legacy naming preserved for file identity
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

        Sprint 8TA B.4 fix: Batch upsert eliminates N+1 query pattern.
        Before: N SELECTs + N INSERTs (2000 ops for 1000 entities)
        After:  Single transaction with batch INSERT OR REPLACE
        """
        import os as _os
        import sqlite3
        from pathlib import Path

        ghost_home = Path.home() / ".hledac"
        ghost_home.mkdir(parents=True, exist_ok=True)
        db_path = ghost_home / "ghost_global.duckdb"
        lock_path = ghost_home / "ghost_global.lock"

        # Use file-based locking
        import fcntl
        lock_file = open(lock_path, "w")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            conn = sqlite3.connect(db_path)
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
            # Before: N SELECTs + N INSERTs (2000 ops for 1000 entities)
            # After:  Single executemany with ON CONFLICT handling (2 ops total)
            conn.executemany(
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
            conn.close()
            return len(entities)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            try:
                _os.remove(lock_path)
            except Exception:
                pass

    async def async_query_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        Return top sources by hit rate for the last N days.
        Thread-safe, non-blocking.
        """
        if not self._initialized or self._closed:
            return []
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
                    for i in range(len(fields)):
                        v = pr[i] or (0 if i < 3 else 0.0)
                        prior_avg[i] += v / len(prior_rows)
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
            provenance_json = _get_canonical_encoder().encode(finding.provenance).decode("utf-8")
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

        # Step 2: DuckDB - tuple rows via executemany (batch, ~10* faster than N individual inserts)
        duckdb_all_ok = False
        try:
            rows: list[list] = []
            for f in findings:
                provenance_json = _get_canonical_encoder().encode(f.provenance).decode("utf-8")
                rows.append([
                    f.finding_id, f.query, f.source_type, f.confidence,
                    f.ts, provenance_json,
                ])
            inserted = self._sync_insert_findings_bulk_as_tuples(rows)
            duckdb_all_ok = inserted >= len(findings)
            if inserted < len(findings):
                _logger.error(f"[D7] Partial DuckDB batch: {inserted}/{len(findings)}")
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

        loop = asyncio.get_running_loop()
        try:
            results = await loop.run_in_executor(
                self._executor,
                self._canonical_findings_batch_to_activation_results,
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
                self._graph_ingest_findings(findings)

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

        3-stupňový fallback na legacy `async_record_canonical_findings_batch`:
          1. pyarrow chybí (try/except import) -> legacy
          2. `HLEDAC_ARROW_INGEST == "0"` (env gate, default ON, opt-out) -> legacy
          3. `len(findings) < _ARROW_MIN_BATCH` (default 20, F265C lowered from 50) -> legacy
          4. sync helper vrátí 0 (jakýkoliv error v Table build / register / INSERT) -> legacy

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
        try:
            wal_ok, duckdb_result = await asyncio.gather(wal_future, duckdb_future)
        except Exception as exc:
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] concurrent executor error ({exc}), using legacy path "
                f"for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)

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
        # Same shape as _sync_record_canonical_findings_batch_arrow_full returns.
        if isinstance(duckdb_result, Exception):
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            logger.warning(
                f"[D7-arrow-fallback] DuckDB executor exception ({duckdb_result}), "
                f"using legacy path for {len(findings)} findings"
            )
            return await self.async_record_canonical_findings_batch(findings)

        duckdb_count, duckdb_err = duckdb_result
        if duckdb_err is not None:
            _logger.error(f"[D7] DuckDB Arrow bulk failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            _logger.error(
                f"[D7] Partial DuckDB batch: {duckdb_count}/{len(findings)}"
            )
            duckdb_all_ok = False
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
                self._graph_ingest_findings(findings)
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
            getattr(self, "_startup_ready", None) and getattr(self._startup_ready, "is_set", lambda: None)(),
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
                provenance_json = _get_canonical_encoder().encode(f.provenance).decode("utf-8")
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
        _is_feed_source: bool = finding.source_type == "rss_atom_pipeline"

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
                        # feed=0.80: feed sources often have similar phrasing, need looser dedup
                        # non-feed=0.85: standard OSINT findings
                        # Historická pozn.: 0.85/0.95 bylo příliš přísné (sprint 1780830658)
                        _semantic_thresh = 0.80 if _is_feed_source else 0.85
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
        # Process in batches of 1024 with event-loop yield between chunks
        CHUNK_SIZE = 1024  # noqa: N806  # M1-safe (~6 MB peak)

        # SEQUENTIAL-2 Level 3: Cross-batch pipelining via bounded pending queue.
        # Architecture:
        #   - Quality gate (Rust rayon, CPU) runs on current thread (fast)
        #   - WAL + DuckDB (I/O + CPU) runs on wal_executor + duckdb_arrow_executor
        #   - Pipeline: while chunk N WAL+DuckDB runs, process chunk N+1 quality gate
        #   - Bounded queue (maxsize=2) prevents unbounded memory growth on M1 8GB
        #   - WAL-first invariant preserved: each batch's DuckDB awaits its own WAL
        #   - Backpressure: if queue full (2 pending batches), yield until one completes
        # On M1 8GB: 2 pending batches × 1024 items × ~5KB ≈ 10 MB max queued.
        _PIPELINE_QUEUE: asyncio.Queue | None = None

        async def _get_pipeline_queue() -> asyncio.Queue:
            nonlocal _PIPELINE_QUEUE
            if _PIPELINE_QUEUE is None:
                _PIPELINE_QUEUE = asyncio.Queue(maxsize=2)
            return _PIPELINE_QUEUE

        pending_tasks: list[tuple[list[int], asyncio.Task]] = []  # (accepted_indices, storage_task)

        for chunk_start in range(0, n, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, n)
            chunk_findings = findings[chunk_start:chunk_end]

            # SEQUENTIAL-2 Level 3: Backpressure — wait for queue slot before processing next chunk.
            # This prevents unbounded memory growth when storage is slower than ingestion.
            # q.join() blocks until all items currently in queue have been processed (task_done called).
            q = await _get_pipeline_queue()
            if q.full():
                await q.join()

            # Sprint P1-2: Batch quality gate via assess_batch (Rust rayon-parallel).
            # Falls back to per-row _assess_finding_quality on any exception.
            fail_open_chunk_findings: list[CanonicalFinding] = []
            fail_open_chunk_indices: list[int] = []
            chunk_accepted_findings: list[CanonicalFinding] = []
            chunk_accepted_indices: list[int] = []

            try:
                chunk_decisions: list[FindingQualityDecision] = self._assess_finding_quality_batch(chunk_findings)
                for i_offset, f in enumerate(chunk_findings):
                    i = chunk_start + i_offset
                    decision = chunk_decisions[i_offset]
                    if not decision.accepted:
                        self._record_quality_rejection(f, decision)
                        results[i] = decision
                    else:
                        # Sprint F216K §1: TemporalAnonymizer - pre-write timestamp anonymization
                        if os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1":
                            try:
                                from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
                                if not hasattr(self, "_temporal_anonymizer"):
                                    self._temporal_anonymizer = TemporalAnonymizer()
                                f.timestamp = self._temporal_anonymizer.anonymize_timestamp(f.timestamp)
                            except Exception:
                                pass
                        chunk_accepted_findings.append(f)
                        chunk_accepted_indices.append(i)
            except Exception:
                # Sprint P1-2: Fall back to per-row assess on batch failure
                self._quality_state._quality_fail_open_count += 1
                for i_offset, f in enumerate(chunk_findings):
                    i = chunk_start + i_offset
                    try:
                        decision = self._assess_finding_quality(f)
                    except Exception:
                        fail_open_chunk_findings.append(f)
                        fail_open_chunk_indices.append(i)
                        continue
                    if not decision.accepted:
                        self._record_quality_rejection(f, decision)
                        results[i] = decision
                    else:
                        if os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1":
                            try:
                                from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
                                if not hasattr(self, "_temporal_anonymizer"):
                                    self._temporal_anonymizer = TemporalAnonymizer()
                                f.timestamp = self._temporal_anonymizer.anonymize_timestamp(f.timestamp)
                            except Exception:
                                pass
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

            # SEQUENTIAL-2 Level 3: Pipeline WAL + DuckDB for this chunk CONCURRENTLY
            # while next iteration's quality gate runs. Queue provides bounded pending slots.
            # Bridge: Coalescer flushes at 1024 items → quality gate filters → accepted chunk
            #         (typically 1-500 items) is passed to _piped_storage → Arrow path.
            if chunk_accepted_findings:
                loop = asyncio.get_running_loop()
                q_ref = q  # Bind current queue ref before awaiting

                async def _piped_storage(
                    findings_to_store: list[CanonicalFinding],
                    indices: list[int],
                    queue_ref: asyncio.Queue,
                ) -> tuple[list[int], list[ActivationResult]]:
                    try:
                        # F5.2: Track coalescer → Arrow synergy.
                        # Each _piped_storage call = one coalescer-chunk that passed quality gate.
                        # If >= _ARROW_MIN_BATCH, Arrow path is eligible; if <, falls to legacy.
                        if len(findings_to_store) >= _ARROW_MIN_BATCH:
                            self._arrow_metrics["arrow_coalescer_potential"] += len(findings_to_store)
                        else:
                            self._arrow_metrics["arrow_coalescer_small_chunk"] += len(findings_to_store)
                        return (indices, await self.async_record_canonical_findings_batch_arrow(findings_to_store))
                    finally:
                        queue_ref.task_done()

                await q_ref.put(None)  # Sentinel: slot reserved in queue
                task = loop.create_task(_piped_storage(chunk_accepted_findings, chunk_accepted_indices, q_ref))
                pending_tasks.append((chunk_accepted_indices, task))

            if chunk_end < n:
                await asyncio.sleep(0)

        # SEQUENTIAL-2 Level 3: Wait for all pending storage tasks to complete and merge results.
        # Quality gate for ALL chunks is already done; we only wait for WAL+DuckDB here.
        all_accepted_findings: list[CanonicalFinding] = []
        for indices, task in pending_tasks:
            try:
                chunk_indices, storage_results = await task
                for idx, sr in zip(chunk_indices, storage_results, strict=False):
                    results[idx] = sr
                    if getattr(sr, "accepted", False):
                        all_accepted_findings.append(findings[idx])
            except Exception:
                logger.warning("[SEQUENTIAL-2] pending storage task failed, falling back to error result")
                for idx in indices:
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

        # Sprint F241: Graph real-time wire - fire graph update async after accepted write.
        # Graph is ADVISORY ONLY - write path never blocks on graph success/failure.
        # Guarded by HLEDAC_GRAPH_REALTIME_WIRE env var (default False, require explicit opt-in).
        if all_accepted_findings and os.getenv("HLEDAC_GRAPH_REALTIME_WIRE") == "true":
            # Sprint 5.4: Wire batch Rust IOC extraction into the graph write path.
            # batch_ioc_extract_unified (rayon-parallel, 2 workers) extracts IOCs from
            # payload_texts in one Rust call instead of slower Python per-finding path.
            # Fail-soft: graph update never blocks ingest on IOC extraction errors.
            if _IOC_EXTRACT_BATCH_AVAILABLE and _rust_batch_ioc_extract is not None:
                try:
                    ioc_texts = [f.payload_text or f.query or "" for f in all_accepted_findings]
                    ioc_results: list[list[tuple[str, str]]] = _rust_batch_ioc_extract(ioc_texts)
                    if ioc_results and self._graph is not None:
                        truth_graph = self._graph
                        buffer_ioc = getattr(truth_graph, "buffer_ioc", None)
                        flush_buffers = getattr(truth_graph, "flush_buffers", None)
                        if callable(buffer_ioc) and callable(flush_buffers):
                            import xxhash
                            for finding_idx, finding in enumerate(all_accepted_findings):
                                for ioc_value, ioc_type in ioc_results[finding_idx]:
                                    ioc_id = f"{ioc_type}:{xxhash.xxh64(ioc_value.encode()).hexdigest()}"
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
        import pyarrow as _pa

        try:
            # Build columnar arrays - zero-copy for str/float, single alloc per column.
            # msgspec.encode returns bytes; we decode once per row, but the result is
            # a single pa.string() array (one contiguous buffer, no Python list of lists).
            provenance_arr = _pa.array(
                [
                    _get_canonical_encoder().encode(f.provenance).decode("utf-8")
                    for f in findings
                ],
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

    def _sync_record_canonical_findings_batch_arrow_full(
        self,
        findings: list[CanonicalFinding],
    ) -> list[dict]:
        """
        Sprint P0-4: Full Arrow batch - LMDB WAL first, then Arrow DuckDB.

        Same shape as `_canonical_findings_batch_to_activation_results` but the
        DuckDB step is the Arrow path. Returns list[dict] with 1:1 mapping.

        MUST be called on the worker thread.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        if not findings:
            return []

        ret: list[dict] = []

        # Step 1: LMDB WAL first (mirror legacy path) - msgspec + per-finding key.
        # Reuses the same fallback logic as _canonical_findings_batch_to_activation_results.
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
                        f"[P0-4 Arrow] Batch WAL failed for {len(items)} items"
                    )
                    for _ in findings:
                        ret.append({
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "lmdb batch failed",
                        })
                    return ret
        except Exception as e:
            _logger.error(f"[P0-4 Arrow] Batch WAL exception: {e}")
            for _ in findings:
                ret.append({
                    "lmdb_success": False,
                    "duckdb_success": None,
                    "error": str(e),
                })
            return ret

        # Step 2: DuckDB Arrow bulk - returns (count, error_type), fail-soft.
        duckdb_count, duckdb_err = self._sync_record_canonical_findings_batch_arrow(findings)
        if duckdb_err is not None:
            _logger.error(
                f"[P0-4 Arrow] DuckDB Arrow bulk failed: {duckdb_err}"
            )
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            # Arrow bulk wrote some but not all - cannot determine which specific
            # rows failed (ON CONFLICT DO NOTHING), treat as all-or-nothing.
            _logger.error(
                f"[P0-4 Arrow] Partial DuckDB batch: {duckdb_count}/{len(findings)}"
            )
            duckdb_all_ok = False
        else:
            duckdb_all_ok = True

        # Step 3: Build per-finding results (1:1, mirrors legacy).
        # Propagate duckdb_err so async wrapper can do typed telemetry.
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

        Cleanup ordering:
          1. Sets _closed=True + _initialized=False immediately
          2. Clears boot barrier (_startup_ready)
          3. Closes DuckDB connections via _sync_close_on_worker (worker thread)
          4. Cancels bg tasks (graph ingest)
          5. Closes truth_write_graph (flush_buffers + close)
          6. Closes ioc_graph (DuckPGQGraph donor)
          7. Closes semantic_store
          8. Closes stix_graph
          9. Closes WAL LMDB + dedup LMDB
          10. Executor is NOT shut down - kept alive for re-init SAFETY

        Executor keep-alive contract (Sprint 8L):
          A new ThreadPoolExecutor cannot reuse the same worker thread.
          Keeping the existing executor allows async_initialize() to re-attach
          to the same thread on the same store instance after aclose().
          This is the ONLY supported re-init path.

        Idempotent: safe to call multiple times.
        """
        if self._closed:
            return

        self._closed = True
        self._initialized = False

        # Sprint 8L: reset boot barrier for re-initialize safety
        # Use clear() instead of replacing the Event object - avoids loop-affinity issues
        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:
            pass

        # Sprint 8H: close connections synchronously by calling the worker method
        # directly. This is safe because DuckDB connections are owned by the
        # worker thread and we are calling from the main thread - the single
        # ThreadPoolExecutor(1 worker) ensures no concurrent access during
        # the synchronous close call. We use submit() to wake the blocked worker.
        try:
            f = self._executor.submit(self._sync_close_on_worker)
            f.result(timeout=5)  # wait for close to complete
        except Exception:
            pass

        # Sprint 8QA: cancel pending background tasks
        # F233A fix: use getattr() instead of hasattr() - _bg_tasks is always set in __init__
        # so hasattr always True; getattr correctly returns the empty set before first use
        _bg = getattr(self, "_bg_tasks", None)
        if _bg:
            for t in _bg:
                t.cancel()
            await safe_gather_fire_and_forget(*_bg, label="duckdb_store:5746")
            _bg.clear()

        # P3-2: cancel background checkpoint task
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            self._checkpoint_task = None

        # Sprint 8WA: close truth-write graph (IOCGraph with buffer_ioc/flush_buffers)
        # GUARD: flush_buffers is IOCGraph-only. DuckPGQGraph has no flush_buffers.
        # Sprint F222: delegated to GraphAttachmentStore
        gs = self._graph_store() if hasattr(self, "_DuckDBShadowStore__graph_store") and self.__graph_store is not None else None  # noqa: E501
        truth_graph = gs.get_truth_write_graph() if gs else None
        if truth_graph is not None:
            try:
                if callable(getattr(truth_graph, "flush_buffers", None)):
                    await truth_graph.flush_buffers()
            except Exception:
                pass
            try:
                if callable(getattr(truth_graph, "close", None)):
                    await truth_graph.close()
            except Exception:
                pass

        # Sprint 8QA/8TF: close analytics/donor IOC graph (DuckPGQGraph)
        # DuckPGQGraph has checkpoint() and close() but no flush_buffers.
        ioc_graph = gs.get_analytics_graph_for_synthesis() if gs else None
        if ioc_graph is not None:
            try:
                if callable(getattr(ioc_graph, "close", None)):
                    await ioc_graph.close()
            except Exception:
                pass

        # Sprint 8SB: close semantic store
        if self._semantic_store is not None:
            try:
                await self._semantic_store.close()
            except Exception:
                pass
            self._semantic_store = None

        # Sprint 8VQ: close STIX-only graph slot
        stix_graph = gs.get_stix_graph() if gs else None
        if stix_graph is not None:
            try:
                if callable(getattr(stix_graph, "close", None)):
                    await stix_graph.close()
            except Exception:
                pass

        # Sprint 8L: Do NOT shutdown the executor - keep it alive for re-initialization.
        # A new ThreadPoolExecutor cannot reuse the same thread, so we keep the
        # existing one to allow async_initialize() to run again on the same store
        # instance after aclose().
        # Sprint 8L: close WAL LMDB to release lock files
        # F233A: _wal_lmdb is declared in __init__ so getattr() is safe
        _wal = getattr(self, "_wal_lmdb", None)
        if _wal is not None:
            try:
                _wal.close()
            except Exception:
                pass
            self._wal_lmdb = None

        # Sprint 8AG: close dedup LMDB - mirrors _do_close() symmetry
        # F233A: _dedup_lmdb is declared in __init__ so getattr() is safe
        _dedup = getattr(self, "_dedup_lmdb", None)
        if _dedup is not None:
            try:
                _dedup.close()
            except Exception:
                pass
            self._dedup_lmdb = None

        # Sprint DuckDB Write Coalescer: stop the coalescer task
        if self._coalescer is not None:
            try:
                await self._coalescer.stop(timeout_s=10.0)
            except Exception:
                pass
            self._coalescer = None

        # Sprint F265B Variant B: Reset _arrow_metrics via weakref.finalize
        # Ensures metrics are cleared even if aclose() is not called explicitly
        # (e.g., store dropped without await). weakref.finalize callback runs on GC
        # if aclose() is skipped; direct clear() covers the normal aclose() path.
        try:
            _finalizer = weakref.finalize(
                self,
                lambda m: m.clear(),
                self._arrow_metrics,
            )
            # Registering the finalizer is enough; detach it so GC doesn't double-call
            _finalizer.atexit = False
        except Exception:
            pass
        # Direct clear() for the normal aclose() path
        self._arrow_metrics.clear()

        # Sprint F265B Variant B: Reassess executor pool size after sprint wind-down.
        # Memory may have been released; scale back up if pressure has decreased.
        self._adjust_executor_pool()

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
                    for batch in rows
                    for r in batch
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
            pass  # noqa: BARE-EXCEPT  # psutil unavailable, skip RAM check

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._vacuum_sync)
            return True
        except Exception as e:
            logger.warning("[duckdb_vacuum] VACUUM failed: %s", e)
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
            logger.info("[duckdb_vacuum] DB size %.1fGB > threshold, running VACUUM", size / (1024**3))
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
                    result = conn.execute(sql, [finding_id])
                    result = list(self.arrow_fetch_batch(conn, sql, [finding_id]))
                    return result
                finally:
                    conn.close()
            else:
                # :memory: mode - use persistent connection
                sql = "SELECT 1 FROM canonical_findings WHERE id = ? LIMIT 1"
                result = self._persistent_conn.execute(sql, [finding_id])
                result = list(self.arrow_fetch_batch(self._persistent_conn, sql, [finding_id]))
                return len(result) > 0
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
        try:
            # Sprint 1.2: shut down write + read pools
            self._write_executor.shutdown(wait=False)
            self._read_executor.shutdown(wait=False)
            # Sprint F285: shut down WAL + DuckDB Arrow split pools
            self._wal_executor.shutdown(wait=False)
            self._duckdb_arrow_executor.shutdown(wait=False)
        except Exception:
            pass
        # Sprint 8AG + F216G: close DedupManager (WAL already closed via _sync_close_on_worker)
        if self._dedup_manager is not None:
            try:
                self._dedup_manager.close()
            except Exception:
                pass
            self._dedup_manager = None

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
            from hledac.universal.paths import LMDB_ROOT
            dedup_path = LMDB_ROOT / "dedup.lmdb"
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

        LAZY IMPORT: graph_service imported here to avoid circular deps
        with duckdb_store.
        """
        try:
            from hledac.universal.knowledge.graph_service import _get_graph

            def _sync_graph_update() -> None:
                try:
                    gs = _get_graph()
                    rows = [
                        (f.ioc_value, f.ioc_type, float(f.confidence), f.source_type or "")
                        for f in accepted_findings
                        if hasattr(f, "ioc_value") and hasattr(f, "ioc_type")
                    ]
                    if rows:
                        gs.upsert_ioc_batch(rows)
                except Exception:
                    pass  # noqa: BARE-EXCEPT  # fail-safe: graph is advisory, never propagates

            # Bounded in-flight cap: reuse Sprint 8QA self._bg_tasks set.
            # getattr fallback covers F233A test fixtures that bypass __init__.
            tasks = getattr(self, "_bg_tasks", None)
            if tasks is None:
                tasks = set()
                self._bg_tasks = tasks
            if len(tasks) >= _MAX_INFLIGHT_GRAPH_UPDATES:
                return  # advisory: drop excess, never block write path

            # asyncio is imported at module level.
            # get_running_loop() (3.10+) raises RuntimeError when no loop
            # is running - replaces deprecated get_event_loop() (which used
            # to silently create one in 3.9 and was removed in 3.12).
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No event loop - sync context (test/CLI); skip advisory.

            async def _graph_update_coro() -> None:
                # DuckDB is NOT thread-safe; route sync upsert via the
                # default ThreadPoolExecutor (in-process, M1 EIGHTGB friendly).
                await loop.run_in_executor(None, _sync_graph_update)

            task = asyncio.create_task(_graph_update_coro())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        except Exception:
            pass  # noqa: BARE-EXCEPT  # fail-safe: feature-gated, never blocks write path

    # P3-2: DuckDB native WAL checkpoint loop
    async def _checkpoint_loop(self) -> None:
        """
        Background checkpoint task for DuckDB native WAL.

        Runs every 60s to flush WAL to main database file, bounding WAL growth.
        Fail-safe: any error is silently caught and logged.
        Only active for file mode; _checkpoint_task is None for :memory: mode.
        """
        _logger = logging.getLogger(__name__)
        while True:
            try:
                await asyncio.sleep(60)
                if self._closed:
                    break
                if self._file_conn is None:
                    continue
                try:
                    self._file_conn.execute("PRAGMA checkpoint")
                except Exception as e:
                    _logger.debug(f"[P3-2] checkpoint error: {e}")
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
    RAMDISK_ACTIVE=True: db at DB_ROOT, temp at RAMDISK_ROOT
    RAMDISK_ACTIVE=False: degraded :memory: fallback

    This is the ONE place in main.py where DuckDBShadowStore is instantiated
    for the owned runtime path. Avoids coupling __main__.py to DuckDBShadowStore
    internals.

    Returns:
        DuckDBShadowStore: initialized store ready for async_initialize()
    """
    try:
        from hledac.universal.paths import DB_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT

        if RAMDISK_ACTIVE:
            db_path = DB_ROOT / "shadow_analytics.duckdb"
            temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            return DuckDBShadowStore(db_path=db_path, temp_dir=temp_dir)
        else:
            # Degraded mode: :memory: (no durability)
            return DuckDBShadowStore()
    except Exception:
        # Fallback: :memory: even if paths.py import fails
        return DuckDBShadowStore()


"""
DuckDB Shadow Analytics Sidecar
================================

Sprint 8AO + 8AS: DuckDB shadow-mode analytical sidecar with async safety.

DESIGN PRINCIPLES
-----------------
- This is a SIDE CAR, not a replacement for SQLite/Kuzu/LanceDB
- DuckDB is NOT imported at module level of any boot-path file
- DuckDB import is deferred to first actual use inside initialize()
- When RAMDISK_ACTIVE=True: persistent DB under DB_ROOT, temp under RAMDISK_ROOT
- When RAMDISK_ACTIVE=False: :memory: mode with persistent single connection
- All DB operations run on a dedicated single-worker ThreadPoolExecutor
- All async public methods use run_in_executor to avoid event-loop blocking
- Connection is created INSIDE the worker thread (thread-affine)
- PRAGMA threads=2 applied after connection init
- Batch methods enforce chunking: max_batch_size=500
- aclose() is idempotent with _closed flag

SCHEMA
------
shadow_findings:  id, query, source_type, confidence, ts
shadow_runs:      run_id, started_at, ended_at, total_fds, rss_mb

ASYNC API SURFACE
----------------
- async_initialize()       — async init wrapper
- async_record_shadow_run(...)   — insert run record
- async_record_shadow_finding(...)  — insert single finding
- async_record_shadow_findings_batch(..., max_batch_size=500) — chunked batch
- async_query_recent_findings(limit=10)  — query findings
- async_healthcheck()      — returns True if healthy
- aclose()            — async idempotent shutdown

USAGE (async)
------------
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

store = DuckDBShadowStore()
await store.async_initialize()
await store.async_record_shadow_run("run1", time.time(), None, 50, 512)
await store.async_record_shadow_findings_batch([...], max_batch_size=500)
await store.aclose()
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import os
import time as _time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import xxhash
import msgspec
from typing import Any, Dict, List, Optional, TypedDict

__all__ = ["DuckDBShadowStore", "ActivationResult", "ReplayResult", "CanonicalFinding"]


class ActivationResult(TypedDict):
    """
    Typed result contract for activation record operations.

    Fields:
        finding_id:     Unique identifier of the finding
        lmdb_success:   True if LMDB WAL write succeeded
        duckdb_success: True if DuckDB write succeeded, False if it failed,
                        None if not yet attempted
        lmdb_key:       "finding:{id}" — LMDB key used
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
      - finding_id: str       — unique identifier
      - query: str             — research query text
      - source_type: str       — source type (e.g., "web", "document", "synthetic")
      - confidence: float       — confidence score [0.0, 1.0]
      - ts: float              — Unix timestamp
      - provenance: tuple[str, ...] — tvrdý invariant, nesmí být None, default = ()

    Volitelná pole:
      - payload_text: str | None — supplementary text payload

    DTO invariants:
      - frozen=True  — immutabilní instance
      - gc=False     — zakázán garbage collector tracking (výkon)
      - msgspec.Struct — zero-copy decode/encode

    TODO 8Q/8R: zvážit přesun CanonicalFinding do sdíleného DTO modulu,
                pokud bude používán mimo storage vrstvu
    """

    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()

    # Volitelné doplňkové pole — jde do LMDB WAL payloadu, ne do DuckDB INSERT
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


# ---------------------------------------------------------------------------
# Quality helper constants and functions
# ---------------------------------------------------------------------------

# Sprint 8W: Configurable entropy threshold (bits per character)
_QUALITY_ENTROPY_THRESHOLD: float = 0.5
# Strings shorter than this skip entropy filtering
_QUALITY_MIN_ENTROPY_LEN: int = 8


def _normalize_for_quality(text: str) -> str:
    """
    Sprint 8W: Normalize text for entropy and dedup quality checks.

    Normalization rules:
      - lowercase
      - strip leading/trailing whitespace
      - collapse internal whitespace to single space (includes tabs/newlines)
      - remove non-printable chars (ord < 32) that are NOT whitespace

    Tabs and newlines (ord 9, 10) are whitespace and get collapsed to space first.
    Other non-printable chars (BEL, NUL, etc.) are removed after whitespace normalization.

    No stemming, lemmatization, transliteration, or locale-dependent logic.
    """
    # Lowercase first
    lowered = text.lower()
    # Strip leading/trailing whitespace
    stripped = lowered.strip()
    # Collapse ALL whitespace (space, tab, newline) to single space
    normalized = " ".join(stripped.split())
    # Remove non-printable chars (ord < 32) that are NOT whitespace
    # Whitespace chars: tab(9), newline(10), vertical tab(11), form feed(12), carriage return(13)
    import string
    whitespace_chars = set(string.whitespace)  # includes space, \t, \n, \v, \f, \r
    cleaned = "".join(ch for ch in normalized if ord(ch) >= 32 or ch in whitespace_chars)
    return cleaned


def _compute_entropy(text: str) -> float:
    """
    Sprint 8W: Compute Shannon entropy in bits per character.

    Uses collections.Counter for efficiency (no Python for-loop over characters).
    Returns 0.0 for empty text.
    """
    if not text:
        return 0.0
    char_counts = Counter(text)
    total = len(text)
    entropy = 0.0
    for count in char_counts.values():
        p = count / total
        if p > 0:
            import math as _math
            entropy -= p * _math.log2(p)
    return entropy


def _normalize_osint_url(url: str) -> str:
    """
    Sprint 8AK: Normalize an OSINT URL for deterministic dedup fingerprinting.

    Rules:
      - lowercase scheme + host
      - strip fragment (#...)
      - strip trailing slash from non-root paths
      - remove common tracking query params (utm_source, utm_medium, utm_campaign, ref, etc.)
      - preserve query params that may affect content identity

    Returns normalized URL string.
    """
    if not url or not isinstance(url, str):
        return ""

    try:
        from urllib.parse import urlparse, urlencode, parse_qsl
    except ImportError:
        return url

    # Strip leading/trailing whitespace
    url = url.strip()

    # Parse — lenient on malformed URLs
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # 1. Lowercase scheme and host
    scheme = parsed.scheme.lower() if parsed.scheme else "http"
    netloc = parsed.netloc.lower()

    # 2. Strip fragment
    fragment = ""

    # 3. Normalize path: strip trailing slash unless root "/"
    path = parsed.path.rstrip("/") if len(parsed.path) > 1 else parsed.path

    # 4. Filter tracking query params — Sprint 8AM spec only
    # Sprint 8AM C.0: strip only these 7 params; preserve "source" param
    TRACKING_QUERY_PARAMS = frozenset({
        "utm_source", "utm_medium", "utm_campaign",
        "utm_content", "utm_term",
        "fbclid",
        "ref",
    })
    try:
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in query_params if k.lower() not in TRACKING_QUERY_PARAMS]
        query = urlencode(filtered) if filtered else ""
    except Exception:
        query = parsed.query

    # Reconstruct
    # netloc may contain port
    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized += f"?{query}"
    if fragment:
        normalized += f"#{fragment}"

    return normalized


def _compute_dedup_fingerprint(text: str) -> str:
    """
    Sprint 8W: Compute BLAKE2b-128 fingerprint of normalized text.

    Uses hashlib.blake2b (NOT Python built-in hash()).
    digest_size=16 → 32 hex chars.
    Stable across process restarts.
    """
    normalized = _normalize_for_quality(text)
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def _compute_url_fingerprint(url: str) -> str:
    """
    Sprint 8AK: URL-first dedup fingerprint.

    If a canonical URL is available in provenance, use it as the primary
    dedup signal (source-independent, deterministic). Falls back to
    BLAKE2b(text) when no URL is present.

    URL is normalized before fingerprinting per OSINT URL normalization rules.

    Returns 32-char hex BLAKE2b-128 fingerprint.
    """
    normalized_url = _normalize_osint_url(url)
    if normalized_url:
        return hashlib.blake2b(normalized_url.encode("utf-8"), digest_size=16).hexdigest()
    return ""


# ---------------------------------------------------------------------------
# Package-level guard: duckdb is imported only inside initialize()
# ---------------------------------------------------------------------------

_DuckDBModule: Optional[Any] = None


def _get_duckdb() -> Any:
    """Lazy import of duckdb — only loaded when sidecar is actually used."""
    global _DuckDBModule
    if _DuckDBModule is None:
        import duckdb

        _DuckDBModule = duckdb
    return _DuckDBModule


# ---------------------------------------------------------------------------
# Env-configurable limits
# ---------------------------------------------------------------------------

_DUCKDB_MEMORY_LIMIT: str = os.environ.get("GHOST_DUCKDB_MEMORY", "1GB")
_DUCKDB_MAX_TEMP: str = os.environ.get("GHOST_DUCKDB_MAX_TEMP", "1GB")

# Sprint 8AG §6.17: Persistent dedup config
_DEDUP_LMDB_MAP_SIZE: int = 64 * 1024 * 1024  # 64MB dedicated dedup LMDB
_DEDUP_HOT_CACHE_MAX: int = 10_000  # hard cap on in-memory dedup cache entries


# ---------------------------------------------------------------------------
# Schema SQL (defined once, used in both modes)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS shadow_findings (
        id              VARCHAR PRIMARY KEY,
        query           VARCHAR,
        source_type     VARCHAR,
        confidence      DOUBLE,
        ts              DOUBLE,
        provenance_json TEXT
    );
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
        findings_per_min REAL DEFAULT 0,
        top_source_type TEXT,
        synthesis_confidence REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS source_hit_log (
        sprint_id TEXT,
        ts DOUBLE,
        source_type TEXT,
        findings_count INT,
        ioc_count INT,
        hit_rate REAL
    );
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
"""

# Sprint 8R: Module-level reusable encoder singleton for CanonicalFinding serialization
_CANONICAL_ENCODER = msgspec.json.Encoder()


class DuckDBShadowStore:
    """
    Async-safe DuckDB sidecar with RAMDISK-first / OPSEC-safe degraded mode.

    CONNECTION MODEL:
    - MODE A (RAMDISK active): file-backed DB + temp on RAMDISK
    - MODE B (RAMDISK inactive): :memory: with PERSISTENT single connection
    - All DB work runs on a dedicated single-worker ThreadPoolExecutor
    - Connection is created INSIDE the worker thread (thread-affine)
    - All public async methods use run_in_executor to avoid event-loop blocking

    RAMDISK_ACTIVE=True:
        database = DB_ROOT / "shadow_analytics.duckdb"
        temp_directory = RAMDISK_ROOT / "duckdb_tmp"  (created before connect)
        memory_limit = GHOST_DUCKDB_MEMORY or 1GB
        max_temp_directory_size = GHOST_DUCKDB_MAX_TEMP or 1GB

    RAMDISK_ACTIVE=False:
        database = :memory:  (single persistent connection)
        max_temp_directory_size = 0GB  (spill disabled)
        NO temp_directory set to SSD fallback
    """

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        temp_dir: Optional[Path | str] = None,
    ) -> None:
        """
        Initialize DuckDBShadowStore.

        Args:
            db_path:  Optional explicit DB path. If None, resolved via _resolve_path().
                     Passing a Path enables file-mode (MODE A) without requiring paths.py.
            temp_dir: Optional explicit temp directory for DuckDB scratch space.
                     Required when db_path is set; ignored for :memory: mode.
        """
        self._initialized: bool = False
        self._closed: bool = False
        # Sprint 8D: test-friendly seam — allow db_path/temp_dir injection
        self._db_path: Optional[Path] = Path(db_path) if db_path is not None else None
        self._temp_dir: Optional[Path] = Path(temp_dir) if temp_dir is not None else None
        self._memory_limit: str = _DUCKDB_MEMORY_LIMIT
        self._max_temp: str = _DUCKDB_MAX_TEMP
        self._duckdb_module: Optional[Any] = None

        # Single-worker executor for all DB operations (thread-affine)
        # Named thread so tests can assert duckdb_worker via thread name
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="duckdb_worker",
        )

        # Persistent connection for :memory: mode; None for file mode
        self._persistent_conn: Optional[Any] = None

        # Sprint 7H: Persistent file-backed connection for file mode
        self._file_conn: Optional[Any] = None

        # Async queue for batch scheduling (optional, deferred to future sprint)
        # For 8AS: direct run_in_executor for each call

        # Sprint 8H: Per-instance replay guard — prevents concurrent replay of same markers
        # NOTE: _replay_lock is lazy; initialize it lazily on first async use
        self._replay_lock: Optional[asyncio.Lock] = None

        # Sprint 8L: Boot barrier — startup replay must complete before writes are accepted
        self._startup_ready: asyncio.Event = asyncio.Event()  # set after init + optional replay
        self._startup_replay_done: bool = False  # True once startup replay has run

        # Sprint 8W: Quality gate counters (separate from storage counters)
        self._quality_rejected_count: int = 0   # low-entropy reject count
        self._quality_duplicate_count: int = 0  # in-memory / quality-layer duplicate count
        self._quality_fail_open_count: int = 0  # quality helper exception → fail-open

        # Sprint 8AK: Persistent duplicate counter (LMDB-backed, cross-source dedup)
        self._persistent_duplicate_count: int = 0

        # Sprint 8AV: Accepted findings counter (quality gate passed → stored)
        self._accepted_count: int = 0

        # Sprint 8AV: Dead-letter namespace for ingested-but-rejected findings
        self.DEAD_LETTER_PREFIX: str = "deadletter_ingest:"

        # Sprint 8W: In-memory dedup set (key = BLAKE2b fingerprint, val = finding_id)
        # Hot cache only — LMDB is the authority for persistence across restarts
        self._dedup_fingerprints: Dict[str, str] = {}

        # Sprint 8AG §6.17: Persistent dedup LMDB — separate from WAL LMDB
        # Namespace: b"dedup:{fingerprint_hex}" → finding_id (UTF-8 bytes)
        self._dedup_lmdb: Optional[Any] = None
        self._dedup_lmdb_path: Optional[Path] = None
        self._dedup_lmdb_last_error: Optional[str] = None
        self._dedup_lmdb_boot_error: Optional[str] = None
        # Bounded hot cache — hard limit to prevent unbounded memory growth
        self._dedup_hot_cache: Dict[str, str] = {}  # fp → finding_id, bounded
        self._dedup_hot_cache_order: list[str] = []  # FIFO order for eviction

        # Sprint 8QA: Background task tracking for graph ingest
        self._bg_tasks: set[asyncio.Task] = set()
        # Sprint 8QA: Injectable IOCGraph instance
        # NON-AUTHORITATIVE: store is NOT graph truth owner. The injected graph
        # may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).
        # Capability must be checked, never assumed. Set by inject_graph().
        self._ioc_graph: Any = None
        self._graph_attachment_kind: Optional[str] = None  # class name of attached backend

        # Sprint 8VQ: Dedicated STIX-only graph slot.
        # TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().
        # DuckPGQGraph is analytics/donor — never inject into _stix_graph.
        # _stix_graph is independent of _ioc_graph (analytics) and _graph_attachment_kind.
        self._stix_graph: Any = None

        # Sprint 8WA: Dedicated truth-write graph slot for ACTIVE buffered writes.
        # TRUTH-WRITE ONLY: only IOCGraph (Kuzu) supports buffer_ioc/flush_buffers.
        # This slot is INDEPENDENT of _ioc_graph (analytics/donor) and _stix_graph (STIX).
        # _truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest.
        self._truth_write_graph: Any = None

        # Sprint 8SB: Semantic store (FastEmbed + LanceDB)
        self._semantic_store: Optional[Any] = None

    # ---------------------------------------------------------------------------
    # Sprint 8QA: IOC Graph integration
    # ---------------------------------------------------------------------------

    def inject_graph(self, graph: Any) -> None:
        """
        Inject a graph instance for IOC ingest on canonical findings.

        STORE IS NOT GRAPH TRUTH OWNER — the injected graph may be:
          - IOCGraph (Kuzu): truth backend, full capability
          - DuckPGQGraph (DuckDB): donor/alternate backend, limited capability

        Capability requirements for buffered writes (ACTIVE phase):
          - Requires: buffer_ioc(), buffer_observation(), flush_buffers()
          - IOCGraph has these. DuckPGQGraph does NOT.

        After inject, use get_graph_attachment_kind() to determine
        which backend was attached and check capabilities explicitly.
        """
        self._ioc_graph = graph
        self._graph_attachment_kind = graph.__class__.__name__ if graph is not None else None

    def get_graph_attachment_kind(self) -> Optional[str]:
        """
        NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.

        Returns None if no graph attached.
        Use this to determine which backend is attached, then call
        hasattr/hasattr for specific capability checks before use.

        This is a COMPAT SEAM, not a canonical graph API.
        """
        return self._graph_attachment_kind

    def graph_supports_buffered_writes(self) -> bool:
        """
        NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase
        buffered writes?

        Returns True only if attached graph has both:
          - buffer_ioc()
          - flush_buffers()

        IOCGraph (Kuzu): True — has full buffered write capability.
        DuckPGQGraph (DuckDB): False — has checkpoint() and add_ioc() only.

        Always check this before triggering background graph ingest,
        do not assume all injected graphs support buffered writes.
        """
        if self._ioc_graph is None:
            return False
        return (
            callable(getattr(self._ioc_graph, "buffer_ioc", None))
            and callable(getattr(self._ioc_graph, "flush_buffers", None))
        )

    def inject_stix_graph(self, graph: Any) -> None:
        """
        Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.

        TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().
        DuckPGQGraph must NEVER be injected here — it lacks STIX capability.

        This slot is INDEPENDENT of _ioc_graph (analytics/donor graph).
        _stix_graph is used exclusively by synthesis runners for STIX context.

        Args:
            graph: IOCGraph (Kuzu) instance or None to clear.
        """
        self._stix_graph = graph

    def get_stix_graph(self) -> Any:
        """
        Sprint 8VQ: Get injected STIX graph for synthesis consumers.

        Returns the injected truth-store graph (IOCGraph/Kuzu) if available,
        else None. DuckPGQGraph is never returned — it lacks export_stix_bundle().

        This is a CONSUMER-SPECIFIC seam, not a generic graph accessor.
        """
        return self._stix_graph

    def inject_truth_write_graph(self, graph: Any) -> None:
        """
        Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.

        TRUTH-WRITE ONLY: only IOCGraph (Kuzu) supports buffer_ioc/flush_buffers.
        DuckPGQGraph must NEVER be injected here — it lacks buffered write capability.

        This slot is INDEPENDENT of:
          - _ioc_graph (analytics/donor graph — DuckPGQGraph in windup)
          - _stix_graph (STIX synthesis graph)

        _truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest
        via _graph_ingest_findings().

        Args:
            graph: IOCGraph (Kuzu) instance or None to clear.
        """
        self._truth_write_graph = graph

    def get_truth_write_graph(self) -> Any:
        """
        Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.

        Returns the injected truth-write graph (IOCGraph/Kuzu) if available,
        else None. DuckPGQGraph is never returned — it lacks buffer_ioc/flush_buffers.

        This is a CONSUMER-SPECIFIC seam for ACTIVE-phase buffered writes only.
        """
        return self._truth_write_graph

    def truth_write_graph_supports_buffered_writes(self) -> bool:
        """
        Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?

        Returns True only if _truth_write_graph is IOCGraph (Kuzu) with both:
          - buffer_ioc()
          - flush_buffers()

        This is a dedicated check for the truth-write slot, independent of
        the analytics _ioc_graph slot.
        """
        if self._truth_write_graph is None:
            return False
        return (
            callable(getattr(self._truth_write_graph, "buffer_ioc", None))
            and callable(getattr(self._truth_write_graph, "flush_buffers", None))
        )

    # ------------------------------------------------------------------
    # Sprint 8TF: Export/Store Seed Seam
    # ------------------------------------------------------------------

    def get_top_seed_nodes(self, n: int = 5) -> list[dict]:
        """
        Sprint 8TF §1: Export-facing read-only seam for top seed nodes.

        PURPOSE
        -------
        Provides a store-facing surface for the export handoff's seed-node use case.
        export_sprint() currently falls back to store._ioc_graph.get_top_nodes_by_degree(n=5)
        directly; this method wraps that call so export consumers don't need to spelunk
        _ioc_graph internals.

        STORE IS NOT GRAPH TRUTH OWNER
        --------------------------------
        The injected graph may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).
        This seam does NOT make DuckDBShadowStore a graph authority.
        It is a thin, fail-open adapter for one specific export-facing read-only operation.

        FUTURE OWNER / REMOVAL CONDITION
        ---------------------------------
        - Future graph truth owner: IOCGraph (Kuzu) or its successor
        - Removal condition: export_sprint() replaces its store._ioc_graph fallback
          entirely with this method, AND no other consumer accesses _ioc_graph directly
          for seed node queries

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires the attached graph to implement get_top_nodes_by_degree(n).
        IOCGraph (Kuzu): has this method.
        DuckPGQGraph (DuckDB): has this method.
        If the method is absent or call fails, returns [] (fail-open).

        Args:
            n: Number of top nodes to return (default 5).

        Returns:
            list[dict]: Each dict has at least "value" and "ioc_type" keys.
            Returns [] if no graph attached or call fails.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "get_top_nodes_by_degree", None)
            if not callable(method):
                return []
            result = method(n=n)
            # Validate return shape — expect list of dicts with value/ioc_type
            if not isinstance(result, list):
                return []
            for item in result:
                if not isinstance(item, dict) or "value" not in item:
                    return []
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint 8VY: Analytics graph read-only seams — REMOVED shell-private access
    # ------------------------------------------------------------------
    # __main__._run_sprint_mode() previously used:
    #   getattr(store_instance, "_ioc_graph", None).stats()
    #   getattr(store_instance, "_ioc_graph", None).find_connected()
    # _windup_synthesis() previously used:
    #   elif hasattr(store, "_ioc_graph") and store._ioc_graph: runner.inject_graph(store._ioc_graph)
    #
    # These private-slot accesses are replaced by narrow, fail-open seam methods below.
    # STORE IS NOT GRAPH TRUTH OWNER — these are diagnostic/analytics read-only seams.

    def get_graph_stats(self) -> dict:
        """
        Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).

        PURPOSE
        -------
        Replaces direct shell access to store._ioc_graph.stats() in __main__._run_sprint_mode().
        DuckDBShadowStore is NOT a graph authority — this is a thin fail-open adapter
        for the diagnostics use case only.

        CONSUMER
        --------
        __main__._run_sprint_mode(): logging [GRAPH] nodes/edges/pgq stats.

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        The analytics _ioc_graph (DuckPGQGraph) is the donor backend.
        Returns {} (fail-open) if no graph attached or call fails.

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires attached graph to implement stats() → {nodes, edges, pgq_active}.
        DuckPGQGraph: has this method.
        IOCGraph: has this method.

        Returns:
            dict: {nodes, edges, pgq_active} or {} if unavailable.
        """
        if self._ioc_graph is None:
            return {}
        try:
            method = getattr(self._ioc_graph, "stats", None)
            if not callable(method):
                return {}
            result = method()
            if not isinstance(result, dict):
                return {}
            # Validate minimal shape
            if not all(k in result for k in ("nodes", "edges")):
                return {}
            return result
        except Exception:
            return {}

    def get_connected_iocs(self, ioc_value: str, max_hops: int = 2) -> list:
        """
        Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).

        PURPOSE
        -------
        Replaces direct shell access to store._ioc_graph.find_connected() in
        __main__._run_sprint_mode(). Diagnostic use case: log connected nodes for top IOC.
        DuckDBShadowStore is NOT a graph authority — thin fail-open adapter.

        CONSUMER
        --------
        __main__._run_sprint_mode(): logging {first_ioc} → {len(connected)} connected nodes.

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        The analytics _ioc_graph (DuckPGQGraph) is the donor backend.
        Returns [] (fail-open) if no graph attached or call fails.

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires attached graph to implement find_connected(value, max_hops) → list.
        DuckPGQGraph: has this method.
        IOCGraph: does NOT have this method → returns [] (fail-open).

        Args:
            ioc_value: The IOC value to find connections for.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            list: Connected IOC nodes or [] if unavailable.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "find_connected", None)
            if not callable(method):
                return []
            result = method(ioc_value, max_hops=max_hops)
            if not isinstance(result, list):
                return []
            return result
        except Exception:
            return []

    def get_analytics_graph_for_synthesis(self) -> Any:
        """
        Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().

        PURPOSE
        -------
        Replaces the elif hasattr(store, "_ioc_graph") and store._ioc_graph fallback in
        _windup_synthesis(). This is the Priority 2 / analytics-donor path for synthesis.

        CONSUMER
        --------
        _windup_synthesis(): runner.inject_graph(store.get_analytics_graph_for_synthesis())

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        DuckDBShadowStore is NOT graph authority. This seam explicitly labels the
        analytics donor backend. Callers must handle None.

        CAPABILITY REQUIREMENTS
        -----------------------
        DuckPGQGraph (analytics donor) has: stats, get_top_nodes_by_degree, export_edge_list.
        DuckPGQGraph does NOT have: export_stix_bundle, buffer_ioc, flush_buffers.
        For STIX, use store.get_stix_graph() (Priority 1).

        Returns:
            Any: The attached analytics graph (DuckPGQGraph) or None.
        """
        return self._ioc_graph

    # ------------------------------------------------------------------
    # Sprint 8TF §2: ghost_global seam — top-100 IOC entities for cross-sprint accumulation
    # ------------------------------------------------------------------

    def get_top_entities_for_ghost_global(
        self,
        n: int = 100,
    ) -> list[tuple[str, str, float]]:
        """
        Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.

        PURPOSE
        -------
        Provides a store-facing surface for the ghost_global upsert use case.
        __main__.py previously spelunked graph attachment internals directly:
            graph.get_nodes()[:100]  ← method does not exist on any graph backend
        This method wraps the correct capability query so __main__.py never accesses
        _ioc_graph internals for this use case.

        STORE IS NOT GRAPH TRUTH OWNER
        --------------------------------
        The injected graph is the authoritative store (IOCGraph=Kuzu or DuckPGQGraph=DuckDB).
        This seam is a thin, fail-open adapter for one specific consumer: ghost_global upsert.
        It does NOT make DuckDBShadowStore a graph authority.

        PAYLOAD SHAPE
        -------------
        Returns list[tuple[str, str, float]] — exactly the shape required by
        upsert_global_entities(entities: list[tuple[str, str, float]]).
        Each tuple: (entity_value, entity_type, confidence_cumulative)

        FUTURE OWNER / REMOVAL CONDITION
        ---------------------------------
        - Future graph truth owner: IOCGraph (Kuzu) — should expose this directly
        - Removal condition: IOCGraph.get_top_entities_for_ghost_global(n=100)
          covers this use case with no remaining __main__.py consumer

        CAPABILITY REQUIREMENTS
        ------------------------
        Requires the attached graph to implement get_top_nodes_by_degree(n).
        DuckPGQGraph (DuckDB): has this method, returns dicts with value/ioc_type/confidence.
        IOCGraph (Kuzu): does NOT have this method — returns [] (fail-open).
        Fail-open: returns [] if graph is None or method is absent.

        Args:
            n: Number of top entities to return (default 100).

        Returns:
            list[tuple[str, str, float]]: Bounded entity payload for ghost_global upsert.
            Returns [] if no graph attached or call fails.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "get_top_nodes_by_degree", None)
            if not callable(method):
                return []
            result = method(n=n)
            if not isinstance(result, list):
                return []
            entities: list[tuple[str, str, float]] = []
            for item in result:
                if isinstance(item, dict):
                    val = item.get("value", "")
                    ioc_type = item.get("ioc_type", "unknown")
                    conf = float(item.get("confidence", 0.5))
                    if val:
                        entities.append((val, ioc_type, conf))
            return entities
        except Exception:
            return []

    def inject_semantic_store(self, store: Any) -> None:
        """
        Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.

        The store is used to buffer findings for FastEmbed embedding + LanceDB
        indexing during WINDUP flush.
        """
        self._semantic_store = store

    def _semantic_buffer_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Sprint 8SB: Buffer findings into SemanticStore for batch embedding.

        Runs as a background task (not awaited). Fail-open: any exception
        is caught and logged — semantic buffering failure never blocks storage.
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        if self._semantic_store is None:
            return
        try:
            for f in findings:
                text = f.payload_text or ""
                if not text:
                    continue
                # Collect IOC types from pattern_matches
                ioc_types: list[str] = []
                pm = getattr(f, "pattern_matches", None)
                if pm:
                    for item in pm:
                        if isinstance(item, tuple) and len(item) >= 2:
                            ioc_types.append(str(item[1]))
                        elif isinstance(item, dict):
                            lbl = item.get("label") or ""
                            if lbl:
                                ioc_types.append(str(lbl))
                ioc_types = list(set(ioc_types)) if ioc_types else []
                self._semantic_store.buffer_finding(
                    text=text,
                    source_type=getattr(f, "source_type", "unknown"),
                    finding_id=f.finding_id,
                    ts=getattr(f, "ts", 0.0),
                    ioc_types=ioc_types,
                )
        except Exception as exc:
            _logger.debug("Semantic buffering skipped: %s", exc)

    def _graph_ingest_findings(self, findings: list[CanonicalFinding]) -> None:
        """
        Background task: ingest findings into IOC graph.

        Called via _bg_tasks tracking after async_ingest_findings_batch succeeds.
        Fail-open: any exception is caught and logged.
        """
        # Sprint 8WA: Use dedicated truth-write graph, not analytics _ioc_graph.
        if self._truth_write_graph is None:
            return

        loop = asyncio.get_running_loop()

        async def _run() -> None:
            try:
                from hledac.universal.knowledge.ioc_graph import (
                    extract_iocs_from_text,
                )

                for finding in findings:
                    text = finding.payload_text or ""
                    # pattern_matches may be attached as extra field
                    matches: list[tuple[str, str]] = []
                    pm = getattr(finding, "pattern_matches", None)
                    if pm:
                        if isinstance(pm, list):
                            for item in pm:
                                if isinstance(item, tuple) and len(item) == 2:
                                    matches.append((str(item[0]), str(item[1])))
                                elif isinstance(item, dict):
                                    v = item.get("value") or item.get("pattern") or ""
                                    l = item.get("label") or ""
                                    matches.append((str(v), str(l)))

                    iocs = extract_iocs_from_text(text, matches)
                    if not iocs:
                        continue

                    # Sprint 8SA: Buffer IOCs — ZERO Kuzu I/O in ACTIVE phase
                    ts = finding.ts
                    src = finding.source_type
                    fid = str(finding.finding_id)

                    id_map: dict[str, str] = {}  # value → ioc_id (computed from value)
                    for value, ioc_type in iocs:
                        await self._truth_write_graph.buffer_ioc(ioc_type, value, 1.0)
                        ioc_id = f"{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}"
                        id_map[value] = ioc_id

                    # Buffer observations — pair all IOCs from same finding
                    values = list(id_map.keys())
                    for i, v_a in enumerate(values):
                        id_a = id_map[v_a]
                        for v_b in values[i + 1:]:
                            id_b = id_map[v_b]
                            await self._truth_write_graph.buffer_observation(
                                id_a, id_b, fid, ts, src
                            )
            except Exception:
                pass

        t = asyncio.create_task(_run())
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    # ---------------------------------------------------------------------------
    # Replay constants (Sprint 8H)
    # ---------------------------------------------------------------------------

    REPLAY_CHUNK_SIZE: int = 100  # markers per chunk; yields event loop between chunks
    MAX_RETRY_COUNT: int = 3      # max retries before dead-lettering a marker
    DEADLETTER_PREFIX: str = "deadletter_duckdb_sync:"  # dead-letter namespace

    # ------------------------------------------------------------------
    # Internal sync helpers — ALL run on the worker thread
    # ------------------------------------------------------------------

    def _init_connection(self) -> None:
        """
        Initialize the DuckDB connection. Must be called from the worker thread.
        Sets up file or :memory: mode, applies PRAGMAs and schema.
        For file mode, creates persistent _file_conn (Sprint 7H).
        """
        duckdb = _get_duckdb()

        if self._db_path:
            # MODE A: RAMDISK active — persistent file DB + temp on RAMDISK
            if self._temp_dir is None:
                self._temp_dir = self._db_path.parent / "duckdb_tmp"
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(self._db_path))
            conn.execute(f"SET memory_limit = '{self._memory_limit}'")
            conn.execute(f"SET max_temp_directory_size = '{self._max_temp}'")
            conn.execute(f"SET temp_directory = '{self._temp_dir}'")
            conn.execute("PRAGMA threads=2")
            conn.execute(_SCHEMA_SQL)
            conn.close()
            # Sprint 8RC: ALTER TABLE for retrokompatibilita (B.2)
            self._apply_schema_migrations()
            # Sprint 7H: Persistent file-backed connection for reuse across writes
            self._file_conn = duckdb.connect(str(self._db_path))
            self._file_conn.execute(f"SET memory_limit = '{self._memory_limit}'")
            self._file_conn.execute(f"SET max_temp_directory_size = '{self._max_temp}'")
            self._file_conn.execute(f"SET temp_directory = '{self._temp_dir}'")
            self._file_conn.execute("PRAGMA threads=2")
        else:
            # MODE B: RAMDISK inactive — :memory: with PERSISTENT single connection
            self._persistent_conn = duckdb.connect(":memory:")
            self._persistent_conn.execute(f"SET memory_limit = '{self._memory_limit}'")
            self._persistent_conn.execute("SET max_temp_directory_size = '0GB'")
            self._persistent_conn.execute("PRAGMA threads=2")
            self._persistent_conn.execute(_SCHEMA_SQL)

    # Sprint 8RC: Retrokompatibilita — add missing columns to old DB files (B.2)
    def _apply_schema_migrations(self) -> None:
        """
        ALTER TABLE ADD COLUMN for any sprint_delta columns missing from old DBs.
        DuckDB doesn't have IF NOT EXISTS for ALTER, so we catch and ignore errors.
        """
        if self._db_path is None:
            return  # :memory: mode — nothing to migrate
        duckdb = _get_duckdb()
        conn = duckdb.connect(str(self._db_path))
        try:
            conn.execute(
                "ALTER TABLE sprint_delta ADD COLUMN findings_per_min REAL DEFAULT 0"
            )
        except Exception:
            pass  # column already exists
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

    def _sync_insert_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        ts: float | None = None,
        provenance_json: str | None = None,
    ) -> bool:
        """Sync insert — MUST be called on the worker thread."""
        try:
            if self._db_path:
                # Use persistent _file_conn for file mode
                if self._file_conn is not None:
                    self._file_conn.execute("BEGIN TRANSACTION")
                    try:
                        self._file_conn.execute(
                            """
                            INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            [finding_id, query, source_type, confidence, ts, provenance_json],
                        )
                        self._file_conn.execute("COMMIT")
                    except Exception:
                        self._file_conn.execute("ROLLBACK")
                        return False
                else:
                    # Fallback: per-call connection
                    duckdb = _get_duckdb()
                    conn = duckdb.connect(str(self._db_path))
                    conn.execute(
                        """
                        INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [finding_id, query, source_type, confidence, ts, provenance_json],
                    )
                    conn.close()
            else:
                self._persistent_conn.execute(
                    """
                    INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [finding_id, query, source_type, confidence, ts, provenance_json],
                )
            return True
        except Exception:
            return False

    def _sync_insert_findings_bulk(
        self,
        findings: List[Dict[str, Any]],
    ) -> int:
        """
        Sprint 7H: True bulk insert using executemany in explicit transaction.
        MUST be called on the worker thread.
        Returns number of successfully inserted records.
        """
        if not findings:
            return 0

        try:
            if self._db_path and self._file_conn is not None:
                # Prewarm on first use
                self._prewarm_file_conn()
                # Build rows list — dict-based, ts/provenance_json optional
                rows = [
                    [
                        r["id"], r["query"], r["source_type"], r["confidence"],
                        r.get("ts"), r.get("provenance_json"),
                    ]
                    for r in findings
                ]
                # Explicit transaction with executemany
                self._file_conn.execute("BEGIN TRANSACTION")
                try:
                    self._file_conn.executemany(
                        """
                        INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    self._file_conn.execute("COMMIT")
                    return len(rows)
                except Exception:
                    self._file_conn.execute("ROLLBACK")
                    return 0
            else:
                # :memory: mode — use persistent_conn
                rows = [
                    [
                        r["id"], r["query"], r["source_type"], r["confidence"],
                        r.get("ts"), r.get("provenance_json"),
                    ]
                    for r in findings
                ]
                self._persistent_conn.execute("BEGIN TRANSACTION")
                try:
                    self._persistent_conn.executemany(
                        """
                        INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    self._persistent_conn.execute("COMMIT")
                    return len(rows)
                except Exception:
                    self._persistent_conn.execute("ROLLBACK")
                    return 0
        except Exception:
            return 0

    def _sync_insert_run(
        self,
        run_id: str,
        started_at: Optional[float],
        ended_at: Optional[float],
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """Sync insert run — MUST be called on the worker thread. Uses persistent _file_conn."""
        try:
            started_iso = _dt.datetime.fromtimestamp(started_at).isoformat() if started_at is not None else None
            ended_iso = _dt.datetime.fromtimestamp(ended_at).isoformat() if ended_at is not None else None

            if self._db_path:
                # Sprint 8A: Use persistent _file_conn instead of per-call connect
                if self._file_conn is not None:
                    self._prewarm_file_conn()
                    self._file_conn.execute(
                        """
                        INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb)
                        VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?)
                        """,
                        [run_id, started_iso, ended_iso, total_fds, rss_mb],
                    )
                else:
                    # Fallback: per-call connection (should not happen in normal use)
                    duckdb = _get_duckdb()
                    conn = duckdb.connect(str(self._db_path))
                    conn.execute(
                        """
                        INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb)
                        VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?)
                        """,
                        [run_id, started_iso, ended_iso, total_fds, rss_mb],
                    )
                    conn.close()
            else:
                self._persistent_conn.execute(
                    """
                    INSERT INTO shadow_runs (run_id, started_at, ended_at, total_fds, rss_mb)
                    VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?)
                    """,
                    [run_id, started_iso, ended_iso, total_fds, rss_mb],
                )
            return True
        except Exception:
            return False

    def _sync_query_findings(self, limit: int) -> List[Dict[str, Any]]:
        """Sync query — MUST be called on the worker thread."""
        try:
            if self._db_path:
                duckdb = _get_duckdb()
                conn = duckdb.connect(str(self._db_path))
                result = conn.execute(
                    """
                    SELECT id, query, source_type, confidence, ts, provenance_json
                    FROM shadow_findings
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [limit],
                ).fetchall()
                conn.close()
            else:
                result = self._persistent_conn.execute(
                    """
                    SELECT id, query, source_type, confidence, ts, provenance_json
                    FROM shadow_findings
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [limit],
                ).fetchall()

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

    # ── Sprint 8RC: sync helpers ─────────────────────────────────────────────

    def _sync_insert_sprint_delta(self, row: dict) -> bool:
        """Sync insert — MUST be called on the worker thread."""
        try:
            if self._db_path and self._file_conn is not None:
                self._prewarm_file_conn()
                self._file_conn.execute(
                    """
                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        row["sprint_id"], row["ts"], row.get("query"),
                        row.get("duration_s", 0), row.get("new_findings", 0),
                        row.get("dedup_hits", 0), row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0), row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_min", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
                    ],
                )
            else:
                self._persistent_conn.execute(
                    """
                    INSERT INTO sprint_delta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        row["sprint_id"], row["ts"], row.get("query"),
                        row.get("duration_s", 0), row.get("new_findings", 0),
                        row.get("dedup_hits", 0), row.get("ioc_nodes", 0),
                        row.get("ioc_new_this_sprint", 0), row.get("uma_peak_gib", 0),
                        row.get("synthesis_success", False),
                        row.get("findings_per_min", 0),
                        row.get("top_source_type"),
                        row.get("synthesis_confidence", 0),
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
        """Sync insert source hit — MUST be called on the worker thread."""
        try:
            if self._db_path and self._file_conn is not None:
                self._prewarm_file_conn()
                self._file_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            else:
                self._persistent_conn.execute(
                    "INSERT INTO source_hit_log VALUES (?,?,?,?,?,?)",
                    [sprint_id, ts, source_type, findings_count, ioc_count, hit_rate],
                )
            return True
        except Exception:
            return False

    def _sync_query_sprint_trend(self, last_n: int) -> list[dict]:
        """Sync query — MUST be called on the worker thread."""
        try:
            if self._db_path:
                duckdb = _get_duckdb()
                conn = duckdb.connect(str(self._db_path))
                result = conn.execute(
                    """
                    SELECT sprint_id, ts, new_findings, ioc_nodes,
                           findings_per_min, synthesis_success, uma_peak_gib
                    FROM sprint_delta
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [last_n],
                ).fetchall()
                conn.close()
            else:
                result = self._persistent_conn.execute(
                    """
                    SELECT sprint_id, ts, new_findings, ioc_nodes,
                           findings_per_min, synthesis_success, uma_peak_gib
                    FROM sprint_delta
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [last_n],
                ).fetchall()
            return [
                {
                    "sprint_id": r[0], "ts": r[1],
                    "new_findings": r[2], "ioc_nodes": r[3],
                    "findings_per_min": r[4],
                    "synthesis_success": bool(r[5]) if r[5] is not None else False,
                    "uma_peak_gib": r[6] or 0.0,
                }
                for r in result
            ]
        except Exception:
            return []

    def _sync_query_source_leaderboard(self, since_ts: float) -> list[dict]:
        """Sync query — MUST be called on the worker thread."""
        try:
            if self._db_path:
                duckdb = _get_duckdb()
                conn = duckdb.connect(str(self._db_path))
                result = conn.execute(
                    """
                    SELECT source_type,
                           SUM(findings_count) as total_findings,
                           AVG(hit_rate) as avg_hit_rate,
                           COUNT(*) as sprint_appearances
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    ORDER BY total_findings DESC
                    """,
                    [since_ts],
                ).fetchall()
                conn.close()
            else:
                result = self._persistent_conn.execute(
                    """
                    SELECT source_type,
                           SUM(findings_count) as total_findings,
                           AVG(hit_rate) as avg_hit_rate,
                           COUNT(*) as sprint_appearances
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    ORDER BY total_findings DESC
                    """,
                    [since_ts],
                ).fetchall()
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
                duckdb = _get_duckdb()
                conn = duckdb.connect(str(self._db_path))
                result = conn.execute(
                    """
                    SELECT source_type, AVG(hit_rate) as avg_hit_rate
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    """,
                    [cutoff],
                ).fetchall()
                conn.close()
            else:
                result = self._persistent_conn.execute(
                    """
                    SELECT source_type, AVG(hit_rate) as avg_hit_rate
                    FROM source_hit_log
                    WHERE ts > ?
                    GROUP BY source_type
                    """,
                    [cutoff],
                ).fetchall()
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
        """Close all connections — MUST be called on the worker thread."""
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
        # Sprint 8L: Close WAL LMDB to release lock files for re-init
        if hasattr(self, "_wal_lmdb") and self._wal_lmdb is not None:
            try:
                self._wal_lmdb.close()
            except Exception:
                pass
            self._wal_lmdb = None

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
            from hledac.universal.paths import RAMDISK_ACTIVE, RAMDISK_ROOT, DB_ROOT
            if RAMDISK_ACTIVE:
                self._db_path = DB_ROOT / "shadow_analytics.duckdb"
                self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
            else:
                self._db_path = DB_ROOT / "analytics.duckdb"
                self._temp_dir = None
        except Exception:
            # Degraded fallback — :memory: (session-only, no durability)
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
        """Sync insert — backward compat. For async use async_record_shadow_finding()."""
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
        ended_at: Optional[float],
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """Sync insert — backward compat. For async use async_record_shadow_run()."""
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

    def query_recent_findings(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Sync query — backward compat. For async use async_query_recent_findings()."""
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_findings, limit)
            return fut.result()
        except Exception:
            return []

    def close(self) -> None:
        """
        Synchronous close — canonical sync cleanup path.

        Explicit divergence from aclose():
          - executor is shut down (re-init NOT supported)
          - graph slots (_truth_write_graph, _ioc_graph, _stix_graph) are NOT closed
          - semantic_store is NOT closed
          - bg_tasks are NOT cancelled (sync path has no bg task infrastructure)

        Cleanup ordering:
          1. _sync_close_on_worker()  — closes DuckDB connections + WAL LMDB
          2. _do_close()              — executor.shutdown + dedup LMDB close

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
        Async initialize — creates connection on the worker thread.

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

        # Sprint 8L: Re-initialize WAL LMDB if it was closed by a previous aclose()
        # This is needed because aclose() sets _wal_lmdb = None to release the lock file
        if not hasattr(self, "_wal_lmdb") or self._wal_lmdb is None:
            _wal_root = self._db_path.parent if self._db_path else None
            if _wal_root is not None:
                from hledac.universal.tools.lmdb_kv import LMDBKVStore
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))

        # Sprint 8AG §6.17: Initialize persistent dedup LMDB after WAL LMDB
        # Uses PERSISTENT LMDB root (LMDB_ROOT), not sprint LMDB
        self._init_persistent_dedup_lmdb()

        # Sprint 8L: Bounded startup replay — only when limit is set and positive
        if replay_pending_limit:
            await self._bounded_startup_replay(
                replay_pending_limit=replay_pending_limit,
                replay_timeout_s=replay_timeout_s,
            )
            self._startup_replay_done = True

        self._startup_ready.set()
        return True

    async def async_record_shadow_run(
        self,
        run_id: str,
        started_at: float,
        ended_at: Optional[float],
        total_fds: int,
        rss_mb: int,
    ) -> bool:
        """
        Insert a run record into the shadow analytics store.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.
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

    async def async_record_shadow_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Insert a single finding into the shadow analytics store.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.
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
        findings: List[Dict[str, Any]],
        max_batch_size: int = 500,
    ) -> int:
        """
        Sprint 7H: True bulk insert using executemany in explicit transaction.
        Each chunk is at most max_batch_size records.
        Returns the number of successfully inserted records.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.
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

    async def async_query_recent_findings(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Query recent findings ordered by timestamp descending.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.
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

    async def async_healthcheck(self) -> bool:
        """
        Quick health check — attempts a zero-cost query.

        Returns True if the store is healthy and responsive.
        """
        if not self._initialized or self._closed:
            return False

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
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

    def _sync_query_recent_findings_by_sprint(
        self,
        sprint_id: str,
        limit: int,
    ) -> list[dict]:
        """Sync — MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT id, query, source_type, confidence, ts
                FROM shadow_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                ORDER BY ts DESC
                LIMIT ?
                """,
                [sprint_id, sprint_id, limit],
            ).fetchall()
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
        """Sync — MUST be called on worker thread."""
        import re

        DOMAIN_RE = re.compile(
            r"(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
        )
        IP_RE = re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        )

        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT id, query, source_type, ts
                FROM shadow_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                ORDER BY ts DESC
                LIMIT ?
                """,
                [sprint_id, sprint_id, limit * 4],
            ).fetchall()
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
        time span (first→last ts). Bounded, read-only, fail-soft.

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
        """Sync — MUST be called on worker thread."""
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
                FROM shadow_findings
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
        """Sync — MUST be called on worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT
                    source_type,
                    COUNT(*) as findings_count,
                    AVG(confidence) as avg_confidence
                FROM shadow_findings
                WHERE query LIKE ('%' || ? || '%')
                   OR id LIKE ('%' || ? || '%')
                GROUP BY source_type
                ORDER BY findings_count DESC
                LIMIT ?
                """,
                [sprint_id, sprint_id, limit],
            ).fetchall()
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
        """Sync upsert scorecard — MUST be called on worker thread."""
        try:
            import orjson
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
    # Sprint 8UC B.2: research_episodes — sprint episode recall
    # ------------------------------------------------------------------

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
        import orjson
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
                    orjson.dumps(data.get("top_findings", [])).decode(),
                    orjson.dumps(data.get("ioc_clusters", [])).decode(),
                    orjson.dumps(data.get("source_yield", {})).decode(),
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
                rows = conn.execute(
                    """SELECT sprint_id, query, summary, top_findings, source_yield, ts
                       FROM research_episodes
                       ORDER BY ts DESC
                       LIMIT ?""",
                    [limit],
                ).fetchall()
                if not rows:
                    return []
                cols = ["sprint_id", "query", "summary", "top_findings", "source_yield", "ts"]
                return [dict(zip(cols, r)) for r in rows]
            except Exception:
                return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, _sync)

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
        Engine: sqlite3 (NOT DuckDB) — legacy naming preserved for file identity
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
        """Sync upsert global entities — MUST be called on worker thread."""
        import os as _os
        import sqlite3

        ghost_home = _os.path.join(_os.path.expanduser("~"), ".hledac")
        _os.makedirs(ghost_home, exist_ok=True)
        db_path = _os.path.join(ghost_home, "ghost_global.duckdb")
        lock_path = _os.path.join(ghost_home, "ghost_global.lock")

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
            count = 0
            now = _time.time()
            for entity_value, entity_type, confidence in entities:
                existing = conn.execute(
                    "SELECT sprint_count, confidence_cumulative FROM global_entities WHERE entity_value = ?",
                    (entity_value,),
                ).fetchone()
                if existing:
                    sprint_count = existing[0] + 1
                    confidence_cumulative = max(existing[1], confidence)
                else:
                    sprint_count = 1
                    confidence_cumulative = confidence
                conn.execute(
                    """
                    INSERT OR REPLACE INTO global_entities
                    (entity_value, entity_type, sprint_count, last_seen, confidence_cumulative)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (entity_value, entity_type, sprint_count, now, confidence_cumulative),
                )
                count += 1
            conn.commit()
            conn.close()
            return count
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

    def get_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        Convenience sync wrapper — returns last N sprints ordered by ts DESC.
        For use in sync contexts (e.g., report printing).
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
        Convenience sync wrapper — returns top sources by hit rate.
        For use in sync contexts (e.g., report printing).
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
    # Sprint Delta Read Seams — output / delta truth (Sprint F150H)
    # ------------------------------------------------------------------

    def get_sprint_scorecard_trend(self, last_n: int = 6) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper — returns last N scorecards
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT sprint_id, ts, findings_per_minute, ioc_density,
                       semantic_novelty, outlines_used, accepted_findings, ioc_nodes
                FROM sprint_scorecard
                ORDER BY ts DESC
                LIMIT ?
                """,
                [last_n],
            ).fetchall()
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

        Covers: new_findings, ioc_new_this_sprint, dedup_hits, findings_per_min,
        uma_peak_gib, synthesis_confidence.

        Use for: "how is this sprint tracking vs history" without ad-hoc SQL.
        Fail-soft — returns empty/near-zero fields on any error.
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}

            current_rows = conn.execute(
                """
                SELECT new_findings, ioc_new_this_sprint, dedup_hits,
                       findings_per_min, uma_peak_gib, synthesis_confidence
                FROM sprint_delta
                WHERE sprint_id = ?
                """,
                [current_sprint_id],
            ).fetchall()

            if not current_rows:
                return {}

            prior_rows = conn.execute(
                """
                SELECT new_findings, ioc_new_this_sprint, dedup_hits,
                       findings_per_min, uma_peak_gib, synthesis_confidence
                FROM sprint_delta
                WHERE sprint_id != ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                [current_sprint_id, lookback],
            ).fetchall()

            cur = current_rows[0]
            fields = [
                "new_findings", "ioc_new_this_sprint", "dedup_hits",
                "findings_per_min", "uma_peak_gib", "synthesis_confidence",
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
                deltas = {f: 0.0 for f in fields}

            return {
                "sprint_id": current_sprint_id,
                "current": {f: cur_vals[i] for i, f in enumerate(fields)},
                "vs_prior_mean": deltas,
            }
        except Exception:
            return {}

    def get_source_mix_trend(self, days: int = 14) -> list[dict]:
        """
        Sprint F150H: Convenience sync wrapper — returns source_type distribution
        broken down by sprint for the last `days`. Each row contains
        source_type, sprint_id, total_findings, and hit_rate.

        Use for: source mix reporting — is web growing vs feed vs document,
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT source_type, sprint_id,
                       SUM(findings_count) as total_findings,
                       AVG(hit_rate) as avg_hit_rate,
                       SUM(ioc_count) as total_iocs
                FROM source_hit_log
                WHERE ts > ?
                GROUP BY source_type, sprint_id
                ORDER BY sprint_id DESC, total_findings DESC
                """,
                [since_ts],
            ).fetchall()
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
        Sprint F150H: Derived yield metrics per sprint — new_findings / duration_s,
        dedup_hits ratio (dedup_hits / new_findings), and ioc_rate
        (ioc_new_this_sprint / new_findings). Returns last N sprints.

        Use for: "are we getting better at extracting unique findings from sources"
        — track yield improvement or degradation across sprints.
        """
        if not self._initialized or self._closed:
            return []
        try:
            fut = self._executor.submit(self._sync_query_yield_trend, last_n)
            return fut.result()
        except Exception:
            return []

    def _sync_query_yield_trend(self, last_n: int) -> list[dict]:
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT sprint_id, ts, new_findings, duration_s,
                       dedup_hits, ioc_new_this_sprint
                FROM sprint_delta
                ORDER BY ts DESC
                LIMIT ?
                """,
                [last_n],
            ).fetchall()
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
    # Sprint F150I: Variant B — high-value sprint ranking seams
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
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
                """,
                [last_n],
            ).fetchall()
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
        findings_per_min from sprint_delta for the same sprint.
        Returns ratio and warns if divergence > 2x.

        Use for: detecting scorecard / delta sync issues.
        Fail-soft — returns empty dict on any error.
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return {}
            rows = conn.execute(
                """
                SELECT
                    c.sprint_id,
                    c.findings_per_minute,
                    COALESCE(d.findings_per_min, 0) AS delta_fpm,
                    d.new_findings,
                    d.duration_s
                FROM sprint_scorecard c
                LEFT JOIN sprint_delta d ON c.sprint_id = d.sprint_id
                WHERE c.sprint_id = ?
                """,
                [sprint_id],
            ).fetchall()
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT sprint_id, ts, new_findings, duration_s,
                       findings_per_min, synthesis_confidence,
                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)
                       AS yield_per_min
                FROM sprint_delta
                WHERE new_findings > 0 AND duration_s > 0
                ORDER BY yield_per_min DESC
                LIMIT ?
                """,
                [last_n],
            ).fetchall()
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
        """Sync — MUST be called on the worker thread."""
        try:
            conn = self._file_conn if self._db_path else self._persistent_conn
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT sprint_id, ts, new_findings, duration_s,
                       findings_per_min, synthesis_confidence,
                       ROUND(new_findings / MAX(duration_s / 60, 0.001), 4)
                       AS yield_per_min
                FROM sprint_delta
                WHERE new_findings > 0 AND duration_s > 0
                ORDER BY yield_per_min ASC
                LIMIT ?
                """,
                [last_n],
            ).fetchall()
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
    # Public Activation API — WAL-first async wrappers (Sprint 8B)
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

        Order: LMDB WAL first → DuckDB second.
        If LMDB OK but DuckDB FAIL → desync=True, LMDB record preserved.
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

        # Sprint 8L: Boot barrier — wait for startup replay to complete before accepting writes
        if not self._startup_ready.is_set():
            try:
                await asyncio.wait_for(
                    self._startup_ready.wait(),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
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
            # result is a dict from _activation_record_finding — normalize to ActivationResult
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
        findings: List[Dict[str, Any]],
    ) -> List[ActivationResult]:
        """
        Record multiple findings with WAL-first semantics.

        Order: LMDB WAL first (via put_many) → DuckDB second (chunked batch).
        Returns one ActivationResult per finding in input order.
        Partial failure: if LMDB OK but DuckDB fails for some/all,
        those entries get desync=True.

        Args:
            findings: List of dicts, each must contain:
                      id, query, source_type, confidence

        Returns:
            List[ActivationResult] — one per finding
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

        # Sprint 8L: Boot barrier — wait for startup replay to complete before accepting writes
        if not self._startup_ready.is_set():
            try:
                await asyncio.wait_for(
                    self._startup_ready.wait(),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
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
            activated_ids = [f.get("id") for f in findings if f.get("id")]

            results: List[ActivationResult] = []
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
    # Sprint 8P: CanonicalFinding DTO — typed ingest API
    # ------------------------------------------------------------------

    async def async_record_canonical_finding(
        self,
        finding: CanonicalFinding,
    ) -> ActivationResult:
        """
        Sprint 8P: Typed ingest API for CanonicalFinding DTO.

        Adapts DTO → existing WAL-first activation path.
        Používá stejný single-thread write executor jako stávající API.

        DTO → storage contract mapping:
          finding.finding_id  → id
          finding.query       → query
          finding.source_type → source_type
          finding.confidence  → confidence
          finding.ts          → ts (in WAL only)
          finding.provenance  → LMDB WAL payload (DuckDB nemá provenance sloupec)
          finding.payload_text → LMDB WAL payload (DuckDB nemá payload_text sloupec)

        Returns ActivationResult with same contract as async_record_activation.

        Provenance: tvrdý invariant — stored in LMDB WAL payload only
        (DuckDB schema nemá provenance_sloupec; backward-compatible,
         probe_8l/probe_8h/probe_8f/probe_8b zůstávají kompatibilní)
        """
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
                await asyncio.wait_for(self._startup_ready.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                return ActivationResult(
                    finding_id=finding.finding_id,
                    lmdb_success=False,
                    duckdb_success=None,
                    lmdb_key=f"finding:{finding.finding_id}",
                    desync=False,
                    error="startup replay timeout",
                )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._canonical_finding_to_activation_result,
                finding,
            )
            desync = bool(result.get("lmdb_success") and result.get("duckdb_success") is False)
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=bool(result.get("lmdb_success")),
                duckdb_success=result.get("duckdb_success"),
                lmdb_key=f"finding:{finding.finding_id}",
                desync=desync,
                error=result.get("error"),
                accepted=True,
            )
        except Exception as e:
            return ActivationResult(
                finding_id=str(finding.finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{finding.finding_id}",
                desync=False,
                error=str(e),
            )

    def _canonical_finding_to_activation_result(
        self,
        finding: CanonicalFinding,
    ) -> dict:
        """
        Sync wrapper: CanonicalFinding DTO → ActivationResult dict.

        Sprint 8R: DTO → storage contract mapping:
          finding.finding_id  → id
          finding.query       → query
          finding.source_type → source_type
          finding.confidence  → confidence
          finding.ts          → ts (DOUBLE in DuckDB)
          finding.provenance  → provenance_json (JSON TEXT in DuckDB via msgspec)
          finding.payload_text → LMDB WAL payload only

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

        # Step 1: LMDB WAL first — msgspec serialization
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    result["error"] = "no wal root"
                    return result
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))

            key = f"finding:{finding.finding_id}"
            # Provenance is part of WAL payload — use msgspec for consistent serialization
            wal_payload = {
                "id": finding.finding_id,
                "query": finding.query,
                "source_type": finding.source_type,
                "confidence": finding.confidence,
                "ts": finding.ts,
                "provenance": finding.provenance,
                "payload_text": finding.payload_text,
            }
            lmdb_ok = self._wal_lmdb.put(key, wal_payload)
            result["lmdb_success"] = lmdb_ok
            if not lmdb_ok:
                _logger.warning(f"[Sprint 8P] WAL failed for {finding.finding_id}")
                return result
        except Exception as e:
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] WAL exception for {finding.finding_id}: {e}")
            return result

        # Step 2: DuckDB second — serialize provenance to JSON and pass ts + provenance_json
        try:
            # Serialize provenance tuple to JSON for DuckDB storage
            provenance_json = _CANONICAL_ENCODER.encode(finding.provenance).decode("utf-8")
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
                self._wal_write_pending_sync_marker(
                    finding.finding_id, finding.query, finding.source_type, finding.confidence,
                )
        except Exception as e:
            result["duckdb_success"] = False
            result["error"] = str(e)
            _logger.error(f"[Sprint 8P] DuckDB exception for {finding.finding_id}: {e}, LMDB preserved")
            self._wal_write_pending_sync_marker(
                finding.finding_id, finding.query, finding.source_type, finding.confidence,
            )

        return result

    async def async_record_canonical_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[ActivationResult]:
        """
        Sprint 8P: Batch typed ingest API for CanonicalFinding DTO list.

        Adapts DTO list → existing WAL-first batch activation path.
        Používá stejný single-thread write executor jako stávající API.

        Returns list[ActivationResult] — 1:1 mapping, len(results) == len(findings).
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
                await asyncio.wait_for(self._startup_ready.wait(), timeout=30.0)
            except asyncio.TimeoutError:
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
            # results is list[dict] — normalize to list[ActivationResult]
            # Sprint 8QA/8TF: trigger graph ingest in background (fire-and-forget via _bg_tasks)
            # GUARD: check capability before triggering — DuckPGQGraph does not have
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
            self._accepted_count += accepted_total

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

    # ------------------------------------------------------------------
    # Sprint F800A: Controller-facing async seam — thin adapters
    # ------------------------------------------------------------------

    async def async_get_recent_findings(
        self,
        limit: int = 10,
    ) -> list[CanonicalFinding]:
        """
        Sprint F800A: Controller-facing async adapter for recent findings.

        Thin wrapper around async_query_recent_findings — converts raw dict rows
        to CanonicalFinding instances so callers receive typed DTOs.

        Thread-safe, non-blocking — runs on duckdb_worker via run_in_executor.
        Returns empty list if store is closed or uninitialized.

        Args:
            limit: Maximum number of findings to return (ordered by ts DESC).

        Returns:
            List[CanonicalFinding] — ordered by ts descending, most recent first.
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
                # Malformed row — skip, do not crash
                continue

        return findings

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

        Thread-safe, non-blocking — delegates to async_record_canonical_findings_batch
        which uses the single-worker executor.

        Args:
            findings: List of CanonicalFinding or dict with keys:
                      finding_id, query, source_type, confidence, ts, provenance.

        Returns:
            List[ActivationResult] — 1:1 mapping, len(results) == len(findings).
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

        # Normalize dicts → CanonicalFinding
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

        # Delegate to truth path — returns list[ActivationResult]
        raw_results = await self.async_record_canonical_findings_batch(canonical_findings)

        # Normalize: async_record_canonical_findings_batch returns list[ActivationResult]
        # async_record_canonical_findings_batch returns list[ActivationResult] at type level,
        # but TypedDict is erased to dict at runtime. Normalize all results.
        normalized: list[ActivationResult] = []
        for r in raw_results:
            # r is dict at runtime (TypedDict erasure); convert if not already ActivResult
            if isinstance(r, dict) and "finding_id" in r:
                # Already a properly-shaped ActivationResult dict — pass through
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
                # Fallback — shouldn't happen with truth path
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
        Sync batch: CanonicalFinding list → list[dict] (not ActivationResult, avoid circular import).

        Returns one dict per finding in input order.
        LMDB WAL uses msgspec.json.encode for provenance serialization.
        DuckDB insert uses tuple rows (list of lists).
        """
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        results: list[dict] = []

        if not findings:
            return results

        # Step 1: LMDB WAL first — msgspec serialization
        lmdb_ok = False
        try:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    for f in findings:
                        results.append({
                            "finding_id": f.finding_id,
                            "lmdb_success": False,
                            "duckdb_success": None,
                            "error": "no wal root",
                        })
                    return results
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))

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
                lmdb_ok = self._wal_lmdb.put_many(items)
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

        # Step 2: DuckDB second — tuple rows with ts and provenance_json (Sprint 8R)
        try:
            rows: list[list] = []
            for f in findings:
                provenance_json = _CANONICAL_ENCODER.encode(f.provenance).decode("utf-8")
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
        for i, f in enumerate(findings):
            duckdb_success = duckdb_all_ok  # simplified per-item model
            results.append({
                "finding_id": f.finding_id,
                "lmdb_success": lmdb_ok,
                "duckdb_success": duckdb_success,
                "error": None,
            })


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

    def _assess_finding_quality(self, finding: CanonicalFinding) -> FindingQualityDecision:
        """
        Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.

        Sprint 8AK: URL-first fingerprint — if a canonical URL is present in
        provenance, use it (normalized) as the primary dedup signal, independent
        of source_type or payload position. Falls back to payload_text.

        Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.
        Lookup order: hot cache → persistent LMDB → store if miss.
        LMDB is the authority; hot cache is a bounded read-through cache.

        Returns FindingQualityDecision (frozen, immutable).
        Fail-open: any exception → accept with reason="quality_check_error".

        Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.
        If both are empty, falls back to query (may accept trivially).
        """
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

        # Sprint 8AK: Separate counter semantics for persistent vs in-memory duplicates
        # - Hot cache hit (in-memory, same process) → _quality_duplicate_count
        # - Persistent LMDB hit (cross-source, survives restarts) → _persistent_duplicate_count

        # Tier 1: hot cache (fast path, bounded)
        duplicate = self._hot_cache_lookup(fingerprint) is not None
        if duplicate:
            self._quality_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
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
            # Miss in hot cache but hit in LMDB — populate hot cache, reject
            self._add_to_hot_cache(fingerprint, stored_finding_id)
            self._persistent_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            return FindingQualityDecision(
                accepted=False,
                reason=reason,
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=True,
            )

        # URL-first path: short-circuit to store (no entropy check needed)
        # _accepted_count is incremented by async_record_canonical_findings_batch — NOT here
        if url_fingerprint:
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True,
                reason=None,
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Short strings (< 8 chars) skip entropy filter
        # _accepted_count is incremented by async_record_canonical_findings_batch — NOT here
        if len(fingerprint) < _QUALITY_MIN_ENTROPY_LEN:
            # Accept: store in LMDB + hot cache
            self._store_persistent_dedup(fingerprint, finding.finding_id)
            self._add_to_hot_cache(fingerprint, finding.finding_id)
            return FindingQualityDecision(
                accepted=True,
                reason="short_string_skip",
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Entropy threshold check
        if entropy < _QUALITY_ENTROPY_THRESHOLD:
            self._quality_rejected_count += 1
            return FindingQualityDecision(
                accepted=False,
                reason="low_entropy_rejected",
                entropy=entropy,
                normalized_hash=fingerprint,
                duplicate=False,
            )

        # Accept: store in LMDB + hot cache
        # NOTE: _accepted_count is NOT incremented here — it is incremented
        # by async_record_canonical_findings_batch when the WAL write succeeds.
        # This avoids double-counting between quality gate and storage layer.
        self._store_persistent_dedup(fingerprint, finding.finding_id)
        self._add_to_hot_cache(fingerprint, finding.finding_id)
        return FindingQualityDecision(
            accepted=True,
            reason=None,
            entropy=entropy,
            normalized_hash=fingerprint,
            duplicate=False,
        )

    async def async_ingest_finding(
        self,
        finding: CanonicalFinding,
    ) -> FindingQualityDecision | ActivationResult:
        """
        Sprint 8W: Quality-gated single-finding ingest.

        Layer ABOVE async_record_canonical_finding — applies quality gate first,
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
            # _accepted_count is NOT incremented here — async_record_canonical_finding
            # handles it on success, and we don't double-count on the fail-open path
            self._quality_fail_open_count += 1
            result = await self.async_record_canonical_finding(finding)
            return result

        if not decision.accepted:
            return decision

        # Phase 2: legacy storage path (WAL-first)
        result = await self.async_record_canonical_finding(finding)
        # Increment _accepted_count if LMDB write succeeded
        if result.lmdb_success:
            self._accepted_count += 1
        return result

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Sprint 8W: Quality-gated batch ingest.

        Layer ABOVE async_record_canonical_findings_batch — applies quality gate to each
        finding, then delegates acceptable ones to legacy batch storage.

        Quality gate is CPU-only, deterministic, and cheap.
        Fail-open: if quality helpers raise for any finding, that finding is stored
        via legacy path.

        Returns list with len(results) == len(findings) — 1:1 invariant.
        Each entry is FindingQualityDecision (rejected/duplicate) or ActivationResult (accepted).
        """
        if not findings:
            return []

        n = len(findings)
        results: list[FindingQualityDecision | ActivationResult | None] = [None] * n
        accepted_findings: list[CanonicalFinding] = []
        accepted_indices: list[int] = []

        for i, f in enumerate(findings):
            try:
                decision = self._assess_finding_quality(f)
            except Exception:
                self._quality_fail_open_count += 1
                result = await self.async_record_canonical_finding(f)
                # Count if LMDB succeeded — this is the fail-open success path
                if getattr(result, 'lmdb_success', False) if hasattr(result, 'lmdb_success') else result.get('lmdb_success', False) if isinstance(result, dict) else False:
                    self._accepted_count += 1
                results[i] = result
                continue

            if not decision.accepted:
                results[i] = decision
            else:
                accepted_findings.append(f)
                accepted_indices.append(i)

        if accepted_findings:
            storage_results = await self.async_record_canonical_findings_batch(accepted_findings)
            # Count accepted (lmdb_success) findings from the batch storage results
            accepted_from_batch = sum(
                1 for sr in storage_results
                if (sr.get("lmdb_success") if isinstance(sr, dict) else getattr(sr, "lmdb_success", False))
            )
            self._accepted_count += accepted_from_batch
            for idx, sr in zip(accepted_indices, storage_results):
                results[idx] = sr

        assert None not in results, "Internal error: 1:1 invariant violated"
        return results  # type: ignore[annotation-unchecked]

    def _sync_insert_findings_bulk_as_tuples(
        self,
        rows: list[list],
    ) -> int:
        """
        Sprint 8R: Bulk insert using list[tuple] with 6 columns (id, query, source_type, confidence, ts, provenance_json).
        MUST be called on the worker thread.
        Returns number of successfully inserted records.
        """
        if not rows:
            return 0

        try:
            # Guard: both file and persistent connections must be available
            if self._db_path and self._file_conn is not None:
                self._prewarm_file_conn()
                self._file_conn.execute("BEGIN TRANSACTION")
                try:
                    self._file_conn.executemany(
                        """
                        INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    self._file_conn.execute("COMMIT")
                    return len(rows)
                except Exception:
                    self._file_conn.execute("ROLLBACK")
                    return 0
            elif self._persistent_conn is not None:
                self._persistent_conn.execute("BEGIN TRANSACTION")
                try:
                    self._persistent_conn.executemany(
                        """
                        INSERT INTO shadow_findings (id, query, source_type, confidence, ts, provenance_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    self._persistent_conn.execute("COMMIT")
                    return len(rows)
                except Exception:
                    self._persistent_conn.execute("ROLLBACK")
                    return 0
        except Exception:
            return 0


    # ------------------------------------------------------------------
    # Async shutdown (new in 8AS)
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """
        Async idempotent shutdown — canonical async cleanup path.

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
          10. Executor is NOT shut down — kept alive for re-init SAFETY

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
        # Use clear() instead of replacing the Event object — avoids loop-affinity issues
        try:
            self._startup_ready.clear()
            self._startup_replay_done = False
        except Exception:
            pass

        # Sprint 8H: close connections synchronously by calling the worker method
        # directly. This is safe because DuckDB connections are owned by the
        # worker thread and we are calling from the main thread — the single
        # ThreadPoolExecutor(1 worker) ensures no concurrent access during
        # the synchronous close call. We use submit() to wake the blocked worker.
        try:
            f = self._executor.submit(self._sync_close_on_worker)
            f.result(timeout=5)  # wait for close to complete
        except Exception:
            pass

        # Sprint 8QA: cancel pending background tasks
        if self._bg_tasks:
            for t in self._bg_tasks:
                t.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()

        # Sprint 8WA: close truth-write graph (IOCGraph with buffer_ioc/flush_buffers)
        # GUARD: flush_buffers is IOCGraph-only. DuckPGQGraph has no flush_buffers.
        if self._truth_write_graph is not None:
            try:
                if callable(getattr(self._truth_write_graph, "flush_buffers", None)):
                    await self._truth_write_graph.flush_buffers()
            except Exception:
                pass
            try:
                if callable(getattr(self._truth_write_graph, "close", None)):
                    await self._truth_write_graph.close()
            except Exception:
                pass
            self._truth_write_graph = None

        # Sprint 8QA/8TF: close analytics/donor IOC graph (DuckPGQGraph)
        # DuckPGQGraph has checkpoint() and close() but no flush_buffers.
        if self._ioc_graph is not None:
            try:
                if callable(getattr(self._ioc_graph, "close", None)):
                    await self._ioc_graph.close()
            except Exception:
                pass
            self._ioc_graph = None

        # Sprint 8SB: close semantic store
        if self._semantic_store is not None:
            try:
                await self._semantic_store.close()
            except Exception:
                pass
            self._semantic_store = None

        # Sprint 8VQ: close STIX-only graph slot
        if self._stix_graph is not None:
            try:
                if callable(getattr(self._stix_graph, "close", None)):
                    await self._stix_graph.close()
            except Exception:
                pass
            self._stix_graph = None

        # Sprint 8L: Do NOT shutdown the executor — keep it alive for re-initialization.
        # A new ThreadPoolExecutor cannot reuse the same thread, so we keep the
        # existing one to allow async_initialize() to run again on the same store
        # instance after aclose().
        # Sprint 8L: close WAL LMDB to release lock files
        if hasattr(self, "_wal_lmdb") and self._wal_lmdb is not None:
            try:
                self._wal_lmdb.close()
            except Exception:
                pass
            self._wal_lmdb = None

        # Sprint 8AG: close dedup LMDB — mirrors _do_close() symmetry
        if hasattr(self, "_dedup_lmdb") and self._dedup_lmdb is not None:
            try:
                self._dedup_lmdb.close()
            except Exception:
                pass
            self._dedup_lmdb = None

    # ------------------------------------------------------------------
    # Sprint 8TC: RRF Fusion — Reciprocal Rank Fusion přes 4 signály
    # ------------------------------------------------------------------

    async def rrf_rank_findings(self, sprint_id: str, k: int = 30) -> list[dict]:
        """
        Sprint 8TC B.1: Reciprocal Rank Fusion přes 4 signály.

        Signály:
          1. semantic_score  — z LanceDB ANN (pokud dostupný)
          2. pattern_count   — počet pattern matche
          3. ioc_degree      — počet navázaných IOC uzlů
          4. recency_score   — inverzní age (novější = vyšší)

        SQL RRF: SUM(1.0 / (k + rank_i)) přes všechny signály.
        Chybějící sloupce se přidávají dynamicky přes ALTER TABLE.

        Args:
            sprint_id: Sprint identifier
            k: RRF constant (default 30 — snižuje vliv nízkých ranků)

        Returns:
            List[dict] s keys: finding_id, content, rrf_score, semantic_score,
            pattern_count, ioc_degree, ts
        """
        if not self._initialized or self._closed:
            return []

        # Dynamicky přidat chybějící sloupce do canonical_findings tabulky
        # (pokud existuje, jinak používáme shadow_findings)
        loop = asyncio.get_running_loop()

        def _sync_rrf_rank() -> list[dict]:
            try:
                conn = self._file_conn if self._db_path else self._persistent_conn
                if conn is None:
                    return []

                # Pokusíme se přidat sloupce — fail safe
                for col_sql in [
                    "ALTER TABLE canonical_findings ADD COLUMN IF NOT EXISTS semantic_score REAL DEFAULT 0.0",
                    "ALTER TABLE canonical_findings ADD COLUMN IF NOT EXISTS pattern_count INT DEFAULT 0",
                    "ALTER TABLE canonical_findings ADD COLUMN IF NOT EXISTS ioc_degree INT DEFAULT 0",
                ]:
                    try:
                        conn.execute(col_sql)
                    except Exception:
                        pass  # Sloupec již existuje nebo tabulka neexistuje

                # RRF SQL — 4 signály, DuckDB window functions
                # s1,s2,s3,s4 jsou ROW_NUMBER() window funkce pro každý signál
                rrf_sql = f"""
                WITH
                  s1 AS (
                      SELECT finding_id,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(semantic_score, 0) DESC) AS r
                        FROM canonical_findings
                       WHERE sprint_id = ?1
                  ),
                  s2 AS (
                      SELECT finding_id,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(pattern_count, 0) DESC) AS r
                        FROM canonical_findings
                       WHERE sprint_id = ?1
                  ),
                  s3 AS (
                      SELECT finding_id,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(ioc_degree, 0) DESC) AS r
                        FROM canonical_findings
                       WHERE sprint_id = ?1
                  ),
                  s4 AS (
                      SELECT finding_id,
                             ROW_NUMBER() OVER (ORDER BY COALESCE(ts, 0) DESC) AS r
                        FROM canonical_findings
                       WHERE sprint_id = ?1
                  ),
                  rrf AS (
                      SELECT finding_id, r FROM s1
                      UNION ALL
                      SELECT finding_id, r FROM s2
                      UNION ALL
                      SELECT finding_id, r FROM s3
                      UNION ALL
                      SELECT finding_id, r FROM s4
                  )
                SELECT f.finding_id,
                       f.content,
                       f.ts,
                       f.semantic_score,
                       f.pattern_count,
                       f.ioc_degree,
                       SUM(1.0 / (?2 + rrf.r)) AS rrf_score
                  FROM rrf
                  JOIN canonical_findings f USING (finding_id)
                 WHERE f.sprint_id = ?1
                 GROUP BY f.finding_id, f.content, f.ts,
                          f.semantic_score, f.pattern_count, f.ioc_degree
                 ORDER BY rrf_score DESC
                 LIMIT ?2
                """

                rows = conn.execute(rrf_sql, [sprint_id, k]).fetchall()
                return [
                    {
                        "finding_id": str(r[0]),
                        "content": r[1] or "",
                        "ts": r[2] or 0.0,
                        "semantic_score": r[3] or 0.0,
                        "pattern_count": r[4] or 0,
                        "ioc_degree": r[5] or 0,
                        "rrf_score": r[6] or 0.0,
                    }
                    for r in rows
                ]
            except Exception:
                return []

        try:
            return await loop.run_in_executor(self._executor, _sync_rrf_rank)
        except Exception:
            return []

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

        # Eager scan — not lazy, not over closed txn
        all_markers = self._wal_scan_pending_sync_markers()
        if not all_markers:
            return

        # Deduplicate
        seen_ids: set = set()
        unique_markers: List[Dict[str, Any]] = []
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
                    await asyncio.wait_for(
                        self.async_replay_single_pending_marker(fid),
                        timeout=max(deadline - _time.monotonic(), 0.1),
                    )
                except asyncio.TimeoutError:
                    # Timeout on single marker — stop replay, leave remaining pending
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
          1. Marker exists? → marker_found
          2. WAL finding:{id} truth exists? → wal_truth_found
          3. If truth missing → failure (can't recover)
          4. DuckDB write via same safe path as activation
          5. Fresh read-back from new connection confirms durability
          6. Success → clear pending marker
          7. Failure → bump retry count; if >= MAX_RETRY_COUNT → dead-letter

        Idempotency: if DuckDB already has the record, consider it a success.

        Args:
            finding_id: The finding identifier to replay.

        Returns:
            ReplayResult with all fields populated.
        """
        # Lazy init of replay lock
        lock = self._ensure_replay_lock()

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
            # No marker → check if DuckDB already has it (idempotent success)
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
        limit: Optional[int] = None,
    ) -> List[ReplayResult]:
        """
        Sprint 8H: Replay all pending markers with chunking and event-loop yields.

        Uses per-instance replay lock to prevent concurrent replay of same markers.
        Processes markers in chunks of REPLAY_CHUNK_SIZE, yielding to event loop
        between chunks to avoid starving live operations.

        Idempotency: markers that already exist in DuckDB are treated as success.

        Args:
            limit: Optional maximum number of markers to replay. None = all.

        Returns:
            List[ReplayResult], one per processed marker.
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
        unique_markers: List[Dict[str, Any]] = []
        for m in all_markers:
            fid = m.get("id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_markers.append(m)
        del seen_ids

        # Apply limit
        if limit is not None:
            unique_markers = unique_markers[:limit]

        results: List[ReplayResult] = []
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
    def db_path(self) -> Optional[Path]:
        """Return the database path (None for :memory: mode)."""
        return self._db_path

    @property
    def temp_dir(self) -> Optional[Path]:
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

        Cheap O(n) prefix scan — bounded by REPLAY_CHUNK_SIZE scan.
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
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
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
    def invariant_temp_dir(self) -> Optional[Path]:
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
            # No temp_dir means :memory: mode — this is OK
            results["temp_dir_on_ramdisk"] = True

        return results

    # ------------------------------------------------------------------
    # Internal helper — shared close logic
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sprint 8A: Activation helper — structured result → LMDB WAL → DuckDB
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
        """
        try:
            import time as _time

            from hledac.universal.tools.lmdb_kv import LMDBKVStore

            # Get or create the shared LMDB WAL store
            if not hasattr(self, "_wal_lmdb"):
                _wal_root = self._db_path.parent if self._db_path else None
                if _wal_root is None:
                    return False
                self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
                # Initialize schema on first access
                try:
                    self._wal_lmdb.put(
                        "_schema_init",
                        {"_": "ok"},
                    )
                    self._wal_lmdb.delete("_schema_init")
                except Exception:
                    pass

            key = f"finding:{finding_id}"
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

    def _activation_record_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> dict:
        """
        Sprint 8A: Record a structured finding — LMDB WAL first, DuckDB second.

        Mapping:
          result.id or uuid4() -> id
          context.query or "" -> query
          source_type from schema/type name -> source_type
          result.confidence or 1.0 -> confidence
          time.time() -> ts

        Partial failure semantics:
          - LMDB OK + DuckDB FAIL → LMDB remains truth, log desync, return duckdb_success=False
          - LMDB FAIL + DuckDB SKIP → return lmdb_success=False, duckdb_success=None

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

    def _wal_write_pending_sync_marker(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Sprint 8F: Write a pending-sync recovery marker to LMDB.

        Marker key:  pending_duckdb_sync:{id}
        Value:       same structure as WAL finding (id, query, source_type, confidence, ts)

        This marker is written ONLY when LMDB succeeded but DuckDB failed.
        A future recovery sprint can find it via prefix scan and retry the DuckDB write.
        The marker is NOT automatically cleared — explicit recovery is required.
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

    def _wal_scan_pending_sync_markers(self) -> List[Dict[str, Any]]:
        """
        Sprint 8F: Efficient prefix scan for all pending_duckdb_sync markers.

        Returns list of marker values (dicts with id, query, source_type, confidence, ts).
        Uses LMDB cursor with prefix iteration — O(n) where n = number of pending markers,
        NOT O(N) full database scan.
        """
        try:
            import orjson

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
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = orjson.loads(vb)
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

    def _wal_get_pending_marker(self, finding_id: str) -> Optional[Dict[str, Any]]:
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
        marker: Dict[str, Any],
    ) -> bool:
        """
        Sprint 8H: Synchronous single-marker replay — MUST be called on the worker thread.

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
                    result = conn.execute(
                        "SELECT 1 FROM shadow_findings WHERE id = ? LIMIT 1",
                        [finding_id],
                    ).fetchall()
                    return len(result) > 0
                finally:
                    conn.close()
            else:
                # :memory: mode — use persistent connection
                result = self._persistent_conn.execute(
                    "SELECT 1 FROM shadow_findings WHERE id = ? LIMIT 1",
                    [finding_id],
                ).fetchall()
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
        findings: List[Dict[str, Any]],
    ) -> dict:
        """
        Sprint 8A: Batch activation — LMDB WAL first, DuckDB second.

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

        # Step 1: LMDB WAL first — use put_many
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
            # Note: we don't rollback LMDB — it remains truth

        return result

    # ------------------------------------------------------------------
    # Internal helper — shared close logic
    # ------------------------------------------------------------------

    def _do_close(self) -> None:
        """
        Synchronous close helper — idempotent.

        Note: _closed guard removed — close() and _do_close() are always called
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
            self._executor.shutdown(wait=False)
        except Exception:
            pass
        # Sprint 8AG: close dedup LMDB (WAL LMDB already closed via _sync_close_on_worker)
        if hasattr(self, "_dedup_lmdb") and self._dedup_lmdb is not None:
            try:
                self._dedup_lmdb.close()
            except Exception:
                pass
            self._dedup_lmdb = None

    # =============================================================================
    # Sprint 8AG §6.17: Persistent Dedup LMDB
    # =============================================================================

    DEDUP_NAMESPACE: str = "dedup:"

    def _dedup_key_from_fingerprint(self, fp: str) -> bytes:
        """Build dedup namespace key from BLAKE2b fingerprint."""
        return f"{self.DEDUP_NAMESPACE}{fp}".encode("utf-8")

    def _dedup_lmdb_key_to_fingerprint(self, key: bytes) -> str:
        """Extract fingerprint from dedup namespace key."""
        return key.decode("utf-8")[len(self.DEDUP_NAMESPACE):]

    def _init_persistent_dedup_lmdb(self) -> None:
        """
        Initialize persistent dedup LMDB at PERSISTENT LMDB_ROOT/dedup.lmdb.

        Fails softly: any exception is caught and stored in _dedup_lmdb_boot_error.
        """
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

    def _lookup_persistent_dedup(self, fp: str) -> Optional[str]:
        """
        Lookup a fingerprint in the persistent dedup LMDB.

        Args:
            fp: 32-char BLAKE2b fingerprint hex string

        Returns:
            finding_id string if found, None otherwise (miss or LMDB unavailable)
        """
        if self._dedup_lmdb is None:
            return None
        try:
            key = self._dedup_key_from_fingerprint(fp)
            with self._dedup_lmdb._env.begin(write=False, buffers=True) as txn:
                raw = txn.get(key)
                if raw is None:
                    return None
                # buffers=True returns memoryview; convert to bytes for decode
                return bytes(raw).decode("utf-8")
        except Exception:
            self._dedup_lmdb_last_error = f"lookup failed for fp={fp[:8]}"
            return None

    def _store_persistent_dedup(self, fp: str, finding_id: str) -> None:
        """
        Store a fingerprint → finding_id mapping in persistent dedup LMDB.

        Args:
            fp: 32-char BLAKE2b fingerprint hex string
            finding_id: canonical finding ID
        """
        if self._dedup_lmdb is None:
            return
        try:
            key = self._dedup_key_from_fingerprint(fp)
            value_bytes = finding_id.encode("utf-8")
            with self._dedup_lmdb._env.begin(write=True) as txn:
                txn.put(key, value_bytes)
        except Exception as e:
            self._dedup_lmdb_last_error = f"store failed for fp={fp[:8]}: {e}"

    def _add_to_hot_cache(self, fp: str, finding_id: str) -> None:
        """
        Add entry to bounded hot cache with FIFO eviction.

        Hard cap: _DEDUP_HOT_CACHE_MAX entries.
        """
        if fp in self._dedup_hot_cache:
            # Existing entry — bump access order to recent (MRU semantics)
            try:
                self._dedup_hot_cache_order.remove(fp)
            except ValueError:
                pass
            self._dedup_hot_cache_order.append(fp)
            return
        if len(self._dedup_hot_cache) >= _DEDUP_HOT_CACHE_MAX:
            oldest = self._dedup_hot_cache_order.pop(0)
            self._dedup_hot_cache.pop(oldest, None)
        self._dedup_hot_cache[fp] = finding_id
        self._dedup_hot_cache_order.append(fp)

    def _hot_cache_lookup(self, fp: str) -> Optional[str]:
        """Bounded hot cache lookup."""
        return self._dedup_hot_cache.get(fp)

    def get_dedup_runtime_status(self) -> dict:
        """
        Sprint 8AG §6.17 + 8AK + 8AV: Typed/cheap status surface for dedup subsystem.

        Sprint 8AK: Explicitly distinguishes in_memory vs persistent duplicate counts.

        Sprint 8AV: Extended with ingest outcome counters to answer:
          - Kolik findings bylo přijato vs odmítnuto?
          - Z jakého důvodu byly odmítnuty?

        Returns:
            dict with:
              - persistent_dedup_enabled: bool
              - last_boot_cleanup_error: str | None
              - last_dedup_error: str | None
              - dedup_lmdb_path: str
              - dedup_namespace: "dedup:"
              - hot_cache_size: int
              - hot_cache_capacity: int
              - in_memory_duplicate_count: int  (hot-cache hits, same process)
              - persistent_duplicate_count: int  (LMDB hits, cross-source)
              - accepted_count: int  (findings that passed quality gate and were stored)
              - low_information_rejected_count: int  (entropy below threshold)
              - in_memory_duplicate_rejected_count: int  (hot-cache duplicate)
              - persistent_duplicate_rejected_count: int  (LMDB cross-source duplicate)
              - other_rejected_count: int  (fail-open exceptions during quality gate)
        """
        return {
            "persistent_dedup_enabled": self._dedup_lmdb is not None,
            "last_boot_cleanup_error": self._dedup_lmdb_boot_error,
            "last_dedup_error": self._dedup_lmdb_last_error,
            "dedup_lmdb_path": str(self._dedup_lmdb_path) if self._dedup_lmdb_path else "",
            "dedup_namespace": self.DEDUP_NAMESPACE,
            "hot_cache_size": len(self._dedup_hot_cache),
            "hot_cache_capacity": _DEDUP_HOT_CACHE_MAX,
            "in_memory_duplicate_count": self._quality_duplicate_count,
            "persistent_duplicate_count": self._persistent_duplicate_count,
            "accepted_count": self._accepted_count,
            "low_information_rejected_count": self._quality_rejected_count,
            "in_memory_duplicate_rejected_count": self._quality_duplicate_count,
            "persistent_duplicate_rejected_count": self._persistent_duplicate_count,
            "other_rejected_count": self._quality_fail_open_count,
        }

    def reset_ingest_reason_counters(self) -> None:
        """
        Sprint 8AV: Reset all ingest outcome counters to zero.

        Side-effect free, test-safe, can be called any time.
        Resets:
          - _accepted_count
          - _quality_rejected_count
          - _quality_duplicate_count
          - _persistent_duplicate_count
          - _quality_fail_open_count
        """
        self._accepted_count = 0
        self._quality_rejected_count = 0
        self._quality_duplicate_count = 0
        self._persistent_duplicate_count = 0
        self._quality_fail_open_count = 0

    def classify_ingest_outcome(
        self,
        decision: FindingQualityDecision | ActivationResult,
    ) -> str:
        """
        Sprint 8AV: Classify the canonical reason string for an ingest outcome.

        Internal use — maps internal FindingQualityDecision or ActivationResult
        to a human-readable reason string.

        Returns one of:
          - "accepted"                          — finding passed quality gate
          - "low_information_rejected"         — entropy below threshold
          - "in_memory_duplicate_rejected"     — hot-cache duplicate
          - "persistent_duplicate_rejected"   — LMDB cross-source duplicate
          - "other_rejected"                   — fail-open or unknown
          - "error_rejected"                   — store/LMDB error
        """
        # FindingQualityDecision is a msgspec.Struct (has 'reason' field).
        # ActivationResult is a TypedDict (use item access: decision["key"]).
        if isinstance(decision, FindingQualityDecision):
            # FindingQualityDecision path — msgspec.Struct supports attribute access
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

        # ActivationResult path (TypedDict — use item access)
        if decision["accepted"]:
            return "accepted"
        error = decision.get("error")
        if error:
            return "error_rejected"
        return "other_rejected"


# =============================================================================
# Sprint 8AM C.3.a: Factory helper for owned store creation
# =============================================================================

def create_owned_store() -> "DuckDBShadowStore":
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
        from hledac.universal.paths import RAMDISK_ACTIVE, RAMDISK_ROOT, DB_ROOT

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

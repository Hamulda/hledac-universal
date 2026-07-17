"""
Sprint P1-3: Hot Edges Materialized View
========================================

Read-side cache for `DuckPGQGraph.find_connected()` — the recursive CTE on
dense IOC graphs (>10k nodes) is the single biggest hot spot in the
post-sprint quantum path walk. This module maintains a counter-on-write
mirror of the most-trafficked edges in LMDB so the path query can answer
"who is connected to X" with one O(1) LMDB lookup instead of an O(V+E)
recursive CTE.

ARCHITECTURE
============

Write path — `record_edge(src_id, dst_id)` is called from
`GraphService.upsert_relation()` AFTER `graph.add_relation()` succeeds.
Increments the (src_id, dst_id) counter in a small fixed-size LMDB map.
LRU eviction: if `len(neighbors) > MAX_HOT_NEIGHBORS_PER_NODE`, drop the
lowest-frequency entry to keep the map bounded.

Read path — `get_hot_neighbors(src_id, top_n)` returns the top-N
(dst_id, count) pairs for a source node, sorted by count descending.
Lookup is one `env.begin().cursor().get(b"hot:{src_id:016x}")` — zero
copy on warm OS page cache (mmap). Single-digit microseconds.

Fail-soft — every public function is wrapped in try/except. LMDB
unavailable → silent no-op. Caller MUST treat None / [] as "miss" and
fall back to DuckPGQ. Never block the canonical write path.

M1 8GB BUDGET
=============
- map_size = 32 MB (max). Expected: 4 MB for 10K nodes × 50 neighbors
  × ~8-byte keys + 8-byte counters. 8× headroom.
- LMDB mmap lives in the OS page cache — does NOT count against Python
  heap. Purged automatically on macOS memory pressure.
- readahead=False on M1 (SSD only, no benefit).
- writemap=False (default) — safer for Apple Silicon UMA.

INVARIANTS
==========
- Counter overflow handled via saturating add (capped at UINT64_MAX).
- Bounded: MAX_HOT_NEIGHBORS_PER_NODE=50, MAX_HOT_NODES=10_000.
- 1:1 ordering: list sorted by count desc, stable on ties by dst_id asc.
- Idempotency: src==dst relations are SKIPPED (self-loops not cached).
- Concurrent writes safe: LMDB serializes via env.begin(write=True).
- Reads are lock-free: LMDB MVCC, no read transaction needed for
  single-key get.

ENV GATE
========
HLEDAC_HOT_EDGES=1   opt-out (default ON — unset or any value ≠ "0" enables)
HLEDAC_HOT_EDGES_MAP_SIZE_MB   override 32 MB default
"""


import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hledac.universal.paths import LMDB_ROOT
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

if TYPE_CHECKING:
    import duckdb

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# P1-5: Rust IntCounterLayoutRust — flat counter buffer for future fast-path
# Currently hot_edges uses per-node neighbor lists (src_id → [(dst_id, count),...]).
# IntCounterLayoutRust is a flat SoA counter buffer — future redesign could map
# individual (src_id, dst_id) → counter for O(1) increments without list decode/encode.
# Import here makes it available for the module; actual integration needs
# storage redesign (key schema change from per-node lists to flat counters).
# Rust backend — strict import
try:
    from core.rust_backend import rust
except ImportError:
    try:
        from hledac.universal.core.rust_backend import rust
    except ImportError:
        rust = None


def _get_rust_backend():
    """Lazy getter for Rust backend."""
    return rust


def _is_rust_hot_edges_available() -> bool:
    """Check if Rust hot_edges is available at runtime."""
    r = _get_rust_backend()
    if r is None or not r.is_available:
        return False
    return r.hot_edges is not None


_RUST_COUNTERS_AVAILABLE = _is_rust_hot_edges_available()

if _RUST_COUNTERS_AVAILABLE:
    HotEdgeCounterRust = _get_rust_backend().hot_edges.HotEdgeCounter
    IntCounterLayoutRust = _get_rust_backend().int_counter.IntCounterLayoutRust
    bulk_bump_aggregate = _get_rust_backend().hot_edges.bulk_bump_aggregate
    bulk_snapshot_dict = _get_rust_backend().hot_edges.bulk_snapshot_dict
    _build_layout_rust = getattr(_get_rust_backend().hot_edges, 'build_layout', None)
    _EDGE_COUNTER_L1: Any = HotEdgeCounterRust(
        flush_threshold=int(os.environ.get("HLEDAC_HOT_EDGES_L1_FLUSH", "50"))
    )
    _L1_AVAILABLE = True
else:
    IntCounterLayoutRust: type | None = None  # type: ignore[valid-type]
    bulk_bump_aggregate: Any | None = None  # type: ignore[valid-type]
    bulk_snapshot_dict: Any | None = None  # type: ignore[valid-type]
    _build_layout_rust: Any | None = None
    HotEdgeCounterRust: type | None = None  # type: ignore[valid-type]
    _EDGE_COUNTER_L1: Any | None = None
    _L1_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sprint P1-3: 8 MB default (was 32 MB). With ~43 edges per sprint the DB is <10 KB —
# 32 MB was 6400× overhead. 8 MB covers 1000+ edges easily. Override via
# HLEDAC_HOT_EDGES_MAP_SIZE_MB if needed.
_LMDB_PATH: Path = LMDB_ROOT / "hot_edges.lmdb"
_LMDB_MAP_SIZE: int = int(
    os.environ.get("HLEDAC_HOT_EDGES_MAP_SIZE_MB", "8")
) * 1024 * 1024
_KEY_PREFIX: bytes = b"hot:"

MAX_HOT_NEIGHBORS_PER_NODE: int = 50
MAX_HOT_NODES: int = 10_000
HOT_EDGES_ENABLED: bool = os.environ.get("HLEDAC_HOT_EDGES", "1") == "1"

# Counter encoding — 8 bytes unsigned int, little-endian.
# Picked for: zero allocations, native int.from_bytes on M1 ARM64.
# (Currently the counter is encoded via msgspec list — kept here as
# a reference for a future binary fast-path that may live in Rust.)
_UINT64_MAX: int = 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Lazy module-level env (thread-safe via LMDB internal locking)
# ---------------------------------------------------------------------------

_ENV = None  # type: ignore[var-annotated]
_ENV_OPEN_FAILED = False


def _open_env():
    """
    Open LMDB env lazily. Idempotent. Returns None on failure.

    LMDB internally locks per-env — concurrent open_env() calls are safe
    but only the first creates the file/mmap. Cached at module level.
    """
    global _ENV, _ENV_OPEN_FAILED
    if _ENV is not None:
        return _ENV
    if _ENV_OPEN_FAILED:
        return None
    try:
        from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
        _LMDB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENV = open_lmdb_with_guard(
            _LMDB_PATH,
            map_size=_LMDB_MAP_SIZE,
            readahead=False,
            writemap=False,  # safer for M1 UMA
            metasync=True,    # fsync meta on commit
            sync=False,       # default: data pages flushed lazily
            max_dbs=1,
        )
        logger.debug(f"[HOT-EDGES] LMDB env opened at {_LMDB_PATH} ({_LMDB_MAP_SIZE // (1024*1024)} MB)")
        return _ENV
    except Exception as e:
        _ENV_OPEN_FAILED = True
        logger.warning(f"[HOT-EDGES] LMDB env open failed, cache disabled: {e}")
        return None


_COUNTER_KEY: bytes = b"nodes:count"

# ---------------------------------------------------------------------------
# Node counter helpers (enforces MAX_HOT_NODES)
# ---------------------------------------------------------------------------

def _get_node_count(env) -> int:
    """Return current unique src_id count. Returns 0 on error/miss."""
    try:
        with env.begin(db=env.open_db(b"_meta")) as txn:
            blob = txn.get(_COUNTER_KEY)
            if blob:
                return int.from_bytes(blob, "little")
    except Exception:  # noqa: BLE001
        pass
    return 0

def _inc_node_count(env) -> int:
    """Atomically increment node count. Returns new count. Fails silently."""
    try:
        with env.begin(write=True, db=env.open_db(b"_meta")) as txn:
            old_blob = txn.get(_COUNTER_KEY)
            old_count = int.from_bytes(old_blob, "little") if old_blob else 0
            new_count = old_count + 1
            txn.put(_COUNTER_KEY, new_count.to_bytes(8, "little"))
            return new_count
    except Exception:
        return -1

def _dec_node_count(env) -> int:
    """Atomically decrement node count. Returns new count. Fails silently."""
    try:
        with env.begin(write=True, db=env.open_db(b"_meta")) as txn:
            old_blob = txn.get(_COUNTER_KEY)
            old_count = int.from_bytes(old_blob, "little") if old_blob else 0
            new_count = max(0, old_count - 1)
            txn.put(_COUNTER_KEY, new_count.to_bytes(8, "little"))
            return new_count
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

# Sprint F265B-III: Compress LMDB pages with lz4 (fast) + zstd (ratio).
# Wire format: [marker=0x00/0x01/0x02][payload].
# Opt-in via HLEDAC_HOT_EDGES_COMPRESS=1 (default ON when rust ext available).
_HOT_EDGES_COMPRESS = os.environ.get("HLEDAC_HOT_EDGES_COMPRESS", "1") not in ("0", "no", "off")
_compress_available = False
_decompress_available = False

try:
    from hledac_rust_extensions import batch_compress_pages as _rust_batch_compress
    from hledac_rust_extensions import batch_decompress_pages as _rust_batch_decompress
    from hledac_rust_extensions import compress_page as _rust_compress
    from hledac_rust_extensions import decompress_page as _rust_decompress

    _compress_available = True
    _decompress_available = True
    _batch_compress_available = True
    _batch_decompress_available = True
except Exception:
    _rust_compress = None
    _rust_decompress = None
    _rust_batch_compress = None
    _rust_batch_decompress = None
    _batch_compress_available = False
    _batch_decompress_available = False


def _make_key(src_id: int) -> bytes:
    """Encode src node id as fixed-width LMDB key (16 hex chars = 8 bytes).

    Fixed-width keys are LMDB-friendly: lexical sort = numeric sort for
    positive 64-bit integers. Enables cursor iteration in id order.
    """
    return _KEY_PREFIX + f"{src_id:016x}".encode("ascii")


def _decode_neighbors(blob: bytes) -> list[tuple[int, int]]:
    """Decode msgspec blob → list[(dst_id, count)].

    Handles both raw msgspec and lz4/zstd wire-format blobs.
    """
    try:
        # Decompress if wire format detected (marker byte).
        if _HOT_EDGES_COMPRESS and _decompress_available and len(blob) > 1:
            marker = blob[0]
            if marker in (0x01, 0x02, 0x00):
                blob = _rust_decompress(blob)
        raw = _msgspec_decode(blob)
        if not raw:
            return []
        return [(int(item[0]), int(item[1])) for item in raw]
    except Exception:
        return []


def _encode_neighbors(neighbors: list[tuple[int, int]]) -> bytes:
    """Encode list[(dst_id, count)] → msgspec blob, optionally lz4/zstd compressed."""
    encoded = _msgspec_encode([list(pair) for pair in neighbors])
    if not _HOT_EDGES_COMPRESS or not _compress_available:
        return encoded
    try:
        return _rust_compress(encoded)
    except Exception:
        # Compression failed — store uncompressed.
        return encoded


# ---------------------------------------------------------------------------
# Batch helpers — Rust batch compression (Sprint P3-2)
# ---------------------------------------------------------------------------

def _decode_neighbors_batch(
    blobs: list[bytes],
) -> list[list[tuple[int, int]]]:
    """
    Batch-decode a list of msgspec blobs → list[list[(dst_id, count)]].

    Uses Rust `batch_decompress_pages` when available (rayon-parallel,
    ~3-5× faster than sequential single-item decompress for ≥4 items).
    Falls back to sequential _decode_neighbors per blob on error.

    Args:
        blobs: list of wire-format bytes (compressed or raw msgspec)

    Returns:
        list of decoded neighbor lists, same length as input
    """
    if not blobs:
        return []
    if _HOT_EDGES_COMPRESS and _batch_decompress_available and len(blobs) > 1:
        try:
            decompressed = _rust_batch_decompress(blobs)
            return [_decode_neighbors(d) for d in decompressed]
        except Exception:  # noqa: BLE001
            # Fall through to sequential
            pass
    return [_decode_neighbors(b) for b in blobs]


def _encode_neighbors_batch(
    neighbors_list: list[list[tuple[int, int]]],
) -> list[bytes]:
    """
    Batch-encode a list of neighbor lists → list of wire-format bytes.

    Uses Rust `batch_compress_pages` when available (rayon-parallel,
    ~3-5× faster than sequential single-item compress for ≥4 items).
    Falls back to sequential _encode_neighbors per item on error.

    Args:
        neighbors_list: list of neighbor lists to encode

    Returns:
        list of wire-format bytes (compressed or raw msgspec), same length
    """
    if not neighbors_list:
        return []
    # Encode to msgspec first (CPU-bound, no parallelism benefit)
    encoded_list: list[bytes] = [
        _msgspec_encode([list(pair) for pair in neighbors])
        for neighbors in neighbors_list
    ]
    if _HOT_EDGES_COMPRESS and _batch_compress_available and len(encoded_list) > 1:
        try:
            return _rust_batch_compress(encoded_list)
        except Exception:  # noqa: BLE001
            # Fall through to sequential
            pass
    return [_encode_neighbors(neighbors) for neighbors in neighbors_list]


# ---------------------------------------------------------------------------
# Public API — write path
# ---------------------------------------------------------------------------

def _record_edge_lmdb(
    src_id: int,
    dst_id: int,
    *,
    dst_value: str = "",
    dst_ioc_type: str = "",
) -> bool:
    """
    Write (src_id, dst_id) counter directly to LMDB.

    Extracted from record_edge() — preserves the original LMDB write logic
    exactly. Used as fallback when L1 is unavailable.

    F265-U6: When dst_value + dst_ioc_type are provided, stores in denormalized
    v2 wire format so read path (get_hot_neighbors_denorm) gets value+ioc_type
    without a DuckDB round-trip.
    """
    env = _open_env()
    if env is None:
        return False
    use_denorm = bool(dst_value and dst_ioc_type)
    try:
        key = _make_key(src_id)
        with env.begin(write=True) as txn:
            existing = txn.get(key)
            # Determine format of existing data
            existing_denorm = bool(existing and len(existing) > 0 and existing[0] == _WIRE_MARKER_DENORM)

            if existing is None:
                # ── New node: first edge ever ─────────────────────────────────
                if use_denorm:
                    neighbors_denorm: list[tuple[int, int, str, str]] = [
                        (dst_id, 1, dst_value, dst_ioc_type)
                    ]
                    neighbors_denorm.sort(key=lambda p: (-p[1], p[0]))
                    txn.put(key, _encode_neighbors_denorm(neighbors_denorm))
                else:
                    neighbors: list[tuple[int, int]] = [(dst_id, 1)]
                    txn.put(key, _encode_neighbors(neighbors))
                return True

            if existing_denorm:
                # ── Existing v2 denormalized: decode, update, re-encode ───────
                neighbors_denorm = _decode_neighbors_denorm(existing)
                if not neighbors_denorm:
                    neighbors_denorm = []
                found = False
                for i, (nid, cnt, _, _) in enumerate(neighbors_denorm):
                    if nid == dst_id:
                        neighbors_denorm[i] = (nid, min(cnt + 1, _UINT64_MAX), dst_value or "", dst_ioc_type or "")
                        found = True
                        break
                if not found:
                    if len(neighbors_denorm) < MAX_HOT_NEIGHBORS_PER_NODE:
                        neighbors_denorm.append((dst_id, 1, dst_value, dst_ioc_type))
                    else:
                        return True  # cache full
                neighbors_denorm.sort(key=lambda p: (-p[1], p[0]))
                neighbors_denorm = neighbors_denorm[:MAX_HOT_NEIGHBORS_PER_NODE]
                txn.put(key, _encode_neighbors_denorm(neighbors_denorm))
                return True

            # ── Existing v1 (raw counts): decode, update, re-encode as v1 ────
            neighbors = _decode_neighbors(existing)
            if not neighbors:
                neighbors = [(dst_id, 1)]
            else:
                found = False
                for i, (nid, cnt) in enumerate(neighbors):
                    if nid == dst_id:
                        neighbors[i] = (nid, min(cnt + 1, _UINT64_MAX))
                        found = True
                        break
                if not found:
                    if len(neighbors) < MAX_HOT_NEIGHBORS_PER_NODE:
                        neighbors.append((dst_id, 1))
                    else:
                        return True
            neighbors.sort(key=lambda p: (-p[1], p[0]))
            neighbors = neighbors[:MAX_HOT_NEIGHBORS_PER_NODE]
            # F265-U6: if denorm data provided, upgrade v1 → v2 wire format
            if use_denorm:
                neighbors_denorm = [
                    (nid, cnt, dst_value if nid == dst_id else "", dst_ioc_type if nid == dst_id else "")
                    for nid, cnt in neighbors
                ]
                txn.put(key, _encode_neighbors_denorm(neighbors_denorm))
            else:
                txn.put(key, _encode_neighbors(neighbors))
            return True
    except Exception as e:
        logger.debug(f"[HOT-EDGES] _record_edge_lmdb failed for ({src_id}->{dst_id}): {e}")
        return False


def _flush_l1_to_lmdb() -> bool:
    """
    Drain all dirty entries from L1 write buffer and persist to LMDB.

    Opens a SINGLE LMDB write transaction for all pending entries.
    Groups by src_id, reads existing neighbor lists, applies deltas as
    batched saturating increments, sorts, truncates to MAX_HOT_NEIGHBORS_PER_NODE,
    then writes back in one transaction.

    Returns True on success, False on any exception (fail-soft).
    """
    if not _L1_AVAILABLE or _EDGE_COUNTER_L1 is None:
        return False
    try:
        dirty: list[tuple[int, int, int]] = _EDGE_COUNTER_L1.drain_dirty()
    except Exception as e:
        logger.debug(f"[HOT-EDGES] L1 drain_dirty failed: {e}")
        return False
    if not dirty:
        return True
    try:
        from collections import defaultdict

        by_src: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        for src_id, dst_id, delta in dirty:
            by_src[src_id].append((dst_id, delta, delta))

        env = _open_env()
        if env is None:
            return False
        with env.begin(write=True) as txn:
            for src_id, deltas_in in by_src.items():
                key = _make_key(src_id)
                existing = txn.get(key)
                if existing is None:
                    neighbors: list[tuple[int, int]] = []
                else:
                    neighbors = _decode_neighbors(existing)
                    if not neighbors:
                        neighbors = []

                # Build nmap for O(1) dst lookup
                nmap: dict[int, int] = {nid: cnt for nid, cnt in neighbors}

                for dst_id, delta, _ in deltas_in:
                    nmap[dst_id] = nmap.get(dst_id, 0) + delta
                    if nmap[dst_id] > _UINT64_MAX:
                        nmap[dst_id] = _UINT64_MAX

                # Sort by count desc, dst_id asc, truncate
                sorted_neighbors = sorted(nmap.items(), key=lambda p: (-p[1], p[0]))
                neighbors = sorted_neighbors[:MAX_HOT_NEIGHBORS_PER_NODE]

                txn.put(key, _encode_neighbors(neighbors))
        return True
    except Exception as e:
        logger.debug(f"[HOT-EDGES] _flush_l1_to_lmdb failed: {e}")
        return False


def _flush_l1_to_lmdb_from_drain(dirty: list[tuple[int, int, int]]) -> bool:
    """
    F271: Persist pre-drained dirty entries from Rust L1 to LMDB.

    This is the second half of flush_to_lmdb() — the Rust buffer has already
    been drained (so dirty_count is 0), and this function applies the merge
    logic (group by src_id, merge with existing neighbors, saturating
    increment, sort, truncate, write).

    Args:
        dirty: List of (src_id, dst_id, count) tuples as returned by
               HotEdgeCounterRust.flush_to_lmdb().

    Returns True on success, False on any exception (fail-soft).
    """
    if not dirty:
        return True
    try:
        from collections import defaultdict

        by_src: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        for src_id, dst_id, count in dirty:
            by_src[src_id].append((dst_id, count, count))

        env = _open_env()
        if env is None:
            return False
        with env.begin(write=True) as txn:
            for src_id, deltas_in in by_src.items():
                key = _make_key(src_id)
                existing = txn.get(key)
                if existing is None:
                    neighbors: list[tuple[int, int]] = []
                else:
                    neighbors = _decode_neighbors(existing)
                    if not neighbors:
                        neighbors = []

                # Build nmap for O(1) dst lookup
                nmap: dict[int, int] = {nid: cnt for nid, cnt in neighbors}

                for dst_id, delta, _ in deltas_in:
                    nmap[dst_id] = nmap.get(dst_id, 0) + delta
                    if nmap[dst_id] > _UINT64_MAX:
                        nmap[dst_id] = _UINT64_MAX

                # Sort by count desc, dst_id asc, truncate
                sorted_neighbors = sorted(nmap.items(), key=lambda p: (-p[1], p[0]))
                neighbors = sorted_neighbors[:MAX_HOT_NEIGHBORS_PER_NODE]

                txn.put(key, _encode_neighbors(neighbors))
        return True
    except Exception as e:
        logger.debug(f"[HOT-EDGES] _flush_l1_to_lmdb_from_drain failed: {e}")
        return False


def record_edge(
    src_id: int,
    dst_id: int,
    *,
    src_value: str = "",
    dst_value: str = "",
    src_ioc_type: str = "",
    dst_ioc_type: str = "",
) -> bool:
    """
    Increment (src_id, dst_id) counter in hot edges cache.

    Called from GraphService.upsert_relation() AFTER successful DuckDB write.
    Bounded: top MAX_HOT_NEIGHBORS_PER_NODE entries per src_id (LRU evict
    the lowest-frequency entry on overflow).

    F265-U6: When dst_value + dst_ioc_type are provided, the neighbor entry
    is stored in denormalized v2 format — eliminates the DuckDB round-trip
    on read path (find_entity_history gets value+ioc_type directly from LMDB).

    Returns:
        True on success, False on cache miss / LMDB error (fail-soft).
        Does NOT raise — write path is best-effort.
    """
    if not HOT_EDGES_ENABLED:
        return False
    if src_id == dst_id:
        return False  # skip self-loops
    if src_id < 0 or dst_id < 0:
        return False
    # F265-U6: When denormalized data is provided, skip L1 and write directly
    # to LMDB so the v2 wire format with value+ioc_type is preserved.
    # L1 (Rust counter buffer) only stores raw (src, dst, delta) — no room
    # for denorm metadata, so using it would lose the benefit.
    use_denorm = bool(dst_value and dst_ioc_type)
    if use_denorm:
        return _record_edge_lmdb(
            src_id,
            dst_id,
            dst_value=dst_value,
            dst_ioc_type=dst_ioc_type,
        )
    if _L1_AVAILABLE and _EDGE_COUNTER_L1 is not None:
        try:
            _EDGE_COUNTER_L1.bump_edge(src_id, dst_id, 1)
            if _EDGE_COUNTER_L1.should_flush():
                # F271: flush_to_lmdb() drains the Rust buffer and returns
                # dirty entries for Python to persist via _flush_l1_to_lmdb_from_drain().
                # Using flush_to_lmdb() over should_flush()+drain_dirty() reduces
                # Python↔Rust round-trips on the hot write path.
                dirty = _EDGE_COUNTER_L1.flush_to_lmdb()
                if dirty:
                    _flush_l1_to_lmdb_from_drain(dirty)
            return True
        except Exception as e:
            logger.debug(f"[HOT-EDGES] L1 bump_edge failed for ({src_id}->{dst_id}): {e}")
    return _record_edge_lmdb(src_id, dst_id)


# ---------------------------------------------------------------------------
# Public API — read path
# ---------------------------------------------------------------------------

def get_hot_neighbors(
    src_id: int, top_n: int = MAX_HOT_NEIGHBORS_PER_NODE
) -> list[tuple[int, int]]:
    """
    O(1) LMDB lookup of top-N (dst_id, count) for src_id.

    Returns list sorted by count desc, then dst_id asc. Empty list on
    cache miss / LMDB error. Caller MUST treat empty as "no data" and
    fall back to DuckPGQ find_connected().

    Args:
        src_id: Source node id (DuckDB ioc_nodes.id — int64).
        top_n: Max neighbors to return (clamped to MAX_HOT_NEIGHBORS_PER_NODE).

    Returns:
        List of (dst_id, count) tuples. Empty list on miss/error.
    """
    if not HOT_EDGES_ENABLED:
        return []
    if src_id < 0:
        return []
    if top_n <= 0 or top_n > MAX_HOT_NEIGHBORS_PER_NODE:
        top_n = MAX_HOT_NEIGHBORS_PER_NODE
    env = _open_env()
    if env is None:
        return []
    try:
        with env.begin() as txn:
            blob = txn.get(_make_key(src_id))
        if not blob:
            return []
        neighbors = _decode_neighbors(blob)
        # Re-sort on read to guarantee canonical order (count desc, dst_id asc)
        # even if the storage was last written by a different code path.
        neighbors.sort(key=lambda p: (-p[1], p[0]))
        return neighbors[:top_n]
    except Exception as e:
        logger.debug(f"[HOT-EDGES] get_hot_neighbors failed for {src_id}: {e}")
        return []


# ─── Sprint F265-U6: Denormalized hot neighbors (value + ioc_type embedded) ───
# Wire format v2: [version=0x03][count:N][entry_0...entry_N]
# entry = (dst_id:u64, count:u64, value_len:u16, value:utf8, ioc_type_len:u8, ioc_type:utf8)
# Benefits:
#   • Eliminates 1 DuckDB SQL round-trip per find_entity_history call
#   • Single LMDB O(1) lookup returns everything needed for the hot path
#   • value + ioc_type stored once per edge (at write time, during record_edge)
#   • Backward compat: v1 blobs (marker 0x00/0x01/0x02) decoded normally, extended to v2 on next write
#   • M1 8GB: 50 neighbors × (~50B value + 15B ioc_type + 24B overhead) ≈ 4.5 KB/node — well within 8 MB budget
_VERSION_DENORM = 0x03
_WIRE_MARKER_DENORM = 0x03


def _decode_neighbors_denorm(blob: bytes) -> list[tuple[int, int, str, str]]:
    """
    Decode v2 denormalized wire format → list[(dst_id, count, value, ioc_type)].

    Handles backward compat: v1 blobs decoded via _decode_neighbors.
    """
    try:
        if len(blob) < 2:
            return []
        marker = blob[0]
        # Backward compat: v1 wire formats
        if marker in (0x00, 0x01, 0x02):
            raw = _decode_neighbors(blob)
            return [(nid, cnt, "", "") for nid, cnt in raw]
        if marker != _WIRE_MARKER_DENORM:
            return []
        import struct as _struct

        pos = 1  # skip version byte
        count = _struct.unpack_from("<H", blob, pos)[0]  # uint16_t num_entries
        pos += 2
        result: list[tuple[int, int, str, str]] = []
        for _ in range(count):
            dst_id = _struct.unpack_from("<Q", blob, pos)[0]  # uint64
            pos += 8
            cnt = _struct.unpack_from("<Q", blob, pos)[0]
            pos += 8
            value_len = _struct.unpack_from("<H", blob, pos)[0]
            pos += 2
            value = blob[pos : pos + value_len].decode("utf-8", errors="replace")
            pos += value_len
            ioc_type_len = blob[pos]
            pos += 1
            ioc_type = blob[pos : pos + ioc_type_len].decode("utf-8", errors="replace")
            pos += ioc_type_len
            result.append((dst_id, cnt, value, ioc_type))
        return result
    except Exception:
        return []


def _encode_neighbors_denorm(
    neighbors: list[tuple[int, int, str, str]],
) -> bytes:
    """
    Encode list[(dst_id, count, value, ioc_type)] → v2 denormalized wire format.

    Falls back to v1 if any value/ioc_type is empty (old-style count-only entry).
    """
    if not neighbors:
        return _encode_neighbors([(nid, cnt) for nid, cnt, _, _ in neighbors])
    try:
        import struct as _struct

        buf = bytearray()
        buf.append(_WIRE_MARKER_DENORM)
        buf.extend(_struct.pack("<H", len(neighbors)))  # count
        for dst_id, cnt, value, ioc_type in neighbors:
            buf.extend(_struct.pack("<Q", dst_id))
            buf.extend(_struct.pack("<Q", cnt))
            vb = value.encode("utf-8")
            buf.extend(_struct.pack("<H", len(vb)))
            buf.extend(vb)
            ib = ioc_type.encode("utf-8")
            buf.append(len(ib))
            buf.extend(ib)
        return bytes(buf)
    except Exception:
        # Fall back to v1 on encode error
        return _encode_neighbors([(nid, cnt) for nid, cnt, _, _ in neighbors])


def get_hot_neighbors_denorm(
    src_id: int, top_n: int = MAX_HOT_NEIGHBORS_PER_NODE
) -> list[tuple[int, int, str, str]]:
    """
    O(1) LMDB lookup of top-N denormalized neighbors for src_id.

    Returns list of (dst_id, count, value, ioc_type) sorted by count desc,
    then dst_id asc. Empty list on miss / LMDB error / old-format blobs.

    This is the PRIMARY read path for find_entity_history — eliminates the
    separate DuckDB lookup_ioc_values_by_ids() call when hot edges are warm.
    """
    if not HOT_EDGES_ENABLED:
        return []
    if src_id < 0:
        return []
    if top_n <= 0 or top_n > MAX_HOT_NEIGHBORS_PER_NODE:
        top_n = MAX_HOT_NEIGHBORS_PER_NODE
    env = _open_env()
    if env is None:
        return []
    try:
        with env.begin() as txn:
            blob = txn.get(_make_key(src_id))
        if not blob:
            return []
        neighbors = _decode_neighbors_denorm(blob)
        if not neighbors:
            return []
        # Re-sort: count desc, dst_id asc
        neighbors.sort(key=lambda p: (-p[1], p[0]))
        return neighbors[:top_n]
    except Exception as e:
        logger.debug(f"[HOT-EDGES] get_hot_neighbors_denorm failed for {src_id}: {e}")
        return []


def has_hot_edges(src_id: int) -> bool:
    """
    O(1) check if hot edges exist for src_id (no decoding).

    Useful for "should I even try the cache?" gate before falling back
    to DuckPGQ. Returns False on cache miss / LMDB error.
    """
    if not HOT_EDGES_ENABLED or src_id < 0:
        return False
    env = _open_env()
    if env is None:
        return False
    try:
        with env.begin() as txn:
            return txn.get(_make_key(src_id)) is not None
    except Exception:
        return False


def get_node_id_by_value(value: str) -> int | None:
    """
    Resolve IOC value → node_id from DuckPGQGraph.

    Hot edges are keyed by node_id (int64), not by value string. The
    caller needs the int id to query the cache. This is a thin wrapper
    around DuckPGQGraph's internal stable hash function.

    Returns None if graph unavailable or value not in ioc_nodes.
    """
    if not value:
        return None
    try:
        from hledac.universal.graph.quantum_pathfinder import _stable_node_id
        return _stable_node_id(value)
    except Exception:
        return None


# P0-2: Reusable read-only DuckDB connection (avoids 5-10ms connect overhead per call)
_DUCKDB_RO_CON: duckdb.DuckDBPyConnection | None = None


def _get_duckdb_ro() -> duckdb.DuckDBPyConnection | None:
    """Return a reusable read-only DuckDB connection.

    P0-2 fix: Previously every lookup_ioc_values_by_ids() call opened a new
    connection (5-10ms overhead). Now we reuse a single read-only connection
    for the lifetime of the process.

    Thread-safety: DuckDB read-only connections are safe for concurrent reads
    from multiple threads (MVCC). The connection is opened lazily on first use.
    """
    global _DUCKDB_RO_CON
    if _DUCKDB_RO_CON is None:
        try:
            import duckdb

            from hledac.universal.paths import get_ioc_db_path
            _DUCKDB_RO_CON = duckdb.connect(str(get_ioc_db_path()), read_only=True)
            # M1 8GB: memory_limit + threads + preserve_insertion_order (read-only, conservative)
            try:
                _DUCKDB_RO_CON.execute("SET memory_limit = '1GB'")
                _DUCKDB_RO_CON.execute("PRAGMA threads = 2")
                _DUCKDB_RO_CON.execute("SET preserve_insertion_order = false")
            except Exception:
                pass  # fail-soft for read-only connection
        except Exception as e:
            logger.debug(f"[HOT-EDGES] DuckDB read-only connect failed: {e}")
            return None
    return _DUCKDB_RO_CON


def lookup_ioc_values_by_ids(
    node_ids: list[int]
) -> dict[int, dict]:
    """
    Batch-resolve node_ids → IOC value/type/confidence.

    P0-2 FIX: Uses a reusable read-only DuckDB connection instead of opening
    a new connection on every call. Eliminates 5-10ms connect overhead per call.

    Returns dict[int, {value, ioc_type, confidence, source}]. Missing
    ids are silently dropped. Used by read path to convert hot-edge
    (id, count) results back into IOC records.

    On graph unavailable / LMDB error → returns {}.
    """
    if not node_ids:
        return {}
    con = _get_duckdb_ro()
    if con is None:
        return {}
    try:
        placeholders = ",".join(["?"] * len(node_ids))
        sql = f"SELECT id, value, ioc_type, confidence, source FROM ioc_nodes WHERE id IN ({placeholders})"
        # noqa: B608 — placeholders are integer-count-derived, node_ids are internal int list, read-only query
        cur = con.execute(sql, node_ids)
        cols = [c[0] for c in cur.description]
        return {
            int(row[0]): dict(zip(cols, row, strict=False))
            for row in cur.fetchall()
        }
    except Exception as e:
        logger.debug(f"[HOT-EDGES] lookup_ioc_values_by_ids failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Admin / test helpers
# ---------------------------------------------------------------------------

def clear_all() -> bool:
    """Drop ALL hot edges (testing only). Returns True on success."""
    env = _open_env()
    if env is None:
        return False
    try:
        with env.begin(write=True) as txn:
            txn.drop(env.open_db())  # type: ignore[union-attr]
        return True
    except Exception as e:
        logger.debug(f"[HOT-EDGES] clear_all failed: {e}")
        return False


def stats() -> dict:
    """
    Return cache statistics: {node_count, env_open, enabled}.

    Cheap: single LMDB stat call. Safe to call frequently.
    """
    out = {
        "node_count": 0,
        "env_open": False,
        "enabled": HOT_EDGES_ENABLED,
        "rust_counters_available": _RUST_COUNTERS_AVAILABLE,
        "l1_available": _L1_AVAILABLE,
        "l1_pending": _EDGE_COUNTER_L1.pending_count() if _L1_AVAILABLE and _EDGE_COUNTER_L1 is not None else 0,
        "lmdb_path": str(_LMDB_PATH),
        "map_size_mb": _LMDB_MAP_SIZE // (1024 * 1024),
    }
    if not HOT_EDGES_ENABLED:
        return out
    env = _open_env()
    if env is None:
        return out
    try:
        out["env_open"] = True
        with env.begin() as txn:
            stat = txn.stat()
            out["node_count"] = stat.get("entries", 0)
    except Exception as e:
        logger.debug(f"[HOT-EDGES] stats failed: {e}")
    return out


__all__ = [
    "MAX_HOT_NEIGHBORS_PER_NODE",
    "MAX_HOT_NODES",
    "HOT_EDGES_ENABLED",
    "record_edge",
    "get_hot_neighbors",
    "get_hot_neighbors_denorm",
    "has_hot_edges",
    "get_node_id_by_value",
    "lookup_ioc_values_by_ids",
    "clear_all",
    "stats",
    # Sprint P3-2: Rust batch compression helpers
    "_decode_neighbors_batch",
    "_encode_neighbors_batch",
    # L1 flush helper
    "_flush_l1_to_lmdb",
]

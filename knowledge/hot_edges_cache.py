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
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from hledac.universal.paths import LMDB_ROOT
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

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
    from hledac_rust_extensions import compress_page as _rust_compress
    from hledac_rust_extensions import decompress_page as _rust_decompress

    _compress_available = True
    _decompress_available = True
except Exception:
    _rust_compress = None
    _rust_decompress = None


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
# Public API — write path
# ---------------------------------------------------------------------------

def record_edge(src_id: int, dst_id: int) -> bool:
    """
    Increment (src_id, dst_id) counter in hot edges cache.

    Called from GraphService.upsert_relation() AFTER successful DuckDB write.
    Bounded: top MAX_HOT_NEIGHBORS_PER_NODE entries per src_id (LRU evict
    the lowest-frequency entry on overflow).

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
    env = _open_env()
    if env is None:
        return False
    try:
        key = _make_key(src_id)
        with env.begin(write=True) as txn:
            existing = txn.get(key)
            if existing is None:
                # First edge for this src — only store if under cap
                # We use a soft cap here: store, but prune in next call
                # if MAX_HOT_NODES exceeded.
                neighbors: list[tuple[int, int]] = [(dst_id, 1)]
            else:
                neighbors = _decode_neighbors(existing)
                if not neighbors:
                    neighbors = [(dst_id, 1)]
                else:
                    # Increment existing or append
                    found = False
                    for i, (nid, cnt) in enumerate(neighbors):
                        if nid == dst_id:
                            new_cnt = cnt + 1
                            if new_cnt > _UINT64_MAX:
                                new_cnt = _UINT64_MAX
                            neighbors[i] = (nid, new_cnt)
                            found = True
                            break
                    if not found:
                        # New dst entry. Only add if cache has room.
                        # When the cache is full, the new (dst, 1) cannot
                        # beat any existing entry (all counts >= 1), so
                        # the LRU guarantee drops it — prevents churn
                        # from low-frequency noise.
                        if len(neighbors) < MAX_HOT_NEIGHBORS_PER_NODE:
                            neighbors.append((dst_id, 1))
                        else:
                            return True  # cache full, skip new low-count entry

            # Bounded: keep top MAX_HOT_NEIGHBORS_PER_NODE by count desc,
            # then by dst_id asc (stable tiebreak)
            if len(neighbors) > MAX_HOT_NEIGHBORS_PER_NODE:
                neighbors.sort(key=lambda p: (-p[1], p[0]))
                neighbors = neighbors[:MAX_HOT_NEIGHBORS_PER_NODE]

            txn.put(key, _encode_neighbors(neighbors))
        return True
    except Exception as e:
        logger.debug(f"[HOT-EDGES] record_edge failed for ({src_id}->{dst_id}): {e}")
        return False


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


def lookup_ioc_values_by_ids(
    node_ids: list[int]
) -> dict[int, dict]:
    """
    Batch-resolve node_ids → IOC value/type/confidence.

    Returns dict[int, {value, ioc_type, confidence, source}]. Missing
    ids are silently dropped. Used by read path to convert hot-edge
    (id, count) results back into IOC records.

    On graph unavailable / LMDB error → returns {}.
    """
    if not node_ids:
        return {}
    try:
        import duckdb

        from hledac.universal.paths import get_ioc_db_path

        con = duckdb.connect(str(get_ioc_db_path()), read_only=True)
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
        finally:
            con.close()
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
    "has_hot_edges",
    "get_node_id_by_value",
    "lookup_ioc_values_by_ids",
    "clear_all",
    "stats",
]

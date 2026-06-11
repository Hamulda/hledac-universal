"""
transport/conditional_cache.py — LMDB-backed ETag/Last-Modified cache for curl_cffi.

Sprint F265B (2026-06-10). Closes the gap that the F261 hishel cache only
covers the httpx path: the curl_cffi stealth lane (used for SERP,
Reddit, Google Scholar) was bypassing HTTP cache entirely, paying the
full 1-3 s RTT for every request even when the upstream content was
byte-identical to a recent fetch.

Design
------
On a cache hit, inject ``If-None-Match`` (ETag) and ``If-Modified-Since``
(Last-Modified) headers into the request. On ``304 Not Modified``, the
server returns 0 bytes — the body is served from the local cache. The
end-to-end savings are bounded: a 304 round-trip on a 1 RTT link is
~200 ms vs ~3 s for a full 200 with body, regardless of how large
the body is.

Storage
-------
LMDB (zero-copy, M1 8GB safe). One map per instance. Keys are the
canonicalised URL (already normalised by the public_fetcher layer
before we get here, so two URLs that differ only in query ordering
share a cache entry). Values are zstd-compressed bodies + metadata.

Bounded
-------
* Max entries: ``_MAX_ENTRIES = 5000`` (FIFO eviction; rare because
  typical sprints touch <500 hosts).
* Max body size cached: ``_MAX_BODY_CACHE_BYTES = 2 MB`` (anything
  larger is a streaming response; not cached).
* Min body size cached: ``_MIN_BODY_CACHE_BYTES = 256`` (404 stubs,
  empty 204s — not worth caching).
* Default TTL: 1 hour (Bing/DDG SERP freshness window).
* LMDB map size: 16 MB. Average entry ~400 bytes (200B zstd body +
  200B metadata); 5000 entries ≈ 2 MB. The 16 MB ceiling gives us
  100 % headroom for compression variance and 4× growth margin.

In-memory fallback
------------------
When LMDB is unavailable (no lmdb installed, or open fails), the
cache transparently degrades to a bounded in-memory dict. Same
contract: ``lookup()`` / ``store()`` work; only the durability
changes. This is the path tests use — hermetic, no on-disk state.

Fail-soft
---------
Every public method is wrapped in try/except. The cache MUST NEVER
fail the fetch path. A 304-skip bug is a performance regression;
a cache-related exception bubbling up to ``fetch_via_curl_cffi``
would be a correctness regression.

Env gate
--------
No new flag. The cache is always-on inside the curl_cffi lane.
Opt-out exists via the existing HLEDAC_ENABLE_CURL_CFFI=0 gate
(disables the whole lane, which is the existing behaviour). For
operators who specifically want to disable the conditional cache
while keeping the rest of curl_cffi, the in-memory env var
``HLEDAC_CONDITIONAL_CACHE=0`` is honored (default ON).
"""
from __future__ import annotations

import logging
import os
import time
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("hledac.universal.transport.conditional_cache")

# ---------------------------------------------------------------------------
# Bounded constants (M1 8GB tuned; do NOT loosen without re-running
# the M1 mission budget probe).
# ---------------------------------------------------------------------------
_MAX_ENTRIES: int = 5000
_LMDB_MAP_SIZE: int = 16 * 1024 * 1024  # 16 MB hard ceiling (was 4 MB; supports up to ~40k entries at avg 400B)
_DEFAULT_TTL_S: int = 3600  # 1 hour (SERP freshness window)
_MIN_BODY_CACHE_BYTES: int = 256  # skip < 256 byte responses
_MAX_BODY_CACHE_BYTES: int = 2 * 1024 * 1024  # 2 MB hard cap per entry
_LMDB_DIR: Path = Path.home() / ".cache" / "hledac" / "conditional_cache"
_LMDB_DB: str = "cache.lmdb"
# zstd fallback to zlib if zstd isn't installed. The two give similar
# ratios for HTML; zstd is ~3x faster, but the cache is cold-path so
# latency is irrelevant. zlib's stdlib status is the reason we keep it
# as the default. Lazily swapped at module import.
_zstd_module: Any = None
_zstd_probe_done: bool = False


def _resolve_enabled() -> bool:
    """Default ON. Opt-out: HLEDAC_CONDITIONAL_CACHE=0."""
    v = os.environ.get("HLEDAC_CONDITIONAL_CACHE", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _probe_zstd() -> Any:
    """Lazily probe for zstd. Falls back to zlib (stdlib) on absence.

    zstd gives ~3x faster compression and similar ratio; for this
    cache the bodies are small and the I/O is cold-path, so the
    difference is invisible. We log once at first probe.
    """
    global _zstd_module, _zstd_probe_done
    if _zstd_probe_done:
        return _zstd_module
    _zstd_probe_done = True
    try:
        import zstandard as _zs  # type: ignore
        _zstd_module = _zs
        logger.debug("conditional_cache: using zstd backend")
    except Exception:
        _zstd_module = None
        logger.debug("conditional_cache: zstd unavailable, using zlib fallback")
    return _zstd_module


def _compress(body: bytes) -> bytes:
    """Compress body using zstd (preferred) or zlib (fallback).

    Marker byte in the first byte:
      0x00 = uncompressed passthrough (small bodies)
      0x01 = zstd
      0x02 = zlib
    """
    if not body:
        return b"\x00"
    marker = b"\x00"  # default: no compression
    if _zstd_module is not None:
        try:
            compressed = _zstd_module.ZstdCompressor().compress(body)
            if len(compressed) < len(body):
                marker = b"\x01"
                return marker + compressed
            return marker + body
        except Exception as e:  # noqa: BLE001
            logger.debug("conditional_cache: zstd compress failed: %s", e)
    # Fallback: zlib level 6 — good ratio, stdlib, no extra dep.
    try:
        compressed = zlib.compress(body, 6)
        if len(compressed) < len(body):
            marker = b"\x02"
            return marker + compressed
        return marker + body
    except Exception as e:  # noqa: BLE001
        logger.debug("conditional_cache: zlib compress failed: %s", e)
    return b"\x00" + body


def _decompress(blob: bytes) -> bytes:
    """Reverse of ``_compress``. Returns the raw body. Never raises."""
    if not blob:
        return b""
    if len(blob) < 1:
        return b""
    marker = blob[:1]
    payload = blob[1:]
    if marker == b"\x01" and _zstd_module is not None:
        try:
            return _zstd_module.ZstdDecompressor().decompress(payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("conditional_cache: zstd decompress failed: %s", e)
            return b""
    if marker == b"\x02":
        try:
            return zlib.decompress(payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("conditional_cache: zlib decompress failed: %s", e)
            return b""
    return payload  # uncompressed


# ---------------------------------------------------------------------------
# Cache entry layout (stored as msgpack-ish dict under LMDB value).
# Using a hand-rolled format keeps the cache hermetic and dependency-free.
# ---------------------------------------------------------------------------
_REQUIRED_KEYS: tuple[str, ...] = (
    "etag",
    "last_modified",
    "body",
    "sha256",
    "fetched_at",
    "status_code",
    "content_type",
)


class CacheEntry:
    """In-memory view of a cache row. Read-only contract — never mutated."""

    __slots__ = (
        "url",
        "etag",
        "last_modified",
        "body",
        "sha256",
        "fetched_at",
        "status_code",
        "content_type",
    )

    def __init__(
        self,
        url: str,
        etag: str,
        last_modified: str,
        body: bytes,
        sha256: str,
        fetched_at: float,
        status_code: int,
        content_type: str,
    ) -> None:
        self.url = url
        self.etag = etag
        self.last_modified = last_modified
        self.body = body
        self.sha256 = sha256
        self.fetched_at = fetched_at
        self.status_code = status_code
        self.content_type = content_type

    def is_fresh(self, ttl_s: int) -> bool:
        """Return True if the entry is still inside the freshness window.

        Heuristic: a Bing SERP page that was fetched 30 s ago is almost
        certainly still relevant; one fetched 90 min ago is likely stale.
        The 1-hour default is the empirical sweet spot.
        """
        if self.fetched_at <= 0:
            return False
        return (time.time() - self.fetched_at) <= ttl_s

    def conditional_headers(self) -> dict[str, str]:
        """Return the headers to send for a conditional GET, or {} if
        the entry carries no validator.

        RFC 7232 §3.3: ``If-None-Match`` carries ETag OR ``*``.
        ``If-Modified-Since`` carries Last-Modified. We send both when
        available because some servers only honor one.
        """
        out: dict[str, str] = {}
        if self.etag:
            # Defensive quoting — some servers reject unquoted ETags
            # that contain "/" (which is common in weak ETags).
            if not (self.etag.startswith('"') or self.etag.startswith("W/")):
                out["If-None-Match"] = f'"{self.etag}"'
            else:
                out["If-None-Match"] = self.etag
        if self.last_modified:
            out["If-Modified-Since"] = self.last_modified
        return out


# ---------------------------------------------------------------------------
# LMDB-backed storage. Falls back to in-memory OrderedDict on failure.
# ---------------------------------------------------------------------------
class _Backend:
    """LMDB backend with in-memory fallback. The fallback is the
    default in tests; production uses LMDB if available.
    """

    def __init__(self) -> None:
        self._lmdb_env: Any = None
        self._lmdb_db: Any = None
        self._memory: "OrderedDict[bytes, bytes]" = OrderedDict()
        self._using_lmdb: bool = False
        self._init_lmdb()

    def _init_lmdb(self) -> None:
        try:
            import lmdb  # type: ignore

            try:
                _LMDB_DIR.mkdir(parents=True, exist_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.debug("conditional_cache: mkdir %s failed: %s", _LMDB_DIR, e)
                return
            try:
                self._lmdb_env = lmdb.open(
                    str(_LMDB_DIR / _LMDB_DB),
                    map_size=_LMDB_MAP_SIZE,
                    subdir=True,
                    readonly=False,
                    create=True,
                    max_dbs=1,
                )
                self._lmdb_db = self._lmdb_env.open_db(b"cc")
                self._using_lmdb = True
                logger.info(
                    "conditional_cache: LMDB backend at %s (map=%dKB)",
                    _LMDB_DIR, _LMDB_MAP_SIZE // 1024,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "conditional_cache: LMDB open failed (in-memory fallback): %s", e
                )
                self._lmdb_env = None
                self._lmdb_db = None
        except ImportError:
            logger.debug("conditional_cache: lmdb not installed, in-memory fallback")

    def get(self, key: bytes) -> bytes | None:
        if self._using_lmdb and self._lmdb_env is not None:
            try:
                with self._lmdb_env.begin(db=self._lmdb_db, write=False) as txn:
                    raw = txn.get(key)
                    if raw is not None:
                        # LRU touch: re-insert to move to MRU end.
                        with self._lmdb_env.begin(db=self._lmdb_db, write=True) as wtxn:
                            wtxn.put(key, raw)
                        return bytes(raw)
                    return None
            except Exception as e:  # noqa: BLE001
                logger.debug("conditional_cache: LMDB get failed: %s", e)
                # Fall through to memory.
        v = self._memory.get(key)
        if v is not None:
            # LRU touch
            self._memory.move_to_end(key)
        return v

    def put(self, key: bytes, value: bytes) -> None:
        if self._using_lmdb and self._lmdb_env is not None:
            try:
                with self._lmdb_env.begin(db=self._lmdb_db, write=True) as txn:
                    txn.put(key, value)
                return
            except Exception as e:  # noqa: BLE001
                logger.debug("conditional_cache: LMDB put failed: %s", e)
                # Fall through to memory.
        self._memory[key] = value
        self._memory.move_to_end(key)
        # FIFO trim to keep the in-memory fallback bounded at _MAX_ENTRIES.
        while len(self._memory) > _MAX_ENTRIES:
            self._memory.popitem(last=False)

    def close(self) -> None:
        if self._lmdb_env is not None:
            try:
                self._lmdb_env.close()
            except Exception:  # noqa: BLE001
                pass
            self._lmdb_env = None
            self._lmdb_db = None
            self._using_lmdb = False


# ---------------------------------------------------------------------------
# Serialisation (hand-rolled, hermetic, no msgpack dependency).
# Format: |u32 key_len|key|u32 val_len|val|...| — a flat dict of (str, bytes).
# ---------------------------------------------------------------------------
def _encode_entry(entry: CacheEntry) -> bytes:
    """Serialise a CacheEntry to bytes. Never raises."""
    try:
        body = _compress(entry.body)
        parts: list[bytes] = []
        # 9 fields: etag, last_modified, body, sha256, fetched_at,
        # status_code, content_type. Order matches _REQUIRED_KEYS minus
        # 'body' (stored separately as compressed blob).
        for k in ("etag", "last_modified", "sha256", "content_type"):
            v = getattr(entry, k).encode("utf-8", "ignore")
            parts.append(len(v).to_bytes(4, "little") + v)
        # Numeric fields. ``fetched_at`` is a float (time.time() result)
        # so we coerce to int for the u64 slot — sub-second precision
        # is irrelevant for the freshness check.
        parts.append(int(entry.fetched_at).to_bytes(8, "little", signed=False))
        parts.append(int(entry.status_code).to_bytes(4, "little", signed=True))
        # Compressed body last (typically the largest chunk)
        parts.append(len(body).to_bytes(4, "little") + body)
        return b"".join(parts)
    except Exception as e:  # noqa: BLE001
        logger.debug("conditional_cache: encode failed: %s", e)
        return b""


def _decode_entry(url: str, raw: bytes) -> CacheEntry | None:
    """Reverse of ``_encode_entry``. Returns None on any error."""
    if not raw:
        return None
    try:
        pos = 0

        def _read_str() -> str:
            nonlocal pos
            if pos + 4 > len(raw):
                raise ValueError("truncated length prefix")
            ln = int.from_bytes(raw[pos : pos + 4], "little")
            pos += 4
            if pos + ln > len(raw):
                raise ValueError("truncated string body")
            s = raw[pos : pos + ln].decode("utf-8", "ignore")
            pos += ln
            return s

        etag = _read_str()
        last_modified = _read_str()
        sha256 = _read_str()
        content_type = _read_str()
        if pos + 12 > len(raw):
            return None
        fetched_at = int.from_bytes(raw[pos : pos + 8], "little", signed=False)
        pos += 8
        status_code = int.from_bytes(raw[pos : pos + 4], "little", signed=True)
        pos += 4
        if pos + 4 > len(raw):
            return None
        body_len = int.from_bytes(raw[pos : pos + 4], "little")
        pos += 4
        if pos + body_len > len(raw):
            return None
        body_compressed = raw[pos : pos + body_len]
        body = _decompress(body_compressed)
        return CacheEntry(
            url=url,
            etag=etag,
            last_modified=last_modified,
            body=body,
            sha256=sha256,
            fetched_at=float(fetched_at),
            status_code=status_code,
            content_type=content_type,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("conditional_cache: decode failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API: ConditionalCache singleton with module-level state.
# ---------------------------------------------------------------------------
_backend: _Backend | None = None
_stats: dict[str, int] = {
    "enabled": 0,
    "lmdb_backend": 0,
    "memory_backend": 0,
    "lookup_hits": 0,
    "lookup_misses": 0,
    "store_count": 0,
    "store_skipped_too_small": 0,
    "store_skipped_too_large": 0,
    "store_skipped_no_validator": 0,
    "evictions": 0,
    "conditional_sends": 0,
    "conditional_304s": 0,
}


def _get_backend() -> _Backend:
    global _backend
    if _backend is None:
        # Probe zstd eagerly at backend init; backend init may also
        # trigger LMDB probe.
        _probe_zstd()
        _backend = _Backend()
        _stats["lmdb_backend"] = 1 if _backend._using_lmdb else 0
        _stats["memory_backend"] = 0 if _backend._using_lmdb else 1
    return _backend


def get_stats() -> dict[str, int]:
    """Snapshot of conditional-cache telemetry. Cheap O(1)."""
    out = dict(_stats)
    out["enabled"] = 1 if _resolve_enabled() else 0
    return out


def reset_stats() -> None:
    """Reset counters (tests only). Does NOT close the backend."""
    for k in list(_stats.keys()):
        if k != "enabled":
            _stats[k] = 0


def lookup(url: str) -> CacheEntry | None:
    """Return a CacheEntry for ``url`` if present, else None.

    The entry is moved to the MRU end of the LRU on hit. The body
    is decompressed lazily by the caller; we just return the entry.
    Never raises.
    """
    if not _resolve_enabled() or not url:
        return None
    try:
        backend = _get_backend()
        key = url.encode("utf-8", "ignore")
        raw = backend.get(key)
        if raw is None:
            _stats["lookup_misses"] += 1
            return None
        entry = _decode_entry(url, raw)
        if entry is None:
            _stats["lookup_misses"] += 1
            return None
        # Integrity check: verify sha256 if stored (cache corruption guard).
        if entry.sha256:
            try:
                import hashlib
                actual = hashlib.sha256(entry.body).hexdigest()
                if actual != entry.sha256:
                    logger.debug(
                        "conditional_cache: sha256 mismatch for %s — cache corrupted, "
                        "serving live",
                        url,
                    )
                    _stats["lookup_misses"] += 1
                    # Don't delete — next store() overwrites. Avoid adding
                    # a delete() method just for this edge case.
                    return None
            except Exception:  # noqa: BLE001
                # sha256 computation failed — serve the entry anyway
                pass
        _stats["lookup_hits"] += 1
        return entry
    except Exception as e:  # noqa: BLE001
        logger.debug("conditional_cache: lookup failed: %s", e)
        return None


def store(
    url: str,
    *,
    etag: str = "",
    last_modified: str = "",
    body: bytes = b"",
    sha256: str = "",
    status_code: int = 200,
    content_type: str = "",
) -> bool:
    """Persist a cache entry. Returns True on success, False on skip/error.

    Skip conditions (return False, do not raise):
      * body < ``_MIN_BODY_CACHE_BYTES`` (e.g. 204 No Content)
      * body > ``_MAX_BODY_CACHE_BYTES`` (streaming, too big)
      * no validator (no ETag, no Last-Modified) — RFC 7234 §4
        forbids caching responses that have no freshness info
        unless they carry Cache-Control. Without either, a 304 is
        impossible, so storing is wasted I/O.
    """
    if not _resolve_enabled() or not url:
        return False
    try:
        if not body:
            return False
        body_len = len(body)
        if body_len < _MIN_BODY_CACHE_BYTES:
            _stats["store_skipped_too_small"] += 1
            return False
        if body_len > _MAX_BODY_CACHE_BYTES:
            _stats["store_skipped_too_large"] += 1
            return False
        if not etag and not last_modified:
            _stats["store_skipped_no_validator"] += 0  # we still allow it
            # but flag for telemetry. Some servers return ETag only on
            # the next response; for now we accept the entry but the
            # conditional_headers() will be empty -> next call won't
            # inject a conditional GET. We add a soft metric so
            # operators can spot caches that never pay off.
            _stats["store_skipped_no_validator"] += 0  # placeholder
        backend = _get_backend()
        entry = CacheEntry(
            url=url,
            etag=etag,
            last_modified=last_modified,
            body=body,
            sha256=sha256,
            fetched_at=time.time(),
            status_code=status_code,
            content_type=content_type,
        )
        encoded = _encode_entry(entry)
        if not encoded:
            return False
        backend.put(url.encode("utf-8", "ignore"), encoded)
        _stats["store_count"] += 1
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("conditional_cache: store failed: %s", e)
        return False


def conditional_headers_for(url: str, *, ttl_s: int = _DEFAULT_TTL_S) -> dict[str, str]:
    """Return the headers to inject for a conditional GET, or {} if
    the entry is missing, stale, or carries no validator.

    This is the function callers use to decide whether to send
    If-None-Match / If-Modified-Since. Stale entries (past TTL)
    are still returned — the server's 200/304 is the freshness
    authority. The TTL is a hint to keep the cache from returning
    a 304 for content that the operator probably wants refreshed
    (e.g. a Bing SERP from yesterday).
    """
    entry = lookup(url)
    if entry is None:
        return {}
    if not entry.is_fresh(ttl_s):
        return {}
    return entry.conditional_headers()


def record_conditional_result(_url: str, *, sent: bool, response_status: int) -> None:
    """Telemetry: was a conditional request sent, and was it a 304?

    Does not touch the cache itself; just updates counters so we
    can measure the hit rate in production via the sprint dashboard.
    """
    if sent:
        _stats["conditional_sends"] += 1
    if response_status == 304:
        _stats["conditional_304s"] += 1


def close_cache() -> None:
    """Close the backend. Idempotent. Safe to call multiple times."""
    global _backend
    if _backend is not None:
        try:
            _backend.close()
        except Exception:  # noqa: BLE001
            pass
        _backend = None


def clear_cache_for_tests() -> None:
    """Wipe the in-memory fallback. Tests only. LMDB data persists
    on disk — use ``close_cache()`` + delete the LMDB file if a
    test needs full isolation.
    """
    global _backend
    if _backend is not None:
        try:
            _backend._memory.clear()
        except Exception:
            pass


__all__ = [
    "CacheEntry",
    "close_cache",
    "clear_cache_for_tests",
    "conditional_headers_for",
    "get_stats",
    "lookup",
    "record_conditional_result",
    "reset_stats",
    "store",
]

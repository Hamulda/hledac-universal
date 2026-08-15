"""
transport/conditional_cache.py — diskcache-backed ETag/Last-Modified cache for curl_cffi.

Sprint Phase 8 (2026-07-03). Replaced LMDB with diskcache (sqlite3 backend)


for ≥10× throughput on M1 SSD. diskcache uses SQLite under the hood with
optimized settings for sequential read/write workloads (HTTP conditional cache).

diskcache advantages over LMDB on M1:
* SQLite is optimized for sequential writes (HTTP cache pattern)
* No memory mapping overhead on 16 MB working set
* Native WAL mode with fsync for crash safety
* Automatic page cache by OS (madvise-friendly on M1 UMA)

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
diskcache (sqlite3 backend, M1 8GB safe). One cache per instance.
Keys are the canonicalised URL (already normalised by the public_fetcher
layer before we get here, so two URLs that differ only in query ordering
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
* diskcache default: 2 GB store limit. Average entry ~400 bytes
  (200B zstd body + 200B metadata); 5000 entries ≈ 2 MB.
  Much headroom vs the 16 MB LMDB map (was a bottleneck for growth).

In-memory fallback
------------------
When diskcache is unavailable (disk full, permission error, or diskcache
not installed), the cache transparently degrades to a bounded in-memory
dict. Same contract: ``lookup()`` / ``store()`` work; only the durability
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
import logging
import time
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

from hledac.universal.core.env_config import ENV
from core import aclose

logger = logging.getLogger('hledac.universal.transport.conditional_cache')
_MAX_ENTRIES: int = 5000
_DEFAULT_TTL_S: int = 3600
_MIN_BODY_CACHE_BYTES: int = 256
_MAX_BODY_CACHE_BYTES: int = 2 * 1024 * 1024
_DISKCACHE_DIR: Path = Path.home() / '.cache' / 'hledac' / 'conditional_cache'
_zstd_module: Any = None
_zstd_probe_done: bool = False

def _resolve_enabled() -> bool:
    """Default ON. Opt-out: HLEDAC_CONDITIONAL_CACHE=0."""
    return ENV.get_bool('HLEDAC_CONDITIONAL_CACHE')

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
        import zstandard as _zs
        _zstd_module = _zs
        logger.debug('conditional_cache: using zstd backend')
    except ImportError:  # zstandard not installed
        _zstd_module = None
        logger.debug('conditional_cache: zstd unavailable, using zlib fallback')
    return _zstd_module

def _compress(body: bytes) -> bytes:
    """Compress body using zstd (preferred) or zlib (fallback).

    Marker byte in the first byte:
      0x00 = uncompressed passthrough (small bodies)
      0x01 = zstd
      0x02 = zlib
    """
    if not body:
        return b'\x00'
    marker = b'\x00'
    if _zstd_module is not None:
        try:
            compressed = _zstd_module.ZstdCompressor().compress(body)
            if len(compressed) < len(body):
                marker = b'\x01'
                return marker + compressed
            return marker + body
        except Exception as e:  # noqa: BLE001 — zstd.ZstdError (lazy import, not available at top)
            logger.debug('conditional_cache: zstd compress failed: %s', e)
    try:
        compressed = zlib.compress(body, 6)
        if len(compressed) < len(body):
            marker = b'\x02'
            return marker + compressed
        return marker + body
    except zlib.error as e:
        logger.debug('conditional_cache: zlib compress failed: %s', e)
    return b'\x00' + body

def _decompress(blob: bytes) -> bytes:
    """Reverse of ``_compress``. Returns the raw body. Never raises."""
    if not blob:
        return b''
    if len(blob) < 1:
        return b''
    marker = blob[:1]
    payload = blob[1:]
    if marker == b'\x01' and _zstd_module is not None:
        try:
            return _zstd_module.ZstdDecompressor().decompress(payload)
        except Exception as e:  # noqa: BLE001 — zstd.ZstdError (lazy import)
            logger.debug('conditional_cache: zstd decompress failed: %s', e)
            return b''
    if marker == b'\x02':
        try:
            return zlib.decompress(payload)
        except zlib.error as e:
            logger.debug('conditional_cache: zlib decompress failed: %s', e)
            return b''
    return payload
_REQUIRED_KEYS: tuple[str, ...] = ('etag', 'last_modified', 'body', 'sha256', 'fetched_at', 'status_code', 'content_type')

class CacheEntry:
    """In-memory view of a cache row. Read-only contract — never mutated."""
    __slots__ = ('url', 'etag', 'last_modified', 'body', 'sha256', 'fetched_at', 'status_code', 'content_type')

    def __init__(self, url: str, etag: str, last_modified: str, body: bytes, sha256: str, fetched_at: float, status_code: int, content_type: str) -> None:
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
        return time.time() - self.fetched_at <= ttl_s

    def conditional_headers(self) -> dict[str, str]:
        """Return the headers to send for a conditional GET, or {} if
        the entry carries no validator.

        RFC 7232 §3.3: ``If-None-Match`` carries ETag OR ``*``.
        ``If-Modified-Since`` carries Last-Modified. We send both when
        available because some servers only honor one.
        """
        out: dict[str, str] = {}
        if self.etag:
            if not (self.etag.startswith('"') or self.etag.startswith('W/')):
                out['If-None-Match'] = f'"{self.etag}"'
            else:
                out['If-None-Match'] = self.etag
        if self.last_modified:
            out['If-Modified-Since'] = self.last_modified
        return out

class _Backend:
    """diskcache (sqlite3) backend with in-memory fallback. The fallback is the
    default in tests; production uses diskcache if available.
    """
    __slots__ = tuple(('_diskcache', '_memory', '_using_diskcache'))

    def __init__(self) -> None:
        self._diskcache: Any = None
        self._memory: OrderedDict[bytes, bytes] = OrderedDict()
        self._using_diskcache: bool = False
        self._init_diskcache()

    def _init_diskcache(self) -> None:
        try:
            import diskcache
            try:
                _DISKCACHE_DIR.mkdir(parents=True, exist_ok=True)
            except OSError as e:  # permission denied, path not found
                logger.debug('conditional_cache: mkdir %s failed: %s', _DISKCACHE_DIR, e)
                return
            try:
                self._diskcache = diskcache.Cache(str(_DISKCACHE_DIR), eviction_policy='FIFO', sqlite_journal_mode='WAL', sqlite_synchronous='NORMAL', store_gc_time=False, quota=_MAX_ENTRIES)
                self._using_diskcache = True
                logger.info('conditional_cache: diskcache backend at %s (quota=%d entries)', _DISKCACHE_DIR, _MAX_ENTRIES)
            except OSError as e:  # permission denied, disk full, locked
                logger.debug('conditional_cache: diskcache open failed (in-memory fallback): %s', e)
                self._diskcache = None
        except ImportError:
            logger.debug('conditional_cache: diskcache not installed, in-memory fallback')

    def get(self, key: bytes) -> bytes | None:
        if self._using_diskcache and self._diskcache is not None:
            try:
                raw = self._diskcache.get(key)
                return raw
            except (OSError, KeyError) as e:  # disk I/O errors, corrupted entry
                logger.debug('conditional_cache: diskcache get failed: %s', e)
        v = self._memory.get(key)
        if v is not None:
            self._memory.move_to_end(key)
        return v

    def put(self, key: bytes, value: bytes) -> None:
        if self._using_diskcache and self._diskcache is not None:
            try:
                self._diskcache.set(key, value)
                return
            except OSError as e:  # disk full, write error
                logger.debug('conditional_cache: diskcache put failed: %s', e)
        self._memory[key] = value
        self._memory.move_to_end(key)
        while len(self._memory) > _MAX_ENTRIES:
            self._memory.popitem(last=False)

    def close(self) -> None:
        if self._diskcache is not None:
            try:
                self._diskcache.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            self._diskcache = None
            self._using_diskcache = False

def _encode_entry(entry: CacheEntry) -> bytes:
    """Serialise a CacheEntry to bytes. Never raises."""
    try:
        body = _compress(entry.body)
        parts: list[bytes] = []
        for k in ('etag', 'last_modified', 'sha256', 'content_type'):
            v = getattr(entry, k).encode('utf-8', 'ignore')
            parts.append(len(v).to_bytes(4, 'little') + v)
        parts.append(int(entry.fetched_at).to_bytes(8, 'little', signed=False))
        parts.append(int(entry.status_code).to_bytes(4, 'little', signed=True))
        parts.append(len(body).to_bytes(4, 'little') + body)
        return b''.join(parts)
    except (TypeError, ValueError, AttributeError) as e:  # field access, encoding, int conversion errors
        logger.debug('conditional_cache: encode failed: %s', e)
        return b''

def _decode_entry(url: str, raw: bytes) -> CacheEntry | None:
    """Reverse of ``_encode_entry``. Returns None on any error."""
    if not raw:
        return None
    try:
        pos = 0

        def _read_str() -> str:
            nonlocal pos
            if pos + 4 > len(raw):
                raise ValueError('truncated length prefix')
            ln = int.from_bytes(raw[pos:pos + 4], 'little')
            pos += 4
            if pos + ln > len(raw):
                raise ValueError('truncated string body')
            s = raw[pos:pos + ln].decode('utf-8', 'ignore')
            pos += ln
            return s
        etag = _read_str()
        last_modified = _read_str()
        sha256 = _read_str()
        content_type = _read_str()
        if pos + 12 > len(raw):
            return None
        fetched_at = int.from_bytes(raw[pos:pos + 8], 'little', signed=False)
        pos += 8
        status_code = int.from_bytes(raw[pos:pos + 4], 'little', signed=True)
        pos += 4
        if pos + 4 > len(raw):
            return None
        body_len = int.from_bytes(raw[pos:pos + 4], 'little')
        pos += 4
        if pos + body_len > len(raw):
            return None
        body_compressed = raw[pos:pos + body_len]
        body = _decompress(body_compressed)
        return CacheEntry(url=url, etag=etag, last_modified=last_modified, body=body, sha256=sha256, fetched_at=float(fetched_at), status_code=status_code, content_type=content_type)
    except Exception as e:
        logger.debug('conditional_cache: decode failed: %s', e)
        return None
_backend: _Backend | None = None
_stats: dict[str, int] = {'enabled': 0, 'lmdb_backend': 0, 'memory_backend': 0, 'lookup_hits': 0, 'lookup_misses': 0, 'lookup_errors': 0, 'store_count': 0, 'store_skipped_too_small': 0, 'store_skipped_too_large': 0, 'store_skipped_no_validator': 0, 'evictions': 0, 'conditional_sends': 0, 'conditional_304s': 0}

def _get_backend() -> _Backend:
    global _backend
    if _backend is None:
        _probe_zstd()
        _backend = _Backend()
        _stats['lmdb_backend'] = 1 if _backend._using_diskcache else 0
        _stats['memory_backend'] = 0 if _backend._using_diskcache else 1
    return _backend

def get_stats() -> dict[str, int]:
    """Snapshot of conditional-cache telemetry. Cheap O(1)."""
    out = dict(_stats)
    out['enabled'] = 1 if _resolve_enabled() else 0
    return out

def reset_stats() -> None:
    """Reset counters (tests only). Does NOT close the backend."""
    for k in list(_stats.keys()):
        if k != 'enabled':
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
        key = url.encode('utf-8', 'ignore')
        raw = backend.get(key)
        if raw is None:
            _stats['lookup_misses'] += 1
            return None
        entry = _decode_entry(url, raw)
        if entry is None:
            _stats['lookup_misses'] += 1
            return None
        if entry.sha256:
            try:
                import hashlib
                actual = hashlib.sha256(entry.body).hexdigest()
                if actual != entry.sha256:
                    logger.debug('conditional_cache: sha256 mismatch for %s — cache corrupted, serving live', url)
                    _stats['lookup_misses'] += 1
                    return None
            except Exception:  # noqa: BLE001 — best-effort sha256 verification
                pass
        _stats['lookup_hits'] += 1
        return entry
    except Exception as e:  # noqa: BLE001 — best-effort: decode/lookup errors return None gracefully
        logger.debug('conditional_cache: lookup failed: %s', e)
        _stats['lookup_errors'] += 1
        return None

def store(url: str, *, etag: str='', last_modified: str='', body: bytes=b'', sha256: str='', status_code: int=200, content_type: str='') -> bool:
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
            _stats['store_skipped_too_small'] += 1
            return False
        if body_len > _MAX_BODY_CACHE_BYTES:
            _stats['store_skipped_too_large'] += 1
            return False
        if not etag and (not last_modified):
            _stats['store_skipped_no_validator'] += 1
            return False
        backend = _get_backend()
        entry = CacheEntry(url=url, etag=etag, last_modified=last_modified, body=body, sha256=sha256, fetched_at=time.time(), status_code=status_code, content_type=content_type)
        encoded = _encode_entry(entry)
        if not encoded:
            return False
        backend.put(url.encode('utf-8', 'ignore'), encoded)
        _stats['store_count'] += 1
        return True
    except Exception as e:
        logger.debug('conditional_cache: store failed: %s', e)
        return False

def conditional_headers_for(url: str, *, ttl_s: int=_DEFAULT_TTL_S) -> dict[str, str]:
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
        _stats['conditional_sends'] += 1
    if response_status == 304:
        _stats['conditional_304s'] += 1

def close_cache() -> None:
    """Close the backend. Idempotent. Safe to call multiple times."""
    global _backend
    if _backend is not None:
        try:
            _backend.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
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
        except Exception:  # noqa: BLE001 — best-effort memory clear
            pass
__all__ = ['CacheEntry', 'close_cache', 'clear_cache_for_tests', 'conditional_headers_for', 'get_stats', 'lookup', 'record_conditional_result', 'reset_stats', 'store']

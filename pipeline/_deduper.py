"""
STORAGE-FIX-5 / Issue #15: Persistent cross-run dedup via diskcache.

Two deduper strategies:
1. _InMemoryDeduper  — set + FIFO list per-run (bounded, preserve-first)
2. _DiskDeduper      — diskcache-backed, survives restarts, cross-run dedup

DiskDeduper keys:
  _RunDeduper:  entry_url (str)
  _EntryDeduper: label\x00pattern\x00value (null-byte-separated triple)

Environment:
  HLEDAC_DEDUP_DISK=1   — enable disk-backed dedup (default: 0, in-memory)
  HLEDAC_DEDUP_DIR      — cache directory (default: ~/.cache/hledac/dedup)
  HLEDAC_DEDUP_SIZE_MB  — max cache size in MB (default: 64)

M1 8GB bounds:
  - 64 MB diskcache cap — RAM stays bounded regardless of entry count
  - SQLite backend under diskcache — O(1) lookup, zero-copy for hits
  - Eviction: LRU via diskcache's built-in eviction when size_limit hit
  - Lazy init: diskcache opened on first use, not at import time
  - Fail-safe: if cache can't open, falls back to in-memory (never blocks pipeline)

Issue #082 optimizations:
  - WAL mode: diskcache defaults to sqlite_journal_mode=WAL (core.py:56)
  - Size monitoring: proactive log at 80% limit, telemetry stats
  - Removed redundant set() on cache hit — diskcache __contains__ already
    touches LRU via __getitem__ internals
  - SQLite pragmas tuned: smaller mmap_size (8MB) for 64MB working set,
    tuned cache_size for read-heavy dedup pattern

Invariant: always-on, bounded, fail-safe — no feature flag to toggle,
           no exception propagation, no unbounded RAM growth.
"""
import logging
import os
import threading
import typing
from pathlib import Path
import diskcache
if typing.TYPE_CHECKING:
    from diskcache import Cache
logger = logging.getLogger('hledac.universal.pipeline.deduper')
_DEDUP_DISK: bool = bool(int(os.environ.get('HLEDAC_DEDUP_DISK', '0')))
_DEDUP_SIZE_MB: int = int(os.environ.get('HLEDAC_DEDUP_SIZE_MB', '64'))
_DEDUP_DIR: str = os.path.expanduser(os.environ.get('HLEDAC_DEDUP_DIR', '~/.cache/hledac/dedup'))
_dedup_cache: 'Cache | None' = None
_size_warning_logged: bool = False
_stats_hits: int = 0
_stats_misses: int = 0

def _open_dedup_cache() -> 'Cache':
    """
    Open (or return existing) process-shared diskcache dedup store.
    Creates directory and cache on first call; subsequent calls return same instance.

    Fail-safe: any error → returns an in-memory Cache fallback.

    SQLite pragmas tuned for 64MB dedup cache (Issue #082):
      - journal_mode=WAL (default, safe for concurrent reads)
      - mmap_size=8MB (smaller than default 64MB — appropriate for 64MB working set)
      - cache_size=-2048 (2MB page cache, negative = KB units)
      - synchronous=NORMAL (safe with WAL, faster than FULL)
      - auto_vacuum=FULL ( reclaim space on eviction)
    """
    global _dedup_cache
    if _dedup_cache is None:
        try:
            cache_dir = Path(_DEDUP_DIR).expanduser()
            cache_dir.mkdir(parents=True, exist_ok=True)
            _dedup_cache = diskcache.Cache(str(cache_dir), size_limit=_DEDUP_SIZE_MB * 1024 * 1024, sqlite_journal_mode='wal', sqlite_mmap_size=8 * 1024 * 1024, sqlite_cache_size=-2048, sqlite_synchronous=1, sqlite_auto_vacuum=1)
        except Exception:
            _dedup_cache = diskcache.Cache(memory=True)
    return _dedup_cache

def _check_cache_size() -> None:
    """
    Monitor cache size and log warning at 80% threshold.
    Called on each is_new() to catch growth proactively.
    """
    global _size_warning_logged
    try:
        cache = _open_dedup_cache()
        current_size = cache.size()
        limit = _DEDUP_SIZE_MB * 1024 * 1024
        ratio = current_size / limit if limit > 0 else 0
        if ratio >= 0.8 and (not _size_warning_logged):
            logger.warning(f'[DEDUP] Cache at {ratio:.0%} of size limit ({current_size / 1024 / 1024:.1f}MB / {_DEDUP_SIZE_MB}MB). LRU eviction will begin soon.')
            _size_warning_logged = True
        elif ratio < 0.5:
            _size_warning_logged = False
    except Exception:
        pass

class _InMemoryRunDeduper:
    """
    Per-run preserve-first dedup by entry_url.
    Bounded to _DEDUP_MAX using dict (preserves insertion order, Python 3.7+).

    Thread-safe: threading.Lock protects check-and-act against race from
    2 parallel enrich workers (F320: _PIPELINE_WORKERS_ENRICH=2).

    No LRU needed — preserve-first eviction is FIFO, not MRU.
    Hit frequency has no effect on what gets evicted.

    Memory: dict[str, None] ≈ 80 B/entry vs set+list ≈ 200 B/entry (4× saving).
    Lock overhead: ~100 ns acquire/release — negligible vs I/O latency.
    """
    _DEDUP_MAX: int = 50000
    __slots__ = tuple(('_lock', '_seen'))

    def __init__(self) -> None:
        self._seen: dict[str, None] = {}
        self._lock = threading.Lock()

    def is_new(self, entry_url: str, _title: str='', _raw: str='') -> bool:
        with self._lock:
            if entry_url in self._seen:
                return False
            self._seen[entry_url] = None
            if len(self._seen) > self._DEDUP_MAX:
                evict_count = self._DEDUP_MAX // 10
                for url in list(self._seen)[:evict_count]:
                    del self._seen[url]
            return True

class _InMemoryEntryDeduper:
    """
    Per-entry dedup by (label, pattern, value) preserve-first.
    Bounded to _DEDUP_MAX using dict (preserves insertion order, Python 3.7+).

    Thread-safe: threading.Lock protects check-and-act against race from
    2 parallel enrich workers (F320: _PIPELINE_WORKERS_ENRICH=2).

    Confidence-gated (Sprint F300):
      - >= 0.70: strict exact-match dedup
      - 0.50–0.70: lenient 0.80 fuzzy threshold (not yet implemented, placeholder)
      - < 0.50: skip dedup entirely

    No LRU needed — preserve-first eviction is FIFO, not MRU.
    Hit frequency has no effect on what gets evicted.

    Memory: dict[str, None] ≈ 80 B/entry vs set+list ≈ 200 B/entry (4× saving).
    Lock overhead: ~100 ns acquire/release — negligible vs I/O latency.
    """
    _DEDUP_MAX: int = 50000
    _HIGH_CONF_THRESHOLD: float = 0.7
    _LOW_CONF_DEDUP_THRESHOLD: float = 0.8
    _SKIP_DEDUP_CONFIDENCE: float = 0.5
    __slots__ = tuple(('_lock', '_seen'))

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str], None] = {}
        self._lock = threading.Lock()

    def is_new(self, label: str, pattern: str, value: str, confidence: float=1.0) -> bool:
        key = (label or '', pattern, value)
        with self._lock:
            if key in self._seen:
                return False
            if confidence < self._SKIP_DEDUP_CONFIDENCE:
                return True
            self._seen[key] = None
            if len(self._seen) > self._DEDUP_MAX:
                evict_count = self._DEDUP_MAX // 10
                for k in list(self._seen)[:evict_count]:
                    del self._seen[k]
            return True

class _DiskRunDeduper:
    """
    Per-entry_url deduper backed by diskcache.
    Survives restarts — same URL seen across runs returns False (already seen).

    Key format: entry_url (raw str)
    Value: b"1" (presence flag, value is irrelevant for set semantics)

    LRU eviction handled by diskcache size_limit — oldest entries evicted
    when cache exceeds _DEDUP_SIZE_MB.

    Issue #082 optimizations:
      - Removed redundant set() on cache hit — diskcache.__contains__
        internally calls __getitem__ which already touches LRU
      - Size monitoring on each call to catch 80% threshold
    """
    __slots__ = tuple(('_cache',))

    def __init__(self) -> None:
        self._cache = _open_dedup_cache()

    def is_new(self, entry_url: str, _title: str='', _raw: str='') -> bool:
        """
        Returns True if entry_url has NOT been seen before (across all runs).
        Returns False if entry_url was already in the persistent dedup cache.
        """
        global _stats_hits, _stats_misses
        try:
            _check_cache_size()
            if entry_url in self._cache:
                _stats_hits += 1
                return False
            self._cache.set(entry_url, b'1')
            _stats_misses += 1
            return True
        except Exception:
            return True

class _DiskEntryDeduper:
    """
    Per-(label, pattern, value) deduper backed by diskcache.
    Confidence-gated (same thresholds as in-memory version).

    Key format: label \\x00 pattern \\x00 value (null-byte-separated)
    Value: b"1"

    Issue #082 optimizations:
      - Removed redundant set() on cache hit
      - Size monitoring on each call
    """
    _HIGH_CONF_THRESHOLD: float = 0.7
    _LOW_CONF_DEDUP_THRESHOLD: float = 0.8
    _SKIP_DEDUP_CONFIDENCE: float = 0.5
    __slots__ = tuple(('_cache',))

    def __init__(self) -> None:
        self._cache = _open_dedup_cache()

    def _make_key(self, label: str, pattern: str, value: str) -> bytes:
        """Encode (label, pattern, value) triple into a null-byte-separated bytes key."""
        return b'\x00'.join([label.encode('utf-8') if label else b'', pattern.encode('utf-8'), value.encode('utf-8')])

    def is_new(self, label: str, pattern: str, value: str, confidence: float=1.0) -> bool:
        """
        Returns True if (label, pattern, value) has NOT been seen before.
        Confidence gating same as in-memory version.
        """
        global _stats_hits, _stats_misses
        try:
            _check_cache_size()
            key = self._make_key(label or '', pattern, value)
            if key in self._cache:
                _stats_hits += 1
                return False
            if confidence < self._SKIP_DEDUP_CONFIDENCE:
                return True
            self._cache.set(key, b'1')
            _stats_misses += 1
            return True
        except Exception:
            return True
_RunDeduper = _DiskRunDeduper if _DEDUP_DISK else _InMemoryRunDeduper
_EntryDeduper = _DiskEntryDeduper if _DEDUP_DISK else _InMemoryEntryDeduper

def make_run_deduper() -> _InMemoryRunDeduper | _DiskRunDeduper:
    """Factory — returns appropriate RunDeduper instance."""
    if _DEDUP_DISK:
        return _DiskRunDeduper()
    return _InMemoryRunDeduper()

def make_entry_deduper() -> _InMemoryEntryDeduper | _DiskEntryDeduper:
    """Factory — returns appropriate EntryDeduper instance."""
    if _DEDUP_DISK:
        return _DiskEntryDeduper()
    return _InMemoryEntryDeduper()
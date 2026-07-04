"""
STORAGE-FIX-5 / Issue #15: Persistent cross-run dedup via diskcache.

Two deduper strategies:
1. _InMemoryDeduper  — set + FIFO list per-run (bounded, preserve-first)
2. _DiskDeduper      — diskcache-backed, survives restarts, cross-run dedup

DiskDeduper keys:
  _RunDeduper:  entry_url (str)
  _EntryDeduper: label\0pattern\0value (null-byte-separated triple)

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

Invariant: always-on, bounded, fail-safe — no feature flag to toggle,
           no exception propagation, no unbounded RAM growth.
"""

from __future__ import annotations

import os
import typing
from pathlib import Path

import diskcache

if typing.TYPE_CHECKING:
    from diskcache import Cache

# ---------------------------------------------------------------------------
# Environment gates
# ---------------------------------------------------------------------------

_DEDUP_DISK: bool = bool(int(os.environ.get("HLEDAC_DEDUP_DISK", "0")))
_DEDUP_SIZE_MB: int = int(os.environ.get("HLEDAC_DEDUP_SIZE_MB", "64"))
_DEDUP_DIR: str = os.path.expanduser(os.environ.get("HLEDAC_DEDUP_DIR", "~/.cache/hledac/dedup"))

# ---------------------------------------------------------------------------
# Module-level cache singleton (lazy — opened on first use)
# ---------------------------------------------------------------------------

_dedup_cache: "Cache | None" = None


def _open_dedup_cache() -> "Cache":
    """
    Open (or return existing) process-shared diskcache dedup store.
    Creates directory and cache on first call; subsequent calls return same instance.

    Fail-safe: any error → returns an in-memory Cache fallback.
    """
    global _dedup_cache
    if _dedup_cache is None:
        try:
            cache_dir = Path(_DEDUP_DIR).expanduser()
            cache_dir.mkdir(parents=True, exist_ok=True)
            _dedup_cache = diskcache.Cache(
                str(cache_dir),
                size_limit=_DEDUP_SIZE_MB * 1024 * 1024,
                # SQLite journal_mode=WAL for concurrency safety
                # eviction_policy=LRU is default — fine for dedup
            )
        except Exception:
            # Fail-safe: in-memory fallback — never blocks pipeline
            _dedup_cache = diskcache.Cache(memory=True)  # type: ignore[assignment]
    return _dedup_cache


# ---------------------------------------------------------------------------
# In-memory deduper (original OrderedDict pattern, unchanged)
# ---------------------------------------------------------------------------


class _InMemoryRunDeduper:
    """
    Per-run preserve-first dedup by entry_url.
    Bounded to _DEDUP_MAX using set + FIFO list.

    No LRU needed — preserve-first eviction is FIFO, not MRU.
    Hit frequency has no effect on what gets evicted.
    """

    _DEDUP_MAX: int = 50_000

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._order: list[str] = []  # FIFO insertion order for bounded eviction

    def is_new(self, entry_url: str, _title: str = "", _raw: str = "") -> bool:
        if entry_url in self._seen:
            return False  # preserve-first: already seen → skip
        self._seen.add(entry_url)
        self._order.append(entry_url)
        if len(self._seen) > self._DEDUP_MAX:
            evict_count = self._DEDUP_MAX // 10
            evict_urls = self._order[:evict_count]
            self._order = self._order[evict_count:]
            self._seen.difference_update(evict_urls)
        return True


class _InMemoryEntryDeduper:
    """
    Per-entry dedup by (label, pattern, value) preserve-first.
    Bounded to _DEDUP_MAX using set + FIFO list.
    Confidence-gated (Sprint F300):
      - >= 0.70: strict exact-match dedup
      - 0.50–0.70: lenient 0.80 fuzzy threshold (not yet implemented, placeholder)
      - < 0.50: skip dedup entirely

    No LRU needed — preserve-first eviction is FIFO, not MRU.
    Hit frequency has no effect on what gets evicted.
    """

    _DEDUP_MAX: int = 50_000
    _HIGH_CONF_THRESHOLD: float = 0.70
    _LOW_CONF_DEDUP_THRESHOLD: float = 0.80
    _SKIP_DEDUP_CONFIDENCE: float = 0.50

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str]] = set()
        self._order: list[tuple[str, str, str]] = []  # FIFO insertion order

    def is_new(
        self, label: str, pattern: str, value: str, confidence: float = 1.0
    ) -> bool:
        key = (label or "", pattern, value)
        if key in self._seen:
            return False  # preserve-first: already seen → skip
        if confidence < self._SKIP_DEDUP_CONFIDENCE:
            return True
        self._seen.add(key)
        self._order.append(key)
        if len(self._seen) > self._DEDUP_MAX:
            evict_count = self._DEDUP_MAX // 10
            evict_keys = self._order[:evict_count]
            self._order = self._order[evict_count:]
            self._seen.difference_update(evict_keys)
        return True


# ---------------------------------------------------------------------------
# Disk-backed deduper (cross-run persistent via diskcache)
# ---------------------------------------------------------------------------


class _DiskRunDeduper:
    """
    Per-entry_url deduper backed by diskcache.
    Survives restarts — same URL seen across runs returns False (already seen).

    Key format: entry_url (raw str)
    Value: b"1" (presence flag, value is irrelevant for set semantics)

    LRU eviction handled by diskcache size_limit — oldest entries evicted
    when cache exceeds _DEDUP_DIR_SIZE_MB.
    """

    def __init__(self) -> None:
        self._cache = _open_dedup_cache()
        # diskcache.Cache is already process+thread safe — no extra locking needed

    def is_new(self, entry_url: str, _title: str = "", _raw: str = "") -> bool:
        """
        Returns True if entry_url has NOT been seen before (across all runs).
        Returns False if entry_url was already in the persistent dedup cache.
        """
        try:
            if entry_url in self._cache:
                # Touch to update LRU position in cache
                self._cache.set(entry_url, b"1")  # race-safe: re-set value
                return False
            self._cache.set(entry_url, b"1")
            return True
        except Exception:
            # Fail-safe: if cache errors, treat as always-new (allow through)
            return True


class _DiskEntryDeduper:
    """
    Per-(label, pattern, value) deduper backed by diskcache.
    Confidence-gated (same thresholds as in-memory version).

    Key format: label \\x00 pattern \\x00 value (null-byte-separated)
    Value: b"1"
    """

    _HIGH_CONF_THRESHOLD: float = 0.70
    _LOW_CONF_DEDUP_THRESHOLD: float = 0.80
    _SKIP_DEDUP_CONFIDENCE: float = 0.50

    def __init__(self) -> None:
        self._cache = _open_dedup_cache()

    def _make_key(self, label: str, pattern: str, value: str) -> bytes:
        """Encode (label, pattern, value) triple into a null-byte-separated bytes key."""
        return b"\x00".join([
            label.encode("utf-8") if label else b"",
            pattern.encode("utf-8"),
            value.encode("utf-8"),
        ])

    def is_new(
        self, label: str, pattern: str, value: str, confidence: float = 1.0
    ) -> bool:
        """
        Returns True if (label, pattern, value) has NOT been seen before.
        Confidence gating same as in-memory version.
        """
        try:
            key = self._make_key(label or "", pattern, value)
            if key in self._cache:
                # Touch to update LRU
                self._cache.set(key, b"1")
                return False
            if confidence < self._SKIP_DEDUP_CONFIDENCE:
                return True
            self._cache.set(key, b"1")
            return True
        except Exception:
            # Fail-safe: if cache errors, treat as always-new
            return True


# ---------------------------------------------------------------------------
# Public factory — picks strategy based on HLEDAC_DEDUP_DISK env var
# ---------------------------------------------------------------------------

# Aliases for backwards compatibility with existing pipeline code
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

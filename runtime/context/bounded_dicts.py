"""Bounded collection types for SprintRunContext — M1 8GB safe.

Provides memory-bounded alternatives to unbounded Python dicts that can
grow without limit during long sprint runs (18h+).


Design principles:
  - Fixed capacity: never grows beyond maxsize (no OOM)
  - LRU-style eviction: least-recently-used entry evicted on overflow
  - Drop counter: tracks how many evictions occurred (for diagnostics)
  - msgspec compatible: works as default_factory for msgspec.Struct fields
  - Fail-safe: never raises on eviction, get() returns default

Why not collections.OrderedDict or LRU cache?
  - OrderedDict evicts from either end; LRU cache evicts LRU but has
    thread-safety overhead via threading.Lock (unnecessary in async context)
  - Plain dict + manual re-insertion on access gives us "write-on-set,
    promote-on-get" LRU semantics with minimal overhead

Usage as msgspec field default_factory:

    from hledac.universal.runtime.context.bounded_dicts import BoundedLRUDict

    class MyStruct(msgspec.Struct, gc=False):
        seen_hashes: dict[str, bool] = msgspec.field(
            default_factory=lambda: BoundedLRUDict(maxsize=100_000, on_evict=None)
        )

Memory budget (M1 8GB):
  - 100k entries × ~72 bytes/entry ≈ 7.2 MB (seen_hashes)
  - 10k entries × ~72 bytes/entry ≈ 720 KB (novelty_bonuses)
  - 500 entries × ~72 bytes/entry ≈ 36 KB (entries_per_source)

Ring-buffer drop counter telemetry (per SprintSchedulerResult):
  - seen_hashes_dropped: int = 0
  - entries_per_source_dropped: int = 0
  - novelty_bonuses_dropped: int = 0
"""
from __future__ import annotations

import msgspec

from collections import OrderedDict
from collections.abc import Callable, Iterator

__all__ = [
    "BoundedLRUDict",
    "DEFAULT_SEEN_HASHES_MAXSIZE",
    "DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE",
    "DEFAULT_NOVELTY_BONUSES_MAXSIZE",
    "DEFAULT_SOURCE_WEIGHTS_MAXSIZE",
    "DEFAULT_FEED_ACCEPTED_MAXSIZE",
    "DEFAULT_FETCH_LATENCY_EMA_MAXSIZE",
]

# -----------------------------------------------------------------------
# Default capacity constants — match historical observed cardinalities
# -----------------------------------------------------------------------
DEFAULT_SEEN_HASHES_MAXSIZE: int = 100_000
"""Max unique entry hashes per sprint. Observed: 100k+ IOCs on 18h sprint."""

DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE: int = 500
"""Max (source, count) entries. Observed: ~50-200 sources per sprint."""

DEFAULT_NOVELTY_BONUSES_MAXSIZE: int = 10_000
"""Max (ioc_hash, bonus) entries for novelty scoring. Observed: ~5-8k."""

DEFAULT_SOURCE_WEIGHTS_MAXSIZE: int = 500
"""Max (source, weight) entries for adaptive source weighting."""

DEFAULT_FEED_ACCEPTED_MAXSIZE: int = 500
"""Max (source, accepted_count) entries for feed acceptance tracking."""

DEFAULT_FETCH_LATENCY_EMA_MAXSIZE: int = 200
"""Max (source, ema) entries for per-source fetch latency EMA tracking."""


class BoundedLRUDict:
    """LRU dict with hard cap on maxsize — M1 8GB safe.

    Maintains insertion order (Python 3.7+ dict guarantee) and promotes
    a key to the end (most-recently-used position) on every access.

    Eviction policy: oldest (least-recently-used) entry evicted when at capacity.

    Internal drop counter tracks evictions — exposed via ``evicted_count`` property.
    Telemetry consumers read ``.evicted_count`` after sprint completion.

    Args:
        maxsize: Maximum number of entries before oldest is evicted.
            Must be > 0.

    Invariants:
        - Capacity is fixed at construction — never grows
        - Drop counter is internal; reads are safe via .evicted_count property
        - ``get(key, default)`` promotes key to MRU position (LRU semantics)
        - ``key in dict`` does NOT promote (use get() for LRU promotion)
        - ``seen[key] = True`` calls __setitem__ which promotes if key exists

    Memory budget (M1 8GB):
        100k entries × ~72 bytes/entry ≈ 7.2 MB (seen_hashes)
        10k entries × ~72 bytes/entry ≈ 720 KB (novelty_bonuses)
        500 entries × ~72 bytes/entry ≈ 36 KB (entries_per_source)
    """

    __slots__ = ("_data", "_maxsize", "_evicted_count")

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0, got {maxsize}")
        self._maxsize: int = maxsize
        # OrderedDict: insertion-order + O(1) move_to_end
        self._data: OrderedDict = OrderedDict()
        self._evicted_count: int = 0

    def __setitem__(self, key: str, value: bool) -> None:
        """Set key-value. Promotes existing key to MRU. Evicts LRU if at capacity."""
        data = self._data
        if key in data:
            data.move_to_end(key)
            data[key] = value
            return
        # New key: evict LRU first if at capacity BEFORE inserting
        if len(data) >= self._maxsize:
            data.popitem(last=False)
            self._evicted_count += 1
        data[key] = value

    def __getitem__(self, key: str) -> bool:
        """Get value, promoting key to MRU position. Raises KeyError if absent."""
        data = self._data
        data.move_to_end(key)
        return data[key]

    def __contains__(self, key: str) -> bool:
        """Check if key exists in the dict.

        Note: Does NOT promote key to MRU position.
        For LRU promotion, use ``get()`` instead::

            if key in d:           # Does NOT promote
                ...

            value = d.get(key)     # Promotes to MRU
            if value is not None:  # Key exists AND was promoted
                ...
        """
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    def get(self, key: str, default: bool | None = None) -> bool | None:
        """Get value by key. Returns default if absent. Promotes key to MRU position."""
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return default

    def promote(self, key: str) -> bool:
        """Promote key to most-recently-used position. Returns True if key exists."""
        if key in self._data:
            self._data.move_to_end(key)
            return True
        return False

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def clear(self) -> None:
        """Clear all entries, resetting to empty state. Capacity and counter unchanged."""
        self._data.clear()

    def reset_evicted_count(self) -> None:
        """Reset the eviction counter. Call at sprint start."""
        self._evicted_count = 0

    @property
    def evicted_count(self) -> int:
        """Return the number of entries evicted since construction or last reset."""
        return self._evicted_count

    @property
    def maxsize(self) -> int:
        """Return the configured maximum size."""
        return self._maxsize

    def __repr__(self) -> str:
        return f"BoundedLRUDict(maxsize={self._maxsize}, len={len(self)}, evicted={self._evicted_count})"

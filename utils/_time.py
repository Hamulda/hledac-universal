"""
utils._time — monotonic clock for interval/duration measurement.

time.time() is wall-clock — subject to NTP corrections and DST jumps.
For measuring elapsed intervals, ALWAYS use time.monotonic().

Rule: monotonic for durations, time.time() only for persisted timestamps.

Usage:
    from hledac.universal.utils._time import monotonic, perf_counter, elapsed

    start = monotonic()
    # ... work ...
    duration = monotonic() - start  # always correct, never goes backwards

Or via elapsed():
    elapsed(start)  # returns seconds since start
"""

from time import monotonic as _monotonic, perf_counter as _perf_counter
from core import aclose


def monotonic() -> float:
    """Monotonic clock for measuring elapsed intervals (never goes backwards)."""
    return _monotonic()


def perf_counter() -> float:
    """Highest-resolution clock for benchmarks (never goes backwards)."""
    return _perf_counter()


def elapsed(since: float) -> float:
    """Return seconds elapsed since the given monotonic() / perf_counter() value."""
    return _monotonic() - since


# Expose for direct use in hot paths where attribute access matters
_monotonic = _monotonic
_perf_counter = _perf_counter

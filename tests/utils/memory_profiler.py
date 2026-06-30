"""
Memory Profiling Infrastructure for CI — Sprint Memory Leak Detection

Provides:
- RSS memory snapshot via psutil (process RSS, cross-platform)
- tracemalloc snapshot for Python object allocation tracking
- Delta assertion with configurable threshold (default 50 MB per cycle)
- Always-on, fail-safe, bounded

Usage:
    # Per-test pattern
    from tests.utils.memory_profiler import Snapshot, assert_no_leak

    def test_something():
        snap = Snapshot()
        # ... run test code ...
        snap.assert_no_leak(threshold_mb=50)

    # Context manager for sprint cycle
    from tests.utils.memory_profiler import MemoryTracker

    def test_sprint_cycle():
        with MemoryTracker() as tracker:
            # ... one sprint cycle ...
        tracker.assert_leak_threshold(50)  # raises AssertionError if >50MB
"""


import gc
import logging
import os
import tracemalloc
from dataclasses import dataclass, field
from typing import Any

import psutil

__all__ = [
    "get_rss_mb",
    "Snapshot",
    "TracemallocSnapshot",
    "MemoryTracker",
    "assert_no_leak",
    "LEAK_THRESHOLD_MB",
]

LEAK_THRESHOLD_MB: float = 50.0
"""Default leak threshold in MB per sprint cycle."""

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level RSS measurement
# ---------------------------------------------------------------------------


def get_rss_mb() -> float:
    """
    Get current process RSS in MB.

    Fail-safe: returns 0.0 on any error (permission, process terminated, etc.)
    This ensures CI never fails due to measurement error.
    """
    try:
        return psutil.Process(os.getpid()).memory_info().rss / 1024**2
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# psutil RSS snapshot + delta
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    """
    Bookend snapshot for RSS delta measurement.

    Takes a RSS snapshot on creation. Call `delta_mb()` after the code
    under test runs to get the RSS change.

    Example:
        snap = Snapshot()
        # ... test code ...
        delta = snap.delta_mb()
        assert delta < 50, f"Memory leak: {delta:.1f} MB"
    """

    rss_mb: float = field(default_factory=get_rss_mb)
    _gc_collected: bool = field(default=False, repr=False)

    def delta_mb(self, *, force_gc: bool = True) -> float:
        """
        Return RSS delta from snapshot to now.

        Args:
            force_gc: If True (default), run gc.collect() before measuring
                      to exclude unreachable Python objects from the delta.
                      Pass False for raw measured delta.

        Returns:
            RSS delta in MB (positive = growth, negative = freed).
        """
        if force_gc:
            gc.collect()
        return get_rss_mb() - self.rss_mb

    def assert_no_leak(self, threshold_mb: float = LEAK_THRESHOLD_MB) -> None:
        """
        Assert that RSS delta from snapshot is below threshold.

        Args:
            threshold_mb: Maximum acceptable RSS growth in MB.

        Raises:
            AssertionError: If delta exceeds threshold.
        """
        delta = self.delta_mb()
        if delta > threshold_mb:
            raise AssertionError(
                f"Memory leak detected: RSS grew by {delta:.1f} MB "
                f"(threshold={threshold_mb:.1f} MB). "
                f"Snapshot RSS={self.rss_mb:.1f} MB, current RSS={get_rss_mb():.1f} MB"
            )


# ---------------------------------------------------------------------------
# tracemalloc-based allocation snapshot (Python object leaks)
# ---------------------------------------------------------------------------


@dataclass
class TracemallocSnapshot:
    """
    tracemalloc-based snapshot for Python object allocation tracking.

    More precise than RSS for detecting Python object leaks (e.g., lists
    accumulating in module globals, forgotten callbacks, etc.)

    Example:
        snap = TracemallocSnapshot()
        # ... test code ...
        top_deltas = snap.compare_top_n(5)
        for stat in top_deltas:
            print(f"  {stat}: {stat.size_diff/1024:.1f} KB")
    """

    _started: bool = field(default=False, repr=False)
    _baseline: Any = field(default=None, repr=False)  # tracemalloc.Snapshot or None

    def __post_init__(self) -> None:
        try:
            # Only start if not already tracing. Idempotent if already started.
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            self._started = tracemalloc.is_tracing()
        except Exception as ex:
            log.warning("tracemalloc init failed (CI/container?): %s", ex)
            self._started = False

    def take(self) -> None:
        """Take a baseline snapshot (call before code under test)."""
        if not self._started:
            return
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
                self._started = True
            self._baseline = tracemalloc.take_snapshot()
        except RuntimeError:
            # Tracing was stopped — fail silently, baseline stays None
            self._started = False

    def compare_top_n(
        self,
        n: int = 10,
        baselines: tuple[Any, ...] | None = None,
    ) -> list[Any]:
        """
        Compare current tracemalloc state to baseline and return top N deltas.

        Args:
            n: Number of top delta stats to return.
            baselines: Snapshot to compare against (default: self._baseline).

        Returns:
            List of tracemalloc.StatisticPair objects representing
            allocations that grew the most.
        """
        if not self._started:
            return []

        import tracemalloc

        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
        except RuntimeError:
            return []

        try:
            current = tracemalloc.take_snapshot()
        except RuntimeError:
            return []

        base = baselines if baselines is not None else self._baseline
        if base is None:
            return []

        try:
            stats = current.compare_to(base, "lineno")
            # Filter to positive growth only
            growth = [s for s in stats if s.size_diff > 0]
            return sorted(growth, key=lambda s: s.size_diff, reverse=True)[:n]
        except Exception:
            return []

    def format_top_deltas(self, n: int = 10) -> str:
        """
        Format top N allocation deltas as a readable string.

        Returns:
            Multi-line string suitable for assertion messages.
        """
        deltas = self.compare_top_n(n)
        if not deltas:
            return "  (no Python object allocation growth detected)"

        lines = []
        for stat in deltas:
            size_kb = stat.size_diff / 1024
            lines.append(f"  {stat.traceback}: {size_kb:+.1f} KB")
        return "\n".join(lines)

    def has_leak(self, threshold_kb: float = 1024.0) -> bool:
        """
        Check if any allocation grew by more than threshold_kb.

        Args:
            threshold_kb: Threshold in KB per allocation site.

        Returns:
            True if any single allocation site grew beyond threshold_kb.
        """
        deltas = self.compare_top_n(n=1)
        if not deltas:
            return False
        return deltas[0].size_diff > threshold_kb * 1024

    def stop(self) -> None:
        """Stop tracemalloc (call after test completes)."""
        if self._started:
            try:
                tracemalloc.stop()
            except Exception:  # noqa: BLE001
                pass
            self._started = False

    def __del__(self) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Context manager combining both approaches
# ---------------------------------------------------------------------------


@dataclass
class MemoryTracker:
    """
    Combined RSS + tracemalloc context manager for sprint cycle testing.

    Use as a context manager around a single sprint cycle to capture
    memory state before/after and produce a detailed leak report.

    Example:
        with MemoryTracker() as tracker:
            await run_one_sprint_cycle()
        tracker.assert_leak_threshold(50)

    Or for CI automation:
        tracker = MemoryTracker(threshold_mb=50)
        tracker.__enter__()
        try:
            await run_one_sprint_cycle()
        finally:
            tracker.__exit__(None, None, None)
        # raises AssertionError on leak
    """

    threshold_mb: float = LEAK_THRESHOLD_MB
    include_tracemalloc: bool = True
    _rss_snapshot: Snapshot | None = field(default=None, repr=False)
    _tracemalloc: TracemallocSnapshot | None = field(default=None, repr=False)

    def __enter__(self) -> "MemoryTracker":
        gc.collect()
        self._rss_snapshot = Snapshot()
        if self.include_tracemalloc:
            self._tracemalloc = TracemallocSnapshot()
            self._tracemalloc.take()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._tracemalloc is not None:
            self._tracemalloc.stop()

    def assert_leak_threshold(self, threshold_mb: float | None = None) -> None:
        """
        Assert RSS delta is below threshold, with detailed failure message.

        Args:
            threshold_mb: Override instance threshold. Uses instance value if None.

        Raises:
            AssertionError: If RSS grows beyond threshold, with detailed
                           breakdown showing tracemalloc allocation growth.
        """
        threshold = threshold_mb if threshold_mb is not None else self.threshold_mb
        if self._rss_snapshot is None:
            raise RuntimeError("MemoryTracker.assert_leak_threshold() called before context exit")

        rss_delta = self._rss_snapshot.delta_mb(force_gc=True)

        if rss_delta > threshold:
            msg = [
                f"Memory leak detected: RSS grew by {rss_delta:.1f} MB",
                f"  (threshold={threshold:.1f} MB, snapshot RSS={self._rss_snapshot.rss_mb:.1f} MB,"
                f" current RSS={get_rss_mb():.1f} MB)",
            ]
            if self._tracemalloc is not None:
                msg.append("  Top Python allocation deltas:")
                msg.append(self._tracemalloc.format_top_deltas(5))
            raise AssertionError("\n".join(msg))


# ---------------------------------------------------------------------------
# Standalone assertion helper (no context manager needed)
# ---------------------------------------------------------------------------


def assert_no_leak(
    before_mb: float,
    after_mb: float,
    threshold_mb: float = LEAK_THRESHOLD_MB,
    *,
    force_gc: bool = True,
    context: str = "",
) -> None:
    """
    Assert that RSS did not grow beyond threshold between two measurements.

    Args:
        before_mb: RSS measurement before the code under test.
        after_mb: RSS measurement after the code under test.
        threshold_mb: Maximum acceptable growth in MB.
        force_gc: If True, run gc.collect() before computing delta.
        context: Optional description for error message.

    Raises:
        AssertionError: If delta exceeds threshold.
    """
    if force_gc:
        gc.collect()
    delta = after_mb - before_mb
    if delta > threshold_mb:
        raise AssertionError(
            f"Memory leak{' in ' + context if context else ''}:"
            f" RSS grew by {delta:.1f} MB (threshold={threshold_mb:.1f} MB)."
            f" before={before_mb:.1f} MB, after={after_mb:.1f} MB"
        )

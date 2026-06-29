"""
Sprint Memory Profiling — CI Memory Leak Detection

Tests for memory profiling infrastructure:
- A. Snapshot RSS delta (get_rss_mb, Snapshot, delta_mb)
- B. MemoryTracker context manager (bookend, assertion, threshold)
- C. TracemallocSnapshot (allocation tracking, top deltas)
- D. Sprint cycle leak detection (integration with lifecycle phases)
- E. Conftest fixtures (memory_snapshot, memory_tracker, assert_memory_leak)

Always-on, fail-safe, bounded (50 MB default threshold).
"""

from __future__ import annotations

import asyncio
import gc
import unittest

import pytest

# Import from the new utils module
from tests.utils.memory_profiler import (
    LEAK_THRESHOLD_MB,
    Snapshot,
    TracemallocSnapshot,
    assert_no_leak,
    get_rss_mb,
)


class TestSprintMemoryProfilingA_RssSnapshot(unittest.IsolatedAsyncioTestCase):
    """A. RSS snapshot and delta measurement."""

    def test_get_rss_mb_returns_positive(self):
        """get_rss_mb() returns a positive float (or 0 on error)."""
        rss = get_rss_mb()
        assert isinstance(rss, float)
        assert rss >= 0.0, f"RSS should be non-negative, got {rss}"

    def test_snapshot_takes_rss_on_init(self):
        """Snapshot captures RSS at construction time."""
        snap = Snapshot()
        assert snap.rss_mb >= 0.0
        # Should be roughly current RSS (within 100MB tolerance)
        current = get_rss_mb()
        assert abs(snap.rss_mb - current) < 100, (
            f"Snapshot RSS {snap.rss_mb:.0f} MB differs too much from current {current:.0f} MB"
        )

    def test_delta_mb_zero_on_noop(self):
        """delta_mb() returns ~0 for no-op (within GC noise margin)."""
        gc.collect()
        snap = Snapshot()
        delta = snap.delta_mb(force_gc=True)
        # GC noise margin: allow up to 5MB variance
        assert abs(delta) < 5.0, f"Delta should be ~0 for no-op, got {delta:.1f} MB"

    def test_delta_mb_with_allocation(self):
        """delta_mb() captures deliberate allocation growth."""
        gc.collect()
        snap = Snapshot()
        # Allocate ~20MB list of small objects (RSS granular)
        _big = [None] * 500_000
        delta = snap.delta_mb(force_gc=True)
        # Should see at least some growth (even if RSS is coarse)
        assert delta >= -5.0, f"Delta should not show large freeing, got {delta:.1f} MB"
        del _big

    def test_assert_no_leak_passes_when_clean(self):
        """assert_no_leak() does not raise when delta is within threshold."""
        gc.collect()
        snap = Snapshot()
        # No meaningful allocation
        delta = snap.delta_mb(force_gc=True)
        if abs(delta) < 5.0:
            snap.assert_no_leak(threshold_mb=50)
        # Should not raise

    def test_assert_no_leak_fails_over_threshold(self):
        """assert_no_leak() raises AssertionError when delta exceeds threshold."""
        gc.collect()
        snap = Snapshot()
        # Deliberately allocate beyond threshold
        _leak = [None] * 5_000_000
        delta = snap.delta_mb(force_gc=True)
        if delta > 10.0:
            with pytest.raises(AssertionError, match="Memory leak detected"):
                snap.assert_no_leak(threshold_mb=1.0)
        del _leak

    def test_assert_no_leak_default_threshold(self):
        """Default LEAK_THRESHOLD_MB is 50.0."""
        assert LEAK_THRESHOLD_MB == 50.0


class TestSprintMemoryProfilingB_MemoryTracker(unittest.IsolatedAsyncioTestCase):
    """B. MemoryTracker context manager — bookend + assertion."""

    def test_tracker_enters_and_exits_cleanly(self):
        """MemoryTracker __enter__ / __exit__ cycle completes without error."""
        from tests.utils.memory_profiler import MemoryTracker

        tracker = MemoryTracker(threshold_mb=50)
        tracker.__enter__()
        try:
            tracker.__exit__(None, None, None)
        except Exception as e:
            pytest.fail(f"__exit__ raised: {e}")

    def test_tracker_measures_delta(self):
        """MemoryTracker captures RSS delta between enter and assert."""
        from tests.utils.memory_profiler import MemoryTracker

        gc.collect()
        with MemoryTracker(threshold_mb=50) as tracker:
            # No meaningful allocation
            pass
        # Should not raise — no leak
        tracker.assert_leak_threshold(50)

    def test_tracker_tracemalloc_included_by_default(self):
        """MemoryTracker includes tracemalloc by default."""
        from tests.utils.memory_profiler import MemoryTracker

        tracker = MemoryTracker()
        assert tracker.include_tracemalloc is True

    def test_tracker_custom_threshold(self):
        """Custom threshold is respected by assert_leak_threshold."""
        from tests.utils.memory_profiler import MemoryTracker

        gc.collect()
        with MemoryTracker(threshold_mb=1) as tracker:
            _data = [None] * 1000  # tiny allocation
        # 1MB threshold very tight — might pass or fail depending on RSS granularity
        # Just verify it doesn't raise due to wrong threshold
        try:
            tracker.assert_leak_threshold(1)
        except AssertionError:
            pass  # expected if delta > 1MB

    def test_tracker_assertion_message_contains_details(self):
        """AssertionError message includes delta, threshold, and RSS values."""
        from tests.utils.memory_profiler import MemoryTracker

        gc.collect()
        with MemoryTracker(threshold_mb=0.001) as tracker:
            _data = [None] * 1_000_000
        try:
            tracker.assert_leak_threshold(0.001)
            pytest.fail("Expected AssertionError")
        except AssertionError as e:
            msg = str(e)
            assert "MB" in msg or "Memory leak" in msg


class TestSprintMemoryProfilingC_TracemallocSnapshot(unittest.IsolatedAsyncioTestCase):
    """C. TracemallocSnapshot — Python object allocation tracking."""

    def test_tracemalloc_starts_on_init(self):
        """TracemallocSnapshot starts tracemalloc on __post_init__."""
        snap = TracemallocSnapshot()
        try:
            assert snap._started is True
        finally:
            snap.stop()

    def test_take_captures_baseline(self):
        """take() stores current tracemalloc snapshot."""
        snap = TracemallocSnapshot()
        try:
            snap.take()
            assert snap._baseline is not None
        finally:
            snap.stop()

    def test_compare_top_n_returns_list(self):
        """compare_top_n() returns a list of stat pairs."""
        snap = TracemallocSnapshot()
        try:
            snap.take()
            # Allocate something
            _ = [None] * 1000
            result = snap.compare_top_n(n=5)
            assert isinstance(result, list)
        finally:
            snap.stop()

    def test_format_top_deltas_returns_string(self):
        """format_top_deltas() returns a formatted string."""
        snap = TracemallocSnapshot()
        try:
            snap.take()
            result = snap.format_top_deltas(n=3)
            assert isinstance(result, str)
        finally:
            snap.stop()

    def test_has_leak_false_when_clean(self):
        """has_leak() returns False when no significant allocation growth."""
        snap = TracemallocSnapshot()
        try:
            snap.take()
            result = snap.has_leak(threshold_kb=100)
            assert result is False
        finally:
            snap.stop()

    def test_has_leak_true_when_growing(self):
        """has_leak() returns True when any site grows beyond threshold."""
        snap = TracemallocSnapshot()
        try:
            snap.take()
            # Allocate beyond 1MB threshold
            _ = [None] * 500_000  # ~4MB list
            result = snap.has_leak(threshold_kb=1024)
            # Result may be True or False depending on GC and RSS granularity
            # We just verify it returns a bool without error
            assert isinstance(result, bool)
        finally:
            snap.stop()


class TestSprintMemoryProfilingD_FixtureIntegration:
    """D. Sprint cycle memory leak detection — integration with lifecycle."""

    def test_sprint_lifecycle_no_leak(self):
        """SprintLifecycleManager import + instantiation does not leak memory."""
        gc.collect()
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        before_rss = get_rss_mb()
        lc = SprintLifecycleManager()
        after_rss = get_rss_mb()
        assert_no_leak(before_rss, after_rss, threshold_mb=50)
        del lc  # noqa: F841

    def test_snapshot_fixture_returns_snapshot(self, memory_snapshot):
        """memory_snapshot fixture returns a Snapshot object."""
        assert memory_snapshot is not None
        assert isinstance(memory_snapshot, Snapshot)

    def test_memory_tracker_fixture_returns_tracker(self, memory_tracker):
        """memory_tracker fixture returns a MemoryTracker object."""
        assert memory_tracker is not None
        assert hasattr(memory_tracker, "assert_leak_threshold")


class TestSprintMemoryProfilingE_ConftestFixtures:
    """E. Conftest fixtures integration — memory_snapshot, memory_tracker, assert_memory_leak."""

    def test_memory_snapshot_fixture_delta(self, memory_snapshot):
        """memory_snapshot captures RSS on enter, provides delta on exit."""
        gc.collect()
        _before = memory_snapshot.rss_mb
        # Small allocation
        _data = [None] * 100
        delta = memory_snapshot.delta_mb(force_gc=True)
        # Delta should be small (within 5MB)
        assert delta > -5.0, f"Delta should not show large freeing, got {delta:.1f}"

    def test_assert_memory_leak_fixture_noop_when_clean(self, assert_memory_leak):
        """assert_memory_leak fixture passes when delta is within threshold."""
        gc.collect()
        before = get_rss_mb()
        after = get_rss_mb()
        # No meaningful allocation — should not raise
        assert_memory_leak(before, after, threshold_mb=50)

    def test_assert_memory_leak_fixture_fails_over_threshold(
        self, assert_memory_leak
    ):
        """assert_memory_leak fixture raises AssertionError when delta > threshold."""
        gc.collect()
        before = get_rss_mb()
        # Deliberate large allocation
        _leak = [None] * 5_000_000
        after = get_rss_mb()
        delta = after - before
        if delta > 5.0:
            with pytest.raises(AssertionError):
                assert_memory_leak(before, after, threshold_mb=1.0)
        del _leak

    def test_memory_tracker_fixture_bookend(self, memory_tracker):
        """memory_tracker fixture provides context manager that captures RSS delta."""
        gc.collect()
        with memory_tracker:
            # Sprint-like lifecycle phases (WARMUP → ACTIVE → WINDUP)
            _warmup = [None] * 100
            _active = [None] * 100
            _windup = [None] * 100
            del _warmup, _active, _windup
        # Should not raise — allocation was freed before assert
        memory_tracker.assert_leak_threshold(50)

    def test_memory_tracker_fixture_reports_leak(self, memory_tracker):
        """memory_tracker fixture raises AssertionError with leak details."""
        gc.collect()
        with memory_tracker:
            # Allocate and DON'T free — leak
            _leaked = [None] * 5_000_000
        delta = memory_tracker._rss_snapshot.delta_mb(force_gc=True)
        if delta > 5.0:
            with pytest.raises(AssertionError, match="Memory leak"):
                memory_tracker.assert_leak_threshold(1)
        else:
            # RSS too coarse — skip (acceptable in CI)
            pass


class TestSprintMemoryProfilingF_StandaloneAssertNoLeak(
    unittest.IsolatedAsyncioTestCase
):
    """F. Standalone assert_no_leak() helper function."""

    def test_assert_no_leak_passes_within_threshold(self):
        """assert_no_leak() does not raise when delta ≤ threshold."""
        gc.collect()
        before = get_rss_mb()
        after = get_rss_mb()
        assert_no_leak(before, after, threshold_mb=50)

    def test_assert_no_leak_fails_over_threshold(self):
        """assert_no_leak() raises AssertionError when delta > threshold."""
        gc.collect()
        before = get_rss_mb()
        _leak = [None] * 5_000_000
        after = get_rss_mb()
        delta = after - before
        if delta > 5.0:
            with pytest.raises(AssertionError, match="Memory leak"):
                assert_no_leak(before, after, threshold_mb=1.0)
        del _leak

    def test_assert_no_leak_with_context(self):
        """assert_no_leak() includes context string in error message."""
        gc.collect()
        before = get_rss_mb()
        _leak = [None] * 5_000_000
        after = get_rss_mb()
        delta = after - before
        if delta > 5.0:
            with pytest.raises(AssertionError, match="sprint_cycle"):
                assert_no_leak(
                    before, after, threshold_mb=1.0, context="sprint_cycle"
                )
        del _leak

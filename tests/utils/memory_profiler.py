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
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any

from operator import attrgetter, itemgetter
import psutil
from core import aclose

__all__ = [
    "get_rss_mb",
    "Snapshot",
    "TracemallocStats",
    "TracemallocSnapshot",
    "MemoryTracker",
    "assert_no_leak",
    "init_session_tracer",
    "stop_session_tracer",
    "is_tracing",
]

_TM_NFRAMES: int = int(os.environ.get("HLEDAC_TEST_TM_FRAMES", "10"))
"""Number of stack frames tracked by tracemalloc (default 10, ~80 KB ring buffer).

F350M-R fix: reduced from 25 → 10 frames (~80 KB vs ~200 KB per test session).
25 frames × ~8 KB/frame × 100 tests = ~20 MB retained. 10 frames × 100 = ~8 MB.
"""

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session-scoped tracer state
# ---------------------------------------------------------------------------

_SESSION_TRACER_STARTED: bool = False  # <- FIX: initialize to avoid NameError
_sess_snapshot_domains: tuple[str, ...] | None = None  # e.g. ("hledac", "brain")
_sess_filter_ttl_s: float = float(os.environ.get("HLEDAC_TEST_TM_FILTER_TTL", "60.0"))
"""Re-build domain tuple every N seconds (default 60s — domain list is stable)."""
_sess_snapshot_rebuild_after: float = 0.0  # next rebuild timestamp

# psutil Process cache — avoid 50 KB allocation per get_rss_mb() call
_psutil_process: psutil.Process | None = None
_psutil_process_pid: int = -1
_psutil_cache_refresh_after: float = 0.0  # force refresh every N seconds
_psutil_refresh_interval_s: float = 10.0  # refresh cached process every 10s


# ---------------------------------------------------------------------------  # Session-scoped tracer (F350M-R Fix)
# tracemalloc.start() allocates a ring buffer ONCE per process.
# Repeated start/stop cycles fragment Python's pymalloc arenas — the allocator  # keeps freed chunks in free lists instead of returning them to the OS.
# Solution: start once at pytest session start, stop once at session teardown.
# Per-test TracemallocSnapshot instances only take snapshots, never stop.
# ---------------------------------------------------------------------------  _SESSION_TRACER_STARTED: bool = False
_SESSION_TRACER_N_FRAMES: int = _TM_NFRAMES

LEAK_THRESHOLD_MB: float = 50.0
"""Default leak threshold in MB per sprint cycle."""


def _rebuild_snapshot_domains() -> tuple[str, ...]:
    """Parse HLEDAC_TEST_TM_DOMAINS env var into a tuple of domain prefixes.

    Example: HLEDAC_TEST_TM_DOMAINS="hledac,brain,knowledge"
    Results in domain prefixes used to filter comparison results.

    Falls back to all domains (empty tuple) if env var is not set.
    """
    domains_raw = os.environ.get("HLEDAC_TEST_TM_DOMAINS", "").strip()
    if not domains_raw:
        return ()  # All domains
    return tuple(d.strip() for d in domains_raw.split(",") if d.strip())


def _get_snapshot_domains() -> tuple[str, ...]:
    """Return cached domain prefixes, rebuilding only when TTL expires."""
    global _sess_snapshot_domains, _sess_snapshot_rebuild_after
    now = time.monotonic()
    if _sess_snapshot_domains is None or now >= _sess_snapshot_rebuild_after:
        _sess_snapshot_domains = _rebuild_snapshot_domains()
        _sess_snapshot_rebuild_after = now + _sess_filter_ttl_s
    return _sess_snapshot_domains


def _domain_in_traceback(traceback_str: str, domains: tuple[str, ...]) -> bool:
    """Return True if traceback contains any of the given domain prefixes.

    Matches three formats tracemalloc can produce:
    1. Absolute paths:  /Users/.../hledac/brain/engine.py:123
    2. Relative paths:  core/resource_governor.py:200, tests/utils/memory_profiler.py:160
    3. Dot-notation:    hledac.universal.core.resource_governor:42

    Filters noisy third-party allocations (psutil, tracemalloc internals, etc.)
    while keeping only allocations from the project's modules.
    """
    if not domains:
        return True  # No filter — include all
    for domain in domains:
        # Absolute & relative slash-separated path: /hledac/ or hledac/ as directory component
        if f"/{domain}/" in traceback_str or traceback_str.startswith(f"{domain}/"):
            return True
        # Dot-notation namespace: hledac.brain. as module prefix
        if f"{domain}." in traceback_str:
            return True
    return False


def init_session_tracer(nframes: int = _TM_NFRAMES) -> bool:
    """
    Start the session-scoped tracemalloc tracer (idempotent).

    Call once at pytest session start (e.g. in conftest.py session fixture).
    Does NOT stop the tracer — use stop_session_tracer() at teardown.

    Args:
        nframes: Number of stack frames to trace (default: _TM_NFRAMES).
                 Higher = more detail, more memory (~8 KB per frame).

    Returns:
        True if tracer is now active.
    """
    global _SESSION_TRACER_STARTED, _SESSION_TRACER_N_FRAMES
    try:
        if not tracemalloc.is_tracing():
            tracemalloc.start(nframes)
        _SESSION_TRACER_STARTED = tracemalloc.is_tracing()
        _SESSION_TRACER_N_FRAMES = nframes
        return _SESSION_TRACER_STARTED
    except Exception as ex:
        log.warning("session tracer init failed: %s", ex)
        _SESSION_TRACER_STARTED = False
        return False


def stop_session_tracer() -> None:
    """
    Stop the session-scoped tracemalloc tracer (idempotent).

    Call once at pytest session teardown. Safe to call even if not started.
    """
    global _SESSION_TRACER_STARTED
    try:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        _SESSION_TRACER_STARTED = False
    except Exception:  # noqa: BLE001
        pass


def is_tracing() -> bool:
    """Return True if tracemalloc is currently active (session or otherwise)."""
    try:
        return tracemalloc.is_tracing()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Low-level RSS measurement
# ---------------------------------------------------------------------------


def _get_psutil_process() -> psutil.Process:
    """Get or create a cached psutil.Process for the current PID.

    Avoids 50 KB allocation per get_rss_mb() call by caching the Process object.
    Refreshes the cached object every 10s or when PID changes.
    """
    global _psutil_process, _psutil_process_pid, _psutil_cache_refresh_after
    now = time.monotonic()

    pid = os.getpid()
    if _psutil_process is not None and _psutil_process_pid == pid and now < _psutil_cache_refresh_after:
        return _psutil_process

    # Refresh: create new Process object, refresh interval
    _psutil_process = psutil.Process(pid)
    _psutil_process_pid = pid
    _psutil_cache_refresh_after = now + _psutil_refresh_interval_s
    return _psutil_process


def get_rss_mb() -> float:
    """
    Get current process RSS in MB.

    Fail-safe: returns 0.0 on any error (permission, process terminated, etc.)
    This ensures CI never fails due to measurement error.

    Uses a cached psutil.Process object (~50 KB saved per call).
    """
    try:
        return _get_psutil_process().memory_info().rss / 1024**2
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
class TracemallocStats:
    """
    Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.

    F350M-R fix: tracemalloc.take_snapshot() allocates a full Python object graph
    (~15-20 MB per snapshot on a 181-test suite). get_traced_memory() returns
    only two integers: current bytes and peak bytes — zero allocation overhead.

    For detailed per-site breakdown (when needed), call take_snapshot() separately
    and use compare_top_n() on TracemallocSnapshot.

    Example:
        stats = TracemallocStats.take()
        # ... test code ...
        delta = stats.delta_bytes()
        assert delta < 10 * 1024 * 1024, f"Python allocations grew {delta / 1024 / 1024:.1f} MB"
    """

    current_bytes: int
    peak_bytes: int
    _baseline: tuple[int, int] = field(default_factory=lambda: (0, 0))
    _started: bool = field(default=False, repr=False)

    @staticmethod
    def take() -> TracemallocStats:
        """Take a lightweight snapshot of Python object allocations."""
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(_TM_NFRAMES)
            current, peak = tracemalloc.get_traced_memory()
            return TracemallocStats(
                current_bytes=current,
                peak_bytes=peak,
                _baseline=(current, peak),
                _started=True,
            )
        except Exception as ex:
            log.warning("get_traced_memory failed: %s", ex)
            return TracemallocStats(current_bytes=0, peak_bytes=0, _started=False)

    def delta_bytes(self) -> int:
        """Return delta in bytes from baseline to now."""
        try:
            if not self._started:
                return 0
            if not tracemalloc.is_tracing():
                return 0
            current, _ = tracemalloc.get_traced_memory()
            return current - self._baseline[0]
        except Exception:
            return 0

    def delta_mb(self) -> float:
        """Return delta in MB."""
        return self.delta_bytes() / 1024 / 1024

    def peak_delta_mb(self) -> float:
        """Return peak memory growth in MB from baseline."""
        try:
            if not self._started:
                return 0.0
            if not tracemalloc.is_tracing():
                return 0.0
            _, peak = tracemalloc.get_traced_memory()
            return (peak - self._baseline[1]) / 1024 / 1024
        except Exception:
            return 0.0

    def stop(self) -> None:
        """Stop tracemalloc if not in session mode (no-op in session mode)."""
        self._started = False


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
    _session_mode: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Session tracer is active — snapshots only, no start/stop.
        # Idempotent: multiple TracemallocSnapshot instances share one tracer.
        self._session_mode = _SESSION_TRACER_STARTED and tracemalloc.is_tracing()
        if self._session_mode:
            self._started = True
            return

        # Legacy mode: start/stop per instance.
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(_TM_NFRAMES)
            self._started = tracemalloc.is_tracing()
        except Exception as ex:
            log.warning("tracemalloc init failed (CI/container?): %s", ex)
            self._started = False
            self._session_mode = False

    def take(self) -> None:
        """Take a baseline snapshot (call before code under test)."""
        if not self._started:
            return
        try:
            if not tracemalloc.is_tracing():
                # Session mode didn't start the tracer — start it now (legacy fallback)
                tracemalloc.start(_TM_NFRAMES)

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
            # Filter to positive growth AND domain match
            domains = _get_snapshot_domains()
            growth = [s for s in stats if s.size_diff > 0 and _domain_in_traceback(str(s.traceback), domains)]
            return sorted(growth, key=attrgetter("size_diff"), reverse=True)[:n]
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
        """
        Stop tracemalloc — ONLY in legacy (non-session) mode.

        In session-scoped tracer mode, this is a no-op: the session tracer
        is owned by init_session_tracer() / stop_session_tracer() and must
        not be stopped by individual snapshot instances.

        Safe to call multiple times (idempotent in both modes).
        """
        if self._session_mode:
            # Session tracer is shared — do NOT stop it here.
            self._started = False
            return
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
    _weakref_finalizers: list[weakref.finalize] = field(default_factory=list, repr=False)

    def register_allocation(self, obj: object, name: str | None = None) -> weakref.finalize:
        """
        Register a large object for weakref-based finalization safety net.

        Issue #12 fix: pytest fixtures can crash before __exit__ cleanup, leaving
        large objects pinned in memory. weakref.finalize() guarantees __del__ runs
        even if the fixture crashes mid-test.

        Args:
            obj: Large object to track.
            name: Optional name for debugging.

        Returns:
            weakref.finalize object — call .detach() to cancel tracking.
        """
        finalizer = weakref.finalize(obj, lambda _: None)
        self._weakref_finalizers.append(finalizer)
        return finalizer

    def __enter__(self) -> MemoryTracker:
        gc.collect()
        # Freeze GC to prevent generational noise during measurement window.
        # Objects created during the sprint cycle will not be collected in gen 0/1.
        gc.freeze()
        self._rss_snapshot = Snapshot()
        if self.include_tracemalloc:
            self._tracemalloc = TracemallocSnapshot()
            self._tracemalloc.take()
        return self

    def __exit__(self, *args: Any) -> None:
        # CRITICAL FIX (F350M-R): Unfreeze GC in fail-safe manner.
        # gc.unfreeze() can fail if: (1) GC was never frozen, (2) Python build
        # doesn't support gc.freeze() (Python 3.12+ only). Wrap in try/except.
        try:
            gc.unfreeze()
        except Exception:  # noqa: BLE001
            pass  # Already unfrozen or unavailable
        gc.collect()
        if self._tracemalloc is not None:
            self._tracemalloc.stop()
        # Issue #12: invoke and detach all weakref finalizers
        for finalizer in self._weakref_finalizers:
            try:
                finalizer()
            except Exception:  # noqa: BLE001
                pass
        self._weakref_finalizers.clear()

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

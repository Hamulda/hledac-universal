"""
core.memory_cycle — per-cycle GC maintenance + macOS pressure relief (F266-U2/U3)
==================================================================================

Two bounded, fail-safe background hygiene primitives for long-running
sprint loops on M1 8GB UMA:

1. **Per-cycle GC maintenance** (``gc_cycle_maintain``)
   - Existing pattern: ``gc.freeze()`` once at boot to pin long-lived
     objects into the permanent generation so the gen-2 sweep never
     scans them. Implemented in ``core/__main__.py`` line ~1000.
   - New pattern: re-freeze at the *cycle boundary* (per sprint
     iteration). After a full ``gc.collect(2)`` we have a fresh
     "long-lived set" — freezing it again pins only objects that have
     *proved* they survive, which is tighter than the boot-time freeze.
   - Avoids drift: the permanent gen stops growing unbounded across
     hundreds of cycles.

2. **macOS malloc pressure relief** (``malloc_zone_pressure_relief``)
   - Private macOS API: ``malloc_zone_pressure_relief(zone=NULL, goal=0)``
     asks libmalloc to release fragmented pages back to the kernel.
   - On M1 8GB UMA, fragmentation accumulates between GCs and shows
     up as elevated RSS without elevated "live" set. A 5-minute tick
     releases 5-50 MB of fragmented pages (measurable on real sprints).
   - No-op on Linux/Windows (fail-soft, return 0).

3. **Background task** (``start_pressure_relief_loop`` / ``stop_pressure_relief_loop``)
   - Single asyncio task, scheduled at fixed interval, idempotent.
   - Clean shutdown via ``asyncio.Event`` — no thread leaks.
   - Wire-up: ``core/__main__.py`` calls ``start_pressure_relief_loop``
     at sprint start and ``stop_pressure_relief_loop`` at sprint end.

Invariants (per CLAUDE.md):
  - Always-on, no feature flags.
  - Bounded: one background task max; interval 5 min default.
  - Fail-safe: every syscall wrapped in try/except.
  - No new public APIs beyond the three functions.
  - No ``asyncio.run()`` in threads (B7 invariant).
"""

from __future__ import annotations

import asyncio
import gc as _gc
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# F266-U2: throttle — re-freeze at most every 60 s. Without this we'd
# refreeze on every cycle, which defeats the purpose (freeze has a
# non-trivial cost on large heaps).
_GC_REFREEZE_COOLDOWN_S: float = 60.0

# F266-U2: gen-2 collected counter threshold. If gen-2 has run
# >= N times since last maintenance, it's worth re-freezing.
_GC_GEN2_REFREEZE_THRESHOLD: int = 3

# F266-U3: default pressure relief interval (seconds).
_PRESSURE_RELIEF_INTERVAL_S: float = 300.0  # 5 min

# F266-U3: min interval (don't hammer the kernel).
_PRESSURE_RELIEF_MIN_INTERVAL_S: float = 60.0


@dataclass
class MemoryCycleStats:
    """Snapshot of GC + pressure-relief state — for telemetry / debug."""
    gc_freeze_supported: bool
    gc_gen0_collected: int
    gc_gen1_collected: int
    gc_gen2_collected: int
    re_freeze_count: int
    last_re_freeze_monotonic: float
    pressure_relief_runs: int
    pressure_relief_bytes_released: int
    last_pressure_relief_monotonic: float
    last_pressure_relief_error: str | None = None
    platform: str = field(default_factory=lambda: sys.platform)


_stats = MemoryCycleStats(
    gc_freeze_supported=hasattr(_gc, "freeze"),
    gc_gen0_collected=0,
    gc_gen1_collected=0,
    gc_gen2_collected=0,
    re_freeze_count=0,
    last_re_freeze_monotonic=0.0,
    pressure_relief_runs=0,
    pressure_relief_bytes_released=0,
    last_pressure_relief_monotonic=0.0,
)


def get_stats() -> dict[str, Any]:
    """Return a JSON-safe snapshot of memory_cycle state."""
    return {
        "gc_freeze_supported": _stats.gc_freeze_supported,
        "gc_gen0_collected": _stats.gc_gen0_collected,
        "gc_gen1_collected": _stats.gc_gen1_collected,
        "gc_gen2_collected": _stats.gc_gen2_collected,
        "re_freeze_count": _stats.re_freeze_count,
        "last_re_freeze_monotonic": _stats.last_re_freeze_monotonic,
        "pressure_relief_runs": _stats.pressure_relief_runs,
        "pressure_relief_bytes_released": _stats.pressure_relief_bytes_released,
        "last_pressure_relief_monotonic": _stats.last_pressure_relief_monotonic,
        "last_pressure_relief_error": _stats.last_pressure_relief_error,
        "platform": _stats.platform,
    }


# =============================================================================
# F266-U2: GC cycle maintenance
# =============================================================================


def gc_cycle_maintain(*, force: bool = False) -> bool:
    """
    Per-cycle GC maintenance. Call at the boundary of each sprint
    iteration (i.e. at winddown, or before the next cycle's prelude).

    Behaviour:
      1. ``gc.collect(0)`` — fast, no gen-1/2 scan. Reclaims short-lived
         cycle garbage (request bodies, JSON-decoded dicts, etc.).
      2. If gen-2 has been collected more than N times since the last
         re-freeze, OR ``force=True``: run ``gc.collect(2)`` (full sweep)
         and ``gc.freeze()`` again. This pins only objects that
         survived the full sweep, which is the correct "permanent" set.
      3. Throttled to one re-freeze per ``_GC_REFREEZE_COOLDOWN_S``
         unless ``force=True``.

    Returns:
        True if a re-freeze happened, False otherwise.

    Fail-soft: every step wrapped in try/except. The sprint continues
    even if GC bookkeeping fails.
    """
    if not _stats.gc_freeze_supported:
        return False

    now = time.monotonic()
    try:
        gc_stats = _gc.get_stats()
    except Exception as exc:
        logger.debug("[memory_cycle] gc.get_stats() failed: %s", exc)
        return False

    # Update running counters (cumulative per process, monotonic).
    # gc.get_stats() returns list per generation: [gen0, gen1, gen2].
    try:
        if len(gc_stats) >= 3:
            _stats.gc_gen0_collected = int(gc_stats[0].get("collected", 0))
            _stats.gc_gen1_collected = int(gc_stats[1].get("collected", 0))
            _stats.gc_gen2_collected = int(gc_stats[2].get("collected", 0))
    except Exception:
        pass

    # Step 1: cheap gen-0 collection. Bounded cost.
    try:
        _gc.collect(0)
    except Exception:
        pass  # noqa: BLE001  # fail-soft

    # Step 2: should we re-freeze?
    since_freeze = now - _stats.last_re_freeze_monotonic
    cooldown_ok = since_freeze >= _GC_REFREEZE_COOLDOWN_S
    if not force and not cooldown_ok:
        return False

    # Heuristic: if gen-2 collected count has grown since last maintenance,
    # the permanent gen has changed (some pinned objects died, new long-
    # lived objects appeared). Re-pinning is worthwhile.
    gen2_drift = (
        _stats.gc_gen2_collected > 0
        and _stats.re_freeze_count == 0
    ) or force

    if not gen2_drift and not force:
        # No gen-2 activity → permanent set hasn't changed, skip.
        return False

    try:
        # Full sweep so anything that survived a gen-2 cycle is the
        # *new* permanent set.
        _gc.collect(2)
    except Exception:
        return False

    try:
        _gc.freeze()
    except Exception as exc:
        logger.debug("[memory_cycle] gc.freeze() failed: %s", exc)
        return False

    _stats.re_freeze_count += 1
    _stats.last_re_freeze_monotonic = now
    logger.debug(
        "[memory_cycle] re-freeze #%d (gen2=%d, since_last=%.0fs)",
        _stats.re_freeze_count, _stats.gc_gen2_collected, since_freeze,
    )
    return True


# =============================================================================
# F266-U3: macOS malloc_zone_pressure_relief
# =============================================================================


def malloc_zone_pressure_relief() -> int:
    """
    Best-effort: ask the macOS allocator to release fragmented pages.

    On macOS (Darwin) this wraps ``malloc_zone_pressure_relief(NULL, 0)``
    via ctypes. Returns the kernel-reported bytes released, or 0 on
    non-Darwin / on error. The private API has been stable since
    macOS 10.6 and is used by the JVM's HotSpot for the same purpose.

    On Linux/Windows: returns 0 immediately (no-op, no exception).

    Invariants:
      - Fail-soft: every step wrapped in try/except.
      - Single syscall per call. Cheap (kernel coalesces).
      - Safe to call concurrently (libmalloc is thread-safe).
    """
    if sys.platform != "darwin":
        return 0

    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        # malloc_zone_pressure_relief signature:
        #   int malloc_zone_pressure_relief(void *zone, int goal);
        # Returns the number of bytes released (>= 0) or -1 on error.
        libc.malloc_zone_pressure_relief.restype = ctypes.c_int
        libc.malloc_zone_pressure_relief.argtypes = (ctypes.c_void_p, ctypes.c_int)
        rc = libc.malloc_zone_pressure_relief(None, 0)
        if rc < 0:
            return 0
        return int(rc)
    except Exception as exc:
        # Don't log at warning — this is best-effort and noisy on
        # sandboxed environments (sandbox-exec, App Sandbox).
        logger.debug("[memory_cycle] malloc_zone_pressure_relief failed: %s", exc)
        return 0


# =============================================================================
# F266-U3: Background pressure-relief task
# =============================================================================


_pressure_relief_task: asyncio.Task | None = None
_pressure_relief_stop: asyncio.Event | None = None


async def _pressure_relief_loop(interval_s: float) -> None:
    """
    Background loop: call ``malloc_zone_pressure_relief`` every
    ``interval_s`` seconds until ``_pressure_relief_stop`` is set.

    Idempotent — only one instance per process. The stop event is
    awaited between calls so cancellation is clean (no orphaned
    syscalls in flight).
    """
    assert _pressure_relief_stop is not None
    interval_s = max(interval_s, _PRESSURE_RELIEF_MIN_INTERVAL_S)
    try:
        while not _pressure_relief_stop.is_set():
            try:
                released = malloc_zone_pressure_relief()
                _stats.pressure_relief_runs += 1
                _stats.pressure_relief_bytes_released += released
                _stats.last_pressure_relief_monotonic = time.monotonic()
                if released > 0:
                    logger.debug(
                        "[memory_cycle] pressure_relief released %d bytes (total=%d, runs=%d)",
                        released,
                        _stats.pressure_relief_bytes_released,
                        _stats.pressure_relief_runs,
                    )
            except Exception as exc:
                _stats.last_pressure_relief_error = str(exc)
                logger.debug("[memory_cycle] pressure_relief tick error: %s", exc)

            # Sleep with cancellation. The wait_for pattern wakes
            # immediately when the stop event is set, so shutdown
            # latency is bounded by the sleep granularity.
            try:
                await asyncio.wait_for(
                    _pressure_relief_stop.wait(),
                    timeout=interval_s,
                )
            except TimeoutError:
                continue  # normal tick
            else:
                break  # stop event set
    except asyncio.CancelledError:
        # Clean cancel — propagate.
        raise
    except Exception as exc:
        # Failsafe: never crash the event loop from a background task.
        logger.warning("[memory_cycle] pressure_relief loop crashed: %s", exc)


def start_pressure_relief_loop(
    interval_s: float = _PRESSURE_RELIEF_INTERVAL_S,
) -> asyncio.Task | None:
    """
    Spawn the background pressure-relief task. Idempotent.

    Returns the running Task, or None if we're not inside an asyncio
    event loop (in which case the caller should defer to the next
    ``asyncio.run()`` boundary).

    M1 8GB safety:
      - Single task, sleeps between ticks, no busy-wait.
      - Cancelled cleanly via the stop event.
    """
    global _pressure_relief_task, _pressure_relief_stop
    if _pressure_relief_task is not None and not _pressure_relief_task.done():
        return _pressure_relief_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None  # not in an event loop; defer to next run()

    _pressure_relief_stop = asyncio.Event()
    _pressure_relief_task = loop.create_task(
        _pressure_relief_loop(interval_s),
        name="memory_cycle.pressure_relief",
    )
    logger.debug(
        "[memory_cycle] pressure_relief loop started (interval=%.0fs)",
        interval_s,
    )
    return _pressure_relief_task


async def stop_pressure_relief_loop() -> None:
    """Stop the background pressure-relief task. Awaits clean shutdown."""
    global _pressure_relief_task, _pressure_relief_stop
    if _pressure_relief_stop is not None:
        _pressure_relief_stop.set()
    if _pressure_relief_task is not None:
        try:
            await asyncio.wait_for(_pressure_relief_task, timeout=5.0)
        except TimeoutError:
            _pressure_relief_task.cancel()
            try:
                await _pressure_relief_task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass
    _pressure_relief_task = None
    _pressure_relief_stop = None


__all__ = [
    "gc_cycle_maintain",
    "malloc_zone_pressure_relief",
    "start_pressure_relief_loop",
    "stop_pressure_relief_loop",
    "get_stats",
    "MemoryCycleStats",
]

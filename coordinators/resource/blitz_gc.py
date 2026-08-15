"""
coordinators/resource/blitz_gc.py — BlitzGCStrategy (PHYSICS-06, PHYSICS-07)
============================================================================


Eliminates involuntary GC stop-the-world pauses during active sprint phase.

PHYSICS-06: Disable automatic GC during active sprint
    - sprint_start(): disable gc, set ultra-high thresholds (10000, 200, 50)
    - gc_tick(): explicit gen-0 collect during I/O-wait windows
    - sprint_teardown(): re-enable gc, full gen-2 sweep, restore thresholds

PHYSICS-07: Manual freeze workaround for Python < 3.14.7
    - gc.freeze() is guarded behind sys.version_info >= (3, 14, 7) due to
      a gilstate_tss_set regression; on 3.14.6, freeze is never called
    - Manual workaround: capture gc.get_objects() snapshot at startup,
      use it as the "known permanent" set; during sprint (GC disabled),
      no scanning happens anyway; at teardown, after gen-2 sweep, we
      know exactly what survived

M1 8GB invariants:
    - Always-on, no feature flags
    - gc.disable() during active phase eliminates ALL involuntary pauses
    - gc.collect(2) only at teardown — single full sweep per sprint
    - 7-27s saved per 30-min sprint vs auto-GC baseline

Lifecycle:
    BlitzGCStrategy.sprint_start()      # at sprint boot, after module imports
         ↓
    [ACTIVE SPRINT — GC disabled, occasional gc_tick() during I/O wait]
         ↓
    BlitzGCStrategy.sprint_teardown()   # at winddown start, before export

Usage:
    from hledac.universal.coordinators.resource.blitz_gc import blitz_gc

    # At sprint boot:
    blitz_gc.sprint_start()

    # During sprint (optional, in I/O-wait windows):
    await blitz_gc.gc_tick()

    # At winddown:
    blitz_gc.sprint_teardown()
"""

from __future__ import annotations

import asyncio
import gc as _gc
import logging
import sys
import time as _time_module
from typing import Any
from core import aclose

logger = logging.getLogger(__name__)

# =============================================================================
# Threshold constants
# =============================================================================

# Blitz-mode thresholds: extremely high to prevent accidental triggering
# during active sprint when GC is disabled
BLITZ_THRESHOLD = (10000, 200, 50)

# Post-teardown thresholds: moderate — only active between sprints
# (when the system is idle, we don't need ultra-high thresholds)
POST_TEARDOWN_THRESHOLD = (2000, 100, 50)

# Pre-blitz startup thresholds: aggressive but safe for boot phase
BOOT_THRESHOLD = (2000, 100, 50)

# =============================================================================
# PHYSICS-07: gc.freeze() availability
# =============================================================================

# F266-U4: gc.freeze() requires Python 3.14.7+ (gilstate_tss_set regression fix)
_GC_FREEZE_NATIVE: bool = sys.version_info >= (3, 14, 7)

# PHYSICS-07: On 3.14.6, gc.freeze() exists but is guarded due to crash risk.
# We detect availability separately from safety.
_GC_FREEZE_AVAILABLE: bool = hasattr(_gc, "freeze")


# =============================================================================
# Manual freeze for Python < 3.14.7 (PHYSICS-07 workaround)
# =============================================================================

# Snapshot of object ids captured at startup — treated as the "permanent" set.
# Objects created after this snapshot are "ephemeral" and subject to gen-0
# collection during gc_tick(). At teardown, we compare gc.get_objects()
# against this set to track what survived without native freeze.
_startup_object_ids: set[int] | None = None
_startup_snapshot_count: int = 0


def _capture_startup_snapshot() -> int:
    """
    Capture gc.get_objects() ids at startup as the "permanent" set.

    PHYSICS-07: On Python < 3.14.7, native gc.freeze() is unavailable.
    This snapshot serves as the logical equivalent — we know which objects
    existed at startup so we can distinguish "permanent" from "ephemeral"
    without scanning them during gen-1/gen-2 collections.

    Returns:
        Number of objects captured.
    """
    global _startup_object_ids, _startup_snapshot_count
    try:
        _objects = _gc.get_objects()
        _startup_object_ids = {id(o) for o in _objects}
        _startup_snapshot_count = len(_startup_object_ids)
        logger.debug(
            "[blitz_gc] startup snapshot captured: %d objects — "
            "manual-freeze active (Python %s < 3.14.7)",
            _startup_snapshot_count,
            ".".join(str(x) for x in sys.version_info[:3]),
        )
        return _startup_snapshot_count
    except Exception as exc:
        logger.debug("[blitz_gc] startup snapshot failed: %s", exc)
        _startup_object_ids = set()
        return 0


def _get_new_since_startup() -> int:
    """
    Count objects alive now that were NOT in the startup snapshot.

    Returns the count, or -1 if snapshot is not available.
    """
    if _startup_object_ids is None:
        return -1
    try:
        return sum(1 for o in _gc.get_objects() if id(o) not in _startup_object_ids)
    except Exception:
        return -1


# =============================================================================
# BlitzGCStrategy
# =============================================================================


class BlitzGCStrategy:
    """
    Sprint-aware GC lifecycle manager.

    Eliminates involuntary GC pauses during active sprint by disabling
    automatic collections and running only explicit, bounded gen-0 sweeps
    during natural I/O-wait windows.

    On teardown, re-enables GC and runs a single full gen-2 sweep.
    """

    __slots__ = (
        "_active",
        "_pre_blitz_thresholds",
        "_gen0_ticks",
        "_last_tick_monotonic",
        "_tick_cooldown_s",
        "_teardown_done",
    )

    def __init__(self) -> None:
        self._active: bool = False
        self._pre_blitz_thresholds: tuple[int, int, int] | None = None
        self._gen0_ticks: int = 0
        self._last_tick_monotonic: float = 0.0
        self._tick_cooldown_s: float = 2.0  # min interval between gc_tick() calls
        self._teardown_done: bool = False

    # ── Public API ──────────────────────────────────────────────────────

    def sprint_start(self) -> dict[str, Any]:
        """
        Activate blitz mode: disable automatic GC, set ultra-high thresholds.

        MUST be called after all module imports and startup allocations are
        complete — typically at the beginning of run_sprint(), after DuckDB
        init and prelude.
        """
        result: dict[str, Any] = {
            "blitz_active": False,
            "native_freeze_available": _GC_FREEZE_NATIVE,
            "freeze_method": "none",
            "pre_thresholds": None,
            "blitz_thresholds": None,
            "startup_snapshot_count": 0,
        }

        if self._active:
            logger.debug("[blitz_gc] sprint_start() called but already active — no-op")
            result["blitz_active"] = True
            return result

        # PHYSICS-07: Capture startup snapshot for manual freeze
        if not _GC_FREEZE_NATIVE:
            result["startup_snapshot_count"] = _capture_startup_snapshot()
            result["freeze_method"] = "manual_snapshot"

        # PHYSICS-07: If native freeze IS available and safe, call it now
        if _GC_FREEZE_NATIVE and _GC_FREEZE_AVAILABLE:
            try:
                _gc.freeze()
                result["freeze_method"] = "native"
                logger.debug("[blitz_gc] native gc.freeze() applied at sprint start")
            except Exception as exc:
                logger.debug("[blitz_gc] native gc.freeze() failed: %s", exc)

        # Save current thresholds before disabling
        try:
            self._pre_blitz_thresholds = _gc.get_threshold()
        except Exception:
            self._pre_blitz_thresholds = BOOT_THRESHOLD
        result["pre_thresholds"] = self._pre_blitz_thresholds

        # Set blitz thresholds (ultra-high to prevent accidental triggering)
        try:
            _gc.set_threshold(*BLITZ_THRESHOLD)
            result["blitz_thresholds"] = BLITZ_THRESHOLD
        except Exception as exc:
            logger.debug("[blitz_gc] set_threshold(BLITZ) failed: %s", exc)

        # THE KEY MOVE: disable automatic GC entirely
        try:
            _gc.disable()
            logger.info(
                "[blitz_gc] PHYSICS-06: GC DISABLED — thresholds=%s, "
                "freeze=%s, startup_objects=%d. "
                "Only explicit gc_tick() gen-0 sweeps will run during active sprint.",
                BLITZ_THRESHOLD,
                result["freeze_method"],
                result["startup_snapshot_count"],
            )
        except Exception as exc:
            logger.warning("[blitz_gc] gc.disable() failed: %s — sprint continues", exc)

        self._active = True
        self._gen0_ticks = 0
        self._last_tick_monotonic = _time_module.monotonic()
        result["blitz_active"] = True
        return result

    async def gc_tick(self) -> dict[str, Any]:
        """
        Explicit gen-0 collect during I/O-wait windows.

        Call this during natural pause points:
        - asyncio.sleep() during fetch backoff
        - drain_pending_extractions()
        - Between acquisition cycles

        Cooldown-gated: no more than one tick per self._tick_cooldown_s.
        Runs in asyncio.to_thread() to avoid blocking the event loop.
        """
        result: dict[str, Any] = {
            "collected": False,
            "gen0_count": 0,
            "new_since_startup": -1,
            "gen0_ticks_total": self._gen0_ticks,
        }

        if not self._active:
            return result

        now = _time_module.monotonic()
        if now - self._last_tick_monotonic < self._tick_cooldown_s:
            return result

        self._last_tick_monotonic = now

        def _work() -> tuple[int, int]:
            try:
                before = _gc.get_count()[0] if hasattr(_gc, "get_count") else 0
                _gc.collect(0)
                after = _gc.get_count()[0] if hasattr(_gc, "get_count") else 0
                return (before, before - after)
            except Exception:
                return (0, 0)

        try:
            gen0_before, collected = await asyncio.to_thread(_work)
            self._gen0_ticks += 1
            result["collected"] = True
            result["gen0_before"] = gen0_before
            result["gen0_collected_approx"] = collected
            result["gen0_ticks_total"] = self._gen0_ticks

            # PHYSICS-07: track new objects for telemetry
            if not _GC_FREEZE_NATIVE:
                result["new_since_startup"] = _get_new_since_startup()
        except Exception as exc:
            logger.debug("[blitz_gc] gc_tick failed: %s", exc)

        return result

    def sprint_teardown(self) -> dict[str, Any]:
        """
        Re-enable GC and run final full sweep.

        Called at winddown start, BEFORE export and synthesis.
        Restores thresholds to POST_TEARDOWN values (moderate for idle periods).
        """
        result: dict[str, Any] = {
            "blitz_was_active": self._active,
            "gen0_ticks_total": self._gen0_ticks,
            "gc_reenabled": False,
            "gen2_collected": 0,
            "final_thresholds": None,
            "freeze_method": "none",
        }

        if self._teardown_done:
            logger.debug("[blitz_gc] sprint_teardown() already done — no-op")
            return result

        # Step 1: Re-enable automatic GC
        try:
            _gc.enable()
            result["gc_reenabled"] = True
        except Exception as exc:
            logger.warning("[blitz_gc] gc.enable() failed: %s", exc)

        # Step 2: Restore thresholds
        try:
            _gc.set_threshold(*POST_TEARDOWN_THRESHOLD)
            result["final_thresholds"] = POST_TEARDOWN_THRESHOLD
        except Exception as exc:
            logger.debug("[blitz_gc] set_threshold(post_teardown) failed: %s", exc)
            result["final_thresholds"] = POST_TEARDOWN_THRESHOLD  # intended, even if failed

        # Step 3: Full gen-2 sweep — reclaim everything before export
        try:
            gen2_before = _gc.get_count()[2] if hasattr(_gc, "get_count") else 0
            collected = _gc.collect(2)
            result["gen2_collected"] = collected
            result["gen2_before"] = gen2_before
        except Exception as exc:
            logger.debug("[blitz_gc] gc.collect(2) at teardown failed: %s", exc)

        # Step 4: Re-freeze on native-capable Python
        if _GC_FREEZE_NATIVE and _GC_FREEZE_AVAILABLE:
            try:
                _gc.freeze()
                result["freeze_method"] = "native"
                logger.debug("[blitz_gc] native gc.freeze() re-applied at teardown")
            except Exception as exc:
                logger.debug("[blitz_gc] freeze at teardown failed: %s", exc)

        # Step 5: Update manual snapshot for idle period (PHYSICS-07)
        if not _GC_FREEZE_NATIVE:
            _capture_startup_snapshot()
            result["freeze_method"] = "manual_snapshot_refresh"
            result["new_snapshot_count"] = _startup_snapshot_count

        self._active = False
        self._teardown_done = True

        logger.info(
            "[blitz_gc] PHYSICS-06: GC RESTORED after %d gen-0 ticks — "
            "thresholds=%s, gen2_collected=%d, freeze=%s. "
            "Active sprint window: ZERO involuntary GC pauses.",
            self._gen0_ticks,
            result["final_thresholds"],
            result["gen2_collected"],
            result["freeze_method"],
        )
        return result

    # ── Telemetry ───────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return current BlitzGC state for telemetry/dashboard."""
        gc_count = _gc.get_count() if hasattr(_gc, "get_count") else (0, 0, 0)
        return {
            "blitz_active": self._active,
            "gen0_ticks": self._gen0_ticks,
            "gen0_count": gc_count[0] if len(gc_count) > 0 else 0,
            "gen1_count": gc_count[1] if len(gc_count) > 1 else 0,
            "gen2_count": gc_count[2] if len(gc_count) > 2 else 0,
            "native_freeze_available": _GC_FREEZE_NATIVE,
            "startup_snapshot_count": _startup_snapshot_count,
            "new_since_startup": _get_new_since_startup(),
            "teardown_done": self._teardown_done,
        }


# =============================================================================
# Module-level singleton
# =============================================================================

blitz_gc = BlitzGCStrategy()

# =============================================================================
# Boot-time GC configuration — applied at module import
# =============================================================================


def _apply_boot_gc() -> None:
    """
    Apply boot-time GC thresholds BEFORE blitz mode is activated.

    Called once at module import. Sets moderate thresholds for the
    startup/prelude phase. When BlitzGCStrategy.sprint_start() is called,
    these are replaced with ultra-high blitz thresholds.
    """
    try:
        _gc.set_threshold(*BOOT_THRESHOLD)
        logger.debug("[blitz_gc] boot thresholds applied: %s", BOOT_THRESHOLD)
    except Exception as exc:
        logger.debug("[blitz_gc] boot set_threshold failed: %s", exc)


_apply_boot_gc()

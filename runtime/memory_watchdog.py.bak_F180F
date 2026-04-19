"""
MemoryWatchdog — tier-suspension seam for IntelligenceDispatcher.

TICKET-007: runtime memory pressure guardrail for intelligence fan-out.

Role:
- Malý integration seam mezi UmaWatchdog (uma_budget) a IntelligenceDispatcher.
- NENÍ nový watchdog — používá existující UmaWatchdog.
- NENÍ orchestration owner — pouze policy interface pro dispatcher.
- NENÍ scheduler/manager/framework.

Responsibilities:
- Registruje UmaWatchdog callbacks pro memory pressure změny.
- Udržuje suspended-tiers sadu.
- Notifikuje dispatcher při změnách pressure level.
- Fail-soft: watchdog errors nezastaví dispatcher.

Pressure classification (M1 8GB UMA thresholds):
- NORMAL:   < 6.0 GB  → všechny tiery aktivní
- WARN:     >= 6.0 GB → TIER2+ mohou být suspendovány
- CRITICAL: >= 6.5 GB → TIER2+ suspendovány, TIER1 caution
- EMERGENCY: >= 7.0 GB → všechny vyšší tiery suspendovány, GC hint

Integration surface:
- IntelligenceDispatcher drží MemoryWatchdog instanci
- Dispatcher.run_tier() kontroluje suspended state před spuštěním
- Watchdog callbacks jsou fail-soft no-op pokud dispatcher nedostupný
"""

from __future__ import annotations

import asyncio
import gc
import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from hledac.universal.utils.uma_budget import (
    UmaWatchdog,
    UmaWatchdogCallbacks,
    get_uma_snapshot,
)

if TYPE_CHECKING:
    from hledac.universal.runtime.intelligence_dispatcher import IntelligenceDispatcher

logger = logging.getLogger(__name__)


class PressureLevel(Enum):
    """Memory pressure classification."""
    NORMAL = auto()
    WARN = auto()
    CRITICAL = auto()
    EMERGENCY = auto()

    @classmethod
    def from_str(cls, level: str) -> "PressureLevel":
        mapping = {
            "normal": cls.NORMAL,
            "warn": cls.WARN,
            "critical": cls.CRITICAL,
            "emergency": cls.EMERGENCY,
        }
        return mapping.get(level, cls.NORMAL)

    def tier2_suspended(self) -> bool:
        """Return True if TIER2 should be suspended at this level."""
        return self in (PressureLevel.CRITICAL, PressureLevel.EMERGENCY)

    def tier1_caution(self) -> bool:
        """Return True if TIER1 should run with caution at this level."""
        return self in (PressureLevel.CRITICAL, PressureLevel.EMERGENCY)


# =============================================================================
# MemoryWatchdogCallbacks — callback interface for dispatcher integration
# =============================================================================

class MemoryWatchdogCallbacks:
    """
    Callback interface for MemoryWatchdog tier-suspension events.

    Default implementations are no-ops — subclasses override what they need.
    """

    def on_tier_suspended(self, tier_name: str, level: PressureLevel) -> None:
        """Called when a tier is suspended due to memory pressure."""

    def on_tier_resumed(self, tier_name: str) -> None:
        """Called when a tier is resumed after pressure decrease."""

    def on_emergency_gc(self, snapshot: dict) -> None:
        """Called on EMERGENCY level — dispatcher can trigger GC if safe."""


# =============================================================================
# MemoryWatchdog — integration seam
# =============================================================================

class MemoryWatchdog:
    """
    Memory pressure seam between UmaWatchdog and IntelligenceDispatcher.

    Small utility class (not a new framework):
    - Wraps UmaWatchdog with PressureLevel classification
    - Maps pressure levels to tier suspension policy
    - Calls tier-suspension callbacks on dispatcher
    - Emergency branch: optional GC suggestion, MLX cache clear attempt

    Invariants:
    - Uses existing UmaWatchdog (uma_budget) — does NOT create new watchdog
    - No background threads beyond UmaWatchdog's own async task
    - Emergency GC is opt-in hint, not forced action
    - All callbacks are fail-soft no-ops by default
    """

    def __init__(
        self,
        callbacks: Optional[MemoryWatchdogCallbacks] = None,
        watchdog_interval: float = 0.5,
    ) -> None:
        self._callbacks = callbacks or MemoryWatchdogCallbacks()
        self._watchdog_interval = watchdog_interval
        self._uma_watchdog: Optional[UmaWatchdog] = None
        self._suspended_tiers: set[str] = set()
        self._current_level: PressureLevel = PressureLevel.NORMAL

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def suspended_tiers(self) -> frozenset[str]:
        """Return current set of suspended tier names."""
        return frozenset(self._suspended_tiers)

    @property
    def current_level(self) -> PressureLevel:
        """Return current memory pressure level."""
        return self._current_level

    @property
    def is_emergency(self) -> bool:
        """True if current level is EMERGENCY."""
        return self._current_level == PressureLevel.EMERGENCY

    def is_tier_suspended(self, tier_name: str) -> bool:
        """Check if a tier is currently suspended."""
        return tier_name in self._suspended_tiers

    def get_snapshot(self) -> dict:
        """Return current UMA snapshot."""
        try:
            return get_uma_snapshot()
        except Exception:
            return {"error": "snapshot_unavailable"}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> asyncio.Task:
        """
        Start UmaWatchdog with MemoryWatchdogCallbacks adapter.

        Returns the asyncio.Task so caller can track it.
        Raises RuntimeError if already running.
        """
        if self._uma_watchdog is not None and self._uma_watchdog.is_running:
            raise RuntimeError("MemoryWatchdog is already running")

        adapter = _MemoryWatchdogCallbackAdapter(self)
        self._uma_watchdog = UmaWatchdog(
            callbacks=adapter,
            interval=self._watchdog_interval,
        )
        return self._uma_watchdog.start()

    def stop(self) -> None:
        """Stop the UmaWatchdog and clear suspension state."""
        if self._uma_watchdog is not None:
            self._uma_watchdog.stop()
            self._uma_watchdog = None
        self._suspended_tiers.clear()
        self._current_level = PressureLevel.NORMAL

    @property
    def is_running(self) -> bool:
        """True if UmaWatchdog is active."""
        return self._uma_watchdog is not None and self._uma_watchdog.is_running

    # ── Internal: pressure update from UmaWatchdog ───────────────────────────

    def _on_pressure_change(self, level_str: str, snapshot: dict) -> None:
        """Process pressure level change from UmaWatchdog callback."""
        new_level = PressureLevel.from_str(level_str)
        old_level = self._current_level
        self._current_level = new_level

        if new_level == old_level:
            return

        logger.debug(
            f"[MemoryWatchdog] pressure transition: {old_level.name} → {new_level.name}"
        )

        # Emergency: special handling
        if new_level == PressureLevel.EMERGENCY:
            self._handle_emergency(snapshot)

        # Tier2 suspension policy
        if new_level.tier2_suspended():
            if "TIER2" not in self._suspended_tiers:
                self._suspended_tiers.add("TIER2")
                self._callbacks.on_tier_suspended("TIER2", new_level)
        else:
            if "TIER2" in self._suspended_tiers:
                self._suspended_tiers.discard("TIER2")
                self._callbacks.on_tier_resumed("TIER2")

        # TIER1 caution flag is advisory only — dispatcher decides

    def _handle_emergency(self, snapshot: dict) -> None:
        """Handle EMERGENCY level: suggest GC, try MLX cache clear."""
        self._callbacks.on_emergency_gc(snapshot)

        # Try safe GC — wrapped in try/except
        try:
            gc.collect()
            logger.debug("[MemoryWatchdog] emergency GC attempted")
        except Exception as e:
            logger.debug(f"[MemoryWatchdog] emergency GC skipped: {e}")

        # Try MLX cache clear — only if mlx is safely available
        self._try_mlx_cache_clear()

    def _try_mlx_cache_clear(self) -> None:
        """Try to clear MLX metal cache. Fail-silent if unavailable."""
        try:
            import mlx.core as mx

            # Check if metal is available before clearing
            if hasattr(mx, "metal") and mx.metal is not None:
                # mx.eval([]) flushes pending lazy operations before clear_cache
                mx.eval([])
                mx.metal.clear_cache()
                logger.debug("[MemoryWatchdog] MLX metal cache cleared")
        except Exception as e:
            logger.debug(f"[MemoryWatchdog] MLX cache clear skipped: {e}")


# =============================================================================
# Callback adapter: UmaWatchdog → MemoryWatchdog
# =============================================================================

class _MemoryWatchdogCallbackAdapter(UmaWatchdogCallbacks):
    """
    Adapter: maps UmaWatchdogCallbacks to MemoryWatchdog pressure logic.
    """

    __slots__ = ("_watchdog",)

    def __init__(self, watchdog: MemoryWatchdog) -> None:
        self._watchdog = watchdog

    def on_warn(self, snapshot: dict) -> None:
        self._watchdog._on_pressure_change("warn", snapshot)

    def on_critical(self, snapshot: dict) -> None:
        self._watchdog._on_pressure_change("critical", snapshot)

    def on_emergency(self, snapshot: dict) -> None:
        self._watchdog._on_pressure_change("emergency", snapshot)


# =============================================================================
# Integration helper — attach to dispatcher
# =============================================================================

def attach_to_dispatcher(
    dispatcher: "IntelligenceDispatcher",
    callbacks: Optional[MemoryWatchdogCallbacks] = None,
    watchdog_interval: float = 0.5,
) -> MemoryWatchdog:
    """
    Attach MemoryWatchdog to an IntelligenceDispatcher instance.

    Returns the MemoryWatchdog so caller can manage its lifecycle.
    """
    watchdog = MemoryWatchdog(
        callbacks=callbacks or _DispatcherWatchdogCallbacks(dispatcher),
        watchdog_interval=watchdog_interval,
    )
    dispatcher._memory_watchdog = watchdog  # type: ignore[attr-defined]
    return watchdog


class _DispatcherWatchdogCallbacks(MemoryWatchdogCallbacks):
    """
    MemoryWatchdogCallbacks that update dispatcher suspension state.
    """

    __slots__ = ("_dispatcher",)

    def __init__(self, dispatcher: "IntelligenceDispatcher") -> None:
        self._dispatcher = dispatcher

    def on_tier_suspended(self, tier_name: str, level: PressureLevel) -> None:
        try:
            if tier_name == "TIER2":
                self._dispatcher._suspended_tiers.add(tier_name)
            logger.info(f"[dispatcher] tier {tier_name} suspended (pressure: {level.name})")
        except Exception:
            pass

    def on_tier_resumed(self, tier_name: str) -> None:
        try:
            self._dispatcher._suspended_tiers.discard(tier_name)
            logger.info(f"[dispatcher] tier {tier_name} resumed")
        except Exception:
            pass

    def on_emergency_gc(self, snapshot: dict) -> None:
        logger.warning(f"[dispatcher] emergency GC hint: {snapshot.get('uma_used_mb', 0):,} MB")

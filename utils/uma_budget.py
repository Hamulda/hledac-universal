"""
UnifiedMemoryBudgetAccountant - Sprint 1B Resource Hardening.

ROLE: Canonical RAW UMA SAMPLER (not a governor/policy/allocator).

This module provides:
- Raw memory sampling (system RAM via psutil, MLX active/peak/cache)
- Pressure level classification (normal/warn/critical/emergency)
- Async watchdog with state-change callbacks

Threshold levels (M1 8GB UMA):
- WARN:   >= 6.0 GB used
- CRITICAL: >= 6.5 GB used
- EMERGENCY: >= 7.0 GB used

AUTHORITY BOUNDARY:
- SAMPLER: reads raw values, no policy, no hysteresis, no budgeting
- GOVERNOR (core/resource_governor.py): policy/hysteresis/runtime governance
- ALLOCATOR (resource_allocator.py): request-level budgeting/concurrency

API:
- get_uma_snapshot() -> dict
- get_uma_usage_mb() -> int | None
- get_uma_pressure_level() -> tuple[int, str]  (pct, "normal"/"warn"/"critical"/"emergency")
- is_uma_critical() -> bool
- is_uma_warn() -> bool
- format_uma_budget_report() -> str

Fail-open: returns "normal" / 0 when all sensors unavailable.
No MLX imports at module level (lazy).
"""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import TYPE_CHECKING, Optional

__all__ = [
    "get_uma_snapshot",
    "get_uma_usage_mb",
    "get_uma_pressure_level",
    "is_uma_critical",
    "is_uma_warn",
    "is_uma_emergency",
    "format_uma_budget_report",
    # Sprint 7F: UMA Watchdog
    "UmaWatchdog",
    "UmaWatchdogCallbacks",
]

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)

# M1 8GB UMA budget thresholds
_UMA_TOTAL_MB: int = 8_192  # 8 GB total
_WARN_THRESHOLD_MB: int = 6_144  # 6.0 GB - Sprint 6B
_CRITICAL_THRESHOLD_MB: int = 6_656  # 6.5 GB - Sprint 6B
_EMERGENCY_THRESHOLD_MB: int = 7_168  # 7.0 GB - Sprint 6B

# psutil lazy import
_psutil: Optional["ModuleType"] = None


def _get_psutil():
    """Lazy import of psutil."""
    global _psutil
    if _psutil is not None:
        return _psutil
    try:
        import psutil

        _psutil = psutil
    except Exception as e:
        logger.debug(f"psutil import failed: {e}")
        _psutil = None
    return _psutil


def _get_mlx_core():
    """Lazy MLX import for memory metrics."""
    try:
        import mlx.core as mx

        return mx
    except Exception:
        return None


def get_system_memory_mb() -> tuple[int, int, int]:
    """
    Get system memory info.

    Returns:
        (total_mb, used_mb, available_mb)
        Returns (0, 0, 0) on failure.
    """
    psutil = _get_psutil()
    if psutil is None:
        return 0, 0, 0

    try:
        mem = psutil.virtual_memory()
        total = getattr(mem, "total", 0)
        used = getattr(mem, "used", 0)
        available = getattr(mem, "available", 0)

        total_mb = total // (1024 * 1024)
        used_mb = used // (1024 * 1024)
        available_mb = available // (1024 * 1024)

        return total_mb, used_mb, available_mb
    except Exception as e:
        logger.debug(f"get_system_memory_mb failed: {e}")
        return 0, 0, 0


def get_mlx_memory_mb() -> tuple[int, int, int]:
    """
    Get MLX memory usage.

    Returns:
        (active_mb, peak_mb, cache_mb)
        Returns (0, 0, 0) if MLX unavailable.
    """
    mx_core = _get_mlx_core()
    if mx_core is None:
        return 0, 0, 0

    try:
        metal = getattr(mx_core, "metal", None)

        active = 0
        if metal is not None and hasattr(metal, "get_active_memory"):
            active = metal.get_active_memory()
        elif hasattr(mx_core, "get_active_memory"):
            active = mx_core.get_active_memory()

        peak = 0
        if metal is not None and hasattr(metal, "get_peak_memory"):
            peak = metal.get_peak_memory()
        elif hasattr(mx_core, "get_peak_memory"):
            peak = mx_core.get_peak_memory()

        cache = 0
        if metal is not None and hasattr(metal, "get_cache_memory"):
            cache = metal.get_cache_memory()
        elif hasattr(mx_core, "get_cache_memory"):
            cache = mx_core.get_cache_memory()

        return (
            active // (1024 * 1024),
            peak // (1024 * 1024),
            cache // (1024 * 1024),
        )
    except Exception as e:
        logger.debug(f"get_mlx_memory_mb failed: {e}")
        return 0, 0, 0


def get_uma_usage_mb() -> Optional[int]:
    """
    Estimate of "used" UMA memory as:
        system_used + mlx_active

    NOTE: On M1 unified memory architecture, system_used may partially
    overlap with mlx_active allocations. This is a conservative pressure
    estimate, not a precise accounting of physical memory pages.
    Returns None if system memory unavailable.
    """
    sys_total, sys_used, _ = get_system_memory_mb()
    if sys_total == 0:
        return None

    mlx_active, _, _ = get_mlx_memory_mb()

    return sys_used + mlx_active


def get_uma_pressure_level() -> tuple[int, str]:
    """
    Calculate UMA pressure percentage and level.

    Returns:
        (usage_pct: int, level: str)
        level: "normal" / "warn" / "critical" / "emergency"

    Uses total 8GB as denominator.
    Fails open to (0, "normal") if measurement unavailable.
    """
    total_mb = get_uma_usage_mb()
    if total_mb is None:
        return 0, "normal"

    # UMA is 8GB total
    usage_pct = int((total_mb / _UMA_TOTAL_MB) * 100)

    if total_mb >= _EMERGENCY_THRESHOLD_MB:
        return usage_pct, "emergency"
    elif total_mb >= _CRITICAL_THRESHOLD_MB:
        return usage_pct, "critical"
    elif total_mb >= _WARN_THRESHOLD_MB:
        return usage_pct, "warn"
    else:
        return usage_pct, "normal"


def is_uma_warn() -> bool:
    """Return True if UMA usage >= 6.0 GB."""
    _, level = get_uma_pressure_level()
    return level in ("warn", "critical", "emergency")


def is_uma_critical() -> bool:
    """Return True if UMA usage >= 6.5 GB."""
    _, level = get_uma_pressure_level()
    return level in ("critical", "emergency")


def is_uma_emergency() -> bool:
    """Return True if UMA usage >= 7.0 GB."""
    _, level = get_uma_pressure_level()
    return level == "emergency"


def get_uma_snapshot() -> dict:
    """
    Return a complete unified memory snapshot.

    Includes system RAM, MLX memory, thresholds, and pressure level.
    """
    sys_total, sys_used, sys_avail = get_system_memory_mb()
    mlx_active, mlx_peak, mlx_cache = get_mlx_memory_mb()
    uma_total_mb = get_uma_usage_mb()
    pressure_pct, pressure_level = get_uma_pressure_level()

    return {
        "uma_total_mb": _UMA_TOTAL_MB,
        "warn_threshold_mb": _WARN_THRESHOLD_MB,
        "critical_threshold_mb": _CRITICAL_THRESHOLD_MB,
        "emergency_threshold_mb": _EMERGENCY_THRESHOLD_MB,
        "system_total_mb": sys_total,
        "system_used_mb": sys_used,
        "system_available_mb": sys_avail,
        "mlx_active_mb": mlx_active,
        "mlx_peak_mb": mlx_peak,
        "mlx_cache_mb": mlx_cache,
        "uma_used_mb": uma_total_mb if uma_total_mb is not None else 0,
        "uma_usage_pct": pressure_pct,
        "uma_pressure_level": pressure_level,
        "is_warn": is_uma_warn(),
        "is_critical": is_uma_critical(),
        "is_emergency": is_uma_emergency(),
        "platform": platform.system(),
    }


def format_uma_budget_report() -> str:
    """
    Format a human-readable UMA budget report.
    """
    snap = get_uma_snapshot()

    lines = [
        "=== UMA Budget Report ===",
        f"Platform:       {snap['platform']}",
        f"UMA Total:      {snap['uma_total_mb']:,} MB",
        f"Warn at:        {snap['warn_threshold_mb']:,} MB",
        f"Critical at:    {snap['critical_threshold_mb']:,} MB",
        "",
        f"System RAM:     {snap['system_used_mb']:,} / {snap['system_total_mb']:,} MB (avail: {snap['system_available_mb']:,})",
        f"MLX Active:     {snap['mlx_active_mb']:,} MB",
        f"MLX Peak:       {snap['mlx_peak_mb']:,} MB",
        f"MLX Cache:      {snap['mlx_cache_mb']:,} MB",
        "",
        f"UMA Used:       {snap['uma_used_mb']:,} MB ({snap['uma_usage_pct']}%)",
        f"Pressure Level: {snap['uma_pressure_level']}",
        f"Is Warn:        {is_uma_warn()}",
        f"Is Critical:    {is_uma_critical()}",
        f"Is Emergency:   {is_uma_emergency()}",
    ]

    return "\n".join(lines)


# =============================================================================
# Sprint 7F: UMA Watchdog — async memory-pressure monitoring with debounce
# =============================================================================


class UmaWatchdogCallbacks:
    """
    Callback interface for UmaWatchdog reactions.
    All methods are optional — unactioned callbacks are no-ops.
    """

    def on_warn(self, _snapshot: dict) -> None:
        """Called when UMA enters WARN state (>= 6.0 GB)."""

    def on_critical(self, _snapshot: dict) -> None:
        """Called when UMA enters CRITICAL state (>= 6.5 GB)."""

    def on_emergency(self, _snapshot: dict) -> None:
        """Called when UMA enters EMERGENCY state (>= 7.0 GB)."""


class UmaWatchdog:
    """
    Async UMA memory watchdog with state-change debounce.

    Polls get_uma_pressure_level() every `interval` seconds (default 0.5s).
    Fires callbacks only on state *changes* (not every poll).
    All callbacks run inside the watchdog's own async loop — never block the caller.

    Invariants:
    - Default polling interval = 0.5s (not 5s)
    - Fail-open: if get_uma_pressure_level() throws, treats as "normal"
    - Debounce: same level re-trigger only after DEBOUNCE_SECONDS have passed
    - Non-blocking: asyncio.sleep is used, never time.sleep
    """

    DEBOUNCE_SECONDS: float = 2.0  # minimum seconds between same-level callbacks

    def __init__(
        self,
        callbacks: UmaWatchdogCallbacks | None = None,
        interval: float = 0.5,
    ) -> None:
        self._callbacks = callbacks
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_fired_level: str = "normal"
        self._last_fired_at: float = 0.0

    def _should_fire(self, level: str, now: float) -> bool:
        """Return True if level should trigger a callback (debounce-aware)."""
        if level == "normal":
            return False
        if level != self._last_fired_level:
            return True
        return (now - self._last_fired_at) >= self.DEBOUNCE_SECONDS

    async def _run(self) -> None:
        """Main polling loop — runs until cancelled."""
        import time

        while self._running:
            try:
                _, level = get_uma_pressure_level()
                now = time.monotonic()

                if self._should_fire(level, now):
                    self._last_fired_level = level
                    self._last_fired_at = now
                    snapshot = get_uma_snapshot()

                    if level == "emergency" and self._callbacks:
                        logger.warning(
                            f"[UMA-WATCHDOG] EMERGENCY triggered: "
                            f"{snapshot.get('uma_used_mb', 0):,} MB "
                            f"({snapshot.get('uma_usage_pct', 0)}%)"
                        )
                        try:
                            self._callbacks.on_emergency(snapshot)
                        except Exception as e:
                            logger.error(f"[UMA-WATCHDOG] on_emergency callback error: {e}")

                    elif level == "critical" and self._callbacks:
                        logger.warning(
                            f"[UMA-WATCHDOG] CRITICAL triggered: "
                            f"{snapshot.get('uma_used_mb', 0):,} MB "
                            f"({snapshot.get('uma_usage_pct', 0)}%)"
                        )
                        try:
                            self._callbacks.on_critical(snapshot)
                        except Exception as e:
                            logger.error(f"[UMA-WATCHDOG] on_critical callback error: {e}")

                    elif level == "warn" and self._callbacks:
                        logger.info(
                            f"[UMA-WATCHDOG] WARN triggered: "
                            f"{snapshot.get('uma_used_mb', 0):,} MB "
                            f"({snapshot.get('uma_usage_pct', 0)}%)"
                        )
                        try:
                            self._callbacks.on_warn(snapshot)
                        except Exception as e:
                            logger.error(f"[UMA-WATCHDOG] on_warn callback error: {e}")

            except Exception as e:
                logger.debug(f"[UMA-WATCHDOG] poll error (fail-open): {e}")

            await asyncio.sleep(self._interval)

    def start(self) -> asyncio.Task:
        """
        Start the watchdog in the current event loop.

        Returns the asyncio.Task so caller can track it.
        Raises RuntimeError if already running.
        """
        if self._task is not None and not self._task.done():
            raise RuntimeError("UmaWatchdog is already running")

        self._running = True
        self._task = asyncio.create_task(self._run(), name="uma_watchdog")
        return self._task

    def stop(self) -> None:
        """Stop the watchdog gracefully."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None

    @property
    def is_running(self) -> bool:
        """True if the watchdog loop is active."""
        return self._running and self._task is not None and not self._task.done()

    @property
    def interval(self) -> float:
        """Return the polling interval in seconds."""
        return self._interval

    @property
    def last_fired_level(self) -> str:
        """Return the last level that triggered a callback."""
        return self._last_fired_level

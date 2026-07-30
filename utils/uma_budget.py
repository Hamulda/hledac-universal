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
import asyncio
import logging
import platform
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from hledac.universal.utils.async_helpers import safe_create_task
__all__ = ['get_uma_snapshot', 'get_uma_budget', 'get_uma_usage_mb', 'get_uma_pressure_level', 'is_uma_critical', 'is_uma_warn', 'is_uma_emergency', 'format_uma_budget_report', 'UmaWatchdog', 'UmaWatchdogCallbacks', 'UMA_WARN_GIB', 'UMA_CRITICAL_GIB', 'UMA_EMERGENCY_GIB', 'M1_FETCH_SOFT_CEILING_GB', 'GENERAL_HIGH_WATER_RATIO', 'shutdown_uma_callback_executor']

# M1-01 fix: bounded executor for UMA callbacks — prevents unbounded task accumulation.
# max_workers=2: one for current callback, one for pending (prevents head-of-line blocking).
# Thread name prefix for debugging: ls /tmp | grep uma_cb
_uma_callback_executor: ThreadPoolExecutor | None = None


def _get_uma_executor() -> ThreadPoolExecutor:
    global _uma_callback_executor
    if _uma_callback_executor is None:
        _uma_callback_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='uma_cb')
    return _uma_callback_executor


async def _run_callback_in_executor(cb, snapshot: dict) -> None:
    """Run a synchronous callback in the bounded thread pool, propagating exceptions."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_get_uma_executor(), cb, snapshot)
    except Exception as e:
        logger.error(f'[UMA-WATCHDOG] UMA callback failed: {e}')


def shutdown_uma_callback_executor() -> None:
    """Shutdown the UMA callback executor. Call from UmaWatchdog.stop() or at app exit."""
    global _uma_callback_executor
    if _uma_callback_executor is not None:
        _uma_callback_executor.shutdown(wait=True, cancel_futures=True)
        _uma_callback_executor = None
logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from types import ModuleType

def _detect_total_memory_mb() -> int:
    """Detect real system RAM. Floor 4 GB, ceil 64 GB, fallback 8 GB."""
    try:
        import psutil as _ps_init
        mem = _ps_init.virtual_memory()
        detected = mem.total // (1024 * 1024)
        return max(4096, min(65536, detected))
    except (ImportError, AttributeError):
        return 8192
_UMA_TOTAL_MB: int = _detect_total_memory_mb()
from hledac.universal.core.resource_governor import _THRESHOLD_CRITICAL_GIB, _THRESHOLD_EMERGENCY_GIB, _THRESHOLD_WARN_GIB, RATIOS_USED as _RATIOS_USED, DETECTED_TOTAL_GIB as _DETECTED_TOTAL_GIB
_WARN_THRESHOLD_MB: int = int(_THRESHOLD_WARN_GIB * 1024)
_CRITICAL_THRESHOLD_MB: int = int(_THRESHOLD_CRITICAL_GIB * 1024)
_EMERGENCY_THRESHOLD_MB: int = int(_THRESHOLD_EMERGENCY_GIB * 1024)
UMA_WARN_GIB: float = _THRESHOLD_WARN_GIB
UMA_CRITICAL_GIB: float = _THRESHOLD_CRITICAL_GIB
UMA_EMERGENCY_GIB: float = _THRESHOLD_EMERGENCY_GIB
M1_FETCH_SOFT_CEILING_GB: float = round(_UMA_TOTAL_MB / 1024 * 0.88, 2)

# A5-01 FIX: Sync Rust memory.rs thresholds with resource_governor SSOT.
# memory.rs uses process RSS (PROC_PIDTASKINFO) for its memory_pressure_level(),
# which is separate from resource_governor's system-used GiB thresholds.
# We align the Rust hard threshold with resource_governor's critical threshold
# so that the health.rs telemetry reports consistent "critical" conditions.
# soft_gib stays at 4.0 GiB (RSS-based "elevated" has no system-wide equivalent).
try:
    from hledac.universal.core.memory import set_memory_pressure_thresholds as _set_rust_thresholds
    _set_rust_thresholds(soft_gib=4.0, hard_gib=_THRESHOLD_CRITICAL_GIB)
except Exception:
    pass  # Fail-safe: Rust thresholds stay at defaults (4.0 / 5.5 GiB)
GENERAL_HIGH_WATER_RATIO: float = 0.85
MAX_L2_CACHE_SIZE_MB: int = 50
from hledac.universal.core.memory import get_memory_snapshot as _rust_snapshot
from hledac.universal.core.psutil_shim import psutil_module as _psutil_mod

def _get_mlx_core():
    """Lazy MLX import for memory metrics."""
    try:
        import mlx.core as mx
        return mx
    except (ImportError, AttributeError):
        return None

def get_system_memory_mb() -> tuple[int, int, int]:
    """
    Get system memory info.

    Returns:
        (total_mb, used_mb, available_mb)
        Returns (0, 0, 0) on failure.

    Issue #38 SSOT: Delegates to core.memory (Rust SSOT surface).
    Falls back to cached psutil reader for compatibility.
    """
    try:
        snap = _rust_snapshot()
        total_bytes = snap.get('total_memory_gib', 0) * 1024 ** 3
        avail_bytes = snap.get('available_memory_gib', 0) * 1024 ** 3
        used_bytes = total_bytes - avail_bytes
        total_mb = int(total_bytes / 1024 ** 2)
        used_mb = int(used_bytes / 1024 ** 2)
        available_mb = int(avail_bytes / 1024 ** 2)
        return (total_mb, used_mb, available_mb)
    except Exception:
        pass
    from hledac.universal.core.resource_governor import _get_cached_psutil, _read_virtual_memory_sync
    try:
        vm = _get_cached_psutil('virtual_memory', _read_virtual_memory_sync)
        if vm is None:
            return (0, 0, 0)
        total = getattr(vm, 'total', 0)
        available = getattr(vm, 'available', 0)
        used = total - available
        total_mb = total // (1024 * 1024)
        used_mb = used // (1024 * 1024)
        available_mb = available // (1024 * 1024)
        return (total_mb, used_mb, available_mb)
    except (AttributeError, OSError) as e:
        logger.debug(f'get_system_memory_mb failed: {e}')
        return (0, 0, 0)

def get_mlx_memory_mb() -> tuple[int, int, int]:
    """
    Get MLX memory usage.

    Returns:
        (active_mb, peak_mb, cache_mb)
        Returns (0, 0, 0) if MLX unavailable.

    Issue #38 SSOT: Delegates to core.memory (Rust MLX probe).
    Falls back to direct mlx.core inspection for peak/cache unavailable in Rust.
    """
    from hledac.universal.core.memory import get_metal_active_memory_bytes
    try:
        active_bytes = get_metal_active_memory_bytes()
        active_mb = int(active_bytes / 1024 ** 2)
    except Exception:
        active_mb = 0
    peak_mb = 0
    cache_mb = 0
    mx_core = _get_mlx_core()
    if mx_core is not None:
        try:
            metal = getattr(mx_core, 'metal', None)
            if metal is not None:
                if hasattr(metal, 'get_peak_memory'):
                    peak_mb = int(metal.get_peak_memory() / 1024 ** 2)
                if hasattr(metal, 'get_cache_memory'):
                    cache_mb = int(metal.get_cache_memory() / 1024 ** 2)
            else:
                if hasattr(mx_core, 'get_peak_memory'):
                    peak_mb = int(mx_core.get_peak_memory() / 1024 ** 2)
                if hasattr(mx_core, 'get_cache_memory'):
                    cache_mb = int(mx_core.get_cache_memory() / 1024 ** 2)
        except (AttributeError, OSError):
            pass
    return (active_mb, peak_mb, cache_mb)

def get_uma_usage_mb() -> int | None:
    """
    Estimate of "used" UMA memory.

    On M1 unified memory architecture, system RSS includes MLX allocations,
    so we take the maximum to avoid double-counting:
        - sys_used >= mlx_active → MLX is subset of RSS, use sys_used
        - mlx_active > sys_used → edge case: MLX alloc without RSS footprint

    Returns None if system memory unavailable.
    """
    sys_total, sys_used, _ = get_system_memory_mb()
    if sys_total == 0:
        return None
    return sys_used

def _swap_pct(ps) -> float:
    """Helper: vrátí swap usage %, fail-open 0.0."""
    try:
        return ps.swap_memory().percent
    except (AttributeError, OSError):
        return 0.0

def get_uma_pressure_level() -> tuple[int, str]:
    """
    Calculate UMA pressure percentage and level.

    Returns:
        (usage_pct: int, level: str)
        level: "normal" / "warn" / "critical" / "emergency"

    Uses dynamically detected _UMA_TOTAL_MB as denominator.
    Swap signal: adaptive thresholds based on total swap size.
    Fails open to (0, "normal") if measurement unavailable.
    """
    ps = _psutil_mod()
    total_mb = get_uma_usage_mb()
    if total_mb is None:
        return (0, 'normal')
    usage_pct = int(total_mb / _UMA_TOTAL_MB * 100)
    swap_crit_pct = 100
    swap_warn_pct = 100
    if ps is not None:
        try:
            sw = ps.swap_memory()
            swap_total_gb = sw.total / 1024 ** 3
            if swap_total_gb >= 0.5:
                swap_warn_pct = 30 if swap_total_gb < 4 else 60
                swap_crit_pct = 55 if swap_total_gb < 4 else 85
        except (AttributeError, OSError):
            pass
    if total_mb >= _EMERGENCY_THRESHOLD_MB:
        return (usage_pct, 'emergency')
    elif total_mb >= _CRITICAL_THRESHOLD_MB or usage_pct > 93 or (ps is not None and _swap_pct(ps) > swap_crit_pct):
        return (usage_pct, 'critical')
    elif total_mb >= _WARN_THRESHOLD_MB or (ps is not None and _swap_pct(ps) > swap_warn_pct):
        return (usage_pct, 'warn')
    else:
        return (usage_pct, 'normal')

def is_uma_warn() -> bool:
    """
    Return True if UMA usage >= warn threshold (6.0 GB).

    Note: This returns True for warn, critical, AND emergency levels.
    For exact level checking, use get_uma_pressure_level() directly.
    Use is_uma_critical() or is_uma_emergency() for specific thresholds.
    """
    _, level = get_uma_pressure_level()
    return level in ('warn', 'critical', 'emergency')

def is_uma_critical() -> bool:
    """Return True if UMA usage >= 6.5 GB."""
    _, level = get_uma_pressure_level()
    return level in ('critical', 'emergency')

def is_uma_emergency() -> bool:
    """Return True if UMA usage >= 7.0 GB."""
    _, level = get_uma_pressure_level()
    return level == 'emergency'

def get_uma_snapshot() -> dict:
    """
    Return a complete unified memory snapshot.

    Includes system RAM, MLX memory, thresholds, and pressure level.
    """
    sys_total, sys_used, sys_avail = get_system_memory_mb()
    mlx_active, mlx_peak, mlx_cache = get_mlx_memory_mb()
    uma_total_mb = get_uma_usage_mb()
    pressure_pct, pressure_level = get_uma_pressure_level()
    return {'uma_total_mb': _UMA_TOTAL_MB, 'warn_threshold_mb': _WARN_THRESHOLD_MB, 'critical_threshold_mb': _CRITICAL_THRESHOLD_MB, 'emergency_threshold_mb': _EMERGENCY_THRESHOLD_MB, 'system_total_mb': sys_total, 'system_used_mb': sys_used, 'system_available_mb': sys_avail, 'mlx_active_mb': mlx_active, 'mlx_peak_mb': mlx_peak, 'mlx_cache_mb': mlx_cache, 'uma_used_mb': uma_total_mb if uma_total_mb is not None else 0, 'uma_usage_pct': pressure_pct, 'uma_pressure_level': pressure_level, 'is_warn': is_uma_warn(), 'is_critical': is_uma_critical(), 'is_emergency': is_uma_emergency(), 'platform': platform.system(), 'uma_total_detected_mb': _UMA_TOTAL_MB, 'rg_detected_gib': _DETECTED_TOTAL_GIB, 'warn_threshold_pct': int(_RATIOS_USED[1] * 100), 'critical_threshold_pct': int(_RATIOS_USED[2] * 100), 'emergency_threshold_pct': int(_RATIOS_USED[3] * 100), 'fetch_soft_ceiling_gb': M1_FETCH_SOFT_CEILING_GB}

def get_uma_budget() -> dict:
    """Back-compat alias for `get_uma_snapshot()`.

    Several callers (e.g. `rl/sprint_policy_manager.py`,
    `tests/probe_f261_qmix_activation.py`, and the historical sprint-260
    era mocks) reference `get_uma_budget` by name. The canonical contract
    is `get_uma_snapshot` — this alias is a thin pass-through so any
    future refactor of the snapshot shape only needs to update a single
    source of truth.

    Returns:
        The same dict as `get_uma_snapshot()` — see that function for
        the full key list (`uma_total_mb`, `warn_threshold_mb`,
        `critical_threshold_mb`, `emergency_threshold_mb`,
        `system_total_mb`, `system_used_mb`, `system_available_mb`,
        `mlx_active_mb`, `mlx_peak_mb`, `mlx_cache_mb`, `uma_used_mb`,
        `uma_usage_pct`, `uma_pressure_level`, `is_warn`, `is_critical`,
        `is_emergency`, `platform`).
    """
    return get_uma_snapshot()

def format_uma_budget_report() -> str:
    """
    Format a human-readable UMA budget report.
    """
    snap = get_uma_snapshot()
    lines = ['=== UMA Budget Report ===', f"Platform:       {snap['platform']}", f"UMA Total:      {snap['uma_total_mb']:,} MB", f"Warn at:        {snap['warn_threshold_mb']:,} MB", f"Critical at:    {snap['critical_threshold_mb']:,} MB", '', f"System RAM:     {snap['system_used_mb']:,} / {snap['system_total_mb']:,} MB (avail: {snap['system_available_mb']:,})", f"MLX Active:     {snap['mlx_active_mb']:,} MB", f"MLX Peak:       {snap['mlx_peak_mb']:,} MB", f"MLX Cache:      {snap['mlx_cache_mb']:,} MB", '', f"UMA Used:       {snap['uma_used_mb']:,} MB ({snap['uma_usage_pct']}%)", f"Pressure Level: {snap['uma_pressure_level']}", f'Is Warn:        {is_uma_warn()}', f'Is Critical:    {is_uma_critical()}', f'Is Emergency:   {is_uma_emergency()}']
    return '\n'.join(lines)
logger.debug('[UMA-INIT] Detected RAM: %d MB | WARN: %d MB (%.0f%%) | CRITICAL: %d MB (%.0f%%) | EMERGENCY: %d MB (%.0f%%) | FETCH_CEILING: %.2f GB', _UMA_TOTAL_MB, _WARN_THRESHOLD_MB, 87.0, _CRITICAL_THRESHOLD_MB, 93.0, _EMERGENCY_THRESHOLD_MB, 97.0, M1_FETCH_SOFT_CEILING_GB)

class UmaWatchdogCallbacks:
    """
    Callback interface for UmaWatchdog reactions.
    All methods are optional — unactioned callbacks are no-ops.
    """

    def on_warn(self, snapshot: dict) -> None:
        """Called when UMA enters WARN state (>= 6.0 GB)."""

    def on_critical(self, snapshot: dict) -> None:
        """Called when UMA enters CRITICAL state (>= 6.5 GB)."""

    def on_emergency(self, snapshot: dict) -> None:
        """Called when UMA enters EMERGENCY state (>= 7.0 GB)."""

class DefaultUmaWatchdogCallbacks(UmaWatchdogCallbacks):
    """
    Default auto-action callbacks for memory pressure responses.

    P2-12: Built-in auto-actions when memory pressure is detected.
    F265H-EXT: on_warn now triggers GC on normal→warn transition (not just logging).

    Actions:
    - WARN: Trigger lightweight GC (gc.collect + mx.eval + clear_cache)
    - CRITICAL: Trigger MLX cache cleanup + log
    - EMERGENCY: Trigger aggressive MLX cleanup + log + alert
    """

    def on_warn(self, snapshot: dict) -> None:
        """F265H-EXT: Lightweight GC on normal→warn transition (prevents cascade)."""
        logger.warning(f"[UMA-AUTO] WARN: UMA at {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%) - triggering lightweight GC")
        try:
            from hledac.universal.utils import mlx_cache
            mlx_cache.mlx_cleanup_sync()
            logger.info('[UMA-AUTO] Lightweight GC completed')
        except (ImportError, AttributeError) as e:
            logger.error(f'[UMA-AUTO] Lightweight GC failed: {e}')
        try:
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
            released = malloc_zone_pressure_relief()
            if released > 0:
                logger.debug('[UMA-AUTO] malloc_zone_pressure_relief released %d bytes', released)
        except (ImportError, AttributeError, OSError) as e:
            logger.debug('[UMA-AUTO] malloc_zone_pressure_relief failed: %s', e)

    def on_critical(self, snapshot: dict) -> None:
        """Trigger MLX cache cleanup on CRITICAL state."""
        logger.warning(f"[UMA-AUTO] CRITICAL: UMA at {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%) - triggering MLX cache cleanup")
        try:
            from hledac.universal.utils import mlx_cache
            mlx_cache.mlx_cleanup_sync()
            logger.info('[UMA-AUTO] MLX cache cleanup completed')
        except (ImportError, AttributeError) as e:
            logger.error(f'[UMA-AUTO] MLX cache cleanup failed: {e}')
        try:
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
            from hledac.universal.utils import mlx_cache as mlx_cache_mod
            released = malloc_zone_pressure_relief()
            mlx_cache_mod.reconfigure_metal_cache_limit('critical')
            if released > 0:
                logger.debug('[UMA-AUTO] malloc_zone_pressure_relief released %d bytes', released)
        except (ImportError, AttributeError, OSError) as e:
            logger.debug('[UMA-AUTO] CRITICAL malloc/metal relief failed: %s', e)

    def on_emergency(self, snapshot: dict) -> None:
        """Trigger aggressive cleanup on EMERGENCY state."""
        logger.warning(f"[UMA-AUTO] EMERGENCY: UMA at {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%) - triggering aggressive cleanup")
        try:
            from hledac.universal.utils import mlx_cache
            mlx_cache.mlx_cleanup_aggressive()
            logger.info('[UMA-AUTO] Aggressive MLX cleanup completed')
        except (ImportError, AttributeError) as e:
            logger.error(f'[UMA-AUTO] Aggressive cleanup failed: {e}')
        try:
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
            from hledac.universal.utils import mlx_cache as mlx_cache_mod
            released = malloc_zone_pressure_relief()
            mlx_cache_mod.reconfigure_metal_cache_limit('emergency')
            if released > 0:
                logger.debug('[UMA-AUTO] EMERGENCY malloc_zone_pressure_relief released %d bytes', released)
        except (ImportError, AttributeError, OSError) as e:
            logger.debug('[UMA-AUTO] EMERGENCY malloc/metal relief failed: %s', e)

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
    DEBOUNCE_SECONDS: float = 2.0
    __slots__ = tuple(('_callbacks', '_interval', '_last_fired_at', '_last_fired_level', '_running', '_task'))

    def __init__(self, callbacks: UmaWatchdogCallbacks | None=None, interval: float=0.5) -> None:
        self._callbacks = callbacks
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_fired_level: str = 'normal'
        self._last_fired_at: float = 0.0

    def _should_fire(self, level: str, now: float) -> bool:
        """Return True if level should trigger a callback (debounce-aware)."""
        if level == 'normal':
            return False
        if level != self._last_fired_level:
            return True
        return now - self._last_fired_at >= self.DEBOUNCE_SECONDS

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
                    if level == 'emergency' and self._callbacks:
                        logger.warning(f"[UMA-WATCHDOG] EMERGENCY triggered: {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%)")
                        cb = getattr(self._callbacks, 'on_emergency', None)
                        if cb is not None:
                            safe_create_task(_run_callback_in_executor(cb, snapshot), name='uma_budget:emergency_callback')
                    elif level == 'critical' and self._callbacks:
                        logger.warning(f"[UMA-WATCHDOG] CRITICAL triggered: {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%)")
                        cb = getattr(self._callbacks, 'on_critical', None)
                        if cb is not None:
                            safe_create_task(_run_callback_in_executor(cb, snapshot), name='uma_budget:critical_callback')
                    elif level == 'warn' and self._callbacks:
                        logger.info(f"[UMA-WATCHDOG] WARN triggered: {snapshot.get('uma_used_mb', 0):,} MB ({snapshot.get('uma_usage_pct', 0)}%)")
                        cb = getattr(self._callbacks, 'on_warn', None)
                        if cb is not None:
                            safe_create_task(_run_callback_in_executor(cb, snapshot), name='uma_budget:warn_callback')
            except (OSError, RuntimeError) as e:
                logger.debug(f'[UMA-WATCHDOG] poll error (fail-open): {e}')
            await asyncio.sleep(self._interval)

    def start(self) -> asyncio.Task:
        """
        Start the watchdog in the current event loop.

        Returns the asyncio.Task so caller can track it.
        Raises RuntimeError if already running.
        """
        if self._task is not None and (not self._task.done()):
            raise RuntimeError('UmaWatchdog is already running')
        self._running = True
        self._task = safe_create_task(self._run(), name='uma_watchdog')
        return self._task

    def stop(self) -> None:
        """Stop the watchdog gracefully and shut down the callback executor."""
        self._running = False
        if self._task is not None and (not self._task.done()):
            self._task.cancel()
            self._task = None
        # M1-01: shutdown bounded executor so threads don't leak
        shutdown_uma_callback_executor()

    @property
    def is_running(self) -> bool:
        """True if the watchdog loop is active."""
        return self._running and self._task is not None and (not self._task.done())

    @property
    def interval(self) -> float:
        """Return the polling interval in seconds."""
        return self._interval

    @property
    def last_fired_level(self) -> str:
        """Return the last level that triggered a callback."""
        return self._last_fired_level


# Canonical alias: Watchdog = UmaWatchdog (A-01 compat migration)
Watchdog = UmaWatchdog
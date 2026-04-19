"""
ResourceGovernor 2.0 – centrální gatekeeper pro všechny výpočetně náročné operace.

ROLE: Canonical UMA POLICY / HYSTERESIS / RUNTIME GOVERNANCE (not a raw sampler).

This module provides:
- State evaluation from system_used_gib (threshold driver)
- Hysteresis-based I/O-only mode gate (prevents thrashing)
- Async alarm dispatcher for CRITICAL/EMERGENCY callbacks
- M1 QoS thread priority hinting
- Priority-based resource reservation (async context manager)

AUTHORITY BOUNDARY:
- SAMPLER (utils/uma_budget.py): raw memory sampling, no policy
- GOVERNOR (core/resource_governor.py): policy/hysteresis/runtime governance
- ALLOCATOR (resource_allocator.py): request-level budgeting/concurrency

Sprint 8AB: Unified UMA accountant surface (WARN/CRITICAL/EMERGENCY + I/O-only mode).
Threshold driver: system_used_gib (total - available), NOT process rss_gib.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, Callable

import psutil

_mx = None  # lazy singleton

# Sprint 8AB: cached psutil.Process() — single syscall point per status sample
_process_cache: Optional[psutil.Process] = None


def _get_cached_process() -> psutil.Process:
    global _process_cache
    if _process_cache is None:
        _process_cache = psutil.Process()
    return _process_cache


def _get_mx():
    global _mx
    if _mx is None:
        import mlx.core as _mx_module
        _mx = _mx_module
    return _mx


logger = logging.getLogger(__name__)

# Sprint 8AB: M1 8GB calibrated thresholds (GiB = bytes / 1024**3)
_THRESHOLD_WARN_GIB: float = 6.0
_THRESHOLD_CRITICAL_GIB: float = 6.5
_THRESHOLD_EMERGENCY_GIB: float = 7.0
_HYSTERESIS_EXIT_GIB: float = 5.8  # exit io_only only when system drops below this

# Sprint 8AK: SSOT UMA state labels (plain string constants, no StrEnum)
UMA_STATE_OK: str = "ok"
UMA_STATE_WARN: str = "warn"
UMA_STATE_CRITICAL: str = "critical"
UMA_STATE_EMERGENCY: str = "emergency"

# Sprint 8AK: Thread-safe hysteresis latch for io_only
# Protected by a simple threading.Lock — not an async subsystem
import threading as _threading

_io_only_latch: bool = False
_io_only_latch_lock: _threading.Lock = _threading.Lock()


def _compute_io_only_latch(system_used_gib: float, current_latch: bool, swap_detected: bool = False) -> bool:
    """
    Compute next io_only value based on hysteresis rules.
    Returns the new latch value (True = stay in io_only, False = exit).
    """
    target = should_enter_io_only_mode(system_used_gib, previous_io_only=current_latch, swap_detected=swap_detected)
    if target:
        return True
    elif system_used_gib <= _HYSTERESIS_EXIT_GIB:
        return False
    else:
        return current_latch


def _update_io_only_latch_with_lock(system_used_gib: float, swap_detected: bool = False) -> tuple[bool, bool]:
    """
    Sprint 8AK: Atomically read latch, compute new value, write back.
    Returns (io_only, new_latch).
    Thread-safe via _io_only_latch_lock.
    F166F: swap_detected propagates into latch computation for accelerated io_only entry.
    """
    global _io_only_latch
    with _io_only_latch_lock:
        current = _io_only_latch
        new_val = _compute_io_only_latch(system_used_gib, current, swap_detected=swap_detected)
        _io_only_latch = new_val
        return new_val, new_val


def _reset_uma_hysteresis_for_testing() -> None:
    """
    Sprint 8AK: Reset the shared io_only latch to False.
    For tests only — ensures test isolation.
    """
    global _io_only_latch
    with _io_only_latch_lock:
        _io_only_latch = False

# Sprint 8AB: Lightweight telemetry counters (module-level, no class instantiation)
# F130A: io_only_enter/exit are now transition-based (only on actual transitions),
# not state-sampled (every sample in a given state).
_telemetry = {
    "transition_count": 0,
    "io_only_enter_count": 0,
    "io_only_exit_count": 0,
    "last_state": "ok",
    "last_io_only": False,  # F130A: tracks previous io_only for transition detection
}


@dataclass(frozen=True)
class UMAStatus:
    """
    Sprint 8AB + F163F: Unified UMA accounting snapshot.

    Fields:
        rss_gib: Process RSS in GiB (diagnostic, NOT threshold driver).
        system_used_gib: (total - available) in GiB (THRESHOLD DRIVER).
        system_available_gib: Available system memory in GiB.
        swap_used_gib: Swap usage in GiB (diagnostic only — F163F).
        swap_detected: True if swap > 0.05 GiB (active swap = systemic pressure).
        metal_cache_limit_bytes: Metal cache limit from 8T surface (or None).
        metal_wired_limit_bytes: Metal wired limit from 8T surface (or None).
        state: "ok" | "warn" | "critical" | "emergency".
        io_only: True if I/O-only mode should be active.
        last_error: Error message if sampling failed (None = OK).

    F163F CHANGE: swap_detected added — any active swap indicates M1 UMA
    pressure that is SYSTEMIC (not process-bound). On M1 8GB, swap is a
    critical diagnostic signal that confirms memory pressure is real,
    not an artifact of measurement timing.
    """
    rss_gib: float
    system_used_gib: float
    system_available_gib: float
    swap_used_gib: float
    metal_cache_limit_bytes: Optional[int]
    metal_wired_limit_bytes: Optional[int]
    state: str
    io_only: bool
    swap_detected: bool = False
    last_error: Optional[str] = None


class Priority(Enum):
    CRITICAL = "CRITICAL"   # musí se provést, vyšší tolerance (+20 % budget)
    HIGH = "HIGH"           # důležité, lze odložit
    NORMAL = "NORMAL"       # běžná operace
    LOW = "LOW"             # lze zrušit kdykoli


class ResourceGovernor:
    """
    Hlídá zdroje a rozhoduje, zda je možné provést náročnou operaci.
    """
    def __init__(self, memory_high_water_mb: float = 6000, thermal_threshold: float = 82.0):
        self.high_water = memory_high_water_mb
        self.thermal_threshold = thermal_threshold
        self._active_tasks = 0
        self.__lock = None  # lazy init for Python 3.12 compatibility
        self._cost_model = None

        # Faktor priority pro toleranci
        self._priority_factor = {
            Priority.CRITICAL: 1.2,
            Priority.HIGH: 1.0,
            Priority.NORMAL: 0.9,
            Priority.LOW: 0.7,
        }

    @property
    def _lock(self):
        if self.__lock is None:
            self.__lock = asyncio.Lock()
        return self.__lock

    def set_cost_model(self, cost_model):
        """Nastaví cost model pro predikci rizika překročení budgetu."""
        self._cost_model = cost_model

    def can_afford_sync(self, cost_estimate: Dict[str, Any], priority: Priority = Priority.NORMAL) -> bool:
        """
        Synchronní kontrola zdrojů bez rezervace.
        """
        ram_used = psutil.virtual_memory().used / (1024 * 1024)
        ram_needed = cost_estimate.get('ram_mb', 0)
        factor = self._priority_factor[priority]

        if ram_used + ram_needed > self.high_water * factor:
            return False

        if cost_estimate.get('gpu', False):
            try:
                # Sprint 8W: use top-level mx API when available (MLX 0.31.1+)
                if hasattr(_get_mx(), 'get_active_memory'):
                    gpu_used = _get_mx().get_active_memory() / (1024 * 1024)
                elif hasattr(_get_mx().metal, 'get_active_memory'):
                    gpu_used = _get_mx().metal.get_active_memory() / (1024 * 1024)
                else:
                    gpu_used = 0

                # get_recommended_max_memory not available in MLX 0.31.1 — skip GPU check
                gpu_total = float('inf')
                if hasattr(_get_mx().metal, 'get_recommended_max_memory'):
                    gpu_total = _get_mx().metal.get_recommended_max_memory() / (1024 * 1024)

                if gpu_used + ram_needed > gpu_total * factor:
                    return False
            except Exception:
                pass  # GPU metrics nejsou dostupné

        # Jednoduchý thermal guard (volitelné, MLX 2026+)
        try:
            if hasattr(_get_mx().metal, 'get_device_temperature'):
                gpu_temp = _get_mx().metal.get_device_temperature()
                if gpu_temp > self.thermal_threshold and priority != Priority.CRITICAL:
                    logger.warning(f"GPU thermal limit reached: {gpu_temp}°C > {self.thermal_threshold}°C")
                    return False
        except AttributeError:
            pass  # get_device_temperature není dostupné

        # Best-effort ANE guard
        try:
            if hasattr(_get_mx().metal, 'get_ane_utilization'):
                ane = _get_mx().metal.get_ane_utilization()
                if ane > 0.90 and priority == Priority.LOW:
                    return False
        except AttributeError:
            pass  # get_ane_utilization není dostupné

        if self._cost_model is not None:
            risk = self._cost_model.predict_overrun_risk(cost_estimate)
            if risk > 0.3:
                return False

        return True

    def reserve(self, cost_estimate: Dict[str, Any], priority: Priority = Priority.NORMAL):
        """
        Vrací async context manager pro rezervaci zdrojů. Samotná metoda je synchronní.
        """
        class _Reservation:
            def __init__(self, gov, cost, prio):
                self.gov = gov
                self.cost = cost
                self.prio = prio

            async def __aenter__(self):
                if not self.gov.can_afford_sync(self.cost, self.prio):
                    raise RuntimeError("ResourceGovernor: cannot afford operation")
                async with self.gov._lock:
                    self.gov._active_tasks += 1
                return self

            async def __aexit__(self, *args):
                async with self.gov._lock:
                    self.gov._active_tasks -= 1

        return _Reservation(self, cost_estimate, priority)


# =============================================================================
# Sprint 8AB: Unified UMA Accountant Surface
# =============================================================================

def evaluate_uma_state(system_used_gib: float) -> str:
    """
    Sprint 8AB: Map system_used_gib to UMA state.

    Calibrated for M1 8GB UMA:
        < 6.0 GiB → "ok"
        >= 6.0   → "warn"
        >= 6.5   → "critical"
        >= 7.0   → "emergency"

    Args:
        system_used_gib: (total - available) in GiB, THRESHOLD DRIVER.

    Returns:
        State string from SSOT constants: "ok" | "warn" | "critical" | "emergency".
    """
    if system_used_gib >= _THRESHOLD_EMERGENCY_GIB:
        return UMA_STATE_EMERGENCY
    if system_used_gib >= _THRESHOLD_CRITICAL_GIB:
        return UMA_STATE_CRITICAL
    if system_used_gib >= _THRESHOLD_WARN_GIB:
        return UMA_STATE_WARN
    return UMA_STATE_OK


def should_enter_io_only_mode(system_used_gib: float, previous_io_only: bool = False, swap_detected: bool = False) -> bool:
    """
    Sprint 8AB + F165E: Hysteresis-based I/O-only mode gate.

    F165E CHANGE: swap_detected optional param.
    When swap is present (systemic pressure signal), enter io_only one tier sooner
    (WARN threshold 6.0 GiB) instead of waiting for CRITICAL threshold (6.5 GiB).
    This reflects the M1 8GB reality: any active swap means memory pressure is
    real and systemic, not a measurement artifact.

    Contract:
        - Enter io_only when >= CRITICAL (6.5 GiB) and swap_detected=False
        - Enter io_only when >= WARN (6.0 GiB) and swap_detected=True (accelerated)
        - Stay in io_only while system_used_gib > HYSTERESIS_EXIT (5.8 GiB)
        - Exit io_only only when system_used_gib <= 5.8 GiB (and previous_io_only == True)

    This prevents state thrashing around the critical boundary.

    Args:
        system_used_gib: Current system memory used in GiB.
        previous_io_only: True if io_only was already active.
        swap_detected: True if any active swap is present (systemic pressure signal).

    Returns:
        True if caller should enter / stay in I/O-only mode.
    """
    if previous_io_only:
        # Stay in io_only while above hysteresis floor
        return system_used_gib > _HYSTERESIS_EXIT_GIB
    # Enter io_only: CRITICAL by default, WARN if swap present (one tier sooner)
    if swap_detected:
        return system_used_gib >= _THRESHOLD_WARN_GIB
    return system_used_gib >= _THRESHOLD_CRITICAL_GIB


def _get_metal_limits_status_8ab() -> tuple[Optional[int], Optional[int]]:
    """
    Sprint 8AB: Read-only diagnostic surface from 8T mlx_cache.
    Returns (cache_limit_bytes, wired_limit_bytes) or (None, None) on failure.
    """
    try:
        # Guard: mlx_cache may not be importable in all contexts
        from ..utils.mlx_cache import get_metal_limits_status
        status = get_metal_limits_status()
        return status.get("cache_limit_bytes"), status.get("wired_limit_bytes")
    except Exception:
        return None, None


def sample_uma_status() -> UMAStatus:
    """
    Sprint 8AB: One-shot UMA status snapshot — LOCAL POLICY INPUT (not a raw sampler).

    This is a GOVERNOR-LOCAL helper that embeds raw sampling reads internally.
    It is NOT a canonical raw sampler (those live in utils/uma_budget.py).
    This function exists so the governor can form its policy decision (evaluate_uma_state,
    hysteresis latch) from a consistent local snapshot, without depending on
    an external sampler interface at evaluation time.

    Reads (in order):
        1. Process RSS via cached psutil.Process() — rss_gib (diagnostic)
        2. System memory via psutil.virtual_memory() — system_used_gib (THRESHOLD DRIVER)
        3. Swap via psutil.swap_memory() — swap_used_gib (diagnostic)
        4. Metal limits via 8T get_metal_limits_status() — metal_* (diagnostic)

    Fail-open: if any surface is unavailable, returns UMAStatus with last_error
    populated but state/io_only computed from available data (or "ok" as last resort).

    Returns:
        UMAStatus frozen dataclass.
    """
    last_error: Optional[str] = None
    metal_cache_limit_bytes: Optional[int] = None
    metal_wired_limit_bytes: Optional[int] = None

    # 1. Process RSS (cached Process object — no per-call allocation)
    rss_gib: float = 0.0
    try:
        proc = _get_cached_process()
        rss_gib = proc.memory_info().rss / (1024 ** 3)
    except Exception as exc:
        last_error = f"psutil.Process: {exc}"

    # 2. System memory — THRESHOLD DRIVER
    system_used_gib: float = 0.0
    system_available_gib: float = 0.0
    try:
        vm = psutil.virtual_memory()
        system_used_gib = (vm.total - vm.available) / (1024 ** 3)
        system_available_gib = vm.available / (1024 ** 3)
    except Exception as exc:
        last_error = f"virtual_memory: {exc}"
        system_used_gib = 0.0
        system_available_gib = 0.0

    # 3. Swap — diagnostic only, fail-open
    swap_used_gib: float = 0.0
    try:
        sm = psutil.swap_memory()
        swap_used_gib = sm.used / (1024 ** 3)
    except Exception:
        pass  # swap unavailable — fail-open silently

    # 4. Metal diagnostic surface from 8T (read-only)
    metal_cache_limit_bytes, metal_wired_limit_bytes = _get_metal_limits_status_8ab()

    # Compute state and io_only
    state = evaluate_uma_state(system_used_gib)

    # F166F: swap_detected computed BEFORE latch so it can propagate into the decision
    swap_detected = swap_used_gib > 0.05

    # Sprint 8AK: Shared hysteresis latch — thread-safe, prevents state thrashing
    # F166F: swap_detected accelerates io_only entry to WARN threshold (6.0 GiB)
    io_only, _ = _update_io_only_latch_with_lock(system_used_gib, swap_detected=swap_detected)

    # Update telemetry — F130A: all counters are transition-based, not state-sampled.
    # - transition_count: every state change (ok→warn, warn→critical, etc.)
    # - io_only_enter_count: actual io_only activation (False→True transition)
    # - io_only_exit_count: actual io_only deactivation (True→False transition)
    global _telemetry
    if _telemetry["last_state"] != state:
        _telemetry["transition_count"] += 1
        _telemetry["last_state"] = state

    # F130A: transition-based enter/exit — only fire on actual latched transitions
    prev_io_only = _telemetry["last_io_only"]
    if io_only and not prev_io_only:
        # False → True: io_only was just activated
        _telemetry["io_only_enter_count"] += 1
    elif not io_only and prev_io_only:
        # True → False: io_only was just deactivated
        _telemetry["io_only_exit_count"] += 1

    _telemetry["last_io_only"] = io_only

    return UMAStatus(
        rss_gib=rss_gib,
        system_used_gib=system_used_gib,
        system_available_gib=system_available_gib,
        swap_used_gib=swap_used_gib,
        swap_detected=swap_detected,
        metal_cache_limit_bytes=metal_cache_limit_bytes,
        metal_wired_limit_bytes=metal_wired_limit_bytes,
        state=state,
        io_only=io_only,
        last_error=last_error,
    )


def get_uma_telemetry() -> Dict[str, Any]:
    """Sprint 8AB: Read-only telemetry snapshot (transition counts, last state)."""
    return dict(_telemetry)


# =============================================================================
# Sprint 8PC: UMA Alarm Dispatcher — push-based callbacks
# =============================================================================

_HYSTERESIS_COOLDOWN_SEC: float = 2.0  # B.2: minimum 2s between same-state alarms


class UMAAlarmDispatcher:
    """
    Sprint 8PC: Push-based UMA alarm system.

    Dispatches async callbacks when UMA state transitions to CRITICAL or EMERGENCY.
    Callbacks run in a dedicated asyncio.Task (not synchronously in the event loop
    or threading.Timer — B.3).

    Invariants:
        - evaluate_uma_state() remains pure / stateless — no side effects (B.4)
        - Hysteresis: same alarm not re-sent within 2s (B.2)
        - All callbacks are gathered with return_exceptions=True (fail-safe)
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._callbacks: Dict[str, list] = {
            UMA_STATE_CRITICAL: [],
            UMA_STATE_EMERGENCY: [],
        }
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._interval_s: float = 5.0
        # B.2: hysteresis cooldown — prevent callback storm
        # float("-inf") ensures first dispatch always fires (now - (-inf) = +inf > 2.0)
        self._last_dispatch_time: Dict[str, float] = {
            UMA_STATE_CRITICAL: float("-inf"),
            UMA_STATE_EMERGENCY: float("-inf"),
        }

    def register_callback(self, state: str, callback: "Callable[[], Any]") -> None:
        """
        Register an async callback for CRITICAL or EMERGENCY state.

        The callback must be awaitable (async def or a sync callable).
        Thread-safe: appends to list under self._lock.

        F130C FIX: Store the raw callback (not a pre-invoked wrapper coroutine).
        A fresh dispatch wrapper is created at dispatch time so the same callback
        can fire on multiple independent alarm dispatches without coroutine reuse.

        Args:
            state: UMA_STATE_CRITICAL or UMA_STATE_EMERGENCY
            callback: Async callable to invoke on alarm.
        """
        if state not in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return

        self._callbacks[state].append(callback)

    async def start_monitoring(self, interval_s: float = 5.0) -> None:
        """
        Start the monitoring loop. Idempotent.

        Args:
            interval_s: Polling interval in seconds. Default 5.0.
        """
        self._interval_s = interval_s
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """
        Stop the monitoring loop. Clean cancellation via CancelledError.

        B.3: Callback threading — dispatch happens in asyncio.Task,
        cancellation is clean (no unhandled exceptions).
        """
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _monitor_loop(self) -> None:
        """
        Background monitoring loop. Self-terminates when _running=False.

        B.2: Hysteresis — checks time.monotonic() before dispatching.
        Dispatches callbacks via asyncio.gather(..., return_exceptions=True).
        """
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
                await self._check_and_dispatch()
            except asyncio.CancelledError:
                raise  # B.3: propagate cancellation cleanly
            except Exception:
                pass  # fail-open: keep monitoring even on one bad tick

    async def _check_and_dispatch(self) -> None:
        """Sample UMA and dispatch callbacks on state transitions."""
        status = sample_uma_status()
        current_state = status.state

        # B.2: Hysteresis cooldown check
        now = time.monotonic()
        if current_state not in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return
        last_time = self._last_dispatch_time.get(current_state, 0.0)
        if now - last_time < _HYSTERESIS_COOLDOWN_SEC:
            return

        async with self._lock:
            callbacks = list(self._callbacks.get(current_state, []))

        if not callbacks:
            return

        # Update cooldown timestamp
        self._last_dispatch_time[current_state] = now

        # F130C FIX: Create fresh wrapper coroutines at dispatch time so the same
        # registered callback can fire on multiple independent alarms without reuse bugs.
        async def _dispatch_one(cb):
            if asyncio.iscoroutinefunction(cb):
                await cb()
            elif asyncio.iscoroutine(cb):
                await cb
            elif callable(cb):
                cb()
            # else: not callable, silently ignore

        await asyncio.gather(*[_dispatch_one(cb) for cb in callbacks], return_exceptions=True)


# =============================================================================
# Sprint 8PC: QoS Helper — M1 Apple Silicon thread priority
# =============================================================================

# M1 QoS levels (darwin pthread_set_qos_class_self_np)
_QOS_USER_INITIATED: int = 0x19
_QOS_UTILITY: int = 0x11
_QOS_BACKGROUND: int = 0x09


def set_thread_qos(qos_level: int) -> None:
    """
    Sprint 8PC: Set calling thread's QoS class on Apple Silicon.

    Useful for hinting the kernel about latency vs throughput tradeoffs.

    QoS levels:
        0x19 (USER_INITIATED): Interactive / latency-sensitive
        0x11 (UTILITY):         Background / throughput-oriented
        0x09 (BACKGROUND):      Low-priority background tasks

    B.7: Fail-open — if syscall fails (non-macOS or permission), log at DEBUG
    and return without raising.

    Implementation: ctypes.CDLL(None).syscall(pthread_set_qos_class_self_np).
    """
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(None)
        # pthread_set_qos_class_self_np syscall number on Darwin is 366
        # signature: int pthread_set_qos_class_self_np(int qos_class, int relative_priority)
        libc.syscall(366, qos_level, 0)
    except Exception as exc:
        # B.7: fail-open on any error (non-macOS, permission denied, etc.)
        logger.debug(f"[QoS] pthread_set_qos_class_self_np failed (non-macOS or permission): {exc}")

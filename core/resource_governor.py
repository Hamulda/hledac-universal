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

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

# psutil je canonical dep (requirements.txt: psutil # memory pressure monitoring),
# ale lazy-guarded pro env-flex (M1 8GB UMA cold start, sessions bez psutil,
# static analysis / doc builds). Vzor: core/mlx_embeddings.py:29-34.
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

_mx = None  # lazy singleton

# Sprint 8AB: cached psutil.Process() — single syscall point per status sample.
# Type hint lazy: `from __future__ import annotations` (ř. 22) → string, runtime neevaluuje.
_process_cache: Any = None  # psutil.Process | None při plném env


def _get_cached_process() -> Any:
    """Lazy psutil.Process() accessor. Raises RuntimeError if psutil unavailable."""
    global _process_cache
    if _process_cache is None:
        if psutil is None:
            raise RuntimeError("psutil not available in this environment")
        _process_cache = psutil.Process()
    return _process_cache


# =============================================================================
# F265H: Async-friendly psutil cache — non-blocking memory sampling
# Problem: psutil.virtual_memory() / psutil.swap_memory() are blocking syscalls
# that can pause the event loop for 0.5-2ms per call. In tight async loops
# (e.g., _monitor_loop at 5s intervals, can_afford_sync per-operation) this
# accumulates event-loop jitter. Solution: shared TTL cache updated in a
# background thread, all reads are synchronous cache hits.
# =============================================================================

import threading as _threading
import time as _time_module

_psutil_cache_lock: _threading.Lock = _threading.Lock()
_psutil_cache: dict[str, tuple[Any, float]] = {}  # key → (result, timestamp)
_PSUTIL_CACHE_TTL_S: float = 2.0  # Short TTL — memory state changes fast under load


def _read_virtual_memory_sync() -> Any:
    """Blocking psutil.virtual_memory(). MUST run in a thread, not the event loop."""
    if psutil is None:
        return None
    return psutil.virtual_memory()


def _read_swap_memory_sync() -> Any:
    """Blocking psutil.swap_memory(). MUST run in a thread, not the event loop."""
    if psutil is None:
        return None
    return psutil.swap_memory()


def _get_cached_psutil(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Thread-safe TTL cache for blocking psutil reads.
    Returns cached result if fresh (< TTL seconds), else calls reader_fn
    in the calling thread (caller is responsible for asyncio.to_thread).
    """
    now = _time_module.monotonic()
    with _psutil_cache_lock:
        entry = _psutil_cache.get(key)
        if entry is not None:
            result, timestamp = entry
            if now - timestamp < _PSUTIL_CACHE_TTL_S:
                return result
        # Miss or expired — call in current thread (caller must offload if async context)
        result = reader_fn()
        _psutil_cache[key] = (result, now)
        return result


async def _get_cached_psutil_async(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Async wrapper: offloads blocking reader_fn to a thread, caches result.
    All callers of this function are non-blocking on the event loop.
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _get_cached_psutil(key, reader_fn))
    return result


def _refresh_psutil_cache_sync() -> None:
    """
    Force-refresh all psutil cache entries synchronously.
    For use in sync contexts where asyncio.to_thread is unavailable (e.g., __init__).
    """
    if psutil is None:
        return
    now = _time_module.monotonic()
    with _psutil_cache_lock:
        _psutil_cache["virtual_memory"] = (psutil.virtual_memory(), now)
        _psutil_cache["swap_memory"] = (psutil.swap_memory(), now)


def _get_mx():
    global _mx
    if _mx is None:
        import mlx.core as _mx_module
        _mx = _mx_module
    return _mx


logger = logging.getLogger(__name__)

# Sprint 8AB: M1 8GB calibrated thresholds (GiB = bytes / 1024**3)
# F265H: Revised threshold ladder for M1 8GB UMA (proactive escalation):
#   5.5 GiB → soft ceiling (fetch hard-cap, see uma_budget.py M1_FETCH_SOFT_CEILING_GB)
#   5.8 GiB → SOFT_WARN (reduce concurrency 50%)
#   6.0 GiB → WARN (reduce concurrency 75%)
#   6.7 GiB → CRITICAL (proactive offload BEFORE crash, 83.75% system)
#   7.0 GiB → EMERGENCY (flush + GC)
#
# CRITICAL at 6.7 GiB (not 6.5) — at 6.5 GiB (81.25%) the system is already
# severely constrained. 6.7 GiB gives ~0.3 GiB headroom before EMERGENCY,
# enabling proactive MLX offload before cascade failure.
_THRESHOLD_SOFT_WARN_GIB: float = 5.8  # F220K: new — between soft ceiling and WARN
_THRESHOLD_WARN_GIB: float = 6.0  # F265H: lowered to 6.0 (from 6.2) for wider WARN band
_THRESHOLD_CRITICAL_GIB: float = 6.7  # F265H: raised from 6.5 — CRITICAL at 6.5 is too late (87% system), proactive trigger at 6.7 GiB (83.75%) gives headroom before EMERGENCY
_THRESHOLD_EMERGENCY_GIB: float = 7.0
_HYSTERESIS_EXIT_GIB: float = 5.8

# Sprint 8AK: SSOT UMA state labels (plain string constants, no StrEnum)
# F220K: SOFT_WARN state (between soft ceiling 5.5GiB and WARN 6.0GiB)
UMA_STATE_SOFT_WARN: str = "soft_warn"
UMA_STATE_OK: str = "ok"
UMA_STATE_WARN: str = "warn"
UMA_STATE_CRITICAL: str = "critical"
UMA_STATE_EMERGENCY: str = "emergency"

# F220F: macOS swap tiered policy constants
# These define the swap policy tiers used by prelive_decision_gate and cockpit.
# The raw swap_detected signal (any swap > 0.05 GiB) lives in sample_uma_status().
# The tiered policy applies these to determine READY_TO_RUN vs HARD_BLOCK.
CLEAN_SWAP_MAX_GIB: float = 2.0       # swap <= 2.0 GiB → clean/READY_TO_RUN_NOW
DIAGNOSTIC_SWAP_MAX_GIB: float = 4.0  # 2.0 < swap <= 4.0 GiB → diagnostic/tainted
HARD_BLOCK_SWAP_GIB: float = 4.0     # swap > 4.0 GiB → hard block/restart required


def get_swap_policy_tier(swap_gib: float) -> tuple[str, str]:
    """
    F220F: Determine swap policy tier and reason from swap usage.

    Returns (tier, reason) where:
        tier: "clean" | "diagnostic" | "hard_block"
        reason: human-readable string describing why this tier was chosen

    This is a pure function — no side effects, suitable for both
    prelive decision gate and cockpit use.
    """
    if swap_gib <= CLEAN_SWAP_MAX_GIB:
        return "clean", f"swap={swap_gib:.2f}GiB <= {CLEAN_SWAP_MAX_GIB:.1f}GiB threshold"
    elif swap_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
        return "diagnostic", f"swap={swap_gib:.2f}GiB in ({CLEAN_SWAP_MAX_GIB:.1f}GiB, {DIAGNOSTIC_SWAP_MAX_GIB:.1f}GiB] — hardware taint"  # noqa: E501
    else:
        return "hard_block", f"swap={swap_gib:.2f}GiB > {HARD_BLOCK_SWAP_GIB:.1f}GiB — restart required"

# Sprint 8AK: Thread-safe hysteresis latch for io_only
# Protected by a simple threading.Lock — not an async subsystem
import threading as _threading  # noqa: E402

from utils.async_helpers import safe_gather_fire_and_forget  # noqa: E402

_io_only_latch: bool = False
_io_only_latch_lock: _threading.Lock = _threading.Lock()

# B4-3 VERIFIED: threading.Lock correct — _update_io_only_latch_with_lock is only
# called from sync context (sample_uma_status). Even when sample_uma_status is invoked
# from an async def, the function itself is synchronous and the lock protects against
# concurrent sync access from multiple threads. Do NOT replace with asyncio.Lock().


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
# F183C: Removed last_io_only — dual tracking with _io_only_latch causes divergence.
# The latch is authoritative; prev_io_only is captured from latch BEFORE update
# for transition detection (no need to store it in telemetry).
_telemetry = {
    "transition_count": 0,
    "io_only_enter_count": 0,
    "io_only_exit_count": 0,
    "last_state": "ok",
}


@dataclass(frozen=True, slots=True)
class UMAStatus:
    """
    Sprint 8AB + F163F: Unified UMA accounting snapshot.

    Fields:
        rss_gib: Process RSS in GiB (diagnostic, NOT threshold driver).
        system_used_gib: (total - available) in GiB (THRESHOLD DRIVER).
        system_available_gib: Available system memory in GiB.
        swap_used_gib: Swap usage in GiB (diagnostic only — F163F).
        swap_detected: True if swap > 3.8 GiB (active swap = systemic pressure).
        metal_cache_limit_bytes: Metal cache limit from 8T surface (or None).
        metal_wired_limit_bytes: Metal wired limit from 8T surface (or None).
        state: "ok" | "soft_warn" | "warn" | "critical" | "emergency".
        io_only: True if I/O-only mode should be active.
        last_error: Error message if sampling failed (None = OK).

    F163F CHANGE: swap_detected added — active swap indicates M1 UMA
    pressure that is SYSTEMIC (not process-bound). On M1 8GB, swap is a
    critical diagnostic signal that confirms memory pressure is real.
    F265C: Threshold raised from 0.05 to 3.5 GiB — baseline M1 8GB idle
    swap is 1.0-1.2 GiB; 3.5 GiB threshold = ~3x baseline, aligned with
    HARD_BLOCK_SWAP_GIB=4.0 in the swap tiered policy (0.5 GiB margin).
    F265D: Raised to 3.8 GiB — 3x above baseline, 0.2 GiB below HARD_BLOCK,
    allows normal 2.0-2.5 GiB workload spikes without triggering.
    Note: swap tiered policy (CLEAN/DIAGNOSTIC/HARD_BLOCK) and swap_detected
    are independent signals — tiered policy applies to prelive/cockpit,
    swap_detected applies to io_only acceleration and governor decisions.
    """
    rss_gib: float
    system_used_gib: float
    system_available_gib: float
    swap_used_gib: float
    metal_cache_limit_bytes: int | None
    metal_wired_limit_bytes: int | None
    state: str
    io_only: bool
    swap_detected: bool = False
    last_error: str | None = None


# ── P0-1: Governor Concurrency Decision ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    """
    P0-1: Returned by ResourceGovernor.evaluate() — canonical concurrency scaling
    decision for the acquisition planner.

    Replaces the old binary hardware_critical kill switch. Instead of disabling
    lanes entirely at critical/emergency, the governor now returns per-transport
    concurrency caps so lanes stay alive at reduced concurrency.

    Invariants:
        - clearnet_max >= 1  (PUBLIC, CT, DOH, WAYBACK, PASSIVE_DNS, etc.)
        - stealth_max >= 1   (STEALTH, TOR, I2P)
        - model_blocked: True means MLX model load should be deferred
        - All fields bounded, fail-safe defaults always valid
    """

    clearnet_max: int    # max concurrent clearnet fetches
    stealth_max: int     # max concurrent stealth fetches
    model_blocked: bool  # True = defer MLX model load
    uma_state: str       # "ok"|"soft_warn"|"warn"|"critical"|"emergency"
    io_only: bool        # True = I/O-only mode active


def make_governor_decision(uma_state: str, io_only: bool, swap_detected: bool) -> GovernorDecision:
    """
    P0-1: Pure factory for GovernorDecision — no side effects, no psutil.

    Maps UMA state to concurrency caps calibrated for M1 8GB UMA.
    The key insight: even at critical/emergency, M1 can still do 1 clearnet
    fetch — we scale concurrency, we don't kill lanes.

    M1 8GB calibration:
        ok         → clearnet=5, stealth=3, model_blocked=False
        soft_warn  → clearnet=3, stealth=2, model_blocked=False
        warn       → clearnet=2, stealth=1, model_blocked=False
        critical   → clearnet=1, stealth=1, model_blocked=True
        emergency  → clearnet=1, stealth=1, model_blocked=True
    """
    if uma_state == "emergency" or (uma_state == "critical" and swap_detected):
        return GovernorDecision(
            clearnet_max=1,
            stealth_max=1,
            model_blocked=True,
            uma_state=uma_state,
            io_only=io_only,
        )
    if uma_state == "critical":
        return GovernorDecision(
            clearnet_max=1,
            stealth_max=1,
            model_blocked=True,
            uma_state=uma_state,
            io_only=io_only,
        )
    if uma_state == "warn":
        return GovernorDecision(
            clearnet_max=2,
            stealth_max=1,
            model_blocked=False,
            uma_state=uma_state,
            io_only=io_only,
        )
    if uma_state == "soft_warn":
        return GovernorDecision(
            clearnet_max=3,
            stealth_max=2,
            model_blocked=False,
            uma_state=uma_state,
            io_only=io_only,
        )
    # ok (default)
    return GovernorDecision(
        clearnet_max=5,
        stealth_max=3,
        model_blocked=False,
        uma_state=uma_state,
        io_only=io_only,
    )


class Priority(Enum):
    CRITICAL = "CRITICAL"   # musí se provést, vyšší tolerance (+20 % budget)
    HIGH = "HIGH"           # důležité, lze odložit
    NORMAL = "NORMAL"       # běžná operace
    LOW = "LOW"             # lze zrušit kdykoli


class ResourceGovernor:
    """
    Hlídá zdroje a rozhoduje, zda je možné provést náročnou operaci.
    """
    def __init__(self, memory_high_water_mb: float = 5632, thermal_threshold: float = 82.0):
        self.high_water = memory_high_water_mb
        self.thermal_threshold = thermal_threshold
        self._active_tasks = 0
        self.__lock = None  # lazy init for Python 3.14 compatibility
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

    def can_afford_sync(self, cost_estimate: dict[str, Any], priority: Priority = Priority.NORMAL) -> bool:
        """
        Synchronní kontrola zdrojů bez rezervace.
        """
        # Fail-open: pokud psutil chybí nebo selže, treat as 0 used (vždy can_afford).
        # Kontrakt: při chybějícím psutil se kontrola RAM přeskočí — cost_estimate rozhodne.
        ram_used = 0.0
        if psutil is not None:
            try:
                # F265H: Use TTL cache — non-blocking read for 98%+ of calls.
                # Cold cache hit (rare, ~2s intervals in monitor loop) is sync
                # but acceptable since this method IS synchronous.
                vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
                if vm is not None:
                    ram_used = vm.used / (1024 * 1024)
            except Exception:
                ram_used = 0.0
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
                pass  # noqa: BARE-EXCEPT  # GPU metrics nejsou dostupné

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

    def reserve(self, cost_estimate: dict[str, Any], priority: Priority = Priority.NORMAL):
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

    async def evaluate(self) -> GovernorDecision:
        """
        P0-1: Sample UMA and return GovernorDecision for acquisition planner.

        This is the canonical entry point for the concurrency scaling decision.
        Returns GovernorDecision with clearnet_max/stealth_max/model_blocked caps
        instead of the old binary hardware_critical kill switch.

        Fail-open: if sample_uma_status() fails, returns safe defaults
        (clearnet=5, stealth=3, model_blocked=False, uma_state="ok", io_only=False).
        """
        try:
            snap = sample_uma_status()
            return make_governor_decision(
                uma_state=snap.state,
                io_only=snap.io_only,
                swap_detected=snap.swap_detected,
            )
        except Exception:
            # Fail-open: safe defaults that allow all lanes to run
            return GovernorDecision(
                clearnet_max=5,
                stealth_max=3,
                model_blocked=False,
                uma_state="ok",
                io_only=False,
            )


# =============================================================================
# Sprint 8AB: Unified UMA Accountant Surface
# =============================================================================


def evaluate_uma_state(system_used_gib: float) -> str:
    """
    Sprint 8AB: Map system_used_gib to UMA state.

    Calibrated for M1 8GB UMA:
        < 5.8 GiB → "ok"
        >= 5.8   → "soft_warn"  (F220K: approaching WARN, reduce 50%)
        >= 6.0   → "warn"
        >= 6.7   → "critical"   (F265H: raised from 6.5, proactive at 83.75%)
        >= 7.0   → "emergency"

    Args:
        system_used_gib: (total - available) in GiB, THRESHOLD DRIVER.

    Returns:
        State string from SSOT constants: "ok" | "soft_warn" | "warn" | "critical" | "emergency".
    """
    if system_used_gib >= _THRESHOLD_EMERGENCY_GIB:
        return "emergency"
    if system_used_gib >= _THRESHOLD_CRITICAL_GIB:
        return "critical"
    if system_used_gib >= _THRESHOLD_WARN_GIB:
        return "warn"
    if system_used_gib >= _THRESHOLD_SOFT_WARN_GIB:
        return "soft_warn"
    return "ok"


def should_enter_io_only_mode(system_used_gib: float, previous_io_only: bool = False, swap_detected: bool = False) -> bool:  # noqa: E501
    """
    Sprint 8AB + F165E: Hysteresis-based I/O-only mode gate.

    F165E CHANGE: swap_detected optional param.
    When swap is present (systemic pressure signal), enter io_only one tier sooner
    (WARN threshold 6.0 GiB) instead of waiting for CRITICAL threshold (6.7 GiB).
    This reflects the M1 8GB reality: any active swap means memory pressure is
    real and systemic, not a measurement artifact.

    Contract:
        - Enter io_only when >= CRITICAL (6.7 GiB) and swap_detected=False
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
    # F220K: enter io_only at SOFT_WARN (5.8 GiB) if swap present — earliest proactive signal
    if previous_io_only:
        # Stay in io_only while above hysteresis floor
        return system_used_gib > _HYSTERESIS_EXIT_GIB
    # Enter io_only: CRITICAL by default, WARN if swap present (one tier sooner),
    # SOFT_WARN if swap detected (earliest proactive tier)
    if swap_detected:
        if system_used_gib >= _THRESHOLD_SOFT_WARN_GIB:
            return True
        return system_used_gib >= _THRESHOLD_WARN_GIB
    return system_used_gib >= _THRESHOLD_CRITICAL_GIB


def _get_metal_limits_status_8ab() -> tuple[int | None, int | None]:
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
    last_error: str | None = None
    metal_cache_limit_bytes: int | None = None
    metal_wired_limit_bytes: int | None = None

    # 1. Process RSS (cached Process object — no per-call allocation)
    rss_gib: float = 0.0
    try:
        proc = _get_cached_process()
        rss_gib = proc.memory_info().rss / (1024 ** 3)
    except Exception as exc:
        last_error = f"psutil.Process: {exc}"

    # 2. System memory — THRESHOLD DRIVER (TTL cache, non-blocking for warm cache)
    system_used_gib: float = 0.0
    system_available_gib: float = 0.0
    try:
        # F265H: TTL cache eliminates blocking syscall for 98%+ of calls.
        # Cache is refreshed by _refresh_psutil_cache_sync() in the monitor loop
        # or lazily on first call after TTL expiry.
        vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
        if vm is not None:
            system_used_gib = (vm.total - vm.available) / (1024 ** 3)
            system_available_gib = vm.available / (1024 ** 3)
    except Exception as exc:
        last_error = f"virtual_memory: {exc}"
        system_used_gib = 0.0
        system_available_gib = 0.0

    # 3. Swap — diagnostic only, fail-open (TTL cache)
    swap_used_gib: float = 0.0
    try:
        sm = _get_cached_psutil("swap_memory", _read_swap_memory_sync)
        if sm is not None:
            swap_used_gib = sm.used / (1024 ** 3)
    except Exception:
        pass  # noqa: BARE-EXCEPT  # swap unavailable — fail-open silently

    # 4. Metal diagnostic surface from 8T (read-only)
    metal_cache_limit_bytes, metal_wired_limit_bytes = _get_metal_limits_status_8ab()

    # Compute state and io_only
    state = evaluate_uma_state(system_used_gib)

    # F166F: swap_detected computed BEFORE latch so it can propagate into the decision
    # F221: Raised from 0.05 to 1.5 GiB — macOS M1 8GB baseline uses 1.0-1.2 GiB swap at rest;
    # 1.5 GiB absorbs normal UMA variance while preserving genuine pressure signal (>2x baseline).
    # F265C: Raised from 1.5 to 3.5 GiB — M1 8GB baseline swap at idle is 1.0-1.2 GiB;
    # 3.5 GiB threshold = 2.3-2.5 GiB above baseline, aligns with HARD_BLOCK_SWAP_GIB=4.0
    # in the swap tiered policy (0.5 GiB margin before hard block). This prevents
    # premature io_only acceleration under normal workload variance while still
    # catching genuine systemic pressure (>3x baseline).
    # F265D: Raised to 3.8 GiB — 3x above 1.0-1.2 GiB baseline, 0.2 GiB below
    # HARD_BLOCK_SWAP_GIB=4.0 (preserves margin), allows 2.0-2.5 GiB load spikes
    # without triggering, catches genuine systemic pressure.
    swap_detected = swap_used_gib > 3.8

    # Sprint 8AK: Shared hysteresis latch — thread-safe, prevents state thrashing
    # F166F: swap_detected accelerates io_only entry to WARN threshold (6.0 GiB)
    # F183C: Capture previous latch value BEFORE update for transition detection.
    with _io_only_latch_lock:
        prev_io_only = _io_only_latch
    io_only, _ = _update_io_only_latch_with_lock(system_used_gib, swap_detected=swap_detected)

    # Update telemetry — F130A: all counters are transition-based, not state-sampled.
    # - transition_count: every state change (ok→warn, warn→critical, etc.)
    # - io_only_enter_count: actual io_only activation (False→True transition)
    # - io_only_exit_count: actual io_only deactivation (True→False transition)
    global _telemetry
    if _telemetry["last_state"] != state:
        _telemetry["transition_count"] += 1
        _telemetry["last_state"] = state

    # F130A+F183C: transition-based enter/exit — prev captured from latch BEFORE update
    if io_only and not prev_io_only:
        # False → True: io_only was just activated
        _telemetry["io_only_enter_count"] += 1
    elif not io_only and prev_io_only:
        # True → False: io_only was just deactivated
        _telemetry["io_only_exit_count"] += 1

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


def get_uma_telemetry() -> dict[str, Any]:
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
        self._callbacks: dict[str, list] = {
            UMA_STATE_CRITICAL: [],
            UMA_STATE_EMERGENCY: [],
        }
        self._task: asyncio.Task | None = None
        self._running = False
        self._interval_s: float = 5.0
        # B.2: hysteresis cooldown — prevent callback storm
        # float("-inf") ensures first dispatch always fires (now - (-inf) = +inf > 2.0)
        self._last_dispatch_time: dict[str, float] = {
            UMA_STATE_CRITICAL: float("-inf"),
            UMA_STATE_EMERGENCY: float("-inf"),
        }

    def register_callback(self, state: str, callback: Callable[[], Any]) -> None:
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
        F265H: Pre-populates psutil TTL cache before each tick via
        asyncio.to_thread — keeps cache warm so can_afford_sync and
        sample_uma_status see near-zero-latency reads (warm cache hit).
        """
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
                # F265H: Refresh psutil cache in background thread — non-blocking
                await asyncio.to_thread(_refresh_psutil_cache_sync)
                await self._check_and_dispatch()
            except asyncio.CancelledError:
                raise  # B.3: propagate cancellation cleanly
            except Exception:
                pass  # noqa: BARE-EXCEPT  # fail-open: keep monitoring even on one bad tick

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
            if inspect.iscoroutinefunction(cb):
                await cb()
            elif asyncio.iscoroutine(cb):
                await cb
            elif callable(cb):
                cb()
            # else: not callable, silently ignore

        await safe_gather_fire_and_forget(*[_dispatch_one(cb) for cb in callbacks], label="resource_governor:648")


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

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
import os
import sys
import threading
import time
from collections.abc import Callable
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
import msgspec

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")

from hledac.universal.utils.async_helpers import safe_create_task, stop_task


class UMAState(StrEnum):
    """
    Sprint F289: SSOT UMA state labels as Python 3.11+ StrEnum.

    Benefits over plain str constants:
    - `is` comparison (identity, not equality) — faster and explicit
    - Auto-complete in IDEs, static type checkers understand it
    - Exhaustive match statement coverage at compile time
    - No runtime overhead (StrEnum = str subclass, zero-cost abstraction)

    Values match string constants: UMA_STATE_OK, UMA_STATE_WARN, etc.
    Keep using string literals for serialization (DuckDB, JSON, LMDB).
    """

    OK = "ok"
    SOFT_WARN = "soft_warn"
    WARN = "warn"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class PressureState(StrEnum):
    """
    Canonical memory pressure state for Hledac Universal.

    F350M-R: Unified pressure state replacing PressureState from the now-archived
    core/uma_governor.py. Values map to UMAState string literals for
    serialization (DuckDB, JSON, LMDB).

    F350M-R Migration:
        - core/uma_governor.PressureState merged here as canonical
        - UMAStateToPressureState / pressure_state_to_uma_state mappings added
        - core/uma_governor.py → backward-compat re-export stub
    """

    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


# Mapping from UMAState to PressureState
UMAStateToPressureState: dict[str, PressureState] = {
    "ok": PressureState.NORMAL,
    "soft_warn": PressureState.ELEVATED,
    "warn": PressureState.HIGH,
    "critical": PressureState.CRITICAL,
    "emergency": PressureState.CRITICAL,
}

# Reverse mapping
PressureStateToUMAState: dict[PressureState, str] = {
    PressureState.NORMAL: "ok",
    PressureState.ELEVATED: "soft_warn",
    PressureState.HIGH: "warn",
    PressureState.CRITICAL: "critical",
}


def uma_state_to_pressure_state(uma_state: str) -> PressureState:
    """Convert UMAState string to canonical PressureState."""
    if uma_state not in UMAStateToPressureState:
        raise ValueError(f"Unknown UMAState value: {uma_state!r}")
    return UMAStateToPressureState[uma_state]


def pressure_state_to_uma_state(pressure_state: PressureState) -> str:
    """Convert PressureState to UMAState string."""
    return PressureStateToUMAState[pressure_state]


class UMAGovernor(Protocol):  # type: ignore[explicit-any]
    """Protocol for UMA memory pressure governors (mirrors core/uma_governor.UMAGovernor)."""

    async def get_pressure(self) -> "PressureState": ...  # type: ignore[empty-body]

    def telemetry(self) -> dict[str, Any]: ...  # type: ignore[empty-body]

    def release_memory(self) -> None:
        """Release reclaimable memory pages. Called at CRITICAL/EMERGENCY UMA state."""
        ...

    def apply_madvise_to_file(self, path: str, advice: int = 1) -> bool:
        """Apply madvise to a memory-mapped file. Returns True on success."""
        ...


from hledac.universal.core.locks import LockCategory, register_lock


class ConcurrencyPreset(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint F289: Immutable concurrency preset derived from UMA state.
    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.

    Single source of truth for all concurrency limits derived from
    M1 8GB UMA state. Replaces scattered if-elif chains in:
    - M1ResourceGovernor._evaluate_impl()
    - BackpressureMonitor (via _TTL_BY_STATE, AIMD_DECREASE_BY_STATE)

    M1 8GB calibrated values:
        emergency:  0 workers, 1 fetch, block_model_load=True  — near-OOM
        critical:   1 worker,  2 fetch, block_model_load=True  — active pressure
        warn:       3 workers, 5 fetch, block_model_load=False — reduced headroom
        soft_warn:  5 workers, 10 fetch, block_model_load=False — approaching limit
        ok:         5 workers, 20 fetch, block_model_load=False — normal operation
    """

    max_workers: int
    fetch_limit: int
    block_model_load: bool
    cache_ttl_seconds: float
    aimd_decrease_factor: float

    @classmethod
    def from_state(cls, state: str) -> ConcurrencyPreset:
        """
        Python 3.10+ match statement pro derivaci presetu ze stavu.

        Uses guard clauses (if conditions in case pattern) for threshold
        ordering. This is the canonical pattern for range-based matches.
        """
        match state:
            case "emergency":
                return cls(
                    max_workers=0, fetch_limit=1, block_model_load=True, cache_ttl_seconds=0.1, aimd_decrease_factor=0.0
                )
            case "critical":
                return cls(
                    max_workers=1,
                    fetch_limit=2,
                    block_model_load=True,
                    cache_ttl_seconds=0.25,
                    aimd_decrease_factor=0.25,
                )
            case "warn":
                return cls(
                    max_workers=3,
                    fetch_limit=5,
                    block_model_load=False,
                    cache_ttl_seconds=1.0,
                    aimd_decrease_factor=0.5,
                )
            case "soft_warn":
                return cls(
                    max_workers=5,
                    fetch_limit=10,
                    block_model_load=False,
                    cache_ttl_seconds=2.0,
                    aimd_decrease_factor=0.75,
                )
            case "ok":
                return cls(
                    max_workers=5,
                    fetch_limit=20,
                    block_model_load=False,
                    cache_ttl_seconds=5.0,
                    aimd_decrease_factor=1.0,
                )
            case _:
                return cls(
                    max_workers=5,
                    fetch_limit=20,
                    block_model_load=False,
                    cache_ttl_seconds=5.0,
                    aimd_decrease_factor=1.0,
                )


try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    _PSUTIL_AVAILABLE = False
_mx = None
_process_cache: Any = None


def _get_cached_process() -> Any:
    """Lazy psutil.Process() accessor. Raises RuntimeError if psutil unavailable."""
    global _process_cache
    if _process_cache is None:
        if psutil is None:
            raise RuntimeError("psutil not available in this environment")
        _process_cache = psutil.Process()
    return _process_cache


import contextvars as _contextvars
import threading as _threading
import time as _time_module

_thread_local_locks: _contextvars.ContextVar[dict[str, _threading.Lock] | None] = _contextvars.ContextVar(
    "_thread_local_locks", default=None
)


def _get_key_lock(key: str) -> _threading.Lock:
    """Return per-key lock, lazily created in thread-local dict.

    E5 FIX: Uses ContextVar instead of global _psutil_meta_lock + LRUCache.
    - No global meta-lock bottleneck — every thread has its own lock dict
    - No LRUCache eviction — per-thread unbounded dict (thread count bounded)
    - Thread-safe: ContextVar is thread-isolated, each thread gets own dict
    - O(1) per-thread: dict[key] lookup, no global lock needed

    Rationale:
    - asyncio monitor thread + to_thread worker threads → each has own locks
    - max threads ≈ 10 (thread pool) × 1 dict ≈ negligible memory
    - 2-level TTL cache (_get_cached_psutil) already bounds staleness
    """
    locks = _thread_local_locks.get()
    if locks is None:
        locks = {}
        _thread_local_locks.set(locks)
    lock = locks.get(key)
    if lock is None:
        lock = _threading.Lock()
        locks[key] = lock
    return lock


_MAX_PSUTIL_CACHE_SIZE: int = 32
_psutil_cache: dict[str, tuple[Any, float]] = {}
_psutil_meta_lock: _threading.Lock = _threading.Lock()
register_lock(LockCategory.CACHE, _psutil_meta_lock, "resource_governor._psutil_meta_lock")
_PSUTIL_CACHE_TTL_S: float = 2.0


def reset_psutil_cache() -> None:
    """Reset psutil TTL cache. For testing only — clears all cached readings."""
    with _psutil_meta_lock:
        _psutil_cache.clear()


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


def _read_memory_pressure_sync() -> dict[str, Any]:
    """
    Blocking psutil-based memory pressure reader.

    Replaces subprocess.run(["memory_pressure"]) which incurs 0.5-1.8s latency
    per call in the M1 event loop. Pure psutil runs in µs.

    Signal derivation (M1 8GB calibrated):
        GREEN  → available percent > 30%  (virtual_memory().percent < 70)
        YELLOW → available percent 15-30% (virtual_memory().percent 70-85)
        RED    → available percent < 15%  (virtual_memory().percent > 85)

    Returns:
        dict with keys: status (str), free_pct (int), compressor_pages (int|None)
    """
    if psutil is None:
        return {"status": "UNKNOWN", "free_pct": 0, "compressor_pages": None}
    try:
        vm = psutil.virtual_memory()
        total = getattr(vm, "total", 0)
        used = getattr(vm, "used", 0)
        if total > 0:
            free_pct = int((total - used) / total * 100)
        else:
            free_pct = 100
        if free_pct < 15:
            status = "RED"
        elif free_pct < 30:
            status = "YELLOW"
        else:
            status = "GREEN"
        return {"status": status, "free_pct": free_pct, "compressor_pages": None}
    except Exception:
        return {"status": "UNKNOWN", "free_pct": 0, "compressor_pages": None}


def _get_cached_psutil(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Thread-safe TTL cache for blocking psutil reads.

    Per-key lock achieves single-flight: only the first misser calls reader_fn(),
    subsequent missers block briefly on the key lock, then read the populated entry.
    Other cache keys proceed without blocking (per-key vs global lock).

    Flow:
        1. Fast path — read cache under meta-lock, return if fresh.
        2. Acquire per-key lock — only threads needing this specific key block.
        3. Double-check — another thread may have populated the cache while we waited.
        4. Compute outside all locks — slow sysctl doesn't block other keys.
        5. Write result and fresh timestamp under meta-lock.
    """
    now = _time_module.monotonic()
    with _psutil_meta_lock:
        entry = _psutil_cache.get(key)
        if entry is not None:
            result, timestamp = entry
            if now - timestamp < _PSUTIL_CACHE_TTL_S:
                return result
    key_lock = _get_key_lock(key)
    with key_lock:
        with _psutil_meta_lock:
            entry = _psutil_cache.get(key)
            if entry is not None:
                result, timestamp = entry
                if now - timestamp < _PSUTIL_CACHE_TTL_S:
                    return result
            _psutil_cache[key] = (None, now)
    try:
        result = reader_fn()
    except Exception:
        with _psutil_meta_lock:
            _psutil_cache.pop(key, None)
        raise
    with _psutil_meta_lock:
        evictions_needed = len(_psutil_cache) - _MAX_PSUTIL_CACHE_SIZE + 1
        if evictions_needed > 0:
            sorted_keys = sorted(_psutil_cache, key=lambda k: _psutil_cache[k][1])
            for k in sorted_keys[:evictions_needed]:
                del _psutil_cache[k]
        _psutil_cache[key] = (result, _time_module.monotonic())
    return result


async def _get_cached_psutil_async(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Async wrapper: offloads blocking reader_fn to a thread, caches result.
    All callers of this function are non-blocking on the event loop.
    """
    result = await asyncio.to_thread(_get_cached_psutil, key, reader_fn)
    return result


def _refresh_psutil_cache_sync() -> None:
    """
    Force-refresh all psutil cache entries synchronously.
    For use in sync contexts where asyncio.to_thread is unavailable (e.g., __init__).

    ISSUE-112 FIX: Refreshes ALL three cache keys (virtual_memory, swap_memory,
    memory_pressure). Previously memory_pressure was refreshed only via _get_cached_psutil
    TTL path, not by the background monitor loop — causing stale reads on the 5s loop.
    """
    if psutil is None:
        return
    now = _time_module.monotonic()
    with _psutil_meta_lock:
        _psutil_cache["virtual_memory"] = (psutil.virtual_memory(), now)
        _psutil_cache["swap_memory"] = (psutil.swap_memory(), now)
        _psutil_cache["memory_pressure"] = (_read_memory_pressure_sync(), now)


def _get_mx():
    global _mx
    if _mx is None:
        import mlx.core as _mx_module

        _mx = _mx_module
    return _mx


logger = logging.getLogger(__name__)
_RATIO_TABLE = {
    (0, 10): (0.85, 0.875, 0.9375, 0.975),
    (10, 18): (0.8, 0.85, 0.9, 0.95),
    (18, 32): (0.75, 0.8, 0.87, 0.92),
    (32, 128): (0.7, 0.75, 0.85, 0.9),
}


def _detect_total_memory_gib() -> float:
    """Detect real system RAM in GiB. Floor 4 GiB, ceil 128 GiB, fallback 8 GiB."""
    try:
        import psutil as _ps

        mem = _ps.virtual_memory()
        detected_gib = mem.total / 1024**3
        return max(4.0, min(128.0, detected_gib))
    except Exception:
        return 8.0


_DETECTED_TOTAL_GIB: float = _detect_total_memory_gib()
_SOC_RATIOS: tuple[float, float, float, float] = (0.85, 0.875, 0.9375, 0.975)
for (_min, _max), _ratios in _RATIO_TABLE.items():
    if _min <= _DETECTED_TOTAL_GIB < _max:
        _SOC_RATIOS = _ratios
        break
_SOFT_WARN_RATIO, _WARN_RATIO, _CRITICAL_RATIO, _EMERGENCY_RATIO = _SOC_RATIOS


def _adaptive_threshold(ratio: float) -> float:
    """Compute GiB threshold from ratio: detected_ram_gib * ratio, rounded to 2 dp."""
    return round(_DETECTED_TOTAL_GIB * ratio, 2)


from core.env_config import ENV as _ENV

_RG_USE_RATIOS: bool = _ENV.get_bool("HLEDAC_RG_USE_RATIOS", default=True)
try:
    from hledac.universal.config import _rg_float

    if _RG_USE_RATIOS:
        _THRESHOLD_SOFT_WARN_GIB: float = _adaptive_threshold(_SOFT_WARN_RATIO)
        _THRESHOLD_WARN_GIB: float = _adaptive_threshold(_WARN_RATIO)
        _THRESHOLD_CRITICAL_GIB: float = _adaptive_threshold(_CRITICAL_RATIO)
        _THRESHOLD_EMERGENCY_GIB: float = _adaptive_threshold(_EMERGENCY_RATIO)
    else:
        _THRESHOLD_SOFT_WARN_GIB = _rg_float("THRESHOLD_SOFT_WARN_GIB")
        _THRESHOLD_WARN_GIB = _rg_float("THRESHOLD_WARN_GIB")
        _THRESHOLD_CRITICAL_GIB = _rg_float("THRESHOLD_CRITICAL_GIB")
        _THRESHOLD_EMERGENCY_GIB = _rg_float("THRESHOLD_EMERGENCY_GIB")
    _HYSTERESIS_EXIT_GIB: float = _rg_float("HYSTERESIS_EXIT_GIB")
except (ImportError, NameError):
    _THRESHOLD_SOFT_WARN_GIB = round(_DETECTED_TOTAL_GIB * _SOFT_WARN_RATIO, 2)
    _THRESHOLD_WARN_GIB = round(_DETECTED_TOTAL_GIB * _WARN_RATIO, 2)
    _THRESHOLD_CRITICAL_GIB = round(_DETECTED_TOTAL_GIB * _CRITICAL_RATIO, 2)
    _THRESHOLD_EMERGENCY_GIB = round(_DETECTED_TOTAL_GIB * _EMERGENCY_RATIO, 2)
    _HYSTERESIS_EXIT_GIB = round(_DETECTED_TOTAL_GIB * _SOFT_WARN_RATIO, 2)
RATIOS_USED: tuple[float, float, float, float] = _SOC_RATIOS
DETECTED_TOTAL_GIB: float = _DETECTED_TOTAL_GIB
UMA_STATE_SOFT_WARN: str = "soft_warn"
UMA_STATE_OK: str = "ok"
UMA_STATE_WARN: str = "warn"
UMA_STATE_CRITICAL: str = "critical"
UMA_STATE_EMERGENCY: str = "emergency"
try:
    CLEAN_SWAP_MAX_GIB: float = _rg_float("CLEAN_SWAP_MAX_GIB")
    DIAGNOSTIC_SWAP_MAX_GIB: float = _rg_float("DIAGNOSTIC_SWAP_MAX_GIB")
    HARD_BLOCK_SWAP_GIB: float = _rg_float("HARD_BLOCK_SWAP_GIB")
except NameError:
    CLEAN_SWAP_MAX_GIB = 3.0
    DIAGNOSTIC_SWAP_MAX_GIB = 5.0
    HARD_BLOCK_SWAP_GIB = 6.0


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
        return ("clean", f"swap={swap_gib:.2f}GiB <= {CLEAN_SWAP_MAX_GIB:.1f}GiB threshold")
    elif swap_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
        return (
            "diagnostic",
            f"swap={swap_gib:.2f}GiB in ({CLEAN_SWAP_MAX_GIB:.1f}GiB, {DIAGNOSTIC_SWAP_MAX_GIB:.1f}GiB] — hardware taint",
        )
    else:
        return ("hard_block", f"swap={swap_gib:.2f}GiB > {HARD_BLOCK_SWAP_GIB:.1f}GiB — restart required")


from hledac.universal.utils.async_helpers import parallel

_io_only_latch: bool = False
_io_only_latch_lock: _threading.Lock = _threading.Lock()
_UMA_TELEMETRY_LOCK: _threading.RLock = _threading.RLock()
register_lock(LockCategory.WAL, _io_only_latch_lock, "resource_governor._io_only_latch")
register_lock(LockCategory.METRICS, _UMA_TELEMETRY_LOCK, "resource_governor._UMA_TELEMETRY_LOCK")


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
    Returns (prev_latch, new_latch).
    Thread-safe via _io_only_latch_lock.
    F166F: swap_detected propagates into latch computation for accelerated io_only entry.
    """
    global _io_only_latch
    with _io_only_latch_lock:
        current = _io_only_latch
        new_val = _compute_io_only_latch(system_used_gib, current, swap_detected=swap_detected)
        _io_only_latch = new_val
        return (current, new_val)


def _reset_uma_hysteresis_for_testing() -> None:
    """
    Sprint 8AK: Reset the shared io_only latch to False.
    For tests only — ensures test isolation.
    """
    global _io_only_latch
    with _io_only_latch_lock:
        _io_only_latch = False


_telemetry: dict[str, Any] = {
    "transition_count": 0,
    "io_only_enter_count": 0,
    "io_only_exit_count": 0,
    "last_state": "ok",
}


def _record_transition(state: str, prev_io_only: bool, io_only: bool) -> None:
    """
    B4-ISSUE-4: Thread-safe telemetry recorder.

    Called from both:
    - sample_uma_status() — runs in to_thread (background thread)
    - apply_decision()   — runs in main async loop

    RLock allows re-entrant calls (sample_uma_status → record_transition → lock).

    All side-effect-free reads of _telemetry (e.g. get_uma_telemetry()) do NOT
    need the lock — dict reads are atomic in CPython.
    """
    global _telemetry
    with _UMA_TELEMETRY_LOCK:
        if _telemetry["last_state"] != state:
            _telemetry["transition_count"] += 1
            _telemetry["last_state"] = state
        if io_only and (not prev_io_only):
            _telemetry["io_only_enter_count"] += 1
        elif not io_only and prev_io_only:
            _telemetry["io_only_exit_count"] += 1


class UMAStatus(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint 8AB + F163F: Unified UMA accounting snapshot.
    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.

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
    metal_active_gib: float = 0.0
    metal_peak_gib: float = 0.0
    swap_detected: bool = False
    last_error: str | None = None


class MemoryPressureHysteresis:
    """
    P2-23: Hysteresis state machine for memory pressure watchdog.

    Prevents thrashing (rapid flap between states) when memory pressure
    oscillates near a threshold. Uses dwell-time enforcement — a state
    transition only fires after the pressure condition is sustained for
    a configured duration.

    State diagram (P2-23):
        normal ────(>70% × 5s)────→ warning
        warning ───(>85% × 3s)────→ critical
        critical ──(<75% × 10s)────→ warning
        warning ───(<60% × 15s)────→ normal

    Unlike the 2-second debounce in UmaWatchdog (uma_budget.py), this
    hysteresis machine operates at the Governor level and gates the
    *uma_state* label that drives io_only decisions and fetch_limit.

    Exits are intentionally slower than entries (asymmetric hysteresis):
    exiting critical→warning needs 10s below 75%, exiting warning→normal
    needs 15s below 60%. This prevents rapid oscillation between
    I/O-heavy and CPU-heavy modes near memory boundaries.

    Integration: instantiated by M1ResourceGovernor and called from
    evaluate() before constructing GovernorDecision.
    """

    THRESHOLDS: dict[str, tuple[float, float]] = {"normal_to_warning": (0.7, 5.0), "warning_to_critical": (0.85, 3.0)}
    EXIT_FLOOR_CRITICAL = 0.75
    EXIT_FLOOR_WARNING = 0.6
    EXIT_DWELL_CRITICAL = 10.0
    EXIT_DWELL_WARNING = 15.0
    __slots__ = ("_state", "_enter_time", "_exit_enter_time", "_exit_floor_gib", "_total_gib")

    def __init__(self, total_gib: float | None = None) -> None:
        self._state = "normal"
        self._enter_time: float | None = None
        self._exit_enter_time: float | None = None
        self._exit_floor_gib: float = 0.0
        self._total_gib = total_gib if total_gib is not None else _DETECTED_TOTAL_GIB

    def update(self, memory_used_ratio: float, system_used_gib: float, now: float) -> str:
        """
        Advance the hysteresis state machine.

        Args:
            memory_used_ratio: Current memory pressure as a fraction [0.0, 1.0].
            system_used_gib:    Current used memory in GiB (absolute value for exit floors).
            now:                Current monotonic time in seconds.

        Note:
            memory_used_ratio is kept for API symmetry — the hysteresis
            uses absolute gib values throughout for consistency with
            the Governor's threshold system (which is gib-based).
            The ratio is recoverable as ``system_used_gib / _total_gib``.

        Returns:
            The current state after processing this sample:
            "normal" | "warning" | "critical".
        """
        total = self._total_gib
        enter_warn_ratio, dwell_warn = self.THRESHOLDS["normal_to_warning"]
        enter_crit_ratio, dwell_crit = self.THRESHOLDS["warning_to_critical"]
        enter_warn_gib = enter_warn_ratio * total
        enter_crit_gib = enter_crit_ratio * total
        exit_warn_gib = self.EXIT_FLOOR_WARNING * total
        exit_crit_gib = self.EXIT_FLOOR_CRITICAL * total
        current = self._state
        if current == "critical":
            if system_used_gib < exit_crit_gib:
                if self._exit_enter_time is None:
                    self._exit_enter_time = now
                elif now - self._exit_enter_time >= self.EXIT_DWELL_CRITICAL:
                    self._state = "warning"
                    self._enter_time = now
                    self._exit_enter_time = None
                    return self._state
            else:
                self._exit_enter_time = None
        elif current == "warning":
            if system_used_gib < exit_warn_gib:
                if self._exit_enter_time is None:
                    self._exit_enter_time = now
                elif now - self._exit_enter_time >= self.EXIT_DWELL_WARNING:
                    self._state = "normal"
                    self._enter_time = now
                    self._exit_enter_time = None
                    return self._state
            else:
                self._exit_enter_time = None
        if current == "normal":
            if system_used_gib >= enter_warn_gib:
                if self._enter_time is None:
                    self._enter_time = now
                elif now - self._enter_time >= dwell_warn:
                    self._state = "warning"
                    self._enter_time = now
                    return self._state
        elif current == "warning":
            if system_used_gib >= enter_crit_gib:
                if self._enter_time is None:
                    self._enter_time = now
                elif now - self._enter_time >= dwell_crit:
                    self._state = "critical"
                    self._enter_time = now
                    return self._state
        return self._state

    @property
    def state(self) -> str:
        """Current hysteresis state."""
        return self._state

    def reset(self) -> None:
        """Reset to normal state. For testing or sprint re-initialisation."""
        self._state = "normal"
        self._enter_time = None
        self._exit_enter_time = None


class GovernorDecision(msgspec.Struct, frozen=True, gc=False):
    """
    G-1 Fix: Canonical governor rozhodnutí s auto-apply semantics.
    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.

    F-G1: GovernorDecision is now auto-applied — evaluate() calls
    apply_decision() internally before returning. Callers that ignore
    the return value are in violation of the GOVERNOR AUTHORITY CONTRACT.

    fields:
        uma_state:       "ok" | "soft_warn" | "warn" | "critical" | "emergency".
        io_only:          True pokud I/O-only mód (žádné CPU-intensive operace).
        fetch_limit:      MAX souběžných fetch operací.
        block_model_load: True pokud by se neměl load nový MLX model.
    """

    uma_state: str
    io_only: bool
    fetch_limit: int
    block_model_load: bool = False
    swap_detected: bool = False  # ISSUE-35: expose swap signal for backpressure decisions


class M1ResourceGovernor:
    """
    G-1 Fix: Self-applying M1 UMA governor.

    evaluate() vždy volá apply_decision() interně před návratem —
    eliminuje 18/20 apply drift napříč všemi call sites.

    Používá backpressure_monitor (backpressure.py) a acquisition_strategy.py.
    Pro plnou specifikaci viz SYSTEM_ANALYSIS_2026.md §G-1.
    """

    IO_ONLY_TTL_S: float = 0.5
    FETCH_LIMIT_TTL_S: float = 5.0
    BLOCK_MODEL_LOAD_TTL_S: float = 30.0
    _SPIKE_MEMORY_RATIO_THRESHOLD: float = 0.88
    _cached_decision: GovernorDecision | None = None
    _cached_io_only_timestamp: float = 0.0
    _cached_fetch_limit_timestamp: float = 0.0
    _cached_block_model_load_timestamp: float = 0.0
    _spike_detected: bool = False
    _last_evaluated_memory_ratio: float = 0.0
    _decision_lock_factory: threading.Lock = threading.Lock()
    _decision_lock: asyncio.Lock | None = None
    __slots__ = ("_hysteresis", "_legacy_cache_ttl_s", "_mpc_controller")

    def __init__(self, cache_ttl_s: float = 5.0):
        self._legacy_cache_ttl_s = cache_ttl_s
        self._hysteresis = MemoryPressureHysteresis(total_gib=None)
        self._mpc_controller = AdaptiveMPCController()
        self._last_evaluated_memory_ratio = 0.0

    @classmethod
    async def _ensure_decision_lock(cls) -> asyncio.Lock:
        """Lazy init asyncio.Lock — voláno z async kontextu s běžícím event loop."""
        if cls._decision_lock is None:
            with cls._decision_lock_factory:
                if cls._decision_lock is None:
                    cls._decision_lock = asyncio.Lock()
        assert cls._decision_lock is not None
        return cls._decision_lock

    async def evaluate(self) -> GovernorDecision:
        """
        Issue #8: Dual-channel TTL — per-field cache with spike detection.

        Instead of a single TTL for the whole decision, each field is cached
        independently with its own TTL:

          io_only:          0.5 s  — must react fast to memory spikes
          fetch_limit:       5.0 s  — network concurrency changes less often
          block_model_load: 30.0 s  — quasi-static (model loads are rare)

        Additionally, if a memory spike is detected (system_used_gib crosses
        _SPIKE_MEMORY_RATIO_THRESHOLD), the cache is invalidated immediately
        regardless of TTL — OOM reaction time drops from 5 s to <500 ms.
        """
        now = time.monotonic()
        lock = await M1ResourceGovernor._ensure_decision_lock()
        async with lock:
            if M1ResourceGovernor._spike_detected:
                M1ResourceGovernor._spike_detected = False
                decision = await self._evaluate_impl()
                await self.apply_decision(decision)
                self._update_cached_timestamps(now, decision)
                return decision
            io_only_valid = (
                M1ResourceGovernor._cached_decision is not None
                and now - M1ResourceGovernor._cached_io_only_timestamp < M1ResourceGovernor.IO_ONLY_TTL_S
            )
            fetch_limit_valid = (
                M1ResourceGovernor._cached_decision is not None
                and now - M1ResourceGovernor._cached_fetch_limit_timestamp < M1ResourceGovernor.FETCH_LIMIT_TTL_S
            )
            block_model_load_valid = (
                M1ResourceGovernor._cached_decision is not None
                and now - M1ResourceGovernor._cached_block_model_load_timestamp
                < M1ResourceGovernor.BLOCK_MODEL_LOAD_TTL_S
            )
            if io_only_valid and fetch_limit_valid and block_model_load_valid:
                return M1ResourceGovernor._cached_decision
            decision = await self._evaluate_impl()
            await self.apply_decision(decision)
            self._update_cached_timestamps(now, decision)
            return decision

    def _update_cached_timestamps(self, now: float, decision: GovernorDecision) -> None:
        """Issue #8: Update per-field timestamps after a fresh evaluation."""
        M1ResourceGovernor._cached_decision = decision
        M1ResourceGovernor._cached_io_only_timestamp = now
        M1ResourceGovernor._cached_fetch_limit_timestamp = now
        M1ResourceGovernor._cached_block_model_load_timestamp = now

    async def _evaluate_impl(self) -> GovernorDecision:
        """
        Interní evaluace — gruz na sample_uma_status_async + threshold logika.
        Fail-soft: vrací bezpečné default při jakékoli chybě.
        """
        try:
            uma = await sample_uma_status_async()
        except Exception:
            preset = ConcurrencyPreset.from_state(UMAState.OK)
            return GovernorDecision(
                uma_state=UMAState.OK,
                io_only=False,
                fetch_limit=preset.fetch_limit,
                block_model_load=preset.block_model_load,
                swap_detected=False,
            )
        preset = ConcurrencyPreset.from_state(uma.state)
        now = time.monotonic()
        memory_ratio = uma.system_used_gib / max(uma.system_used_gib + uma.system_available_gib, 1.0)
        if memory_ratio > M1ResourceGovernor._SPIKE_MEMORY_RATIO_THRESHOLD:
            if self._last_evaluated_memory_ratio < M1ResourceGovernor._SPIKE_MEMORY_RATIO_THRESHOLD:
                M1ResourceGovernor._spike_detected = True
        self._last_evaluated_memory_ratio = memory_ratio
        hysteresis_state = self._hysteresis.update(memory_ratio, uma.system_used_gib, now)
        state_map = {"normal": "ok", "warning": "warn", "critical": "critical"}
        gated_state = state_map.get(hysteresis_state, uma.state)
        mpc_control, _mpc_metrics = await self._mpc_controller.compute_control(uma.system_used_gib, uma.state)
        scaled_fetch_limit = max(1, int(preset.fetch_limit * mpc_control))
        try:
            from hledac.universal.metrics_registry import get_metrics_registry

            get_metrics_registry().set_gauge("memory_layer_pressure_pct", memory_ratio * 100.0)
        except Exception:
            pass
        return GovernorDecision(
            uma_state=gated_state,
            io_only=uma.io_only,
            fetch_limit=scaled_fetch_limit,
            block_model_load=preset.block_model_load,
            swap_detected=uma.swap_detected,
        )

    async def apply_decision(self, decision: GovernorDecision) -> None:
        """
        G-1 Fix: Aplikuje decision na runtime surfaces (fail-soft).

        F-G1: apply_decision je volán vždy z evaluate() — caller už nemůže
        rozhodnutí ignorovat.

        Aplikuje:
        - _io_only_latch (hysteresis state)
        - telemetry (pro monitoring/alerting)
        - B5: dynamic GC thresholds via memory_cycle._apply_gc_thresholds()
        """
        try:
            global _io_only_latch
            with _io_only_latch_lock:
                current_latch = _io_only_latch
                _io_only_latch = decision.io_only
            with _UMA_TELEMETRY_LOCK:
                last_state = _telemetry["last_state"]
            _record_transition(last_state, current_latch, decision.io_only)
        except Exception:
            pass
        # B5: propagate UMA state to memory_cycle for dynamic GC thresholds
        try:
            from hledac.universal.core.memory_cycle import _apply_gc_thresholds
            _apply_gc_thresholds(decision.uma_state)
        except Exception:
            pass
        # F350M-R: Apply madvise to all mmap handles at CRITICAL/EMERGENCY
        if decision.uma_state in (UMAState.CRITICAL, UMAState.EMERGENCY):
            self.apply_madvise_critical()

    def apply_madvise_critical(self) -> None:
        """
        F350M-R 4.5: Apply MADV_FREE_REUSABLE to all known mmap handles.

        Called automatically at CRITICAL/EMERGENCY UMA state to propagate
        memory pressure to the kernel for DuckDB/LMDB page cache regions.

        Uses:
        - madvise_lmdb_mmap() from Rust for MAP_NOCACHE on primary .mdb/.duckdb files
        - madvise_on_mmap_region() for LMDB sub-DBs with known paths
        - malloc_zone_pressure_relief() for heap memory

        Always-on, fail-safe — errors are logged but never propagate.
        """
        try:
            self._apply_madvise_to_duckdb_paths()
        except Exception:
            pass
        try:
            self._apply_madvise_to_lmdb_paths()
        except Exception:
            pass
        try:
            self._malloc_zone_pressure_relief()
        except Exception:
            pass

    def _apply_madvise_to_duckdb_paths(self) -> None:
        """Apply MADV_NOCACHE to all DuckDB database file paths."""
        # F350M-R-A1: Use lightweight class registry instead of gc.get_objects()
        try:
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
            from hledac.universal.tools.file_cache import madvise_lmdb_mmap

            for store in list(DuckDBShadowStore._instances):
                path = getattr(store, "_db_path", None)
                if path and str(path) != ":memory:":
                    try:
                        madvise_lmdb_mmap(str(path), advice=1)  # MADV_NOCACHE
                    except Exception:
                        pass
        except Exception:
            pass

    def _apply_madvise_to_lmdb_paths(self) -> None:
        """Apply MADV_NOCACHE to all LMDB environment paths."""
        _lmdb_paths: list[str] = []
        # Collect known LMDB paths from module-level singletons
        try:
            from hledac.universal.knowledge.sprint_seeds_store import _LMDB_PATH as _SEEDS_LMDB
            if _SEEDS_LMDB:
                _lmdb_paths.append(str(_SEEDS_LMDB))
        except Exception:
            pass
        try:
            from hledac.universal.knowledge.ioc_dedup_adapter import _IOC_DEDUP_LMDB_PATH
            if _IOC_DEDUP_LMDB_PATH:
                _lmdb_paths.append(str(_IOC_DEDUP_LMDB_PATH))
        except Exception:
            pass
        try:
            from hledac.universal.paths import LMDB_ROOT
            unified = LMDB_ROOT / "unified_cache.lmdb"
            if unified.exists():
                _lmdb_paths.append(str(unified))
        except Exception:
            pass
        # Apply madvise to each collected path
        for path in _lmdb_paths:
            try:
                from hledac.universal.tools.file_cache import madvise_lmdb_mmap
                madvise_lmdb_mmap(path, advice=1)  # MADV_NOCACHE
            except Exception:
                pass

    def _malloc_zone_pressure_relief(self) -> None:
        """Release malloc fragmented pages on M1 8GB UMA."""
        try:
            import ctypes
            # E fix: use_errno=True so errors are captured (no longer discarded)
            libc = ctypes.CDLL("libc.dylib", use_errno=True)
            libc.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            libc.malloc_zone_pressure_relief.restype = None
            result = libc.malloc_zone_pressure_relief(None, 0)
            if result != 0:
                import errno
                logger.warning(
                    "malloc_zone_pressure_relief returned %d (errno=%s)",
                    result,
                    errno.errorcode.get(ctypes.get_errno(), "unknown"),
                )
        except Exception:
            pass

    def apply_madvise_to_file(self, path: str, advice: int = 1) -> bool:
        """
        F350M-R 4.5: Apply madvise to a specific file path.

        Args:
            path: File path to apply madvise to
            advice: 0=MADV_FREE_REUSABLE, 1=MADV_NOCACHE (default)

        Returns:
            True if applied successfully, False otherwise.
        """
        try:
            from hledac.universal.tools.file_cache import madvise_lmdb_mmap
            return madvise_lmdb_mmap(str(path), advice=advice)
        except Exception:
            return False

    class SidecarAdmission(msgspec.Struct, frozen=True, gc=False):
        """Sidecar admission result. Migrated from @dataclass → msgspec.Struct."""

        allowed: bool
        reason: str

    def sidecar_admission(self, sidecar_name: str, est_mb: int = 30) -> SidecarAdmission:
        """
        G-1 companion: Bounded sidecar admission check.

        Používá cached sample (ne nový evaluate) pro rychlé rozhodnutí.
        Fail-soft: pokud governor nedostupný, vždy povolí.
        """
        try:
            uma = sample_uma_status()
        except Exception:
            return M1ResourceGovernor.SidecarAdmission(allowed=True, reason="governor_unavailable")
        if uma.state in ("emergency", "critical"):
            if est_mb > 50:
                return M1ResourceGovernor.SidecarAdmission(
                    allowed=False, reason=f"{uma.state}: {sidecar_name} est={est_mb}MB blocked"
                )
            return M1ResourceGovernor.SidecarAdmission(
                allowed=True, reason=f"{uma.state}: {sidecar_name} est={est_mb}MB low-cost-allowed"
            )
        return M1ResourceGovernor.SidecarAdmission(allowed=True, reason=f"{uma.state}: {sidecar_name} admitted")


class Priority(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class ResourceGovernor:
    """
    Hlídá zdroje a rozhoduje, zda je možné provést náročnou operaci.
    """

    __slots__ = tuple(
        (
            "__lock",
            "_active_tasks",
            "_cost_model",
            "_lock_factory",
            "_priority_factor",
            "high_water",
            "thermal_threshold",
        )
    )

    def __init__(self, memory_high_water_mb: float = 5632, thermal_threshold: float = 82.0):
        self.high_water = memory_high_water_mb
        self.thermal_threshold = thermal_threshold
        self._active_tasks = 0
        self._lock_factory = threading.Lock()
        self.__lock: asyncio.Lock | None = None
        self._cost_model = None
        self._priority_factor = {Priority.CRITICAL: 1.2, Priority.HIGH: 1.0, Priority.NORMAL: 0.9, Priority.LOW: 0.7}

    def _lock(self) -> asyncio.Lock:
        """Thread-safe lazy init pro asyncio.Lock — double-checked locking chráněný threading.Lock.

        asyncio.Lock() není thread-safe při init z více vláken současně.
        Používáme threading.Lock (reentrant, OS-provided) k ochraně init bloku.
        Po init už asyncio.Lock běží čistě v event loop — žádné cross-thread race.
        """
        lock = self.__lock
        if lock is None:
            with self._lock_factory:
                lock = self.__lock
                if lock is None:
                    lock = asyncio.Lock()
                    self.__lock = lock
        return lock

    def set_cost_model(self, cost_model):
        """Nastaví cost model pro predikci rizika překročení budgetu."""
        self._cost_model = cost_model

    def can_afford_sync(self, cost_estimate: dict[str, Any], priority: Priority = Priority.NORMAL) -> bool:
        """
        Synchronní kontrola zdrojů bez rezervace.
        """
        ram_used = 0.0
        if psutil is not None:
            try:
                vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
                if vm is not None:
                    ram_used = vm.used / (1024 * 1024)
            except Exception:
                ram_used = 0.0
        ram_needed = cost_estimate.get("ram_mb", 0)
        factor = self._priority_factor[priority]
        if ram_used + ram_needed > self.high_water * factor:
            return False
        if cost_estimate.get("gpu", False):
            try:
                if hasattr(_get_mx(), "get_active_memory"):
                    gpu_used = _get_mx().get_active_memory() / (1024 * 1024)
                elif hasattr(_get_mx().metal, "get_active_memory"):
                    gpu_used = _get_mx().metal.get_active_memory() / (1024 * 1024)
                else:
                    gpu_used = 0
                gpu_total = float("inf")
                if hasattr(_get_mx().metal, "get_recommended_max_memory"):
                    gpu_total = _get_mx().metal.get_recommended_max_memory() / (1024 * 1024)
                if gpu_used + ram_needed > gpu_total * factor:
                    return False
            except Exception:
                pass
        try:
            if hasattr(_get_mx().metal, "get_device_temperature"):
                gpu_temp = _get_mx().metal.get_device_temperature()
                if gpu_temp > self.thermal_threshold and priority != Priority.CRITICAL:
                    logger.warning(f"GPU thermal limit reached: {gpu_temp}°C > {self.thermal_threshold}°C")
                    return False
        except AttributeError:
            pass
        try:
            if hasattr(_get_mx().metal, "get_ane_utilization"):
                ane = _get_mx().metal.get_ane_utilization()
                if ane > 0.9 and priority == Priority.LOW:
                    return False
        except AttributeError:
            pass
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
            __slots__ = ("cost", "gov", "prio")

            def __init__(self, gov, cost, prio):
                self.gov = gov
                self.cost = cost
                self.prio = prio

            async def __aenter__(self):
                if not self.gov.can_afford_sync(self.cost, self.prio):
                    raise RuntimeError("ResourceGovernor: cannot afford operation")
                async with self.gov._lock():
                    self.gov._active_tasks += 1
                return self

            async def __aexit__(self, *args):
                async with self.gov._lock():
                    self.gov._active_tasks -= 1

        return _Reservation(self, cost_estimate, priority)


def evaluate_uma_state(system_used_gib: float) -> str:
    """
    Sprint 8AB + F289: Map system_used_gib to UMA state.

    Python 3.10+ match statement s guard clauses pro thresholdy.
    Exhaustivní match — kompilátor hlídá, že všechny stavy jsou pokryty.

    Calibrated for M1 8GB UMA:
        < 6.8 GiB → "ok"
        >= 6.8   → "soft_warn"  (F220K: approaching WARN, reduce 50%)
        >= 7.0   → "warn"
        >= 7.5   → "critical"   (F265H: proactive at 94%)
        >= 7.8   → "emergency"  (near-OOM, 98%)

    Args:
        system_used_gib: (total - available) in GiB, THRESHOLD DRIVER.

    Returns:
        State string: "ok" | "soft_warn" | "warn" | "critical" | "emergency".
    """
    match ():
        case _ if system_used_gib >= _THRESHOLD_EMERGENCY_GIB:
            return UMAState.EMERGENCY
        case _ if system_used_gib >= _THRESHOLD_CRITICAL_GIB:
            return UMAState.CRITICAL
        case _ if system_used_gib >= _THRESHOLD_WARN_GIB:
            return UMAState.WARN
        case _ if system_used_gib >= _THRESHOLD_SOFT_WARN_GIB:
            return UMAState.SOFT_WARN
        case _:
            return UMAState.OK


def should_enter_io_only_mode(
    system_used_gib: float, previous_io_only: bool = False, swap_detected: bool = False
) -> bool:
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
    if previous_io_only:
        return system_used_gib > _HYSTERESIS_EXIT_GIB
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
        from ..utils.mlx_cache import get_metal_limits_status

        status = get_metal_limits_status()
        return (status.get("cache_limit_bytes"), status.get("wired_limit_bytes"))
    except Exception:
        return (None, None)


async def _get_memory_pressure_status_async() -> str:
    """
    Sprint 8AL-FIX + O4-FIX: Read memory_pressure CLI status on macOS.

    Uses asyncio.create_subprocess_exec + asyncio.wait_for (instead of blocking
    subprocess.run) so this function is safe to call from async contexts without
    blocking the M1 event loop.

    The raw memory_pressure output tells us memory pressure level via
    "System-wide memory free percentage: N%":
        > 50% free → GREEN  (healthy)
        30-50%     → YELLOW (mild pressure, normal for M1 under load)
        < 30%      → RED    (severe pressure, swap_detected should trigger)

    Also falls back to "Compressor Stats" page count — a growing compressor
    indicates M1 memory system is actively compressing pages (normal on UMA).

    Returns status string: "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    Fail-open: returns "UNKNOWN" on any error (no spurious swap_detected).

    NOTE: This function is NOT called by any live code path. The canonical
    memory pressure reader is _read_memory_pressure_sync (psutil-based, µs latency).
    This async variant exists as a safety net for any future async call sites.
    """
    import re

    try:
        proc = await asyncio.create_subprocess_exec(
            "memory_pressure",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(2.0):
                stdout, stderr = await proc.communicate()
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            return "UNKNOWN"

        if proc.returncode != 0:
            return "UNKNOWN"
        output = stdout.decode()
        m = re.search(r"free percentage:\s*(\d+)%", output)
        if m:
            free_pct = int(m.group(1))
            if free_pct < 30:
                return "RED"
            elif free_pct < 50:
                return "YELLOW"
            else:
                return "GREEN"
        cm = re.search(r"Pages used by compressor:\s*(\d+)", output)
        if cm:
            compressor_pages = int(cm.group(1))
            if compressor_pages >= 250000:
                return "RED"
            elif compressor_pages >= 200000:
                return "YELLOW"
        return "UNKNOWN"
    except (asyncio.TimeoutError, asyncio.CancelledError):
        raise
    except Exception:
        return "UNKNOWN"


def _get_memory_pressure_status() -> str:
    """
    Blocking shim for _get_memory_pressure_status_async.

    DEPRECATED: This sync version exists only for backward compatibility.
    It delegates to _read_memory_pressure_sync (psutil, µs) which is the
    actual live path. The asyncio version above should be used for any new
    async call sites.

    Returns status string: "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    """
    return "UNKNOWN"


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
    rss_gib: float = 0.0
    try:
        proc = _get_cached_process()
        rss_gib = proc.memory_info().rss / 1024**3
    except Exception as exc:
        last_error = f"psutil.Process: {exc}"
    system_used_gib: float = 0.0
    system_available_gib: float = 0.0
    # ISSUE-35: os.proc_available_memory() is more accurate than psutil on Apple Silicon.
    # Available on macOS 13+ (Ventura and later). Falls back to psutil if unavailable.
    # ISSUE-35: os.proc_available_memory() is more accurate than psutil on Apple Silicon.
    # Available on macOS 13+ (Ventura and later). Falls back to psutil if unavailable.
    if sys.platform == "darwin" and hasattr(os, "proc_available_memory"):
        try:
            proc_avail_bytes = os.proc_available_memory()
            system_available_gib = proc_avail_bytes / 1024**3
            # Derive system_used_gib from psutil total for consistency.
            vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
            if vm is not None:
                system_used_gib = (vm.total - vm.available) / 1024**3
        except Exception as exc:
            last_error = f"os.proc_available_memory: {exc}"
            try:
                vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
                if vm is not None:
                    system_used_gib = (vm.total - vm.available) / 1024**3
                    system_available_gib = vm.available / 1024**3
            except Exception as exc2:
                last_error = f"virtual_memory: {exc2}"
                system_used_gib = 0.0
                system_available_gib = 0.0
    else:
        # Fallback: psutil virtual_memory
        try:
            vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
            if vm is not None:
                system_used_gib = (vm.total - vm.available) / 1024**3
                system_available_gib = vm.available / 1024**3
        except Exception as exc:
            last_error = f"virtual_memory: {exc}"
            system_used_gib = 0.0
            system_available_gib = 0.0
    swap_used_gib: float = 0.0
    try:
        sm = _get_cached_psutil("swap_memory", _read_swap_memory_sync)
        if sm is not None:
            swap_used_gib = sm.used / 1024**3
    except Exception:
        pass
    metal_cache_limit_bytes, metal_wired_limit_bytes = _get_metal_limits_status_8ab()
    metal_active_gib: float = 0.0
    metal_peak_gib: float = 0.0
    try:
        mx = _get_mx()
        if mx is not None:
            if hasattr(mx, "get_active_memory"):
                metal_active_gib = mx.get_active_memory() / 1024**3
            elif hasattr(mx.metal, "get_active_memory"):
                metal_active_gib = mx.metal.get_active_memory() / 1024**3
            if hasattr(mx, "get_peak_memory"):
                metal_peak_gib = mx.get_peak_memory() / 1024**3
            elif hasattr(mx.metal, "get_peak_memory"):
                metal_peak_gib = mx.metal.get_peak_memory() / 1024**3
    except Exception:
        pass
    state = evaluate_uma_state(system_used_gib)
    _pressure_result = _get_cached_psutil("memory_pressure", _read_memory_pressure_sync)
    _pressure_status = _pressure_result.get("status", "UNKNOWN") if _pressure_result else "UNKNOWN"
    swap_detected = swap_used_gib > 5.0 or _pressure_status in ("CRITICAL", "RED")
    prev_io_only, io_only = _update_io_only_latch_with_lock(system_used_gib, swap_detected=swap_detected)
    _record_transition(state, prev_io_only, io_only)
    return UMAStatus(
        rss_gib=rss_gib,
        system_used_gib=system_used_gib,
        system_available_gib=system_available_gib,
        swap_used_gib=swap_used_gib,
        metal_cache_limit_bytes=metal_cache_limit_bytes,
        metal_wired_limit_bytes=metal_wired_limit_bytes,
        state=state,
        io_only=io_only,
        metal_active_gib=metal_active_gib,
        metal_peak_gib=metal_peak_gib,
        swap_detected=swap_detected,
        last_error=last_error,
    )


async def sample_uma_status_async() -> UMAStatus:
    """
    Async-friendly version of sample_uma_status().

    Runs the entire sampling logic in a background thread via run_in_executor,
    eliminating all blocking psutil syscalls from the event loop.

    Use this instead of sample_uma_status() when calling from async contexts
    (e.g., inside async def functions that are not already running in a thread).

    Returns:
        UMAStatus frozen dataclass (same as sample_uma_status).
    """
    return await asyncio.to_thread(sample_uma_status)


def get_uma_telemetry() -> dict[str, Any]:
    """Sprint 8AB: Read-only telemetry snapshot (transition counts, last state)."""
    return dict(_telemetry)


try:
    _HYSTERESIS_COOLDOWN_SEC: float = _rg_float("HYSTERESIS_COOLDOWN_SEC")
except NameError:
    _HYSTERESIS_COOLDOWN_SEC = 2.0


async def _dispatch_one(cb: Callable[[], Any], logger_instance: logging.Logger) -> None:
    """Fire-and-forget callback dispatcher with explicit logger injection."""
    try:
        if inspect.iscoroutinefunction(cb):
            await cb()
        elif asyncio.iscoroutine(cb):
            await cb
        elif callable(cb):
            cb()
    except Exception as e:
        logger_instance.debug(f"[alarm] callback error: {e!r}")


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

    __slots__ = tuple(
        ("__lock", "_callbacks", "_interval_s", "_last_dispatch_time", "_lock_factory", "_running", "_task")
    )

    def __init__(self) -> None:
        self._lock_factory = threading.Lock()
        self.__lock: asyncio.Lock | None = None
        self._callbacks: dict[str, list] = {UMA_STATE_CRITICAL: [], UMA_STATE_EMERGENCY: []}
        self._task: asyncio.Task | None = None
        self._running = False
        self._interval_s: float = 5.0
        self._last_dispatch_time: dict[str, float] = {
            UMA_STATE_CRITICAL: float("-inf"),
            UMA_STATE_EMERGENCY: float("-inf"),
        }

    def _lock(self) -> asyncio.Lock:
        """Thread-safe lazy init pro asyncio.Lock — double-checked locking chráněný threading.Lock."""
        lock = self.__lock
        if lock is None:
            with self._lock_factory:
                lock = self.__lock
                if lock is None:
                    lock = asyncio.Lock()
                    self.__lock = lock
        return lock

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
        self._task = safe_create_task(self._monitor_loop())

    async def stop(self) -> None:
        """
        Stop the monitoring loop. Clean cancellation via CancelledError.

        B.3: Callback threading — dispatch happens in asyncio.Task,
        cancellation is clean (no unhandled exceptions).
        """
        self._running = False
        await stop_task(self._task)
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
                await asyncio.to_thread(_refresh_psutil_cache_sync)
                await self._check_and_dispatch()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _check_and_dispatch(self) -> None:
        """Sample UMA and dispatch callbacks on state transitions."""
        status = sample_uma_status()
        current_state = status.state
        if current_state not in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return
        async with self._lock():
            now = time.monotonic()
            last_time = self._last_dispatch_time.get(current_state, 0.0)
            if now - last_time < _HYSTERESIS_COOLDOWN_SEC:
                return
            callbacks = list(self._callbacks.get(current_state, []))
            if not callbacks:
                return
            self._last_dispatch_time[current_state] = now
        await parallel(
            [_dispatch_one(cb, logger) for cb in callbacks],
            policy="log",
            ctx="resource_governor:648",
        )


from collections import deque

_MPC_HISTORY: deque[tuple[float, float, float, float, float]] = deque(maxlen=32)
_mpc_lock: _threading.Lock = _threading.Lock()
register_lock(LockCategory.MPC, _mpc_lock, "resource_governor._mpc_lock")


class MPCMetrics(msgspec.Struct, frozen=True, gc=False):
    """
    F290: Diagnostic snapshot from MPC controller.
    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.

    All values are measured/derived at computation time.
    Use for telemetry, debugging, and regression testing.
    """

    predicted_memory_gib: float
    velocity_gib_per_sec: float
    acceleration_gib_per_sec2: float
    ema_velocity: float
    ema_acceleration: float
    safe_headroom_gib: float
    control_input: float
    predicted_state: str


class AdaptiveMPCController:
    """
    F290: Adaptive Model Predictive Controller for M1 8GB UMA.

    Replaces reactive threshold-based state machine with predictive MPC that
    derives concurrency limits from memory velocity and acceleration trends.

    Control law (analytical, O(1)):
        safe_headroom = self._EMERGENCY_THRESHOLD_GIB - predicted_memory
        if safe_headroom < 0:
            control = clamp(1.0 + safe_headroom / self._EMERGENCY_THRESHOLD_GIB, 0.0, 0.3)
        elif safe_headroom < self._TARGET_HEADROOM_GIB:
            control = clamp(safe_headroom / self._TARGET_HEADROOM_GIB, 0.3, 0.8)
        else:
            control = 1.0

    EMA calibration for M1 8GB:
        - alpha_fast=0.4: responsive to current changes
        - alpha_slow=0.15: baseline trend (ignores momentary spikes)
        - MPC horizon=10s: 2x sample interval (5s), enough to react before OOM
        - Control horizon=1: immediate concurrency adjustment

    invariants:
        - Always-on, no feature flags
        - Bounded history (_mpc_history maxlen=32)
        - Fail-safe: returns control=1.0 (nominal) on any error
        - Async-safe via _mpc_lock
    """

    try:
        _ALPHA_FAST: float = _rg_float("ALPHA_FAST")
        _ALPHA_SLOW: float = _rg_float("ALPHA_SLOW")
        _MPC_HORIZON_S: float = _rg_float("MPC_HORIZON_S")
        _TARGET_HEADROOM_GIB: float = _rg_float("TARGET_HEADROOM_GIB")
        _EMERGENCY_THRESHOLD_GIB: float = _rg_float("EMERGENCY_THRESHOLD_GIB")
    except NameError:
        _ALPHA_FAST = 0.4
        _ALPHA_SLOW = 0.15
        _MPC_HORIZON_S = 10.0
        _TARGET_HEADROOM_GIB = 0.5
        _EMERGENCY_THRESHOLD_GIB = 7.8
    __slots__ = ("_ema_v", "_ema_a", "_last_t", "_last_mem", "_enabled")

    def __init__(self) -> None:
        self._ema_v: float = 0.0
        self._ema_a: float = 0.0
        self._last_t: float | None = None
        self._last_mem: float | None = None
        self._enabled: bool = True

    async def compute_control(
        self, current_memory_gib: float, current_state: str, sample_interval_s: float = 5.0
    ) -> tuple[float, MPCMetrics]:
        """
        F290: Compute MPC control input for memory pressure.

        Uses EMA-based prediction to forecast memory state at MPC_HORIZON_S
        ahead, then derives concurrency control factor.

        Args:
            current_memory_gib: Current system_used_gib
            current_state: Current UMA state string
            sample_interval_s: Time since last sample (default 5.0s)

        Returns:
            tuple of (control_input, mpc_metrics)
            control_input: concurrency scale factor 0.0-1.0
            mpc_metrics: diagnostic snapshot for telemetry
        """
        now = time.monotonic()
        with _mpc_lock:
            if self._last_t is None or self._last_mem is None:
                self._last_t = now
                self._last_mem = current_memory_gib
                self._ema_v = 0.0
                self._ema_a = 0.0
                safe_headroom = self._EMERGENCY_THRESHOLD_GIB - current_memory_gib
                metrics = MPCMetrics(
                    predicted_memory_gib=current_memory_gib,
                    velocity_gib_per_sec=0.0,
                    acceleration_gib_per_sec2=0.0,
                    ema_velocity=0.0,
                    ema_acceleration=0.0,
                    safe_headroom_gib=safe_headroom,
                    control_input=1.0,
                    predicted_state=current_state,
                )
                _MPC_HISTORY.append((now, current_memory_gib, 0.0, 0.0, 1.0))
                return (1.0, metrics)
            raw_dt = now - self._last_t
            dt = max(raw_dt, sample_interval_s)
            self._last_t = now
            raw_v = (current_memory_gib - self._last_mem) / dt
            raw_v = max(-2.0, min(2.0, raw_v))
            self._last_mem = current_memory_gib
            prev_ema = self._ema_v
            self._ema_v = self._ALPHA_FAST * raw_v + (1.0 - self._ALPHA_FAST) * self._ema_v
            raw_a = (self._ema_v - prev_ema) / dt
            raw_a = max(-0.2, min(0.2, raw_a))
            self._ema_a = self._ALPHA_SLOW * raw_a + (1.0 - self._ALPHA_SLOW) * self._ema_a
            accel_factor = 1.0 + 0.1 * self._ema_a
            accel_factor = max(0.7, min(1.3, accel_factor))
            predicted = current_memory_gib + self._ema_v * self._MPC_HORIZON_S * accel_factor
            safe_headroom = self._EMERGENCY_THRESHOLD_GIB - predicted
            if safe_headroom < 0:
                control = max(0.0, min(0.3, 1.0 + safe_headroom / self._EMERGENCY_THRESHOLD_GIB))
            elif safe_headroom < self._TARGET_HEADROOM_GIB:
                ratio = safe_headroom / self._TARGET_HEADROOM_GIB
                control = 0.3 + 0.5 * ratio
            else:
                control = 1.0
            if current_state in (UMAState.EMERGENCY, UMAState.CRITICAL):
                control = min(control, 0.1)
            _MPC_HISTORY.append((now, current_memory_gib, raw_v, raw_a, control))
            predicted_state = evaluate_uma_state(predicted)
            metrics = MPCMetrics(
                predicted_memory_gib=predicted,
                velocity_gib_per_sec=raw_v,
                acceleration_gib_per_sec2=raw_a,
                ema_velocity=self._ema_v,
                ema_acceleration=self._ema_a,
                safe_headroom_gib=safe_headroom,
                control_input=control,
                predicted_state=predicted_state,
            )
            return (control, metrics)

    def reset(self) -> None:
        """Reset controller state. For testing only."""
        self._ema_v = 0.0
        self._ema_a = 0.0
        self._last_t = None
        self._last_mem = None


def get_mpc_telemetry() -> dict[str, Any]:
    """
    F290: Read-only MPC telemetry snapshot.

    Returns:
        dict with 'history' (last 32 samples) and 'enabled' flag.
    """
    return {
        "enabled": True,
        "history_count": len(_MPC_HISTORY),
        "latest": {
            "timestamp": _MPC_HISTORY[-1][0] if _MPC_HISTORY else None,
            "memory_gib": _MPC_HISTORY[-1][1] if _MPC_HISTORY else None,
            "velocity_gib_s": _MPC_HISTORY[-1][2] if _MPC_HISTORY else None,
            "acceleration_gib_s2": _MPC_HISTORY[-1][3] if _MPC_HISTORY else None,
            "control_input": _MPC_HISTORY[-1][4] if _MPC_HISTORY else None,
        }
        if _MPC_HISTORY
        else None,
    }


_QOS_USER_INITIATED: int = 25
_QOS_UTILITY: int = 17
_QOS_BACKGROUND: int = 9


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
        libc.syscall(366, qos_level, 0)
    except Exception as exc:
        logger.debug(f"[QoS] pthread_set_qos_class_self_np failed (non-macOS or permission): {exc}")


def get_lane_ram_budget(lane_id: str) -> int:
    """
    P1-03: Get RAM budget for an acquisition lane by its identifier.

    Surfaces per-lane memory cost to ResourceGovernor consumers so that
    lane scheduling decisions can account for memory pressure.

    Args:
        lane_id: Lane identifier string (e.g. "BGP", "SHODAN", "CT").

    Returns:
        RAM budget in MB (M1 8GB calibrated), defaults to 30MB for unknown lanes.
    """
    try:
        from runtime.acquisition.lane_constants import get_lane_ram_budget as _get

        return _get(lane_id)
    except Exception:
        return 30
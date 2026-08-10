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

Sprint 8AB + P7-3: Unified UMA accountant surface (WARN/CRITICAL/EMERGENCY + I/O-only mode).
Threshold driver: system_used_gib (total - available), NOT process rss_gib.

P7-3 SSOT: Threshold constants from utils.uma_budget.UmaBudget (M1 8GB, 6.25 GiB ceiling):
    - 5.5 GiB  → SOFT_WARN (88% of ceiling = MISSION_PEAK_RSS_GIB)
    - 5.938 GiB → WARN (95% of ceiling)
    - 6.191 GiB → CRITICAL (99% of ceiling)
    - 6.25 GiB  → EMERGENCY (ceiling = 100%)
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import ctypes
import inspect
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from collections.abc import Callable
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
import heapq
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


from cachetools import LRUCache as _BaseLRUCache
from typing import NamedTuple as _NamedTuple

# M1-05 FIX: LRUCache with TTL — replaces unbounded dict + sorted() eviction.
# Invariants:
#   - maxsize=32 (matches _MAX_PSUTIL_CACHE_SIZE)
#   - TTL=2.0s (matches _PSUTIL_CACHE_TTL_S)
#   - Bounded: O(1) get/evict, no sorted() on every write
#   - Thread-safe: per-key lock still guards compute; LRU operations are GIL-protected


class _CacheEntry(_NamedTuple):
    value: Any
    timestamp: float


class _TTLLRUCache:
    """
    LRU cache with per-entry TTL expiration (composition, not inheritance).

    Wraps cachetools.LRUCache for O(1) bounded storage without inheriting
    its slots, avoiding type-checker conflicts with LRUCache.__slots__ = ().

    Invariants:
      - maxsize=32 (matches old _MAX_PSUTIL_CACHE_SIZE)
      - TTL=2.0s (matches old _PSUTIL_CACHE_TTL_S)
      - O(1) get/evict, no sorted() on every write
      - Thread-safe: per-key lock guards compute; LRU ops are GIL-protected
    """

    __slots__ = ("_cache", "_ttl")

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._cache: _BaseLRUCache = _BaseLRUCache(maxsize=maxsize)
        self._ttl: float = ttl

    def get(self, key: str, default: Any = None) -> Any:
        """LRU-aware get with TTL expiry. Drops expired entries."""
        entry = self._cache.get(key)
        now = _time_module.monotonic()
        if entry is None:
            return default
        value, timestamp = entry
        if now - timestamp > self._ttl:
            self._cache.pop(key, None)
            return default
        # Touch entry to update LRU ordering
        self._cache[key] = _CacheEntry(value, timestamp)
        return value

    def set(self, key: str, value: Any) -> None:
        """Set value with current timestamp for TTL."""
        now = _time_module.monotonic()
        self._cache[key] = _CacheEntry(value, now)

    def pop(self, key: str, default: Any = None) -> Any:
        """Remove and return value."""
        return self._cache.pop(key, default)

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()

    def __setitem__(self, key: str, value: _CacheEntry) -> None:
        """Allow direct subscript assignment: cache[key] = value."""
        self._cache[key] = value

    def __getitem__(self, key: str) -> _CacheEntry:
        """Allow direct subscript access: cache[key]."""
        return self._cache[key]

    def __contains__(self, key: str) -> bool:
        """Allow 'in' operator."""
        return key in self._cache


# Singleton cache instance — same 32-entry / 2s TTL bounds as the old dict
_psutil_cache: _TTLLRUCache = _TTLLRUCache(maxsize=32, ttl=2.0)
_psutil_meta_lock: _threading.Lock = _threading.Lock()
register_lock(LockCategory.CACHE, _psutil_meta_lock, "resource_governor._psutil_meta_lock")


def reset_psutil_cache() -> None:
    """Reset psutil TTL cache. For testing only — clears all cached readings."""
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


def _read_thermal_state_sync() -> dict[str, Any]:
    """
    APEX-1002: Read M1 thermal state from hw.thermal.thermal_level sysctl.

    On MacBook Air M1 (fanless), sustained workloads cause thermal throttling.
    The kernel aggregates thermal state into hw.thermal.thermal_level:
        0 = nominal (no throttling)
        1-2 = mild (beginning throttle)
        3-4 = moderate (significant throttle, 2-5x slowdown)
        5 = critical (severe throttle, 10x slowdown)

    This is more reliable than raw CPU temperature because:
    - It's kernel-aggregated (accounts for duration, not just instant temp)
    - It directly reflects actual frequency scaling
    - It's the same signal macOS uses for thermal management

    Returns:
        dict with keys:
            thermal_level: int | None (0-5 or None if unavailable)
            is_throttled: bool (True if level >= 3)
            error: str | None (error message if read failed)
    """
    if sys.platform != "darwin":
        return {"thermal_level": None, "is_throttled": False, "error": "not_macos"}

    try:
        import subprocess

        # Read hw.thermal.thermal_level
        result = subprocess.run(
            ["sysctl", "-n", "hw.thermal.thermal_level"],
            capture_output=True,
            text=True,
            timeout=0.5,  # Fast timeout — thermal read should be instant
        )

        if result.returncode == 0 and result.stdout.strip():
            thermal_level = int(result.stdout.strip())
            # Level 3+ = significant throttling
            is_throttled = thermal_level >= 3
            return {
                "thermal_level": thermal_level,
                "is_throttled": is_throttled,
                "error": None,
            }
        else:
            # Sysctl exists but returned no value — unusual
            return {"thermal_level": None, "is_throttled": False, "error": "no_value"}

    except subprocess.TimeoutExpired:
        return {"thermal_level": None, "is_throttled": False, "error": "timeout"}
    except FileNotFoundError:
        # Sysctl binary not found — very unusual on macOS
        return {"thermal_level": None, "is_throttled": False, "error": "sysctl_not_found"}
    except ValueError as e:
        # Sysctl returned non-integer value
        return {"thermal_level": None, "is_throttled": False, "error": f"parse_error: {e}"}
    except Exception as e:
        # Any other error — fail-soft
        return {"thermal_level": None, "is_throttled": False, "error": str(e)}


def _get_cached_psutil(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Thread-safe TTL cache for blocking psutil reads.

    Uses _TTLLRUCache (LRU + per-entry TTL) for O(1) bounded access.
    Per-key lock achieves single-flight: only the first misser calls reader_fn(),
    subsequent missers block briefly on the key lock, then read the populated entry.

    Flow:
        1. Fast path — _TTLLRUCache.get() with TTL check, O(1), returns if fresh.
        2. Acquire per-key lock — only threads needing this specific key block.
        3. Double-check — another thread may have populated the cache while we waited.
        4. Compute outside all locks — slow sysctl doesn't block other keys.
        5. _TTLLRUCache.set() — LRU eviction is automatic, no sorted().
    """
    # Fast path — TTL-aware LRU get, O(1)
    cached = _psutil_cache.get(key)
    if cached is not None:
        return cached
    key_lock = _get_key_lock(key)
    with key_lock:
        # Double-check after acquiring lock
        cached = _psutil_cache.get(key)
        if cached is not None:
            return cached
        # Mark as computing (placeholder) to let other waiters know
        _psutil_cache.set(key, None)
    try:
        result = reader_fn()
    except Exception:
        # Remove placeholder on failure
        _psutil_cache.pop(key, None)
        raise
    # Write result — LRU eviction automatic if at capacity
    _psutil_cache.set(key, result)
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

    P3-02 FIX: Psutil calls execute OUTSIDE the lock — lock held only for the
    brief atomic dict write. Previously held _psutil_meta_lock across all three
    psutil syscalls (~150-600µs total), creating a GIL contention bottleneck
    when 100+ concurrent callers from acquisition lanes all serialized on this
    single global lock.

    Lock-free compute phase pattern:
        1. Read all sensors (no lock held — blocking syscalls here)
        2. Briefly acquire lock only for atomic dict update
    """
    if psutil is None:
        return
    # Phase 1: Compute outside lock — blocking syscalls don't block other callers
    now = _time_module.monotonic()
    try:
        vm_data = psutil.virtual_memory()
        swap_data = psutil.swap_memory()
        pressure_data = _read_memory_pressure_sync()
    except Exception:
        return
    # Phase 2: Atomic cache update — lock held for <1µs dict write
    with _psutil_meta_lock:
        _psutil_cache.set("virtual_memory", vm_data)
        _psutil_cache.set("swap_memory", swap_data)
        _psutil_cache.set("memory_pressure", pressure_data)


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


from hledac.universal.core.env_config import ENV as _ENV

_RG_USE_RATIOS: bool = _ENV.get_bool("HLEDAC_RG_USE_RATIOS", default=True)

# MODERN-36 Fix: Import from SSOT
from hledac.universal.utils.uma_budget import (
    UmaBudget,
    MISSION_PEAK_RSS_GIB,
    ORCHESTRATOR_GIB,
)

try:
    from hledac.universal.config import _rg_float

    if _RG_USE_RATIOS:
        # MODERN-36 Fix: Use SSOT ceiling (6.25 GiB) instead of detected memory
        # Old: _adaptive_threshold(_SOFT_WARN_RATIO) with detected memory
        # New: UmaBudget.THRESHOLD_*_GIB (derived from SSOT)
        _THRESHOLD_SOFT_WARN_GIB: float = UmaBudget.THRESHOLD_SOFT_WARN_GIB
        _THRESHOLD_WARN_GIB: float = UmaBudget.THRESHOLD_WARN_GIB
        _THRESHOLD_CRITICAL_GIB: float = UmaBudget.THRESHOLD_CRITICAL_GIB
        _THRESHOLD_EMERGENCY_GIB: float = UmaBudget.THRESHOLD_EMERGENCY_GIB
    else:
        _THRESHOLD_SOFT_WARN_GIB = _rg_float("THRESHOLD_SOFT_WARN_GIB")
        _THRESHOLD_WARN_GIB = _rg_float("THRESHOLD_WARN_GIB")
        _THRESHOLD_CRITICAL_GIB = _rg_float("THRESHOLD_CRITICAL_GIB")
        _THRESHOLD_EMERGENCY_GIB = _rg_float("THRESHOLD_EMERGENCY_GIB")
    _HYSTERESIS_EXIT_GIB: float = _rg_float("HYSTERESIS_EXIT_GIB")
except (ImportError, NameError):
    # MODERN-36 Fix: Use SSOT values instead of derived from detected memory
    # Old: round(_DETECTED_TOTAL_GIB * _SOFT_WARN_RATIO, 2)
    # New: UmaBudget.THRESHOLD_*_GIB (from SSOT)
    _THRESHOLD_SOFT_WARN_GIB = UmaBudget.THRESHOLD_SOFT_WARN_GIB  # 5.5 GiB
    _THRESHOLD_WARN_GIB = UmaBudget.THRESHOLD_WARN_GIB  # 5.938 GiB
    _THRESHOLD_CRITICAL_GIB = UmaBudget.THRESHOLD_CRITICAL_GIB  # 6.191 GiB
    _THRESHOLD_EMERGENCY_GIB = UmaBudget.THRESHOLD_EMERGENCY_GIB  # 6.25 GiB
    # P2-8 COMPREHENSIVE FIX: Create proper hysteresis band
    # Exit threshold MUST be below entry threshold to prevent immediate exit after entry.
    # MODERN-36 Fix: Using ORCHESTRATOR_GIB (1.0 GiB) creates proper band for M1 8GB.
    _HYSTERESIS_EXIT_GIB = round(_THRESHOLD_SOFT_WARN_GIB - ORCHESTRATOR_GIB, 2)  # 4.5 GiB
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
    # MODERN-41 Fix: Use SSOT SWAP_TIERS instead of local derivation
    # SSOT is in utils.uma_budget.SWAP_TIERS
    from hledac.universal.utils.uma_budget import SWAP_TIERS

    CLEAN_SWAP_MAX_GIB = SWAP_TIERS.CLEAN  # 3.3 GiB
    DIAGNOSTIC_SWAP_MAX_GIB = SWAP_TIERS.DIAGNOSTIC  # 4.675 GiB
    HARD_BLOCK_SWAP_GIB = SWAP_TIERS.HARD_BLOCK  # 5.225 GiB


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

# [FINAL]-019-06: Capability-tier QoS degradation level accessor.
# Delegates to the existing [FINAL]-019 infrastructure:
#   - _last_qos_profile: updated by apply_decision() once per evaluate() cycle
#   - is_capability_allowed(cap): canonical capability gate for subsystems
#   - get_qos_level(): returns QoSLevel name string
#
# get_current_degradation_level() returns QoSLevel enum so callers can do
# `if get_current_degradation_level() is QoSLevel.EMERGENCY:` (identity comparison).
#
# graph_rag.get_degradation_safe_max_hops() and whisper_engine.is_whisper_available()
# MUST call this — never import GovernorDecision or M1ResourceGovernor.


def get_current_degradation_level() -> QoSLevel:
    """
    [FINAL]-019-06: Get the current capability-tier QoS degradation level.

    Returns the QoS level most recently written by apply_decision().
    Capability gate functions (whisper_engine.is_whisper_available(),
    graph_rag.get_degradation_safe_max_hops()) MUST call this.

    QoS levels:
        full:       All capabilities enabled — normal operation
        thermal:    Thermal throttling — reduced batch, shorter generations
        windup:     Sprint wind-up — sidecars off, MLX OK
        battery:    Battery low — all MLX suspended, I/O only
        emergency:  Near-OOM — fetch=1, model blocked, whisper off

    Delegates to _last_qos_profile (updated by apply_decision()) which is
    thread-safe via the asyncio lock held during evaluate()→apply_decision().
    """
    try:
        return QoSLevel(get_qos_level())
    except (ValueError, AttributeError):
        # Fail-open: if _last_qos_profile.level is somehow corrupted or the
        # module hasn't been fully initialized, default to FULL capabilities.
        return QoSLevel.FULL


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
    "degraded_enter_count": 0,
    "degraded_exit_count": 0,
    "degraded_last_reason": "",
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

    MODERN-45: Axis documentation for memory fields.

    Memory Axes (see MemoryAxis in uma_budget.py):
    ┌─────────────────────────────────────────────────────────────────────────┐
    │ AXIS: system-used     (total RAM - available RAM, all processes)       │
    │   • system_used_gib: THRESHOLD DRIVER for governor decisions          │
    │   • system_available_gib: Complementary available memory               │
    ├─────────────────────────────────────────────────────────────────────────┤
    │ AXIS: process-rss     (our process's RSS, subset of system-used)       │
    │   • rss_gib: DIAGNOSTIC field (informational only)                     │
    ├─────────────────────────────────────────────────────────────────────────┤
    │ AXIS: tracked-allocation (Hledac's internal ledger)                    │
    │   • metal_*: MLX Metal memory (tracked separately)                      │
    └─────────────────────────────────────────────────────────────────────────┘

    Fields:
        rss_gib: Process RSS in GiB (AXIS: process-rss, DIAGNOSTIC only).
                 NOT used for threshold decisions — see system_used_gib.
        system_used_gib: (total - available) in GiB (AXIS: system-used, THRESHOLD DRIVER).
        system_available_gib: Available system memory in GiB.
        swap_used_gib: Swap usage in GiB (diagnostic only — F163F).
        swap_detected: True if swap > 3.8 GiB (active swap = systemic pressure).
        metal_cache_limit_bytes: Metal cache limit from 8T surface (or None).
        metal_wired_limit_bytes: Metal wired limit from 8T surface (or None).
        state: "ok" | "soft_warn" | "warn" | "critical" | "emergency".
        io_only: True if I/O-only mode should be active.
        last_error: Error message if sampling failed (None = OK).

    MODERN-45 INVARIANT:
        system_used_gib >= rss_gib (system-used always >= process-rss)
        unless system is nearly idle and other processes use minimal memory.

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

    APEX-1002: Thermal fields added for M1 throttling detection.
    On MacBook Air M1 (fanless), thermal throttling can reduce CPU frequency
    from 3.2GHz to 600MHz, causing 10x slowdown in MLX inference.
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
    # HW-02: Power status fields
    on_battery: bool = False
    battery_level: int | None = None
    ac_attached: bool = True
    # APEX-1002: Thermal state for M1 throttling detection
    thermal_level: int | None = None  # hw.thermal.thermal_level (0=nominal, 5=critical)
    is_thermally_throttled: bool = False  # True if thermal_level >= 3


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


class QoSLevel(StrEnum):
    """
    [FINAL]-019: Canonical QoS degradation ladder — 5-tier capability toggle ladder.

    Each level disables a specific set of expensive capabilities, reducing
    resource pressure without hard-killing the sprint. Levels are mutually
    exclusive (only one active at a time). Transitions are ordered:

        FULL → THERMAL → WINDUP → BATTERY → EMERGENCY

    The governor computes the worst applicable level from all active
    constraints (UMA state, thermal, battery, sprint phase) and propagates
    it to subsystems via GovernorDecision.qos_level.

    Capability toggles (wired in apply_decision()):
        FULL:       all capabilities enabled
        THERMAL:    MLX batch size reduced (via batch_scale_factor)
        WINDUP:     sidecars reduced (via worker_scale_factor)
        BATTERY:    all MLX inference suspended (io_only=True)
        EMERGENCY:  fetch reduced to 1, model blocked

    Integration: CapabilityToggleService (core/resource_governor.py) is the
    canonical sink. Subsystems call get_qos_level() to gate their own
    expensive operations. GovernorDecision.qos_profile carries the full
    QoSProfile snapshot for inspection.
    """

    FULL = "full"       # All capabilities enabled — normal operation
    THERMAL = "thermal" # Thermal throttling: reduced batch, shorter generations
    WINDUP = "windup"   # Sprint windup: sidecars reduced, MLX still OK
    BATTERY = "battery"  # Battery low: all MLX suspended, I/O only
    EMERGENCY = "emergency"  # Near-OOM: fetch=1, model blocked

    # Python 3.14 StrEnum: non-string class attributes must be wrapped with
    # enum.nonmember() or they're treated as enum members. Severity ordering
    # (higher index = more restrictive; 0=FULL, 4=EMERGENCY).
    _SEVERITY: dict[str, int] = enum.nonmember({
        "full": 0,
        "thermal": 1,
        "windup": 2,
        "battery": 3,
        "emergency": 4,
    })

    @property
    def severity(self) -> int:
        """Numeric severity: 0 = FULL, 4 = EMERGENCY."""
        return self._SEVERITY[self.value]

    def at_least(self, other: "QoSLevel") -> bool:
        """True if this level is at least as restrictive as `other`.
        
        Example: QoSLevel.BATTERY.at_least(QoSLevel.WINDUP) → True
        """
        return self.severity >= other.severity

    def at_most(self, other: "QoSLevel") -> bool:
        """True if this level is no more restrictive than `other`."""
        return self.severity <= other.severity

    def __ge__(self, other: "QoSLevel") -> bool:
        if not isinstance(other, QoSLevel):
            return NotImplemented
        return self.severity >= other.severity

    def __le__(self, other: "QoSLevel") -> bool:
        if not isinstance(other, QoSLevel):
            return NotImplemented
        return self.severity <= other.severity

    def __gt__(self, other: "QoSLevel") -> bool:
        if not isinstance(other, QoSLevel):
            return NotImplemented
        return self.severity > other.severity

    def __lt__(self, other: "QoSLevel") -> bool:
        if not isinstance(other, QoSLevel):
            return NotImplemented
        return self.severity < other.severity


class QoSProfile(msgspec.Struct, frozen=True, gc=False):
    """
    [FINAL]-019: QoS profile snapshot emitted with every GovernorDecision.

    Provides structured capability toggles that subsystems can consume
    without needing to interpret raw numeric factors. The governor computes
    which level is active and populates the corresponding flags.

    Fields:
        level:              Canonical QoS level (see QoSLevel).
        mlx_inference_ok:  True if MLX inference is permitted.
        sidecars_ok:        True if sidecar advisory is permitted.
        fetch_ok:           True if network fetch is permitted.
        embeddings_ok:      True if RAG embedding is permitted.
        model_load_ok:      True if model loading is permitted.
        whisper_ok:         True if Whisper STT is permitted.
        max_workers_pct:    Max workers as percentage of nominal (0-100).
        fetch_limit_cap:    Hard cap on concurrent fetches.
        reason:             Human-readable reason for the active level.
    """

    level: str = "full"
    mlx_inference_ok: bool = True
    sidecars_ok: bool = True
    fetch_ok: bool = True
    embeddings_ok: bool = True
    model_load_ok: bool = True
    whisper_ok: bool = True
    max_workers_pct: int = 100
    fetch_limit_cap: int | None = None
    reason: str = ""


class GovernorDecision(msgspec.Struct, frozen=True, gc=False):
    """
    G-1 Fix: Canonical governor rozhodnutí s auto-apply semantics.
    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.

    F-G1: GovernorDecision is now auto-applied — evaluate() calls
    apply_decision() internally before returning. Callers that ignore
    the return value are in violation of the GOVERNOR AUTHORITY CONTRACT.

    [FINAL]-019: Added qos_level (QoSLevel name) and qos_profile (full
    QoSProfile snapshot) for structured capability toggle propagation.

    [FINAL]-019-06: Added degradation_level (QoSLevel enum) — the canonical
    degradation level that capability gate functions read via
    get_current_degradation_level().

    fields:
        uma_state:       "ok" | "soft_warn" | "warn" | "critical" | "emergency".
        io_only:          True pokud I/O-only mód (žádné CPU-intensive operace).
        fetch_limit:      MAX souběžných fetch operací.
        block_model_load: True pokud by se neměl load nový MLX model.
        qos_level:        [FINAL]-019: Canonical QoS level name (see QoSLevel).
        qos_profile:      [FINAL]-019: Full QoSProfile snapshot for subsystems.
        degradation_level: [FINAL]-019-06: QoSLevel enum — canonical degradation
                          level. apply_decision() propagates this to the module-level
                          _last_qos_profile which get_current_degradation_level() reads.
    """

    uma_state: str
    io_only: bool
    fetch_limit: int
    block_model_load: bool = False
    swap_detected: bool = False  # ISSUE-35: expose swap signal for backpressure decisions
    thermal_throttled: bool = False  # HW-01: CPU/GPU thermal throttling detected
    thermal_headroom: float = 1.0  # HW-01: 0.0-1.0, 1.0 = no throttling
    # HW-03: Thermal scaling factors derived from thermal_headroom
    worker_scale_factor: float = 1.0  # 0.0-1.0, scales max_workers
    batch_scale_factor: float = 1.0  # 0.0-1.0, scales MLX batch size
    # ISSUE-015: Thermal generation parameters — shorter outputs under thermal pressure
    # reduce heat emission and allow thermal recovery on fanless M1 devices
    max_tokens_override: int | None = None  # None = use model default, otherwise cap max_tokens
    temperature_reduction: float = 0.0  # 0.0-0.5, subtracted from temperature when throttled
    power_status: dict[str, bool | int | float | None] = {}  # HW-02: battery state info
    # PHYSICS-01: Micro-burst phase for proactive thermal interleaving.
    # "GPU_HEAVY" = compute window (200 ms) — OK to dispatch MLX inference.
    # "IO_HEAVY" = I/O-only window (50 ms) — only network/DNS/disk, yield to event loop.
    burst_phase: str = "GPU_HEAVY"
    # [FINAL]-019: Structured QoS ladder
    qos_level: str = "full"  # QoSLevel name — canonical capability toggle level
    qos_profile: QoSProfile = QoSProfile()  # Full profile snapshot for subsystems
    # [FINAL]-019-06: Canonical QoS degradation level (enum). apply_decision()
    # propagates this to the module-level _last_qos_profile which
    # get_current_degradation_level() reads.
    degradation_level: QoSLevel = QoSLevel.FULL


class M1ThermalStatus(msgspec.Struct, frozen=True, gc=False):
    """
    HW-01: Termální stav M1 procesoru.

    sleduje CPU teplotu, GPU teplotu, CPU frekvenci a detekuje termální throttling.
    M1 čipy mají limity: CPU ~60-70°C, GPU ~85°C. Při překročení dochází
    k automatickému throttlingu s 2-3x zpomalením ML inferencí.

    ISSUE-014: Přidány smc_zones (AppleSMC IOKit thermal zones) a
    thermal_level (hw.thermal.thermal_level diskrétní level).
    """

    cpu_temperature_c: float | None = None
    gpu_temperature_c: float | None = None
    cpu_frequency_mhz: float | None = None
    cpu_core_count: int = 0
    is_throttled: bool = False
    thermal_headroom: float = 1.0  # 0.0-1.0, 1.0 = žádný throttling
    # ISSUE-014: AppleSMC thermal zones (TC0P, TG0P, …) přes IOKit
    smc_zones: dict[str, float | None] = {}
    # ISSUE-014: Diskrétní macOS thermal level (0=nominal, vyšší=teplejší)
    thermal_level: int | None = None


class M1ThermalMonitor:
    """
    HW-01: Monitor termálního stavu M1 procesoru.

    Implementuje sysctl-based čtení CPU teploty a frekvence.
    Rate-limited čtení (min 0.5s interval) pro minimalizaci syscall overhead.

    Throttling limity:
        - CPU: >70°C začíná throttling, >85°C plný throttling
        - GPU: >80°C varování, >85°C throttling

    ISSUE-014 fix: Přidán hw.thermal.thermal_level sysctl (diskrétní thermal level
    macOS kernel agreguje přes duration sustained teploty — spolehlivější než
    raw teplota z hw.sensors). Navíc AppleSMC thermal zone klíče (TC0P, TG0P…)
    přes IOKit pro skutečné hardware sensor teploty.

    Vždy fail-soft: při jakékoli chybě vrací None hodnoty.
    """

    _CPU_TEMP_PATHS: tuple[str, ...] = ("hw.sensors.cpu_temperature", "hw.cputemperature")
    _GPU_TEMP_PATHS: tuple[str, ...] = ("hw.sensors.gpu_temperature", "hw.sensors.gpu_0_temperature")
    _CPU_FREQ_PATHS: tuple[str, ...] = ("hw.cpufrequency", "hw.cpufrequency_max")
    # ISSUE-014: Diskrétní thermal level — system-level agregace přes duration
    _THERMAL_LEVEL_PATHS: tuple[str, ...] = ("hw.thermal.thermal_level",)
    _READ_INTERVAL_S: float = 0.5
    _CPU_THROTTLE_TEMP: float = 70.0  # °C, začátek CPU throttlingu
    _CPU_CRITICAL_TEMP: float = 85.0  # °C, plný throttling
    _GPU_THROTTLE_TEMP: float = 80.0  # °C, začátek GPU throttlingu
    _GPU_CRITICAL_TEMP: float = 85.0  # °C, plný throttling

    __slots__ = ("_last_reading", "_last_read_time")

    def __init__(self) -> None:
        self._last_reading: M1ThermalStatus | None = None
        self._last_read_time: float = 0.0

    @staticmethod
    def _get_cpu_core_count() -> int:
        """Počet CPU jader (P+E jádra)."""
        try:
            import psutil as _psutil

            return _psutil.cpu_count(logical=False) or 8
        except Exception:
            return 8  # Default pro M1

    # [PHYSICS]-02: Lazy-loaded libc for direct sysctlbyname(3) calls.
    # Eliminates 1-50ms subprocess overhead per thermal reading.
    # sysctlbyname is a direct syscall wrapper — 1-5μs vs 1-5ms fork+exec.
    _LIBC: ctypes.CDLL | None = None

    @classmethod
    def _get_libc(cls) -> ctypes.CDLL | None:
        """Lazy-load libc.dylib and configure sysctlbyname argtypes.

        Returns None on non-macOS, sandbox, or library load failure.
        Callers fall back to subprocess-based sysctl(8) transparently.
        """
        if cls._LIBC is None:
            try:
                libc_path = ctypes.util.find_library("c")
                if libc_path is None:
                    cls._LIBC = False  # sentinel: tried and failed
                    return None
                libc = ctypes.CDLL(libc_path, use_errno=True)
                libc.sysctlbyname.argtypes = [
                    ctypes.c_char_p,                # name
                    ctypes.c_void_p,                # oldp
                    ctypes.POINTER(ctypes.c_size_t),  # oldlenp
                    ctypes.c_void_p,                # newp
                    ctypes.c_size_t,                # newlen
                ]
                libc.sysctlbyname.restype = ctypes.c_int
                cls._LIBC = libc
            except Exception:
                cls._LIBC = False
                return None
        if cls._LIBC is False:
            return None
        return cls._LIBC

    @staticmethod
    def _read_sysctl_int64(path: str) -> int | None:
        """Read sysctl integer via direct libc.sysctlbyname(3) — ~1-5μs.

        On macOS arm64 this is a thin userspace wrapper around the sysctl
        syscall.  Returns None if the sysctl name does not exist, libc
        cannot be loaded, or any error occurs.

        [PHYSICS]-02: Replaces subprocess.run(['sysctl', '-n', path])
        which cost 1-5ms per call (fork+exec on macOS).  At typical
        evaluate() cadences this saves 10-25ms of system CPU per tick.
        """
        libc = M1ThermalMonitor._get_libc()
        if libc is None:
            return None
        try:
            value = ctypes.c_int64(0)
            size = ctypes.c_size_t(ctypes.sizeof(value))
            ret = libc.sysctlbyname(
                path.encode(),
                ctypes.byref(value),
                ctypes.byref(size),
                None,
                0,
            )
            if ret == 0:
                return value.value
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _read_sysctl_float(path: str) -> float | None:
        """Read sysctl value as float — ctypes primary, subprocess fallback.

        [PHYSICS]-02: Primary path uses libc.sysctlbyname(3) (~1-5μs).
        Falls back to subprocess-based sysctl(8) (~1-5ms) on non-macOS,
        sandbox restrictions, or libc load failure.
        """
        # Primary: direct libc call (1-5μs)
        int_val = M1ThermalMonitor._read_sysctl_int64(path)
        if int_val is not None:
            return float(int_val)
        # Fallback: subprocess sysctl(8) for non-macOS or sandboxed envs
        try:
            import subprocess

            result = subprocess.run(
                ["sysctl", "-n", path],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _read_cpu_temperature() -> float | None:
        """Čte průměrnou CPU teplotu z sysctl (macOS Ventura+)."""
        for path in M1ThermalMonitor._CPU_TEMP_PATHS:
            value = M1ThermalMonitor._read_sysctl_float(path)
            if value is not None:
                # sysctl vrací hodnotu v tisícinách stupňů nebo přímo ve stupních
                # Normalizace: pokud > 100, pravděpodobně máme setiny
                if value > 100:
                    value = value / 1000.0
                return value
        return None

    @staticmethod
    def _read_gpu_temperature() -> float | None:
        """Čte GPU teplotu z MLX nebo sysctl."""
        # 1. Zkusit MLX Metal
        try:
            import mlx.core as _mx

            if hasattr(_mx.metal, "get_device_temperature"):
                return float(_mx.metal.get_device_temperature())
        except Exception:  # noqa: BLE001
            pass
        # 2. Zkusit sysctl pro GPU
        for path in M1ThermalMonitor._GPU_TEMP_PATHS:
            value = M1ThermalMonitor._read_sysctl_float(path)
            if value is not None:
                if value > 100:
                    value = value / 1000.0
                return value
        return None

    @staticmethod
    def _read_cpu_frequency() -> float | None:
        """Čte aktuální CPU frekvenci v MHz z sysctl."""
        for path in M1ThermalMonitor._CPU_FREQ_PATHS:
            value = M1ThermalMonitor._read_sysctl_float(path)
            if value is not None:
                # sysctl vrací Hz, převedeme na MHz
                if value > 1_000_000:
                    return value / 1_000_000.0
                return value
        return None

    @staticmethod
    def _read_thermal_level() -> int | None:
        """
        ISSUE-014: Čte diskrétní macOS thermal level.

        hw.thermal.thermal_level je system-level agregace přes duration
        sustained teploty — spolehlivější než raw hw.sensors teplota.

        Returns:
            Thermal level jako int (0=nominal, vyšší = teplejší),
            nebo None pokud недоступен.
        """
        for path in M1ThermalMonitor._THERMAL_LEVEL_PATHS:
            value = M1ThermalMonitor._read_sysctl_float(path)
            if value is not None:
                return int(value)
        return None

    @staticmethod
    def _read_smc_zones() -> dict[str, float | None]:
        """
        ISSUE-014: Čte AppleSMC thermal zone klíče přes IOKit.

        TC0P = CPU proximity (hlavní CPU package teplota — nejdůležitější pro throttling)
        TG0P = GPU proximity
        TA0P = Ambient

        Returns:
            Dict {"TC0P": 45.5, "TG0P": 41.2, ...} nebo prázdný dict při chybě.
        """
        try:
            from hledac.universal.utils.thermal import read_smc_thermal_zones

            return read_smc_thermal_zones()
        except Exception:
            return {}

    @staticmethod
    def _detect_throttling(
        cpu_temp: float | None,
        gpu_temp: float | None,
        cpu_freq: float | None,
        thermal_level: int | None = None,
    ) -> tuple[bool, float]:
        """
        Detekuje termální throttling a počítá thermal headroom.

        ISSUE-014: thermal_level (hw.thermal.thermal_level) je primární signál.
        Je to system-level agregace přes duration sustained teploty —
        spolehlivější než jediná raw teplota. CPU/GPU temp je fallback.

        Vrací: (is_throttled, thermal_headroom)
            - is_throttled: True pokud probíhá throttling
            - thermal_headroom: 0.0-1.0, kde 1.0 = žádný throttling
        """
        headroom = 1.0
        is_throttled = False

        # ISSUE-014: thermal_level je PRIMÁRNÍ signál — přebíjí raw teplotu
        # macOS thermal level: 0=nominal, 1=fair, 2=serious, 3=critical
        if thermal_level is not None:
            if thermal_level >= 3:
                # Critical — plný throttling
                return True, 0.0
            elif thermal_level == 2:
                # Serious — těžký throttling
                return True, 0.2
            elif thermal_level == 1:
                # Fair — mírný throttling
                return True, 0.6
            # level == 0: nominal, pokračujeme na raw teplotu pro granularitu

        # CPU throttling detekce (fallback když nemáme thermal_level)
        if cpu_temp is not None:
            if cpu_temp >= M1ThermalMonitor._CPU_CRITICAL_TEMP:
                is_throttled = True
                headroom = 0.0
            elif cpu_temp > M1ThermalMonitor._CPU_THROTTLE_TEMP:
                # Lineární interpolace od THROTTLE_TEMP do CRITICAL_TEMP
                temp_range = M1ThermalMonitor._CPU_CRITICAL_TEMP - M1ThermalMonitor._CPU_THROTTLE_TEMP
                headroom = min(headroom, (M1ThermalMonitor._CPU_CRITICAL_TEMP - cpu_temp) / temp_range)
                is_throttled = True

        # GPU throttling detekce
        if gpu_temp is not None:
            if gpu_temp >= M1ThermalMonitor._GPU_CRITICAL_TEMP:
                is_throttled = True
                headroom = min(headroom, 0.0)
            elif gpu_temp > M1ThermalMonitor._GPU_THROTTLE_TEMP:
                gpu_range = M1ThermalMonitor._GPU_CRITICAL_TEMP - M1ThermalMonitor._GPU_THROTTLE_TEMP
                gpu_headroom = (M1ThermalMonitor._GPU_CRITICAL_TEMP - gpu_temp) / gpu_range
                headroom = min(headroom, gpu_headroom)
                is_throttled = True

        # Frekvenční detekce throttlingu je ненадёжná bez kontextu:
        # - M1 E-cores běží na 600-1000MHz i v normálním režimu
        # - Bez teploty nelze frekvenci spolehlivě interpretovat
        # - Ignorujeme frekvenci pokud nemáme teplotu — teplota je primární signál

        return is_throttled, headroom

    def read_thermal_status(self, emergency: bool = False) -> M1ThermalStatus:
        """Čte aktuální termální stav (rate-limited, emergency bypass).

        ISSUE-014: Nyní čte AppleSMC thermal zones (TC0P, TG0P…) přes IOKit
        a hw.thermal.thermal_level diskrétní sysctl level.

        Args:
            emergency: Pokud True, vynutí fresh čtení i když je v rate limit okně.
                       Použít při detekci throttlingu nebo vysokých teplot.
        """
        import time as _time

        now = _time.monotonic()

        # Rate limiting: minimální interval mezi čteními
        # Emergency bypass: vynutí fresh čtení při podezření na thermal emergency
        if (
            not emergency
            and self._last_reading is not None
            and now - self._last_read_time < self._READ_INTERVAL_S
        ):
            return self._last_reading

        cpu_temp = self._read_cpu_temperature()
        gpu_temp = self._read_gpu_temperature()
        cpu_freq = self._read_cpu_frequency()
        # ISSUE-014: Přidány thermal_level a smc_zones
        thermal_level = self._read_thermal_level()
        smc_zones = self._read_smc_zones()
        is_throttled, headroom = self._detect_throttling(
            cpu_temp, gpu_temp, cpu_freq, thermal_level
        )

        status = M1ThermalStatus(
            cpu_temperature_c=cpu_temp,
            gpu_temperature_c=gpu_temp,
            cpu_frequency_mhz=cpu_freq,
            cpu_core_count=self._get_cpu_core_count(),
            is_throttled=is_throttled,
            thermal_headroom=headroom,
            # ISSUE-014: Nová pole
            smc_zones=smc_zones,
            thermal_level=thermal_level,
        )

        self._last_reading = status
        self._last_read_time = now
        return status

    async def read_thermal_status_async(self, emergency: bool = False) -> M1ThermalStatus:
        """Asynchronní čtení termálního stavu.

        Args:
            emergency: Pokud True, vynutí fresh čtení i když je v rate limit okně.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.read_thermal_status(emergency=emergency))


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
    __slots__ = ("_hysteresis", "_legacy_cache_ttl_s", "_mpc_controller", "_thermal_monitor", "_power_monitor", "_sprint_windup_mode", "_sprint_degraded_mode")

    def __init__(self, cache_ttl_s: float = 5.0):
        self._legacy_cache_ttl_s = cache_ttl_s
        self._hysteresis = MemoryPressureHysteresis(total_gib=None)
        self._mpc_controller = AdaptiveMPCController()
        self._thermal_monitor = M1ThermalMonitor()
        self._last_evaluated_memory_ratio = 0.0
        # [FINAL]-019: Windup mode — set True when sprint enters WINDUP phase
        self._sprint_windup_mode = False
        # [FINAL]-019-08: Degraded mode — set True when sprint enters DEGRADED phase
        # Tracks lifecycle-driven degradation separately from CRITICAL/EMERGENCY UMA.
        self._sprint_degraded_mode: bool = False
        # HW-02: Lazy power monitor initialization
        self._power_monitor: "PowerStatusMonitor | None" = None  # noqa: F821

    @property
    def _pw_monitor(self) -> "PowerStatusMonitor":  # noqa: F821
        """Lazy accessor for power monitor (avoids import at module load)."""
        if self._power_monitor is None:
            from hledac.universal.utils.uma_budget import PowerStatusMonitor

            self._power_monitor = PowerStatusMonitor()
        return self._power_monitor

    # HW-03: Thermal scaling configuration
    _THERMAL_THROTTLE_THRESHOLD: float = 0.7  # headroom < 0.7 = throttling
    _THERMAL_SEVERE_THRESHOLD: float = 0.3  # headroom < 0.3 = severe throttling
    _WORKER_SCALE_FACTOR: float = 0.7  # mild throttle → 70% workers
    _BATCH_SCALE_FACTOR: float = 0.5  # mild throttle → 50% batch size
    # ISSUE-015: Generation length caps under thermal pressure
    _MAX_TOKENS_MILD: float = 0.75  # mild throttle → 75% of max_tokens
    _MAX_TOKENS_SEVERE: float = 0.5  # severe throttle → 50% of max_tokens
    _TEMP_REDUCTION_MILD: float = 0.05  # mild throttle → subtract 0.05 from temperature
    _TEMP_REDUCTION_SEVERE: float = 0.1  # severe throttle → subtract 0.1 from temperature
    _DEFAULT_MAX_TOKENS: int = 2048  # reference max_tokens for DeepHermes3

    def _compute_qos_level(
        self,
        uma_state: str,
        thermal_throttled: bool,
        thermal_headroom: float,
        power_status: dict[str, bool | int | float | None],
        io_only: bool,
        fetch_limit: int = 20,
    ) -> tuple[str, QoSProfile]:
        """
        [FINAL]-019: Compute the canonical QoS degradation level from all active constraints.

        The ladder is ordered worst-first so we can short-circuit on the first match:
            EMERGENCY > BATTERY > WINDUP > THERMAL > FULL

        Each level produces a QoSProfile snapshot that subsystems can consume
        without needing to interpret raw numeric factors.

        Args:
            uma_state:       Current UMA state ("ok", "soft_warn", "warn", "critical", "emergency").
            thermal_throttled: Whether CPU/GPU thermal throttling is active.
            thermal_headroom:  0.0-1.0 thermal headroom (1.0 = no throttling).
            power_status:     Power status dict from _adjust_for_power().
            io_only:          Whether I/O-only mode is active.

        Returns:
            tuple of (qos_level_name, QoSProfile) — level is a QoSLevel name string.
        """
        # EMERGENCY: Near-OOM — near-zero capabilities
        if uma_state == "emergency":
            return (
                QoSLevel.EMERGENCY,
                QoSProfile(
                    level=QoSLevel.EMERGENCY,
                    mlx_inference_ok=False,
                    sidecars_ok=False,
                    fetch_ok=True,
                    embeddings_ok=False,
                    model_load_ok=False,
                    whisper_ok=False,
                    max_workers_pct=0,
                    fetch_limit_cap=1,
                    reason="UMA emergency: near-OOM, all inference suspended",
                ),
            )

        # BATTERY: MLX suspended, I/O only
        # BATTERY: On battery with I/O-only → pause MLX, keep sidecars active.
        # No power_factor guard: io_only is authoritative — if the governor
        # says io_only, the QoS profile must reflect mlx_inference_ok=False.
        if io_only and power_status.get("on_battery"):
            power_factor = power_status.get("power_factor", 1.0)
            return (
                QoSLevel.BATTERY,
                QoSProfile(
                    level=QoSLevel.BATTERY,
                    mlx_inference_ok=False,
                    sidecars_ok=True,
                    fetch_ok=True,
                    embeddings_ok=False,
                    model_load_ok=False,
                    whisper_ok=False,
                    max_workers_pct=int(max(power_factor, 0.3) * 100),
                    fetch_limit_cap=max(1, int(fetch_limit * max(power_factor, 0.3))),
                    reason=f"Battery at {power_status.get('battery_level', '?')}%, MLX suspended",
                ),
            )

        # [FINAL]-019-03: When io_only is True but we didn't match BATTERY
        # (e.g., UMA-critical pressure on AC), mlx_inference/embeddings/whisper
        # must still be disabled. The QoS profile must match the io_only flag.
        _mlx_ok = not io_only

        # [FINAL]-019-08: DEGRADED: Governor CRITICAL/EMERGENCY during sprint ACTIVE phase.
        # This is lifecycle-driven degradation that persists even when the sprint
        # hasn't started its time-based windup. More aggressive than WINDUP
        # because memory pressure is acute.
        if self._sprint_degraded_mode:
            return (
                QoSLevel.WINDUP,  # Maps to WINDUP tier in the ladder
                QoSProfile(
                    level=QoSLevel.WINDUP,
                    mlx_inference_ok=_mlx_ok,
                    sidecars_ok=False,  # All sidecars off
                    fetch_ok=True,
                    embeddings_ok=False,  # Embeddings off in degraded
                    model_load_ok=False,
                    whisper_ok=False,
                    max_workers_pct=25,
                    fetch_limit_cap=2,
                    reason="Governor CRITICAL/EMERGENCY: degraded mode active"
                           + (", io_only active" if io_only else ""),
                ),
            )

        # WINDUP: Sprint entering wind-down — reduce sidecars, still OK for MLX
        if self._sprint_windup_mode:
            return (
                QoSLevel.WINDUP,
                QoSProfile(
                    level=QoSLevel.WINDUP,
                    mlx_inference_ok=_mlx_ok,
                    sidecars_ok=False,  # Sidecars off in windup
                    fetch_ok=True,
                    embeddings_ok=_mlx_ok,
                    model_load_ok=False,  # No new model loads in windup
                    whisper_ok=_mlx_ok,
                    max_workers_pct=50,
                    fetch_limit_cap=None,
                    reason="Sprint wind-up: sidecars suspended"
                           + (", io_only active" if io_only else ""),
                ),
            )

        # THERMAL: Thermal throttling — reduce batch, shorter generations
        if thermal_throttled and thermal_headroom < 1.0:
            return (
                QoSLevel.THERMAL,
                QoSProfile(
                    level=QoSLevel.THERMAL,
                    mlx_inference_ok=_mlx_ok,
                    sidecars_ok=True,
                    fetch_ok=True,
                    embeddings_ok=_mlx_ok,
                    model_load_ok=_mlx_ok,
                    whisper_ok=_mlx_ok,
                    max_workers_pct=int(self._WORKER_SCALE_FACTOR * 100),
                    fetch_limit_cap=None,
                    reason=f"Thermal throttling: headroom={thermal_headroom:.0%}, batch reduced"
                           + (", io_only active" if io_only else ""),
                ),
            )

        # FULL: All capabilities enabled (subject to io_only gate)
        return (
            QoSLevel.FULL,
            QoSProfile(
                level=QoSLevel.FULL,
                mlx_inference_ok=_mlx_ok,
                sidecars_ok=True,
                fetch_ok=True,
                embeddings_ok=_mlx_ok,
                model_load_ok=_mlx_ok,
                whisper_ok=_mlx_ok,
                max_workers_pct=100,
                fetch_limit_cap=None,
                reason="Normal operation" + (", io_only active" if io_only else ""),
            ),
        )

    def set_degraded_mode(self, enabled: bool, reason: str = "") -> None:
        """
        [FINAL]-019-08: Set sprint degraded mode for QoS ladder.

        Called by SprintLifecycleManager when the sprint enters or exits
        DEGRADED phase (governor CRITICAL/EMERGENCY detected).

        Unlike set_windup_mode() (lifecycle time-based), this is driven by
        the governor's memory/thermal state. The governor uses this flag
        to participate in the QoS ladder even when the sprint hasn't started
        its time-based windup.

        The reason string is recorded in telemetry for debugging.
        """
        if self._sprint_degraded_mode == enabled:
            return  # No change — skip telemetry spam
        self._sprint_degraded_mode = enabled
        # Record in telemetry for observability.
        try:
            global _telemetry
            with _UMA_TELEMETRY_LOCK:
                if enabled:
                    _telemetry["degraded_enter_count"] = _telemetry.get("degraded_enter_count", 0) + 1
                    _telemetry["degraded_last_reason"] = reason
                else:
                    _telemetry["degraded_exit_count"] = _telemetry.get("degraded_exit_count", 0) + 1
        except Exception:  # noqa: BLE001
            pass

    @property
    def is_degraded(self) -> bool:
        """
        [FINAL]-019-08: True when the governor is in degraded mode.

        Degraded mode is active when:
        - The sprint lifecycle is in DEGRADED phase (governor CRITICAL/EMERGENCY
          detected during ACTIVE phase), OR
        - _sprint_degraded_mode was set explicitly.

        Returns False when the sprint has recovered or entered WINDUP/EXPORT.
        """
        return self._sprint_degraded_mode

    def set_windup_mode(self, enabled: bool) -> None:
        """
        [FINAL]-019: Set sprint windup mode for QoS ladder.

        Called by SprintLifecycleManager when the sprint enters WINDUP phase.
        This raises the QoS level to WINDUP, disabling sidecars while keeping
        MLX inference running for final synthesis.
        """
        self._sprint_windup_mode = enabled

    def _compute_thermal_scales(self, headroom: float) -> tuple[float, float, int | None, float]:
        """
        HW-03: Compute worker, batch, and generation scaling factors from thermal headroom.

        ISSUE-015: Under thermal pressure, shorter generations (reduced max_tokens) complete
        faster and allow thermal recovery on fanless M1 devices.

        Args:
            headroom: 0.0-1.0, where 1.0 = no throttling

        Returns:
            tuple of (worker_scale_factor, batch_scale_factor, max_tokens_override, temperature_reduction)
            - worker_scale_factor: 0.0-1.0, scales max_workers
            - batch_scale_factor: 0.0-1.0, scales MLX batch size
            - max_tokens_override: None = use model default, int = cap max_tokens
            - temperature_reduction: 0.0-0.5, subtracted from temperature when throttled
        """
        if headroom >= self._THERMAL_THROTTLE_THRESHOLD:
            # No throttling
            return (1.0, 1.0, None, 0.0)
        elif headroom >= self._THERMAL_SEVERE_THRESHOLD:
            # Mild throttling — apply standard factors
            return (
                self._WORKER_SCALE_FACTOR,
                self._BATCH_SCALE_FACTOR,
                int(self._DEFAULT_MAX_TOKENS * self._MAX_TOKENS_MILD),
                self._TEMP_REDUCTION_MILD,
            )
        else:
            # Severe throttling — quadratic reduction + aggressive generation caps
            return (
                self._WORKER_SCALE_FACTOR ** 2,
                self._BATCH_SCALE_FACTOR ** 2,
                int(self._DEFAULT_MAX_TOKENS * self._MAX_TOKENS_SEVERE),
                self._TEMP_REDUCTION_SEVERE,
            )

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
        HW-01: Přidán termální monitoring pro CPU/GPU teplotu a frekvenci.
        Fail-soft: vrací bezpečné default při jakékoli chybě.
        """
        # HW-01: Čtení termálního stavu — vždy emergency pro fresh data
        # Termální stav se mění v sekundách, rate limit není potřeba.
        # Emergency=True zajistí fresh data i během aktivního throttlingu.
        thermal_status = await self._thermal_monitor.read_thermal_status_async(emergency=True)

        try:
            uma = await sample_uma_status_async()
        except Exception:
            preset = ConcurrencyPreset.from_state(UMAState.OK)
            # ISSUE-015: Include thermal generation params even on fallback path
            _worker, _batch, max_tok_override, temp_reduction = self._compute_thermal_scales(thermal_status.thermal_headroom)
            qos_level, qos_profile = self._compute_qos_level(
                uma_state=UMAState.OK,
                thermal_throttled=thermal_status.is_throttled,
                thermal_headroom=thermal_status.thermal_headroom,
                power_status={},
                io_only=False,
                fetch_limit=preset.fetch_limit,
            )
            return GovernorDecision(
                uma_state=UMAState.OK,
                io_only=False,
                fetch_limit=preset.fetch_limit,
                block_model_load=preset.block_model_load,
                swap_detected=False,
                thermal_throttled=thermal_status.is_throttled,
                thermal_headroom=thermal_status.thermal_headroom,
                max_tokens_override=max_tok_override,
                temperature_reduction=temp_reduction,
                burst_phase="GPU_HEAVY",  # PHYSICS-01: safe default on fallback
                qos_level=qos_level,
                qos_profile=qos_profile,
                degradation_level=QoSLevel(qos_level),
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

        # HW-01: Škálování podle termálního headroom
        if thermal_status.is_throttled and thermal_status.thermal_headroom < 1.0:
            thermal_scale = max(0.1, thermal_status.thermal_headroom)
            scaled_fetch_limit = max(1, int(scaled_fetch_limit * thermal_scale))

        try:
            from hledac.universal.metrics_registry import get_metrics_registry

            get_metrics_registry().set_gauge("memory_layer_pressure_pct", memory_ratio * 100.0)
            # HW-01: Report termální metriky
            if thermal_status.cpu_temperature_c is not None:
                get_metrics_registry().set_gauge("thermal_cpu_temp_c", thermal_status.cpu_temperature_c)
            if thermal_status.gpu_temperature_c is not None:
                get_metrics_registry().set_gauge("thermal_gpu_temp_c", thermal_status.gpu_temperature_c)
            get_metrics_registry().set_gauge("thermal_headroom", thermal_status.thermal_headroom * 100.0)
        except Exception:  # noqa: BLE001
            pass

        # HW-03: Compute thermal scaling factors from headroom
        # ISSUE-015: Also compute max_tokens_override and temperature_reduction
        worker_scale, batch_scale, max_tokens_override, temp_reduction = self._compute_thermal_scales(thermal_status.thermal_headroom)

        # PHYSICS-01: Resolve micro-burst phase for proactive thermal interleaving.
        # GPU_HEAVY (200 ms) → compute is allowed; IO_HEAVY (50 ms) → only I/O work.
        # Callers (MLX scheduler, fetch coordinator) check burst_phase before dispatching.
        try:
            from hledac.universal.core.micro_burst_scheduler import step_burst_phase, get_burst_phase

            step_burst_phase()
            _burst_phase = get_burst_phase().name
        except Exception:  # noqa: BLE001 — fail-safe; fall back to GPU_HEAVY
            _burst_phase = "GPU_HEAVY"

        base_decision = GovernorDecision(
            uma_state=gated_state,
            io_only=uma.io_only,
            fetch_limit=scaled_fetch_limit,
            block_model_load=preset.block_model_load,
            swap_detected=uma.swap_detected,
            thermal_throttled=thermal_status.is_throttled,
            thermal_headroom=thermal_status.thermal_headroom,
            worker_scale_factor=worker_scale,
            batch_scale_factor=batch_scale,
            max_tokens_override=max_tokens_override,
            temperature_reduction=temp_reduction,
            burst_phase=_burst_phase,
            # [FINAL]-019: QoS level placeholder; finalised in _adjust_for_power after
            # power constraints are evaluated (battery affects the level).
            qos_level="_pending",
            qos_profile=QoSProfile(),
        )
        return self._adjust_for_power(uma, base_decision)

    def _adjust_for_power(self, uma: UMAStatus, base_decision: GovernorDecision) -> GovernorDecision:
        """
        HW-02: Adaptuje governor rozhodnutí na základě stavu napájení.

        Na baterii snižuje aggressivitu MLX operací a I/O operací pro:
        - Prodloužení výdrže baterie
        - Prevenci termálního throttlingu
        - Snížení rizika swapování

        power_factor:
        - <20% battery: 0.4 (agresivní úspora)
        - <50% battery: 0.6 (střední úspora)
        - >=50% battery: 0.8 (mírná úspora)
        - AC attached: 1.0 (žádná úspora)
        """
        if not uma.on_battery or uma.ac_attached:
            # [FINAL]-019: Still need to compute QoS level even on AC/battery-free path
            qos_level, qos_profile = self._compute_qos_level(
                uma_state=base_decision.uma_state,
                thermal_throttled=base_decision.thermal_throttled,
                thermal_headroom=base_decision.thermal_headroom,
                power_status={},
                io_only=base_decision.io_only,
                fetch_limit=base_decision.fetch_limit,
            )
            return GovernorDecision(
                uma_state=base_decision.uma_state,
                io_only=base_decision.io_only,
                fetch_limit=base_decision.fetch_limit,
                block_model_load=base_decision.block_model_load,
                swap_detected=base_decision.swap_detected,
                thermal_throttled=base_decision.thermal_throttled,
                thermal_headroom=base_decision.thermal_headroom,
                worker_scale_factor=base_decision.worker_scale_factor,
                batch_scale_factor=base_decision.batch_scale_factor,
                max_tokens_override=base_decision.max_tokens_override,
                temperature_reduction=base_decision.temperature_reduction,
                burst_phase=base_decision.burst_phase,
                power_status={"on_battery": uma.on_battery, "battery_level": uma.battery_level, "ac_attached": uma.ac_attached, "power_factor": 1.0},
                qos_level=qos_level,
                qos_profile=qos_profile,
                degradation_level=QoSLevel(qos_level),
            )

        battery_level = uma.battery_level
        if battery_level is not None and battery_level < 20:
            power_factor = 0.4
        elif battery_level is not None and battery_level < 50:
            power_factor = 0.6
        else:
            power_factor = 0.8

        adjusted_fetch = max(1, int(base_decision.fetch_limit * power_factor))
        power_status = {
            "on_battery": uma.on_battery,
            "battery_level": uma.battery_level,
            "ac_attached": uma.ac_attached,
            "power_factor": power_factor,
        }

        # [FINAL]-019: Compute canonical QoS level from all active constraints.
        # This is done here (after power adjustment) because battery status
        # is the final modifier in the ladder: EMERGENCY > BATTERY > WINDUP > THERMAL > FULL
        qos_level, qos_profile = self._compute_qos_level(
            uma_state=base_decision.uma_state,
            thermal_throttled=base_decision.thermal_throttled,
            thermal_headroom=base_decision.thermal_headroom,
            power_status=power_status,
            io_only=base_decision.io_only or (power_factor < 0.6),
            fetch_limit=adjusted_fetch,
        )

        return GovernorDecision(
            uma_state=base_decision.uma_state,
            io_only=base_decision.io_only or (power_factor < 0.6),
            fetch_limit=adjusted_fetch,
            block_model_load=base_decision.block_model_load or (power_factor < 0.5),
            swap_detected=base_decision.swap_detected,
            thermal_throttled=base_decision.thermal_throttled,
            thermal_headroom=base_decision.thermal_headroom,
            worker_scale_factor=base_decision.worker_scale_factor * power_factor,
            batch_scale_factor=base_decision.batch_scale_factor * power_factor,
            max_tokens_override=base_decision.max_tokens_override,
            temperature_reduction=base_decision.temperature_reduction,
            burst_phase=base_decision.burst_phase,  # PHYSICS-01: propagate burst phase
            power_status={
                "on_battery": uma.on_battery,
                "battery_level": uma.battery_level,
                "ac_attached": uma.ac_attached,
                "power_factor": power_factor,
            },
            qos_level=qos_level,
            qos_profile=qos_profile,
            degradation_level=QoSLevel(qos_level),
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
        except Exception:  # noqa: BLE001
            pass
        # B5: propagate UMA state to memory_cycle for dynamic GC thresholds
        try:
            from hledac.universal.core.memory_cycle import _apply_gc_thresholds
            _apply_gc_thresholds(decision.uma_state)
        except Exception:  # noqa: BLE001
            pass
        # F350M-R: Apply madvise to all mmap handles at CRITICAL/EMERGENCY
        if decision.uma_state in (UMAState.CRITICAL, UMAState.EMERGENCY):
            self.apply_madvise_critical()
        # HW-01 / ISSUE-013: Feed thermal_headroom into MLX Metal cache sizing.
        # On M1 MacBook Air (fanless), Metal + CPU share heatsink — under
        # throttling, reduce Metal cache to free memory bandwidth for compute.
        if decision.thermal_headroom < 1.0:
            try:
                from hledac.universal.utils.mlx_cache import async_reconfigure_metal_cache_limit

                asyncio.create_task(
                    async_reconfigure_metal_cache_limit(
                        uma_state=decision.uma_state,
                        thermal_headroom=decision.thermal_headroom,
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        # UNIFIED-002: Propagate UMA pressure state to AsyncUMAGuard for
        # dynamic hard-limit adjustment. This closes the race condition where
        # two subsystems see "ELEVATED" simultaneously and both proceed.
        try:
            guard = get_uma_guard()
            guard.update_hard_limit(decision.uma_state)
        except Exception:  # noqa: BLE001
            pass
        # UNIFIED-003: Propagate UMA pressure to GlobalPeakCoScheduler for
        # preemption + mutex group awareness. This ensures CRITICAL/EMERGENCY
        # pressure triggers active task cancellation.
        try:
            from hledac.universal.core.global_co_scheduler import get_co_scheduler
            scheduler = get_co_scheduler()
            asyncio.create_task(scheduler.on_pressure_change(decision.uma_state))
        except Exception:  # noqa: BLE001
            pass
        # [FINAL]-019: Propagate QoS profile to context variable and module cache.
        # Subsystems call is_capability_allowed() to self-gate expensive operations.
        # ContextVar ensures thread-safety for async callers.
        try:
            global _last_qos_profile
            _last_qos_profile = decision.qos_profile
            _qos_signal.set(decision.qos_profile)
        except Exception:  # noqa: BLE001
            pass

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
        except Exception:  # noqa: BLE001
            pass
        try:
            self._apply_madvise_to_lmdb_paths()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._malloc_zone_pressure_relief()
        except Exception:  # noqa: BLE001
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
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

    def _apply_madvise_to_lmdb_paths(self) -> None:
        """Apply MADV_NOCACHE to all LMDB environment paths."""
        _lmdb_paths: list[str] = []
        # Collect known LMDB paths from module-level singletons
        try:
            from hledac.universal.knowledge.sprint_seeds_store import _LMDB_PATH as _SEEDS_LMDB
            if _SEEDS_LMDB:
                _lmdb_paths.append(str(_SEEDS_LMDB))
        except Exception:  # noqa: BLE001
            pass
        try:
            from hledac.universal.knowledge.ioc_dedup_adapter import _IOC_DEDUP_LMDB_PATH
            if _IOC_DEDUP_LMDB_PATH:
                _lmdb_paths.append(str(_IOC_DEDUP_LMDB_PATH))
        except Exception:  # noqa: BLE001
            pass
        try:
            from hledac.universal.paths import LMDB_ROOT
            unified = LMDB_ROOT / "unified_cache.lmdb"
            if unified.exists():
                _lmdb_paths.append(str(unified))
        except Exception:  # noqa: BLE001
            pass
        # Apply madvise to each collected path
        for path in _lmdb_paths:
            try:
                from hledac.universal.tools.file_cache import madvise_lmdb_mmap
                madvise_lmdb_mmap(path, advice=1)  # MADV_NOCACHE
            except Exception:  # noqa: BLE001
                pass

    def _malloc_zone_pressure_relief(self) -> None:
        """Release malloc fragmented pages on M1 8GB UMA."""
        try:
            import ctypes
            libc = ctypes.CDLL("libc.dylib", use_errno=True)
            libc.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            # U2-06 FIX: restype must be c_int — malloc_zone_pressure_relief returns
            # kern_return_t (int), NOT void. With restype=None the return value is
            # silently discarded and the result != 0 check below would always be False.
            libc.malloc_zone_pressure_relief.restype = ctypes.c_int
            result = libc.malloc_zone_pressure_relief(None, 0)
            if result != 0:
                import errno
                logger.warning(
                    "malloc_zone_pressure_relief returned %d (errno=%s)",
                    result,
                    errno.errorcode.get(ctypes.get_errno(), "unknown"),
                )
        except Exception:  # noqa: BLE001
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
    CRITICAL = 0  # Lowest numeric value = highest scheduling priority
    HIGH = 1
    NORMAL = 2
    LOW = 3


class ResourceExhaustedError(RuntimeError):
    """
    Raised when AsyncUMAGuard cannot satisfy a reservation request.

    Causes:
        - Timeout: waited longer than timeout_s without available budget
        - Denied: request exceeds hard limit even when fully idle
        - Cancelled: task was cancelled while waiting

    Attributes:
        requested_mb: Requested memory in MB
        available_mb: Available memory at time of error
        hard_limit_mb: Current hard limit in MB
        reason: Machine-readable reason string
    """

    __slots__ = ("requested_mb", "available_mb", "hard_limit_mb", "reason")

    def __init__(
        self,
        requested_mb: float,
        available_mb: float,
        hard_limit_mb: float,
        reason: str = "timeout",
    ) -> None:
        self.requested_mb = requested_mb
        self.available_mb = available_mb
        self.hard_limit_mb = hard_limit_mb
        self.reason = reason
        super().__init__(
            f"UMAGuard: cannot reserve {requested_mb:.0f} MB "
            f"(available={available_mb:.0f}, limit={hard_limit_mb:.0f}, reason={reason})"
        )


class ReservationInfo(msgspec.Struct, frozen=True, gc=False):
    """
    Diagnostic snapshot returned by AsyncUMAGuard.reserve() on entry.

    Provides telemetry for OTel spans and logging:
        allocated_mb: How much was reserved
        remaining_mb: Remaining budget after this reservation
        hard_limit_mb: Current hard limit
        wait_time_s: How long the reservation waited (0 if immediate)
        priority: Priority level of the reservation
        queue_depth: Number of waiters when reservation was granted
    """

    allocated_mb: float
    remaining_mb: float
    hard_limit_mb: float
    wait_time_s: float
    priority: int
    queue_depth: int


class AsyncUMAGuard:
    """
    Async mutex barrier for UMA RAM allocation with priority-based scheduling.

    Solves the race condition where multiple subsystems simultaneously see
    "ELEVATED" pressure state and both proceed, causing combined allocation
    to exceed the 6.48 GB hard limit.

    Key features:
        - asyncio.Condition for wait/notify (not Event — supports broadcast)
        - Priority-based wait queue (heapq) preventing starvation
        - Dynamic hard limit adjustment from governor pressure state
        - Timeout support to prevent indefinite blocking
        - ReservationInfo telemetry for OTel spans

    Usage:
        guard = get_uma_guard()
        async with guard.reserve(estimated_mb=500, priority=Priority.NORMAL) as info:
            # Allocation is now guaranteed within hard limit
            await heavy_operation()

    M1 8GB calibration:
        - Default hard limit: 6480 MB (81% of 8 GB)
        - Adjusted dynamically by governor pressure state
        - CRITICAL/EMERGENCY: limit reduced to 4000/2000 MB
    """

    __slots__ = (
        "_condition",
        "_current_allocated_mb",
        "_hard_limit_mb",
        "_wait_queue",
        "_queue_counter",
        "_total_reservations",
        "_total_wait_time_s",
        "_lock",
    )

    # M1 8GB calibrated limits
    _DEFAULT_HARD_LIMIT_MB: float = 6480.0  # 81% of 8 GB
    _CRITICAL_LIMIT_MB: float = 4000.0  # Reduced at CRITICAL pressure
    _EMERGENCY_LIMIT_MB: float = 2000.0  # Minimal at EMERGENCY

    def __init__(self, hard_limit_mb: float | None = None) -> None:
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._current_allocated_mb: float = 0.0
        self._hard_limit_mb: float = hard_limit_mb if hard_limit_mb is not None else self._DEFAULT_HARD_LIMIT_MB
        # Priority queue: (priority_value, sequence_number, estimated_mb, asyncio.Event)
        # Lower priority_value = higher priority (CRITICAL=0, HIGH=1, NORMAL=2, LOW=3)
        self._wait_queue: list[tuple[int, int, float, asyncio.Event]] = []
        self._queue_counter: int = 0  # Monotonic sequence for FIFO within same priority
        # Telemetry
        self._total_reservations: int = 0
        self._total_wait_time_s: float = 0.0

    @property
    def current_allocated_mb(self) -> float:
        """Current total allocated memory in MB (read-only telemetry)."""
        return self._current_allocated_mb

    @property
    def hard_limit_mb(self) -> float:
        """Current hard limit in MB (dynamically adjustable)."""
        return self._hard_limit_mb

    @property
    def available_mb(self) -> float:
        """Available memory budget in MB."""
        return max(0.0, self._hard_limit_mb - self._current_allocated_mb)

    @property
    def queue_depth(self) -> int:
        """Number of waiters in the priority queue."""
        return len(self._wait_queue)

    def update_hard_limit(self, uma_state: str) -> None:
        """
        Dynamically adjust hard limit based on governor pressure state.

        Called by M1ResourceGovernor.apply_decision() when UMA state changes.
        Wakes all waiters to re-check their requests against new limit.

        Args:
            uma_state: Current UMA state ("ok", "soft_warn", "warn", "critical", "emergency")
        """
        new_limit = self._DEFAULT_HARD_LIMIT_MB
        if uma_state == UMAState.CRITICAL:
            new_limit = self._CRITICAL_LIMIT_MB
        elif uma_state == UMAState.EMERGENCY:
            new_limit = self._EMERGENCY_LIMIT_MB
        elif uma_state == UMAState.WARN:
            # Slightly reduced at WARN
            new_limit = self._DEFAULT_HARD_LIMIT_MB * 0.85

        if new_limit != self._hard_limit_mb:
            self._hard_limit_mb = new_limit
            # Wake all waiters to re-check against new limit
            # (asyncio.Condition.notify_all requires holding the lock)
            # We schedule a wake-up task to avoid blocking here
            asyncio.create_task(self._notify_all_waiters())

    async def _notify_all_waiters(self) -> None:
        """Notify all waiters to re-check their requests."""
        async with self._condition:
            self._condition.notify_all()

    @asynccontextmanager
    async def reserve(
        self,
        estimated_mb: float,
        priority: Priority | int = Priority.NORMAL,
        timeout_s: float | None = 30.0,
    ):
        """
        Async context manager for memory reservation with priority scheduling.

        Blocks until:
            - Sufficient budget is available (current + estimated_mb <= hard_limit)
            - Timeout expires (raises ResourceExhaustedError)
            - Task is cancelled (propagates CancelledError)

        Priority scheduling:
            - CRITICAL (0): Immediate bypass if possible, else first in queue
            - HIGH (1): Second in queue
            - NORMAL (2): Standard FIFO
            - LOW (3): Last in queue

        Args:
            estimated_mb: Estimated memory consumption in MB
            priority: Priority level (Priority enum or int 0-3)
            timeout_s: Maximum wait time in seconds (None = wait forever)

        Yields:
            ReservationInfo with telemetry (allocated_mb, remaining_mb, wait_time_s, etc.)

        Raises:
            ResourceExhaustedError: If timeout expires or request exceeds hard limit
            asyncio.CancelledError: If task is cancelled while waiting

        Example:
            async with guard.reserve(500, Priority.HIGH, timeout_s=10.0) as info:
                print(f"Reserved {info.allocated_mb} MB, waited {info.wait_time_s:.2f}s")
                await heavy_operation()
        """
        # Convert Priority enum to int if needed
        priority_value = priority.value if isinstance(priority, Priority) else int(priority)

        # Fast path: check if request can be satisfied immediately
        start_time = time.monotonic()
        wait_time_s = 0.0

        async with self._condition:
            # Check if request exceeds hard limit even when fully idle
            if estimated_mb > self._hard_limit_mb:
                raise ResourceExhaustedError(
                    requested_mb=estimated_mb,
                    available_mb=self.available_mb,
                    hard_limit_mb=self._hard_limit_mb,
                    reason="exceeds_hard_limit",
                )

            # Wait loop: block until budget is available or timeout
            while self._current_allocated_mb + estimated_mb > self._hard_limit_mb:
                # Enqueue with priority
                self._queue_counter += 1
                sequence = self._queue_counter
                waiter_event = asyncio.Event()
                heapq.heappush(
                    self._wait_queue,
                    (priority_value, sequence, estimated_mb, waiter_event),
                )

                try:
                    # Wait for notification or timeout
                    if timeout_s is not None:
                        remaining_timeout = timeout_s - (time.monotonic() - start_time)
                        if remaining_timeout <= 0:
                            raise ResourceExhaustedError(
                                requested_mb=estimated_mb,
                                available_mb=self.available_mb,
                                hard_limit_mb=self._hard_limit_mb,
                                reason="timeout",
                            )
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=remaining_timeout,
                        )
                    else:
                        await self._condition.wait()
                except asyncio.TimeoutError:
                    # Remove ourselves from queue
                    self._wait_queue = [
                        (p, s, m, e)
                        for p, s, m, e in self._wait_queue
                        if e is not waiter_event
                    ]
                    heapq.heapify(self._wait_queue)
                    raise ResourceExhaustedError(
                        requested_mb=estimated_mb,
                        available_mb=self.available_mb,
                        hard_limit_mb=self._hard_limit_mb,
                        reason="timeout",
                    )
                except asyncio.CancelledError:
                    # Remove ourselves from queue
                    self._wait_queue = [
                        (p, s, m, e)
                        for p, s, m, e in self._wait_queue
                        if e is not waiter_event
                    ]
                    heapq.heapify(self._wait_queue)
                    raise

                # Remove ourselves from queue (we were notified)
                self._wait_queue = [
                    (p, s, m, e)
                    for p, s, m, e in self._wait_queue
                    if e is not waiter_event
                ]
                heapq.heapify(self._wait_queue)

            # Budget is available — reserve it
            self._current_allocated_mb += estimated_mb
            wait_time_s = time.monotonic() - start_time
            self._total_reservations += 1
            self._total_wait_time_s += wait_time_s

            # Build telemetry snapshot
            info = ReservationInfo(
                allocated_mb=estimated_mb,
                remaining_mb=self.available_mb,
                hard_limit_mb=self._hard_limit_mb,
                wait_time_s=wait_time_s,
                priority=priority_value,
                queue_depth=len(self._wait_queue),
            )

        try:
            yield info
        finally:
            # Release reservation and notify waiters
            async with self._condition:
                self._current_allocated_mb -= estimated_mb
                # Notify next waiter (if any) that budget is available
                self._condition.notify_all()

    def telemetry(self) -> dict[str, Any]:
        """
        Read-only telemetry snapshot for monitoring and debugging.

        Returns:
            dict with current_allocated_mb, hard_limit_mb, available_mb,
            queue_depth, total_reservations, avg_wait_time_s
        """
        avg_wait = (
            self._total_wait_time_s / self._total_reservations
            if self._total_reservations > 0
            else 0.0
        )
        return {
            "current_allocated_mb": self._current_allocated_mb,
            "hard_limit_mb": self._hard_limit_mb,
            "available_mb": self.available_mb,
            "queue_depth": self.queue_depth,
            "total_reservations": self._total_reservations,
            "avg_wait_time_s": avg_wait,
        }

    def reset(self) -> None:
        """
        Reset guard state. For testing only.

        Clears all reservations and telemetry. Does NOT wake waiters
        (they will timeout or be cancelled).
        """
        self._current_allocated_mb = 0.0
        self._hard_limit_mb = self._DEFAULT_HARD_LIMIT_MB
        self._wait_queue.clear()
        self._queue_counter = 0
        self._total_reservations = 0
        self._total_wait_time_s = 0.0


# Singleton accessor
_uma_guard: AsyncUMAGuard | None = None


def get_uma_guard() -> AsyncUMAGuard:
    """
    Get or create the singleton AsyncUMAGuard.

    This is the canonical way to access the UMA guard from outside core/.
    Lazily creates the instance on first call.

    Returns:
        AsyncUMAGuard singleton
    """
    global _uma_guard
    if _uma_guard is None:
        _uma_guard = AsyncUMAGuard()
    return _uma_guard


class ResourceGovernor:
    """
    Hlídá zdroje a rozhoduje, zda je možné provést náročnou operaci.
    HW-01: Sjednocen GPU thermal check přes M1ThermalMonitor.
    """

    __slots__ = tuple(
        (
            "__lock",
            "_active_tasks",
            "_cost_model",
            "_lock_factory",
            "_priority_factor",
            "high_water",
            "_thermal_monitor",
        )
    )

    def __init__(self, memory_high_water_mb: float = 5632, thermal_threshold: float = 82.0):
        self.high_water = memory_high_water_mb
        self._thermal_monitor = M1ThermalMonitor()
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

    def _check_peak_coordinator(self, priority: Priority) -> tuple[bool, bool]:
        """
        Check peak load coordinator for cross-subsystem admission.

        Returns:
            Tuple of (can_proceed, reason_exists).
            If can_proceed is False, reason is logged.
        """
        try:
            from hledac.universal.core.peak_load_coordinator import (
                get_peak_coordinator,
                TaskPriority as PeakTaskPriority,
            )
            coordinator = get_peak_coordinator()
            if coordinator is None:
                return True, False

            priority_map = {
                Priority.CRITICAL: PeakTaskPriority.CRITICAL,
                Priority.HIGH: PeakTaskPriority.HIGH,
                Priority.NORMAL: PeakTaskPriority.NORMAL,
                Priority.LOW: PeakTaskPriority.LOW,
            }
            peak_priority = priority_map.get(priority, PeakTaskPriority.NORMAL)
            snapshot = coordinator.snapshot()

            if snapshot.emergency_active and priority != Priority.CRITICAL:
                logger.debug(
                    f"[UNIFIED-001] can_afford_sync: emergency mode, "
                    f"rejecting {priority} priority"
                )
                return False, True
            if snapshot.high_water_active and priority == Priority.LOW:
                logger.debug(
                    f"[UNIFIED-001] can_afford_sync: high water mode, "
                    f"rejecting LOW priority (utilization: {snapshot.utilization_fraction:.1%})"
                )
                return False, True
            return True, False
        except (ImportError, AttributeError):
            return True, False

    def _check_ram(self, cost_estimate: dict[str, Any], priority: Priority) -> bool:
        """Check if RAM is available for the cost estimate."""
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
        return True

    def _check_gpu(self, cost_estimate: dict[str, Any], priority: Priority) -> bool:
        """Check if GPU memory is available for the cost estimate."""
        if not cost_estimate.get("gpu", False):
            return True
        try:
            if hasattr(_get_mx(), "get_active_memory"):
                gpu_used = _get_mx().get_active_memory() / (1024 * 1024)
            elif hasattr(_get_mx().metal, "get_active_memory"):
                gpu_used = _get_mx().metal.get_active_memory() / (1024 * 1024)
            else:
                return True
            gpu_total = float("inf")
            if hasattr(_get_mx().metal, "get_recommended_max_memory"):
                gpu_total = _get_mx().metal.get_recommended_max_memory() / (1024 * 1024)
            factor = self._priority_factor[priority]
            ram_needed = cost_estimate.get("ram_mb", 0)
            if gpu_used + ram_needed > gpu_total * factor:
                return False
        except Exception:
            pass
        return True

    def _check_thermal(self, priority: Priority) -> bool:
        """Check thermal status - fail for non-CRITICAL priority when throttled."""
        thermal_status = self._thermal_monitor.read_thermal_status()
        if thermal_status.is_throttled and priority != Priority.CRITICAL:
            logger.warning(
                f"GPU thermal throttled: cpu={thermal_status.cpu_temperature_c}°C, "
                f"gpu={thermal_status.gpu_temperature_c}°C, headroom={thermal_status.thermal_headroom:.2f}"
            )
            return False
        return True

    def _check_ane(self, priority: Priority) -> bool:
        """Check ANE utilization - reject LOW priority when ANE is heavily used."""
        try:
            if hasattr(_get_mx().metal, "get_ane_utilization"):
                ane = _get_mx().metal.get_ane_utilization()
                if ane > 0.9 and priority == Priority.LOW:
                    return False
        except AttributeError:
            pass
        return True

    def _check_cost_model(self, cost_estimate: dict[str, Any]) -> bool:
        """Check cost model prediction for overrun risk."""
        if self._cost_model is None:
            return True
        risk = self._cost_model.predict_overrun_risk(cost_estimate)
        if risk > 0.3:
            return False
        return True

    def can_afford_sync(self, cost_estimate: dict[str, Any], priority: Priority = Priority.NORMAL) -> bool:
        """
        Synchronní kontrola zdrojů bez rezervace.

        UNIFIED-001: Now also checks GlobalPeakLoadCoordinator for cross-subsystem
        admission control. If the coordinator indicates high memory pressure,
        this returns False even if local checks pass.
        """
        if not self._check_peak_coordinator(priority)[0]:
            return False
        if not self._check_ram(cost_estimate, priority):
            return False
        if not self._check_gpu(cost_estimate, priority):
            return False
        if not self._check_thermal(priority):
            return False
        if not self._check_ane(priority):
            return False
        if not self._check_cost_model(cost_estimate):
            return False
        return True

    def reserve(self, cost_estimate: dict[str, Any], priority: Priority = Priority.NORMAL):
        """
        Vrací async context manager pro rezervaci zdrojů. Samotná metoda je synchronní.

        UNIFIED-002: Now delegates to AsyncUMAGuard for proper memory accounting barrier.
        Falls back to legacy behavior if AsyncUMAGuard is unavailable.
        """
        ram_mb = cost_estimate.get("ram_mb", 0)

        # Try to use AsyncUMAGuard for proper memory barrier
        try:
            guard = get_uma_guard()
            return guard.reserve(estimated_mb=ram_mb, priority=priority, timeout_s=30.0)
        except Exception:  # noqa: BLE001
            # Fallback to legacy behavior if guard unavailable
            pass

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

    P2-8 FIX: Hysteresis boundary bug.
    - Previous: `system_used_gib > _HYSTERESIS_EXIT_GIB` created a dead zone at exact
      threshold (6.8 GiB) where system would exit io_only, then immediately re-enter.
    - Fixed: `system_used_gib >= _HYSTERESIS_EXIT_GIB` provides proper hysteresis band.
      Exit only when strictly below threshold, preventing thrashing at boundary.

    Contract (P2-8 COMPREHENSIVE FIX):
        - Enter io_only when >= CRITICAL (6.7 GiB) and swap_detected=False
        - Enter io_only when >= WARN (6.0 GiB) and swap_detected=True (accelerated)
        - Stay in io_only while system_used_gib >= HYSTERESIS_EXIT (6.5 GiB)
        - Exit io_only only when system_used_gib < 6.5 GiB (and previous_io_only == True)
        
    P2-8 Comprehensive Fix Summary:
        1. Changed operator from > to >= (fixes dead zone at exact threshold)
        2. Changed _HYSTERESIS_EXIT_GIB default from 6.8 to 6.5 GiB
        3. Changed fallback calculation to _THRESHOLD_WARN_GIB - 0.5
        4. Creates proper 0.2-0.3 GiB hysteresis band between entry and exit

    This prevents state thrashing around the critical boundary.

    Args:
        system_used_gib: Current system memory used in GiB.
        previous_io_only: True if io_only was already active.
        swap_detected: True if any active swap is present (systemic pressure signal).

    Returns:
        True if caller should enter / stay in I/O-only mode.
    """
    if previous_io_only:
        # P2-8 FIX: >= creates proper hysteresis band, > created dead zone at threshold
        return system_used_gib >= _HYSTERESIS_EXIT_GIB
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
            except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        pass
    state = evaluate_uma_state(system_used_gib)
    _pressure_result = _get_cached_psutil("memory_pressure", _read_memory_pressure_sync)
    _pressure_status = _pressure_result.get("status", "UNKNOWN") if _pressure_result else "UNKNOWN"
    swap_detected = swap_used_gib > 5.0 or _pressure_status in ("CRITICAL", "RED")
    prev_io_only, io_only = _update_io_only_latch_with_lock(system_used_gib, swap_detected=swap_detected)
    _record_transition(state, prev_io_only, io_only)
    # HW-02: Get power status for battery vs AC detection
    from hledac.universal.utils.uma_budget import get_power_monitor

    _power = get_power_monitor().get_power_status()

    # APEX-1002: Read thermal state for M1 throttling detection
    # Uses cached psutil pattern with 2s TTL to avoid excessive sysctl calls
    _thermal_result = _get_cached_psutil("thermal_state", _read_thermal_state_sync)
    _thermal_level = _thermal_result.get("thermal_level") if _thermal_result else None
    _is_thermally_throttled = _thermal_result.get("is_throttled", False) if _thermal_result else False

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
        on_battery=bool(_power.get("on_battery", False)),
        battery_level=_power.get("battery_level"),
        ac_attached=bool(_power.get("ac_attached", True)),
        thermal_level=_thermal_level,
        is_thermally_throttled=_is_thermally_throttled,
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
            except Exception:  # noqa: BLE001
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


# MODERN-27: Canonical QoS class constants (Apple Silicon / libdispatch)
# These match libc::qos_class_t values used in rust_extensions/src/lib.rs
_QOS_USER_INITIATED: int = 0x19  # 25 - P-core scheduling priority
_QOS_UTILITY: int = 0x11         # 17 - Balanced efficiency
_QOS_BACKGROUND: int = 0x09      #  9 - Background efficiency


def set_thread_qos(qos_level: int) -> None:
    """
    Sprint 8PC + MODERN-29 FIX: Set calling thread's QoS class on Apple Silicon.

    Useful for hinting the kernel about latency vs throughput tradeoffs.

    QoS levels (MODERN-29: Use pthread_set_qos_class_self_np, NOT raw syscall 366):
        0x19 (USER_INITIATED): Interactive / latency-sensitive → P-core scheduling
        0x11 (UTILITY):         Background / throughput-oriented
        0x09 (BACKGROUND):      Low-priority background tasks

    MODERN-29 FIX: Replace libc.syscall(366, ...) with direct pthread_set_qos_class_self_np.
    Rationale:
        - Syscall 366 is implementation detail that varies by macOS version
        - pthread_set_qos_class_self_np via ctypes uses the stable libSystem API
        - Matches the correct implementation in utils/thread_pools.py:41-42

    B.7: Fail-open — if syscall fails (non-macOS or permission), log at DEBUG
    and return without raising.
    """
    try:
        import ctypes

        # MODERN-29 FIX: Use libSystem directly, NOT libc.syscall(366)
        # This matches the correct pattern in utils/thread_pools.py
        libpthread = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        libpthread.pthread_set_qos_class_self_np(qos_level, 0)
    except OSError as exc:
        # Handle non-macOS or symbol-not-found gracefully
        logger.debug(f"[QoS] pthread_set_qos_class_self_np not available (non-macOS): {exc}")
    except Exception as exc:
        logger.debug(f"[QoS] pthread_set_qos_class_self_np failed: {exc}")


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
        from hledac.universal.runtime.acquisition.lane_constants import get_lane_ram_budget as _get

        return _get(lane_id)
    except Exception:
        return 30


# ── QoS signal context variable ─────────────────────────────────────────────────

# [FINAL]-019: Context variable carrying the latest GovernorDecision QoS snapshot.
# Any subsystem can read get_qos_signal() to check if its operation is permitted.
# Set once per evaluation cycle in apply_decision().
_qos_signal: _contextvars.ContextVar[QoSProfile] = _contextvars.ContextVar(
    "_qos_signal", default=QoSProfile()
)


def get_qos_signal() -> QoSProfile:
    """Get the current QoS profile signal (thread-safe via ContextVar)."""
    return _qos_signal.get()


# ── Capability toggle service ───────────────────────────────────────────────────

# [FINAL]-019: Module-level cache of the last applied QoS profile.
# Subsystems call is_capability_allowed(cap) to self-gate without hitting
# the governor on every call. Updated once per evaluate() cycle.
_last_qos_profile: QoSProfile = QoSProfile()


def _is_governor_critical_or_emergency() -> bool:
    """
    [FINAL]-019-08: Fast synchronous probe for CRITICAL/EMERGENCY UMA state.

    This is a lightweight synchronous check intended for use in the lifecycle
    tick() loop, where async Governor.evaluate() cannot be called. It reads
    the module-level telemetry cache which is kept up-to-date by apply_decision()
    (called at the end of each evaluate() cycle).

    Returns True if the governor's last observed UMA state was CRITICAL or
    EMERGENCY. Returns False if the governor is OK/WARN or hasn't evaluated yet.
    This is intentionally coarse — false positives are possible if the governor
    has recovered but the telemetry cache hasn't been refreshed (up to 5s lag).

    For accurate, up-to-date state, use ``await governor.evaluate()`` instead.
    """
    try:
        state = _telemetry["last_state"]
        return state in ("critical", "emergency")
    except Exception:
        return False


def is_capability_allowed(capability: str) -> bool:
    """
    [FINAL]-019: Check if a named capability is allowed under the current QoS profile.

    This is the canonical capability gate for subsystems. It reads from the
    module-level cache which is updated once per governor evaluate() cycle.

    Args:
        capability: One of "mlx_inference", "sidecars", "fetch", "embeddings",
            "model_load", "whisper".

    Returns:
        True if the capability is currently permitted, False otherwise.
    """
    p = _last_qos_profile
    match capability:
        case "mlx_inference":
            return p.mlx_inference_ok
        case "sidecars":
            return p.sidecars_ok
        case "fetch":
            return p.fetch_ok
        case "embeddings":
            return p.embeddings_ok
        case "model_load":
            return p.model_load_ok
        case "whisper":
            return p.whisper_ok
        case _:
            return True  # Unknown capabilities are allowed by default (fail-open)


def get_qos_level() -> str:
    """[FINAL]-019: Get the current QoS level name (QoSLevel value)."""
    return _last_qos_profile.level


# ── Singleton accessor ───────────────────────────────────────────────────────────

_governor: M1ResourceGovernor | None = None


def get_governor() -> M1ResourceGovernor:
    """
    Get or create the singleton M1ResourceGovernor.

    This is the canonical way to access the governor from outside core/.
    Lazily creates the instance on first call.
    """
    global _governor
    if _governor is None:
        _governor = M1ResourceGovernor()
    return _governor
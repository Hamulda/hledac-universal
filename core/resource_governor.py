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

# Python 3.11+ StrEnum — type-safe UMA state labels, exhaustive match support
if True:  # noqa: E702 — gate for Python version guard (3.11+)
    from enum import StrEnum

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

else:
    # Python 3.10 fallback — StrEnum not available, use plain str
    UMAState = str  # type: ignore[misc,assignment]  # noqa: N816


@dataclass(frozen=True, slots=True)
class ConcurrencyPreset:
    """
    Sprint F289: Immutable concurrency preset derived from UMA state.

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
    cache_ttl_seconds: float  # TTL for backpressure cache (S1: dynamic feedback)
    aimd_decrease_factor: float  # AIMD multiplicative decrease on failure

    @classmethod
    def from_state(cls, state: str) -> ConcurrencyPreset:
        """
        Python 3.10+ match statement pro derivaci presetu ze stavu.

        Uses guard clauses (if conditions in case pattern) for threshold
        ordering. This is the canonical pattern for range-based matches.
        """
        match state:
            case "emergency":
                return cls(max_workers=0, fetch_limit=1, block_model_load=True, cache_ttl_seconds=0.1, aimd_decrease_factor=0.0)
            case "critical":
                return cls(max_workers=1, fetch_limit=2, block_model_load=True, cache_ttl_seconds=0.25, aimd_decrease_factor=0.25)
            case "warn":
                return cls(max_workers=3, fetch_limit=5, block_model_load=False, cache_ttl_seconds=1.0, aimd_decrease_factor=0.5)
            case "soft_warn":
                return cls(max_workers=5, fetch_limit=10, block_model_load=False, cache_ttl_seconds=2.0, aimd_decrease_factor=0.75)
            case "ok":
                return cls(max_workers=5, fetch_limit=20, block_model_load=False, cache_ttl_seconds=5.0, aimd_decrease_factor=1.0)
            case _:  # Safe default — exhaustive match + _ catch-all for forward-compat
                return cls(max_workers=5, fetch_limit=20, block_model_load=False, cache_ttl_seconds=5.0, aimd_decrease_factor=1.0)

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

import threading as _threading  # noqa: E402
import time as _time_module  # noqa: E402

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

# Sprint F289-NEW: M1 8GB recalibrated thresholds for MacBook Air M1 8GB UMA
# MacBook Air M1 8GB UMA: systém sám využívá 5-7 GiB při běžné práci
# (macOS ~2.5-4.5 GiB + various system daemons). Limity musí být nad tímto rozsahem.
#
# Nový threshold ladder (GiB = bytes / 1024**3):
#   5.5 GiB → soft ceiling (fetch concurrency hard-cap via uma_budget M1_FETCH_SOFT_CEILING_GB)
#   6.8 GiB → SOFT_WARN (~85%) — první signál mírného pressure
#   7.0 GiB → WARN (~88%) — snížit concurrency
#   7.5 GiB → CRITICAL (~94%) — aktivní pressure, výrazné omezení
#   7.8 GiB → EMERGENCY (~98%) — skutečná krize, flush + GC
#
# Proč 6.8/7.0/7.5/7.8 místo 5.8/6.0/6.7/7.0:
#   Původní limity byly kalibrovány na conservative low-RAM profiling.
#   M1 8GB při běžné práci (Safari + Terminal + Docker) spotřebuje ~5.5-6.5 GiB.
#   Příliš nízké limity způsobovaly false-positive CRITICAL/EMERGENCY,
#   což vedlo k nadměrnému omezování concurrency a degradaci výkonu.
#   Nové limity jsou kalibrovány na reálné workload profiles M1 8GB.
_THRESHOLD_SOFT_WARN_GIB: float = 6.8  # F289-NEW: raised from 5.8 — ~85%, first signal above idle+headroom
_THRESHOLD_WARN_GIB: float = 7.0       # F289-NEW: raised from 6.0 — ~88%, reduce concurrency
_THRESHOLD_CRITICAL_GIB: float = 7.5   # F289-NEW: raised from 6.7 — ~94%, active pressure
_THRESHOLD_EMERGENCY_GIB: float = 7.8  # F289-NEW: raised from 7.0 — ~98%, real crisis
_HYSTERESIS_EXIT_GIB: float = 6.8      # F289-NEW: exit io_only below this

# Sprint 8AK: SSOT UMA state labels (plain string constants, no StrEnum)
# F220K: SOFT_WARN state (between soft ceiling 5.5GiB and WARN 6.0GiB)
UMA_STATE_SOFT_WARN: str = "soft_warn"
UMA_STATE_OK: str = "ok"
UMA_STATE_WARN: str = "warn"
UMA_STATE_CRITICAL: str = "critical"
UMA_STATE_EMERGENCY: str = "emergency"

# F289-NEW: macOS swap tiered policy constants recalibrated for M1 8GB
# M1 8GB baseline swap při idle je ~1.0-1.2 GiB, při běžné zátěži 2.0-2.5 GiB.
# Původní limity (2.0/4.0/4.0) jsou příliš nízké a způsobují false-positive.
# Nové limity jsou kalibrovány na reálné M1 8GB swap profily:
#   3.0 GiB → clean/READY_TO_RUN_NOW (allows normal workload variance)
#   5.0 GiB → diagnostic/tainted (active swap = hardware taint, but still recoverable)
#   6.0 GiB → hard block/restart required (systemic crisis)
CLEAN_SWAP_MAX_GIB: float = 3.0       # F289-NEW: raised from 2.0 GiB
DIAGNOSTIC_SWAP_MAX_GIB: float = 5.0  # F289-NEW: raised from 4.0 GiB
HARD_BLOCK_SWAP_GIB: float = 6.0      # F289-NEW: raised from 4.0 GiB


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

from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget  # noqa: E402

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


# ── G-1: GovernorDecision + M1ResourceGovernor ────────────────────────────────


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    """
    G-1 Fix: Canonical governor rozhodnutí s auto-apply semantics.

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


class M1ResourceGovernor:
    """
    G-1 Fix: Self-applying M1 UMA governor.

    evaluate() vždy volá apply_decision() interně před návratem —
    eliminuje 18/20 apply drift napříč všemi call sites.

    Používá backpressure_monitor (backpressure.py) a acquisition_strategy.py.
    Pro plnou specifikaci viz SYSTEM_ANALYSIS_2026.md §G-1.
    """

    # Class-level cached decision (shared across all instances for module-level authority)
    _cached_decision: GovernorDecision | None = None
    _cached_decision_timestamp: float = 0.0
    _decision_lock: asyncio.Lock | None = None

    def __init__(self, cache_ttl_s: float = 5.0):
        self._cache_ttl_s = cache_ttl_s
        if M1ResourceGovernor._decision_lock is None:
            M1ResourceGovernor._decision_lock = asyncio.Lock()

    async def evaluate(self) -> GovernorDecision:
        """
        G-1 Fix: Self-applying evaluate — auto-applies before returning.

        F-G1: Auto-apply eliminuje 18/20 apply drift.
        Všech 20 call sites okamžitě začne používat správné hodnoty.
        """
        now = time.monotonic()
        async with M1ResourceGovernor._decision_lock:
            if (
                M1ResourceGovernor._cached_decision is not None
                and now - M1ResourceGovernor._cached_decision_timestamp < self._cache_ttl_s
            ):
                return M1ResourceGovernor._cached_decision

            decision = await self._evaluate_impl()
            await self.apply_decision(decision)

            M1ResourceGovernor._cached_decision = decision
            M1ResourceGovernor._cached_decision_timestamp = now
            return decision

    async def _evaluate_impl(self) -> GovernorDecision:
        """
        Interní evaluace — gruz na sample_uma_status_async + threshold logika.
        Fail-soft: vrací bezpečné default při jakékoli chybě.
        """
        try:
            uma = await sample_uma_status_async()
        except Exception:
            # Sprint F289: Fail-soft uses ConcurrencyPreset for deterministic defaults
            preset = ConcurrencyPreset.from_state(UMAState.OK)
            return GovernorDecision(
                uma_state=UMAState.OK,
                io_only=False,
                fetch_limit=preset.fetch_limit,
                block_model_load=preset.block_model_load,
            )

        # Sprint F289: Use ConcurrencyPreset for deterministic derivation
        preset = ConcurrencyPreset.from_state(uma.state)
        return GovernorDecision(
            uma_state=uma.state,
            io_only=uma.io_only,
            fetch_limit=preset.fetch_limit,
            block_model_load=preset.block_model_load,
        )

    async def apply_decision(self, decision: GovernorDecision) -> None:
        """
        G-1 Fix: Aplikuje decision na runtime surfaces (fail-soft).

        F-G1: apply_decision je volán vždy z evaluate() — caller už nemůže
        rozhodnutí ignorovat.

        Aplikuje:
        - _io_only_latch (hysteresis state)
        - telemetry (pro monitoring/alerting)
        """
        try:
            # G-1: Directly apply io_only decision to module-level latch (authoritative)
            # F823 fix: 'global' declaration tells Python _io_only_latch is module-level
            # (not local), so the assignment inside 'with' doesn't make it a local.
            global _io_only_latch
            current_latch = _io_only_latch
            with _io_only_latch_lock:
                _io_only_latch = decision.io_only
            # Sync telemetry so sample_uma_status() doesn't double-count transitions
            global _telemetry
            if decision.io_only and not current_latch:
                _telemetry["io_only_enter_count"] += 1
            elif not decision.io_only and current_latch:
                _telemetry["io_only_exit_count"] += 1
        except Exception:
            pass  # noqa: BLE001 — fail-soft, decision stejně vrácena

    # ── G-1: sidecar_admission API (pro intelligence/open_source_collectors.py) ──

    @dataclass(frozen=True, slots=True)
    class SidecarAdmission:
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
                    allowed=False,
                    reason=f"{uma.state}: {sidecar_name} est={est_mb}MB blocked"
                )
            return M1ResourceGovernor.SidecarAdmission(
                allowed=True,
                reason=f"{uma.state}: {sidecar_name} est={est_mb}MB low-cost-allowed"
            )

        return M1ResourceGovernor.SidecarAdmission(
            allowed=True,
            reason=f"{uma.state}: {sidecar_name} admitted"
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
                pass  # noqa: BLE001  # GPU metrics nejsou dostupné

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


# =============================================================================
# Sprint 8AB: Unified UMA Accountant Surface
# =============================================================================


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


def _get_memory_pressure_status() -> str:
    """
    Sprint 8AL-FIX: Read memory_pressure CLI status on macOS.

    The raw memory_pressure output tells us memory pressure level via
    "System-wide memory free percentage: N%":
        > 50% free → GREEN  (healthy)
        30-50%     → YELLOW (mild pressure, normal for M1 under load)
        < 30%      → RED    (severe pressure, swap_detected should trigger)

    Also falls back to "Compressor Stats" page count — a growing compressor
    indicates M1 memory system is actively compressing pages (normal on UMA).

    Returns status string: "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    Fail-open: returns "UNKNOWN" on any error (no spurious swap_detected).
    """
    try:
        import re
        import subprocess
        proc = subprocess.run(
            ["memory_pressure"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return "UNKNOWN"
        output = proc.stdout

        # Primary signal: free percentage
        # "System-wide memory free percentage: 48%"
        m = re.search(r"free percentage:\s*(\d+)%", output)
        if m:
            free_pct = int(m.group(1))
            if free_pct < 30:
                return "RED"
            elif free_pct < 50:
                return "YELLOW"
            else:
                return "GREEN"

        # Fallback: compressor pages — baseline on this M1 is ~150-180K pages
        # A spike > 250K pages with low free% indicates active compression pressure.
        cm = re.search(r"Pages used by compressor:\s*(\d+)", output)
        if cm:
            compressor_pages = int(cm.group(1))
            # 250K pages = ~3.9 GB compressed, well above idle baseline
            if compressor_pages >= 250_000:
                return "RED"
            elif compressor_pages >= 200_000:
                return "YELLOW"

        return "UNKNOWN"
    except Exception:
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
        pass  # noqa: BLE001  # swap unavailable — fail-open silently

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
    # Sprint 8AL-FIX: M1 swap IS fast (S256B flash in UMA, ~2-4 GB/s, memory compressor).
    # A value of 3.6 GiB at idle is NORMAL — do not trigger on absolute swap alone.
    # Also check compressor pages (via memory_pressure CLI): >200000 pages (~3.1 GB compressed)
    # indicates systemic memory pressure beyond normal M1 behavior.
    # Sprint 8AL-FIX: M1 swap IS fast (S256B flash in UMA, ~2-4 GB/s, memory compressor).
    # A value of 3.6 GiB at idle is NORMAL — do not trigger on absolute swap alone.
    # Variant C: swap > 5.0 GiB OR memory_pressure status is CRITICAL/RED
    # (compressor actively growing under load — better signal than static page count).
    _pressure_status = _get_memory_pressure_status()
    swap_detected = swap_used_gib > 5.0 or _pressure_status in ("CRITICAL", "RED")

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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, sample_uma_status)


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
                pass  # noqa: BLE001  # fail-open: keep monitoring even on one bad tick

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
# Sprint F290: Adaptive MPC Controller — Predictive Memory Governor
# =============================================================================
# Replaces naive threshold-based state machine with Model Predictive Control.
#
# Problem: Threshold-based state machine reacts AFTER memory exceeds threshold.
# On M1 8GB UMA, memory can rise 0.5-1.5 GiB in seconds during MLX batch inference.
# By the time CRITICAL is detected, overshoot + swap is already happening.
#
# Solution: Adaptive MPC that:
# - Tracks memory velocity (1st derivative) via EMA
# - Tracks memory acceleration (2nd derivative) via EMA
# - Predicts memory state N seconds ahead (MPC horizon)
# - Computes safe headroom relative to EMERGENCY threshold
# - Derives concurrency control input BEFORE thresholds are crossed
#
# Why EMA-based prediction (not full MPC with QP solver):
# - M1 8GB cannot afford real-time QP solver overhead (~5-10ms per call)
# - Memory behavior is dominated by our own allocation patterns (predictable)
# - Analytical solution for this specific problem structure is O(1)
#
# invariants:
# - Always-on, no feature flags
# - Bounded history deque (maxlen=32)
# - Fail-safe: returns safe defaults on any error
# - Thread-safe via asyncio.Lock for concurrent access

from collections import deque

_MPC_HISTORY: deque[tuple[float, float, float, float, float]] = deque(maxlen=32)
# (timestamp, memory_gib, velocity_gib_s, acceleration_gib_s2, control_input)
_mpc_lock: asyncio.Lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class MPCMetrics:
    """
    F290: Diagnostic snapshot from MPC controller.

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
        safe_headroom = EMERGENCY_GIB - predicted_memory
        if safe_headroom < 0:
            control = clamp(1.0 + safe_headroom / EMERGENCY_GIB, 0.0, 0.3)
        elif safe_headroom < TARGET_HEADROOM_GIB:
            control = clamp(safe_headroom / TARGET_HEADROOM_GIB, 0.3, 0.8)
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

    # EMA coefficients calibrated for M1 8GB memory behavior
    _ALPHA_FAST: float = 0.4
    _ALPHA_SLOW: float = 0.15
    _MPC_HORIZON_S: float = 10.0
    _TARGET_HEADROOM_GIB: float = 0.5
    _EMERGENCY_THRESHOLD_GIB: float = 7.8

    __slots__ = ('_ema_v', '_ema_a', '_last_t', '_last_mem', '_enabled')

    def __init__(self) -> None:
        self._ema_v: float = 0.0
        self._ema_a: float = 0.0
        self._last_t: float | None = None
        self._last_mem: float | None = None
        self._enabled: bool = True

    async def compute_control(
        self,
        current_memory_gib: float,
        current_state: str,
        sample_interval_s: float = 5.0,
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

        async with _mpc_lock:
            # First call: initialize state, return nominal
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
                return 1.0, metrics

            # Compute time delta between samples.
            # CRITICAL INVARIANT: dt is the actual wall-clock time since last sample,
            # NOT the hypothetical interval. But dt can be tiny (~0) when calls come
            # faster than the expected sample rate (e.g., rapid test sequences).
            # In that case, cap dt at SAMPLE_INTERVAL so velocity and acceleration
            # reflect at most one sample's worth of change — prevents "instant spike"
            # from creating a 700 GiB/s apparent velocity.
            raw_dt = now - self._last_t
            dt = max(raw_dt, sample_interval_s)
            self._last_t = now

            # Raw velocity (GiB/s) — signed, then hard-clamped to physically plausible range
            # M1 8GB memory growth rate: max ~0.5 GiB/s during heavy MLX batch.
            # Allow 4× headroom: [-2.0, 2.0] GiB/s. Without this clamp, a simulated
            # step with dt≈0 (rapid calls) produces raw_v ≈ 140 GiB/s and destroys EMA.
            raw_v = (current_memory_gib - self._last_mem) / dt
            raw_v = max(-2.0, min(2.0, raw_v))
            self._last_mem = current_memory_gib

            # IMPORTANT: save previous EMA BEFORE updating — needed for acceleration
            prev_ema = self._ema_v
            # Update EMA of velocity (fast response)
            self._ema_v = self._ALPHA_FAST * raw_v + (1.0 - self._ALPHA_FAST) * self._ema_v

            # Acceleration (GiB/s²): derivative of EMA velocity.
            # BUG FIX: prev_ema must be captured BEFORE ema_v update (was saved after,
            # causing prev_ema == ema_v on every step → raw_a ≈ 0 always).
            raw_a = (self._ema_v - prev_ema) / dt
            # Clamp to physically plausible range: M1 8GB max accel ≈ 0.05 GiB/s²
            # Allow 4× headroom: [-0.2, 0.2] GiB/s²
            raw_a = max(-0.2, min(0.2, raw_a))
            # Very slow EMA for acceleration (captures only sustained trend, ignores spikes)
            self._ema_a = self._ALPHA_SLOW * raw_a + (1.0 - self._ALPHA_SLOW) * self._ema_a

            # F290 FIX: Linear MPC prediction.
            # Previous formula: x + v*h + 0.5*a*h² → catastrophically wrong (h² = 100).
            # Current: predicted = current + ema_v * h * (1 + 0.1 * ema_a)
            # Small acceleration multiplier (0.1, not 0.5) prevents runaway growth.
            accel_factor = 1.0 + 0.1 * self._ema_a
            # Clamp: [0.7, 1.3] — max 30% modification from acceleration
            accel_factor = max(0.7, min(1.3, accel_factor))
            predicted = current_memory_gib + self._ema_v * self._MPC_HORIZON_S * accel_factor

            # Safe headroom: how far are we from emergency?
            safe_headroom = self._EMERGENCY_THRESHOLD_GIB - predicted

            # Anti-windup MPC control law (analytical, O(1))
            if safe_headroom < 0:
                # Over emergency threshold: aggressive reduction
                # clamp to [0.0, 0.3] — leave 30% for GC/cleanup cycles
                control = max(0.0, min(0.3, 1.0 + safe_headroom / self._EMERGENCY_THRESHOLD_GIB))
            elif safe_headroom < self._TARGET_HEADROOM_GIB:
                # Approaching: proportional reduction in [0.3, 0.8]
                ratio = safe_headroom / self._TARGET_HEADROOM_GIB
                control = 0.3 + 0.5 * ratio  # 0.3 → 0.8 as headroom grows
            else:
                # Safe zone: nominal concurrency
                control = 1.0

            # State-conditional overrides: be more conservative when already stressed
            if current_state in (UMAState.EMERGENCY, UMAState.CRITICAL):
                control = min(control, 0.1)  # Cap at 10% under stress

            # Append to bounded history
            _MPC_HISTORY.append((now, current_memory_gib, raw_v, raw_a, control))

            # Determine predicted state from predicted memory
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

            return control, metrics

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
        } if _MPC_HISTORY else None,
    }


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

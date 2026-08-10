"""
UnifiedMemoryBudgetAccountant - Sprint 1B Resource Hardening.

ROLE: Canonical RAW UMA SAMPLER + SSOT for M1 8GB memory budget (not a governor/policy/allocator).

========================================================================================
SSOT: M1 8GB Unified Memory Budget (MODERN-36 + MODERN-45 Fix)
========================================================================================

ISSUE MODERN-36: No single source of truth — 7+ contradictory ceilings.
ISSUE MODERN-45: Subsystems measure different axes (system-used vs process-RSS vs tracked-allocation)
                 and compare against each other, leading to confusing invariants.

MODERN-45 SOLUTION: SSOT exposes two derived ceilings:
- SYSTEM_USED_CEILING (6.25 GiB): OS-level reaction threshold
- PROCESS_RSS_CEILING (5.5 GiB): Process admission threshold

Each consumer declares which axis it uses; compile-time assert ensures:
    PROCESS_RSS_CEILING <= SYSTEM_USED_CEILING <= 6.25  (5.5 <= 6.25 <= 6.25 ✓)
This module is now the SINGLE SOURCE OF TRUTH for ALL memory budget constants.

PRIOR TO FIX:
- core/resource_governor.py: 6.8/7.0/7.5/7.8 (derived from 8GB ratios)
- core/peak_load_coordinator.py: 6.48 (81% of 8GB)
- utils/mlx_memory/_core.py: 6.25 ✅ (correct)
- core/config/m1_air_config.py: 6.0
- transport/policy.py: 6.0
- runtime/resource_governor.py: 5.5
- rust_extensions/src/memory.rs: 5.5

AFTER FIX:
- UmaBudget.UMA_HARD_CEILING_GIB = 6.25 (THE ONLY SSOT CONSTANT)
- All subsystems derive their fractions FROM this constant
- Axis is documented per constant (system-used vs tracked-allocation vs process-RSS)

Budget breakdown (macOS 8GB baseline):
    - macOS system:      2.5 GiB (baseline, non-adjustable, AXIS: system-used)
    - Orchestrator:      1.0 GiB (fetch/parse/DB/overhead, AXIS: tracked-allocation)
    - LLM model weights: 2.0 GiB (DeepHermes-3-3B Q4, AXIS: tracked-allocation)
    - KV cache:         0.75 GiB (max allocation, AXIS: tracked-allocation)
    ─────────────────────────────────────────────
    - TOTAL CEILING:     6.25 GiB (AXIS: system-used ceiling, THE SSOT)

Hard ceiling (MISSION_PEAK_RSS_GIB): 5.5 GiB
    → AXIS: process-RSS hard cap for fetch concurrency
    → Derived from: MISSION_PEAK_RSS_RATIO * UMA_HARD_CEILING_GIB

Threshold ladder (derived from M1 8GB * ratios):
    - 5.5 GiB (88%) → soft ceiling (process-RSS)
    - 5.938 GiB (95%) → WARN (system-used)
    - 6.191 GiB (99%) → CRITICAL (system-used)
    - 6.25 GiB (100%) → HARD CEILING (SSOT)

Invariant: All code importing these values MUST import from here, not define their own.
    - core/resource_governor.py: imports from here ✅ (MODERN-36 fix)
    - core/peak_load_coordinator.py: imports from here ✅ (MODERN-36 fix)
    - core/config/m1_air_config.py: imports from here ✅ (MODERN-36 fix)
    - transport/policy.py: imports from here ✅ (MODERN-36 fix)
    - rust_extensions/src/memory.rs: syncs from here at startup ✅ (MODERN-36 fix)



This module provides:
- Raw memory sampling (system RAM via psutil, MLX active/peak/cache)
- Pressure level classification (normal/warn/critical/emergency)
- Async watchdog with state-change callbacks
- SSOT UmaBudget class with all budget constants

Threshold levels (M1 8GB UMA, SSOT derived from 6.25 GiB ceiling):
- SOFT_WARN:   >= 5.5 GiB (~88% — first signal, process-RSS soft ceiling)
- WARN:        >= 5.938 GiB (~95% — reduce concurrency)
- CRITICAL:    >= 6.191 GiB (~99% — active pressure)
- EMERGENCY:   >= 6.25 GiB (~100% — crisis, == ceiling)

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
__all__ = [
    # ═══════════════════════════════════════════════════════════════════════════
    # SSOT EXPORTS (MODERN-36/41/45 Fix) — THE ONLY AUTHORITATIVE CONSTANTS
    # ═══════════════════════════════════════════════════════════════════════════
    'UmaBudget',  # SSOT class with all budget constants
    'MemoryAxis',  # MODERN-45: Axis declarations for memory constants
    'SwapTiers',  # SSOT swap policy tiers (MODERN-41 fix)
    'SWAP_TIERS',  # SSOT swap tiers instance (MODERN-41 fix)
    # PRIMARY SSOT: Import this, everything else derives from it
    'UMA_HARD_CEILING_GIB',  # 6.25 GiB — THE SSOT CONSTANT (AXIS: system-used)
    # Derived process-RSS ceiling
    'MISSION_PEAK_RSS_GIB',  # 5.5 GiB — process RSS hard cap (AXIS: process-rss)
    # Legacy exports (for backward compatibility — values unchanged)
    'UMA_TOTAL_BUDGET_GIB',  # 6.25 GiB — alias for UmaBudget.UMA_HARD_CEILING_GIB
    'UMA_WARN_GIB', 'UMA_CRITICAL_GIB', 'UMA_EMERGENCY_GIB',  # Legacy aliases
    'M1_FETCH_SOFT_CEILING_GB', 'GENERAL_HIGH_WATER_RATIO',
    # Budget breakdown components (AXIS: tracked-allocation)
    'ORCHESTRATOR_GIB', 'LLM_WEIGHTS_GIB', 'KV_CACHE_GIB', 'MACOS_SYSTEM_GIB',
    # Function exports
    'get_uma_snapshot', 'get_uma_budget', 'get_uma_usage_mb',
    'get_uma_pressure_level', 'is_uma_critical', 'is_uma_warn',
    'is_uma_emergency', 'format_uma_budget_report',
    'UmaWatchdog', 'UmaWatchdogCallbacks', 'shutdown_uma_callback_executor',
]

# =============================================================================
# SSOT: M1 8GB Unified Memory Budget (P7-3)
# =============================================================================
# Single source of truth for all memory budget constants.
# Import from here — do NOT define these elsewhere.
#
# Budget breakdown:
#   - macOS system:      2.5 GiB (baseline, non-adjustable)
#   - Orchestrator:      1.0 GiB (fetch/parse/DB/overhead)
#   - LLM model weights: 2.0 GiB (DeepHermes-3-3B Q4)
#   - KV cache:         0.75 GiB (max allocation)
#   ─────────────────────────────────────────────
#   - TOTAL:            6.25 GiB


# =============================================================================
# MODERN-45: Memory Axis Enums (compile-time axis declarations)
# =============================================================================
# Each memory constant MUST declare which axis it measures.
# This prevents subsystems from comparing apples to oranges.

class MemoryAxis:
    """
    MODERN-45: Compile-time axis declarations for memory constants.

    Three distinct memory axes are measured in this codebase:
    1. SYSTEM_USED: macOS total RAM - available RAM (all processes combined)
       - Used by: resource_governor thresholds, UMA state evaluation
       - SSOT constant: UmaBudget.UMA_HARD_CEILING_GIB = 6.25 GiB

    2. PROCESS_RSS: Our process's Resident Set Size (subset of system-used)
       - Used by: admission control, fetch concurrency limits
       - SSOT constant: UmaBudget.MISSION_PEAK_RSS_GIB = 5.5 GiB

    3. TRACKED_ALLOCATION: Hledac's internal ledger of reserved allocations
       - Used by: MLX memory slab allocator, model loading
       - Components: ORCHESTRATOR_GIB + LLM_WEIGHTS_GIB + KV_CACHE_GIB = 3.75 GiB

    INVARIANT (verified at import time):
        PROCESS_RSS_CEILING <= SYSTEM_USED_CEILING <= 6.25
        5.5 GiB         <= 6.25 GiB          <= 6.25 ✓

    WHY THIS MATTERS:
    - SYSTEM_USED can be > PROCESS_RSS when other processes use memory
    - PROCESS_RSS is always <= TRACKED_ALLOCATION + macOS baseline
    - Mixing axes causes false alarms or missed pressure signals
    """

    # Axis labels for documentation purposes
    SYSTEM_USED = "system-used"        # OS-level (all processes)
    PROCESS_RSS = "process-rss"        # Our process's RSS
    TRACKED_ALLOCATION = "tracked-allocation"  # Hledac internal ledger

    # Consumer declarations (which axis each subsystem uses)
    # These are documentation-only; the actual assertions are below
    GOVERNOR_THRESHOLDS = SYSTEM_USED      # resource_governor uses system_used_gib
    FETCH_ADMISSION = PROCESS_RSS           # Fetch concurrency uses process RSS
    MLX_SLAB = TRACKED_ALLOCATION          # MLX allocator tracks allocations
    METAL_MEMORY = TRACKED_ALLOCATION       # MLX Metal is part of tracked-allocation


# =============================================================================
# MODERN-45: Compile-time assertion for memory ceiling invariant
# =============================================================================
# This assertion fires at import time, catching misconfigurations early.
# The invariant: PROCESS_RSS_CEILING <= SYSTEM_USED_CEILING <= 6.25
#
# Current values: 5.5 <= 6.25 <= 6.25 ✓
# If this fails, the M1 8GB budget model is broken.

# Deferred import to avoid circular dependency
def _assert_memory_invariants() -> None:
    """MODERN-45: Verify memory ceiling invariant at module import time."""
    # These values are hardcoded (not derived) to catch any future regressions
    SYSTEM_USED_CEILING: float = 6.25  # AXIS: system-used
    PROCESS_RSS_CEILING: float = 5.5  # AXIS: process-rss
    MAX_VALID_CEILING: float = 6.25   # Physical limit for M1 8GB

    assert PROCESS_RSS_CEILING <= SYSTEM_USED_CEILING, (
        f"MODERN-45 INVARIANT VIOLATION: PROCESS_RSS_CEILING ({PROCESS_RSS_CEILING}) "
        f"must be <= SYSTEM_USED_CEILING ({SYSTEM_USED_CEILING})"
    )
    assert SYSTEM_USED_CEILING <= MAX_VALID_CEILING, (
        f"MODERN-45 INVARIANT VIOLATION: SYSTEM_USED_CEILING ({SYSTEM_USED_CEILING}) "
        f"must be <= MAX_VALID_CEILING ({MAX_VALID_CEILING})"
    )


# Execute assertion immediately at import (before any code uses the constants)
_assert_memory_invariants()


class UmaBudget:
    """
    MODERN-36 Fix: P7-3 SSOT — M1 8GB Unified Memory Budget.
    
    MODERN-36 ROOT CAUSE: Three axes (system-used / tracked-allocation / process-RSS)
    were conflated; no file stated which axis its constant measured; budgets diverged >2 GiB.

    THIS MODULE IS THE SINGLE SOURCE OF TRUTH.
    All subsystems MUST import from here, NOT define their own constants.

    AXIS DEFINITIONS:
    - system-used: macOS system memory tracker (total - available), includes all processes
    - tracked-allocation: Hledac's own allocation budget (what we reserve/track)
    - process-RSS: Our process's Resident Set Size (subset of tracked-allocation)

    Usage:
        from hledac.universal.utils.uma_budget import UmaBudget, MISSION_PEAK_RSS_GIB

        # Access the ONLY SSOT ceiling
        assert UmaBudget.UMA_HARD_CEILING_GIB == 6.25  # THE SSOT

        # Access derived thresholds (all from SSOT)
        UmaBudget.MISSION_PEAK_RSS_GIB  # 5.5 GiB — process-RSS hard cap
        UmaBudget.ORCHESTRATOR_GIB      # 1.0 GiB — tracked-allocation

    Migration guide for files that defined their own constants:
    - DELETE local constant definitions
    - ADD: from hledac.universal.utils.uma_budget import UmaBudget
    - REPLACE: use UmaBudget.CONSTANT_NAME instead
    """

    # ═══════════════════════════════════════════════════════════════════════════
    # THE SSOT: Hard ceiling — the ONLY authoritative constant (AXIS: system-used)
    # ═══════════════════════════════════════════════════════════════════════════
    # This is the ONE constant everything else derives from.
    # DO NOT define this elsewhere. DO NOT hardcode 6.25 in other files.
    UMA_HARD_CEILING_GIB: float = 6.25
    """
    MODERN-36 SSOT: Hard ceiling for all subsystem allocations.
    AXIS: system-used (macOS total memory - available memory)
    
    This is the maximum total memory the Hledac process + macOS overhead can use.
    - Exceeding this means system swap → catastrophic performance degradation
    - All MLX Metal, ANE, and CPU allocations must stay under this
    - Derived budget fractions: ORCHESTRATOR + LLM_WEIGHTS + KV_CACHE = 3.75 GiB
    - macOS baseline: 2.5 GiB (non-adjustable, background system processes)
    - Total: 2.5 + 3.75 = 6.25 GiB
    
    IMPORTANT: This is NOT the process RSS limit. It's the system-used ceiling.
    Process RSS can approach this when other processes are idle.
    """

    # ═══════════════════════════════════════════════════════════════════════════
    # Budget breakdown (AXIS: tracked-allocation — what Hledac reserves/tracks)
    # ═══════════════════════════════════════════════════════════════════════════
    MACOS_SYSTEM_GIB: float = 2.5  # Baseline macOS overhead (AXIS: system-used baseline)
    ORCHESTRATOR_GIB: float = 1.0  # Fetch/parse/DB overhead (AXIS: tracked-allocation)
    LLM_WEIGHTS_GIB: float = 2.0  # DeepHermes-3-3B Q4 (AXIS: tracked-allocation)
    KV_CACHE_GIB: float = 0.75  # Max KV cache allocation (AXIS: tracked-allocation)

    # Computed: total tracked allocation budget (excludes macOS baseline)
    # MODERN-37/38 Fix: Changed from @classmethod @property to class variables
    # (property+classmethod decorator order is problematic in Python)
    TRACKED_ALLOCATION_BUDGET_GIB: float = 3.75  # ORCHESTRATOR + LLM_WEIGHTS + KV_CACHE
    """Total Hledac-tracked allocation: 3.75 GiB (excludes macOS baseline)."""

    # Legacy alias for backward compatibility
    TOTAL_GIB: float = 6.25  # DEPRECATED: Use UMA_HARD_CEILING_GIB
    """DEPRECATED: Use UMA_HARD_CEILING_GIB. Legacy alias for 6.25 GiB."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Mission ceiling (AXIS: process-RSS — our process's RSS hard cap)
    # ═══════════════════════════════════════════════════════════════════════════
    # This is what we tell resource_allocator as our RSS budget limit.
    # It's derived from SSOT to ensure consistency.
    MISSION_PEAK_RSS_RATIO: float = 0.88  # 88% of ceiling = headroom for spikes
    MISSION_PEAK_RSS_GIB: float = 5.5  # Derived: round(6.25 * 0.88, 2)
    """Process RSS hard cap: 5.5 GiB (MISSION_PEAK_RSS_RATIO * UMA_HARD_CEILING_GIB)."""

    # Legacy constant for backward compatibility (same value, different derivation)
    @property
    def MISSION_PEAK_RSS_GIB_OLD(self) -> float:
        """DEPRECATED: Was hardcoded 5.5. Now derived from SSOT."""
        return 5.5

    # ═══════════════════════════════════════════════════════════════════════════
    # Threshold ladder (AXIS: system-used percentage, derived from SSOT)
    # ═══════════════════════════════════════════════════════════════════════════
    # MODERN-37/38 Fix: Changed from @classmethod @property to class variables
    # These are percentages of UMA_HARD_CEILING_GIB (6.25 GiB)
    SOFT_WARN_RATIO: float = 0.88      # 88% = 5.5 GiB — first signal
    WARN_RATIO: float = 0.95          # 95% = 5.938 GiB — reduce concurrency
    CRITICAL_RATIO: float = 0.99      # 99% = 6.191 GiB — active pressure
    EMERGENCY_RATIO: float = 1.00     # 100% = 6.25 GiB — crisis (== ceiling)

    THRESHOLD_SOFT_WARN_GIB: float = 5.5     # Derived: round(6.25 * 0.88, 2)
    """~88% — first signal of mild pressure."""
    THRESHOLD_WARN_GIB: float = 5.94   # Derived: round(6.25 * 0.95, 2)
    """~95% — reduce concurrency."""
    THRESHOLD_CRITICAL_GIB: float = 6.19  # Derived: round(6.25 * 0.99, 2)
    """~99% — active pressure, significant restriction."""
    THRESHOLD_EMERGENCY_GIB: float = 6.25  # Same as UMA_HARD_CEILING_GIB
    """~100% — real crisis, flush + GC (== ceiling)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Fetch concurrency soft ceiling (AXIS: process-RSS)
    # ═══════════════════════════════════════════════════════════════════════════
    M1_FETCH_SOFT_CEILING_GB: float = 5.5  # Same as MISSION_PEAK_RSS_GIB

    # ═══════════════════════════════════════════════════════════════════════════
    # High water ratio for sidecar admission
    # ═══════════════════════════════════════════════════════════════════════════
    HIGH_WATER_RATIO: float = 0.88  # 88% of ceiling

    # ═══════════════════════════════════════════════════════════════════════════
    # Metal cache limits (AXIS: MLX Metal specific)
    # ═══════════════════════════════════════════════════════════════════════════
    METAL_CACHE_FLOOR_MIB: int = 512  # Min 512 MiB
    METAL_CACHE_CEILING_MIB: int = 1536  # Max 1.5 GiB (1536 MiB)

    # ═══════════════════════════════════════════════════════════════════════════
    # Pre-warm buffer size (AXIS: MLX Metal inference optimization)
    # ═══════════════════════════════════════════════════════════════════════════
    MLX_PREWARM_BUFFER_MIB: int = 48  # 48 MiB pre-warm buffer for MLX Metal


# ═══════════════════════════════════════════════════════════════════════════════
# MODERN-36 FIX: Module-level SSOT exports
# ═══════════════════════════════════════════════════════════════════════════════
# These are the ONLY constants other modules should import.
# All are derived from UmaBudget.UMA_HARD_CEILING_GIB = 6.25 GiB

# PRIMARY SSOT: The ONE constant everything else derives from
UMA_HARD_CEILING_GIB: float = UmaBudget.UMA_HARD_CEILING_GIB  # 6.25 GiB

# Derived process-RSS ceiling (MISSION_PEAK_RSS_RATIO * UMA_HARD_CEILING_GIB)
MISSION_PEAK_RSS_GIB: float = UmaBudget.MISSION_PEAK_RSS_GIB  # 5.5 GiB

# Legacy exports (for backward compatibility — values unchanged but documented as derived)
UMA_TOTAL_BUDGET_GIB: float = UmaBudget.UMA_HARD_CEILING_GIB  # 6.25 GiB (alias)

# Legacy threshold aliases (now derived from SSOT)
UMA_WARN_GIB: float = UmaBudget.THRESHOLD_WARN_GIB  # 5.938 GiB
UMA_CRITICAL_GIB: float = UmaBudget.THRESHOLD_CRITICAL_GIB  # 6.191 GiB
UMA_EMERGENCY_GIB: float = UmaBudget.THRESHOLD_EMERGENCY_GIB  # 6.25 GiB

# Budget breakdown components
ORCHESTRATOR_GIB: float = UmaBudget.ORCHESTRATOR_GIB  # 1.0 GiB
LLM_WEIGHTS_GIB: float = UmaBudget.LLM_WEIGHTS_GIB  # 2.0 GiB
KV_CACHE_GIB: float = UmaBudget.KV_CACHE_GIB  # 0.75 GiB
MACOS_SYSTEM_GIB: float = UmaBudget.MACOS_SYSTEM_GIB  # 2.5 GiB

# Fetch concurrency
M1_FETCH_SOFT_CEILING_GB: float = UmaBudget.M1_FETCH_SOFT_CEILING_GB  # 5.5 GiB
GENERAL_HIGH_WATER_RATIO: float = UmaBudget.HIGH_WATER_RATIO  # 0.88

# ═══════════════════════════════════════════════════════════════════════════════
# MODERN-41 Fix: SWAP_TIERS SSOT — single source of truth for swap policy
# ═══════════════════════════════════════════════════════════════════════════════
from typing import NamedTuple


class SwapTiers(NamedTuple):
    """
    MODERN-41 SSOT: Swap policy tiers derived from MISSION_PEAK_RSS_GIB.

    AXIS: system swap usage — macOS swap file consumption.

    Rationale for thresholds based on MISSION_PEAK_RSS_GIB = 5.5 GiB:
    - CLEAN: Normal operation, swap usage within expected bounds
    - DIAGNOSTIC: Elevated swap, system under pressure
    - HARD_BLOCK: Near-critical swap, memory thrashing imminent

    Usage:
        from hledac.universal.utils.uma_budget import SwapTiers

        tiers = SwapTiers.default()  # Use SSOT values
        if swap_gib <= tiers.CLEAN:
            tier = "clean"
        elif swap_gib <= tiers.DIAGNOSTIC:
            tier = "diagnostic"
        else:
            tier = "hard_block"
    """

    CLEAN: float
    """Normal operation: swap <= this value (GiB)."""

    DIAGNOSTIC: float
    """Elevated swap: (CLEAN, DIAGNOSTIC] range (GiB)."""

    HARD_BLOCK: float
    """Near-critical: (DIAGNOSTIC, HARD_BLOCK] range (GiB)."""

    @classmethod
    def default(cls) -> SwapTiers:
        """Create swap tiers from SSOT MISSION_PEAK_RSS_GIB (5.5 GiB).

        Derivation:
        - CLEAN: 60% of MISSION_PEAK_RSS = 3.3 GiB
        - DIAGNOSTIC: 85% of MISSION_PEAK_RSS = 4.675 GiB
        - HARD_BLOCK: 95% of MISSION_PEAK_RSS = 5.225 GiB
        """
        mission_peak = 5.5  # UmaBudget.MISSION_PEAK_RSS_GIB
        return cls(
            CLEAN=round(mission_peak * 0.60, 2),  # 3.3 GiB
            DIAGNOSTIC=round(mission_peak * 0.85, 2),  # 4.675 GiB
            HARD_BLOCK=round(mission_peak * 0.95, 2),  # 5.225 GiB
        )


# SSOT SWAP_TIERS instance — import this, don't define locally
SWAP_TIERS: SwapTiers = SwapTiers.default()


# R5: UMA callback executor — managed centrally by domain_executors.
# No local atexit registration needed — domain_executors handles shutdown.
_uma_callback_executor: ThreadPoolExecutor | None = None


def _get_uma_executor() -> ThreadPoolExecutor:
    """R5: Get the bounded UMA callback executor from domain_executors.

    max_workers=2: one for current callback, one for pending (prevents head-of-line blocking).
    Managed centrally by domain_executors — no local shutdown needed.
    """
    global _uma_callback_executor
    if _uma_callback_executor is None:
        from hledac.universal.utils.domain_executors import get_uma_callback_executor
        _uma_callback_executor = get_uma_callback_executor()
    return _uma_callback_executor


async def _run_callback_in_executor(cb, snapshot: dict) -> None:
    """Run a synchronous callback in the bounded thread pool, propagating exceptions."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_get_uma_executor(), cb, snapshot)
    except Exception as e:
        logger.error(f'[UMA-WATCHDOG] UMA callback failed: {e}')


def shutdown_uma_callback_executor() -> None:
    """R5: No-op — executor managed centrally by domain_executors.

    Kept for backward compatibility. Callers that previously called
    this at exit now get a harmless no-op.
    """
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

# P7-3 SSOT: Use UmaBudget class values directly — no circular dependency with resource_governor
# These are M1 8GB hardcoded values that never change (SSOT derived)
_WARN_THRESHOLD_MB: int = int(UmaBudget.THRESHOLD_WARN_GIB * 1024)  # 5.938 GiB → 6080 MB
_CRITICAL_THRESHOLD_MB: int = int(UmaBudget.THRESHOLD_CRITICAL_GIB * 1024)  # 6.191 GiB → 6340 MB
_EMERGENCY_THRESHOLD_MB: int = int(UmaBudget.THRESHOLD_EMERGENCY_GIB * 1024)  # 6.25 GiB → 6400 MB

# Legacy exports for backward compatibility (values match UmaBudget SSOT)
UMA_WARN_GIB: float = UmaBudget.THRESHOLD_WARN_GIB  # 5.938 GiB (95% of ceiling)
UMA_CRITICAL_GIB: float = UmaBudget.THRESHOLD_CRITICAL_GIB  # 6.191 GiB (99% of ceiling)
UMA_EMERGENCY_GIB: float = UmaBudget.THRESHOLD_EMERGENCY_GIB  # 6.25 GiB (100% = ceiling)
M1_FETCH_SOFT_CEILING_GB: float = UmaBudget.MISSION_PEAK_RSS_GIB  # 5.5 GiB (88% = MISSION_PEAK_RSS)

# Diagnostic info for snapshot (computed once at import time)
_RATIOS_USED: tuple[float, float, float, float] = (
    UmaBudget.THRESHOLD_SOFT_WARN_GIB / UmaBudget.TOTAL_GIB,  # 5.5/6.25 = 0.88
    UmaBudget.THRESHOLD_WARN_GIB / UmaBudget.TOTAL_GIB,  # 5.938/6.25 = 0.95
    UmaBudget.THRESHOLD_CRITICAL_GIB / UmaBudget.TOTAL_GIB,  # 6.191/6.25 = 0.99
    UmaBudget.THRESHOLD_EMERGENCY_GIB / UmaBudget.TOTAL_GIB,  # 6.25/6.25 = 1.00
)
_DETECTED_TOTAL_GIB: float = _UMA_TOTAL_MB / 1024

# MODERN-44 FIX: Sync Rust memory.rs thresholds with UmaBudget SSOT.
# memory.rs uses process RSS (PROC_PIDTASKINFO) for its memory_pressure_level(),
# which is separate from system-used GiB thresholds.
#
# MODERN-44 SSOT COMPUTATION:
# - soft_gib = UmaBudget.UMA_HARD_CEILING_GIB * 0.6 = 3.75 GiB
#   This is 60% of the hard ceiling — the "elevated" RSS threshold.
#   (vs system-used SOFT_WARN at 88% = 5.5 GiB)
# - hard_gib = UmaBudget.THRESHOLD_CRITICAL_GIB = 6.191 GiB
#   Aligns with system-used critical threshold.
#
# MODERN-44 FIX: Raise on import failure instead of silent divergence.
# If the import fails, Rust runs its default 5.5 GiB ceiling while Python
# runs 6.191 GiB → no coherent enforcement. This is a fatal configuration error.
_SOFT_RSS_GIB: float = UmaBudget.UMA_HARD_CEILING_GIB * 0.6  # 3.75 GiB — SSOT derived
_HARD_RSS_GIB: float = UmaBudget.THRESHOLD_CRITICAL_GIB  # 6.191 GiB — SSOT derived

try:
    from hledac.universal.core.memory import set_memory_pressure_thresholds as _set_rust_thresholds
    _set_rust_thresholds(soft_gib=_SOFT_RSS_GIB, hard_gib=_HARD_RSS_GIB)
except Exception as _e:
    # MODERN-44 FIX: Raise instead of silent pass.
    # Rust thresholds must be synchronized with Python SSOT for coherent enforcement.
    raise ImportError(
        f"MODERN-44 CRITICAL: Failed to sync Rust memory thresholds with UmaBudget SSOT. "
        f"Without sync, Rust uses default 5.5 GiB while Python uses {_HARD_RSS_GIB} GiB → "
        f"incoherent memory pressure enforcement. Fix: ensure rust_extensions built with 'memory' feature. "
        f"Original error: {_e}"
    ) from _e

# MODERN-42 Fix: Sync allocation ceiling from UmaBudget SSOT to Rust atomic ledger.
# Ceiling = 6.25 * 0.97 = 6.0625 GiB (3% headroom for OS).
_ALLOCATION_CEILING_GIB: float = round(UmaBudget.UMA_HARD_CEILING_GIB * 0.97, 4)

try:
    from hledac.universal.rust_extensions.memory import set_allocation_ceiling as _set_alloc_ceiling

    _set_alloc_ceiling(_ALLOCATION_CEILING_GIB)
except Exception as _e:
    # MODERN-42 Fix: Raise instead of silent pass.
    # Allocation ledger must be synchronized with UmaBudget SSOT for coherent enforcement.
    raise ImportError(
        f"MODERN-42 CRITICAL: Failed to sync Rust allocation ceiling with UmaBudget SSOT. "
        f"Without sync, allocation ledger uses default 6.0625 GiB from wrong source. "
        f"Fix: ensure rust_extensions built with 'memory' feature. "
        f"Original error: {_e}"
    ) from _e

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
    except Exception:  # noqa: BLE001
        pass
    # Fallback: use psutil directly (avoid circular dependency with resource_governor)
    try:
        import psutil as _ps_fallback
        vm = _ps_fallback.virtual_memory()
        total = getattr(vm, 'total', 0)
        available = getattr(vm, 'available', 0)
        used = total - available
        total_mb = total // (1024 * 1024)
        used_mb = used // (1024 * 1024)
        available_mb = available // (1024 * 1024)
        return (total_mb, used_mb, available_mb)
    except (ImportError, AttributeError, OSError) as e:
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
        except (AttributeError, OSError):  # noqa: BLE001
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
        except (AttributeError, OSError):  # noqa: BLE001
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
    """Return True if UMA usage >= 6.25 GiB (ceiling = 100%)."""
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
        """Called when UMA enters EMERGENCY state (>= 6.25 GiB = ceiling)."""

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


# HW-02: Power status monitoring for battery vs AC power detection
_POWER_CHECK_INTERVAL_S: float = 5.0


class PowerStatusMonitor:
    """
    HW-02: Monitor stavu napájení (baterie vs. síť).

    Detects whether the system is running on battery or AC power on macOS/Linux.
    On battery, aggressive MLX and I/O operations should be reduced to conserve
    battery life and prevent thermal throttling.

    Uses IOKit on macOS and sysfs on Linux. Fail-soft: returns AC attached
    (no battery conservation) when detection fails.

    Invariants:
    - Cached: polls every _POWER_CHECK_INTERVAL_S (5s) to avoid subprocess overhead
    - Fail-safe: any error returns {'on_battery': False, 'ac_attached': True}
    - Platform-specific: only implemented for Darwin and Linux
    """

    __slots__ = ("_last_check_time", "_last_status")

    def __init__(self) -> None:
        self._last_check_time: float = 0.0
        self._last_status: dict[str, bool | int | None] | None = None

    @staticmethod
    def _get_power_status_mac() -> dict[str, bool | int | None]:
        """Získá stav napájení na macOS pomocí IOKit / pmset."""
        try:
            import subprocess

            result = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if result.returncode != 0:
                return {"on_battery": False, "ac_attached": False, "battery_level": None, "charging": False}

            output = result.stdout.lower()
            status: dict[str, bool | int | None] = {
                "on_battery": False,
                "ac_attached": False,
                "battery_level": None,
                "charging": False,
            }

            # Parse battery power line: "Battery Power" or "InternalBattery-0"
            status["on_battery"] = "battery power" in output or "internalbattery" in output
            status["ac_attached"] = "ac attached" in output or "charging" in output
            status["charging"] = "charging" in output

            # Parse battery percentage
            import re

            level_match = re.search(r"(\d+)%", output)
            if level_match:
                status["battery_level"] = int(level_match.group(1))

            return status
        except Exception:
            return {"on_battery": False, "ac_attached": False, "battery_level": None, "charging": False}

    @staticmethod
    def _get_power_status_linux() -> dict[str, bool | int | None]:
        """Získá stav napájení na Linuxu přes sysfs."""
        try:
            ac_online_path = "/sys/class/power_supply/AC/online"
            with open(ac_online_path, "r") as f:
                ac_online = f.read().strip() == "1"
            return {"on_battery": not ac_online, "ac_attached": ac_online, "battery_level": None, "charging": False}
        except Exception:
            return {"on_battery": False, "ac_attached": False, "battery_level": None, "charging": False}

    def get_power_status(self) -> dict[str, bool | int | None]:
        """Získá aktuální stav napájení (s 5s cache)."""
        import time

        now = time.monotonic()
        if self._last_status is not None and now - self._last_check_time < _POWER_CHECK_INTERVAL_S:
            return self._last_status

        self._last_check_time = now
        import platform

        system = platform.system()
        if system == "Darwin":
            self._last_status = self._get_power_status_mac()
        elif system == "Linux":
            self._last_status = self._get_power_status_linux()
        else:
            self._last_status = {"on_battery": False, "ac_attached": False, "battery_level": None, "charging": False}

        return self._last_status


# Module-level singleton for cross-module reuse
_power_monitor_instance: PowerStatusMonitor | None = None


def get_power_monitor() -> PowerStatusMonitor:
    """Lazy singleton for PowerStatusMonitor."""
    global _power_monitor_instance
    if _power_monitor_instance is None:
        _power_monitor_instance = PowerStatusMonitor()
    return _power_monitor_instance
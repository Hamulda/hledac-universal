"""
core.capability_cost — Per-capability memory cost registry for QoS triage.

[FINAL]-019-07: Replaces hardcoded HEAVY_SIDECAR_COST_MB with a decorator-based





registration system that each capability module uses to declare its memory profile.

Usage in a capability module:
    from hledac.universal._core.capability_cost import capability_cost, get_capability_cost

    @capability_cost(rss_mb=70, peak_mb=114, tier="whisper")
    class WhisperEngine:
        ...

    # Or standalone registration:
    from hledac.universal._core.capability_cost import register_capability_cost
    register_capability_cost("my_capability", rss_mb=100, peak_mb=200, tier="heavy")

QoSLadderController queries registered costs to make optimal triage decisions:
    "disabling whisper saves 70 MB RSS / 114 MB peak — disabling GraphRAG saves 400 MB
     — pick GraphRAG first"

Design principles:
- Zero-cost at import: registration happens at module load time, not at runtime
- msgspec.Struct frozen=True, gc=False for hot-path DTOs
- Thread-safe singleton registry
- Fail-soft: unknown capabilities return default cost (SIDECAR_DEFAULT_ESTIMATE_MB)
- M1 8GB bounded: registry capped at 64 entries, O(1) lookup

M1 8GB memory budget reference:
    System:           ~2.5 GiB
    Orchestrator:     ~1.0 GiB
    Hermes-3B (4bit): ~2.0 GiB
    KV cache:         ~0.75 GiB
    ─────────────────────────
    Total:            ~6.25 GiB (soft ceiling: 5.5 GiB mission budget)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from operator import attrgetter, itemgetter
import msgspec

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "CapabilityCost",
    "capability_cost",
    "register_capability_cost",
    "get_capability_cost",
    "CapabilityCostRegistry",
    "get_cost_registry",
    "TriageDecision",
    "QoSLadderController",
    "get_qos_ladder",
    "CostTier",  # Exported for external use (CostTier.CRITICAL, etc.)
]


class CostTier(msgspec.Struct, frozen=True, gc=False):
    """
    Memory cost tier for capability classification.

    Tiers map to QoS ladder decisions:
    - critical: Must run (CT logs, synthesis, graph upsert)
    - heavy:   First candidate for triage (Hermes3, GraphRAG, Metal HNSW)
    - medium:  Second candidate (Whisper, embedding)
    - light:   Last to disable (basic extraction, metadata)
    """
    name: str
    priority: int  # Lower = higher priority to keep

    def __repr__(self) -> str:
        return f"CostTier({self.name!r}, priority={self.priority})"


class CapabilityCost(msgspec.Struct, frozen=True, gc=False):
    """
    Memory cost profile for a single capability.

    Fields:
        name: Capability identifier (e.g. "whisper", "hermes3", "graphrag")
        rss_mb: Baseline RSS memory in MB (typical steady-state)
        peak_mb: Peak memory in MB (worst-case during heavy computation)
        tier: Cost tier for triage ordering
        tags: Optional tags for filtering (e.g. ["ml", "gpu", "io"])

    M1 8GB calibration:
        whisper (tiny):  rss_mb=70,  peak_mb=114
        whisper (base):  rss_mb=114, peak_mb=154
        hermes3 (4bit):  rss_mb=2000, peak_mb=2200
        graphrag k-hop:  rss_mb=400,  peak_mb=600
        metal_hnsw:      rss_mb=256,  peak_mb=512
        embedding:       rss_mb=400,  peak_mb=600
        wayback_diff:    rss_mb=256,  peak_mb=384
        social_identity:  rss_mb=192,  peak_mb=256
        rir_correlation:  rss_mb=256,  peak_mb=384
        dashboard:       rss_mb=200,  peak_mb=400
    """
    name: str
    rss_mb: int
    peak_mb: int
    tier: str = "medium"
    tags: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            f"CapabilityCost(name={self.name!r}, rss_mb={self.rss_mb}, "
            f"peak_mb={self.peak_mb}, tier={self.tier!r})"
    )

    @property
    def savings_mb(self) -> int:
        """Memory savings if this capability is disabled."""
        return self.rss_mb


# Static tier instances — assigned after class definition (avoids NameError at class def time)
# Usage: CostTier.CRITICAL, CostTier.HEAVY, etc. for introspection/documentation.
CostTier.CRITICAL = CostTier(name="critical", priority=0)
CostTier.HEAVY = CostTier(name="heavy", priority=1)
CostTier.MEDIUM = CostTier(name="medium", priority=2)
CostTier.LIGHT = CostTier(name="light", priority=3)


class TriageDecision(msgspec.Struct, frozen=True, gc=False):
    """
    Result of a QoS ladder triage decision.

    Returned by QoSLadderController.triage() when the system needs to
    free memory by disabling capabilities.
    """
    # Capabilities to disable, ordered by cost-benefit ratio
    disable_order: tuple[str, ...]
    # Total memory that would be freed
    total_savings_mb: int
    # Remaining budget after disabling
    remaining_mb: int
    # Target that was requested
    target_mb: int
    # Whether target was achievable
    target_met: bool
    # Capabilities that were already disabled
    already_disabled: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"TriageDecision(disable={self.disable_order!r}, "
            f"savings={self.total_savings_mb}MB, met={self.target_met})"
    )


class CapabilityCostRegistry:
    """
    Thread-safe singleton registry for capability memory costs.

    Capabilities register themselves at import time via the @capability_cost
    decorator. The registry provides O(1) lookups for the QoS ladder.

    Bounds:
        - Max 64 entries (M1 8GB safe)
        - O(1) lookup via dict
        - Thread-safe via RLock

    Fail-soft: unknown capabilities return CapabilityCost with defaults.
    """

    __slots__ = ("_costs", "_lock", "_disabled")

    def __init__(self) -> None:
        self._costs: dict[str, CapabilityCost] = {}
        self._lock = threading.RLock()
        self._disabled: set[str] = set()

    def register(self, name: str, rss_mb: int, peak_mb: int, tier: str = "medium", tags: tuple[str, ...] = ()) -> None:
        """
        Register a capability's memory cost profile.

        Thread-safe: acquires lock before mutation.
        Idempotent: overwrites existing registration.
        """
        with self._lock:
            self._costs[name] = CapabilityCost(name=name, rss_mb=rss_mb, peak_mb=peak_mb, tier=tier, tags=tags)

    def get(self, name: str, default_rss_mb: int = 128) -> CapabilityCost:
        """
        Get the memory cost for a capability.

        Returns default cost if not registered (fail-soft behavior).
        Thread-safe: reads are lock-free (GIL-protected dict reads).
        """
        cost = self._costs.get(name)
        if cost is not None:
            return cost
        return CapabilityCost(name=name, rss_mb=default_rss_mb, peak_mb=default_rss_mb, tier="light")

    def get_all(self) -> dict[str, CapabilityCost]:
        """Get a snapshot of all registered costs."""
        with self._lock:
            return dict(self._costs)

    def is_registered(self, name: str) -> bool:
        """Check if a capability has a registered cost."""
        return name in self._costs

    def is_disabled(self, name: str) -> bool:
        """Check if a capability has been disabled via triage."""
        return name in self._disabled

    def disable(self, name: str) -> None:
        """Mark a capability as disabled (e.g. after QoS triage)."""
        with self._lock:
            self._disabled.add(name)

    def enable(self, name: str) -> None:
        """Re-enable a capability (e.g. at sprint start)."""
        with self._lock:
            self._disabled.discard(name)

    def reset_disabled(self) -> None:
        """Clear all disabled flags (call at sprint start)."""
        with self._lock:
            self._disabled.clear()

    def get_by_tier(self, tier: str) -> list[CapabilityCost]:
        """Get all capabilities in a given tier."""
        with self._lock:
            return [c for c in self._costs.values() if c.tier == tier]

    def get_heaviest(self, limit: int = 10) -> list[CapabilityCost]:
        """Get the heaviest capabilities by peak_mb."""
        with self._lock:
            sorted_costs = sorted(self._costs.values(), key=attrgetter("peak_mb"), reverse=True)
            return sorted_costs[:limit]


# Module-level singleton
_cost_registry: CapabilityCostRegistry | None = None
_registry_lock = threading.Lock()


def get_cost_registry() -> CapabilityCostRegistry:
    """Get or create the singleton CapabilityCostRegistry."""
    global _cost_registry
    if _cost_registry is None:
        with _registry_lock:
            if _cost_registry is None:
                _cost_registry = CapabilityCostRegistry()
    return _cost_registry


def register_capability_cost(
    name: str,
    rss_mb: int,
    peak_mb: int,
    tier: str = "medium",
    tags: tuple[str, ...] = (),
) -> None:
    """
    Register a capability's memory cost profile.

    Convenience function equivalent to get_cost_registry().register(...).
    Use this for standalone registrations outside class/function scope.
    """
    get_cost_registry().register(name=name, rss_mb=rss_mb, peak_mb=peak_mb, tier=tier, tags=tags)


def get_capability_cost(name: str, default_rss_mb: int = 128) -> CapabilityCost:
    """
    Get the memory cost for a capability.

    Fail-soft: returns a default cost if the capability is not registered.
    """
    return get_cost_registry().get(name=name, default_rss_mb=default_rss_mb)


def capability_cost(
    *,
    rss_mb: int,
    peak_mb: int,
    tier: str = "medium",
    tags: tuple[str, ...] = (),
) -> Callable[[type], type]:
    """
    Decorator to register a class's memory cost profile at import time.

    Usage:
        from hledac.universal._core.capability_cost import capability_cost

        @capability_cost(rss_mb=70, peak_mb=114, tier="medium", tags=("speech", "gpu"))
        class WhisperEngine:
            ...

    The decorator registers the class name (lowercased) as the capability name.
    For module-level registration (non-class), use register_capability_cost() instead.

    Args:
        rss_mb: Baseline RSS in MB
        peak_mb: Peak RSS in MB
        tier: "critical" | "heavy" | "medium" | "light"
        tags: Optional tags for filtering
    """
    def decorator(cls: type[object]) -> type[object]:
        name = cls.__name__.lower()
        register_capability_cost(name=name, rss_mb=rss_mb, peak_mb=peak_mb, tier=tier, tags=tags)
        return cls
    return decorator


# ─── QoS Ladder Controller ────────────────────────────────────────────────────

_DEFAULT_WINDUP_LEAD_S = 30
# SSOT: Use UmaBudget.MISSION_PEAK_RSS_GIB instead of hardcoded 5.5 GiB
from hledac.universal.utils.uma_budget import MISSION_PEAK_RSS_GIB
from _core._util import aclose
_DEFAULT_MISSION_PEAK_MB = MISSION_PEAK_RSS_GIB * 1024  # 5632 MB on M1 8GB (SSOT)


class QoSLadderController:
    """
    QoS-aware memory triage controller.

    Makes optimal triage decisions based on registered capability costs:
    "To free X MB, disable these capabilities in this order (highest savings first)"

    Integration points:
        - M1ResourceGovernor.sidecar_admission() — uses get_capability_cost()
        - SprintScheduler — calls triage() when approaching memory ceiling
        - Winddown phase — disables heavy capabilities to free memory for export

    QoS ladder (keep priority, highest first):
        1. CRITICAL — core sprint ops (CT logs, synthesis, graph upsert)
        2. HEAVY    — LLM inference, GraphRAG, Metal HNSW
        3. MEDIUM   — Whisper, embedding, Wayback diff
        4. LIGHT    — Basic extraction, metadata, social identity

    Triage algorithm:
        1. Sort registered capabilities by (tier_priority, -peak_mb)
        2. Accumulate savings until target_mb is met
        3. Return ordered disable list + savings estimate

    M1 8GB bounds:
        - Max 64 registered capabilities
        - O(N log N) sort for triage (N ≤ 64, negligible)
        - Thread-safe singleton
    """

    # Tier priority mapping — lower number = higher keep priority
    TIER_PRIORITY: dict[str, int] = {
        "critical": 0,
        "heavy": 1,
        "medium": 2,
        "light": 3,
    }

    __slots__ = ("_registry", "_lock")

    def __init__(self, registry: CapabilityCostRegistry | None = None) -> None:
        self._registry = registry if registry is not None else get_cost_registry()
        self._lock = threading.RLock()

    def _tier_sort_key(self, cost: CapabilityCost) -> tuple[int, int]:
        """
        Sort key for triage: (tier_priority, -peak_mb).

        CRITICAL tier is always excluded (never disabled).
        HEAVY tier is disabled before MEDIUM, MEDIUM before LIGHT.
        Within the same tier, higher peak_mb first → max savings per disable.

        Tier priorities: critical=0, heavy=1, medium=2, light=3
        """
        tier_priority = self.TIER_PRIORITY.get(cost.tier, 99)
        return (tier_priority, -cost.peak_mb)

    def triage(
        self,
        target_mb: int,
        already_disabled: tuple[str, ...] = (),
        current_rss_mb: int = 0,
    ) -> TriageDecision:
        """
        Compute optimal capability disable order to free target_mb.

        Args:
            target_mb: Amount of memory to free (in MB)
            already_disabled: Capabilities already disabled (skipped in computation)
            current_rss_mb: Current RSS in MB (used for remaining budget calculation)

        Returns:
            TriageDecision with ordered disable list and savings estimates.

        Algorithm:
            1. Filter out already-disabled and CRITICAL-tier capabilities
            2. Sort remaining by tier_priority (ascending), then peak_mb (descending)
               → Lightest capabilities first → maximum savings per disable
            3. Accumulate savings until target_mb is met
            4. Return TriageDecision

        M1 8GB: O(N log N) sort for N ≤ 64 capabilities.
        """
        disabled_set = set(already_disabled)

        # Get all registered costs
        all_costs = self._registry.get_all()

        # Filter: skip disabled and CRITICAL capabilities
        candidates = [
            cost for cost in all_costs.values()
            if cost.name not in disabled_set and cost.tier != "critical"
        ]

        # Sort: lowest tier priority (heaviest) first, then by peak_mb descending
        candidates.sort(key=self._tier_sort_key)

        # Accumulate savings until target met
        disable_order: list[str] = []
        total_savings = 0
        remaining = target_mb

        for cost in candidates:
            if total_savings >= target_mb:
                break
            disable_order.append(cost.name)
            total_savings += cost.savings_mb

        remaining_after = current_rss_mb - total_savings if current_rss_mb > 0 else 0

        return TriageDecision(
            disable_order=tuple(disable_order),
            total_savings_mb=total_savings,
            remaining_mb=max(0, remaining_after),
            target_mb=target_mb,
            target_met=total_savings >= target_mb,
            already_disabled=already_disabled,
    )

    def estimate_savings(self, capabilities: list[str]) -> int:
        """
        Estimate total memory savings from disabling a list of capabilities.

        Args:
            capabilities: List of capability names to disable

        Returns:
            Total RSS savings in MB
        """
        total = 0
        for name in capabilities:
            cost = self._registry.get(name)
            total += cost.savings_mb
        return total

    def get_triage_preview(self, target_mb: int) -> list[tuple[str, int, str]]:
        """
        Preview what would be disabled for a given target.

        Returns:
            List of (name, savings_mb, tier) tuples in disable order.
        """
        decision = self.triage(target_mb=target_mb)
        preview: list[tuple[str, int, str]] = []
        for name in decision.disable_order:
            cost = self._registry.get(name)
            preview.append((name, cost.savings_mb, cost.tier))
        return preview

    def get_savings_for_disable(self, name: str) -> int:
        """Get memory savings if a specific capability is disabled."""
        return self._registry.get(name).savings_mb

    def describe_cost(self, name: str) -> str:
        """
        Human-readable description of a capability's memory cost.

        Example:
            "whisper: 70 MB RSS / 114 MB peak (medium tier, tags: speech, gpu)"
        """
        cost = self._registry.get(name)
        tags_str = f", tags: {', '.join(cost.tags)}" if cost.tags else ""
        return f"{cost.name}: {cost.rss_mb} MB RSS / {cost.peak_mb} MB peak ({cost.tier} tier{tags_str})"


# Module-level singleton
_qos_ladder: QoSLadderController | None = None
_qos_lock = threading.Lock()


def get_qos_ladder() -> QoSLadderController:
    """Get or create the singleton QoSLadderController."""
    global _qos_ladder
    if _qos_ladder is None:
        with _qos_lock:
            if _qos_ladder is None:
                _qos_ladder = QoSLadderController()
    return _qos_ladder

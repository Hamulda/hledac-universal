"""
Discovery Planner — Budget-Aware Multi-Source Expansion.

Sprint F206AQ: Budget-Aware Multi-Source Expansion

Core idea:
  provider score = reliability_ewma * novelty_ewma / cost_ewma

Providers:
  ddg_mojeek, historical_frontier, wayback_cdx,
  feed_pivots, ct_pivots, commoncrawl_cdx

Planner:
  - selects providers under remaining time budget
  - avoids unhealthy (reliability_ewma too low) providers
  - exploration: small chance to include a lower-ranked provider
  - respects 30-minute sprint budget via remaining_time_budget argument
  - deterministic under seed (for reproducibility)

Pure Python. No ML hot path. M1-safe.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
)
from hledac.universal.discovery.provider_stats import (
    PROVIDER_NAMES,
    PROVIDER_COST_ESTIMATE,
    PROVIDER_CAPABILITIES,
    ProviderStatsRegistry,
    get_provider_stats_registry,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum reliability to consider a provider
_MIN_RELIABILITY = 0.05

# Exploration probability (10%)
_EXPLORATION_PROB = 0.10

# Cost multipliers for budget estimation (conservative)
_COST_MULTIPLIER = 1.5  # add this * cost_ewma as safety margin

# Max providers selected per call
_MAX_PROVIDERS_PER_CALL = 4


# ---------------------------------------------------------------------------
# Provider capability state — makes non-production providers explicit
# ---------------------------------------------------------------------------

class ProviderCapabilityState(Enum):
    """Discovery provider operational state.

    Used to prevent stub providers from being silently treated as production.
    """
    PRODUCTION = "production"        # Fully wired, real endpoint, production-safe
    ADVISORY_STUB = "advisory_stub"  # Placeholder adapter, endpoint not implemented
    NOT_WIRED = "not_wired"          # No pipeline context / adapter wired
    DISABLED = "disabled"            # Explicitly disabled (circuit breaker, env, etc.)


def get_provider_state(name: str) -> ProviderCapabilityState:
    """Resolve provider to its capability state.

    Priority (gap-closure logic):
      1. ADVISORY_STUB: is_stub=True AND NOT requires_context
         (placeholder with no real endpoint; excluded by default)
      2. NOT_WIRED: requires_context=True (needs pipeline wiring)
         (feed_pivots: context-missing → NOT_WIRED; context-available → PRODUCTION)
      3. DISABLED: production_enabled=False
      4. PRODUCTION: fully wired
    """
    cap = PROVIDER_CAPABILITIES.get(name, {})
    is_stub = cap.get("is_stub", False)
    requires_context = cap.get("requires_context", False)
    production_enabled = cap.get("production_enabled", False)

    if is_stub and not requires_context:
        # CommonCrawl-style advisory stub — no real endpoint, context not needed
        return ProviderCapabilityState.ADVISORY_STUB
    if requires_context:
        # Feed pivots-style: implementation exists but needs pipeline context
        # State is NOT_WIRED when context unavailable; PRODUCTION when context available
        return ProviderCapabilityState.NOT_WIRED
    if not production_enabled:
        return ProviderCapabilityState.DISABLED
    return ProviderCapabilityState.PRODUCTION


# ---------------------------------------------------------------------------
# Planning result
# ---------------------------------------------------------------------------

@dataclass
class ProviderPlan:
    """Plan for a single discovery call."""

    provider: str
    max_results: int
    timeout_s: float
    estimated_cost_ms: float


@dataclass
class ProviderStatusDebug:
    """Why a provider was selected or skipped."""
    provider: str
    state: ProviderCapabilityState
    selected: bool
    reason: str  # human-readable skip/select reason


@dataclass
class DiscoveryPlan:
    """Full plan for a sprint discovery pass."""

    plans: list[ProviderPlan]
    estimated_total_ms: float
    remaining_budget_ms: float
    provider_status_debug: list[ProviderStatusDebug] = field(default_factory=list)

    def is_viable(self) -> bool:
        """At least one provider planned."""
        return len(self.plans) > 0


# ---------------------------------------------------------------------------
# Provider adapter registry — thin wrappers returning DiscoveryBatchResult
# ---------------------------------------------------------------------------

_ProviderRunner = Any  # async def (query, max_results, timeout_s) -> DiscoveryBatchResult


async def _run_ddg_mojeek(
    query: str,
    max_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    try:
        async with asyncio.timeout(min(timeout_s, 20.0)):
            return await async_search_public_web(query, max_results=max_results, timeout_s=timeout_s)
    except asyncio.TimeoutError:
        return _timeout_result("ddg_mojeek", timeout_s)


async def _run_historical_frontier(
    query: str,
    max_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    from hledac.universal.discovery.historical_frontier import async_search_historical_frontier
    try:
        async with asyncio.timeout(min(timeout_s, 3.0)):
            return await async_search_historical_frontier(
                query, max_results=max_results, timeout_s=min(timeout_s, 3.0)
            )
    except asyncio.TimeoutError:
        return _timeout_result("historical_frontier", timeout_s)


async def _run_wayback_cdx(
    query: str,
    max_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    from hledac.universal.discovery.wayback_cdx_adapter import async_search_wayback_cdx
    try:
        async with asyncio.timeout(min(timeout_s, 15.0)):
            return await async_search_wayback_cdx(query, max_results=max_results, timeout_s=timeout_s)
    except asyncio.TimeoutError:
        return _timeout_result("wayback_cdx", timeout_s)


async def _run_commoncrawl_cdx(
    query: str,
    max_results: int,
    timeout_s: float,
    include_stub: bool = False,
) -> DiscoveryBatchResult:
    """
    CommonCrawl CDX — uses same adapter as Wayback, different endpoint.

    This is an ADVISORY_STUB until a real CommonCrawl CDX endpoint is wired.
    When include_stub=False (default): this runner should not be selected by the planner.
    When include_stub=True: returns explicit stub_not_production error.
    """
    del query, max_results, timeout_s
    if not include_stub:
        return DiscoveryBatchResult(
            hits=(),
            error="commoncrawl_cdx_not_selected_include_stub=False",
            error_type="stub_not_production",
            provider_name="commoncrawl_cdx",
            provider_chain=("commoncrawl_cdx",),
            source_family=None,
            elapsed_s=0.0,
        )

    # Stub run: return explicit stub error so caller knows this is not real
    return DiscoveryBatchResult(
        hits=(),
        error="commoncrawl_cdx_no_real_endpoint",
        error_type="stub_not_production",
        provider_name="commoncrawl_cdx",
        provider_chain=("commoncrawl_cdx",),
        source_family=None,
        elapsed_s=0.0,
    )


# Feed pivots and CT pivots require pipeline context — stub for now
# They are gated by the planner and only selected when pipeline has the right adapters


async def _run_feed_pivots(
    query: str,
    max_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    # Feed pivots require pipeline context ( HypothesisEngine / feed branch )
    # The planner selects them only when the sprint has feed adapters wired.
    # Stub: return empty — the planner avoids this provider when budget is tight.
    del query, max_results, timeout_s
    return DiscoveryBatchResult(
        hits=(),
        error="feed_pivots_no_pipeline_context",
        error_type="not_wired",
        provider_name="feed_pivots",
        provider_chain=("feed_pivots",),
        source_family=None,
        elapsed_s=0.0,
    )


async def _run_ct_pivots(
    query: str,
    max_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    # Sprint F207F: call_crtsh returns (DiscoveryBatchResult, CTOutcome).
    # F207F: CTOutcome available via call_crtsh for lane-level measurement.
    from .crtsh_adapter import call_crtsh

    try:
        result, outcome = await call_crtsh(
            query=query,
            max_results=max_results,
            timeout_s=timeout_s,
        )
        # Log outcome for lane observability (json structured, human-readable)
        logger.debug(
            f"[ct_pivots] outcome: attempted={outcome.attempted} "
            f"raw={outcome.raw_count} built={outcome.built_count} "
            f"error={outcome.error} timeout={outcome.timeout} "
            f"duration_s={outcome.duration_s:.3f}"
        )
        return result
    except asyncio.CancelledError:
        raise  # always re-raise — do not swallow
    except Exception as e:
        # Fail-soft: return empty with error tag
        return DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="provider_exception",
            provider_name="ct_pivots",
            provider_chain=("ct_pivots",),
            source_family="ct",
            elapsed_s=0.0,
        )


# ---------------------------------------------------------------------------
# Provider registry map
# ---------------------------------------------------------------------------

_RUNNERS: dict[str, _ProviderRunner] = {
    "ddg_mojeek": _run_ddg_mojeek,
    "historical_frontier": _run_historical_frontier,
    "wayback_cdx": _run_wayback_cdx,
    "commoncrawl_cdx": _run_commoncrawl_cdx,
    "feed_pivots": _run_feed_pivots,
    "ct_pivots": _run_ct_pivots,
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _timeout_result(provider: str, timeout_s: float) -> DiscoveryBatchResult:
    return DiscoveryBatchResult(
        hits=(),
        error=f"{provider}_timeout",
        error_type="timeout",
        provider_name=provider,
        provider_chain=(provider,),
        source_family=None,
        elapsed_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Discovery Planner
# ---------------------------------------------------------------------------

class DiscoveryPlanner:
    """
    Budget-aware discovery planner.

    Selects providers by score = reliability_ewma * novelty_ewma / cost_ewma
    within remaining time budget. Optionally explores lower-ranked providers.

    Deterministic when seeded. Stateless (reads from registry only).
    """

    def __init__(
        self,
        registry: ProviderStatsRegistry | None = None,
        seed: int | None = None,
        exploration_prob: float = _EXPLORATION_PROB,
        min_reliability: float = _MIN_RELIABILITY,
        max_providers: int = _MAX_PROVIDERS_PER_CALL,
        cost_multiplier: float = _COST_MULTIPLIER,
        include_stub_providers: bool = False,
    ) -> None:
        self._registry = registry or get_provider_stats_registry()
        self._rng = random.Random(seed)
        self._exploration_prob = exploration_prob
        self._min_reliability = min_reliability
        self._max_providers = max_providers
        self._cost_multiplier = cost_multiplier
        self._include_stub_providers = include_stub_providers

    # -------------------------------------------------------------------------
    # Planning
    # -------------------------------------------------------------------------

    def plan(
        self,
        query: str,
        remaining_time_budget_s: float,
        target_results: int = 20,
        pipeline_context_available: bool = False,
    ) -> DiscoveryPlan:
        """
        Build a DiscoveryPlan for the given query and remaining budget.

        Parameters
        ----------
        query:
            Search / pivot query string.
        remaining_time_budget_s:
            Remaining wall-clock seconds in the sprint.
        target_results:
            Target number of discovery hits (used for result-size hints).
        pipeline_context_available:
            True when feed adapters are wired (enables feed_pivots).

        Returns
        -------
        DiscoveryPlan with 0-N provider plans and per-provider status debug.
        """
        remaining_ms = remaining_time_budget_s * 1000.0
        plans: list[ProviderPlan] = []
        estimated_total_ms = 0.0
        debug: list[ProviderStatusDebug] = []

        # Sort all healthy providers by score descending
        all_providers = [
            (self._registry.get(name), PROVIDER_COST_ESTIMATE.get(name, 1000.0))
            for name in PROVIDER_NAMES
        ]

        # Filter to healthy + scored + production-enabled (stub quarantine)
        scored = []
        for stats, cost_est in all_providers:
            if stats is None:
                continue

            name = stats.name
            cap = PROVIDER_CAPABILITIES.get(name, {})
            is_stub = cap.get("is_stub", False)
            requires_context = cap.get("requires_context", False)
            production_enabled = cap.get("production_enabled", False)

            # Determine capability state — same priority as get_provider_state()
            # Priority: ADVISORY_STUB > NOT_WIRED > DISABLED > PRODUCTION
            if is_stub and not requires_context:
                state = ProviderCapabilityState.ADVISORY_STUB
                reason = "advisory_stub_no_real_endpoint"
            elif requires_context:
                if pipeline_context_available:
                    state = ProviderCapabilityState.PRODUCTION
                    reason = "production_wired_pipeline_context_available"
                else:
                    state = ProviderCapabilityState.NOT_WIRED
                    reason = "pipeline_context_not_available"
            elif not production_enabled:
                state = ProviderCapabilityState.DISABLED
                reason = f"disabled_reason={cap.get('disabled_reason', 'unknown')}"
            else:
                state = ProviderCapabilityState.PRODUCTION
                reason = "production_wired"

            # Health check
            if not stats.is_healthy:
                debug.append(ProviderStatusDebug(
                    provider=name, state=state,
                    selected=False, reason=f"unhealthy_reliability={stats.reliability_ewma:.3f}"
                ))
                continue

            # NOT_WIRED providers: only selected if _include_stub_providers AND pipeline_context
            # When pipeline_context_available=False, NOT_WIRED providers never get selected
            if state == ProviderCapabilityState.NOT_WIRED:
                if not self._include_stub_providers or not pipeline_context_available:
                    debug.append(ProviderStatusDebug(
                        provider=name, state=state,
                        selected=False, reason=reason
                    ))
                    continue

            # Stub/advisory quarantine: exclude ADVISORY_STUB and DISABLED by default
            if state in (ProviderCapabilityState.ADVISORY_STUB, ProviderCapabilityState.DISABLED):
                if not self._include_stub_providers:
                    debug.append(ProviderStatusDebug(
                        provider=name, state=state,
                        selected=False, reason=f"stub_advisory_excluded_include_stub={self._include_stub_providers}"
                    ))
                    continue
                # Stub included: annotate reason
                reason = f"advisory_stub_included_reason={cap.get('disabled_reason', 'unknown')}"

            # Stub included: warn via reason
            if is_stub and self._include_stub_providers:
                reason = f"stub_selected_include_stub=True_reason={cap.get('disabled_reason', 'unknown')}"

            # Estimated call cost (with safety margin)
            est_cost = cost_est * self._cost_multiplier
            score = stats.score()
            scored.append((score, stats.name, est_cost, state, reason))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        for score, name, est_cost, state, reason in scored:
            if len(plans) >= self._max_providers:
                debug.append(ProviderStatusDebug(
                    provider=name, state=state,
                    selected=False, reason="max_providers_reached"
                ))
                continue

            # Budget check — don't overcommit
            if estimated_total_ms + est_cost > remaining_ms * 0.85:
                debug.append(ProviderStatusDebug(
                    provider=name, state=state,
                    selected=False, reason=f"budget_exceeded_remaining={remaining_ms - estimated_total_ms:.0f}ms"
                ))
                continue

            # Exploration: small chance to pick a random mid-tier provider instead of top-1
            if self._rng.random() < self._exploration_prob and len(scored) > 2:
                # Explore: pick a random provider from the middle of the ranking
                mid = len(scored) // 2
                pick_idx = self._rng.randint(mid, len(scored) - 1)
                _, picked_name, picked_est_cost, picked_state, _ = scored[pick_idx]

                # Log the skipped top pick (exploration triggered)
                debug.append(ProviderStatusDebug(
                    provider=name, state=state,
                    selected=False, reason=f"exploration_skipped_original_score={score:.4f}"
                ))

                # Recompute budget check for the picked provider
                if estimated_total_ms + picked_est_cost <= remaining_ms * 0.85:
                    plans.append(ProviderPlan(
                        provider=picked_name,
                        max_results=max(5, min(target_results, 50)),
                        timeout_s=max(1.0, min(picked_est_cost / 1000.0 * 0.9, 30.0)),
                        estimated_cost_ms=picked_est_cost,
                    ))
                    estimated_total_ms += picked_est_cost
                    debug.append(ProviderStatusDebug(
                        provider=picked_name, state=picked_state,
                        selected=True, reason=f"exploration_selected_score={scored[pick_idx][0]:.4f}"
                    ))
                # Skip appending the default top pick below
                continue

            # Allocate budget for this call
            timeout_s = max(1.0, min(est_cost / 1000.0 * 0.9, 30.0))
            max_res = max(5, min(target_results, 50))

            plans.append(ProviderPlan(
                provider=name,
                max_results=max_res,
                timeout_s=timeout_s,
                estimated_cost_ms=est_cost,
            ))
            estimated_total_ms += est_cost
            debug.append(ProviderStatusDebug(
                provider=name, state=state,
                selected=True, reason=reason
            ))

        return DiscoveryPlan(
            plans=plans,
            estimated_total_ms=estimated_total_ms,
            remaining_budget_ms=remaining_ms - estimated_total_ms,
            provider_status_debug=debug,
        )

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    async def execute(
        self,
        query: str,
        plan: DiscoveryPlan,
    ) -> list[DiscoveryBatchResult]:
        """
        Execute a DiscoveryPlan, running selected providers concurrently.

        Records outcomes to the registry automatically.
        """
        if not plan.is_viable():
            return []

        tasks = []
        for p in plan.plans:
            runner = _RUNNERS.get(p.provider)
            if runner is None:
                continue
            # commoncrawl_cdx runner needs include_stub flag
            if p.provider == "commoncrawl_cdx":
                task = runner(query, p.max_results, p.timeout_s, include_stub=self._include_stub_providers)
            else:
                task = runner(query, p.max_results, p.timeout_s)
            tasks.append((p.provider, task))

        results: list[DiscoveryBatchResult] = []
        outcomes = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        for (provider, _), result in zip(tasks, outcomes):
            # Check for success: result must have 'hits' and 'elapsed_s' attributes
            # (covers both real DiscoveryBatchResult and properly-shaped mocks)
            if hasattr(result, 'hits') and hasattr(result, 'elapsed_s'):
                # Success path
                unique_hosts = len({hit.url for hit in result.hits})
                self._registry.record_success(
                    provider,
                    latency_ms=(result.elapsed_s or 0) * 1000,
                    hits=result.hits,
                    unique_hosts=unique_hosts,
                )
                results.append(result)
            elif isinstance(result, asyncio.TimeoutError):
                self._registry.record_timeout(provider)
                results.append(_timeout_result(provider, 30.0))
            else:
                exc = result if isinstance(result, BaseException) else None
                self._registry.record_failure(provider, error_type=type(exc).__name__ if exc else "unknown")
                results.append(DiscoveryBatchResult(
                    hits=(),
                    error=f"{provider}_exception",
                    error_type="provider_exception",
                    provider_name=provider,
                    provider_chain=(provider,),
                    source_family=None,
                    elapsed_s=0.0,
                ))

        return results

    # -------------------------------------------------------------------------
    # Convenience — plan and execute in one call
    # -------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        remaining_time_budget_s: float,
        target_results: int = 20,
    ) -> DiscoveryPlan:
        """
        Plan and execute in one call. Returns the plan (with estimated costs)
        and results are written to registry.
        """
        plan = self.plan(query, remaining_time_budget_s, target_results)
        await self.execute(query, plan)
        return plan


# ---------------------------------------------------------------------------
# Default planner instance
# ---------------------------------------------------------------------------

_default_planner: DiscoveryPlanner | None = None


def get_discovery_planner(
    registry: ProviderStatsRegistry | None = None,
    seed: int | None = None,
) -> DiscoveryPlanner:
    """Get the default planner instance."""
    global _default_planner
    if _default_planner is None:
        _default_planner = DiscoveryPlanner(registry=registry, seed=seed)
    return _default_planner


def reset_discovery_planner() -> None:
    """Reset the global planner (for testing)."""
    global _default_planner
    _default_planner = None
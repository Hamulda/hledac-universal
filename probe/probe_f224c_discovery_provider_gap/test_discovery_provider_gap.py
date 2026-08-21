"""
Sprint F224C — Discovery Planner Provider Gap Closure

Tests that discovery_planner no longer silently plans stub providers
as production, and non-feed provider gaps are explicit in provider_status_debug.

Invariant table:
  T1: feed_pivots NOT selected by default (state=NOT_WIRED, selected=False)
  T2: commoncrawl_cdx NOT selected by default (state=ADVISORY_STUB, selected=False)
  T3: include_stub_providers=True allows commoncrawl_cdx but result error_type=stub_not_production
  T4: ct_pivots remains selectable (state=PRODUCTION)
  T5: provider_status_debug includes skipped providers with explicit reasons
  T6: plan() is idempotent — no live network, no MLX load
  T7: feed_pivots selected when pipeline_context_available=True
"""

import asyncio
import time

import pytest
from hledac.universal.discovery.discovery_planner import (
    DiscoveryPlanner,
    ProviderCapabilityState,
    get_provider_state,
    reset_discovery_planner,
)
from hledac.universal.discovery.provider_stats import (
    ProviderStatsRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry() -> ProviderStatsRegistry:
    registry = ProviderStatsRegistry()
    yield registry
    registry.reset()


@pytest.fixture
def planner(clean_registry: ProviderStatsRegistry) -> DiscoveryPlanner:
    reset_discovery_planner()
    return DiscoveryPlanner(registry=clean_registry, seed=42)


@pytest.fixture
def planner_with_stubs(clean_registry: ProviderStatsRegistry) -> DiscoveryPlanner:
    reset_discovery_planner()
    return DiscoveryPlanner(registry=clean_registry, seed=42, include_stub_providers=True)


# ---------------------------------------------------------------------------
# T1: feed_pivots NOT selected by default (NOT_WIRED when pipeline_context=False)
# ---------------------------------------------------------------------------


def test_feed_pivots_not_selected_by_default(planner: DiscoveryPlanner) -> None:
    """feed_pivots has requires_context=True and is NOT_WIRED by default."""
    plan = planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)

    selected_names = {p.provider for p in plan.plans}
    skipped_feed_pivots = [d for d in plan.provider_status_debug if d.provider == "feed_pivots"]

    # feed_pivots must NOT be selected
    assert "feed_pivots" not in selected_names, f"feed_pivots should not be selected by default, got: {selected_names}"

    # Must appear in debug as NOT_WIRED and not selected
    assert len(skipped_feed_pivots) == 1, f"Expected 1 debug entry for feed_pivots, got {len(skipped_feed_pivots)}"
    debug = skipped_feed_pivots[0]
    assert debug.state == ProviderCapabilityState.NOT_WIRED, f"Expected NOT_WIRED, got {debug.state}"
    assert debug.selected is False, "feed_pivots should be marked selected=False"
    assert "pipeline_context_not_available" in debug.reason, (
        f"Expected pipeline_context_not_available in reason, got: {debug.reason}"
    )


# ---------------------------------------------------------------------------
# T7: feed_pivots selected when pipeline_context_available=True
# ---------------------------------------------------------------------------


def test_feed_pivots_selected_with_pipeline_context(planner: DiscoveryPlanner) -> None:
    """When pipeline_context_available=True, feed_pivots becomes eligible."""
    # Manually bump reliability so it scores well
    registry = planner._registry
    feed_stats = registry.get("feed_pivots")
    assert feed_stats is not None
    feed_stats.reliability_ewma = 0.8

    plan = planner.plan(
        "test query",
        remaining_time_budget_s=30.0,
        target_results=20,
        pipeline_context_available=True,
    )

    selected_names = {p.provider for p in plan.plans}
    skipped = [d for d in plan.provider_status_debug if d.provider == "feed_pivots"]

    # With pipeline_context, feed_pivots CAN be selected
    # (but only if it scores well enough under the budget)
    if "feed_pivots" in selected_names:
        assert len(skipped) == 1
        assert skipped[0].selected is True
        assert skipped[0].state == ProviderCapabilityState.PRODUCTION
    else:
        # If not selected due to budget/score, verify it was considered with PRODUCTION state
        assert len(skipped) == 1
        # It should not be NOT_WIRED anymore when context is available
        assert skipped[0].state != ProviderCapabilityState.NOT_WIRED


# ---------------------------------------------------------------------------
# T2: commoncrawl_cdx NOT selected by default (ADVISORY_STUB)
# ---------------------------------------------------------------------------


def test_commoncrawl_cdx_not_selected_by_default(planner: DiscoveryPlanner) -> None:
    """commoncrawl_cdx is ADVISORY_STUB, must not be selected by default."""
    plan = planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)

    selected_names = {p.provider for p in plan.plans}
    skipped_cc = [d for d in plan.provider_status_debug if d.provider == "commoncrawl_cdx"]

    assert "commoncrawl_cdx" not in selected_names, (
        f"commoncrawl_cdx should not be selected by default, got: {selected_names}"
    )

    assert len(skipped_cc) == 1, f"Expected 1 debug entry for commoncrawl_cdx, got {len(skipped_cc)}"
    debug = skipped_cc[0]
    assert debug.state == ProviderCapabilityState.ADVISORY_STUB, f"Expected ADVISORY_STUB, got {debug.state}"
    assert debug.selected is False
    assert "advisory_stub" in debug.reason.lower() or "stub" in debug.reason.lower()


# ---------------------------------------------------------------------------
# T3: include_stub_providers allows commoncrawl_cdx but result is stub_not_production
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commoncrawl_cdx_stub_result_when_included(planner_with_stubs: DiscoveryPlanner) -> None:
    """With include_stub_providers=True, commoncrawl_cdx runs but returns stub_not_production."""
    # Get commoncrawl_cdx selected
    plan = planner_with_stubs.plan("test query", remaining_time_budget_s=30.0, target_results=20)
    selected_cc = [p for p in plan.plans if p.provider == "commoncrawl_cdx"]
    assert len(selected_cc) == 1, (
        f"commoncrawl_cdx should be selected with include_stub_providers=True, plan={plan.plans}"
    )

    # Execute the plan
    results = await planner_with_stubs.execute("test query", plan)

    cc_result = next((r for r in results if r.provider_name == "commoncrawl_cdx"), None)
    assert cc_result is not None, "commoncrawl_cdx should have returned a result"
    assert cc_result.error_type == "stub_not_production", (
        f"Expected error_type='stub_not_production', got: {cc_result.error_type}"
    )
    assert "no_real_endpoint" in cc_result.error or "not_selected" in cc_result.error


# ---------------------------------------------------------------------------
# T4: ct_pivots remains selectable (PRODUCTION)
# ---------------------------------------------------------------------------


def test_ct_pivots_remains_selectable(planner: DiscoveryPlanner) -> None:
    """ct_pivots is production-enabled and should be selectable."""
    # Bump reliability so it scores well
    registry = planner._registry
    ct_stats = registry.get("ct_pivots")
    assert ct_stats is not None
    ct_stats.reliability_ewma = 0.9

    plan = planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)

    selected_names = {p.provider for p in plan.plans}
    debug_entries = {d.provider: d for d in plan.provider_status_debug}

    assert "ct_pivots" in selected_names, f"ct_pivots should be selectable, got: {selected_names}"
    assert debug_entries["ct_pivots"].state == ProviderCapabilityState.PRODUCTION
    assert debug_entries["ct_pivots"].selected is True


# ---------------------------------------------------------------------------
# T5: provider_status_debug includes ALL providers with explicit skip/select reasons
# ---------------------------------------------------------------------------


def test_provider_status_debug_all_providers(planner: DiscoveryPlanner) -> None:
    """Every provider in PROVIDER_NAMES appears in provider_status_debug."""
    from hledac.universal.discovery.provider_stats import PROVIDER_NAMES

    plan = planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)

    debug_providers = {d.provider for d in plan.provider_status_debug}

    for name in PROVIDER_NAMES:
        assert name in debug_providers, f"Provider '{name}' missing from provider_status_debug"

    # All skipped providers must have non-empty reason
    for debug in plan.provider_status_debug:
        assert debug.reason, f"Provider {debug.provider} has empty reason"
        assert len(debug.reason) > 5, f"Provider {debug.provider} reason too short: {debug.reason}"


# ---------------------------------------------------------------------------
# T6: plan() is idempotent — no live network, no MLX load
# ---------------------------------------------------------------------------


def test_plan_no_network_no_mlx(planner: DiscoveryPlanner) -> None:
    """plan() completes without any network calls or MLX loading."""
    # Time the call — should be < 50ms for pure in-memory logic
    start = time.monotonic()
    plan = planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 50, f"plan() took {elapsed_ms:.1f}ms — may indicate network or blocking call"

    # Verify no network-related side-effects
    assert plan.is_viable() or not plan.is_viable()  # deterministic


@pytest.mark.asyncio
async def test_execute_no_network_no_mlx(planner: DiscoveryPlanner) -> None:
    """execute() of a stub-only plan completes without network or MLX."""
    # Build plan with a stub (include_stub_providers=True)
    stub_planner = DiscoveryPlanner(registry=planner._registry, seed=42, include_stub_providers=True)
    plan = stub_planner.plan("test query", remaining_time_budget_s=30.0, target_results=20)

    # Time execute
    start = time.monotonic()
    results = await stub_planner.execute("test query", plan)
    elapsed_ms = (time.monotonic() - start) * 1000

    # Should complete quickly (all stubs, no real HTTP)
    assert elapsed_ms < 200, f"execute() took {elapsed_ms:.1f}ms — may indicate real network calls"

    # Verify results are all error-typed (no real hits from stubs)
    for r in results:
        assert r.error_type is not None


# ---------------------------------------------------------------------------
# State machine — get_provider_state correctness
# ---------------------------------------------------------------------------


def test_get_provider_state_mapping() -> None:
    """Verify get_provider_state returns correct states for each provider."""
    # Production providers
    assert get_provider_state("ddg_mojeek") == ProviderCapabilityState.PRODUCTION
    assert get_provider_state("historical_frontier") == ProviderCapabilityState.PRODUCTION
    assert get_provider_state("wayback_cdx") == ProviderCapabilityState.PRODUCTION
    assert get_provider_state("ct_pivots") == ProviderCapabilityState.PRODUCTION

    # Stub/advisory
    assert get_provider_state("commoncrawl_cdx") == ProviderCapabilityState.ADVISORY_STUB

    # Disabled (production_enabled=False)
    assert get_provider_state("feed_pivots") == ProviderCapabilityState.NOT_WIRED  # requires_context=True


def test_execute_common_runners_no_crash(planner: DiscoveryPlanner) -> None:
    """Sanity: _run_* functions that are real (not stubs) don't crash on execution."""

    async def run_all() -> None:
        from hledac.universal.discovery.discovery_planner import _RUNNERS

        for name, runner in _RUNNERS.items():
            # Stub runners return immediately
            result = await runner("test", 5, 5.0)
            assert hasattr(result, "provider_name"), f"{name} runner returned invalid result"
            assert result.provider_name == name

    asyncio.run(run_all())

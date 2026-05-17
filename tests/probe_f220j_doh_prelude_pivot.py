"""F220J: DOH prelude pivot seed wiring — probe tests.

Tests:
1. pivot domain seed invokes DOH with domain
2. raw non-domain query does not invoke DOH
3. IP seed reverse remains deferred (build_lane_query returns _disabled)
4. DOH runner exception fail-soft
5. telemetry contains doh_seed_source="pivot_plan"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from runtime.sprint_scheduler import SprintSchedulerResult, SprintScheduler
from runtime.acquisition_strategy import AcquisitionLane, build_lane_query


# ----------------------------------------------------------------------
# Test 1: pivot domain seed invokes DOH with domain
# ----------------------------------------------------------------------


async def test_pivot_domain_seed_used_for_doh():
    """When pivot_lanes contains lane='DOH' seed_type='domain', DOH runs with that domain."""
    from runtime.sprint_scheduler import SprintScheduler

    @dataclass(frozen=True, slots=True)
    class _MockLanePlanItem:
        lane: str
        seed_value: str
        seed_type: str
        priority: float
        reason: str

    # Simulate pivot plan: domain evil.com → DOH
    pivot_items = (
        _MockLanePlanItem(lane="DOH", seed_value="evil.com", seed_type="domain",
                           priority=0.8, reason="domain_doh"),
    )

    # build_acquisition_plan that returns DOH as enabled
    mock_plan = MagicMock()
    mock_plan.plans = [
        MagicMock(lane=MagicMock(value="DOH"), enabled=True, timeout_s=20.0, max_items=5),
    ]

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

    # DOH adapter mock
    mock_doh_adapter = MagicMock()
    mock_doh_adapter.run = AsyncMock(return_value=[])

    class _FakeTime:
        @staticmethod
        def monotonic():
            return 0.0
        @staticmethod
        def time():
            return 0.0

    scheduler = object.__new__(SprintScheduler)
    scheduler._doh_adapter = mock_doh_adapter
    scheduler._result = SprintSchedulerResult()
    scheduler._acquisition_plan = mock_plan
    scheduler._sidecar_orchestrator = MagicMock()

    await scheduler._run_doh_prelude_lane(
        query="something unrelated",
        duckdb_store=mock_store,
        time_module=_FakeTime,
        nonfeed_prelude_attempted=[],
        nonfeed_prelude_terminal=[],
        nonfeed_prelude_accepted={},
        pivot_doh_items=pivot_items,
    )

    # DOH adapter should have been called with "evil.com" from pivot plan
    # not with "something unrelated" from raw query
    mock_doh_adapter.run.assert_called_once()
    call_args = mock_doh_adapter.run.call_args
    assert call_args.kwargs.get("domain") == "evil.com" or call_args[1].get("domain") == "evil.com", \
        f"DOH run called with wrong domain: {call_args}"
    # doh_seed_source telemetry
    assert scheduler._result.doh_seed_source == "pivot_plan", \
        f"Expected pivot_plan, got {scheduler._result.doh_seed_source}"
    print("  test_pivot_domain_seed_used_for_doh PASS")


# ----------------------------------------------------------------------
# Test 2: raw non-domain query does not invoke DOH
# ----------------------------------------------------------------------


async def test_raw_query_no_domain_skips_doh():
    """When no pivot DOH item exists and raw query has no domain, DOH skips with no_domain_seed."""
    from runtime.sprint_scheduler import SprintScheduler

    time_mod = MagicMock()
    time_mod.monotonic.return_value = 0.0
    time_mod.time.return_value = 0.0

    class _FakeTime:
        @staticmethod
        def monotonic():
            return 0.0
        @staticmethod
        def time():
            return 0.0

    mock_store = MagicMock()

    mock_doh_adapter = MagicMock()
    mock_doh_adapter.run = AsyncMock(return_value=[])

    scheduler = object.__new__(SprintScheduler)
    scheduler._doh_adapter = mock_doh_adapter
    scheduler._result = SprintSchedulerResult()
    scheduler._acquisition_plan = None
    scheduler._sidecar_orchestrator = MagicMock()

    await scheduler._run_doh_prelude_lane(
        query="this has no domain at all",  # raw query with no domain
        duckdb_store=mock_store,
        time_module=_FakeTime,
        nonfeed_prelude_attempted=[],
        nonfeed_prelude_terminal=[],
        nonfeed_prelude_accepted={},
        pivot_doh_items=(),  # no pivot items
    )

    # DOH adapter should NOT have been called
    mock_doh_adapter.run.assert_not_called()
    # should have returned no_candidates
    assert scheduler._result.doh_terminal_stage == "no_candidates", \
        f"Expected no_candidates, got {scheduler._result.doh_terminal_stage}"
    assert scheduler._result.doh_seed_source == "no_domain_seed", \
        f"Expected no_domain_seed, got {scheduler._result.doh_seed_source}"
    print("  test_raw_query_no_domain_skips_doh PASS")


# ----------------------------------------------------------------------
# Test 3: IP seed → DOH reverse deferred (build_lane_query returns _disabled)
# ----------------------------------------------------------------------


def test_ip_query_returns_disabled_for_doh():
    """build_lane_query for an IP returns _disabled with ip_seed_reverse_doh_deferred."""
    result = build_lane_query("1.2.3.4", AcquisitionLane.DOH)
    assert isinstance(result, dict), f"Expected dict for IP, got {type(result)}"
    assert result.get("_disabled") is True, f"Expected _disabled=True, got {result}"
    assert "ip_seed_reverse_doh_deferred" in result.get("reason", ""), \
        f"Expected ip_seed_reverse_doh_deferred, got {result.get('reason')}"
    print("  test_ip_query_returns_disabled_for_doh PASS")


# ----------------------------------------------------------------------
# Test 4: DOH runner exception fail-soft
# ----------------------------------------------------------------------


async def test_doh_runner_exception_fails_soft():
    """When DOH adapter raises, DOH terminal stage = provider_error, no exception propagated."""
    from runtime.sprint_scheduler import SprintScheduler

    @dataclass(frozen=True, slots=True)
    class _MockLanePlanItem:
        lane: str
        seed_value: str
        seed_type: str
        priority: float
        reason: str

    pivot_items = (
        _MockLanePlanItem(lane="DOH", seed_value="evil.com", seed_type="domain",
                           priority=0.8, reason="domain_doh"),
    )

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

    mock_doh_adapter = MagicMock()
    mock_doh_adapter.run = AsyncMock(side_effect=RuntimeError("DNS timeout"))

    class _FakeTime:
        @staticmethod
        def monotonic():
            return 0.0
        @staticmethod
        def time():
            return 0.0

    mock_plan = MagicMock()
    mock_plan.plans = []

    scheduler = object.__new__(SprintScheduler)
    scheduler._doh_adapter = mock_doh_adapter
    scheduler._result = SprintSchedulerResult()
    scheduler._acquisition_plan = mock_plan
    scheduler._sidecar_orchestrator = MagicMock()

    result_tuple = await scheduler._run_doh_prelude_lane(
        query="whatever",
        duckdb_store=mock_store,
        time_module=_FakeTime,
        nonfeed_prelude_attempted=[],
        nonfeed_prelude_terminal=[],
        nonfeed_prelude_accepted={},
        pivot_doh_items=pivot_items,
    )

    lane_name, accepted = result_tuple
    assert lane_name == "DOH"
    assert accepted == 0
    assert scheduler._result.doh_terminal_stage == "provider_error", \
        f"Expected provider_error, got {scheduler._result.doh_terminal_stage}"
    assert "RuntimeError" in scheduler._result.doh_provider_errors[0], \
        f"Expected RuntimeError in provider_errors, got {scheduler._result.doh_provider_errors}"
    print("  test_doh_runner_exception_fails_soft PASS")


# ----------------------------------------------------------------------
# Test 5: telemetry doh_seed_source field
# ----------------------------------------------------------------------


def test_doh_seed_source_telemetry_field():
    """SprintSchedulerResult has doh_seed_source field with correct default."""
    result = SprintSchedulerResult()
    assert hasattr(result, "doh_seed_source"), "Missing doh_seed_source field"
    assert result.doh_seed_source == "", f"Expected empty default, got {result.doh_seed_source}"
    print("  test_doh_seed_source_telemetry_field PASS")


async def test_doh_seed_source_set_to_pivot_plan():
    """doh_seed_source is set to 'pivot_plan' when pivot domain is used."""
    from runtime.sprint_scheduler import SprintScheduler

    @dataclass(frozen=True, slots=True)
    class _MockLanePlanItem:
        lane: str
        seed_value: str
        seed_type: str
        priority: float
        reason: str

    pivot_items = (
        _MockLanePlanItem(lane="DOH", seed_value="pivot-example.com", seed_type="domain",
                           priority=0.8, reason="domain_doh"),
    )

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

    mock_doh_adapter = MagicMock()
    mock_doh_adapter.run = AsyncMock(return_value=[])

    class _FakeTime:
        @staticmethod
        def monotonic():
            return 0.0
        @staticmethod
        def time():
            return 0.0

    mock_plan = MagicMock()
    mock_plan.plans = []

    scheduler = object.__new__(SprintScheduler)
    scheduler._doh_adapter = mock_doh_adapter
    scheduler._result = SprintSchedulerResult()
    scheduler._acquisition_plan = mock_plan
    scheduler._sidecar_orchestrator = MagicMock()

    await scheduler._run_doh_prelude_lane(
        query="irrelevant query",
        duckdb_store=mock_store,
        time_module=_FakeTime,
        nonfeed_prelude_attempted=[],
        nonfeed_prelude_terminal=[],
        nonfeed_prelude_accepted={},
        pivot_doh_items=pivot_items,
    )

    assert scheduler._result.doh_seed_source == "pivot_plan", \
        f"Expected pivot_plan, got {scheduler._result.doh_seed_source}"
    print("  test_doh_seed_source_set_to_pivot_plan PASS")


async def test_doh_seed_source_set_to_raw_query():
    """doh_seed_source is set to 'raw_query' when pivot has no DOH item but query has domain."""
    from runtime.sprint_scheduler import SprintScheduler

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

    mock_doh_adapter = MagicMock()
    mock_doh_adapter.run = AsyncMock(return_value=[])

    class _FakeTime:
        @staticmethod
        def monotonic():
            return 0.0
        @staticmethod
        def time():
            return 0.0

    mock_plan = MagicMock()
    mock_plan.plans = []

    scheduler = object.__new__(SprintScheduler)
    scheduler._doh_adapter = mock_doh_adapter
    scheduler._result = SprintSchedulerResult()
    scheduler._acquisition_plan = mock_plan
    scheduler._sidecar_orchestrator = MagicMock()

    # Query with a domain but no pivot DOH item
    await scheduler._run_doh_prelude_lane(
        query="evil.com malicious site",  # raw query contains domain
        duckdb_store=mock_store,
        time_module=_FakeTime,
        nonfeed_prelude_attempted=[],
        nonfeed_prelude_terminal=[],
        nonfeed_prelude_accepted={},
        pivot_doh_items=(),  # no pivot DOH item
    )

    assert scheduler._result.doh_seed_source == "raw_query", \
        f"Expected raw_query, got {scheduler._result.doh_seed_source}"
    print("  test_doh_seed_source_set_to_raw_query PASS")


# ----------------------------------------------------------------------
# Test 6: raw query domain extraction still works
# ----------------------------------------------------------------------


def test_build_lane_query_extracts_domain_from_raw_query():
    """build_lane_query returns first domain from raw query when query contains domain."""
    result = build_lane_query("evil.com", AcquisitionLane.DOH)
    assert result == "evil.com", f"Expected 'evil.com', got {result}"

    result2 = build_lane_query("check evil.com now", AcquisitionLane.DOH)
    assert result2 == "evil.com", f"Expected 'evil.com', got {result2}"
    print("  test_build_lane_query_extracts_domain_from_raw_query PASS")


async def _async_main():
    await test_pivot_domain_seed_used_for_doh()
    await test_raw_query_no_domain_skips_doh()
    test_ip_query_returns_disabled_for_doh()
    await test_doh_runner_exception_fails_soft()
    test_doh_seed_source_telemetry_field()
    await test_doh_seed_source_set_to_pivot_plan()
    await test_doh_seed_source_set_to_raw_query()
    test_build_lane_query_extracts_domain_from_raw_query()


if __name__ == "__main__":
    import asyncio
    print("F220J: DOH Prelude Pivot Wiring — probe tests\n")
    asyncio.run(_async_main())
    print("\nAll F220J DOH prelude pivot wiring tests passed.")
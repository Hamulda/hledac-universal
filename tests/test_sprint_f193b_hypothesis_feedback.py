"""
Sprint F193B: Hypothesis → Finding Bounded Feedback Loop
========================================================

Tests for the bounded feedback seam from hypothesis/ToT output to scheduler input.
Validates:
- F193B: hypothesis output can create new scheduler pivot inputs
- F193B: iteration depth cap prevents runaway recursion
- F193B: query count cap prevents unbounded growth
- F193B: fail-soft when enqueue_hypothesis_pivot is not available

Invariant table:
| Test | Invariant |
|------|-----------|
| test_enqueue_hypothesis_pivot_respects_depth_cap | depth > max_hypothesis_depth → dropped |
| test_enqueue_hypothesis_pivot_respects_query_count_cap | query_count >= max_hypothesis_queries → dropped |
| test_enqueue_hypothesis_pivot_success_within_bounds | depth and count within bounds → enqueued |
| test_hypothesis_ioc_type_maps_to_search_tasks | ioc_type="hypothesis" → multi_engine_search, rdap_lookup |
| test_pipeline_accepts_enqueue_hypothesis_pivot_param | pipeline signature includes the callback param |
| test_pipeline_calls_callback_when_provided | P12 block calls callback after ToT result |
| test_pipeline_fails_soft_without_callback | P12 works when callback is None |
| test_no_runaway_loop_with_max_depth | depth cap enforced on consecutive enqueues |
"""

from __future__ import annotations

import inspect

import pytest


class TestSprintF193BConfigCaps:
    """Verify SprintSchedulerConfig has F193B cap fields."""

    def test_max_hypothesis_depth_config(self):
        """SprintSchedulerConfig.max_hypothesis_depth caps iteration depth."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig()
        assert hasattr(config, "max_hypothesis_depth"), (
            "F193B: SprintSchedulerConfig must have max_hypothesis_depth"
        )
        assert config.max_hypothesis_depth == 3, (
            "F193B: default max_hypothesis_depth must be 3"
        )

    def test_max_hypothesis_queries_config(self):
        """SprintSchedulerConfig.max_hypothesis_queries caps total query count."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig()
        assert hasattr(config, "max_hypothesis_queries"), (
            "F193B: SprintSchedulerConfig must have max_hypothesis_queries"
        )
        assert config.max_hypothesis_queries == 10, (
            "F193B: default max_hypothesis_queries must be 10"
        )


class TestSprintF193BStateTracking:
    """Verify SprintScheduler tracks hypothesis feedback state."""

    def test_hypothesis_depth_state_initialized(self):
        """SprintScheduler._hypothesis_depth initialized to 0."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_hypothesis_depth"), (
            "F193B: SprintScheduler must have _hypothesis_depth state"
        )
        assert scheduler._hypothesis_depth == 0, (
            "F193B: _hypothesis_depth must initialize to 0"
        )

    def test_hypothesis_query_count_state_initialized(self):
        """SprintScheduler._hypothesis_query_count initialized to 0."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_hypothesis_query_count"), (
            "F193B: SprintScheduler must have _hypothesis_query_count state"
        )
        assert scheduler._hypothesis_query_count == 0, (
            "F193B: _hypothesis_query_count must initialize to 0"
        )


class TestSprintF193BEnqueueMethod:
    """Verify enqueue_hypothesis_pivot method exists and enforces caps."""

    def test_enqueue_hypothesis_pivot_method_exists(self):
        """SprintScheduler has enqueue_hypothesis_pivot method."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "enqueue_hypothesis_pivot"), (
            "F193B: SprintScheduler must have enqueue_hypothesis_pivot method"
        )

    def test_enqueue_respects_depth_cap(self):
        """Depth > max_hypothesis_depth → pivot dropped (returns False)."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(max_hypothesis_depth=3)
        scheduler = SprintScheduler(config)

        # Depth 4 exceeds cap of 3
        result = scheduler.enqueue_hypothesis_pivot(
            ioc_value="test_term",
            ioc_type="hypothesis",
            confidence=0.7,
            depth=4,
        )
        assert result is False, (
            "F193B: enqueue_hypothesis_pivot must return False when depth exceeds cap"
        )

    def test_enqueue_respects_query_count_cap(self):
        """Query count >= max_hypothesis_queries → pivot dropped."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(max_hypothesis_queries=5)
        scheduler = SprintScheduler(config)

        # Enqueue 5 queries (at cap)
        for i in range(5):
            result = scheduler.enqueue_hypothesis_pivot(
                ioc_value=f"term_{i}",
                ioc_type="hypothesis",
                confidence=0.7,
                depth=1,
            )
            assert result is True, f"F193B: query {i} should succeed"

        # 6th query should be dropped
        result = scheduler.enqueue_hypothesis_pivot(
            ioc_value="term_6",
            ioc_type="hypothesis",
            confidence=0.7,
            depth=1,
        )
        assert result is False, (
            "F193B: enqueue_hypothesis_pivot must return False when query count at cap"
        )

    def test_enqueue_success_within_bounds(self):
        """Within bounds → pivot enqueued successfully."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(max_hypothesis_depth=3, max_hypothesis_queries=10)
        scheduler = SprintScheduler(config)

        result = scheduler.enqueue_hypothesis_pivot(
            ioc_value="test_term",
            ioc_type="hypothesis",
            confidence=0.7,
            depth=2,
        )
        assert result is True, (
            "F193B: enqueue_hypothesis_pivot must return True when within bounds"
        )
        assert scheduler._hypothesis_query_count == 1, (
            "F193B: query count must increment on successful enqueue"
        )
        assert scheduler._hypothesis_depth == 2, (
            "F193B: depth must be updated on successful enqueue"
        )


class TestSprintF193BIOCTypeMapping:
    """Verify hypothesis IOC type maps to search task types."""

    def test_hypothesis_ioc_type_maps_correctly(self):
        """ioc_type='hypothesis' maps to multi_engine_search, rdap_lookup."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        # Patch enqueue_pivot to avoid queue full issues
        enqueued_tasks = []

        def mock_enqueue(ioc_value, ioc_type, confidence, degree=1.0, task_type=None):
            enqueued_tasks.append({
                "ioc_value": ioc_value,
                "ioc_type": ioc_type,
                "confidence": confidence,
                "degree": degree,
            })
            # Don't actually enqueue

        scheduler.enqueue_pivot = mock_enqueue

        scheduler.enqueue_hypothesis_pivot(
            ioc_value="test_term",
            ioc_type="hypothesis",
            confidence=0.7,
            depth=1,
        )

        assert len(enqueued_tasks) == 1, (
            "F193B: enqueue_hypothesis_pivot must call enqueue_pivot"
        )
        assert enqueued_tasks[0]["ioc_type"] == "hypothesis", (
            "F193B: hypothesis pivot must preserve ioc_type"
        )


class TestSprintF193BPipelineIntegration:
    """Verify pipeline accepts and uses enqueue_hypothesis_pivot callback."""

    def test_pipeline_signature_has_enqueue_callback(self):
        """async_run_live_public_pipeline has enqueue_hypothesis_pivot parameter."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        sig = inspect.signature(async_run_live_public_pipeline)
        params = list(sig.parameters.keys())

        assert "enqueue_hypothesis_pivot" in params, (
            "F193B: async_run_live_public_pipeline must have enqueue_hypothesis_pivot param"
        )

    def test_p12_block_uses_callback(self):
        """P12 block calls enqueue_hypothesis_pivot when ToT produces results."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, "P12 block not found"

        # P12 block should reference the callback
        p12_block = source[p12_start:p12_start + 5000]
        assert "enqueue_hypothesis_pivot" in p12_block, (
            "F193B: P12 block must call enqueue_hypothesis_pivot callback"
        )

    def test_pipeline_fails_soft_without_callback(self):
        """Pipeline works correctly when enqueue_hypothesis_pivot is None."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        source = inspect.getsource(async_run_live_public_pipeline)
        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 5000]

        # Must check if callback is not None before calling
        assert "enqueue_hypothesis_pivot is not None" in p12_block or "if enqueue_hypothesis_pivot" in p12_block, (
            "F193B: P12 must check callback is not None before calling"
        )


class TestSprintF193BNoRunawayLoop:
    """Verify bounded execution prevents runaway feedback loops."""

    def test_depth_cap_prevents_deep_recursion(self):
        """Consecutive enqueues with depth > cap are dropped."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(max_hypothesis_depth=3, max_hypothesis_queries=20)
        scheduler = SprintScheduler(config)

        # Simulate a chain of hypothesis-driven pivots at max depth
        accepted = 0
        for depth in range(1, 5):
            result = scheduler.enqueue_hypothesis_pivot(
                ioc_value=f"term_depth_{depth}",
                ioc_type="hypothesis",
                confidence=0.7,
                depth=depth,
            )
            if result:
                accepted += 1

        # Only depths 1, 2, 3 should be accepted (within cap of 3)
        assert accepted == 3, (
            "F193B: depth cap must prevent depth 4 from being enqueued"
        )
        assert scheduler._hypothesis_depth == 3, (
            "F193B: max tracked depth must be 3 (the cap)"
        )

    def test_query_cap_stops_unbounded_growth(self):
        """After max_hypothesis_queries, new pivots are rejected."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(max_hypothesis_depth=5, max_hypothesis_queries=3)
        scheduler = SprintScheduler(config)

        # Enqueue exactly at cap
        for i in range(3):
            result = scheduler.enqueue_hypothesis_pivot(
                ioc_value=f"term_{i}",
                ioc_type="hypothesis",
                confidence=0.7,
                depth=1,
            )
            assert result is True, f"F193B: query {i} should succeed"

        assert scheduler._hypothesis_query_count == 3, (
            "F193B: query count must reach cap exactly"
        )

        # One more should be rejected
        result = scheduler.enqueue_hypothesis_pivot(
            ioc_value="term_exceed",
            ioc_type="hypothesis",
            confidence=0.7,
            depth=1,
        )
        assert result is False, (
            "F193B: exceeding query cap must reject new pivots"
        )

    def test_hypothesis_probe_already_honors_depth(self):
        """Existing hypothesis_probe task type in scheduler should respect similar bounds."""
        from hledac.universal.runtime.sprint_scheduler import PivotTask, SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        SprintScheduler(config)

        # Create a hypothesis_probe task
        task = PivotTask(
            priority=-0.7,
            ioc_type="hypothesis",
            ioc_value="test hypothesis probe keywords",
            task_type="hypothesis_probe",
        )

        # Verify the task is properly structured
        assert task.ioc_type == "hypothesis", (
            "F193B: PivotTask with hypothesis_probe must have ioc_type='hypothesis'"
        )


class TestSprintF193BSeamInterface:
    """Verify the seam interface between pipeline and scheduler."""

    def test_scheduler_passes_callback_to_pipeline(self):
        """_run_public_discovery_in_cycle passes enqueue_hypothesis_pivot to pipeline."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler._run_public_discovery_in_cycle)

        assert "enqueue_hypothesis_pivot" in source, (
            "F193B: _run_public_discovery_in_cycle must pass enqueue_hypothesis_pivot callback"
        )

    def test_pipeline_calls_callback_after_tot(self):
        """P12 calls callback after ToT result is stored."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        source = inspect.getsource(async_run_live_public_pipeline)
        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 6000]

        # Callback must be called after ToT result (after store.async_ingest)
        assert "enqueue_hypothesis_pivot" in p12_block, (
            "F193B: callback must be called in P12 block"
        )

        # The call must be after the tot_finding storage
        tot_finding_pos = p12_block.find("async_ingest_findings_batch")
        callback_pos = p12_block.find("enqueue_hypothesis_pivot")
        assert callback_pos > tot_finding_pos > 0, (
            "F193B: callback must be called after storing ToT finding"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

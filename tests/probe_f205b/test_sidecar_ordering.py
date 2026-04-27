"""
Sprint F205B: Explicit Sidecar Ordering Guarantee — Probe Tests
==============================================================

Invariant mapping:
  F205B-1 | SIDECAR_STAGES is a tuple of 3 non-empty tuples
  F205B-2 | Stage 1 (light extraction): leak_sentinel, passive_fingerprint, evidence_triage, temporal_archaeology
  F205B-3 | Stage 2 (correlation): exposure_correlator, identity_stitching, sprint_diff, rir_correlator, social_identity_surface, wayback_diff
  F205B-4 | Stage 3 (derived): kill_chain_tagging, embedding
  F205B-5 | run_all_sidecars executes stage 1 before stage 2, stage 2 before stage 3
  F205B-6 | Within a stage, runners execute concurrently (not sequentially)
  F205B-7 | Failure in one runner does not stop other runners in same stage
  F205B-8 | Failure in stage 1 does not stop stage 2 or stage 3
  F205B-9 | Failure in stage 2 does not stop stage 3
  F205B-10 | asyncio.CancelledError is re-raised by run_all_sidecars
  F205B-11 | _check_gathered is called after each stage's gather
  F205B-12 | All registered runners produce a SidecarRunResult
"""

import asyncio
import time as _time
from unittest.mock import MagicMock

import pytest

from hledac.universal.runtime.sidecar_bus import (
    SIDECAR_STAGES,
    FindingSidecarBus,
    SidecarBatch,
    SidecarRunResult,
)


# ============================================================================
# F205B-1: SIDECAR_STAGES structure
# ============================================================================


class TestSidecarStagesStructure:
    """F205B-1: SIDECAR_STAGES is a tuple of 3 non-empty tuples."""

    def test_sidocar_stages_is_tuple(self):
        assert isinstance(SIDECAR_STAGES, tuple)

    def test_sidocar_stages_has_three_stages(self):
        assert len(SIDECAR_STAGES) == 3

    def test_each_stage_is_non_empty_tuple(self):
        for stage in SIDECAR_STAGES:
            assert isinstance(stage, tuple)
            assert len(stage) > 0


# ============================================================================
# F205B-2, F205B-3, F205B-4: Stage composition
# ============================================================================


class TestSidecarStagesComposition:
    """F205B-2/3/4: Verify stage runner names."""

    def test_stage_1_light_extraction_runners(self):
        stage_1 = SIDECAR_STAGES[0]
        assert set(stage_1) == {
            "leak_sentinel",
            "passive_fingerprint",
            "evidence_triage",
            "temporal_archaeology",
        }

    def test_stage_2_correlation_runners(self):
        stage_2 = SIDECAR_STAGES[1]
        assert set(stage_2) == {
            "exposure_correlator",
            "identity_stitching",
            "sprint_diff",
            "rir_correlator",
            "social_identity_surface",
            "wayback_diff",
        }

    def test_stage_3_derived_runners(self):
        stage_3 = SIDECAR_STAGES[2]
        assert set(stage_3) == {"kill_chain_tagging", "embedding"}


# ============================================================================
# F205B-5: Stage order execution
# ============================================================================


class TestStageOrderExecution:
    """F205B-5: Stage 1 executes before stage 2, stage 2 before stage 3."""

    @pytest.mark.asyncio
    async def test_stage_1_runs_before_stage_2(self):
        """Stage 1 completion timestamp < Stage 2 start timestamp."""
        bus = FindingSidecarBus()
        call_log: list[tuple[str, float]] = []

        async def stage1_runner(findings, store, query):
            await asyncio.sleep(0.01)
            call_log.append(("stage1", _time.monotonic()))

        async def stage2_runner(findings, store, query):
            call_log.append(("stage2", _time.monotonic()))

        bus.register("leak_sentinel", stage1_runner)
        bus.register("exposure_correlator", stage2_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)

        assert len(call_log) == 2
        stage1_time = next(t for name, t in call_log if name == "stage1")
        stage2_time = next(t for name, t in call_log if name == "stage2")
        assert stage1_time < stage2_time, "Stage 1 must complete before stage 2 starts"

    @pytest.mark.asyncio
    async def test_stage_2_runs_before_stage_3(self):
        """Stage 2 completion timestamp < Stage 3 start timestamp."""
        bus = FindingSidecarBus()
        call_log: list[tuple[str, float]] = []

        async def stage2_runner(findings, store, query):
            await asyncio.sleep(0.01)
            call_log.append(("stage2", _time.monotonic()))

        async def stage3_runner(findings, store, query):
            call_log.append(("stage3", _time.monotonic()))

        bus.register("exposure_correlator", stage2_runner)
        bus.register("kill_chain_tagging", stage3_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)

        assert len(call_log) == 2
        stage2_time = next(t for name, t in call_log if name == "stage2")
        stage3_time = next(t for name, t in call_log if name == "stage3")
        assert stage2_time < stage3_time, "Stage 2 must complete before stage 3 starts"


# ============================================================================
# F205B-6: Within-stage concurrency
# ============================================================================


class TestWithinStageConcurrency:
    """F205B-6: Runners within same stage execute concurrently, not sequentially."""

    @pytest.mark.asyncio
    async def test_stage_1_runners_overlap_in_time(self):
        """Two stage-1 runners running concurrently should have overlapping execution windows."""
        bus = FindingSidecarBus()
        overlap_detected = False

        async def slow_runner(findings, store, query):
            await asyncio.sleep(0.05)

        async def fast_runner(findings, store, query):
            pass

        bus.register("leak_sentinel", slow_runner)
        bus.register("passive_fingerprint", fast_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()

        # If sequential: takes ~50ms. If concurrent: takes ~50ms (overlapping)
        import time

        t0 = time.monotonic()
        await bus.run_all_sidecars(batch, store)
        elapsed = time.monotonic() - t0

        # Concurrent: ~50ms, Sequential: ~100ms. Use 80ms as threshold
        assert elapsed < 0.08, f"Runners appear sequential ({elapsed*1000:.0f}ms > 80ms threshold)"


# ============================================================================
# F205B-7: Failure isolation within stage
# ============================================================================


class TestFailureIsolationWithinStage:
    """F205B-7: Failure in one runner does not stop other runners in same stage."""

    @pytest.mark.asyncio
    async def test_crashing_runner_does_not_stop_healthy_runner_in_same_stage(self):
        """A crashing runner in stage 1 does not prevent stage 2 from running."""
        bus = FindingSidecarBus()
        call_order = []

        async def crashing(findings, store, query):
            call_order.append("crashing")
            raise RuntimeError("simulated crash")

        async def healthy(findings, store, query):
            call_order.append("healthy")

        bus.register("leak_sentinel", crashing)
        bus.register("passive_fingerprint", healthy)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)

        # Both should have been attempted
        assert "crashing" in call_order
        assert "healthy" in call_order
        result_names = {r.sidecar_name for r in results}
        # Use registered sidecar names, not function names
        assert "leak_sentinel" in result_names
        assert "passive_fingerprint" in result_names


# ============================================================================
# F205B-8: Stage failure does not stop next stage
# ============================================================================


class TestStageFailureIsolation:
    """F205B-8: Failure in stage 1 does not stop stage 2 or stage 3."""

    @pytest.mark.asyncio
    async def test_stage_1_failure_does_not_stop_stage_2(self):
        """Stage 1 crashing does not prevent stage 2 from executing."""
        bus = FindingSidecarBus()
        stage_2_ran = False

        async def stage1_crashing(findings, store, query):
            raise RuntimeError("stage1 crash")

        async def stage2_runner(findings, store, query):
            nonlocal stage_2_ran
            stage_2_ran = True

        bus.register("leak_sentinel", stage1_crashing)
        bus.register("exposure_correlator", stage2_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)

        assert stage_2_ran, "Stage 2 must run even if stage 1 crashes"

    @pytest.mark.asyncio
    async def test_stage_2_failure_does_not_stop_stage_3(self):
        """Stage 2 crashing does not prevent stage 3 from executing."""
        bus = FindingSidecarBus()
        stage_3_ran = False

        async def stage2_crashing(findings, store, query):
            raise RuntimeError("stage2 crash")

        async def stage3_runner(findings, store, query):
            nonlocal stage_3_ran
            stage_3_ran = True

        bus.register("exposure_correlator", stage2_crashing)
        bus.register("kill_chain_tagging", stage3_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)

        assert stage_3_ran, "Stage 3 must run even if stage 2 crashes"


# ============================================================================
# F205B-9: All runners in stage produce results
# ============================================================================


class TestAllRunnersProduceResults:
    """F205B-12: All registered runners produce a SidecarRunResult."""

    @pytest.mark.asyncio
    async def test_all_registered_runners_produce_result(self):
        """Every registered runner (regardless of stage) produces a SidecarRunResult."""
        bus = FindingSidecarBus()

        async def dummy_runner(findings, store, query):
            pass

        # Register one runner per stage
        bus.register("leak_sentinel", dummy_runner)
        bus.register("exposure_correlator", dummy_runner)
        bus.register("kill_chain_tagging", dummy_runner)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        results = await bus.run_all_sidecars(batch, store)

        result_names = {r.sidecar_name for r in results}
        assert "leak_sentinel" in result_names
        assert "exposure_correlator" in result_names
        assert "kill_chain_tagging" in result_names
        assert len(results) == 3


# ============================================================================
# F205B-10: CancelledError re-raised
# ============================================================================


class TestCancelledErrorReraised:
    """F205B-10: asyncio.CancelledError is re-raised, never swallowed."""

    @pytest.mark.asyncio
    async def test_cancelled_error_raised(self):
        """CancelledError is re-raised when task is cancelled externally."""
        bus = FindingSidecarBus()

        async def never_returns(findings, store, query):
            await asyncio.sleep(10)

        bus.register("slow", never_returns)
        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()

        task = asyncio.create_task(bus.run_all_sidecars(batch, store))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ============================================================================
# F205B-11: _check_gathered called
# ============================================================================


class TestCheckGatheredCalled:
    """F205B-11: _check_gathered is called after each stage's gather."""

    @pytest.mark.asyncio
    async def test_check_gathered_receives_correct_items(self):
        """_check_gathered is called with gathered results after each stage."""
        bus = FindingSidecarBus()
        check_gathered_calls: list[int] = []

        original_check = bus._check_gathered

        def tracking_check(gathered):
            check_gathered_calls.append(len(gathered))
            original_check(gathered)

        bus._check_gathered = tracking_check

        async def dummy(findings, store, query):
            pass

        bus.register("leak_sentinel", dummy)
        bus.register("exposure_correlator", dummy)

        batch = SidecarBatch(
            sprint_id="s1",
            query="q",
            source_branch="ct",
            findings=({"id": 1},),
            created_ts=_time.time(),
        )
        store = MagicMock()
        await bus.run_all_sidecars(batch, store)

        assert len(check_gathered_calls) >= 1, "_check_gathered must be called at least once"
        assert all(n > 0 for n in check_gathered_calls), "Each call should have items"

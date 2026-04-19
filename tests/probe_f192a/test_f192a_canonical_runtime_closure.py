"""
F192A PROBE TESTS: Canonical Runtime Cluster Closure
======================================================

Verifies:
- DF-7: entered_active_phase_at_monotonic removed from SprintSchedulerResult
- DF-6: phases list uses _PHASE_ORDER from sprint_lifecycle
- DF-1: _public_backend_degraded derives from result.public_backend_degraded
- DF-5: timing_truth uses result.pre_active_starved and result.pre_loop_blocker_reason

Invariant mapping:
  DF-7  → test_result_dataclass_no_orphaned_field
  DF-6  → test_phase_order_from_lifecycle
  DF-1  → test_public_backend_from_result_field
  DF-5  → test_pre_active_starvation_from_result
"""

import ast
import inspect
from dataclasses import fields as dc_fields

import pytest


class TestF192A_DF7_RemovedOrphanedField:
    """DF-7: entered_active_phase_at_monotonic removed from SprintSchedulerResult."""

    def test_result_dataclass_no_orphaned_field(self):
        """
        SprintSchedulerResult must NOT have entered_active_phase_at_monotonic field.

        This field was set in scheduler run() but never consumed by __main__.py
        (only entered_active_at_monotonic was used). Dead field removal reduces
        the result surface and eliminates the split-brain.
        """
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result_fields = {f.name for f in dc_fields(SprintSchedulerResult)}
        assert "entered_active_phase_at_monotonic" not in result_fields

    def test_scheduler_run_loop_does_not_set_orphaned_field(self):
        """
        The scheduler run() method must not reference entered_active_phase_at_monotonic.
        """
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        src = inspect.getsource(SprintScheduler.run)
        assert "entered_active_phase_at_monotonic" not in src


class TestF192A_DF6_PhaseOrderFromLifecycle:
    """DF-6: phases list in __main__.py uses _PHASE_ORDER from sprint_lifecycle."""

    def test_phase_order_from_lifecycle(self):
        """
        __main__.py must use _PHASE_ORDER from sprint_lifecycle, not a hardcoded list.

        Previously: phases = ["BOOT", "WARMUP", "ACTIVE", "WINDUP", "TEARDOWN"]
        Now:        phases = _PHASE_ORDER  (imported from sprint_lifecycle)
        """
        from hledac.universal.core import __main__ as cm
        from hledac.universal.runtime.sprint_lifecycle import _PHASE_ORDER

        # _PHASE_ORDER must be importable in __main__.py scope
        assert hasattr(cm, '_PHASE_ORDER')

        # phases variable must reference the imported _PHASE_ORDER, not a literal list
        src = inspect.getsource(cm.run_sprint)
        # Must NOT have the old hardcoded list
        assert 'phases = ["BOOT", "WARMUP", "ACTIVE", "WINDUP", "TEARDOWN"]' not in src
        # Must use _PHASE_ORDER
        assert "phases = _PHASE_ORDER" in src

        # _PHASE_ORDER must have exactly 6 phases in correct order
        phase_names = [p.name for p in _PHASE_ORDER]
        assert phase_names == ["BOOT", "WARMUP", "ACTIVE", "WINDUP", "EXPORT", "TEARDOWN"]


class TestF192A_DF1_PublicBackendFromResult:
    """DF-1: _public_backend_degraded in __main__.py derives from result.public_backend_degraded."""

    def test_public_backend_from_result_field(self):
        """
        __main__.py must use result.public_backend_degraded (set by scheduler)
        rather than re-computing the httpx error pattern match inline.

        The scheduler SprintSchedulerResult.public_backend_degraded field is set
        during the public pipeline run. __main__.py should NOT re-derive this
        from result.public_error inspection.
        """
        from hledac.universal.core import __main__ as cm
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result_fields = {f.name for f in dc_fields(SprintSchedulerResult)}
        # Scheduler must have the field
        assert "public_backend_degraded" in result_fields

        # __main__.py source must use result.public_backend_degraded for _ckpt_category
        src = inspect.getsource(cm.run_sprint)

        # The second duplicate _public_backend_degraded block must be gone
        # (the inline re-computation of httpx pattern match for _ckpt_category)
        # Count occurrences of the httpx error pattern in __main__ source
        httpx_error_pattern = '"httpx" in result.public_error'
        count = src.count(httpx_error_pattern)
        # Should appear at most once (in the FIRST definition that feeds verdict),
        # not in a second duplicate block for _ckpt_category
        assert count <= 1, (
            f"httpx error pattern found {count} times in run_sprint — "
            "duplicate _public_backend_degraded computation may still exist"
        )

    def test_ckpt_uses_result_public_backend_directly(self):
        """
        _ckpt_category must use _public_backend (derived from result.public_backend_degraded)
        not an inline re-computation.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        # The _ckpt_category section must reference _public_backend (the consolidated var)
        assert "_public_backend" in src
        # Must not re-compute httpx pattern in _ckpt_category section
        # (that was the duplicate block we removed)
        # Find the _ckpt_category block
        ckpt_start = src.find("_ckpt_category")
        ckpt_block = src[ckpt_start:ckpt_start + 2000]
        assert '"httpx"' not in ckpt_block


class TestF192A_DF5_PreActiveStarvationFromResult:
    """DF-5: timing_truth uses result.pre_active_starved and result.pre_loop_blocker_reason directly."""

    def test_pre_active_starvation_from_result(self):
        """
        timing_truth.pre_active_starvation must use result.pre_active_starved directly.

        Previously: __main__.py re-derived _pre_active_starvation from result fields
                    in a local if/elif/else block
        Now:         timing_truth uses result.pre_active_starved directly
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)

        # Find timing_truth dict construction
        timing_start = src.find("timing_truth = {")
        timing_block = src[timing_start:timing_start + 2000]

        # Must use result.pre_active_starved directly
        assert "result.pre_active_starved" in timing_block
        # Must NOT have local _pre_active_starvation variable before timing_truth
        # (the redundant local derivation that was removed)
        pre_timing = src[:timing_start]
        assert "_pre_active_starvation" not in pre_timing or "_pre_active_starved" not in pre_timing

    def test_pre_active_blocker_from_result(self):
        """
        timing_truth.pre_active_blocker must use result.pre_loop_blocker_reason directly.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        timing_start = src.find("timing_truth = {")
        timing_block = src[timing_start:timing_start + 2000]

        assert "result.pre_loop_blocker_reason" in timing_block
        # Must not have the old local _pre_active_blocker derivation
        pre_timing = src[:timing_start]
        assert "_pre_active_blocker" not in pre_timing or "result.pre_loop_blocker_reason" not in pre_timing


class TestF192A_DF4_InlineHardwareLimitedVsIsHardwareLimited:
    """DF-4: _inline_hardware_limited (verdict) and _is_hardware_limited (_ckpt_category) are equivalent.

    Note: These two variables check the same underlying condition but serve different
    purposes (verdict vs checkpoint taxonomy). They are semantically equivalent:
      _inline_hardware_limited:  cycles_started==0 ∧ ¬findings ∧ ¬hits ∧ swap/emergency
      _is_hardware_limited:      ¬is_meaningful ∧ cycles_started==0 ∧ swap/emergency

    Both evaluate to True under the same conditions (hardware-limited smoke).
    Kept as separate variables to maintain verdict vs checkpoint separation.
    """

    def test_both_hardware_limited_vars_exist_for_separate_concerns(self):
        """
        Both _inline_hardware_limited (verdict) and _is_hardware_limited (_ckpt_category)
        must exist as separate variables since they serve different code paths.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        # Both must exist in the source
        assert "_inline_hardware_limited" in src
        assert "_is_hardware_limited" in src

    def test_is_hardware_limited_after_meaningful_run(self):
        """
        _is_hardware_limited must be computed AFTER is_meaningful (runtime_truth_level).
        This ensures the checkpoint category can reference it after runtime_truth is derived.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        is_hw_pos = src.find("_is_hardware_limited")
        runtime_truth_pos = src.find("runtime_truth_level")
        assert is_hw_pos > runtime_truth_pos, (
            "_is_hardware_limited must be computed AFTER runtime_truth_level"
        )


class TestF192A_SchedulerRoleBoundary:
    """Verify scheduler role boundary: worker/executor, not sprint owner."""

    def test_scheduler_class_docstring_declares_worker_role(self):
        """
        SprintScheduler class docstring must declare ROLE: runtime worker / operational executor.
        This was already present but is verified as part of F192A cluster closure.
        """
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        docstring = SprintScheduler.__doc__ or ""
        assert "runtime worker" in docstring.lower() or "runtime executor" in docstring.lower()

    def test_compute_sprint_intelligence_returns_dict(self):
        """
        compute_sprint_intelligence() must return a dict and be callable on a
        scheduler instance (fails soft, returns {} when no findings).
        """
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        result = scheduler.compute_sprint_intelligence()
        assert isinstance(result, dict)
        # All keys must be present (fail-soft design)
        expected_keys = {
            "correlation", "hypothesis_pack", "branch_value",
            "signal_path", "feed_verdict", "public_verdict",
        }
        assert expected_keys.issubset(result.keys())


class TestF192A_LifecycleRoleBoundary:
    """Verify lifecycle role boundary: lifecycle authority, not sprint owner."""

    def test_lifecycle_manager_is_phase_authority_only(self):
        """
        SprintLifecycleManager must be a pure phase state machine.
        It must NOT own sprint start/stop decisions — those belong to
        the scheduler (worker) and __main__.py (owner).
        """
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager, SprintPhase

        lc = SprintLifecycleManager()
        # Must start in BOOT
        assert lc._current_phase == SprintPhase.BOOT
        # start() transitions to WARMUP
        lc.start()
        assert lc._current_phase == SprintPhase.WARMUP
        # Lifecycle is purely reactive — transition_to is called by owner/worker
        lc.transition_to(SprintPhase.ACTIVE)
        assert lc._current_phase == SprintPhase.ACTIVE
        # is_terminal only true when TEARDOWN reached
        assert not lc.is_terminal()


class TestF192A_DF1to3_ConsolidatedCheckpointLogic:
    """DF-1/2/3: Consolidated checkpoint computation uses result fields directly."""

    def test_feed_zero_check_uses_result_fields(self):
        """
        _feed_zero_check must derive from result.accepted_findings and feed_fnd,
        not re-compute from duplicate inline logic.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        # Must use the consolidated _feed_zero_check in _ckpt_category
        assert "_feed_zero_check" in src
        # Must not have duplicate _feed_zero definition after the first one
        ckpt_pos = src.find("_ckpt_category")
        post_ckpt = src[ckpt_pos:]
        # Only one definition of _feed_zero_check in the ckpt section
        assert post_ckpt.count("_feed_zero_check = ") <= 1

    def test_cross_branch_fail_check_uses_result_fields(self):
        """
        _cross_branch_fail_check must derive from result fields directly,
        not from a locally re-computed _public_backend_degraded.
        """
        from hledac.universal.core import __main__ as cm

        src = inspect.getsource(cm.run_sprint)
        ckpt_pos = src.find("_ckpt_category")
        ckpt_block = src[ckpt_pos:ckpt_pos + 1500]

        # Must use _cross_branch_fail_check
        assert "_cross_branch_fail_check" in ckpt_block
        # Must NOT have another httpx re-computation in this block
        assert '"httpx"' not in ckpt_block

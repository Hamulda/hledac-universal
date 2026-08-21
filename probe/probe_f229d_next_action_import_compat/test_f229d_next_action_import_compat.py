"""
F229D: NEXT ACTION IMPORT COMPATIBILITY SEAL

Probe F229D — hermetic source/import smoke for next_action extraction.

Verifies:
  1. Source assertions — lsm imports from next_action module, no local defs
  2. Import assertions — modules load without runtime deps, symbols are callable
  3. Behavior assertion — same fixture produces same tuple via both paths

No live execution. No network. No MLX.
"""

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

# test file: hledac/universal/tests/probe_f229d_next_action_import_compat/test_*.py
# parent chain: test_f229d... -> tests/probe_f229d... -> tests -> hledac/universal
UNIVERSAL_ROOT = Path(__file__).resolve().parent.parent.parent
LSM_PATH = UNIVERSAL_ROOT / "benchmarks" / "live_sprint_measurement.py"
NAM_PATH = UNIVERSAL_ROOT / "benchmarks" / "live_measurement_next_action.py"

# ---------------------------------------------------------------------------
# Source assertions
# ---------------------------------------------------------------------------


class TestSourceAssertions:
    """lsm imports _derive_next_action from live_measurement_next_action."""

    def test_lsm_file_exists(self) -> None:
        assert LSM_PATH.exists(), f"live_sprint_measurement.py not found at {LSM_PATH}"

    def test_nam_file_exists(self) -> None:
        assert NAM_PATH.exists(), f"live_measurement_next_action.py not found at {NAM_PATH}"

    def test_lsm_imports_derive_next_action(self) -> None:
        """Line ~676: from benchmarks.live_measurement_next_action import _derive_next_action."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "benchmarks.live_measurement_next_action":
                    names = [alias.name for alias in node.names]
                    assert "_derive_next_action" in names, f"_derive_next_action not in import statement: {names}"
                    return
        pytest.fail("_derive_next_action import from benchmarks.live_measurement_next_action not found")

    def test_lsm_imports_next_action_input(self) -> None:
        """Line ~677: NextActionInput also imported from next_action module."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "benchmarks.live_measurement_next_action":
                    names = [alias.name for alias in node.names]
                    assert "NextActionInput" in names, f"NextActionInput not in import statement: {names}"
                    return
        pytest.fail("NextActionInput import from benchmarks.live_measurement_next_action not found")

    def test_lsm_imports_was_family_attempted(self) -> None:
        """Line ~679: _was_family_attempted also imported."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "benchmarks.live_measurement_next_action":
                    names = [alias.name for alias in node.names]
                    assert "_was_family_attempted" in names, f"_was_family_attempted not in import statement: {names}"
                    return
        pytest.fail("_was_family_attempted import from benchmarks.live_measurement_next_action not found")

    def test_lsm_no_local_next_action_input(self) -> None:
        """live_sprint_measurement.py does NOT locally define NextActionInput."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "NextActionInput":
                pytest.fail("NextActionInput defined locally in live_sprint_measurement.py — extraction incomplete")

    def test_lsm_no_local_rule_helpers(self) -> None:
        """live_sprint_measurement.py does NOT locally define any _rule_* helpers."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        local_rules = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("_rule")
        ]
        assert not local_rules, f"Local _rule_* definitions found in live_sprint_measurement.py: {local_rules}"

    def test_lsm_no_local_was_family_attempted(self) -> None:
        """live_sprint_measurement.py does NOT locally define _was_family_attempted."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_was_family_attempted":
                pytest.fail(
                    "_was_family_attempted defined locally in live_sprint_measurement.py — extraction incomplete"
                )

    def test_lsm_no_local_derive_next_action(self) -> None:
        """live_sprint_measurement.py does NOT locally define _derive_next_action."""
        src = LSM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_derive_next_action":
                pytest.fail("_derive_next_action defined locally in live_sprint_measurement.py — extraction incomplete")

    def test_nam_exports_expected_symbols(self) -> None:
        """next_action module __all__ contains the expected public symbols."""
        src = NAM_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            names = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)]
                            expected = {"NextActionInput", "_derive_next_action", "_was_family_attempted"}
                            assert expected.issubset(set(names)), f"__all__ missing expected symbols. Found: {names}"
                            return
        pytest.fail("__all__ not found in live_measurement_next_action.py")


# ---------------------------------------------------------------------------
# Import assertions — no runtime execution, just import + callable checks
# ---------------------------------------------------------------------------


class TestImportAssertions:
    """Modules import without runtime deps; symbols are callable."""

    @pytest.fixture
    def lsm_module(self):
        """Import live_sprint_measurement without live execution."""
        # Manipulate sys.path so benchmarks package resolves from UNVERSAL_ROOT
        root = str(UNIVERSAL_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        # Import the module (no live execution)
        import benchmarks.live_sprint_measurement as m

        return m

    @pytest.fixture
    def nam_module(self):
        """Import live_measurement_next_action without runtime deps."""
        root = str(UNIVERSAL_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import benchmarks.live_measurement_next_action as m

        return m

    def test_nam_import_succeeds(self, nam_module) -> None:
        """import benchmarks.live_measurement_next_action succeeds without runtime deps."""
        assert nam_module is not None

    def test_lsm_import_succeeds(self, lsm_module) -> None:
        """import benchmarks.live_sprint_measurement succeeds without live execution."""
        assert lsm_module is not None

    def test_lsm_derive_next_action_is_callable(self, lsm_module) -> None:
        """lsm._derive_next_action is callable."""
        assert callable(lsm_module._derive_next_action)

    def test_nam_derive_next_action_is_callable(self, nam_module) -> None:
        """next_action_module._derive_next_action is callable."""
        assert callable(nam_module._derive_next_action)

    def test_lsm_next_action_input_is_callable_ctor(self, lsm_module) -> None:
        """lsm.NextActionInput is a dataclass (constructible)."""
        # NextActionInput is a frozen dataclass — verify it exists and has fields
        assert hasattr(lsm_module, "NextActionInput")
        cls = lsm_module.NextActionInput
        assert hasattr(cls, "__dataclass_fields__")

    def test_nam_next_action_input_fields(self, nam_module) -> None:
        """NAM NextActionInput has the expected fields."""
        cls = nam_module.NextActionInput
        fields = cls.__dataclass_fields__
        required = {
            "status",
            "is_memory_gate_abort",
            "nonfeed_accepted_findings",
            "public_fetch_attempted",
            "public_findings",
            "feed_findings",
            "total_findings",
            "ct_findings",
            "runtime_truth",
        }
        actual = set(fields.keys())
        missing = required - actual
        assert not missing, f"NextActionInput missing fields: {missing}"

    def test_nam_rule_count(self) -> None:
        """next_action module defines 8 _rule helper functions."""
        src = NAM_PATH.read_text()
        tree = ast.parse(src)
        rules = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("_rule")
        ]
        assert len(rules) == 8, f"Expected 8 _rule helpers, found {len(rules)}: {rules}"

    def test_lsm_rules_re_exported(self, lsm_module) -> None:
        """live_sprint_measurement re-exports all _rule helpers from NAM."""
        for name in [
            "_rule_wallclock_enforcement",
            "_rule0b_memory_or_swap_gate",
            "_rule0g_prewindup_barrier",
            "_rule_profile_propagation",
            "_rule_terminality",
            "_rule_provider_surface",
            "_rule_quality_gate",
            "_rule_default",
        ]:
            assert hasattr(lsm_module, name), f"Missing re-export: {name}"


# ---------------------------------------------------------------------------
# Behavior assertion — same fixture through both paths returns same tuple
# ---------------------------------------------------------------------------


class TestBehaviorAssertion:
    """Identical inputs produce identical outputs via lsm and nam paths."""

    @pytest.fixture
    def nam_module(self):
        root = str(UNIVERSAL_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import benchmarks.live_measurement_next_action as m

        return m

    @pytest.fixture
    def lsm_module(self):
        root = str(UNIVERSAL_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import benchmarks.live_sprint_measurement as m

        return m

    def _make_minimal_input(self, nam_module) -> Any:
        """Build a NextActionInput with enough fields to exercise _derive_next_action."""
        # Use a minimal runtime_truth dict
        runtime_truth = {
            "branch_mix": {"public_findings": 0, "feed_findings": 0},
            "public_branch_timed_out": False,
            "feed_branch_timed_out": False,
            "cycles_started": 0,
        }
        return nam_module.NextActionInput(
            status="COMPLETE",
            is_memory_gate_abort=False,
            nonfeed_accepted_findings=0,
            public_fetch_attempted=False,
            public_findings=0,
            feed_findings=0,
            total_findings=0,
            ct_findings=0,
            runtime_truth=runtime_truth,
        )

    def test_same_input_same_output_nam_path(self, nam_module) -> None:
        """NAM path produces a well-formed (action, detail) tuple."""
        inp = self._make_minimal_input(nam_module)
        result = nam_module._derive_next_action(
            status=inp.status,
            is_memory_gate_abort=inp.is_memory_gate_abort,
            nonfeed_accepted_findings=inp.nonfeed_accepted_findings,
            public_fetch_attempted=inp.public_fetch_attempted,
            public_findings=inp.public_findings,
            feed_findings=inp.feed_findings,
            total_findings=inp.total_findings,
            ct_findings=inp.ct_findings,
            runtime_truth=inp.runtime_truth,
            feed_dominance_score=inp.feed_dominance_score,
            top_public_reject_reason=inp.top_public_reject_reason,
            nonfeed_starvation_suspected=inp.nonfeed_starvation_suspected,
            prewindup_barrier_checked=inp.prewindup_barrier_checked,
            prewindup_barrier_satisfied=inp.prewindup_barrier_satisfied,
            prewindup_required_lanes=getattr(inp, "prewindup_required_lanes", None),
            prewindup_attempted_lanes=getattr(inp, "prewindup_attempted_lanes", None),
            acquisition_strategy=getattr(inp, "acquisition_strategy", None),
            return_guard_observation=getattr(inp, "return_guard_observation", None),
            scheduler_exit=getattr(inp, "scheduler_exit", None),
            acquisition_terminality_checked=getattr(inp, "acquisition_terminality_checked", False),
            acquisition_terminality_satisfied=getattr(inp, "acquisition_terminality_satisfied", False),
            acquisition_terminality_missing_lanes=getattr(inp, "acquisition_terminality_missing_lanes", None),
            run_quality_verdict=getattr(inp, "run_quality_verdict", None),
            acquisition_prelude_checked=getattr(inp, "acquisition_prelude_checked", False),
            acquisition_prelude_ran=getattr(inp, "acquisition_prelude_ran", False),
            acquisition_prelude_required_lanes=getattr(inp, "acquisition_prelude_required_lanes", None),
            acquisition_prelude_terminal_lanes=getattr(inp, "acquisition_prelude_terminal_lanes", None),
            acquisition_prelude_missing_lanes=getattr(inp, "acquisition_prelude_missing_lanes", None),
            acquisition_prelude_skipped_lanes=getattr(inp, "acquisition_prelude_skipped_lanes", False),
            acquisition_prelude_errors=getattr(inp, "acquisition_prelude_errors", None),
            acquisition_prelude_duration_s=getattr(inp, "acquisition_prelude_duration_s", None),
            acquisition_prelude_reason=getattr(inp, "acquisition_prelude_reason", None),
            windup_guard_observation=getattr(inp, "windup_guard_observation", None),
            scheduler_deadline_enforced=getattr(inp, "scheduler_deadline_enforced", False),
            scheduler_deadline_checks=getattr(inp, "scheduler_deadline_checks", 0),
        )
        assert isinstance(result, tuple), f"result must be tuple, got {type(result)}"
        action = result[0]
        assert isinstance(action, str), f"action must be str, got {type(action)}"
        assert action == "unknown" or action.startswith("fix_") or action.startswith("clean_"), (
            f"Unexpected action: {action}"
        )

    def test_same_input_same_output_both_paths(self, nam_module, lsm_module) -> None:
        """Identical inputs produce identical (action, detail) tuples via lsm and nam."""
        # Build a fixture with mixed findings
        runtime_truth = {
            "branch_mix": {"public_findings": 3, "feed_findings": 1},
            "public_branch_timed_out": False,
            "feed_branch_timed_out": True,
            "cycles_started": 1,
        }
        inp = nam_module.NextActionInput(
            status="COMPLETE",
            is_memory_gate_abort=False,
            nonfeed_accepted_findings=2,
            public_fetch_attempted=True,
            public_findings=3,
            feed_findings=1,
            total_findings=6,
            ct_findings=4,
            runtime_truth=runtime_truth,
            feed_dominance_score=0.1,
            top_public_reject_reason=None,
            nonfeed_starvation_suspected=False,
            prewindup_barrier_checked=True,
            prewindup_barrier_satisfied=True,
            prewindup_required_lanes=["public"],
            prewindup_attempted_lanes=["public"],
            acquisition_strategy={"public": {"type": "fetch"}},
            return_guard_observation=None,
            scheduler_exit=None,
            acquisition_terminality_checked=False,
            acquisition_terminality_satisfied=False,
            acquisition_terminality_missing_lanes=None,
            run_quality_verdict=None,
            acquisition_prelude_checked=True,
            acquisition_prelude_ran=True,
            acquisition_prelude_required_lanes=None,
            acquisition_prelude_terminal_lanes=None,
            acquisition_prelude_missing_lanes=None,
            acquisition_prelude_skipped_lanes=False,
            acquisition_prelude_errors=None,
            acquisition_prelude_duration_s=0.5,
            acquisition_prelude_reason=None,
            windup_guard_observation=None,
            scheduler_deadline_enforced=False,
            scheduler_deadline_checks=0,
        )

        def call(mod, input_inp):
            return mod._derive_next_action(
                status=input_inp.status,
                is_memory_gate_abort=input_inp.is_memory_gate_abort,
                nonfeed_accepted_findings=input_inp.nonfeed_accepted_findings,
                public_fetch_attempted=input_inp.public_fetch_attempted,
                public_findings=input_inp.public_findings,
                feed_findings=input_inp.feed_findings,
                total_findings=input_inp.total_findings,
                ct_findings=input_inp.ct_findings,
                runtime_truth=input_inp.runtime_truth,
                feed_dominance_score=input_inp.feed_dominance_score,
                top_public_reject_reason=input_inp.top_public_reject_reason,
                nonfeed_starvation_suspected=input_inp.nonfeed_starvation_suspected,
                prewindup_barrier_checked=input_inp.prewindup_barrier_checked,
                prewindup_barrier_satisfied=input_inp.prewindup_barrier_satisfied,
                prewindup_required_lanes=getattr(input_inp, "prewindup_required_lanes", None),
                prewindup_attempted_lanes=getattr(input_inp, "prewindup_attempted_lanes", None),
                acquisition_strategy=getattr(input_inp, "acquisition_strategy", None),
                return_guard_observation=getattr(input_inp, "return_guard_observation", None),
                scheduler_exit=getattr(input_inp, "scheduler_exit", None),
                acquisition_terminality_checked=getattr(input_inp, "acquisition_terminality_checked", False),
                acquisition_terminality_satisfied=getattr(input_inp, "acquisition_terminality_satisfied", False),
                acquisition_terminality_missing_lanes=getattr(input_inp, "acquisition_terminality_missing_lanes", None),
                run_quality_verdict=getattr(input_inp, "run_quality_verdict", None),
                acquisition_prelude_checked=getattr(input_inp, "acquisition_prelude_checked", False),
                acquisition_prelude_ran=getattr(input_inp, "acquisition_prelude_ran", False),
                acquisition_prelude_required_lanes=getattr(input_inp, "acquisition_prelude_required_lanes", None),
                acquisition_prelude_terminal_lanes=getattr(input_inp, "acquisition_prelude_terminal_lanes", None),
                acquisition_prelude_missing_lanes=getattr(input_inp, "acquisition_prelude_missing_lanes", None),
                acquisition_prelude_skipped_lanes=getattr(input_inp, "acquisition_prelude_skipped_lanes", False),
                acquisition_prelude_errors=getattr(input_inp, "acquisition_prelude_errors", None),
                acquisition_prelude_duration_s=getattr(input_inp, "acquisition_prelude_duration_s", None),
                acquisition_prelude_reason=getattr(input_inp, "acquisition_prelude_reason", None),
                windup_guard_observation=getattr(input_inp, "windup_guard_observation", None),
                scheduler_deadline_enforced=getattr(input_inp, "scheduler_deadline_enforced", False),
                scheduler_deadline_checks=getattr(input_inp, "scheduler_deadline_checks", 0),
            )

        nam_result = call(nam_module, inp)
        lsm_result = call(lsm_module, inp)

        assert nam_result == lsm_result, f"Behavior mismatch:\n  NAM result: {nam_result}\n  LSM result: {lsm_result}"

    def test_was_family_attempted_both_paths(self, nam_module, lsm_module) -> None:
        """_was_family_attempted returns same result via both modules."""
        rt = {"branch_mix": {"public_findings": 5}, "public_branch_timed_out": False}
        assert nam_module._was_family_attempted(rt, "public") == lsm_module._was_family_attempted(rt, "public")
        assert nam_module._was_family_attempted(rt, "feed") == lsm_module._was_family_attempted(rt, "feed")
        # Timed-out case
        rt2 = {"branch_mix": {}, "public_branch_timed_out": True}
        assert nam_module._was_family_attempted(rt2, "public") == lsm_module._was_family_attempted(rt2, "public")

"""
Sprint F206J: Architecture Seal Tests
=====================================

Verifies F206 series seal — architectural verdicts, scheduler decomposition,
regression matrix, and documentation completeness.

Invariant mapping:
  F206J-1  | shadow_inputs.py has VERDICT block
  F206J-2  | shadow_parity.py has VERDICT block
  F206J-3  | shadow_pre_decision.py has VERDICT block
  F206J-4  | windup_engine.py has VERDICT block
  F206J-5  | SprintLifecycleRunner is imported in sprint_scheduler
  F206J-6  | SprintAdvisoryRunner is imported in sprint_scheduler
  F206J-7  | SidecarDispatcher is imported in sprint_scheduler
  F206J-8  | run_baseline.py accepts --profile f206-regression
  F206J-9  | F206_REGRESSION_LANES includes all 9 F206 probe lanes
  F206J-10 | REAL_ARCHITECTURE.md has section for each of F206A through F206J
  F206J-11 | LONGTERM_PLAN.md has F206 section
  F206J-12 | All 9 F206 probe lane directories exist
  F206J-13 | GHOST_INVARIANTS: gather calls use return_exceptions=True
  F206J-14 | knowledge/target_memory.py exports MAX_DRIFT_REASONS and MAX_DRIFT_DELTA_KEYS
  F206J-15 | knowledge/graph_service.py exports MAX_GRAPH_ANALYTICS_TOP_K and MAX_GRAPH_ANALYTICS_NODES
  F206J-16 | runtime/sidecar_bus.py exports SIDECAR_STAGES
  F206J-17 | runtime/sidecar_dispatcher.py exports DispatchOutcome
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ============================================================================
# F206J-1/2/3/4: Shadow module verdict blocks
# ============================================================================


class TestShadowVerdictBlocks:
    """Verify each shadow module has a VERDICT block."""

    SHADOW_MODULES = [
        ("runtime/shadow_inputs.py", "ACTIVE"),
        ("runtime/shadow_parity.py", "ACTIVE"),
        ("runtime/shadow_pre_decision.py", "ACTIVE"),
        ("runtime/windup_engine.py", "DORMANT"),
    ]

    @pytest.mark.parametrize("module_path,expected_verdict", SHADOW_MODULES)
    def test_verdict_block_exists(self, module_path: str, expected_verdict: str):
        """F206J-1/2/3/4: Module has VERDICT block with expected verdict."""
        full_path = Path(__file__).parent.parent.parent / module_path
        assert full_path.exists(), f"Module not found: {full_path}"
        content = full_path.read_text()
        assert "VERDICT" in content, f"No VERDICT block in {module_path}"
        assert expected_verdict in content, (
            f"Expected verdict '{expected_verdict}' not found in {module_path}"
        )


# ============================================================================
# F206J-5/6/7: Scheduler decomposition imports
# ============================================================================


class TestSchedulerDecompositionImports:
    """Verify extracted runners are imported by SprintScheduler."""

    def test_lifecycle_runner_imported(self):
        """F206J-5: SprintLifecycleRunner is imported in sprint_scheduler."""
        scheduler_path = (
            Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        )
        content = scheduler_path.read_text()
        assert "SprintLifecycleRunner" in content, (
            "SprintLifecycleRunner not imported in sprint_scheduler"
        )
        assert "from" in content and "sprint_lifecycle_runner" in content, (
            "sprint_lifecycle_runner import not found"
        )

    def test_advisory_runner_imported(self):
        """F206J-6: SprintAdvisoryRunner is imported in sprint_scheduler."""
        scheduler_path = (
            Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        )
        content = scheduler_path.read_text()
        assert "SprintAdvisoryRunner" in content, (
            "SprintAdvisoryRunner not imported in sprint_scheduler"
        )
        assert "sprint_advisory_runner" in content, (
            "sprint_advisory_runner import not found"
        )

    def test_sidecar_dispatcher_imported(self):
        """F206J-7: SidecarDispatcher is imported in sprint_scheduler."""
        scheduler_path = (
            Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        )
        content = scheduler_path.read_text()
        assert "SidecarDispatcher" in content, (
            "SidecarDispatcher not imported in sprint_scheduler"
        )
        assert "sidecar_dispatcher" in content, (
            "sidecar_dispatcher import not found"
        )


# ============================================================================
# F206J-8/9: Baseline runner f206-regression profile
# ============================================================================


class TestBaselineRegressionProfile:
    """Verify f206-regression profile is wired."""

    def test_f206_regression_profile_accepted(self):
        """F206J-8: run_baseline.py accepts --profile f206-regression."""
        baseline_path = Path(__file__).parent.parent.parent / "run_baseline.py"
        content = baseline_path.read_text()
        assert 'f206-regression' in content, (
            "f206-regression not found in run_baseline.py"
        )

    def test_f206_regression_lanes_list_complete(self):
        """F206J-9: F206_REGRESSION_LANES includes all 9 F206 lanes."""
        baseline_path = Path(__file__).parent.parent.parent / "run_baseline.py"
        content = baseline_path.read_text()

        # Verify F206_REGRESSION_LANES is defined and includes all 9 lanes
        expected_lanes = [
            "probe_f206a", "probe_f206b", "probe_f206c",
            "probe_f206d", "probe_f206e", "probe_f206f",
            "probe_f206g", "probe_f206h", "probe_f206i",
        ]
        for lane in expected_lanes:
            assert f'"{lane}"' in content, f"Lane {lane} missing from run_baseline.py"
        # F206_REGRESSION_LANES = GREEN_PROBE_LANES + F206_PROBE_LANES
        assert "F206_PROBE_LANES" in content, "F206_PROBE_LANES not defined in run_baseline.py"
        assert "F206_REGRESSION_LANES" in content, "F206_REGRESSION_LANES not defined in run_baseline.py"


# ============================================================================
# F206J-10: REAL_ARCHITECTURE.md sections
# ============================================================================


class TestArchitectureDocSections:
    """Verify REAL_ARCHITECTURE.md has all F206 sections."""

    REQUIRED_SECTIONS = [
        "F206A",
        "F206B",
        "F206C",
        "F206D",
        "F206E",
        "F206F",
        "F206G",
        "F206H",
        "F206I",
        "F206J",
    ]

    def test_real_architecture_has_all_f206_sections(self):
        """F206J-10: REAL_ARCHITECTURE.md has section for each of F206A-J."""
        arch_path = Path(__file__).parent.parent.parent / "REAL_ARCHITECTURE.md"
        content = arch_path.read_text()
        for section in self.REQUIRED_SECTIONS:
            assert f"## {section}" in content, (
                f"Section {section} not found in REAL_ARCHITECTURE.md"
            )


# ============================================================================
# F206J-11: LONGTERM_PLAN.md F206 section
# ============================================================================


class TestLongTermPlanF206:
    """Verify LONGTERM_PLAN.md has F206 section."""

    def test_longterm_plan_has_f206_section(self):
        """F206J-11: LONGTERM_PLAN.md has F206 section."""
        plan_path = Path(__file__).parent.parent.parent / "LONGTERM_PLAN.md"
        content = plan_path.read_text()
        assert "F206A" in content or "F206" in content, (
            "F206 section not found in LONGTERM_PLAN.md"
        )
        assert "F206A–F206J" in content or "F206A–I" in content, (
            "F206 series entry not found in LONGTERM_PLAN.md"
        )


# ============================================================================
# F206J-12: All 9 F206 probe lane directories exist
# ============================================================================


class TestF206ProbeLanesExist:
    """Verify all 9 F206 probe lane directories exist."""

    F206_LANES = [
        "probe_f206a", "probe_f206b", "probe_f206c",
        "probe_f206d", "probe_f206e", "probe_f206f",
        "probe_f206g", "probe_f206h", "probe_f206i",
    ]

    def test_all_f206_lane_dirs_exist(self):
        """F206J-12: All 9 F206 probe lane directories exist."""
        tests_root = Path(__file__).parent.parent.parent / "tests"
        for lane in self.F206_LANES:
            lane_path = tests_root / lane
            assert lane_path.exists(), f"Probe lane directory not found: {lane}"
            assert lane_path.is_dir(), f"Expected directory: {lane}"


# ============================================================================
# F206J-13: GHOST_INVARIANTS — gather uses return_exceptions=True
# ============================================================================


class TestGhostInvariants:
    """Verify GHOST_INVARIANTS are enforced."""

    def test_gather_calls_use_return_exceptions(self):
        """F206J-13: asyncio.gather calls use return_exceptions=True."""
        scheduler_path = (
            Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        )
        content = scheduler_path.read_text()

        # Parse the file to find all asyncio.gather calls
        tree = ast.parse(content)

        gather_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "gather"
                ):
                    # Check if return_exceptions is passed
                    has_return_exceptions = any(
                        kw.arg == "return_exceptions"
                        for kw in node.keywords
                    )
                    gather_calls.append(has_return_exceptions)

        assert len(gather_calls) > 0, "No asyncio.gather calls found in sprint_scheduler"
        # All gather calls should use return_exceptions=True
        assert all(gather_calls), (
            f"Found {sum(1 for g in gather_calls if not g)} gather calls without "
            "return_exceptions=True"
        )


# ============================================================================
# F206J-14: target_memory bounds exports
# ============================================================================


class TestTargetMemoryBounds:
    """Verify target_memory exports F206H bounds."""

    def test_max_drift_reasons_exported(self):
        """F206J-14: MAX_DRIFT_REASONS exported from target_memory."""
        from hledac.universal.knowledge import target_memory

        assert hasattr(target_memory, "MAX_DRIFT_REASONS"), (
            "MAX_DRIFT_REASONS not exported from target_memory"
        )
        assert target_memory.MAX_DRIFT_REASONS > 0, (
            "MAX_DRIFT_REASONS must be positive"
        )

    def test_max_drift_delta_keys_exported(self):
        """F206J-14: MAX_DRIFT_DELTA_KEYS exported from target_memory."""
        from hledac.universal.knowledge import target_memory

        assert hasattr(target_memory, "MAX_DRIFT_DELTA_KEYS"), (
            "MAX_DRIFT_DELTA_KEYS not exported from target_memory"
        )
        assert target_memory.MAX_DRIFT_DELTA_KEYS > 0, (
            "MAX_DRIFT_DELTA_KEYS must be positive"
        )


# ============================================================================
# F206J-15: graph_service bounds exports
# ============================================================================


class TestGraphServiceBounds:
    """Verify graph_service exports F206G bounds."""

    def test_max_graph_analytics_top_k_exported(self):
        """F206J-15: MAX_GRAPH_ANALYTICS_TOP_K exported from graph_service."""
        from hledac.universal.knowledge import graph_service

        assert hasattr(graph_service, "MAX_GRAPH_ANALYTICS_TOP_K"), (
            "MAX_GRAPH_ANALYTICS_TOP_K not exported from graph_service"
        )
        assert graph_service.MAX_GRAPH_ANALYTICS_TOP_K > 0

    def test_max_graph_analytics_nodes_exported(self):
        """F206J-15: MAX_GRAPH_ANALYTICS_NODES exported from graph_service."""
        from hledac.universal.knowledge import graph_service

        assert hasattr(graph_service, "MAX_GRAPH_ANALYTICS_NODES"), (
            "MAX_GRAPH_ANALYTICS_NODES not exported from graph_service"
        )
        assert graph_service.MAX_GRAPH_ANALYTICS_NODES > 0


# ============================================================================
# F206J-16: sidecar_bus SIDECAR_STAGES export
# ============================================================================


class TestSidecarBusExports:
    """Verify sidecar_bus exports SIDECAR_STAGES."""

    def test_sidecar_stages_exported(self):
        """F206J-16: SIDECAR_STAGES exported from sidecar_bus."""
        from hledac.universal.runtime import sidecar_bus

        assert hasattr(sidecar_bus, "SIDECAR_STAGES"), (
            "SIDECAR_STAGES not exported from sidecar_bus"
        )
        assert isinstance(sidecar_bus.SIDECAR_STAGES, tuple), (
            "SIDECAR_STAGES must be a tuple"
        )
        assert len(sidecar_bus.SIDECAR_STAGES) == 3, (
            "SIDECAR_STAGES must have 3 stages"
        )


# ============================================================================
# F206J-17: sidecar_dispatcher DispatchOutcome export
# ============================================================================


class TestSidecarDispatcherExports:
    """Verify sidecar_dispatcher exports DispatchOutcome."""

    def test_dispatch_outcome_exported(self):
        """F206J-17: DispatchOutcome exported from sidecar_dispatcher."""
        from hledac.universal.runtime import sidecar_dispatcher

        assert hasattr(sidecar_dispatcher, "DispatchOutcome"), (
            "DispatchOutcome not exported from sidecar_dispatcher"
        )


# ============================================================================
# Smoke: module imports
# ============================================================================


class TestSmoke:
    """F206J-SMOKE: All F206J-related modules import without error."""

    def test_shadow_inputs_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import shadow_inputs  # noqa: F401

    def test_shadow_parity_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import shadow_parity  # noqa: F401

    def test_shadow_pre_decision_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import shadow_pre_decision  # noqa: F401

    def test_windup_engine_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import windup_engine  # noqa: F401

    def test_sprint_lifecycle_runner_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import sprint_lifecycle_runner  # noqa: F401

    def test_sprint_advisory_runner_imports(self):
        """Module imports without error."""
        from hledac.universal.runtime import sprint_advisory_runner  # noqa: F401

    def test_target_memory_imports(self):
        """Module imports without error."""
        from hledac.universal.knowledge import target_memory  # noqa: F401

    def test_graph_service_imports(self):
        """Module imports without error."""
        from hledac.universal.knowledge import graph_service  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

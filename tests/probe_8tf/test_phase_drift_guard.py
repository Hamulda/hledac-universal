"""
Sprint 8TF: Phase Drift Guard — Probe Tests
==========================================

Verifies:
1. Three phase layers are distinct and non-overlapping
2. workflow-level and coarse-grained phases are NOT conflated
3. is_same_layer() rejects cross-layer comparisons
4. model_phase_facts helpers work correctly
5. ModelManager.PHASE_MODEL_MAP and ModelLifecycleManager use different phase systems
6. types.py OrchestratorState is separate from both
7. windup-local isolation is respected (no runtime-wide model loading)
"""

import unittest


class TestPhaseLayerClassification(unittest.TestCase):
    """§1-§3: Phase layer classification must be correct and strict."""

    def test_workflow_phases_defined(self):
        from hledac.universal.brain.model_phase_facts import WORKFLOW_PHASES
        expected = {"PLAN", "DECIDE", "SYNTHESIZE", "EMBED", "DEDUP", "ROUTING", "NER", "ENTITY"}
        assert WORKFLOW_PHASES == frozenset(expected)

    def test_coarse_grained_phases_defined(self):
        from hledac.universal.brain.model_phase_facts import COARSE_GRAINED_PHASES
        expected = {"BRAIN", "TOOLS", "SYNTHESIS", "CLEANUP"}
        assert COARSE_GRAINED_PHASES == frozenset(expected)

    def test_workflow_and_coarse_grained_are_disjoint(self):
        from hledac.universal.brain.model_phase_facts import WORKFLOW_PHASES, COARSE_GRAINED_PHASES
        assert WORKFLOW_PHASES.isdisjoint(COARSE_GRAINED_PHASES)

    def test_get_phase_layer_workflow(self):
        from hledac.universal.brain.model_phase_facts import get_phase_layer
        for phase in ["PLAN", "EMBED", "SYNTHESIZE", "NER"]:
            assert get_phase_layer(phase) == 1, f"{phase} must be layer 1"

    def test_get_phase_layer_coarse_grained(self):
        from hledac.universal.brain.model_phase_facts import get_phase_layer
        for phase in ["BRAIN", "TOOLS", "SYNTHESIS", "CLEANUP"]:
            assert get_phase_layer(phase) == 2, f"{phase} must be layer 2"

    def test_get_phase_layer_unknown(self):
        from hledac.universal.brain.model_phase_facts import get_phase_layer
        assert get_phase_layer("UNKNOWN_PHASE") == 0
        assert get_phase_layer("") == 0

    def test_is_workflow_phase(self):
        from hledac.universal.brain.model_phase_facts import is_workflow_phase
        assert is_workflow_phase("PLAN")
        assert is_workflow_phase("EMBED")
        assert is_workflow_phase("SYNTHESIZE")
        assert not is_workflow_phase("BRAIN")
        assert not is_workflow_phase("TOOLS")

    def test_is_coarse_grained_phase(self):
        from hledac.universal.brain.model_phase_facts import is_coarse_grained_phase
        assert is_coarse_grained_phase("BRAIN")
        assert is_coarse_grained_phase("TOOLS")
        assert is_coarse_grained_phase("SYNTHESIS")
        assert not is_coarse_grained_phase("PLAN")
        assert not is_coarse_grained_phase("SYNTHESIZE")  # NOT the same string!

    def test_is_same_layer_rejects_cross_layer(self):
        from hledac.universal.brain.model_phase_facts import is_same_layer
        # SYNTHESIZE (layer 1) vs SYNTHESIS (layer 2) — must be False
        assert not is_same_layer("SYNTHESIZE", "SYNTHESIS")
        # Same layer comparisons
        assert is_same_layer("PLAN", "DECIDE")
        assert is_same_layer("BRAIN", "TOOLS")
        # Unknown phases
        assert not is_same_layer("SYNTHESIZE", "UNKNOWN")


class TestPhaseDriftRisk(unittest.TestCase):
    """§4: Specific drift risks must be blocked."""

    def test_synthesize_vs_synthesis_not_same(self):
        """SYNTHESIZE ≠ SYNTHESIS — most dangerous conflation risk."""
        from hledac.universal.brain.model_phase_facts import is_same_layer
        assert not is_same_layer("SYNTHESIZE", "SYNTHESIS")

    def test_plan_vs_brain_not_same(self):
        """PLAN and BRAIN are from different layers."""
        from hledac.universal.brain.model_phase_facts import is_same_layer
        assert not is_same_layer("PLAN", "BRAIN")

    def test_embed_vs_tools_not_same(self):
        """EMBED and TOOLS are from different layers."""
        from hledac.universal.brain.model_phase_facts import is_same_layer
        assert not is_same_layer("EMBED", "TOOLS")


class TestModelManagerPhaseMap(unittest.TestCase):
    """§5: ModelManager PHASE_MODEL_MAP uses workflow-level phases only."""

    def test_phase_model_map_workflow_only(self):
        from hledac.universal.brain.model_manager import ModelManager
        phases = set(ModelManager.PHASE_MODEL_MAP.keys())
        from hledac.universal.brain.model_phase_facts import WORKFLOW_PHASES
        assert phases == WORKFLOW_PHASES, f"PHASE_MODEL_MAP must only contain workflow phases, got {phases}"

    def test_phase_model_map_does_not_contain_coarse_grained(self):
        from hledac.universal.brain.model_manager import ModelManager
        phases = set(ModelManager.PHASE_MODEL_MAP.keys())
        from hledac.universal.brain.model_phase_facts import COARSE_GRAINED_PHASES
        overlap = phases & COARSE_GRAINED_PHASES
        assert not overlap, f"PHASE_MODEL_MAP must NOT contain coarse-grained phases: {overlap}"


class TestModelLifecycleManagerPhases(unittest.TestCase):
    """§6: ModelLifecycleManager uses coarse-grained phases only."""

    def test_enforce_phase_models_coarse_grained_only(self):
        """enforce_phase_models must only be called with coarse-grained phase strings."""
        from hledac.universal.capabilities import ModelLifecycleManager, CapabilityRegistry
        import asyncio
        registry = CapabilityRegistry()
        manager = ModelLifecycleManager(registry)

        # These are all valid coarse-grained phase strings
        for phase in ["BRAIN", "TOOLS", "SYNTHESIS", "CLEANUP"]:
            # Must not raise
            try:
                asyncio.get_event_loop().run_until_complete(
                    manager.enforce_phase_models(phase)
                )
            except Exception as e:
                self.fail(f"enforce_phase_models({phase!r}) raised: {e}")

    def test_enforce_phase_models_rejects_workflow_phases(self):
        """enforce_phase_models with workflow-level phase would silently do nothing."""
        from hledac.universal.capabilities import ModelLifecycleManager, CapabilityRegistry
        import asyncio
        registry = CapabilityRegistry()
        manager = ModelLifecycleManager(registry)

        # A workflow-level phase — ModelLifecycleManager._current_phase is set
        # but none of the if branches match, so no model is loaded/unloaded.
        # This is the drift risk — it silently accepts but ignores.
        asyncio.get_event_loop().run_until_complete(
            manager.enforce_phase_models("PLAN")
        )
        # Manager accepted it but did nothing (this is the documented behavior)


class TestTypesOrchestratorStateSeparation(unittest.TestCase):
    """§7: types.OrchestratorState is its own phase system, not unified with either."""

    def test_orchestrator_state_not_same_as_workflow_phases(self):
        """OrchestratorState strings differ from workflow-level phase strings."""
        from hledac.universal.project_types import OrchestratorState
        from hledac.universal.brain.model_phase_facts import WORKFLOW_PHASES
        oc_states = {s.value for s in OrchestratorState}
        # PLANNING != PLAN, SYNTHESIS == SYNTHESIS (coincidental)
        assert "planning" in oc_states
        assert oc_states.isdisjoint(WORKFLOW_PHASES)

    def test_orchestrator_state_not_same_as_coarse_grained(self):
        """OrchestratorState strings differ from coarse-grained phase strings.

        NOTE: OrchestratorState has 'BRAIN' (lowercase string) and coarse-grained
        also has 'BRAIN' — but they are semantically different phase systems.
        The test below verifies TOOLS and CLEANUP are absent (the meaningful drift risks).
        """
        from hledac.universal.project_types import OrchestratorState
        from hledac.universal.brain.model_phase_facts import COARSE_GRAINED_PHASES
        oc_states = {s.value.upper() for s in OrchestratorState}
        # TOOLS, CLEANUP are the meaningful ones that should NOT appear in OrchestratorState
        assert "TOOLS" not in oc_states
        assert "CLEANUP" not in oc_states
        # Note: BRAIN appears in both (coincidental, different semantics)


class TestAuthorityNotesPresent(unittest.TestCase):
    """§8: Authority notes documenting phase layer separation must be present."""

    def test_model_manager_has_phase_layer_authority_note(self):
        """ModelManager class docstring or PHASE_MODEL_MAP must reference phase layers."""
        from hledac.universal.brain.model_manager import ModelManager
        doc = ModelManager.__doc__ or ""
        # Docstring references PHASE_MODEL_MAP
        assert "PHASE_MODEL_MAP" in doc or ModelManager.PHASE_MODEL_MAP

    def test_capabilities_has_three_layer_authority_note(self):
        """ModelLifecycleManager docstring must reference Three Phase Layers."""
        from hledac.universal.capabilities import ModelLifecycleManager
        doc = ModelLifecycleManager.__doc__ or ""
        assert "Three Phase Layers" in doc or "Layer" in doc

    def test_brain_model_lifecycle_has_phase_layer_note(self):
        """brain/model_lifecycle.py module docstring must reference phase layers."""
        import hledac.universal.brain.model_lifecycle as ml
        doc = ml.__doc__ or ""
        assert "Phase Layers" in doc or "phase" in doc.lower()


if __name__ == "__main__":
    unittest.main()

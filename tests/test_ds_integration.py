"""
Test Dempster-Shafer integration with HypothesisEngine.

Tests DS second-opinion channel:
1. DS bug fix: supporting evidence → belief('support') grows, belief('conflict') not
2. DS contradiction: more conflicting than supporting → has_contradiction = True
3. DS backward compat: use_dempster_shafer=False → to_dict() no ds_* keys
4. DS conflict mass: conflict_mass() is float 0.0-1.0 after mixed evidence
"""

from brain.hypothesis_engine import Hypothesis, HypothesisEngine


class TestDempsterShaferIntegration:
    """Test DS second-opinion channel integration."""

    def test_ds_active_by_default(self):
        """
        HypothesisEngine() without arguments → _ds_engine is not None.
        Verifies use_dempster_shafer=True is the new default.
        """
        engine = HypothesisEngine()
        assert engine._ds_engine is not None, (
            "DS engine should be active by default (use_dempster_shafer=True)"
        )

    def test_ds_bug_fix_supporting_evidence_routes_correctly(self):
        """
        Supporting evidence should add mass to 'support' hypothesis in DS engine,
        not to 'conflict'.
        """
        engine = HypothesisEngine(use_dempster_shafer=True)
        hyp = Hypothesis(
            id="test-support",
            statement="Test hypothesis",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        # Inject ds_engine reference (as HypothesisEngine does when tracking)
        hyp._ds_engine = engine._ds_engine
        engine._hypotheses["test-support"] = hyp

        # Add supporting evidence
        hyp.add_supporting_evidence("e1", weight=0.8)

        ds = engine._ds_engine
        # Supporting evidence should increase 'support' belief
        assert ds.belief("support") > 0.0, "DS belief('support') should grow from supporting evidence"

        # Add more supporting - 'support' should continue to grow
        belief_before = ds.belief("support")
        hyp.add_supporting_evidence("e2", weight=0.3)
        assert ds.belief("support") > belief_before, "DS belief('support') should increase further"

    def test_ds_contradiction_detected(self):
        """
        When conflicting evidence exceeds supporting evidence,
        has_contradiction should be True.
        """
        engine = HypothesisEngine(use_dempster_shafer=True, ds_contradiction_threshold=0.5)
        hyp = Hypothesis(
            id="test矛盾",
            statement="Test contradiction",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        hyp._ds_engine = engine._ds_engine
        engine._hypotheses["test矛盾"] = hyp

        # Add more conflicting than supporting
        hyp.add_conflicting_evidence("c1", weight=1.0)
        hyp.add_conflicting_evidence("c2", weight=1.0)
        hyp.add_conflicting_evidence("c3", weight=1.0)
        hyp.add_supporting_evidence("s1", weight=0.1)  # weak support only

        # Engine's has_contradiction should be True
        assert engine.has_contradiction is True, "Engine should detect contradiction when conflict dominates"
        # Direct DS check
        assert engine._ds_engine.detect_contradiction(threshold=0.5) is True

    def test_ds_backward_compat_no_ds_keys_when_disabled(self):
        """
        When use_dempster_shafer=False, Hypothesis.to_dict() should NOT
        include ds_belief_support, ds_belief_conflict, ds_conflict_mass, ds_contradiction.
        """
        engine = HypothesisEngine(use_dempster_shafer=False)
        hyp = Hypothesis(
            id="test-back compat",
            statement="Test backward compat",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        engine._hypotheses["test-back compat"] = hyp

        # to_dict without ds_engine should not have ds_* keys
        result = hyp.to_dict()
        assert "ds_belief_support" not in result
        assert "ds_belief_conflict" not in result
        assert "ds_conflict_mass" not in result
        assert "ds_contradiction" not in result

    def test_ds_conflict_mass_is_bounded_float(self):
        """
        After mixed evidence, conflict_mass() should return float in [0.0, 1.0].
        """
        engine = HypothesisEngine(use_dempster_shafer=True)
        hyp = Hypothesis(
            id="test-conflict-float",
            statement="Test conflict mass bounds",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        hyp._ds_engine = engine._ds_engine
        engine._hypotheses["test-conflict-float"] = hyp

        hyp.add_supporting_evidence("s1", weight=0.5)
        hyp.add_conflicting_evidence("c1", weight=0.5)

        ds = engine._ds_engine
        conflict = ds.conflict_mass()

        assert isinstance(conflict, float), f"conflict_mass should be float, got {type(conflict)}"
        assert 0.0 <= conflict <= 1.0, f"conflict_mass should be in [0,1], got {conflict}"

    def test_ds_to_dict_includes_fields_when_enabled(self):
        """
        When to_dict(ds_engine=...) is called with a DS engine,
        ds_* fields should be present in output.
        """
        engine = HypothesisEngine(use_dempster_shafer=True)
        hyp = Hypothesis(
            id="test-to-dict",
            statement="Test to_dict with DS",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        hyp._ds_engine = engine._ds_engine
        engine._hypotheses["test-to-dict"] = hyp

        hyp.add_supporting_evidence("s1", weight=0.6)

        result = hyp.to_dict(ds_engine=engine._ds_engine)

        assert "ds_belief_support" in result
        assert "ds_belief_conflict" in result
        assert "ds_conflict_mass" in result
        assert "ds_contradiction" in result
        assert isinstance(result["ds_belief_support"], float)
        assert isinstance(result["ds_contradiction"], bool)

    def test_ds_belief_no_contradiction_when_supportDominates(self):
        """
        When supporting evidence dominates, has_contradiction should be False.
        """
        engine = HypothesisEngine(use_dempster_shafer=True, ds_contradiction_threshold=0.5)
        hyp = Hypothesis(
            id="test-no矛盾",
            statement="Test no contradiction",
            hypothesis_type="existence",
            prior_probability=0.5,
            posterior_probability=0.5,
            confidence=0.5,
            supporting_evidence=[],
            conflicting_evidence=[],
        )
        hyp._ds_engine = engine._ds_engine
        engine._hypotheses["test-no矛盾"] = hyp

        # Add strong supporting, weak conflicting
        hyp.add_supporting_evidence("s1", weight=1.0)
        hyp.add_supporting_evidence("s2", weight=1.0)
        hyp.add_conflicting_evidence("c1", weight=0.1)

        assert engine.has_contradiction is False
        assert engine._ds_engine.detect_contradiction(threshold=0.5) is False

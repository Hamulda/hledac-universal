"""
F26X: Dataclass → msgspec.Struct Migration Tests
================================================

Tests for migrated types in:
- brain/hypothesis_engine/_types.py
- runtime/acquisition_strategy.py

Run: pytest tests/test_f26x_dataclass_migration.py -v
"""

from datetime import UTC, datetime

import msgspec
import pytest


class TestHypothesisEngineTypes:
    """Tests for brain/hypothesis_engine/_types.py migrations."""

    def test_evidence_is_msgspec_struct(self) -> None:
        from brain.hypothesis_engine._types import Evidence

        assert issubclass(Evidence, msgspec.Struct)

    def test_evidence_instantiation(self) -> None:
        from brain.hypothesis_engine._types import Evidence

        e = Evidence("id1", "src", "content", datetime.now(UTC), 0.9, 0.8, {"k": "v"})
        assert e.evidence_id == "id1"
        assert e.source == "src"
        assert e.reliability == 0.9
        assert e.metadata == {"k": "v"}

    def test_evidence_mutable(self) -> None:
        from brain.hypothesis_engine._types import Evidence

        e = Evidence("id1", "src", "content", datetime.now(UTC))
        e.metadata["new"] = "value"
        assert e.metadata["new"] == "value"

    def test_evidence_json_roundtrip(self) -> None:
        from brain.hypothesis_engine._types import Evidence

        e = Evidence("id1", "src", "content", datetime.now(UTC), 0.9, 0.8)
        encoded = msgspec.json.encode(e)
        decoded = msgspec.json.decode(encoded, type=Evidence)
        assert decoded.evidence_id == "id1"
        assert decoded.reliability == 0.9

    def test_dark_query_is_frozen(self) -> None:
        from brain.hypothesis_engine._types import DarkQuery, DarkQueryType

        dq = DarkQuery(DarkQueryType.ONION, "test", 0.5, ("ioc1",), "reason")
        with pytest.raises(AttributeError):
            dq.priority = 0.9

    def test_dark_query_tuple_default(self) -> None:
        from brain.hypothesis_engine._types import DarkQuery, DarkQueryType

        dq = DarkQuery(DarkQueryType.IPFS, "query", 0.5)
        assert dq.source_iocs == ()

    def test_falsification_result(self) -> None:
        from brain.hypothesis_engine._types import FalsificationResult

        fr = FalsificationResult(True, 0.7, ["ce1"], "reason")
        assert fr.falsified is True
        assert fr.counter_evidence == ["ce1"]

    def test_test_design_default_factory(self) -> None:
        from brain.hypothesis_engine._types import TestDesign

        td = TestDesign("type", "desc")
        assert td.required_data == []
        td.required_data.append("data1")
        assert td.required_data == ["data1"]

    def test_causal_entity_frozen(self) -> None:
        from brain.hypothesis_engine._types import CausalEntity

        ce = CausalEntity("eid", "ip", "192.168.1.1", (), 0.0, 0.0)
        with pytest.raises(AttributeError):
            ce.entity_id = "new"

    def test_anomaly_signal_frozen(self) -> None:
        from brain.hypothesis_engine._types import AnomalySignal

        sig = AnomalySignal("cross_domain", ("e1",), ("s1",), ("s2",), 0.5)
        with pytest.raises(AttributeError):
            sig.score = 0.9

    def test_event_mutable(self) -> None:
        from brain.hypothesis_engine._types import Event

        ev = Event("eid", "desc", datetime.now(UTC), "src", {})
        ev.metadata["key"] = "val"
        assert ev.metadata["key"] == "val"

    def test_contradiction_timestamp_default(self) -> None:
        from brain.hypothesis_engine._types import Contradiction

        c = Contradiction("a", "b", "factual", 0.5)
        assert isinstance(c.detected_at, datetime)

    def test_adversarial_report_nested_structs(self) -> None:
        from brain.hypothesis_engine._types import AdversarialReport, Evidence

        e = Evidence("id", "src", "content", datetime.now(UTC))
        ar = AdversarialReport(
            hypothesis="test hyp",
            supporting_evidence=[e],
            contradicting_evidence=[],
            credibility_assessment={},
            contradictions_found=[],
            temporal_consistency=True,
            overall_confidence=0.7,
            devil_advocate_score=0.3,
        )
        assert len(ar.supporting_evidence) == 1
        assert ar.overall_confidence == 0.7

    def test_source_credibility_still_dataclass(self) -> None:
        """SourceCredibility kept as dataclass (has runtime update method)."""
        from dataclasses import is_dataclass

        from brain.hypothesis_engine._types import SourceCredibility

        assert is_dataclass(SourceCredibility)

    def test_test_result_still_dataclass(self) -> None:
        """TestResult kept as dataclass (has __post_init__)."""
        from dataclasses import is_dataclass

        from brain.hypothesis_engine._types import TestResult

        assert is_dataclass(TestResult)


class TestAcquisitionStrategyTypes:
    """Tests for runtime/acquisition_strategy.py migrations."""

    def test_acquisition_lane_plan_is_msgspec(self) -> None:
        from runtime.acquisition_strategy import AcquisitionLanePlan

        assert issubclass(AcquisitionLanePlan, msgspec.Struct)

    def test_acquisition_lane_plan_frozen(self) -> None:
        from runtime.acquisition_strategy import AcquisitionLanePlan

        plan = AcquisitionLanePlan("FEED", True, "test", 50, 30, 2, "low")
        with pytest.raises(AttributeError):
            plan.max_items = 100

    def test_acquisition_lane_plan_defaults(self) -> None:
        from runtime.acquisition_strategy import AcquisitionLanePlan

        plan = AcquisitionLanePlan("FEED", True, "test")
        assert plan.max_items == 50
        assert plan.timeout_s == 30
        assert plan.concurrency == 2
        assert plan.risk_level == "medium"

    def test_lane_spec_frozen(self) -> None:
        from runtime.acquisition_strategy import LaneSpec

        spec = LaneSpec(50, 30, "low")
        with pytest.raises(AttributeError):
            spec.max_items = 100

    def test_lane_rule_is_msgspec(self) -> None:
        from runtime.acquisition_strategy import AcquisitionContext, LaneRule, LaneSpec, RiskLevel

        spec = LaneSpec(50, 30, RiskLevel.LOW)

        def enabled(_: AcquisitionContext) -> bool:
            return True

        def reason(_: AcquisitionContext) -> str:
            return "test"

        def concurrency(_: AcquisitionContext) -> int:
            return 2

        rule = LaneRule("FEED", spec, enabled, reason, concurrency)
        assert rule.lane == "FEED"
        assert rule.enabled is not None

    def test_acquisition_context_still_dataclass(self) -> None:
        """AcquisitionContext kept as dataclass (uses field(default=...))."""
        from dataclasses import is_dataclass

        from runtime.acquisition_strategy import AcquisitionContext

        assert is_dataclass(AcquisitionContext)

    def test_acquisition_context_field_defaults(self) -> None:
        from runtime.acquisition_strategy import AcquisitionContext

        ctx = AcquisitionContext(
            query="test",
            duration_s=300,
            aggressive_mode=False,
            uma_state="ok",
            swap_detected=False,
            hardware_critical=False,
            has_domain=True,
            has_url=False,
            has_crypto=False,
            has_long_duration=True,
            is_nonfeed_diagnostic=False,
            transport_degraded=False,
            stealth_ready=False,
            base_concurrency=2,
            is_academic=False,
        )
        assert ctx._feed_max_items == 50
        assert ctx._feed_cap_reason is None

    def test_feed_dominance_budget_still_msgspec(self) -> None:
        """FeedDominanceBudget already migrated in prior sprint."""
        from runtime.acquisition_strategy import FeedDominanceBudget

        assert issubclass(FeedDominanceBudget, msgspec.Struct)

    def test_feed_dominance_budget_methods(self) -> None:
        from runtime.acquisition_strategy import FeedDominanceBudget

        fdb = FeedDominanceBudget(
            max_feed_accepted_before_nonfeed_terminal=100,
            max_feed_per_source=50,
        )
        assert fdb.is_active()
        assert not fdb.is_sentinel()

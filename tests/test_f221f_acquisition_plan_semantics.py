# Sprint F221F: Acquisition Plan Semantics Split — Probe Tests
"""
Validates the semantic split of acquisition plan fields:

- prelude_plan: original plan dicts (backward compatible alias for `plan`)
- required_lane_plan: mandatory lanes from terminality.required_lanes
- runtime_attempted_lanes: lanes where source_family_outcomes shows attempted=True
- effective_acquisition_plan: union(required_lane_plan, runtime_attempted_lanes)
- plan_semantics: "prelude_only" | "effective_runtime"

Scope: runtime/acquisition_strategy.py build_acquisition_report() only.
No runtime behavior changes — report-only fields.
"""


from hledac.universal.runtime.acquisition_strategy import (
    ACQUISITION_REPORT_SCHEMA_VERSION,
    build_acquisition_report,
)


class TestF221F_PlanSemanticsSplit:
    """F221F: Acquisition plan semantics split probe tests."""

    def test_empty_prelude_plan_but_public_required_creates_effective_plan(self):
        """
        Acceptance fixture: plan=[] but required_lanes=["PUBLIC"].
        effective_acquisition_plan MUST include "PUBLIC".
        """
        terminality = {
            "required_lanes": ["PUBLIC"],
            "checked": ["PUBLIC"],
            "satisfied": [],
            "missing_lanes": ["PUBLIC"],
        }
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["prelude_plan"] == []
        assert report["required_lane_plan"] == ["PUBLIC"]
        assert "public" in report["runtime_attempted_lanes"]
        assert "PUBLIC" in report["effective_acquisition_plan"] or "public" in report["effective_acquisition_plan"]
        assert report["plan_semantics"] == "effective_runtime"

    def test_runtime_attempted_lanes_extracted_from_source_family_outcomes(self):
        """
        Test that runtime_attempted_lanes is correctly derived from source_family_outcomes.
        Only lanes with attempted=True are included.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
            {"family": "feed", "attempted": True, "skipped": False, "raw_count": 10, "accepted_count": 8},
            {"family": "ct", "attempted": False, "skipped": True, "raw_count": 0, "accepted_count": 0},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        attempted = report["runtime_attempted_lanes"]
        assert "public" in attempted
        assert "feed" in attempted
        assert "ct" not in attempted  # skipped, not attempted

    def test_plan_semantics_effective_runtime_when_lanes_attempted(self):
        """
        When any lane is attempted, plan_semantics must be 'effective_runtime'.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "feed", "attempted": True, "skipped": False, "raw_count": 10, "accepted_count": 8},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["plan_semantics"] == "effective_runtime"

    def test_plan_semantics_prelude_only_when_no_lanes_attempted(self):
        """
        When no lane is attempted, plan_semantics must be 'prelude_only'.
        This is the key fix: plan=[] no longer misleads when PUBLIC was required+attempted.
        """
        terminality = {"required_lanes": ["PUBLIC"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": False, "skipped": True, "raw_count": 0, "accepted_count": 0},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["plan_semantics"] == "prelude_only"
        assert "public" not in report["runtime_attempted_lanes"]

    def test_domain_prelude_plan_still_preserved(self):
        """
        When a real plan exists, prelude_plan must preserve it.
        plan_semantics should be 'effective_runtime' if any lane was attempted.
        """
        from hledac.universal.runtime.acquisition_strategy import (
            AcquisitionLane,
            AcquisitionLanePlan,
            AcquisitionStrategySnapshot,
        )

        plan_snapshot = AcquisitionStrategySnapshot(plans=[
            AcquisitionLanePlan(
                lane=AcquisitionLane.PUBLIC,
                enabled=True,
                reason="domain_query",
                max_items=20,
                timeout_s=60,
                concurrency=2,
                risk_level="LOW",
            ),
            AcquisitionLanePlan(
                lane=AcquisitionLane.FEED,
                enabled=True,
                reason="always_enabled",
                max_items=50,
                timeout_s=30,
                concurrency=3,
                risk_level="LOW",
            ),
        ])

        terminality = {"required_lanes": ["PUBLIC"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=plan_snapshot,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        # prelude_plan should contain the original plan dicts
        assert len(report["prelude_plan"]) == 2
        assert report["prelude_plan"] == report["plan"]  # backward compat alias
        assert report["plan_semantics"] == "effective_runtime"
        assert "PUBLIC" in report["required_lane_plan"] or "public" in report["required_lane_plan"]

    def test_no_runtime_behavior_changes_report_only(self):
        """
        Verify that build_acquisition_report produces the same schema_version
        and other existing fields — only ADDING new fields, not changing behavior.
        """
        report = build_acquisition_report(
            plan=None,
            terminality={"required_lanes": [], "checked": [], "satisfied": []},
            source_family_outcomes=[],
        )

        # Must have all new F221F fields
        assert "prelude_plan" in report
        assert "required_lane_plan" in report
        assert "runtime_attempted_lanes" in report
        assert "effective_acquisition_plan" in report
        assert "plan_semantics" in report

        # Must have original fields for backward compatibility
        assert report["schema_version"] == ACQUISITION_REPORT_SCHEMA_VERSION
        assert "plan" in report  # original field unchanged
        assert "terminality" in report
        assert "source_family_outcomes" in report

        # plan_semantics must be one of the two valid values
        assert report["plan_semantics"] in ("prelude_only", "effective_runtime")

    def test_effective_acquisition_plan_union(self):
        """
        effective_acquisition_plan = required_lane_plan ∪ runtime_attempted_lanes
        """
        terminality = {"required_lanes": ["CT"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        effective = report["effective_acquisition_plan"]
        # CT from required_lane_plan
        assert "CT" in effective or "ct" in effective
        # public from runtime_attempted_lanes
        assert "public" in effective
        # union size should be 2
        assert len(effective) == 2

    def test_required_lane_plan_from_terminality(self):
        """
        required_lane_plan extracts only required=True lanes from terminality.
        """
        terminality = {
            "required_lanes": ["PUBLIC", "CT"],
            "checked": ["PUBLIC", "CT", "FEED"],
            "satisfied": ["PUBLIC"],
            "missing_lanes": ["CT"],
        }

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=[],
        )

        required = report["required_lane_plan"]
        assert "PUBLIC" in required
        assert "CT" in required
        assert len(required) == 2

    def test_empty_source_family_outcomes_prelude_only(self):
        """
        When source_family_outcomes is empty/None, plan_semantics = 'prelude_only'.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=None,
        )

        assert report["plan_semantics"] == "prelude_only"
        assert report["runtime_attempted_lanes"] == []
        assert report["effective_acquisition_plan"] == []

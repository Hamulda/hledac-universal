"""Sprint F206S: Benchmark artifact hygiene — canonical truth surfaces presence.

SUCCESS CRITERIA (SEALED):
- report JSON has top-level canonical_run_summary
- report JSON has top-level runtime_truth
- report JSON has top-level timing_truth
- E2E artifact parser detects runtime_truth_present=true and timing_truth_present=true
- depleted query classification remains separate from report truth presence
- no runtime/scheduler behavior change
- tests pass
"""
import json
import pathlib
import pytest

UNIVERSAL_ROOT = pathlib.Path("hledac/universal")
REPORT_GLOB = pathlib.Path.home() / ".hledac/reports/*.json"


class TestReportTruthSurfaces:
    """Verify canonical truth surfaces are present in report artifact."""

    def test_canonical_run_summary_top_level(self, report_artifact):
        """Report must have top-level canonical_run_summary dict."""
        assert "canonical_run_summary" in report_artifact, (
            "canonical_run_summary missing from top-level — benchmark artifact hygiene gap"
        )
        assert isinstance(report_artifact["canonical_run_summary"], dict), (
            "canonical_run_summary must be a dict"
        )

    def test_runtime_truth_top_level(self, report_artifact):
        """Report must have top-level runtime_truth dict."""
        assert "runtime_truth" in report_artifact, (
            "runtime_truth missing from top-level — benchmark artifact hygiene gap"
        )
        assert isinstance(report_artifact["runtime_truth"], dict), (
            "runtime_truth must be a dict"
        )

    def test_timing_truth_top_level(self, report_artifact):
        """Report must have top-level timing_truth dict."""
        assert "timing_truth" in report_artifact, (
            "timing_truth missing from top-level — benchmark artifact hygiene gap"
        )
        assert isinstance(report_artifact["timing_truth"], dict), (
            "timing_truth must be a dict"
        )

    def test_scorecard_fields_preserved(self, report_artifact):
        """Additive write must not break existing scorecard/export fields."""
        preserved = [
            "synthesis_engine_used",
            "gnn_predicted_links",
            "top_graph_nodes",
            "phase_duration_seconds",
            "identity_candidates_found",
            "identity_findings_produced",
            "product_value_summary",
            "analyst_brief",
        ]
        for field in preserved:
            assert field in report_artifact, f"Preserved field '{field}' missing from report"

    def test_depleted_not_interpreted_as_missing_truth(self, report_artifact):
        """_signal_quality_classification='depleted' may coexist with truth surfaces present."""
        # Depletion is signal classification, not truth presence classification.
        # These are orthogonal dimensions.
        sig_class = report_artifact.get("_signal_quality_classification", "unknown")
        has_runtime = "runtime_truth" in report_artifact
        has_timing = "timing_truth" in report_artifact
        has_crs = "canonical_run_summary" in report_artifact

        # Depleted query CAN have truth surfaces present
        if sig_class == "depleted":
            assert has_runtime or has_timing or has_crs, (
                "Depleted classification does not imply missing truth surfaces — "
                "depletion is signal-level, not reporting-level"
            )

    def test_canonical_run_summary_minimal_fields(self, report_artifact):
        """canonical_run_summary must contain CHECKPOINT-0 required fields."""
        if "canonical_run_summary" not in report_artifact:
            pytest.skip("canonical_run_summary not in artifact")
        crs = report_artifact["canonical_run_summary"]
        required = ["meaningful", "primary_signal", "canonical_sprint_owner", "canonical_path_used"]
        for field in required:
            assert field in crs, f"canonical_run_summary missing required field: {field}"

    def test_runtime_truth_minimal_fields(self, report_artifact):
        """runtime_truth must contain is_meaningful and primary_signal_source."""
        if "runtime_truth" not in report_artifact:
            pytest.skip("runtime_truth not in artifact")
        rt = report_artifact["runtime_truth"]
        required = ["is_meaningful", "primary_signal_source"]
        for field in required:
            assert field in rt, f"runtime_truth missing required field: {field}"

    def test_timing_truth_minimal_fields(self, report_artifact):
        """timing_truth must contain requested_duration_s and active_runtime_occurred."""
        if "timing_truth" not in report_artifact:
            pytest.skip("timing_truth not in artifact")
        tt = report_artifact["timing_truth"]
        required = ["requested_duration_s", "active_runtime_occurred"]
        for field in required:
            assert field in tt, f"timing_truth missing required field: {field}"

    def test_json_artifact_valid(self, report_artifact):
        """Report artifact must be valid, parseable JSON."""
        assert report_artifact is not None, "Report artifact is None"
        # Verify it's already a dict (parsed)
        assert isinstance(report_artifact, dict), "Report artifact must be a dict"

    def test_additive_not_schema_breaking(self, report_artifact):
        """Adding truth surfaces must not remove or rename existing fields."""
        existing = [
            "synthesis_engine_used",
            "gnn_predicted_links",
            "top_graph_nodes",
            "phase_duration_seconds",
            "identity_candidates_found",
            "identity_findings_produced",
            "product_value_summary",
            "analyst_brief",
        ]
        for field in existing:
            assert field in report_artifact, (
                f"Additive write removed existing field '{field}' — schema-breaking change"
            )
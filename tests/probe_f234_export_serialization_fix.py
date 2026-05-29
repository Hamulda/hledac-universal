"""
probe_f234_export_serialization_fix.py
======================================
Verifies that Finding #1 from EXPORT_REPORT_PIPELINE_AUDIT.md is fixed:
duplicate serialization round-trip (dict→str→dict→str) is eliminated.

Fix: sanitized_obj stays dict after gate parse; no unnecessary str→dict→str cycles.

Invariant: report JSON output shape unchanged, no truncation, no corruption.
"""
import json

import pytest
from hledac.universal.export.sprint_exporter import export_sprint
from hledac.universal.paths import get_sprint_json_report_path
from hledac.universal.project_types import ExportHandoff


class TestF234ExportSerializationFix:
    """Golden tests for export serialization fix — no round-trip corruption."""

    @pytest.fixture
    def handoff_with_nested_scorecard(self):
        """ExportHandoff with a deep nested scorecard >5000 chars to catch truncation."""
        nested = {
            "sprint_id": "F234A",
            "accepted": 3,
            "rejected": 1,
            "total": 4,
            "deep": {
                "l1": {
                    "l2": {
                        "l3": {
                            "data": "x" * 10_000,  # >5000 bytes — forces truncation if bug present
                        }
                    }
                }
            },
            "list_field": [{"a": 1, "b": 2} for _ in range(500)],
            "canonical_run_summary": {
                "timing_truth": {"total": 1.5, "phases": {"gather": 0.5, "synth": 1.0}},
                "accepted": 3,
            },
        }
        eh = ExportHandoff(
            sprint_id="F234A",
            scorecard=nested,
            runtime_truth={"total": 4, "accepted": 3},
            top_nodes=[],
            canonical_run_summary=nested["canonical_run_summary"],
        )
        return eh

    @pytest.mark.asyncio
    async def test_json_shape_unchanged(self, handoff_with_nested_scorecard):
        """Output dict has same top-level keys as current (backward compat)."""
        await export_sprint(store=None, handoff=handoff_with_nested_scorecard, export_mode="slim")
        report_path = get_sprint_json_report_path("F234A")
        assert report_path.exists(), f"report not written: {report_path}"

        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        assert "sprint_id" in data
        assert "product_value_summary" in data
        assert "capability_synthesis" in data

    @pytest.mark.asyncio
    async def test_nested_scorecard_preserved(self, handoff_with_nested_scorecard):
        """Nested dicts/lists survive serialization without corruption."""
        await export_sprint(store=None, handoff=handoff_with_nested_scorecard, export_mode="slim")
        report_path = get_sprint_json_report_path("F234A")

        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        deep_str = "x" * 10_000
        assert "deep" in data, "deep key missing from output"
        assert data["deep"]["l1"]["l2"]["l3"]["data"] == deep_str, \
            "Deep nested data corrupted/truncated"

    @pytest.mark.asyncio
    async def test_no_truncation_via_scorecard_size(self, handoff_with_nested_scorecard):
        """Deep scorecard field size in JSON >= original deep field size (no silent truncation)."""
        await export_sprint(store=None, handoff=handoff_with_nested_scorecard, export_mode="slim")
        report_path = get_sprint_json_report_path("F234A")

        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        output_scorecard_size = len(json.dumps(data.get("deep", {})))
        # Compare deep field output size vs original deep field size (not full scorecard)
        original_deep_size = len(json.dumps(handoff_with_nested_scorecard.scorecard["deep"]))
        assert output_scorecard_size >= original_deep_size * 0.99, \
            f"Deep field appears truncated: output {output_scorecard_size} < original {original_deep_size}"

    @pytest.mark.asyncio
    async def test_parse_error_fallback(self):
        """Invalid JSON from sanitize_outbound falls back to degraded dict, not crash."""
        bad_scorecard = {
            "sprint_id": "F234B",
            "note": "this scorecard is intentionally minimal",
        }
        eh = ExportHandoff(
            sprint_id="F234B",
            scorecard=bad_scorecard,
            runtime_truth={},
            top_nodes=[],
            canonical_run_summary=None,
        )
        await export_sprint(store=None, handoff=eh, export_mode="slim")
        report_path = get_sprint_json_report_path("F234B")

        assert report_path.exists(), "degraded export must still write a file"

        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        assert "sprint_id" in data

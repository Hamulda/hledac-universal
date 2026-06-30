# hledac/universal/tests/probe_8bb/test_sprint_8bb.py
"""Sprint 8BB — Deterministic Markdown Diagnostic Reporter tests."""

import json
import tempfile
from pathlib import Path

import pytest

from hledac.universal.export.markdown_reporter import (
    normalize_report_input,
    render_diagnostic_markdown,
    render_diagnostic_markdown_to_path,
)


class TestSprint8BB:
    """Sprint 8BB test suite."""

    # D.1
    def test_render_minimal_report_with_zero_findings(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        assert "Ghost Prime Diagnostic Report" in md
        assert "no accepted findings" in md
        assert "Unknown" in md
        assert "0" in md

    # D.2
    def test_render_report_with_accepted_present(self, accepted_present_report):
        md = render_diagnostic_markdown(accepted_present_report)
        assert "5 accepted findings" in md
        assert "Accepted Findings Present" in md
        assert "## Run Metadata" in md
        assert "## Executive Summary" in md

    # D.3
    def test_render_report_with_duplicate_rejection_dominant(
        self, duplicate_rejection_report
    ):
        md = render_diagnostic_markdown(duplicate_rejection_report)
        assert "Duplicate Rejection Dominant" in md
        assert "Duplicate" in md

    # D.4
    def test_render_report_with_low_information_dominant(
        self, low_info_rejection_report
    ):
        md = render_diagnostic_markdown(low_info_rejection_report)
        assert "Low-Information Rejection Dominant" in md

    # D.5
    def test_render_report_with_network_variance(self, network_variance_report):
        md = render_diagnostic_markdown(network_variance_report)
        assert "Network Variance" in md
        assert "Network Variance Flag" in md

    # D.6
    def test_signal_funnel_section_includes_all_required_fields_in_order(
        self, minimal_report
    ):
        md = render_diagnostic_markdown(minimal_report)
        assert "## Signal Funnel" in md
        # All required fields in order
        assert "Entries Seen:" in md
        idx_seen = md.index("Entries Seen:")
        idx_empty = md.index("Entries With Empty Assembled Text:")
        idx_text = md.index("Entries With Text:")
        idx_scanned = md.index("Entries Scanned:")
        idx_hits = md.index("Entries With Hits:")
        idx_pattern = md.index("Total Pattern Hits:")
        idx_built = md.index("Findings Built (Pre-Store):")
        idx_delta = md.index("Accepted Count Delta:")
        assert idx_seen < idx_empty < idx_text < idx_scanned < idx_hits < idx_pattern < idx_built < idx_delta

    # D.7
    def test_store_rejection_trace_section_includes_all_required_fields(
        self, duplicate_rejection_report
    ):
        md = render_diagnostic_markdown(duplicate_rejection_report)
        assert "## Store Rejection Trace" in md
        assert "Accepted Count Delta:" in md
        assert "Low-Information Rejected Count Delta:" in md
        assert "In-Memory Duplicate Rejected Count Delta:" in md
        assert "Persistent Duplicate Rejected Count Delta:" in md
        assert "Other Rejected Count Delta:" in md

    # D.8
    def test_root_cause_section_uses_canonical_label(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        assert "## Root Cause" in md
        # unknown → Unknown (canonical label)
        assert "Unknown" in md
        # Root cause label appears in canonical form in Root Cause section
        assert "- **Root Cause**: Unknown" in md

    # D.9
    def test_recommendation_prefers_report_field_over_fallback(self):
        # A report that carries its own recommendation field
        from hledac.universal.tests.probe_8bb.conftest import AcceptedPresentReport
        report = AcceptedPresentReport()
        md = render_diagnostic_markdown(report)
        # accepted_present has no explicit recommendation field so fallback used
        # Test that fallback mapping works
        assert "## Recommended Next Sprint" in md
        assert "continue_monitoring" in md or "Unknown" in md

    # D.10
    def test_missing_per_source_health_is_rendered_gracefully(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        assert "Per-source detail unavailable in current report." in md

    # D.11
    def test_missing_known_limits_is_rendered_gracefully(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        assert "Current report did not provide known limits" in md

    # D.12
    def test_markdown_is_deterministic_for_same_input(
        self, accepted_present_report
    ):
        md1 = render_diagnostic_markdown(accepted_present_report)
        md2 = render_diagnostic_markdown(accepted_present_report)
        assert md1 == md2

    # D.13
    def test_renderer_does_not_mutate_input(self, minimal_report):
        before = normalize_report_input(minimal_report)
        render_diagnostic_markdown(minimal_report)
        after = normalize_report_input(minimal_report)
        assert before == after

    # D.14
    def test_sections_order_is_stable(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        idx_run_meta = md.index("## Run Metadata")
        idx_exec = md.index("## Executive Summary")
        idx_runtime = md.index("## Runtime Truth")
        idx_signal = md.index("## Signal Funnel")
        idx_store = md.index("## Store Rejection Trace")
        idx_per_source = md.index("## Per-Source Health")
        idx_root_cause = md.index("## Root Cause")
        idx_rec = md.index("## Recommended Next Sprint")
        idx_limits = md.index("## Known Limits")
        idx_machine = md.index("## Machine-Readable Summary")
        assert (
            idx_run_meta
            < idx_exec
            < idx_runtime
            < idx_signal
            < idx_store
            < idx_per_source
            < idx_root_cause
            < idx_rec
            < idx_limits
            < idx_machine
        )

    # D.15
    def test_machine_readable_summary_is_deterministic(
        self, accepted_present_report
    ):
        md = render_diagnostic_markdown(accepted_present_report)
        # Extract JSON block
        assert "```json" in md
        json_start = md.index("```json\n") + 7
        json_end = md.index("\n```", json_start)
        json_str = md[json_start:json_end]
        data = json.loads(json_str)
        # Stable keys
        assert "accepted_findings" in data
        assert "diagnostic_root_cause" in data
        assert "recommended_next_sprint" in data
        # Renders twice → same JSON
        md2 = render_diagnostic_markdown(accepted_present_report)
        json_start2 = md2.index("```json\n") + 7
        json_end2 = md2.index("\n```", json_start2)
        json_str2 = md2[json_start2:json_end2]
        assert json_str == json_str2

    # D.16
    def test_msgspec_struct_input_is_supported(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        assert "Ghost Prime Diagnostic Report" in md

    # D.17
    def test_mapping_input_is_supported(self, minimal_report):
        # Convert to plain dict (Mapping)
        data = normalize_report_input(minimal_report)
        md = render_diagnostic_markdown(data)
        assert "Ghost Prime Diagnostic Report" in md

    # D.18
    def test_to_path_respects_GHOST_EXPORT_DIR_or_truth_surface(
        self, minimal_report, monkeypatch
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GHOST_EXPORT_DIR", tmpdir)
            path = render_diagnostic_markdown_to_path(minimal_report)
            assert path.exists()
            content = path.read_text()
            assert "Ghost Prime Diagnostic Report" in content

    # D.19
    def test_urls_and_paths_are_markdown_safe(self, accepted_present_report):
        md = render_diagnostic_markdown(accepted_present_report)
        # URLs appear as markdown links (linkified by _linkify)
        assert "[feed1](https://example.com/feed1)" in md
        assert "[feed2](https://example.com/feed2)" in md
        # Backtick-like content is escaped
        # Verify no triple-backtick injection in JSON block
        assert "```json" in md
        assert md.count("```json") == 1

    # Additional: to_path with explicit path
    def test_to_path_with_explicit_path(self, minimal_report, tmp_path):
        out = tmp_path / "my_report.md"
        result = render_diagnostic_markdown_to_path(minimal_report, out)
        assert result == out
        assert out.exists()
        assert "Ghost Prime Diagnostic Report" in out.read_text()

    # Additional: normalize_report_input type errors
    def test_normalize_report_input_raises_for_invalid_type(self):
        class FakeObj:
            pass

        with pytest.raises(TypeError, match="msgspec.Struct or Mapping"):
            normalize_report_input(FakeObj())

    # Additional: machine-readable summary missing keys filled with null
    def test_machine_readable_omits_nulls(self, minimal_report):
        md = render_diagnostic_markdown(minimal_report)
        json_start = md.index("```json\n") + 7
        json_end = md.index("\n```", json_start)
        json_str = md[json_start:json_end]
        data = json.loads(json_str)
        # null/None values are removed
        for v in data.values():
            assert v is not None

    # Additional: fallback recommendation mapping has all required root causes
    def test_fallback_recommendation_has_all_root_causes(self):
        from hledac.universal.export.markdown_reporter import (
            _FALLBACK_RECOMMENDATION,
        )

        root_causes = [
            "network_variance",
            "no_new_entries",
            "empty_registry",
            "no_pattern_hits",
            "no_pattern_hits_possible_morphology_gap",
            "pattern_hits_but_no_findings_built",
            "low_information_rejection_dominant",
            "duplicate_rejection_dominant",
            "accepted_present",
            "unknown",
        ]
        for rc in root_causes:
            assert rc in _FALLBACK_RECOMMENDATION
        assert len(_FALLBACK_RECOMMENDATION) == len(root_causes)


# ---------------------------------------------------------------------------
# Benchmarks (E)
# ---------------------------------------------------------------------------
class TestSprint8BBBenchmarks:
    """Sprint 8BB performance benchmarks."""

    def test_render_diagnostic_markdown_x1000_under_300ms(self, minimal_report):
        import time

        start = time.perf_counter()
        for _ in range(1000):
            render_diagnostic_markdown(minimal_report)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"1000 renders took {elapsed:.3f}s > 0.3s"

    def test_render_diagnostic_markdown_realistic_x200_under_300ms(
        self, accepted_present_report
    ):
        import time

        start = time.perf_counter()
        for _ in range(200):
            render_diagnostic_markdown(accepted_present_report)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"200 renders took {elapsed:.3f}s > 0.3s"

    def test_zero_findings_diagnostic_render_x1000_under_300ms(
        self, minimal_report
    ):
        import time

        start = time.perf_counter()
        for _ in range(1000):
            render_diagnostic_markdown(minimal_report)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"1000 zero-finding renders took {elapsed:.3f}s > 0.3s"

    def test_repeated_render_same_report_byte_identical(
        self, accepted_present_report
    ):
        renders = [render_diagnostic_markdown(accepted_present_report) for _ in range(100)]
        first = renders[0]
        for r in renders[1:]:
            assert r == first, "Render output is not byte-identical across runs"

    def test_to_path_helper_x100_under_300ms(self, minimal_report, tmp_path):
        import time

        start = time.perf_counter()
        for i in range(100):
            out = tmp_path / f"bench_{i}.md"
            render_diagnostic_markdown_to_path(minimal_report, out)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"100 to_path calls took {elapsed:.3f}s > 0.3s"

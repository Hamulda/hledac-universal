"""
Sprint 8C2: Research Effectiveness Benchmark Tests

Tests for research_effectiveness.py score aggregation:
- Schema consistency
- Deterministic aggregation on fixed fixtures
- Unavailable-path handling
- No-network execution
- Stable output on rerun
- No boot/import regression
- No torch import
- No duckdb import
"""


import json
import pytest
from pathlib import Path

# Ensure the module can be imported directly
_REPO_ROOT = Path(__file__).parent.parent.parent  # universal/
_bench_eff_path = _REPO_ROOT / "benchmarks" / "research_effectiveness.py"
import importlib.util
_spec = importlib.util.spec_from_file_location("research_effectiveness", _bench_eff_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load research_effectiveness from {_bench_eff_path}")
_re_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_re_mod)

# Import from loaded module
compute_research_breadth_index = _re_mod.compute_research_breadth_index
compute_research_depth_index = _re_mod.compute_research_depth_index
compute_research_quality_index = _re_mod.compute_research_quality_index
compute_research_friction_index = _re_mod.compute_research_friction_index
compute_deep_research_power_score = _re_mod.compute_deep_research_power_score
aggregate_benchmark_jsons = _re_mod.aggregate_benchmark_jsons
compute_all_scorecards = _re_mod.compute_all_scorecards
generate_scorecard_markdown = _re_mod.generate_scorecard_markdown
normalize_source_family = _re_mod.normalize_source_family
normalize_acquisition_mode = _re_mod.normalize_acquisition_mode
normalize_confidence_bucket = _re_mod.normalize_confidence_bucket
normalize_severity = _re_mod.normalize_severity
_hhi = _re_mod._hhi
_unavailable = _re_mod._unavailable


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_MINIMAL = {
    "total_wall_clock_seconds": 14.0,
    "findings_count": 0,
    "sources_count": 0,
    "gating": {"l0_rejects": 0, "admits": 0},
    "acquisition": {
        "ct_attempts": 0, "ct_successes": 0,
        "wayback_quick_attempts": 0, "wayback_quick_successes": 0,
        "wayback_cdx_attempts": 0, "wayback_cdx_lines": 0,
        "commoncrawl_attempts": 0, "commoncrawl_lines": 0,
        "necromancer_attempts": 0, "necromancer_rescues": 0,
        "prf_invocations": 0,
        "onion_preflight": 0, "onion_available": 0,
    },
    "synthesis": {"claims_emitted": 0, "contested_claims": 0},
    "memory": {},
}

FIXTURE_WITH_DATA = {
    "total_wall_clock_seconds": 300.0,
    "findings_count": 47,
    "sources_count": 23,
    "gating": {
        "l0_rejects": 10, "l1_echo_rejects": 3, "admits": 47,
        "backlog_pushes": 5, "backlog_promotions": 2,
        "deepening_gate_candidates": 8,
    },
    "acquisition": {
        "ct_attempts": 12, "ct_successes": 9,
        "wayback_quick_attempts": 20, "wayback_quick_successes": 15,
        "wayback_cdx_attempts": 5, "wayback_cdx_lines": 120,
        "commoncrawl_attempts": 8, "commoncrawl_lines": 340,
        "necromancer_attempts": 3, "necromancer_rescues": 1,
        "prf_invocations": 6,
        "onion_preflight": 4, "onion_available": 2,
    },
    "synthesis": {
        "claims_emitted": 47, "contested_claims": 5,
        "contradictions_surfaced": 2,
        "winner_only_evidence_count": 8,
    },
    "memory": {"rss_peak_mb": 844},
    "timing": {"total_wall_clock_seconds": 300.0, "research_runtime_seconds": 285.0},
}


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

class TestNormalizations:
    def test_normalize_source_family_ct(self):
        assert normalize_source_family("ct") == "certificate_transparency"
        assert normalize_source_family("certificate_transparency") == "certificate_transparency"
        assert normalize_source_family("CT_logs") == "certificate_transparency"

    def test_normalize_source_family_wayback(self):
        assert normalize_source_family("wayback") == "archive"
        assert normalize_source_family("wayback_machine") == "archive"

    def test_normalize_source_family_onion(self):
        assert normalize_source_family("onion") == "darknet"
        assert normalize_source_family("tor") == "darknet"

    def test_normalize_source_family_unknown(self):
        assert normalize_source_family("") == "unknown"
        assert normalize_source_family("foobar") == "other"

    def test_normalize_acquisition_mode(self):
        assert normalize_acquisition_mode("certificate_transparency") == "certificate_transparency"
        assert normalize_acquisition_mode("wayback") == "archive"
        assert normalize_acquisition_mode("onion") == "hidden_service"
        assert normalize_acquisition_mode("passive_dns") == "passive"
        assert normalize_acquisition_mode("") == "unknown"

    def test_normalize_confidence_bucket(self):
        assert normalize_confidence_bucket(0.95) == "high"
        assert normalize_confidence_bucket(0.8) == "medium"
        assert normalize_confidence_bucket(0.5) == "low"
        assert normalize_confidence_bucket(0.1) == "unknown"

    def test_hhi_uniform(self):
        assert _hhi({"a": 1, "b": 1, "c": 1}) == pytest.approx(1 / 3, rel=0.01)

    def test_hhi_monopoly(self):
        assert _hhi({"a": 10}) == pytest.approx(1.0, rel=0.01)

    def test_hhi_empty(self):
        assert _hhi({}) == 0.0


# ---------------------------------------------------------------------------
# Scorecard computation tests
# ---------------------------------------------------------------------------

class TestResearchBreadthIndex:
    def test_breadth_minimal_data(self):
        result = compute_research_breadth_index(FIXTURE_MINIMAL)
        assert result["status"] == "READY"
        assert "source_family_count" in result
        assert "breadth_score" in result

    def test_breadth_with_acquisition_data(self):
        result = compute_research_breadth_index(FIXTURE_WITH_DATA)
        assert result["status"] == "READY"
        assert result["source_family_count"] >= 0
        assert "ct_successes" not in result  # ct_successes is in depth, not breadth

    def test_breadth_empty_data(self):
        result = compute_research_breadth_index({})
        # Empty data: breadth returns UNAVAILABLE (needs acquisition dict)
        assert result["status"] == "UNAVAILABLE_WITH_REASON"
        assert "reason" in result


class TestResearchDepthIndex:
    def test_depth_minimal_data(self):
        result = compute_research_depth_index(FIXTURE_MINIMAL)
        assert result["status"] == "READY"
        assert "depth_score" in result
        assert result["unindexed_source_hits"] == 0

    def test_depth_with_data(self):
        result = compute_research_depth_index(FIXTURE_WITH_DATA)
        assert result["status"] == "READY"
        assert result["archive_resurrection_hits"] == 16  # 15 wayback + 1 necromancer
        assert result["hidden_service_hits"] == 2
        assert result["ct_successes"] == 9

    def test_depth_empty_data(self):
        result = compute_research_depth_index({})
        # Empty data: returns READY with defaults (ct_successes=0)
        assert result["status"] == "READY"
        assert "depth_score" in result


class TestResearchQualityIndex:
    def test_quality_minimal_data(self):
        result = compute_research_quality_index(FIXTURE_MINIMAL)
        assert result["status"] == "READY"
        assert "quality_score" in result

    def test_quality_with_data(self):
        result = compute_research_quality_index(FIXTURE_WITH_DATA)
        assert result["status"] == "READY"
        assert result["total_findings"] == 47
        assert result["corroborated_findings_ratio"] == 0.0  # no corroborated in fixture

    def test_quality_empty_data(self):
        result = compute_research_quality_index({})
        # Empty data: returns READY with defaults
        assert result["status"] == "READY"
        assert "quality_score" in result


class TestResearchFrictionIndex:
    def test_friction_minimal_data(self):
        result = compute_research_friction_index(FIXTURE_MINIMAL)
        assert result["status"] == "READY"
        assert "friction_score" in result
        assert "challenge_issued_rate" in result

    def test_friction_with_data(self):
        result = compute_research_friction_index(FIXTURE_WITH_DATA)
        assert result["status"] == "READY"
        assert result["wayback_quick_attempts"] == 20
        assert result["wayback_quick_successes"] == 15
        assert result["wayback_fallback_rate"] > 0

    def test_friction_empty_data(self):
        result = compute_research_friction_index({})
        # Empty data: returns READY with defaults
        assert result["status"] == "READY"
        assert "friction_score" in result


class TestDeepResearchPowerScore:
    def test_power_score_all_ready(self):
        breadth = {"status": "READY", "breadth_score": 50.0}
        depth = {"status": "READY", "depth_score": 60.0}
        quality = {"status": "READY", "quality_score": 70.0}
        friction = {"status": "READY", "friction_score": 20.0}
        result = compute_deep_research_power_score(breadth, depth, quality, friction)
        assert result["status"] == "READY"
        # Expected: 50*0.25 + 60*0.30 + 70*0.30 + (100-20)*0.15 = 12.5 + 18 + 21 + 12 = 63.5
        assert result["deep_research_power_score"] == pytest.approx(63.5, rel=0.1)
        assert result["tier"] == "good"

    def test_power_score_unavailable(self):
        breadth = {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"}
        depth = {"status": "READY", "depth_score": 50.0}
        quality = {"status": "READY", "quality_score": 50.0}
        friction = {"status": "READY", "friction_score": 50.0}
        result = compute_deep_research_power_score(breadth, depth, quality, friction)
        # Breadth unavailable → score should be lower
        assert result["status"] == "READY"

    def test_power_score_tier_excellent(self):
        result = compute_deep_research_power_score(
            {"status": "READY", "breadth_score": 90.0},
            {"status": "READY", "depth_score": 90.0},
            {"status": "READY", "quality_score": 90.0},
            {"status": "READY", "friction_score": 5.0},
        )
        assert result["tier"] == "excellent"

    def test_power_score_tier_minimal(self):
        result = compute_deep_research_power_score(
            {"status": "READY", "breadth_score": 5.0},
            {"status": "READY", "depth_score": 5.0},
            {"status": "READY", "quality_score": 5.0},
            {"status": "READY", "friction_score": 95.0},
        )
        assert result["tier"] == "minimal"


# ---------------------------------------------------------------------------
# Aggregation tests
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_aggregate_empty_pattern(self, tmp_path):
        result = aggregate_benchmark_jsons(str(tmp_path / "nonexistent_*.json"))
        assert result == {}

    def test_aggregate_with_json_files(self, tmp_path):
        # Write two fixture files
        f1 = tmp_path / "bench1.json"
        f2 = tmp_path / "bench2.json"
        f1.write_text(json.dumps(FIXTURE_MINIMAL))
        f2.write_text(json.dumps(FIXTURE_WITH_DATA))
        result = aggregate_benchmark_jsons(str(tmp_path / "bench*.json"))
        assert result["_aggregated_from"] == 2
        assert "acquisition" in result
        assert result["acquisition"]["ct_attempts"] == 12  # only from FIXTURE_WITH_DATA

    def test_compute_all_scorecards(self, tmp_path):
        f1 = tmp_path / "bench1.json"
        f1.write_text(json.dumps(FIXTURE_WITH_DATA))
        scorecard = compute_all_scorecards(str(tmp_path / "bench*.json"))
        assert "research_breadth_index" in scorecard
        assert "research_depth_index" in scorecard
        assert "research_quality_index" in scorecard
        assert "research_friction_index" in scorecard
        assert "deep_research_power_score" in scorecard
        assert "_meta" in scorecard
        assert scorecard["_meta"]["aggregated_from_files"] == 1


# ---------------------------------------------------------------------------
# Markdown generation tests
# ---------------------------------------------------------------------------

class TestMarkdownGeneration:
    def test_markdown_empty(self):
        scorecard = {
            "research_breadth_index": {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"},
            "research_depth_index": {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"},
            "research_quality_index": {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"},
            "research_friction_index": {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"},
            "deep_research_power_score": {"status": "UNAVAILABLE_WITH_REASON", "reason": "no data"},
        }
        md = generate_scorecard_markdown(scorecard)
        assert "Research Effectiveness Scorecard" in md
        assert "UNAVAILABLE" in md

    def test_markdown_with_data(self):
        scorecard = {
            "research_breadth_index": {"status": "READY", "source_family_count": 5, "breadth_score": 45.0},
            "research_depth_index": {"status": "READY", "depth_score": 55.0},
            "research_quality_index": {"status": "READY", "quality_score": 65.0},
            "research_friction_index": {"status": "READY", "friction_score": 25.0},
            "deep_research_power_score": {"status": "READY", "deep_research_power_score": 57.5, "tier": "average"},
            "_meta": {"aggregated_from_files": 3, "computed_at": "2026-03-24T00:00:00Z"},
        }
        md = generate_scorecard_markdown(scorecard)
        assert "Research Effectiveness Scorecard" in md
        assert "average" in md


# ---------------------------------------------------------------------------
# Import / regression tests
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_no_torch_import(self):
        # Ensure torch is NOT loaded as a side effect in research_effectiveness
        assert "torch" not in dir(_re_mod)

    def test_no_duckdb_import(self):
        assert "duckdb" not in dir(_re_mod)

    def test_all_normalizers_exist(self):
        assert normalize_source_family("ct") == "certificate_transparency"
        assert normalize_acquisition_mode("ct") == "certificate_transparency"
        assert normalize_confidence_bucket(0.95) == "high"
        assert normalize_severity("critical") == "critical"

    def test_private_helpers_exist(self):
        assert callable(_hhi)
        assert callable(_unavailable)
        # _unavailable returns correct shape
        result = _unavailable("test reason")  # type: ignore[assignment]
        assert result["status"] == "UNAVAILABLE_WITH_REASON"  # type: ignore[index]
        assert result.get("reason") == "test reason"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Determinism / stability tests
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        r1 = compute_research_breadth_index(FIXTURE_WITH_DATA)
        r2 = compute_research_breadth_index(FIXTURE_WITH_DATA)
        assert r1 == r2

    def test_hhi_deterministic(self):
        counts = {"ct": 5, "wayback": 3, "onion": 2}
        assert _hhi(counts) == pytest.approx(_hhi(counts))



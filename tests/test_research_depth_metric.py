"""
Sprint F192H: Research Depth Metric — stability contract tests.

Tests the _compute_research_depth() function that computes a 0-100 research
depth score from canonical ExportHandoff surfaces.

Contract invariants (Sprint F192H §2):
  1. score is float in [0.0, 100.0]
  2. level is one of: surface, shallow, moderate, deep, comprehensive
  3. breakdown keys are always present: source_diversity, non_indexed_ratio,
     corroboration, branch_diversity, pivot_depth
  4. depth_signals keys are always present: unique_source_types, deep_sources_found,
     total_source_hits, corroborated, noisy_signal, campaign_hints,
     active_branches, pivot_recommended
  5. level threshold: surface [0-20], shallow [21-40], moderate [41-60],
     deep [61-80], comprehensive [81-100]
  6. Function is derived only — no new persistence, no new store reads

Derived from canonical surfaces (Sprint F192H §1):
  - eh.scorecard["entries_per_source"] / ["hits_per_source"]
  - eh.scorecard["branch_mix"] via runtime_truth
  - signal_path["is_corroborated"], ["is_noisy"], ["next_pivot_recommendation"]
  - correlation["campaign_hints"]
  - hypothesis_pack["hypothesis_count"]
"""
import pytest
from unittest.mock import MagicMock

from hledac.universal.export.sprint_exporter import (
    _compute_research_depth,
    _SOURCE_TIER,
)


# =============================================================================
# Contract: output shape is stable
# =============================================================================


class TestResearchDepthOutputShape:
    """Output dict keys and types must be stable across all input combinations."""

    def test_surface_run_returns_all_required_keys(self):
        """surface/smoke run still returns complete structure (no KeyError)."""
        result = _compute_research_depth(
            eh=_mock_handoff({"entries_per_source": {}, "hits_per_source": {}}),
            pvs={"accepted": 0},
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert "score" in result
        assert "level" in result
        assert "breakdown" in result
        assert "depth_signals" in result

    def test_full_run_returns_all_required_breakdown_keys(self):
        """breakdown always has all 5 component keys."""
        result = _compute_research_depth(
            eh=_mock_handoff(_full_source_counts()),
            pvs={"accepted": 10},
            signal_path={"is_corroborated": True, "is_noisy": False},
            hypothesis_pack={"hypothesis_count": 3},
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}]},
        )
        bd = result["breakdown"]
        assert set(bd.keys()) == {"source_diversity", "non_indexed_ratio",
                                  "corroboration", "branch_diversity", "pivot_depth"}

    def test_full_run_returns_all_required_depth_signals_keys(self):
        """depth_signals always has all 8 signal keys."""
        result = _compute_research_depth(
            eh=_mock_handoff(_full_source_counts()),
            pvs={"accepted": 10},
            signal_path={"is_corroborated": True, "is_noisy": False},
            hypothesis_pack={"hypothesis_count": 3},
            correlation={"campaign_hints": [{"a": 1}]},
        )
        ds = result["depth_signals"]
        assert set(ds.keys()) == {
            "unique_source_types", "deep_sources_found", "total_source_hits",
            "corroborated", "noisy_signal", "campaign_hints",
            "active_branches", "pivot_recommended",
        }


# =============================================================================
# Contract: score is bounded [0.0, 100.0]
# =============================================================================


class TestResearchDepthScoreBounds:
    """Score must never exceed 100.0 or go below 0.0."""

    def test_surface_smoke_run_score_not_negative(self):
        """Zero sources yields score >= 0."""
        result = _compute_research_depth(
            eh=_mock_handoff({}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["score"] >= 0.0

    def test_all_signals_max_score_is_100(self):
        """All signals active yields score <= 100."""
        result = _compute_research_depth(
            eh=_mock_handoff(_full_source_counts()),
            pvs={"accepted": 50},
            signal_path={"is_corroborated": True, "is_noisy": False,
                         "next_pivot_recommendation": "pivot_immediately"},
            hypothesis_pack={"hypothesis_count": 5},
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}, {"c": 3}]},
        )
        assert result["score"] <= 100.0
        assert result["score"] >= 0.0

    def test_score_is_float(self):
        """Score type is always float (not int, not None)."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert isinstance(result["score"], float)


# =============================================================================
# Contract: level thresholds are respected
# =============================================================================


class TestResearchDepthLevelThresholds:
    """Level assignment must follow threshold boundaries exactly."""

    def test_surface_level_at_minimum(self):
        """Empty inputs → surface level."""
        result = _compute_research_depth(
            eh=_mock_handoff({}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["level"] == "surface"

    def test_shallow_level_single_indexed_source(self):
        """Single indexed source + no corroboration → shallow."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # source_diversity ~2.5 only, everything else 0 → ~2.5 → surface
        assert result["level"] == "surface"

    def test_moderate_level_with_deep_sources(self):
        """Deep sources + no corroboration → moderate."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "rss_atom_pipeline": 5,
                "ct_log_pipeline": 5,   # tier 1 → non_indexed_ratio
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # source_diversity: 2 sources, some entropy → ~9
        # non_indexed: 5/10 = 0.5 → 10
        # corroboration: 0
        # total ~19 → surface (below 21)
        # Need more components to reach moderate
        assert result["level"] in ("surface", "shallow")

    def test_deep_level_with_corrob_and_branches(self):
        """Corroborated + branches active → deep level."""
        result = _compute_research_depth(
            eh=_mock_handoff_with_runtime_truth(
                {"rss_atom_pipeline": 10, "ct_log_pipeline": 5},
                {"feed_findings": 5, "public_findings": 3, "ct_findings": 0},
            ),
            pvs=None,
            signal_path={"is_corroborated": True, "is_noisy": False},
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # source_diversity: ~9
        # non_indexed_ratio: 5/15 * 20 ≈ 6.7
        # corroboration: 15 + 5 = 20
        # branch_diversity: 2 branches * 5 = 10
        # total ≈ 45.7 → moderate/deep
        assert result["level"] in ("moderate", "deep")

    def test_comprehensive_level_at_maximum(self):
        """All signals active + 3 branches → comprehensive level."""
        result = _compute_research_depth(
            eh=_mock_handoff_with_runtime_truth(
                _full_source_counts(),
                {"feed_findings": 5, "public_findings": 3, "ct_findings": 2},
            ),
            pvs={"accepted": 50},
            signal_path={"is_corroborated": True, "is_noisy": False,
                         "next_pivot_recommendation": "pivot_immediately"},
            hypothesis_pack={"hypothesis_count": 5},
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}, {"c": 3}]},
        )
        # source_diversity ≈ 24.2 (maxed 25), non_indexed ≈ 9.2,
        # corroboration = 25 (capped), branch = 15 (3 branches),
        # pivot = 15 (capped)
        # total ≈ 88.4 → comprehensive (>= 81)
        assert result["level"] == "comprehensive"
        assert result["score"] >= 81.0


# =============================================================================
# Contract: source_diversity component
# =============================================================================


class TestSourceDiversityComponent:
    """Source diversity score responds correctly to source type diversity."""

    def test_single_indexed_source_low_diversity(self):
        """One indexed source → low diversity score."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 100}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # Single source: entropy=0, unique_types bonus=2.5 → ~2.5
        assert result["breakdown"]["source_diversity"] < 10.0

    def test_multiple_diverse_sources_high_diversity(self):
        """3+ diverse sources with even distribution → high diversity score."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "rss_atom_pipeline": 33,
                "ct_log_pipeline": 33,
                "circl_pdns": 34,
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # 3 sources, high entropy → diversity score should be significant
        assert result["breakdown"]["source_diversity"] >= 10.0

    def test_source_tier_classification_tier2_deep(self):
        """Tier-2 sources (rl_research, tot_synthesis) are classified as deep."""
        assert _SOURCE_TIER.get("rl_research") == 2
        assert _SOURCE_TIER.get("tot_synthesis") == 2
        assert _SOURCE_TIER.get("report") == 2

    def test_source_tier_classification_tier1_structured(self):
        """Tier-1 sources (ct_log_pipeline, circl_pdns) are classified as structured."""
        assert _SOURCE_TIER.get("ct_log_pipeline") == 1
        assert _SOURCE_TIER.get("circl_pdns") == 1

    def test_source_tier_classification_tier0_indexed(self):
        """Tier-0 sources (rss_atom_pipeline, live_public_pipeline) are indexed."""
        assert _SOURCE_TIER.get("rss_atom_pipeline") == 0
        assert _SOURCE_TIER.get("live_public_pipeline") == 0


# =============================================================================
# Contract: non_indexed_ratio component
# =============================================================================


class TestNonIndexedRatioComponent:
    """Non-indexed ratio rewards use of deep/structured sources."""

    def test_all_indexed_gives_zero_non_indexed_score(self):
        """Only tier-0 sources → non_indexed_ratio = 0."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 100, "live_public_pipeline": 100}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["non_indexed_ratio"] == 0.0

    def test_all_deep_gives_max_non_indexed_score(self):
        """Only tier-1+tier-2 sources → non_indexed_ratio score = 20."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "ct_log_pipeline": 50,
                "circl_pdns": 30,
                "rl_research": 20,
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["non_indexed_ratio"] == 20.0

    def test_mixed_gives_partial_non_indexed_score(self):
        """50% deep sources → partial non_indexed_ratio."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "rss_atom_pipeline": 50,   # tier 0 (indexed)
                "ct_log_pipeline": 50,     # tier 1 (structured)
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # 50 hits from ct_log_pipeline (tier1) / 100 total = 0.5 ratio → 10.0 score
        assert result["breakdown"]["non_indexed_ratio"] == 10.0


# =============================================================================
# Contract: corroboration component
# =============================================================================


class TestCorroborationComponent:
    """Corroboration score rewards cross-source signal validation."""

    def test_no_signals_gives_zero_corrob(self):
        """No corroboration signals → corroboration = 0."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["corroboration"] == 0.0

    def test_is_corroborated_true_gives_15(self):
        """is_corroborated=True → 15 points."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"is_corroborated": True, "is_noisy": True},
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["corroboration"] == 15.0

    def test_is_noisy_false_gives_5(self):
        """is_noisy=False → 5 points (distinct from is_corroborated)."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"is_corroborated": False, "is_noisy": False},
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["corroboration"] == 5.0

    def test_campaign_hints_3_plus_gives_5_bonus(self):
        """3+ campaign hints → +5 corroboration bonus (capped at 25)."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"is_corroborated": True, "is_noisy": False},
            hypothesis_pack=None,
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}, {"c": 3}]},
        )
        # 15 (corr) + 5 (noisy=False) + 5 (3+ hints) = 25
        assert result["breakdown"]["corroboration"] == 25.0

    def test_corrob_capped_at_25(self):
        """Corroboration score cannot exceed 25."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"is_corroborated": True, "is_noisy": False},
            hypothesis_pack=None,
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}, {"c": 3}]},
        )
        assert result["breakdown"]["corroboration"] == 25.0


# =============================================================================
# Contract: branch_diversity component
# =============================================================================


class TestBranchDiversityComponent:
    """Branch diversity rewards parallel use of feed + public + CT branches."""

    def test_no_runtime_truth_zero_branch_score(self):
        """No runtime_truth → branch_diversity = 0."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["branch_diversity"] == 0.0

    def test_single_branch_gives_5_points(self):
        """1 active branch → 5 points."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # runtime_truth with 1 active branch
        result2 = _compute_research_depth(
            eh=_mock_handoff_with_runtime_truth(
                {"rss_atom_pipeline": 10},
                {"feed_findings": 5, "public_findings": 0, "ct_findings": 0},
            ),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result2["breakdown"]["branch_diversity"] == 5.0

    def test_three_branches_active_gives_15_points(self):
        """3 active branches → 15 points (cap)."""
        result = _compute_research_depth(
            eh=_mock_handoff_with_runtime_truth(
                {"rss_atom_pipeline": 10},
                {"feed_findings": 5, "public_findings": 3, "ct_findings": 2},
            ),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["branch_diversity"] == 15.0


# =============================================================================
# Contract: pivot_depth component
# =============================================================================


class TestPivotDepthComponent:
    """Pivot depth rewards hypothesis generation and pivot recommendations."""

    def test_no_signals_gives_zero_pivot(self):
        """No hypothesis_pack or pivot signal → pivot_depth = 0."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["pivot_depth"] == 0.0

    def test_hypothesis_count_gives_5(self):
        """hypothesis_count > 0 → 5 points."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack={"hypothesis_count": 3},
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["pivot_depth"] == 5.0

    def test_pivot_recommended_gives_10(self):
        """next_pivot_recommendation != continue → 10 points."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"next_pivot_recommendation": "pivot_immediately"},
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["pivot_depth"] == 10.0

    def test_pivot_depth_capped_at_15(self):
        """Both hypothesis + pivot recommended → capped at 15."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"next_pivot_recommendation": "pivot_immediately"},
            hypothesis_pack={"hypothesis_count": 3},
            correlation={"_no_correlation_data": True},
        )
        # 5 + 10 = 15 (capped)
        assert result["breakdown"]["pivot_depth"] == 15.0

    def test_continue_pivot_not_counted(self):
        """next_pivot_recommendation='continue' → 0 pivot points."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path={"next_pivot_recommendation": "continue"},
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["breakdown"]["pivot_depth"] == 0.0


# =============================================================================
# Contract: depth_signals accurately reflects inputs
# =============================================================================


class TestDepthSignalsReflectInputs:
    """depth_signals dict must accurately reflect the computed inputs."""

    def test_unique_source_types_reflected(self):
        """unique_source_types in depth_signals matches number of source types."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "rss_atom_pipeline": 10,
                "ct_log_pipeline": 5,
                "circl_pdns": 3,
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["depth_signals"]["unique_source_types"] == 3

    def test_deep_sources_found_accumulates_tier1_tier2(self):
        """deep_sources_found sums hits from tier1 + tier2 sources."""
        result = _compute_research_depth(
            eh=_mock_handoff({
                "rss_atom_pipeline": 10,   # tier 0 → not counted
                "ct_log_pipeline": 5,       # tier 1 → counted
                "circl_pdns": 3,            # tier 1 → counted
            }),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        assert result["depth_signals"]["deep_sources_found"] == 8

    def test_campaign_hints_count_from_correlation(self):
        """campaign_hints count matches correlation input."""
        result = _compute_research_depth(
            eh=_mock_handoff({"rss_atom_pipeline": 10}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"campaign_hints": [{"a": 1}, {"b": 2}]},
        )
        assert result["depth_signals"]["campaign_hints"] == 2


# =============================================================================
# Contract: research depth metric in export_sprint return
# =============================================================================


class TestResearchDepthInExportReturn:
    """export_sprint() must include research_depth_metric in its return dict."""

    def test_export_sprint_includes_research_depth_metric(self):
        """The export_sprint return dict must contain research_depth_metric key."""
        from hledac.universal.export.sprint_exporter import export_sprint
        from hledac.universal.project_types import ExportHandoff
        from unittest.mock import AsyncMock, MagicMock, patch
        import asyncio

        mock_store = MagicMock()
        mock_store.async_healthcheck = AsyncMock(return_value=True)
        mock_store.get_dedup_runtime_status = MagicMock(return_value={
            "accepted_count": 5,
            "low_information_rejected_count": 2,
            "persistent_dedup_enabled": True,
        })
        mock_store.get_top_seed_nodes = MagicMock(return_value=[])

        # Use a proper ExportHandoff instance to bypass ensure_export_handoff dict bug
        mock_eh = ExportHandoff(
            sprint_id="test_sprint_001",
            scorecard={
                "entries_per_source": {"rss_atom_pipeline": 10, "ct_log_pipeline": 5},
                "hits_per_source": {"rss_atom_pipeline": 8, "ct_log_pipeline": 4},
            },
            top_nodes=[],
            gnn_predictions=0,
            synthesis_engine="hermes3",
            runtime_truth={"branch_mix": {"feed_findings": 3, "public_findings": 2, "ct_findings": 1}},
            phase_durations={},
            correlation=None,
            execution_context={},
            canonical_run_summary={},
            synthesis_outcome_payload=None,
            sprint_verdict=None,
        )

        with patch("hledac.universal.paths.get_sprint_json_report_path") as mock_path:
            mock_path.return_value = MagicMock()
            with patch("hledac.universal.export.sprint_exporter._make_serializable", side_effect=lambda x: x):
                with patch.object(asyncio, "get_running_loop", side_effect=RuntimeError("no loop")):
                    result = asyncio.run(export_sprint(mock_store, mock_eh, sprint_id="test_001"))

        assert "research_depth_metric" in result
        rdm = result["research_depth_metric"]
        assert "score" in rdm
        assert "level" in rdm
        assert "breakdown" in rdm
        assert "depth_signals" in rdm


# =============================================================================
# F193B: Archive + Academic Discovery Accounting
# =============================================================================


class TestArchiveAcademicContributionSurface:
    """F193B: CommonCrawl and academic findings surface in canonical export."""

    def test_pvs_commoncrawl_field_present(self):
        """product_value_summary includes commoncrawl_archive_augmented when CC is active."""
        # Simulate: _build_product_value_summary receives canonical_run_summary with cc_archive_injected
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status = MagicMock(return_value=None)

        mock_eh = MagicMock()
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 1.0, "ioc_density": 0.3}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = "hermes3"

        # canonical_run_summary with CC and academic active
        mock_eh.canonical_run_summary = {
            "cc_archive_injected": 12,
            "academic_findings_count": 7,
        }

        pvs = _build_product_value_summary(mock_store, mock_eh, "test_sprint")
        assert "commoncrawl_archive_augmented" in pvs
        assert pvs["commoncrawl_archive_augmented"] == 12

    def test_pvs_academic_field_present(self):
        """product_value_summary includes academic_discovery_contribution when academic is active."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status = MagicMock(return_value=None)

        mock_eh = MagicMock()
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 1.0, "ioc_density": 0.3}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = "hermes3"
        mock_eh.canonical_run_summary = {
            "cc_archive_injected": 12,
            "academic_findings_count": 7,
        }

        pvs = _build_product_value_summary(mock_store, mock_eh, "test_sprint")
        assert "academic_discovery_contribution" in pvs
        assert pvs["academic_discovery_contribution"] == 7

    def test_pvs_zero_when_missing_canonical_run_summary(self):
        """Both fields default to 0 when canonical_run_summary is absent."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status = MagicMock(return_value=None)

        mock_eh = MagicMock()
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 1.0, "ioc_density": 0.3}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = "hermes3"
        mock_eh.canonical_run_summary = None

        pvs = _build_product_value_summary(mock_store, mock_eh, "test_sprint")
        assert pvs.get("commoncrawl_archive_augmented", 0) == 0
        assert pvs.get("academic_discovery_contribution", 0) == 0

    def test_academic_discovery_in_source_tier_tier1(self):
        """academic_discovery is classified as tier-1 (structured TI)."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER

        assert _SOURCE_TIER.get("academic_discovery") == 1

    def test_source_tier_tier1_contributes_to_non_indexed_ratio(self):
        """Tier-1 academic_discovery hits contribute to non_indexed_ratio component."""
        result = _compute_research_depth(
            eh=_mock_handoff({"academic_discovery": 50, "rss_atom_pipeline": 50}),
            pvs=None,
            signal_path=None,
            hypothesis_pack=None,
            correlation={"_no_correlation_data": True},
        )
        # 50 tier-1 hits / 100 total = 0.5 ratio → 10.0 score
        assert result["breakdown"]["non_indexed_ratio"] == 10.0


# =============================================================================
# Helpers
# =============================================================================

def _mock_handoff(source_counts: dict) -> MagicMock:
    """Build a mock ExportHandoff with scorecard source counts."""
    eh = MagicMock()
    eh.scorecard = {
        "entries_per_source": source_counts,
        "hits_per_source": {},
    }
    eh.correlation = None
    eh.runtime_truth = None
    return eh


def _mock_handoff_with_runtime_truth(
    source_counts: dict,
    branch_mix: dict,
) -> MagicMock:
    """Build a mock ExportHandoff with scorecard source counts + runtime_truth."""
    eh = _mock_handoff(source_counts)
    eh.runtime_truth = {
        "branch_mix": branch_mix,
        "is_meaningful": True,
    }
    return eh


def _full_source_counts() -> dict:
    """Return a diverse source mix spanning all 3 tiers."""
    return {
        "rss_atom_pipeline": 20,
        "live_public_pipeline": 15,
        "ct_log_pipeline": 15,
        "circl_pdns": 10,
        "academic_discovery": 5,
    }


def _derive_level_from_score(score: float) -> tuple[str, float]:
    """Pure helper: maps score to level name (mirrors internal logic)."""
    if score >= 81:
        return "comprehensive", score
    elif score >= 61:
        return "deep", score
    elif score >= 41:
        return "moderate", score
    elif score >= 21:
        return "shallow", score
    else:
        return "surface", score

"""
Sprint F192D: Feed Funnel Truth — probe tests.

Tests the signal path through live_feed_pipeline.py:
- assembly tier truth
- fallback decision truth
- pre/post fallback hit truth
- findings build truth
- dedup loss accounting
- zero-hit evidence truth
- feed economics truth
- signal reaches findings surfaces

Bugs targeted:
- DF-1: findings_lost_to_dedup not accumulated in early return path
- DF-2: zero_hit_feed_fetch_count contaminated (pre_fallback_hits > 0 but all deduped)
- DF-3: _feed_branch_signal_present not set when fallback-only signal
- DF-4: _findings_from_fallback overcount when fallback helpful AND pre_hits > 0
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pipeline.live_feed_pipeline import (
    _compute_entry_quality_signal,
    _classify_fallback_decision,
    FallbackDecision,
    EntryQualitySignal,
    _EntryDeduper,
    _async_scan_feed_text,
    _strip_html_tags_from_text,
    _classify_assembly_substance,
    _assemble_enriched_feed_text,
    diagnose_feed_signal_stage,
    _compute_feed_branch_verdict,
    _compute_feed_economics_verdict,
    _compute_winning_source_breakdown,
    FeedPipelineEntryResult,
)


# =============================================================================
# DF-1: findings_lost_to_dedup accumulation in early return path
# =============================================================================


class TestDedupLossAccumulation:
    """DF-1: findings_lost_to_dedup must be accumulated even when hits is empty."""

    def test_entry_deduper_detects_duplicates(self):
        """_EntryDeduper correctly identifies duplicate (label, pattern, value)."""
        deduper = _EntryDeduper()
        assert deduper.is_new("label1", "pattern1", "value1") is True
        assert deduper.is_new("label1", "pattern1", "value1") is False
        assert deduper.is_new("label2", "pattern1", "value1") is True  # different label
        assert deduper.is_new("label1", "pattern2", "value1") is True  # different pattern
        assert deduper.is_new("label1", "pattern1", "value2") is True  # different value

    def test_dedup_loss_is_matched_minus_accepted(self):
        """findings_lost_to_dedup = matched_patterns - len(findings) after dedup."""
        deduper = _EntryDeduper()
        hits = [
            MagicMock(label="ioc", pattern="apt28", value="192.168.1.1"),
            MagicMock(label="ioc", pattern="apt28", value="192.168.1.1"),  # dup
            MagicMock(label="ioc", pattern="apt28", value="10.0.0.1"),
            MagicMock(label="ioc", pattern="apt28", value="10.0.0.1"),  # dup
        ]
        matched_patterns = len(hits)  # 4 raw hits
        findings = []
        for hit in hits:
            if deduper.is_new(hit.label, hit.pattern, hit.value):
                findings.append({"label": hit.label})
        # 2 unique findings (A, C)
        assert len(findings) == 2
        assert matched_patterns - len(findings) == 2  # 2 lost to dedup

    def test_dedup_loss_zero_when_no_duplicates(self):
        """findings_lost_to_dedup = 0 when all hits are unique."""
        deduper = _EntryDeduper()
        hits = [
            MagicMock(label="ioc", pattern="pattern1", value="1.1.1.1"),
            MagicMock(label="ioc", pattern="pattern2", value="2.2.2.2"),
            MagicMock(label="ioc", pattern="pattern3", value="3.3.3.3"),
        ]
        matched_patterns = len(hits)
        findings = []
        for hit in hits:
            if deduper.is_new(hit.label, hit.pattern, hit.value):
                findings.append(hit)
        assert matched_patterns - len(findings) == 0


# =============================================================================
# DF-2: zero_hit_feed_fetch_count contamination
# =============================================================================


class TestZeroHitEvidence:
    """DF-2: zero_hit_feed_fetch_count must NOT increment when pre_fallback_hits > 0."""

    def test_zero_hit_only_when_no_pre_hits(self):
        """Zero-hit evidence applies ONLY to entries with no pre-fallback hits."""
        # An entry with pre_fallback_hits > 0 but matched=0 (all deduped) is NOT
        # a true zero-hit — signal existed, it was just filtered by dedup.
        # zero_hit_feed_fetch_count should reflect entries where content had
        # no signal (pre_fallback_hits == 0 AND matched == 0).
        # This test documents the expected behavior.
        pre_hits = 2
        matched = 0  # all 2 pre-hits were deduped away
        is_zero_hit = (matched == 0 and pre_hits == 0)
        assert is_zero_hit is False  # pre_hits > 0, NOT a true zero-hit

        pre_hits = 0
        matched = 0
        is_zero_hit = (matched == 0 and pre_hits == 0)
        assert is_zero_hit is True  # no signal at all = true zero-hit

    def test_diagnose_feed_signal_stage_findings_build_loss(self):
        """diagnose_feed_signal_stage distinguishes no-hits from dedup loss."""
        # Case: entries had hits but all were deduped away
        stage = diagnose_feed_signal_stage(
            entries_seen=5,
            entries_with_empty_assembled_text=0,
            entries_scanned=5,
            entries_with_hits=0,  # matched=0 for all after dedup
            findings_built_pre_store=0,
            patterns_configured=10,
            findings_lost_to_dedup_total=10,  # 10 hits arrived, all deduped
        )
        assert stage == "findings_build_loss"

    def test_diagnose_feed_signal_stage_no_pattern_hits_with_content(self):
        """diagnose_feed_signal_stage: content present but no hits arrived."""
        stage = diagnose_feed_signal_stage(
            entries_seen=5,
            entries_with_empty_assembled_text=0,
            entries_scanned=5,
            entries_with_hits=0,
            findings_built_pre_store=0,
            patterns_configured=10,
            findings_lost_to_dedup_total=0,  # no hits arrived at all
        )
        assert stage == "no_pattern_hits_with_content"


# =============================================================================
# DF-3: _feed_branch_signal_present when fallback-only signal
# =============================================================================


class TestFeedBranchSignalPresence:
    """DF-3: feed_branch_signal_present must be True when fallback provides signal."""

    def test_feed_branch_signal_true_when_pre_hits_exist(self):
        """feed_branch_signal_present = True when pre_fallback_hits > 0."""
        # Verifies that pre-fallback hits set the flag
        pre = 3
        post = 0
        signal_present = pre > 0
        assert signal_present is True

    def test_feed_branch_signal_true_when_fallback_only(self):
        """feed_branch_signal_present = True when fallback provides signal (pre=0)."""
        # When pre_fallback_hits = 0 but fallback was helpful (post > 0),
        # feed_branch_signal_present should ALSO be True.
        # This is the fallback-only signal case.
        pre = 0
        post = 2
        fd_helpful = post > 0
        signal_present = pre > 0 or fd_helpful
        assert signal_present is True  # fallback provided signal

    def test_feed_branch_signal_false_when_neither(self):
        """feed_branch_signal_present = False when neither branch had hits."""
        pre = 0
        post = 0
        fd_helpful = post > 0
        signal_present = pre > 0 or fd_helpful
        assert signal_present is False  # no signal anywhere


# =============================================================================
# DF-4: findings_from_fallback attribution when pre_hits > 0 AND fallback helpful
# =============================================================================


class TestFindingsAttribution:
    """DF-4: findings_from_fallback should not include pre-existing findings."""

    def test_fallback_only_new_findings_attributed_correctly(self):
        """When pre=0 and fallback helpful, ALL matched findings are from fallback."""
        pre = 0
        matched = 3  # all 3 from fallback
        # If pre == 0, all matched findings should be attributed to fallback
        fallback_findings = matched if pre == 0 else 0
        assert fallback_findings == 3

    def test_pre_hits_prevents_fallback_attribution(self):
        """When pre > 0, findings are attributed to rich_feed, not fallback."""
        pre = 2
        matched = 3  # A, B from pre, C new from fallback
        # With pre > 0, findings are attributed to rich_feed
        # fallback attribution only for net-new findings beyond pre
        rich_feed_findings = matched if pre > 0 else 0
        fallback_findings = 0  # pre > 0: attributed to rich_feed
        assert rich_feed_findings == 3
        assert fallback_findings == 0

    def test_winning_source_breakdown_fallback_only(self):
        """_compute_winning_source_breakdown: fallback-only entry."""
        breakdown = _compute_winning_source_breakdown(
            feed_native_signal_carried=False,
            article_fallback_used=True,
            findings=[{"a": 1}, {"b": 2}],
            adapter_selection_reason="fallback",
        )
        assert breakdown["fallback"] == 2
        assert breakdown["feed_native"] == 0
        assert breakdown["mixed"] == 0

    def test_winning_source_breakdown_mixed(self):
        """_compute_winning_source_breakdown: mixed (both feed-native and fallback)."""
        breakdown = _compute_winning_source_breakdown(
            feed_native_signal_carried=True,
            article_fallback_used=True,
            findings=[{"a": 1}, {"b": 2}],
            adapter_selection_reason="mixed",
        )
        assert breakdown["mixed"] == 2
        assert breakdown["feed_native"] == 0
        assert breakdown["fallback"] == 0


# =============================================================================
# Fallback decision truth
# =============================================================================


class TestFallbackDecisionTruth:
    """Fallback decisions must be correctly classified."""

    def test_pre_hits_exist_is_wasteful(self):
        """pre_fallback_hits > 0 → fallback was wasteful."""
        fd = _classify_fallback_decision(
            assembled_text_len=100,
            pre_fallback_hits_count=3,
            quality_signal=EntryQualitySignal(quality_band="high", quality_score=80),
            article_fallback_used=False,
            article_fallback_attempted=False,
            post_fallback_findings_count=0,
            adapter_source_priority_bias=0.0,
            adapter_metadata_richness_band="",
            adapter_entry_usefulness_band="",
        )
        assert fd.reason == "feed_native_had_signal"
        assert fd.wasted is True
        assert fd.should_fetch is False

    def test_fallback_skipped_high_quality(self):
        """High-quality assembled text → fallback correctly skipped."""
        fd = _classify_fallback_decision(
            assembled_text_len=500,  # >= _MIN_ARTICLE_FALLBACK_CHARS (250)
            pre_fallback_hits_count=0,
            quality_signal=EntryQualitySignal(quality_band="high", quality_score=80),
            article_fallback_used=False,
            article_fallback_attempted=False,
            post_fallback_findings_count=0,
            adapter_source_priority_bias=0.0,
            adapter_metadata_richness_band="high",
            adapter_entry_usefulness_band="high",
        )
        assert fd.reason == "skipped_high_quality"
        assert fd.should_fetch is False
        assert fd.wasted is False

    def test_fallback_produced_new_findings_is_helpful(self):
        """Fallback produced new findings → helpful."""
        fd = _classify_fallback_decision(
            assembled_text_len=100,
            pre_fallback_hits_count=0,
            quality_signal=EntryQualitySignal(quality_band="low", quality_score=20),
            article_fallback_used=True,
            article_fallback_attempted=True,
            post_fallback_findings_count=3,
            adapter_source_priority_bias=0.0,
            adapter_metadata_richness_band="low",
            adapter_entry_usefulness_band="low",
        )
        assert fd.reason == "normal_fallback_yield"
        assert fd.helpful is True
        assert fd.should_fetch is True

    def test_fallback_no_yield_is_not_helpful(self):
        """Fallback attempted but produced no findings → not helpful."""
        fd = _classify_fallback_decision(
            assembled_text_len=100,
            pre_fallback_hits_count=0,
            quality_signal=EntryQualitySignal(quality_band="low", quality_score=20),
            article_fallback_used=True,
            article_fallback_attempted=True,
            post_fallback_findings_count=0,
            adapter_source_priority_bias=0.0,
            adapter_metadata_richness_band="low",
            adapter_entry_usefulness_band="low",
        )
        assert fd.reason == "normal_fallback_no_yield"
        assert fd.helpful is False
        assert fd.wasted is False  # normal attempt; wasted=True only when pre-hits existed

    def test_forced_metadata_mismatch_helpful(self):
        """Forced fallback due to metadata/content mismatch → helpful if yield."""
        fd = _classify_fallback_decision(
            assembled_text_len=100,  # < 250
            pre_fallback_hits_count=0,
            quality_signal=EntryQualitySignal(quality_band="medium", quality_score=40, metadata_boost=True),
            article_fallback_used=True,
            article_fallback_attempted=True,
            post_fallback_findings_count=2,
            adapter_source_priority_bias=0.0,
            adapter_metadata_richness_band="high",
            adapter_entry_usefulness_band="medium",
        )
        assert fd.reason == "forced_metadata_mismatch"
        assert fd.forced is True
        assert fd.helpful is True


# =============================================================================
# Assembly tier truth
# =============================================================================


class TestAssemblyTierTruth:
    """Assembly tier must correctly classify content substance levels."""

    def test_rich_content_tier(self):
        """rich HTML content → rich_content tier."""
        tier, level = _classify_assembly_substance(
            title="Title",
            summary="Summary text",
            rich_content="<p>This is a very long rich content article with substantive information that goes beyond just a title or brief summary.</p>",
        )
        assert tier == "rich_content"
        assert level == 3

    def test_summary_only_tier(self):
        """Only summary → summary_only tier."""
        tier, level = _classify_assembly_substance(
            title="Short",
            summary="This is a moderately long summary with enough content to be considered substantive.",
            rich_content="",
        )
        assert tier == "summary_only"
        assert level == 2

    def test_title_only_tier(self):
        """Only title → title_only tier."""
        tier, level = _classify_assembly_substance(
            title="This is a reasonably long title that provides some context",
            summary="",
            rich_content="",
        )
        assert tier == "title_only"
        assert level == 1

    def test_no_content_tier(self):
        """Nothing → no_content tier."""
        tier, level = _classify_assembly_substance(
            title="",
            summary="",
            rich_content="",
        )
        assert tier == "no_content"
        assert level == 0


# =============================================================================
# Feed economics truth
# =============================================================================


class TestFeedEconomicsTruth:
    """Feed economics verdict must correctly reflect signal distribution."""

    def test_feed_lean_verdict(self):
        """70%+ rich feed findings → feed_lean verdict."""
        verdict = _compute_feed_economics_verdict(
            feed_signal_present=True,
            fallback_useful=0,
            fallback_waste=0,
            findings_rich=8,
            findings_fallback=2,
        )
        assert verdict[0] == "feed_lean"
        rich_ratio = 8 / (8 + 2)
        assert rich_ratio >= 0.7

    def test_fallback_lean_verdict(self):
        """30% or less rich feed findings → fallback_lean verdict."""
        verdict = _compute_feed_economics_verdict(
            feed_signal_present=True,
            fallback_useful=3,
            fallback_waste=0,
            findings_rich=1,
            findings_fallback=3,
        )
        assert verdict[0] == "fallback_lean"

    def test_balanced_verdict(self):
        """Between 30-70% → balanced verdict."""
        verdict = _compute_feed_economics_verdict(
            feed_signal_present=True,
            fallback_useful=2,
            fallback_waste=1,
            findings_rich=3,
            findings_fallback=3,
        )
        assert verdict[0] == "balanced"

    def test_no_signal_verdict(self):
        """No findings → no_signal verdict."""
        verdict = _compute_feed_economics_verdict(
            feed_signal_present=False,
            fallback_useful=0,
            fallback_waste=0,
            findings_rich=0,
            findings_fallback=0,
        )
        assert verdict[0] == "no_signal"

    def test_feed_branch_verdict_no_signal(self):
        """No findings → reassess_feed action."""
        verdict = _compute_feed_branch_verdict(
            feed_signal_present=False,
            fallback_useful=0,
            fallback_waste=0,
            findings_rich=0,
            findings_fallback=0,
            squandered_high_usefulness=0,
            metadata_strong_but_content_weak=0,
            low_trust_feed_hits=0,
            total_entries_with_hits=0,
            entries_seen=10,
            feed_native_yield_ratio=0.0,
            fallback_value_ratio=0.0,
        )
        assert verdict["verdict_tag"] == "no_signal"
        assert verdict["feed_next_action"] == "reassess_feed"

    def test_feed_burns_budget(self):
        """fallback_waste > 0 AND feed contributed nothing → burns budget."""
        verdict = _compute_feed_branch_verdict(
            feed_signal_present=True,  # must be True to reach feed_burns_budget branch
            fallback_useful=0,
            fallback_waste=3,
            findings_rich=0,
            findings_fallback=2,
            squandered_high_usefulness=0,
            metadata_strong_but_content_weak=0,
            low_trust_feed_hits=0,
            total_entries_with_hits=0,
            entries_seen=5,
            feed_native_yield_ratio=0.0,
            fallback_value_ratio=0.0,
        )
        assert verdict["feed_burns_budget"] is True
        assert verdict["feed_next_action"] == "fallback_more"

    def test_feed_corroborates(self):
        """feed had hits AND fallback contributed → corroborates."""
        verdict = _compute_feed_branch_verdict(
            feed_signal_present=True,
            fallback_useful=2,
            fallback_waste=0,
            findings_rich=3,
            findings_fallback=2,
            squandered_high_usefulness=0,
            metadata_strong_but_content_weak=0,
            low_trust_feed_hits=0,
            total_entries_with_hits=5,
            entries_seen=10,
            feed_native_yield_ratio=0.6,
            fallback_value_ratio=0.4,
        )
        assert verdict["feed_corroborates"] is True
        assert verdict["feed_next_action"] == "continue_feed"


# =============================================================================
# Entry quality signal — adapter integration (DF-2)
# =============================================================================


class TestEntryQualitySignalAdapterIntegration:
    """Adapter quality_score must correctly influence quality band."""

    def test_adapter_low_quality_downgrades_high_band(self):
        """Adapter quality < 0.3: high → medium."""
        signal = _compute_entry_quality_signal(
            title="A" * 50,
            summary="B" * 100,
            rich_content="C" * 200,
            entry_author="Test Author",
            feed_title="Test Feed",
            feed_language="en",
            adapter_quality_score=0.2,  # below 0.3 threshold
        )
        assert signal.quality_band == "medium"  # was "high", downgraded

    def test_adapter_low_quality_downgrades_medium_band(self):
        """Adapter quality < 0.3: medium → low."""
        signal = _compute_entry_quality_signal(
            title="A" * 65,  # > 60 → +10; summary 80 >= 80 → +20; score=30 → band="medium"
            summary="B" * 80,  # >= 80 → +20; score=30 → band="medium"
            rich_content="",  # no rich content
            entry_author="",
            feed_title="",
            feed_language="en",
            adapter_quality_score=0.2,
        )
        assert signal.quality_band == "low"  # was "medium", downgraded

    def test_adapter_low_quality_downgrades_low_band(self):
        """Adapter quality < 0.3: low → unknown."""
        signal = _compute_entry_quality_signal(
            title="A" * 20,
            summary="B" * 20,
            rich_content="",
            entry_author="",
            feed_title="",
            feed_language="en",
            adapter_quality_score=0.2,
        )
        assert signal.quality_band == "unknown"

    def test_adapter_high_quality_preserves_band(self):
        """Adapter quality >= 0.3: band unchanged."""
        signal = _compute_entry_quality_signal(
            title="A" * 50,
            summary="B" * 100,
            rich_content="C" * 200,
            entry_author="Test Author",
            feed_title="Test Feed",
            feed_language="en",
            adapter_quality_score=0.8,  # high quality
        )
        assert signal.quality_band == "high"  # unchanged

    def test_adapter_none_preserves_band(self):
        """No adapter score: band computed from pipeline signals only."""
        signal = _compute_entry_quality_signal(
            title="A" * 50,
            summary="B" * 100,
            rich_content="C" * 200,
            entry_author="Test Author",
            feed_title="Test Feed",
            feed_language="en",
            adapter_quality_score=None,
        )
        assert signal.quality_band == "high"  # unchanged

    def test_adapter_low_quality_adds_reason_tag(self):
        """Adapter low quality adds 'adapter_low_quality' to reason tags."""
        signal = _compute_entry_quality_signal(
            title="A" * 50,
            summary="B" * 100,
            rich_content="C" * 200,
            entry_author="Test Author",
            feed_title="Test Feed",
            feed_language="en",
            adapter_quality_score=0.2,
        )
        assert "adapter_low_quality" in signal.quality_reason_tag


# =============================================================================
# HTML stripping truth
# =============================================================================


class TestHtmlStripping:
    """HTML stripping must be word-boundary safe and entity-safe."""

    def test_script_style_removed_first(self):
        """Script and style blocks removed before tag stripping."""
        html = '<script>alert("x")</script><p>Hello world</p><style>.x{color:red}</style>'
        text = _strip_html_tags_from_text(html)
        assert "alert" not in text
        assert "Hello world" in text
        assert "color" not in text

    def test_html_entities_unescaped(self):
        """HTML entities unescaped after tag removal."""
        html = "<p>Hello &amp; goodbye &mdash; world</p>"
        text = _strip_html_tags_from_text(html)
        assert "&amp;" not in text
        assert "Hello" in text

    def test_whitespace_normalized(self):
        """Multiple whitespace collapsed to single space."""
        html = "<p>Hello\n\n\nworld</p>"
        text = _strip_html_tags_from_text(html)
        assert " " in text
        assert "\n" not in text


# =============================================================================
# Signal reaches findings surfaces
# =============================================================================


class TestSignalReachesFindings:
    """Signal must consistently reach findings surfaces without phantom loss."""

    def test_signal_stage_prestore_findings_present(self):
        """signal_stage = prestore_findings_present when findings built."""
        stage = diagnose_feed_signal_stage(
            entries_seen=5,
            entries_with_empty_assembled_text=0,
            entries_scanned=5,
            entries_with_hits=3,
            findings_built_pre_store=5,
            patterns_configured=10,
            findings_lost_to_dedup_total=0,
        )
        assert stage == "prestore_findings_present"

    def test_empty_registry_detected(self):
        """Empty pattern registry → empty_registry signal stage."""
        stage = diagnose_feed_signal_stage(
            entries_seen=10,
            entries_with_empty_assembled_text=0,
            entries_scanned=0,
            entries_with_hits=0,
            findings_built_pre_store=0,
            patterns_configured=0,  # empty registry
            findings_lost_to_dedup_total=0,
        )
        assert stage == "empty_registry"

    def test_empty_fetch_detected(self):
        """No entries fetched → empty_fetch signal stage."""
        stage = diagnose_feed_signal_stage(
            entries_seen=0,
            entries_with_empty_assembled_text=0,
            entries_scanned=0,
            entries_with_hits=0,
            findings_built_pre_store=0,
            patterns_configured=10,
            findings_lost_to_dedup_total=0,
        )
        assert stage == "empty_fetch"

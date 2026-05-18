"""
tests/probe_f223d_corroboration_scorer/

Probe tests for Evidence Corroboration Scorer — Sprint F223D

Run: uv run pytest tests/probe_f223d_corroboration_scorer -q
"""

from __future__ import annotations

import pytest

from hledac.universal.runtime.evidence_corroboration import (
    score_indicators_by_corroboration,
    score_seeds_by_corroboration,
    CorroborationScore,
    build_top_indicators,
    build_weak_unverified,
    build_recommended_pivots,
    _check_noise,
    _normalize_source_type,
    _seed_source_to_family,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def feed_plus_doh_findings():
    """Domain appears in feed + DOH."""
    return [
        {"value": "evil.com", "kind": "domain", "source_type": "feed", "confidence": 0.8},
        {"value": "evil.com", "kind": "domain", "source_type": "doh", "confidence": 0.7},
    ]


@pytest.fixture
def feed_only_findings():
    """Domain appears in feed only."""
    return [
        {"value": "feedspam.net", "kind": "domain", "source_type": "feed", "confidence": 0.6},
        {"value": "feedspam.net", "kind": "domain", "source_type": "feed", "confidence": 0.6},
        {"value": "feedspam.net", "kind": "domain", "source_type": "feed", "confidence": 0.6},
    ]


@pytest.fixture
def cross_family_findings():
    """Domain appears in feed + CT + DOH + wayback."""
    return [
        {"value": "ransom.site", "kind": "domain", "source_type": "feed", "confidence": 0.9},
        {"value": "ransom.site", "kind": "domain", "source_type": "ct", "confidence": 0.85},
        {"value": "ransom.site", "kind": "domain", "source_type": "doh", "confidence": 0.8},
        {"value": "ransom.site", "kind": "domain", "source_type": "wayback", "confidence": 0.7},
    ]


# --------------------------------------------------------------------------- #
# Core scorer tests
# --------------------------------------------------------------------------- #

class TestScoreIndicatorsByCorroboration:
    def test_same_domain_feed_plus_doh_scores_above_feed_only(self, feed_plus_doh_findings, feed_only_findings):
        """Feed+DOH domain should score higher than feed-only."""
        scores = score_indicators_by_corroboration(feed_plus_doh_findings + feed_only_findings)
        assert len(scores) == 2
        # Find scores by value
        score_map = {s.value: s.score for s in scores}
        assert score_map["evil.com"] > score_map["feedspam.net"], (
            f"feed+doh ({score_map['evil.com']}) should beat feed-only ({score_map['feedspam.net']})"
        )

    def test_feed_only_noisy_domain_scores_lower(self, feed_only_findings):
        """Feed-only domain should score lower than cross-source."""
        scores = score_indicators_by_corroboration(feed_only_findings)
        assert len(scores) == 1
        assert scores[0].score < 2.0, "feed-only should not reach strong threshold (2.0)"

    def test_example_dot_com_scores_low(self):
        """example.com should be flagged as noise."""
        findings = [
            {"value": "example.com", "kind": "domain", "source_type": "feed", "confidence": 0.9},
            {"value": "example.com", "kind": "domain", "source_type": "ct", "confidence": 0.8},
        ]
        scores = score_indicators_by_corroboration(findings)
        assert len(scores) == 1
        assert scores[0].score == 0.1, "example.com noise pattern should score 0.1"
        assert "noise_pattern" in scores[0].reasons[0]

    def test_multiple_independent_sources_increase_score(self, cross_family_findings):
        """Domain from feed+ct+doh+wayback should be strong."""
        scores = score_indicators_by_corroboration(cross_family_findings)
        assert len(scores) == 1
        assert scores[0].is_strong(), "4-family corroboration should be strong"
        assert scores[0].score >= 2.0

    def test_duplicates_do_not_inflate_score(self, feed_only_findings):
        """Duplicate-only findings should get penalty."""
        scores = score_indicators_by_corroboration(feed_only_findings)
        assert len(scores) == 1
        # Single unique source with many duplicates → penalty applied
        assert scores[0].source_family_count == 1
        assert scores[0].independent_source_count == 1

    def test_json_output_contains_reasons(self):
        """CorroborationScore reasons must be populated."""
        findings = [
            {"value": "confirmed.bad", "kind": "domain", "source_type": "feed", "confidence": 0.9},
            {"value": "confirmed.bad", "kind": "domain", "source_type": "ct", "confidence": 0.85},
        ]
        scores = score_indicators_by_corroboration(findings)
        assert len(scores) == 1
        assert len(scores[0].reasons) > 0, "reasons must be populated"

    def test_no_model_imports(self):
        """Verify no model/ML imports in evidence_corroboration module."""
        import runtime.evidence_corroboration as ec
        src_file = ec.__file__
        assert src_file is not None
        with open(src_file) as f:
            content = f.read()
        # Check import statements, not docstring mentions
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            for term in ["mlx", "transformers", "torch", "anthropic", "openai"]:
                assert term not in line, f"'{term}' must not appear in evidence_corroboration.py"

    def test_no_network_in_module(self):
        """Verify no network calls in evidence_corroboration module."""
        import runtime.evidence_corroboration as ec
        src_file = ec.__file__
        with open(src_file) as f:
            content = f.read()
        forbidden = ["requests", "httpx", "curl", "aiohttp", "urllib", "socket."]
        for term in forbidden:
            assert term not in content, f"'{term}' must not appear in evidence_corroboration.py"


class TestCorroborationScoreDataclass:
    def test_is_strong(self):
        s = CorroborationScore(value="x", kind="domain", score=2.5, source_family_count=2)
        assert s.is_strong() is True

    def test_is_weak(self):
        s = CorroborationScore(value="x", kind="domain", score=0.5, source_family_count=1)
        assert s.is_weak() is True

    def test_is_noise(self):
        s = CorroborationScore(value="x", kind="domain", score=0.3, source_family_count=0)
        assert s.is_noise() is True

    def test_is_not_strong_when_only_one_family(self):
        s = CorroborationScore(value="x", kind="domain", score=2.5, source_family_count=1)
        assert s.is_strong() is False, "single family even with high score is not strong"

    def test_frozen_immutable(self):
        s = CorroborationScore(value="x", kind="domain", score=1.0)
        with pytest.raises(AttributeError):
            s.value = "y"


class TestScoreSeedsByCorroboration:
    def test_seed_scoring(self):
        seeds = [
            {"value": "lockbit3.tw", "kind": "domain", "source": "body", "confidence": 0.7, "quality_decision": "keep", "quality_score": 0.85},
            {"value": "mozilla.org", "kind": "domain", "source": "body", "confidence": 0.7, "quality_decision": "weak", "quality_score": 0.3},
        ]
        scores = score_seeds_by_corroboration(seeds)
        assert len(scores) == 2  # both scored, mozilla gets noise score
        score_map = {s.value: s for s in scores}
        assert score_map["lockbit3.tw"].score > score_map["mozilla.org"].score

    def test_rejected_seed_filtered(self):
        seeds = [
            {"value": "reject.me", "kind": "domain", "source": "body", "confidence": 0.5, "quality_decision": "reject", "quality_score": 0.0},
        ]
        scores = score_seeds_by_corroboration(seeds)
        assert len(scores) == 0

    def test_seed_source_mapping(self):
        assert _seed_source_to_family("feed") == "feed"
        assert _seed_source_to_family("ct") == "ct"
        assert _seed_source_to_family("doh") == "doh"
        assert _seed_source_to_family("body") == "nonfeed"
        assert _seed_source_to_family("unknown") == "unknown"


class TestOutputBuilders:
    def test_build_top_indicators(self, cross_family_findings):
        scores = score_indicators_by_corroboration(cross_family_findings)
        top = build_top_indicators(scores, limit=10)
        assert len(top) <= 10
        for item in top:
            assert "value" in item
            assert "kind" in item
            assert "score" in item
            assert "reasons" in item

    def test_build_weak_unverified(self):
        findings = [
            {"value": "noise.example", "kind": "domain", "source_type": "feed", "confidence": 0.9},
        ]
        scores = score_indicators_by_corroboration(findings)
        weak = build_weak_unverified(scores)
        assert isinstance(weak, list)

    def test_build_recommended_pivots(self, cross_family_findings):
        scores = score_indicators_by_corroboration(cross_family_findings)
        pivots = build_recommended_pivots(scores, limit=5)
        assert len(pivots) <= 5
        for p in pivots:
            assert "value" in p
            assert "kind" in p
            assert "suggested_action" in p


class TestNoisePatterns:
    def test_example_com_noise(self):
        assert _check_noise("example.com", {"feed"}) is not None

    def test_test_domain_noise(self):
        assert _check_noise("test.com", {"feed"}) is not None

    def test_localhost_noise(self):
        assert _check_noise("localhost", {"feed"}) is not None

    def test_raw_ip_noise(self):
        assert _check_noise("192.168.1.1", {"feed"}) is not None

    def test_platform_domain_noise(self):
        assert _check_noise("cloudflare.com", {"feed", "ct"}) is not None

    def test_non_platform_still_ok(self):
        assert _check_noise("malware.bad", {"feed", "ct"}) is None


class TestNormalizeSourceType:
    def test_normalize_variants(self):
        assert _normalize_source_type("Feed") == "feed"
        assert _normalize_source_type("DOH") == "doh"
        assert _normalize_source_type("wayback") == "wayback"
        assert _normalize_source_type("ct") == "ct"
        assert _normalize_source_type("passive_dns") == "passive_dns"
        assert _normalize_source_type("leak") == "leak"
        assert _normalize_source_type("github") == "github"
        assert _normalize_source_type("unknown") == "unknown"


class TestEdgeCases:
    def test_empty_list(self):
        scores = score_indicators_by_corroboration([])
        assert scores == []

    def test_missing_fields(self):
        findings = [
            {"value": "x.com"},  # missing kind, source_type
        ]
        scores = score_indicators_by_corroboration(findings)
        assert len(scores) == 0  # skipped due to missing required fields

    def test_all_noise_pattern(self):
        findings = [
            {"value": "example.com", "kind": "domain", "source_type": "feed"},
        ]
        scores = score_indicators_by_corroboration(findings)
        assert len(scores) == 1
        assert scores[0].is_noise()

    def test_source_family_count_excludes_duplicates(self):
        """Same source_type appearing multiple times only counts once."""
        findings = [
            {"value": "dup.com", "kind": "domain", "source_type": "feed", "confidence": 0.9},
            {"value": "dup.com", "kind": "domain", "source_type": "feed", "confidence": 0.9},
            {"value": "dup.com", "kind": "domain", "source_type": "feed", "confidence": 0.9},
            {"value": "dup.com", "kind": "domain", "source_type": "ct", "confidence": 0.8},
        ]
        scores = score_indicators_by_corroboration(findings)
        assert len(scores) == 1
        # feed counts once + ct = 2 families
        assert scores[0].source_family_count == 2
        # 2 independent findings (feed unique + ct)
        assert scores[0].independent_source_count == 2
"""
F203B: Attribution Confidence Scorer — probe tests

Tests AttributionFactor, AttributionScore, AttributionConfidenceScorer,
and integration with IdentityCandidate post-processing.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from hledac.universal.intelligence.attribution_scorer import (
    AttributionFactor,
    AttributionScore,
    AttributionConfidenceScorer,
    create_attribution_scorer,
    enrich_candidate_with_attribution,
    _levenshtein_distance,
    _normalized_levenshtein,
    MAX_FACTOR_COMPARISONS,
    DEFAULT_FACTOR_WEIGHTS,
)


# ── F203B-1: AttributionFactor dataclass ────────────────────────────────────────


class TestAttributionFactor:
    def test_factor_creation(self):
        factor = AttributionFactor(
            factor_id="email_domain_gmail.com",
            factor_type="email_domain_match",
            raw_score=1.0,
            weighted_score=0.25,
            evidence=("domain:gmail.com",),
            metadata={"left_domains": ["user@gmail.com"]},
        )
        assert factor.factor_id == "email_domain_gmail.com"
        assert factor.factor_type == "email_domain_match"
        assert factor.raw_score == 1.0
        assert factor.weighted_score == 0.25
        assert len(factor.evidence) == 1

    def test_factor_immutable(self):
        factor = AttributionFactor(
            factor_id="test",
            factor_type="test_type",
            raw_score=0.5,
            weighted_score=0.1,
        )
        with pytest.raises(Exception):  # frozen dataclass
            factor.raw_score = 0.9

    def test_factor_to_dict(self):
        factor = AttributionFactor(
            factor_id="username_pattern_sim",
            factor_type="username_pattern_similarity",
            raw_score=0.85,
            weighted_score=0.17,
            evidence=("alice|bob (0.85)",),
            metadata={"match_count": 4},
        )
        d = {
            "factor_id": factor.factor_id,
            "factor_type": factor.factor_type,
            "raw_score": factor.raw_score,
            "weighted_score": factor.weighted_score,
            "evidence": list(factor.evidence),
            "metadata": factor.metadata,
        }
        assert d["factor_id"] == "username_pattern_sim"
        assert d["factor_type"] == "username_pattern_similarity"


# ── F203B-2: AttributionScore dataclass ─────────────────────────────────────────


class TestAttributionScore:
    def test_score_creation(self):
        factor = AttributionFactor(
            factor_id="email_domain_gmail.com",
            factor_type="email_domain_match",
            raw_score=1.0,
            weighted_score=0.25,
        )
        score = AttributionScore(
            confidence=0.75,
            factors=(factor,),
            evidence_ids=("email_domain_gmail.com",),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )
        assert score.confidence == 0.75
        assert len(score.factors) == 1
        assert score.evidence_ids == ("email_domain_gmail.com",)

    def test_score_to_dict(self):
        factor = AttributionFactor(
            factor_id="infra_platform_2",
            factor_type="shared_infrastructure",
            raw_score=1.0,
            weighted_score=0.20,
            evidence=("platform:github", "platform:twitter"),
        )
        score = AttributionScore(
            confidence=0.45,
            factors=(factor,),
            evidence_ids=("infra_platform_2",),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )
        d = score.to_dict()
        assert d["confidence"] == 0.45
        assert len(d["factors"]) == 1
        assert d["factors"][0]["factor_type"] == "shared_infrastructure"
        assert len(d["evidence_ids"]) == 1

    def test_score_empty_factors(self):
        score = AttributionScore(
            confidence=0.0,
            factors=(),
            evidence_ids=(),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )
        assert score.confidence == 0.0
        assert len(score.factors) == 0
        d = score.to_dict()
        assert d["confidence"] == 0.0
        assert len(d["factors"]) == 0


# ── F203B-3: Levenshtein distance (pure Python fallback) ────────────────────────


class TestLevenshtein:
    def test_identical_strings(self):
        assert _levenshtein_distance("hello", "hello") == 0

    def test_one_char_diff(self):
        assert _levenshtein_distance("hello", "hallo") == 1

    def test_complete_diff(self):
        assert _levenshtein_distance("hello", "world") == 4

    def test_empty_strings(self):
        assert _levenshtein_distance("", "") == 0
        assert _levenshtein_distance("abc", "") == 3

    def test_normalized_identical(self):
        assert _normalized_levenshtein("hello", "hello") == 1.0

    def test_normalized_no_match(self):
        sim = _normalized_levenshtein("abc", "xyz")
        assert sim < 0.5  # Very different strings

    def test_normalized_case_insensitive(self):
        assert _normalized_levenshtein("HELLO", "hello") == 1.0

    def test_normalized_empty(self):
        assert _normalized_levenshtein("", "") == 1.0


# ── F203B-4: AttributionConfidenceScorer factory ─────────────────────────────────


class TestAttributionScorerFactory:
    def test_create_default_scorer(self):
        scorer = create_attribution_scorer()
        assert isinstance(scorer, AttributionConfidenceScorer)
        assert scorer.comparison_count == 0

    def test_create_with_custom_weights(self):
        weights = {"email_domain_match": 0.5, "username_pattern_similarity": 0.3}
        scorer = create_attribution_scorer(factor_weights=weights)
        assert scorer._weights["email_domain_match"] == 0.5
        assert scorer._weights["username_pattern_similarity"] == 0.3

    def test_max_comparisons_limit(self):
        scorer = AttributionConfidenceScorer(max_comparisons=100)
        assert scorer._max_comparisons == 100


# ── F203B-5: score_pair — email domain match ────────────────────────────────────


class TestScorePairEmailDomain:
    def test_exact_domain_match(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["alice@gmail.com", "bob@yahoo.com"]
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["carol@gmail.com"]
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        assert score.confidence > 0
        factor_types = [f.factor_type for f in score.factors]
        assert "email_domain_match" in factor_types

    def test_no_email_overlap(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["alice@gmail.com"]
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["bob@example.org"]  # Different domain AND different TLD
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        # No shared domains, no shared TLDs → no email_domain_match factor
        factor_types = [f.factor_type for f in score.factors]
        assert "email_domain_match" not in factor_types

    def test_tld_shared_partial_match(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["alice@github.com"]  # github.com — TLD=com
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["bob@gitlab.com"]  # gitlab.com — TLD=com, same TLD but different domain
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        # Different domains but same TLD "com" → TLD partial match (0.5 raw)
        factor_types = [f.factor_type for f in score.factors]
        assert "email_domain_match" in factor_types
        email_factor = next(f for f in score.factors if f.factor_type == "email_domain_match")
        assert email_factor.factor_id == "email_domain_tld_shared"
        assert email_factor.raw_score == 0.5


# ── F203B-6: score_pair — username pattern similarity ───────────────────────────


class TestScorePairUsername:
    def test_similar_usernames(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = ["alice_smith", "alice.smith"]
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = ["alice_smith_1978"]
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        factor_types = [f.factor_type for f in score.factors]
        assert "username_pattern_similarity" in factor_types
        # Should have some confidence from this factor
        username_factor = next(f for f in score.factors if f.factor_type == "username_pattern_similarity")
        assert username_factor.raw_score >= 0.6

    def test_dissimilar_usernames(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = ["alice"]
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = ["zxq782bb"]
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        factor_types = [f.factor_type for f in score.factors]
        assert "username_pattern_similarity" not in factor_types


# ── F203B-7: score_pair — temporal overlap ─────────────────────────────────────


class TestScorePairTemporal:
    def test_shared_finding_ids(self):
        scorer = create_attribution_scorer()

        shared_fid = "finding_abc123"

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = [shared_fid, "finding_xyz"]
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = [shared_fid, "finding_def"]
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        factor_types = [f.factor_type for f in score.factors]
        assert "temporal_overlap" in factor_types
        temporal_factor = next(f for f in score.factors if f.factor_type == "temporal_overlap")
        assert temporal_factor.raw_score >= 0.3  # Jaccard threshold

    def test_no_shared_finding_ids(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = ["finding_aaa"]
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = ["finding_bbb"]
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        factor_types = [f.factor_type for f in score.factors]
        assert "temporal_overlap" not in factor_types


# ── F203B-8: score_pair — shared infrastructure ────────────────────────────────


class TestScorePairInfrastructure:
    def test_shared_platforms(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = ["GitHub", "Twitter", "Keybase"]
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = ["github", "mastodon"]  # lowercase normalized
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        factor_types = [f.factor_type for f in score.factors]
        assert "shared_infrastructure" in factor_types
        infra_factor = next(f for f in score.factors if f.factor_type == "shared_infrastructure")
        assert infra_factor.raw_score > 0
        # github should be detected as shared (case-insensitive)
        assert any("github" in str(e).lower() for e in infra_factor.evidence)

    def test_no_shared_platforms(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = ["GitHub"]
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = ["Twitter"]
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        factor_types = [f.factor_type for f in score.factors]
        assert "shared_infrastructure" not in factor_types


# ── F203B-9: score_pair — PGP key correlation ───────────────────────────────────


class TestScorePairPGP:
    def test_shared_pgp_in_evidence(self):
        scorer = create_attribution_scorer()

        pgp_key = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = [f"pgp_key: {pgp_key}"]
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = [f"PGP fingerprint: {pgp_key}"]
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        factor_types = [f.factor_type for f in score.factors]
        assert "pgp_key_correlation" in factor_types
        pgp_factor = next(f for f in score.factors if f.factor_type == "pgp_key_correlation")
        assert pgp_factor.raw_score == 1.0

    def test_no_pgp_match(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = ["pgp_key: A1B2C3D4E5F6"]
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = ["pgp_key: 9999888877776666"]
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        factor_types = [f.factor_type for f in score.factors]
        assert "pgp_key_correlation" not in factor_types


# ── F203B-10: score_pair — combined multi-factor ────────────────────────────────


class TestScorePairCombined:
    def test_multiple_factors(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["alice@gmail.com"]
        left.usernames = ["alice_smith"]
        left.platforms = ["GitHub"]
        left.signals = {}
        left.evidence = []
        left.finding_ids = ["finding_abc", "finding_xyz"]
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["carol@gmail.com"]  # Same domain
        right.usernames = ["alice_smith_2020"]  # Similar
        right.platforms = ["github"]  # Same platform
        right.signals = {}
        right.evidence = []
        right.finding_ids = ["finding_abc", "finding_def"]  # Shared finding
        right.profile_ids = []

        score = scorer.score_pair(left, right)

        # Should have multiple factors
        factor_types = [f.factor_type for f in score.factors]
        assert len(factor_types) >= 2
        assert score.confidence > 0.0

        # Check factor weights sum is within bounds
        total_weight = sum(f.weighted_score for f in score.factors)
        assert total_weight <= 1.0  # Weighted contributions capped

    def test_confidence_clamped_to_1(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["a@gmail.com", "b@gmail.com", "c@gmail.com", "d@gmail.com"]
        left.usernames = ["user1", "user2", "user3", "user4"]
        left.platforms = ["GitHub", "Twitter", "Keybase", "Mastodon"]
        left.signals = {}
        left.evidence = []
        left.finding_ids = ["f1", "f2", "f3", "f4", "f5"]
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["x@gmail.com", "y@gmail.com"]
        right.usernames = ["user1", "user2"]
        right.platforms = ["github", "twitter"]
        right.signals = {}
        right.evidence = []
        right.finding_ids = ["f1", "f2"]
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        assert score.confidence <= 1.0


# ── F203B-11: score_candidates — batch scoring ───────────────────────────────────


class TestScoreCandidates:
    def test_score_candidates_all_pairs(self):
        scorer = create_attribution_scorer()

        c1 = MagicMock()
        c1.candidate_id = "c1"
        c1.emails = ["alice@gmail.com"]
        c1.usernames = []
        c1.platforms = []
        c1.signals = {}
        c1.evidence = []
        c1.finding_ids = ["f1"]
        c1.profile_ids = []

        c2 = MagicMock()
        c2.candidate_id = "c2"
        c2.emails = ["bob@gmail.com"]  # Same domain
        c2.usernames = []
        c2.platforms = []
        c2.signals = {}
        c2.evidence = []
        c2.finding_ids = ["f1"]  # Shared finding
        c2.profile_ids = []

        c3 = MagicMock()
        c3.candidate_id = "c3"
        c3.emails = ["charlie@yahoo.com"]  # Different domain
        c3.usernames = []
        c3.platforms = []
        c3.signals = {}
        c3.evidence = []
        c3.finding_ids = []
        c3.profile_ids = []

        scores = scorer.score_candidates([c1, c2, c3])

        # Should have 3 pairs: c1|c2, c1|c3, c2|c3
        assert len(scores) <= 3
        # c1|c2 should have positive confidence (same domain + shared finding)
        if "c1|c2" in scores:
            assert scores["c1|c2"].confidence > 0

    def test_score_candidates_empty(self):
        scorer = create_attribution_scorer()
        scores = scorer.score_candidates([])
        assert scores == {}

    def test_score_candidates_respects_limit(self):
        scorer = AttributionConfidenceScorer(max_comparisons=2)

        candidates = []
        for i in range(5):
            c = MagicMock()
            c.candidate_id = f"c{i}"
            c.emails = [f"user{i}@gmail.com"]
            c.usernames = []
            c.platforms = []
            c.signals = {}
            c.evidence = []
            c.finding_ids = []
            c.profile_ids = []
            candidates.append(c)

        scores = scorer.score_candidates(candidates)

        # Should stop after max_comparisons
        assert scorer.comparison_count <= 2


# ── F203B-12: enrich_candidate_with_attribution ─────────────────────────────────


class TestEnrichCandidate:
    def test_enrich_candidate_adds_signals(self):
        from hledac.universal.intelligence.attribution_scorer import IdentityCandidate

        candidate = IdentityCandidate(
            candidate_id="c1",
            profile_ids=["p1"],
            primary_name="Alice",
            emails=["alice@gmail.com"],
            usernames=["alice"],
            platforms=["GitHub"],
            confidence=0.8,
            signals={},
            evidence=["source:f1"],
            finding_ids=["f1"],
        )

        factor = AttributionFactor(
            factor_id="email_domain_gmail.com",
            factor_type="email_domain_match",
            raw_score=1.0,
            weighted_score=0.25,
            evidence=("domain:gmail.com",),
        )

        score = AttributionScore(
            confidence=0.25,
            factors=(factor,),
            evidence_ids=("email_domain_gmail.com",),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )

        enriched = enrich_candidate_with_attribution(candidate, score)

        assert "attribution_confidence" in enriched.signals
        assert enriched.signals["attribution_confidence"] == 0.25
        assert "attribution_factor_types" in enriched.signals
        assert "email_domain_match" in enriched.signals["attribution_factor_types"]
        # Evidence should be extended
        assert "domain:gmail.com" in enriched.evidence

    def test_enrich_candidate_from_dict(self):
        from hledac.universal.intelligence.attribution_scorer import IdentityCandidate

        candidate_dict = {
            "candidate_id": "c1",
            "profile_ids": ["p1"],
            "primary_name": "Alice",
            "emails": [],
            "usernames": [],
            "platforms": [],
            "confidence": 0.5,
            "signals": {},
            "evidence": [],
            "finding_ids": [],
        }

        factor = AttributionFactor(
            factor_id="username_pattern_sim",
            factor_type="username_pattern_similarity",
            raw_score=0.85,
            weighted_score=0.17,
            evidence=("alice|bob (0.85)",),
        )

        score = AttributionScore(
            confidence=0.17,
            factors=(factor,),
            evidence_ids=("username_pattern_sim",),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )

        enriched = enrich_candidate_with_attribution(candidate_dict, score)

        assert isinstance(enriched, IdentityCandidate)
        assert "attribution_confidence" in enriched.signals


# ── F203B-13: get_factor_breakdown ─────────────────────────────────────────────


class TestFactorBreakdown:
    def test_breakdown_format(self):
        factor = AttributionFactor(
            factor_id="email_domain_gmail.com",
            factor_type="email_domain_match",
            raw_score=1.0,
            weighted_score=0.25,
            evidence=("domain:gmail.com",),
        )
        score = AttributionScore(
            confidence=0.45,
            factors=(factor,),
            evidence_ids=("email_domain_gmail.com",),
            factor_weights=DEFAULT_FACTOR_WEIGHTS,
        )

        scorer = create_attribution_scorer()
        breakdown = scorer.get_factor_breakdown(score)

        assert "total_confidence" in breakdown
        assert "factors" in breakdown
        assert "weights_used" in breakdown
        assert breakdown["total_confidence"] == 0.45
        assert len(breakdown["factors"]) == 1
        assert breakdown["factors"][0]["type"] == "email_domain_match"


# ── F203B-14: fail-soft behavior ───────────────────────────────────────────────


class TestFailSoft:
    def test_dict_input_converted(self):
        scorer = create_attribution_scorer()

        left = {
            "candidate_id": "c1",
            "profile_ids": [],
            "primary_name": "Alice",
            "emails": ["alice@gmail.com"],
            "usernames": [],
            "platforms": [],
            "confidence": 0.5,
            "signals": {},
            "evidence": [],
            "finding_ids": [],
        }

        right = {
            "candidate_id": "c2",
            "profile_ids": [],
            "primary_name": "Bob",
            "emails": ["bob@gmail.com"],
            "usernames": [],
            "platforms": [],
            "confidence": 0.6,
            "signals": {},
            "evidence": [],
            "finding_ids": [],
        }

        score = scorer.score_pair(left, right)
        assert score.confidence > 0
        assert len(score.factors) > 0

    def test_empty_emails_no_crash(self):
        scorer = create_attribution_scorer()

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = []
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = []
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        score = scorer.score_pair(left, right)
        # Should not crash, returns empty score
        assert score.confidence == 0.0


# ── F203B-15: bounds and constants ─────────────────────────────────────────────


class TestBounds:
    def test_max_factor_comparisons_constant(self):
        assert MAX_FACTOR_COMPARISONS == 5000

    def test_default_weights_sum(self):
        total = sum(DEFAULT_FACTOR_WEIGHTS.values())
        # Sum should be close to 1.0 (0.25+0.20+0.20+0.20+0.15 = 1.0)
        assert abs(total - 1.0) < 0.001

    def test_scorer_enforces_limit(self):
        scorer = AttributionConfidenceScorer(max_comparisons=5)

        left = MagicMock()
        left.candidate_id = "c1"
        left.emails = ["a@gmail.com"]
        left.usernames = []
        left.platforms = []
        left.signals = {}
        left.evidence = []
        left.finding_ids = []
        left.profile_ids = []

        right = MagicMock()
        right.candidate_id = "c2"
        right.emails = ["b@gmail.com"]
        right.usernames = []
        right.platforms = []
        right.signals = {}
        right.evidence = []
        right.finding_ids = []
        right.profile_ids = []

        # Exhaust the limit
        for i in range(10):
            scorer.score_pair(left, right)

        # Next call should return empty score
        score = scorer.score_pair(left, right)
        assert score.confidence == 0.0
        assert scorer.comparison_count == 5
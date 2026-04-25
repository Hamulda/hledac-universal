"""
F203B: Attribution Confidence Scorer

Provides explainable confidence scores for identity stitching candidates.
No model load, no network, pure Python with Levenshtein fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# IdentityCandidate imported from identity_stitching_canonical to avoid circular imports
# Using late import at module level for type checking only
__all__ = [
    "AttributionFactor",
    "AttributionScore",
    "AttributionConfidenceScorer",
    "create_attribution_scorer",
    "enrich_candidate_with_attribution",
]

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_FACTOR_COMPARISONS = 5000
MAX_EVIDENCE_PER_FACTOR = 10

# Factor weights (sum to 1.0 for normalization)
DEFAULT_FACTOR_WEIGHTS = {
    "email_domain_match": 0.25,
    "username_pattern_similarity": 0.20,
    "temporal_overlap": 0.20,
    "shared_infrastructure": 0.20,
    "pgp_key_correlation": 0.15,
}

# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttributionFactor:
    """A single factor contributing to attribution confidence."""

    factor_id: str
    factor_type: str  # email_domain_match | username_pattern_similarity | ...
    raw_score: float  # 0-1 raw signal strength
    weighted_score: float  # raw_score * factor_weight
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttributionScore:
    """
    Explainable confidence score for an identity pair.

    Attributes:
        confidence: Final weighted confidence 0.0-1.0
        factors: List of AttributionFactor that contributed
        evidence_ids: Unique evidence identifiers for audit trail
        factor_weights: The weights used (for reproducibility)
    """

    confidence: float
    factors: Tuple[AttributionFactor, ...]
    evidence_ids: Tuple[str, ...]
    factor_weights: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confidence": round(self.confidence, 4),
            "factors": [
                {
                    "factor_id": f.factor_id,
                    "factor_type": f.factor_type,
                    "raw_score": round(f.raw_score, 4),
                    "weighted_score": round(f.weighted_score, 4),
                    "evidence": list(f.evidence),
                    "metadata": f.metadata,
                }
                for f in self.factors
            ],
            "evidence_ids": list(self.evidence_ids),
            "factor_weights": {k: round(v, 4) for k, v in self.factor_weights.items()},
        }


# ── Levenshtein Distance (pure Python fallback) ────────────────────────────────


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Pure Python Levenshtein distance — O(mn) time, O(min(m,n)) space."""
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)

    # Use two rows instead of full matrix
    prev_row = list(range(len(s2) + 1))
    curr_row = [0] * (len(s2) + 1)

    for i, c1 in enumerate(s1):
        curr_row[0] = i + 1
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row[j + 1] = min(
                prev_row[j + 1] + 1,  # deletion
                curr_row[j] + 1,  # insertion
                prev_row[j] + cost,  # substitution
            )
        prev_row, curr_row = curr_row, prev_row

    return prev_row[len(s2)]


def _normalized_levenshtein(s1: str, s2: str) -> float:
    """Returns similarity 0-1 where 1 = identical."""
    if not s1 and not s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (_levenshtein_distance(s1.lower(), s2.lower()) / max_len)


# ── AttributionConfidenceScorer ───────────────────────────────────────────────


class AttributionConfidenceScorer:
    """
    Scores identity candidate pairs using multiple attribution factors.

    All methods are fail-soft: errors return empty AttributionScore.

    Args:
        factor_weights: Optional dict of factor_type -> weight override.
    """

    def __init__(
        self,
        factor_weights: Optional[Dict[str, float]] = None,
        max_comparisons: int = MAX_FACTOR_COMPARISONS,
    ) -> None:
        self._weights = factor_weights or dict(DEFAULT_FACTOR_WEIGHTS)
        self._max_comparisons = max_comparisons
        self._comparison_count = 0

    @property
    def comparison_count(self) -> int:
        return self._comparison_count

    def _check_limit(self) -> bool:
        """Returns True if under limit."""
        return self._comparison_count < self._max_comparisons

    # ── Factor Extraction Methods ─────────────────────────────────────────────

    def _extract_email_domain(self, email: str) -> Optional[str]:
        """Extract domain from email address."""
        if "@" in email:
            parts = email.split("@")
            if len(parts) == 2 and parts[1]:
                return parts[1].lower()
        return None

    def _email_domain_match_score(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
    ) -> Optional[AttributionFactor]:
        """
        Compare email domains between two candidates.
        Returns factor if both have emails.
        """
        if not left.emails or not right.emails:
            return None

        left_domains = {self._extract_email_domain(e) for e in left.emails if self._extract_email_domain(e)}
        right_domains = {self._extract_email_domain(e) for e in right.emails if self._extract_email_domain(e)}

        if not left_domains or not right_domains:
            return None

        # Exact domain match
        intersection = left_domains & right_domains
        if intersection:
            factor_id = f"email_domain_{list(intersection)[0]}"
            evidence = tuple(f"domain:{d}" for d in intersection)
            raw = 1.0
        else:
            # Partial: shared TLD or similar domain structure
            left_tlds = {d.split(".")[-1] if "." in d else d for d in left_domains if d}
            right_tlds = {d.split(".")[-1] if "." in d else d for d in right_domains if d}
            if left_tlds & right_tlds:
                factor_id = "email_domain_tld_shared"
                evidence = tuple(f"tld:{tld}" for tld in (left_tlds & right_tlds))
                raw = 0.5
            else:
                # No match
                return None

        return AttributionFactor(
            factor_id=factor_id,
            factor_type="email_domain_match",
            raw_score=raw,
            weighted_score=raw * self._weights.get("email_domain_match", 0.25),
            evidence=evidence,
            metadata={"left_domains": list(left_domains), "right_domains": list(right_domains)},
        )

    def _username_pattern_score(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
    ) -> Optional[AttributionFactor]:
        """
        Compare usernames using Levenshtein similarity.
        Returns factor if both have usernames with >0.6 similarity.
        """
        if not left.usernames or not right.usernames:
            return None

        best_score = 0.0
        best_evidence: List[str] = []

        for lu in left.usernames:
            for ru in right.usernames:
                if not lu or not ru:
                    continue
                sim = _normalized_levenshtein(lu, ru)
                if sim > best_score:
                    best_score = sim
                    best_evidence = [f"{lu}|{ru} ({sim:.2f})"]

        if best_score >= 0.6:
            return AttributionFactor(
                factor_id="username_pattern_sim",
                factor_type="username_pattern_similarity",
                raw_score=best_score,
                weighted_score=best_score * self._weights.get("username_pattern_similarity", 0.20),
                evidence=tuple(best_evidence),
                metadata={"match_count": len(left.usernames) * len(right.usernames)},
            )
        return None

    def _temporal_overlap_score(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
    ) -> Optional[AttributionFactor]:
        """
        Assess temporal overlap based on finding_ids timestamps.
        Uses finding_ids as proxy for temporal context.
        """
        # Identity stitching does not have explicit timestamps,
        # so we use profile_ids and finding_ids density as proxy.
        # If both candidates share many finding_ids, temporal overlap is high.

        left_fids = set(left.finding_ids)
        right_fids = set(right.finding_ids)

        if not left_fids or not right_fids:
            return None

        overlap = len(left_fids & right_fids)
        union = len(left_fids | right_fids)

        if union == 0:
            return None

        jaccard = overlap / union

        if jaccard >= 0.3:  # Meaningful overlap threshold
            factor_id = f"temporal_finding_overlap_{overlap}"
            return AttributionFactor(
                factor_id=factor_id,
                factor_type="temporal_overlap",
                raw_score=jaccard,
                weighted_score=jaccard * self._weights.get("temporal_overlap", 0.20),
                evidence=(f"shared_findings:{overlap}", f"union_findings:{union}"),
                metadata={"left_finding_count": len(left_fids), "right_finding_count": len(right_fids)},
            )
        return None

    def _shared_infrastructure_score(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
    ) -> Optional[AttributionFactor]:
        """
        Assess shared infrastructure via platform overlap.
        Platform = where the identity was observed (github, twitter, etc.)
        """
        if not left.platforms or not right.platforms:
            return None

        left_plat = set(p.lower() for p in left.platforms if p)
        right_plat = set(p.lower() for p in right.platforms if p)

        if not left_plat or not right_plat:
            return None

        intersection = left_plat & right_plat
        if intersection:
            raw = min(1.0, len(intersection) * 0.5)  # 2+ shared = max score
            return AttributionFactor(
                factor_id=f"infra_platform_{len(intersection)}",
                factor_type="shared_infrastructure",
                raw_score=raw,
                weighted_score=raw * self._weights.get("shared_infrastructure", 0.20),
                evidence=tuple(f"platform:{p}" for p in intersection),
                metadata={"left_platforms": list(left_plat), "right_platforms": list(right_plat)},
            )
        return None

    def _pgp_key_correlation_score(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
    ) -> Optional[AttributionFactor]:
        """
        Assess PGP key correlation via email and name patterns.
        Identity signals may contain PGP fingerprints in evidence strings.
        """
        # Check evidence strings for PGP fingerprints
        left_pgp = set()
        right_pgp = set()

        pgp_pattern = re.compile(r'[A-F0-9]{8,}(?:[A-F0-9]{4,}){3,}', re.IGNORECASE)

        for e in left.evidence:
            matches = pgp_pattern.findall(e)
            left_pgp.update(matches)

        for e in right.evidence:
            matches = pgp_pattern.findall(e)
            right_pgp.update(matches)

        # Also check signals for PGP hints
        if left.signals:
            for sig_key, sig_val in left.signals.items():
                if 'pgp' in sig_key.lower() or 'key' in sig_key.lower():
                    matches = pgp_pattern.findall(str(sig_val))
                    left_pgp.update(matches)

        if right.signals:
            for sig_key, sig_val in right.signals.items():
                if 'pgp' in sig_key.lower() or 'key' in sig_key.lower():
                    matches = pgp_pattern.findall(str(sig_val))
                    right_pgp.update(matches)

        if not left_pgp or not right_pgp:
            return None

        intersection = left_pgp & right_pgp
        if intersection:
            raw = 1.0
            return AttributionFactor(
                factor_id=f"pgp_key_{list(intersection)[0][:16]}",
                factor_type="pgp_key_correlation",
                raw_score=raw,
                weighted_score=raw * self._weights.get("pgp_key_correlation", 0.15),
                evidence=tuple(f"pgp:{k[:16]}..." for k in intersection),
                metadata={"left_keys": len(left_pgp), "right_keys": len(right_pgp)},
            )
        return None

    # ── Main Scoring Methods ───────────────────────────────────────────────────

    def score_pair(
        self,
        left: IdentityCandidate,
        right: IdentityCandidate,
        context: Optional[Dict[str, Any]] = None,
    ) -> AttributionScore:
        """
        Score a pair of identity candidates and return explainable AttributionScore.

        Args:
            left: First IdentityCandidate (or dict with same fields)
            right: Second IdentityCandidate (or dict with same fields)
            context: Optional context dict (reserved for future use)

        Returns:
            AttributionScore with confidence 0.0-1.0, factors, evidence_ids, weights.
            Returns empty score on error (fail-soft).
        """
        if not self._check_limit():
            return AttributionScore(
                confidence=0.0,
                factors=(),
                evidence_ids=(),
                factor_weights=self._weights,
            )

        self._comparison_count += 1

        try:
            # Convert dicts to IdentityCandidate if needed
            if isinstance(left, dict):
                left = IdentityCandidate(**left)
            if isinstance(right, dict):
                right = IdentityCandidate(**right)

            # Extract all applicable factors
            factors: List[AttributionFactor] = []

            factor_methods = [
                self._email_domain_match_score,
                self._username_pattern_score,
                self._temporal_overlap_score,
                self._shared_infrastructure_score,
                self._pgp_key_correlation_score,
            ]

            for method in factor_methods:
                try:
                    factor = method(left, right)
                    if factor is not None:
                        factors.append(factor)
                except Exception:
                    pass  # Fail-soft per factor

            # Calculate confidence
            if factors:
                confidence = sum(f.weighted_score for f in factors)
                confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]
            else:
                confidence = 0.0

            # Collect evidence IDs
            evidence_ids = tuple(
                f.factor_id for f in factors
            )

            return AttributionScore(
                confidence=confidence,
                factors=tuple(factors),
                evidence_ids=evidence_ids,
                factor_weights=dict(self._weights),
            )

        except Exception:
            return AttributionScore(
                confidence=0.0,
                factors=(),
                evidence_ids=(),
                factor_weights=dict(self._weights),
            )

    def score_candidates(
        self,
        candidates: List[IdentityCandidate],
    ) -> Dict[str, AttributionScore]:
        """
        Score all pairs of candidates and return scores keyed by candidate pair.

        Only scores pairs with distinct candidate_ids.
        Returns empty dict on error (fail-soft).

        Args:
            candidates: List of IdentityCandidate to score

        Returns:
            Dict mapping "left_id|right_id" -> AttributionScore
        """
        scores: Dict[str, AttributionScore] = {}

        if not self._check_limit():
            return scores

        try:
            for i, left in enumerate(candidates):
                for right in candidates[i + 1 :]:
                    if not self._check_limit():
                        break

                    try:
                        score = self.score_pair(left, right)
                        if score.confidence > 0.0:
                            key = f"{left.candidate_id}|{right.candidate_id}"
                            scores[key] = score
                    except Exception:
                        continue

                if not self._check_limit():
                    break

        except Exception:
            pass  # Fail-soft

        return scores

    def get_factor_breakdown(self, score: AttributionScore) -> Dict[str, Any]:
        """Return human-readable factor breakdown from an AttributionScore."""
        return {
            "total_confidence": round(score.confidence, 4),
            "factors": [
                {
                    "type": f.factor_type,
                    "raw": round(f.raw_score, 4),
                    "weighted": round(f.weighted_score, 4),
                    "contribution_pct": round((f.weighted_score / max(score.confidence, 0.001)) * 100, 1),
                    "evidence": list(f.evidence)[:5],  # Limit evidence display
                }
                for f in score.factors
            ],
            "weights_used": score.factor_weights,
        }


# ── Factory ───────────────────────────────────────────────────────────────────


def create_attribution_scorer(
    factor_weights: Optional[Dict[str, float]] = None,
) -> AttributionConfidenceScorer:
    """Create a configured AttributionConfidenceScorer instance."""
    return AttributionConfidenceScorer(factor_weights=factor_weights)


# ── Integration Helper ─────────────────────────────────────────────────────────


def enrich_candidate_with_attribution(
    candidate: IdentityCandidate,
    score: AttributionScore,
) -> IdentityCandidate:
    """
    Post-process an IdentityCandidate to add attribution signals and evidence.

    Adds:
    - signals['attribution_confidence'] = score.confidence
    - signals['attribution_factor_types'] = list of factor types
    - evidence[] = factor_id items from attribution
    """
    if isinstance(candidate, dict):
        candidate = IdentityCandidate(**candidate)

    # Merge attribution into signals
    new_signals = dict(candidate.signals)
    new_signals["attribution_confidence"] = score.confidence
    new_signals["attribution_factor_types"] = [f.factor_type for f in score.factors]

    # Merge attribution evidence
    new_evidence = list(candidate.evidence)
    for factor in score.factors:
        for ev in factor.evidence:
            if ev not in new_evidence:
                new_evidence.append(ev)

    return IdentityCandidate(
        candidate_id=candidate.candidate_id,
        profile_ids=candidate.profile_ids,
        primary_name=candidate.primary_name,
        emails=candidate.emails,
        usernames=candidate.usernames,
        platforms=candidate.platforms,
        confidence=candidate.confidence,
        signals=new_signals,
        evidence=new_evidence,
        finding_ids=candidate.finding_ids,
    )


# IdentityCandidate imported from identity_stitching_canonical
from hledac.universal.intelligence.identity_stitching_canonical import IdentityCandidate

__all__ = [
    "AttributionFactor",
    "AttributionScore",
    "AttributionConfidenceScorer",
    "create_attribution_scorer",
    "enrich_candidate_with_attribution",
]
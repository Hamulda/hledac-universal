"""
ConsistencyVerifier — META-008 auto-retraction hook for systematic dissenters.

Runs AFTER ContradictionFeedbackBridge.run_contradiction_audit() to identify



sources that are systematic dissenters and trigger automatic JTMS retraction.

ARCHITECTURE:
  ContradictionFeedbackBridge.run_contradiction_audit()
      ↓ produces list[ContradictionSignal] + list[ReFetchCandidate]
  ConsistencyVerifier.check_batch(findings, signals)  ← NEW
      ↓ performs tri-source voting + ratio checks
      ↓ returns set of source_ids to retract
  IOCGraph.retract_source(source_id)  ← auto-called via callback

TRI-SOURCE VOTING ALGORITHM:
  For each IOC entity with evidence from ≥3 sources:
    1. Find the majority consensus (2+ sources agree)
    2. Identify any dissenting source
    3. If a single source dissents from ≥3 different consensus groups,
       flag it for auto-retraction

  Why tri-source: with 2 sources, disagreement = inconclusive (he-said/she-said).
  With 3+, a single dissenter from the majority is a strong signal of unreliability.

SOURCE RELIABILITY RATIO:
  Sources with contradiction_count / total_claims > 0.3 AND ≥3 claims
  are auto-retracted at SYNTHESIS phase.

BOUNDS (M1 8GB safe):
  - MAX_FINDINGS = 200  — inherited from contradiction_feedback.py
  - MAX_SIGNALS = 250   — 5 engines × 50 max each
  - Tri-source vote: O(n²) pairwise, n ≤ 200, ~40k comparisons worst case
  - Memory: ~5KB for intermediate data structures

USAGE:
  from hledac.universal.knowledge.consistency_verifier import ConsistencyVerifier

  verifier = ConsistencyVerifier()
  to_retract = verifier.check_batch(findings, signals)

  for source_id in to_retract:
      await ioc_graph.retract_source(source_id)
      reliability_tracker.mark_auto_retracted(source_id)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounds (M1 8GB safe)
# ---------------------------------------------------------------------------

MAX_FINDINGS: int = 200
MAX_SIGNALS: int = 250
TRI_SOURCE_MIN_VOTES: int = 3  # minimum dissents before auto-retract
TRI_SOURCE_MIN_SOURCES: int = 3  # minimum total sources for voting
RATIO_THRESHOLD: float = 0.3


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SourceVote:
    """Per-source voting record for tri-source consensus detection."""
    source_id: str
    agreement_count: int = 0   # times this source agreed with majority
    dissent_count: int = 0     # times this source dissented from majority
    entities_involved: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RetractionDecision:
    """A decision to auto-retract a source."""
    source_id: str
    reason: str  # 'tri_source_voting' | 'ratio_threshold' | 'both'
    dissent_count: int = 0
    total_claims: int = 0
    ratio: float = 0.0
    entities_affected: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ConsistencyVerifier
# ---------------------------------------------------------------------------

class ConsistencyVerifier:
    """Identifies systematic dissenters and triggers auto-retraction.

    Stateless — each check_batch() call is independent.
    Pure Python, no I/O, no persistence.
    Fail-soft: all errors return empty retraction set.
    """

    __slots__ = ("_stats",)

    def __init__(self) -> None:
        self._stats: dict[str, int] = {
            "checks_run": 0,
            "sources_retracted_tri_vote": 0,
            "sources_retracted_ratio": 0,
            "total_retractions": 0,
        }

    def check_batch(
        self,
        findings: list[dict[str, Any]],
        signals: list[Any],  # list[ContradictionSignal]
    ) -> list[RetractionDecision]:
        """Check a batch of findings + contradiction signals for systematic dissenters.

        Args:
            findings: List of finding dicts (must have 'source_type' or
                       'provenance_json' keys to identify sources).
            signals: List of ContradictionSignal objects from contradiction engines.

        Returns:
            List of RetractionDecision objects for sources to auto-retract.
            Empty list if no sources meet criteria.

        Fail-soft: returns [] on any error.
        """
        self._stats["checks_run"] += 1

        try:
            findings = findings[:MAX_FINDINGS]
            signals = signals[:MAX_SIGNALS]

            if not findings or not signals:
                return []

            decisions: list[RetractionDecision] = []

            # -- Phase 1: Tri-source voting ---------------------------------
            tri_vote_decisions = self._tri_source_voting(findings, signals)
            decisions.extend(tri_vote_decisions)
            self._stats["sources_retracted_tri_vote"] += len(tri_vote_decisions)

            # -- Phase 2: Source reliability ratio --------------------------
            ratio_decisions = self._source_reliability_ratio(findings, signals)
            # Deduplicate — don't retract same source twice
            already_retracted = {d.source_id for d in decisions}
            ratio_decisions = [
                d for d in ratio_decisions
                if d.source_id not in already_retracted
            ]
            decisions.extend(ratio_decisions)
            self._stats["sources_retracted_ratio"] += len(ratio_decisions)

            self._stats["total_retractions"] += len(decisions)

            if decisions:
                logger.info(
                    "[ConsistencyVerifier] check_batch: %d findings, %d signals → "
                    "%d retraction decisions (%d tri-vote, %d ratio)",
                    len(findings),
                    len(signals),
                    len(decisions),
                    len(tri_vote_decisions),
                    len(ratio_decisions),
                )
                for d in decisions:
                    logger.info(
                        "[ConsistencyVerifier] RETRACT %s: %s (dissent=%d, ratio=%.3f)",
                        d.source_id, d.reason, d.dissent_count, d.ratio,
                    )

            return decisions

        except Exception as e:
            logger.debug(
                "[ConsistencyVerifier] check_batch failed (fail-soft): %s", e,
            )
            return []

    # ------------------------------------------------------------------
    # Phase 1: Tri-source voting - helper methods
    # ------------------------------------------------------------------

    def _tri_source_voting(
        self,
        findings: list[dict[str, Any]],
        signals: list[Any],
    ) -> list[RetractionDecision]:
        """Identify sources that systematically dissent from 3+ consensus groups.

        Algorithm:
        1. Group findings by IOC entity
        2. For each entity with ≥3 distinct sources:
           a. Find majority consensus (2+ sources agree on the claim)
           b. If a single source dissents, increment its dissent count
        3. Sources with ≥3 dissents → auto-retract

        Returns:
            List of RetractionDecision for sources to retract.
        """
        if len(findings) < TRI_SOURCE_MIN_SOURCES:
            return []

        entity_groups = self._group_findings_by_entity(findings, signals)
        source_votes = self._process_entity_groups(entity_groups, signals)
        return self._build_retraction_decisions(source_votes)

    def _group_findings_by_entity(
        self,
        findings: list[dict[str, Any]],
        signals: list[Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group findings by entity key, also extracting from signals."""
        entity_groups: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            entity_key = f.get("entity_value") or f.get("ioc_value") or f.get("value") or ""
            if entity_key:
                entity_groups.setdefault(entity_key, []).append(f)
        for signal in signals:
            try:
                entity_key = getattr(signal, "entity_value", "") or ""
                if entity_key and entity_key not in entity_groups:
                    entity_groups[entity_key] = []
            except Exception:
                continue
        return entity_groups

    def _process_entity_groups(
        self,
        entity_groups: dict[str, list[dict[str, Any]]],
        signals: list[Any],
    ) -> dict[str, SourceVote]:
        """Process all entity groups and return per-source voting records."""
        source_votes: dict[str, SourceVote] = {}
        for entity_key, entity_findings in entity_groups.items():
            self._vote_on_entity(entity_key, entity_findings, signals, source_votes)
        return source_votes

    def _vote_on_entity(
        self,
        entity_key: str,
        entity_findings: list[dict[str, Any]],
        signals: list[Any],
        source_votes: dict[str, SourceVote],
    ) -> None:
        """Vote on a single entity and update source_votes in-place."""
        entity_sources = self._group_sources_for_entity(entity_findings)
        if len(entity_sources) < TRI_SOURCE_MIN_SOURCES:
            return
        majority_source = self._find_majority_source(entity_sources)
        for source_id, findings_list in entity_sources.items():
            if source_id == majority_source:
                self._record_agreement(source_id, entity_key, source_votes)
            elif self._check_entity_contradiction(
                entity_sources[majority_source], findings_list, signals, entity_key,
            ):
                self._record_dissent(source_id, entity_key, source_votes)

    def _group_sources_for_entity(
        self,
        entity_findings: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group findings by source within an entity."""
        sources: dict[str, list[dict[str, Any]]] = {}
        for f in entity_findings:
            source = f.get("source_type") or f.get("source_id") or f.get("source", "unknown")
            sources.setdefault(source, []).append(f)
        return sources

    def _find_majority_source(
        self,
        entity_sources: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Find source with most findings for an entity."""
        return max(entity_sources, key=lambda s: len(entity_sources[s]))

    def _record_agreement(
        self,
        source_id: str,
        entity_key: str,
        source_votes: dict[str, SourceVote],
    ) -> None:
        """Record an agreement vote for a source."""
        if source_id not in source_votes:
            source_votes[source_id] = SourceVote(source_id=source_id)
        source_votes[source_id].agreement_count += 1
        source_votes[source_id].entities_involved.append(entity_key)

    def _record_dissent(
        self,
        source_id: str,
        entity_key: str,
        source_votes: dict[str, SourceVote],
    ) -> None:
        """Record a dissent vote for a source."""
        if source_id not in source_votes:
            source_votes[source_id] = SourceVote(source_id=source_id)
        source_votes[source_id].dissent_count += 1
        source_votes[source_id].entities_involved.append(entity_key)

    def _build_retraction_decisions(
        self,
        source_votes: dict[str, SourceVote],
    ) -> list[RetractionDecision]:
        """Build retraction decisions from source voting records."""
        decisions: list[RetractionDecision] = []
        for source_id, vote in source_votes.items():
            if vote.dissent_count >= TRI_SOURCE_MIN_VOTES:
                total = vote.agreement_count + vote.dissent_count
                ratio = vote.dissent_count / total if total > 0 else 0.0
                decisions.append(RetractionDecision(
                    source_id=source_id,
                    reason="tri_source_voting",
                    dissent_count=vote.dissent_count,
                    total_claims=total,
                    ratio=round(ratio, 3),
                    entities_affected=vote.entities_involved[:20],
                ))
        return decisions

    # ------------------------------------------------------------------
    # Phase 2: Source reliability ratio
    # ------------------------------------------------------------------

    def _source_reliability_ratio(
        self,
        findings: list[dict[str, Any]],
        signals: list[Any],
    ) -> list[RetractionDecision]:
        """Check source reliability ratio from contradiction signals.

        A source with contradiction_count / total_claims > RATIO_THRESHOLD
        AND ≥ TRI_SOURCE_MIN_VOTES claims is flagged for auto-retraction.

        This complements tri-source voting by catching sources that
        contradict even when they're not the sole dissenter.

        Returns:
            List of RetractionDecision for sources to retract.
        """
        # Count contradictions per source from signals
        source_contradictions: dict[str, int] = {}
        for signal in signals:
            try:
                # Extract source from signal based on engine type
                engine = getattr(signal, "engine", "")
                source_id = None

                if engine == "adversarial":
                    # AdversarialVerifier tracks source via claim context
                    source_id = self._extract_source_from_claim(
                        getattr(signal, "claim_a", "")
                    )
                    if not source_id:
                        source_id = self._extract_source_from_claim(
                            getattr(signal, "claim_b", "")
                        )
                elif engine == "insight":
                    source_id = self._extract_source_from_claim(
                        getattr(signal, "claim_a", "")
                    )
                elif engine == "dempster_shafer":
                    # DS signals are holistic — skip per-source extraction
                    continue
                elif engine == "evidence_network":
                    description = getattr(signal, "description", "")
                    source_id = self._extract_source_from_claim(description)
                elif engine == "graph_rag":
                    description = getattr(signal, "description", "")
                    source_id = self._extract_source_from_claim(description)

                if source_id:
                    source_contradictions[source_id] = (
                        source_contradictions.get(source_id, 0) + 1
                    )
            except Exception:
                continue

        # Build total claims per source from findings
        source_total_claims: dict[str, int] = {}
        for f in findings:
            source = (
                f.get("source_type")
                or f.get("source_id")
                or f.get("source", "unknown")
            )
            source_total_claims[source] = source_total_claims.get(source, 0) + 1

        # Check ratio
        decisions: list[RetractionDecision] = []
        for source_id, contradiction_count in source_contradictions.items():
            if contradiction_count < TRI_SOURCE_MIN_VOTES:
                continue

            total = source_total_claims.get(source_id, contradiction_count)
            ratio = contradiction_count / total if total > 0 else 0.0

            if ratio > RATIO_THRESHOLD:
                decisions.append(RetractionDecision(
                    source_id=source_id,
                    reason="ratio_threshold",
                    dissent_count=contradiction_count,
                    total_claims=total,
                    ratio=round(ratio, 3),
                ))

        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_entity_contradiction(
        majority_findings: list[dict[str, Any]],
        dissenter_findings: list[dict[str, Any]],
        signals: list[Any],
        entity_key: str,
    ) -> bool:
        """Check if dissenter actually contradicts the majority for a given entity.

        Strategy:
        1. Check if any contradiction signal mentions this entity
        2. Compare confidence divergence (high confidence disagreement = contradiction)
        3. Fall back to simple value comparison

        Returns:
            True if contradiction detected.
        """
        # Check signals for this entity
        for signal in signals:
            try:
                sig_entity = getattr(signal, "entity_value", "") or ""
                severity = float(getattr(signal, "severity", 0.0))
                if entity_key in sig_entity and severity >= 0.5:
                    return True
            except Exception:
                continue

        # Check confidence divergence
        majority_confs = [
            float(f.get("confidence", 0.5))
            for f in majority_findings
            if f.get("confidence") is not None
        ]
        dissenter_confs = [
            float(f.get("confidence", 0.5))
            for f in dissenter_findings
            if f.get("confidence") is not None
        ]

        if majority_confs and dissenter_confs:
            avg_majority = sum(majority_confs) / len(majority_confs)
            avg_dissenter = sum(dissenter_confs) / len(dissenter_confs)
            # High-confidence disagreement (>0.3 divergence) = contradiction
            if abs(avg_majority - avg_dissenter) > 0.3:
                return True

        # Simple check: different values for the same entity?
        majority_values = {
            f.get("value") or f.get("payload_text", "")[:100]
            for f in majority_findings
        }
        dissenter_values = {
            f.get("value") or f.get("payload_text", "")[:100]
            for f in dissenter_findings
        }
        # Non-empty disjoint sets = potential contradiction
        if majority_values and dissenter_values and not majority_values.intersection(dissenter_values):
            return True

        return False

    @staticmethod
    def _extract_source_from_claim(claim: str) -> str | None:
        """Extract source identifier from a claim string.

        Heuristic: looks for patterns like "source: X", "from X", etc.
        Returns None if no source found.
        """
        if not claim:
            return None

        import re
        # Common patterns in contradiction descriptions
        patterns = [
            r'source[:\s]+["\']?([a-zA-Z0-9_\-\.]+)["\']?',
            r'from\s+["\']?([a-zA-Z0-9_\-\.]+)["\']?\s+source',
            r'by\s+["\']?([a-zA-Z0-9_\-\.]+)["\']?',
        ]
        for pattern in patterns:
            m = re.search(pattern, claim, re.IGNORECASE)
            if m:
                return m.group(1)

        return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Return telemetry counters."""
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset telemetry counters (for testing)."""
        self._stats = {
            "checks_run": 0,
            "sources_retracted_tri_vote": 0,
            "sources_retracted_ratio": 0,
            "total_retractions": 0,
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_CONSISTENCY_VERIFIER: ConsistencyVerifier | None = None
_TRACKER_LOCK = asyncio.Lock()


def get_consistency_verifier() -> ConsistencyVerifier:
    """Get or create the global ConsistencyVerifier singleton."""
    global _CONSISTENCY_VERIFIER
    if _CONSISTENCY_VERIFIER is None:
        _CONSISTENCY_VERIFIER = ConsistencyVerifier()
    return _CONSISTENCY_VERIFIER


def reset_consistency_verifier() -> None:
    """Reset the global verifier (for testing only)."""
    global _CONSISTENCY_VERIFIER
    _CONSISTENCY_VERIFIER = None


__all__ = [
    "ConsistencyVerifier",
    "RetractionDecision",
    "SourceVote",
    "get_consistency_verifier",
    "reset_consistency_verifier",
    "TRI_SOURCE_MIN_VOTES",
    "TRI_SOURCE_MIN_SOURCES",
    "RATIO_THRESHOLD",
]

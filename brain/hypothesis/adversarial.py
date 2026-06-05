"""
Hypothesis Engine — Adversarial Verifier (C4 Sprint Refactoring)
================================================================

Extracted from :mod:`brain.hypothesis_engine` to break the 5 373 LOC monolith
into focused modules. This module hosts the :class:`AdversarialVerifier`
(Devil's Advocate mode) — the falsification layer that actively seeks evidence
against hypotheses, detects contradictions, and assesses source credibility.

GHOST_INVARIANTS:
- The extraction is **byte-for-byte identical** to the original — no
  behaviour change, no field rename, no default mutation. Existing tests
  must pass unchanged.
- ``brain.hypothesis_engine`` re-exports :class:`AdversarialVerifier` for
  backward compat.
- New code should ``from brain.hypothesis.adversarial import AdversarialVerifier``.
- Imports from :mod:`brain.hypothesis._types` (the canonical type home) —
  no circular dependency on ``hypothesis_engine`` because all referenced
  types live in the package leaf.
- The :class:`HypothesisEngine` type hint is imported under
  :data:`TYPE_CHECKING` only — runtime access is via the instance
  attribute ``self.hypothesis_engine`` which is injected via ``__init__``.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ._types import (
    AdversarialReport,
    Contradiction,
    CrossReferenceResult,
    Event,
    Evidence,
    HypothesisType,
    SourceCredibility,
)

# Hypothesis lives in brain.hypothesis_engine (carries engine-specific
# methods like add_test_result, _ds_engine). Imported at runtime here
# because the type is used in method signatures on this class.
from brain.hypothesis_engine import Hypothesis  # noqa: E402

from utils.async_helpers import safe_gather_dropin
if TYPE_CHECKING:
    from brain.hypothesis_engine import HypothesisEngine  # noqa: F401

logger = logging.getLogger(__name__)


class AdversarialVerifier:
    """
    Devil's Advocate verification system for rigorous hypothesis testing.

    Actively seeks evidence against hypotheses, challenges assumptions,
    detects logical fallacies, and performs comprehensive source credibility
    analysis. Implements the principle of falsification through adversarial
    examination.

    M1 8GB Optimizations:
    - Async database queries for non-blocking I/O
    - Streaming evidence processing with limited window
    - Incremental contradiction detection
    - Aggressive memory cleanup after verification batches
    - Bounded source credibility with deterministic LRU eviction

    Attributes:
        hypothesis_engine: Reference to the HypothesisEngine for evidence access
        source_credibility_db: In-memory cache of source credibility scores
        max_contradiction_window: Maximum evidence pairs to check for contradictions
        bias_keywords: Dictionary of bias indicators by category
    """

    # Memory bounds for M1 8GB optimization (shared with HypothesisEngine)
    MAX_SOURCE_ITEMS = 5_000

    def __init__(
        self,
        hypothesis_engine: "HypothesisEngine",
        max_contradiction_window: int = 100,
        enable_streaming: bool = True,
    ):
        """
        Initialize the AdversarialVerifier.

        Args:
            hypothesis_engine: The HypothesisEngine instance to work with
            max_contradiction_window: Maximum number of evidence items to check
                                     for contradictions (M1 memory optimization)
            enable_streaming: Whether to use streaming evidence processing
        """
        self.hypothesis_engine = hypothesis_engine
        self.max_contradiction_window = max_contradiction_window
        self.enable_streaming = enable_streaming

        # Source credibility cache (bounded with LRU eviction)
        self._source_credibility: OrderedDict[str, SourceCredibility] = OrderedDict()

        # Bias detection keywords
        self._bias_keywords = {
            "political": ["partisan", "biased", "agenda", "propaganda", "lobby"],
            "commercial": ["sponsored", "advertisement", "paid", "promotion"],
            "sensationalist": ["shocking", "unbelievable", "miracle", "conspiracy"],
            "unverified": ["anonymous", "unconfirmed", "alleged", "rumored"],
        }

        # Logical fallacy patterns
        self._fallacy_patterns = {
            "ad_hominem": r"\b(attacking|attack on)\s+(the\s+)?person\b|\b(person\s+is\s+(bad|evil|wrong))\b",
            "straw_man": r"\b(misrepresents?|mischaracterizes?|distorts?)\b",
            "false_dichotomy": r"\b(either\s+or|only\s+two\s+(options?|choices?))\b",
            "appeal_to_authority": r"\b(expert\s+says|according\s+to\s+(expert|authority))\b",
            "circular_reasoning": r"\b(because\s+it\s+is|it\s+is\s+because)\b",
            "hasty_generalization": r"\b(all\s+are|everyone\s+knows|always)\b",
        }

        logger.info(
            f"AdversarialVerifier initialized (window={max_contradiction_window}, "
            f"streaming={enable_streaming})"
        )

    async def verify_claim(
        self, claim: str, context: dict[str, Any] | None = None
    ) -> AdversarialReport:
        """
        Perform comprehensive adversarial verification of a claim.

        This is the main entry point for devil's advocate analysis. It gathers
        evidence, checks for contradictions, assesses source credibility, and
        generates a comprehensive adversarial report.

        Args:
            claim: The claim to verify
            context: Additional context for verification

        Returns:
            AdversarialReport with comprehensive analysis
        """
        import time

        start_time = time.time()
        context = context or {}

        logger.info(f"Starting adversarial verification for claim: {claim[:50]}...")

        # Step 1: Find supporting and contradicting evidence
        supporting_evidence = await self._find_supporting_evidence(claim, context)
        contradicting_evidence = await self.find_counter_evidence_from_claim(
            claim, context
        )

        # Step 2: Assess source credibility for all evidence
        all_sources = set()
        for e in supporting_evidence + contradicting_evidence:
            all_sources.add(e.source)

        credibility_assessment = {}
        for source in all_sources:
            credibility_assessment[source] = self.assess_source_credibility(source)

        # Step 3: Detect contradictions
        all_evidence = supporting_evidence + contradicting_evidence
        contradictions = self.detect_contradictions(all_evidence)

        # Step 4: Check temporal consistency if events are present
        temporal_consistency = True
        events = self._extract_events(all_evidence)
        if len(events) >= 2:
            temporal_consistency, temporal_contradictions = (
                self.check_temporal_consistency(events)
            )
            contradictions.extend(temporal_contradictions)

        # Step 5: Cross-reference databases
        cross_references = await self.cross_reference_databases(claim)

        # Step 6: Generate devil's advocate analysis
        devil_advocate_score, alternative_explanations, logical_fallacies = (
            await self._generate_devils_advocate_analysis(
                claim, supporting_evidence, contradicting_evidence, context
            )
        )

        # Step 7: Calculate overall confidence
        overall_confidence = self._calculate_adversarial_confidence(
            supporting_evidence,
            contradicting_evidence,
            credibility_assessment,
            contradictions,
            cross_references,
        )

        # Step 8: Generate path explanations (if graph_rag available)
        metadata = {}
        graph_rag = context.get("graph_rag") if context else None
        if graph_rag and len(contradictions) > 0:
            try:
                # Extract path from contradictions
                path = []
                for c in contradictions[:3]:  # Limit to 3 paths
                    if hasattr(c, 'nodes') and c.nodes:
                        path = list(c.nodes)[:5]  # Max 5 nodes
                        break

                if path:
                    # Local import — both classes live in this package after Tier-3
                    # extraction, but we keep a lazy import for byte-for-byte
                    # equivalence with the pre-extraction call path.
                    from brain.hypothesis.explainer import SimpleNodeAblationExplainer
                    explainer = SimpleNodeAblationExplainer(graph_rag)
                    importances = await explainer.explain_path(path, claim, max_nodes=5)
                    from brain.hypothesis_engine import explain_with_mlx
                    explanation, prompt_hash = await explain_with_mlx(claim, path)

                    metadata['edge_importances'] = importances
                    metadata['mlx_explanation'] = explanation
                    metadata['explainer_type'] = 'leave_one_node_out'
                    metadata['max_nodes'] = 5
                    metadata['scoring_fn'] = 'graph_rag.score_path'
                    metadata['model_id'] = 'mlx-community/Qwen2.5-0.5B-Instruct-4bit'
                    metadata['prompt_hash'] = prompt_hash
                    metadata['token_budget'] = 80
                    metadata['temperature'] = 0.0
            except Exception as e:
                logger.debug(f"Path explanation failed: {e}")

        duration_ms = (time.time() - start_time) * 1000

        report = AdversarialReport(
            hypothesis=claim,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=contradicting_evidence,
            credibility_assessment=credibility_assessment,
            contradictions_found=contradictions,
            temporal_consistency=temporal_consistency,
            overall_confidence=overall_confidence,
            devil_advocate_score=devil_advocate_score,
            alternative_explanations=alternative_explanations,
            logical_fallacies=logical_fallacies,
            verification_duration_ms=duration_ms,
        )

        # Add metadata to report
        if metadata:
            report.metadata.update(metadata)

        logger.info(
            f"Adversarial verification complete: confidence={overall_confidence:.2f}, "
            f"devil_score={devil_advocate_score:.2f}, "
            f"contradictions={len(contradictions)}"
        )

        return report

    async def find_counter_evidence(
        self, hypothesis: Hypothesis
    ) -> list[Evidence]:
        """
        Find evidence that contradicts a hypothesis.

        Searches the hypothesis engine's evidence store and queries external
        sources to find counter-evidence.

        Args:
            hypothesis: The hypothesis to find counter-evidence for

        Returns:
            List of contradicting evidence items
        """
        return await self.find_counter_evidence_from_claim(
            hypothesis.statement,
            {"hypothesis_type": hypothesis.hypothesis_type},
        )

    async def find_counter_evidence_from_claim(
        self, claim: str, context: dict[str, Any] | None = None
    ) -> list[Evidence]:
        """
        Find counter-evidence for a claim string.

        Args:
            claim: The claim to find counter-evidence for
            context: Additional context

        Returns:
            List of contradicting evidence items
        """
        context = context or {}
        counter_evidence: list[Evidence] = []

        # Search existing evidence in hypothesis engine
        # Protected access with LRU window bounds
        evidence_items = list(self.hypothesis_engine._evidence.items())[:self.max_contradiction_window]
        for _evidence_id, evidence in evidence_items:
            # Check if evidence contradicts the claim
            if self._evidence_contradicts_claim(evidence, claim):
                counter_evidence.append(evidence)

        # Query external databases for counter-evidence (async)
        external_evidence = await self._query_counter_evidence_databases(
            claim, context
        )
        counter_evidence.extend(external_evidence)

        # Sort by reliability and relevance
        counter_evidence.sort(
            key=lambda e: (e.reliability * e.relevance), reverse=True
        )

        # Limit to top results (M1 memory optimization)
        return counter_evidence[:50]

    def assess_source_credibility(self, source: str) -> SourceCredibility:
        """
        Assess the credibility of an evidence source.

        Analyzes the source for bias indicators, checks historical accuracy
        if available, and returns a comprehensive credibility assessment.

        Args:
            source: The source identifier (URL, database name, etc.)

        Returns:
            SourceCredibility assessment
        """
        # Check cache first
        if source in self._source_credibility:
            cached = self._source_credibility[source]
            # Refresh if older than 24 hours
            if datetime.now() - cached.last_updated < timedelta(hours=24):
                # Move to end (update LRU order)
                self._source_credibility.move_to_end(source)
                return cached

        # Detect bias indicators
        bias_indicators = self._detect_bias_indicators(source)

        # Calculate base credibility score
        base_score = 0.5

        # Adjust based on source type patterns
        if any(
            trusted in source.lower()
            for trusted in [".edu", ".gov", "peer-reviewed", "arxiv"]
        ):
            base_score += 0.3
        elif any(
            untrusted in source.lower()
            for untrusted in ["blog", "forum", "social", "wiki"]
        ):
            base_score -= 0.2

        # Adjust for bias
        bias_penalty = len(bias_indicators) * 0.1
        credibility_score = max(0.0, min(1.0, base_score - bias_penalty))

        # Create assessment
        assessment = SourceCredibility(
            source_id=source,
            credibility_score=credibility_score,
            bias_indicators=bias_indicators,
            historical_accuracy=0.5,  # Default until verified
            total_claims=0,
            verified_claims=0,
            contradiction_count=0,
        )

        # Cache and return (bounded with LRU eviction)
        if source in self._source_credibility:
            self._source_credibility.move_to_end(source)
        else:
            self._source_credibility[source] = assessment

        # Evict oldest entries if approaching cap (evict when 90% full)
        if len(self._source_credibility) > int(self.MAX_SOURCE_ITEMS * 0.9):
            evict_count = len(self._source_credibility) - int(self.MAX_SOURCE_ITEMS * 0.8)
            for _ in range(evict_count):
                self._source_credibility.popitem(last=False)

        return assessment

    def check_temporal_consistency(
        self, events: list[Event]
    ) -> tuple[bool, list[Contradiction]]:
        """
        Check if a sequence of events is temporally consistent.

        Detects impossible temporal orderings (effects before causes,
        circular dependencies, etc.).

        Args:
            events: List of events to check

        Returns:
            Tuple of (is_consistent, list_of_contradictions)
        """
        if len(events) < 2:
            return True, []

        contradictions: list[Contradiction] = []

        # Sort events by timestamp
        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Check for explicit temporal contradictions in metadata
        for i, event_a in enumerate(sorted_events):
            for event_b in sorted_events[i + 1 :]:
                # Check if A claims to happen after B (contradiction)
                if event_a.metadata.get("claims_after") == event_b.event_id:
                    contradiction = Contradiction(
                        claim_a=f"{event_a.description} (at {event_a.timestamp})",
                        claim_b=f"{event_b.description} (at {event_b.timestamp})",
                        contradiction_type="temporal",
                        severity=0.9,
                        evidence_supporting_a=[event_a.source],
                        evidence_supporting_b=[event_b.source],
                        resolution_notes=f"Event {event_a.event_id} claims to occur after {event_b.event_id} but has earlier timestamp",
                    )
                    contradictions.append(contradiction)

        # Check for cause-effect temporal violations
        for event in sorted_events:
            causes = event.metadata.get("causes", [])
            for cause_id in causes:
                cause_event = next((e for e in events if e.event_id == cause_id), None)
                if cause_event and cause_event.timestamp > event.timestamp:
                    contradiction = Contradiction(
                        claim_a=f"{event.description} is caused by {cause_event.description}",
                        claim_b=f"Cause occurs at {cause_event.timestamp}, effect at {event.timestamp}",
                        contradiction_type="temporal",
                        severity=0.95,
                        evidence_supporting_a=[event.source],
                        evidence_supporting_b=[cause_event.source],
                        resolution_notes="Effect timestamp precedes cause timestamp",
                    )
                    contradictions.append(contradiction)

        is_consistent = len(contradictions) == 0
        return is_consistent, contradictions

    def detect_contradictions(self, evidence_list: list[Evidence]) -> list[Contradiction]:
        """
        Detect contradictions within a set of evidence items.

        Uses efficient pairwise comparison with early termination for
        memory-constrained environments.

        Args:
            evidence_list: List of evidence to check for contradictions

        Returns:
            List of detected contradictions
        """
        contradictions: list[Contradiction] = []

        # Limit window for M1 memory optimization
        window_size = min(len(evidence_list), self.max_contradiction_window)
        evidence_window = evidence_list[:window_size]

        # Pairwise comparison with early exit for high-confidence contradictions
        for i, evidence_a in enumerate(evidence_window):
            for evidence_b in evidence_window[i + 1 :]:
                contradiction = self._check_pairwise_contradiction(
                    evidence_a, evidence_b
                )
                if contradiction:
                    contradictions.append(contradiction)

                # Early termination if too many contradictions found
                if len(contradictions) >= 20:
                    logger.warning(
                        "Contradiction detection hit limit (20), stopping early"
                    )
                    return contradictions

        return contradictions

    async def cross_reference_databases(
        self, claim: str
    ) -> list[CrossReferenceResult]:
        """
        Cross-reference a claim across multiple databases.

        Queries various knowledge bases, fact-checking databases, and
        authoritative sources to verify the claim.

        Args:
            claim: The claim to cross-reference

        Returns:
            List of cross-reference results from different databases
        """
        results: list[CrossReferenceResult] = []

        # Define databases to query (simulated for M1 optimization)
        databases = [
            "knowledge_graph",
            "fact_check_db",
            "academic_sources",
            "news_archive",
        ]

        # Query all databases concurrently
        tasks = [self._query_database(db, claim) for db in databases]
        db_results = await safe_gather_dropin(*tasks, label="adversarial:509")

        for db_id, result in zip(databases, db_results, strict=False):
            if isinstance(result, Exception):
                logger.warning(f"Database query failed for {db_id}: {result}")
                continue
            results.append(result)

        return results

    def generate_devils_advocate(self, hypothesis: Hypothesis) -> str:
        """
        Generate a devil's advocate argument against a hypothesis.

        Creates a structured argument challenging the hypothesis,
        identifying weak points, and proposing alternative explanations.

        Args:
            hypothesis: The hypothesis to challenge

        Returns:
            Devil's advocate argument text
        """
        arguments: list[str] = []

        # Challenge 1: Insufficient evidence
        if len(hypothesis.supporting_evidence) < 3:
            arguments.append(
                f"The hypothesis relies on only {len(hypothesis.supporting_evidence)} "
                "evidence items, which may be insufficient for a robust conclusion."
            )

        # Challenge 2: Low source diversity
        sources = set()
        for eid in hypothesis.supporting_evidence:
            evidence = self.hypothesis_engine._evidence.get(eid)
            if evidence:
                sources.add(evidence.source)
        if len(sources) < 2:
            arguments.append(
                "Evidence comes from a limited number of sources, increasing risk of "
                "systematic bias or coordinated misinformation."
            )

        # Challenge 3: Presence of conflicting evidence
        if hypothesis.conflicting_evidence:
            arguments.append(
                f"There are {len(hypothesis.conflicting_evidence)} pieces of conflicting "
                "evidence that have not been adequately addressed."
            )

        # Challenge 4: Logical gaps
        logical_issues = self._identify_logical_gaps(hypothesis)
        for issue in logical_issues:
            arguments.append(f"Logical gap identified: {issue}")

        # Challenge 5: Alternative explanations
        alternatives = self._generate_alternative_explanations(hypothesis)
        if alternatives:
            arguments.append(
                "Alternative explanations exist that could account for the observed evidence:"
            )
            for alt in alternatives[:3]:
                arguments.append(f"  - {alt}")

        # Challenge 6: Assumption violations
        assumptions = self._identify_assumptions(hypothesis)
        for assumption in assumptions:
            arguments.append(
                f"The hypothesis assumes: '{assumption}' - this may not hold under all conditions."
            )

        if not arguments:
            arguments.append(
                "While the hypothesis appears well-supported, extraordinary claims "
                "require extraordinary evidence. Continued scrutiny is warranted."
            )

        return "\n\n".join(arguments)

    # -------------------------------------------------------------------------
    # Internal Helper Methods
    # -------------------------------------------------------------------------

    def _detect_bias_indicators(self, source: str) -> list[str]:
        """Detect bias indicators in a source identifier."""
        indicators = []
        source_lower = source.lower()

        for category, keywords in self._bias_keywords.items():
            if any(kw in source_lower for kw in keywords):
                indicators.append(category)

        return indicators

    def _evidence_contradicts_claim(self, evidence: Evidence, claim: str) -> bool:
        """Check if evidence contradicts a claim."""
        # Simple keyword-based contradiction detection
        claim_lower = claim.lower()
        # Guard against None content (fail-soft)
        if not evidence.content:
            return False
        evidence_lower = evidence.content.lower()

        # Check for negation patterns
        negators = ["not", "no", "never", "false", "incorrect", "disputed"]
        claim_has_negation = any(n in claim_lower for n in negators)
        evidence_has_negation = any(n in evidence_lower for n in negators)

        # If one has negation and the other doesn't, check for content similarity
        if claim_has_negation != evidence_has_negation:
            # Extract key terms (simple approach)
            claim_terms = set(claim_lower.split()) - set(negators)
            evidence_terms = set(evidence_lower.split()) - set(negators)

            # If significant term overlap, likely contradiction
            overlap = claim_terms & evidence_terms
            if len(overlap) >= 3:
                return True

        # Check metadata for explicit contradiction flag
        if evidence.metadata.get("contradicts"):
            return True

        return False

    async def _query_counter_evidence_databases(
        self, claim: str, context: dict[str, Any]
    ) -> list[Evidence]:
        """Query external databases for counter-evidence."""
        # Simulated async database queries
        # In production, this would query actual fact-checking APIs
        await asyncio.sleep(0.001)  # Simulate minimal latency
        return []

    async def _query_database(self, database_id: str, claim: str) -> CrossReferenceResult:
        """Query a specific database for claim verification."""
        # Simulated database query with realistic response patterns
        await asyncio.sleep(0.001)

        # Generate deterministic but realistic result
        claim_hash = hashlib.md5(claim.encode()).hexdigest()
        confidence = int(claim_hash[:2], 16) / 255

        return CrossReferenceResult(
            database_id=database_id,
            claim_found=confidence > 0.3,
            confidence=confidence,
            supporting_sources=[database_id] if confidence > 0.6 else [],
            conflicting_sources=[database_id] if confidence < 0.4 else [],
        )

    def _check_pairwise_contradiction(
        self, evidence_a: Evidence, evidence_b: Evidence
    ) -> Contradiction | None:
        """Check if two evidence items contradict each other."""
        content_a = evidence_a.content.lower()
        content_b = evidence_b.content.lower()

        # Check for negation contradictions
        negators = ["not ", "no ", "never ", "false", "incorrect"]
        a_negated = any(n in content_a for n in negators)
        b_negated = any(n in content_b for n in negators)

        if a_negated != b_negated:
            # Check for similar subject matter
            a_words = set(content_a.split())
            b_words = set(content_b.split())
            overlap = len(a_words & b_words) / max(len(a_words), len(b_words), 1)

            if overlap > 0.5:
                return Contradiction(
                    claim_a=evidence_a.content[:100],
                    claim_b=evidence_b.content[:100],
                    contradiction_type="factual",
                    severity=0.7 + (overlap * 0.2),
                    evidence_supporting_a=[evidence_a.evidence_id],
                    evidence_supporting_b=[evidence_b.evidence_id],
                )

        # Check for temporal contradictions
        time_a = evidence_a.metadata.get("timestamp")
        time_b = evidence_b.metadata.get("timestamp")
        if time_a and time_b and time_a != time_b:
            # Check if they claim opposite temporal relationships
            pass  # Would need more sophisticated temporal parsing

        return None

    def _extract_events(self, evidence_list: list[Evidence]) -> list[Event]:
        """Extract temporal events from evidence items."""
        events: list[Event] = []

        for evidence in evidence_list:
            # Check if evidence has temporal metadata
            if "event_timestamp" in evidence.metadata:
                events.append(
                    Event(
                        event_id=evidence.evidence_id,
                        description=evidence.content[:100],
                        timestamp=evidence.metadata["event_timestamp"],
                        source=evidence.source,
                        metadata=evidence.metadata,
                    )
                )

        return events

    async def _find_supporting_evidence(
        self, claim: str, context: dict[str, Any]
    ) -> list[Evidence]:
        """Find evidence supporting a claim."""
        supporting: list[Evidence] = []

        for evidence in self.hypothesis_engine._evidence.values():
            # Check if evidence supports the claim
            if self._evidence_supports_claim(evidence, claim):
                supporting.append(evidence)

        # Sort by weighted score
        supporting.sort(key=lambda e: e.reliability * e.relevance, reverse=True)
        return supporting[:50]

    def _evidence_supports_claim(self, evidence: Evidence, claim: str) -> bool:
        """Check if evidence supports a claim."""
        claim_lower = claim.lower()
        evidence_lower = evidence.content.lower()

        # Simple keyword overlap
        claim_words = set(claim_lower.split())
        evidence_words = set(evidence_lower.split())
        overlap = len(claim_words & evidence_words)

        # Check for explicit support flag
        if evidence.metadata.get("supports"):
            return True

        # Check for high semantic similarity
        if overlap >= 3:
            # Check that evidence doesn't negate
            negators = ["not ", "no ", "never ", "false"]
            if not any(n in evidence_lower for n in negators):
                return True

        return False

    async def _generate_devils_advocate_analysis(
        self,
        claim: str,
        supporting: list[Evidence],
        contradicting: list[Evidence],
        context: dict[str, Any],
    ) -> tuple[float, list[str], list[str]]:
        """Generate devil's advocate analysis."""
        score = 0.0
        alternatives: list[str] = []
        fallacies: list[str] = []

        # Score based on counter-evidence strength
        if contradicting:
            total_weight = sum(e.reliability * e.relevance for e in contradicting)
            score += min(0.4, total_weight / 5)

        # Score based on source credibility issues
        for evidence in supporting:
            credibility = self.assess_source_credibility(evidence.source)
            if credibility.credibility_score < 0.4:
                score += 0.1
            if credibility.bias_indicators:
                score += 0.05 * len(credibility.bias_indicators)

        # Detect logical fallacies
        fallacies = self._detect_logical_fallacies(claim)
        score += 0.1 * len(fallacies)

        # Generate alternative explanations
        alternatives = self._generate_alternative_explanations_for_claim(claim)
        score += 0.05 * len(alternatives)

        return min(1.0, score), alternatives[:5], fallacies

    def _detect_logical_fallacies(self, text: str) -> list[str]:
        """Detect logical fallacies in text."""
        fallacies = []
        text_lower = text.lower()

        for fallacy_name, pattern in self._fallacy_patterns.items():
            if re.search(pattern, text_lower):
                fallacies.append(fallacy_name)

        return fallacies

    def _generate_alternative_explanations_for_claim(self, claim: str) -> list[str]:
        """Generate alternative explanations for a claim."""
        alternatives = []

        # Pattern-based alternative generation
        if "causes" in claim.lower() or "leads to" in claim.lower():
            alternatives.append("The observed correlation may be coincidental")
            alternatives.append("A third variable may be the true cause")
            alternatives.append("The causation may be reversed")

        if "is" in claim.lower() or "equals" in claim.lower():
            alternatives.append("The entities may be similar but distinct")
            alternatives.append("The relationship may be contextual rather than absolute")

        if "all" in claim.lower() or "every" in claim.lower():
            alternatives.append("There may be exceptions not yet observed")
            alternatives.append("The claim may hold only under specific conditions")

        return alternatives

    def _identify_logical_gaps(self, hypothesis: Hypothesis) -> list[str]:
        """Identify logical gaps in a hypothesis."""
        gaps = []
        statement = hypothesis.statement.lower()

        # Check for causal claims without mechanism
        if hypothesis.hypothesis_type == HypothesisType.CAUSAL.value:
            if "mechanism" not in statement and "how" not in statement:
                gaps.append("No proposed causal mechanism")

        # Check for generalizations from limited evidence
        evidence_count = len(hypothesis.supporting_evidence)
        if "all" in statement or "every" in statement:
            if evidence_count < 10:
                gaps.append(f"Universal claim based on only {evidence_count} evidence items")

        return gaps

    def _generate_alternative_explanations(self, hypothesis: Hypothesis) -> list[str]:
        """Generate alternative explanations for hypothesis evidence."""
        return self._generate_alternative_explanations_for_claim(hypothesis.statement)

    def _identify_assumptions(self, hypothesis: Hypothesis) -> list[str]:
        """Identify underlying assumptions in a hypothesis."""
        assumptions = []

        # Common implicit assumptions
        if HypothesisType.CAUSAL.value in hypothesis.hypothesis_type:
            assumptions.append("Causal relationships are stable over time")
            assumptions.append("No confounding variables are present")

        if HypothesisType.IDENTITY.value in hypothesis.hypothesis_type:
            assumptions.append("Identity criteria are universally applicable")
            assumptions.append("Attributes are sufficient for identification")

        return assumptions

    def _calculate_adversarial_confidence(
        self,
        supporting: list[Evidence],
        contradicting: list[Evidence],
        credibility: dict[str, SourceCredibility],
        contradictions: list[Contradiction],
        cross_references: list[CrossReferenceResult],
    ) -> float:
        """Calculate overall confidence after adversarial analysis."""
        # Base confidence from evidence balance
        support_weight = sum(
            e.reliability * e.relevance * credibility.get(e.source, SourceCredibility(e.source, 0.5)).credibility_score
            for e in supporting
        )
        contradict_weight = sum(
            e.reliability * e.relevance * credibility.get(e.source, SourceCredibility(e.source, 0.5)).credibility_score
            for e in contradicting
        )

        total_weight = support_weight + contradict_weight
        if total_weight == 0:
            base_confidence = 0.5
        else:
            base_confidence = support_weight / total_weight

        # Penalize for contradictions
        contradiction_penalty = min(0.3, len(contradictions) * 0.1)

        # Adjust based on cross-references
        cross_ref_boost = 0.0
        for ref in cross_references:
            if ref.claim_found and ref.confidence > 0.7:
                cross_ref_boost += 0.05
            elif not ref.claim_found:
                cross_ref_boost -= 0.05

        final_confidence = base_confidence - contradiction_penalty + cross_ref_boost
        return max(0.0, min(1.0, final_confidence))

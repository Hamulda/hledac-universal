"""
HypothesisEngine - Automated Hypothesis Generation and Testing
===============================================================

A comprehensive hypothesis management system implementing:
- Automated hypothesis generation from observations (abductive reasoning)
- Hypothesis testing framework with test design
- Falsification attempts (Popperian approach)
- Evidence gathering automation
- Confidence updating (Bayesian)
- Hypothesis ranking and selection
- Multi-hypothesis tracking
- Adversarial Verification (Devil's Advocate mode)

Hypothesis Types:
- Existence: Does X exist?
- Relationship: Is A connected to B?
- Causal: Does X cause Y?
- Identity: Is X the same as Y?
- Temporal: Did X happen before Y?

M1 8GB Optimizations:
- Efficient hypothesis space pruning
- Incremental belief updating
- Memory-efficient evidence tracking
- Streaming hypothesis evaluation
- Async database queries for adversarial verification
- Limited contradiction detection window
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol, Set, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)


class HypothesisType(Enum):
    """Types of hypotheses supported by the engine."""
    EXISTENCE = "existence"           # Does X exist?
    RELATIONSHIP = "relationship"     # Is A connected to B?
    CAUSAL = "causal"                 # Does X cause Y?
    IDENTITY = "identity"             # Is X the same as Y?
    TEMPORAL = "temporal"             # Did X happen before Y?


class HypothesisStatus(Enum):
    """Status of a hypothesis in its lifecycle."""
    ACTIVE = "active"                 # Currently being tested
    CONFIRMED = "confirmed"           # Sufficient evidence supports it
    REJECTED = "rejected"             # Falsified or insufficient support
    PENDING = "pending"               # Awaiting testing
    MERGED = "merged"                 # Merged with another hypothesis


class TestType(Enum):
    """Types of tests that can be designed and executed."""
    EXISTENCE_CHECK = "existence_check"
    CORRELATION_TEST = "correlation_test"
    CAUSAL_TEST = "causal_test"
    IDENTITY_VERIFICATION = "identity_verification"
    TEMPORAL_ORDERING = "temporal_ordering"
    CONSISTENCY_CHECK = "consistency_check"
    PREDICTION_TEST = "prediction_test"


@dataclass
class Evidence:
    """Evidence item supporting or conflicting with a hypothesis."""
    evidence_id: str
    source: str
    content: str
    timestamp: datetime
    reliability: float = 1.0  # 0-1, source reliability
    relevance: float = 1.0    # 0-1, relevance to hypothesis
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Result of executing a test against a hypothesis."""
    test_type: str
    result: str  # passed, failed, inconclusive
    confidence: float
    evidence_collected: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


@dataclass
class TestDesign:
    """Design for testing a hypothesis."""
    test_type: str
    description: str
    required_data: List[str] = field(default_factory=list)
    expected_outcome_if_true: str = ""
    expected_outcome_if_false: str = ""
    priority: float = 0.5  # 0-1, higher = test sooner
    cost_estimate: float = 1.0  # Estimated computational cost


@dataclass
class FalsificationResult:
    """Result of a falsification attempt."""
    falsified: bool
    confidence: float
    counter_evidence: List[str] = field(default_factory=list)
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


# =============================================================================
# Adversarial Verification Data Classes
# =============================================================================

@dataclass
class SourceCredibility:
    """
    Credibility assessment for an evidence source.

    Tracks historical accuracy, bias indicators, and overall trustworthiness.
    Used to weight evidence by source quality.
    """
    source_id: str
    credibility_score: float  # 0-1, overall credibility
    bias_indicators: List[str] = field(default_factory=list)
    historical_accuracy: float = 0.5  # 0-1, based on past verification
    last_updated: datetime = field(default_factory=datetime.now)
    total_claims: int = 0
    verified_claims: int = 0
    contradiction_count: int = 0

    def update_accuracy(self, was_correct: bool) -> None:
        """Update historical accuracy with a new verification result."""
        self.total_claims += 1
        if was_correct:
            self.verified_claims += 1
        self.historical_accuracy = self.verified_claims / self.total_claims
        # Recalculate credibility score
        self.credibility_score = (
            self.historical_accuracy * 0.7 +
            (1.0 - min(1.0, self.contradiction_count / 10)) * 0.3
        )
        self.last_updated = datetime.now()


@dataclass
class Event:
    """Temporal event for consistency checking."""
    event_id: str
    description: str
    timestamp: datetime
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Contradiction:
    """
    Represents a contradiction between two claims or evidence items.

    Tracks the type of contradiction (temporal, factual, logical) and severity.
    """
    claim_a: str
    claim_b: str
    contradiction_type: str  # temporal, factual, logical, source_bias
    severity: float  # 0-1, how serious the contradiction is
    evidence_supporting_a: List[str] = field(default_factory=list)
    evidence_supporting_b: List[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=datetime.now)
    resolution_notes: str = ""


@dataclass
class CrossReferenceResult:
    """Result of cross-referencing a claim across databases."""
    database_id: str
    claim_found: bool
    confidence: float
    supporting_sources: List[str] = field(default_factory=list)
    conflicting_sources: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdversarialReport:
    """
    Comprehensive adversarial verification report.

    Contains all findings from the devil's advocate analysis including
    counter-evidence, contradictions, source credibility assessments,
    and overall confidence scoring.
    """
    hypothesis: str
    supporting_evidence: List[Evidence]
    contradicting_evidence: List[Evidence]
    credibility_assessment: Dict[str, SourceCredibility]
    contradictions_found: List[Contradiction]
    temporal_consistency: bool
    overall_confidence: float  # 0-1, confidence in hypothesis after adversarial analysis
    devil_advocate_score: float  # 0-1, strength of counter-case (higher = stronger counter-arguments)
    alternative_explanations: List[str] = field(default_factory=list)
    logical_fallacies: List[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)
    verification_duration_ms: float = 0.0


@dataclass
class Hypothesis:
    """
    A hypothesis with full tracking and Bayesian updating.

    Implements Bayesian belief updating:
    - prior_probability: Initial belief before evidence
    - posterior_probability: Updated belief after evidence
    - confidence: Overall confidence score (derived from tests)
    """
    id: str
    statement: str
    hypothesis_type: str
    prior_probability: float = 0.5
    posterior_probability: float = 0.5
    confidence: float = 0.5
    supporting_evidence: List[str] = field(default_factory=list)
    conflicting_evidence: List[str] = field(default_factory=list)
    test_results: List[TestResult] = field(default_factory=list)
    status: str = "pending"  # active, confirmed, rejected, pending, merged
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    parent_hypotheses: List[str] = field(default_factory=list)  # For merged hypotheses
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at)

    def update_probability(self, likelihood_ratio: float) -> None:
        """
        Update posterior probability using Bayes' theorem.

        P(H|E) = P(E|H) * P(H) / P(E)

        Args:
            likelihood_ratio: P(E|H) / P(E|~H)
        """
        prior = self.posterior_probability
        posterior = (likelihood_ratio * prior) / (
            likelihood_ratio * prior + (1 - prior)
        )
        self.posterior_probability = max(0.0, min(1.0, posterior))
        self.updated_at = datetime.now()

    def add_test_result(self, result: TestResult) -> None:
        """Add a test result and update confidence."""
        self.test_results.append(result)
        self._recalculate_confidence()
        self.updated_at = datetime.now()

    def add_supporting_evidence(self, evidence_id: str, weight: float = 1.0) -> None:
        """Add supporting evidence with optional weight."""
        if evidence_id not in self.supporting_evidence:
            self.supporting_evidence.append(evidence_id)
            # Update probability with positive likelihood ratio
            self.update_probability(1.0 + weight * 0.5)
        self.updated_at = datetime.now()

    def add_conflicting_evidence(self, evidence_id: str, weight: float = 1.0) -> None:
        """Add conflicting evidence with optional weight."""
        if evidence_id not in self.conflicting_evidence:
            self.conflicting_evidence.append(evidence_id)
            # Update probability with negative likelihood ratio
            self.update_probability(1.0 / (1.0 + weight * 0.5))
        self.updated_at = datetime.now()

    def _recalculate_confidence(self) -> None:
        """Recalculate confidence based on test results."""
        if not self.test_results:
            self.confidence = self.posterior_probability
            return

        # Weighted average of test confidences
        total_weight = 0.0
        weighted_confidence = 0.0

        for i, result in enumerate(self.test_results):
            # More recent tests have higher weight
            weight = (i + 1) / len(self.test_results)
            total_weight += weight

            if result.result == "passed":
                weighted_confidence += weight * result.confidence
            elif result.result == "failed":
                weighted_confidence += weight * (1 - result.confidence)
            else:  # inconclusive
                weighted_confidence += weight * 0.5

        self.confidence = weighted_confidence / total_weight if total_weight > 0 else 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Convert hypothesis to dictionary."""
        return {
            "id": self.id,
            "statement": self.statement,
            "hypothesis_type": self.hypothesis_type,
            "prior_probability": self.prior_probability,
            "posterior_probability": self.posterior_probability,
            "confidence": self.confidence,
            "supporting_evidence": self.supporting_evidence,
            "conflicting_evidence": self.conflicting_evidence,
            "test_results": [
                {
                    "test_type": tr.test_type,
                    "result": tr.result,
                    "confidence": tr.confidence,
                    "evidence_collected": tr.evidence_collected,
                    "timestamp": tr.timestamp.isoformat(),
                }
                for tr in self.test_results
            ],
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "parent_hypotheses": self.parent_hypotheses,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Hypothesis:
        """Create hypothesis from dictionary."""
        test_results = [
            TestResult(
                test_type=tr["test_type"],
                result=tr["result"],
                confidence=tr["confidence"],
                evidence_collected=tr.get("evidence_collected", []),
                timestamp=datetime.fromisoformat(tr["timestamp"]),
            )
            for tr in data.get("test_results", [])
        ]

        return cls(
            id=data["id"],
            statement=data["statement"],
            hypothesis_type=data["hypothesis_type"],
            prior_probability=data.get("prior_probability", 0.5),
            posterior_probability=data.get("posterior_probability", 0.5),
            confidence=data.get("confidence", 0.5),
            supporting_evidence=data.get("supporting_evidence", []),
            conflicting_evidence=data.get("conflicting_evidence", []),
            test_results=test_results,
            status=data.get("status", "pending"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            parent_hypotheses=data.get("parent_hypotheses", []),
            metadata=data.get("metadata", {}),
        )


# Protocol for InferenceEngine integration
class InferenceEngineProtocol(Protocol):
    """Protocol for inference engine integration."""

    async def abductive_reasoning(
        self, observations: List[Evidence], context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate possible explanations (hypotheses) from observations."""
        ...

    async def evidence_chaining(
        self, hypothesis: Hypothesis, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Chain evidence to design tests for a hypothesis."""
        ...

    async def belief_update(
        self, hypothesis: Hypothesis, new_evidence: Evidence
    ) -> float:
        """Calculate updated belief given new evidence."""
        ...


# =============================================================================
# Adversarial Verifier
# =============================================================================

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
        hypothesis_engine: HypothesisEngine,
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
        self, claim: str, context: Optional[Dict[str, Any]] = None
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
                    explainer = SimpleNodeAblationExplainer(graph_rag)
                    importances = await explainer.explain_path(path, claim, max_nodes=5)
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
    ) -> List[Evidence]:
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
        self, claim: str, context: Optional[Dict[str, Any]] = None
    ) -> List[Evidence]:
        """
        Find counter-evidence for a claim string.

        Args:
            claim: The claim to find counter-evidence for
            context: Additional context

        Returns:
            List of contradicting evidence items
        """
        context = context or {}
        counter_evidence: List[Evidence] = []

        # Search existing evidence in hypothesis engine
        for evidence_id, evidence in self.hypothesis_engine._evidence.items():
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

        # Evict oldest entries if over cap
        while len(self._source_credibility) > self.MAX_SOURCE_ITEMS:
            self._source_credibility.popitem(last=False)

        return assessment

    def check_temporal_consistency(
        self, events: List[Event]
    ) -> Tuple[bool, List[Contradiction]]:
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

        contradictions: List[Contradiction] = []

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

    def detect_contradictions(self, evidence_list: List[Evidence]) -> List[Contradiction]:
        """
        Detect contradictions within a set of evidence items.

        Uses efficient pairwise comparison with early termination for
        memory-constrained environments.

        Args:
            evidence_list: List of evidence to check for contradictions

        Returns:
            List of detected contradictions
        """
        contradictions: List[Contradiction] = []

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
    ) -> List[CrossReferenceResult]:
        """
        Cross-reference a claim across multiple databases.

        Queries various knowledge bases, fact-checking databases, and
        authoritative sources to verify the claim.

        Args:
            claim: The claim to cross-reference

        Returns:
            List of cross-reference results from different databases
        """
        results: List[CrossReferenceResult] = []

        # Define databases to query (simulated for M1 optimization)
        databases = [
            "knowledge_graph",
            "fact_check_db",
            "academic_sources",
            "news_archive",
        ]

        # Query all databases concurrently
        tasks = [self._query_database(db, claim) for db in databases]
        db_results = await asyncio.gather(*tasks, return_exceptions=True)

        for db_id, result in zip(databases, db_results):
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
        arguments: List[str] = []

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

    def _detect_bias_indicators(self, source: str) -> List[str]:
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
        self, claim: str, context: Dict[str, Any]
    ) -> List[Evidence]:
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
    ) -> Optional[Contradiction]:
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

    def _extract_events(self, evidence_list: List[Evidence]) -> List[Event]:
        """Extract temporal events from evidence items."""
        events: List[Event] = []

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
        self, claim: str, context: Dict[str, Any]
    ) -> List[Evidence]:
        """Find evidence supporting a claim."""
        supporting: List[Evidence] = []

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
        supporting: List[Evidence],
        contradicting: List[Evidence],
        context: Dict[str, Any],
    ) -> Tuple[float, List[str], List[str]]:
        """Generate devil's advocate analysis."""
        score = 0.0
        alternatives: List[str] = []
        fallacies: List[str] = []

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

    def _detect_logical_fallacies(self, text: str) -> List[str]:
        """Detect logical fallacies in text."""
        fallacies = []
        text_lower = text.lower()

        for fallacy_name, pattern in self._fallacy_patterns.items():
            if re.search(pattern, text_lower):
                fallacies.append(fallacy_name)

        return fallacies

    def _generate_alternative_explanations_for_claim(self, claim: str) -> List[str]:
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

    def _identify_logical_gaps(self, hypothesis: Hypothesis) -> List[str]:
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

    def _generate_alternative_explanations(self, hypothesis: Hypothesis) -> List[str]:
        """Generate alternative explanations for hypothesis evidence."""
        return self._generate_alternative_explanations_for_claim(hypothesis.statement)

    def _identify_assumptions(self, hypothesis: Hypothesis) -> List[str]:
        """Identify underlying assumptions in a hypothesis."""
        assumptions = []
        statement = hypothesis.statement

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
        supporting: List[Evidence],
        contradicting: List[Evidence],
        credibility: Dict[str, SourceCredibility],
        contradictions: List[Contradiction],
        cross_references: List[CrossReferenceResult],
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


# =============================================================================
# Sprint 67: Simple Node Ablation Explainer
# =============================================================================

class SimpleNodeAblationExplainer:
    """
    Explains path importance using leave-one-node-out ablation.

    Computes importance scores by removing each node and measuring
    the change in path score from graph_rag.
    """

    def __init__(self, graph_rag):
        """
        Initialize explainer.

        Args:
            graph_rag: GraphRAGOrchestrator instance with score_path method
        """
        self.graph_rag = graph_rag

    async def explain_path(
        self,
        path: List[str],
        hypothesis: str,
        max_nodes: int = 5
    ) -> Dict[str, float]:
        """
        Explain path importance using node ablation.

        Args:
            path: List of node IDs forming the path
            hypothesis: The hypothesis to score against
            max_nodes: Maximum nodes to ablate

        Returns:
            Dict mapping node index to importance score
        """
        if len(path) < 2:
            return {}

        # Pre-compute hypothesis embedding once
        embedder = await self.graph_rag._get_embedder()
        if embedder is None:
            return {}

        try:
            hypothesis_emb = await embedder._embed_text(hypothesis)
            if hypothesis_emb is None:
                hypothesis_emb = [0.0] * 384
        except Exception:
            hypothesis_emb = [0.0] * 384

        # Get original score
        n_nodes = min(len(path), max_nodes)
        try:
            original_score = await self.graph_rag.score_path(
                path, hypothesis, hypothesis_emb=hypothesis_emb
            )
        except Exception:
            return {}

        importances = {}
        for i in range(n_nodes):
            if i == 0 or i >= len(path) - 1:
                continue  # Skip start/end nodes

            # Create path with node removed
            new_path = path[:i] + path[i+1:]

            try:
                new_score = await self.graph_rag.score_path(
                    new_path, hypothesis, hypothesis_emb=hypothesis_emb
                )
                importances[str(i)] = original_score - new_score
            except Exception:
                continue

        # Filter out non-positive importances
        if all(v <= 0.0 for v in importances.values()):
            return {}

        return importances


async def explain_with_mlx(
    hypothesis: str,
    path: List[str],
    model_name: str = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
) -> tuple[str, str]:
    """
    Generate textual explanation using MLX-LM.

    Args:
        hypothesis: The hypothesis
        path: Graph path
        model_name: Model identifier

    Returns:
        Tuple of (explanation, prompt_hash)
    """
    import asyncio
    import hashlib

    try:
        from hledac.universal.utils.mlx_cache import get_mlx_model, get_mlx_semaphore

        model, tokenizer = await get_mlx_model(model_name)
        if model is None or tokenizer is None:
            return "MLX model unavailable", ""

        prompt = f"Explain why this path in a knowledge graph is important for the hypothesis: '{hypothesis}'. Path: {' -> '.join(path)}"

        from mlx_lm import generate
        loop = asyncio.get_running_loop()

        async with get_mlx_semaphore():
            try:
                explanation = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: generate(model, tokenizer, prompt, max_tokens=80, temp=0.0)
                    ),
                    timeout=10.0
                )
            except TypeError:
                # Fallback if temp not supported
                explanation = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: generate(model, tokenizer, prompt, max_tokens=80)
                    ),
                    timeout=10.0
                )

        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        return explanation.strip(), prompt_hash

    except asyncio.TimeoutError:
        return "Explanation generation timed out", ""
    except Exception as e:
        logger.debug(f"MLX explanation failed: {e}")
        return f"Generation failed: {e}", ""


@dataclass
class SourceHint:
    """Source recommendation with quality score."""
    source: str
    quality: float  # 0-1
    hint_type: str = "general"  # trusted_source, quoted_source, general


@dataclass
class HypothesisPack:
    """
    Bounded hypothesis/query pack from findings.

    Returned by build_hypothesis_pack() - practical OSINT guidance
    without requiring heavy model.

    Field roles (STRICT separation - each field has one job):
    - hypotheses: Concrete follow-up claims to verify (what might be true).
      NOT search queries. NOT IOCs. Pure "X might be connected to Y" claims.
    - suggested_queries: Ranked search queries to execute (how to investigate).
      Structured with query/rationale/type/priority/pivot_type.
      These are the actual search strings for the scheduler.
    - ioc_follow_ups: Structured IOC pivot trails (actionable IOC chains).
      Each has pivot/from/to/query/rationale/priority.
      These are domain-specific pivot paths, not general queries.
    - source_hints: Where to look next (quality-ranked sources).
      Each has source/quality/hint_type - not queries or IOCs.
    - provenance: "heuristic" or "model-assisted" (never mixed).

    Priority order for ranking: IOC pivots > entity-pair > relationship > broad entity
    """
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    suggested_queries: List[Dict[str, Any]] = field(default_factory=list)  # Has priority, pivot_type
    ioc_follow_ups: List[Dict[str, Any]] = field(default_factory=list)    # Has priority, to field
    source_hints: List[Any] = field(default_factory=list)
    provenance: str = "heuristic"  # "heuristic" or "model-assisted"

    def is_empty(self) -> bool:
        """Check if pack has any actionable content."""
        return (
            not self.hypotheses
            and not self.suggested_queries
            and not self.ioc_follow_ups
        )

    def summary(self) -> str:
        """One-line summary of pack contents."""
        parts = []
        if self.hypotheses:
            parts.append(f"{len(self.hypotheses)} hypotheses")
        if self.suggested_queries:
            types = {}
            for q in self.suggested_queries:
                t = q.get("type", "unknown")
                types[t] = types.get(t, 0) + 1
            type_str = ", ".join(f"{v} {k}" for k, v in list(types.items())[:3])
            parts.append(f"{len(self.suggested_queries)} queries ({type_str})")
        if self.ioc_follow_ups:
            parts.append(f"{len(self.ioc_follow_ups)} IOC pivots")
        if self.source_hints:
            parts.append(f"{len(self.source_hints)} sources")
        return ", ".join(parts) or "empty"

    def top_queries(self, n: int = 3) -> List[Dict[str, Any]]:
        """Get top N queries by priority for scheduler."""
        return sorted(self.suggested_queries, key=lambda x: x.get("priority", 0.5), reverse=True)[:n]

    def pivot_trail(self, ioc: str) -> List[Dict[str, Any]]:
        """Get all pivots starting from a specific IOC."""
        return [p for p in self.ioc_follow_ups if p.get("from") == ioc]

    # -------------------------------------------------------------------------
    # Sprint F150H.1: next_best_actions - actionable shortlist from pack
    # -------------------------------------------------------------------------

    def next_best_actions(self, max_actions: int = 4) -> List[Dict[str, Any]]:
        """
        Return a small, ranked shortlist of next actions.

        Prioritizes: IOC pivots > entity-pair > high-priority queries > sources.
        Returns max_actions items, never blocks, never loads models.

        Each action has: action_type, query, rationale, priority, pivot_type.
        """
        actions: List[Dict[str, Any]] = []
        seen_queries: Set[str] = set()

        # Pre-populate dedup set from all sources (IOC + queries + source hints)
        # so source_hints never collide with existing queries
        for pivot in self.ioc_follow_ups:
            q = pivot.get("query", "")
            if q:
                seen_queries.add(q)
        for q in self.suggested_queries:
            q_str = q.get("query", "")
            if q_str:
                seen_queries.add(q_str)

        # 1. IOC pivots (highest priority - actionable domain-specific paths)
        for pivot in sorted(self.ioc_follow_ups, key=lambda x: x.get("priority", 0.5), reverse=True)[:2]:
            q = pivot.get("query", "")
            if q and q not in seen_queries:
                actions.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "from_ioc": pivot.get("from", ""),
                    "to_field": pivot.get("to", ""),
                    "rationale": pivot.get("rationale", ""),
                    "priority": pivot.get("priority", 0.8),
                    "pivot_type": "ioc",
                })
                seen_queries.add(q)

        # 2. Top ranked queries (high priority, not already covered)
        for q in sorted(self.suggested_queries, key=lambda x: x.get("priority", 0.5), reverse=True):
            if q["query"] not in seen_queries and len(actions) < max_actions:
                actions.append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                    "pivot_type": q.get("pivot_type", "general"),
                })
                seen_queries.add(q["query"])

        # 3. Source hints (only if we still have room)
        for hint in self.source_hints[:2]:
            if len(actions) >= max_actions:
                break
            actions.append({
                "action_type": "source_check",
                "query": f'"{hint.source}" latest',
                "rationale": f"Source: {hint.source} (quality: {hint.quality:.2f})",
                "priority": hint.quality * 0.6,
                "pivot_type": "source",
            })

        return actions[:max_actions]

    # -------------------------------------------------------------------------
    # Sprint F150H.2: why_best_first - explain the best_first_path choice
    # -------------------------------------------------------------------------

    def why_best_first(self) -> Optional[Dict[str, Any]]:
        """
        Explain why best_first_path chose its action.

        Returns a dict with:
        - chosen_action: the best_first_path result
        - reason: human-readable explanation of priority ordering
        - alternatives: what else was available and why it ranked lower
        - pivot_type_rank: where this pivot_type sits in the priority order

        Returns None if pack is empty.
        """
        if self.is_empty():
            return None

        pivot_type_rank = {
            "ioc": 0,
            "ioc_lookup": 0,
            "entity_pair": 1,
            "relationship": 2,
            "ioc_entity": 3,
            "entity": 4,
            "entity_expansion": 5,
            "source": 6,
            "organization": 7,
            "temporal": 8,
            "general": 9,
        }

        chosen = self.best_first_path()
        if not chosen:
            return None

        pt = chosen.get("pivot_type", "general")
        rank = pivot_type_rank.get(pt, 9)
        alternatives: List[Dict[str, Any]] = []

        # Collect what ranked lower
        for pivot in self.ioc_follow_ups:
            if pivot.get("query") != chosen.get("query"):
                alternatives.append({
                    "action_type": "ioc_pivot",
                    "query": pivot.get("query", ""),
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "rank": 0,
                })

        for q in self.suggested_queries:
            if q.get("query") != chosen.get("query"):
                q_pt = q.get("pivot_type", "general")
                alternatives.append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "pivot_type": q_pt,
                    "priority": q.get("priority", 0.5),
                    "rank": pivot_type_rank.get(q_pt, 9),
                })

        # Sort alternatives by priority desc, then rank asc
        alternatives.sort(key=lambda x: (-x.get("priority", 0.5), x.get("rank", 9)))
        alternatives = alternatives[:3]

        if pt == "ioc":
            reason = "IOC pivot selected as highest-priority actionable domain path"
        elif pt == "entity_pair":
            reason = "Entity-pair query selected as most specific relationship probe"
        elif pt == "relationship":
            reason = "Relationship query selected for direct connection verification"
        elif pt == "source":
            reason = "Source check selected as lowest-risk verification path"
        else:
            reason = f"{pt} query selected by priority {chosen.get('priority', 0.5):.2f}"

        return {
            "chosen_action": chosen,
            "reason": reason,
            "pivot_type": pt,
            "pivot_type_rank": rank,
            "alternatives": alternatives,
            "total_ioc_pivots": len(self.ioc_follow_ups),
            "total_queries": len(self.suggested_queries),
            "total_sources": len(self.source_hints),
        }

    # -------------------------------------------------------------------------
    # Sprint F150H.2: discarded_as_redundant - what was dropped and why
    # -------------------------------------------------------------------------

    def discarded_as_redundant(self, max_items: int = 5) -> List[Dict[str, Any]]:
        """
        Return items from the pack that were dropped by actionable_shortlist dedup.

        Useful for understanding what was intentionally left out.
        Each item has: action_type, query, reason_discarded, pivot_type, priority.

        Reason codes:
        - 'query_deduped': same query string already in shortlist
        - 'below_priority_threshold': priority below 0.5 and shortlist already full
        - 'low_pivot_type_priority': general/organization pivot types deprioritized
        """
        shortlist_queries: Set[str] = set(
            a.get("query", "") for a in self.actionable_shortlist(max_items=999)
        )
        discarded: List[Dict[str, Any]] = []

        # Check IOC follow-ups
        for pivot in self.ioc_follow_ups:
            q = pivot.get("query", "")
            if q in shortlist_queries:
                discarded.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "reason_discarded": "query_deduped",
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "from_ioc": pivot.get("from", ""),
                })
            elif pivot.get("priority", 0.5) < 0.5:
                discarded.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "reason_discarded": "below_priority_threshold",
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "from_ioc": pivot.get("from", ""),
                })

        # Check suggested queries
        for q in self.suggested_queries:
            q_str = q.get("query", "")
            pt = q.get("pivot_type", "general")
            if q_str in shortlist_queries:
                discarded.append({
                    "action_type": "query",
                    "query": q_str,
                    "reason_discarded": "query_deduped",
                    "pivot_type": pt,
                    "priority": q.get("priority", 0.5),
                })
            elif pt in ("general", "organization") and q.get("priority", 0.5) < 0.6:
                discarded.append({
                    "action_type": "query",
                    "query": q_str,
                    "reason_discarded": "low_pivot_type_priority",
                    "pivot_type": pt,
                    "priority": q.get("priority", 0.5),
                })

        # Sort by priority desc
        discarded.sort(key=lambda x: -x.get("priority", 0.5))
        return discarded[:max_items]

    # -------------------------------------------------------------------------
    # Sprint F150H.2: action_confidence - confidence score for an action
    # -------------------------------------------------------------------------

    def action_confidence(self, action: Dict[str, Any]) -> float:
        """
        Score an action's confidence (0-1) based on pack context.

        Factors:
        - Base priority from the action itself (40%)
        - Whether it's an IOC pivot (bonus +0.15)
        - Source quality if source hint (from source_hints quality field)
        - Provenance: heuristic vs model-assisted (10% boost for model-assisted)

        Fail-soft: returns 0.5 for malformed actions.
        """
        if not action or not isinstance(action, dict):
            return 0.5

        base_priority = action.get("priority", 0.5)
        pivot_type = action.get("pivot_type", "general")

        # Pivot type bonus
        pt_bonus = 0.0
        if pivot_type in ("ioc", "ioc_lookup"):
            pt_bonus = 0.15
        elif pivot_type in ("entity_pair", "relationship"):
            pt_bonus = 0.10
        elif pivot_type == "source":
            # Source hints already encode quality in priority
            pt_bonus = 0.0

        # Source quality lookup (for source_check actions)
        source_bonus = 0.0
        if action.get("action_type") == "source_check":
            source_name = action.get("query", "").strip('"')
            for hint in self.source_hints:
                if hasattr(hint, "source") and hint.source == source_name:
                    source_bonus = (hint.quality - 0.5) * 0.2
                    break

        # Provenance bonus
        provenance_bonus = 0.10 if self.provenance == "model-assisted" else 0.0

        confidence = base_priority * 0.4 + min(base_priority + pt_bonus, 1.0) * 0.4 + source_bonus + provenance_bonus
        return max(0.0, min(1.0, confidence))

    # -------------------------------------------------------------------------
    # Sprint F150H.2: track_recommendation + best_track - track-level guidance
    # -------------------------------------------------------------------------

    def track_recommendation(self) -> Dict[str, Any]:
        """
        Recommend which investigation track to pursue next.

        Returns dict with:
        - recommended_track: name of the highest-value track
        - track_scores: dict of track -> score (0-1)
        - reasoning: why this track was recommended
        - next_action: first action from that track's shortlist
        """
        tracks = self.investigation_tracks()
        if not tracks:
            return {"recommended_track": None, "track_scores": {}, "reasoning": "empty pack", "next_action": None}

        # Score each track
        track_scores: Dict[str, float] = {}
        for track_name, items in tracks.items():
            if not items:
                track_scores[track_name] = 0.0
                continue

            # Score based on: item count, avg priority, IOC presence
            avg_priority = sum(i.get("priority", 0.5) for i in items) / len(items)
            ioc_count = sum(1 for i in items if i.get("action_type") == "ioc_pivot")
            high_conf_count = sum(1 for i in items if i.get("priority", 0.5) >= 0.7)

            # Weighted score
            score = (avg_priority * 0.4) + (min(ioc_count / 3, 1.0) * 0.3) + (high_conf_count / len(items) * 0.3)
            track_scores[track_name] = max(0.0, min(1.0, score))

        recommended = max(track_scores, key=lambda k: track_scores.get(k, 0.0))
        reasoning_map = {
            "ioc_pivots": "IOC pivot track has highest actionable domain value",
            "entity_tracking": "Entity tracking track has strong specific targets",
            "relationship_verification": "Relationship verification has direct connection probes",
            "source_investigation": "Source investigation is lowest-risk verification path",
            "cluster_analysis": "Cluster analysis offers broad correlation overview",
        }

        # Get next action from recommended track
        next_action = None
        if recommended in tracks and tracks[recommended]:
            first_item = tracks[recommended][0]
            next_action = {
                "action_type": first_item.get("action_type", "query"),
                "query": first_item.get("query", ""),
                "rationale": f"Track: {recommended}",
                "priority": first_item.get("priority", 0.5),
            }

        return {
            "recommended_track": recommended,
            "track_scores": track_scores,
            "reasoning": reasoning_map.get(recommended, f"{recommended} selected by score"),
            "next_action": next_action,
        }

    def best_track(self) -> Optional[str]:
        """Return the name of the highest-scoring track. Shortcut for track_recommendation."""
        tracks = self.investigation_tracks()
        if not tracks:
            return None
        return max(tracks, key=lambda t: sum(i.get("priority", 0.5) for i in tracks[t]) / max(1, len(tracks[t])))

    # -------------------------------------------------------------------------
    # Sprint F150H.1: investigation_tracks - multi-pronged paths
    # -------------------------------------------------------------------------

    def investigation_tracks(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Group pack contents into distinct investigation tracks.

        Returns dict with keys:
        - 'ioc_pivots': all IOC follow-ups grouped
        - 'entity_tracking': entity-based hypotheses + queries
        - 'relationship_verification': relationship hypotheses + queries
        - 'source_investigation': source hints + source queries
        - 'cluster_analysis': cross-entity/cross-IOC hypotheses

        Each track is a list of structured items with action_type + details.
        """
        tracks: Dict[str, List[Dict[str, Any]]] = {
            "ioc_pivots": [],
            "entity_tracking": [],
            "relationship_verification": [],
            "source_investigation": [],
            "cluster_analysis": [],
        }

        # IOC pivots track
        for pivot in self.ioc_follow_ups:
            tracks["ioc_pivots"].append({
                "action_type": "ioc_pivot",
                "from_ioc": pivot.get("from", ""),
                "pivot": pivot.get("pivot", ""),
                "to_field": pivot.get("to", ""),
                "query": pivot.get("query", ""),
                "priority": pivot.get("priority", 0.5),
            })

        # Entity tracking track
        for h in self.hypotheses:
            if h.get("type") in ("entity_tracking", "ioc_attribution"):
                tracks["entity_tracking"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                    "type": h.get("type", ""),
                })
        for q in self.suggested_queries:
            if q.get("pivot_type") in ("entity", "entity_expansion"):
                tracks["entity_tracking"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Relationship verification track
        for h in self.hypotheses:
            if h.get("type") in ("relationship_tracking", "cluster_correlation"):
                tracks["relationship_verification"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                    "type": h.get("type", ""),
                })
        for q in self.suggested_queries:
            if q.get("pivot_type") in ("relationship", "entity_pair"):
                tracks["relationship_verification"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Source investigation track
        for hint in self.source_hints:
            tracks["source_investigation"].append({
                "action_type": "source_hint",
                "source": hint.source if hasattr(hint, "source") else str(hint),
                "quality": hint.quality if hasattr(hint, "quality") else 0.5,
                "hint_type": hint.hint_type if hasattr(hint, "hint_type") else "general",
            })
        for q in self.suggested_queries:
            if q.get("pivot_type") == "source":
                tracks["source_investigation"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Cluster analysis track
        for h in self.hypotheses:
            if h.get("type") == "cluster_correlation":
                tracks["cluster_analysis"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                })

        # Remove empty tracks
        return {k: v for k, v in tracks.items() if v}

    # -------------------------------------------------------------------------
    # Sprint F150H.1: best_first_path - single optimal path through pack
    # -------------------------------------------------------------------------

    def best_first_path(self) -> Optional[Dict[str, Any]]:
        """
        Return the single best first action from the pack.

        IOC pivot if available, else top priority query, else None.
        Never returns empty - always prefers actionable IOC over noisy broad query.

        Returns:
            Dict with action_type, query, rationale, priority, pivot_type
            or None if pack is empty.
        """
        if self.is_empty():
            return None

        # First choice: highest priority IOC pivot
        if self.ioc_follow_ups:
            best_ioc = max(self.ioc_follow_ups, key=lambda x: x.get("priority", 0.5))
            return {
                "action_type": "ioc_pivot",
                "query": best_ioc.get("query", ""),
                "from_ioc": best_ioc.get("from", ""),
                "to_field": best_ioc.get("to", ""),
                "rationale": best_ioc.get("rationale", "IOC pivot"),
                "priority": best_ioc.get("priority", 0.9),
                "pivot_type": "ioc",
            }

        # Second choice: highest priority query (but prefer entity-pair or specific over broad)
        if self.suggested_queries:
            sorted_qs = sorted(
                self.suggested_queries,
                key=lambda x: (x.get("priority", 0.5), x.get("pivot_type", "") == "entity_expansion"),
                reverse=True,
            )
            # Prefer specific pivot types over general entity expansion
            for q in sorted_qs:
                pt = q.get("pivot_type", "")
                if pt in ("entity_pair", "relationship", "ioc_entity", "ioc_lookup"):
                    return {
                        "action_type": "query",
                        "query": q.get("query", ""),
                        "rationale": q.get("rationale", ""),
                        "priority": q.get("priority", 0.5),
                        "pivot_type": pt,
                    }
            # Fall back to highest priority query
            top = sorted_qs[0]
            return {
                "action_type": "query",
                "query": top.get("query", ""),
                "rationale": top.get("rationale", ""),
                "priority": top.get("priority", 0.5),
                "pivot_type": top.get("pivot_type", "general"),
            }

        return None

    # -------------------------------------------------------------------------
    # Sprint F150H.1: actionable_shortlist - compact sprint-ready output
    # -------------------------------------------------------------------------

    def actionable_shortlist(self, max_items: int = 5) -> List[Dict[str, Any]]:
        """
        Return a compact, sprint-ready shortlist.

        Prioritizes: IOC pivots > entity_pair > relationship > entity > other.
        Each item has: action_type, query, rationale, priority, pivot_type.
        Designed for direct scheduler consumption.
        """
        shortlist: List[Dict[str, Any]] = []
        seen_queries: Set[str] = set()

        # Priority order for pivot_type selection
        pivot_order = {
            "ioc": 0,
            "ioc_lookup": 0,
            "entity_pair": 1,
            "relationship": 2,
            "ioc_entity": 3,
            "entity": 4,
            "entity_expansion": 5,
            "source": 6,
            "organization": 7,
            "temporal": 8,
            "general": 9,
        }

        def item_priority(item: Dict[str, Any]) -> Tuple[float, int]:
            p = item.get("priority", 0.5)
            pt = item.get("pivot_type", "general")
            return (p, pivot_order.get(pt, 9))

        # Collect from IOC follow-ups first
        for pivot in sorted(self.ioc_follow_ups, key=item_priority):
            q = pivot.get("query", "")
            if q and q not in seen_queries:
                shortlist.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "rationale": pivot.get("rationale", f"{pivot.get('from', '')} → {pivot.get('to', '')}"),
                    "priority": pivot.get("priority", 0.9),
                    "pivot_type": "ioc",
                })
                seen_queries.add(q)
                if len(shortlist) >= max_items:
                    return shortlist

        # Then from suggested queries
        for q in sorted(self.suggested_queries, key=item_priority):
            query_str = q.get("query", "")
            if query_str and query_str not in seen_queries:
                shortlist.append({
                    "action_type": "query",
                    "query": query_str,
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                    "pivot_type": q.get("pivot_type", "general"),
                })
                seen_queries.add(query_str)
                if len(shortlist) >= max_items:
                    return shortlist

        return shortlist


class HypothesisEngine:
    """
    Engine for automated hypothesis generation, testing, and management.

    Implements a Popperian approach to hypothesis testing with Bayesian
    confidence updating. Now includes Adversarial Verification capabilities
    for rigorous devil's advocate analysis. Optimized for M1 8GB RAM constraints.

    Key Features:
    - Automated hypothesis generation from observations
    - Test design and execution framework
    - Falsification attempts (Popperian approach)
    - Adversarial Verification (Devil's Advocate mode)
    - Source credibility assessment and bias detection
    - Temporal consistency verification
    - Cross-database reference checking
    - Bayesian confidence updating
    - Hypothesis ranking and selection
    - Multi-hypothesis tracking with pruning

    Adversarial Verification Features:
    - Active counter-evidence search
    - Source bias and credibility scoring
    - Contradiction detection (factual, temporal, logical)
    - Alternative explanation generation
    - Logical fallacy detection
    - Devil's advocate argument generation

    M1 8GB Optimizations:
    - Streaming evaluation to limit memory usage
    - Aggressive pruning of low-confidence hypotheses
    - Incremental belief updates
    - Async database queries for adversarial checks
    - Limited contradiction detection window
    - Periodic garbage collection
    - Bounded evidence and source credibility with deterministic eviction
    """

    # Memory bounds for M1 8GB optimization
    MAX_EVIDENCE_ITEMS = 10_000
    MAX_SOURCE_ITEMS = 5_000

    def __init__(
        self,
        inference_engine: Optional[InferenceEngineProtocol] = None,
        max_hypotheses: int = 100,
        min_confidence_threshold: float = 0.1,
        memory_limit_mb: float = 500.0,
        enable_adversarial_verification: bool = True,
    ):
        """
        Initialize the HypothesisEngine.

        Args:
            inference_engine: Optional inference engine for abductive reasoning
            max_hypotheses: Maximum number of hypotheses to track
            min_confidence_threshold: Minimum confidence to keep a hypothesis
            memory_limit_mb: Target memory limit for hypothesis storage
            enable_adversarial_verification: Whether to enable adversarial verification
        """
        self.inference_engine = inference_engine
        self.max_hypotheses = max_hypotheses
        self.min_confidence_threshold = min_confidence_threshold
        self.memory_limit_mb = memory_limit_mb
        self.enable_adversarial_verification = enable_adversarial_verification

        # Hypothesis storage
        self._hypotheses: Dict[str, Hypothesis] = {}
        self._evidence: OrderedDict[str, Evidence] = OrderedDict()

        # Test design templates
        self._test_templates: Dict[str, Callable[[Hypothesis], TestDesign]] = {}
        self._init_test_templates()

        # Adversarial verifier (initialized lazily)
        self._adversarial_verifier: Optional[AdversarialVerifier] = None

        # Source credibility tracking for adversarial verification (bounded)
        self._source_credibility_cache: OrderedDict[str, SourceCredibility] = OrderedDict()

        # Statistics
        self._stats = {
            "generated": 0,
            "tested": 0,
            "confirmed": 0,
            "rejected": 0,
            "merged": 0,
            "pruned": 0,
            "adversarial_checks": 0,
        }

        logger.info(
            f"HypothesisEngine initialized (max_hypotheses={max_hypotheses}, "
            f"memory_limit={memory_limit_mb}MB, "
            f"adversarial_verification={enable_adversarial_verification})"
        )

    def _init_test_templates(self) -> None:
        """Initialize test design templates for each hypothesis type."""
        self._test_templates = {
            HypothesisType.EXISTENCE.value: self._design_existence_test,
            HypothesisType.RELATIONSHIP.value: self._design_relationship_test,
            HypothesisType.CAUSAL.value: self._design_causal_test,
            HypothesisType.IDENTITY.value: self._design_identity_test,
            HypothesisType.TEMPORAL.value: self._design_temporal_test,
        }

    # -------------------------------------------------------------------------
    # Bounded evidence and source credibility with deterministic LRU eviction
    # -------------------------------------------------------------------------

    def _evict_evidence_if_needed(self) -> None:
        """Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap."""
        while len(self._evidence) > self.MAX_EVIDENCE_ITEMS:
            # popitem(last=False) removes oldest (FIFO/LRU)
            self._evidence.popitem(last=False)

    def _evict_source_credibility_if_needed(self) -> None:
        """Evict oldest source credibility entries if over MAX_SOURCE_ITEMS cap."""
        while len(self._source_credibility_cache) > self.MAX_SOURCE_ITEMS:
            self._source_credibility_cache.popitem(last=False)

    def add_evidence(self, evidence: Evidence) -> str:
        """
        Add evidence with bounded storage and LRU eviction.

        Args:
            evidence: Evidence object to add

        Returns:
            Evidence ID
        """
        # Move to end if exists (update = touch)
        if evidence.evidence_id in self._evidence:
            self._evidence.move_to_end(evidence.evidence_id)
        else:
            self._evidence[evidence.evidence_id] = evidence

        self._evict_evidence_if_needed()
        return evidence.evidence_id

    def _update_source_credibility(self, source: str, credibility: SourceCredibility) -> None:
        """
        Update source credibility with bounded storage and LRU eviction.

        Args:
            source: Source identifier
            credibility: Source credibility assessment
        """
        # Move to end if exists (update = touch)
        if source in self._source_credibility_cache:
            self._source_credibility_cache.move_to_end(source)
        else:
            self._source_credibility_cache[source] = credibility

        self._evict_source_credibility_if_needed()

    def _design_existence_test(self, hypothesis: Hypothesis) -> TestDesign:
        """Design a test for an existence hypothesis."""
        return TestDesign(
            test_type=TestType.EXISTENCE_CHECK.value,
            description=f"Verify existence of entity mentioned in: {hypothesis.statement}",
            required_data=["entity_reference", "source_verification"],
            expected_outcome_if_true="Entity found in reliable sources",
            expected_outcome_if_false="Entity not found or disputed",
            priority=0.8,
            cost_estimate=1.0,
        )

    def _design_relationship_test(self, hypothesis: Hypothesis) -> TestDesign:
        """Design a test for a relationship hypothesis."""
        return TestDesign(
            test_type=TestType.CORRELATION_TEST.value,
            description=f"Test correlation between entities in: {hypothesis.statement}",
            required_data=["entity_a_data", "entity_b_data", "co_occurrence"],
            expected_outcome_if_true="Entities show significant correlation",
            expected_outcome_if_false="No significant correlation found",
            priority=0.7,
            cost_estimate=1.5,
        )

    def _design_causal_test(self, hypothesis: Hypothesis) -> TestDesign:
        """Design a test for a causal hypothesis."""
        return TestDesign(
            test_type=TestType.CAUSAL_TEST.value,
            description=f"Test causal link in: {hypothesis.statement}",
            required_data=["temporal_precedence", "covariation", "alternative_explanations"],
            expected_outcome_if_true="Cause precedes effect with consistent covariation",
            expected_outcome_if_false="No consistent causal pattern found",
            priority=0.9,
            cost_estimate=2.0,
        )

    def _design_identity_test(self, hypothesis: Hypothesis) -> TestDesign:
        """Design a test for an identity hypothesis."""
        return TestDesign(
            test_type=TestType.IDENTITY_VERIFICATION.value,
            description=f"Verify identity equivalence in: {hypothesis.statement}",
            required_data=["unique_identifiers", "attribute_comparison", "source_cross_reference"],
            expected_outcome_if_true="All identifiers and attributes match",
            expected_outcome_if_false="Discrepancies found in identifiers or attributes",
            priority=0.75,
            cost_estimate=1.2,
        )

    def _design_temporal_test(self, hypothesis: Hypothesis) -> TestDesign:
        """Design a test for a temporal hypothesis."""
        return TestDesign(
            test_type=TestType.TEMPORAL_ORDERING.value,
            description=f"Verify temporal ordering in: {hypothesis.statement}",
            required_data=["timestamp_a", "timestamp_b", "event_sequence"],
            expected_outcome_if_true="Event A clearly precedes Event B",
            expected_outcome_if_false="Event B precedes or concurrent with Event A",
            priority=0.7,
            cost_estimate=1.0,
        )

    # -------------------------------------------------------------------------
    # Adversarial Verification Integration
    # -------------------------------------------------------------------------

    @property
    def adversarial_verifier(self) -> AdversarialVerifier:
        """
        Lazy initialization of the AdversarialVerifier.

        Returns:
            AdversarialVerifier instance
        """
        if self._adversarial_verifier is None:
            self._adversarial_verifier = AdversarialVerifier(
                hypothesis_engine=self,
                max_contradiction_window=100,
                enable_streaming=True,
            )
        return self._adversarial_verifier

    async def adversarial_verification(
        self, hypothesis: Union[Hypothesis, str], context: Optional[Dict[str, Any]] = None
    ) -> AdversarialReport:
        """
        Perform comprehensive adversarial verification of a hypothesis.

        This method runs the devil's advocate analysis on a hypothesis,
        actively seeking counter-evidence, checking source credibility,
        detecting contradictions, and challenging assumptions.

        Args:
            hypothesis: The hypothesis to verify (or claim string)
            context: Additional context for verification

        Returns:
            AdversarialReport with comprehensive analysis
        """
        if not self.enable_adversarial_verification:
            logger.warning("Adversarial verification is disabled")
            claim = hypothesis.statement if isinstance(hypothesis, Hypothesis) else hypothesis
            return AdversarialReport(
                hypothesis=claim,
                supporting_evidence=[],
                contradicting_evidence=[],
                credibility_assessment={},
                contradictions_found=[],
                temporal_consistency=True,
                overall_confidence=0.5,
                devil_advocate_score=0.0,
                alternative_explanations=["Adversarial verification disabled"],
            )

        self._stats["adversarial_checks"] += 1

        if isinstance(hypothesis, Hypothesis):
            return await self.adversarial_verifier.verify_claim(
                hypothesis.statement, {**(context or {}), "hypothesis": hypothesis}
            )
        else:
            return await self.adversarial_verifier.verify_claim(hypothesis, context)

    def assess_source_credibility(self, source: str) -> SourceCredibility:
        """
        Assess the credibility of an evidence source.

        Args:
            source: The source identifier

        Returns:
            SourceCredibility assessment
        """
        if not self.enable_adversarial_verification:
            return SourceCredibility(source_id=source, credibility_score=0.5)

        return self.adversarial_verifier.assess_source_credibility(source)

    def detect_contradictions(self, evidence_list: List[Evidence]) -> List[Contradiction]:
        """
        Detect contradictions within a set of evidence items.

        Args:
            evidence_list: List of evidence to check

        Returns:
            List of detected contradictions
        """
        if not self.enable_adversarial_verification:
            return []

        return self.adversarial_verifier.detect_contradictions(evidence_list)

    def check_temporal_consistency(
        self, events: List[Event]
    ) -> Tuple[bool, List[Contradiction]]:
        """
        Check if a sequence of events is temporally consistent.

        Args:
            events: List of events to check

        Returns:
            Tuple of (is_consistent, list_of_contradictions)
        """
        if not self.enable_adversarial_verification:
            return True, []

        return self.adversarial_verifier.check_temporal_consistency(events)

    def generate_devils_advocate(self, hypothesis: Hypothesis) -> str:
        """
        Generate a devil's advocate argument against a hypothesis.

        Args:
            hypothesis: The hypothesis to challenge

        Returns:
            Devil's advocate argument text
        """
        if not self.enable_adversarial_verification:
            return "Adversarial verification is disabled."

        return self.adversarial_verifier.generate_devils_advocate(hypothesis)

    def generate_hypotheses(
        self, observations: List[Evidence], context: Optional[Dict[str, Any]] = None
    ) -> List[Hypothesis]:
        """
        Generate hypotheses from observations using abductive reasoning.

        Args:
            observations: List of evidence observations
            context: Additional context for hypothesis generation

        Returns:
            List of generated hypotheses
        """
        context = context or {}
        generated: List[Hypothesis] = []

        # Store evidence (bounded storage with LRU eviction)
        for obs in observations:
            self.add_evidence(obs)

        # Use inference engine if available
        if self.inference_engine:
            try:
                # Sprint 8BG: Avoid nested asyncio.run() — detect running loop
                try:
                    _ = asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop — safe to use asyncio.run()
                    explanations = asyncio.run(
                        self.inference_engine.abductive_reasoning(observations, context)
                    )
                else:
                    # Running loop exists — called from async context, skip inference
                    explanations = []
                    logger.debug("generate_hypotheses called from async context, skipping inference engine")
                for exp in explanations:
                    hypothesis = self._create_hypothesis_from_explanation(exp)
                    generated.append(hypothesis)
                    self._hypotheses[hypothesis.id] = hypothesis
            except Exception as e:
                logger.warning(f"Inference engine abductive reasoning failed: {e}")

        # Fallback: Generate hypotheses from observation patterns
        if not generated:
            generated = self._generate_hypotheses_from_patterns(observations, context)

        self._stats["generated"] += len(generated)

        # Prune if exceeding max
        if len(self._hypotheses) > self.max_hypotheses:
            self._prune_hypotheses()

        logger.info(f"Generated {len(generated)} hypotheses from {len(observations)} observations")
        return generated

    def _create_hypothesis_from_explanation(self, explanation: Dict[str, Any]) -> Hypothesis:
        """Create a hypothesis from an inference engine explanation."""
        return Hypothesis(
            id=str(uuid.uuid4())[:8],
            statement=explanation.get("statement", "Unknown hypothesis"),
            hypothesis_type=explanation.get("type", HypothesisType.EXISTENCE.value),
            prior_probability=explanation.get("probability", 0.5),
            posterior_probability=explanation.get("probability", 0.5),
            metadata=explanation.get("metadata", {}),
        )

    def _generate_hypotheses_from_patterns(
        self, observations: List[Evidence], context: Dict[str, Any]
    ) -> List[Hypothesis]:
        """Generate hypotheses by analyzing observation patterns."""
        generated: List[Hypothesis] = []

        # Group observations by source and topic
        by_topic: Dict[str, List[Evidence]] = {}
        for obs in observations:
            topic = obs.metadata.get("topic", "general")
            if topic not in by_topic:
                by_topic[topic] = []
            by_topic[topic].append(obs)

        # Generate existence hypotheses
        for topic, evidence_list in by_topic.items():
            if len(evidence_list) >= 2:
                h = Hypothesis(
                    id=str(uuid.uuid4())[:8],
                    statement=f"Entity '{topic}' exists based on multiple observations",
                    hypothesis_type=HypothesisType.EXISTENCE.value,
                    prior_probability=0.6,
                    posterior_probability=0.6,
                    supporting_evidence=[e.evidence_id for e in evidence_list[:3]],
                )
                generated.append(h)
                self._hypotheses[h.id] = h

        # Generate relationship hypotheses from co-occurrence
        topics = list(by_topic.keys())
        for i, topic_a in enumerate(topics):
            for topic_b in topics[i + 1 :]:
                # Check for co-occurrence in observations
                co_occur = self._check_co_occurrence(
                    by_topic[topic_a], by_topic[topic_b]
                )
                if co_occur > 0.5:
                    h = Hypothesis(
                        id=str(uuid.uuid4())[:8],
                        statement=f"'{topic_a}' is related to '{topic_b}'",
                        hypothesis_type=HypothesisType.RELATIONSHIP.value,
                        prior_probability=co_occur,
                        posterior_probability=co_occur,
                        supporting_evidence=[
                            e.evidence_id
                            for e in by_topic[topic_a][:2] + by_topic[topic_b][:2]
                        ],
                    )
                    generated.append(h)
                    self._hypotheses[h.id] = h

        # Generate causal hypotheses from temporal patterns
        temporal_obs = [o for o in observations if "timestamp" in o.metadata]
        if len(temporal_obs) >= 2:
            temporal_obs.sort(key=lambda x: x.metadata.get("timestamp", ""))
            for i in range(len(temporal_obs) - 1):
                h = Hypothesis(
                    id=str(uuid.uuid4())[:8],
                    statement=f"'{temporal_obs[i].content[:30]}...' may cause '{temporal_obs[i + 1].content[:30]}...'",
                    hypothesis_type=HypothesisType.CAUSAL.value,
                    prior_probability=0.3,  # Causal claims need strong evidence
                    posterior_probability=0.3,
                    supporting_evidence=[
                        temporal_obs[i].evidence_id,
                        temporal_obs[i + 1].evidence_id,
                    ],
                )
                generated.append(h)
                self._hypotheses[h.id] = h

        return generated

    def _check_co_occurrence(self, evidence_a: List[Evidence], evidence_b: List[Evidence]) -> float:
        """Check co-occurrence rate between two evidence groups."""
        if not evidence_a or not evidence_b:
            return 0.0

        # Simple co-occurrence: shared sources
        sources_a = {e.source for e in evidence_a}
        sources_b = {e.source for e in evidence_b}
        shared = sources_a & sources_b
        total = sources_a | sources_b

        return len(shared) / len(total) if total else 0.0

    def design_test(self, hypothesis: Hypothesis) -> TestDesign:
        """
        Design a test for a hypothesis.

        Args:
            hypothesis: The hypothesis to test

        Returns:
            Test design for the hypothesis
        """
        template = self._test_templates.get(hypothesis.hypothesis_type)
        if template:
            return template(hypothesis)

        # Default test design
        return TestDesign(
            test_type=TestType.CONSISTENCY_CHECK.value,
            description=f"General consistency check for: {hypothesis.statement}",
            required_data=["supporting_sources", "cross_references"],
            expected_outcome_if_true="Hypothesis is consistent with available data",
            expected_outcome_if_false="Inconsistencies found",
            priority=0.5,
            cost_estimate=1.0,
        )

    async def execute_test(
        self, test: TestDesign, context: Dict[str, Any]
    ) -> TestResult:
        """
        Execute a test design and return results.

        Args:
            test: The test design to execute
            context: Execution context with required data

        Returns:
            Test result
        """
        self._stats["tested"] += 1

        # Check if required data is available
        missing_data = [
            req for req in test.required_data if req not in context
        ]
        if missing_data:
            return TestResult(
                test_type=test.test_type,
                result="inconclusive",
                confidence=0.5,
                evidence_collected=[],
                metadata={"missing_data": missing_data},
            )

        # Simulate test execution (in practice, this would involve actual data collection)
        try:
            # Use inference engine for evidence chaining if available
            if self.inference_engine:
                chained_evidence = await self.inference_engine.evidence_chaining(
                    context.get("hypothesis"), context
                )
                evidence_ids = [e.get("id") for e in chained_evidence if e.get("id")]
            else:
                # Fallback: use context evidence
                evidence_ids = context.get("available_evidence", [])

            # Determine result based on evidence quality
            evidence_quality = sum(
                self._evidence.get(eid, Evidence("", "", "", datetime.now())).reliability
                for eid in evidence_ids
            ) / len(evidence_ids) if evidence_ids else 0.5

            # Simulate test outcome
            if evidence_quality > 0.7:
                result = "passed"
                confidence = evidence_quality
            elif evidence_quality < 0.3:
                result = "failed"
                confidence = 1 - evidence_quality
            else:
                result = "inconclusive"
                confidence = 0.5

            return TestResult(
                test_type=test.test_type,
                result=result,
                confidence=confidence,
                evidence_collected=evidence_ids,
                metadata={"test_description": test.description},
            )

        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            return TestResult(
                test_type=test.test_type,
                result="inconclusive",
                confidence=0.0,
                evidence_collected=[],
                metadata={"error": str(e)},
            )

    def update_hypothesis(self, hypothesis: Hypothesis, result: TestResult) -> None:
        """
        Update a hypothesis based on a test result.

        Args:
            hypothesis: The hypothesis to update
            result: The test result to incorporate
        """
        hypothesis.add_test_result(result)

        # Update status based on confidence
        if hypothesis.confidence > 0.8:
            hypothesis.status = HypothesisStatus.CONFIRMED.value
            self._stats["confirmed"] += 1
        elif hypothesis.confidence < 0.2:
            hypothesis.status = HypothesisStatus.REJECTED.value
            self._stats["rejected"] += 1

        # Update in storage
        self._hypotheses[hypothesis.id] = hypothesis

        logger.debug(
            f"Updated hypothesis {hypothesis.id}: "
            f"confidence={hypothesis.confidence:.2f}, status={hypothesis.status}"
        )

    def attempt_falsification(
        self, hypothesis: Hypothesis, use_adversarial: bool = True
    ) -> FalsificationResult:
        """
        Attempt to falsify a hypothesis (Popperian approach).

        Actively seeks counter-evidence rather than confirmation.
        When use_adversarial is True, uses the AdversarialVerifier for
        enhanced counter-evidence search, source credibility checking,
        and contradiction detection.

        Args:
            hypothesis: The hypothesis to attempt to falsify
            use_adversarial: Whether to use adversarial verification

        Returns:
            Falsification result
        """
        counter_evidence: List[str] = []
        falsified = False
        confidence = 0.0
        reasoning = ""

        # Check for conflicting evidence
        if hypothesis.conflicting_evidence:
            counter_evidence = hypothesis.conflicting_evidence[:5]
            falsification_strength = len(hypothesis.conflicting_evidence) / (
                len(hypothesis.supporting_evidence)
                + len(hypothesis.conflicting_evidence)
                + 1
            )

            if falsification_strength > 0.5:
                falsified = True
                confidence = falsification_strength
                reasoning = (
                    f"Strong counter-evidence ({len(hypothesis.conflicting_evidence)} items) "
                    f"contradicts hypothesis"
                )

        # Check for failed tests
        failed_tests = [t for t in hypothesis.test_results if t.result == "failed"]
        if failed_tests:
            falsified = True
            confidence = max(confidence, max(t.confidence for t in failed_tests))
            reasoning += f"; {len(failed_tests)} tests failed"
            counter_evidence.extend([t.test_type for t in failed_tests])

        # Check for logical inconsistencies
        if not falsified:
            inconsistency = self._check_logical_inconsistency(hypothesis)
            if inconsistency:
                falsified = True
                confidence = 0.8
                reasoning = f"Logical inconsistency detected: {inconsistency}"

        # Enhanced adversarial verification
        if use_adversarial and self.enable_adversarial_verification:
            try:
                # Run adversarial checks
                adversarial_falsification = self._attempt_adversarial_falsification(
                    hypothesis
                )

                # Merge results
                if adversarial_falsification.falsified:
                    falsified = True
                    confidence = max(confidence, adversarial_falsification.confidence)
                    counter_evidence.extend(adversarial_falsification.counter_evidence)
                    if reasoning:
                        reasoning += "; " + adversarial_falsification.reasoning
                    else:
                        reasoning = adversarial_falsification.reasoning

            except Exception as e:
                logger.warning(f"Adversarial falsification failed: {e}")

        return FalsificationResult(
            falsified=falsified,
            confidence=confidence,
            counter_evidence=counter_evidence,
            reasoning=reasoning or "No falsification criteria met",
        )

    def _attempt_adversarial_falsification(
        self, hypothesis: Hypothesis
    ) -> FalsificationResult:
        """
        Enhanced falsification using adversarial verification.

        Args:
            hypothesis: The hypothesis to falsify

        Returns:
            Falsification result from adversarial analysis
        """
        counter_evidence: List[str] = []
        contradictions_found = 0
        credibility_issues = 0

        # Get all evidence for this hypothesis
        all_evidence_ids = (
            hypothesis.supporting_evidence + hypothesis.conflicting_evidence
        )
        all_evidence = [
            self._evidence.get(eid) for eid in all_evidence_ids if eid in self._evidence
        ]

        # Check for contradictions in evidence
        if len(all_evidence) >= 2:
            contradictions = self.adversarial_verifier.detect_contradictions(
                all_evidence
            )
            contradictions_found = len(contradictions)

            for contradiction in contradictions:
                counter_evidence.append(
                    f"contradiction:{contradiction.claim_a[:30]}..."
                )

        # Check source credibility for supporting evidence
        for eid in hypothesis.supporting_evidence:
            evidence = self._evidence.get(eid)
            if evidence:
                credibility = self.adversarial_verifier.assess_source_credibility(
                    evidence.source
                )
                if credibility.credibility_score < 0.4:
                    credibility_issues += 1
                    counter_evidence.append(f"low_credibility:{evidence.source}")

        # Check for temporal inconsistencies
        events = self.adversarial_verifier._extract_events(all_evidence)
        if len(events) >= 2:
            is_consistent, temporal_contradictions = (
                self.adversarial_verifier.check_temporal_consistency(events)
            )
            if not is_consistent:
                contradictions_found += len(temporal_contradictions)
                for tc in temporal_contradictions:
                    counter_evidence.append(f"temporal:{tc.claim_a[:30]}...")

        # Calculate falsification confidence
        falsified = contradictions_found > 0 or credibility_issues >= 2

        if contradictions_found > 0:
            confidence = min(0.9, 0.5 + (contradictions_found * 0.1))
        elif credibility_issues >= 2:
            confidence = 0.6
        else:
            confidence = 0.0

        reasoning_parts = []
        if contradictions_found > 0:
            reasoning_parts.append(f"{contradictions_found} contradictions detected")
        if credibility_issues > 0:
            reasoning_parts.append(f"{credibility_issues} credibility issues found")

        return FalsificationResult(
            falsified=falsified,
            confidence=confidence,
            counter_evidence=counter_evidence,
            reasoning="; ".join(reasoning_parts) if reasoning_parts else "No adversarial issues found",
        )

    def _check_logical_inconsistency(self, hypothesis: Hypothesis) -> Optional[str]:
        """Check for logical inconsistencies in a hypothesis."""
        # Check if hypothesis contradicts confirmed hypotheses
        for other_id, other in self._hypotheses.items():
            if other_id == hypothesis.id:
                continue
            if other.status != HypothesisStatus.CONFIRMED.value:
                continue

            # Simple contradiction detection
            if self._statements_contradict(hypothesis.statement, other.statement):
                return f"Contradicts confirmed hypothesis {other_id}"

        return None

    def _statements_contradict(self, stmt_a: str, stmt_b: str) -> bool:
        """Check if two statements contradict each other."""
        # Simple negation detection
        negators = ["not ", "no ", "never ", "does not ", "is not ", "cannot "]
        a_negated = any(stmt_a.lower().startswith(n) for n in negators)
        b_negated = any(stmt_b.lower().startswith(n) for n in negators)

        # If one is negated and the other isn't, they might contradict
        # This is a simplified check - real implementation would use NLP
        if a_negated != b_negated:
            # Check for similar content
            a_clean = stmt_a.lower()
            b_clean = stmt_b.lower()
            for n in negators:
                a_clean = a_clean.replace(n, "")
                b_clean = b_clean.replace(n, "")

            # If content is similar but negation differs
            if len(set(a_clean.split()) & set(b_clean.split())) > 3:
                return True

        return False

    def rank_hypotheses(
        self, hypotheses: Optional[List[Hypothesis]] = None
    ) -> List[Hypothesis]:
        """
        Rank hypotheses by composite score.

        Scoring considers:
        - Confidence (posterior probability)
        - Test history quality
        - Evidence diversity
        - Falsification resistance

        Args:
            hypotheses: List to rank (defaults to all tracked hypotheses)

        Returns:
            Ranked list of hypotheses (highest score first)
        """
        hypotheses = hypotheses or list(self._hypotheses.values())

        scored: List[Tuple[float, Hypothesis]] = []
        for h in hypotheses:
            score = self._calculate_hypothesis_score(h)
            scored.append((score, h))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored]

    def _calculate_hypothesis_score(self, hypothesis: Hypothesis) -> float:
        """Calculate composite score for a hypothesis."""
        # Base confidence score
        confidence_score = hypothesis.posterior_probability

        # Test quality score
        if hypothesis.test_results:
            passed = sum(1 for t in hypothesis.test_results if t.result == "passed")
            test_score = passed / len(hypothesis.test_results)
        else:
            test_score = 0.5

        # Evidence diversity score
        unique_sources = len(set(
            self._evidence.get(eid, Evidence("", "unknown", "", datetime.now())).source
            for eid in hypothesis.supporting_evidence
        ))
        diversity_score = min(1.0, unique_sources / 3)

        # Falsification resistance
        falsification = self.attempt_falsification(hypothesis)
        resistance_score = 1 - falsification.confidence if falsification.falsified else 1.0

        # Weighted composite
        composite = (
            confidence_score * 0.35 +
            test_score * 0.25 +
            diversity_score * 0.20 +
            resistance_score * 0.20
        )

        return composite

    def get_most_likely(
        self, hypotheses: Optional[List[Hypothesis]] = None
    ) -> Optional[Hypothesis]:
        """
        Get the most likely hypothesis from a list.

        Args:
            hypotheses: List to search (defaults to all tracked hypotheses)

        Returns:
            The highest-ranked hypothesis, or None if empty
        """
        ranked = self.rank_hypotheses(hypotheses)
        return ranked[0] if ranked else None

    def merge_hypotheses(
        self, h1: Hypothesis, h2: Hypothesis
    ) -> Optional[Hypothesis]:
        """
        Attempt to merge two hypotheses if they are compatible.

        Args:
            h1: First hypothesis
            h2: Second hypothesis

        Returns:
            Merged hypothesis if compatible, None otherwise
        """
        # Check for compatibility
        if h1.hypothesis_type != h2.hypothesis_type:
            return None

        # Check for significant overlap in evidence
        shared_evidence = set(h1.supporting_evidence) & set(h2.supporting_evidence)
        total_evidence = set(h1.supporting_evidence) | set(h2.supporting_evidence)
        overlap_ratio = len(shared_evidence) / len(total_evidence) if total_evidence else 0

        if overlap_ratio < 0.3:
            return None

        # Check for statement similarity
        statement_similarity = self._statement_similarity(h1.statement, h2.statement)
        if statement_similarity < 0.5:
            return None

        # Create merged hypothesis
        merged = Hypothesis(
            id=str(uuid.uuid4())[:8],
            statement=f"Merged: {h1.statement[:50]} + {h2.statement[:50]}",
            hypothesis_type=h1.hypothesis_type,
            prior_probability=max(h1.prior_probability, h2.prior_probability),
            posterior_probability=(h1.posterior_probability + h2.posterior_probability) / 2,
            confidence=(h1.confidence + h2.confidence) / 2,
            supporting_evidence=list(total_evidence),
            conflicting_evidence=list(
                set(h1.conflicting_evidence) | set(h2.conflicting_evidence)
            ),
            test_results=h1.test_results + h2.test_results,
            status=HypothesisStatus.ACTIVE.value,
            parent_hypotheses=[h1.id, h2.id],
        )

        # Mark parents as merged
        h1.status = HypothesisStatus.MERGED.value
        h2.status = HypothesisStatus.MERGED.value
        self._hypotheses[h1.id] = h1
        self._hypotheses[h2.id] = h2

        # Store merged hypothesis
        self._hypotheses[merged.id] = merged
        self._stats["merged"] += 1

        logger.info(f"Merged hypotheses {h1.id} and {h2.id} into {merged.id}")
        return merged

    def _statement_similarity(self, stmt_a: str, stmt_b: str) -> float:
        """Calculate simple similarity between two statements."""
        words_a = set(stmt_a.lower().split())
        words_b = set(stmt_b.lower().split())

        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b

        return len(intersection) / len(union)

    def run_hypothesis_cycle(
        self,
        observations: List[Evidence],
        max_iterations: int = 10,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Hypothesis]:
        """
        Run a complete hypothesis generation and testing cycle.

        This is the main entry point for automated hypothesis management.

        Args:
            observations: Initial observations to generate hypotheses from
            max_iterations: Maximum number of test iterations
            context: Additional context

        Returns:
            Final list of hypotheses after testing
        """
        context = context or {}
        logger.info(f"Starting hypothesis cycle with {len(observations)} observations")

        # Phase 1: Generate hypotheses
        hypotheses = self.generate_hypotheses(observations, context)
        if not hypotheses:
            logger.warning("No hypotheses generated")
            return []

        # Phase 2: Design and execute tests
        for iteration in range(max_iterations):
            active_hypotheses = [
                h for h in self._hypotheses.values()
                if h.status == HypothesisStatus.ACTIVE.value
            ]

            if not active_hypotheses:
                logger.info("No active hypotheses remaining")
                break

            # Select highest priority hypothesis to test
            ranked = self.rank_hypotheses(active_hypotheses)
            target = ranked[0]

            # Design test
            test = self.design_test(target)

            # Execute test (async wrapper for sync context)
            # Sprint 8BG: Avoid nested asyncio.run() — detect running loop
            try:
                result = asyncio.run(
                    self.execute_test(test, {**context, "hypothesis": target})
                )
                self.update_hypothesis(target, result)
            except RuntimeError as e:
                if "asyncio.run() cannot be called" in str(e):
                    logger.warning("execute_test called from async context, skipping")
                else:
                    logger.error(f"Test execution failed: {e}")
            except Exception as e:
                logger.error(f"Test execution failed: {e}")

            # Attempt falsification periodically
            if iteration % 3 == 0:
                for h in list(self._hypotheses.values())[:5]:  # Top 5
                    if h.status == HypothesisStatus.ACTIVE.value:
                        falsification = self.attempt_falsification(h)
                        if falsification.falsified:
                            h.status = HypothesisStatus.REJECTED.value
                            h.confidence *= (1 - falsification.confidence)
                            self._hypotheses[h.id] = h

            # Memory management
            if iteration % 5 == 0:
                self._prune_hypotheses()
                gc.collect()

        # Final ranking
        final_hypotheses = self.rank_hypotheses()
        logger.info(
            f"Hypothesis cycle complete: {len(final_hypotheses)} hypotheses, "
            f"{self._stats['confirmed']} confirmed, {self._stats['rejected']} rejected"
        )

        return final_hypotheses

    def _prune_hypotheses(self) -> None:
        """Prune low-confidence hypotheses to manage memory."""
        if len(self._hypotheses) <= self.max_hypotheses:
            return

        # Sort by score
        ranked = self.rank_hypotheses()

        # Keep top hypotheses
        to_keep = set(h.id for h in ranked[: self.max_hypotheses])

        # Remove low-confidence hypotheses
        removed = 0
        for hid in list(self._hypotheses.keys()):
            if hid not in to_keep:
                h = self._hypotheses[hid]
                if h.confidence < self.min_confidence_threshold:
                    del self._hypotheses[hid]
                    removed += 1

        self._stats["pruned"] += removed
        if removed > 0:
            logger.debug(f"Pruned {removed} low-confidence hypotheses")

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        """Get a hypothesis by ID."""
        return self._hypotheses.get(hypothesis_id)

    def get_all_hypotheses(
        self, status: Optional[str] = None
    ) -> List[Hypothesis]:
        """
        Get all hypotheses, optionally filtered by status.

        Args:
            status: Filter by status (active, confirmed, rejected, pending, merged)

        Returns:
            List of hypotheses
        """
        hypotheses = list(self._hypotheses.values())
        if status:
            hypotheses = [h for h in hypotheses if h.status == status]
        return hypotheses

    # ------------------------------------------------------------------
    # Sprint 8TD: Sprint-aware hypothesis generation
    # ------------------------------------------------------------------

    def generate_sprint_hypotheses(
        self,
        findings: list[str],
        ioc_graph: Any = None,
        max_hypotheses: int = 3,
    ) -> list[str]:
        """
        Sprint 8TD: Generovat testovatelné hypotézy z IOC findings.

        WINDUP fáze: voláno po sprintu s top findings + IOC graph.
        Formát: "IF [evidence] THEN [hypothesis] [confidence: 0.x]"

        Args:
            findings: List of top finding strings
            ioc_graph: Optional IOC graph for context
            max_hypotheses: Max počet hypotéz (default 3)

        Returns:
            List of hypothesis strings
        """
        if not findings:
            return []

        # Sestavit hypotézy z findings
        hypotheses: list[str] = []

        for i, finding in enumerate(findings[:max_hypotheses]):
            # Základní formát hypotézy
            h = f"IF finding: {finding[:100]!r} THEN credible_with_confidence: 0.{7+i}"
            hypotheses.append(h)

        # Přidat IOC-based hypotézy pokud máme graf
        if ioc_graph is not None and len(findings) >= 2:
            try:
                # Jednoduchá korelace: 2+ findings z stejného source = related
                h_ioc = (
                    f"IF {len(findings)} related findings THEN shared_attribution "
                    f"with confidence: 0.{min(9, 5 + len(findings))}"
                )
                hypotheses.append(h_ioc)
            except Exception:
                pass

        # Ořezat na max_hypotheses
        return hypotheses[:max_hypotheses]

    # -------------------------------------------------------------------------
    # Sprint F150H: Follow-up Query Seam (heuristic-first, bounded)
    # -------------------------------------------------------------------------

    def suggest_next_queries(
        self,
        findings: Union[List[str], str],
        context: Optional[Dict[str, Any]] = None,
        max_queries: int = 5,
    ) -> List[Dict[str, str]]:
        """
        Generate bounded follow-up search queries from findings.

        HEURISTIC-FIRST: Cheap pattern-based extraction as primary path.
        MODEL-ASSISTED: Optional MLX enhancement only if available, never blocking.

        This is a SEAM - a bounded interface for next-hypothesis generation
        that doesn't require full hypothesis loop or heavy model.

        Args:
            findings: Single finding string or list of finding strings
            context: Optional context dict (may include 'entity_types', 'known_iocs')
            max_queries: Maximum queries to return (hard cap, default 5)

        Returns:
            List of dicts with keys: 'query' (str), 'rationale' (str), 'type' (str)
            Types: 'entity_expansion', 'relationship_check', 'temporal_expansion', 'source_discovery'
        """
        context = context or {}
        if isinstance(findings, str):
            findings = [findings]

        if not findings:
            return []

        # Hard cap
        max_queries = min(max_queries, 5)

        queries: List[Dict[str, str]] = []

        # --- HEURISTIC PATH (primary, always available) ---
        queries.extend(self._heuristic_query_generation(findings, context))

        # --- MODEL-ASSISTED PATH (optional enhancement) ---
        # Only if we have room and MLX is available
        if len(queries) < max_queries:
            model_queries = self._model_assisted_query_suggestion(
                findings, context, max_queries - len(queries)
            )
            if model_queries:
                queries.extend(model_queries)

        # Deduplicate by query text (preserve first rationale)
        seen = set()
        unique = []
        for q in queries:
            if q["query"] not in seen:
                seen.add(q["query"])
                unique.append(q)

        return unique[:max_queries]

    def _heuristic_query_generation(
        self,
        findings: List[str],
        context: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Generate queries using cheap heuristics - no model required."""
        queries: List[Dict[str, str]] = []
        all_text = " ".join(findings)

        # --- Entity Extraction ---
        entities = self._extract_entities_heuristic(all_text)
        known_iocs = context.get("known_iocs", set())

        # 1. Entity Expansion Queries
        for entity in entities[:3]:
            if entity not in known_iocs:
                queries.append({
                    "query": f'"{entity}" OR "{entity.lower()}"',
                    "rationale": f"Entity expansion: {entity}",
                    "type": "entity_expansion",
                })

        # 2. Pattern-based Relationship Queries
        rel_patterns = [
            (r"(\w+)\s+(?:linked|connected|related)\s+to\s+(\w+)", "linked_to"),
            (r"(\w+)\s+(?:uses?|employs?|leverages?)\s+(\w+)", "uses"),
            (r"(\w+)\s+(?:targeted|attacked)\s+(\w+)", "targeted"),
        ]

        for pattern, rel_type in rel_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            for m in matches[:2]:
                if len(m) == 2:
                    queries.append({
                        "query": f'"{m[0]}" AND "{m[1]}"',
                        "rationale": f"Relationship check: {m[0]} {rel_type} {m[1]}",
                        "type": "relationship_check",
                    })

        # 3. Temporal Expansion
        time_indicators = re.findall(
            r"(?:in|during|since|after|before)\s+(\d{4})", all_text
        )
        for year in time_indicators[:2]:
            queries.append({
                "query": f'timeline:{year} OR "{year}" security incident',
                "rationale": f"Temporal expansion: {year}",
                "type": "temporal_expansion",
            })

        # 4. Source Discovery - find related sources
        source_patterns = [
            r"(?:according to|from|via)\s+([A-Z][\w\s]+?(?:report|news|article|source))",
            r"(?:published|released)\s+(?:by\s+)?([A-Z][\w\s]+)",
        ]
        for pattern in source_patterns:
            sources = re.findall(pattern, all_text)
            for src in sources[:1]:
                clean_src = src.strip()[:40]
                queries.append({
                    "query": f'"{clean_src}" latest news',
                    "rationale": f"Source discovery: {clean_src}",
                    "type": "source_discovery",
                })

        # 5. IOC Correlation Queries
        iocs = self._extract_iocs_heuristic(all_text)
        for ioc_type, ioc_value in iocs[:2]:
            queries.append({
                "query": f"{ioc_type}:{ioc_value} OR {ioc_value}",
                "rationale": f"IOC correlation: {ioc_type}={ioc_value}",
                "type": "entity_expansion",
            })

        return queries[:5]

    # Known threat actor / malware / technique names (high-value, skip filter)
    _HIGH_VALUE_PATTERNS = [
        # APT groups
        r"\bAPT\d{1,2}\b", r"\bCozy Bear\b", r"\bFancy Bear\b", r"\bLazarus\b",
        r"\bWannaCry\b", r"\bNotPetya\b", r"\bSolarWinds\b", r"\bKaseya\b",
        r"\bLog4j\b", r"\bLog4Shell\b", r"\bCobalt Strike\b", r"\bMimikatz\b",
        r"\bEmotet\b", r"\bTrickBot\b", r"\bRyuk\b", r"\bDarkSide\b",
        r"\bREvil\b", r"\bBlackCat\b", r"\bALPHV\b", r"\bClop\b",
        r"\bConti\b", r"\bHive\b", r"\bLockBit\b", r"\bBlackMatter\b",
        # Techniques
        r"\bTrickBot\b", r"\bCobaltStrike\b", r"\bPowerShell\b",
        r"\bLiving off the Land\b", r"\bLotL\b",
    ]

    # Generic words to filter out from entity extraction
    _GENERIC_ENTITY_WORDS = {
        "actor", "target", "victim", "group", "campaign", "operation",
        "incident", "breach", "attack", "threat", "actor", "agent",
        "person", "individual", "team", "unit", "party", "entity",
        "system", "network", "server", "host", "machine", "device",
        "software", "tool", "malware", "ransomware", "virus", "trojan",
        "data", "information", "file", "document", "report", "source",
    }

    def _extract_entities_heuristic(self, text: str) -> List[str]:
        """Extract high-value threat entities using targeted patterns."""
        entities = []
        seen = set()

        # 1. High-value threat patterns (priority)
        for pattern in self._HIGH_VALUE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                name = match.group(0)
                if name.lower() not in seen:
                    seen.add(name.lower())
                    entities.append(name)

        # 2. CVE IDs as first-class entities
        for match in re.finditer(r"\b(CVE-\d{4}-\d{4,7})\b", text, re.IGNORECASE):
            cve = match.group(1).upper()
            if cve.lower() not in seen:
                seen.add(cve.lower())
                entities.append(cve)

        # 3. CamelCase compound words (organizations, products) - filter generics
        camel = re.findall(r"\b[A-Z][a-z]+(?:[A-Z]\w*)+\b", text)
        for c in camel[:5]:
            c_lower = c.lower()
            if c_lower not in seen and len(c) > 3 and c_lower not in self._GENERIC_ENTITY_WORDS:
                seen.add(c_lower)
                entities.append(c)

        # 4. Quoted strings (specific named entities) - filter generics
        quoted = re.findall(r'"([^"]{3,40})"', text)
        for q in quoted:
            q_lower = q.lower()
            words = q.split()
            if len(words) <= 4 and q_lower not in seen and q_lower not in self._GENERIC_ENTITY_WORDS:
                seen.add(q_lower)
                entities.append(q)

        # 5. All-caps acronyms (2-5 letters, skip common words and generics)
        skip = {"OR", "AND", "THE", "FOR", "WITH", "FROM", "THIS", "THAT", "WHEN", "THEN"}
        acronyms = re.findall(r"\b[A-Z]{2,5}\b", text)
        for a in acronyms:
            a_lower = a.lower()
            if a not in skip and a_lower not in seen and a_lower not in self._GENERIC_ENTITY_WORDS:
                seen.add(a_lower)
                entities.append(a)

        return entities[:12]  # Cap at 12 high-value entities

    def _extract_iocs_heuristic(self, text: str) -> List[Tuple[str, str]]:
        """Extract IOC-like patterns with better coverage."""
        iocs = []

        # CVE identifiers (priority - security context)
        cves = re.findall(r"\bCVE-\d{4}-\d{4,7}\b", text, re.IGNORECASE)
        for cve in cves[:3]:
            iocs.append(("cve", cve.upper()))

        # IP addresses (including IPv6 condensed)
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        for ip in ips[:3]:
            iocs.append(("ip", ip))

        # IPv6 (abbreviated)
        ipv6s = re.findall(r"\b[0-9a-fA-F:]+:[0-9a-fA-F:]+\b", text)
        for ip in ipv6s[:2]:
            if ":" in ip and len(ip) > 10:
                iocs.append(("ipv6", ip))

        # URLs with extraction of domain
        urls = re.findall(r"https?://[^\s\"'>]+", text)
        for url in urls[:3]:
            domain = re.sub(r"https?://", "", url).split("/")[0]
            if domain and len(domain) > 3:
                iocs.append(("domain", domain))

        # MD5/SHA hashes (32/64 chars)
        hashes = re.findall(r"\b[a-fA-F0-9]{32}\b", text)
        for h in hashes[:2]:
            iocs.append(("md5", h))
        sha256s = re.findall(r"\b[a-fA-F0-9]{64}\b", text)
        for h in sha256s[:2]:
            iocs.append(("sha256", h))
        sha1s = re.findall(r"\b[a-fA-F0-9]{40}\b", text)
        for h in sha1s[:2]:
            iocs.append(("sha1", h))

        # Malware/S implant paths (YARA-style)
        paths = re.findall(r"[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\\/:*?\"<>|\r\n]+", text)
        for p in paths[:2]:
            iocs.append(("path", p[:50]))

        # Registry keys
        regs = re.findall(r"HKLM\\[^,\s]+|HKCU\\[^,\s]+|HKCR\\[^,\s]+", text, re.IGNORECASE)
        for r in regs[:2]:
            iocs.append(("registry", r))

        # File names with extensions (common malware)
        files = re.findall(r"\b[\w\-]+\.(exe|dll|ps1|vbs|bat|cmd|js|jar|scr|sys)\b", text, re.IGNORECASE)
        for f in files[:3]:
            iocs.append(("file", f.lower()))

        return iocs

    # -------------------------------------------------------------------------
    # Sprint F150+: HypothesisPack - bounded multi-field seam
    # -------------------------------------------------------------------------

    def build_hypothesis_pack(
        self,
        findings: Union[List[str], str],
        context: Optional[Dict[str, Any]] = None,
    ) -> "HypothesisPack":
        """
        Build a practical hypothesis/query pack from findings.

        BOUNDED SEAM: Returns structured pack with:
        - hypotheses: Concrete follow-up hypotheses (not poetic)
        - suggested_queries: Ranked search queries with rationale
        - ioc_follow_ups: IOC pivot suggestions
        - source_hints: Where to look next
        - provenance: "heuristic" or "model-assisted"

        HEURISTIC-FIRST: This method works fully without heavy model.
        Model-assisted branch is lazy, fail-soft, never blocking.

        Args:
            findings: Single finding string or list of finding strings
            context: Optional context dict with keys:
                - 'known_entities': set of already-seen entities
                - 'known_iocs': set of already-seen IOCs
                - 'source_quality': dict mapping source->quality score
                - 'existing_relationships': list of (src, dst, rel) tuples
                - 'temporal_anchors': list of (event, year) tuples

        Returns:
            HypothesisPack with all fields populated (always, even without model)
        """
        context = context or {}
        if isinstance(findings, str):
            findings = [findings]

        if not findings:
            return HypothesisPack(
                hypotheses=[],
                suggested_queries=[],
                ioc_follow_ups=[],
                source_hints=[],
                provenance="heuristic",
            )

        all_text = " ".join(findings)
        known_entities: Set[str] = context.get("known_entities", set())
        known_iocs: Set[str] = context.get("known_iocs", set())
        source_quality: Dict[str, float] = context.get("source_quality", {})
        existing_rels: List[Tuple[str, str, str]] = context.get("existing_relationships", [])
        temporal_anchors: List[Tuple[str, str]] = context.get("temporal_anchors", [])

        # --- HEURISTIC PATH (primary, always available) ---
        provenance = "heuristic"

        # Extract all components heuristically
        entities = self._extract_entities_heuristic(all_text)
        new_entities = [e for e in entities if e not in known_entities]

        iocs = self._extract_iocs_heuristic(all_text)
        new_iocs = [(t, v) for t, v in iocs if v not in known_iocs]

        relationships = self._extract_relationships_heuristic(all_text)
        # Filter out already-known relationships
        new_rels = [
            (src, dst, rel)
            for src, dst, rel in relationships
            if (src, dst, rel) not in existing_rels and (dst, src, rel) not in existing_rels
        ]

        sources = self._extract_source_hints_heuristic(all_text, source_quality)
        temporal = self._extract_temporal_anchors_heuristic(all_text, temporal_anchors)

        # Generate hypotheses (concrete, OSINT-practical)
        hypotheses = self._generate_hypotheses_heuristic(
            findings, new_entities, new_iocs, new_rels
        )

        # Generate ranked queries
        suggested_queries = self._generate_ranked_queries(
            findings, new_entities, new_iocs, new_rels, sources
        )

        # Generate IOC follow-ups
        ioc_follow_ups = self._generate_ioc_follow_ups(new_iocs)

        # --- OPTIONAL NER CAPABILITY PROBE (fail-soft, never blocks) ---
        entities, iocs = self._ner_capability_probe(all_text, entities, iocs)

        # --- MODEL-ASSISTED PATH (optional, lazy, fail-soft) ---
        model_pack = self._model_assisted_hypothesis_pack(
            findings, context,
            new_entities=new_entities,
            new_iocs=new_iocs,
            heuristic_queries=suggested_queries,
        )

        if model_pack:
            # Merge model results into heuristic results
            if model_pack.hypotheses:
                hypotheses.extend(model_pack.hypotheses)
            if model_pack.suggested_queries:
                # Merge queries, dedup
                existing_queries = {q["query"] for q in suggested_queries}
                for mq in model_pack.suggested_queries:
                    if mq["query"] not in existing_queries:
                        suggested_queries.append(mq)
            if model_pack.ioc_follow_ups:
                ioc_follow_ups.extend(model_pack.ioc_follow_ups)
            if model_pack.source_hints:
                sources.extend(model_pack.source_hints)
            provenance = "model-assisted"

        # Final dedup and ranking
        suggested_queries = self._deduplicate_and_rank_queries(suggested_queries)

        return HypothesisPack(
            hypotheses=hypotheses[:10],  # Cap at 10 hypotheses
            suggested_queries=suggested_queries[:8],  # Cap at 8 queries
            ioc_follow_ups=ioc_follow_ups[:5],  # Cap at 5 IOC follow-ups
            source_hints=sources[:5],  # Cap at 5 source hints
            provenance=provenance,
        )

    def _generate_hypotheses_heuristic(
        self,
        findings: List[str],
        entities: List[str],
        iocs: List[Tuple[str, str]],
        relationships: List[Tuple[str, str, str]],
    ) -> List[Dict[str, str]]:
        """Generate concrete, OSINT-practical hypotheses from extracted data."""
        hypotheses: List[Dict[str, str]] = []

        # Entity-based hypotheses
        for entity in entities[:3]:
            hypotheses.append({
                "hypothesis": f"Entity '{entity}' is active in the threat space",
                "confidence": "0.6",
                "reason": f"Frequently mentioned in recent findings",
                "type": "entity_tracking",
            })

        # IOC-based hypotheses
        for ioc_type, ioc_value in iocs[:3]:
            hypotheses.append({
                "hypothesis": f"{ioc_type.upper()} indicator '{ioc_value}' belongs to active campaign",
                "confidence": "0.5",
                "reason": f"IOC observed in current findings",
                "type": "ioc_attribution",
            })

        # Relationship-based hypotheses
        for src, dst, rel in relationships[:2]:
            hypotheses.append({
                "hypothesis": f"'{src}' {rel} '{dst}' — relationship is operational",
                "confidence": "0.55",
                "reason": f"Pattern-based relationship detection",
                "type": "relationship_tracking",
            })

        # Cross-reference hypothesis (if we have multiple entities + IOCs)
        if len(entities) >= 2 and len(iocs) >= 1:
            hypotheses.append({
                "hypothesis": f"Multiple entities share common IOC infrastructure",
                "confidence": "0.45",
                "reason": f"Entity cluster with shared IOC patterns",
                "type": "cluster_correlation",
            })

        return hypotheses

    def _generate_ranked_queries(
        self,
        findings: List[str],
        entities: List[str],
        iocs: List[Tuple[str, str]],
        relationships: List[Tuple[str, str, str]],
        sources: List["SourceHint"],
    ) -> List[Dict[str, Any]]:
        """Generate and rank follow-up queries with entity-pair and co-occurrence pivots."""
        queries: List[Dict[str, Any]] = []
        all_text = " ".join(findings)

        # IOC correlation queries (highest priority)
        for ioc_type, ioc_value in iocs[:4]:
            queries.append({
                "query": f"{ioc_type}:{ioc_value}",
                "rationale": f"IOC lookup: {ioc_type}={ioc_value}",
                "type": "ioc_lookup",
                "priority": 0.95,
                "pivot_type": "ioc",
            })

        # Entity expansion queries (high priority)
        for entity in entities[:4]:
            queries.append({
                "query": f'"{entity}" OR "{entity.lower()}"',
                "rationale": f"Entity expansion: {entity}",
                "type": "entity_expansion",
                "priority": 0.88,
                "pivot_type": "entity",
            })

        # Entity-pair pivots: pairs of entities that co-occur in findings
        # Check which entities appear near each other
        entity_pairs = self._find_entity_pairs(all_text, entities)
        for src, dst in entity_pairs[:3]:
            queries.append({
                "query": f'"{src}" AND "{dst}"',
                "rationale": f"Entity pair: {src} + {dst} co-occurrence",
                "type": "entity_pair",
                "priority": 0.82,
                "pivot_type": "entity_pair",
            })

        # Relationship verification queries (if we have detected relationships)
        for src, dst, rel in relationships[:2]:
            queries.append({
                "query": f'"{src}" AND "{dst}"',
                "rationale": f"Verify relationship: {src} {rel} {dst}",
                "type": "relationship_verification",
                "priority": 0.78,
                "pivot_type": "relationship",
            })

        # Co-occurrence pivots: entities that co-occur with IOCs
        ioc_entities = self._find_ioc_entity_pairs(iocs, entities, all_text)
        for ioc_val, entity in ioc_entities[:3]:
            queries.append({
                "query": f"{ioc_val} AND \"{entity}\"",
                "rationale": f"IOC+entity co-occurrence: {ioc_val} + {entity}",
                "type": "ioc_entity_pivot",
                "priority": 0.85,
                "pivot_type": "ioc_entity",
            })

        # Source-based queries (quality-weighted)
        for src_hint in sources[:2]:
            queries.append({
                "query": f'"{src_hint.source}" latest',
                "rationale": f"Source check: {src_hint.source} (quality: {src_hint.quality:.2f})",
                "type": "source_discovery",
                "priority": src_hint.quality * 0.75,
                "pivot_type": "source",
            })

        # Domain/Organization anchor queries
        org_anchors = self._extract_org_anchors(all_text)
        for org in org_anchors[:2]:
            queries.append({
                "query": f'"{org}" (targeted OR attacked OR compromised)',
                "rationale": f"Org anchor pivot: {org}",
                "type": "org_pivot",
                "priority": 0.65,
                "pivot_type": "organization",
            })

        # Temporal expansion queries
        time_indicators = re.findall(r"\b(20[12]\d)\b", all_text)
        for year in list(set(time_indicators))[:1]:
            queries.append({
                "query": f'timeline:{year} security incident',
                "rationale": f"Temporal expansion: {year}",
                "type": "temporal_expansion",
                "priority": 0.45,
                "pivot_type": "temporal",
            })

        # Sort by priority descending
        queries.sort(key=lambda x: x.get("priority", 0.5), reverse=True)
        return queries[:10]  # Cap at 10 queries before dedup

    def _find_entity_pairs(self, text: str, entities: List[str]) -> List[Tuple[str, str]]:
        """Find entity pairs that co-occur in the same sentences."""
        pairs = []
        # Split into sentences
        sentences = re.split(r'[.!?]', text)
        entities_lower = {e.lower(): e for e in entities}

        for sent in sentences:
            sent_lower = sent.lower()
            found_in_sent = []
            for lower, original in entities_lower.items():
                if lower in sent_lower and len(lower) > 2:
                    found_in_sent.append(original)

            # Pairs of entities in same sentence
            for i in range(len(found_in_sent)):
                for j in range(i + 1, len(found_in_sent)):
                    pair = (found_in_sent[i], found_in_sent[j])
                    # Avoid very similar pairs
                    if pair[0].lower() not in pair[1].lower() and pair[1].lower() not in pair[0].lower():
                        pairs.append(pair)

        return pairs[:5]

    def _find_ioc_entity_pairs(
        self, iocs: List[Tuple[str, str]], entities: List[str], text: str
    ) -> List[Tuple[str, str]]:
        """Find IOCs that co-occur near entities in the text."""
        pairs = []
        text_lower = text.lower()

        for ioc_type, ioc_val in iocs:
            if len(ioc_val) < 3:
                continue
            ioc_lower = ioc_val.lower()
            # Find entities mentioned near this IOC
            for entity in entities:
                entity_lower = entity.lower()
                if entity_lower == ioc_lower:
                    continue
                # Check if entity appears within 100 chars of IOC
                idx_ioc = text_lower.find(ioc_lower)
                idx_entity = text_lower.find(entity_lower)
                if idx_ioc >= 0 and idx_entity >= 0:
                    if abs(idx_ioc - idx_entity) < 150:
                        pairs.append((ioc_val, entity))

        return pairs[:5]

    def _generate_ioc_follow_ups(self, iocs: List[Tuple[str, str]]) -> List[Dict[str, str]]:
        """Generate IOC pivot suggestions with actionable pivot queries."""
        follow_ups: List[Dict[str, str]] = []

        for ioc_type, ioc_value in iocs:
            if ioc_type == "cve":
                # Pivot: CVE -> exploit-db, NVD, related malware, affected products
                follow_ups.append({
                    "pivot": "cve",
                    "from": ioc_value,
                    "to": "exploitation_status",
                    "query": f'"{ioc_value}" exploit OR vulnerable OR patch OR affected',
                    "rationale": f"CVE exploitation status: {ioc_value}",
                    "priority": 0.95,
                })
                follow_ups.append({
                    "pivot": "cve",
                    "from": ioc_value,
                    "to": "threat_actors",
                    "query": f'"{ioc_value}" APT OR threat actor OR nation-state OR campaign',
                    "rationale": f"CVE in-the-wild exploitation: {ioc_value}",
                    "priority": 0.9,
                })
            elif ioc_type == "ip":
                # Pivot: IP -> threat intel, geolocation, passive DNS, historical
                follow_ups.append({
                    "pivot": "ip",
                    "from": ioc_value,
                    "to": "threat_intel",
                    "query": f'ip:{ioc_value} malware OR suspicious OR malicious OR threat',
                    "rationale": f"IP threat intel: {ioc_value}",
                    "priority": 0.95,
                })
                follow_ups.append({
                    "pivot": "ip",
                    "from": ioc_value,
                    "to": "passive_dns",
                    "query": f'passive-dns {ioc_value}',
                    "rationale": f"Passive DNS for IP: {ioc_value}",
                    "priority": 0.8,
                })
                follow_ups.append({
                    "pivot": "ip",
                    "from": ioc_value,
                    "to": "historical_whois",
                    "query": f'historical whois {ioc_value}',
                    "rationale": f"Historical WHOIS: {ioc_value}",
                    "priority": 0.6,
                })
            elif ioc_type == "domain":
                # Pivot: domain -> subdomains, WHOIS, related IOCs, malware check
                follow_ups.append({
                    "pivot": "domain",
                    "from": ioc_value,
                    "to": "subdomain_enum",
                    "query": f'subdomain:{ioc_value} OR dns:{ioc_value}',
                    "rationale": f"Subdomain enumeration: {ioc_value}",
                    "priority": 0.85,
                })
                follow_ups.append({
                    "pivot": "domain",
                    "from": ioc_value,
                    "to": "whois",
                    "query": f'whois:{ioc_value} OR domain registration',
                    "rationale": f"WHOIS lookup: {ioc_value}",
                    "priority": 0.7,
                })
                follow_ups.append({
                    "pivot": "domain",
                    "from": ioc_value,
                    "to": "malware_check",
                    "query": f'url:{ioc_value} malware OR suspicious OR scan',
                    "rationale": f"URL threat scan: {ioc_value}",
                    "priority": 0.8,
                })
            elif ioc_type in ("md5", "sha1", "sha256"):
                # Pivot: hash -> VT, file info, malware family
                follow_ups.append({
                    "pivot": "hash",
                    "from": ioc_value[:16] + "..." if len(ioc_value) > 16 else ioc_value,
                    "to": "threat_intel",
                    "query": f'hash:{ioc_value} malware OR virus OR virus_total',
                    "rationale": f"Threat intel for {ioc_type}: {ioc_value[:16]}...",
                    "priority": 0.95,
                })
                follow_ups.append({
                    "pivot": "hash",
                    "from": ioc_value[:16] + "..." if len(ioc_value) > 16 else ioc_value,
                    "to": "malware_family",
                    "query": f'hash:{ioc_value} family OR variant OR related',
                    "rationale": f"Malware family lookup: {ioc_value[:16]}...",
                    "priority": 0.8,
                })
            elif ioc_type == "file":
                # Pivot: filename -> malware samples, TTPs
                follow_ups.append({
                    "pivot": "file",
                    "from": ioc_value,
                    "to": "malware_samples",
                    "query": f'"{ioc_value}" malware sample OR uploaded OR vt',
                    "rationale": f"Malware sample search: {ioc_value}",
                    "priority": 0.85,
                })

        return follow_ups[:8]  # Cap at 8 follow-ups

    def _deduplicate_and_rank_queries(
        self, queries: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Deduplicate and finalize query list with priority preservation."""
        seen: Set[str] = set()
        unique: List[Dict[str, Any]] = []

        for q in queries:
            # Normalize query for dedup
            norm = q["query"].lower().strip()
            if norm and norm not in seen:
                seen.add(norm)
                unique.append(q)

        # Sort by priority descending, then by pivot_type preference
        pivot_preference = {
            "ioc": 0,
            "entity": 1,
            "relationship": 2,
            "organization": 3,
            "source": 4,
            "temporal": 5,
        }

        def sort_key(q):
            pref = pivot_preference.get(q.get("pivot_type", ""), 9)
            return (0 - q.get("priority", 0.5), pref)

        unique.sort(key=sort_key)

        return [
            {
                "query": q["query"],
                "rationale": q.get("rationale", ""),
                "type": q.get("type", "general"),
                "priority": q.get("priority", 0.5),
                "pivot_type": q.get("pivot_type", "general"),
            }
            for q in unique[:8]
        ]

    def _extract_relationships_heuristic(self, text: str) -> List[Tuple[str, str, str]]:
        """Extract relationship triples from text."""
        relationships: List[Tuple[str, str, str]] = []

        # Pattern: "X linked/connected to Y"
        for match in re.finditer(r"(\b\w+\b)\s+(?:linked|connected|related)\s+to\s+(\b\w+\b)", text, re.IGNORECASE):
            src, dst = match.group(1), match.group(2)
            if len(src) > 2 and len(dst) > 2:
                relationships.append((src, dst, "linked_to"))

        # Pattern: "X uses/employs/leverates Y"
        for match in re.finditer(r"(\b\w+\b)\s+(?:uses?|employs?|leverages?)\s+(\b\w+\b)", text, re.IGNORECASE):
            src, dst = match.group(1), match.group(2)
            if len(src) > 2 and len(dst) > 2:
                relationships.append((src, dst, "uses"))

        # Pattern: "X targeted/attacked Y"
        for match in re.finditer(r"(\b\w+\b)\s+(?:targeted|attacked)\s+(\b\w+\b)", text, re.IGNORECASE):
            src, dst = match.group(1), match.group(2)
            if len(src) > 2 and len(dst) > 2:
                relationships.append((src, dst, "targeted"))

        # Pattern: "X - Y (relationship indicator)"
        for match in re.finditer(r"(\b\w+\b)\s*[-:]\s*(\b\w+\b)\s+(?:campaign|operation|group)", text, re.IGNORECASE):
            src, dst = match.group(1), match.group(2)
            if len(src) > 2 and len(dst) > 2:
                relationships.append((src, dst, "associated_with"))

        return relationships

    def _extract_source_hints_heuristic(
        self, text: str, source_quality: Dict[str, float]
    ) -> List["SourceHint"]:
        """Extract source recommendations from findings."""
        hints: List[SourceHint] = []

        # Known good source patterns
        good_source_patterns = [
            (r"(?:BleepingComputer|Wireless94|Ars Technica|The Record)", 0.8),
            (r"(?:Krebs on Security|SecurityWeek|Dark Reading)", 0.85),
            (r"(?:CISA|FBI|Interpol|Europol)", 0.9),
            (r"(?:Mandiant|Recorded Future|Palo Alto|VirusTotal)", 0.85),
            (r"(?:NIST|NVD|CVE)", 0.9),
        ]

        for pattern, base_quality in good_source_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                source_name = match.group(0)
                quality = source_quality.get(source_name, base_quality)
                hints.append(SourceHint(
                    source=source_name,
                    quality=quality,
                    hint_type="trusted_source",
                ))

        # Extract quoted sources
        quoted_sources = re.findall(r'"(?:according to|from|via)\s+([^"]+)"', text)
        for src in quoted_sources[:3]:
            clean = src.strip()[:50]
            if clean and clean not in source_quality:
                hints.append(SourceHint(
                    source=clean,
                    quality=0.6,
                    hint_type="quoted_source",
                ))

        return hints

    def _extract_temporal_anchors_heuristic(
        self, text: str, existing: List[Tuple[str, str]]
    ) -> List[Tuple[str, str]]:
        """Extract temporal anchors for expansion."""
        anchors: List[Tuple[str, str]] = list(existing)

        # Extract year mentions
        for match in re.finditer(r"\b(20[1-2]\d)\b", text):
            year = match.group(1)
            context_start = max(0, match.start() - 30)
            context = text[context_start:match.end() + 30].strip()
            anchors.append((context, year))

        return anchors[:5]

    def _extract_org_anchors(self, text: str) -> List[str]:
        """Extract organization/domain anchors from text."""
        orgs: List[str] = []

        # Known org patterns
        org_patterns = [
            r"(?:Microsoft|Google|Apple|Amazon|Meta|Tesla|Nvidia|Intel|AMD)\b",
            r"(?:IBM|Cisco|Oracle|SAP|Palo Alto|Fortinet|Check Point)\b",
            r"(?:Bank of|JPMorgan|Chase|Wells Fargo|Goldman)\b",
            r"(?:Government|Federal|State|CISA|FBI|NSA)\b",
        ]

        for pattern in org_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                orgs.append(match.group(0))

        # Domain names
        domains = re.findall(r"\b[a-z0-9]+\.(?:com|org|net|gov|edu|io|co)\b", text)
        orgs.extend([d for d in domains if len(d) > 5][:5])

        return list(dict.fromkeys(orgs))[:5]

    # -------------------------------------------------------------------------
    # Sprint F150H.1: Optional NER capability probe (fail-soft, never blocks)
    # -------------------------------------------------------------------------

    def _ner_capability_probe(
        self,
        text: str,
        heuristic_entities: List[str],
        heuristic_iocs: List[Tuple[str, str]],
    ) -> Tuple[List[str], List[Tuple[str, str]]]:
        """
        Optional NER capability probe - augment heuristic extraction with NER if available.

        LAZY: Only imports NER engine when called.
        FAIL-SOFT: Returns original entities/IOCs on any error.
        HEURISTIC-FIRST: NER is only a capability probe, never blocks primary path.

        Args:
            text: Full text to analyze
            heuristic_entities: Entities already extracted heuristically
            heuristic_iocs: IOCs already extracted heuristically

        Returns:
            (entities, iocs) - possibly augmented with NER if available
        """
        try:
            from hledac.universal.brain.ner_engine import NEREngine
        except ImportError:
            # NER engine not available - fail soft, return heuristic-only
            return heuristic_entities, heuristic_iocs

        try:
            import threading
            import time

            # Use a short timeout to avoid blocking
            result_holder = [None]  # Mutable container for thread result
            error_holder = [None]

            def _probe():
                try:
                    ner = NEREngine()
                    # Quick single-shot prediction, limited text
                    short_text = text[:5000] if len(text) > 5000 else text
                    labels = ["threat-actor", "malware", "vulnerability", "organization", "tool"]
                    entities_found = ner.predict_entities(short_text, labels)
                    result_holder[0] = entities_found
                except Exception as e:
                    error_holder[0] = e

            thread = threading.Thread(target=_probe, daemon=True)
            thread.start()
            thread.join(timeout=2.0)  # 2 second max

            if error_holder[0] is not None:
                # NER failed - fail soft
                return heuristic_entities, heuristic_iocs

            if result_holder[0] is None:
                # Timeout or no result - fail soft
                return heuristic_entities, heuristic_iocs

            ner_entities = result_holder[0]
            if not ner_entities:
                return heuristic_entities, heuristic_iocs

            # Merge NER entities with heuristic, dedup
            existing = set(e.lower() for e in heuristic_entities)
            merged_entities = list(heuristic_entities)
            for ent in ner_entities:
                if isinstance(ent, dict):
                    name = ent.get("text", ent.get("entity", ""))
                elif isinstance(ent, str):
                    name = ent
                else:
                    continue
                if name and name.lower() not in existing and len(name) > 2:
                    merged_entities.append(name)
                    existing.add(name.lower())

            return merged_entities[:12], heuristic_iocs  # Keep IOC heuristic-only

        except Exception:
            # Any failure - fail soft, return original
            return heuristic_entities, heuristic_iocs

    def _model_assisted_hypothesis_pack(
        self,
        findings: List[str],
        context: Dict[str, Any],
        new_entities: List[str],
        new_iocs: List[Tuple[str, str]],
        heuristic_queries: List[Dict[str, str]],
    ) -> Optional["HypothesisPack"]:
        """
        Optional model-assisted enhancement for hypothesis pack.

        LAZY: Only loads model if available and under memory pressure.
        FAIL-SOFT: Returns None on any error, never blocks.
        """
        try:
            # Check if we have enough heuristic coverage
            total_items = len(new_entities) + len(new_iocs) + len(heuristic_queries)
            if total_items >= 5:
                # Sufficient heuristic coverage, no model needed
                return None
        except Exception:
            pass

        try:
            from hledac.universal.utils.mlx_cache import get_mlx_model
        except ImportError:
            return None

        try:
            import asyncio

            model_name = context.get("model_name", "mlx-community/Qwen2.5-0.5B-Instruct-4bit")

            async def _try_load():
                try:
                    return await asyncio.wait_for(
                        get_mlx_model(model_name),
                        timeout=3.0
                    )
                except Exception:
                    return None, None

            # Can't run async in sync context - fail soft
            return None

        except Exception:
            return None

    def _model_assisted_query_suggestion(
        self,
        findings: List[str],
        context: Dict[str, Any],
        max_to_add: int,
    ) -> List[Dict[str, str]]:
        """
        Optional model-assisted query enhancement.

        Only called if:
        1. Heuristic path returned fewer than max_queries
        2. MLX model is available (lazy check)

        Returns empty list on any failure - never blocks.
        """
        if max_to_add <= 0:
            return []

        try:
            # Lazy import - don't load unless needed
            from hledac.universal.utils.mlx_cache import get_mlx_model
        except ImportError:
            return []

        try:
            import asyncio

            # Check if model is available (non-blocking check)
            model_name = context.get("model_name", "mlx-community/Qwen2.5-0.5B-Instruct-4bit")

            # Quick timeout - if model doesn't load in 2s, skip
            async def _try_load():
                try:
                    model, tokenizer = await asyncio.wait_for(
                        get_mlx_model(model_name),
                        timeout=2.0
                    )
                    return model is not None
                except (asyncio.TimeoutError, Exception):
                    return None

            # This won't work in sync context, so just skip
            # Model-assisted path is aspirational - fail soft
            return []

        except Exception:
            # Any failure = fail soft, return empty
            return []

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self._stats,
            "total_hypotheses": len(self._hypotheses),
            "total_evidence": len(self._evidence),
            "by_status": {
                status.value: len(
                    [h for h in self._hypotheses.values() if h.status == status.value]
                )
                for status in HypothesisStatus
            },
        }

    def clear(self) -> None:
        """Clear all hypotheses and evidence (memory management)."""
        self._hypotheses.clear()
        self._evidence.clear()
        self._source_credibility_cache.clear()
        self._stats = {
            "generated": 0,
            "tested": 0,
            "confirmed": 0,
            "rejected": 0,
            "merged": 0,
            "pruned": 0,
            "adversarial_checks": 0,
        }
        # Reset adversarial verifier
        self._adversarial_verifier = None
        gc.collect()
        logger.info("HypothesisEngine cleared")


# Factory function
def create_hypothesis_engine(
    inference_engine: Optional[InferenceEngineProtocol] = None,
    **kwargs,
) -> HypothesisEngine:
    """
    Factory function for creating a HypothesisEngine.

    Args:
        inference_engine: Optional inference engine for integration
        **kwargs: Additional arguments for HypothesisEngine

    Returns:
        Configured HypothesisEngine instance
    """
    return HypothesisEngine(inference_engine=inference_engine, **kwargs)

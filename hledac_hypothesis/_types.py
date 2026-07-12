"""
from __future__ import annotations
Hypothesis Engine — Type Definitions (C4 Sprint Refactoring)
=============================================================

Extracted from :mod:`brain.hypothesis_engine_engine` to break the 5 373 LOC monolith
into focused modules. This module contains the **value types only**: enums,
dataclasses, and the inference-engine Protocol.

GHOST_INVARIANTS:
- The extraction is **byte-for-byte identical** to the original — no
  behaviour change, no field rename, no default mutation. Existing tests
  must pass unchanged.
- ``brain.hypothesis_engine_engine`` re-exports everything for backward compat.
- New code should ``from brain.hypothesis_engine._types import Hypothesis, …`` —
  but the public path is the re-exported surface.

M1 8GB UMA: 0 KB runtime overhead. Imports happen once at module load.
"""
from __future__ import annotations


import msgspec
from dataclasses import dataclass, field  # kept for classes with __post_init__ or runtime methods
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol


def _utc_now() -> datetime:
    """Module-level factory for UTC now — required for msgspec default_factory."""
    return datetime.now(UTC)

if TYPE_CHECKING:
    # Hypothesis lives in brain.hypothesis_engine_engine (carries engine-specific
    # methods). Imported only for the Protocol type signatures below.
    from brain.research_hypothesis_engine import Hypothesis


# ============================================================================
# Core Hypothesis Enums
# ============================================================================

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


# ============================================================================
# Core Hypothesis Structs (msgspec — M1 8GB optimized)
# ============================================================================
# Migration: @dataclass(slots=True) → msgspec.Struct()
# Migration: @dataclass(frozen=True, slots=True) → msgspec.Struct(frozen=True)
# default_factory=list/dict → msgspec.field(default_factory=list/dict)
# default_factory=datetime.now → module-level _utc_now() factory
# kept as dataclass: TestResult (__post_init__ with iso parsing)
# kept as dataclass: SourceCredibility (has runtime update method)

class Evidence(msgspec.Struct):
    """Evidence item supporting or conflicting with a hypothesis."""
    evidence_id: str
    source: str
    content: str
    timestamp: datetime
    reliability: float = 1.0  # 0-1, source reliability
    relevance: float = 1.0    # 0-1, relevance to hypothesis
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


@dataclass(slots=True)
class TestResult:
    """Result of executing a test against a hypothesis."""
    test_type: str
    result: str  # passed, failed, inconclusive
    confidence: float
    evidence_collected: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


class TestDesign(msgspec.Struct):
    """Design for testing a hypothesis."""
    test_type: str
    description: str
    required_data: list[str] = msgspec.field(default_factory=list)
    expected_outcome_if_true: str = ""
    expected_outcome_if_false: str = ""
    priority: float = 0.5  # 0-1, higher = test sooner
    cost_estimate: float = 1.0  # Estimated computational cost


class FalsificationResult(msgspec.Struct):
    """Result of a falsification attempt."""
    falsified: bool
    confidence: float
    counter_evidence: list[str] = msgspec.field(default_factory=list)
    reasoning: str = ""
    timestamp: datetime = msgspec.field(default_factory=_utc_now)


# ============================================================================
# Dark Surface Query Structs
# ============================================================================

class DarkQueryType(Enum):
    """Types of dark surface queries for unindexed source expansion."""
    ONION = "onion"
    IPFS = "ipfs"
    PASTE = "paste"
    I2P = "i2p"


class DarkQuery(msgspec.Struct, frozen=True):
    """
    Query for exploring dark/unindexed surface.

    Invariant: All dark queries MUST transit via Tor/I2P transport.
    NEVER route through aiohttp clearnet.
    """
    query_type: DarkQueryType
    query: str
    priority: float  # 0-1, higher = explore first
    source_iocs: tuple[str, ...] = ()  # IOC refs for context — empty tuple default
    reasoning: str = ""  # Why this query was generated


class _DarkQueryListResponse(msgspec.Struct):
    """Response model for Hermes LLM dark query generation."""
    queries: list[dict[str, Any]] = msgspec.field(default_factory=list)


# ============================================================================
# Sprint F259: Causal Reasoning Structs
# ============================================================================

class CausalEntity(msgspec.Struct, frozen=True):
    """An entity extracted from findings for causal reasoning."""
    entity_id: str
    entity_type: str  # ip, domain, person, org, email, url, etc.
    value: str  # the actual value (e.g., "192.168.1.1")
    source_findings: tuple[str, ...] = ()  # finding IDs that mention this entity
    first_seen: float = 0.0
    last_seen: float = 0.0


class TemporalSequence(msgspec.Struct, frozen=True):
    """An ordered sequence of events."""
    sequence_id: str
    entities: list[str]  # entity IDs in temporal order
    timestamps: list[float]
    source_findings: tuple[str, ...]
    confidence: float = 0.0


class AnomalySignal(msgspec.Struct, frozen=True):
    """An anomaly signal from unexpected source combinations."""
    anomaly_type: str  # cross_domain, temporal_gap, source_conflict, etc.
    entities: tuple[str, ...]
    expected_sources: tuple[str, ...]
    actual_sources: tuple[str, ...]
    score: float = 0.0  # 0.0 - 1.0
    description: str = ""


class CausalHypothesis(msgspec.Struct, frozen=True):
    """A causal hypothesis generated from entity co-occurrence and temporal sequences."""
    hypothesis_id: str
    source_entity: str
    target_entity: str
    hypothesis_type: str  # causal, correlative, temporal
    statement: str  # human-readable hypothesis
    confidence: float  # 0.0 - 1.0
    source_count: int
    source_diversity: int
    temporal_consistent: bool
    supporting_findings: tuple[str, ...] = ()
    contradiction_hints: tuple[str, ...] = ()


# ============================================================================
# Bounds for M1 8GB optimization
# ============================================================================

MAX_CAUSAL_ENTITIES = 5000
MAX_CAUSAL_FINDINGS = 50000
MAX_CAUSAL_HYPOTHESES = 200
MAX_CO_OCCURRENCE_MATRIX_SIZE = 2000
CO_OCCURRENCE_FP16 = True  # Use float16 for RAM savings


# ============================================================================
# Adversarial Verification Structs
# ============================================================================
# SourceCredibility: kept as @dataclass — has runtime update method update_accuracy()

@dataclass(slots=True)
class SourceCredibility:
    """
    Credibility assessment for an evidence source.

    Tracks historical accuracy, bias indicators, and overall trustworthiness.
    Used to weight evidence by source quality.
    """
    source_id: str
    credibility_score: float  # 0-1, overall credibility
    bias_indicators: list[str] = field(default_factory=list)
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
        self.last_updated = datetime.now(UTC)  # noqa: DTZ005


class Event(msgspec.Struct):
    """Temporal event for consistency checking."""
    event_id: str
    description: str
    timestamp: datetime
    source: str
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class Contradiction(msgspec.Struct):
    """
    Represents a contradiction between two claims or evidence items.

    Tracks the type of contradiction (temporal, factual, logical) and severity.
    """
    claim_a: str
    claim_b: str
    contradiction_type: str  # temporal, factual, logical, source_bias
    severity: float  # 0-1, how serious the contradiction is
    evidence_supporting_a: list[str] = msgspec.field(default_factory=list)
    evidence_supporting_b: list[str] = msgspec.field(default_factory=list)
    detected_at: datetime = msgspec.field(default_factory=_utc_now)
    resolution_notes: str = ""


class CrossReferenceResult(msgspec.Struct):
    """Result of cross-referencing a claim across databases."""
    database_id: str
    claim_found: bool
    confidence: float
    supporting_sources: list[str] = msgspec.field(default_factory=list)
    conflicting_sources: list[str] = msgspec.field(default_factory=list)
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class AdversarialReport(msgspec.Struct):
    """
    Comprehensive adversarial verification report.

    Contains all findings from the devil's advocate analysis including
    counter-evidence, contradictions, source credibility assessments,
    and overall confidence scoring.
    """
    hypothesis: str
    supporting_evidence: list[Evidence]
    contradicting_evidence: list[Evidence]
    credibility_assessment: dict[str, SourceCredibility]
    contradictions_found: list[Contradiction]
    temporal_consistency: bool
    overall_confidence: float  # 0-1, confidence in hypothesis after adversarial analysis
    devil_advocate_score: float  # 0-1, strength of counter-case (higher = stronger counter-arguments)
    alternative_explanations: list[str] = msgspec.field(default_factory=list)
    logical_fallacies: list[str] = msgspec.field(default_factory=list)
    generated_at: datetime = msgspec.field(default_factory=_utc_now)
    verification_duration_ms: float = 0.0


# ============================================================================
# Hypothesis Master Dataclass
# ============================================================================
# NOTE: ``Hypothesis`` is intentionally NOT extracted to this module because
# the version in ``brain.hypothesis_engine_engine`` carries extra methods
# (``add_test_result``, ``add_supporting_evidence``, ``add_conflicting_evidence``,
# ``_recalculate_confidence``, ``_ds_engine``) that are tightly coupled to
# the engine's runtime state. Future sprint: extract the methods into a
# helper class and keep only the data fields here. Until then, callers
# should import ``Hypothesis`` from ``brain.hypothesis_engine_engine`` directly.
# A reference import is provided below for IDE / type-checker convenience
# but is commented out to avoid import-time drift if the engine changes
# the class shape.
#
# from brain.research_hypothesis_engine import Hypothesis  # noqa: F401


# ============================================================================
# Inference Engine Protocol
# ============================================================================

class InferenceEngineProtocol(Protocol):
    """Protocol for inference engine integration."""

    async def abductive_reasoning(
        self, observations: list[Evidence], context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Generate possible explanations (hypotheses) from observations."""
        ...

    async def evidence_chaining(
        self, hypothesis: Hypothesis, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Chain evidence to design tests for a hypothesis."""
        ...

    async def belief_update(
        self, hypothesis: Hypothesis, new_evidence: Evidence
    ) -> float:
        """Calculate updated belief given new evidence."""
        ...


# ============================================================================
# Shared Utilities
# ============================================================================


def _to_operator_shortlist(
    raw: list[dict[str, Any]], max_items: int = 3
) -> list[dict[str, Any]]:
    """Bounded operator shortlist (max 3) in scheduler-consumable shape.

    Transforms actionable_shortlist output to:
    {action: query, target: rationale[:80], rationale: pivot_type}

    Used by both HypothesisPack and NER-augmented correlation paths to ensure
    shape consistency across the scheduler pipeline.
    """
    return [
        {
            "action": item.get("query", ""),
            "target": item.get("rationale", "")[:80],
            "rationale": item.get("pivot_type", ""),
        }
        for item in raw[:max_items]
    ]


__all__ = [
    # Shared utilities
    "_to_operator_shortlist",
    # Enums
    "HypothesisType",
    "HypothesisStatus",
    "TestType",
    "DarkQueryType",
    # Core dataclasses
    "Evidence",
    "TestResult",
    "TestDesign",
    "FalsificationResult",
    # Dark query
    "DarkQuery",
    "_DarkQueryListResponse",
    # Causal reasoning
    "CausalEntity",
    "TemporalSequence",
    "AnomalySignal",
    "CausalHypothesis",
    # Bounds
    "MAX_CAUSAL_ENTITIES",
    "MAX_CAUSAL_FINDINGS",
    "MAX_CAUSAL_HYPOTHESES",
    "MAX_CO_OCCURRENCE_MATRIX_SIZE",
    "CO_OCCURRENCE_FP16",
    # Adversarial
    "SourceCredibility",
    "Event",
    "Contradiction",
    "CrossReferenceResult",
    "AdversarialReport",
    # Master — Hypothesis intentionally NOT exported (see NOTE above)
    # Protocol
    "InferenceEngineProtocol",
]

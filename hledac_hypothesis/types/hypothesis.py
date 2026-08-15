"""
Hypothesis core types — hledac_hypothesis.types.hypothesis
=========================================================

Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol
from core import aclose

if TYPE_CHECKING:
    # Hypothesis lives in brain.research_hypothesis_engine (carries engine-specific
    # methods). Imported only for the Protocol type signatures below.
    from hledac.universal.brain.research_hypothesis_engine import Hypothesis


class HypothesisType(Enum):
    """Types of hypotheses supported by the engine."""
    EXISTENCE = "existence"           # Does X exist?
    RELATIONSHIP = "relationship"     # Is A connected to B?
    CAUSAL = "causal"                 # Does X cause Y?
    IDENTITY = "identity"            # Is X the same as Y?
    TEMPORAL = "temporal"            # Did X happen before Y?


class HypothesisStatus(Enum):
    """Status of a hypothesis in its lifecycle."""
    ACTIVE = "active"                 # Currently being tested
    CONFIRMED = "confirmed"          # Sufficient evidence supports it
    REJECTED = "rejected"            # Falsified or insufficient support
    PENDING = "pending"              # Awaiting testing
    MERGED = "merged"                # Merged with another hypothesis


class InferenceEngineProtocol(Protocol):
    """Protocol for inference engine integration."""

    async def abductive_reasoning(
        self, observations: list[object], context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Generate possible explanations (hypotheses) from observations."""
        ...

    async def evidence_chaining(
        self, hypothesis: object, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Chain evidence to design tests for a hypothesis."""
        ...

    async def belief_update(
        self, hypothesis: object, new_evidence: object
    ) -> float:
        """Calculate updated belief given new evidence."""
        ...


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
    "HypothesisType",
    "HypothesisStatus",
    "InferenceEngineProtocol",
    "_to_operator_shortlist",
]

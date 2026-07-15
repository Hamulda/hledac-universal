"""
Anomaly and adversarial types — hledac_hypothesis.types.anomaly
==============================================================

Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import msgspec


def _utc_now() -> datetime:
    """Module-level factory for UTC now — required for msgspec default_factory."""
    return datetime.now(UTC)


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
    supporting_evidence: list[object]  # Evidence items
    contradicting_evidence: list[object]  # Evidence items
    credibility_assessment: dict[str, object]  # SourceCredibility
    contradictions_found: list[Contradiction]
    temporal_consistency: bool
    overall_confidence: float  # 0-1, confidence in hypothesis after adversarial analysis
    devil_advocate_score: float  # 0-1, strength of counter-case (higher = stronger counter-arguments)
    alternative_explanations: list[str] = msgspec.field(default_factory=list)
    logical_fallacies: list[str] = msgspec.field(default_factory=list)
    generated_at: datetime = msgspec.field(default_factory=_utc_now)
    verification_duration_ms: float = 0.0


__all__ = [
    "Contradiction",
    "CrossReferenceResult",
    "AdversarialReport",
]

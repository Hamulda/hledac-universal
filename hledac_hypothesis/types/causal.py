"""
Causal reasoning types — hledac_hypothesis.types.causal
=======================================================





Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
M1 8GB: CO_OCCURRENCE_FP16=True saves RAM on co-occurrence matrices.
"""

from __future__ import annotations

from typing import Any

import msgspec
from _core import aclose

from compat.msgspec_gc_compat import Struct


# ============================================================================
# Bounds for M1 8GB optimization
# ============================================================================

MAX_CAUSAL_ENTITIES = 5000
MAX_CAUSAL_FINDINGS = 50000
MAX_CAUSAL_HYPOTHESES = 200
MAX_CO_OCCURRENCE_MATRIX_SIZE = 2000
CO_OCCURRENCE_FP16 = True  # Use float16 for RAM savings


class CausalEntity(Struct, frozen=True):
    """An entity extracted from findings for causal reasoning."""
    entity_id: str
    entity_type: str  # ip, domain, person, org, email, url, etc.
    value: str  # the actual value (e.g., "192.168.1.1")
    source_findings: tuple[str, ...] = ()  # finding IDs that mention this entity
    first_seen: float = 0.0
    last_seen: float = 0.0


class TemporalSequence(Struct, frozen=True):
    """An ordered sequence of events."""
    sequence_id: str
    entities: list[str]  # entity IDs in temporal order
    timestamps: list[float]
    source_findings: tuple[str, ...]
    confidence: float = 0.0


class AnomalySignal(Struct, frozen=True):
    """An anomaly signal from unexpected source combinations."""
    anomaly_type: str  # cross_domain, temporal_gap, source_conflict, etc.
    entities: tuple[str, ...]
    expected_sources: tuple[str, ...]
    actual_sources: tuple[str, ...]
    score: float = 0.0  # 0.0 - 1.0
    description: str = ""


class CausalHypothesis(Struct, frozen=True):
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


__all__ = [
    # Bounds
    "MAX_CAUSAL_ENTITIES",
    "MAX_CAUSAL_FINDINGS",
    "MAX_CAUSAL_HYPOTHESES",
    "MAX_CO_OCCURRENCE_MATRIX_SIZE",
    "CO_OCCURRENCE_FP16",
    # Types
    "CausalEntity",
    "TemporalSequence",
    "AnomalySignal",
    "CausalHypothesis",
]

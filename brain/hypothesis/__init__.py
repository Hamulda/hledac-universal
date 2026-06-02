"""
Hypothesis Engine — Package Entry Point (C4 Sprint Refactoring)
=================================================================

Splits the 5 373 LOC monolith ``brain/hypothesis_engine.py`` into focused
submodules. The ``brain.hypothesis_engine`` module re-exports every public
symbol from here for **backward compatibility** — existing imports
(``from brain.hypothesis_engine import Hypothesis, …``) keep working.

Module layout (planned, incremental):
- ``_types``     — enums + dataclass DTOs + Protocol (extracted: this commit)
- ``adversarial`` — AdversarialVerifier (~840 LOC; planned)
- ``explainer``  — SimpleNodeAblationExplainer (~140 LOC; planned)
- ``packs``      — SourceHint + HypothesisPack (~713 LOC; planned)
- ``engine``     — HypothesisEngine (~3 124 LOC; planned)

GHOST_INVARIANTS:
- Every public symbol that used to live in ``hypothesis_engine`` is still
  re-exported from there. No call site needs to change.
- New code should prefer ``from brain.hypothesis._types import Hypothesis``.
- Submodule extraction is byte-for-byte; field names, defaults, and
  ordering are preserved exactly.
"""
from __future__ import annotations

from ._types import (
    # Enums
    HypothesisType,
    HypothesisStatus,
    TestType,
    DarkQueryType,
    # Core dataclasses
    Evidence,
    TestResult,
    TestDesign,
    FalsificationResult,
    # Dark query
    DarkQuery,
    _DarkQueryListResponse,
    # Causal reasoning
    CausalEntity,
    TemporalSequence,
    AnomalySignal,
    CausalHypothesis,
    # Bounds
    MAX_CAUSAL_ENTITIES,
    MAX_CAUSAL_FINDINGS,
    MAX_CAUSAL_HYPOTHESES,
    MAX_CO_OCCURRENCE_MATRIX_SIZE,
    CO_OCCURRENCE_FP16,
    # Adversarial
    SourceCredibility,
    Event,
    Contradiction,
    CrossReferenceResult,
    AdversarialReport,
    # Protocol
    InferenceEngineProtocol,
)

__all__ = [
    "HypothesisType",
    "HypothesisStatus",
    "TestType",
    "DarkQueryType",
    "Evidence",
    "TestResult",
    "TestDesign",
    "FalsificationResult",
    "DarkQuery",
    "_DarkQueryListResponse",
    "CausalEntity",
    "TemporalSequence",
    "AnomalySignal",
    "CausalHypothesis",
    "MAX_CAUSAL_ENTITIES",
    "MAX_CAUSAL_FINDINGS",
    "MAX_CAUSAL_HYPOTHESES",
    "MAX_CO_OCCURRENCE_MATRIX_SIZE",
    "CO_OCCURRENCE_FP16",
    "SourceCredibility",
    "Event",
    "Contradiction",
    "CrossReferenceResult",
    "AdversarialReport",
    "InferenceEngineProtocol",
]

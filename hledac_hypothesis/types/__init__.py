"""
hledac_hypothesis/types/ — focused type submodules
===================================================

Split from ``hledac_hypothesis._types`` (C4 Sprint Refactoring).

Six modules:
    evidence   — Evidence, SourceCredibility, Event
    test       — TestType, TestResult, TestDesign, FalsificationResult
    query      — DarkQueryType, DarkQuery, _DarkQueryListResponse
    causal     — CausalEntity, TemporalSequence, AnomalySignal,
                 CausalHypothesis + bounds constants
    hypothesis — HypothesisType, HypothesisStatus, InferenceEngineProtocol
    anomaly    — Contradiction, CrossReferenceResult, AdversarialReport

GHOST_INVARIANTS:
- Byte-for-byte identical to original _types definitions — no behaviour change
- All types remain importable from hledac_hypothesis._types (backward compat)
- New canonical path: ``from hledac_hypothesis.types.evidence import Evidence``
- M1 8GB: 0 KB runtime overhead — imports are lazy, module load is one-time

.. rubric:: Backward compat

``hledac_hypothesis._types`` re-exports everything so existing callers
(e.g. ``from hledac_hypothesis._types import Evidence``) continue to work
without modification.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Import directly from submodules — DO NOT import from _types (circular)
from .evidence import (
    Evidence,
    SourceCredibility,
    Event,
)

from .test import (
    TestType,
    TestResult,
    TestDesign,
    FalsificationResult,
)

from .query import (
    DarkQueryType,
    DarkQuery,
    _DarkQueryListResponse,
)

from .causal import (
    CausalEntity,
    TemporalSequence,
    AnomalySignal,
    CausalHypothesis,
    MAX_CAUSAL_ENTITIES,
    MAX_CAUSAL_FINDINGS,
    MAX_CAUSAL_HYPOTHESES,
    MAX_CO_OCCURRENCE_MATRIX_SIZE,
    CO_OCCURRENCE_FP16,
)

from .hypothesis import (
    HypothesisType,
    HypothesisStatus,
    InferenceEngineProtocol,
    _to_operator_shortlist,
)






    Contradiction,
    CrossReferenceResult,
    AdversarialReport,
)


__all__ = [
    # Enums
    "HypothesisType",

from _core import aclose    "HypothesisStatus",
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
    # Protocol
    "InferenceEngineProtocol",
    # Utilities
    "_to_operator_shortlist",
]

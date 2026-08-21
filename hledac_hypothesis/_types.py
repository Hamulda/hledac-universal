"""
hledac_hypothesis._types — backward-compat re-export layer
===========================================================

.. deprecated::
    ``hledac_hypothesis._types`` is kept for backward compatibility.
    New code should import directly from the split submodules::

        from hledac_hypothesis.types.evidence import Evidence
        from hledac_hypothesis.types.test import TestType, TestResult
        from hledac_hypothesis.types.query import DarkQuery, DarkQueryType
        from hledac_hypothesis.types.causal import CausalEntity, CausalHypothesis
        from hledac_hypothesis.types.hypothesis import HypothesisType, HypothesisStatus
        from hledac_hypothesis.types.anomaly import Contradiction, AdversarialReport

M1 8GB: 0 KB runtime overhead — all imports are lazy module refs.
GHOST_INVARIANTS:
- Byte-for-byte identical type definitions (backward compat)
- All original ``__all__`` names preserved
- New canonical path: ``hledac_hypothesis.types.*``
"""

from __future__ import annotations

# Re-export everything from the new types/ subpackage
from hledac_hypothesis.types import (
    CO_OCCURRENCE_FP16,
    # Bounds
    MAX_CAUSAL_ENTITIES,
    MAX_CAUSAL_FINDINGS,
    MAX_CAUSAL_HYPOTHESES,
    MAX_CO_OCCURRENCE_MATRIX_SIZE,
    AdversarialReport,
    AnomalySignal,
    # Causal reasoning
    CausalEntity,
    CausalHypothesis,
    Contradiction,
    CrossReferenceResult,
    # Dark query
    DarkQuery,
    DarkQueryType,
    Event,
    # Core dataclasses
    Evidence,
    FalsificationResult,
    HypothesisStatus,
    # Enums
    HypothesisType,
    # Protocol
    InferenceEngineProtocol,
    # Adversarial
    SourceCredibility,
    TemporalSequence,
    TestDesign,
    TestResult,
    TestType,
    _DarkQueryListResponse,
    # Utilities
    _to_operator_shortlist,
)

__all__ = [
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
    # Protocol
    "InferenceEngineProtocol",
    # Utilities
    "_to_operator_shortlist",
]

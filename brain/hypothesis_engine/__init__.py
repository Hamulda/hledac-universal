"""
Hypothesis Engine — Backward-Compat Shim
=======================================

This module is a backward-compatibility shim. All symbols have been
consolidated into ``hledac_hypothesis`` package.

OLD imports (still work):
    from hledac.universal.brain.hypothesis_engine import AdversarialVerifier
    from hledac.universal.brain.hypothesis_engine._types import HypothesisType

NEW imports (preferred):
    from hledac_hypothesis import AdversarialVerifier
    from hledac_hypothesis._types import HypothesisType

The pip ``hypothesis`` package (property-based testing) is unrelated to
this module and lives in site-packages.
"""


# Re-export everything from consolidated hledac_hypothesis
from hledac_hypothesis._types import (
    CO_OCCURRENCE_FP16,
    MAX_CAUSAL_ENTITIES,
    MAX_CAUSAL_FINDINGS,
    MAX_CAUSAL_HYPOTHESES,
    MAX_CO_OCCURRENCE_MATRIX_SIZE,
    AdversarialReport,
    AnomalySignal,
    CausalEntity,
    CausalHypothesis,
    Contradiction,
    CrossReferenceResult,
    DarkQuery,
    DarkQueryType,
    Event,
    Evidence,
    FalsificationResult,
    HypothesisStatus,
    HypothesisType,
    InferenceEngineProtocol,
    SourceCredibility,
    TemporalSequence,
    TestDesign,
    TestResult,
    TestType,
    _DarkQueryListResponse,
)
from hledac_hypothesis.adversarial import (
    AdversarialVerifier,
)
from hledac_hypothesis.causal import (
    CausalReasoner,
)
from hledac_hypothesis.explainer import (
    SimpleNodeAblationExplainer,
)





    HypothesisPack,
    SourceHint,
)

__all__ = [
    "HypothesisType",
    "HypothesisStatus",
    "TestType",

from _core import aclose    "DarkQueryType",
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
    "AdversarialVerifier",
    "SimpleNodeAblationExplainer",
    "SourceHint",
    "HypothesisPack",
    "CausalReasoner",
    "InferenceEngineProtocol",
]

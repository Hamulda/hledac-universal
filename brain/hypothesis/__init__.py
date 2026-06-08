"""
Hypothesis Engine — Package Entry Point (C4 Sprint Refactoring)
=================================================================

Splits the 5 373 LOC monolith ``brain/research_hypothesis_engine.py`` into focused
submodules. The ``brain.hypothesis_engine`` module re-exports every public
symbol from here for **backward compatibility** — existing imports
(``from brain.research_hypothesis_engine import Hypothesis, …``) keep working.

Module layout (planned, incremental):
- ``_types``     — enums + dataclass DTOs + Protocol (C4 Tier-1+2, extracted)
- ``adversarial`` — AdversarialVerifier (837 LOC, C4 Tier-3 partial, extracted)
- ``explainer``  — SimpleNodeAblationExplainer (78 LOC, C4 Tier-3 partial, extracted)
- ``packs``      — SourceHint + HypothesisPack (711 LOC, C4 Tier-4, extracted)
- ``engine``     — HypothesisEngine (~3 126 LOC; planned)

GHOST_INVARIANTS:
- Every public symbol that used to live in ``hypothesis_engine`` is still
  re-exported from there. No call site needs to change.
- New code should prefer
  ``from brain.hypothesis._types import Hypothesis``,
  ``from brain.hypothesis.adversarial import AdversarialVerifier``,
  ``from brain.hypothesis.explainer import SimpleNodeAblationExplainer``,
  ``from brain.hypothesis.packs import SourceHint, HypothesisPack``.
- Submodule extraction is byte-for-byte; field names, defaults, and
  ordering are preserved exactly.
- ``explain_with_mlx`` (the MLX-LM companion helper) is **not** part of
  this package — it lives in ``brain.hypothesis_engine`` as a module-level
  function and is imported lazily by ``AdversarialVerifier`` when path
  explanations are requested.
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
from .adversarial import (
    AdversarialVerifier,
)
from .explainer import (
    SimpleNodeAblationExplainer,
)
from .packs import (
    SourceHint,
    HypothesisPack,
)
from .causal import (
    CausalReasoner,
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
    "AdversarialVerifier",
    "SimpleNodeAblationExplainer",
    "SourceHint",
    "HypothesisPack",
    "CausalReasoner",
    "InferenceEngineProtocol",
]

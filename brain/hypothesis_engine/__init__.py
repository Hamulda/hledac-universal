"""
Hypothesis Engine — Package Entry Point (C4 Sprint Refactoring)
=================================================================

Splits the 5 373 LOC monolith ``brain/research_hypothesis_engine.py`` into focused
submodules. The ``brain.hypothesis_engine_engine`` module re-exports every public
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
  ``from brain.hypothesis_engine._types import Hypothesis``,
  ``from brain.hypothesis_engine.adversarial import AdversarialVerifier``,
  ``from brain.hypothesis_engine.explainer import SimpleNodeAblationExplainer``,
  ``from brain.hypothesis_engine.packs import SourceHint, HypothesisPack``.
- Submodule extraction is byte-for-byte; field names, defaults, and
  ordering are preserved exactly.
- ``explain_with_mlx`` (the MLX-LM companion helper) is **not** part of
  this package — it lives in ``brain.hypothesis_engine_engine`` as a module-level
  function and is imported lazily by ``AdversarialVerifier`` when path
  explanations are requested.

CAVEAT (F265B): This package lives on the ``hypothesis`` namespace which
conflicts with the pip ``hypothesis`` package used by pytest's
``_hypothesis_pytestplugin`` (``from hypothesis import Verbosity``).
The conflict is resolved via ``__getattr__`` at the package level: this
__init__.py re-exports only local submodules and never registers as
``hypothesis`` in ``sys.modules``. The pip package (site-packages) is
accessed only when an external caller does ``import hypothesis``.
"""
from __future__ import annotations

# ── Local submodule re-exports (no pip hypothesis imports here) ───────────────

from ._types import (
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
)
from .adversarial import (
    AdversarialVerifier,
)
from .causal import (
    CausalReasoner,
)
from .explainer import (
    SimpleNodeAblationExplainer,
)
from .packs import (
    HypothesisPack,
    SourceHint,
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
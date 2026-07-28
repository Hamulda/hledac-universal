"""
hledac_hypothesis — OSINT Hypothesis Generation Package
=====================================================

Consolidated home for all OSINT hypothesis generation and causal reasoning
code. Previously spread across ``brain/hypothesis_engine/`` submodules.

Package Structure
-----------------
- ``_types.py``          — Enums, dataclasses, protocols (HypothesisType, Evidence, etc.)
- ``adversarial.py``     — AdversarialVerifier (Devil's Advocate falsification)
- ``causal.py``          — CausalReasoner (entity extraction, temporal sequencing)
- ``explainer.py``       — SimpleNodeAblationExplainer + explain_with_mlx
- ``packs.py``           — HypothesisPack + SourceHint (bounded query packs)
- ``dempster_shafer.py`` — Dempster-Shafer evidence theory
- ``eig.py``             — Evidence Inference Graph
- ``hypothesisgenerator.py`` — HypothesisGenerator (heuristic + DSPy)

Naming Conflict Resolution
-------------------------
The pip ``hypothesis`` package (property-based testing) is unrelated.
All homegrown OSINT hypothesis code lives in this package (``hledac_hypothesis``),
not in a package named ``hypothesis``.

Backward Compatibility
---------------------
``brain/hypothesis_engine/`` is a backward-compat shim that re-exports from
this package. Existing imports continue to work:
    from hledac.universal.brain.hypothesis_engine import AdversarialVerifier  # OK (shim)
    from hledac_hypothesis import AdversarialVerifier      # Preferred

Canonical Imports (NEW)
----------------------
    from hledac_hypothesis import HypothesisEngine
    from hledac_hypothesis._types import HypothesisType, Evidence
    from hledac_hypothesis.adversarial import AdversarialVerifier
    from hledac_hypothesis.causal import CausalReasoner
    from hledac_hypothesis.packs import HypothesisPack, SourceHint
"""


import logging
import os
from typing import TYPE_CHECKING, Any

# Re-export types from submodules for convenience
from ._types import (
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
    _to_operator_shortlist,
)

from .adversarial import (
    AdversarialVerifier,
)

from .causal import (
    CausalReasoner,
)

from .explainer import (
    SimpleNodeAblationExplainer,
    explain_with_mlx,
)

from .packs import (
    HypothesisPack,
    SourceHint,
)

logger = logging.getLogger(__name__)
from hledac.universal.runtime.lane_registry import LANE_REGISTRY

def _is_dspy_enabled() -> bool:
    """DSPy lane enablement check via LaneRegistry."""
    return LANE_REGISTRY.is_enabled("dspy")


# Lazy exports for HypothesisEngine and related classes from brain.research_hypothesis_engine
def __getattr__(name: str) -> Any:
    if name in (
        "HypothesisEngine",
        "Hypothesis",
        "FalsificationResult",
        "DarkQuery",
        "DarkQueryType",
        "InferenceEngineProtocol",
        "ResearchHypothesis",
        "HypothesisGenerator",
    ):
        try:
            from hledac.universal.brain.research_hypothesis_engine import (
                FalsificationResult,
                HypothesisEngine,
                HypothesisPack,
                HypothesisStatus,
                InferenceEngineProtocol,
            )
            from hledac_hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis

            exports = {
                "HypothesisEngine": HypothesisEngine,
                "HypothesisStatus": HypothesisStatus,
                "HypothesisPack": HypothesisPack,
                "FalsificationResult": FalsificationResult,
                "InferenceEngineProtocol": InferenceEngineProtocol,
                "ResearchHypothesis": ResearchHypothesis,
                "HypothesisGenerator": HypothesisGenerator,
            }
            if name == "DarkQuery":
                from hledac.universal.brain.research_hypothesis_engine import DarkQuery
                exports["DarkQuery"] = DarkQuery
            elif name == "DarkQueryType":
                from hledac.universal.brain.research_hypothesis_engine import DarkQueryType
                exports["DarkQueryType"] = DarkQueryType
            elif name == "Hypothesis":
                from hledac.universal.brain.research_hypothesis_engine import Hypothesis
                exports["Hypothesis"] = Hypothesis

            val = exports.get(name)
            globals()[name] = val
            return val
        except ImportError:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from hledac.universal.brain.research_hypothesis_engine import (
        DarkQuery,
        DarkQueryType,
        Evidence,
        FalsificationResult,
        Hypothesis,
        HypothesisEngine,
        HypothesisPack,
        HypothesisStatus,
        HypothesisType,
        InferenceEngineProtocol,
        TestDesign,
        TestResult,
    )
    from hledac_hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis


__all__ = [
    # Types from _types
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
    # Classes from submodules
    "AdversarialVerifier",
    "SimpleNodeAblationExplainer",
    "SourceHint",
    "HypothesisPack",
    "CausalReasoner",
    "InferenceEngineProtocol",
    # Utilities
    "_to_operator_shortlist",
    # Lazy exports
    "HypothesisEngine",
    "Hypothesis",
    "ResearchHypothesis",
    "HypothesisGenerator",
]

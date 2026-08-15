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
- ``eig.py``              — Evidence Inference Graph
- ``hypothesisgenerator.py`` — HypothesisGenerator (heuristic + DSPy)
- ``types/``             — Canonical type subpackage (Evidence, HypothesisType, etc.)

Naming Conflict Resolution
-------------------------
The pip ``hypothesis`` package (property-based testing) is unrelated.
All homegrown OSINT hypothesis code lives in this package (``hledac_hypothesis``),
not in a package named ``hypothesis``.

M1 8GB Lazy Invariant:
  All submodules are loaded lazily via ``__getattr__`` — no MLX, numpy, or
  DSPy is loaded at import time. Only ``_types`` constants (enums/ints) and
  ``LANE_REGISTRY`` are imported eagerly.

Canonical Imports (NEW)
----------------------
    from hledac_hypothesis import HypothesisEngine
    from hledac_hypothesis._types import HypothesisType, Evidence
    from hledac_hypothesis.adversarial import AdversarialVerifier
    from hledac_hypothesis.causal import CausalReasoner
    from hledac_hypothesis.packs import HypothesisPack, SourceHint

Backward Compatibility
---------------------
``brain/hypothesis_engine/`` and ``brain.research_hypothesis_engine`` are
backward-compat shims that re-export from this package.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# ── Eager: only type constants and lane registry (no MLX/numpy/DSPy) ───────────
from hledac.universal.runtime.lane_registry import LANE_REGISTRY






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

# ── Lazy __getattr__ — all submodules loaded on first use ──────────────────────

from _core import aclose_SUPMOD_LAZY: tuple[str, ...] = (
    "AdversarialVerifier",
    "CausalReasoner",
    "SimpleNodeAblationExplainer",
    "explain_with_mlx",
    "HypothesisPack",
    "SourceHint",
)

_ENGINE_LAZY: tuple[str, ...] = (
    "HypothesisEngine",
    "Hypothesis",
    "FalsificationResult",
    "DarkQuery",
    "DarkQueryType",
    "InferenceEngineProtocol",
    "ResearchHypothesis",
    "HypothesisGenerator",
)


def __getattr__(name: str) -> Any:
    # AdversarialVerifier, CausalReasoner, explainer, packs — lazy submodules
    if name in _SUPMOD_LAZY:
        import sys
        mod_map: dict[str, str] = {
            "AdversarialVerifier": "adversarial",
            "CausalReasoner": "causal",
            "SimpleNodeAblationExplainer": "explainer",
            "explain_with_mlx": "explainer",
            "HypothesisPack": "packs",
            "SourceHint": "packs",
        }
        mod_name = mod_map[name]
        module = __import__(f"hledac_hypothesis.{mod_name}", fromlist=[name])
        val = getattr(module, name)
        globals()[name] = val
        return val

    # HypothesisEngine + engine-bound types — from brain shim (lazy)
    if name in _ENGINE_LAZY:
        try:
            from hledac.universal.brain.research_hypothesis_engine import (
                FalsificationResult,
                HypothesisEngine,
                HypothesisPack,
                HypothesisStatus,
                InferenceEngineProtocol,
            )
            from hledac_hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis

            exports: dict[str, Any] = {
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


# ── TYPE_CHECKING block — IDE autocompletion without runtime cost ──────────────
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


__all__: list[str] = [
    # Eager: type constants (no runtime cost — enums/ints)
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
    # Lazy submodules (adversarial, causal, explainer, packs)
    "AdversarialVerifier",
    "SimpleNodeAblationExplainer",
    "SourceHint",
    "HypothesisPack",
    "CausalReasoner",
    "InferenceEngineProtocol",
    "_to_operator_shortlist",
    # Lazy engine exports
    "HypothesisEngine",
    "Hypothesis",
    "ResearchHypothesis",
    "HypothesisGenerator",
]

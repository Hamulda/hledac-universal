"""
hypothesis/__init__.py

Re-exported types for hypothesis engine.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Optional DSPy engine
# -------------------------------------------------------------------------
HAS_DSPY = bool(os.environ.get("HLEDAC_ENABLE_DSPY", ""))

# -------------------------------------------------------------------------
# Re-exports — lazy resolve to avoid hard dependency on hypothesis_engine
# -------------------------------------------------------------------------
def __getattr__(name: str) -> Any:
    if name in (
        "HypothesisEngine",
        "Hypothesis",
        "HypothesisStatus",
        "HypothesisType",
        "HypothesisPack",
        "Evidence",
        "TestResult",
        "TestDesign",
        "FalsificationResult",
        "DarkQuery",
        "DarkQueryType",
        "InferenceEngineProtocol",
        "ResearchHypothesis",
        "HypothesisGenerator",
    ):
        try:
            from brain.hypothesis_engine import (
                FalsificationResult,
                HypothesisEngine,
                HypothesisPack,
                HypothesisStatus,
                InferenceEngineProtocol,
            )
            from hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis

            mod = globals()
            exports = {
                "HypothesisEngine": HypothesisEngine,
                "HypothesisStatus": HypothesisStatus,
                "HypothesisPack": HypothesisPack,
                "FalsificationResult": FalsificationResult,
                "InferenceEngineProtocol": InferenceEngineProtocol,
                "ResearchHypothesis": ResearchHypothesis,
                "HypothesisGenerator": HypothesisGenerator,
            }
            # DarkQuery/DarkQueryType not in hypothesis_engine — map it
            if name == "DarkQuery":
                from brain.hypothesis_engine import DarkQuery

                exports["DarkQuery"] = DarkQuery
            elif name == "DarkQueryType":
                from brain.hypothesis_engine import DarkQueryType

                exports["DarkQueryType"] = DarkQueryType
            elif name == "Hypothesis":
                from brain.hypothesis_engine import Hypothesis

                exports["Hypothesis"] = Hypothesis
            elif name == "HypothesisType":
                from brain.hypothesis_engine import HypothesisType

                exports["HypothesisType"] = HypothesisType
            elif name == "Evidence":
                from brain.hypothesis_engine import Evidence

                exports["Evidence"] = Evidence
            elif name == "TestResult":
                from brain.hypothesis_engine import TestResult

                exports["TestResult"] = TestResult
            elif name == "TestDesign":
                from brain.hypothesis_engine import TestDesign

                exports["TestDesign"] = TestDesign

            val = exports.get(name)
            mod[name] = val
            return val
        except ImportError:
            raise AttributeError(f"module {__name__!r} has no attr {name!r}")

    raise AttributeError(f"module {__name__!r} has no attr {name!r}")


if TYPE_CHECKING:
    from brain.hypothesis_engine import (
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
    from hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis


__all__ = [
    "HypothesisEngine",
    "Hypothesis",
    "HypothesisStatus",
    "HypothesisType",
    "HypothesisPack",
    "Evidence",
    "TestResult",
    "TestDesign",
    "FalsificationResult",
    "DarkQuery",
    "DarkQueryType",
    "InferenceEngineProtocol",
    "ResearchHypothesis",
    "HypothesisGenerator",
]

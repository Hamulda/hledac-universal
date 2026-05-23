"""
hypothesis/ — Hypothesis generation, falsification, and tracking.

Lazy re-export of HypothesisEngine from brain.hypothesis_engine.
All hypothesis operations flow through this package.

Canonical entry: HypothesisEngine (brain.hypothesis_engine)
"""

from __future__ import annotations

import importlib
import types
from typing import TYPE_CHECKING

# ── Lazy re-export ────────────────────────────────────────────────────────────
_modules = {
    "HypothesisEngine": "brain.hypothesis_engine",
    "Hypothesis": "brain.hypothesis_engine",
    "HypothesisStatus": "brain.hypothesis_engine",
    "HypothesisType": "brain.hypothesis_engine",
    "HypothesisPack": "brain.hypothesis_engine",
    "Evidence": "brain.hypothesis_engine",
    "TestResult": "brain.hypothesis_engine",
    "TestDesign": "brain.hypothesis_engine",
    "FalsificationResult": "brain.hypothesis_engine",
    "DarkQuery": "brain.hypothesis_engine",
    "DarkQueryType": "brain.hypothesis_engine",
    "HypothesisGraph": "brain.hypothesis_engine",
    "InferenceEngineProtocol": "brain.hypothesis_engine",
}

_loaded: dict[str, types.ModuleType] = {}

def __getattr__(name: str):
    if name in _loaded:
        return getattr(_loaded[name], name)
    if name in _modules:
        mod = importlib.import_module(_modules[name], package="hledac.universal")
        _loaded[name] = mod
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from brain.hypothesis_engine import (
        HypothesisEngine,
        Hypothesis,
        HypothesisStatus,
        HypothesisType,
        HypothesisPack,
        Evidence,
        TestResult,
        TestDesign,
        FalsificationResult,
        DarkQuery,
        DarkQueryType,
        InferenceEngineProtocol,
    )

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
]
"""
Brain komponenty pro UniversalResearchOrchestrator.

PROMOTION GATE — FACADE MODULE
================================
brain/__init__.py je čistý FACADE / re-export modul.
Neinstantiuje žádné těžké enginy přímo — pouze zpřístupňuje symboly.

STATUS: FACADE (export-only, no active promotion path)
M1 8GB MEMORY CEILING: N/A — facade nealokuje žádné zdroje
ALLOWED PURPOSE: Re-export dostupných brain submodulů přes _AVAILABLE flagy
PROMOTION ELIGIBILITY: NO — žádný brain engine není canonical-surface

Submoduly a jejich status (viz každý modul):
- Hermes3Engine: L1 canonical (samostatný soubor)
- DecisionEngine: L1 HELPER-only (brain/decision_engine.py) — DEPRECATED shim, canonical owner is Hermes3Engine
- InsightEngine: EXPERIMENTAL — importuj z insight_engine.py
- InferenceEngine: EXPERIMENTAL — importuj z inference_engine.py
- HypothesisEngine: EXPERIMENTAL — importuj z hypothesis_engine.py
- MoERouter: DORMANT — mlx_nn-none guard, žádné aktivní volání
- DistillationEngine: DORMANT — nn=None guard, žádné aktivní volání
- ModelManager: L1 canonical (samostatný soubor, M1 lifecycle management)
- NEREngine: EXPERIMENTAL — GLiNER-X model, velká RAM stopa

DŮLEŽITÉ: Brain facade NEPROMPTUJE žádné heavy enginy do aktivního runtime.
Přidání nového importu sem neznamená, že je "podporováno" nebo "production-ready".
Vždy kontroluj _AVAILABLE flag a přítomnost SKUTEČNÝCH call sites v kódu.
"""

from enum import Enum


# DecisionType — re-exported from Hermes3Engine compat shim (decision_engine.py deleted)
class DecisionType(Enum):
    RESEARCH = "research"
    EXECUTION = "execution"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    SYNTHESIS = "synthesis"
    ERROR = "error"
    COMPLETE = "complete"

from .hermes3_engine import Hermes3Engine

try:
    from .insight_engine import (
        Anomaly,
        CausalRelationship,
        Contradiction,
        Gap,
        Hypothesis,
        Insight,
        InsightAnalysisResult,
        InsightEngine,
        Pattern,
        SynthesisLevel,
        create_insight_engine,
    )
    INSIGHT_AVAILABLE = True
except ImportError:
    INSIGHT_AVAILABLE = False

# Inference Engine (OSINT inference and reasoning)
try:
    from .inference_engine import (
        Evidence,
        HopStep,
        InferenceEngine,
        InferenceRule,
        InferenceStep,
        InferenceType,
        MultiHopPath,
        # Multi-Hop Reasoning
        MultiHopReasoner,
        ResolvedEntity,
        create_inference_engine,
    )
    from .inference_engine import (
        Hypothesis as InferenceHypothesis,
    )
    INFERENCE_AVAILABLE = True
except ImportError:
    INFERENCE_AVAILABLE = False

# Hypothesis Engine (automated hypothesis generation and testing)
try:
    from .hypothesis_engine import (
        AdversarialReport,
        # Adversarial Verification
        AdversarialVerifier,
        Contradiction,
        FalsificationResult,
        Hypothesis,
        HypothesisEngine,
        HypothesisStatus,
        HypothesisType,
        SourceCredibility,
        TestDesign,
        TestResult,
        TestType,
        create_hypothesis_engine,
    )
    from .hypothesis_engine import (
        Evidence as HypothesisEvidence,
    )
    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

# MoE Router
# NOTE: moe_router.py has 'class RouterMLP(mlx_nn.Module)' where mlx_nn=None
# when MLX import fails via ImportError. This causes AttributeError, not ImportError.
# Bounded compat: catch broader Exception to ensure fail-soft containment.
try:
    from .moe_router import MoERouter, MoERouterConfig, create_moe_router
    MOE_AVAILABLE = True
except ImportError:
    MOE_AVAILABLE = False
except Exception:
    # AttributeError/TypeError from nn=None when MLX unavailable
    MOE_AVAILABLE = False

# Distillation Engine (MLX-based reasoning chain quality scoring)
# NOTE: distillation_engine.py has 'class CriticMLP(nn.Module)' where nn=None
# when MLX import fails via ImportError. This causes TypeError, not ImportError.
# Bounded compat: catch broader Exception to ensure fail-soft containment.
try:
    from .distillation_engine import (
        CriticMLP,
        DistillationEngine,
        DistillationExample,
        create_distillation_engine,
    )
    DISTILLATION_AVAILABLE = True
except ImportError:
    DISTILLATION_AVAILABLE = False
except Exception:
    # AttributeError/TypeError from nn=None when MLX unavailable
    DISTILLATION_AVAILABLE = False

# Model Manager (lifecycle management for M1 8GB)
# ModernBertEngine (extractive summarization via MLX embeddings)
try:
    from .modernbert_engine import ModernBertEngine
    MODERNBERT_AVAILABLE = True
except ImportError:
    MODERNBERT_AVAILABLE = False

# Sprint F222: ModelEngine Protocol + ModernBertModelAdapter
try:
    from .model_engine import ModelEngine
    from .modernbert_adapter import ModernBertModelAdapter
    MODEL_ENGINE_AVAILABLE = True
except ImportError:
    MODEL_ENGINE_AVAILABLE = False

# Model Manager (lifecycle management for M1 8GB)
try:
    from .model_manager import (
        ModelManager,
        ModelType,
        get_model_manager,
        reset_model_manager,
    )
    MODEL_MANAGER_AVAILABLE = True
except ImportError:
    MODEL_MANAGER_AVAILABLE = False

# NER Engine (GLiNER-X for entity extraction)
# Sprint 8VG: kanonické místo pro NER/IOC je brain.ner_engine
try:
    from .ner_engine import (
        Entity,
        IOCScorer,
        NEREngine,
        extract_iocs_from_text,
        get_ner_engine,
        reset_ner_engine,
    )
    NER_ENGINE_AVAILABLE = True
except ImportError:
    NER_ENGINE_AVAILABLE = False

# P13: Embedding model lifecycle management
try:
    from ..embedding_pipeline import (
        load_embedding_model,
        unload_embedding_model,
    )
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

# ─── Capability Catalog ──────────────────────────────────────────────────────
# Explicit catalog of brain engine availability. Callers should use
# is_brain_engine_available("insight") rather than checking _AVAILABLE directly.
AVAILABLE_BRAIN_ENGINES = {
    "insight": INSIGHT_AVAILABLE,
    "inference": INFERENCE_AVAILABLE,
    "hypothesis": HYPOTHESIS_AVAILABLE,
    "moe": MOE_AVAILABLE,
    "distillation": DISTILLATION_AVAILABLE,
    "modernbert": MODERNBERT_AVAILABLE,
    "model_manager": MODEL_MANAGER_AVAILABLE,
    "ner_engine": NER_ENGINE_AVAILABLE,
    "embedding": EMBEDDING_AVAILABLE,
}


def is_brain_engine_available(name: str) -> bool:
    """
    Runtime capability check for brain engines.

    Args:
        name: Engine name ("insight", "inference", "hypothesis", "moe",
               "distillation", "modernbert", "model_manager", "ner_engine", "embedding")

    Returns:
        True if the engine is available and its symbols are importable.

    Example:
        if is_brain_engine_available("insight"):
            from brain import InsightEngine
    """
    return AVAILABLE_BRAIN_ENGINES.get(name, False)


def get_available_brain_engines() -> dict[str, bool]:
    """Return the full capability catalog as a dict."""
    return AVAILABLE_BRAIN_ENGINES.copy()

__all__ = [
    "Hermes3Engine",
    "DecisionType",
    # Insight
    "InsightEngine",
    "InsightAnalysisResult",
    "Insight",
    "Pattern",
    "Anomaly",
    "Contradiction",
    "Gap",
    "Hypothesis",
    "CausalRelationship",
    "SynthesisLevel",
    "create_insight_engine",
    "INSIGHT_AVAILABLE",
    # Inference
    "InferenceEngine",
    "Evidence",
    "InferenceStep",
    "InferenceHypothesis",
    "ResolvedEntity",
    "InferenceRule",
    "InferenceType",
    "create_inference_engine",
    "INFERENCE_AVAILABLE",
    # Multi-Hop Reasoning
    "MultiHopReasoner",
    "HopStep",
    "MultiHopPath",
    # Hypothesis
    "HypothesisEngine",
    "Hypothesis",
    "HypothesisType",
    "HypothesisStatus",
    "TestResult",
    "TestDesign",
    "TestType",
    "FalsificationResult",
    "HypothesisEvidence",
    "create_hypothesis_engine",
    "HYPOTHESIS_AVAILABLE",
    # Adversarial Verification
    "AdversarialVerifier",
    "SourceCredibility",
    "Contradiction",
    "AdversarialReport",
    # MoE Router
    "MoERouter",
    "MoERouterConfig",
    "create_moe_router",
    "MOE_AVAILABLE",
    # Distillation Engine
    "DistillationEngine",
    "DistillationExample",
    "CriticMLP",
    "create_distillation_engine",
    "DISTILLATION_AVAILABLE",
    # ModernBertEngine
    "ModernBertEngine",
    "MODERNBERT_AVAILABLE",
    # Sprint F222: ModelEngine Protocol + adapter
    "ModelEngine",
    "ModernBertModelAdapter",
    "MODEL_ENGINE_AVAILABLE",
    # Model Manager
    "ModelManager",
    "ModelType",
    "get_model_manager",
    "reset_model_manager",
    "MODEL_MANAGER_AVAILABLE",
    # NER/IOC (Sprint 8VG)
    "NEREngine",
    "Entity",
    "get_ner_engine",
    "reset_ner_engine",
    "extract_iocs_from_text",
    "IOCScorer",
    "NER_ENGINE_AVAILABLE",
    # P13: Embedding Model Lifecycle
    "load_embedding_model",
    "unload_embedding_model",
    "EMBEDDING_AVAILABLE",
    # Capability Catalog API
    "AVAILABLE_BRAIN_ENGINES",
    "is_brain_engine_available",
    "get_available_brain_engines",
]

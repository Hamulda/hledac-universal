"""
Brain komponenty pro UniversalResearchOrchestrator.

Obsahuje:
- Hermes3Engine: Hlavní LLM engine s Hermes-3
- DecisionEngine: Rozhodovací logika
- InsightEngine: Pokročilá generace insightů (z deep_research)
- InferenceEngine: Inferenční engine pro OSINT analýzu
- HypothesisEngine: Automatizovaná generace a testování hypotéz
- MoERouter: Mixture-of-Experts routing pro M1 8GB
- DistillationEngine: MLX-based reasoning chain quality scoring
"""

from .hermes3_engine import Hermes3Engine
from .decision_engine import DecisionEngine, DecisionType

# Insight Engine (from deep_research/insight_generator.py)
try:
    from .insight_engine import (
        InsightEngine,
        InsightAnalysisResult,
        Insight,
        Pattern,
        Anomaly,
        Contradiction,
        Gap,
        Hypothesis,
        CausalRelationship,
        SynthesisLevel,
        create_insight_engine,
    )
    INSIGHT_AVAILABLE = True
except ImportError:
    INSIGHT_AVAILABLE = False

# Inference Engine (OSINT inference and reasoning)
try:
    from .inference_engine import (
        InferenceEngine,
        Evidence,
        InferenceStep,
        Hypothesis as InferenceHypothesis,
        ResolvedEntity,
        InferenceRule,
        InferenceType,
        create_inference_engine,
        # Multi-Hop Reasoning
        MultiHopReasoner,
        HopStep,
        MultiHopPath,
    )
    INFERENCE_AVAILABLE = True
except ImportError:
    INFERENCE_AVAILABLE = False

# Hypothesis Engine (automated hypothesis generation and testing)
try:
    from .hypothesis_engine import (
        HypothesisEngine,
        Hypothesis,
        HypothesisType,
        HypothesisStatus,
        TestResult,
        TestDesign,
        TestType,
        FalsificationResult,
        Evidence as HypothesisEvidence,
        create_hypothesis_engine,
        # Adversarial Verification
        AdversarialVerifier,
        SourceCredibility,
        Contradiction,
        AdversarialReport,
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
        DistillationEngine,
        DistillationExample,
        CriticMLP,
        create_distillation_engine,
    )
    DISTILLATION_AVAILABLE = True
except ImportError:
    DISTILLATION_AVAILABLE = False
except Exception:
    # AttributeError/TypeError from nn=None when MLX unavailable
    DISTILLATION_AVAILABLE = False

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
        NEREngine,
        Entity,
        get_ner_engine,
        reset_ner_engine,
        extract_iocs_from_text,
        IOCScorer,
    )
    NER_ENGINE_AVAILABLE = True
except ImportError:
    NER_ENGINE_AVAILABLE = False

__all__ = [
    "Hermes3Engine",
    "DecisionEngine",
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
    # Model Manager
    "ModelManager",
    "ModelType",
    "get_model_manager",
    "reset_model_manager",
    "MODEL_MANAGER_AVAILABLE",
    # NER/IOC (Sprint 8VG)
    "extract_iocs_from_text",
    "IOCScorer",
]

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

from .deephermes3_engine import DeepHermes3Engine, parse_thinking_output  # noqa: E402

# ─── Phase 2 Modular Brain Components (PEP 698) ───────────────────────────────
# Extracted from DeepHermes3Engine God Class refactoring.
# Module-level None defaults: needed for AVAILABLE_BRAIN_ENGINES dict (eval at import time).
# __getattr__ updates these via globals() on first attribute access.
# NOTE: "from brain import X" returns current value without triggering __getattr__;
# use "getattr(brain, 'X')" or access brain.X to trigger lazy loading.

# Sprint LoRA-1: LoRA fine-tuning via mlx_lm.lora — lazy (A2-FIX)
# mlx_lm import is ~300ms GPU init; defer to first attribute access.
LORA_AVAILABLE = None

# Sprint P0-2: MLXBatchedExecutor — lazy (A2-FIX)
MLXBatchedExecutor = None  # type: ignore[assignment,misc]
MLX_BATCHED_EXECUTOR_AVAILABLE = None

# Sprint P0-3: MLXWorkerThread — lazy (A2-FIX)
MLXWorkerThread = None  # type: ignore[assignment,misc]
MLX_WORKER_THREAD_AVAILABLE = None

# Sprint P2-1b: InferencePipeliner — lazy (A2-FIX)
InferencePipeliner = None  # type: ignore[assignment,misc]
INFERENCE_PIPELINER_AVAILABLE = None

# InsightEngine — lazy: 1.9s cold import deferred (A2-FIX)
INSIGHT_AVAILABLE = None
Anomaly = None  # type: ignore[assignment,misc]
CausalRelationship = None  # type: ignore[assignment,misc]
Contradiction = None  # type: ignore[assignment,misc]
Gap = None  # type: ignore[assignment,misc]
Hypothesis = None  # type: ignore[assignment,misc]
Insight = None  # type: ignore[assignment,misc]
InsightAnalysisResult = None  # type: ignore[assignment,misc]
InsightEngine = None  # type: ignore[assignment,misc]
Pattern = None  # type: ignore[assignment,misc]
SynthesisLevel = None  # type: ignore[assignment,misc]
create_insight_engine = None  # type: ignore[assignment,misc]

# InferenceEngine — lazy (A2-FIX)
INFERENCE_AVAILABLE = None
Evidence = None  # type: ignore[assignment,misc]
HopStep = None  # type: ignore[assignment,misc]
InferenceEngine = None  # type: ignore[assignment,misc]
InferenceRule = None  # type: ignore[assignment,misc]
InferenceStep = None  # type: ignore[assignment,misc]
InferenceType = None  # type: ignore[assignment,misc]
MultiHopPath = None  # type: ignore[assignment,misc]
MultiHopReasoner = None  # type: ignore[assignment,misc]
ResolvedEntity = None  # type: ignore[assignment,misc]
create_inference_engine = None  # type: ignore[assignment,misc]
InferenceHypothesis = None  # type: ignore[assignment,misc]

# HypothesisEngine — lazy (A2-FIX): deferred from top-level to __getattr__ to break
# circular import. hypothesis_engine/adversarial.py imports Hypothesis at module level.
HYPOTHESIS_AVAILABLE = None
Hypothesis = None  # type: ignore[assignment,misc]
HypothesisEngine = None  # type: ignore[assignment,misc]

# MoE Router — lazy (A2-FIX)
MOE_AVAILABLE = None
MoERouter = None  # type: ignore[assignment,misc]
MoERouterConfig = None  # type: ignore[assignment,misc]
create_moe_router = None  # type: ignore[assignment,misc]

# Distillation Engine — lazy (A2-FIX)
DISTILLATION_AVAILABLE = None
CriticMLP = None  # type: ignore[assignment,misc]
DistillationEngine = None  # type: ignore[assignment,misc]
DistillationExample = None  # type: ignore[assignment,misc]
create_distillation_engine = None  # type: ignore[assignment,misc]

# ModernBertEngine — lazy (A2-FIX)
MODERNBERT_AVAILABLE = None
ModernBertEngine = None  # type: ignore[assignment,misc]

# Sprint F222: ModelEngine + ModernBertModelAdapter — lazy (A2-FIX)
MODEL_ENGINE_AVAILABLE = None
ModelEngine = None  # type: ignore[assignment,misc]
ModernBertModelAdapter = None  # type: ignore[assignment,misc]

# ModelManager — lazy (A2-FIX)
MODEL_MANAGER_AVAILABLE = None
ModelManager = None  # type: ignore[assignment,misc]
ModelType = None  # type: ignore[assignment,misc]
get_model_manager = None  # type: ignore[assignment,misc]
reset_model_manager = None  # type: ignore[assignment,misc]

# NER Engine — lazy (A2-FIX)
NER_ENGINE_AVAILABLE = None
Entity = None  # type: ignore[assignment,misc]
IOCScorer = None  # type: ignore[assignment,misc]
NEREngine = None  # type: ignore[assignment,misc]
extract_iocs_from_text = None  # type: ignore[assignment,misc]
get_ner_engine = None  # type: ignore[assignment,misc]
reset_ner_engine = None  # type: ignore[assignment,misc]

# P13: Embedding pipeline — lazy (A2-FIX)
EMBEDDING_AVAILABLE = None
load_embedding_model = None  # type: ignore[assignment,misc]
unload_embedding_model = None  # type: ignore[assignment,misc]


# ─── PEP 562 Lazy Imports via __getattr__ ─────────────────────────────────────
# A2-FIX: All 12 non-circular engines defer import until first attribute access.
# Cold import cost drops from ~9.7s to ~150ms (enum + flag defs only).
# HypothesisEngine stays eager (circular dep with hypothesis_engine/adversarial.py).
def __getattr__(name: str):
    # Phase 2: Modular Brain Components (extracted from DeepHermes3Engine)
    if name in ("METAL_AVAILABLE",):
        global METAL_AVAILABLE
        try:
            from . import _metal
            METAL_AVAILABLE = True
            AVAILABLE_BRAIN_ENGINES["metal"] = True
        except Exception:
            METAL_AVAILABLE = False
            AVAILABLE_BRAIN_ENGINES["metal"] = False
        return METAL_AVAILABLE
    if name in ("CACHE_AVAILABLE",):
        global CACHE_AVAILABLE
        try:
            from . import _cache
            CACHE_AVAILABLE = True
            AVAILABLE_BRAIN_ENGINES["cache"] = True
        except Exception:
            CACHE_AVAILABLE = False
            AVAILABLE_BRAIN_ENGINES["cache"] = False
        return CACHE_AVAILABLE
    if name in ("BATCH_AVAILABLE",):
        global BATCH_AVAILABLE
        try:
            from . import _batch
            BATCH_AVAILABLE = True
            AVAILABLE_BRAIN_ENGINES["batch"] = True
        except Exception:
            BATCH_AVAILABLE = False
            AVAILABLE_BRAIN_ENGINES["batch"] = False
        return BATCH_AVAILABLE
    # Sprint LoRA-1: mlx_lm.lora deferred import
    if name in ("LORA_AVAILABLE",):
        global LORA_AVAILABLE
        try:
            import mlx_lm
            _ = mlx_lm.lora.load_lora_model
            LORA_AVAILABLE = True
        except Exception:
            LORA_AVAILABLE = False
        return LORA_AVAILABLE
    # MLXBatchedExecutor — Sprint P0-2
    if name in ("MLXBatchedExecutor", "MLX_BATCHED_EXECUTOR_AVAILABLE"):
        global MLXBatchedExecutor, MLX_BATCHED_EXECUTOR_AVAILABLE
        try:
            from .mlx_batched_executor import MLXBatchedExecutor
            MLX_BATCHED_EXECUTOR_AVAILABLE = True
        except ImportError:
            MLXBatchedExecutor = None  # type: ignore[assignment,misc]
            MLX_BATCHED_EXECUTOR_AVAILABLE = False
        return MLXBatchedExecutor if name == "MLXBatchedExecutor" else MLX_BATCHED_EXECUTOR_AVAILABLE
    # MLXWorkerThread — Sprint P0-3
    if name in ("MLXWorkerThread", "MLX_WORKER_THREAD_AVAILABLE"):
        global MLXWorkerThread, MLX_WORKER_THREAD_AVAILABLE
        try:
            from .mlx_worker_thread import MLXWorkerThread
            MLX_WORKER_THREAD_AVAILABLE = True
        except ImportError:
            MLXWorkerThread = None  # type: ignore[assignment,misc]
            MLX_WORKER_THREAD_AVAILABLE = False
        return MLXWorkerThread if name == "MLXWorkerThread" else MLX_WORKER_THREAD_AVAILABLE
    # InferencePipeliner — Sprint P2-1b
    if name in ("InferencePipeliner", "INFERENCE_PIPELINER_AVAILABLE"):
        global InferencePipeliner, INFERENCE_PIPELINER_AVAILABLE
        try:
            from .inference_pipeliner import InferencePipeliner
            INFERENCE_PIPELINER_AVAILABLE = True
        except ImportError:
            InferencePipeliner = None  # type: ignore[assignment,misc]
            INFERENCE_PIPELINER_AVAILABLE = False
        return InferencePipeliner if name == "InferencePipeliner" else INFERENCE_PIPELINER_AVAILABLE
    # InsightEngine — 1.9s cold import deferred
    if name in (
        "Anomaly", "CausalRelationship", "Contradiction", "Gap",
        "Insight", "InsightAnalysisResult", "InsightEngine",
        "Pattern", "SynthesisLevel", "create_insight_engine",
        "INSIGHT_AVAILABLE",
    ):
        global INSIGHT_AVAILABLE, Anomaly, CausalRelationship, Contradiction, Gap, Insight, InsightAnalysisResult, InsightEngine, Pattern, SynthesisLevel, create_insight_engine
        from . import insight_engine as _ie
        INSIGHT_AVAILABLE = True
        Anomaly = _ie.Anomaly
        CausalRelationship = _ie.CausalRelationship
        Contradiction = _ie.Contradiction
        Gap = _ie.Gap
        Insight = _ie.Insight
        InsightAnalysisResult = _ie.InsightAnalysisResult
        InsightEngine = _ie.InsightEngine
        Pattern = _ie.Pattern
        SynthesisLevel = _ie.SynthesisLevel
        create_insight_engine = _ie.create_insight_engine
        if name == "INSIGHT_AVAILABLE":
            return True
        return globals()[name]
    # InferenceEngine
    if name in (
        "Evidence", "HopStep", "InferenceEngine", "InferenceRule",
        "InferenceStep", "InferenceType", "MultiHopPath",
        "MultiHopReasoner", "ResolvedEntity", "create_inference_engine",
        "InferenceHypothesis", "INFERENCE_AVAILABLE",
    ):
        global INFERENCE_AVAILABLE, Evidence, HopStep, InferenceEngine, InferenceRule, InferenceStep, InferenceType, MultiHopPath, MultiHopReasoner, ResolvedEntity, create_inference_engine, InferenceHypothesis
        from . import inference_engine as _ie
        INFERENCE_AVAILABLE = True
        Evidence = _ie.Evidence
        HopStep = _ie.HopStep
        InferenceEngine = _ie.InferenceEngine
        InferenceRule = _ie.InferenceRule
        InferenceStep = _ie.InferenceStep
        InferenceType = _ie.InferenceType
        MultiHopPath = _ie.MultiHopPath
        MultiHopReasoner = _ie.MultiHopReasoner
        ResolvedEntity = _ie.ResolvedEntity
        create_inference_engine = _ie.create_inference_engine
        InferenceHypothesis = _ie.Hypothesis
        if name == "INFERENCE_AVAILABLE":
            return True
        return globals()[name]
    # HypothesisEngine — lazy (A2-FIX): deferred from top-level to break circular dep
    if name in (
        "AdversarialReport", "AdversarialVerifier", "_HE_Contradiction",
        "FalsificationResult", "Hypothesis", "HypothesisEngine",
        "HypothesisStatus", "HypothesisType", "SourceCredibility",
        "TestDesign", "TestResult", "TestType",
        "create_hypothesis_engine", "HypothesisEvidence",
        "HYPOTHESIS_AVAILABLE",
    ):
        global HYPOTHESIS_AVAILABLE, Hypothesis, HypothesisEngine
        global AdversarialReport, AdversarialVerifier, FalsificationResult
        global HypothesisStatus, HypothesisType, SourceCredibility
        global TestDesign, TestResult, TestType, create_hypothesis_engine
        global HypothesisEvidence, _HE_Contradiction
        try:
            from .research_hypothesis_engine import (
                AdversarialReport,
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
            from .research_hypothesis_engine import (
                Evidence as HypothesisEvidence,
            )
            # Alias for compatibility
            _HE_Contradiction = Contradiction
            HYPOTHESIS_AVAILABLE = True
        except ImportError:
            Hypothesis = None  # type: ignore[assignment,misc]
            HYPOTHESIS_AVAILABLE = False
        if name == "HYPOTHESIS_AVAILABLE":
            return HYPOTHESIS_AVAILABLE
        if name == "_HE_Contradiction":
            return _HE_Contradiction
        return globals().get(name)
    # MoE Router
    if name in ("MoERouter", "MoERouterConfig", "create_moe_router", "MOE_AVAILABLE"):
        global MOE_AVAILABLE, MoERouter, MoERouterConfig, create_moe_router
        try:
            from . import moe_router as _mr
            MOE_AVAILABLE = True
            MoERouter = _mr.MoERouter
            MoERouterConfig = _mr.MoERouterConfig
            create_moe_router = _mr.create_moe_router
        except ImportError:
            MOE_AVAILABLE = False
        if name == "MOE_AVAILABLE":
            return MOE_AVAILABLE
        return globals().get(name)
    # Distillation Engine
    if name in ("CriticMLP", "DistillationEngine", "DistillationExample",
                "create_distillation_engine", "DISTILLATION_AVAILABLE"):
        global DISTILLATION_AVAILABLE, CriticMLP, DistillationEngine, DistillationExample, create_distillation_engine
        try:
            from . import distillation_engine as _de
            DISTILLATION_AVAILABLE = True
            CriticMLP = _de.CriticMLP
            DistillationEngine = _de.DistillationEngine
            DistillationExample = _de.DistillationExample
            create_distillation_engine = _de.create_distillation_engine
        except ImportError:
            DISTILLATION_AVAILABLE = False
        if name == "DISTILLATION_AVAILABLE":
            return DISTILLATION_AVAILABLE
        return globals().get(name)
    # ModernBertEngine
    if name in ("ModernBertEngine", "MODERNBERT_AVAILABLE"):
        global MODERNBERT_AVAILABLE, ModernBertEngine
        try:
            from . import modernbert_engine as _mb
            MODERNBERT_AVAILABLE = True
            ModernBertEngine = _mb.ModernBertEngine
        except ImportError:
            MODERNBERT_AVAILABLE = False
        if name == "MODERNBERT_AVAILABLE":
            return MODERNBERT_AVAILABLE
        return globals().get(name)
    # ModelEngine + ModernBertModelAdapter — Sprint F222
    if name in ("ModelEngine", "ModernBertModelAdapter", "MODEL_ENGINE_AVAILABLE"):
        global MODEL_ENGINE_AVAILABLE, ModelEngine, ModernBertModelAdapter
        try:
            from . import model_engine as _me
            from . import modernbert_adapter as _ma
            MODEL_ENGINE_AVAILABLE = True
            ModelEngine = _me.ModelEngine
            ModernBertModelAdapter = _ma.ModernBertModelAdapter
        except ImportError:
            MODEL_ENGINE_AVAILABLE = False
        if name == "MODEL_ENGINE_AVAILABLE":
            return MODEL_ENGINE_AVAILABLE
        return globals().get(name)
    # ModelManager
    if name in ("ModelManager", "ModelType", "get_model_manager",
                "reset_model_manager", "MODEL_MANAGER_AVAILABLE"):
        global MODEL_MANAGER_AVAILABLE, ModelManager, ModelType, get_model_manager, reset_model_manager
        try:
            from . import model_manager as _mm
            MODEL_ENGINE_AVAILABLE = getattr(_mm, 'MODEL_MANAGER_AVAILABLE', True)
            ModelManager = _mm.ModelManager
            ModelType = _mm.ModelType
            get_model_manager = _mm.get_model_manager
            reset_model_manager = _mm.reset_model_manager
        except ImportError:
            MODEL_MANAGER_AVAILABLE = False
        if name == "MODEL_MANAGER_AVAILABLE":
            return MODEL_MANAGER_AVAILABLE
        return globals().get(name)
    # NER Engine
    if name in (
        "Entity", "IOCScorer", "NEREngine",
        "extract_iocs_from_text", "get_ner_engine", "reset_ner_engine",
        "NER_ENGINE_AVAILABLE",
    ):
        global NER_ENGINE_AVAILABLE, Entity, IOCScorer, NEREngine, extract_iocs_from_text, get_ner_engine, reset_ner_engine
        try:
            from . import ner_engine as _ne
            NER_ENGINE_AVAILABLE = True
            Entity = _ne.Entity
            IOCScorer = _ne.IOCScorer
            NEREngine = _ne.NEREngine
            extract_iocs_from_text = _ne.extract_iocs_from_text
            get_ner_engine = _ne.get_ner_engine
            reset_ner_engine = _ne.reset_ner_engine
        except ImportError:
            NER_ENGINE_AVAILABLE = False
        if name == "NER_ENGINE_AVAILABLE":
            return NER_ENGINE_AVAILABLE
        return globals().get(name)
    # Embedding pipeline
    if name in ("load_embedding_model", "unload_embedding_model", "EMBEDDING_AVAILABLE"):
        global EMBEDDING_AVAILABLE, load_embedding_model, unload_embedding_model
        try:
            from .. import embedding_pipeline as _ep
            EMBEDDING_AVAILABLE = True
            load_embedding_model = _ep.load_embedding_model
            unload_embedding_model = _ep.unload_embedding_model
        except ImportError:
            EMBEDDING_AVAILABLE = False
        if name == "EMBEDDING_AVAILABLE":
            return EMBEDDING_AVAILABLE
        return globals().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ─── Capability Catalog ──────────────────────────────────────────────────────
# Explicit catalog of brain engine availability. Callers should use
# is_brain_engine_available("insight") rather than checking _AVAILABLE directly.
AVAILABLE_BRAIN_ENGINES = {
    # Phase 2: Modular Brain Components (None = not yet probed via __getattr__)
    "metal": None,
    "cache": None,
    "batch": None,
    # Legacy engines
    "insight": None,
    "inference": None,
    "hypothesis": None,
    "moe": None,
    "distillation": None,
    "modernbert": None,
    "model_manager": None,
    "ner_engine": None,
    "embedding": None,
}


# Map from public name → _AVAILABLE flag attribute name
_ENGINE_FLAG_MAP = {
    "metal": "METAL_AVAILABLE",
    "cache": "CACHE_AVAILABLE",
    "batch": "BATCH_AVAILABLE",
    "insight": "INSIGHT_AVAILABLE",
    "inference": "INFERENCE_AVAILABLE",
    "hypothesis": "HYPOTHESIS_AVAILABLE",
    "moe": "MOE_AVAILABLE",
    "distillation": "DISTILLATION_AVAILABLE",
    "modernbert": "MODERNBERT_AVAILABLE",
    "model_manager": "MODEL_MANAGER_AVAILABLE",
    "ner_engine": "NER_ENGINE_AVAILABLE",
    "embedding": "EMBEDDING_AVAILABLE",
}


def is_brain_engine_available(name: str) -> bool:
    """
    Runtime capability check for brain engines.

    Args:
        name: Engine name ("metal", "cache", "batch", "insight", "inference",
               "hypothesis", "moe", "distillation", "modernbert", "model_manager",
               "ner_engine", "embedding")

    Returns:
        True if the engine is available and its symbols are importable.
        Triggers __getattr__ probe for lazy engines on first call.
    """
    val = AVAILABLE_BRAIN_ENGINES.get(name, False)
    if val is None:
        # Trigger __getattr__ for the corresponding _AVAILABLE flag
        flag_name = _ENGINE_FLAG_MAP.get(name, name)
        getattr(__import__(__name__), flag_name)
        val = AVAILABLE_BRAIN_ENGINES.get(name, False)
    return bool(val)


def get_available_brain_engines() -> dict[str, bool]:
    """Return the full capability catalog as a dict (None → False)."""
    return {k: bool(v) for k, v in AVAILABLE_BRAIN_ENGINES.items()}

__all__ = [
    "DeepHermes3Engine",
    "parse_thinking_output",
    # Phase 2: Modular Brain Components (extracted from DeepHermes3Engine)
    "METAL_AVAILABLE",
    "CACHE_AVAILABLE",
    "BATCH_AVAILABLE",
    # Sprint P0-2: Continuous batching executor
    "MLXBatchedExecutor",
    "MLX_BATCHED_EXECUTOR_AVAILABLE",
    # Sprint LoRA-1: LoRA fine-tuning adapter
    "LORA_AVAILABLE",
    # Sprint P0-3: Dedicated MLX worker thread
    "MLXWorkerThread",
    "MLX_WORKER_THREAD_AVAILABLE",
    # Sprint P2-1b: Inference pipeliner with prompt preprocessing overlap
    "InferencePipeliner",
    "INFERENCE_PIPELINER_AVAILABLE",
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

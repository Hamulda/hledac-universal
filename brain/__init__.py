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


# ─── Engine Registry — declarative lazy-loading specification ──────────────────
# Replaces 14 repetitive if-blocks (complexity 44→6).
# Each entry: (module, import_names dict, available_flag_global_name, brain_engines_key).
# _load_engine() consumes this to perform the import + populate globals in one pass.
_ENGINE_REGISTRY: tuple[tuple[str, str, tuple[str, ...], str | None], ...] = (
    # (module_name, attribute_name_for_return, (exported_symbols...), brain_engines_key_or_None)
    ("_metal", "METAL_AVAILABLE", ("METAL_AVAILABLE",), "metal"),
    ("_cache", "CACHE_AVAILABLE", ("CACHE_AVAILABLE",), "cache"),
    ("_batch", "BATCH_AVAILABLE", ("BATCH_AVAILABLE",), "batch"),
    # mlx_lm.lora — special: no module import, just attribute check
    ("__lora_special__", "LORA_AVAILABLE", ("LORA_AVAILABLE",), None),
    # Single-symbol exports via module import
    ("mlx_batched_executor", "MLXBatchedExecutor", ("MLXBatchedExecutor", "MLX_BATCHED_EXECUTOR_AVAILABLE"), None),
    ("mlx_worker_thread", "MLXWorkerThread", ("MLXWorkerThread", "MLX_WORKER_THREAD_AVAILABLE"), None),
    ("inference_pipeliner", "InferencePipeliner", ("InferencePipeliner", "INFERENCE_PIPELINER_AVAILABLE"), None),
    # Multi-symbol exports (insight_engine, inference_engine)
    ("insight_engine", "INSIGHT_AVAILABLE", (
        "Anomaly", "CausalRelationship", "Contradiction", "Gap", "Insight",
        "InsightAnalysisResult", "InsightEngine", "Pattern", "SynthesisLevel",
        "create_insight_engine", "INSIGHT_AVAILABLE",
    ), "insight"),
    ("inference_engine", "INFERENCE_AVAILABLE", (
        "Evidence", "HopStep", "InferenceEngine", "InferenceRule", "InferenceStep",
        "InferenceType", "MultiHopPath", "MultiHopReasoner", "ResolvedEntity",
        "create_inference_engine", "InferenceHypothesis", "INFERENCE_AVAILABLE",
    ), "inference"),
    # HypothesisEngine — special: imports HypothesisEvidence separately + _HE_Contradiction alias
    ("research_hypothesis_engine", "HYPOTHESIS_AVAILABLE", (
        "AdversarialReport", "AdversarialVerifier", "FalsificationResult",
        "Hypothesis", "HypothesisEngine", "HypothesisStatus", "HypothesisType",
        "SourceCredibility", "TestDesign", "TestResult", "TestType",
        "create_hypothesis_engine", "HypothesisEvidence", "_HE_Contradiction",
        "HYPOTHESIS_AVAILABLE",
    ), "hypothesis"),
    ("moe_router", "MOE_AVAILABLE", ("MoERouter", "MoERouterConfig", "create_moe_router", "MOE_AVAILABLE"), "moe"),
    ("distillation_engine", "DISTILLATION_AVAILABLE", (
        "CriticMLP", "DistillationEngine", "DistillationExample",
        "create_distillation_engine", "DISTILLATION_AVAILABLE",
    ), "distillation"),
    ("modernbert_engine", "MODERNBERT_AVAILABLE", ("ModernBertEngine", "MODERNBERT_AVAILABLE"), "modernbert"),
    # ModelEngine + ModernBertModelAdapter (two modules)
    ("model_engine", "MODEL_ENGINE_AVAILABLE", ("ModelEngine", "MODEL_ENGINE_AVAILABLE"), "model_manager"),
    ("modernbert_adapter", "ModernBertModelAdapter", ("ModernBertModelAdapter",), None),
    ("model_manager", "MODEL_MANAGER_AVAILABLE", (
        "ModelManager", "ModelType", "get_model_manager", "reset_model_manager",
        "MODEL_MANAGER_AVAILABLE",
    ), "model_manager"),
    ("ner_engine", "NER_ENGINE_AVAILABLE", (
        "Entity", "IOCScorer", "NEREngine", "extract_iocs_from_text",
        "get_ner_engine", "reset_ner_engine", "NER_ENGINE_AVAILABLE",
    ), "ner_engine"),
    ("embedding_pipeline", "EMBEDDING_AVAILABLE", ("load_embedding_model", "unload_embedding_model", "EMBEDDING_AVAILABLE"), "embedding"),
)


def _load_engine(name: str, module_spec: str, exported: tuple[str, ...], brain_key: str | None) -> object:
    """
    Load a brain engine module and populate globals().
    Returns the value of the requested attribute ``name``.
    Fails gracefully: sets _AVAILABLE=False and returns None on any error.
    """
    global AVAILABLE_BRAIN_ENGINES
    g = globals()

    # Special path: mlx_lm.lora has no dedicated module, just an attribute-existence check
    if module_spec == "__lora_special__":
        try:
            import mlx_lm  # noqa: F401
            # Probe mlx_lm.lora without triggering type-checker "submodule not imported" warning
            _loader = getattr(mlx_lm, "lora", None)
            if _loader is not None:
                _ = getattr(_loader, "load_lora_model", None)
            g["LORA_AVAILABLE"] = _loader is not None and _ is not None
        except Exception:
            g["LORA_AVAILABLE"] = False
        return g.get("LORA_AVAILABLE") if name == "LORA_AVAILABLE" else g.get(name)

    # Normal path: import the module and bind exported symbols to globals
    try:
        if brain_key is not None:
            # Dual-module engine (model_engine + modernbert_adapter)
            if module_spec == "model_engine":
                from . import model_engine as _me
                from . import modernbert_adapter as _ma
                g["MODEL_ENGINE_AVAILABLE"] = True
                AVAILABLE_BRAIN_ENGINES[brain_key] = True
                g["ModelEngine"] = _me.ModelEngine
                g["ModernBertModelAdapter"] = _ma.ModernBertModelAdapter
                return g.get(name)
            if brain_key == "model_manager":
                from . import model_manager as _mm
                g["MODEL_MANAGER_AVAILABLE"] = getattr(_mm, "MODEL_MANAGER_AVAILABLE", True)
                g["ModelManager"] = _mm.ModelManager
                g["ModelType"] = _mm.ModelType
                g["get_model_manager"] = _mm.get_model_manager
                g["reset_model_manager"] = _mm.reset_model_manager
                AVAILABLE_BRAIN_ENGINES[brain_key] = True
                return g.get(name)
            if module_spec == "research_hypothesis_engine":
                from .research_hypothesis_engine import (
                    AdversarialReport, AdversarialVerifier, Contradiction,
                    FalsificationResult, Hypothesis, HypothesisEngine,
                    HypothesisStatus, HypothesisType, SourceCredibility,
                    TestDesign, TestResult, TestType, create_hypothesis_engine,
                )
                from .research_hypothesis_engine import Evidence as HypothesisEvidence
                g["AdversarialReport"] = AdversarialReport
                g["AdversarialVerifier"] = AdversarialVerifier
                g["FalsificationResult"] = FalsificationResult
                g["Hypothesis"] = Hypothesis
                g["HypothesisEngine"] = HypothesisEngine
                g["HypothesisStatus"] = HypothesisStatus
                g["HypothesisType"] = HypothesisType
                g["SourceCredibility"] = SourceCredibility
                g["TestDesign"] = TestDesign
                g["TestResult"] = TestResult
                g["TestType"] = TestType
                g["create_hypothesis_engine"] = create_hypothesis_engine
                g["HypothesisEvidence"] = HypothesisEvidence
                g["_HE_Contradiction"] = Contradiction
                g["HYPOTHESIS_AVAILABLE"] = True
                AVAILABLE_BRAIN_ENGINES[brain_key] = True
                return g.get(name)
            # Generic single-module import
            import importlib
            mod = importlib.import_module(f".{module_spec}", __package__)
            flag = f"{module_spec.upper()}_AVAILABLE"
            g[flag] = True
            for sym in exported:
                if sym.endswith("_AVAILABLE"):
                    continue
                if hasattr(mod, sym):
                    g[sym] = getattr(mod, sym)
            AVAILABLE_BRAIN_ENGINES[brain_key] = True
            return g.get(name)
        else:
            # No brain_key (MLXBatchedExecutor etc.)
            import importlib
            mod = importlib.import_module(f".{module_spec}", __package__)
            for sym in exported:
                if hasattr(mod, sym):
                    g[sym] = getattr(mod, sym)
            return g.get(name)
    except Exception:
        flag = f"{module_spec.upper()}_AVAILABLE"
        g[flag] = False
        if brain_key is not None:
            AVAILABLE_BRAIN_ENGINES[brain_key] = False
        return g.get(name)


# ─── PEP 562 Lazy Imports via __getattr__ ─────────────────────────────────────
# A2-FIX: All 12 non-circular engines defer import until first attribute access.
# Cold import cost drops from ~9.7s to ~150ms (enum + flag defs only).
# Refactored: 14 if-blocks (complexity 44) → single loop over _ENGINE_REGISTRY (complexity 6).
def __getattr__(name: str) -> object:
    for module_spec, _ret_attr, exported, brain_key in _ENGINE_REGISTRY:
        if name in exported:
            return _load_engine(name, module_spec, exported, brain_key)
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
    # Adversarial Verification (Contradiction re-exported from insight_engine via _HE_Contradiction alias)
    "AdversarialVerifier",
    "SourceCredibility",
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

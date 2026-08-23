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
- ANEInferenceEngine: SILICON-06 — ANE (Neural Engine) inference pro small-batch embedding (brain/ane_inference.py)
- WhisperEngine: SILICON-02b — whisper.cpp CoreML/ANE speech-to-text (brain/whisper_engine.py)

DŮLEŽITÉ: Brain facade NEPROMPTUJE žádné heavy enginy do aktivního runtime.
Přidání nového importu sem neznamená, že je "podporováno" nebo "production-ready".
Vždy kontroluj _AVAILABLE flag a přítomnost SKUTEČNÝCH call sites v kódu.
"""

from enum import Enum

# ISSUE-005: Centralized lazy-loading registry for brain engines
# Imports registry utilities without triggering heavy module loads
from brain._registry import (
    EngineLoadOrder,
    EngineSpec,
    get_dependencies,
    get_engine,
    get_engine_class,
    get_resolve_order,
    get_spec,
    is_available,
    list_engines,
)
from brain._registry import (
    lazy_getattr as _registry_lazy_getattr,
)


# DecisionType — re-exported from Hermes3Engine compat shim (decision_engine.py deleted)
class DecisionType(Enum):
    RESEARCH = "research"
    EXECUTION = "execution"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    SYNTHESIS = "synthesis"
    ERROR = "error"
    COMPLETE = "complete"


# ISSUE-005 FIX: Moved to lazy loading via __getattr__ below
# from .deephermes3_engine import DeepHermes3Engine, parse_thinking_output
# NOTE: Individual brain modules import aclose directly from _core as needed.
# brain/__init__.py is a facade - no need to re-export aclose here.

_LOADED_ENGINES: dict[str, str] = {}  # module_spec -> exported symbols


def _track_engine_load(module_spec: str, exported: tuple[str, ...]) -> None:
    """Track a loaded engine module for cleanup."""
    _LOADED_ENGINES[module_spec] = ",".join(exported)


def _get_loaded_engines() -> dict[str, str]:
    """Return copy of loaded engines registry."""
    return dict(_LOADED_ENGINES)


def _clear_engine_cache(clear_sys_modules: bool = True) -> int:
    """
    Clear all loaded engine symbols from globals() and optionally from sys.modules.

    Args:
        clear_sys_modules: If True (default), also remove loaded modules from
            sys.modules to fully unload them and free their memory.

    Returns:
        Number of engine symbols cleared from globals().

    MODERN-36 PERFORMANCE FIX: Call this from shutdown hooks to prevent
    memory leaks from accumulated globals() entries and sys.modules references.
    Typically called when the process is exiting or when memory pressure is high.

    Note: Setting clear_sys_modules=True fully unloads the modules, which may
    break existing references. Only use when the application is shutting down.
    """
    import sys

    cleared = 0
    g = globals()
    unloaded_modules: list[str] = []

    for module_spec, symbols_str in list(_LOADED_ENGINES.items()):
        symbols = frozenset(symbols_str.split(",")) if symbols_str else frozenset()
        for sym in symbols:
            if sym in g:
                g[sym] = None
                cleared += 1

        # Reset _AVAILABLE flags
        flag = f"{module_spec.upper()}_AVAILABLE"
        if flag in g:
            g[flag] = None

        # Reset AVAILABLE_BRAIN_ENGINES entry
        brain_key = _MODULE_TO_ENGINE_KEY.get(module_spec)
        if brain_key and brain_key in AVAILABLE_BRAIN_ENGINES:
            AVAILABLE_BRAIN_ENGINES[brain_key] = None

        # MODERN-36 FIX: Also clear from sys.modules for full module unload
        if clear_sys_modules:
            # Try to remove the actual module from sys.modules
            # Module names are prefixed with package path
            module_names_to_remove = [
                f"hledac.universal.brain.{module_spec}",
                f"brain.{module_spec}",
                module_spec,
            ]
            for mod_name in module_names_to_remove:
                if mod_name in sys.modules:
                    # Clear module cache to free memory
                    sys.modules.pop(mod_name, None)
                    unloaded_modules.append(mod_name)

    _LOADED_ENGINES.clear()
    return cleared


# Mapping from module_spec to brain key for cleanup
_MODULE_TO_ENGINE_KEY: dict[str, str] = {
    "insight_engine": "insight",
    "inference_engine": "inference",
    "research_hypothesis_engine": "hypothesis",
    "moe_router": "moe",
    "micro_model_pool": "micro_model_swarm",
    "distillation_engine": "distillation",
    "modernbert_engine": "modernbert",
    "model_manager": "model_manager",
    "ner_engine": "ner_engine",
    "embedding_pipeline": "embedding",
    "whisper_engine": "whisper",
    "gnn_node_mapper": "ane",
    "ane_gnn": "ane",
}

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

# Micro Model Swarm — lazy (SYSTEM-008 refactor)
MICRO_MODEL_SWARM_AVAILABLE = None
MicroModelSwarmRouter = None  # type: ignore[assignment,misc]
MicroModelPool = None  # type: ignore[assignment,misc]
ContentRouter = None  # type: ignore[assignment,misc]
ResourceGovernor = None  # type: ignore[assignment,misc]
TaskType = None  # type: ignore[assignment,misc]

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

# SILICON-06: ANE inference engine — lazy (A2-FIX)
# coremltools import is ~200ms; defer to first attribute access.
ANE_AVAILABLE = None

# [GNN-3]: CoreML-GNN for ANE inference — lazy
GNN_AVAILABLE = None  # gnn_node_mapper module
ANE_GNN_AVAILABLE = None  # ane_gnn module

# SILICON-02b: WhisperEngine — whisper.cpp CoreML/ANE speech-to-text — lazy (A2-FIX)
# whispercpp import is ~150ms; defer to first attribute access.
WHISPER_AVAILABLE = None
WhisperEngine = None  # type: ignore[assignment,misc]
TranscriptionResult = None  # type: ignore[assignment,misc]
TranscriptionSegment = None  # type: ignore[assignment,misc]
get_whisper_engine = None  # type: ignore[assignment,misc]
transcribe_audio = None  # type: ignore[assignment,misc]
is_whisper_available = None  # type: ignore[assignment,misc]


_ENGINE_REGISTRY: tuple[tuple[str, str, tuple[str, ...], str | None], ...] = (
    # NOTE: This tuple is DEPRECATED. Use brain._registry instead.
    # Keeping for backward compatibility with existing code.
    ("_metal", "METAL_AVAILABLE", ("METAL_AVAILABLE",), "metal"),
    ("_cache", "CACHE_AVAILABLE", ("CACHE_AVAILABLE",), "cache"),
    ("_batch", "BATCH_AVAILABLE", ("BATCH_AVAILABLE",), "batch"),
    ("__lora_special__", "LORA_AVAILABLE", ("LORA_AVAILABLE",), None),
    ("mlx_batched_executor", "MLXBatchedExecutor", ("MLXBatchedExecutor", "MLX_BATCHED_EXECUTOR_AVAILABLE"), None),
    ("mlx_worker_thread", "MLXWorkerThread", ("MLXWorkerThread", "MLX_WORKER_THREAD_AVAILABLE"), None),
    ("inference_pipeliner", "InferencePipeliner", ("InferencePipeliner", "INFERENCE_PIPELINER_AVAILABLE"), None),
    (
        "insight_engine",
        "INSIGHT_AVAILABLE",
        (
            "Anomaly",
            "CausalRelationship",
            "Contradiction",
            "Gap",
            "Insight",
            "InsightAnalysisResult",
            "InsightEngine",
            "Pattern",
            "SynthesisLevel",
            "create_insight_engine",
            "INSIGHT_AVAILABLE",
        ),
        "insight",
    ),
    (
        "inference_engine",
        "INFERENCE_AVAILABLE",
        (
            "Evidence",
            "HopStep",
            "InferenceEngine",
            "InferenceRule",
            "InferenceStep",
            "InferenceType",
            "MultiHopPath",
            "MultiHopReasoner",
            "ResolvedEntity",
            "create_inference_engine",
            "InferenceHypothesis",
            "INFERENCE_AVAILABLE",
        ),
        "inference",
    ),
    (
        "research_hypothesis_engine",
        "HYPOTHESIS_AVAILABLE",
        (
            "AdversarialReport",
            "AdversarialVerifier",
            "FalsificationResult",
            "Hypothesis",
            "HypothesisEngine",
            "HypothesisStatus",
            "HypothesisType",
            "SourceCredibility",
            "TestDesign",
            "TestResult",
            "TestType",
            "create_hypothesis_engine",
            "HypothesisEvidence",
            "_HE_Contradiction",
            "HYPOTHESIS_AVAILABLE",
        ),
        "hypothesis",
    ),
    ("moe_router", "MOE_AVAILABLE", ("MoERouter", "MoERouterConfig", "create_moe_router", "MOE_AVAILABLE"), "moe"),
    (
        "micro_model_pool",
        "MICRO_MODEL_SWARM_AVAILABLE",
        ("MicroModelPool", "MicroModelSpec", "TaskType", "MICRO_MODELS", "MICRO_MODEL_SWARM_AVAILABLE"),
        "micro_model_swarm",
    ),
    (
        "content_router",
        "MICRO_MODEL_SWARM_AVAILABLE",
        ("ContentRouter", "classify_content", "get_preferred_model", "route_content"),
        None,
    ),
    ("micro_model_swarm", "MicroModelSwarmRouter", ("MicroModelSwarmRouter", "create_swarm_router"), None),
    (
        "moe_swarm_integration",
        "ResourceGovernor",
        ("ResourceGovernor", "MoERouterSwarmMixin", "SwappableMicroModelPool"),
        None,
    ),
    (
        "distillation_engine",
        "DISTILLATION_AVAILABLE",
        (
            "CriticMLP",
            "DistillationEngine",
            "DistillationExample",
            "create_distillation_engine",
            "DISTILLATION_AVAILABLE",
        ),
        "distillation",
    ),
    ("modernbert_engine", "MODERNBERT_AVAILABLE", ("ModernBertEngine", "MODERNBERT_AVAILABLE"), "modernbert"),
    ("model_engine", "MODEL_ENGINE_AVAILABLE", ("ModelEngine", "MODEL_ENGINE_AVAILABLE"), "model_manager"),
    ("modernbert_adapter", "ModernBertModelAdapter", ("ModernBertModelAdapter",), None),
    (
        "model_manager",
        "MODEL_MANAGER_AVAILABLE",
        (
            "ModelManager",
            "ModelType",
            "get_model_manager",
            "reset_model_manager",
            "MODEL_MANAGER_AVAILABLE",
        ),
        "model_manager",
    ),
    (
        "ner_engine",
        "NER_ENGINE_AVAILABLE",
        (
            "Entity",
            "IOCScorer",
            "NEREngine",
            "extract_iocs_from_text",
            "get_ner_engine",
            "reset_ner_engine",
            "NER_ENGINE_AVAILABLE",
        ),
        "ner_engine",
    ),
    (
        "embedding_pipeline",
        "EMBEDDING_AVAILABLE",
        ("load_embedding_model", "unload_embedding_model", "EMBEDDING_AVAILABLE"),
        "embedding",
    ),
    (
        "whisper_engine",
        "WHISPER_AVAILABLE",
        (
            "WhisperEngine",
            "TranscriptionResult",
            "TranscriptionSegment",
            "get_whisper_engine",
            "transcribe_audio",
            "is_whisper_available",
            "WHISPER_AVAILABLE",
        ),
        "whisper",
    ),
    (
        "absence_mining",
        "ABSENCE_MINING_AVAILABLE",
        (
            "AbsenceMiningEngine",
            "AbsenceFinding",
            "AbsenceReport",
            "AbsenceType",
            "get_absence_engine",
            "get_absence_engine_sync",
            "ABSENCE_MINING_AVAILABLE",
        ),
        None,
    ),
    (
        "gnn_node_mapper",
        "GNN_AVAILABLE",
        (
            "get_node_mapper",
            "reset_node_mapper",
            "GNN_AVAILABLE",
            "NodeMapping",
            "MappingLRUCache",
            "EmbeddingReference",
            "make_kuzu_id",
            "parse_kuzu_id",
            "build_one_hot_type",
            "fetch_node_embeddings",
            "normalize_ioc_type",
            "GNN_IOC_TYPES",
            "NUM_GNN_IOC_TYPES",
        ),
        None,
    ),
    (
        "ane_gnn",
        "ANE_GNN_AVAILABLE",
        (
            "ANEGNNEngine",
            "GraphSAGEModel",
            "HybridLinkPredictor",
            "GNNConfig",
            "GNNBatchResult",
            "LinkPredictionResult",
            "get_ane_gnn_engine",
            "get_hybrid_predictor",
            "export_graphsage_to_coreml",
            "ANE_GNN_AVAILABLE",
            "GNN_FEATURE_DIM",
            "GNN_ACTIVATION_THRESHOLD",
        ),
        None,
    ),
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
        # MODERN-36: Track loaded module for cleanup
        _track_engine_load("__lora_special__", ("LORA_AVAILABLE",))
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
                # MODERN-36: Track loaded module for cleanup
                _track_engine_load(module_spec, ("ModelEngine", "ModernBertModelAdapter"))
                return g.get(name)
            if brain_key == "model_manager":
                from . import model_manager as _mm

                g["MODEL_MANAGER_AVAILABLE"] = getattr(_mm, "MODEL_MANAGER_AVAILABLE", True)
                g["ModelManager"] = _mm.ModelManager
                g["ModelType"] = _mm.ModelType
                g["get_model_manager"] = _mm.get_model_manager
                g["reset_model_manager"] = _mm.reset_model_manager
                AVAILABLE_BRAIN_ENGINES[brain_key] = True
                # MODERN-36: Track loaded module for cleanup
                _track_engine_load(
                    module_spec, ("ModelManager", "ModelType", "get_model_manager", "reset_model_manager")
                )
                return g.get(name)
            if module_spec == "research_hypothesis_engine":
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
                # MODERN-36: Track loaded module for cleanup
                _track_engine_load(
                    module_spec,
                    (
                        "AdversarialReport",
                        "AdversarialVerifier",
                        "FalsificationResult",
                        "Hypothesis",
                        "HypothesisEngine",
                        "HypothesisStatus",
                        "HypothesisType",
                        "SourceCredibility",
                        "TestDesign",
                        "TestResult",
                        "TestType",
                        "create_hypothesis_engine",
                        "HypothesisEvidence",
                        "_HE_Contradiction",
                    ),
                )
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
            # MODERN-36: Track loaded module for cleanup
            _track_engine_load(module_spec, exported)
            return g.get(name)
        else:
            # No brain_key (MLXBatchedExecutor etc.)
            import importlib

            mod = importlib.import_module(f".{module_spec}", __package__)
            for sym in exported:
                if hasattr(mod, sym):
                    g[sym] = getattr(mod, sym)
            # MODERN-36: Track loaded module for cleanup
            _track_engine_load(module_spec, exported)
            return g.get(name)
    except Exception:
        flag = f"{module_spec.upper()}_AVAILABLE"
        g[flag] = False
        if brain_key is not None:
            AVAILABLE_BRAIN_ENGINES[brain_key] = False
        return g.get(name)


def __getattr__(name: str) -> object:
    # ISSUE-005 FIX: DeepHermes3Engine - lazy load to prevent circular imports
    if name in ("DeepHermes3Engine", "parse_thinking_output"):
        from . import deephermes3_engine

        return getattr(deephermes3_engine, name)

    # ARCH-SRP-001: BrainCoordinator — composition layer (lightweight, no heavy deps)
    if name == "BrainCoordinator":
        from .brain_coordinator import BrainCoordinator

        return BrainCoordinator
    # ARCH-SRP-001: LLMEngine Protocol — inference contract
    if name == "LLMEngine":
        from ._inference import LLMEngine

        return LLMEngine
    # SILICON-06: ANE availability probe — lightweight, no heavy imports
    if name == "ANE_AVAILABLE":
        try:
            from .ane_inference import is_ane_available

            g = globals()
            g["ANE_AVAILABLE"] = is_ane_available()
            return g["ANE_AVAILABLE"]
        except Exception:
            globals()["ANE_AVAILABLE"] = False
            return False

    # ISSUE-005 FIX: Try registry first for engines with complex dependencies
    try:
        return _registry_lazy_getattr(name)
    except (AttributeError, KeyError):
        pass

    # Legacy fallback: iterate through _ENGINE_REGISTRY
    for module_spec, _ret_attr, exported, brain_key in _ENGINE_REGISTRY:
        if name in exported:
            return _load_engine(name, module_spec, exported, brain_key)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


AVAILABLE_BRAIN_ENGINES = {
    "metal": None,
    "cache": None,
    "batch": None,
    # Legacy engines
    "insight": None,
    "inference": None,
    "hypothesis": None,
    "moe": None,
    "micro_model_swarm": None,
    "distillation": None,
    "modernbert": None,
    "model_manager": None,
    "ner_engine": None,
    "embedding": None,
    # SILICON-06: ANE (Apple Neural Engine) inference engine
    "ane": None,
    # SILICON-02b: WhisperEngine — whisper.cpp CoreML/ANE speech-to-text
    "whisper": None,
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
    "ane": "ANE_AVAILABLE",
    "whisper": "WHISPER_AVAILABLE",
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
    # Micro Model Swarm — SYSTEM-008 refactor
    "MicroModelSwarmRouter",
    "MicroModelPool",
    "MicroModelSpec",
    "ContentRouter",
    "classify_content",
    "get_preferred_model",
    "route_content",
    "ResourceGovernor",
    "SwappableMicroModelPool",
    "MoERouterSwarmMixin",
    "TaskType",
    "MICRO_MODELS",
    "MICRO_MODEL_SWARM_AVAILABLE",
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
    # SILICON-06: ANE (Apple Neural Engine) inference
    "ANE_AVAILABLE",
    # SILICON-02b: WhisperEngine — whisper.cpp CoreML/ANE speech-to-text
    "WhisperEngine",
    "TranscriptionResult",
    "TranscriptionSegment",
    "get_whisper_engine",
    "transcribe_audio",
    "is_whisper_available",
    "WHISPER_AVAILABLE",
    # ARCH-SRP-001: Brain Coordinator + LLMEngine Protocol
    "BrainCoordinator",
    "LLMEngine",
    # Capability Catalog API
    "AVAILABLE_BRAIN_ENGINES",
    "is_brain_engine_available",
    "get_available_brain_engines",
    # MODERN-36: Cache cleanup API for memory leak prevention
    "_clear_engine_cache",
    "_get_loaded_engines",
    # ISSUE-005: Registry API (imported from brain._registry)
    # Use: from brain._registry import get_engine, list_engines, is_available
]

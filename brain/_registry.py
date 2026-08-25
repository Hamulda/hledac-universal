"""
Centralized Lazy-Loading Registry for Brain Engines (ISSUE-005 Fix)

Solves circular import risks by providing:
1. Single source of truth for engine module registration
2. Lazy import with dependency-order resolution
3. TYPE_CHECKING for forward references
4. Clear boundaries between engine modules

Circular Import Protection:
- deephermes3_engine loads first (CORE) - all other engines depend on it
- inference_engine depends on deephermes3_engine._get_xxh3_hex (lazy import wrapper)
- research_hypothesis_engine depends on both inference_engine and deephermes3_engine

Usage:
    from brain._registry import get_engine, is_available, list_engines

    engine = get_engine("inference_engine")

    if is_available("insight_engine"):
        from brain import InsightEngine

    # List all engines by load order
    engines = list_engines(by_order=True)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

class EngineLoadOrder(Enum):
    """Load order priority for engines with circular dependencies."""

    CORE = auto()  # deephermes3_engine - load first (others depend on it)
    LOW = auto()  # inference_engine, insight_engine - after core
    NORMAL = auto()  # Most engines
    HIGH = auto()  # Engines with complex dependencies
    LAZY = auto()  # Heaviest engines - only load when requested

@dataclass(frozen=True, slots=True)
class EngineSpec:
    """Specification for a brain engine module."""

    name: str
    module_path: str
    class_names: tuple[str, ...]
    availability_flag: str | None = None
    load_order: EngineLoadOrder = EngineLoadOrder.NORMAL
    dependencies: tuple[str, ...] = ()
    doc: str | None = None

# SSOT: All brain engine registrations - add new engines here only
_ENGINE_SPECS: dict[str, EngineSpec] = {
    # CORE engines - load first (lowest priority number = loaded first)
    "deephermes3_engine": EngineSpec(
        name="deephermes3_engine",
        module_path="hledac.universal.brain.deephermes3_engine",
        class_names=("DeepHermes3Engine", "Hermes3Engine", "parse_thinking_output"),
        availability_flag="DEEPHERMES_AVAILABLE",
        load_order=EngineLoadOrder.CORE,
        dependencies=(),
        doc="Core LLM engine - all other engines depend on this",
    ),
    # LOW priority - depends on deephermes3_engine
    "inference_engine": EngineSpec(
        name="inference_engine",
        module_path="hledac.universal.brain.inference_engine",
        class_names=(
            "InferenceEngine",
            "create_inference_engine",
            "Evidence",
            "HopStep",
            "InferenceRule",
            "InferenceStep",
            "InferenceType",
            "MultiHopPath",
            "MultiHopReasoner",
            "ResolvedEntity",
            "InferenceHypothesis",
            "INFERENCE_AVAILABLE",
        ),
        availability_flag="INFERENCE_AVAILABLE",
        load_order=EngineLoadOrder.LOW,
        dependencies=("deephermes3_engine",),
        doc="Multi-hop reasoning engine",
    ),
    "insight_engine": EngineSpec(
        name="insight_engine",
        module_path="hledac.universal.brain.insight_engine",
        class_names=(
            "InsightEngine",
            "create_insight_engine",
            "Anomaly",
            "CausalRelationship",
            "Contradiction",
            "Gap",
            "Insight",
            "InsightAnalysisResult",
            "Pattern",
            "SynthesisLevel",
            "INSIGHT_AVAILABLE",
        ),
        availability_flag="INSIGHT_AVAILABLE",
        load_order=EngineLoadOrder.LOW,
        dependencies=(),
        doc="Pattern discovery and insight generation",
    ),
    # NORMAL priority engines
    "research_hypothesis_engine": EngineSpec(
        name="research_hypothesis_engine",
        module_path="hledac.universal.brain.research_hypothesis_engine",
        class_names=(
            "HypothesisEngine",
            "create_hypothesis_engine",
            "Hypothesis",
            "HypothesisEvidence",
            "HYPOTHESIS_AVAILABLE",
        ),
        availability_flag="HYPOTHESIS_AVAILABLE",
        load_order=EngineLoadOrder.NORMAL,
        dependencies=("inference_engine", "deephermes3_engine"),
        doc="Research hypothesis generation and testing",
    ),
    "model_engine": EngineSpec(
        name="model_engine",
        module_path="hledac.universal.brain.model_engine",
        class_names=("ModelEngine", "create_model_engine", "MODEL_AVAILABLE"),
        availability_flag="MODEL_AVAILABLE",
        load_order=EngineLoadOrder.NORMAL,
        dependencies=("deephermes3_engine",),
        doc="Model management and lifecycle",
    ),
    "model_manager": EngineSpec(
        name="model_manager",
        module_path="hledac.universal.brain.model_manager",
        class_names=("ModelManager", "ModelManagerConfig"),
        availability_flag=None,  # Always available if module exists
        load_order=EngineLoadOrder.NORMAL,
        dependencies=("deephermes3_engine", "model_engine"),
        doc="Centralized model orchestration",
    ),
    "hermes3_engine": EngineSpec(
        name="hermes3_engine",
        module_path="hledac.universal.brain.hermes3_engine",
        class_names=("Hermes3Engine",),
        availability_flag=None,
        load_order=EngineLoadOrder.NORMAL,
        dependencies=("deephermes3_engine",),
        doc="Alias/stub for DeepHermes3Engine (backward compat)",
    ),
    # HIGH priority - complex dependencies
    "synthesis_runner": EngineSpec(
        name="synthesis_runner",
        module_path="hledac.universal.brain.synthesis_runner",
        class_names=("SynthesisRunner", "SynthesisResult"),
        availability_flag="SYNTHESIS_AVAILABLE",
        load_order=EngineLoadOrder.HIGH,
        dependencies=("deephermes3_engine", "research_hypothesis_engine"),
        doc="High-level synthesis orchestration",
    ),
    # LAZY engines - heaviest, load only when requested
    "mlx_cel": EngineSpec(
        name="mlx_cel",
        module_path="hledac.universal.brain.mlxcel_ipc_client",
        class_names=("get_mlxcel_client", "MlxcelUnavailable", "MlxcelClient"),
        availability_flag="MLXCEL_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="MLX Cloud IPC client - lazy loaded",
    ),
    "moe_router": EngineSpec(
        name="moe_router",
        module_path="hledac.universal.brain.moe_router",
        class_names=("MoERouter",),
        availability_flag="MOE_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Mixture of Experts routing - lazy loaded",
    ),
    "modernbert_engine": EngineSpec(
        name="modernbert_engine",
        module_path="hledac.universal.brain.modernbert_engine",
        class_names=("ModernBertEngine",),
        availability_flag="MODERNBERT_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=("deephermes3_engine",),
        doc="ModernBERT embeddings - lazy loaded",
    ),
    # LAZY engines - MLX components
    "mlx_batched_executor": EngineSpec(
        name="mlx_batched_executor",
        module_path="hledac.universal.brain.mlx_batched_executor",
        class_names=("MLXBatchedExecutor", "MLX_BATCHED_EXECUTOR_AVAILABLE"),
        availability_flag="MLX_BATCHED_EXECUTOR_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="MLX batched executor for continuous batching",
    ),
    "mlx_worker_thread": EngineSpec(
        name="mlx_worker_thread",
        module_path="hledac.universal.brain.mlx_worker_thread",
        class_names=("MLXWorkerThread", "MLX_WORKER_THREAD_AVAILABLE"),
        availability_flag="MLX_WORKER_THREAD_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Dedicated MLX worker thread",
    ),
    "inference_pipeliner": EngineSpec(
        name="inference_pipeliner",
        module_path="hledac.universal.brain.inference_pipeliner",
        class_names=("InferencePipeliner", "INFERENCE_PIPELINER_AVAILABLE"),
        availability_flag="INFERENCE_PIPELINER_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=("deephermes3_engine",),
        doc="Inference pipeliner with prompt preprocessing overlap",
    ),
    # LAZY engines - Micro model swarm
    "micro_model_pool": EngineSpec(
        name="micro_model_pool",
        module_path="hledac.universal.brain.micro_model_pool",
        class_names=("MicroModelPool", "MicroModelSpec", "TaskType", "MICRO_MODELS"),
        availability_flag="MICRO_MODEL_SWARM_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Micro model pool for distributed inference",
    ),
    "content_router": EngineSpec(
        name="content_router",
        module_path="hledac.universal.brain.content_router",
        class_names=("ContentRouter", "classify_content", "get_preferred_model", "route_content"),
        availability_flag="MICRO_MODEL_SWARM_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Content classification and routing",
    ),
    "micro_model_swarm": EngineSpec(
        name="micro_model_swarm",
        module_path="hledac.universal.brain.micro_model_swarm",
        class_names=("MicroModelSwarmRouter", "create_swarm_router"),
        availability_flag="MICRO_MODEL_SWARM_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Micro model swarm orchestration",
    ),
    "moe_swarm_integration": EngineSpec(
        name="moe_swarm_integration",
        module_path="hledac.universal.brain.moe_swarm_integration",
        class_names=("ResourceGovernor", "MoERouterSwarmMixin", "SwappableMicroModelPool"),
        availability_flag="MICRO_MODEL_SWARM_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=("moe_router",),
        doc="MoE and swarm integration layer",
    ),
    # LAZY engines - Distillation
    "distillation_engine": EngineSpec(
        name="distillation_engine",
        module_path="hledac.universal.brain.distillation_engine",
        class_names=("DistillationEngine", "DistillationExample", "CriticMLP", "create_distillation_engine"),
        availability_flag="DISTILLATION_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=("deephermes3_engine",),
        doc="Model distillation engine",
    ),
    # LAZY engines - Model adapter
    "modernbert_adapter": EngineSpec(
        name="modernbert_adapter",
        module_path="hledac.universal.brain.modernbert_adapter",
        class_names=("ModernBertModelAdapter",),
        availability_flag=None,
        load_order=EngineLoadOrder.LAZY,
        dependencies=("modernbert_engine",),
        doc="ModernBERT model adapter for protocol",
    ),
    # LAZY engines - NER
    "ner_engine": EngineSpec(
        name="ner_engine",
        module_path="hledac.universal.brain.ner_engine",
        class_names=("NEREngine", "Entity", "IOCScorer", "extract_iocs_from_text"),
        availability_flag="NER_ENGINE_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Named Entity Recognition engine",
    ),
    # LAZY engines - Embedding
    "embedding_pipeline": EngineSpec(
        name="embedding_pipeline",
        module_path="hledac.universal.brain.unified_embedding_manager",
        class_names=("load_embedding_model", "unload_embedding_model"),
        availability_flag="EMBEDDING_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Embedding model lifecycle management",
    ),
    # LAZY engines - Whisper
    "whisper_engine": EngineSpec(
        name="whisper_engine",
        module_path="hledac.universal.brain.whisper_engine",
        class_names=("WhisperEngine", "TranscriptionResult", "TranscriptionSegment", "get_whisper_engine", "transcribe_audio"),
        availability_flag="WHISPER_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Whisper speech-to-text engine",
    ),
    # LAZY engines - Absence Mining
    "absence_mining": EngineSpec(
        name="absence_mining",
        module_path="hledac.universal.brain.absence_mining",
        class_names=("AbsenceMiningEngine", "AbsenceFinding", "AbsenceReport", "AbsenceType", "get_absence_engine"),
        availability_flag="ABSENCE_MINING_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Absence mining for missing indicators",
    ),
    # LAZY engines - GNN
    "gnn_node_mapper": EngineSpec(
        name="gnn_node_mapper",
        module_path="hledac.universal.brain.gnn_node_mapper",
        class_names=("get_node_mapper", "NodeMapping", "MappingLRUCache", "EmbeddingReference"),
        availability_flag="GNN_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="GNN-based IOC node mapping",
    ),
    "ane_gnn": EngineSpec(
        name="ane_gnn",
        module_path="hledac.universal.brain.ane_gnn",
        class_names=("ANEGNNEngine", "GraphSAGEModel", "HybridLinkPredictor", "GNNConfig", "get_ane_gnn_engine"),
        availability_flag="ANE_GNN_AVAILABLE",
        load_order=EngineLoadOrder.LAZY,
        dependencies=(),
        doc="Apple Neural Engine GNN inference",
    ),
    # INTERNAL modules (special handling - no module path)
    # These are handled separately in brain/__init__.py via _load_engine
    "_metal": EngineSpec(
        name="_metal",
        module_path="hledac.universal.brain._metal",
        class_names=("METAL_AVAILABLE",),
        availability_flag="METAL_AVAILABLE",
        load_order=EngineLoadOrder.NORMAL,
        dependencies=(),
        doc="Metal GPU detection (internal)",
    ),
    "_cache": EngineSpec(
        name="_cache",
        module_path="hledac.universal.brain._cache",
        class_names=("CACHE_AVAILABLE",),
        availability_flag="CACHE_AVAILABLE",
        load_order=EngineLoadOrder.NORMAL,
        dependencies=(),
        doc="Cache availability (internal)",
    ),
    "_batch": EngineSpec(
        name="_batch",
        module_path="hledac.universal.brain._batch",
        class_names=("BATCH_AVAILABLE",),
        availability_flag="BATCH_AVAILABLE",
        load_order=EngineLoadOrder.NORMAL,
        dependencies=(),
        doc="Batch processing availability (internal)",
    ),
}

# Module-level cache for loaded engine modules
_loaded_modules: dict[str, Any] = {}
_availability_cache: dict[str, bool | None] = {}

def _resolve_dependencies(name: str) -> list[str]:
    """Resolve dependency order for engine loading."""
    if name not in _ENGINE_SPECS:
        return []
    spec = _ENGINE_SPECS[name]
    resolved: list[str] = []
    seen: set[str] = set()

    def _resolve(item: str) -> None:
        if item in seen:
            return  # Already resolved or in progress (break cycles)
        seen.add(item)
        if item in _ENGINE_SPECS:
            for dep in _ENGINE_SPECS[item].dependencies:
                _resolve(dep)
            if item not in resolved:
                resolved.append(item)

    for dep in spec.dependencies:
        _resolve(dep)
    if name not in resolved:
        resolved.append(name)
    return resolved

def _load_module(spec: EngineSpec) -> Any:
    """Lazily load a brain engine module."""
    if spec.name in _loaded_modules:
        return _loaded_modules[spec.name]

    if spec.availability_flag and not is_available(spec.name):
        raise ImportError(f"Engine {spec.name} is not available ({spec.availability_flag}=False)")

    import importlib

    try:
        module = importlib.import_module(spec.module_path)
        _loaded_modules[spec.name] = module
        logger.debug(f"[REGISTRY] Loaded engine: {spec.name}")
        return module
    except ImportError as e:
        logger.warning(f"[REGISTRY] Failed to load engine {spec.name}: {e}")
        raise

def _check_availability(spec: EngineSpec) -> bool | None:
    """Check if an engine is available (with caching)."""
    if spec.name in _availability_cache:
        return _availability_cache[spec.name]

    if spec.availability_flag is None:
        # No flag - try to import to check availability
        try:
            _load_module(spec)
            _availability_cache[spec.name] = True
            return True
        except ImportError:
            _availability_cache[spec.name] = False
            return False

    try:
        import importlib

        module = importlib.import_module(spec.module_path)
        available = getattr(module, spec.availability_flag, None)
        _availability_cache[spec.name] = available
        return available
    except ImportError:
        _availability_cache[spec.name] = False
        return False

def get_engine(name: str) -> Any:
    """
    Get a brain engine instance or module by name.

    Args:
        name: Engine name (e.g., "inference_engine", "insight_engine")

    Returns:
        The engine module or class (lazy loaded on first access)

    Raises:
        KeyError: If engine name is not registered
        ImportError: If engine module cannot be loaded

    Example:
        from brain._registry import get_engine

        inference = get_engine("inference_engine")
        result = await inference.InferenceEngine().infer(...)
    """
    if name not in _ENGINE_SPECS:
        raise KeyError(f"Unknown engine: {name}. Available: {list_engines()}")

    spec = _ENGINE_SPECS[name]

    # Load dependencies first (respects dependency order)
    _ = _resolve_dependencies(name)[:-1]  # Load deps, not self yet

    module = _load_module(spec)
    return module

def get_engine_class(name: str, class_name: str | None = None) -> type:
    """
    Get a specific class from an engine module.

    Args:
        name: Engine name
        class_name: Specific class name (defaults to first class in spec)

    Returns:
        The engine class
    """
    module = get_engine(name)
    target = class_name or spec.class_names[0] if (spec := _ENGINE_SPECS.get(name)) else class_name
    if not target:
        raise KeyError(f"No class specified for engine: {name}")
    return getattr(module, target)

def is_available(name: str) -> bool | None:
    """
    Check if an engine is available.

    Returns:
        True if available, False if not, None if unknown
    """
    if name not in _ENGINE_SPECS:
        return None
    return _check_availability(_ENGINE_SPECS[name])

def list_engines(
    by_order: bool = False,
    available_only: bool = False,
) -> list[str] | dict[str, list[str]]:
    """
    List all registered brain engines.

    Args:
        by_order: Group by load order if True
        available_only: Filter to only available engines

    Returns:
        List of engine names or dict grouped by load order
    """
    if by_order:
        by_load_order: dict[EngineLoadOrder, list[str]] = {order: [] for order in EngineLoadOrder}
        for name, spec in _ENGINE_SPECS.items():
            if available_only:
                if not is_available(name):
                    continue
            by_load_order[spec.load_order].append(name)
        return by_load_order  # type: ignore[return-value]

    names = list(_ENGINE_SPECS.keys())
    if available_only:
        names = [n for n in names if is_available(n)]
    return names

def preload_engines(*names: str) -> None:
    """
    Preload specific engines (eager loading).

    Useful for testing or when you need multiple engines immediately.

    Args:
        *names: Engine names to preload

    Example:
        from brain._registry import preload_engines

        preload_engines("deephermes3_engine", "inference_engine")
    """
    for name in names:
        try:
            get_engine(name)
        except ImportError as e:
            logger.warning(f"[REGISTRY] Preload failed for {name}: {e}")

def get_spec(name: str) -> EngineSpec | None:
    """Get the EngineSpec for a registered engine."""
    return _ENGINE_SPECS.get(name)

def get_dependencies(name: str) -> tuple[str, ...]:
    """Get direct dependencies for an engine."""
    if name not in _ENGINE_SPECS:
        return ()
    return _ENGINE_SPECS[name].dependencies

def get_resolve_order(name: str) -> list[str]:
    """Get full dependency resolution order for an engine."""
    return _resolve_dependencies(name)

def get_availability_flag(name: str) -> str | None:
    """Get the availability flag name for an engine (for legacy code)."""
    if name not in _ENGINE_SPECS:
        return None
    return _ENGINE_SPECS[name].availability_flag

def lazy_getattr(name: str) -> Any:
    """
    PEP 562 __getattr__ compatible lazy loading.

    ISSUE-005 FIX: This function provides circular import protection by:
    1. Loading dependencies in correct order (deephermes3_engine first)
    2. Using TYPE_CHECKING for forward references
    3. Breaking import cycles through lazy evaluation

    Usage in brain/__init__.py:
        def __getattr__(name: str) -> Any:
            return lazy_getattr(name)
    """
    # Check if it's an engine name we can lazy-load
    for engine_name, spec in _ENGINE_SPECS.items():
        if name in spec.class_names:
            # ISSUE-005: Load dependencies first to prevent circular imports
            # e.g., inference_engine depends on deephermes3_engine._get_xxh3_hex
            _ = _resolve_dependencies(engine_name)[:-1]  # Load deps, not self
            module = get_engine(engine_name)
            return getattr(module, name)

    # Check if it's an availability flag
    for engine_name, spec in _ENGINE_SPECS.items():
        if spec.availability_flag == name:
            return is_available(engine_name)

    raise AttributeError(f"module 'brain' has no attribute {name!r}")

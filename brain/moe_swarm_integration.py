"""
MoERouter + MicroModelSwarm Integration Layer
==============================================

Fixed Mixin State Violation:
- MoERouterSwarmMixin is now stateless (no class-level _swarm_router)
- Uses dependency injection for router instance
- Thread-safe instance-based pattern

Key Changes:
1. MoERouter now uses MicroModelSwarmRouter for micro-model routing
2. Content-based routing via regex patterns (no ML model needed)
3. Pointer swap instead of mlx_lm.load() for hot-swap <1ms (TRUE ZERO-COPY)
4. UMA-resident micro-models with LRU eviction
5. Adaptive memory budget using AdaptiveMemoryManager

Usage:
    from hledac.universal.brain.moe_swarm_integration import MoERouterSwarmMixin
    
    class MyMoERouter(MoERouterSwarmMixin, MoERouter):
        ...
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from .content_router import (
    ContentRouter,
    classify_content,
    get_preferred_model,
    route_content,
)

# MODERN-35 Fix: Import CPU affinity utilities for MLX Metal operations
from hledac.universal.utils.cpu_affinity import (
    set_mlx_affinity,
    is_apple_silicon,
)
from .micro_model_pool import (
    MICRO_MODELS,
    IMicroModelPool,
    LoadedMicroModel,
    MicroModelPool,
    MicroModelSpec,
    TaskType,
)
from .micro_model_swarm import create_micro_model_pool  # P3-1 FIX: was importing from micro_model_pool (wrong module)
from .micro_model_swarm import (
    MicroModelSwarmRouter,
    create_swarm_router,
)

if TYPE_CHECKING:
    import mlx.core as mx
    import mlx.nn as mlx_nn

logger = logging.getLogger(__name__)


# =============================================================================
# AdaptiveMemoryManager for Adaptive Memory Budget (NEW-M6: renamed from ResourceGovernor)
# =============================================================================

class AdaptiveMemoryManager:
    """
    Adaptive memory management for Apple Silicon.
    
    Dynamically adjusts memory budgets based on:
    - Available system memory
    - Current memory pressure
    - Active model requirements
    
    Designed for M1 8GB MacBook Air with UMA architecture.
    """
    
    # Memory thresholds (as ratio of available memory)
    LOW_PRESSURE = 0.60  # 60% - comfortable
    MEDIUM_PRESSURE = 0.75  # 75% - start being cautious
    HIGH_PRESSURE = 0.85  # 85% - aggressive eviction
    CRITICAL_PRESSURE = 0.92  # 92% - refuse new allocations
    
    # System memory estimates (MB)
    SYSTEM_OVERHEAD = 1536  # 1.5 GB for macOS + apps
    ACTIVATION_RESERVE = 1024  # 1 GB for KV cache + activations
    MAIN_MODEL_ESTIMATE = 2048  # 2 GB for DeepHermes-3B Int4
    
    def __init__(self, total_memory_mb: int = 8192):
        """
        Initialize AdaptiveMemoryManager.
        
        Args:
            total_memory_mb: Total system memory (default 8GB for M1 MacBook Air)
        """
        self._total_memory = total_memory_mb * 1024 * 1024
        self._lock = threading.RLock()
        self._cache_gc_threshold = self.LOW_PRESSURE
        
    @staticmethod
    def get_available_memory() -> int:
        """
        Get available memory using psutil or fallback.
        
        Returns:
            Available memory in bytes
        """
        try:
            import psutil
            return psutil.virtual_memory().available
        except ImportError:
            # Fallback: assume 4GB available on 8GB system
            return 4 * 1024 * 1024 * 1024
    
    @staticmethod
    def get_memory_pressure() -> float:
        """
        Get current system memory pressure.
        
        Returns:
            Memory pressure ratio (0.0 to 1.0)
        """
        try:
            import psutil
            vm = psutil.virtual_memory()
            return vm.percent / 100.0
        except ImportError:
            # Fallback: estimate based on loaded models
            return 0.5
    
    def calculate_micro_model_budget(self) -> int:
        """
        Calculate optimal memory budget for micro-models.
        
        Formula:
        budget = available_memory - system_overhead - main_model - activation_reserve
        
        Returns:
            Recommended budget in MB
        """
        available = self.get_available_memory() / (1024 * 1024)  # Convert to MB
        
        budget = available - self.SYSTEM_OVERHEAD - self.MAIN_MODEL_ESTIMATE - self.ACTIVATION_RESERVE
        
        # Clamp to reasonable bounds
        budget = max(512, min(budget, 4096))  # 512 MB to 4 GB
        
        return int(budget)
    
    def should_evict(self, current_pressure: float) -> bool:
        """Check if eviction should be triggered."""
        return current_pressure > self.HIGH_PRESSURE
    
    def can_allocate(self, size_mb: int, current_pressure: float) -> bool:
        """Check if new allocation is safe.
        
        P3-8 FIX: size_mb is in MB, _total_memory is stored in bytes.
        Convert _total_memory to MB for consistent unit comparison.
        """
        total_memory_mb = self._total_memory / (1024 * 1024)
        return current_pressure + (size_mb / total_memory_mb) < self.CRITICAL_PRESSURE
    
    def get_eviction_threshold(self, current_pressure: float) -> float:
        """
        Get dynamic eviction threshold based on system pressure.
        
        Args:
            current_pressure: Current micro-model pool pressure
            
        Returns:
            Eviction threshold (0.0 to 1.0)
        """
        system_pressure = self.get_memory_pressure()
        
        # More aggressive eviction when system is under pressure
        if system_pressure > 0.80:
            return 0.80  # Be more conservative
        elif system_pressure > 0.70:
            return 0.85
        else:
            return 0.90  # Default for TRUE ZERO-COPY


# =============================================================================
# SwappableMicroModelPool — Pool with AdaptiveMemoryManager integration
# =============================================================================

class SwappableMicroModelPool(MicroModelPool):
    """
    MicroModelPool with adaptive memory management via AdaptiveMemoryManager.
    
    ISSUE-022-06 FIX: Inherits batch preload from MicroModelPool for
    fragmentation-resistant UMA allocation.
    
    Extends MicroModelPool with:
    - Dynamic eviction thresholds based on system memory
    - Automatic budget recalculation
    - Graceful degradation under memory pressure
    - Batch preload with <5% fragmentation (vs 10-20% sequential)
    """
    
    def __init__(
        self,
        memory_budget_mb: int | None = None,
        eviction_threshold: float = 0.90,
        preload_all: bool = True,
        use_adaptive_budget: bool = True,
    ):
        """
        Initialize SwappableMicroModelPool.
        
        Args:
            memory_budget_mb: Initial memory budget (default: adaptive via AdaptiveMemoryManager)
            eviction_threshold: When to start evicting
            preload_all: Whether to preload all models at startup
            use_adaptive_budget: Use AdaptiveMemoryManager for dynamic budget
        """
        # Calculate adaptive budget first if needed
        if use_adaptive_budget:
            governor = AdaptiveMemoryManager()
            adaptive_budget = governor.calculate_micro_model_budget()
            # Use adaptive budget if not explicitly set or adaptive is preferred
            if memory_budget_mb is None or use_adaptive_budget:
                memory_budget_mb = adaptive_budget
                logger.info(f"[SwappablePool] Adaptive budget calculated: {adaptive_budget} MB")
            elif adaptive_budget < memory_budget_mb:
                logger.info(f"[SwappablePool] Reducing budget from {memory_budget_mb} to {adaptive_budget} MB (system constraint)")
                memory_budget_mb = adaptive_budget
        
        # Ensure we have a valid budget
        memory_budget_mb = memory_budget_mb or 2048
        
        super().__init__(
            memory_budget_mb=memory_budget_mb,
            eviction_threshold=eviction_threshold,
            preload_all=preload_all,
        )
        self._use_adaptive_budget = use_adaptive_budget
        self._governor = AdaptiveMemoryManager()
    
    @property
    def eviction_threshold(self) -> float:
        """Get dynamic eviction threshold based on system memory."""
        if self._use_adaptive_budget:
            return self._governor.get_eviction_threshold(self.memory_pressure)
        return self._eviction_threshold
    
    def should_allow_new_allocation(self, size_mb: int) -> bool:
        """Check if new model allocation should be allowed."""
        if not self._use_adaptive_budget:
            return True
        return self._governor.can_allocate(size_mb, self.memory_pressure)
    
    def recalculate_budget(self) -> int:
        """
        Recalculate memory budget based on current system state.
        
        Returns:
            New budget in MB
        """
        if not self._use_adaptive_budget:
            return int(self._memory_budget / (1024 * 1024))
        
        new_budget = self._governor.calculate_micro_model_budget()
        self._memory_budget = new_budget * 1024 * 1024
        logger.info(f"[SwappablePool] Budget recalculated: {new_budget} MB")
        return new_budget


# =============================================================================
# FIXED: MoERouterSwarmMixin — Stateless Mixin with Dependency Injection
# =============================================================================

class MoERouterSwarmMixin:
    """
    Mixin that adds MicroModelSwarm capabilities to MoERouter.
    
    FIXED: No class-level state - uses dependency injection pattern.
    This fix resolves the mixin state violation where _swarm_router
    was shared across all instances as a class variable.
    
    This mixin extends MoERouter with:
    - Content-based routing (regex patterns)
    - Micro-model pool with <100ms hot-swap
    - UMA-resident micro-models
    - Adaptive memory budget via AdaptiveMemoryManager
    - Automatic LRU eviction
    
    Usage:
        class MoERouterWithSwarm(MoERouterSwarmMixin, MoERouter):
            pass
    
    Or with explicit router injection:
        mixin = MoERouterSwarmMixin()
        mixin.inject_swarm_router(my_router)
    """
    
    # No class-level state anymore! This was the bug.
    # _swarm_router: Optional[MicroModelSwarmRouter] = None  # REMOVED
    # _swarm_lock: threading.Lock = threading.Lock()  # REMOVED
    
    def __init__(self, *args, **kwargs):
        """
        Initialize mixin - instance-level state only.
        
        Note: super().__init__() is called LAST to ensure proper MRO chain
        for mixin classes. This allows the mixin to set up state before
        the parent class initializes.
        """
        # Instance-level router (not class-level!)
        self._swarm_router: Optional[MicroModelSwarmRouter] = None
        self._swarm_lock: threading.Lock = threading.Lock()
        self._swarm_initialized: bool = False
        
        # Initialize parent class (MUST be last for mixin compatibility)
        super().__init__(*args, **kwargs)
    
    def inject_swarm_router(self, router: MicroModelSwarmRouter) -> None:
        """
        Inject a pre-configured MicroModelSwarmRouter instance.
        
        This is the preferred way to provide the router - allows for:
        - Testing with mock routers
        - Sharing routers across instances
        - Custom router configuration
        
        Args:
            router: MicroModelSwarmRouter instance
        """
        with self._swarm_lock:
            self._swarm_router = router
    
    def _get_swarm_router(self) -> MicroModelSwarmRouter:
        """
        Get or create the MicroModelSwarmRouter instance.
        
        This is instance-level, not class-level! Each mixin instance
        has its own router (or shares via inject_swarm_router).
        
        Thread-safe: Uses double-checked locking pattern with proper
        lock acquisition before any write operation.
        
        Returns:
            MicroModelSwarmRouter instance
        """
        # Fast path: already initialized (no lock needed for read)
        if self._swarm_router is not None:
            return self._swarm_router
        
        # Slow path: need to create - acquire lock
        with self._swarm_lock:
            # Double-checked locking pattern - re-check under lock
            if self._swarm_router is None:
                # Create router with adaptive memory budget
                memory_budget = self._get_swarm_memory_budget()
                
                self._swarm_router = create_swarm_router(
                    memory_budget_mb=memory_budget,
                    preload_models=True,
                    use_adaptive_budget=True,
                )
                
                logger.info(
                    f"[SWARM] MicroModelSwarmRouter initialized "
                    f"(budget: {memory_budget} MB, adaptive=True)"
                )
        
        return self._swarm_router
    
    def _get_swarm_memory_budget(self) -> int:
        """
        Calculate memory budget for micro-models based on available RAM.
        
        Uses AdaptiveMemoryManager for adaptive budget calculation.
        
        M1 MacBook Air 8GB:
        - System + apps: ~1.5 GB
        - DeepHermes-3B (Int4): ~2.0 GB
        - Reserve for activations/KV: ~1.0 GB
        - Micro-model pool: ~3.5 GB
        
        Returns:
            Memory budget in MB
        """
        governor = AdaptiveMemoryManager()
        return governor.calculate_micro_model_budget()
    
    async def _init_swarm_router(self) -> None:
        """
        Initialize the swarm router asynchronously.
        
        Called from MoERouter.__ainit__() during initialization.
        Preloads priority models (SmolLM triage) in background.
        """
        router = self._get_swarm_router()
        
        # Register main model if we have it
        if hasattr(self, '_model') and hasattr(self, '_tokenizer'):
            if self._model is not None and self._tokenizer is not None:
                router.register_main_model(self._model, self._tokenizer)
                logger.info("[SWARM] Main model registered with swarm router")
        
        # Preload priority models in background
        await asyncio.to_thread(router.preload_priority_models)
        logger.info("[SWARM] Priority micro-models preloading...")
        self._swarm_initialized = True
    
    async def _load_micro_model(self, model_id: str) -> bool:
        """
        Load a micro-model via pointer swap (not mlx_lm.load()).
        
        This is the SWARM-001 fix: Instead of full mlx_lm.load() (1-20s),
        we use the pre-loaded model pool and swap pointers (<100ms).
        
        Args:
            model_id: ID of micro-model to load (e.g., "qwen_coder")
        
        Returns:
            True if model is available/loaded
        """
        router = self._get_swarm_router()
        
        # Try fast path: already loaded
        if model_id in router.loaded_models:
            logger.debug(f"[SWARM] Micro-model '{model_id}' already loaded (pointer swap)")
            return True
        
        # Slow path: need to load
        logger.info(f"[SWARM] Loading micro-model: {model_id}")
        
        try:
            loop = asyncio.get_event_loop()
            loaded = await loop.run_in_executor(
                None,
                router._pool.get_model,
                model_id,
            )
            
            if loaded is not None:
                logger.info(f"[SWARM] ✓ Micro-model '{model_id}' loaded")
                return True
            else:
                logger.warning(f"[SWARM] Failed to load micro-model: {model_id}")
                return False
                
        except Exception as e:
            logger.error(f"[SWARM] Error loading micro-model '{model_id}': {e}")
            return False
    
    async def _classify_and_route(
        self,
        query: str,
    ) -> tuple[str | None, TaskType]:
        """
        Classify query and route to appropriate micro-model.
        
        Uses pure functions from content_router module for stateless routing.
        
        Args:
            query: Input text to classify
        
        Returns:
            Tuple of (micro_model_id, task_type)
            micro_model_id is None if routing to main model is recommended
        """
        return route_content(query)
    
    def _generate_with_micro_model(
        self,
        model_id: str,
        prompt: str,
        max_tokens: int = 256,
        temp: float = 0.7,
        **kwargs,
    ) -> tuple[str, bool]:
        """
        Generate text using a micro-model.
        
        Handles model swapping transparently and returns to main model
        after generation.
        
        Args:
            model_id: ID of micro-model to use
            prompt: Input prompt
            max_tokens: Max tokens to generate
            temp: Temperature
            **kwargs: Additional generation args
        
        Returns:
            Tuple of (generated_text, used_micro_model)
        """
        router = self._get_swarm_router()
        
        try:
            result = router._pool.generate(
                model_id,
                prompt,
                max_tokens=max_tokens,
                temp=temp,
                **kwargs,
            )
            return (result, True)
        except Exception as e:
            logger.error(f"[SWARM] Micro-model generation failed: {e}")
            return ("", False)
    
    def _classify_only(self, text: str) -> TaskType:
        """
        Fast content classification without model loading.
        
        Uses pure function from content_router module.
        <1ms latency.
        
        Args:
            text: Input text to classify
        
        Returns:
            TaskType classification
        """
        return classify_content(text)
    
    async def _swap_expert_to_micro(
        self,
        expert_name: str,
        query: str,
    ) -> bool:
        """
        Swap expert to use micro-model based on query content.
        
        This is the integration point: MoERouter._load_expert() calls this
        to decide whether to use a micro-model instead of the main 3B model.
        
        Args:
            expert_name: Name of expert being loaded
            query: Current query (used for routing)
        
        Returns:
            True if micro-model was successfully loaded/activated
        """
        # Classify the query using pure function
        model_id, task_type = route_content(query)
        
        if model_id is None:
            logger.debug(f"[SWARM] No micro-model for task {task_type}")
            return False
        
        # Check if this expert should use this micro-model
        expert_task_map = {
            'osint': TaskType.CLASSIFICATION,
            'security': TaskType.CODE,
            'temporal': TaskType.SYNTHESIS,
            'graph': TaskType.GENERAL,
            'synthesis': TaskType.SYNTHESIS,
        }
        
        expected_task = expert_task_map.get(expert_name)
        if expected_task != task_type:
            logger.debug(
                f"[SWARM] Task mismatch: expert '{expert_name}' expects {expected_task}, "
                f"query is {task_type}"
            )
            return False
        
        # Load micro-model
        return await self._load_micro_model(model_id)
    
    def get_swarm_stats(self) -> dict[str, Any]:
        """Get comprehensive swarm router statistics."""
        router = self._get_swarm_router()
        return router.get_stats()
    
    @property
    def swarm_memory_pressure(self) -> float:
        """Current micro-model pool memory pressure."""
        router = self._get_swarm_router()
        return router.memory_pressure
    
    @property
    def swarm_loaded_models(self) -> list[str]:
        """List of currently loaded micro-models."""
        router = self._get_swarm_router()
        return router.loaded_models


# =============================================================================
# Factory Functions
# =============================================================================

def create_swappable_pool(
    memory_budget_mb: int | None = None,
    use_adaptive_budget: bool = True,
) -> SwappableMicroModelPool:
    """
    Create a MicroModelPool with adaptive memory management.
    
    Uses adaptive budget by default (AdaptiveMemoryManager calculates optimal
    memory allocation for M1 8GB: ~3.2 GB).
    
    Args:
        memory_budget_mb: Initial memory budget (default: adaptive via AdaptiveMemoryManager)
        use_adaptive_budget: Use AdaptiveMemoryManager for dynamic budget (default: True)
        
    Returns:
        SwappableMicroModelPool instance
    """
    # If memory_budget_mb is None, AdaptiveMemoryManager will calculate optimal value
    initial_budget = memory_budget_mb if memory_budget_mb is not None else 4096  # Temporary high value, will be adjusted
    
    return SwappableMicroModelPool(
        memory_budget_mb=initial_budget,
        use_adaptive_budget=use_adaptive_budget,
    )


# =============================================================================
# ISSUE-022-06: Batch Preload Control Utilities
# =============================================================================

def prepare_batch_preload() -> None:
    """
    ISSUE-022-06 FIX: Prepare system for batch preload.
    
    Call this BEFORE creating the pool or loading models to ensure
    optimal UMA allocation:
    1. Clears Metal caches
    2. Runs GC
    3. Syncs GPU
    
    Should be called once at application startup before any ML work.
    """
    from .micro_model_pool import get_uma_monitor
    
    monitor = get_uma_monitor()
    monitor.snapshot("app_start")
    monitor.clear_caches()
    monitor.snapshot("preload_prepared")
    
    logger.info("[ISSUE-022-06] Batch preload prepared: caches cleared, UMA ready")


def get_fragmentation_report() -> dict[str, Any]:
    """
    ISSUE-022-06: Get current UMA fragmentation report.
    
    Returns:
        Dict with fragmentation metrics and recommendations
    """
    from .micro_model_pool import get_uma_monitor
    
    return get_uma_monitor().get_report()


def log_fragmentation_metrics() -> None:
    """
    ISSUE-022-06: Log current fragmentation metrics.
    
    Useful for debugging memory issues.
    """
    report = get_fragmentation_report()
    
    logger.info(f"[ISSUE-022-06] UMA Fragmentation: {report['status']}")
    logger.info(f"[ISSUE-022-06] Fragmentation Score: {report['fragmentation_score']:.4f}")
    
    if report['snapshots']:
        for snap in report['snapshots']:
            logger.info(
                f"[ISSUE-022-06]   {snap['label']}: "
                f"active={snap['active_memory_mb']:.1f}MB, "
                f"wired={snap['wired_memory_mb']:.1f}MB"
            )
    
    for rec in report['recommendations']:
        logger.info(f"[ISSUE-022-06] Recommendation: {rec}")


# =============================================================================
# ENHANCED MOE ROUTER WITH SWARM SUPPORT
# =============================================================================

class MoERouterWithSwarm:
    """
    Enhanced MoERouter with MicroModelSwarm support.
    
    This class combines MoERouter and MoERouterSwarmMixin into a single
    class for easy integration.
    
    Usage:
        router = MoERouterWithSwarm(config)
        await router.initialize()
        
        # Auto-routing based on query content
        result = await router.route_and_generate(query)
    
    Note: This is a convenience class. For full MoERouter functionality,
    use MoERouterSwarmMixin with the original MoERouter class.
    """
    
    def __init__(
        self,
        config: Optional[Any] = None,
        enable_swarm: bool = True,
        memory_budget_mb: int | None = None,
        use_adaptive_budget: bool = True,
    ):
        """
        Initialize enhanced MoERouter with SWARM support.
        
        Args:
            config: MoERouterConfig (or None for defaults)
            enable_swarm: Enable micro-model routing (default: True)
            memory_budget_mb: Memory budget for micro-models (default: adaptive via AdaptiveMemoryManager)
            use_adaptive_budget: Use AdaptiveMemoryManager for dynamic budget
        """
        self._config = config
        self._enable_swarm = enable_swarm
        # None means "use adaptive" - stored as actual value after calculation
        self._memory_budget: int | None = memory_budget_mb
        self._use_adaptive_budget = use_adaptive_budget
        self._initialized = False
        
        # Swarm router (lazy init)
        self._swarm_router: Optional[MicroModelSwarmRouter] = None
        
        # Content router (pure functions, no state)
        self._content_router = ContentRouter()
        
        # Expert state (from original MoERouter)
        self._experts: dict[str, tuple[Any, Any]] = {}
        self._expert_usage: dict[str, int] = {}
        self._prompt_cache_by_expert: dict[str, Any] = {}
        
        # Main model reference (DeepHermes-3B)
        self._main_model: Optional[Any] = None
        self._main_tokenizer: Optional[Any] = None
    
    @property
    def config(self) -> Any:
        """Get router configuration."""
        return self._config
    
    async def initialize(
        self,
        model: Optional[Any] = None,
        tokenizer: Optional[Any] = None,
    ) -> None:
        """
        Initialize the router and micro-model pool.
        
        Args:
            model: Main model instance (DeepHermes-3B)
            tokenizer: Main tokenizer instance
        """
        if self._initialized:
            return
        
        self._main_model = model
        self._main_tokenizer = tokenizer
        
        if self._enable_swarm:
            # Pass None for memory_budget_mb if not explicitly set - let AdaptiveMemoryManager calculate
            budget = None if self._memory_budget is None else self._memory_budget
            self._swarm_router = create_swarm_router(
                memory_budget_mb=budget,
                preload_models=True,
                use_adaptive_budget=self._use_adaptive_budget,
            )
            
            if model is not None and tokenizer is not None:
                self._swarm_router.register_main_model(model, tokenizer)
            
            # Get actual budget (may have been adjusted by AdaptiveMemoryManager)
            actual_budget = int(self._swarm_router._pool._memory_budget / (1024 * 1024))
            logger.info(
                f"[SWARM] MicroModelSwarm initialized "
                f"(budget: {actual_budget} MB, adaptive={self._use_adaptive_budget})"
            )
        
        self._initialized = True
    
    async def route(
        self,
        query: str,
        use_micro_model: bool = True,
    ) -> tuple[str | None, TaskType, dict[str, Any]]:
        """
        Route query to appropriate model.
        
        Args:
            query: Input query text
            use_micro_model: Whether to prefer micro-models
        
        Returns:
            Tuple of (model_id, task_type, metadata)
            model_id is micro-model ID or None for main model
        """
        # Use pure function for stateless routing
        model_id, task_type = route_content(query)
        
        metadata = {
            "task_type": task_type.name,
            "content_classified": True,
            "micro_model_available": model_id is not None,
        }
        
        if not use_micro_model:
            return (None, task_type, metadata)
        
        # Verify micro-model availability
        if model_id and self._swarm_router:
            if model_id not in self._swarm_router.loaded_models:
                # Try to load
                loaded = self._swarm_router._pool.get_model(model_id)
                if loaded is None:
                    model_id = None
                    metadata["load_failed"] = True
        
        return (model_id, task_type, metadata)
    
    async def generate(
        self,
        query: str,
        model_id: Optional[str] = None,
        max_tokens: int = 256,
        temp: float = 0.7,
    ) -> str:
        """
        Generate response for query.
        
        Args:
            query: Input query
            model_id: Specific micro-model to use (or None for main)
            max_tokens: Max tokens to generate
            temp: Temperature
        
        Returns:
            Generated text
        """
        if model_id and self._swarm_router:
            result = self._swarm_router._pool.generate(
                model_id,
                query,
                max_tokens=max_tokens,
                temp=temp,
            )
            return result
        
        # Use main model
        if self._main_model is None:
            raise RuntimeError("No main model available")
        
        # MODERN-35 Fix: Set P-core affinity before MLX Metal inference
        # E-cores are strictly reserved for I/O operations only
        if is_apple_silicon():
            set_mlx_affinity()
        
        import mlx_lm
        return mlx_lm.generate(
            self._main_model,
            self._main_tokenizer,
            prompt=query,
            max_tokens=max_tokens,
            temp=temp,
        )
    
    async def route_and_generate(
        self,
        query: str,
        max_tokens: int = 256,
        temp: float = 0.7,
        prefer_micro_model: bool = True,
    ) -> tuple[str, str | None, TaskType]:
        """
        Combined routing and generation.
        
        This is the main entry point for SWARM-001 enabled inference.
        
        Args:
            query: Input query
            max_tokens: Max tokens to generate
            temp: Temperature
            prefer_micro_model: Prefer micro-model over main (default: True)
        
        Returns:
            Tuple of (generated_text, model_id_used, task_type)
        """
        model_id, task_type, metadata = await self.route(
            query,
            use_micro_model=prefer_micro_model,
        )
        
        try:
            result = await self.generate(
                query,
                model_id=model_id,
                max_tokens=max_tokens,
                temp=temp,
            )
            return (result, model_id, task_type)
        except Exception as e:
            logger.error(f"[SWARM] Generation failed: {e}")
            
            # Fallback to main model
            try:
                result = await self.generate(query, model_id=None)
                return (result, None, task_type)
            except Exception:
                return ("[ERROR] Generation failed", None, TaskType.GENERAL)
    
    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive statistics."""
        stats = {
            "initialized": self._initialized,
            "enable_swarm": self._enable_swarm,
            "memory_budget_mb": self._memory_budget,
            "use_adaptive_budget": self._use_adaptive_budget,
            "expert_count": len(self._experts),
        }
        
        if self._swarm_router:
            stats["swarm"] = self._swarm_router.get_stats()
        
        return stats
    
    @property
    def memory_pressure(self) -> float:
        """Current memory pressure across all models."""
        if self._swarm_router:
            return self._swarm_router.memory_pressure
        return 0.0
    
    @property
    def loaded_micro_models(self) -> list[str]:
        """List of loaded micro-model IDs."""
        if self._swarm_router:
            return self._swarm_router.loaded_models
        return []


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def enable_swarm_routing(
    router: Any,
    memory_budget_mb: int | None = None,
    use_adaptive_budget: bool = True,
) -> MicroModelSwarmRouter:
    """
    Enable SWARM routing on an existing MoERouter instance.
    
    This is the migration path: call this on existing MoERouter
    instances to add SWARM-001 capabilities.
    
    Args:
        router: Existing MoERouter instance
        memory_budget_mb: Memory budget for micro-models (default: adaptive)
        use_adaptive_budget: Use AdaptiveMemoryManager for dynamic budget
    
    Returns:
        The MicroModelSwarmRouter instance
    """
    swarm = create_swarm_router(
        memory_budget_mb=memory_budget_mb,
        preload_models=True,
        use_adaptive_budget=use_adaptive_budget,
    )
    
    # Register main model from router if available
    if hasattr(router, '_model') and hasattr(router, '_tokenizer'):
        swarm.register_main_model(router._model, router._tokenizer)
    
    # Attach to router
    router._swarm_router = swarm
    
    return swarm


def get_swarm_router() -> MicroModelSwarmRouter:
    """
    Get a new MicroModelSwarmRouter instance.
    
    Uses adaptive memory budget by default for M1 8GB optimization.
    """
    return create_swarm_router(
        memory_budget_mb=None,  # Let AdaptiveMemoryManager calculate
        preload_models=False,
        use_adaptive_budget=True,
    )

"""
MoERouter + MicroModelSwarm Integration Layer
==============================================



This module provides the integration between MoERouter and MicroModelSwarmRouter,
enabling the SWARM-001 fix for the monolithic model bottleneck.

Key Changes:
1. MoERouter now uses MicroModelSwarmRouter for micro-model routing
2. Content-based routing via regex patterns (no ML model needed)
3. Pointer swap instead of mlx_lm.load() for hot-swap <100ms
4. UMA-resident micro-models with LRU eviction

Usage:
    from hledac.universal.brain.moe_swarm_integration import MoERouterSwarmMixin
    
    class MyMoERouter(MoERouterSwarmMixin, MoERouter):
        ...

Integration Point:
    - MoERouter._load_expert() -> _load_micro_model() (pointer swap)
    - MoERouter.route() -> classify_and_route() (content-based)
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from .micro_model_swarm import (
    MICRO_MODELS,
    ContentRouter,
    MicroModelPool,
    MicroModelSwarmRouter,
    TaskType,
    create_swarm_router,
)

if TYPE_CHECKING:
    import mlx.core as mx
    import mlx.nn as mlx_nn

logger = logging.getLogger(__name__)


class MoERouterSwarmMixin:
    """
    Mixin that adds MicroModelSwarm capabilities to MoERouter.
    
    This mixin extends MoERouter with:
    - Content-based routing (regex patterns)
    - Micro-model pool with <100ms hot-swap
    - UMA-resident micro-models
    - Automatic LRU eviction
    
    Usage:
        class MoERouterWithSwarm(MoERouterSwarmMixin, MoERouter):
            pass
    
    Then use MoERouterWithSwarm instead of MoERouter.
    """
    
    # Class-level router instance (shared across all instances)
    _swarm_router: Optional[MicroModelSwarmRouter] = None
    _swarm_lock: threading.Lock = threading.Lock()
    
    def _get_swarm_router(self) -> MicroModelSwarmRouter:
        """
        Get or create the global MicroModelSwarmRouter singleton.
        
        This ensures we only have one pool of micro-models in memory,
        shared across all MoERouter instances.
        """
        if self._swarm_router is not None:
            return self._swarm_router
        
        with self._swarm_lock:
            if self._swarm_router is None:
                # Create router with memory budget optimized for M1 8GB
                memory_budget = self._get_swarm_memory_budget()
                
                self._swarm_router = create_swarm_router(
                    memory_budget_mb=memory_budget,
                    preload_models=True,
                )
                
                logger.info(
                    f"[SWARM] MicroModelSwarmRouter initialized "
                    f"(budget: {memory_budget} MB)"
                )
            
            return self._swarm_router
    
    def _get_swarm_memory_budget(self) -> int:
        """
        Calculate memory budget for micro-models based on available RAM.
        
        M1 MacBook Air 8GB:
        - System + apps: ~1.5 GB
        - DeepHermes-3B (Int4): ~2.0 GB
        - Reserve for activations/KV: ~1.0 GB
        - Micro-model pool: ~3.5 GB
        
        This gives us room for 3-4 micro-models simultaneously.
        """
        # Conservative estimate for M1 8GB
        return 3200  # 3.2 GB for micro-models
    
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
        
        This combines content-based classification with micro-model routing.
        Uses regex patterns for fast classification (<1ms).
        
        Args:
            query: Input text to classify
        
        Returns:
            Tuple of (micro_model_id, task_type)
            micro_model_id is None if routing to main model is recommended
        """
        router = self._get_swarm_router()
        return router.route(query)
    
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
        
        Uses only regex patterns — no ML model needed.
        <1ms latency.
        
        Args:
            text: Input text to classify
        
        Returns:
            TaskType classification
        """
        router = self._get_swarm_router()
        return router.classify(text)
    
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
        # Classify the query
        model_id, task_type = await self._classify_and_route(query)
        
        if model_id is None:
            # No micro-model available for this task
            logger.debug(f"[SWARM] No micro-model for task {task_type}")
            return False
        
        # Check if this expert should use this micro-model
        expert_task_map = {
            'osint': TaskType.CLASSIFICATION,
            'security': TaskType.CODE,  # Code-related security tasks
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
        memory_budget_mb: int = 3200,
    ):
        """
        Initialize enhanced MoERouter with SWARM support.
        
        Args:
            config: MoERouterConfig (or None for defaults)
            enable_swarm: Enable micro-model routing (default: True)
            memory_budget_mb: Memory budget for micro-models (default: 3.2 GB)
        """
        self._config = config
        self._enable_swarm = enable_swarm
        self._memory_budget = memory_budget_mb
        self._initialized = False
        
        # Swarm router (lazy init)
        self._swarm_router: Optional[MicroModelSwarmRouter] = None
        
        # Content router (always available, no ML needed)
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
            self._swarm_router = create_swarm_router(
                memory_budget_mb=self._memory_budget,
                preload_models=True,
            )
            
            if model is not None and tokenizer is not None:
                self._swarm_router.register_main_model(model, tokenizer)
            
            logger.info(
                f"[SWARM] MicroModelSwarm initialized "
                f"(budget: {self._memory_budget} MB)"
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
        task_type = self._content_router.classify(query)
        model_id = self._content_router.get_preferred_model(task_type)
        
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
    memory_budget_mb: int = 3200,
) -> MicroModelSwarmRouter:
    """
    Enable SWARM routing on an existing MoERouter instance.
    
    This is the migration path: call this on existing MoERouter
    instances to add SWARM-001 capabilities.
    
    Args:
        router: Existing MoERouter instance
        memory_budget_mb: Memory budget for micro-models
    
    Returns:
        The MicroModelSwarmRouter instance
    """
    swarm = create_swarm_router(memory_budget_mb=memory_budget_mb)
    
    # Register main model from router if available
    if hasattr(router, '_model') and hasattr(router, '_tokenizer'):
        swarm.register_main_model(router._model, router._tokenizer)
    
    # Attach to router
    router._swarm_router = swarm
    
    return swarm


def get_swarm_router() -> MicroModelSwarmRouter:
    """Get the global MicroModelSwarmRouter instance."""
    return MicroModelSwarmRouter()

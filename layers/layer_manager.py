"""
Layer Manager - Centralized Layer Orchestration
===============================================

.. deprecated::
    This module is deprecated. Use `layers.core.registry.LayerRegistry` instead:

        from layers.core import LayerRegistry

    This file provides a higher-level orchestration that should be refactored.
    The LayerRegistry in layers.core provides the core functionality.

This file exists for backward compatibility only and will be removed in a future version.
"""

# Deprecation warning
import warnings

warnings.warn(
    "layers.layer_manager is deprecated. Use layers.core.LayerRegistry instead.",
    DeprecationWarning,
    stacklevel=2,
)
import asyncio
import gc
import inspect
import logging
from enum import Enum
from typing import Any

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)

__all__ = [
    "M1MemoryOptimizer",
    "LayerStatus",
    "LayerHealth",
    "LayerManager",
    "UnifiedCapabilitiesManager",
    "create_layer_manager",
    "get_layer_manager",
    "create_capabilities_manager",
    "get_capabilities_manager",
]


class M1MemoryOptimizer:
    """
    M1 MacBook Air 8GB RAM optimization utilities.

    IMPORTANT: Layer cleanup utility only — not the canonical Uma governor.
    Canonical Uma policy lives in core/resource_governor.py.

    Provides:
    - Aggressive garbage collection
    - MLX cache clearing
    - Memory pressure monitoring
    - Context swap between layers
    """

    __slots__ = ("_cache_clears", "_context_swaps", "_gc_count", "memory_limit_mb")

    def __init__(self, memory_limit_mb: float = 5500) -> None:
        self.memory_limit_mb = memory_limit_mb
        self._gc_count = 0
        self._cache_clears = 0
        self._context_swaps = 0

    async def force_cleanup(self) -> dict[str, Any]:
        """Force aggressive memory cleanup."""
        import psutil

        before = psutil.virtual_memory().used / (1024 * 1024)
        try:
            import mlx.core as mx

            mx.eval([])
            mx.clear_cache()
            self._cache_clears += 1
            logger.debug("🧹 MLX cache cleared")
        except Exception:  # noqa: BLE001
            pass
        gc.collect()
        self._gc_count += 1
        logger.debug("🗑️ GC #%s", self._gc_count)
        await asyncio.sleep(0.1)
        after = psutil.virtual_memory().used / (1024 * 1024)
        return {"memory_freed_mb": before - after, "gc_count": self._gc_count, "cache_clears": self._cache_clears}

    def check_memory_pressure(self) -> bool:
        """Check if system is under memory pressure."""
        try:
            import psutil

            memory = psutil.virtual_memory()
            used_mb = memory.used / (1024 * 1024)
            return used_mb > self.memory_limit_mb
        except Exception:
            return False

    async def context_swap(self, unload_layers: list[str], load_layers: list[str]) -> None:
        """
        Perform context swap: unload layers, cleanup, load new layers.

        Args:
            unload_layers: Layer names to unload
            load_layers: Layer names to load
        """
        logger.info("🔄 Context swap: %s → %s", unload_layers, load_layers)
        for layer_name in unload_layers:
            await self._unload_layer(layer_name)
        await self.force_cleanup()
        for layer_name in load_layers:
            await self._load_layer(layer_name)
        self._context_swaps += 1
        logger.info("✅ Context swap complete (#%s)", self._context_swaps)

    async def _unload_layer(self, layer_name: str) -> None:
        """Unload a layer to free memory."""
        logger.debug("📤 Unloading layer: %s", layer_name)
        # ISSUE-018 fix: Removed unnecessary asyncio.sleep(0.05) polling
        # No async I/O or external event waiting needed - unload is synchronous

    async def _load_layer(self, layer_name: str) -> None:
        """Load a layer."""
        logger.debug("📥 Loading layer: %s", layer_name)
        # ISSUE-018 fix: Removed unnecessary asyncio.sleep(0.05) polling
        # No async I/O or external event waiting needed - load is synchronous

    def get_stats(self) -> dict[str, Any]:
        """Get optimizer statistics."""
        return {
            "gc_count": self._gc_count,
            "cache_clears": self._cache_clears,
            "context_swaps": self._context_swaps,
            "memory_limit_mb": self.memory_limit_mb,
        }


class LayerStatus(Enum):
    """Layer initialization status"""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class LayerHealth(Struct):
    """Layer health status"""

    name: str
    status: LayerStatus
    initialized: bool
    error_message: str | None = None
    metadata: dict[str, Any] | None = None


class LayerManager:
    """
    .. deprecated::
        This class is DEPRECATED and DORMANT.


    Centralized manager for all universal layers.
    Features:
    - Ordered initialization (dependencies first)
    - Health monitoring
    - Graceful shutdown
    - Layer dependency resolution
    - M1 memory-aware boot sequence
    - Shared GhostDirector singleton (prevents duplicate initialization)

    Canonical Path: ``core.__main__.run_sprint()`` → ``SprintScheduler``
    Do NOT use for new production runtime code.

    Preserved For: legacy/autonomous_orchestrator.py, tests/scripts/docs only.
    """

    __slots__ = (
        "_async_method_cache",
        "_communication",
        "_content",
        "_ghost",
        "_ghost_director",
        "_ghost_director_initialized",
        "_layers",
        "_memory",
        "_memory_optimizer",
        "_privacy",
        "_research",
        "_security",
        "_status",
        "_stealth",
        "config",
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialize LayerManager.

        Args:
            config: Optional configuration for layers
        """
        self.config = config or {}
        self._layers: dict[str, Any] = {}
        self._status: dict[str, LayerStatus] = {}
        self._async_method_cache: dict[str, dict[str, bool]] = {}
        self._ghost = None
        self._memory = None
        self._security = None
        self._stealth = None
        self._research = None
        self._privacy = None
        self._communication = None
        self._content = None
        self._ghost_director: Any | None = None
        self._ghost_director_initialized: bool = False
        self._memory_optimizer = M1MemoryOptimizer(memory_limit_mb=self.config.get("memory_limit_mb", 5500))
        logger.info("LayerManager initialized (M1 8GB optimized)")

    def _is_async(self, layer_name: str, method_name: str, obj: Any) -> bool:
        """
        F272B: Cached inspect.iscoroutinefunction() lookup.

        Pre-computes and caches the async status of layer methods at registration
        time, so hot paths (health_check, cleanup, context_swap) don't re-check
        on every call.
        """
        if layer_name not in self._async_method_cache:
            self._async_method_cache[layer_name] = {}
        cache = self._async_method_cache[layer_name]
        if method_name not in cache:
            method = getattr(obj, method_name, None)
            cache[method_name] = method is not None and inspect.iscoroutinefunction(method)
        return cache[method_name]

    def get_ghost_director(self) -> Any | None:
        """
        Get or create shared GhostDirector instance.

        This is a singleton pattern to prevent both GhostLayer and ResearchLayer
        from creating their own GhostDirector instances, saving M1 8GB RAM.

        Returns:
            GhostDirector instance or None if not available
        """
        if self._ghost_director is None:
            try:
                from hledac.universal.cortex.director import GhostDirector

                self._ghost_director = GhostDirector(max_steps=20)
                logger.info("✅ GhostDirector singleton created in LayerManager")
            except ImportError as e:
                logger.warning(f"⚠️ GhostDirector not available: {e}")
                return None
        return self._ghost_director

    async def initialize_ghost_director(self) -> bool:
        """
        Initialize the shared GhostDirector drivers.

        Returns:
            True if initialization successful
        """
        if self._ghost_director_initialized:
            return True
        director = self.get_ghost_director()
        if director is None:
            return False
        try:
            await director.initialize_drivers()
            self._ghost_director_initialized = True
            logger.info("✅ GhostDirector drivers initialized")
            return True
        except Exception as e:
            logger.error("❌ GhostDirector initialization failed: %s", e)
            return False

    @property
    def ghost(self) -> Any:
        """Get or create ghost layer"""
        if self._ghost is None:
            from .ghost_layer import GhostLayer

            self._ghost = GhostLayer(ghost_director=self.get_ghost_director())
        return self._ghost

    @property
    def memory(self) -> Any:
        """Get or create memory layer"""
        if self._memory is None:
            from .memory_layer import MemoryLayer

            self._memory = MemoryLayer()
        return self._memory

    @property
    def security(self) -> Any:
        """Get or create security layer"""
        if self._security is None:
            from .security_layer import SecurityLayer

            self._security = SecurityLayer()
        return self._security

    @property
    def stealth(self) -> Any:
        """Get or create stealth layer"""
        if self._stealth is None:
            from .stealth_layer import StealthLayer

            self._stealth = StealthLayer()
        return self._stealth

    @property
    def research(self) -> Any:
        """Get or create research layer"""
        if self._research is None:
            from .research_layer import ResearchLayer

            self._research = ResearchLayer(ghost_director=self.get_ghost_director())
        return self._research

    @property
    def privacy(self) -> Any:
        """Get or create privacy layer"""
        if self._privacy is None:
            from ..config import PrivacyConfig
            from .privacy_layer import PrivacyLayer

            config = self.config.get("privacy", PrivacyConfig())
            self._privacy = PrivacyLayer(config=config, security_layer=self.security)
        return self._privacy

    @property
    def communication(self) -> Any:
        """Get or create communication layer"""
        if self._communication is None:
            from .communication_layer import CommunicationConfig, CommunicationLayer

            config = CommunicationConfig()
            self._communication = CommunicationLayer(config)
        return self._communication

    @property
    def content(self) -> Any:
        """Get or create content layer"""
        if self._content is None:
            from .content_layer import ContentCleaner

            self._content = ContentCleaner()
        return self._content

    async def initialize_all(self) -> bool:
        """
        Initialize all layers in proper order.

        Boot sequence (M1-optimized):
        1. Ghost (SystemContext) - anti-VM, security baseline
        2. Memory - RAM management before heavy ops
        3. Security - encryption ready
        4. Coordination - watchdog starts
        5. Stealth - protection active
        6. Research - AI components
        7. Privacy - network protection
        8. Communication - messaging ready
        9. Content - processing ready

        Returns:
            True if all layers initialized successfully
        """
        initialization_order = [
            ("ghost", self.ghost),
            ("memory", self.memory),
            ("security", self.security),
            ("coordination", self.coordination),
            ("stealth", self.stealth),
            ("research", self.research),
            ("privacy", self.privacy),
            ("communication", self.communication),
            ("content", self.content),
        ]
        success = True
        for name, layer in initialization_order:
            try:
                self._status[name] = LayerStatus.INITIALIZING
                logger.info("Initializing layer: %s", name)
                if hasattr(layer, "initialize") and self._is_async(name, "initialize", layer):
                    await layer.initialize()
                elif hasattr(layer, "_init_watchdog") and name == "coordination":
                    layer._init_watchdog()
                self._status[name] = LayerStatus.READY
                self._layers[name] = layer
                for method_name in ("initialize", "get_stats", "cleanup"):
                    self._is_async(name, method_name, layer)
                logger.info("Layer ready: %s", name)
                if name in ["research", "ghost", "memory"]:
                    cleanup = await self._memory_optimizer.force_cleanup()
                    logger.debug(f"Post-{name} cleanup: {cleanup['memory_freed_mb']:.1f}MB freed")
            except Exception as e:
                self._status[name] = LayerStatus.ERROR
                logger.error("Layer initialization failed: %s - %s", name, e)
                success = False
                if name in ["research", "content"]:
                    logger.warning(f"Non-critical layer {name} failed, continuing in degraded mode")
                    success = True
                else:
                    break
        return success

    async def health_check(self) -> dict[str, LayerHealth]:
        """
        Check health of all layers.

        Returns:
            Dictionary of layer health statuses
        """
        health = {}
        for name, layer in self._layers.items():
            try:
                status = self._status.get(name, LayerStatus.UNINITIALIZED)
                metadata = {}
                if hasattr(layer, "get_stats"):
                    try:
                        if self._is_async(name, "get_stats", layer):
                            metadata = await layer.get_stats()
                        else:
                            metadata = layer.get_stats()
                    except Exception as e:
                        metadata = {"error": str(e)}
                health[name] = LayerHealth(
                    name=name, status=status, initialized=status == LayerStatus.READY, metadata=metadata
                )
            except Exception as e:
                health[name] = LayerHealth(name=name, status=LayerStatus.ERROR, initialized=False, error_message=str(e))
        return health

    def get_layer(self, name: str) -> Any | None:
        """
        Get layer by name.

        Args:
            name: Layer name (ghost, memory, security, etc.)

        Returns:
            Layer instance or None
        """
        return self._layers.get(name)

    async def context_swap(self, active_layers: list[str]) -> bool:
        """
        Perform context swap to activate only specified layers.

        M1 8GB Optimization: Unloads inactive layers, loads active layers,
        performs aggressive cleanup between transitions.

        Args:
            active_layers: List of layer names to keep active

        Returns:
            True if context swap successful
        """
        logger.info("🔄 Context swap: active layers = %s", active_layers)
        current_active = [name for name, status in self._status.items() if status == LayerStatus.READY]
        to_unload = [name for name in current_active if name not in active_layers]
        to_load = [name for name in active_layers if name not in current_active]
        await self._memory_optimizer.context_swap(to_unload, to_load)
        for name in to_unload:
            if name in self._layers:
                layer = self._layers[name]
                if hasattr(layer, "cleanup") and self._is_async(name, "cleanup", layer):
                    try:
                        await layer.cleanup()
                    except Exception as e:
                        logger.warning(f"Layer cleanup failed: {name} - {e}")
                self._status[name] = LayerStatus.SHUTDOWN
        for name in to_load:
            if name not in self._layers:
                layer = getattr(self, name)
                if hasattr(layer, "initialize") and self._is_async(name, "initialize", layer):
                    try:
                        await layer.initialize()
                        self._status[name] = LayerStatus.READY
                        self._layers[name] = layer
                    except Exception as e:
                        logger.error("Layer initialization failed: %s - %s", name, e)
                        self._status[name] = LayerStatus.ERROR
        logger.info("✅ Context swap complete")
        return True

    async def force_memory_cleanup(self) -> dict[str, Any]:
        """
        Force immediate memory cleanup.

        Returns:
            Cleanup statistics
        """
        return await self._memory_optimizer.force_cleanup()

    def check_memory_pressure(self) -> bool:
        """
        Check if system is under memory pressure.

        Returns:
            True if memory pressure detected
        """
        return self._memory_optimizer.check_memory_pressure()

    async def shutdown_all(self) -> bool:
        """
        Gracefully shutdown all layers in reverse order.

        Returns:
            True if all layers shutdown successfully
        """
        shutdown_order = [
            "content",
            "communication",
            "privacy",
            "research",
            "stealth",
            "coordination",
            "security",
            "memory",
            "ghost",
        ]
        success = True
        for name in shutdown_order:
            if name not in self._layers:
                continue
            try:
                layer = self._layers[name]
                logger.info("Shutting down layer: %s", name)
                if hasattr(layer, "cleanup") and self._is_async(name, "cleanup", layer):
                    await layer.cleanup()
                if name == "memory" and hasattr(layer, "shutdown"):
                    layer.shutdown()
                self._status[name] = LayerStatus.SHUTDOWN
                logger.info("Layer shutdown: %s", name)
            except Exception as e:
                logger.error("Layer shutdown failed: %s - %s", name, e)
                success = False
        return success

    def get_summary(self) -> dict[str, Any]:
        """
        Get summary of all layers.

        Returns:
            Summary dictionary with layer statuses
        """
        return {
            "total_layers": len(self._layers),
            "ready": sum(1 for s in self._status.values() if s == LayerStatus.READY),
            "errors": sum(1 for s in self._status.values() if s == LayerStatus.ERROR),
            "uninitialized": sum(1 for s in self._status.values() if s == LayerStatus.UNINITIALIZED),
            "layers": {
                name: {"status": status.value, "initialized": status == LayerStatus.READY}
                for name, status in self._status.items()
            },
            "m1_optimizer": self._memory_optimizer.get_stats(),
        }


def create_layer_manager(config: dict[str, Any] | None = None) -> LayerManager:
    """Factory function to create LayerManager"""
    return LayerManager(config)


_layer_manager_instance: LayerManager | None = None


def get_layer_manager() -> LayerManager:
    """Get or create global LayerManager instance"""
    global _layer_manager_instance
    if _layer_manager_instance is None:
        _layer_manager_instance = LayerManager()
    return _layer_manager_instance


class UnifiedCapabilitiesManager:
    """
    Centralized access to ALL system capabilities.

    Combines:
    - All 9 Layers (Ghost, Memory, Security, Stealth, Research, Privacy, Coordination, Communication, Content)
    - All 8+ Coordinators (Research, Execution, Security, Memory, etc.)
    - All Utils (Query expansion, ranking, cache, etc.)

    This is the single entry point for accessing any system capability.
    """

    __slots__ = ("_coordinators", "_initialized", "_utils", "layers")

    def __init__(self, layer_manager: LayerManager | None = None) -> None:
        self.layers = layer_manager or get_layer_manager()
        self._coordinators: dict[str, Any] = {}
        self._utils: dict[str, Any] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize all capabilities"""
        if self._initialized:
            return True
        logger.info("🚀 Initializing Unified Capabilities Manager...")
        await self.layers.initialize_all()
        await self._init_coordinators()
        await self._init_utils()
        self._initialized = True
        logger.info("✅ Unified Capabilities Manager ready")
        return True

    async def _init_coordinators(self) -> None:
        """Initialize all coordinators via coordination layer"""
        try:
            coord_layer = self.layers.coordination
            if hasattr(coord_layer, "initialize"):
                await coord_layer.initialize()
                logger.info("✅ Coordinators initialized via CoordinationLayer")
        except Exception as e:
            logger.warning("Coordinator initialization: %s", e)

    async def _init_utils(self) -> None:
        """Initialize utility components"""
        try:
            from ..utils.intelligent_cache import IntelligentCache
            from ..utils.language import LanguageDetector
            from ..utils.query_expansion import QueryExpander
            from ..utils.ranking import ReciprocalRankFusion

            self._utils["query_expander"] = QueryExpander()
            self._utils["ranking"] = ReciprocalRankFusion()
            self._utils["cache"] = IntelligentCache()
            self._utils["language_detector"] = LanguageDetector()
            logger.info("✅ Utils initialized: %s", list(self._utils.keys()))
        except Exception as e:
            logger.warning("Utils initialization: %s", e)

    @property
    def ghost(self) -> Any:
        """Ghost layer with anti-loop, vault, system context"""
        return self.layers.ghost

    @property
    def memory(self) -> Any:
        """Memory layer with RAM disk, shared memory"""
        return self.layers.memory

    @property
    def security(self) -> Any:
        """Security layer with obfuscation, audit"""
        return self.layers.security

    @property
    def stealth(self) -> Any:
        """Stealth layer with browser, evasion"""
        return self.layers.stealth

    @property
    def research(self) -> Any:
        """Research layer with GhostDirector"""
        return self.layers.research

    @property
    def privacy(self) -> Any:
        """Privacy layer with VPN/Tor, PGP"""
        return self.layers.privacy

    @property
    def coordination(self) -> Any:
        """Coordination layer with all coordinators"""
        return self.layers.coordination

    @property
    def communication(self) -> Any:
        """Communication layer with A2A protocol"""
        return self.layers.communication

    @property
    def content(self) -> Any:
        """Content layer with HTML cleaning"""
        return self.layers.content

    def get_coordinator(self, name: str) -> Any | None:
        """Get coordinator by name"""
        return self._coordinators.get(name)

    @property
    def agent_coordination(self) -> Any | None:
        """Agent coordination engine"""
        if "agent" not in self._coordinators:
            try:
                from ..coordinators.agent_coordination_engine import AgentCoordinationEngine

                self._coordinators["agent"] = AgentCoordinationEngine()
            except Exception as e:
                logger.debug("Agent coordination not available: %s", e)
        return self._coordinators.get("agent")

    @property
    def research_optimizer(self) -> Any | None:
        """Research optimizer with caching"""
        if "optimizer" not in self._coordinators:
            try:
                from ..coordinators.research_optimizer import ResearchOptimizer

                self._coordinators["optimizer"] = ResearchOptimizer()
            except Exception as e:
                logger.debug(f"Research optimizer not available: {e}")
        return self._coordinators.get("optimizer")

    @property
    def privacy_enhanced(self) -> Any | None:
        """Privacy enhanced research"""
        if "privacy" not in self._coordinators:
            try:
                from ..coordinators.privacy_enhanced_research import PrivacyEnhancedResearch

                self._coordinators["privacy"] = PrivacyEnhancedResearch()
            except Exception as e:
                logger.debug("Privacy enhanced not available: %s", e)
        return self._coordinators.get("privacy")

    @property
    def execution(self) -> Any | None:
        """Execution coordinator"""
        if "execution" not in self._coordinators:
            try:
                from ..coordinators.execution_coordinator import UniversalExecutionCoordinator

                self._coordinators["execution"] = UniversalExecutionCoordinator()
            except Exception as e:
                logger.debug("Execution coordinator not available: %s", e)
        return self._coordinators.get("execution")

    @property
    def memory_coordination(self) -> Any | None:
        """Memory coordinator"""
        if "memory_coord" not in self._coordinators:
            try:
                from ..coordinators.memory_coordinator import UniversalMemoryCoordinator

                self._coordinators["memory_coord"] = UniversalMemoryCoordinator()
            except Exception as e:
                logger.debug("Memory coordinator not available: %s", e)
        return self._coordinators.get("memory_coord")

    @property
    def security_coordination(self) -> Any | None:
        """Security coordinator"""
        if "security_coord" not in self._coordinators:
            try:
                from ..coordinators.security_coordinator import UniversalSecurityCoordinator

                self._coordinators["security_coord"] = UniversalSecurityCoordinator()
            except Exception as e:
                logger.debug("Security coordinator not available: %s", e)
        return self._coordinators.get("security_coord")

    @property
    def monitoring(self) -> Any | None:
        """Monitoring coordinator"""
        if "monitoring" not in self._coordinators:
            try:
                from ..coordinators.monitoring_coordinator import UniversalMonitoringCoordinator

                self._coordinators["monitoring"] = UniversalMonitoringCoordinator()
            except Exception as e:
                logger.debug("Monitoring coordinator not available: %s", e)
        return self._coordinators.get("monitoring")

    @property
    def query_expander(self) -> Any | None:
        """Query expansion utility"""
        return self._utils.get("query_expander")

    @property
    def ranking(self) -> Any | None:
        """Ranking/fusion utility"""
        return self._utils.get("ranking")

    @property
    def cache(self) -> Any | None:
        """Intelligent cache"""
        return self._utils.get("cache")

    @property
    def language_detector(self) -> Any | None:
        """Language detection"""
        return self._utils.get("language_detector")

    @property
    def rag(self) -> Any | None:
        """RAG engine"""
        try:
            from ..knowledge.rag_engine import RAGEngine

            if "rag" not in self._coordinators:
                self._coordinators["rag"] = RAGEngine()
            return self._coordinators["rag"]
        except Exception as e:
            logger.debug("RAG not available: %s", e)
            return None

    @property
    def knowledge_graph(self) -> Any | None:
        """Atomic storage knowledge graph"""
        try:
            from ..legacy.atomic_storage import AtomicJSONKnowledgeGraph

            if "kg" not in self._coordinators:
                self._coordinators["kg"] = AtomicJSONKnowledgeGraph()
            return self._coordinators["kg"]
        except Exception as e:
            logger.debug("Knowledge graph not available: %s", e)
            return None

    async def health_check(self) -> dict[str, Any]:
        """Comprehensive health check of all capabilities"""
        layer_health = await self.layers.health_check()
        return {
            "layers": layer_health,
            "coordinators": {
                name: "available" if coord is not None else "unavailable" for name, coord in self._coordinators.items()
            },
            "utils": list(self._utils.keys()),
            "overall_status": "healthy"
            if all(h.status == LayerStatus.READY for h in layer_health.values())
            else "degraded",
        }

    def get_capabilities_summary(self) -> dict[str, list[str]]:
        """Get summary of all available capabilities"""
        return {
            "layers": [
                "ghost",
                "memory",
                "security",
                "stealth",
                "research",
                "privacy",
                "coordination",
                "communication",
                "content",
            ],
            "coordinators": list(self._coordinators.keys()),
            "utils": list(self._utils.keys()),
        }

    async def cleanup(self) -> None:
        """Cleanup all capabilities"""
        await self.layers.shutdown_all()
        self._initialized = False


def create_capabilities_manager(layer_manager: LayerManager | None = None) -> UnifiedCapabilitiesManager:
    """Create unified capabilities manager"""
    return UnifiedCapabilitiesManager(layer_manager)


_capabilities_manager_instance: UnifiedCapabilitiesManager | None = None


def get_capabilities_manager() -> UnifiedCapabilitiesManager:
    """Get or create global capabilities manager"""
    global _capabilities_manager_instance
    if _capabilities_manager_instance is None:
        _capabilities_manager_instance = UnifiedCapabilitiesManager()
    return _capabilities_manager_instance

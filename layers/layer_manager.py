"""
Layer Manager - Centralized Layer Orchestration
===============================================

Provides unified initialization, coordination and lifecycle management
for all universal orchestrator layers.

Usage:
    manager = LayerManager()
    await manager.initialize_all()  # Boot sequence
    
    # Access any layer
    watchdog = manager.coordination.watchdog
    system_context = manager.ghost.system_context
    
    # Health check
    health = await manager.health_check()
    
    # Graceful shutdown
    await manager.shutdown_all()
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# M1 8GB Optimization: Context swap and memory management
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

    def __init__(self, memory_limit_mb: float = 5500):
        self.memory_limit_mb = memory_limit_mb
        self._gc_count = 0
        self._cache_clears = 0
        self._context_swaps = 0

    async def force_cleanup(self) -> Dict[str, Any]:
        """Force aggressive memory cleanup."""
        import psutil

        before = psutil.virtual_memory().used / (1024 * 1024)

        # 1. Clear MLX cache
        try:
            import mlx.core as mx
            mx.eval([])
            mx.clear_cache()
            self._cache_clears += 1
            logger.debug("🧹 MLX cache cleared")
        except Exception:
            pass

        # 2. Force garbage collection
        gc.collect()
        self._gc_count += 1
        logger.debug(f"🗑️ GC #{self._gc_count}")

        # 3. Small delay for cleanup to take effect
        await asyncio.sleep(0.1)

        after = psutil.virtual_memory().used / (1024 * 1024)

        return {
            "memory_freed_mb": before - after,
            "gc_count": self._gc_count,
            "cache_clears": self._cache_clears,
        }

    def check_memory_pressure(self) -> bool:
        """Check if system is under memory pressure."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            used_mb = memory.used / (1024 * 1024)
            return used_mb > self.memory_limit_mb
        except Exception:
            return False

    async def context_swap(self, unload_layers: List[str], load_layers: List[str]) -> None:
        """
        Perform context swap: unload layers, cleanup, load new layers.

        Args:
            unload_layers: Layer names to unload
            load_layers: Layer names to load
        """
        logger.info(f"🔄 Context swap: {unload_layers} → {load_layers}")

        # Unload layers
        for layer_name in unload_layers:
            await self._unload_layer(layer_name)

        # Aggressive cleanup
        await self.force_cleanup()

        # Load new layers
        for layer_name in load_layers:
            await self._load_layer(layer_name)

        self._context_swaps += 1
        logger.info(f"✅ Context swap complete (#{self._context_swaps})")

    async def _unload_layer(self, layer_name: str) -> None:
        """Unload a layer to free memory."""
        logger.debug(f"📤 Unloading layer: {layer_name}")
        # Layer unloading is handled by the layer itself
        await asyncio.sleep(0.05)  # Small delay for cleanup

    async def _load_layer(self, layer_name: str) -> None:
        """Load a layer."""
        logger.debug(f"📥 Loading layer: {layer_name}")
        await asyncio.sleep(0.05)  # Small delay for initialization

    def get_stats(self) -> Dict[str, Any]:
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


@dataclass
class LayerHealth:
    """Layer health status"""
    name: str
    status: LayerStatus
    initialized: bool
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None


class LayerManager:
    """
    Centralized manager for all universal layers.

    Features:
    - Ordered initialization (dependencies first)
    - Health monitoring
    - Graceful shutdown
    - Layer dependency resolution
    - M1 memory-aware boot sequence
    - Shared GhostDirector singleton (prevents duplicate initialization)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize LayerManager.

        Args:
            config: Optional configuration for layers
        """
        self.config = config or {}
        self._layers: Dict[str, Any] = {}
        self._status: Dict[str, LayerStatus] = {}

        # Lazy imports to avoid circular dependencies
        self._coordination = None
        self._ghost = None
        self._memory = None
        self._security = None
        self._stealth = None
        self._research = None
        self._privacy = None
        self._communication = None
        self._content = None

        # Shared GhostDirector singleton - prevents duplicate initialization
        # between GhostLayer and ResearchLayer (M1 RAM optimization)
        self._ghost_director: Optional[Any] = None
        self._ghost_director_initialized: bool = False

        # M1 8GB Memory Optimizer
        self._memory_optimizer = M1MemoryOptimizer(
            memory_limit_mb=self.config.get('memory_limit_mb', 5500)
        )

        logger.info("LayerManager initialized (M1 8GB optimized)")

    def get_ghost_director(self) -> Optional[Any]:
        """
        Get or create shared GhostDirector instance.

        This is a singleton pattern to prevent both GhostLayer and ResearchLayer
        from creating their own GhostDirector instances, saving M1 8GB RAM.

        Returns:
            GhostDirector instance or None if not available
        """
        if self._ghost_director is None:
            try:
                from hledac.cortex.director import GhostDirector

                self._ghost_director = GhostDirector(
                    max_steps=20,
                )
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
            logger.error(f"❌ GhostDirector initialization failed: {e}")
            return False
    
    @property
    def coordination(self) -> Any:
        """Get or create coordination layer"""
        if self._coordination is None:
            from .coordination_layer import CoordinationLayer
            self._coordination = CoordinationLayer()
        return self._coordination
    
    @property
    def ghost(self) -> Any:
        """Get or create ghost layer"""
        if self._ghost is None:
            from .ghost_layer import GhostLayer
            # Pass shared GhostDirector reference to prevent duplicate initialization
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
            # Pass shared GhostDirector reference to prevent duplicate initialization
            self._research = ResearchLayer(ghost_director=self.get_ghost_director())
        return self._research
    
    @property
    def privacy(self) -> Any:
        """Get or create privacy layer"""
        if self._privacy is None:
            from .privacy_layer import PrivacyLayer
            from ..config import PrivacyConfig
            # Pass security layer for unified audit logging
            config = self.config.get('privacy', PrivacyConfig())
            self._privacy = PrivacyLayer(config=config, security_layer=self.security)
        return self._privacy
    
    @property
    def communication(self) -> Any:
        """Get or create communication layer"""
        if self._communication is None:
            from .communication_layer import CommunicationLayer, CommunicationConfig
            # Sprint 82M: Pass config to CommunicationLayer
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
                logger.info(f"Initializing layer: {name}")
                
                # Check if layer has async initialize method
                if hasattr(layer, 'initialize') and inspect.iscoroutinefunction(layer.initialize):
                    await layer.initialize()
                elif hasattr(layer, '_init_watchdog') and name == "coordination":
                    # Special case for coordination layer
                    layer._init_watchdog()
                
                self._status[name] = LayerStatus.READY
                self._layers[name] = layer
                logger.info(f"Layer ready: {name}")

                # M1 8GB: Force cleanup after heavy layers
                if name in ["research", "ghost", "memory"]:
                    cleanup = await self._memory_optimizer.force_cleanup()
                    logger.debug(f"Post-{name} cleanup: {cleanup['memory_freed_mb']:.1f}MB freed")

            except Exception as e:
                self._status[name] = LayerStatus.ERROR
                logger.error(f"Layer initialization failed: {name} - {e}")
                success = False
                
                # M1-specific: continue with degraded mode if non-critical layer fails
                if name in ["research", "content"]:
                    logger.warning(f"Non-critical layer {name} failed, continuing in degraded mode")
                    success = True
                else:
                    break
        
        return success
    
    async def health_check(self) -> Dict[str, LayerHealth]:
        """
        Check health of all layers.
        
        Returns:
            Dictionary of layer health statuses
        """
        health = {}
        
        for name, layer in self._layers.items():
            try:
                status = self._status.get(name, LayerStatus.UNINITIALIZED)
                
                # Get layer-specific health info
                metadata = {}
                if hasattr(layer, 'get_stats'):
                    try:
                        if inspect.iscoroutinefunction(layer.get_stats):
                            metadata = await layer.get_stats()
                        else:
                            metadata = layer.get_stats()
                    except Exception as e:
                        metadata = {"error": str(e)}
                
                health[name] = LayerHealth(
                    name=name,
                    status=status,
                    initialized=status == LayerStatus.READY,
                    metadata=metadata
                )
                
            except Exception as e:
                health[name] = LayerHealth(
                    name=name,
                    status=LayerStatus.ERROR,
                    initialized=False,
                    error_message=str(e)
                )
        
        return health
    
    def get_layer(self, name: str) -> Optional[Any]:
        """
        Get layer by name.
        
        Args:
            name: Layer name (ghost, memory, security, etc.)
            
        Returns:
            Layer instance or None
        """
        return self._layers.get(name)

    async def context_swap(self, active_layers: List[str]) -> bool:
        """
        Perform context swap to activate only specified layers.

        M1 8GB Optimization: Unloads inactive layers, loads active layers,
        performs aggressive cleanup between transitions.

        Args:
            active_layers: List of layer names to keep active

        Returns:
            True if context swap successful
        """
        logger.info(f"🔄 Context swap: active layers = {active_layers}")

        # Determine layers to unload/load
        current_active = [name for name, status in self._status.items()
                         if status == LayerStatus.READY]
        to_unload = [name for name in current_active if name not in active_layers]
        to_load = [name for name in active_layers if name not in current_active]

        # Perform context swap via optimizer
        await self._memory_optimizer.context_swap(to_unload, to_load)

        # Update layer statuses
        for name in to_unload:
            if name in self._layers:
                layer = self._layers[name]
                if hasattr(layer, 'cleanup') and inspect.iscoroutinefunction(layer.cleanup):
                    try:
                        await layer.cleanup()
                    except Exception as e:
                        logger.warning(f"Layer cleanup failed: {name} - {e}")
                self._status[name] = LayerStatus.SHUTDOWN

        for name in to_load:
            if name not in self._layers:
                # Initialize layer
                layer = getattr(self, name)
                if hasattr(layer, 'initialize') and inspect.iscoroutinefunction(layer.initialize):
                    try:
                        await layer.initialize()
                        self._status[name] = LayerStatus.READY
                        self._layers[name] = layer
                    except Exception as e:
                        logger.error(f"Layer initialization failed: {name} - {e}")
                        self._status[name] = LayerStatus.ERROR

        logger.info(f"✅ Context swap complete")
        return True

    async def force_memory_cleanup(self) -> Dict[str, Any]:
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
                logger.info(f"Shutting down layer: {name}")
                
                # Check if layer has cleanup method
                if hasattr(layer, 'cleanup') and inspect.iscoroutinefunction(layer.cleanup):
                    await layer.cleanup()
                elif hasattr(layer, 'nuke') and name == "memory":
                    # Special case for memory layer (RAM disk cleanup)
                    if hasattr(layer, 'ram_disk'):
                        layer.ram_disk.nuke()
                
                self._status[name] = LayerStatus.SHUTDOWN
                logger.info(f"Layer shutdown: {name}")
                
            except Exception as e:
                logger.error(f"Layer shutdown failed: {name} - {e}")
                success = False
        
        return success
    
    def get_summary(self) -> Dict[str, Any]:
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
                name: {
                    "status": status.value,
                    "initialized": status == LayerStatus.READY
                }
                for name, status in self._status.items()
            },
            "m1_optimizer": self._memory_optimizer.get_stats(),
        }


# Convenience factory function
def create_layer_manager(config: Optional[Dict[str, Any]] = None) -> LayerManager:
    """Factory function to create LayerManager"""
    return LayerManager(config)


# Singleton instance for application-wide use
_layer_manager_instance: Optional[LayerManager] = None


def get_layer_manager() -> LayerManager:
    """Get or create global LayerManager instance"""
    global _layer_manager_instance
    if _layer_manager_instance is None:
        _layer_manager_instance = LayerManager()
    return _layer_manager_instance


# =============================================================================
# UNIFIED CAPABILITIES MANAGER - All Tools & Coordinators in One Place
# =============================================================================

class UnifiedCapabilitiesManager:
    """
    Centralized access to ALL system capabilities.
    
    Combines:
    - All 9 Layers (Ghost, Memory, Security, Stealth, Research, Privacy, Coordination, Communication, Content)
    - All 8+ Coordinators (Research, Execution, Security, Memory, etc.)
    - All Utils (Query expansion, ranking, cache, etc.)
    
    This is the single entry point for accessing any system capability.
    """
    
    def __init__(self, layer_manager: Optional[LayerManager] = None):
        self.layers = layer_manager or get_layer_manager()
        self._coordinators: Dict[str, Any] = {}
        self._utils: Dict[str, Any] = {}
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialize all capabilities"""
        if self._initialized:
            return True
        
        logger.info("🚀 Initializing Unified Capabilities Manager...")
        
        # 1. Initialize all layers
        await self.layers.initialize_all()
        
        # 2. Initialize coordinators through coordination layer
        await self._init_coordinators()
        
        # 3. Initialize utils
        await self._init_utils()
        
        self._initialized = True
        logger.info("✅ Unified Capabilities Manager ready")
        return True
    
    async def _init_coordinators(self) -> None:
        """Initialize all coordinators via coordination layer"""
        try:
            coord_layer = self.layers.coordination
            if hasattr(coord_layer, 'initialize'):
                await coord_layer.initialize()
                logger.info("✅ Coordinators initialized via CoordinationLayer")
        except Exception as e:
            logger.warning(f"Coordinator initialization: {e}")
    
    async def _init_utils(self) -> None:
        """Initialize utility components"""
        try:
            from ..utils.query_expansion import QueryExpander
            from ..utils.ranking import ReciprocalRankFusion
            from ..utils.intelligent_cache import IntelligentCache
            from ..utils.language import LanguageDetector
            
            self._utils['query_expander'] = QueryExpander()
            self._utils['ranking'] = ReciprocalRankFusion()
            self._utils['cache'] = IntelligentCache()
            self._utils['language_detector'] = LanguageDetector()
            
            logger.info(f"✅ Utils initialized: {list(self._utils.keys())}")
        except Exception as e:
            logger.warning(f"Utils initialization: {e}")
    
    # === LAYER ACCESS ===
    
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
    
    # === COORDINATOR ACCESS ===
    
    def get_coordinator(self, name: str) -> Optional[Any]:
        """Get coordinator by name"""
        return self._coordinators.get(name)
    
    @property
    def agent_coordination(self) -> Optional[Any]:
        """Agent coordination engine"""
        if 'agent' not in self._coordinators:
            try:
                from ..coordinators.agent_coordination_engine import AgentCoordinationEngine
                self._coordinators['agent'] = AgentCoordinationEngine()
            except Exception as e:
                logger.debug(f"Agent coordination not available: {e}")
        return self._coordinators.get('agent')
    
    @property
    def research_optimizer(self) -> Optional[Any]:
        """Research optimizer with caching"""
        if 'optimizer' not in self._coordinators:
            try:
                from ..coordinators.research_optimizer import ResearchOptimizer
                self._coordinators['optimizer'] = ResearchOptimizer()
            except Exception as e:
                logger.debug(f"Research optimizer not available: {e}")
        return self._coordinators.get('optimizer')
    
    @property
    def privacy_enhanced(self) -> Optional[Any]:
        """Privacy enhanced research"""
        if 'privacy' not in self._coordinators:
            try:
                from ..coordinators.privacy_enhanced_research import PrivacyEnhancedResearch
                self._coordinators['privacy'] = PrivacyEnhancedResearch()
            except Exception as e:
                logger.debug(f"Privacy enhanced not available: {e}")
        return self._coordinators.get('privacy')
    
    @property
    def advanced_research(self) -> Optional[Any]:
        """Advanced research coordinator"""
        if 'advanced' not in self._coordinators:
            try:
                from ..coordinators.advanced_research_coordinator import UniversalAdvancedResearchCoordinator
                self._coordinators['advanced'] = UniversalAdvancedResearchCoordinator()
            except Exception as e:
                logger.debug(f"Advanced research not available: {e}")
        return self._coordinators.get('advanced')
    
    @property
    def execution(self) -> Optional[Any]:
        """Execution coordinator"""
        if 'execution' not in self._coordinators:
            try:
                from ..coordinators.execution_coordinator import UniversalExecutionCoordinator
                self._coordinators['execution'] = UniversalExecutionCoordinator()
            except Exception as e:
                logger.debug(f"Execution coordinator not available: {e}")
        return self._coordinators.get('execution')
    
    @property
    def memory_coordination(self) -> Optional[Any]:
        """Memory coordinator"""
        if 'memory_coord' not in self._coordinators:
            try:
                from ..coordinators.memory_coordinator import UniversalMemoryCoordinator
                self._coordinators['memory_coord'] = UniversalMemoryCoordinator()
            except Exception as e:
                logger.debug(f"Memory coordinator not available: {e}")
        return self._coordinators.get('memory_coord')
    
    @property
    def security_coordination(self) -> Optional[Any]:
        """Security coordinator"""
        if 'security_coord' not in self._coordinators:
            try:
                from ..coordinators.security_coordinator import UniversalSecurityCoordinator
                self._coordinators['security_coord'] = UniversalSecurityCoordinator()
            except Exception as e:
                logger.debug(f"Security coordinator not available: {e}")
        return self._coordinators.get('security_coord')
    
    @property
    def monitoring(self) -> Optional[Any]:
        """Monitoring coordinator"""
        if 'monitoring' not in self._coordinators:
            try:
                from ..coordinators.monitoring_coordinator import UniversalMonitoringCoordinator
                self._coordinators['monitoring'] = UniversalMonitoringCoordinator()
            except Exception as e:
                logger.debug(f"Monitoring coordinator not available: {e}")
        return self._coordinators.get('monitoring')
    
    # === UTILS ACCESS ===
    
    @property
    def query_expander(self) -> Optional[Any]:
        """Query expansion utility"""
        return self._utils.get('query_expander')
    
    @property
    def ranking(self) -> Optional[Any]:
        """Ranking/fusion utility"""
        return self._utils.get('ranking')
    
    @property
    def cache(self) -> Optional[Any]:
        """Intelligent cache"""
        return self._utils.get('cache')
    
    @property
    def language_detector(self) -> Optional[Any]:
        """Language detection"""
        return self._utils.get('language_detector')
    
    # === KNOWLEDGE ACCESS ===
    
    @property
    def rag(self) -> Optional[Any]:
        """RAG engine"""
        try:
            from ..knowledge.rag_engine import RAGEngine
            if 'rag' not in self._coordinators:
                self._coordinators['rag'] = RAGEngine()
            return self._coordinators['rag']
        except Exception as e:
            logger.debug(f"RAG not available: {e}")
            return None
    
    @property
    def knowledge_graph(self) -> Optional[Any]:
        """Atomic storage knowledge graph"""
        try:
            from ..legacy.atomic_storage import AtomicJSONKnowledgeGraph
            if 'kg' not in self._coordinators:
                self._coordinators['kg'] = AtomicJSONKnowledgeGraph()
            return self._coordinators['kg']
        except Exception as e:
            logger.debug(f"Knowledge graph not available: {e}")
            return None
    
    # === HEALTH & STATUS ===
    
    async def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of all capabilities"""
        layer_health = await self.layers.health_check()
        
        return {
            "layers": layer_health,
            "coordinators": {
                name: "available" if coord is not None else "unavailable"
                for name, coord in self._coordinators.items()
            },
            "utils": list(self._utils.keys()),
            "overall_status": "healthy" if all(
                h.status == LayerStatus.READY for h in layer_health.values()
            ) else "degraded"
        }
    
    def get_capabilities_summary(self) -> Dict[str, List[str]]:
        """Get summary of all available capabilities"""
        return {
            "layers": ["ghost", "memory", "security", "stealth", "research", 
                      "privacy", "coordination", "communication", "content"],
            "coordinators": list(self._coordinators.keys()),
            "utils": list(self._utils.keys()),
        }
    
    async def cleanup(self) -> None:
        """Cleanup all capabilities"""
        await self.layers.shutdown_all()
        self._initialized = False


# Factory function
def create_capabilities_manager(layer_manager: Optional[LayerManager] = None) -> UnifiedCapabilitiesManager:
    """Create unified capabilities manager"""
    return UnifiedCapabilitiesManager(layer_manager)


# Singleton
_capabilities_manager_instance: Optional[UnifiedCapabilitiesManager] = None


def get_capabilities_manager() -> UnifiedCapabilitiesManager:
    """Get or create global capabilities manager"""
    global _capabilities_manager_instance
    if _capabilities_manager_instance is None:
        _capabilities_manager_instance = UnifiedCapabilitiesManager()
    return _capabilities_manager_instance

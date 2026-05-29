"""
Memory Layer - M1 Memory Management and Context Swap
=====================================================

Manages memory for M1 8GB MacBook Air:
- System state machine (HEALTHY → MEMORY_PRESSURE → ...)
- Context swap between orchestrator states (unload/load models)
- Background health monitoring (memory, CPU, temperature)
- Thermal awareness and throttling
- Automatic mitigation actions
- RAM Disk operations (hdiutil-based)
- Shared memory for zero-copy inter-process communication
- Entropy masking for stealth operations

This is a thin wrapper around existing MemoryCoordinator with
integration logic for the universal orchestrator.

IMPORTANT: Layer-system memory surface — not the canonical Uma policy owner.
Canonical sprint Uma governance lives in core/resource_governor.py.
This module provides get_memory_pressure() for layer consumers.

Refactored with internal classes for M1 8GB optimization:
- _MemoryStateManager: System state machine and health monitoring
- _StorageCoordinator: RAM disk and shared memory management
- _StealthMemoryManager: Entropy masking for stealth operations
- _ThermalSampler: Offload-only thermal sampling (not canonical Uma owner)
"""

from __future__ import annotations

import asyncio
import gc
import logging
import subprocess
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Sprint 5N: Lazy MLX import - MLX is optional for M1 compatibility
_MLX_CORE = None

def _get_mlx():
    """Lazy import MLX core - returns None if MLX not available."""
    global _MLX_CORE
    if _MLX_CORE is None:
        try:
            import mlx.core as mx
            _MLX_CORE = mx
        except ImportError:
            _MLX_CORE = None
    return _MLX_CORE

from hledac.universal.project_types import (
    MemoryConfig,
    MemoryPressureError,
    OrchestratorState,
    SystemMetrics,
    SystemState,
)

logger = logging.getLogger(__name__)

# =============================================================================
# THERMAL SAMPLING OFFLOAD SEAM (not canonical Uma owner)
# =============================================================================
# This sampler is a pure sampling/offload layer — it does NOT own Uma policy.
# Canonical M1 Uma governance remains in core/resource_governor.py.
# This class only offloads blocking thermal reads from async hot paths.


@dataclass(slots=True, frozen=True)
class ThermalSnapshot:
    """Immutable snapshot of thermal reading with TTL tracking."""
    celsius: float | None
    sampled_at_monotonic: float  # time.monotonic() at sample time


class _ThermalSampler:
    """
    Async-owned thermal sampler with TTL caching and fail-soft recovery.

    Offloads blocking ioreg subprocess calls from async hot paths via
    asyncio.to_thread. Not the canonical Uma owner — sampling only.
    """

    def __init__(self, ttl_s: float = 10.0) -> None:
        self._ttl_s = ttl_s
        self._lock = asyncio.Lock()
        self._cache: ThermalSnapshot | None = None

    def _read_temperature_sync(self) -> float | None:
        """
        Blocking thermal read via ioreg (M1 MacBook Air).
        MUST be called via asyncio.to_thread, never directly from event loop.
        Returns None on any error (fail-soft).
        """
        try:
            subprocess.run(
                ["ioreg", "-r", "-c", "AppleSmartBattery", "-w0"],
                capture_output=True, text=True, timeout=1
            )
            # Current implementation returns None — structured for future parsing
            return None
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None

    async def sample(self) -> float | None:
        """
        Get temperature with TTL caching and double-check locking.

        Fast path: returns cached value if fresh (within TTL).
        Slow path: acquires lock, double-checks freshness, then samples.
        All blocking I/O offloaded via asyncio.to_thread.
        """
        now = time.monotonic()
        cached = self._cache

        # Fast path: fresh cache without lock
        if cached is not None and (now - cached.sampled_at_monotonic) < self._ttl_s:
            return cached.celsius

        # Slow path: double-check with lock
        async with self._lock:
            # Double-check after acquiring lock (another coroutine may have updated)
            now = time.monotonic()
            if self._cache is not None and (now - self._cache.sampled_at_monotonic) < self._ttl_s:
                return self._cache.celsius

            # Sample via thread offload
            celsius = await asyncio.to_thread(self._read_temperature_sync)

            # Fail-soft: preserve last known value if sample failed
            if celsius is None and self._cache is not None:
                cached_age = now - self._cache.sampled_at_monotonic
                if cached_age < self._ttl_s * 2:  # Allow stale window
                    celsius = self._cache.celsius

            self._cache = ThermalSnapshot(celsius=celsius, sampled_at_monotonic=now)
            return celsius


# =============================================================================
# INTERNAL MEMORY MANAGEMENT CLASSES (M1 8GB Optimized)
# =============================================================================

class _MemoryStateManager:
    """
    Internal: System state machine and health monitoring.

    Responsibilities:
    - System state transitions (HEALTHY → MEMORY_PRESSURE → ...)
    - Background health monitoring loop
    - Thermal awareness and throttling
    - Automatic mitigation actions
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._current_state = SystemState.HEALTHY
        self._max_history = 100
        self._metrics_history: deque = deque(maxlen=self._max_history)  # F206L: bounded
        self._health_check_task: asyncio.Task | None = None
        self._running = False
        self._state_change_callbacks: list[Callable[[SystemState, SystemState], None]] = []
        self._state_transitions: dict[str, int] = {s.value: 0 for s in SystemState}
        # Thermal sampler: offload-only, not canonical Uma owner
        self._thermal_sampler = _ThermalSampler(ttl_s=10.0)

    async def start_monitoring(self) -> None:
        """Start background health monitoring."""
        self._running = True
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(),
            name="memory_health_check"
        )
        logger.info("🏥 Memory state monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop background health monitoring."""
        self._running = False
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

    async def _health_check_loop(self) -> None:
        """Background health monitoring loop."""
        while self._running:
            try:
                metrics = await self._perform_health_check()
                self._metrics_history.append(metrics)
                if len(self._metrics_history) > self._max_history:
                    self._metrics_history.popleft()

                new_state = self._determine_state(metrics)
                if new_state != self._current_state:
                    await self._handle_state_transition(self._current_state, new_state, metrics)

                await asyncio.sleep(self.config.health_check_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(5)

    async def _perform_health_check(self) -> SystemMetrics:
        """Collect system health metrics."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            memory_used_mb = memory.used / (1024 * 1024)
            memory_available_mb = memory.available / (1024 * 1024)
            cpu_percent = psutil.cpu_percent(interval=0.1)
            temperature_c = await self._get_temperature()

            return SystemMetrics(
                memory_used_mb=memory_used_mb,
                memory_available_mb=memory_available_mb,
                cpu_percent=cpu_percent,
                temperature_c=temperature_c,
                state=self._current_state,
                timestamp=__import__('time').time()
            )
        except Exception as e:
            logger.warning(f"Failed to collect metrics: {e}")
            return SystemMetrics(
                memory_used_mb=0,
                memory_available_mb=self.config.memory_limit_mb,
                cpu_percent=0,
                temperature_c=None,
                state=self._current_state,
                timestamp=__import__('time').time()
            )

    async def _get_temperature(self) -> float | None:
        """
        Get M1 temperature (if available).

        Delegates to _ThermalSampler for async-safe sampling.
        This is sampling/offload only — NOT canonical Uma policy owner.
        """
        return await self._thermal_sampler.sample()

    def _determine_state(self, metrics: SystemMetrics) -> SystemState:
        """Determine system state from metrics."""
        if metrics.temperature_c and metrics.temperature_c > self.config.thermal_threshold_c:
            return SystemState.THERMAL_THROTTLING

        memory_usage_percent = (
            metrics.memory_used_mb /
            (metrics.memory_used_mb + metrics.memory_available_mb)
        ) * 100 if (metrics.memory_used_mb + metrics.memory_available_mb) > 0 else 0

        if metrics.memory_used_mb > self.config.memory_limit_mb:
            return SystemState.MEMORY_PRESSURE

        if memory_usage_percent > 90:
            return SystemState.DEGRADED

        return SystemState.HEALTHY

    async def _handle_state_transition(
        self, old_state: SystemState, new_state: SystemState, metrics: SystemMetrics
    ) -> None:
        """Handle system state transition."""
        logger.warning(
            f"🚨 System state transition: {old_state.value} → {new_state.value} "
            f"(Memory: {metrics.memory_used_mb:.0f}MB, CPU: {metrics.cpu_percent:.1f}%)"
        )
        self._current_state = new_state
        self._state_transitions[new_state.value] += 1

        for callback in self._state_change_callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                logger.warning(f"State change callback error: {e}")

    def on_state_change(self, callback: Callable[[SystemState, SystemState], None]) -> None:
        """Register callback for system state changes."""
        self._state_change_callbacks.append(callback)

    def get_current_state(self) -> SystemState:
        """Get current system state."""
        return self._current_state

    def get_metrics(self) -> SystemMetrics:
        """Get current system metrics."""
        if self._metrics_history:
            return self._metrics_history[-1]
        return SystemMetrics(
            memory_used_mb=0,
            memory_available_mb=self.config.memory_limit_mb,
            cpu_percent=0,
            temperature_c=None,
            state=self._current_state,
            timestamp=__import__('time').time()
        )

    def get_statistics(self) -> dict[str, Any]:
        """Get state manager statistics."""
        return {
            "current_state": self._current_state.value,
            "state_transitions": self._state_transitions,
            "metrics_history_count": len(self._metrics_history),
        }


class _StorageCoordinator:
    """
    Internal: RAM disk and shared memory management.

    Responsibilities:
    - RAM disk creation and management (hdiutil-based)
    - Shared memory for zero-copy IPC
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._ramdisk_manager: RAMDiskManager | None = None
        self._shared_memory_manager: SharedMemoryManager | None = None

    async def initialize(self) -> None:
        """Initialize storage coordinators."""
        await self._init_shared_memory_manager()

    async def _init_shared_memory_manager(self) -> None:
        """Initialize SharedMemoryManager for zero-copy operations."""
        try:
            self._shared_memory_manager = SharedMemoryManager(
                max_memory_mb=self.config.memory_limit_mb // 2
            )
            logger.info("✅ SharedMemoryManager initialized")
        except Exception as e:
            logger.warning(f"⚠️ SharedMemoryManager not available: {e}")
            self._shared_memory_manager = None

    def create_ramdisk(self, size_mb: int | None = None) -> RAMDiskManager:
        """Create a RAM disk for high-speed temporary storage."""
        config = RAMDiskConfig(size_mb=size_mb or 512)
        self._ramdisk_manager = RAMDiskManager(config)
        return self._ramdisk_manager

    def get_ramdisk(self) -> RAMDiskManager | None:
        """Get current RAM disk manager if active."""
        return self._ramdisk_manager

    def create_shared_block(self, data: bytes, data_type: str,
                           metadata: dict[str, Any] | None = None) -> str | None:
        """Create a shared memory block for zero-copy data sharing."""
        if self._shared_memory_manager:
            try:
                return self._shared_memory_manager.create_shared_block(data, data_type, metadata)
            except Exception as e:
                logger.error(f"Failed to create shared block: {e}")
        return None

    def get_shared_data(self, block_id: str) -> bytes | None:
        """Retrieve data from shared memory block."""
        if self._shared_memory_manager:
            return self._shared_memory_manager.get_shared_data(block_id)
        return None

    def release_shared_block(self, block_id: str) -> bool:
        """Release a shared memory block."""
        if self._shared_memory_manager:
            return self._shared_memory_manager.release_block(block_id)
        return False

    def shutdown(self) -> None:
        """Shutdown storage coordinators."""
        if self._shared_memory_manager:
            self._shared_memory_manager.shutdown()
        # Sprint 35: Explicit RAM disk shutdown
        if hasattr(self, '_ramdisk_manager') and self._ramdisk_manager:
            self._ramdisk_manager.shutdown()

    def get_statistics(self) -> dict[str, Any]:
        """Get storage coordinator statistics."""
        stats = {}
        if self._shared_memory_manager:
            stats['shared_memory'] = self._shared_memory_manager.get_statistics()
        return stats


class _StealthMemoryManager:
    """
    Internal: Entropy masking for stealth operations.

    Responsibilities:
    - Entropy noise injection to reduce Shannon entropy
    - Stealth memory operations
    """

    def __init__(self):
        self._entropy_masking_manager: EntropyMaskingManager | None = None

    async def initialize(self) -> None:
        """Initialize stealth memory manager."""
        await self._init_entropy_masking_manager()

    async def _init_entropy_masking_manager(self) -> None:
        """Initialize EntropyMaskingManager for stealth operations."""
        try:
            self._entropy_masking_manager = EntropyMaskingManager(noise_size_mb=50)
            logger.info("✅ EntropyMaskingManager initialized")
        except Exception as e:
            logger.warning(f"⚠️ EntropyMaskingManager not available: {e}")
            self._entropy_masking_manager = None

    def inject_entropy_noise(self) -> str | None:
        """Inject entropy noise to reduce Shannon entropy."""
        if self._entropy_masking_manager:
            try:
                return self._entropy_masking_manager.inject_entropy_noise()
            except Exception as e:
                logger.error(f"Failed to inject entropy noise: {e}")
        return None

    def get_entropy_stats(self) -> dict[str, Any]:
        """Get entropy masking statistics."""
        if self._entropy_masking_manager:
            return self._entropy_masking_manager.get_entropy_reduction_stats()
        return {'active_masking': False}

    def clear_noise_blocks(self) -> None:
        """Clear all entropy noise blocks."""
        if self._entropy_masking_manager:
            self._entropy_masking_manager.clear_noise_blocks()

    def get_statistics(self) -> dict[str, Any]:
        """Get stealth memory statistics."""
        if self._entropy_masking_manager:
            return self._entropy_masking_manager.get_entropy_reduction_stats()
        return {'active_masking': False}


# =============================================================================
# MAIN MEMORY LAYER (Coordinates internal managers)
# =============================================================================

class MemoryLayer:
    """
    Memory management layer for M1 8GB optimization.

    Uses internal coordinator classes for clean separation of concerns:
    - _MemoryStateManager: System state machine and health monitoring
    - _StorageCoordinator: RAM disk and shared memory management
    - _StealthMemoryManager: Entropy masking for stealth operations

    Key features:
    1. System state machine with automatic transitions
    2. Context swap (unload/load models between orchestrator states)
    3. Background health monitoring
    4. Thermal throttling at 85°C
    5. Automatic mitigation actions
    6. RAM Disk for high-speed temporary storage
    7. Shared memory for zero-copy data sharing
    8. Entropy masking for stealth operations

    Example:
        memory = MemoryLayer(config)
        await memory.initialize()

        # Create RAM disk for temporary storage
        ramdisk = memory.create_ramdisk(size_mb=512)

        # Create shared memory block
        block_id = memory.create_shared_block(b'data', 'artifact')

        # Register state change callback
        memory.on_state_change(lambda old, new: print(f"{old} → {new}"))
    """

    def __init__(self, config: MemoryConfig | None = None):
        """
        Initialize MemoryLayer.

        Args:
            config: Memory configuration (uses defaults if None)
        """
        self.config = config or MemoryConfig()

        # Internal coordinators (refactored from monolithic design)
        self._state_manager = _MemoryStateManager(self.config)
        self._storage = _StorageCoordinator(self.config)
        self._stealth = _StealthMemoryManager()

        # Loaded models tracking (for context swap)
        self._loaded_models: dict[str, Any] = {}
        self._model_states: dict[str, dict[str, Any]] = {}

        # Statistics
        self._context_swaps = 0
        self._gc_calls = 0
        self._cache_clears = 0

        # Forward state change callbacks
        self._state_manager.on_state_change(self._on_state_change)

        logger.info(f"MemoryLayer initialized (limit: {self.config.memory_limit_mb}MB)")

    def _on_state_change(self, old_state: SystemState, new_state: SystemState) -> None:
        """Handle state changes from internal state manager."""
        logger.debug(f"MemoryLayer state change: {old_state.value} → {new_state.value}")

    async def initialize(self) -> bool:
        """
        Initialize MemoryLayer and start health monitoring.

        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing MemoryLayer...")

            # Verify MLX availability (M1 optimization)
            mx = _get_mlx()
            try:
                if mx is not None and hasattr(mx, 'metal'):
                    mx.metal.reset_peak_memory()
                    logger.info("✅ MLX Metal available")
                else:
                    logger.warning("⚠️ MLX not available - running in CPU mode")
            except Exception as e:
                logger.warning(f"⚠️ MLX Metal not fully available: {e}")

            # Initialize internal coordinators
            await self._storage.initialize()
            await self._stealth.initialize()

            # Start health monitoring
            await self._state_manager.start_monitoring()

            logger.info("✅ MemoryLayer initialized successfully")
            return True

        except Exception as e:
            logger.error(f"❌ MemoryLayer initialization failed: {e}")
            return False

    # ====================================================================
    # RAM Disk Operations (delegated to _StorageCoordinator)
    # ====================================================================

    def create_ramdisk(self, size_mb: int | None = None) -> RAMDiskManager:
        """
        Create a RAM disk for high-speed temporary storage.

        Args:
            size_mb: Size in MB (default: 512MB or config)

        Returns:
            RAMDiskManager instance (context manager)

        Example:
            with memory.create_ramdisk(512) as ramdisk:
                paths = ramdisk.setup_integration_directories()
                # Use paths['tantivy_store'], paths['vision_sentry']
        """
        return self._storage.create_ramdisk(size_mb)

    def get_ramdisk(self) -> RAMDiskManager | None:
        """Get current RAM disk manager if active."""
        return self._storage.get_ramdisk()

    # ====================================================================
    # Shared Memory Operations (delegated to _StorageCoordinator)
    # ====================================================================

    def create_shared_block(self, data: bytes, data_type: str,
                           metadata: dict[str, Any] | None = None) -> str | None:
        """
        Create a shared memory block for zero-copy data sharing.

        Args:
            data: Raw data to share
            data_type: Type of data ('artifact', 'entities', 'analysis', 'ai_insight')
            metadata: Additional metadata

        Returns:
            Block ID if successful, None otherwise
        """
        return self._storage.create_shared_block(data, data_type, metadata)

    def get_shared_data(self, block_id: str) -> bytes | None:
        """Retrieve data from shared memory block."""
        return self._storage.get_shared_data(block_id)

    def release_shared_block(self, block_id: str) -> bool:
        """Release a shared memory block."""
        return self._storage.release_shared_block(block_id)

    # ====================================================================
    # Entropy Masking (Stealth) - delegated to _StealthMemoryManager
    # ====================================================================

    def inject_entropy_noise(self) -> str | None:
        """
        Inject entropy noise to reduce Shannon entropy (stealth operations).

        Returns:
            Block ID of injected noise
        """
        return self._stealth.inject_entropy_noise()

    def get_entropy_stats(self) -> dict[str, Any]:
        """Get entropy masking statistics."""
        return self._stealth.get_entropy_stats()

    async def transition_state(
        self,
        old_state: OrchestratorState,
        new_state: OrchestratorState
    ) -> None:
        """
        Transition between orchestrator states with context swap.

        This method:
        1. Unloads models from old state
        2. Clears MLX cache
        3. Runs garbage collection
        4. Loads models for new state

        Args:
            old_state: Previous orchestrator state
            new_state: New orchestrator state
        """
        logger.info(f"🔄 Context swap: {old_state.value} → {new_state.value}")

        try:
            # Step 1: Unload models from old state
            await self._unload_models_for_state(old_state)

            # Step 2: Force garbage collection
            await self._force_gc()

            # Step 3: Clear MLX cache
            await self._clear_mlx_cache()

            # Step 4: Load models for new state
            await self._load_models_for_state(new_state)

            self._context_swaps += 1
            logger.info(f"✅ Context swap complete (#{self._context_swaps})")

        except MemoryPressureError:
            logger.error("❌ Memory pressure during transition")
            # Trigger recovery
            await self._enter_recovery_mode()
            raise

        except Exception as e:
            logger.error(f"❌ Context swap failed: {e}")
            raise

    async def _unload_models_for_state(self, state: OrchestratorState) -> None:
        """Unload models associated with given state"""
        models_to_unload = self._get_models_for_state(state)

        for model_name in models_to_unload:
            if model_name in self._loaded_models:
                logger.info(f"📤 Unloading model: {model_name}")

                # Save state if needed
                self._model_states[model_name] = self._save_model_state(model_name)

                # Unload
                await self._unload_model(model_name)
                del self._loaded_models[model_name]

    async def _load_models_for_state(self, state: OrchestratorState) -> None:
        """Load models required for given state"""
        models_to_load = self._get_models_for_state(state)

        for model_name in models_to_load:
            if model_name not in self._loaded_models:
                logger.info(f"📥 Loading model: {model_name}")

                # Check memory before loading
                if not await self._check_memory_available():
                    raise MemoryPressureError(
                        f"Not enough memory to load {model_name}"
                    )

                # Load model
                model = await self._load_model(model_name)
                self._loaded_models[model_name] = model

    def _get_models_for_state(self, state: OrchestratorState) -> list[str]:
        """Get list of models required for given state"""
        # Map states to required models
        state_models = {
            OrchestratorState.IDLE: [],
            OrchestratorState.PLANNING: ["hermes-3"],
            OrchestratorState.BRAIN: ["hermes-3"],
            OrchestratorState.EXECUTION: ["qwen-cleaner"],  # Small model for cleanup
            OrchestratorState.SYNTHESIS: ["hermes-3"],
            OrchestratorState.ERROR: [],
        }
        return state_models.get(state, [])

    async def _load_model(self, model_name: str) -> Any:
        """Load a model by name"""
        # This would integrate with M1ModelManager
        # For now, placeholder
        logger.debug(f"Loading model: {model_name}")

        if model_name == "hermes-3":
            # Lazy import
            try:
                from mlx_lm import load as mlx_load
                model, tokenizer = mlx_load("mlx-community/Hermes-3-Llama-3.2-3B-bf16")
                return {"model": model, "tokenizer": tokenizer}
            except Exception as e:
                logger.error(f"Failed to load Hermes-3: {e}")
                return None

        # Note: qwen-cleaner model removed (deprecated)
        return None

    async def _unload_model(self, model_name: str) -> None:
        """Unload a model"""
        logger.debug(f"Unloading model: {model_name}")
        # Model will be garbage collected

    def _save_model_state(self, model_name: str) -> dict[str, Any]:
        """Save model state for later restoration"""
        # Placeholder for state saving
        return {"name": model_name, "timestamp": __import__('time').time()}

    async def _force_gc(self) -> None:
        """Force garbage collection"""
        gc.collect()
        self._gc_calls += 1
        logger.debug(f"🗑️ Garbage collection #{self._gc_calls}")

    async def _clear_mlx_cache(self) -> None:
        """Clear MLX cache"""
        mx = _get_mlx()
        try:
            if mx is not None:
                mx.eval([])  # M218A: Flush pending ops before clearing cache
                mx.clear_cache()
                self._cache_clears += 1
                logger.debug(f"🧹 MLX cache cleared #{self._cache_clears}")
            else:
                logger.debug("🧹 MLX not available - skipping cache clear")
        except Exception as e:
            logger.warning(f"⚠️ Failed to clear MLX cache: {e}")

    async def _apply_memory_mitigation(self) -> None:
        """Apply memory pressure mitigation"""
        logger.warning("🧠 Applying memory mitigation...")
        await self._force_gc()
        await self._clear_mlx_cache()
        # Could also unload non-essential models here

    async def _apply_thermal_mitigation(self) -> None:
        """Apply thermal throttling mitigation"""
        logger.warning("🌡️ Applying thermal mitigation...")
        # Could reduce ASIC frequency, throttle processing, etc.

    async def _enter_recovery_mode(self) -> None:
        """Enter recovery mode"""
        # Get metrics from state manager
        self._state_manager.get_metrics()
        await self._apply_recovery_mode()

    async def _apply_recovery_mode(self) -> None:
        """Apply recovery mode actions"""
        logger.warning("🚑 Entering recovery mode...")

        # Aggressive cleanup
        await self._force_gc()
        await self._clear_mlx_cache()

        # Unload all models
        for model_name in list(self._loaded_models.keys()):
            await self._unload_model(model_name)
            del self._loaded_models[model_name]

        # Wait a bit
        await asyncio.sleep(2)

    async def _check_memory_available(self) -> bool:
        """Check if enough memory is available for operation"""
        metrics = self._state_manager.get_metrics()
        available = metrics.memory_available_mb

        # Require at least 500MB free
        return available > 500

    def on_state_change(self, callback: Callable[[SystemState, SystemState], None]) -> None:
        """
        Register callback for system state changes.

        Args:
            callback: Function(old_state, new_state) called on state change
        """
        self._state_manager.on_state_change(callback)

    def get_current_state(self) -> SystemState:
        """Get current system state"""
        return self._state_manager.get_current_state()

    def get_metrics(self) -> SystemMetrics:
        """Get current system metrics"""
        return self._state_manager.get_metrics()

    def get_statistics(self) -> dict[str, Any]:
        """Get memory layer statistics"""
        stats = {
            "current_state": self._state_manager.get_current_state().value,
            "state_transitions": self._state_manager.get_statistics().get("state_transitions", {}),
            "context_swaps": self._context_swaps,
            "gc_calls": self._gc_calls,
            "cache_clears": self._cache_clears,
            "loaded_models": list(self._loaded_models.keys()),
            "metrics_history_count": self._state_manager.get_statistics().get("metrics_history_count", 0),
        }

        # Add storage stats
        stats.update(self._storage.get_statistics())

        # Add stealth stats
        stats['entropy_masking'] = self._stealth.get_statistics()

        return stats

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("🧹 Cleaning up MemoryLayer...")

        # Stop health check loop
        await self._state_manager.stop_monitoring()

        # Cleanup storage
        self._storage.shutdown()

        # Cleanup stealth
        self._stealth.clear_noise_blocks()

        # Unload all models
        for model_name in list(self._loaded_models.keys()):
            await self._unload_model(model_name)

        self._loaded_models.clear()

        # Final cleanup
        await self._force_gc()
        await self._clear_mlx_cache()

        logger.info("✅ MemoryLayer cleanup complete")


# =============================================================================
# KERNEL MEMORY MANAGEMENT (from kernel/memory.py)
# =============================================================================

import math
import mmap
import multiprocessing as mp
import multiprocessing.shared_memory as shm
import os
import secrets
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RAMDiskConfig:
    """Configuration for RAM disk creation"""
    size_mb: int = 512  # Default 512MB
    volume_name: str = "GhostVolume"
    filesystem: str = "HFS+"
    min_memory_mb: int = 1024  # Minimum memory required
    max_memory_usage_percent: float = 0.3  # Max 30% of available memory


@dataclass
class SharedMemoryBlock:
    """Metadata for a shared memory block."""
    block_id: str
    size: int
    created_at: float
    process_id: int
    data_type: str  # 'artifact', 'entities', 'analysis', 'ai_insight'
    metadata: dict[str, Any]


@dataclass
class ProcessMessage:
    """Inter-process communication message."""
    message_type: str  # 'data_ready', 'processing_complete', 'shutdown'
    block_id: str | None = None
    sender_process: str = ''
    receiver_process: str = ''
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class RAMDiskManager:
    """
    macOS M1 specific RAM disk manager for stealth operations.

    Provides forensic-clean, high-speed temporary storage that
    leaves no traces on disk when destroyed.

    Example:
        with RAMDiskManager(RAMDiskConfig(size_mb=512)) as ramdisk:
            paths = ramdisk.setup_integration_directories()
            # Use paths for TantivyStore, VisionSentry
        # Auto-nuked on exit
    """

    def __init__(self, config: RAMDiskConfig | None = None):
        self.config = config or RAMDiskConfig()
        self.device_path: str | None = None
        self.mount_path: Path | None = None
        self.is_attached = False
        self._sectors_per_mb = 2048  # 512-byte sectors

    def get_available_memory_mb(self) -> int:
        """Get available memory in MB"""
        import psutil
        memory = psutil.virtual_memory()
        return int(memory.available / 1024 / 1024)

    def calculate_optimal_size(self) -> int:
        """Calculate optimal RAM disk size based on available memory"""
        available_mb = self.get_available_memory_mb()

        # Ensure minimum memory requirement
        if available_mb < self.config.min_memory_mb:
            raise MemoryError(
                f"Insufficient memory: {available_mb}MB available, "
                f"{self.config.min_memory_mb}MB required"
            )

        # Calculate max size based on percentage limit
        max_size_mb = int(available_mb * self.config.max_memory_usage_percent)
        optimal_size = min(self.config.size_mb, max_size_mb)

        logger.info(f"Available memory: {available_mb}MB, RAM disk size: {optimal_size}MB")
        return optimal_size

    def create_ramdisk(self, size_mb: int | None = None) -> str:
        """
        Create a RAM disk using hdiutil.

        Args:
            size_mb: Size in MB, if None uses config size

        Returns:
            Mount path of the RAM disk
        """
        if self.is_attached:
            raise RuntimeError("RAM disk already attached")

        # Calculate size
        if size_mb is None:
            size_mb = self.calculate_optimal_size()

        sectors = size_mb * self._sectors_per_mb

        try:
            # Create RAM disk (not mounted yet)
            cmd_attach = ["hdiutil", "attach", "-nomount", f"ram://{sectors}"]
            result = subprocess.run(
                cmd_attach,
                capture_output=True,
                text=True,
                check=True
            )

            # Extract device path from output
            self.device_path = result.stdout.strip()

            if not self.device_path.startswith("/dev/"):
                raise RuntimeError(f"Invalid device path: {self.device_path}")

            # Format the volume
            cmd_format = [
                "diskutil",
                "erasevolume",
                self.config.filesystem,
                self.config.volume_name,
                self.device_path
            ]

            subprocess.run(cmd_format, capture_output=True, text=True, check=True)

            # Set mount path
            self.mount_path = Path(f"/Volumes/{self.config.volume_name}")
            self.is_attached = True

            logger.info(f"RAM disk created: {self.device_path} -> {self.mount_path}")
            logger.info(f"Size: {size_mb}MB, Speed: ~60GB/s")

            return str(self.mount_path)

        except subprocess.CalledProcessError as e:
            self.cleanup_on_error()
            raise RuntimeError(f"RAM disk creation failed: {e.stderr}") from e

    def get_integration_paths(self) -> dict[str, str]:
        """Get paths for component integration."""
        if not self.is_attached or not self.mount_path:
            raise RuntimeError("RAM disk not attached")

        return {
            "tantivy_store": str(self.mount_path / "tantivy_indexes"),
            "vision_sentry": str(self.mount_path / "vision_temp"),
            "temp_files": str(self.mount_path / "temp"),
            "cache": str(self.mount_path / "cache")
        }

    def setup_integration_directories(self) -> dict[str, str]:
        """Create directories for component integration."""
        paths = self.get_integration_paths()

        for name, path in paths.items():
            Path(path).mkdir(parents=True, exist_ok=True)
            logger.debug(f"Created directory: {name} -> {path}")

        return paths

    def get_performance_stats(self) -> dict[str, Any]:
        """Get RAM disk performance statistics"""
        if not self.is_attached:
            return {"status": "not_attached"}

        try:
            # Get disk usage
            if self.mount_path and self.mount_path.exists():
                usage = shutil.disk_usage(str(self.mount_path))
                stats = {
                    "status": "attached",
                    "device_path": self.device_path,
                    "mount_path": str(self.mount_path),
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "usage_percent": (usage.used / usage.total) * 100,
                    "theoretical_speed_gbps": 60,  # RAM speed
                    "filesystem": self.config.filesystem
                }
            else:
                stats = {"status": "attached_no_mount"}

            return stats

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"status": "error", "error": str(e)}

    def nuke(self) -> bool:
        """
        Immediately and irretrievably destroy the RAM disk.

        This method provides instant forensic cleanup by disconnecting
        the memory cells, causing immediate and complete data loss.

        Returns:
            True if successful, False otherwise
        """
        if not self.is_attached:
            logger.warning("RAM disk not attached, nothing to nuke")
            return True

        try:
            # Force detach - this immediately loses all data
            if self.device_path:
                cmd_detach = ["hdiutil", "detach", self.device_path, "-force"]
                subprocess.run(cmd_detach, capture_output=True, text=True, check=True)

                logger.critical(f"RAM disk nuked: {self.device_path}")
                logger.critical("All data irretrievably lost - forensic clean")

            # Reset state
            self.is_attached = False
            self.device_path = None
            self.mount_path = None

            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"RAM disk nuke failed: {e.stderr}")
            # Force reset state even on error
            self.is_attached = False
            self.device_path = None
            self.mount_path = None
            return False

    def cleanup_on_error(self):
        """Cleanup in case of errors during creation"""
        if self.device_path:
            try:
                subprocess.run(
                    ["hdiutil", "detach", self.device_path, "-force"],
                    capture_output=True,
                    check=False
                )
            except Exception:
                pass

        self.is_attached = False
        self.device_path = None
        self.mount_path = None

    def __enter__(self):
        """Context manager entry"""
        self.create_ramdisk()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - always nuke on exit"""
        self.nuke()
        if exc_type:
            logger.error(f"RAM disk error: {exc_val}")

    def shutdown(self) -> bool:
        """Explicit shutdown – calls nuke() if attached."""
        if self.is_attached:
            return self.nuke()
        return True


class SharedMemoryManager:
    """
    Advanced shared memory manager for zero-copy data sharing between processes.

    Manages shared memory blocks, inter-process communication, and resource cleanup.
    Optimized for M1 architecture with dedicated core assignment.
    """

    def __init__(self, max_memory_mb: int = 1024):
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.active_blocks: dict[str, SharedMemoryBlock] = {}
        self.shared_memory_objects: dict[str, shm.SharedMemory] = {}
        self.process_queues: dict[str, mp.Queue] = {}
        self.shutdown_event = mp.Event()

        # M1 Core assignment
        self.core_assignments = {
            'network': 0,      # Efficiency Core
            'analysis': 1,     # Performance Core 1
            'ai': 2,          # Performance Core 2 (with GPU)
            'orchestrator': 3  # Performance Core 3
        }

        # Statistics
        self.stats = {
            'total_blocks_created': 0,
            'total_bytes_shared': 0,
            'active_blocks': 0,
            'peak_memory_usage': 0,
            'cleanup_operations': 0
        }

        logger.info("SharedMemoryManager initialized for M1 architecture")

    def create_shared_block(self, data: bytes, data_type: str,
                           metadata: dict[str, Any] = None) -> str:
        """
        Create a shared memory block with zero-copy data sharing.

        Args:
            data: Raw data to share (bytes)
            data_type: Type of data ('artifact', 'entities', 'analysis', 'ai_insight')
            metadata: Additional metadata for the block

        Returns:
            Block ID for referencing the shared memory
        """
        try:
            # Check memory limits
            if len(data) > self.max_memory_bytes:
                raise ValueError(f"Data size {len(data)} exceeds maximum {self.max_memory_bytes}")

            # Generate unique block ID
            block_id = str(uuid.uuid4())

            # Create shared memory block
            shared_mem = shm.SharedMemory(create=True, size=len(data))
            shared_mem.buf[:len(data)] = data  # Zero-copy operation

            # Store block metadata
            block_info = SharedMemoryBlock(
                block_id=block_id,
                size=len(data),
                created_at=time.time(),
                process_id=mp.current_process().pid,
                data_type=data_type,
                metadata=metadata or {}
            )

            # Store references
            self.active_blocks[block_id] = block_info
            self.shared_memory_objects[block_id] = shared_mem

            # Update statistics
            self.stats['total_blocks_created'] += 1
            self.stats['total_bytes_shared'] += len(data)
            self.stats['active_blocks'] = len(self.active_blocks)

            current_usage = sum(block.size for block in self.active_blocks.values())
            if current_usage > self.stats['peak_memory_usage']:
                self.stats['peak_memory_usage'] = current_usage

            logger.info(f"Created shared block {block_id}: {len(data)} bytes ({data_type})")
            return block_id

        except Exception as e:
            logger.error(f"Failed to create shared block: {e}")
            raise

    def get_shared_data(self, block_id: str) -> bytes | None:
        """Retrieve data from shared memory block (zero-copy read)."""
        try:
            if block_id not in self.shared_memory_objects:
                logger.warning(f"Shared block {block_id} not found")
                return None

            shared_mem = self.shared_memory_objects[block_id]
            block_info = self.active_blocks[block_id]

            # Zero-copy read access - create new bytes object from memoryview
            data = bytes(shared_mem.buf[:block_info.size])

            logger.debug(f"Retrieved {len(data)} bytes from block {block_id}")
            return data

        except Exception as e:
            logger.error(f"Failed to retrieve shared data from {block_id}: {e}")
            return None

    def release_block(self, block_id: str) -> bool:
        """Release a shared memory block."""
        try:
            if block_id in self.shared_memory_objects:
                shared_mem = self.shared_memory_objects[block_id]
                block_info = self.active_blocks[block_id]

                # Close and unlink shared memory
                shared_mem.close()
                shared_mem.unlink()

                # Remove from tracking
                del self.shared_memory_objects[block_id]
                del self.active_blocks[block_id]

                # Update statistics
                self.stats['active_blocks'] = len(self.active_blocks)
                self.stats['cleanup_operations'] += 1

                logger.info(f"Released shared block {block_id}: {block_info.size} bytes")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to release block {block_id}: {e}")
            return False

    def cleanup_all_blocks(self) -> int:
        """Clean up all shared memory blocks."""
        cleaned_count = 0

        block_ids = list(self.active_blocks.keys())
        for block_id in block_ids:
            if self.release_block(block_id):
                cleaned_count += 1

        logger.info(f"Cleaned up {cleaned_count} shared memory blocks")
        return cleaned_count

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive statistics about shared memory usage."""
        current_usage = sum(block.size for block in self.active_blocks.values())

        return {
            **self.stats,
            'current_memory_usage_bytes': current_usage,
            'current_memory_usage_mb': current_usage / (1024 * 1024),
            'memory_utilization_percent': (current_usage / self.max_memory_bytes) * 100,
            'active_block_types': {
                data_type: len([b for b in self.active_blocks.values() if b.data_type == data_type])
                for data_type in {b.data_type for b in self.active_blocks.values()}
            }
        }

    def shutdown(self):
        """Shutdown shared memory manager and clean up all resources."""
        logger.info("Shutting down SharedMemoryManager...")

        # Signal shutdown
        self.shutdown_event.set()

        # Clean up all blocks
        self.cleanup_all_blocks()

        # Close queues
        for queue in self.process_queues.values():
            try:
                queue.close()
                queue.join_thread()
            except Exception:
                pass

        # Clear references
        self.active_blocks.clear()
        self.shared_memory_objects.clear()
        self.process_queues.clear()

        logger.info("SharedMemoryManager shutdown complete")


class EntropyMaskingManager:
    """
    Gray Matter Entropy Masking for stealth operations.

    Reduces Shannon entropy to make encrypted operations appear
    as normal application activity to EDR scanners.
    """

    def __init__(self, noise_size_mb: int = 50):
        self.noise_size_bytes = noise_size_mb * 1024 * 1024
        self.noise_blocks: dict[str, mmap.mmap] = {}
        self.noise_content = self._generate_noise_content()
        self.active_masking = False

        logger.info(f"EntropyMaskingManager initialized with {noise_size_mb}MB noise buffer")

    def _generate_noise_content(self) -> bytes:
        """Generate repetitive content that appears as normal application data."""
        mit_license = """MIT License

Copyright (c) 2025 Hledac Development Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

        shakespeare_text = """
To be, or not to be, that is the question:
Whether 'tis nobler in the mind to suffer
The slings and arrows of outrageous fortune,
Or to take arms against a sea of troubles
And by opposing end them. To die—to sleep,
No more; and by a sleep to say we end
The heart-ache and the thousand natural shocks
That flesh is heir to: 'tis a consummation
Devoutly to be wish'd. To die, to sleep;
To sleep, perchance to dream—ay, there's the rub,
For in that sleep of death what dreams may come,
When we have shuffled off this mortal coil,
Must give us pause: there's the respect
That makes calamity of so long life.
"""

        # Combine content
        combined_content = mit_license + "\n" + shakespeare_text

        # Sprint 82N: Fixed - calculate repetitions mathematically instead of slow while loop
        # This is O(1) instead of O(n) iterations
        content_bytes = combined_content.encode()
        content_len = len(content_bytes)

        if content_len == 0:
            return b''

        # Calculate exact number of repetitions needed
        repetitions = (self.noise_size_bytes // content_len) + 1

        # Use multiplication for O(1) construction instead of O(n) loop
        repeated_content = (combined_content + "\n") * repetitions

        # Return exactly the requested byte size
        return repeated_content.encode()[:self.noise_size_bytes]

    def inject_entropy_noise(self, block_id: str = None) -> str:
        """
        Inject entropy noise into memory to reduce overall Shannon entropy.

        Args:
            block_id: Optional block ID for tracking

        Returns:
            ID of the injected noise block
        """
        try:
            # Generate unique block ID if not provided
            if block_id is None:
                block_id = f"entropy_noise_{secrets.token_hex(8)}"

            # Create temporary file for noise
            temp_path = f"/tmp/hledac_entropy_{block_id}.bin"

            with open(temp_path, 'wb') as f:
                f.write(self.noise_content)

            # Memory map the file for zero-copy operations
            with open(temp_path, 'r+b') as f:
                noise_mmap = mmap.mmap(f.fileno(), 0)
                self.noise_blocks[block_id] = noise_mmap

            logger.info(f"Injected entropy noise block {block_id}: {self.noise_size_bytes} bytes")
            self.active_masking = True

            return block_id

        except Exception as e:
            logger.error(f"Failed to inject entropy noise: {e}")
            raise

    def calculate_shannon_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data."""
        if not data:
            return 0.0

        # Count byte frequencies
        byte_counts = [0] * 256
        for byte in data:
            byte_counts[byte] += 1

        # Calculate entropy
        entropy = 0.0
        data_len = len(data)

        for count in byte_counts:
            if count > 0:
                probability = count / data_len
                entropy -= probability * math.log2(probability) if probability > 0 else 0

        return entropy

    def get_entropy_reduction_stats(self) -> dict[str, Any]:
        """Get statistics about entropy reduction."""
        if not self.noise_blocks:
            return {
                'active_masking': False,
                'noise_blocks_count': 0,
                'total_noise_bytes': 0
            }

        # Calculate entropy of noise content
        noise_entropy = self.calculate_shannon_entropy(self.noise_content)

        # Calculate theoretical entropy reduction
        total_noise_bytes = len(self.noise_blocks) * self.noise_size_bytes
        entropy_reduction = noise_entropy * (total_noise_bytes / (1024 * 1024))  # MB

        return {
            'active_masking': self.active_masking,
            'noise_blocks_count': len(self.noise_blocks),
            'total_noise_bytes': total_noise_bytes,
            'noise_entropy': noise_entropy,
            'theoretical_entropy_reduction_mb': entropy_reduction,
            'stealth_effectiveness': 'HIGH' if noise_entropy < 4.0 else 'MEDIUM'
        }

    def clear_noise_blocks(self):
        """Clear all entropy noise blocks"""
        for _block_id, noise_mmap in self.noise_blocks.items():
            try:
                noise_mmap.close()
            except Exception:
                pass

        # Clean up temporary files
        try:
            import glob
            temp_files = glob.glob("/tmp/hledac_entropy_*.bin")
        except Exception:
            temp_files = []
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except Exception:
                pass

        self.noise_blocks.clear()
        self.active_masking = False

        logger.info("All entropy noise blocks cleared")

    def __del__(self):
        """Cleanup on deletion"""
        self.clear_noise_blocks()


# Export additional classes
__all__ = [
    'MemoryLayer',
    'RAMDiskManager',
    'RAMDiskConfig',
    'SharedMemoryManager',
    'EntropyMaskingManager',
    'SharedMemoryBlock',
]

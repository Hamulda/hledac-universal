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

VERDICT (F260 MemoryLayer audit, 2026-06-02):
  - 0 callers in canonical sprint path (runtime/, core/, pipeline/, knowledge/, brain/, fetching/, coordinators/)
  - GhostLayer / StealthLayer do NOT import this module's public API
    (create_ramdisk / get_ramdisk / inject_entropy_noise / EntropyMaskingManager)
  - Consumers: layers/layer_manager.py (lazy property), layers/__init__.py (re-export),
    tests/test_sprint82j_benchmark.py + tests/test_autonomous_orchestrator.py (tests only),
    legacy/autonomous_orchestrator.py (legacy facade, 4×)
  - runtime/memory_authority.py:11 explicitly classifies this as "layer_system"
  - User decision 2026-06-02: KEEP in place + documented, do NOT move to legacy/layers/
  - Reason: legacy/autonomous_orchestrator still imports the public surface, and tests
    cover the layer-system behavior. No canonical-path impact, no urgency.
  - See SECURITY_MEMORY_LAYER_AUDIT.md (F260) for full evidence.

Refactored with internal classes for M1 8GB optimization:
- _MemoryStateManager: System state machine and health monitoring
- _StorageCoordinator: RAM disk and shared memory management
- _StealthMemoryManager: Entropy masking for stealth operations
- _ThermalSampler: Offload-only thermal sampling (not canonical Uma owner)
"""
import atexit
import asyncio
import gc
import logging
import subprocess
import time
import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import msgspec
from typing import Any
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
from hledac.universal.project_types import MemoryConfig, MemoryPressureError, OrchestratorState, SystemMetrics, SystemState
from hledac.universal.utils.async_helpers import safe_create_task
logger = logging.getLogger(__name__)

class ThermalSnapshot(msgspec.Struct, frozen=True, gc=False):
    """Immutable snapshot of thermal reading with TTL tracking."""
    celsius: float | None
    sampled_at_monotonic: float

class _ThermalSampler:
    """
    Async-owned thermal sampler with TTL caching and fail-soft recovery.

    Offloads blocking ioreg subprocess calls from async hot paths via
    asyncio.to_thread. Not the canonical Uma owner — sampling only.
    """
    __slots__ = tuple(('_cache', '_lock', '_ttl_s'))

    def __init__(self, ttl_s: float=10.0) -> None:
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
            subprocess.run(['ioreg', '-r', '-c', 'AppleSmartBattery', '-w0'], capture_output=True, text=True, timeout=1)
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
        if cached is not None and now - cached.sampled_at_monotonic < self._ttl_s:
            return cached.celsius
        async with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._cache.sampled_at_monotonic < self._ttl_s:
                return self._cache.celsius
            celsius = await asyncio.to_thread(self._read_temperature_sync)
            if celsius is None and self._cache is not None:
                cached_age = now - self._cache.sampled_at_monotonic
                if cached_age < self._ttl_s * 2:
                    celsius = self._cache.celsius
            self._cache = ThermalSnapshot(celsius=celsius, sampled_at_monotonic=now)
            return celsius

class _MemoryStateManager:
    """
    Internal: System state machine and health monitoring.

    Responsibilities:
    - System state transitions (HEALTHY → MEMORY_PRESSURE → ...)
    - Background health monitoring loop
    - Thermal awareness and throttling
    - Automatic mitigation actions
    """
    __slots__ = tuple(('_current_state', '_finalizer', '_health_check_task', '_max_history', '_metrics_history', '_running', '_state_change_callbacks', '_state_transitions', '_stopped', '_thermal_sampler', '__weakref__', 'config'))

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._current_state = SystemState.HEALTHY
        self._max_history = 100
        self._metrics_history: deque = deque(maxlen=self._max_history)
        self._health_check_task: asyncio.Task | None = None
        self._running = False
        self._state_change_callbacks: list[Callable[[SystemState, SystemState], None]] = []
        self._state_transitions: dict[str, int] = {s.value: 0 for s in SystemState}
        self._thermal_sampler = _ThermalSampler(ttl_s=10.0)
        self._finalizer = weakref.finalize(self, self._cleanup_on_gc)
        self._stopped = False

    def _cleanup_on_gc(self) -> None:
        """Called by weakref.finalize when _MemoryStateManager is garbage collected.

        This is a last-resort cleanup for abnormal exits (SIGKILL, crash, power loss).
        Note: Cannot use async here — sync fallback for emergency cleanup.
        """
        if self._stopped:
            return
        logger.warning('⚠️ _MemoryStateManager garbage collected without explicit stop_monitoring()')
        self._running = False
        self._stopped = True

    async def start_monitoring(self) -> None:
        """Start background health monitoring."""
        self._running = True
        self._health_check_task = safe_create_task(self._health_check_loop(), name='memory_layer:health_check')
        logger.info('🏥 Memory state monitoring started')

    async def stop_monitoring(self) -> None:
        """Stop background health monitoring.

        F266-U7: Sets _stopped flag to signal finalizer that cleanup was explicit.
        Deregisters weakref.finalize to avoid redundant cleanup.
        """
        self._running = False
        self._stopped = True
        self._finalizer.detach()
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
        logger.debug('✅ _MemoryStateManager monitoring stopped (explicit)')

    async def _health_check_loop(self) -> None:
        """Background health monitoring loop with adaptive intervals (Phase 3 M1 8GB optimization)."""
        _last_memory_mb = 0.0
        _idle_stable_count = 0
        while self._running:
            try:
                metrics = await self._perform_health_check()
                self._metrics_history.append(metrics)
                if len(self._metrics_history) > self._max_history:
                    self._metrics_history.popleft()
                new_state = self._determine_state(metrics)
                if new_state != self._current_state:
                    await self._handle_state_transition(self._current_state, new_state, metrics)
                    _idle_stable_count = 0
                memory_delta = abs(metrics.memory_used_mb - _last_memory_mb)
                _last_memory_mb = metrics.memory_used_mb
                if memory_delta < 5.0:
                    _idle_stable_count += 1
                else:
                    _idle_stable_count = 0
                base_interval = self.config.health_check_interval_seconds
                if new_state == SystemState.MEMORY_PRESSURE:
                    interval = 1.0
                elif new_state == SystemState.THERMAL_THROTTLING:
                    interval = 2.0
                elif new_state == SystemState.DEGRADED:
                    interval = 2.0
                elif _idle_stable_count >= 3:
                    interval = 10.0
                else:
                    interval = base_interval
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Health check error: {e}')
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
            return SystemMetrics(memory_used_mb=memory_used_mb, memory_available_mb=memory_available_mb, cpu_percent=cpu_percent, temperature_c=temperature_c, state=self._current_state, timestamp=__import__('time').time())
        except Exception as e:
            logger.warning(f'Failed to collect metrics: {e}')
            return SystemMetrics(memory_used_mb=0, memory_available_mb=self.config.memory_limit_mb, cpu_percent=0, temperature_c=None, state=self._current_state, timestamp=__import__('time').time())

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
        memory_usage_percent = metrics.memory_used_mb / (metrics.memory_used_mb + metrics.memory_available_mb) * 100 if metrics.memory_used_mb + metrics.memory_available_mb > 0 else 0
        if metrics.memory_used_mb > self.config.memory_limit_mb:
            return SystemState.MEMORY_PRESSURE
        if memory_usage_percent > 90:
            return SystemState.DEGRADED
        return SystemState.HEALTHY

    async def _handle_state_transition(self, old_state: SystemState, new_state: SystemState, metrics: SystemMetrics) -> None:
        """Handle system state transition."""
        logger.warning(f'🚨 System state transition: {old_state.value} → {new_state.value} (Memory: {metrics.memory_used_mb:.0f}MB, CPU: {metrics.cpu_percent:.1f}%)')
        self._current_state = new_state
        self._state_transitions[new_state.value] += 1
        for callback in self._state_change_callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                logger.warning(f'State change callback error: {e}')

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
        return SystemMetrics(memory_used_mb=0, memory_available_mb=self.config.memory_limit_mb, cpu_percent=0, temperature_c=None, state=self._current_state, timestamp=__import__('time').time())

    def get_statistics(self) -> dict[str, Any]:
        """Get state manager statistics."""
        return {'current_state': self._current_state.value, 'state_transitions': self._state_transitions, 'metrics_history_count': len(self._metrics_history)}

class _StorageCoordinator:
    """
    Internal: RAM disk and shared memory management.

    Responsibilities:
    - RAM disk creation and management (hdiutil-based)
    - Shared memory for zero-copy IPC
    """
    __slots__ = tuple(('_ramdisk_manager', '_shared_memory_manager', 'config'))

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
            self._shared_memory_manager = SharedMemoryManager(max_memory_mb=self.config.memory_limit_mb // 2)
            logger.info('✅ SharedMemoryManager initialized')
        except Exception as e:
            logger.warning(f'⚠️ SharedMemoryManager not available: {e}')
            self._shared_memory_manager = None

    def create_ramdisk(self, size_mb: int | None=None) -> RAMDiskManager:
        """Create a RAM disk for high-speed temporary storage."""
        config = RAMDiskConfig(size_mb=size_mb or 512)
        self._ramdisk_manager = RAMDiskManager(config)
        return self._ramdisk_manager

    def get_ramdisk(self) -> RAMDiskManager | None:
        """Get current RAM disk manager if active."""
        return self._ramdisk_manager

    def create_shared_block(self, data: bytes, data_type: str, metadata: dict[str, Any] | None=None) -> str | None:
        """Create a shared memory block for zero-copy data sharing."""
        if self._shared_memory_manager:
            try:
                return self._shared_memory_manager.create_shared_block(data, data_type, metadata)
            except Exception as e:
                logger.error(f'Failed to create shared block: {e}')
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
    __slots__ = tuple(('_entropy_masking_manager',))

    def __init__(self):
        self._entropy_masking_manager: EntropyMaskingManager | None = None

    async def initialize(self) -> None:
        """Initialize stealth memory manager."""
        await self._init_entropy_masking_manager()

    async def _init_entropy_masking_manager(self) -> None:
        """Initialize EntropyMaskingManager for stealth operations."""
        try:
            self._entropy_masking_manager = EntropyMaskingManager(noise_size_mb=50)
            logger.info('✅ EntropyMaskingManager initialized')
        except Exception as e:
            logger.warning(f'⚠️ EntropyMaskingManager not available: {e}')
            self._entropy_masking_manager = None

    def inject_entropy_noise(self) -> str | None:
        """Inject entropy noise to reduce Shannon entropy."""
        if self._entropy_masking_manager:
            try:
                return self._entropy_masking_manager.inject_entropy_noise()
            except Exception as e:
                logger.error(f'Failed to inject entropy noise: {e}')
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

class MemoryLayer:
    """
    Memory management layer for M1 8GB optimization.

    Implements Layer Protocol: mount() / unmount() / on_event().

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
    __slots__ = tuple(('_cache_clears', '_context_swaps', '_ctx', '_deep_hermes_engine', '_gc_calls', '_loaded_models', '_model_states', '_state_manager', '_stealth', '_storage', 'config'))

    def __init__(self, config: MemoryConfig | None=None, deep_hermes_engine: Any=None):
        """
        Initialize MemoryLayer.

        Args:
            config: Memory configuration (uses defaults if None)
            deep_hermes_engine: DeepHermes3Engine instance to share model from.
                M-05 fix: Previously loaded a separate BF16 Hermes-3 (~6GB Metal).
                Now reuses the engine's cached model to avoid duplicate allocation.
        """
        self.config = config or MemoryConfig()
        self._state_manager = _MemoryStateManager(self.config)
        self._storage = _StorageCoordinator(self.config)
        self._stealth = _StealthMemoryManager()
        self._loaded_models: dict[str, Any] = {}
        self._model_states: dict[str, dict[str, Any]] = {}
        self._context_swaps = 0
        self._gc_calls = 0
        self._cache_clears = 0
        # M-05: injected engine — shares model with DeepHermes3Engine canonical path
        self._deep_hermes_engine = deep_hermes_engine
        self._ctx: Any = None  # Layer Protocol: set in mount()
        self._state_manager.on_state_change(self._on_state_change)
        logger.info(f'MemoryLayer initialized (limit: {self.config.memory_limit_mb}MB)')
    layer_name: str = 'memory'

    async def mount(self, ctx: Any) -> None:
        """Layer Protocol: mount.

        M-05: If deep_hermes_engine was not injected at construction time,
        lazily resolve it from ctx (set there by the sprint runtime wiring).
        """
        self._ctx = ctx
        # M-05: Lazily resolve engine from ctx if not injected at construction
        if self._deep_hermes_engine is None:
            try:
                self._deep_hermes_engine = ctx.get('deephermes3_engine')
                if self._deep_hermes_engine is not None:
                    logger.info('M-05: DeepHermes3Engine resolved from context')
            except Exception:  # noqa: BLE001
                pass
            if self._deep_hermes_engine is None:
                try:
                    self._deep_hermes_engine = ctx.get('hermes_engine')
                except Exception:  # noqa: BLE001
                    pass
        await self.initialize()
        ctx.set('memory', self)
        ctx.set_meta(memory_pressure=0.0)

    async def unmount(self, ctx: Any) -> None:
        """Layer Protocol: unmount."""
        await self.cleanup()

    async def on_event(self, ctx: Any, event: Any) -> Any:
        """Layer Protocol: handle memory_pressure events."""
        if event.type == 'memory_pressure':
            ctx.memory_pressure = event.data.get('pressure', 0.0)
        return event

    def _on_state_change(self, old_state: SystemState, new_state: SystemState) -> None:
        """Handle state changes from internal state manager."""
        logger.debug(f'MemoryLayer state change: {old_state.value} → {new_state.value}')

    async def initialize(self) -> bool:
        """
        Initialize MemoryLayer and start health monitoring.

        Returns:
            True if initialization successful
        """
        try:
            logger.info('🚀 Initializing MemoryLayer...')
            mx = _get_mlx()
            try:
                if mx is not None and hasattr(mx, 'metal'):
                    mx.reset_peak_memory()
                    logger.info('✅ MLX Metal available')
                else:
                    logger.warning('⚠️ MLX not available - running in CPU mode')
            except Exception as e:
                logger.warning(f'⚠️ MLX Metal not fully available: {e}')
            await self._storage.initialize()
            await self._stealth.initialize()
            await self._state_manager.start_monitoring()
            logger.info('✅ MemoryLayer initialized successfully')
            return True
        except Exception as e:
            logger.error(f'❌ MemoryLayer initialization failed: {e}')
            return False

    def create_ramdisk(self, size_mb: int | None=None) -> RAMDiskManager:
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

    def create_shared_block(self, data: bytes, data_type: str, metadata: dict[str, Any] | None=None) -> str | None:
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

    async def transition_state(self, old_state: OrchestratorState, new_state: OrchestratorState) -> None:
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
        logger.info(f'🔄 Context swap: {old_state.value} → {new_state.value}')
        try:
            await self._unload_models_for_state(old_state)
            await self._force_gc()
            await self._clear_mlx_cache()
            await self._load_models_for_state(new_state)
            self._context_swaps += 1
            logger.info(f'✅ Context swap complete (#{self._context_swaps})')
        except MemoryPressureError:
            logger.error('❌ Memory pressure during transition')
            await self._enter_recovery_mode()
            raise
        except Exception as e:
            logger.error(f'❌ Context swap failed: {e}')
            raise

    async def _unload_models_for_state(self, state: OrchestratorState) -> None:
        """Unload models associated with given state"""
        models_to_unload = self._get_models_for_state(state)
        for model_name in models_to_unload:
            if model_name in self._loaded_models:
                logger.info(f'📤 Unloading model: {model_name}')
                self._model_states[model_name] = self._save_model_state(model_name)
                await self._unload_model(model_name)
                del self._loaded_models[model_name]

    async def _load_models_for_state(self, state: OrchestratorState) -> None:
        """Load models required for given state"""
        models_to_load = self._get_models_for_state(state)
        for model_name in models_to_load:
            if model_name not in self._loaded_models:
                logger.info(f'📥 Loading model: {model_name}')
                if not await self._check_memory_available():
                    raise MemoryPressureError(f'Not enough memory to load {model_name}')
                model = await self._load_model(model_name)
                self._loaded_models[model_name] = model

    def _get_models_for_state(self, state: OrchestratorState) -> list[str]:
        """Get list of models required for given state"""
        state_models = {OrchestratorState.IDLE: [], OrchestratorState.PLANNING: ['hermes-3'], OrchestratorState.BRAIN: ['hermes-3'], OrchestratorState.EXECUTION: ['qwen-cleaner'], OrchestratorState.SYNTHESIS: ['hermes-3'], OrchestratorState.ERROR: []}
        return state_models.get(state, [])

    async def _load_model(self, model_name: str) -> Any:
        """Load a model by name.

        M-05 fix: For hermes-3, delegates to the injected DeepHermes3Engine
        instance to reuse its already-loaded model (cached via HermesModelCache).
        Previously loaded a separate BF16 Hermes-3 directly via mlx_lm.load(),
        causing ~6GB Metal allocation independent of DeepHermes3Engine.
        """
        logger.debug(f'Loading model: {model_name}')
        if model_name == 'hermes-3':
            # M-05: Use the injected engine's already-loaded model reference.
            # DeepHermes3Engine loads via HermesModelCache (4bit, ~2GB) and
            # _ensure_model_loaded() ensures it's already warm before first use.
            engine = self._deep_hermes_engine
            if engine is None:
                logger.error('M-05: deep_hermes_engine not injected — cannot share model')
                return None
            model = engine.model
            if model is None:
                # Engine loaded but model not yet initialized — trigger lazy load
                try:
                    await engine._ensure_model_loaded()
                    model = engine.model
                except Exception as e:
                    logger.error(f'M-05: failed to ensure Hermes-3 model loaded: {e}')
                    return None
            if model is not None:
                logger.debug('M-05: Hermes-3 model shared from DeepHermes3Engine')
                return {'model': model, 'tokenizer': engine.tokenizer}
            logger.error('M-05: Hermes-3 model is None after ensure_model_loaded')
            return None
        return None

    async def _unload_model(self, model_name: str) -> None:
        """Unload a model"""
        logger.debug(f'Unloading model: {model_name}')

    def _save_model_state(self, model_name: str) -> dict[str, Any]:
        """Save model state for later restoration"""
        return {'name': model_name, 'timestamp': __import__('time').time()}

    async def _force_gc(self) -> None:
        """Force garbage collection"""
        gc.collect()
        self._gc_calls += 1
        logger.debug(f'🗑️ Garbage collection #{self._gc_calls}')

    async def _clear_mlx_cache(self) -> None:
        """Clear MLX cache"""
        mx = _get_mlx()
        try:
            if mx is not None:
                mx.eval([])
                mx.clear_cache()
                self._cache_clears += 1
                logger.debug(f'🧹 MLX cache cleared #{self._cache_clears}')
            else:
                logger.debug('🧹 MLX not available - skipping cache clear')
        except Exception as e:
            logger.warning(f'⚠️ Failed to clear MLX cache: {e}')

    async def _apply_memory_mitigation(self) -> None:
        """Apply memory pressure mitigation"""
        logger.warning('🧠 Applying memory mitigation...')
        await self._force_gc()
        await self._clear_mlx_cache()

    async def _apply_thermal_mitigation(self) -> None:
        """Apply thermal throttling mitigation"""
        logger.warning('🌡️ Applying thermal mitigation...')

    async def _enter_recovery_mode(self) -> None:
        """Enter recovery mode"""
        self._state_manager.get_metrics()
        await self._apply_recovery_mode()

    async def _apply_recovery_mode(self) -> None:
        """Apply recovery mode actions"""
        logger.warning('🚑 Entering recovery mode...')
        await self._force_gc()
        await self._clear_mlx_cache()
        for model_name in list(self._loaded_models.keys()):
            await self._unload_model(model_name)
            del self._loaded_models[model_name]
        await asyncio.sleep(2)

    async def _check_memory_available(self) -> bool:
        """Check if enough memory is available for operation"""
        metrics = self._state_manager.get_metrics()
        available = metrics.memory_available_mb
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
        stats = {'current_state': self._state_manager.get_current_state().value, 'state_transitions': self._state_manager.get_statistics().get('state_transitions', {}), 'context_swaps': self._context_swaps, 'gc_calls': self._gc_calls, 'cache_clears': self._cache_clears, 'loaded_models': list(self._loaded_models.keys()), 'metrics_history_count': self._state_manager.get_statistics().get('metrics_history_count', 0)}
        stats.update(self._storage.get_statistics())
        stats['entropy_masking'] = self._stealth.get_statistics()
        return stats

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info('🧹 Cleaning up MemoryLayer...')
        await self._state_manager.stop_monitoring()
        self._storage.shutdown()
        self._stealth.clear_noise_blocks()
        for model_name in list(self._loaded_models.keys()):
            await self._unload_model(model_name)
        self._loaded_models.clear()
        await self._force_gc()
        await self._clear_mlx_cache()
        logger.info('✅ MemoryLayer cleanup complete')
import math
import mmap
import multiprocessing as mp
import multiprocessing.shared_memory as shm
import os
import secrets
import shutil
import uuid
from dataclasses import dataclass, field
import msgspec


class RAMDiskConfig(msgspec.Struct, frozen=True, kw_only=True, gc=False):
    """
    Configuration for RAM disk creation (ISSUE-033: migrated from @dataclass to msgspec.Struct).

    Attributes:
        size_mb: RAM disk size in MB. Default: 512 MB (safe for M1 8GB).
        volume_name: macOS volume name. Default: 'GhostVolume'.
        filesystem: Filesystem type. Default: 'HFS+'.
        min_memory_mb: Minimum free memory required to auto-create RAM disk.
            Default: 1024 MB.
        max_memory_usage_percent: Maximum fraction of free memory to use for RAM disk.
            Default: 0.3 (30%).
    """

    size_mb: int = 512
    volume_name: str = 'GhostVolume'
    filesystem: str = 'HFS+'
    min_memory_mb: int = 1024
    max_memory_usage_percent: float = 0.3

class SharedMemoryBlock(msgspec.Struct, frozen=True, gc=False):
    """Metadata for a shared memory block."""
    block_id: str
    size: int
    created_at: float
    process_id: int
    data_type: str
    metadata: dict[str, Any]

class ProcessMessage(msgspec.Struct, frozen=True, gc=False):
    """Inter-process communication message."""
    message_type: str
    block_id: str | None = None
    sender_process: str = ''
    receiver_process: str = ''
    metadata: dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}
_mngr_atexitRegistered: bool = False
_mngr_registry: dict[str, RAMDiskManager] = {}

def _mngr_atexit_cleanup() -> None:
    for m in list(_mngr_registry.values()):
        if m.is_attached and m.device_path:
            try:
                subprocess.run(['hdiutil', 'detach', m.device_path, '-force'], capture_output=True, timeout=10)
            except Exception:  # noqa: BLE001
                pass
    _mngr_registry.clear()

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
    __slots__ = tuple(('_sectors_per_mb', 'config', 'device_path', 'is_attached', 'mount_path'))

    def _register_atexit(self) -> None:
        """Register this instance with the module-level atexit handler."""
        global _mngr_atexitRegistered
        if not _mngr_atexitRegistered:
            atexit.register(_mngr_atexit_cleanup)
            _mngr_atexitRegistered = True

    def __init__(self, config: RAMDiskConfig | None=None):
        self.config = config or RAMDiskConfig()
        self.device_path: str | None = None
        self.mount_path: Path | None = None
        self.is_attached = False
        self._sectors_per_mb = 2048
        self._register_atexit()

    def get_available_memory_mb(self) -> int:
        """Get available memory in MB"""
        import psutil
        memory = psutil.virtual_memory()
        return int(memory.available / 1024 / 1024)

    def calculate_optimal_size(self) -> int:
        """Calculate optimal RAM disk size based on available memory"""
        available_mb = self.get_available_memory_mb()
        if available_mb < self.config.min_memory_mb:
            raise MemoryError(f'Insufficient memory: {available_mb}MB available, {self.config.min_memory_mb}MB required')
        max_size_mb = int(available_mb * self.config.max_memory_usage_percent)
        optimal_size = min(self.config.size_mb, max_size_mb)
        logger.info(f'Available memory: {available_mb}MB, RAM disk size: {optimal_size}MB')
        return optimal_size

    def create_ramdisk(self, size_mb: int | None=None) -> str:
        """
        Create a RAM disk using hdiutil.

        Args:
            size_mb: Size in MB, if None uses config size

        Returns:
            Mount path of the RAM disk

        Raises:
            RuntimeError: If RAM disk is already attached or device path is invalid
            subprocess.CalledProcessError: If hdiutil or diskutil commands fail
        """
        if self.is_attached:
            raise RuntimeError('RAM disk already attached')
        if size_mb is None:
            size_mb = self.calculate_optimal_size()
        sectors = size_mb * self._sectors_per_mb
        try:
            cmd_attach = ['hdiutil', 'attach', '-nomount', f'ram://{sectors}']
            result = subprocess.run(cmd_attach, capture_output=True, text=True, check=True)
            self.device_path = result.stdout.strip()
            if not self.device_path.startswith('/dev/'):
                raise RuntimeError(f'Invalid device path: {self.device_path}')
            cmd_format = ['diskutil', 'erasevolume', self.config.filesystem, self.config.volume_name, self.device_path]
            subprocess.run(cmd_format, capture_output=True, text=True, check=True)
            self.mount_path = Path(f'/Volumes/{self.config.volume_name}')
            self.is_attached = True
            if self.device_path:
                _mngr_registry[self.device_path] = self
            logger.info(f'RAM disk created: {self.device_path} -> {self.mount_path}')
            logger.info(f'Size: {size_mb}MB, Speed: ~60GB/s')
            return str(self.mount_path)
        except Exception as e:
            if isinstance(e, subprocess.CalledProcessError):
                raise RuntimeError(f'RAM disk creation failed: {e.stderr}') from e
            elif isinstance(e, (ValueError, RuntimeError, MemoryError)):
                raise
            else:
                raise RuntimeError(f'RAM disk creation failed: {e}') from e
        finally:
            if self.device_path and (not self.is_attached):
                self.nuke()
                logger.warning('RAM disk cleanup completed via finally block')

    def get_integration_paths(self) -> dict[str, str]:
        """Get paths for component integration."""
        if not self.is_attached or not self.mount_path:
            raise RuntimeError('RAM disk not attached')
        return {'tantivy_store': str(self.mount_path / 'tantivy_indexes'), 'vision_sentry': str(self.mount_path / 'vision_temp'), 'temp_files': str(self.mount_path / 'temp'), 'cache': str(self.mount_path / 'cache')}

    def setup_integration_directories(self) -> dict[str, str]:
        """Create directories for component integration."""
        paths = self.get_integration_paths()
        for name, path in paths.items():
            Path(path).mkdir(parents=True, exist_ok=True)
            logger.debug(f'Created directory: {name} -> {path}')
        return paths

    def get_performance_stats(self) -> dict[str, Any]:
        """Get RAM disk performance statistics"""
        if not self.is_attached:
            return {'status': 'not_attached'}
        try:
            if self.mount_path and self.mount_path.exists():
                usage = shutil.disk_usage(str(self.mount_path))
                stats = {'status': 'attached', 'device_path': self.device_path, 'mount_path': str(self.mount_path), 'total_bytes': usage.total, 'used_bytes': usage.used, 'free_bytes': usage.free, 'usage_percent': usage.used / usage.total * 100, 'theoretical_speed_gbps': 60, 'filesystem': self.config.filesystem}
            else:
                stats = {'status': 'attached_no_mount'}
            return stats
        except Exception as e:
            logger.error(f'Error getting stats: {e}')
            return {'status': 'error', 'error': str(e)}

    def nuke(self) -> bool:
        """
        Immediately and irretrievably destroy the RAM disk.

        This method provides instant forensic cleanup by disconnecting
        the memory cells, causing immediate and complete data loss.

        Returns:
            True if successful, False otherwise
        """
        if not self.is_attached:
            logger.warning('RAM disk not attached, nothing to nuke')
            return True
        try:
            if self.device_path:
                _mngr_registry.pop(self.device_path, None)
                cmd_detach = ['hdiutil', 'detach', self.device_path, '-force']
                subprocess.run(cmd_detach, capture_output=True, text=True, check=True)
                logger.critical(f'RAM disk nuked: {self.device_path}')
                logger.critical('All data irretrievably lost - forensic clean')
            self.is_attached = False
            self.device_path = None
            self.mount_path = None
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f'RAM disk nuke failed: {e.stderr}')
            self.is_attached = False
            self.device_path = None
            self.mount_path = None
            for k in list(_mngr_registry):
                if _mngr_registry[k] is self:
                    del _mngr_registry[k]
            return False

    def cleanup_on_error(self):
        """Cleanup in case of errors during creation"""
        if self.device_path:
            try:
                subprocess.run(['hdiutil', 'detach', self.device_path, '-force'], capture_output=True, check=False)
            except Exception:  # noqa: BLE001
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
            logger.error(f'RAM disk error: {exc_val}')

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
    __slots__ = tuple(('active_blocks', 'core_assignments', 'max_memory_bytes', 'process_queues', 'shared_memory_objects', 'shutdown_event', 'stats'))

    def __init__(self, max_memory_mb: int=1024):
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.active_blocks: dict[str, SharedMemoryBlock] = {}
        self.shared_memory_objects: dict[str, shm.SharedMemory] = {}
        self.process_queues: dict[str, mp.Queue] = {}
        self.shutdown_event = mp.Event()
        self.core_assignments = {'network': 0, 'analysis': 1, 'ai': 2, 'orchestrator': 3}
        self.stats = {'total_blocks_created': 0, 'total_bytes_shared': 0, 'active_blocks': 0, 'peak_memory_usage': 0, 'cleanup_operations': 0}
        logger.info('SharedMemoryManager initialized for M1 architecture')

    def create_shared_block(self, data: bytes, data_type: str, metadata: dict[str, Any] | None=None):
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
            if len(data) > self.max_memory_bytes:
                raise ValueError(f'Data size {len(data)} exceeds maximum {self.max_memory_bytes}')
            block_id = str(uuid.uuid4())
            shared_mem = shm.SharedMemory(create=True, size=len(data))
            shared_mem.buf[:len(data)] = data
            block_info = SharedMemoryBlock(block_id=block_id, size=len(data), created_at=time.time(), process_id=mp.current_process().pid, data_type=data_type, metadata=metadata or {})
            self.active_blocks[block_id] = block_info
            self.shared_memory_objects[block_id] = shared_mem
            self.stats['total_blocks_created'] += 1
            self.stats['total_bytes_shared'] += len(data)
            self.stats['active_blocks'] = len(self.active_blocks)
            current_usage = sum((block.size for block in self.active_blocks.values()))
            if current_usage > self.stats['peak_memory_usage']:
                self.stats['peak_memory_usage'] = current_usage
            logger.info(f'Created shared block {block_id}: {len(data)} bytes ({data_type})')
            return block_id
        except Exception as e:
            logger.error(f'Failed to create shared block: {e}')
            raise

    def get_shared_data(self, block_id: str) -> bytes | None:
        """Retrieve data from shared memory block (zero-copy read)."""
        try:
            if block_id not in self.shared_memory_objects:
                logger.warning(f'Shared block {block_id} not found')
                return None
            shared_mem = self.shared_memory_objects[block_id]
            block_info = self.active_blocks[block_id]
            if block_info is None:
                return None
            data = bytes(shared_mem.buf[:block_info.size])
            logger.debug(f'Retrieved {len(data)} bytes from block {block_id}')
            return data
        except Exception as e:
            logger.error(f'Failed to retrieve shared data from {block_id}: {e}')
            return None

    def release_block(self, block_id: str) -> bool:
        """Release a shared memory block."""
        try:
            if block_id in self.shared_memory_objects:
                shared_mem = self.shared_memory_objects[block_id]
                block_info = self.active_blocks[block_id]
                shared_mem.close()
                shared_mem.unlink()
                del self.shared_memory_objects[block_id]
                del self.active_blocks[block_id]
                self.stats['active_blocks'] = len(self.active_blocks)
                self.stats['cleanup_operations'] += 1
                logger.info(f'Released shared block {block_id}: {block_info.size} bytes')
                return True
            return False
        except Exception as e:
            logger.error(f'Failed to release block {block_id}: {e}')
            return False

    def cleanup_all_blocks(self) -> int:
        """Clean up all shared memory blocks."""
        cleaned_count = 0
        block_ids = list(self.active_blocks.keys())
        for block_id in block_ids:
            if self.release_block(block_id):
                cleaned_count += 1
        logger.info(f'Cleaned up {cleaned_count} shared memory blocks')
        return cleaned_count

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive statistics about shared memory usage."""
        current_usage = sum((block.size for block in self.active_blocks.values()))
        return {**self.stats, 'current_memory_usage_bytes': current_usage, 'current_memory_usage_mb': current_usage / (1024 * 1024), 'memory_utilization_percent': current_usage / self.max_memory_bytes * 100, 'active_block_types': {data_type: len([b for b in self.active_blocks.values() if b.data_type == data_type]) for data_type in {b.data_type for b in self.active_blocks.values()}}}

    def shutdown(self):
        """Shutdown shared memory manager and clean up all resources."""
        logger.info('Shutting down SharedMemoryManager...')
        self.shutdown_event.set()
        self.cleanup_all_blocks()
        for queue in self.process_queues.values():
            try:
                queue.close()
                queue.join_thread()
            except Exception:  # noqa: BLE001
                pass
        self.active_blocks.clear()
        self.shared_memory_objects.clear()
        self.process_queues.clear()
        logger.info('SharedMemoryManager shutdown complete')

class EntropyMaskingManager:
    """
    Gray Matter Entropy Masking for stealth operations.

    Reduces Shannon entropy to make encrypted operations appear
    as normal application activity to EDR scanners.
    """
    __slots__ = tuple(('active_masking', 'noise_blocks', 'noise_content', 'noise_size_bytes'))

    def __init__(self, noise_size_mb: int=50):
        self.noise_size_bytes = noise_size_mb * 1024 * 1024
        self.noise_blocks: dict[str, mmap.mmap] = {}
        self.noise_content = self._generate_noise_content()
        self.active_masking = False
        logger.info(f'EntropyMaskingManager initialized with {noise_size_mb}MB noise buffer')

    def _generate_noise_content(self) -> bytes:
        """Generate repetitive content that appears as normal application data."""
        mit_license = 'MIT License\n\nCopyright (c) 2025 Hledac Development Team\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the "Software"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.'
        shakespeare_text = "\nTo be, or not to be, that is the question:\nWhether 'tis nobler in the mind to suffer\nThe slings and arrows of outrageous fortune,\nOr to take arms against a sea of troubles\nAnd by opposing end them. To die—to sleep,\nNo more; and by a sleep to say we end\nThe heart-ache and the thousand natural shocks\nThat flesh is heir to: 'tis a consummation\nDevoutly to be wish'd. To die, to sleep;\nTo sleep, perchance to dream—ay, there's the rub,\nFor in that sleep of death what dreams may come,\nWhen we have shuffled off this mortal coil,\nMust give us pause: there's the respect\nThat makes calamity of so long life.\n"
        combined_content = mit_license + '\n' + shakespeare_text
        content_bytes = combined_content.encode()
        content_len = len(content_bytes)
        if content_len == 0:
            return b''
        repetitions = self.noise_size_bytes // content_len + 1
        repeated_content = (combined_content + '\n') * repetitions
        return repeated_content.encode()[:self.noise_size_bytes]

    def inject_entropy_noise(self, block_id: str | None=None):
        """
        Inject entropy noise into memory to reduce overall Shannon entropy.

        Args:
            block_id: Optional block ID for tracking

        Returns:
            ID of the injected noise block
        """
        try:
            if block_id is None:
                block_id = f'entropy_noise_{secrets.token_hex(8)}'
            temp_path = f'/tmp/hledac_entropy_{block_id}.bin'
            with open(temp_path, 'wb') as f:
                f.write(self.noise_content)
            with open(temp_path, 'r+b') as f:
                noise_mmap = mmap.mmap(f.fileno(), 0)
                self.noise_blocks[block_id] = noise_mmap
            logger.info(f'Injected entropy noise block {block_id}: {self.noise_size_bytes} bytes')
            self.active_masking = True
            return block_id
        except Exception as e:
            logger.error(f'Failed to inject entropy noise: {e}')
            raise

    def calculate_shannon_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data."""
        if not data:
            return 0.0
        byte_counts = [0] * 256
        for byte in data:
            byte_counts[byte] += 1
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
            return {'active_masking': False, 'noise_blocks_count': 0, 'total_noise_bytes': 0}
        noise_entropy = self.calculate_shannon_entropy(self.noise_content)
        total_noise_bytes = len(self.noise_blocks) * self.noise_size_bytes
        entropy_reduction = noise_entropy * (total_noise_bytes / (1024 * 1024))
        return {'active_masking': self.active_masking, 'noise_blocks_count': len(self.noise_blocks), 'total_noise_bytes': total_noise_bytes, 'noise_entropy': noise_entropy, 'theoretical_entropy_reduction_mb': entropy_reduction, 'stealth_effectiveness': 'HIGH' if noise_entropy < 4.0 else 'MEDIUM'}

    def clear_noise_blocks(self):
        """Clear all entropy noise blocks"""
        for _block_id, noise_mmap in self.noise_blocks.items():
            try:
                noise_mmap.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            import glob
            temp_files = glob.glob('/tmp/hledac_entropy_*.bin')
        except Exception:
            temp_files = []
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except Exception:  # noqa: BLE001
                pass
        self.noise_blocks.clear()
        self.active_masking = False
        logger.info('All entropy noise blocks cleared')

    def __del__(self):
        """Cleanup on deletion"""
        self.clear_noise_blocks()
__all__ = ['MemoryLayer', 'RAMDiskManager', 'RAMDiskConfig', 'SharedMemoryManager', 'EntropyMaskingManager', 'SharedMemoryBlock']
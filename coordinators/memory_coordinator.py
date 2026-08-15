"""Universal Memory Coordinator — priority-based zones, MLX cache management, thread-safe operations.

See :ref:`memory-coordinator` for class index, neuromorphic STDP layer details,
context optimization moved imports, and memory pressure polling.










"""
from __future__ import annotations

import asyncio
import ctypes
import gc
import hashlib
import itertools
import logging
import sys
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

from hledac.universal.core.psutil_shim import psutil
from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for
from hledac.universal.utils.lru_cache import LRUCache

try:
    import numpy as np
    from numpy.typing import NDArray
    HAS_NUMPY = True
except ImportError:
    np = None
    NDArray = 'NDArray'
    HAS_NUMPY = False
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.msgspec_json import decode_zstd as _decode_zstd
from hledac.universal.utils.msgspec_json import encode_zstd as _encode_zstd

try:
    import usearch
    USEARCH_AVAILABLE = True
except ImportError:
    usearch = None
    USEARCH_AVAILABLE = False
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryManager, NeuromorphicMemoryZone
import contextlib

from hledac.universal.core.resource_governor import PressureState
from core import aclose


def _serialize_to_json(data: Any) -> bytes:
    """Serialize data to JSON bytes using msgspec, compressed with zstd.

    Single path: msgspec.json facade (msgspec → orjson → json fallback)
    with zstd compression (PEP 706, Python 3.14+ stdlib).
    """
    return _encode_zstd(data)

def _deserialize_from_json(data: bytes) -> Any:
    """Deserialize from zstd-compressed JSON bytes via msgspec facade."""
    return _decode_zstd(data)
logger = logging.getLogger(__name__)

# ISSUE-5: Atomic counter for cleanup_count — itertools.count + threading.Lock
# avoids asyncio.Lock overhead for a simple increment-only counter
_cleanup_counter = itertools.count(1)
_cleanup_lock = threading.Lock()


def _next_cleanup_id() -> int:
    """Thread-safe increment for cleanup_count (atomic counter pattern)."""
    with _cleanup_lock:
        return next(_cleanup_counter)

def _get_np() -> Any | None:
    """Return numpy module. Defined at module level for type compatibility."""
    if not HAS_NUMPY:
        return None
    return np
MAX_SIMILARITIES = 1000
MAX_PATTERNS = 2000

# Backward compat alias - MemoryPressureLevel now points to canonical PressureState
MemoryPressureLevel = PressureState

class ThermalState(IntEnum):
    """Thermal state levels for M1 optimization (Sprint 72/73)."""
    NORMAL = 0
    WARM = 1
    HOT = 2
    CRITICAL = 3

class MemoryZone(Enum):
    """
    Memory zones for allocation priority.

    Priority tiers (eviction order from most to least evictable):
    - LOW: Easily evictable
    - MEDIUM: Standard allocations
    - HIGH: Important, avoid eviction
    - CRITICAL: Cannot release

    Note: BRAIN/TOOLS/SYNTHESIS/SYSTEM were removed in F214 — dual zone
    system collapsed to single priority-based system.
    """
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'

class MemoryAllocation(Struct):
    """Represents a memory allocation."""
    allocation_id: str
    zone: MemoryZone
    size_bytes: int
    priority: int
    created_at: float
    last_accessed: float
    evictable: bool = True
    on_evict: Callable | None = None

class MemoryStatistics(Struct):
    """Memory usage statistics."""
    total_memory_mb: float
    used_memory_mb: float
    available_memory_mb: float
    peak_usage_mb: float
    current_level: MemoryPressureLevel
    cleanup_count: int
    last_cleanup_time: float
    allocation_count: int = 0

class ZoneStatistics(Struct, frozen=True):
    """Statistics for a specific memory zone (immutable, msgspec zero-copy)."""
    zone: str
    allocation_count: int
    total_bytes: int
    total_mb: float
    evictable_count: int
    non_evictable_count: int

class UniversalMemoryCoordinator:
    """
    Universal memory coordinator for M1 8GB optimization.

    Integrates features from:
    - M1 Master Optimizer: Aggressive GC, MLX cache, allocation tracking
    - Universal Infrastructure: Zone-based cleanup, async operations
    - Neuromorphic Memory: Brain-inspired memory with STDP learning

    Thread-safe memory management with:
    - Zone-based allocation and eviction
    - Memory pressure monitoring
    - Aggressive cleanup with MLX cache clearing
    - Callback system for pressure events
    - Neuromorphic memory zones and pattern storage
    """
    __slots__ = ('_alloc_lock', '_alloc_lock_once', '_cached_cpu_percent', '_cached_on_battery', '_last_battery_check', '_last_cpu_sample_time', '_last_memory_stats', '_neuro_enabled', '_neuro_memory', '_pressure_lock', '_pressure_lock_once', '_running', '_stats_lock', '_stats_lock_once', '_thermal_history', '_thermal_state', 'allocations', 'callbacks', 'lock', 'memory_limit_bytes', 'memory_limit_mb', 'statistics', 'zone_allocations')

    def __init__(self, memory_limit_mb: float=5500, enable_neuromorphic: bool=False) -> None:
        """
        Initialize memory coordinator.

        Args:
            memory_limit_mb: Memory limit in MB (default 5.5GB for M1 8GB)
            enable_neuromorphic: Whether to enable neuromorphic memory (ISSUE-5: default False for faster init)
        """
        self.memory_limit_mb = memory_limit_mb
        self.memory_limit_bytes = memory_limit_mb * 1024 * 1024
        self.allocations: dict[str, MemoryAllocation] = {}
        self.zone_allocations: dict[MemoryZone, LRUCache] = {zone: LRUCache() for zone in MemoryZone}
        self.statistics = MemoryStatistics(total_memory_mb=psutil.virtual_memory().total / (1024 * 1024), used_memory_mb=0, available_memory_mb=0, peak_usage_mb=0, current_level=MemoryPressureLevel.NORMAL, cleanup_count=0, last_cleanup_time=0)
        self.callbacks: list[Callable] = []
        # ISSUE-5 OPTIMIZATION: Reduced from 6 to 3 asyncio.Lock instances.
        # _alloc_lock  — allocation/free/touch (serializes heap writes) [KEEP]
        # _stats_lock  — REMOVED (atomic itertools.counter handles cleanup_count; reads are lock-free)
        # _thermal_lock — REMOVED (thermal state reads are thread-safe via NSProcessInfo)
        # _pressure_lock — memory pressure handling [KEEP]
        # _neuro_lock   — REMOVED (neuromorphic subsystem is separate)
        self._alloc_lock: asyncio.Lock | None = None
        self._alloc_lock_once: asyncio.Lock | None = None
        self._stats_lock: asyncio.Lock | None = None
        self._stats_lock_once: asyncio.Lock | None = None
        self._pressure_lock: asyncio.Lock | None = None
        self._pressure_lock_once: asyncio.Lock | None = None
        self._neuro_memory: NeuromorphicMemoryManager | None = None
        self._neuro_enabled = enable_neuromorphic
        if enable_neuromorphic:
            self._initialize_neuromorphic_memory()
        logger.info(f'UniversalMemoryCoordinator initialized with {memory_limit_mb}MB limit')
        self._thermal_state = ThermalState.NORMAL
        self._thermal_history = deque(maxlen=10)
        self._running = True
        self._last_battery_check = 0.0
        self._cached_on_battery = False
        # ISSUE-16: CPU sampling cache (non-blocking psutil.cpu_percent interval=None)
        self._last_cpu_sample_time: float | None = None
        self._cached_cpu_percent: float | None = None
        # ISSUE-P2-7b: Cache last MemoryStatistics to avoid stale reads in get_pressure()
        self._last_memory_stats: MemoryStatistics | None = None
        # Backward compatibility — route old self.lock users to _get_alloc_lock()
        self.lock = self._alloc_lock  # type: ignore[assignment]

    def _get_alloc_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock initialization for _alloc_lock (ISSUE-5)."""
        if self._alloc_lock is None:
            if self._alloc_lock_once is None:
                self._alloc_lock_once = asyncio.Lock()
            self._alloc_lock = self._alloc_lock_once
        return self._alloc_lock

    def _get_stats_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock initialization for _stats_lock (ISSUE-5)."""
        if self._stats_lock is None:
            if self._stats_lock_once is None:
                self._stats_lock_once = asyncio.Lock()
            self._stats_lock = self._stats_lock_once
        return self._stats_lock

    def _get_pressure_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock initialization for _pressure_lock (ISSUE-5)."""
        if self._pressure_lock is None:
            if self._pressure_lock_once is None:
                self._pressure_lock_once = asyncio.Lock()
            self._pressure_lock = self._pressure_lock_once
        return self._pressure_lock

    def _get_thermal_state_native(self) -> ThermalState | None:
        """
        Získat tepelný stav přes NSProcessInfo (PyObjC).
        Fallback na None.
        """
        try:
            from Foundation import NSProcessInfo
            thermal_state = NSProcessInfo.processInfo().thermalState
            if thermal_state == 0:
                return ThermalState.NORMAL
            elif thermal_state == 1:
                return ThermalState.WARM
            elif thermal_state == 2:
                return ThermalState.HOT
            elif thermal_state == 3:
                return ThermalState.CRITICAL
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _estimate_thermal_load(self) -> ThermalState:
        """
        Fallback – odhad podle zátěže CPU a memory pressure (non-blocking).

        ISSUE-16 fix: psutil.cpu_percent(interval=0.1) blokuje event loop na 100ms.
        Řešení:
        - psutil.cpu_percent(interval=None) — non-blocking, vrací průměr od posledního volání
        - Časová cache s min 1s intervalem mezi vzorky
        - Linux fallback: /sys/class/thermal/thermal_zone*/temp

        Poznámka: asyncio.to_thread není potřeba — interval=None je non-blocking.
        """
        import psutil

        try:
            now = time.time()
            # None placeholder pattern — konzistentní s _get_lock() v celém kódu
            if self._last_cpu_sample_time is None or self._cached_cpu_percent is None:
                psutil.cpu_percent(interval=None)  # baseline (ignorováno)
                self._last_cpu_sample_time = now
                self._cached_cpu_percent = 0.0
            else:
                elapsed = now - self._last_cpu_sample_time
                if elapsed >= 1.0:  # Min 1s mezi vzorky
                    self._cached_cpu_percent = psutil.cpu_percent(interval=None)
                    self._last_cpu_sample_time = now

            cpu_percent = self._cached_cpu_percent
            mem_pressure = self._calculate_pressure_level()

            # Linux thermal fallback — /sys/class/thermal/thermal_zone*/temp
            linux_thermal: int | None = None
            if sys.platform == "linux":
                try:
                    import glob as _glob
                    zones = _glob.glob("/sys/class/thermal/thermal_zone*/temp")
                    if zones:
                        with open(zones[0]) as _f:
                            linux_thermal = int(_f.read().strip()) // 1000  # m°C → °C
                except Exception:  # noqa: BLE001
                    pass

            # macOS: NSProcessInfo.thermalState je primární (volá se v _update_thermal_state)
            # Zde používáme cpu_percent jako fallback signal
            if linux_thermal is not None:
                # Linux thermal zone — přímé měření
                if linux_thermal >= 85:
                    return ThermalState.CRITICAL
                elif linux_thermal >= 70:
                    return ThermalState.HOT
                elif linux_thermal >= 55:
                    return ThermalState.WARM

            # CPU + memory pressure fallback (macOS / general)
            if cpu_percent > 90 and mem_pressure in (MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL):
                return ThermalState.CRITICAL
            elif cpu_percent > 70 and mem_pressure in (MemoryPressureLevel.ELEVATED, MemoryPressureLevel.HIGH):
                return ThermalState.HOT
            elif cpu_percent > 50 and mem_pressure == MemoryPressureLevel.ELEVATED:
                return ThermalState.WARM
            return ThermalState.NORMAL
        except Exception:
            return ThermalState.NORMAL

    async def _update_thermal_state(self) -> ThermalState:
        """Aktualizuje cached thermal state (non-blocking).

        ISSUE-16 fix: _estimate_thermal_load je nyní async s non-blocking psutil.cpu_percent.
        _thermal_monitor_loop ji volá napřímo (bez asyncio.to_thread).
        """
        native = self._get_thermal_state_native()
        if native is not None:
            return native
        return await self._estimate_thermal_load()

    def get_thermal_state(self) -> ThermalState:
        return self._thermal_state

    def should_throttle(self) -> bool:
        return self._thermal_state in (ThermalState.HOT, ThermalState.CRITICAL)

    def get_thermal_trend(self) -> str:
        """Returns thermal trend (rising, stable, falling) from history."""
        if len(self._thermal_history) < 3:
            return 'stable'
        last = self._thermal_history[-1][1].value
        prev = self._thermal_history[-2][1].value
        if last > prev:
            return 'rising'
        elif last < prev:
            return 'falling'
        return 'stable'

    def get_pressure_level(self, used_memory_mb: float | None = None) -> str:
        """Returns memory pressure level.

        Args:
            used_memory_mb: Optional pre-fetched value to avoid stale reads.
                If None, reads self.statistics.used_memory_mb (may be stale
                if called concurrently with get_memory_usage).
        """
        current = self._calculate_pressure_level(used_memory_mb)
        if current == MemoryPressureLevel.CRITICAL:
            return 'critical'
        elif current == MemoryPressureLevel.HIGH:
            return 'high'
        elif current == MemoryPressureLevel.ELEVATED:
            return 'elevated'
        return 'normal'

    async def get_pressure(self) -> PressureState:
        """Get canonical pressure state (UMAGovernor protocol)."""
        # Re-use latest value from get_memory_usage if available, avoids stale read
        if hasattr(self, '_last_memory_stats') and self._last_memory_stats is not None:
            current = self._calculate_pressure_level(self._last_memory_stats.used_memory_mb)
        else:
            current = self._calculate_pressure_level()
        return current

    def get_power_state(self) -> dict:
        """Synchronous power state — calls _on_battery_power() which uses subprocess.run.

        USE ONLY from non-async contexts (e.g. __init__, sync callbacks).
        From async contexts use get_power_state_async() instead.
        """
        return {'on_battery': self._on_battery_power(), 'thermal_state': self._thermal_state.name.lower(), 'thermal_trend': self.get_thermal_trend(), 'memory_pressure_level': self.get_pressure_level(), 'should_throttle': self.should_throttle()}

    async def get_power_state_async(self) -> dict:
        """Async power state — calls _on_battery_power_async() which uses asyncio.create_subprocess_exec.

        USE from async contexts (event loop). Avoids blocking the event loop.
        """
        return {'on_battery': await self._on_battery_power_async(), 'thermal_state': self._thermal_state.name.lower(), 'thermal_trend': self.get_thermal_trend(), 'memory_pressure_level': self.get_pressure_level(), 'should_throttle': self.should_throttle()}

    def get_reranking_context(self) -> dict:
        """Reranking context pro lancedb_store adaptive reranking.

        Narrow seam — lancedb_store NEvolá _on_battery_power() přímo.
        Tato metoda je jediný entry point pro thermal/battery awareness
        v search_similar_adaptive().
        """
        state = self.get_power_state()
        try:
            import psutil
            state['available_gb'] = psutil.virtual_memory().available / 1024 ** 3
        except Exception:
            state['available_gb'] = 8.0
        return state

    def _on_battery_power(self) -> bool:
        """Detekuje běh na baterii – cache s TTL 30s (sync path).

        Fallback pro macOS bez psutil.sensors_battery().
        Async varianta je _on_battery_power_async() pro event-loop context.
        """
        now = time.time()
        if now - self._last_battery_check > 30:
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    self._cached_on_battery = not battery.power_plugged
                else:
                    import subprocess
                    result = subprocess.run(['pmset', '-g', 'batt'], capture_output=True, text=True, timeout=2)
                    self._cached_on_battery = 'discharging' in result.stdout.lower()
            except Exception:
                self._cached_on_battery = True
            self._last_battery_check = now
        return self._cached_on_battery

    async def _on_battery_power_async(self) -> bool:
        """Async varianta – běží v thread poolu, neblokuje event loop.

        Poznámky:
        - psutil.sensors_battery() je sync I/O (~µs), běží v asyncio.to_thread()
          aby neblokoval event loop v async kontextu
        - pmset subprocess běží čistě async přes create_subprocess_exec
        - returncode check zajišťuje že cache se neaktualizuje při selhání pmset
        - asyncio.CancelledError je re-raised (nedědí z Exception v Python 3.8+,
          ale pro kompatibilitu s older Python verze je explicitně ošetřena)
        """
        now = time.time()
        if now - self._last_battery_check > 30:
            try:
                # psutil.sensors_battery() — sync I/O, thread pool aby neblokoval
                battery = await asyncio.to_thread(psutil.sensors_battery)
                if battery is not None:
                    self._cached_on_battery = not battery.power_plugged
                else:
                    proc = await asyncio.create_subprocess_exec(
                        'pmset', '-g', 'batt',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    # ISSUE-3 fix: check returncode — nepoužívat stdout když pmset selhal
                    async with asyncio.timeout(1.0):
                        stdout, _ = await proc.communicate()
                    if proc.returncode == 0:
                        self._cached_on_battery = b'discharging' in stdout.lower()
                    # else: cache se neaktualizuje, ponechá předchozí hodnotu
            except asyncio.CancelledError:
                raise  # Re-raise — CancelledError nesmí být polykána
            except TimeoutError:  # noqa: BLE001
                # Timeout — ponechat starou cached hodnotu
                pass
            except Exception:
                self._cached_on_battery = True
            self._last_battery_check = now
        return self._cached_on_battery

    async def _thermal_monitor_loop(self) -> None:
        """Background task – aktualizuje stav každých 30s (adaptivně).

        ISSUE-16 fix: _update_thermal_state je async, voláme napřímo.
        _estimate_thermal_load uvnitř běží non-blocking přes asyncio.to_thread.
        """
        while self._running:
            try:
                new_state = await self._update_thermal_state()
                if new_state != self._thermal_state:
                    logger.info(f'[Thermal] State changed: {self._thermal_state.value} -> {new_state.value}')
                    self._thermal_state = new_state
                    self._thermal_history.append((time.time(), new_state))
                interval = 10 if self._thermal_state in (ThermalState.HOT, ThermalState.CRITICAL) else 30
            except Exception as e:
                logger.debug(f'Thermal monitor error: {e}')
                interval = 60
            await asyncio.sleep(interval)

    def stop_thermal_monitor(self) -> None:
        """Zastavit thermal monitor loop (voláno při cleanup)."""
        self._running = False

    def _initialize_neuromorphic_memory(self, n_neurons: int=512) -> None:
        """
        Initialize neuromorphic memory manager (runtime lazy import).

        NeuromorphicMemoryManager lives in ``knowledge.neuromorphic`` and is
        gated behind ``HLEDAC_ENABLE_NEURO=1``.

        Args:
            n_neurons: Number of neurons (default 512 for M1 optimization)
        """
        try:
            from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryManager
            self._neuro_memory = NeuromorphicMemoryManager(n_neurons=n_neurons, connectivity=0.03)
            logger.info('Neuromorphic memory initialized: %s neurons', n_neurons)
        except Exception as e:
            logger.warning('Failed to initialize neuromorphic memory: %s', e)
            self._neuro_memory = None
            self._neuro_enabled = False

    def allocate_neuromorphic_zone(self, zone_type: NeuromorphicMemoryZone, size: int) -> dict[str, Any]:
        """
        Allocate a neuromorphic memory zone.

        Args:
            zone_type: Type of memory zone to allocate
            size: Number of patterns the zone should hold

        Returns:
            Allocation result with zone info
        """
        from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryZone
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}
        if zone_type == NeuromorphicMemoryZone.WORKING_MEMORY:
            self._neuro_memory.working_memory = deque(self._neuro_memory.working_memory, maxlen=size)
        elif zone_type == NeuromorphicMemoryZone.LONG_TERM_MEMORY:
            self._neuro_memory.long_term_memory = deque(self._neuro_memory.long_term_memory, maxlen=size)
        elif zone_type == NeuromorphicMemoryZone.EPISODIC_BUFFER:
            self._neuro_memory.episodic_buffer = deque(self._neuro_memory.episodic_buffer, maxlen=size)
        return {'success': True, 'zone': zone_type.value, 'size': size, 'neurons': self._neuro_memory.n_neurons}

    def store_neural_pattern(self, zone: NeuromorphicMemoryZone, pattern_id: str, data: Any) -> dict[str, Any]:
        """
        Store a pattern in neuromorphic memory.

        Args:
            zone: Memory zone to store in
            pattern_id: Unique pattern identifier
            data: Data to encode and store

        Returns:
            Storage result with metadata
        """
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}
        try:
            success = self._neuro_memory.store_pattern(pattern_id, data, zone)
            return {'success': success, 'pattern_id': pattern_id, 'zone': zone.value, 'timestamp': time.time()}
        except Exception as e:
            logger.error(f'Failed to store neural pattern: {e}')
            return {'success': False, 'error': str(e)}

    def recall_neural_pattern(self, zone: NeuromorphicMemoryZone, pattern_id: str, completion: bool=True) -> dict[str, Any]:
        """
        Recall a pattern from neuromorphic memory.

        Args:
            zone: Memory zone to recall from (used for lookup priority)
            pattern_id: Pattern identifier
            completion: Whether to perform pattern completion

        Returns:
            Recalled pattern data or error
        """
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}
        try:
            result = self._neuro_memory.recall_pattern(pattern_id, completion)
            if result:
                return {'success': True, 'pattern': result, 'zone': zone.value}
            else:
                return {'success': False, 'error': f'Pattern {pattern_id} not found', 'zone': zone.value}
        except Exception as e:
            logger.error(f'Failed to recall neural pattern: {e}')
            return {'success': False, 'error': str(e)}

    def consolidate_neural_memories(self, strength_threshold: float=0.5) -> dict[str, Any]:
        """
        Consolidate strong working memories to long-term memory.

        Args:
            strength_threshold: Minimum strength for consolidation

        Returns:
            Consolidation results
        """
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}
        try:
            count = self._neuro_memory.consolidate_memories(strength_threshold)
            self._neuro_memory._memory_replay(n_replays=min(count, 20))
            return {'success': True, 'consolidated_count': count, 'working_memory_size': len(self._neuro_memory.working_memory), 'long_term_memory_size': len(self._neuro_memory.long_term_memory)}
        except Exception as e:
            logger.error(f'Failed to consolidate neural memories: {e}')
            return {'success': False, 'error': str(e)}

    def get_neuromorphic_stats(self) -> dict[str, Any]:
        """Get neuromorphic memory statistics."""
        if not self._neuro_memory:
            return {'enabled': False}
        return {'enabled': True, **self._neuro_memory.get_stats()}

    def cleanup_neuromorphic_memory(self) -> dict[str, Any]:
        """Perform aggressive cleanup of neuromorphic memory."""
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}
        forgotten = self._neuro_memory.forget_weak_memories(threshold=0.2)
        self._neuro_memory.cleanup()
        return {'success': True, 'forgotten_patterns': forgotten, 'remaining_patterns': len(self._neuro_memory._patterns)}

    async def allocate(self, allocation_id: str, zone: MemoryZone, size_bytes: int, priority: int=5, evictable: bool=True, on_evict: Callable | None=None) -> bool:
        """
        Allocate memory in a specific zone.

        Args:
            allocation_id: Unique identifier for allocation
            zone: Memory zone for allocation
            size_bytes: Size in bytes
            priority: Priority (1-10, lower is more important)
            evictable: Whether allocation can be evicted
            on_evict: Callback when allocation is evicted

        Returns:
            True if allocation successful
        """
        async with self._get_alloc_lock():
            if allocation_id in self.allocations:
                logger.warning(f'Allocation {allocation_id} already exists')
                return False
            available = self._get_available_memory()
            if size_bytes > available:
                logger.warning(f'Not enough memory for {allocation_id}: {size_bytes} > {available}')
                if not await self._handle_memory_pressure(size_bytes - available):
                    return False
            allocation = MemoryAllocation(allocation_id=allocation_id, zone=zone, size_bytes=size_bytes, priority=priority, created_at=time.time(), last_accessed=time.time(), evictable=evictable, on_evict=on_evict)
            self.allocations[allocation_id] = allocation
            self.zone_allocations[zone][allocation_id] = allocation
            logger.debug(f'Allocated {allocation_id} in zone {zone.value}: {size_bytes} bytes')
            return True

    async def free(self, allocation_id: str) -> bool:
        """
        Free memory allocation.

        Args:
            allocation_id: Allocation ID to free

        Returns:
            True if allocation was freed
        """
        async with self._get_alloc_lock():
            if allocation_id not in self.allocations:
                return False
            allocation = self.allocations[allocation_id]
            if allocation_id in self.zone_allocations[allocation.zone]:
                del self.zone_allocations[allocation.zone][allocation_id]
            del self.allocations[allocation_id]
            logger.debug(f'Freed allocation {allocation_id}')
            return True

    async def touch(self, allocation_id: str) -> None:
        """
        Update last accessed time for allocation.
        Moves allocation to end of zone (LRU).

        Args:
            allocation_id: Allocation ID to touch
        """
        async with self._get_alloc_lock():
            if allocation_id in self.allocations:
                allocation = self.allocations[allocation_id]
                allocation.last_accessed = time.time()
                zone = allocation.zone
                if allocation_id in self.zone_allocations[zone]:
                    self.zone_allocations[zone].move_to_end(allocation_id)

    async def aggressive_cleanup(self) -> dict[str, Any]:
        """
        Perform aggressive garbage collection and MLX cache clearing.

        F267 FIX: Canonical cleanup order (F183C invariant):
          1. gc.collect() — uvolní Python refs na MLX objekty PRVNÍ
          2. gc.collect(2) — full collection na uvolněné objekty
          3. weakref.collect() — vyčistit weak reference
          4. clear_mlx_cache() — eval + clear_cache (interně dělá gc+eval+clear)
          5. Další GC kola — pro jistotu po cleanup

        Dřívější pořadí (clear_mlx_cache PŘED gc.collect) bylo špatně:
        Python objekty držely MLX tensory ještě při clear_cache, což mohlo
        na M1 8GB způsobit brief over-budget.

        Returns:
            Cleanup results
        """
        logger.info('🧹 Performing aggressive cleanup...')
        results: dict[str, bool | int | str] = {'mlx_cache_cleared': False, 'gc_collections': 0, 'weakref_collected': 0, 'neuromorphic_cleaned': False, 'success': False}
        try:
            gc.collect()
            results['gc_collections'] += 1
            gc.collect(2)
            results['gc_collections'] += 1
            with contextlib.suppress(Exception):
                results['weakref_collected'] = weakref.collect()
            if self._neuro_memory:
                neuro_result = self.cleanup_neuromorphic_memory()
                results['neuromorphic_cleaned'] = neuro_result.get('success', False)
                results['neuromorphic_forgotten'] = neuro_result.get('forgotten_patterns', 0)
                logger.info('✓ Neuromorphic memory cleaned')
            # M5: metal_reclaim() = canonical gc+eval+clear+dynamic_limit (MEM-2 pattern)
            # RSS > soft ceiling is one of the 3 designated call sites.
            try:
                from hledac.universal.utils.mlx_memory import metal_reclaim
                metal_reclaim()
                results['mlx_cache_cleared'] = True
                logger.info('✓ MLX cache cleared via metal_reclaim')
            except ImportError:
                logger.debug('mlx_memory not available, skipping MLX cache clear')
            gc.collect()
            results['gc_collections'] += 1
            await self.record_cleanup('aggressive_cleanup')
            results['success'] = True
            logger.info('✓ Aggressive cleanup complete')
        except Exception as e:
            logger.error(f'Error during aggressive cleanup: {e}')
            results['error'] = str(e)
        return results

    async def cleanup(self, level: MemoryPressureLevel | None=None) -> bool:
        """
        Async cleanup with zone-based eviction.

        Args:
            level: Cleanup level (None = use current pressure)

        Returns:
            True if anything was released
        """
        if level is None:
            level = (await self.get_memory_usage()).current_level
        logger.info(f'Memory cleanup triggered: {level.value}')
        released = False
        if level in [MemoryPressureLevel.ELEVATED, MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL]:
            released |= await self.clear_zone(MemoryZone.LOW) > 0
        if level in [MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL]:
            released |= await self.clear_zone(MemoryZone.MEDIUM) > 0
        if level == MemoryPressureLevel.CRITICAL:
            released |= await self.clear_zone(MemoryZone.HIGH) > 0
        cleanup_result = await self.aggressive_cleanup()
        released |= cleanup_result['success']
        return released

    async def clear_zone(self, zone: MemoryZone) -> int:
        """
        Clear all evictable allocations in a zone.

        Args:
            zone: Zone to clear

        Returns:
            Number of allocations cleared
        """
        async with self._get_alloc_lock():
            allocations = list(self.zone_allocations[zone].keys())
            count = 0
            for allocation_id in allocations:
                allocation = self.allocations.get(allocation_id)
                if allocation and allocation.evictable:
                    if allocation.on_evict:
                        try:
                            allocation.on_evict()
                        except Exception as e:
                            logger.error(f'Eviction callback error for {allocation_id}: {e}')
                    await self.free(allocation_id)
                    count += 1
            if count > 0:
                logger.info(f'Cleared {count} allocations from zone {zone.value}')
            return count

    async def record_cleanup(self, component: str) -> None:
        """
        Record a cleanup event using atomic counter (ISSUE-5 optimization).

        Args:
            component: Component that performed cleanup
        """
        # ISSUE-5: Use atomic itertools.count instead of lock-protected increment
        new_count = _next_cleanup_id()
        async with self._get_stats_lock():
            self.statistics.cleanup_count = new_count
            self.statistics.last_cleanup_time = time.time()
        logger.info(f'Cleanup recorded for {component} (total: {new_count})')

    async def get_memory_usage(self) -> MemoryStatistics:
        """
        Get current memory usage statistics.

        Returns:
            MemoryStatistics object

        Note:
            psutil calls (virtual_memory, memory_info) are blocking I/O (~5-50ms).
            They run outside _stats_lock via asyncio.to_thread to avoid blocking
            the event loop and to minimize lock contention with concurrent callers
            of get_zone_usage().
        """
        # Blocking I/O outside lock — eliminates event-loop blocking
        vm = await asyncio.to_thread(psutil.virtual_memory)
        process = psutil.Process()
        used_mb = await asyncio.to_thread(lambda: process.memory_info().rss / (1024 * 1024))

        async with self._get_stats_lock():
            self.statistics.used_memory_mb = used_mb
            self.statistics.available_memory_mb = vm.available / (1024 * 1024)
            self.statistics.peak_usage_mb = max(self.statistics.peak_usage_mb, used_mb)
            self.statistics.current_level = self._calculate_pressure_level()
            self.statistics.allocation_count = len(self.allocations)
            result = MemoryStatistics(total_memory_mb=vm.total / (1024 * 1024), used_memory_mb=used_mb, available_memory_mb=vm.available / (1024 * 1024), peak_usage_mb=self.statistics.peak_usage_mb, current_level=self.statistics.current_level, cleanup_count=self.statistics.cleanup_count, last_cleanup_time=self.statistics.last_cleanup_time, allocation_count=len(self.allocations))
            self._last_memory_stats = result
            return result

    async def get_zone_usage(self, zone: MemoryZone) -> ZoneStatistics:
        """
        Get memory usage for a specific zone.

        Args:
            zone: Zone to query

        Returns:
            ZoneStatistics object
        """
        async with self._get_stats_lock():
            allocations = list(self.zone_allocations[zone].values())
            total_bytes = sum(a.size_bytes for a in allocations)
            evictable = sum(1 for a in allocations if a.evictable)
            return ZoneStatistics(zone=zone.value, allocation_count=len(allocations), total_bytes=total_bytes, total_mb=total_bytes / (1024 * 1024), evictable_count=evictable, non_evictable_count=len(allocations) - evictable)

    async def get_all_zone_usage(self) -> dict[str, ZoneStatistics]:
        """Get usage for all zones (parallel fetch, fail-safe)."""
        from hledac.universal.utils.asyncx import parallel
        result = await parallel(
            [self.get_zone_usage(z) for z in MemoryZone],
            policy="collect",
            ctx="zone_usage",
        )
        return {
            z.value: data if not isinstance(data, Exception) else ZoneStatistics(
                zone=z.value, allocation_count=0, total_bytes=0, total_mb=0.0,
                evictable_count=0, non_evictable_count=0,
            )
            for z, data in zip(MemoryZone, result.ok, strict=True)
        }

    async def get_stats(self) -> dict[str, Any]:
        """Get comprehensive memory statistics (parallel zone fetch, fail-safe)."""
        from hledac.universal.utils.asyncx import parallel
        stats = await self.get_memory_usage()
        zone_result = await parallel(
            [self.get_zone_usage(z) for z in MemoryZone],
            policy="collect",
            ctx="zone_stats",
        )
        zones = {
            z.value: msgspec.to_builtins(data) if not isinstance(data, Exception) else {
                'zone': z.value, 'allocation_count': 0, 'total_bytes': 0,
                'total_mb': 0.0, 'evictable_count': 0, 'non_evictable_count': 0,
            }
            for z, data in zip(MemoryZone, zone_result.ok, strict=True)
        }
        result = {'total_mb': stats.total_memory_mb, 'used_mb': stats.used_memory_mb, 'available_mb': stats.available_memory_mb, 'peak_mb': stats.peak_usage_mb, 'percent': stats.used_memory_mb / stats.total_memory_mb * 100, 'limit_mb': self.memory_limit_mb, 'pressure': stats.current_level.value, 'allocations': stats.allocation_count, 'cleanups': stats.cleanup_count, 'zones': zones}
        if self._neuro_memory:
            result['neuromorphic'] = self.get_neuromorphic_stats()
        return result

    def register_callback(self, callback: Callable[[MemoryPressureLevel], None]) -> None:
        """
        Register a callback for memory pressure events.

        Args:
            callback: Callback function(level: MemoryPressureLevel)
        """
        self.callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[MemoryPressureLevel], None]) -> bool:
        """
        Unregister a callback.

        Args:
            callback: Callback to remove

        Returns:
            True if callback was removed
        """
        if callback in self.callbacks:
            self.callbacks.remove(callback)
            return True
        return False

    def _notify_callbacks(self, level: MemoryPressureLevel) -> None:
        """Notify registered callbacks of memory pressure."""
        for callback in self.callbacks:
            try:
                callback(level)
            except Exception as e:
                logger.error(f'Callback error: {e}')

    def _get_available_memory(self) -> int:
        """Get available memory in bytes."""
        vm = psutil.virtual_memory()
        return int(vm.available)

    async def _handle_memory_pressure(self, required_bytes: int) -> bool:
        """
        Handle memory pressure by evicting allocations.

        Args:
            required_bytes: Required memory in bytes

        Returns:
            True if enough memory was freed
        """
        logger.warning(f'Handling memory pressure, need {required_bytes} bytes')
        async with self._get_pressure_lock():
            evictable = [a for a in self.allocations.values() if a.evictable]
            evictable.sort(key=lambda a: (a.priority, a.last_accessed))
            freed_bytes = 0
            for allocation in evictable:
                if freed_bytes >= required_bytes:
                    break
                if allocation.on_evict:
                    try:
                        allocation.on_evict()
                    except Exception as e:
                        logger.error(f'Eviction callback error: {e}')
                await self.free(allocation.allocation_id)
                freed_bytes += allocation.size_bytes
                logger.debug(f'Evicted {allocation.allocation_id} ({allocation.size_bytes} bytes)')
            logger.info(f'Freed {freed_bytes} bytes via eviction')
            return freed_bytes >= required_bytes

    def _calculate_pressure_level(self, used_memory_mb: float | None = None) -> MemoryPressureLevel:
        """Calculate current memory pressure level.

        Args:
            used_memory_mb: Optional pre-fetched value. If None, reads from
                self.statistics (caller must hold _stats_lock in that case).
        """
        if used_memory_mb is None:
            used_memory_mb = self.statistics.used_memory_mb
        usage_ratio = used_memory_mb / self.memory_limit_mb
        if usage_ratio < 0.6:
            return MemoryPressureLevel.NORMAL
        elif usage_ratio < 0.8:
            return MemoryPressureLevel.ELEVATED
        elif usage_ratio < 0.9:
            return MemoryPressureLevel.HIGH
        else:
            return MemoryPressureLevel.CRITICAL

    async def check_pressure(self) -> MemoryPressureLevel:
        """
        Check current memory pressure level.

        Returns:
            Current pressure level
        """
        return (await self.get_memory_usage()).current_level

    async def register_object(self, obj: Any, zone: MemoryZone=MemoryZone.MEDIUM) -> None:
        """
        Register an object to a zone (simplified API).

        Args:
            obj: Object to register
            zone: Zone to register in
        """
        allocation_id = f'obj_{id(obj)}_{zone.value}'
        import sys
        try:
            size = sys.getsizeof(obj)
        except Exception:
            size = 1024
        await self.allocate(allocation_id=allocation_id, zone=zone, size_bytes=size, priority=5, evictable=zone in [MemoryZone.LOW, MemoryZone.MEDIUM])

    def create_url_filter(self, use_binary_fuse: bool=True, cache_size: int=1000) -> dict[str, Any]:
        """
        Create memory-efficient URL filter using Binary Fuse Filter.

        Integrated from: tools/preserved_logic/fast_filter.py

        Features:
        - Binary Fuse Filter (10x smaller than Bloom filter, 0% false negatives)
        - LRU cache for recent checks
        - Domain, URL, and pattern-based blocking
        - Memory-optimized for M1 8GB

        Args:
            use_binary_fuse: Use pyxorfilter (fallback to Python set if unavailable)
            cache_size: LRU cache size for recent checks

        Returns:
            Filter instance info
        """
        try:
            from hledac.universal.tools.preserved_logic.fast_filter import FastFilter
            filter_instance = FastFilter(use_bff=use_binary_fuse, enable_cache=True)
            filter_id = f'url_filter_{id(filter_instance)}'
            if not hasattr(self, '_filters'):
                self._filters = {}
            self._filters[filter_id] = filter_instance
            return {'success': True, 'filter_id': filter_id, 'type': 'FastFilter', 'binary_fuse_available': filter_instance.is_bff_available(), 'default_blocked_domains': len(FastFilter.DEFAULT_BLOCKED_DOMAINS), 'cache_enabled': True, 'cache_size': cache_size}
        except ImportError:
            logger.warning('FastFilter not available')
            return {'success': False, 'error': 'FastFilter module not available'}
        except Exception as e:
            logger.error(f'Failed to create URL filter: {e}')
            return {'success': False, 'error': str(e)}

    def check_url_allowed(self, filter_id: str, url: str) -> dict[str, Any]:
        """
        Check if URL is allowed (not blocked) using FastFilter.

        Args:
            filter_id: Filter instance ID from create_url_filter
            url: URL to check

        Returns:
            Check result with allow/block status
        """
        if not hasattr(self, '_filters') or filter_id not in self._filters:
            return {'success': False, 'error': 'Filter not found', 'allowed': True}
        try:
            filter_instance = self._filters[filter_id]
            allowed = filter_instance.check_url(url)
            stats = filter_instance.get_stats()
            return {'success': True, 'url': url, 'allowed': allowed, 'blocked': not allowed, 'filter_stats': stats}
        except Exception as e:
            logger.error(f'URL check failed: {e}')
            return {'success': False, 'error': str(e), 'allowed': True}

    def add_blocked_urls(self, filter_id: str, urls: list[str], domains: list[str] | None=None, patterns: list[str] | None=None) -> dict[str, Any]:
        """
        Add blocked URLs, domains, or patterns to filter.

        Args:
            filter_id: Filter instance ID
            urls: URLs to block
            domains: Domains to block
            patterns: Regex patterns to block

        Returns:
            Update result
        """
        if not hasattr(self, '_filters') or filter_id not in self._filters:
            return {'success': False, 'error': 'Filter not found'}
        try:
            filter_instance = self._filters[filter_id]
            added_count = 0
            if urls:
                for url in urls:
                    filter_instance.add_blocked_url(url)
                    added_count += 1
            if domains:
                for domain in domains:
                    filter_instance.add_blocked_domain(domain)
                    added_count += 1
            if patterns:
                for pattern in patterns:
                    filter_instance.add_blocked_pattern(pattern)
                    added_count += 1
            return {'success': True, 'added_count': added_count, 'total_blocked': filter_instance._set_filter.size() if filter_instance._set_filter else 0}
        except Exception as e:
            logger.error(f'Failed to add blocked items: {e}')
            return {'success': False, 'error': str(e)}

    def detect_language(self, text: str, min_length: int=10, fallback: bool=True) -> dict[str, Any]:
        """
        Fast language detection optimized for M1 Apple Silicon.

        Integrated from: tools/preserved_logic/fast_lang.py

        Features:
        - Uses fast-langdetect (FTZ format) for ultra-fast detection
        - Character range fallback for CJK, Cyrillic, Arabic
        - Word-based fallback for Czech/English detection
        - Supports 30+ languages

        Args:
            text: Text to analyze
            min_length: Minimum text length for detection
            fallback: Enable fallback detection methods

        Returns:
            Detection result with language code and name
        """
        try:
            from hledac.universal.tools.preserved_logic.fast_lang import LanguageDetector
            detector = LanguageDetector(fallback_mode=fallback)
            lang_code = detector.detect(text, min_length=min_length)
            lang_name = detector.get_language_name(lang_code)
            return {'success': True, 'language_code': lang_code, 'language_name': lang_name, 'supported': detector.is_supported(lang_code), 'text_length': len(text), 'min_length': min_length}
        except ImportError:
            logger.warning('LanguageDetector not available')
            return {'success': False, 'error': 'LanguageDetector not available', 'language_code': 'unknown', 'language_name': 'Unknown'}
        except Exception as e:
            logger.error(f'Language detection failed: {e}')
            return {'success': False, 'error': str(e), 'language_code': 'unknown'}

    def batch_detect_languages(self, texts: list[str], min_length: int=10) -> dict[str, Any]:
        """
        Detect languages for multiple texts.

        Args:
            texts: List of texts to analyze
            min_length: Minimum text length for detection

        Returns:
            Batch detection results
        """
        try:
            from hledac.universal.tools.preserved_logic.fast_lang import LanguageDetector
            detector = LanguageDetector()
            results = detector.batch_detect(texts, min_length=min_length)
            lang_counts = {}
            for lang in results:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            return {'success': True, 'total_texts': len(texts), 'results': [{'text_preview': text[:50] + '...' if len(text) > 50 else text, 'language_code': lang, 'language_name': detector.get_language_name(lang)} for text, lang in zip(texts, results, strict=False)], 'language_distribution': lang_counts}
        except Exception as e:
            logger.error(f'Batch language detection failed: {e}')
            return {'success': False, 'error': str(e)}

    def filter_by_language(self, texts: list[Any], allowed_languages: list[str]) -> dict[str, Any]:
        """
        Filter texts by allowed languages.

        Args:
            texts: List of texts or (text, metadata) tuples
            allowed_languages: List of allowed language codes (e.g., ['en', 'cs'])

        Returns:
            Filtered results
        """
        try:
            from hledac.universal.tools.preserved_logic.fast_lang import LanguageDetector
            detector = LanguageDetector()
            filtered = detector.filter_by_language(texts, allowed_languages)
            return {'success': True, 'total_input': len(texts), 'filtered_count': len(filtered), 'allowed_languages': allowed_languages, 'filtered_items': filtered}
        except Exception as e:
            logger.error(f'Language filtering failed: {e}')
            return {'success': False, 'error': str(e)}

# DEPRECATED — MOVED to coordinators/memory/ (F320)
class _DeprecatedContextPriority(Enum):
    """Placeholder to preserve enum values during migration."""


class ContextPriority(Enum):
    """Priority levels for context items."""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'

class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = 'data_collection'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'
    VALIDATION = 'validation'

class ContextItem(Struct):
    """Individual context item with metadata for three-tier storage."""
    item_id: str
    content: str
    metadata: dict[str, Any]
    tokens: int
    priority: ContextPriority
    access_count: int
    last_accessed: float
    embedding: Any | None = None
    content_type: str = 'general'
    confidence: float = 0.5

class CompressedContext(Struct):
    """Compressed context container."""
    context_id: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    critical_content: str
    important_summary: str
    abstract_summary: str
    full_compressed: bytes
    metadata: dict[str, Any]
    timestamp: float

class ContextOptimizationManager:
    """
    Context optimization with three-tier storage and compression.

    Integrated from context_optimization/ modules:
    - Three-tier storage: hot (RAM), warm (cache), cold (disk)
    - FastEmbed embeddings for semantic search (optional)
    - LZ4 compression for storage
    - Phase-based prioritization
    """
    __slots__ = ('cold_storage', 'embedder', 'embedding_dim', 'enable_embeddings', 'hot_context', 'hot_tokens', 'max_hot_tokens', 'max_warm_tokens', 'phase_weights', 'stats', 'storage_path', 'warm_context', 'warm_tokens')

    def __init__(self, max_hot_tokens: int=20000, max_warm_tokens: int=40000, storage_path: str='./context_cache', enable_embeddings: bool=False) -> None:
        """
        Initialize context optimization manager.

        Args:
            max_hot_tokens: Maximum tokens in hot (RAM) storage
            max_warm_tokens: Maximum tokens in warm (cache) storage
            storage_path: Path for persistent storage
            enable_embeddings: Whether to enable semantic embeddings
        """
        self.max_hot_tokens = max_hot_tokens
        self.max_warm_tokens = max_warm_tokens
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.hot_context: dict[str, ContextItem] = {}
        self.warm_context: dict[str, ContextItem] = {}
        self.cold_storage: dict[str, ContextItem] = {}
        self.hot_tokens = 0
        self.warm_tokens = 0
        self.enable_embeddings = enable_embeddings
        self.embedder = None
        self.embedding_dim = 384
        if enable_embeddings:
            self._initialize_embedder()
        self.stats = {'hits': 0, 'misses': 0, 'evictions': 0, 'promotions': 0, 'compressions': 0, 'total_requests': 0}
        self.phase_weights = {ResearchPhase.DATA_COLLECTION: {'data_source': 0.9, 'research': 0.7}, ResearchPhase.ANALYSIS: {'analysis': 0.9, 'insight': 0.8}, ResearchPhase.SYNTHESIS: {'synthesis': 0.9, 'summary': 0.8}, ResearchPhase.VALIDATION: {'validation': 0.9, 'evidence': 0.7}}
        logger.info(f'ContextOptimizationManager initialized (hot: {max_hot_tokens}, warm: {max_warm_tokens})')

    def _initialize_embedder(self) -> None:
        """Initialize MLXEmbedder (primary) — Apple Silicon native, M1 8GB optimal."""
        try:
            from hledac.universal.brain.mlx_embedder import MLXEmbedder
            self.embedder = MLXEmbedder()
            self._mlx_embedder = self.embedder
            self.embedding_dim = 384
            logger.info('MLXEmbedder initialized for semantic search')
        except Exception:
            logger.warning('MLXEmbedder not available, semantic search disabled')
            self.enable_embeddings = False

    def add_context(self, item_id: str, content: str, metadata: dict[str, Any] | None=None, priority: ContextPriority=ContextPriority.MEDIUM, phase: ResearchPhase=ResearchPhase.DATA_COLLECTION) -> bool:
        """
        Add context item to three-tier storage.

        Args:
            item_id: Unique item identifier
            content: Content to store
            metadata: Additional metadata
            priority: Item priority
            phase: Current research phase

        Returns:
            True if added successfully
        """
        metadata = metadata or {}
        tokens = len(content.split())
        content_type = metadata.get('type', 'general')
        phase_weight = self.phase_weights.get(phase, {}).get(content_type, 0.5)
        item = ContextItem(item_id=item_id, content=content, metadata=metadata, tokens=tokens, priority=priority, access_count=0, last_accessed=time.time(), content_type=content_type, confidence=metadata.get('confidence', 0.5))
        if priority == ContextPriority.HIGH or phase_weight > 0.8:
            if self.hot_tokens + tokens > self.max_hot_tokens:
                self._evict_from_hot(tokens)
            self.hot_context[item_id] = item
            self.hot_tokens += tokens
        elif priority == ContextPriority.MEDIUM or phase_weight > 0.5:
            if self.warm_tokens + tokens > self.max_warm_tokens:
                self._evict_from_warm(tokens)
            self.warm_context[item_id] = item
            self.warm_tokens += tokens
        else:
            self.cold_storage[item_id] = item
            self._persist_to_disk(item)
        return True

    def get_context(self, item_id: str) -> str | None:
        """
        Retrieve context item with automatic promotion.

        Args:
            item_id: Item identifier

        Returns:
            Content if found, None otherwise
        """
        self.stats['total_requests'] += 1
        if item_id in self.hot_context:
            item = self.hot_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            return item.content
        if item_id in self.warm_context:
            item = self.warm_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_hot(item)
            self.stats['hits'] += 1
            return item.content
        if item_id in self.cold_storage:
            item = self.cold_storage[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_warm(item)
            self.stats['hits'] += 1
            return item.content
        self.stats['misses'] += 1
        return None

    def compress_context(self, context_id: str, content: str, compression_level: int=3) -> CompressedContext:
        """
        Compress context using LZ4.

        Args:
            context_id: Unique identifier
            content: Content to compress
            compression_level: LZ4 compression level

        Returns:
            CompressedContext object
        """
        try:
            import lz4.frame
            original_size = len(content.encode('utf-8'))
            compressed = lz4.frame.compress(content.encode('utf-8'), compression_level=compression_level)
            compressed_size = len(compressed)
            words = content.split()
            critical = ' '.join(words[:50]) if len(words) > 50 else content
            important = ' '.join(words[:100]) if len(words) > 100 else content
            abstract = ' '.join(words[:20]) if len(words) > 20 else content
            result = CompressedContext(context_id=context_id, original_size=original_size, compressed_size=compressed_size, compression_ratio=original_size / max(compressed_size, 1), critical_content=critical, important_summary=important, abstract_summary=abstract, full_compressed=compressed, metadata={'compression_level': compression_level}, timestamp=time.time())
            self.stats['compressions'] += 1
            return result
        except ImportError:
            logger.warning('LZ4 not available, returning uncompressed')
            return CompressedContext(context_id=context_id, original_size=len(content.encode('utf-8')), compressed_size=len(content.encode('utf-8')), compression_ratio=1.0, critical_content=content[:200], important_summary=content[:500], abstract_summary=content[:100], full_compressed=content.encode('utf-8'), metadata={}, timestamp=time.time())

    def decompress_context(self, compressed: CompressedContext, detail_level: str='important') -> str:
        """
        Decompress context at specified detail level.

        Args:
            compressed: CompressedContext object
            detail_level: 'critical', 'important', or 'abstract'

        Returns:
            Decompressed content
        """
        if detail_level == 'critical':
            return compressed.critical_content
        elif detail_level == 'abstract':
            return compressed.abstract_summary
        else:
            try:
                import lz4.frame
                return lz4.frame.decompress(compressed.full_compressed).decode('utf-8')
            except Exception:
                return compressed.important_summary

    def _evict_from_hot(self, required_tokens: int) -> None:
        """Evict items from hot storage to make room."""
        items = sorted(self.hot_context.items(), key=lambda x: (x[1].priority.value, x[1].last_accessed))
        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.hot_context[item_id]
            self.hot_tokens -= item.tokens
            freed += item.tokens
            if self.warm_tokens + item.tokens <= self.max_warm_tokens:
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens
            else:
                self._evict_from_warm(item.tokens)
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens
        self.stats['evictions'] += 1

    def _evict_from_warm(self, required_tokens: int) -> None:
        """Evict items from warm storage to cold storage."""
        items = sorted(self.warm_context.items(), key=lambda x: (x[1].priority.value, x[1].last_accessed))
        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.warm_context[item_id]
            self.warm_tokens -= item.tokens
            freed += item.tokens
            self.cold_storage[item_id] = item
            self._persist_to_disk(item)

    def _promote_to_hot(self, item: ContextItem) -> None:
        """Promote item from warm to hot storage."""
        if item.tokens > self.max_hot_tokens:
            return
        if self.hot_tokens + item.tokens > self.max_hot_tokens:
            self._evict_from_hot(item.tokens)
        if item.item_id in self.warm_context:
            del self.warm_context[item.item_id]
            self.warm_tokens -= item.tokens
        self.hot_context[item.item_id] = item
        self.hot_tokens += item.tokens
        self.stats['promotions'] += 1

    def _promote_to_warm(self, item: ContextItem) -> None:
        """Promote item from cold to warm storage."""
        if item.tokens > self.max_warm_tokens:
            return
        if self.warm_tokens + item.tokens > self.max_warm_tokens:
            self._evict_from_warm(item.tokens)
        if item.item_id in self.cold_storage:
            del self.cold_storage[item.item_id]
        self.warm_context[item.item_id] = item
        self.warm_tokens += item.tokens
        self.stats['promotions'] += 1

    def _persist_to_disk(self, item: ContextItem) -> None:
        """Persist item to disk storage."""
        file_path = self.storage_path / f'{item.item_id}.json'
        try:
            with open(file_path, 'wb') as f:
                f.write(_serialize_to_json(item))
        except Exception as e:
            logger.error(f'Failed to persist {item.item_id}: {e}')

    def get_stats(self) -> dict[str, Any]:
        """Get context optimization statistics."""
        return {**self.stats, 'hot_items': len(self.hot_context), 'warm_items': len(self.warm_context), 'cold_items': len(self.cold_storage), 'hot_tokens': self.hot_tokens, 'warm_tokens': self.warm_tokens, 'hit_rate': self.stats['hits'] / max(self.stats['total_requests'], 1)}

class CacheType(Enum):
    """Types of cache entries."""
    SEMANTIC = 'semantic'
    COMPUTATION = 'computation'
    QUERY = 'query'

class CacheLocation(Enum):
    """Cache location levels."""
    L1_MEMORY = 'l1_memory'
    L2_DISK = 'l2_disk'

class CacheEntry(Struct):
    """Single cache entry with FAISS embedding support."""
    cache_id: str
    content: Any
    embedding: Any | None
    access_count: int
    last_accessed: float
    created_at: float
    size_bytes: int
    cache_type: CacheType
    metadata: dict[str, Any]

class MultiLevelContextCache:
    """
    Multi-level context cache with semantic search using FAISS.

    Features:
    - L1 (memory) + L2 (disk) hierarchy
    - FAISS semantic index for similarity search
    - Thread-safe operations
    - CacheType classification
    - Configurable similarity threshold
    """
    __slots__ = ('_hnsw_ef_construction', '_hnsw_ef_search', '_hnsw_index', '_hnsw_m', '_hnsw_max_elements', '_l1_freq', '_l2_freq', '_lock', 'embedder', 'embedding_dim', 'embedding_model', 'embedding_to_cache_id', 'faiss_available', 'l1_cache', 'l1_max_size_bytes', 'l2_cache', 'l2_storage_path', 'max_entries', 'semantic_index', 'similarity_threshold', 'stats')

    def __init__(self, embedding_model: str='nomic-ai/nomic-embed-text-v1.5', l1_max_size_mb: float=100.0, l2_storage_path: str='cache_storage', similarity_threshold: float=0.95, max_entries: int=10000) -> None:
        """
        Initialize multi-level cache.

        Args:
            embedding_model: FastEmbed model name
            l1_max_size_mb: Maximum L1 cache size in MB
            l2_storage_path: Path for L2 disk cache
            similarity_threshold: Threshold for semantic similarity
            max_entries: Maximum total entries
        """
        self.embedding_model = embedding_model
        self.l1_max_size_bytes = int(l1_max_size_mb * 1024 * 1024)
        self.l2_storage_path = Path(l2_storage_path)
        self.l2_storage_path.mkdir(parents=True, exist_ok=True)
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.embedder = None
        self.embedding_dim = 384
        self._initialize_embedder()
        self.l1_cache: LRUCache[str, CacheEntry] = LRUCache()
        self.l2_cache: dict[str, CacheEntry] = {}
        self._l1_freq: dict[str, int] = {}
        self._l2_freq: dict[str, int] = {}
        try:
            import faiss
            self.semantic_index = faiss.IndexFlatIP(self.embedding_dim)
            self.faiss_available = True
        except ImportError:
            logger.warning('FAISS not available, semantic search disabled')
            self.semantic_index = None
            self.faiss_available = False
        self._hnsw_index = None
        self._hnsw_max_elements = 10000
        self._hnsw_m = 16
        self._hnsw_ef_construction = 200
        self._hnsw_ef_search = 50
        if USEARCH_AVAILABLE:
            self._init_hnsw()
        self.embedding_to_cache_id: dict[int, str] = {}
        self.stats = {'hits': 0, 'misses': 0, 'total_requests': 0, 'l1_promotions': 0, 'l2_demotions': 0, 'evictions': 0, 'similarities': []}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._load_l2_cache()
        self._rebuild_semantic_index()

    def _init_hnsw(self) -> None:
        """Initialize usearch index for approximate nearest neighbor search (Sprint 26)."""
        if not USEARCH_AVAILABLE:
            return
        try:
            import usearch.index
            self._hnsw_index = usearch.index.Index(ndim=self.embedding_dim, metric='cos', dtype='f32', connectivity=self._hnsw_m, expansion_add=min(self._hnsw_ef_construction, 100), expansion_search=self._hnsw_ef_search)
            logger.debug('USearch index initialized')
        except Exception as e:
            logger.warning(f'USearch index initialization failed: {e}')
            self._hnsw_index = None

    def _hnsw_search(self, query_emb: Any, k: int) -> list[int]:
        """Search usearch index for approximate nearest neighbors (Sprint 26)."""
        if self._hnsw_index is None:
            return []
        try:
            results = self._hnsw_index.search(query_emb.astype(np.float32), count=k)
            return [int(getattr(r, 'key', 0)) for r in results]
        except Exception:
            return []

    def _initialize_embedder(self) -> None:
        """fastembed REMOVED P0-1: MLXEmbedder used elsewhere; cache uses dummy embeddings."""
        self.embedder = None
        self.embedding_dim = 384

    def _load_l2_cache(self) -> None:
        """Load L2 cache from disk. Prefer zstd-compressed .json.zst, fallback to .json."""
        try:
            zst_file = self.l2_storage_path / 'l2_cache.json.zst'
            json_file = self.l2_storage_path / 'l2_cache.json'
            if zst_file.exists():
                with open(zst_file, 'rb') as f:
                    cache_bytes = f.read()
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning('L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh', len(cache_bytes) // (1024 * 1024))
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_from_json(cache_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.zst)')
            elif json_file.exists():
                with open(json_file, 'rb') as f:
                    cache_bytes = f.read()
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning('L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh', len(cache_bytes) // (1024 * 1024))
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_from_json(cache_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.json legacy)')
            else:
                self.l2_cache = {}
        except Exception as e:
            logger.warning(f'Could not load L2 cache: {e}')
            self.l2_cache = {}

    def _save_l2_cache(self) -> None:
        """Save L2 cache to disk as zstd-compressed .json.zst."""
        try:
            cache_file = self.l2_storage_path / 'l2_cache.json.zst'
            with open(cache_file, 'wb') as f:
                f.write(_serialize_to_json(self.l2_cache))
        except Exception as e:
            logger.warning(f'Could not save L2 cache: {e}')

    def _rebuild_semantic_index(self) -> None:
        """Rebuild FAISS semantic index from existing entries."""
        if not self.faiss_available:
            return
        try:
            import faiss
            self.semantic_index = faiss.IndexFlatIP(self.embedding_dim)
            self.embedding_to_cache_id.clear()
            all_entries = list(self.l1_cache.values()) + list(self.l2_cache.values())
            for entry in all_entries:
                if entry.embedding is not None:
                    embedding_id = len(self.embedding_to_cache_id)
                    self.embedding_to_cache_id[embedding_id] = entry.cache_id
                    self.semantic_index.add(entry.embedding.reshape(1, -1).astype('float32'))
        except Exception as e:
            logger.warning(f'Could not rebuild semantic index: {e}')
    _embedding_cache: dict[str, Any] = {}
    _embedding_cache_lock: asyncio.Lock | None = None

    async def _get_embedding_async(self, text: str) -> Any | None:
        """Get embedding for text using MLXEmbedder or FastEmbed (async).

        F320-Issue2: Results are cached by NFC-normalized text to avoid
        re-encoding the same string across cycles."""
        import unicodedata
        normalized = unicodedata.normalize('NFC', text)
        if self._embedding_cache_lock is None:
            try:
                self._embedding_cache_lock = asyncio.Lock()
            except Exception:
                self._embedding_cache_lock = None
        cached = self._embedding_cache.get(normalized)
        if cached is not None:
            return cached
        if self.embedder:
            try:
                if hasattr(self.embedder, 'encode_batch'):
                    # C7-FIX: Use asyncio.Runner() instead of new_event_loop/run_until_complete.
                    # Runner handles loop lifecycle automatically and is the modern Python 3.11+ pattern.
                    result = await self.embedder.encode_batch([text])
                    return result[0] if result else None
            except Exception as e:
                logger.debug(f'Embedding failed: {e}')
        result = None
        self._embedding_cache[normalized] = result
        return result

    def _get_embedding(self, text: str) -> Any | None:
        """Get embedding for text using MLXEmbedder or FastEmbed (sync wrapper).

        C7-FIX: Uses run_sync_async() from sync_bridge for M1 safety.
        Prefer async _get_embedding_async() when called from async context.
        """
        import unicodedata

        from hledac.universal.utils.sync_bridge import run_sync_async
        normalized = unicodedata.normalize('NFC', text)
        cached = self._embedding_cache.get(normalized)
        if cached is not None:
            return cached
        try:
            return run_sync_async(self._get_embedding_async(text))
        except Exception:
            return None

    async def get(self, input_data: Any, cache_type: CacheType=CacheType.COMPUTATION, threshold: float | None=None) -> Any | None:
        """
        Get cached result using semantic similarity search.

        Args:
            input_data: Input data to lookup
            cache_type: Type of cache entry
            threshold: Custom similarity threshold

        Returns:
            Cached content or None if not found
        """
        threshold = threshold or self.similarity_threshold
        self.stats['total_requests'] += 1
        input_text = str(input_data)
        similar_entry = await self._find_similar_entry(input_text, threshold)
        if similar_entry:
            async with self._lock:
                self.stats['hits'] += 1
                self._update_access(similar_entry.cache_id)
                if similar_entry.cache_id in self.l2_cache:
                    self._promote_to_l1(similar_entry.cache_id)
            return similar_entry.content
        self.stats['misses'] += 1
        return None

    async def _find_similar_entry(self, input_text: str, threshold: float) -> CacheEntry | None:
        """Find semantically similar cache entry using usearch (Sprint 26) or FAISS fallback."""
        if self._hnsw_index is not None:
            return await self._find_similar_entry_hnsw(input_text, threshold)
        if not self.faiss_available or self.semantic_index is None:
            return None
        input_embedding = await self._get_embedding_async(input_text)
        if input_embedding is None:
            return None
        try:
            query_embedding = input_embedding.reshape(1, -1).astype('float32')
            D, I = self.semantic_index.search(query_embedding, 10)
            for idx, similarity in zip(I[0], D[0], strict=False):
                if float(similarity) >= threshold:
                    cache_id = self.embedding_to_cache_id.get(int(idx))
                    if not cache_id:
                        continue
                    entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                    if entry:
                        async with self._lock:
                            self.stats['similarities'].append(float(similarity))
                        return entry
        except Exception as e:
            logger.debug(f'Similarity search failed: {e}')
        return None

    async def _find_similar_entry_hnsw(self, input_text: str, threshold: float) -> CacheEntry | None:
        """Find semantically similar cache entry using usearch (Sprint 26)."""
        input_embedding = await self._get_embedding_async(input_text)
        if input_embedding is None:
            return None
        try:
            indices = self._hnsw_search(input_embedding, k=10)
            for idx in indices:
                cache_id = self.embedding_to_cache_id.get(int(idx))
                if not cache_id:
                    continue
                entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                if entry:
                    async with self._lock:
                        self.stats['similarities'].append(1.0)
                    return entry
        except Exception as e:
            logger.debug(f'USearch similarity search failed: {e}')
        return None

    async def set(self, input_data: Any, content: Any, cache_type: CacheType=CacheType.COMPUTATION) -> None:
        """
        Cache a computation result.

        Args:
            input_data: Input data (used as key)
            content: Result to cache
            cache_type: Type of cache entry
        """
        cache_id = hashlib.md5(str(input_data).encode()).hexdigest()[:16]
        if cache_id in self.l1_cache or cache_id in self.l2_cache:
            return
        input_text = str(input_data)
        embedding = await self._get_embedding_async(input_text)
        cache_entry = CacheEntry(cache_id=cache_id, content=content, embedding=embedding, access_count=1, last_accessed=time.time(), created_at=time.time(), size_bytes=sys.getsizeof(content), cache_type=cache_type, metadata={})
        async with self._lock:
            if embedding is not None and self.faiss_available:
                try:
                    embedding_id = len(self.embedding_to_cache_id)
                    self.embedding_to_cache_id[embedding_id] = cache_id
                    self.semantic_index.add(embedding.reshape(1, -1).astype('float32'))
                except Exception as e:
                    logger.debug(f'Could not add to semantic index: {e}')
            if self._get_l1_size_bytes() + cache_entry.size_bytes <= self.l1_max_size_bytes:
                self.l1_cache[cache_id] = cache_entry
                self.l1_cache.move_to_end(cache_id)
                self._l1_freq[cache_id] = 1
            else:
                self.l2_cache[cache_id] = cache_entry
                self._l2_freq[cache_id] = 1
                await asyncio.to_thread(self._save_l2_cache)
            self._check_eviction()

    def _get_l1_size_bytes(self) -> int:
        """Get total size of L1 cache."""
        return sum(entry.size_bytes for entry in self.l1_cache.values())

    def _update_access(self, cache_id: str) -> None:
        """Update access statistics and LFU frequency counter for cache entry.

        S3 fix: Inkrements _l1_freq or _l2_freq frequency counter for LFU eviction.
        Also calls _check_eviction() to prevent unbounded growth on read-heavy workloads.
        """
        current_time = time.time()
        if cache_id in self.l1_cache:
            entry = self.l1_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self.l1_cache.move_to_end(cache_id)
            self._l1_freq[cache_id] = self._l1_freq.get(cache_id, 0) + 1
        elif cache_id in self.l2_cache:
            entry = self.l2_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self._l2_freq[cache_id] = self._l2_freq.get(cache_id, 0) + 1
        self._check_eviction()

    def _promote_to_l1(self, cache_id: str) -> None:
        """Promote entry from L2 to L1 cache."""
        if cache_id not in self.l2_cache:
            return
        entry = self.l2_cache.pop(cache_id)
        if self._get_l1_size_bytes() + entry.size_bytes <= self.l1_max_size_bytes:
            self.l1_cache[cache_id] = entry
            self.stats['l1_promotions'] += 1
        else:
            self.l2_cache[cache_id] = entry
        self._save_l2_cache()

    def _check_eviction(self) -> None:
        """Check and perform LFU eviction if needed.

        S3 fix: LFU eviction replaces LRU. Frequency counters (_l1_freq, _l2_freq)
        track access count. Eviction targets least-frequently-used items first.
        Batch eviction removes 10% of entries at once to avoid O(n) per-item overhead.
        """
        while self._get_l1_size_bytes() > self.l1_max_size_bytes and self.l1_cache:
            lfu_id = min(self._l1_freq, key=self._l1_freq.get) if self._l1_freq else None
            if lfu_id and lfu_id in self.l1_cache:
                oldest_id, oldest_entry = (lfu_id, self.l1_cache.pop(lfu_id))
                self._l1_freq.pop(lfu_id, None)
            else:
                oldest_id, oldest_entry = self.l1_cache.popitem(last=False)
                self._l1_freq.pop(oldest_id, None)
            self.l2_cache[oldest_id] = oldest_entry
            self._l2_freq[oldest_id] = self._l1_freq.get(oldest_id, 1)
            self.stats['l2_demotions'] += 1
        total_entries = len(self.l1_cache) + len(self.l2_cache)
        if total_entries > self.max_entries and self.l2_cache:
            batch_size = max(1, int(self.max_entries * 0.1))
            evicted = 0
            for _ in range(min(batch_size, len(self.l2_cache))):
                if not self.l2_cache:
                    break
                lfu_id = min(self._l2_freq, key=self._l2_freq.get) if self._l2_freq else None
                if lfu_id and lfu_id in self.l2_cache:
                    del self.l2_cache[lfu_id]
                    self._l2_freq.pop(lfu_id, None)
                else:
                    oldest_id = min(self.l2_cache.keys(), key=lambda k: self.l2_cache[k].last_accessed)
                    del self.l2_cache[oldest_id]
                    self._l2_freq.pop(oldest_id, None)
                self.stats['evictions'] += 1
                evicted += 1
            if evicted > 0:
                self._save_l2_cache()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self.stats['hits'] + self.stats['misses']
        avg_similarity = 0.0
        if self.stats['similarities']:
            avg_similarity = sum(self.stats['similarities']) / len(self.stats['similarities'])
        return {'total_entries': len(self.l1_cache) + len(self.l2_cache), 'l1_entries': len(self.l1_cache), 'l2_entries': len(self.l2_cache), 'hit_count': self.stats['hits'], 'miss_count': self.stats['misses'], 'hit_rate': self.stats['hits'] / total if total > 0 else 0.0, 'l1_size_mb': self._get_l1_size_bytes() / (1024 * 1024), 'avg_similarity_score': avg_similarity, 'l1_promotions': self.stats['l1_promotions'], 'l2_demotions': self.stats['l2_demotions'], 'evictions': self.stats['evictions']}

    async def clear(self, location: CacheLocation | None=None) -> None:
        """
        Clear cache entries.

        Args:
            location: Specific location to clear, or None for all
        """
        async with self._lock:
            if location is None or location == CacheLocation.L1_MEMORY:
                self.l1_cache.clear()
                self._l1_freq.clear()
            if location is None or location == CacheLocation.L2_DISK:
                self.l2_cache.clear()
                self._l2_freq.clear()
                self._save_l2_cache()
            self._rebuild_semantic_index()

class MemoryPressurePoller:
    """Throttled memory pressure monitoring."""
    __slots__ = ('_interval', '_level', '_shutdown', '_task')

    def __init__(self, interval: float=5.0) -> None:
        self._interval = interval
        self._level = 0.1
        self._task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        """Start polling."""
        self._task = safe_create_task(self._poll_loop(), name='memory_coordinator:poll')

    async def aclose(self, timeout_s: float=10.0) -> None:
        """
        Graceful shutdown — signal poller to stop, bounded wait.

        Args:
            timeout_s: max seconds to wait for poll loop to finish (default 10.0).
        """
        self._shutdown.set()
        if self._task is not None:
            try:
                await safe_wait_for(self._task, timeout=timeout_s, label='memory_coord_shutdown')
            except TimeoutError:
                self._task.cancel()
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await self._task
                logger.debug('memory_coordinator: poll loop cancelled after %.1fs', timeout_s)

    async def _poll_loop(self) -> None:
        """Polling loop."""
        try:
            libc = ctypes.CDLL('/usr/lib/libc.dylib')
            libc.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            libc.sysctlbyname.restype = ctypes.c_int
        except Exception:
            libc = None
        while True:
            try:
                if libc is not None:
                    val = ctypes.c_uint32()
                    size = ctypes.c_size_t(4)
                    ret = libc.sysctlbyname(b'kern.memorystatus_vm_pressure_level', ctypes.byref(val), ctypes.byref(size), None, 0)
                    if ret == 0:
                        self._level = {0: 0.1, 2: 0.6, 4: 0.95}.get(val.value, 0.1)
            except Exception as e:
                logger.warning(f'MemoryPressurePoller error: {e}')
            await asyncio.sleep(self._interval)
            if self._shutdown.is_set():
                break

    def get_level(self) -> float:
        """Get current memory pressure level (0.0 - 1.0)."""
        return self._level

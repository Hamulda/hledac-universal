"""
Universal Memory Coordinator
============================

Memory management combining:
- Priority-based zones (CRITICAL, HIGH, MEDIUM, LOW)
- Aggressive garbage collection with MLX cache clearing via mlx_memory adapter
- Thread-safe operations with locks
- Memory pressure callbacks

Features:
- Single unified zone system (collapsed dual zones in F214)
- MLX cache management delegated to utils/mlx_memory.py
- Allocation tracking with eviction callbacks
- Memory pressure monitoring with callbacks
- Thread-safe operations

Class Index
-----------
**Neuromorphic / STDP Layer** (lines 138-655):
  - ``NeuromorphicMemoryZone`` — Enum: ZONE_A (active), ZONE_B (consolidation), ZONE_C (dormant)
  - ``MemoryPattern`` — dataclass: temporal pattern with timestamp, intensity, frequency
  - ``STDPParameters`` — dataclass: spike-timing-dependent plasticity config (A_plus, A_minus, tau)
  - ``NeuromorphicMemoryManager`` — STDP-based neuromorphic memory with zone transitions

**Memory Allocation & Zones** (lines 656-728):
  - ``MemoryPressureLevel`` — Enum: NORMAL, WARNING, CRITICAL, EMERGENCY
  - ``ThermalState`` — IntEnum: COLD, COOL, NOMINAL, WARM, HOT
  - ``MemoryZone`` — Enum: CRITICAL, HIGH, MEDIUM, LOW (priority-based memory zones)
  - ``MemoryAllocation`` — dataclass: per-zone allocation entry with used/peak/frag
  - ``MemoryStatistics`` — dataclass: global memory stats
  - ``ZoneStatistics`` — dataclass: per-zone memory stats

**Core Coordinator** (lines 729-1915):
  - ``UniversalMemoryCoordinator`` — main facade; thread-safe zone management, MLX coupling is lazy/fail-soft

**Context Optimization** (lines 1917-2335):
  - ``ContextPriority`` — Enum: CRITICAL, HIGH, MEDIUM, LOW, BACKGROUND
  - ``ResearchPhase`` — Enum: DISCOVERY, ACQUISITION, ANALYSIS, SYNTHESIS, REPORTING
  - ``ContextItem`` — dataclass: priority + metadata for one context entry
  - ``CompressedContext`` — dataclass: compressed representation with ratio
  - ``ContextOptimizationManager`` — LANCEDB reranking integration; narrow seam ``get_reranking_context()`` for thermal/battery-aware reranking

**Multi-Level Cache** (lines 2337-2820):
  - ``CacheType`` — Enum: L1 (hot), L2 (warm), L3 (cold)
  - ``CacheLocation`` — Enum: RAM, DISK, LMDB
  - ``CacheEntry`` — dataclass: cached item with key, data, size, access metadata
  - ``MultiLevelContextCache`` — LRU cache with L1/L2/L3 tiers and RAM pressure gating

**Memory Pressure Polling** (lines 2822-end):
  - ``MemoryPressurePoller`` — background poller with callbacks on pressure transitions

Notes
-----
- MLX memory coupling is lazy and fail-soft: MLX is not loaded or initialized by this module
- Neuromorphic subsystem is optional, controlled by ``enable_neuromorphic`` parameter on ``UniversalMemoryCoordinator``
- ``get_reranking_context()`` is the narrow seam for Lancedb/reranking with thermal/battery awareness
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import hashlib
import logging
import sys
import threading
import time
import weakref
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

import psutil

try:
    import numpy as np
    from numpy.typing import NDArray
    HAS_NUMPY = True
except ImportError:
    np = None
    NDArray = "NDArray"  # type: ignore[misc]
    HAS_NUMPY = False

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json

try:
    import compression.zstd as _zstd
    ZSTD_AVAILABLE = True
except (ImportError, Exception):
    ZSTD_AVAILABLE = False
    _zstd = None

# Sprint 26: Optional hnswlib for ANN search (replaces FAISS)
try:
    import hnswlib
    HNSWLIB_AVAILABLE = True
except ImportError:
    hnswlib = None
    HNSWLIB_AVAILABLE = False

# Sprint 8AA: Lazy scipy import - only loaded when NeuromorphicMemoryManager
# is actually instantiated, not at module cold-start (~227ms savings)
SCIPY_AVAILABLE = True  # assume available; verified at first use
_scipy_sparse_module = None

def _get_sparse():
    """Lazy scipy.sparse loader - defers ~227ms import cost until first use."""
    global _scipy_sparse_module
    if _scipy_sparse_module is None:
        try:
            from scipy import sparse as _sparse
            _scipy_sparse_module = _sparse
        except ImportError:
            _scipy_sparse_module = None
            globals()['SCIPY_AVAILABLE'] = False
    return _scipy_sparse_module


def _ndarray_to_list(obj: Any) -> Any:
    """Convert numpy arrays to lists for JSON serialization."""
    if HAS_NUMPY and isinstance(obj, np.ndarray):
        return obj.tolist()
    if is_dataclass(obj):
        return {k: _ndarray_to_list(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _ndarray_to_list(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ndarray_to_list(item) for item in obj]
    return obj


def _serialize_to_json(data: Any) -> bytes:
    """Serialize data to JSON bytes using orjson, compressed with zstd."""
    converted = _ndarray_to_list(data)
    payload = orjson.dumps(converted) if ORJSON_AVAILABLE else _json.dumps(converted).encode()
    if ZSTD_AVAILABLE and _zstd is not None:
        return _zstd.compress(payload)
    return payload


def _deserialize_from_json(data: bytes) -> Any:
    """Deserialize from zstd-compressed or raw JSON bytes."""
    if ZSTD_AVAILABLE and _zstd is not None:
        try:
            return orjson.loads(_zstd.decompress(data))
        except Exception:
            pass
    return orjson.loads(data) if ORJSON_AVAILABLE else _json.loads(data.decode())


logger = logging.getLogger(__name__)

# Lazy numpy wrapper - loads numpy on first use of NeuromorphicMemoryManager
# This defers numpy import until neuromorphic path is actually needed
def _get_np():
    """Return numpy module. Defined at module level for type compatibility."""
    if not HAS_NUMPY:
        return None
    return np

# Memory bounds
MAX_SIMILARITIES = 1000
MAX_PATTERNS = 2000


# =======================================================================
# Neuromorphic Memory Components
# =======================================================================

class NeuromorphicMemoryZone(Enum):
    """Memory zones for neuromorphic memory system."""
    WORKING_MEMORY = "working_memory"
    LONG_TERM_MEMORY = "long_term_memory"
    EPISODIC_BUFFER = "episodic_buffer"


@dataclass
class MemoryPattern:
    """
    A memory pattern stored in neuromorphic memory.

    Attributes:
        pattern_id: Unique identifier for the pattern
        neuron_activations: Sparse array of neuron activation values
        timestamp: Creation time
        strength: Memory strength (0.0 to 1.0)
        metadata: Additional pattern metadata
    """
    pattern_id: str
    neuron_activations: Any  # np.ndarray at runtime
    timestamp: float
    strength: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def decay(self, decay_rate: float = 0.01) -> None:
        """Apply exponential decay to memory strength."""
        self.strength *= (1.0 - decay_rate)
        self.strength = max(0.0, self.strength)

    def reinforce(self, amount: float = 0.1) -> None:
        """Reinforce memory strength (capped at 1.0)."""
        self.strength = min(1.0, self.strength + amount)


@dataclass
class STDPParameters:
    """Spike-Timing-Dependent Plasticity parameters."""
    A_plus: float = 0.01       # LTP learning rate
    A_minus: float = 0.0105    # LTD learning rate (slightly larger than LTP)
    tau_plus: float = 20.0     # LTP time constant (ms)
    tau_minus: float = 20.0    # LTD time constant (ms)
    w_max: float = 1.0         # Maximum synaptic weight
    w_min: float = 0.0         # Minimum synaptic weight


class NeuromorphicMemoryManager:
    """
    Neuromorphic memory manager with STDP learning.

    Implements brain-inspired memory storage with:
    - Sparse weight matrices for M1 8GB optimization
    - Circular buffers for memory storage
    - STDP (Spike-Timing-Dependent Plasticity) learning
    - Memory consolidation and replay
    - Aggressive cleanup for memory constraints

    M1 Optimizations:
    - Sparse scipy matrices for synaptic weights
    - Circular buffers with fixed capacity
    - Lazy numpy array creation
    - Automatic memory cleanup on threshold
    """

    def __init__(
        self,
        n_neurons: int = 1024,
        stdp_params: STDPParameters | None = None,
        working_memory_capacity: int = 100,
        long_term_capacity: int = 1000,
        connectivity: float = 0.05
    ):
        """
        Initialize neuromorphic memory manager.

        Args:
            n_neurons: Number of neurons in the network
            stdp_params: STDP learning parameters
            working_memory_capacity: Max patterns in working memory
            long_term_capacity: Max patterns in long-term memory
            connectivity: Synaptic connectivity (sparse)
        """
        self.n_neurons = n_neurons
        self.stdp_params = stdp_params or STDPParameters()
        self.connectivity = connectivity

        # Memory storage with circular buffers
        self.working_memory: deque = deque(maxlen=working_memory_capacity)
        self.long_term_memory: deque = deque(maxlen=long_term_capacity)
        self.episodic_buffer: deque = deque(maxlen=50)

        # Pattern lookup by ID
        self._patterns: dict[str, MemoryPattern] = {}

        # Sparse synaptic weight matrix (M1 optimized) - guard against missing scipy
        if _get_sparse() is not None:
            self._init_synaptic_weights()
        else:
            self.synaptic_weights = None
            logger.warning("NeuromorphicMemoryManager: scipy.sparse not available, synaptic weights disabled")

        # Spike traces for STDP (lazy numpy)
        _np = _get_np()
        self.spike_traces = _np.zeros(n_neurons) if _np else []
        self.trace_decay = 0.9

        # Sleep/replay parameters
        self.sleep_active = False
        self.replay_count = 0

        # Statistics (similarities bounded as deque)
        self.stats = {
            'patterns_stored': 0,
            'patterns_recalled': 0,
            'consolidations': 0,
            'replays': 0,
            'synaptic_updates': 0,
            'similarities': deque(maxlen=MAX_SIMILARITIES)
        }

        logger.info(
            f"NeuromorphicMemoryManager initialized: {n_neurons} neurons, "
            f"connectivity={connectivity}"
        )

    def _init_synaptic_weights(self) -> None:
        """Initialize sparse synaptic weight matrix."""
        if not HAS_NUMPY:
            logger.warning("NeuromorphicMemoryManager: numpy not available, synaptic weights disabled")
            self.synaptic_weights = None
            return

        # Create sparse random connectivity
        n_connections = int(self.n_neurons * self.n_neurons * self.connectivity)

        # Random source and target indices (lazy numpy)
        _np = _get_np()
        assert _np is not None, "numpy required for neuromorphic initialization"
        sources = _np.random.randint(0, self.n_neurons, n_connections)
        targets = _np.random.randint(0, self.n_neurons, n_connections)

        # Remove self-connections
        mask = sources != targets
        sources, targets = sources[mask], targets[mask]

        # Random initial weights
        weights = _np.random.exponential(0.1, len(sources))
        weights = _np.clip(weights, self.stdp_params.w_min, self.stdp_params.w_max)

        # Create sparse matrix in COO format then convert to CSR
        _sparse = _get_sparse()
        assert _sparse is not None, "scipy.sparse required for neuromorphic initialization"
        self.synaptic_weights = _sparse.csr_matrix(
            (weights, (sources, targets)),
            shape=(self.n_neurons, self.n_neurons)
        )

    def _encode_pattern(self, data: Any) -> Any:
        """
        Convert data to neuron activation pattern.

        Uses hash-based encoding for deterministic mapping.
        """
        import hashlib
        import json

        # Convert data to string representation
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, sort_keys=True)
        else:
            data_str = str(data)

        # Generate hash-based activation pattern (xxhash — non-cryptographic)
        try:
            from hledac_rust_extensions import content_hash_hex as _xxh64
            hash_val = _xxh64(data_str)
        except Exception:
            hash_val = hashlib.sha256(data_str.encode()).hexdigest()

        _np = _get_np()
        if not _np:
            return []  # numpy not available

        # Create sparse activation pattern (lazy numpy)
        activations = _np.zeros(self.n_neurons)

        # Use hash chunks to determine active neurons
        chunk_size = 8
        n_active = min(64, self.n_neurons // 16)  # ~6% active

        for i in range(n_active):
            chunk = hash_val[i * chunk_size:(i + 1) * chunk_size]
            neuron_idx = int(chunk, 16) % self.n_neurons
            # Activation strength based on hash value
            activations[neuron_idx] = (int(chunk, 16) / (16 ** chunk_size)) * 0.5 + 0.5

        return activations

    def _stdp_update(self, pre: int, post: int, delta_t: float) -> float:
        """
        Apply STDP learning rule.

        Args:
            pre: Presynaptic neuron index
            post: Postsynaptic neuron index
            delta_t: Time difference (post_time - pre_time)

        Returns:
            Weight change amount
        """
        _np = _get_np()
        assert _np is not None, "numpy required for STDP update"
        if delta_t > 0:
            # Long-Term Potentiation (LTP) - pre before post
            delta_w = self.stdp_params.A_plus * _np.exp(-delta_t / self.stdp_params.tau_plus)
        else:
            # Long-Term Depression (LTD) - post before pre
            delta_w = -self.stdp_params.A_minus * _np.exp(delta_t / self.stdp_params.tau_minus)

        return delta_w

    def store_pattern(
        self,
        pattern_id: str,
        data: Any,
        zone: NeuromorphicMemoryZone = NeuromorphicMemoryZone.WORKING_MEMORY
    ) -> bool:
        """
        Store a pattern in neuromorphic memory.

        Args:
            pattern_id: Unique pattern identifier
            data: Data to encode and store
            zone: Memory zone to store in

        Returns:
            True if stored successfully
        """
        # Encode data to neuron activations
        activations = self._encode_pattern(data)

        # Create memory pattern
        pattern = MemoryPattern(
            pattern_id=pattern_id,
            neuron_activations=activations,
            timestamp=time.time(),
            strength=1.0,
            metadata={'data': data, 'zone': zone.value}
        )

        # Store in appropriate zone
        if zone == NeuromorphicMemoryZone.WORKING_MEMORY:
            self.working_memory.append(pattern)
        elif zone == NeuromorphicMemoryZone.LONG_TERM_MEMORY:
            self.long_term_memory.append(pattern)
        elif zone == NeuromorphicMemoryZone.EPISODIC_BUFFER:
            self.episodic_buffer.append(pattern)

        # Update pattern lookup
        self._patterns[pattern_id] = pattern

        # FIFO eviction for bounded _patterns
        if len(self._patterns) > MAX_PATTERNS:
            try:
                oldest = next(iter(self._patterns))
                del self._patterns[oldest]
            except Exception:
                pass  # fail-safe

        # Update synaptic weights based on co-activation
        self._update_weights_from_pattern(activations)

        self.stats['patterns_stored'] += 1
        logger.debug(f"Stored pattern {pattern_id} in {zone.value}")

        return True

    def _update_weights_from_pattern(self, activations: Any) -> None:
        """Update synaptic weights based on pattern co-activation."""
        if self.synaptic_weights is None:
            return  # scipy not available, skip

        _np = _get_np()
        if _np is None:
            return  # numpy not available

        active_neurons = _np.where(activations > 0.3)[0]

        if len(active_neurons) < 2:
            return

        # Strengthen connections between co-active neurons (Hebbian learning)
        for i, pre in enumerate(active_neurons):
            for post in active_neurons[i+1:]:
                # Update weight with small increment
                current_weight = self.synaptic_weights[pre, post]
                if current_weight == 0:
                    # Create new synapse with small weight
                    new_weight = 0.05
                else:
                    # Strengthen existing synapse
                    new_weight = min(
                        self.stdp_params.w_max,
                        current_weight + self.stdp_params.A_plus
                    )

                self.synaptic_weights[pre, post] = new_weight
                self.stats['synaptic_updates'] += 1

    def recall_pattern(
        self,
        pattern_id: str,
        completion: bool = True
    ) -> dict[str, Any] | None:
        """
        Recall a pattern from memory.

        Args:
            pattern_id: Pattern identifier
            completion: Whether to perform pattern completion

        Returns:
            Pattern data with metadata, or None if not found
        """
        # Check all memory zones
        pattern = self._patterns.get(pattern_id)

        if pattern is None:
            return None

        # Reinforce memory on recall
        pattern.reinforce(0.05)

        # Pattern completion if requested
        if completion:
            completed_activations = self._pattern_completion(pattern.neuron_activations)
            pattern.neuron_activations = completed_activations

        self.stats['patterns_recalled'] += 1

        return {
            'pattern_id': pattern.pattern_id,
            'data': pattern.metadata.get('data'),
            'strength': pattern.strength,
            'timestamp': pattern.timestamp,
            'activations': pattern.neuron_activations
        }

    def _pattern_completion(self, partial_activations: Any) -> Any:
        """
        Perform pattern completion using associative memory.

        Uses synaptic weights to fill in missing activation patterns.
        """
        if self.synaptic_weights is None:
            return partial_activations  # scipy not available, return input

        # Propagate activation through synaptic network
        completed = partial_activations.copy()

        # Iterative propagation
        for _ in range(3):  # Limited iterations for efficiency
            # Sparse matrix multiplication
            propagated = self.synaptic_weights.dot(completed)
            # Combine with original (weighted average)
            completed = 0.7 * partial_activations + 0.3 * propagated
            # Apply threshold
            _np = _get_np()
            assert _np is not None, "numpy required for pattern completion"
            completed = _np.clip(completed, 0, 1)

        return completed

    def consolidate_memories(self, strength_threshold: float = 0.5) -> int:
        """
        Consolidate strong working memories to long-term memory.

        Args:
            strength_threshold: Minimum strength for consolidation

        Returns:
            Number of patterns consolidated
        """
        consolidated = 0

        # Find strong patterns in working memory
        for pattern in list(self.working_memory):
            if pattern.strength >= strength_threshold:
                # Move to long-term memory
                self.long_term_memory.append(pattern)
                pattern.metadata['zone'] = NeuromorphicMemoryZone.LONG_TERM_MEMORY.value
                consolidated += 1

        self.stats['consolidations'] += consolidated
        logger.info(f"Consolidated {consolidated} patterns to long-term memory")

        return consolidated

    def forget_weak_memories(self, threshold: float = 0.1) -> int:
        """
        Remove weak memories below threshold strength.

        Args:
            threshold: Minimum strength to keep

        Returns:
            Number of patterns forgotten
        """
        forgotten = 0

        # Check working memory
        for pattern in list(self.working_memory):
            if pattern.strength < threshold:
                self.working_memory.remove(pattern)
                if pattern.pattern_id in self._patterns:
                    del self._patterns[pattern.pattern_id]
                forgotten += 1

        # Check long-term memory (rarely forgotten)
        for pattern in list(self.long_term_memory):
            if pattern.strength < threshold * 0.5:  # Stricter for LTM
                self.long_term_memory.remove(pattern)
                if pattern.pattern_id in self._patterns:
                    del self._patterns[pattern.pattern_id]
                forgotten += 1

        logger.info(f"Forgot {forgotten} weak memories")
        return forgotten

    def _memory_replay(self, n_replays: int = 10) -> None:
        """
        Strengthen memories through replay (sleep-like consolidation).

        Args:
            n_replays: Number of memory replays
        """
        if not self.long_term_memory:
            return

        # Select random memories for replay
        memories = list(self.long_term_memory)
        n_samples = min(n_replays, len(memories))

        for _ in range(n_samples):
            _np = _get_np()
            assert _np is not None, "numpy required for memory replay"
            pattern = memories[_np.random.randint(len(memories))]
            # Strengthen memory
            pattern.reinforce(0.1)
            # Re-activate pattern
            self._update_weights_from_pattern(pattern.neuron_activations)

        self.stats['replays'] += n_samples

    async def start_sleep_replay(self, duration_seconds: float = 5.0) -> None:
        """
        Start sleep-like memory replay for consolidation.

        Args:
            duration_seconds: Duration of replay phase
        """
        self.sleep_active = True
        start_time = time.time()

        logger.info("Starting memory replay (sleep phase)")

        while self.sleep_active and (time.time() - start_time) < duration_seconds:
            self._memory_replay(n_replays=5)
            # Safe: asyncio.sleep in async def — does NOT block the event loop.
            await asyncio.sleep(0.1)

        self.sleep_active = False
        logger.info("Memory replay completed")

    async def stop_sleep_replay(self) -> None:
        """Stop sleep replay (async to allow event loop to interrupt the loop)."""
        self.sleep_active = False

    def apply_decay(self, decay_rate: float = 0.001) -> None:
        """Apply decay to all memory strengths."""
        for pattern in self.working_memory:
            pattern.decay(decay_rate)
        for pattern in self.long_term_memory:
            pattern.decay(decay_rate * 0.5)  # Slower decay for LTM

    def get_stats(self) -> dict[str, Any]:
        """Get neuromorphic memory statistics."""
        stats = {
            **self.stats,
            'working_memory_size': len(self.working_memory),
            'long_term_memory_size': len(self.long_term_memory),
            'episodic_buffer_size': len(self.episodic_buffer),
            'total_patterns': len(self._patterns),
            'n_neurons': self.n_neurons
        }
        if self.synaptic_weights is not None:
            stats['synaptic_density'] = self.synaptic_weights.nnz / (self.n_neurons ** 2)
        else:
            stats['synaptic_density'] = 0.0
        return stats

    def cleanup(self) -> None:
        """Aggressive cleanup for M1 memory constraints."""
        # Clear episodic buffer
        self.episodic_buffer.clear()

        # Forget weak memories
        self.forget_weak_memories(threshold=0.2)

        # Compact pattern storage
        active_ids = set()
        for pattern in list(self.working_memory) + list(self.long_term_memory):
            active_ids.add(pattern.pattern_id)

        # Remove orphaned patterns
        orphaned = set(self._patterns.keys()) - active_ids
        for pid in orphaned:
            del self._patterns[pid]

        logger.info(f"Neuromorphic memory cleanup: removed {len(orphaned)} orphaned patterns")


class MemoryPressureLevel(Enum):
    """Memory pressure levels for M1 8GB optimization."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


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
    # Universal priority zones (replaces former M1 Master + Universal dual system)
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class MemoryAllocation:
    """Represents a memory allocation."""
    allocation_id: str
    zone: MemoryZone
    size_bytes: int
    priority: int
    created_at: float
    last_accessed: float
    evictable: bool = True
    on_evict: Callable | None = None


@dataclass
class MemoryStatistics:
    """Memory usage statistics."""
    total_memory_mb: float
    used_memory_mb: float
    available_memory_mb: float
    peak_usage_mb: float
    current_level: MemoryPressureLevel
    cleanup_count: int
    last_cleanup_time: float
    allocation_count: int = 0


@dataclass
class ZoneStatistics:
    """Statistics for a specific memory zone."""
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

    def __init__(self, memory_limit_mb: float = 5500, enable_neuromorphic: bool = True):
        """
        Initialize memory coordinator.

        Args:
            memory_limit_mb: Memory limit in MB (default 5.5GB for M1 8GB)
            enable_neuromorphic: Whether to enable neuromorphic memory
        """
        self.memory_limit_mb = memory_limit_mb
        self.memory_limit_bytes = memory_limit_mb * 1024 * 1024

        # Allocation tracking
        self.allocations: dict[str, MemoryAllocation] = {}
        self.zone_allocations: dict[MemoryZone, OrderedDict] = {
            zone: OrderedDict() for zone in MemoryZone
        }

        # Statistics
        self.statistics = MemoryStatistics(
            total_memory_mb=psutil.virtual_memory().total / (1024 * 1024),
            used_memory_mb=0,
            available_memory_mb=0,
            peak_usage_mb=0,
            current_level=MemoryPressureLevel.NORMAL,
            cleanup_count=0,
            last_cleanup_time=0
        )

        # Callbacks and synchronization
        self.callbacks: list[Callable] = []
        # SAFETY: SAFE_SYNC_BOUNDARY — threading.Lock guards ONLY sync methods
        # (allocate, free, touch, record_cleanup, get_memory_usage, get_zone_usage).
        # All locked methods are regular def, never async def, and are called
        # exclusively from sync contexts (tests, pq_index.py, internal sync flows).
        # No await occurs inside the lock; therefore threading.Lock is correct
        # and does NOT block the asyncio event loop.
        self.lock = threading.Lock()

        # Neuromorphic memory integration
        self._neuro_memory: NeuromorphicMemoryManager | None = None
        self._neuro_enabled = enable_neuromorphic
        if enable_neuromorphic:
            self._initialize_neuromorphic_memory()

        logger.info(f"UniversalMemoryCoordinator initialized with {memory_limit_mb}MB limit")

        # Sprint 72: Thermal and power state initialization
        self._thermal_state = ThermalState.NORMAL
        self._thermal_history = deque(maxlen=10)
        self._running = True
        self._last_battery_check = 0
        self._cached_on_battery = False

    # =======================================================================
    # Sprint 72: Thermal State Monitoring
    # =======================================================================

    def _get_thermal_state_native(self) -> ThermalState | None:
        """
        Získat tepelný stav přes NSProcessInfo (PyObjC).
        Fallback na None.
        """
        try:
            from Foundation import NSProcessInfo
            thermal_state = NSProcessInfo.processInfo().thermalState
            # Mapování: 0=nominal, 1=light, 2=heavy, 3=critical
            if thermal_state == 0:
                return ThermalState.NORMAL
            elif thermal_state == 1:
                return ThermalState.WARM
            elif thermal_state == 2:
                return ThermalState.HOT
            elif thermal_state == 3:
                return ThermalState.CRITICAL
        except Exception:
            pass
        return None

    def _estimate_thermal_load(self) -> ThermalState:
        """
        Fallback – odhad podle zátěže CPU a memory pressure.
        """
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            mem_pressure = self._calculate_pressure_level()

            if cpu_percent > 90 and mem_pressure in (MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL):
                return ThermalState.CRITICAL
            elif cpu_percent > 70 and mem_pressure in (MemoryPressureLevel.ELEVATED, MemoryPressureLevel.HIGH):
                return ThermalState.HOT
            elif cpu_percent > 50 and mem_pressure == MemoryPressureLevel.ELEVATED:
                return ThermalState.WARM
            return ThermalState.NORMAL
        except Exception:
            return ThermalState.NORMAL

    def _update_thermal_state(self) -> ThermalState:
        """Aktualizuje cached thermal state."""
        native = self._get_thermal_state_native()
        if native is not None:
            return native
        return self._estimate_thermal_load()

    def get_thermal_state(self) -> ThermalState:
        return self._thermal_state

    def should_throttle(self) -> bool:
        return self._thermal_state in (ThermalState.HOT, ThermalState.CRITICAL)

    def get_thermal_trend(self) -> str:
        """Returns thermal trend (rising, stable, falling) from history."""
        if len(self._thermal_history) < 3:
            return "stable"
        last = self._thermal_history[-1][1].value
        prev = self._thermal_history[-2][1].value
        if last > prev:
            return "rising"
        elif last < prev:
            return "falling"
        return "stable"

    def get_pressure_level(self) -> str:
        """Returns memory pressure level."""
        current = self._calculate_pressure_level()
        if current == MemoryPressureLevel.CRITICAL:
            return "critical"
        elif current == MemoryPressureLevel.HIGH:
            return "high"
        elif current == MemoryPressureLevel.ELEVATED:
            return "elevated"
        return "normal"

    def get_power_state(self) -> dict:
        return {
            "on_battery": self._on_battery_power(),
            "thermal_state": self._thermal_state.name.lower(),
            "thermal_trend": self.get_thermal_trend(),
            "memory_pressure_level": self.get_pressure_level(),
            "should_throttle": self.should_throttle()
        }

    def get_reranking_context(self) -> dict:
        """Reranking context pro lancedb_store adaptive reranking.

        Narrow seam — lancedb_store NEvolá _on_battery_power() přímo.
        Tato metoda je jediný entry point pro thermal/battery awareness
        v search_similar_adaptive().
        """
        state = self.get_power_state()
        try:
            import psutil
            state["available_gb"] = psutil.virtual_memory().available / (1024**3)
        except Exception:
            state["available_gb"] = 8.0
        return state

    def _on_battery_power(self) -> bool:
        """Detekuje běh na baterii – cache s TTL."""
        now = time.time()
        if now - self._last_battery_check > 60:  # aktualizace každou minutu
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    self._cached_on_battery = not battery.power_plugged
                else:
                    # Fallback na pmset
                    import subprocess
                    result = subprocess.run(
                        ['pmset', '-g', 'batt'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    self._cached_on_battery = b'discharging' in result.stdout.lower().encode()
            except Exception:
                self._cached_on_battery = True  # conservative
            self._last_battery_check = now
        return self._cached_on_battery

    async def _thermal_monitor_loop(self):
        """Background task – aktualizuje stav každých 30s (adaptivně)."""
        while self._running:
            try:
                new_state = await asyncio.to_thread(self._update_thermal_state)

                if new_state != self._thermal_state:
                    logger.info(f"[Thermal] State changed: {self._thermal_state.value} -> {new_state.value}")
                    self._thermal_state = new_state
                    self._thermal_history.append((time.time(), new_state))

                # Adaptivní interval: při throttlingu měř častěji
                if self._thermal_state in (ThermalState.HOT, ThermalState.CRITICAL):
                    interval = 10
                else:
                    interval = 30
            except Exception as e:
                logger.debug(f"Thermal monitor error: {e}")
                interval = 60
            await asyncio.sleep(interval)

    def stop_thermal_monitor(self):
        """Zastavit thermal monitor loop (voláno při cleanup)."""
        self._running = False

    # =======================================================================
    # Neuromorphic Memory Integration
    # =======================================================================

    def _initialize_neuromorphic_memory(
        self,
        n_neurons: int = 512,  # Reduced for M1 8GB
        working_capacity: int = 50,
        long_term_capacity: int = 500
    ) -> None:
        """
        Initialize neuromorphic memory manager.

        Args:
            n_neurons: Number of neurons (default 512 for M1 optimization)
            working_capacity: Working memory pattern capacity
            long_term_capacity: Long-term memory pattern capacity
        """
        try:
            self._neuro_memory = NeuromorphicMemoryManager(
                n_neurons=n_neurons,
                working_memory_capacity=working_capacity,
                long_term_capacity=long_term_capacity,
                connectivity=0.03  # Ultra-sparse for M1
            )
            logger.info(
                f"Neuromorphic memory initialized: {n_neurons} neurons, "
                f"WM:{working_capacity}, LTM:{long_term_capacity}"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize neuromorphic memory: {e}")
            self._neuro_memory = None
            self._neuro_enabled = False

    def allocate_neuromorphic_zone(
        self,
        zone_type: NeuromorphicMemoryZone,
        size: int
    ) -> dict[str, Any]:
        """
        Allocate a neuromorphic memory zone.

        Args:
            zone_type: Type of memory zone to allocate
            size: Number of patterns the zone should hold

        Returns:
            Allocation result with zone info
        """
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}

        # Adjust deque maxlen for the zone
        if zone_type == NeuromorphicMemoryZone.WORKING_MEMORY:
            self._neuro_memory.working_memory = deque(
                self._neuro_memory.working_memory,
                maxlen=size
            )
        elif zone_type == NeuromorphicMemoryZone.LONG_TERM_MEMORY:
            self._neuro_memory.long_term_memory = deque(
                self._neuro_memory.long_term_memory,
                maxlen=size
            )
        elif zone_type == NeuromorphicMemoryZone.EPISODIC_BUFFER:
            self._neuro_memory.episodic_buffer = deque(
                self._neuro_memory.episodic_buffer,
                maxlen=size
            )

        return {
            'success': True,
            'zone': zone_type.value,
            'size': size,
            'neurons': self._neuro_memory.n_neurons
        }

    def store_neural_pattern(
        self,
        zone: NeuromorphicMemoryZone,
        pattern_id: str,
        data: Any
    ) -> dict[str, Any]:
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
            return {
                'success': success,
                'pattern_id': pattern_id,
                'zone': zone.value,
                'timestamp': time.time()
            }
        except Exception as e:
            logger.error(f"Failed to store neural pattern: {e}")
            return {'success': False, 'error': str(e)}

    def recall_neural_pattern(
        self,
        zone: NeuromorphicMemoryZone,
        pattern_id: str,
        completion: bool = True
    ) -> dict[str, Any]:
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
                return {
                    'success': True,
                    'pattern': result,
                    'zone': zone.value
                }
            else:
                return {
                    'success': False,
                    'error': f'Pattern {pattern_id} not found',
                    'zone': zone.value
                }
        except Exception as e:
            logger.error(f"Failed to recall neural pattern: {e}")
            return {'success': False, 'error': str(e)}

    def consolidate_neural_memories(
        self,
        strength_threshold: float = 0.5
    ) -> dict[str, Any]:
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

            # Also run memory replay for sleep-like consolidation
            self._neuro_memory._memory_replay(n_replays=min(count, 20))

            return {
                'success': True,
                'consolidated_count': count,
                'working_memory_size': len(self._neuro_memory.working_memory),
                'long_term_memory_size': len(self._neuro_memory.long_term_memory)
            }
        except Exception as e:
            logger.error(f"Failed to consolidate neural memories: {e}")
            return {'success': False, 'error': str(e)}

    def get_neuromorphic_stats(self) -> dict[str, Any]:
        """Get neuromorphic memory statistics."""
        if not self._neuro_memory:
            return {'enabled': False}

        return {
            'enabled': True,
            **self._neuro_memory.get_stats()
        }

    def cleanup_neuromorphic_memory(self) -> dict[str, Any]:
        """Perform aggressive cleanup of neuromorphic memory."""
        if not self._neuro_memory:
            return {'success': False, 'error': 'Neuromorphic memory not initialized'}

        forgotten = self._neuro_memory.forget_weak_memories(threshold=0.2)
        self._neuro_memory.cleanup()

        return {
            'success': True,
            'forgotten_patterns': forgotten,
            'remaining_patterns': len(self._neuro_memory._patterns)
        }

    # ========================================================================
    # Allocation Management
    # ========================================================================

    def allocate(
        self,
        allocation_id: str,
        zone: MemoryZone,
        size_bytes: int,
        priority: int = 5,
        evictable: bool = True,
        on_evict: Callable | None = None
    ) -> bool:
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
        with self.lock:
            if allocation_id in self.allocations:
                logger.warning(f"Allocation {allocation_id} already exists")
                return False

            available = self._get_available_memory()
            if size_bytes > available:
                logger.warning(
                    f"Not enough memory for {allocation_id}: "
                    f"{size_bytes} > {available}"
                )
                # Try to handle pressure
                if not self._handle_memory_pressure(size_bytes - available):
                    return False

            allocation = MemoryAllocation(
                allocation_id=allocation_id,
                zone=zone,
                size_bytes=size_bytes,
                priority=priority,
                created_at=time.time(),
                last_accessed=time.time(),
                evictable=evictable,
                on_evict=on_evict
            )

            self.allocations[allocation_id] = allocation
            self.zone_allocations[zone][allocation_id] = allocation

            logger.debug(
                f"Allocated {allocation_id} in zone {zone.value}: "
                f"{size_bytes} bytes"
            )
            return True

    def free(self, allocation_id: str) -> bool:
        """
        Free memory allocation.

        Args:
            allocation_id: Allocation ID to free

        Returns:
            True if allocation was freed
        """
        with self.lock:
            if allocation_id not in self.allocations:
                return False

            allocation = self.allocations[allocation_id]

            if allocation_id in self.zone_allocations[allocation.zone]:
                del self.zone_allocations[allocation.zone][allocation_id]

            del self.allocations[allocation_id]

            logger.debug(f"Freed allocation {allocation_id}")
            return True

    def touch(self, allocation_id: str) -> None:
        """
        Update last accessed time for allocation.
        Moves allocation to end of zone (LRU).

        Args:
            allocation_id: Allocation ID to touch
        """
        with self.lock:
            if allocation_id in self.allocations:
                allocation = self.allocations[allocation_id]
                allocation.last_accessed = time.time()

                zone = allocation.zone
                if allocation_id in self.zone_allocations[zone]:
                    self.zone_allocations[zone].move_to_end(allocation_id)

    # ========================================================================
    # Memory Cleanup
    # ========================================================================

    def aggressive_cleanup(self) -> dict[str, Any]:
        """
        Perform aggressive garbage collection and MLX cache clearing.

        Returns:
            Cleanup results
        """
        logger.info("🧹 Performing aggressive cleanup...")

        results = {
            "mlx_cache_cleared": False,
            "gc_collections": 0,
            "weakref_collected": 0,
            "neuromorphic_cleaned": False,
            "success": False
        }

        try:
            # Clear MLX cache via canonical mlx_memory adapter
            try:
                from hledac.utils.mlx_memory import clear_mlx_cache as _clear_mlx_cache
                results["mlx_cache_cleared"] = _clear_mlx_cache()
                if results["mlx_cache_cleared"]:
                    logger.info("✓ MLX cache cleared")
            except ImportError:
                logger.debug("mlx_memory not available, skipping MLX cache clear")

            # Cleanup neuromorphic memory
            if self._neuro_memory:
                neuro_result = self.cleanup_neuromorphic_memory()
                results["neuromorphic_cleaned"] = neuro_result.get('success', False)
                results["neuromorphic_forgotten"] = neuro_result.get('forgotten_patterns', 0)
                logger.info("✓ Neuromorphic memory cleaned")

            # Aggressive GC
            gc.collect()
            results["gc_collections"] += 1

            # Full collection
            gc.collect(2)
            results["gc_collections"] += 1

            # Weakref collection
            try:
                results["weakref_collected"] = weakref.collect()
            except Exception:
                pass

            # Additional GC passes
            for _ in range(3):
                gc.collect()
                results["gc_collections"] += 1

            self.record_cleanup("aggressive_cleanup")
            results["success"] = True
            logger.info("✓ Aggressive cleanup complete")

        except Exception as e:
            logger.error(f"Error during aggressive cleanup: {e}")
            results["error"] = str(e)

        return results

    async def cleanup(self, level: MemoryPressureLevel | None = None) -> bool:
        """
        Async cleanup with zone-based eviction.

        Args:
            level: Cleanup level (None = use current pressure)

        Returns:
            True if anything was released
        """
        if level is None:
            level = self.get_memory_usage().current_level

        logger.info(f"Memory cleanup triggered: {level.value}")

        released = False

        # Universal zones cleanup
        if level in [MemoryPressureLevel.ELEVATED, MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL]:
            # Release LOW zone
            released |= self.clear_zone(MemoryZone.LOW) > 0

        if level in [MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL]:
            # Release MEDIUM zone
            released |= self.clear_zone(MemoryZone.MEDIUM) > 0

        # HIGH zone cleanup (for CRITICAL only)
        if level == MemoryPressureLevel.CRITICAL:
            released |= self.clear_zone(MemoryZone.HIGH) > 0

        # Aggressive cleanup
        cleanup_result = self.aggressive_cleanup()
        released |= cleanup_result["success"]

        return released

    def clear_zone(self, zone: MemoryZone) -> int:
        """
        Clear all evictable allocations in a zone.

        Args:
            zone: Zone to clear

        Returns:
            Number of allocations cleared
        """
        with self.lock:
            allocations = list(self.zone_allocations[zone].keys())
            count = 0

            for allocation_id in allocations:
                allocation = self.allocations.get(allocation_id)
                if allocation and allocation.evictable:
                    # Call eviction callback
                    if allocation.on_evict:
                        try:
                            allocation.on_evict()
                        except Exception as e:
                            logger.error(
                                f"Eviction callback error for {allocation_id}: {e}"
                            )

                    self.free(allocation_id)
                    count += 1

            if count > 0:
                logger.info(f"Cleared {count} allocations from zone {zone.value}")
            return count

    def record_cleanup(self, component: str) -> None:
        """
        Record a cleanup event.

        Args:
            component: Component that performed cleanup
        """
        with self.lock:
            self.statistics.cleanup_count += 1
            self.statistics.last_cleanup_time = time.time()
            logger.info(
                f"Cleanup recorded for {component} "
                f"(total: {self.statistics.cleanup_count})"
            )

    # ========================================================================
    # Memory Statistics
    # ========================================================================

    def get_memory_usage(self) -> MemoryStatistics:
        """
        Get current memory usage statistics.

        Returns:
            MemoryStatistics object
        """
        vm = psutil.virtual_memory()
        process = psutil.Process()

        with self.lock:
            used_mb = process.memory_info().rss / (1024 * 1024)
            self.statistics.used_memory_mb = used_mb
            self.statistics.available_memory_mb = vm.available / (1024 * 1024)

            if used_mb > self.statistics.peak_usage_mb:
                self.statistics.peak_usage_mb = used_mb

            self.statistics.current_level = self._calculate_pressure_level()
            self.statistics.allocation_count = len(self.allocations)

            return MemoryStatistics(
                total_memory_mb=vm.total / (1024 * 1024),
                used_memory_mb=used_mb,
                available_memory_mb=vm.available / (1024 * 1024),
                peak_usage_mb=self.statistics.peak_usage_mb,
                current_level=self.statistics.current_level,
                cleanup_count=self.statistics.cleanup_count,
                last_cleanup_time=self.statistics.last_cleanup_time,
                allocation_count=len(self.allocations)
            )

    def get_zone_usage(self, zone: MemoryZone) -> ZoneStatistics:
        """
        Get memory usage for a specific zone.

        Args:
            zone: Zone to query

        Returns:
            ZoneStatistics object
        """
        with self.lock:
            allocations = list(self.zone_allocations[zone].values())
            total_bytes = sum(a.size_bytes for a in allocations)
            evictable = sum(1 for a in allocations if a.evictable)

            return ZoneStatistics(
                zone=zone.value,
                allocation_count=len(allocations),
                total_bytes=total_bytes,
                total_mb=total_bytes / (1024 * 1024),
                evictable_count=evictable,
                non_evictable_count=len(allocations) - evictable
            )

    def get_all_zone_usage(self) -> dict[str, ZoneStatistics]:
        """Get usage for all zones."""
        return {
            zone.value: self.get_zone_usage(zone)
            for zone in MemoryZone
        }

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive memory statistics."""
        stats = self.get_memory_usage()
        result = {
            "total_mb": stats.total_memory_mb,
            "used_mb": stats.used_memory_mb,
            "available_mb": stats.available_memory_mb,
            "peak_mb": stats.peak_usage_mb,
            "percent": (stats.used_memory_mb / stats.total_memory_mb) * 100,
            "limit_mb": self.memory_limit_mb,
            "pressure": stats.current_level.value,
            "allocations": stats.allocation_count,
            "cleanups": stats.cleanup_count,
            "zones": {
                zone.value: self.get_zone_usage(zone).__dict__
                for zone in MemoryZone
            }
        }

        # Add neuromorphic memory stats if enabled
        if self._neuro_memory:
            result["neuromorphic"] = self.get_neuromorphic_stats()

        return result

    # ========================================================================
    # Callbacks
    # ========================================================================

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
                logger.error(f"Callback error: {e}")

    # ========================================================================
    # Internal Methods
    # ========================================================================

    def _get_available_memory(self) -> int:
        """Get available memory in bytes."""
        vm = psutil.virtual_memory()
        return int(vm.available)

    def _handle_memory_pressure(self, required_bytes: int) -> bool:
        """
        Handle memory pressure by evicting allocations.

        Args:
            required_bytes: Required memory in bytes

        Returns:
            True if enough memory was freed
        """
        logger.warning(f"Handling memory pressure, need {required_bytes} bytes")

        with self.lock:
            # Get evictable allocations sorted by priority and access time
            evictable = [
                a for a in self.allocations.values()
                if a.evictable
            ]
            evictable.sort(key=lambda a: (a.priority, a.last_accessed))

            freed_bytes = 0
            for allocation in evictable:
                if freed_bytes >= required_bytes:
                    break

                # Call eviction callback
                if allocation.on_evict:
                    try:
                        allocation.on_evict()
                    except Exception as e:
                        logger.error(f"Eviction callback error: {e}")

                self.free(allocation.allocation_id)
                freed_bytes += allocation.size_bytes

                logger.debug(
                    f"Evicted {allocation.allocation_id} "
                    f"({allocation.size_bytes} bytes)"
                )

            logger.info(f"Freed {freed_bytes} bytes via eviction")
            return freed_bytes >= required_bytes

    def _calculate_pressure_level(self) -> MemoryPressureLevel:
        """Calculate current memory pressure level."""
        usage_ratio = self.statistics.used_memory_mb / self.memory_limit_mb

        if usage_ratio < 0.6:
            return MemoryPressureLevel.NORMAL
        elif usage_ratio < 0.8:
            return MemoryPressureLevel.ELEVATED
        elif usage_ratio < 0.9:
            return MemoryPressureLevel.HIGH
        else:
            return MemoryPressureLevel.CRITICAL

    def check_pressure(self) -> MemoryPressureLevel:
        """
        Check current memory pressure level.

        Returns:
            Current pressure level
        """
        return self.get_memory_usage().current_level

    def register_object(self, obj: Any, zone: MemoryZone = MemoryZone.MEDIUM) -> None:
        """
        Register an object to a zone (simplified API).

        Args:
            obj: Object to register
            zone: Zone to register in
        """
        # Create allocation ID from object id
        allocation_id = f"obj_{id(obj)}_{zone.value}"

        # Estimate size (simplified)
        import sys
        try:
            size = sys.getsizeof(obj)
        except Exception:
            size = 1024  # Default 1KB

        self.allocate(
            allocation_id=allocation_id,
            zone=zone,
            size_bytes=size,
            priority=5,
            evictable=(zone in [MemoryZone.LOW, MemoryZone.MEDIUM])
        )

    # ========================================================================
    # FastFilter Integration (from tools/preserved_logic/fast_filter.py)
    # ========================================================================

    def create_url_filter(
        self,
        use_binary_fuse: bool = True,
        cache_size: int = 1000
    ) -> dict[str, Any]:
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
            from hledac.tools.preserved_logic.fast_filter import FastFilter

            filter_instance = FastFilter(
                use_bff=use_binary_fuse,
                enable_cache=True
            )

            # Store in coordinator's registry
            filter_id = f"url_filter_{id(filter_instance)}"
            if not hasattr(self, '_filters'):
                self._filters = {}
            self._filters[filter_id] = filter_instance

            return {
                'success': True,
                'filter_id': filter_id,
                'type': 'FastFilter',
                'binary_fuse_available': filter_instance.is_bff_available(),
                'default_blocked_domains': len(FastFilter.DEFAULT_BLOCKED_DOMAINS),
                'cache_enabled': True,
                'cache_size': cache_size
            }

        except ImportError:
            logger.warning("FastFilter not available")
            return {'success': False, 'error': 'FastFilter module not available'}
        except Exception as e:
            logger.error(f"Failed to create URL filter: {e}")
            return {'success': False, 'error': str(e)}

    def check_url_allowed(
        self,
        filter_id: str,
        url: str
    ) -> dict[str, Any]:
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

            return {
                'success': True,
                'url': url,
                'allowed': allowed,
                'blocked': not allowed,
                'filter_stats': stats
            }

        except Exception as e:
            logger.error(f"URL check failed: {e}")
            return {'success': False, 'error': str(e), 'allowed': True}

    def add_blocked_urls(
        self,
        filter_id: str,
        urls: list[str],
        domains: list[str] | None = None,
        patterns: list[str] | None = None
    ) -> dict[str, Any]:
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

            return {
                'success': True,
                'added_count': added_count,
                'total_blocked': filter_instance._set_filter.size() if filter_instance._set_filter else 0
            }

        except Exception as e:
            logger.error(f"Failed to add blocked items: {e}")
            return {'success': False, 'error': str(e)}

    # ========================================================================
    # FastLang Integration (from tools/preserved_logic/fast_lang.py)
    # ========================================================================

    def detect_language(
        self,
        text: str,
        min_length: int = 10,
        fallback: bool = True
    ) -> dict[str, Any]:
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
            from hledac.tools.preserved_logic.fast_lang import LanguageDetector

            detector = LanguageDetector(fallback_mode=fallback)
            lang_code = detector.detect(text, min_length=min_length)
            lang_name = detector.get_language_name(lang_code)

            return {
                'success': True,
                'language_code': lang_code,
                'language_name': lang_name,
                'supported': detector.is_supported(lang_code),
                'text_length': len(text),
                'min_length': min_length
            }

        except ImportError:
            logger.warning("LanguageDetector not available")
            return {
                'success': False,
                'error': 'LanguageDetector not available',
                'language_code': 'unknown',
                'language_name': 'Unknown'
            }
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'language_code': 'unknown'
            }

    def batch_detect_languages(
        self,
        texts: list[str],
        min_length: int = 10
    ) -> dict[str, Any]:
        """
        Detect languages for multiple texts.

        Args:
            texts: List of texts to analyze
            min_length: Minimum text length for detection

        Returns:
            Batch detection results
        """
        try:
            from hledac.tools.preserved_logic.fast_lang import LanguageDetector

            detector = LanguageDetector()
            results = detector.batch_detect(texts, min_length=min_length)

            # Count languages
            lang_counts = {}
            for lang in results:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

            return {
                'success': True,
                'total_texts': len(texts),
                'results': [
                    {
                        'text_preview': text[:50] + "..." if len(text) > 50 else text,
                        'language_code': lang,
                        'language_name': detector.get_language_name(lang)
                    }
                    for text, lang in zip(texts, results, strict=False)
                ],
                'language_distribution': lang_counts
            }

        except Exception as e:
            logger.error(f"Batch language detection failed: {e}")
            return {'success': False, 'error': str(e)}

    def filter_by_language(
        self,
        texts: list[Any],
        allowed_languages: list[str]
    ) -> dict[str, Any]:
        """
        Filter texts by allowed languages.

        Args:
            texts: List of texts or (text, metadata) tuples
            allowed_languages: List of allowed language codes (e.g., ['en', 'cs'])

        Returns:
            Filtered results
        """
        try:
            from hledac.tools.preserved_logic.fast_lang import LanguageDetector

            detector = LanguageDetector()
            filtered = detector.filter_by_language(texts, allowed_languages)

            return {
                'success': True,
                'total_input': len(texts),
                'filtered_count': len(filtered),
                'allowed_languages': allowed_languages,
                'filtered_items': filtered
            }

        except Exception as e:
            logger.error(f"Language filtering failed: {e}")
            return {'success': False, 'error': str(e)}


# ========================================================================
# Context Optimization Integration (from context_optimization/)
# ========================================================================

class ContextPriority(Enum):
    """Priority levels for context items."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = "data_collection"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    VALIDATION = "validation"


@dataclass
class ContextItem:
    """Individual context item with metadata for three-tier storage."""
    item_id: str
    content: str
    metadata: dict[str, Any]
    tokens: int
    priority: ContextPriority
    access_count: int
    last_accessed: float
    embedding: Any | None = None
    content_type: str = "general"
    confidence: float = 0.5


@dataclass
class CompressedContext:
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

    def __init__(
        self,
        max_hot_tokens: int = 20_000,
        max_warm_tokens: int = 40_000,
        storage_path: str = "./context_cache",
        enable_embeddings: bool = False
    ):
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

        # Three-tier storage
        self.hot_context: dict[str, ContextItem] = {}
        self.warm_context: dict[str, ContextItem] = {}
        self.cold_storage: dict[str, ContextItem] = {}

        # Token tracking
        self.hot_tokens = 0
        self.warm_tokens = 0

        # Embedding support (optional)
        self.enable_embeddings = enable_embeddings
        self.embedder = None
        self.embedding_dim = 384

        if enable_embeddings:
            self._initialize_embedder()

        # Statistics
        self.stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'promotions': 0,
            'compressions': 0,
            'total_requests': 0
        }

        # Phase-based weights
        self.phase_weights = {
            ResearchPhase.DATA_COLLECTION: {'data_source': 0.9, 'research': 0.7},
            ResearchPhase.ANALYSIS: {'analysis': 0.9, 'insight': 0.8},
            ResearchPhase.SYNTHESIS: {'synthesis': 0.9, 'summary': 0.8},
            ResearchPhase.VALIDATION: {'validation': 0.9, 'evidence': 0.7}
        }

        logger.info(f"ContextOptimizationManager initialized (hot: {max_hot_tokens}, warm: {max_warm_tokens})")

    def _initialize_embedder(self):
        """Initialize FastEmbed embedder (optional)."""
        try:
            from fastembed import TextEmbedding
            # Use ModernBERT instead of deprecated all-MiniLM
            self.embedder = TextEmbedding(
                model_name="nomic-ai/nomic-embed-text-v1.5",
                cache_dir=str(self.storage_path / "embeddings"),
                threads=2  # Low for M1
            )
            self.embedding_dim = self.embedder.dim
            logger.info("FastEmbed initialized for semantic search")
        except ImportError:
            logger.warning("FastEmbed not available, semantic search disabled")
            self.enable_embeddings = False

    def add_context(
        self,
        item_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        priority: ContextPriority = ContextPriority.MEDIUM,
        phase: ResearchPhase = ResearchPhase.DATA_COLLECTION
    ) -> bool:
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
        tokens = len(content.split())  # Simple tokenization

        # Calculate phase-adjusted priority
        content_type = metadata.get('type', 'general')
        phase_weight = self.phase_weights.get(phase, {}).get(content_type, 0.5)

        item = ContextItem(
            item_id=item_id,
            content=content,
            metadata=metadata,
            tokens=tokens,
            priority=priority,
            access_count=0,
            last_accessed=time.time(),
            content_type=content_type,
            confidence=metadata.get('confidence', 0.5)
        )

        # Determine tier based on priority and phase
        if priority == ContextPriority.HIGH or phase_weight > 0.8:
            # Hot storage
            if self.hot_tokens + tokens > self.max_hot_tokens:
                self._evict_from_hot(tokens)
            self.hot_context[item_id] = item
            self.hot_tokens += tokens
        elif priority == ContextPriority.MEDIUM or phase_weight > 0.5:
            # Warm storage
            if self.warm_tokens + tokens > self.max_warm_tokens:
                self._evict_from_warm(tokens)
            self.warm_context[item_id] = item
            self.warm_tokens += tokens
        else:
            # Cold storage (persist to disk)
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

        # Check hot storage first
        if item_id in self.hot_context:
            item = self.hot_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            return item.content

        # Check warm storage
        if item_id in self.warm_context:
            item = self.warm_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_hot(item)
            self.stats['hits'] += 1
            return item.content

        # Check cold storage
        if item_id in self.cold_storage:
            item = self.cold_storage[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_warm(item)
            self.stats['hits'] += 1
            return item.content

        self.stats['misses'] += 1
        return None

    def compress_context(
        self,
        context_id: str,
        content: str,
        compression_level: int = 3
    ) -> CompressedContext:
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
            compressed = lz4.frame.compress(
                content.encode('utf-8'),
                compression_level=compression_level
            )
            compressed_size = len(compressed)

            # Create summaries at different levels
            words = content.split()
            critical = ' '.join(words[:50]) if len(words) > 50 else content
            important = ' '.join(words[:100]) if len(words) > 100 else content
            abstract = ' '.join(words[:20]) if len(words) > 20 else content

            result = CompressedContext(
                context_id=context_id,
                original_size=original_size,
                compressed_size=compressed_size,
                compression_ratio=original_size / max(compressed_size, 1),
                critical_content=critical,
                important_summary=important,
                abstract_summary=abstract,
                full_compressed=compressed,
                metadata={'compression_level': compression_level},
                timestamp=time.time()
            )

            self.stats['compressions'] += 1
            return result

        except ImportError:
            logger.warning("LZ4 not available, returning uncompressed")
            return CompressedContext(
                context_id=context_id,
                original_size=len(content.encode('utf-8')),
                compressed_size=len(content.encode('utf-8')),
                compression_ratio=1.0,
                critical_content=content[:200],
                important_summary=content[:500],
                abstract_summary=content[:100],
                full_compressed=content.encode('utf-8'),
                metadata={},
                timestamp=time.time()
            )

    def decompress_context(self, compressed: CompressedContext, detail_level: str = "important") -> str:
        """
        Decompress context at specified detail level.

        Args:
            compressed: CompressedContext object
            detail_level: 'critical', 'important', or 'abstract'

        Returns:
            Decompressed content
        """
        if detail_level == "critical":
            return compressed.critical_content
        elif detail_level == "abstract":
            return compressed.abstract_summary
        else:
            # Full decompression
            try:
                import lz4.frame
                return lz4.frame.decompress(compressed.full_compressed).decode('utf-8')
            except Exception:
                return compressed.important_summary

    def _evict_from_hot(self, required_tokens: int):
        """Evict items from hot storage to make room."""
        items = sorted(
            self.hot_context.items(),
            key=lambda x: (x[1].priority.value, x[1].last_accessed)
        )

        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.hot_context[item_id]
            self.hot_tokens -= item.tokens
            freed += item.tokens

            # Move to warm
            if self.warm_tokens + item.tokens <= self.max_warm_tokens:
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens
            else:
                self._evict_from_warm(item.tokens)
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens

        self.stats['evictions'] += 1

    def _evict_from_warm(self, required_tokens: int):
        """Evict items from warm storage to cold storage."""
        items = sorted(
            self.warm_context.items(),
            key=lambda x: (x[1].priority.value, x[1].last_accessed)
        )

        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.warm_context[item_id]
            self.warm_tokens -= item.tokens
            freed += item.tokens

            # Move to cold
            self.cold_storage[item_id] = item
            self._persist_to_disk(item)

    def _promote_to_hot(self, item: ContextItem):
        """Promote item from warm to hot storage."""
        if item.tokens > self.max_hot_tokens:
            return  # Too big for hot

        if self.hot_tokens + item.tokens > self.max_hot_tokens:
            self._evict_from_hot(item.tokens)

        if item.item_id in self.warm_context:
            del self.warm_context[item.item_id]
            self.warm_tokens -= item.tokens

        self.hot_context[item.item_id] = item
        self.hot_tokens += item.tokens
        self.stats['promotions'] += 1

    def _promote_to_warm(self, item: ContextItem):
        """Promote item from cold to warm storage."""
        if item.tokens > self.max_warm_tokens:
            return  # Too big for warm

        if self.warm_tokens + item.tokens > self.max_warm_tokens:
            self._evict_from_warm(item.tokens)

        if item.item_id in self.cold_storage:
            del self.cold_storage[item.item_id]

        self.warm_context[item.item_id] = item
        self.warm_tokens += item.tokens
        self.stats['promotions'] += 1

    def _persist_to_disk(self, item: ContextItem):
        """Persist item to disk storage."""
        file_path = self.storage_path / f"{item.item_id}.json"
        try:
            with open(file_path, 'wb') as f:
                f.write(_serialize_to_json(item))
        except Exception as e:
            logger.error(f"Failed to persist {item.item_id}: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get context optimization statistics."""
        return {
            **self.stats,
            'hot_items': len(self.hot_context),
            'warm_items': len(self.warm_context),
            'cold_items': len(self.cold_storage),
            'hot_tokens': self.hot_tokens,
            'warm_tokens': self.warm_tokens,
            'hit_rate': self.stats['hits'] / max(self.stats['total_requests'], 1)
        }


# ========================================================================
# Multi-Level Context Cache (from context_optimization/context_cache.py)
# ========================================================================

class CacheType(Enum):
    """Types of cache entries."""
    SEMANTIC = "semantic"
    COMPUTATION = "computation"
    QUERY = "query"


class CacheLocation(Enum):
    """Cache location levels."""
    L1_MEMORY = "l1_memory"
    L2_DISK = "l2_disk"


@dataclass
class CacheEntry:
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

    def __init__(
        self,
        embedding_model: str = "nomic-ai/nomic-embed-text-v1.5",
        l1_max_size_mb: float = 100.0,
        l2_storage_path: str = "cache_storage",
        similarity_threshold: float = 0.95,
        max_entries: int = 10000
    ):
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

        # Embedding model
        self.embedder = None
        self.embedding_dim = 384
        self._initialize_embedder()

        # Multi-level storage
        self.l1_cache: Ordereddict[str, CacheEntry] = OrderedDict()
        self.l2_cache: dict[str, CacheEntry] = {}

        # FAISS semantic index
        try:
            import faiss
            self.semantic_index = faiss.IndexFlatIP(self.embedding_dim)
            self.faiss_available = True
        except ImportError:
            logger.warning("FAISS not available, semantic search disabled")
            self.semantic_index = None
            self.faiss_available = False

        # Sprint 26: hnswlib for approximate nearest neighbor search
        self._hnsw_index = None
        self._hnsw_max_elements = 10000
        self._hnsw_m = 16
        self._hnsw_ef_construction = 200
        self._hnsw_ef_search = 50
        if HNSWLIB_AVAILABLE:
            self._init_hnsw()

        self.embedding_to_cache_id: dict[int, str] = {}

        # Statistics
        self.stats = {
            "hits": 0,
            "misses": 0,
            "total_requests": 0,
            "l1_promotions": 0,
            "l2_demotions": 0,
            "evictions": 0,
            "similarities": []
        }

        # Thread safety — asyncio.Lock because all callers are async.
        # Sync boundary methods use self.lock (threading.Lock, line 765) instead.
        self._lock: asyncio.Lock = asyncio.Lock()

        # Load existing L2 cache
        self._load_l2_cache()
        self._rebuild_semantic_index()

    def _init_hnsw(self) -> None:
        """Initialize hnswlib index for approximate nearest neighbor search (Sprint 26)."""
        if not HNSWLIB_AVAILABLE:
            return
        try:
            self._hnsw_index = hnswlib.Index(space='cosine', dim=self.embedding_dim)
            self._hnsw_index.init_index(
                max_elements=self._hnsw_max_elements,
                ef_construction=self._hnsw_ef_construction,
                M=self._hnsw_m
            )
            self._hnsw_index.set_ef(self._hnsw_ef_search)
            logger.debug("HNSW index initialized")
        except Exception as e:
            logger.warning(f"HNSW index initialization failed: {e}")
            self._hnsw_index = None

    def _hnsw_search(self, query_emb: Any, k: int) -> list[int]:
        """Search hnsw index for approximate nearest neighbors (Sprint 26)."""
        if self._hnsw_index is None:
            return []
        try:
            labels, distances = self._hnsw_index.knn_query(query_emb, k=k)
            return list(labels[0])
        except Exception:
            return []

    def _initialize_embedder(self):
        """Initialize FastEmbed embedder."""
        try:
            from fastembed import TextEmbedding
            self.embedder = TextEmbedding(
                model_name=self.embedding_model,
                cache_dir=str(self.l2_storage_path / "embeddings"),
                threads=2
            )
            self.embedding_dim = self.embedder.dim
            logger.info(f"Cache embedder loaded: {self.embedding_dim}d")
        except ImportError:
            logger.warning("FastEmbed not available")
            self.embedder = None

    def _load_l2_cache(self):
        """Load L2 cache from disk. Prefer zstd-compressed .json.zst, fallback to .json."""
        try:
            zst_file = self.l2_storage_path / "l2_cache.json.zst"
            json_file = self.l2_storage_path / "l2_cache.json"
            if zst_file.exists():
                cache_bytes = f.read()
                # 50MB limit for L2 cache load
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning(
                        "L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh",
                        len(cache_bytes) // (1024 * 1024)
                    )
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_from_json(cache_bytes)
                logger.info(f"Loaded {len(self.l2_cache)} entries from L2 cache (.zst)")
            elif json_file.exists():
                cache_bytes = f.read()
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning(
                        "L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh",
                        len(cache_bytes) // (1024 * 1024)
                    )
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_from_json(cache_bytes)
                logger.info(f"Loaded {len(self.l2_cache)} entries from L2 cache (.json legacy)")
            else:
                self.l2_cache = {}
        except Exception as e:
            logger.warning(f"Could not load L2 cache: {e}")
            self.l2_cache = {}

    def _save_l2_cache(self):
        """Save L2 cache to disk as zstd-compressed .json.zst."""
        try:
            cache_file = self.l2_storage_path / "l2_cache.json.zst"
            with open(cache_file, 'wb') as f:
                f.write(_serialize_to_json(self.l2_cache))
        except Exception as e:
            logger.warning(f"Could not save L2 cache: {e}")

    def _rebuild_semantic_index(self):
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
            logger.warning(f"Could not rebuild semantic index: {e}")

    def _get_embedding(self, text: str) -> Any | None:
        """Get embedding for text."""
        if self.embedder:
            try:
                embeddings = list(self.embedder.embed([text]))
                if embeddings:
                    return np.array(embeddings[0])
            except Exception as e:
                logger.debug(f"Embedding failed: {e}")
        return None

    async def get(
        self,
        input_data: Any,
        cache_type: CacheType = CacheType.COMPUTATION,
        threshold: float | None = None
    ) -> Any | None:
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

        self.stats["total_requests"] += 1

        input_text = str(input_data)

        # Check semantic cache for similar entries
        similar_entry = await self._find_similar_entry(input_text, threshold)

        if similar_entry:
            async with self._lock:
                self.stats["hits"] += 1
                self._update_access(similar_entry.cache_id)

                # Promote to L1 if in L2
                if similar_entry.cache_id in self.l2_cache:
                    self._promote_to_l1(similar_entry.cache_id)

            return similar_entry.content

            self.stats["misses"] += 1
            return None

    async def _find_similar_entry(
        self,
        input_text: str,
        threshold: float
    ) -> CacheEntry | None:
        """Find semantically similar cache entry using hnswlib (Sprint 26) or FAISS fallback."""
        # Sprint 26: Prefer hnswlib for ANN search
        if self._hnsw_index is not None:
            return await self._find_similar_entry_hnsw(input_text, threshold)

        # Fallback to FAISS
        if not self.faiss_available or self.semantic_index is None:
            return None

        input_embedding = self._get_embedding(input_text)
        if input_embedding is None:
            return None

        try:
            # Search for similar embeddings
            query_embedding = input_embedding.reshape(1, -1).astype('float32')
            D, I = self.semantic_index.search(query_embedding, 10)

            # Check if any similarity meets threshold
            for idx, similarity in zip(I[0], D[0], strict=False):
                if float(similarity) >= threshold:
                    cache_id = self.embedding_to_cache_id.get(int(idx))
                    if not cache_id:
                        continue

                    # Get entry from L1 or L2
                    entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                    if entry:
                        async with self._lock:
                            self.stats["similarities"].append(float(similarity))
                        return entry
        except Exception as e:
            logger.debug(f"Similarity search failed: {e}")

        return None

    async def _find_similar_entry_hnsw(
        self,
        input_text: str,
        threshold: float
    ) -> CacheEntry | None:
        """Find semantically similar cache entry using hnswlib (Sprint 26)."""
        input_embedding = self._get_embedding(input_text)
        if input_embedding is None:
            return None

        try:
            # Search using hnswlib
            indices = self._hnsw_search(input_embedding, k=10)

            for idx in indices:
                cache_id = self.embedding_to_cache_id.get(int(idx))
                if not cache_id:
                    continue

                # Get entry from L1 or L2
                entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                if entry:
                    # Compute similarity (hnswlib returns distances, convert to similarity)
                    # For cosine distance: similarity = 1 - distance
                    async with self._lock:
                        self.stats["similarities"].append(1.0)  # Assume match for hnsw
                    return entry
        except Exception as e:
            logger.debug(f"HNSW similarity search failed: {e}")

        return None

    async def set(
        self,
        input_data: Any,
        content: Any,
        cache_type: CacheType = CacheType.COMPUTATION
    ):
        """
        Cache a computation result.

        Args:
            input_data: Input data (used as key)
            content: Result to cache
            cache_type: Type of cache entry
        """
        # Generate cache ID
        cache_id = hashlib.md5(str(input_data).encode()).hexdigest()[:16]

        # Don't cache if already exists
        if cache_id in self.l1_cache or cache_id in self.l2_cache:
            return

        # Create cache entry
        input_text = str(input_data)
        embedding = self._get_embedding(input_text)

        cache_entry = CacheEntry(
            cache_id=cache_id,
            content=content,
            embedding=embedding,
            access_count=1,
            last_accessed=time.time(),
            created_at=time.time(),
            size_bytes=sys.getsizeof(content),
            cache_type=cache_type,
            metadata={}
        )

        async with self._lock:
            # Add to semantic index
            if embedding is not None and self.faiss_available:
                try:
                    embedding_id = len(self.embedding_to_cache_id)
                    self.embedding_to_cache_id[embedding_id] = cache_id
                    self.semantic_index.add(embedding.reshape(1, -1).astype('float32'))
                except Exception as e:
                    logger.debug(f"Could not add to semantic index: {e}")

            # Add to L1 if space available
            if self._get_l1_size_bytes() + cache_entry.size_bytes <= self.l1_max_size_bytes:
                self.l1_cache[cache_id] = cache_entry
                self.l1_cache.move_to_end(cache_id)
            else:
                # Add to L2
                self.l2_cache[cache_id] = cache_entry
                self._save_l2_cache()

            # Check eviction
            self._check_eviction()

    def _get_l1_size_bytes(self) -> int:
        """Get total size of L1 cache."""
        return sum(entry.size_bytes for entry in self.l1_cache.values())

    def _update_access(self, cache_id: str):
        """Update access statistics for cache entry."""
        current_time = time.time()

        if cache_id in self.l1_cache:
            entry = self.l1_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self.l1_cache.move_to_end(cache_id)
        elif cache_id in self.l2_cache:
            entry = self.l2_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time

    def _promote_to_l1(self, cache_id: str):
        """Promote entry from L2 to L1 cache."""
        if cache_id not in self.l2_cache:
            return

        entry = self.l2_cache.pop(cache_id)

        # Check if L1 has space
        if self._get_l1_size_bytes() + entry.size_bytes <= self.l1_max_size_bytes:
            self.l1_cache[cache_id] = entry
            self.stats["l1_promotions"] += 1
        else:
            # Put back to L2
            self.l2_cache[cache_id] = entry

        self._save_l2_cache()

    def _check_eviction(self):
        """Check and perform eviction if needed."""
        # Evict from L1 to L2 if L1 is over capacity
        while self._get_l1_size_bytes() > self.l1_max_size_bytes and self.l1_cache:
            # Get oldest entry
            oldest_id, oldest_entry = self.l1_cache.popitem(last=False)

            # Move to L2
            self.l2_cache[oldest_id] = oldest_entry
            self.stats["l2_demotions"] += 1

        # Evict from L2 if total entries exceed max
        total_entries = len(self.l1_cache) + len(self.l2_cache)
        if total_entries > self.max_entries and self.l2_cache:
            # Remove oldest from L2
            oldest_id = min(self.l2_cache.keys(),
                          key=lambda k: self.l2_cache[k].last_accessed)
            del self.l2_cache[oldest_id]
            self.stats["evictions"] += 1
            self._save_l2_cache()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self.stats["hits"] + self.stats["misses"]
        avg_similarity = 0.0
        if self.stats["similarities"]:
            avg_similarity = sum(self.stats["similarities"]) / len(self.stats["similarities"])

        return {
            "total_entries": len(self.l1_cache) + len(self.l2_cache),
            "l1_entries": len(self.l1_cache),
            "l2_entries": len(self.l2_cache),
            "hit_count": self.stats["hits"],
            "miss_count": self.stats["misses"],
            "hit_rate": self.stats["hits"] / total if total > 0 else 0.0,
            "l1_size_mb": self._get_l1_size_bytes() / (1024 * 1024),
            "avg_similarity_score": avg_similarity,
            "l1_promotions": self.stats["l1_promotions"],
            "l2_demotions": self.stats["l2_demotions"],
            "evictions": self.stats["evictions"]
        }

    async def clear(self, location: CacheLocation | None = None):
        """
        Clear cache entries.

        Args:
            location: Specific location to clear, or None for all
        """
        async with self._lock:
            if location is None or location == CacheLocation.L1_MEMORY:
                self.l1_cache.clear()

            if location is None or location == CacheLocation.L2_DISK:
                self.l2_cache.clear()
                self._save_l2_cache()

            # Rebuild semantic index
            self._rebuild_semantic_index()


# =============================================================================
# Sprint 80: Memory Pressure Poller
# =============================================================================

class MemoryPressurePoller:
    """Throttled memory pressure monitoring."""

    def __init__(self, interval: float = 5.0):
        self._interval = interval
        self._level = 0.1
        self._task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    async def start(self):
        """Start polling."""
        self._task = asyncio.create_task(self._poll_loop(), name="memory_coordinator:poll")

    async def aclose(self):
        """Gracefully stop the poller."""
        self._shutdown.set()
        if self._task is not None:
            await self._task

    async def _poll_loop(self):
        """Polling loop."""
        try:
            libc = ctypes.CDLL('/usr/lib/libc.dylib')
            libc.sysctlbyname.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_void_p,
                ctypes.c_size_t
            ]
            libc.sysctlbyname.restype = ctypes.c_int
        except Exception:
            libc = None

        while True:
            try:
                if libc is not None:
                    val = ctypes.c_uint32()
                    size = ctypes.c_size_t(4)
                    ret = libc.sysctlbyname(
                        b"kern.memorystatus_vm_pressure_level",
                        ctypes.byref(val),
                        ctypes.byref(size),
                        None,
                        0
                    )
                    if ret == 0:
                        self._level = {0: 0.1, 2: 0.6, 4: 0.95}.get(val.value, 0.1)
            except Exception as e:
                logger.warning(f"MemoryPressurePoller error: {e}")
            await asyncio.sleep(self._interval)
            if self._shutdown.is_set():
                break

    def get_level(self) -> float:
        """Get current memory pressure level (0.0 - 1.0)."""
        return self._level

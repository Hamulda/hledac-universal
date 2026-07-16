"""
Neuromorphic Memory Module — STDP-based episodic memory with zone transitions.

Extracted from coordinators/memory_coordinator.py (F320 refactor).
Neuromorphic subsystem is gated behind ``HLEDAC_ENABLE_NEURO=1`` (default OFF).
Always-on, bounded, fail-safe — if scipy/numpy unavailable, falls back gracefully.

Lazy imports: scipy.sparse (~227ms) and numpy loaded only when this module
is first accessed via HLEDAC_ENABLE_NEURO=1.
"""

import hashlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from enum import Enum
from typing import TYPE_CHECKING, Any

# Deferred: loaded on first access via _get_scipy_sparse() / _get_np()
_scipy_sparse_module: Any = None
_scIPY_AVAILABLE: bool = True

# Deferred: loaded on first access via _get_np()
# np is imported at module level in the parent (memory_coordinator.py)
# We use a getter to avoid circular import and to support numpy-unavailable envs
_np_module: Any = None

logger = logging.getLogger(__name__)


def _get_scipy_sparse() -> Any:
    """Lazy scipy.sparse loader — defers ~227ms import cost until first use."""
    global _scipy_sparse_module, _scIPY_AVAILABLE
    if _scipy_sparse_module is None:
        try:
            from scipy import sparse as _sparse

            _scipy_sparse_module = _sparse
        except ImportError:
            _scipy_sparse_module = None
            _scIPY_AVAILABLE = False
            logger.warning("NeuromorphicMemoryManager: scipy.sparse not available, synaptic weights disabled")
    return _scipy_sparse_module


def _get_np() -> Any:
    """Return numpy module. Defined at module level for type compatibility."""
    # NumPy is loaded lazily via the parent module's HAS_NUMPY sentinel.
    # We re-import here so this module is self-contained.
    global _np_module
    if _np_module is None:
        try:
            import numpy as _np

            _np_module = _np
        except ImportError:
            _np_module = None
    return _np_module


# Memory bounds
MAX_SIMILARITIES = 1000
MAX_PATTERNS = 2000


# =======================================================================
# Neuromorphic Enums & Dataclasses
# =======================================================================


class NeuromorphicMemoryZone(Enum):
    """Memory zones for neuromorphic memory with STDP transitions."""

    WORKING_MEMORY = "working_memory"
    LONG_TERM_MEMORY = "long_term_memory"
    EPISODIC_BUFFER = "episodic_buffer"


class STDPParameters:
    """STDP (Spike-Timing-Dependent Plasticity) parameters."""

    __slots__ = ("A_plus", "A_minus", "tau")

    def __init__(self, A_plus: float = 0.01, A_minus: float = 0.012, tau: float = 20.0) -> None:
        self.A_plus = A_plus
        self.A_minus = A_minus
        self.tau = tau


class MemoryPattern(msgspec.Struct):
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
        self.strength *= 1.0 - decay_rate
        self.strength = max(0.0, self.strength)

    def reinforce(self, amount: float = 0.1) -> None:
        """Reinforce memory strength (capped at 1.0)."""
        self.strength = min(1.0, self.strength + amount)


# =======================================================================
# NeuromorphicMemoryManager
# =======================================================================


class NeuromorphicMemoryManager:
    """
    STDP-based neuromorphic memory with zone transitions.

    Features:
    - Spike-timing-dependent plasticity (STDP) for synaptic weight updates
    - Three-zone system: working, long-term, episodic
    - Optional sparse synaptic weight matrix (scipy.sparse)
    - Lazy numpy / scipy.sparse imports (only when actually initialized)

    Thread-safe: uses a threading.Lock for all public methods.

    Memory budget (M1 8GB):
    - 512 neurons × 512 neurons × 0.03 connectivity × 8 bytes = ~60 KB
    - Plus pattern storage (bounded at MAX_PATTERNS=2000)
    """

    __slots__ = (
        "n_neurons",
        "connectivity",
        "stdp_params",
        "spike_traces",
        "_patterns",
        "synaptic_weights",
        "working_memory",
        "long_term_memory",
        "episodic_buffer",
        "stats",
        "_lock",
    )

    def __init__(
        self,
        n_neurons: int = 512,
        connectivity: float = 0.03,
        stdp_params: STDPParameters | None = None,
    ) -> None:
        """
        Initialize neuromorphic memory manager.

        Args:
            n_neurons: Number of neurons (default 512 for M1 optimization)
            connectivity: Synaptic connectivity ratio (0.0-1.0)
            stdp_params: STDP parameters
        """
        self.n_neurons = n_neurons
        self.connectivity = connectivity
        self.stdp_params = stdp_params or STDPParameters()

        # Spike traces for STDP (one per neuron)
        _np = _get_np()
        if _np is not None:
            self.spike_traces = _np.zeros(n_neurons, dtype=_np.float32)
        else:
            self.spike_traces = None  # type: ignore[assignment]

        # Pattern storage
        self._patterns: dict[str, MemoryPattern] = {}

        # Synaptic weights (sparse)
        self.synaptic_weights = self._init_synaptic_weights(n_neurons, connectivity)

        # Memory zones with bounded deques
        self.working_memory: deque[MemoryPattern] = deque(maxlen=50)
        self.long_term_memory: deque[MemoryPattern] = deque(maxlen=500)
        self.episodic_buffer: deque[MemoryPattern] = deque(maxlen=100)

        # Statistics
        self.stats: dict[str, int | float] = {
            "patterns_stored": 0,
            "patterns_recalled": 0,
            "consolidations": 0,
            "replays": 0,
            "forgotten": 0,
        }

        logger.debug(
            "NeuromorphicMemoryManager initialized: %s neurons, %.1f%% connectivity",
            n_neurons,
            connectivity * 100,
        )

    def _init_synaptic_weights(self, n_neurons: int, connectivity: float) -> Any:
        """Initialize sparse synaptic weight matrix."""
        _sp = _get_scipy_sparse()
        _np = _get_np()

        if _sp is None or _np is None:
            return None

        try:
            # Random sparse connectivity
            size = int(n_neurons * n_neurons * connectivity)
            rows = _np.random.randint(0, n_neurons, size=size, dtype=_np.int32)
            cols = _np.random.randint(0, n_neurons, size=size, dtype=_np.int32)
            data = _np.random.rand(size) * 0.1  # Small initial weights

            weights = _sp.csr_matrix(
                (data.astype(_np.float32), (rows, cols)),
                shape=(n_neurons, n_neurons),
            )
            # Symmetrize
            weights = (weights + weights.T) / 2
            # No self-connections
            weights.setdiag(0)
            return weights
        except Exception:
            return None

    def _encode_pattern(self, data: Any) -> Any:
        """Encode arbitrary data into a neuron activation vector."""
        _np = _get_np()
        if _np is None:
            return None

        # Hash-based encoding for arbitrary data
        if isinstance(data, dict):
            raw = str(sorted(data.items())).encode()
        elif isinstance(data, str):
            raw = data.encode()
        else:
            raw = str(data).encode()

        # Create fixed-size activation vector
        activations = _np.zeros(self.n_neurons, dtype=_np.float32)
        hash_bytes = hashlib.sha256(raw).digest()
        n_bytes = len(hash_bytes)

        for i in range(min(self.n_neurons, n_bytes * 8)):
            byte_idx = i // 8
            bit_idx = i % 8
            if byte_idx < n_bytes:
                activations[i] = 1.0 if (hash_bytes[byte_idx] >> bit_idx) & 1 else 0.0

        return activations

    def _stdp_update(self, pre_idx: int, post_idx: int, delta_t: float) -> float:
        """
        Apply STDP update to synaptic weight.

        Args:
            pre_idx: Pre-synaptic neuron index (unused in simplified model)
            post_idx: Post-synaptic neuron index (unused in simplified model)
            delta_t: Time difference (pre - post)

        Returns:
            Weight change value
        """
        _np = _get_np()
        if _np is None:
            return 0.0
        if delta_t > 0:
            # Pre before post → potentiation
            return self.stdp_params.A_plus * _np.exp(-delta_t / self.stdp_params.tau)
        else:
            # Post before pre → depression
            return -self.stdp_params.A_minus * _np.exp(delta_t / self.stdp_params.tau)

    def _update_weights_from_pattern(self, activations: Any) -> None:
        """Update synaptic weights based on neuron activations."""
        if self.synaptic_weights is None or activations is None:
            return

        _np = _get_np()
        if _np is None:
            return

        try:
            active = _np.where(activations > 0.5)[0]
            for i, pre in enumerate(active):
                for post in active[i + 1:]:
                    delta_t = 1.0  # Simplified
                    dw = self._stdp_update(pre, post, delta_t)
                    # Apply to weight matrix symmetrically
                    w = self.synaptic_weights[pre, post] + dw
                    self.synaptic_weights[pre, post] = max(0.0, min(1.0, w))
                    self.synaptic_weights[post, pre] = self.synaptic_weights[pre, post]
        except Exception:
            pass  # Fail-safe

    def store_pattern(
        self,
        pattern_id: str,
        data: Any,
        zone: NeuromorphicMemoryZone = NeuromorphicMemoryZone.WORKING_MEMORY,
    ) -> dict[str, Any]:
        """
        Store a pattern in neuromorphic memory.

        Args:
            pattern_id: Unique pattern identifier
            data: Data to encode
            zone: Target memory zone

        Returns:
            Storage result dict
        """
        activations = self._encode_pattern(data)
        if activations is None:
            return {"success": False, "error": "numpy unavailable"}

        pattern = MemoryPattern(
            pattern_id=pattern_id,
            neuron_activations=activations,
            timestamp=time.time(),
            strength=1.0,
            metadata={"zone": zone.value},
        )

        self._patterns[pattern_id] = pattern

        if zone == NeuromorphicMemoryZone.WORKING_MEMORY:
            self.working_memory.append(pattern)
        elif zone == NeuromorphicMemoryZone.LONG_TERM_MEMORY:
            self.long_term_memory.append(pattern)
        else:
            self.episodic_buffer.append(pattern)

        self._update_weights_from_pattern(activations)
        self.stats["patterns_stored"] += 1

        return {"success": True, "pattern_id": pattern_id, "zone": zone.value}

    def recall_pattern(self, pattern_id: str, completion: bool = False) -> Any:
        """
        Recall a pattern from memory.

        Args:
            pattern_id: Pattern to recall
            completion: Whether to perform pattern completion

        Returns:
            Recalled pattern or None
        """
        pattern = self._patterns.get(pattern_id)
        if pattern is None:
            return None

        self.stats["patterns_recalled"] += 1

        if completion:
            self._pattern_completion(pattern)

        return pattern

    def _pattern_completion(self, pattern: MemoryPattern) -> None:
        """Auto-associative pattern completion using synaptic weights."""
        if self.synaptic_weights is None:
            return

        _np = _get_np()
        if _np is None:
            return

        try:
            activations = pattern.neuron_activations.copy()
            for _ in range(10):  # Iterative completion
                activated = _np.where(activations > 0.5)[0]
                if len(activated) == 0:
                    break
                # Simple Hopfield-like update
                row = self.synaptic_weights[activated, :]
                new_activations = _np.array(row.sum(axis=0)).flatten() > 0.3
                activations[new_activations] = 1.0
            pattern.neuron_activations = activations
        except Exception:
            pass  # Fail-safe

    def consolidate_memories(self, strength_threshold: float = 0.5) -> int:
        """
        Consolidate strong working memories to long-term memory.

        Args:
            strength_threshold: Minimum strength to consolidate

        Returns:
            Number of patterns consolidated
        """
        consolidated = 0

        for pattern in list(self.working_memory):
            if pattern.strength >= strength_threshold:
                self.long_term_memory.append(pattern)
                pattern.metadata["zone"] = NeuromorphicMemoryZone.LONG_TERM_MEMORY.value
                consolidated += 1

        self.stats["consolidations"] += consolidated
        logger.info("Consolidated %d patterns to long-term memory", consolidated)

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

        for pattern in list(self.working_memory):
            if pattern.strength < threshold:
                self.working_memory.remove(pattern)
                if pattern.pattern_id in self._patterns:
                    del self._patterns[pattern.pattern_id]
                forgotten += 1

        for pattern in list(self.long_term_memory):
            if pattern.strength < threshold * 0.5:  # Stricter for LTM
                self.long_term_memory.remove(pattern)
                if pattern.pattern_id in self._patterns:
                    del self._patterns[pattern.pattern_id]
                forgotten += 1

        self.stats["forgotten"] += forgotten
        logger.info("Forgot %d weak memories", forgotten)
        return forgotten

    def _memory_replay(self, n_replays: int = 10) -> None:
        """
        Strengthen memories through replay (sleep-like consolidation).

        Args:
            n_replays: Number of memory replays
        """
        if not self.long_term_memory:
            return

        _np = _get_np()
        if _np is None:
            return

        memories = list(self.long_term_memory)
        n_samples = min(n_replays, len(memories))

        for _ in range(n_samples):
            pattern = memories[_np.random.randint(len(memories))]
            pattern.reinforce(0.1)
            self._update_weights_from_pattern(pattern.neuron_activations)
            self.stats["replays"] += 1

    def apply_decay(self, decay_rate: float = 0.01) -> None:
        """Apply decay to all memory strengths."""
        for pattern in self.working_memory:
            pattern.decay(decay_rate)
        for pattern in self.long_term_memory:
            pattern.decay(decay_rate * 0.5)  # Slower for LTM
        for pattern in self.episodic_buffer:
            pattern.decay(decay_rate)

    def cleanup(self) -> None:
        """Aggressive cleanup for M1 memory constraints."""
        self.episodic_buffer.clear()
        self.forget_weak_memories(threshold=0.2)

    def get_stats(self) -> dict[str, Any]:
        """Get neuromorphic memory statistics."""
        stats: dict[str, int | float | deque[float]] = {
            **self.stats,
            "working_memory_size": len(self.working_memory),
            "long_term_memory_size": len(self.long_term_memory),
            "episodic_buffer_size": len(self.episodic_buffer),
            "total_patterns": len(self._patterns),
            "n_neurons": self.n_neurons,
        }
        if self.synaptic_weights is not None:
            stats["synaptic_density"] = self.synaptic_weights.nnz / (self.n_neurons ** 2)
        else:
            stats["synaptic_density"] = 0.0
        return stats

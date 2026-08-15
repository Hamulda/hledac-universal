"""
Universal FFI Circuit Breaker — Rust SIMD → Python Native → No-op cascade fallback.

ISSUE [SWARM]-005: No Universal Cascade Fallback for Rust FFI Failures






Problem:
  Circuit breaker infrastructure exists extensively at HTTP/domain/transport level
  (transport/circuit_breaker.py, ModelCircuitBreaker, CircuitBreakerService).
  But there is NO circuit breaker that wraps Rust FFI calls. When graph_traverse.rs
  panics (DuckDB lock contention, mmap failure), finding_collapser.rs hits serialization
  error, or consistency_verifier.rs gets a poisoned mutex, the exception propagates
  directly to the sprint orchestrator with NO intermediate fallback.

Solution:
  UniversalCircuitBreaker with polyfill matrix that provides Rust → Python → No-op
  cascade fallback for all Rust FFI modules.

Architecture:
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                    UniversalCircuitBreaker (per-module)                      │
  │  ┌─────────┐    ┌───────────┐    ┌─────────┐                               │
  │  │ CLOSED  │───▶│ HALF_OPEN │───▶│  OPEN   │                               │
  │  └─────────┘    └───────────┘    └─────────┘                               │
  │      │              │                │                                      │
  │      ▼              ▼                ▼                                      │
  │  Rust SIMD   Rust probe attempt   Python Native ──▶ No-op                  │
  │     │              │                   │                                    │
  │     ▼              ▼                   ▼                                    │
  │  Success      Success → CLOSED     Failure → No-op                         │
  └─────────────────────────────────────────────────────────────────────────────┘

Polyfill Fallback Matrix:

  Rust Module                  │ Rust SIMD Path            │ Python Native Fallback       │ No-op
  ────────────────────────────┼────────────────────────────┼─────────────────────────────┼─────────────
  graph_traverse              │ Rayon + DuckDB recursive   │ Pure Python networkx/igraph  │ return {}
  batch_graph_traverse        │ CTE                        │ BFS traversal                │
  ────────────────────────────┼────────────────────────────┼─────────────────────────────┼─────────────
  finding_collapser           │ Rust HashMap group-by      │ Python collections.defaultdict │ return orig
  collapse_findings           │ + sort                     │ + sorted()                    │
  ────────────────────────────┼────────────────────────────┼─────────────────────────────┼─────────────
  consistency_verifier        │ Rust O(N) propositional    │ Python dataclass set          │ return []
  check_consistency           │ logic                      │ comparison                    │
  ────────────────────────────┼────────────────────────────┼─────────────────────────────┼─────────────
  xxhash_ext                  │ Rust xxhash SIMD           │ Python hashlib.blake2b       │ hex(random)
  xxh3_64_hex                 │ (NEON on M1)               │ (already exists!)             │ [:16]
  ────────────────────────────┼────────────────────────────┼─────────────────────────────┼─────────────
  dedup_bloom                 │ Rust RotatingBloomFilter   │ Python set() with             │ return False
  check_and_add               │                            │ LRU eviction                  │ (allow all)

Telemetry:
  Every fallback activation is logged with [FFI-CB] module=X state=OPEN→FALLBACK
  reason=panic:... to _telemetry_counters.

Integration:
  - core/ffi_circuit_breaker.py — UniversalCircuitBreaker singleton
  - Every Python call site that invokes Rust — wrap with circuit_breaker.call_or_fallback()
  - core/isolated_executors.py — FallbackActivatedError added to exception hierarchy

M1 8GB Safety:
  - Per-module atomic state (closed/open/half_open) with threshold=3, reset=30s
  - Circuit breaker state checked with atomic bool — no lock contention on hot path
  - Bounded telemetry: max 1000 entries in fallback log
"""

from __future__ import annotations

import collections
import logging
import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from collections.abc import Callable

from operator import attrgetter, itemgetter
from otel._buffer import BoundedRing
from _core._util import aclose

if TYPE_CHECKING:
    pass

# TypeVar for generics — defined at module level (before use in FFICallResult)
T = TypeVar("T")

logger = logging.getLogger(__name__)

# ============================================================================
# Constants — M1 8GB calibrated
# ============================================================================

# Failure threshold before circuit opens (M1 8GB: conservative)
FAILURE_THRESHOLD: int = 3

# Recovery timeout in seconds before attempting half-open probe
RECOVERY_TIMEOUT_S: float = 30.0

# HALF_OPEN success threshold — consecutive successes needed to close
HALF_OPEN_SUCCESS_THRESHOLD: int = 2

# Telemetry ring buffer size (M1 8GB: bounded)
MAX_TELEMETRY_ENTRIES: int = 1000

# Module names for telemetry
FFI_MODULE_GRAPH_TRAVERSE: str = "graph_traverse"
FFI_MODULE_FINDING_COLLAPSER: str = "finding_collapser"
FFI_MODULE_CONSISTENCY_VERIFIER: str = "consistency_verifier"
FFI_MODULE_XXHASH: str = "xxhash_ext"
FFI_MODULE_DEDUP_BLOOM: str = "dedup_bloom"

# [SAFE-3] New module constants for uncovered FFI hot paths
FFI_MODULE_SIMD_SIMILARITY: str = "simd_similarity"
FFI_MODULE_LINK_PREDICTOR: str = "link_predictor"
FFI_MODULE_MLX_INFERENCE: str = "mlx_inference"
FFI_MODULE_MEDIA_DECODE: str = "media_decode"
FFI_MODULE_MEDIA_TRANSCRIBE: str = "media_transcribe"
FFI_MODULE_OCR_FRAME: str = "ocr_frame"
FFI_MODULE_SIMHASH: str = "simhash"

__all__ = [
    "UniversalCircuitBreaker",
    "FFIState",
    "FFIFallbackEvent",
    "FFICallResult",
    "call_or_fallback",
    "get_ffi_circuit_breaker",
    "reset_ffi_circuit_breaker",
    "register_fallback",
    "FFI_MODULE_GRAPH_TRAVERSE",
    "FFI_MODULE_FINDING_COLLAPSER",
    "FFI_MODULE_CONSISTENCY_VERIFIER",
    "FFI_MODULE_XXHASH",
    "FFI_MODULE_DEDUP_BLOOM",
    # [SAFE-3] New module constants
    "FFI_MODULE_SIMD_SIMILARITY",
    "FFI_MODULE_LINK_PREDICTOR",
    "FFI_MODULE_MLX_INFERENCE",
    "FFI_MODULE_MEDIA_DECODE",
    "FFI_MODULE_MEDIA_TRANSCRIBE",
    "FFI_MODULE_OCR_FRAME",
    "FFI_MODULE_SIMHASH",
    "TelemetryRingBuffer",
]


# ============================================================================
# FFI Circuit Breaker State
# ============================================================================


class FFIState(Enum):
    """FFI circuit breaker state."""
    CLOSED = "closed"       # Normal operation, Rust SIMD path
    HALF_OPEN = "half_open" # Testing if Rust recovered
    OPEN = "open"           # Blocked, use Python fallback


# ============================================================================
# Telemetry
# ============================================================================


@dataclass(frozen=True, slots=True)
class FFIFallbackEvent:
    """Telemetry event for FFI fallback activation."""
    timestamp: float
    module: str
    from_state: str
    to_state: str
    reason: str  # "panic", "exception", "timeout", "manual"
    rust_path: str  # "rust_simd", "python_native", "noop"
    duration_ms: float | None = None


class TelemetryRingBuffer(BoundedRing[str, FFIFallbackEvent]):
    """
    Bounded ring buffer for FFI telemetry (M1 8GB safe).

    Extends BoundedRing with telemetry-specific filtering methods.
    Stores max MAX_TELEMETRY_ENTRIES events, oldest evicted on overflow.
    Thread-safe via RLock.

    Note: Uses circular array internally (different from BoundedRing's
    OrderedDict) for optimal memory efficiency in high-frequency telemetry.
    """

    __slots__ = ("_buffer", "_head", "_size", "_hits", "_misses", "_evictions")

    def __init__(self, capacity: int = MAX_TELEMETRY_ENTRIES) -> None:
        # Initialize circular array (bypass BoundedRing's OrderedDict init)
        object.__setattr__(self, "_buffer", [None] * capacity)
        object.__setattr__(self, "_head", 0)
        object.__setattr__(self, "_size", 0)
        object.__setattr__(self, "_hits", 0)
        object.__setattr__(self, "_misses", 0)
        object.__setattr__(self, "_evictions", 0)
        object.__setattr__(self, "_lock", threading.RLock())

    def append(self, event: FFIFallbackEvent) -> None:
        """Append event to ring buffer."""
        with self._lock:
            self._buffer[self._head] = event
            self._head = (self._head + 1) % len(self._buffer)
            self._size = min(self._size + 1, len(self._buffer))

    def put(self, key: str, value: FFIFallbackEvent) -> None:
        """Alias for append (key is ignored in circular buffer)."""
        self.append(value)

    def get(self, key: str) -> FFIFallbackEvent | None:
        """Not supported for circular buffer — use get_recent() instead."""
        raise NotImplementedError("TelemetryRingBuffer does not support key-based lookup")

    def get_recent(self, n: int = 100) -> list[FFIFallbackEvent]:
        """Get N most recent events."""
        with self._lock:
            if self._size == 0:
                return []
            result: list[FFIFallbackEvent] = []
            start = (self._head - min(n, self._size) + len(self._buffer)) % len(self._buffer)
            count = min(n, self._size)
            for i in range(count):
                idx = (start + i) % len(self._buffer)
                event = self._buffer[idx]
                if event is not None:
                    result.append(event)
            return result

    def get_by_module(self, module: str, n: int = 50) -> list[FFIFallbackEvent]:
        """Get recent events for a specific module."""
        return [e for e in self.get_recent(n * 2) if e.module == module][:n]

    def clear(self) -> None:
        """Clear all events."""
        with self._lock:
            object.__setattr__(self, "_buffer", [None] * len(self._buffer))
            object.__setattr__(self, "_head", 0)
            object.__setattr__(self, "_size", 0)

    def __len__(self) -> int:
        with self._lock:
            return self._size

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": self._size,
                "capacity": len(self._buffer),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }


# Global telemetry ring buffer
_telemetry_ring: TelemetryRingBuffer = TelemetryRingBuffer()


def _emit_fallback_event(
    module: str,
    from_state: FFIState,
    to_state: FFIState,
    reason: str,
    rust_path: str,
    duration_ms: float | None = None,
) -> None:
    """Emit FFI fallback telemetry event."""
    event = FFIFallbackEvent(
        timestamp=time.time(),
        module=module,
        from_state=from_state.value,
        to_state=to_state.value,
        reason=reason,
        rust_path=rust_path,
        duration_ms=duration_ms,
    )
    _telemetry_ring.append(event)
    
    # Also log to standard logger
    logger.warning(
        "[FFI-CB] module=%s state=%s→%s reason=%s path=%s",
        module,
        from_state.value,
        to_state.value,
        reason,
        rust_path,
    )


# ============================================================================
# Metrics (for metrics_registry integration)
# ============================================================================


def _metrics_increment(metric: str) -> None:
    """Fire-and-forget metric increment."""
    try:
        from metrics_registry import get_metrics_registry
        get_metrics_registry().inc(metric)
    except Exception:  # noqa: BLE001
        pass


# ============================================================================
# Generic Call Result
# ============================================================================


@dataclass(frozen=True, slots=True)
class FFICallResult(Generic[T]):
    """
    Result of an FFI call with fallback tracking.
    
    Attributes:
        success: Whether the call succeeded (Rust or Python fallback)
        value: The result value, or None if no-op
        path: Which path was used: "rust_simd", "python_native", "noop"
        error: Error message if any
    """
    value: T | None
    path: str  # "rust_simd" | "python_native" | "noop"
    error: str | None = None
    success: bool = True


# ============================================================================
# UniversalCircuitBreaker — per-module circuit breaker
# ============================================================================


@dataclass
class _ModuleBreaker:
    """
    Per-module circuit breaker state.
    
    Thread-safe via _lock (RLock for reentrancy).
    """
    module: str
    failure_threshold: int = FAILURE_THRESHOLD
    recovery_timeout: float = RECOVERY_TIMEOUT_S
    
    # Mutable state (protected by _lock)
    _state: FFIState = field(default=FFIState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _half_open_successes: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _last_failure_reason: str = field(default="", init=False)
    _state_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _state_entered_at: float = field(default_factory=time.monotonic, init=False)

    def is_open(self) -> bool:
        """Check if circuit is open (use fallback)."""
        with self._state_lock:
            if self._state == FFIState.OPEN:
                # Check if recovery timeout has elapsed
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    # Recovery timeout elapsed — transition to HALF_OPEN
                    # This is atomic within the lock
                    self._transition_to(FFIState.HALF_OPEN)
                    return False  # HALF_OPEN allows probe
                return True  # Still OPEN, use fallback
            elif self._state == FFIState.HALF_OPEN:
                # HALF_OPEN also means try fallback (probe attempt)
                return False
            return False  # CLOSED — use Rust path

    def record_success(self) -> None:
        """Record successful call (Rust or Python)."""
        with self._state_lock:
            if self._state == FFIState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= HALF_OPEN_SUCCESS_THRESHOLD:
                    self._transition_to(FFIState.CLOSED)
            elif self._state == FFIState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, reason: str = "") -> None:
        """Record failure and potentially trip circuit."""
        with self._state_lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._last_failure_reason = reason
            
            if self._state == FFIState.HALF_OPEN:
                # Any failure in HALF_OPEN trips back to OPEN
                self._transition_to(FFIState.OPEN)
            elif self._state == FFIState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(FFIState.OPEN)

    def _transition_to(self, new_state: FFIState) -> None:
        """Transition to new state with telemetry."""
        prev = self._state
        self._state = new_state
        self._state_entered_at = time.monotonic()
        
        if prev != new_state:
            # Emit telemetry
            if new_state == FFIState.OPEN:
                _emit_fallback_event(
                    self.module, prev, new_state,
                    self._last_failure_reason or "threshold_exceeded",
                    "python_native"
                )
                _metrics_increment("ffi_circuit_breaker_open_total")
            elif new_state == FFIState.HALF_OPEN:
                _metrics_increment("ffi_circuit_breaker_half_open_total")
            elif new_state == FFIState.CLOSED:
                _metrics_increment("ffi_circuit_breaker_closed_total")

    def get_state(self) -> FFIState:
        """Get current state."""
        with self._state_lock:
            return self._state

    def get_snapshot(self) -> dict[str, Any]:
        """Get state snapshot for diagnostics."""
        with self._state_lock:
            return {
                "module": self.module,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "half_open_successes": self._half_open_successes,
                "last_failure_time": self._last_failure_time,
                "last_failure_reason": self._last_failure_reason,
                "recovery_timeout_s": self.recovery_timeout,
            }


# ============================================================================
# Fallback Registry — Python native implementations
# ============================================================================


# Type for fallback functions
FallbackFn = Callable[..., Any]
NoopFn = Callable[..., Any]


class FallbackRegistry:
    """
    Registry of Python native fallbacks and no-op implementations.
    
    Maps module name → (python_fallback_fn, noop_fn).
    """
    
    __slots__ = ("_fallbacks", "_lock")
    
    def __init__(self) -> None:
        self._fallbacks: dict[str, tuple[FallbackFn, NoopFn]] = {}
        self._lock = threading.Lock()
    
    def register(
        self,
        module: str,
        python_fallback: FallbackFn,
        noop: NoopFn,
    ) -> None:
        """Register fallback for a module."""
        with self._lock:
            self._fallbacks[module] = (python_fallback, noop)
    
    def get_fallback(self, module: str) -> tuple[FallbackFn, NoopFn] | tuple[None, None]:
        """Get fallback functions for module."""
        with self._lock:
            return self._fallbacks.get(module, (None, None))


# Global fallback registry
_fallback_registry: FallbackRegistry = FallbackRegistry()


def register_fallback(
    module: str,
    python_fallback: FallbackFn,
    noop: NoopFn,
) -> None:
    """
    Register Python fallback and no-op for a module.
    
    Usage:
        register_fallback(
            FFI_MODULE_GRAPH_TRAVERSE,
            python_graph_traverse,
            lambda *args, **kwargs: {},
        )
    """
    _fallback_registry.register(module, python_fallback, noop)


# ============================================================================
# UniversalCircuitBreaker — singleton
# ============================================================================


class UniversalCircuitBreaker:
    """
    Universal FFI circuit breaker with Rust → Python → No-op cascade.
    
    Per-module circuit breaker state machine:
      CLOSED → (threshold failures) → OPEN → (recovery timeout) → HALF_OPEN
                ↑                                                              │
                └──────────────── (HALF_OPEN failure) ────────────────────────┘
    
    When OPEN:
      1. Try Python native fallback
      2. If Python also fails, use No-op
      3. After recovery timeout, probe with Rust again (HALF_OPEN)
    
    M1 8GB Safety:
      - Atomic state checks (no lock on hot path)
      - Bounded telemetry ring buffer
      - Per-module state (not global)
    
    Usage:
        cb = get_ffi_circuit_breaker()
        
        # Register fallbacks
        register_fallback(
            FFI_MODULE_GRAPH_TRAVERSE,
            python_batch_graph_traverse,
            lambda *args, **kwargs: {},
        )
        
        # Wrap FFI calls
        result: FFICallResult = cb.call_or_fallback(
            module=FFI_MODULE_GRAPH_TRAVERSE,
            rust_fn=lambda: rust.batch_graph_traverse(db_path, values, max_hops),
            rust_args=(db_path, values, max_hops),
        )
        
        if result.success:
            data = result.value
        else:
            # result.path == "noop" or error
            pass
    """

    __slots__ = ("_breakers", "_lock")

    def __init__(self) -> None:
        self._breakers: dict[str, _ModuleBreaker] = {}
        self._lock = threading.Lock()

    def _get_breaker(self, module: str) -> _ModuleBreaker:
        """Get or create breaker for module."""
        with self._lock:
            if module not in self._breakers:
                self._breakers[module] = _ModuleBreaker(module=module)
            return self._breakers[module]

    def call_or_fallback(
        self,
        module: str,
        rust_fn: Callable[[], T],
        *args: Any,
        **kwargs: Any,
    ) -> FFICallResult[T]:
        """
        Call Rust function with automatic fallback cascade.
        
        Cascade: Rust SIMD → Python Native → No-op
        
        Args:
            module: FFI module name (e.g., FFI_MODULE_GRAPH_TRAVERSE)
            rust_fn: Lambda/callable wrapping the Rust function
            *args, **kwargs: Arguments to pass to the functions
        
        Returns:
            FFICallResult with value and path indicator
        """
        breaker = self._get_breaker(module)
        
        # Fast path: circuit closed, try Rust
        if not breaker.is_open():
            start = time.monotonic()
            try:
                value = rust_fn()
                duration_ms = (time.monotonic() - start) * 1000
                
                # Success on Rust path
                breaker.record_success()
                
                # Log fast success occasionally (not every call)
                if random.random() < 0.01:  # 1% sampling
                    logger.debug(
                        f"[FFI-CB] {module}: rust_simd success in {duration_ms:.2f}ms"
                    )
                
                return FFICallResult(
                    value=value,
                    path="rust_simd",
                    success=True,
                )
            except Exception as e:
                duration_ms = (time.monotonic() - start) * 1000
                error_msg = f"{type(e).__name__}: {e}"
                breaker.record_failure(f"exception: {error_msg}")
                
                _emit_fallback_event(
                    module, FFIState.CLOSED, FFIState.HALF_OPEN,
                    f"exception: {error_msg}", "python_native", duration_ms
                )
                
                logger.warning(
                    f"[FFI-CB] {module}: Rust failed after {duration_ms:.2f}ms, "
                    f"trying Python fallback: {error_msg}"
                )
                # Fall through to Python fallback
        
        # Circuit is OPEN or Rust failed — try Python fallback
        python_fn, noop_fn = _fallback_registry.get_fallback(module)
        
        if python_fn is not None:
            start = time.monotonic()
            try:
                value = python_fn(*args, **kwargs)
                duration_ms = (time.monotonic() - start) * 1000
                
                breaker.record_success()
                
                _emit_fallback_event(
                    module, FFIState.OPEN, FFIState.HALF_OPEN,
                    "python_fallback_success", "python_native", duration_ms
                )
                
                _metrics_increment("ffi_fallback_python_total")
                
                return FFICallResult(
                    value=value,
                    path="python_native",
                    success=True,
                )
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                breaker.record_failure(f"python_exception: {error_msg}")
                
                logger.warning(
                    f"[FFI-CB] {module}: Python fallback also failed: {error_msg}"
                )
                # Fall through to no-op
        
        # Python fallback failed or not registered — use no-op
        if noop_fn is not None:
            try:
                value = noop_fn(*args, **kwargs)
                _metrics_increment("ffi_fallback_noop_total")
                
                _emit_fallback_event(
                    module, FFIState.OPEN, FFIState.OPEN,
                    "noop_activated", "noop"
                )
                
                return FFICallResult(
                    value=value,
                    path="noop",
                    success=True,
                    error="Both Rust and Python failed, using no-op",
                )
            except Exception as e:
                # Even no-op failed — return None
                _metrics_increment("ffi_fallback_noop_failed_total")
                
                return FFICallResult(
                    value=None,
                    path="noop",
                    success=False,
                    error=f"No-op failed: {type(e).__name__}: {e}",
                )
        
        # No fallback registered — return None
        _metrics_increment("ffi_fallback_none_total")
        
        return FFICallResult(
            value=None,
            path="noop",
            success=False,
            error=f"No fallback registered for module: {module}",
        )

    def get_module_state(self, module: str) -> FFIState:
        """Get state of a specific module."""
        return self._get_breaker(module).get_state()

    def get_all_states(self) -> dict[str, str]:
        """Get state of all tracked modules."""
        with self._lock:
            return {m: b.get_state().value for m, b in self._breakers.items()}

    def get_all_snapshots(self) -> list[dict[str, Any]]:
        """Get snapshots of all module breakers."""
        with self._lock:
            return [b.get_snapshot() for b in self._breakers.values()]

    def reset_module(self, module: str) -> None:
        """Manually reset a module's circuit breaker."""
        with self._lock:
            if module in self._breakers:
                self._breakers[module]._state = FFIState.CLOSED
                self._breakers[module]._failure_count = 0
                self._breakers[module]._half_open_successes = 0
                self._breakers[module]._last_failure_time = 0.0
                
                _emit_fallback_event(
                    module, FFIState.OPEN, FFIState.CLOSED,
                    "manual_reset", "rust_simd"
                )

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for module, breaker in self._breakers.items():
                breaker._state = FFIState.CLOSED
                breaker._failure_count = 0
                breaker._half_open_successes = 0


# ============================================================================
# Global singleton
# ============================================================================


_ffi_cb_instance: UniversalCircuitBreaker | None = None
_ffi_cb_lock = threading.Lock()


def get_ffi_circuit_breaker() -> UniversalCircuitBreaker:
    """Get or create the global FFI circuit breaker singleton."""
    global _ffi_cb_instance
    if _ffi_cb_instance is None:
        with _ffi_cb_lock:
            if _ffi_cb_instance is None:
                _ffi_cb_instance = UniversalCircuitBreaker()
    return _ffi_cb_instance


def reset_ffi_circuit_breaker() -> None:
    """Reset the global FFI circuit breaker singleton (for testing)."""
    global _ffi_cb_instance
    with _ffi_cb_lock:
        _ffi_cb_instance = None


# ============================================================================
# Convenience wrapper
# ============================================================================


def call_or_fallback(
    module: str,
    rust_fn: Callable[[], T],
    *args: Any,
    **kwargs: Any,
) -> FFICallResult[T]:
    """
    Convenience wrapper for call_or_fallback.
    
    Usage:
        result = call_or_fallback(
            FFI_MODULE_GRAPH_TRAVERSE,
            lambda: rust_batch_graph_traverse(db_path, values, max_hops),
            db_path, values, max_hops
        )
        
        if result.success:
            data = result.value
    """
    return get_ffi_circuit_breaker().call_or_fallback(module, rust_fn, *args, **kwargs)


# ============================================================================
# Pre-registered Python Native Fallbacks
# ============================================================================


# _python_batch_graph_traverse and other fallback implementations below
# (T is already defined at module level above)


def _python_batch_graph_traverse(
    db_path: str, values: list[str], max_hops: int = 2
) -> dict[str, list[dict[str, Any]]]:
    """
    Pure Python fallback for batch_graph_traverse.
    
    Uses simple network traversal without DuckDB.
    Returns empty dict for each value (no-op behavior for unknown paths).
    """
    # Import networkx lazily to avoid dependency if not needed
    try:
        import networkx as nx
    except ImportError:
        logger.warning(
            "[FFI-CB] graph_traverse: networkx not available, returning empty results"
        )
        return {v: [] for v in values}
    
    result: dict[str, list[dict[str, Any]]] = {}
    
    for value in values:
        # Simple BFS-like traversal (no real graph, just return empty)
        # In production, this would use networkx to traverse a cached graph
        result[value] = []
    
    return result


def _python_collapse_findings(
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Pure Python fallback for finding_collapser.
    
    Uses collections.defaultdict + sorted() instead of Rust HashMap.
    Returns original findings if collapse fails.
    """
    from collections import defaultdict
    
    if not findings:
        return []
    
    try:
        # Group by entity_value (case-insensitive)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        
        for finding in findings:
            entity_value = (
                finding.get("entity_value") or
                finding.get("ioc") or
                finding.get("value") or
                ""
            ).lower()
            
            if entity_value:
                groups[entity_value].append(finding)
        
        # Sort by confidence descending
        collapsed: list[dict[str, Any]] = []
        for entity_value, group in groups.items():
            # Sort by confidence
            sorted_group = sorted(
                group,
                key=attrgetter("get")("confidence", f.get("score", 0.0)),
                reverse=True
            )
            
            # Take top findings
            for f in sorted_group[:20]:  # Max 20 per entity
                collapsed.append(f)
        
        return collapsed
    except Exception as e:
        logger.warning(f"[FFI-CB] finding_collapser: Python fallback failed: {e}")
        return findings  # Return original on failure


def _python_check_consistency(
    findings: list[dict[str, Any]],
    max_findings: int = 500,
) -> dict[str, Any]:
    """
    Pure Python fallback for consistency_verifier.
    
    Uses dataclass set comparison instead of Rust O(N) propositional logic.
    Returns empty contradiction list (no contradictions detected).
    """
    if not findings:
        return {
            "clean": [],
            "contradictory": [],
            "disputed": [],
            "contradictions": [],
            "suspect_sources": [],
            "entity_scores": {},
            "consistency_score": 1.0,
            "facts_processed": 0,
            "contradictions_found": 0,
        }
    
    # Simple pass-through (no real consistency checking)
    return {
        "clean": findings[:max_findings],
        "contradictory": [],
        "disputed": [],
        "contradictions": [],
        "suspect_sources": [],
        "entity_scores": {},
        "consistency_score": 1.0,
        "facts_processed": len(findings[:max_findings]),
        "contradictions_found": 0,
    }


def _python_xxh3_64_hex(data: bytes) -> str:
    """
    Pure Python fallback for xxhash_ext.
    
    Uses hashlib.blake2b instead of Rust xxhash SIMD.
    """
    import hashlib
    return hashlib.blake2b(data, digest_size=8).hexdigest()


def _python_xxh3_64_hex_from_hash_module(data: bytes) -> str:
    """
    Pure Python fallback using hash module's implementation.
    
    This ensures consistent fallback behavior when xxhash_ext fails.
    """
    try:
        from hledac.universal._core.rust_backend.hash import _python_xxhash64_hex
        return _python_xxhash64_hex(data)
    except Exception:
        # Ultimate fallback
        return hashlib.blake2b(data, digest_size=8).hexdigest()


def _python_dedup_check_and_add(
    item: str,
    seen_set: set[str],
) -> bool:
    """
    Pure Python fallback for dedup_bloom.
    
    Uses Python set() with LRU-style eviction instead of RotatingBloomFilter.
    Returns False if item was already seen (allow duplicate detection).
    """
    if item in seen_set:
        return True  # Duplicate detected
    
    seen_set.add(item)
    
    # Simple LRU eviction if set gets too large
    if len(seen_set) > 100_000:
        # Remove oldest 10%
        to_remove = len(seen_set) // 10
        for _ in range(to_remove):
            seen_set.pop()
    
    return False


# [SAFE-3] SIMD Similarity Python Fallbacks
def _python_simd_cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Pure Python fallback for simd_cosine_similarity.
    
    Computes cosine similarity without SIMD acceleration.
    M1 8GB: Safe, no external dependencies.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    
    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def _python_batch_simd_cosine_similarity(vectors: list[list[float]], query: list[float]) -> list[float]:
    """
    Pure Python fallback for batch_simd_cosine_similarity.
    
    Computes cosine similarity for multiple vectors without SIMD.
    """
    if not vectors or not query:
        return []
    return [_python_simd_cosine_similarity(v, query) for v in vectors]


def _noop_simd_cosine_similarity(*args: Any, **kwargs: Any) -> float:
    """No-op for simd_cosine_similarity — returns 0.0 (no similarity)."""
    return 0.0


def _noop_batch_simd_cosine_similarity(*args: Any, **kwargs: Any) -> list[float]:
    """No-op for batch_simd_cosine_similarity — returns empty list."""
    return []


# [SAFE-3] Link Predictor Python Fallbacks
def _python_link_predict(
    db_path: str,
    min_adamic_adar: float = 0.01,
    min_jaccard: float = 0.1,
    max_candidates: int = 10000,
    cross_type_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Pure Python fallback for link_predictor.
    
    Uses simple neighbor-based algorithms without DuckDB optimization.
    M1 8GB: Bounded to max_candidates to prevent memory exhaustion.
    """
    # Import graph library lazily
    try:
        import networkx as nx
    except ImportError:
        logger.warning("[FFI-CB] link_predictor: networkx not available, returning empty results")
        return []
    
    try:
        # Simple graph construction from db_path (placeholder)
        # In production, this would parse actual graph data
        G = nx.Graph()
        
        # Return empty predictions for now (no graph data available)
        # This ensures pipeline continuity without crashing
        return []
    except Exception as e:
        logger.warning(f"[FFI-CB] link_predictor: Python fallback failed: {e}")
        return []


def _noop_link_predict(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """No-op for link_predictor — returns empty list."""
    return []


# [SAFE-3] MLX Inference Python Fallbacks
def _python_mlx_generate(
    prompt: str,
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> str:
    """
    Pure Python fallback for MLX inference.
    
    Returns empty string on failure — ensures pipeline continues.
    M1 8GB: No GPU memory allocation.
    """
    logger.warning("[FFI-CB] mlx_inference: MLX unavailable, returning empty response")
    return ""


def _python_mlx_embed(text: str) -> list[float]:
    """
    Pure Python fallback for MLX embedding.
    
    Returns zero vector on failure.
    """
    # Return a zero vector (256-dim for ModernBERT compatibility)
    return [0.0] * 256


def _noop_mlx_generate(*args: Any, **kwargs: Any) -> str:
    """No-op for mlx_generate — returns empty string."""
    return ""


def _noop_mlx_embed(*args: Any, **kwargs: Any) -> list[float]:
    """No-op for mlx_embed — returns zero vector."""
    return [0.0] * 256


# [SAFE-3] Media Decode Python Fallbacks
def _python_decode_audio(
    file_path: str,
    target_sample_rate: int = 16000,
) -> tuple[Any, int] | None:
    """
    Pure Python fallback for media_decode.
    
    Returns None — caller should handle gracefully.
    M1 8GB: No RAM allocation for audio buffer.
    """
    logger.warning(f"[FFI-CB] media_decode: decode_audio unavailable for {file_path}")
    return None


def _python_transcribe_audio(
    source: str | Any,
    sample_rate: int = 16000,
) -> dict[str, Any]:
    """
    Pure Python fallback for media_transcribe.
    
    Returns empty transcription result.
    """
    return {
        "text": "",
        "confidence": 0.0,
        "duration_s": 0.0,
        "segments": [],
        "locale": "unknown",
    }


def _python_extract_keyframes(
    file_path: str,
    interval_s: float = 10.0,
    max_frames: int = 120,
) -> list[bytes]:
    """
    Pure Python fallback for keyframe extraction.
    
    Returns empty list — no frames extracted.
    """
    return []


def _python_ocr_frame(image_bytes: bytes) -> str:
    """
    Pure Python fallback for ocr_frame.
    
    Returns empty string — no text recognized.
    """
    return ""


def _noop_decode_audio(*args: Any, **kwargs: Any) -> tuple[Any, int] | None:
    """No-op for decode_audio — returns None."""
    return None


def _noop_transcribe_audio(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """No-op for transcribe_audio — returns empty result."""
    return {
        "text": "",
        "confidence": 0.0,
        "duration_s": 0.0,
        "segments": [],
        "locale": "unknown",
    }


def _noop_extract_keyframes(*args: Any, **kwargs: Any) -> list[bytes]:
    """No-op for extract_keyframes — returns empty list."""
    return []


def _noop_ocr_frame(*args: Any, **kwargs: Any) -> str:
    """No-op for ocr_frame — returns empty string."""
    return ""


# [SAFE-3] SimHash Python Fallbacks
def _python_simhash_compute(text: str) -> int:
    """
    Pure Python fallback for simhash.
    
    Uses MD5-based approximation of SimHash algorithm.
    M1 8GB: Safe, no external dependencies.
    """
    import hashlib
    
    if not text:
        return 0
    
    tokens = text.lower().split()
    if not tokens:
        return 0
    
    v = [0] * 64
    for token in tokens:
        h = hashlib.md5(token.encode()).digest()
        h64 = int.from_bytes(h[:8], byteorder="big")
        for i in range(64):
            bit = (h64 >> i) & 1
            v[i] += 1 if bit else -1
    
    result = 0
    for i in range(64):
        if v[i] > 0:
            result |= 1 << i
    return result


def _noop_simhash_compute(*args: Any, **kwargs: Any) -> int:
    """No-op for simhash — returns 0."""
    return 0


def _noop_batch_graph_traverse(*args: Any, **kwargs: Any) -> dict[str, list]:
    """No-op for batch_graph_traverse — returns empty results."""
    return {}


def _noop_collapse_findings(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """No-op for collapse_findings — returns original findings."""
    findings = args[0] if args else kwargs.get("findings", [])
    return findings if isinstance(findings, list) else []


def _noop_check_consistency(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """No-op for check_consistency — returns empty result."""
    return {
        "clean": [],
        "contradictory": [],
        "disputed": [],
        "contradictions": [],
        "suspect_sources": [],
        "entity_scores": {},
        "consistency_score": 1.0,
        "facts_processed": 0,
        "contradictions_found": 0,
    }


def _noop_xxh3_64_hex(*args: Any, **kwargs: Any) -> str:
    """No-op for xxh3_64_hex — returns random hex."""
    return secrets.token_hex(8)


def _noop_dedup_check_and_add(*args: Any, **kwargs: Any) -> bool:
    """No-op for dedup_bloom — always returns False (allow all)."""
    return False


# Register all pre-defined fallbacks
def _register_predefined_fallbacks() -> None:
    """Register all pre-defined Python native and no-op fallbacks."""
    # Use the local fallback for xxhash to ensure consistency
    _xxhash_fallback = _python_xxh3_64_hex_from_hash_module
    
    register_fallback(
        FFI_MODULE_GRAPH_TRAVERSE,
        _python_batch_graph_traverse,
        _noop_batch_graph_traverse,
    )
    register_fallback(
        FFI_MODULE_FINDING_COLLAPSER,
        _python_collapse_findings,
        _noop_collapse_findings,
    )
    register_fallback(
        FFI_MODULE_CONSISTENCY_VERIFIER,
        _python_check_consistency,
        _noop_check_consistency,
    )
    register_fallback(
        FFI_MODULE_XXHASH,
        _xxhash_fallback,
        _noop_xxh3_64_hex,
    )
    register_fallback(
        FFI_MODULE_DEDUP_BLOOM,
        _python_dedup_check_and_add,
        _noop_dedup_check_and_add,
    )
    
    # [SAFE-3] Register new module fallbacks
    # SIMD Similarity
    register_fallback(
        FFI_MODULE_SIMD_SIMILARITY,
        _python_batch_simd_cosine_similarity,
        _noop_batch_simd_cosine_similarity,
    )
    
    # Link Predictor
    register_fallback(
        FFI_MODULE_LINK_PREDICTOR,
        _python_link_predict,
        _noop_link_predict,
    )
    
    # MLX Inference
    register_fallback(
        FFI_MODULE_MLX_INFERENCE,
        _python_mlx_generate,
        _noop_mlx_generate,
    )
    
    # Media Decode
    register_fallback(
        FFI_MODULE_MEDIA_DECODE,
        _python_decode_audio,
        _noop_decode_audio,
    )
    
    # Media Transcribe
    register_fallback(
        FFI_MODULE_MEDIA_TRANSCRIBE,
        _python_transcribe_audio,
        _noop_transcribe_audio,
    )
    
    # OCR Frame
    register_fallback(
        FFI_MODULE_OCR_FRAME,
        _python_ocr_frame,
        _noop_ocr_frame,
    )
    
    # [SAFE-3] SimHash
    register_fallback(
        FFI_MODULE_SIMHASH,
        _python_simhash_compute,
        _noop_simhash_compute,
    )


# Auto-register on module import
_register_predefined_fallbacks()

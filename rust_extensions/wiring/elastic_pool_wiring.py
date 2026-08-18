"""
Elastic Pool Rust Integration Wiring
====================================

Wires rust_extensions/src/elastic_pool.rs to:
- coordinators/performance_coordinator.py
- _core/isolated_executors.py

Purpose:
- Adaptive thread pool sizing based on memory pressure
- CPU/IO/SIMD/MLX/Graph pool thread management
- M1 8GB safe: prevents OOM during MLX inference

Integration Point:
- PerformanceCoordinator.__init__() — initialize pools at startup
- PerformanceCoordinator._periodic_optimization() — adaptive resize
- Logging: logger.info(f"Elastic pools: cpu={cpu}, io={io}, mlx={mlx}")

B1 Implementation: Adaptive pool sizing for agent_pool_size

Usage:
    from rust_extensions.wiring.elastic_pool_wiring import elastic_pool_wired
    
    # Get all pool threads at startup
    threads = elastic_pool_wired.get_all_pool_threads()
    
    # Resize CPU pool based on memory pressure
    new_size = elastic_pool_wired.get_adaptive_cpu_pool_size(memory_pressure=0.7)
    elastic_pool_wired.resize_cpu_pool(new_size)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy loader for Rust elastic pool bindings
_RUST_ELASTIC: dict | None = None


def _get_elastic_rust() -> dict | None:
    """Lazily load Rust elastic pool bindings via rust.raw."""
    global _RUST_ELASTIC
    if _RUST_ELASTIC is not None:
        return _RUST_ELASTIC
    
    try:
        from hledac.universal._core.rust_backend import rust
        
        raw = rust.raw
        if raw is None:
            return None
        
        # Collect available functions
        fns = {
            "resize_cpu_pool": getattr(raw, "resize_cpu_pool", None),
            "resize_io_pool": getattr(raw, "resize_io_pool", None),
            "init_elastic_pools": getattr(raw, "init_elastic_pools", None),
            "get_cpu_threads": getattr(raw, "get_elastic_cpu_threads", None),
            "get_io_threads": getattr(raw, "get_elastic_io_threads", None),
            "get_total_threads": getattr(raw, "get_elastic_total_threads", None),
            "get_simd_pool_threads": getattr(raw, "get_simd_pool_threads", None),
            "get_mlx_pool_threads": getattr(raw, "get_mlx_pool_threads", None),
            "get_graph_pool_threads": getattr(raw, "get_graph_pool_threads", None),
            "get_all_pool_threads": getattr(raw, "get_all_pool_threads", None),
        }
        
        # Check required functions
        required = ["resize_cpu_pool", "resize_io_pool", "init_elastic_pools", 
                    "get_cpu_threads", "get_io_threads"]
        if any(fns.get(k) is None for k in required):
            logger.warning("[elastic_pool_wiring] Missing required Rust functions")
            return None
        
        _RUST_ELASTIC = fns
        return _RUST_ELASTIC
    except Exception as e:
        logger.warning(f"[elastic_pool_wiring] Failed to load Rust bindings: {e}")
        return None


class ElasticPoolWired:
    """
    Wired elastic pool manager for adaptive thread sizing.
    
    Provides a clean Python API over the Rust elastic_pool module with:
    - Lazy initialization
    - Graceful fallback when Rust is unavailable
    - Adaptive sizing based on memory pressure
    - Comprehensive pool statistics
    """
    
    __slots__ = ("_initialized", "_last_cpu", "_last_io", "_last_simd", "_last_mlx", "_last_graph")
    
    def __init__(self) -> None:
        self._initialized: bool = False
        self._last_cpu: int = 0
        self._last_io: int = 0
        self._last_simd: int = 0
        self._last_mlx: int = 0
        self._last_graph: int = 0
    
    def is_available(self) -> bool:
        """Check if Rust elastic pool bindings are available."""
        rust = _get_elastic_rust()
        return rust is not None
    
    def initialize(self) -> bool:
        """
        Initialize elastic pools from Rust.
        
        Returns:
            True if initialization succeeded.
        """
        if self._initialized:
            return True
        
        rust = _get_elastic_rust()
        if rust is None:
            logger.warning("[elastic_pool_wiring] Rust bindings unavailable — using Python fallback")
            return False
        
        try:
            cpu, io = rust["init_elastic_pools"]()
            self._last_cpu = cpu
            self._last_io = io
            self._initialized = True
            logger.info(f"[elastic_pool_wiring] Initialized: cpu={cpu}, io={io}")
            return True
        except Exception as e:
            logger.error(f"[elastic_pool_wiring] Init failed: {e}")
            return False
    
    def get_cpu_pool_threads(self) -> int:
        """Get current CPU pool thread count."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            threads = rust["get_cpu_threads"]()
            self._last_cpu = threads
            return threads
        except Exception:
            return self._last_cpu
    
    def get_io_pool_threads(self) -> int:
        """Get current I/O pool thread count."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            threads = rust["get_io_threads"]()
            self._last_io = threads
            return threads
        except Exception:
            return self._last_io
    
    def get_simd_pool_threads(self) -> int:
        """Get current SIMD pool thread count (dedicated ARM NEON pool)."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            threads = rust["get_simd_pool_threads"]()
            self._last_simd = threads
            return threads
        except Exception:
            return self._last_simd
    
    def get_mlx_pool_threads(self) -> int:
        """Get current MLX pool thread count (dedicated Metal pool)."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            threads = rust["get_mlx_pool_threads"]()
            self._last_mlx = threads
            return threads
        except Exception:
            return self._last_mlx
    
    def get_graph_pool_threads(self) -> int:
        """Get current Graph pool thread count (dedicated Kuzu pool)."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            threads = rust["get_graph_pool_threads"]()
            self._last_graph = threads
            return threads
        except Exception:
            return self._last_graph
    
    def get_all_pool_threads(self) -> int:
        """Get total threads across all pools (cpu + io + simd + mlx + graph)."""
        rust = _get_elastic_rust()
        if rust is None:
            return 0
        try:
            return rust["get_all_pool_threads"]()
        except Exception:
            # Fallback: sum individual pools
            return (self.get_cpu_pool_threads() + self.get_io_pool_threads() + 
                    self.get_simd_pool_threads() + self.get_mlx_pool_threads() + 
                    self.get_graph_pool_threads())
    
    def resize_cpu_pool(self, num_threads: int) -> int:
        """
        Resize CPU pool to specified thread count.
        
        Args:
            num_threads: Target thread count (clamped to 1-MAX_TOTAL_THREADS).
            
        Returns:
            Actual thread count after resize.
        """
        rust = _get_elastic_rust()
        if rust is None:
            return self._last_cpu
        try:
            actual = rust["resize_cpu_pool"](num_threads)
            self._last_cpu = actual
            return actual
        except Exception as e:
            logger.error(f"[elastic_pool_wiring] resize_cpu_pool({num_threads}) failed: {e}")
            return self._last_cpu
    
    def resize_io_pool(self, num_threads: int) -> int:
        """
        Resize I/O pool to specified thread count.
        
        Args:
            num_threads: Target thread count (clamped to budget).
            
        Returns:
            Actual thread count after resize.
        """
        rust = _get_elastic_rust()
        if rust is None:
            return self._last_io
        try:
            actual = rust["resize_io_pool"](num_threads)
            self._last_io = actual
            return actual
        except Exception as e:
            logger.error(f"[elastic_pool_wiring] resize_io_pool({num_threads}) failed: {e}")
            return self._last_io
    
    def get_adaptive_cpu_pool_size(self, memory_pressure: float = 0.5) -> int:
        """
        Calculate adaptive CPU pool size based on memory pressure.
        
        B1: This replaces the hardcoded agent_pool_size: int = 4.
        
        Memory pressure thresholds (M1 8GB optimized):
            - < 0.60 (idle):       MAX threads = 4
            - 0.60-0.75 (normal):  threads = 3
            - 0.75-0.85 (warning): threads = 2
            - > 0.85 (critical):   threads = 1
        
        Args:
            memory_pressure: Memory pressure ratio (0.0-1.0).
                           0.0 = idle, 1.0 = OOM risk.
        
        Returns:
            Recommended CPU pool size (1-4).
        """
        if memory_pressure < 0.60:
            return 4  # Idle: full capacity
        elif memory_pressure < 0.75:
            return 3  # Normal: slight reduction
        elif memory_pressure < 0.85:
            return 2  # Warning: safe mode
        else:
            return 1  # Critical: minimal threads
    
    def get_pool_stats(self) -> dict:
        """
        Get comprehensive pool statistics.
        
        Returns:
            Dict with all pool thread counts and totals.
        """
        cpu = self.get_cpu_pool_threads()
        io = self.get_io_pool_threads()
        simd = self.get_simd_pool_threads()
        mlx = self.get_mlx_pool_threads()
        graph = self.get_graph_pool_threads()
        
        return {
            "cpu": cpu,
            "io": io,
            "simd": simd,
            "mlx": mlx,
            "graph": graph,
            "total": cpu + io + simd + mlx + graph,
            "all_pool_threads": self.get_all_pool_threads(),
        }
    
    def adaptive_resize(self, memory_pressure: float) -> bool:
        """
        Perform adaptive resize based on memory pressure.
        
        B1: Core method for eliminating OOM during MLX inference.
        
        Args:
            memory_pressure: Current memory pressure ratio (0.0-1.0).
            
        Returns:
            True if resize was performed.
        """
        target = self.get_adaptive_cpu_pool_size(memory_pressure)
        current = self.get_cpu_pool_threads()
        
        if target != current:
            actual = self.resize_cpu_pool(target)
            logger.info(
                f"[elastic_pool_wiring] Adaptive resize: cpu {current} -> {actual} "
                f"(pressure={memory_pressure:.2f})"
            )
            return actual != current
        
        return False


# Singleton instance
_elastic_pool_wired: ElasticPoolWired | None = None


def elastic_pool_wired() -> ElasticPoolWired:
    """Get the wired elastic pool singleton."""
    global _elastic_pool_wired
    if _elastic_pool_wired is None:
        _elastic_pool_wired = ElasticPoolWired()
    return _elastic_pool_wired


def get_pool_stats() -> dict:
    """Get pool statistics (convenience function)."""
    return elastic_pool_wired().get_pool_stats()


def get_all_pool_threads() -> int:
    """Get total threads across all pools (convenience function)."""
    return elastic_pool_wired().get_all_pool_threads()


def resize_cpu_pool(num_threads: int) -> int:
    """Resize CPU pool (convenience function)."""
    return elastic_pool_wired().resize_cpu_pool(num_threads)


def adaptive_resize(memory_pressure: float) -> bool:
    """Perform adaptive resize based on memory pressure (convenience function)."""
    return elastic_pool_wired().adaptive_resize(memory_pressure)

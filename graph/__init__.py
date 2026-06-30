"""
Graph algorithms and quantum-inspired pathfinding for knowledge graphs.

This module provides:
- QuantumInspiredPathFinder: Quantum random walks on knowledge graphs
- QuantumPathConfig: Configuration for quantum pathfinding
- DuckPGQGraph: SQL/PGQ graph backend (DuckDB-backed)
- find_best_path: Convenience async wrapper for single-source/target pathfinding
"""


from typing import Any

# Graph Manager (pyvis visualization layer)
try:
    from .graph_manager import GRAPH_AVAILABLE, GraphManager
except ImportError:
    GRAPH_AVAILABLE = False
    GraphManager = None

# Quantum Pathfinder (lazy-loaded) — heavy MLX/scipy/numpy only via _get_*() helpers
try:
    from .quantum_pathfinder import (
        MAX_QUANTUM_EDGES,
        MAX_QUANTUM_NODES,
        QUANTUM_PATHFINDER_AVAILABLE,
        DuckPGQGraph,
        QuantumInspiredPathFinder,
        QuantumPathConfig,
        create_quantum_pathfinder,
        find_best_path,
    )
except ImportError:
    QUANTUM_PATHFINDER_AVAILABLE = False
    QuantumInspiredPathFinder = None
    QuantumPathConfig = None
    DuckPGQGraph = None
    MAX_QUANTUM_NODES = 4096
    MAX_QUANTUM_EDGES = 50000

    def create_quantum_pathfinder(config: Any = None) -> Any:
        """Factory function returning None when not available."""
        return None

    async def find_best_path(graph: Any, start: str, end: str) -> list[str]:
        """Stub returning [] when quantum_pathfinder is unavailable."""
        return []

__all__ = [
    # Graph Manager
    "GraphManager",
    "GRAPH_AVAILABLE",
    # Quantum Pathfinder
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "DuckPGQGraph",
    "create_quantum_pathfinder",
    "find_best_path",
    "MAX_QUANTUM_NODES",
    "MAX_QUANTUM_EDGES",
    "QUANTUM_PATHFINDER_AVAILABLE",
]

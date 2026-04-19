"""
Graph algorithms and quantum-inspired pathfinding for knowledge graphs.

This module provides:
- QuantumInspiredPathFinder: Quantum random walks on knowledge graphs
- QuantumPathConfig: Configuration for quantum pathfinding
"""

# Graph Manager (pyvis visualization layer)
try:
    from .graph_manager import GraphManager, GRAPH_AVAILABLE
except ImportError:
    GRAPH_AVAILABLE = False
    GraphManager = None

# Quantum Pathfinder (lazy-loaded)
try:
    from .quantum_pathfinder import (
        QuantumInspiredPathFinder,
        QuantumPathConfig,
        create_quantum_pathfinder,
    )
    QUANTUM_PATHFINDER_AVAILABLE = True
except ImportError:
    QUANTUM_PATHFINDER_AVAILABLE = False
    QuantumInspiredPathFinder = None
    QuantumPathConfig = None

    def create_quantum_pathfinder(config=None):
        """Factory function returning None when not available."""
        return None

__all__ = [
    # Graph Manager
    "GraphManager",
    "GRAPH_AVAILABLE",
    # Quantum Pathfinder
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "create_quantum_pathfinder",
    "QUANTUM_PATHFINDER_AVAILABLE",
]

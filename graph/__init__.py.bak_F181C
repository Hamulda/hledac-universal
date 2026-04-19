"""
Graph algorithms and quantum-inspired pathfinding for knowledge graphs.

This module provides:
- QuantumInspiredPathFinder: Quantum random walks on knowledge graphs
- QuantumPathConfig: Configuration for quantum pathfinding
"""

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
    # Quantum Pathfinder
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "create_quantum_pathfinder",
    "QUANTUM_PATHFINDER_AVAILABLE",
]

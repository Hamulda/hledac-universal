# graph.py — Graph domain
"""
Graph traversal domain for entity graph operations.

NOTE: batch_graph_traverse is wired DIRECTLY via hledac_rust_extensions
(prefetch_oracle_integration.py:420) — NOT through this domain layer,
because the domain method signature (root_ids, graph_path, max_depth, direction)
is incompatible with the standalone Rust function signature
(db_path, source_values, max_hops).

This module provides the graph domain object expected by RustBackend.graph
property. The actual rayon-parallel traversal lives in the standalone
hledac_rust_extensions.batch_graph_traverse function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _GraphDomain:
    """Minimal domain stub — satisfies RustBackend.graph property contract.

    Actual graph traversal is handled by standalone hledac_rust_extensions
    functions (batch_graph_traverse, graph_traverse_single) wired directly
    in prefetch_oracle_integration.py and quantum_pathfinder.py.
    """
    __slots__ = ()

    def batch_graph_traverse(
        self,
        root_ids: list[int],
        graph_path: str,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        # Graph traversal via standalone Rust (prefetch_oracle) or
        # DuckPGQ path in quantum_pathfinder._find_connected_base.
        # This stub exists only to satisfy the domain property contract.
        return []


def get_graph_domain(ext: object | None) -> _GraphDomain:
    """Factory: returns _GraphDomain (graph traversal lives in standalone Rust)."""
    return _GraphDomain()

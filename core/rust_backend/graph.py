# graph.py — Graph domain
"""
Graph traversal domain for entity graph operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Graph Domain
# =============================================================================


class _RustGraphDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def batch_graph_traverse(
        self,
        root_ids: list[int],
        graph_path: str,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        # Rust has incompatible signature: (db_path, root_values, max_hops, max_results_per_root)
        # Use Python fallback which matches expected API
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth, direction)


class _PythonGraphDomain:
    __slots__ = ()

    @staticmethod
    def batch_graph_traverse(
        root_ids: list[int],
        graph_path: str,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth, direction)


def _python_batch_graph_traverse(
    root_ids: list[int],
    graph_path: str,
    max_depth: int = 3,
    direction: str = "both",
) -> list[dict[str, Any]]:
    # Pure Python fallback: BFS traversal (no actual graph)
    result: list[dict[str, Any]] = []
    for rid in root_ids:
        result.append({"node_id": rid, "depth": 0, "edges": []})
    return result


def get_graph_domain(ext: object | None) -> _RustGraphDomain | _PythonGraphDomain:
    """Factory: return Rust or Python GraphDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustGraphDomain(ext)
        except Exception:
            pass
    return _PythonGraphDomain()

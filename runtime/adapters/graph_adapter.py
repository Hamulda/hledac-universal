"""
runtime/adapters/graph_adapter.py — F270: DuckPGQ Graph Adapter
=============================================================

Adapter implementing GraphProtocol for DuckPGQGraph.
Non-breaking: wraps existing graph service without changes.

GHOST_INVARIANTS:
- Fail-safe: upsert returns False on error
- Bounded: entity/claim limits enforced by underlying service
"""

from __future__ import annotations

from typing import Any

from runtime.protocols.graph_protocol import GraphProtocol


class DuckPGQGraphAdapter(GraphProtocol):
    """
    Adapter wrapping DuckPGQGraph to implement GraphProtocol.

    Non-breaking: wraps existing graph service and delegates
    to it without changing behavior.

    Usage:
        graph = DuckPGQGraph(...)
        adapter = DuckPGQGraphAdapter(graph)
        # Use as GraphProtocol
        await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id)
    """

    __slots__ = ('_graph',)

    def __init__(self, graph: Any) -> None:
        """
        Initialize adapter with existing DuckPGQGraph.

        Args:
            graph: DuckPGQGraph instance to wrap
        """
        self._graph = graph

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Delegate IOC upsert to graph."""
        try:
            return await self._graph.upsert_ioc(
                ioc_value, ioc_type, sprint_id, properties
            )
        except Exception:
            return False

    def find_connected(
        self, ioc_value: str, max_depth: int = 2
    ) -> list[dict[str, Any]]:
        """Delegate graph traversal to underlying service."""
        try:
            return self._graph.find_connected(ioc_value, max_depth)
        except Exception:
            return []

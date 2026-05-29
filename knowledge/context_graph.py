"""
ContextGraph - Lightweight In-Memory Context Tracker
====================================================

DEPRECATED: This module is a simple in-memory context tracker.
It is NOT a storage backend and does NOT persist data.
Do NOT use as authoritative graph storage.

For persistent knowledge graph storage, use:
- IOCGraph (KuzuDB) for IOC entity truth store
- DuckPGQGraph (DuckDB) for analytics donor backend
"""

from __future__ import annotations

import json
from typing import Any


class ContextGraph:
    """
    DEPRECATED: Simple in-memory context graph.

    NOT a storage backend — data is not persisted.
    Use IOCGraph (KuzuDB) for authoritative IOC storage.
    """

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    def add_node(
        self, node_id: str, node_type: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Adds a node to the graph."""
        if not any(n["id"] == node_id for n in self.nodes):
                self.nodes.append({"id": node_id, "type": node_type, "attributes": attributes or {}})

    def add_edge(
        self, source: str, target: str, edge_type: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Adds an edge to the graph."""
        self.edges.append(
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "attributes": attributes or {},
            }
        )

    def to_json(self) -> str:
        """Serializes the graph to a JSON string."""
        return json.dumps({"nodes": self.nodes, "edges": self.edges}, indent=2)

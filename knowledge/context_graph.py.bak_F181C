from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ContextGraph:
    """A simple in-memory context graph."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []

    def add_node(
        self, node_id: str, node_type: str, attributes: Optional[Dict[str, Any]] = None
    ) -> None:
        """Adds a node to the graph."""
        if not any(n["id"] == node_id for n in self.nodes):
                self.nodes.append({"id": node_id, "type": node_type, "attributes": attributes or {}})

    def add_edge(
        self, source: str, target: str, edge_type: str, attributes: Optional[Dict[str, Any]] = None
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

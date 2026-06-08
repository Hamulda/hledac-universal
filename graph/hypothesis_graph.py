"""
Hypothesis graph — bounded stub for Sprint F196B probe suite.

Real implementation lives in legacy/.  This stub provides the
dataclasses + MAX_* constants that `tests/test_hypothesis_builder.py`
imports.  No I/O, no network, no MLX.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_NODES: int = 5_000
MAX_EDGES: int = 20_000


@dataclass
class HypothesisNode:
    """Single node in the hypothesis graph."""
    node_id: str
    label: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HypothesisEdge:
    """Directed edge between two hypothesis nodes."""
    edge_id: str
    source_id: str
    target_id: str
    weight: float = 1.0
    rationale: str = ""


@dataclass
class HiddenBridge:
    """Latent edge discovered by the pathfinder."""
    bridge_id: str
    endpoint_a: str
    endpoint_b: str
    score: float = 0.0


@dataclass
class AnomalousCluster:
    """A cluster whose edge density or weight distribution is anomalous."""
    cluster_id: str
    node_ids: list[str] = field(default_factory=list)
    anomaly_score: float = 0.0


class HypothesisGraph:
    """Minimal in-memory graph — no I/O."""

    def __init__(self, max_nodes: int = MAX_NODES, max_edges: int = MAX_EDGES) -> None:
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self._nodes: dict[str, HypothesisNode] = {}
        self._edges: dict[str, HypothesisEdge] = {}

    def add_node(self, node: HypothesisNode) -> bool:
        if len(self._nodes) >= self.max_nodes:
            return False
        self._nodes[node.node_id] = node
        return True

    def add_edge(self, edge: HypothesisEdge) -> bool:
        if len(self._edges) >= self.max_edges:
            return False
        self._edges[edge.edge_id] = edge
        return True

    def nodes(self) -> list[HypothesisNode]:
        return list(self._nodes.values())

    def edges(self) -> list[HypothesisEdge]:
        return list(self._edges.values())


__all__ = [
    "HypothesisGraph",
    "HypothesisNode",
    "HypothesisEdge",
    "HiddenBridge",
    "AnomalousCluster",
    "MAX_NODES",
    "MAX_EDGES",
]

"""
Hypothesis graph — bounded in-memory graph for Sprint F259.

Supports two call conventions for ``HypothesisEdge``:
  * canonical: ``edge_id, source_id, target_id, weight, rationale, ...``
  * legacy / test-friendly: ``source, target, hypothesis_type, statement,
    confidence, supporting_sources, temporal_sequence``

Legacy kwargs are resolved in ``__init__``; the canonical attribute names
remain the source of truth for serialization.
"""


from collections import deque
from dataclasses import dataclass, field
import msgspec
from typing import Any

MAX_NODES: int = 5_000
MAX_EDGES: int = 20_000


@dataclass
class HypothesisNode:
    """Single node in the hypothesis graph."""
    node_id: str
    label: str
    payload: dict[str, Any] = field(default_factory=dict)


class HypothesisEdge:
    """Directed edge between two hypothesis nodes.

    Accepts both canonical kwargs (``source_id``/``target_id``/``weight``/
    ``rationale``) and legacy kwargs (``source``/``target``/
    ``hypothesis_type``/``statement``/``confidence``/
    ``supporting_sources``/``temporal_sequence``) used by the test suite.
    """

    __slots__ = (
        "edge_id",
        "source_id",
        "target_id",
        "weight",
        "rationale",
        "hypothesis_type",
        "supporting_sources",
        "temporal_sequence",
    )

    def __init__(
        self,
        edge_id: str | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
        weight: float = 1.0,
        rationale: str = "",
        # Legacy / test-friendly aliases
        source: str | None = None,
        target: str | None = None,
        hypothesis_type: str = "causal",
        statement: str | None = None,
        confidence: float | None = None,
        supporting_sources: tuple[str, ...] = (),
        temporal_sequence: tuple[Any, ...] = (),
    ) -> None:
        # Resolve source/target aliases
        if source_id is None and source is not None:
            source_id = source
        if target_id is None and target is not None:
            target_id = target
        if source_id is None or target_id is None:
            raise ValueError(
                "HypothesisEdge requires source and target "
                "(use source_id/target_id or source/target)"
            )
        # Resolve statement/confidence aliases
        if statement is not None:
            rationale = statement
        if confidence is not None:
            weight = confidence
        # Auto-derive edge_id if not provided
        if edge_id is None:
            edge_id = f"{source_id}->{target_id}"
        self.edge_id = edge_id
        self.source_id = source_id
        self.target_id = target_id
        self.weight = float(weight)
        self.rationale = rationale
        self.hypothesis_type = hypothesis_type
        self.supporting_sources = tuple(supporting_sources)
        self.temporal_sequence = tuple(temporal_sequence)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HypothesisEdge(edge_id={self.edge_id!r}, "
            f"source_id={self.source_id!r}, target_id={self.target_id!r}, "
            f"weight={self.weight}, type={self.hypothesis_type!r})"
        )


@dataclass(frozen=True)
class HiddenBridge:
    """Latent edge discovered by the pathfinder."""
    bridge_id: str
    endpoint_a: str
    endpoint_b: str
    score: float = 0.0


@dataclass(frozen=True)
class AnomalousCluster:
    """A cluster whose edge density or weight distribution is anomalous."""
    cluster_id: str
    node_ids: list[str] = field(default_factory=list)
    anomaly_score: float = 0.0


class HypothesisGraph:
    """Bounded in-memory graph with both test-stub and full API surface.

    Backwards-compatible with the minimal stub used by other test suites:
    ``add_node``/``add_edge``/``nodes``/``edges`` still work, while
    ``add_entity``/``add_hypothesis_edge``/``node_count``/``edge_count``/
    ``get_entity_type``/``find_hidden_bridges``/
    ``detect_anomalous_clusters``/``to_dict``/``from_dict``/
    ``to_stix_bundle`` round out the F259 contract.
    """

    def __init__(
        self,
        max_nodes: int = MAX_NODES,
        max_edges: int = MAX_EDGES,
    ) -> None:
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self._nodes: dict[str, HypothesisNode] = {}
        self._edges: dict[str, HypothesisEdge] = {}
        self._entity_types: dict[str, str] = {}
        # Bounded BFS scratch buffer — never grows past max_nodes.
        self._bfs_scratch: deque[str] = deque(maxlen=max_nodes)

    # ------------------------------------------------------------------
    # Node / edge insertion (test-friendly API)
    # ------------------------------------------------------------------

    def add_entity(self, entity_id: str, entity_type: str) -> bool:
        """Add an entity node. Returns False if duplicate or at capacity."""
        if entity_id in self._entity_types:
            return False
        if len(self._nodes) >= self.max_nodes:
            return False
        self._nodes[entity_id] = HypothesisNode(
            node_id=entity_id,
            label=entity_id,
            payload={"entity_type": entity_type},
        )
        self._entity_types[entity_id] = entity_type
        return True

    def add_hypothesis_edge(self, edge: HypothesisEdge) -> bool:
        """Add a hypothesis edge. Returns False if at capacity."""
        if len(self._edges) >= self.max_edges:
            return False
        self._edges[edge.edge_id] = edge
        return True

    # ------------------------------------------------------------------
    # Backwards-compatible minimal API
    # ------------------------------------------------------------------

    def add_node(self, node: HypothesisNode) -> bool:
        if len(self._nodes) >= self.max_nodes:
            return False
        self._nodes[node.node_id] = node
        etype = node.payload.get("entity_type") if isinstance(node.payload, dict) else None
        if etype is not None:
            self._entity_types[node.node_id] = etype
        return True

    def add_edge(self, edge: HypothesisEdge) -> bool:
        return self.add_hypothesis_edge(edge)

    def nodes(self) -> list[HypothesisNode]:
        return list(self._nodes.values())

    def edges(self) -> list[HypothesisEdge]:
        return list(self._edges.values())

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def get_entity_type(self, entity_id: str) -> str | None:
        return self._entity_types.get(entity_id)

    # ------------------------------------------------------------------
    # Bounded analytics
    # ------------------------------------------------------------------

    def _adjacency(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {nid: set() for nid in self._nodes}
        for e in self._edges.values():
            if e.source_id in adj and e.target_id in adj:
                adj[e.source_id].add(e.target_id)
                adj[e.target_id].add(e.source_id)
        return adj

    def _bfs_reachable(
        self, start: str, skip: str | None, adj: dict[str, set[str]]
    ) -> set[str]:
        seen: set[str] = {start}
        self._bfs_scratch.clear()
        self._bfs_scratch.append(start)
        while self._bfs_scratch:
            node = self._bfs_scratch.popleft()
            for neighbor in adj.get(node, ()):
                if neighbor == skip or neighbor in seen:
                    continue
                seen.add(neighbor)
                self._bfs_scratch.append(neighbor)
        return seen

    def find_hidden_bridges(self) -> list[HiddenBridge]:
        """Nodes that, if removed, would disconnect at least two neighbors.

        Naive articulation-point heuristic — bounded by max_nodes BFS, no
        external graph library required.
        """
        if len(self._nodes) < 3:
            return []
        adj = self._adjacency()
        bridges: list[HiddenBridge] = []
        for nid, neighbors in adj.items():
            if len(neighbors) < 2:
                continue
            neighbor_list = sorted(neighbors)
            seed = neighbor_list[0]
            reachable = self._bfs_reachable(seed, skip=nid, adj=adj)
            for other in neighbor_list[1:]:
                if other not in reachable:
                    bridges.append(
                        HiddenBridge(
                            bridge_id=f"bridge::{nid}::{other}",
                            endpoint_a=seed,
                            endpoint_b=other,
                            score=1.0,
                        )
                    )
                    break  # one bridge per node is enough
        return bridges

    def detect_anomalous_clusters(self) -> list[AnomalousCluster]:
        """Flag connected components with density > 0.5 as anomalous."""
        if not self._nodes:
            return []
        adj = self._adjacency()
        visited: set[str] = set()
        components: list[set[str]] = []
        for nid in self._nodes:
            if nid in visited:
                continue
            comp = self._bfs_reachable(nid, skip=None, adj=adj)
            visited |= comp
            components.append(comp)
        anomalies: list[AnomalousCluster] = []
        for idx, comp in enumerate(components):
            n = len(comp)
            if n < 2:
                continue
            edges_in_comp = sum(
                1
                for e in self._edges.values()
                if e.source_id in comp and e.target_id in comp
            )
            max_edges = n * (n - 1)
            density = (2 * edges_in_comp / max_edges) if max_edges else 0.0
            if density > 0.5:
                anomalies.append(
                    AnomalousCluster(
                        cluster_id=f"cluster::{idx}",
                        node_ids=sorted(comp),
                        anomaly_score=density,
                    )
                )
        return anomalies

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "label": n.label,
                    "payload": dict(n.payload),
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "weight": e.weight,
                    "rationale": e.rationale,
                    "hypothesis_type": e.hypothesis_type,
                    "supporting_sources": list(e.supporting_sources),
                    "temporal_sequence": list(e.temporal_sequence),
                }
                for e in self._edges.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HypothesisGraph:
        graph = cls(
            max_nodes=int(data.get("max_nodes", MAX_NODES)),
            max_edges=int(data.get("max_edges", MAX_EDGES)),
        )
        for n in data.get("nodes", []):
            node = HypothesisNode(
                node_id=n["node_id"],
                label=n.get("label", n["node_id"]),
                payload=dict(n.get("payload", {})),
            )
            graph._nodes[node.node_id] = node
            etype = node.payload.get("entity_type") if isinstance(node.payload, dict) else None
            if etype is not None:
                graph._entity_types[node.node_id] = etype
        for e in data.get("edges", []):
            edge = HypothesisEdge(
                edge_id=e["edge_id"],
                source_id=e["source_id"],
                target_id=e["target_id"],
                weight=float(e.get("weight", 1.0)),
                rationale=e.get("rationale", ""),
                hypothesis_type=e.get("hypothesis_type", "causal"),
                supporting_sources=tuple(e.get("supporting_sources", ())),
                temporal_sequence=tuple(e.get("temporal_sequence", ())),
            )
            graph._edges[edge.edge_id] = edge
        return graph

    def to_stix_bundle(self) -> dict[str, Any]:
        """Emit a minimal STIX 2.1 bundle (identity + relationship objects)."""
        objects: list[dict[str, Any]] = []
        for node in self._nodes.values():
            etype = self._entity_types.get(node.node_id, "unknown")
            objects.append(
                {
                    "type": "identity",
                    "spec_version": "2.1",
                    "id": f"identity--{node.node_id}",
                    "created": "1970-01-01T00:00:00.000Z",
                    "modified": "1970-01-01T00:00:00.000Z",
                    "name": node.label,
                    "identity_class": etype,
                }
            )
        for edge in self._edges.values():
            objects.append(
                {
                    "type": "relationship",
                    "spec_version": "2.1",
                    "id": f"relationship--{edge.edge_id}",
                    "created": "1970-01-01T00:00:00.000Z",
                    "modified": "1970-01-01T00:00:00.000Z",
                    "relationship_type": edge.hypothesis_type or "related-to",
                    "source_ref": f"identity--{edge.source_id}",
                    "target_ref": f"identity--{edge.target_id}",
                    "description": edge.rationale,
                }
            )
        return {
            "type": "bundle",
            "id": f"bundle--{id(self):x}",
            "spec_version": "2.1",
            "objects": objects,
        }


__all__ = [
    "HypothesisGraph",
    "HypothesisNode",
    "HypothesisEdge",
    "HiddenBridge",
    "AnomalousCluster",
    "MAX_NODES",
    "MAX_EDGES",
]

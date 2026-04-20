"""
GraphManager — networkx + pyvis visualization layer.

FÁZE P9: Knowledge graph a vizualizace.
Anti-patterns: žádné velké grafové DB, žádné detailní atributy (M1 8GB).

Streamované přidávání uzlů/hran pro paměťovou efektivitu.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["GraphManager", "GRAPH_AVAILABLE"]

GRAPH_AVAILABLE = True

# Lazy-first: light deps only at module level
_NETWORKX_AVAILABLE = True
_PYVIS_AVAILABLE = True


class GraphManager:
    """
    Lightweight graph visualization using networkx + pyvis.

    Anti-patterns enforced:
    - Žádné velké grafové DB — pouze networkx.Graph (in-memory)
    - Žádné detailní atributy — only entity_type + value per node
    - Streamované přidávání uzlů — žádné batch bulk operations

    Methods:
    - add_entity(entity_type, value): add node with attributes
    - add_relation(source, target, relation_type): add edge
    - export_html(path): render to interactive HTML via pyvis
    """

    def __init__(self) -> None:
        # Lazy import to avoid paying networkx cost when not used
        self._nx = self._get_networkx()
        self._graph = self._nx.Graph()
        self._node_count = 0

    @staticmethod
    def _get_networkx() -> Any:
        global _NETWORKX_AVAILABLE
        if not _NETWORKX_AVAILABLE:
            raise ImportError("networkx not available")
        import networkx as nx
        return nx

    def add_entity(self, entity_type: str, value: str) -> None:
        """
        Add a node with entity_type and value attributes.

        Streamované přidávání — žádné batch operace.
        Paměť: M1 8GB budget — pouze lightweight atributy.
        """
        if not value or not value.strip():
            return
        node_id = f"{entity_type}:{value}"
        if node_id in self._graph:
            return  # already exists — skip

        self._graph.add_node(
            node_id,
            entity_type=entity_type,
            value=value,
            label=self._short_label(entity_type, value),
        )
        self._node_count += 1

    @staticmethod
    def _short_label(entity_type: str, value: str) -> str:
        """Krátký label pro vizualizaci — max 40 znaků."""
        short = f"{entity_type}:{value}"
        return short[:40] + "…" if len(short) > 40 else short

    def add_relation(
        self, source: str, target: str, relation_type: str
    ) -> None:
        """
        Add an edge between two entities.

        Streamované přidávání — voláno po každé extrakci IOC.
        """
        src_id = f"domain:{source}" if ":" not in source else source
        dst_id = f"domain:{target}" if ":" not in target else target

        # Auto-create nodes if they don't exist
        for node_id, etype, evalue in [
            (src_id, *self._parse_entity(source)),
            (dst_id, *self._parse_entity(target)),
        ]:
            if node_id not in self._graph:
                self._graph.add_node(
                    node_id,
                    entity_type=etype,
                    value=evalue,
                    label=self._short_label(etype, evalue),
                )
                self._node_count += 1

        edge_id = (src_id, dst_id)
        if self._graph.has_edge(*edge_id):
            return  # already exists — skip

        self._graph.add_edge(
            src_id,
            dst_id,
            relation_type=relation_type,
            label=relation_type,
        )

    @staticmethod
    def _parse_entity(entity: str) -> tuple[str, str]:
        """Parse entity string into (entity_type, value)."""
        if ":" in entity:
            parts = entity.split(":", 1)
            return parts[0], parts[1]
        # Default: treat as domain if looks like one
        if "." in entity and not entity.startswith(("0x", "CVE", "GHSA")):
            return "domain", entity
        return "entity", entity

    def node_count(self) -> int:
        """Return current node count."""
        return self._node_count

    def edge_count(self) -> int:
        """Return current edge count."""
        return self._graph.number_of_edges()

    def to_networkx(self) -> Any:
        """
        FÁZE P18: Return internal networkx graph for external use.

        Returns:
            networkx.Graph: copy of internal graph with all nodes and edges
        """
        return self._graph.copy()

    async def find_path(self, start_entity: str, end_entity: str) -> list[str]:
        """
        FÁZE P14: Find path between two entities using quantum pathfinder.

        Args:
            start_entity: Start entity string (e.g., 'example.com')
            end_entity: End entity string (e.g., 'target.com')

        Returns:
            List of node IDs forming the path, or empty list if no path found.
        """
        from hledac.universal.graph.quantum_pathfinder import find_best_path

        try:
            # Normalize entity strings to node IDs
            start_id = start_entity if f"domain:{start_entity}" in self._graph else f"domain:{start_entity}"
            end_id = end_entity if f"domain:{end_entity}" in self._graph else f"domain:{end_entity}"

            # Use the internal networkx graph
            path = await find_best_path(self._graph, start_id, end_id)
            return path
        except Exception as e:
            logger.warning(f"[GraphManager] find_path failed: {e}")
            return []

    def export_html(self, path: str) -> None:
        """
        Export graph to interactive HTML using pyvis.

        Falls back to simple edge-list text export if pyvis unavailable.
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        try:
            from pyvis.network import Network
        except ImportError:
            logger.warning("[GraphManager] pyvis not available, falling back to text export")
            self._export_text(path)
            return

        try:
            net = Network(
                height="750px",
                width="100%",
                bgcolor="#1a1a2e",
                font_color="white",
                directed=False,
            )
            net.barnes_hut(
                gravity=-5000,
                central_gravity=0.01,
                spring_length=150,
                spring_strength=0.02,
            )

            # Add nodes with pyvis styling
            for node_id, data in self._graph.nodes(data=True):
                entity_type = data.get("entity_type", "unknown")
                # Color by entity type
                color_map = {
                    "domain": "#00ff88",
                    "ipv4": "#ff6b6b",
                    "ipv6": "#ff8787",
                    "url": "#ffd93d",
                    "cve": "#ff4757",
                    "hash": "#a55eea",
                    "email": "#26de81",
                    "domain": "#00ff88",
                }
                color = color_map.get(entity_type.lower(), "#70a1ff")

                net.add_node(
                    node_id,
                    label=data.get("label", node_id),
                    title=f"{entity_type}\n{data.get('value', '')}",
                    color=color,
                    size=20,
                )

            # Add edges
            for src, dst, edata in self._graph.edges(data=True):
                rel = edata.get("relation_type", "related")
                net.add_edge(
                    src,
                    dst,
                    title=rel,
                    label=rel[:20],
                )

            net.save_graph(path)
            logger.info(f"[GraphManager] Exported HTML graph to {path}")

        except Exception as e:
            logger.warning(f"[GraphManager] HTML export failed: {e}, falling back to text")
            self._export_text(path)

    def _export_text(self, path: str) -> None:
        """Fallback: plain text edge-list export."""
        with open(path, "w") as f:
            f.write("# Hledac Entity Graph\n\n")
            f.write(f"# Nodes: {self._node_count}, Edges: {self._graph.number_of_edges()}\n\n")
            f.write("## Nodes\n")
            for node_id, data in self._graph.nodes(data=True):
                f.write(f"  {node_id}\n")
            f.write("\n## Edges\n")
            for src, dst, edata in self._graph.edges(data=True):
                rel = edata.get("relation_type", "related")
                f.write(f"  {src} --[{rel}]--> {dst}\n")
        logger.info(f"[GraphManager] Exported text graph to {path}")

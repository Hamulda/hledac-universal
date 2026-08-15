"""
GraphManager — rustworkx + pyvis visualization layer.

P2-5d: Migrated from igraph to rustworkx (2026-07-17).

rustworkx is a Rust-based graph library (3-10x faster than igraph, M1-optimized).
python-igraph removed from dependencies; rustworkx was already present.

Anti-patterns enforced:
- Žádné velké grafové DB — pouze rustworkx.PyGraph (in-memory)
- Žádné detailní atributy — only entity_type + value per node
- Streamované přidávání uzlů — žádné batch bulk operations

Methods:
- add_entity(entity_type, value): add node with attributes
- add_relation(source, target, relation_type): add edge
- export_html(path): render to interactive HTML via pyvis
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any
from core import aclose

if TYPE_CHECKING:
    import rustworkx as rx

logger = logging.getLogger(__name__)
__all__ = ['GraphManager', 'GRAPH_AVAILABLE']
GRAPH_AVAILABLE = True
_PYVIS_AVAILABLE = True


class GraphManager:
    """
    Lightweight graph visualization using rustworkx + pyvis.

    P2-5d: Uses rustworkx.PyGraph instead of igraph.Graph.
    rustworkx is a Rust-based graph library with superior performance on M1.

    Anti-patterns enforced:
    - Žádné velké grafové DB — pouze rustworkx.PyGraph (in-memory)
    - Žádné detailní atributy — only entity_type + value per node
    - Streamované přidávání uzlů — žádné batch bulk operations

    Methods:
    - add_entity(entity_type, value): add node with attributes
    - add_relation(source, target, relation_type): add edge
    - export_html(path): render to interactive HTML via pyvis
    """
    __slots__ = ('_graph', '_rx', '_node_index', '_node_attrs')

    def __init__(self) -> None:
        self._rx = self._get_rustworkx()
        # rustworkx PyGraph is undirected by default (unlike igraph's explicit directed=False)
        self._graph = self._rx.PyGraph()
        self._node_index: dict[str, int] = {}
        # Node attributes: node_id -> {'entity_type': str, 'value': str, 'label': str}
        self._node_attrs: dict[int, dict[str, str]] = {}

    @staticmethod
    def _get_rustworkx() -> Any:
        import rustworkx as rx
        return rx

    def add_entity(self, entity_type: str, value: str) -> None:
        """
        Add a node with entity_type and value attributes.

        Streamované přidávání — žádné batch operace.
        Paměť: M1 8GB budget — pouze lightweight atributy.
        """
        if not value or not value.strip():
            return
        node_id = f'{entity_type}:{value}'
        if node_id in self._node_index:
            return
        # rustworkx add_node returns the node index
        node_idx = self._graph.add_node(node_id)
        self._node_index[node_id] = node_idx
        self._node_attrs[node_idx] = {
            'entity_type': entity_type,
            'value': value,
            'label': self._short_label(entity_type, value),
            'name': node_id,
        }

    @staticmethod
    def _short_label(entity_type: str, value: str) -> str:
        """Krátký label pro vizualizaci — max 40 znaků."""
        short = f'{entity_type}:{value}'
        return short[:40] + '…' if len(short) > 40 else short

    def add_relation(self, source: str, target: str, relation_type: str) -> None:
        """
        Add an edge between two entities.

        Streamované přidávání — voláno po každé extrakci IOC.
        """
        # Parse entities once - get (entity_type, value) tuples
        src_type, src_value = self._parse_entity(source)
        dst_type, dst_value = self._parse_entity(target)
        # Build node_ids from parsed components (consistent with add_entity)
        src_id = f'{src_type}:{src_value}'
        dst_id = f'{dst_type}:{dst_value}'
        # Ensure both nodes exist
        for node_id, etype, evalue in [(src_id, src_type, src_value), (dst_id, dst_type, dst_value)]:
            if node_id not in self._node_index:
                node_idx = self._graph.add_node(node_id)
                self._node_index[node_id] = node_idx
                self._node_attrs[node_idx] = {
                    'entity_type': etype,
                    'value': evalue,
                    'label': self._short_label(etype, evalue),
                    'name': node_id,
                }
        src_idx = self._node_index[src_id]
        dst_idx = self._node_index[dst_id]
        # Check if edge already exists
        try:
            self._graph.get_edge_data(src_idx, dst_idx)
            return
        except Exception:  # noqa: BLE001
            # No edge exists, add new one
            pass
        # Add edge with relation_type as edge weight/data
        self._graph.add_edge(src_idx, dst_idx, relation_type)

    @staticmethod
    def _parse_entity(entity: str) -> tuple[str, str]:
        """Parse entity string into (entity_type, value)."""
        if ':' in entity:
            parts = entity.split(':', 1)
            return (parts[0], parts[1])
        if '.' in entity and (not entity.startswith(('0x', 'CVE', 'GHSA'))):
            return ('domain', entity)
        return ('entity', entity)

    def node_count(self) -> int:
        """Return current node count."""
        return len(self._node_index)

    def edge_count(self) -> int:
        """Return current edge count."""
        return self._graph.num_edges()

    def to_rustworkx(self) -> Any:
        """
        Return internal rustworkx graph for external use.

        Returns:
            rustworkx.PyGraph: copy of internal graph with all nodes and edges
        """
        # rustworkx PyGraph doesn't have a copy() method, so we rebuild
        g = self._rx.PyGraph()
        idx_map = {}  # old_idx -> new_idx
        for node_id, old_idx in self._node_index.items():
            new_idx = g.add_node(node_id)
            idx_map[old_idx] = new_idx
        # Re-add all edges
        for old_src, old_dst in self._graph.edge_indices():
            edge_data = self._graph.get_edge_data(old_src, old_dst)
            if old_src in idx_map and old_dst in idx_map:
                g.add_edge(idx_map[old_src], idx_map[old_dst], edge_data)
        return g

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
            start_id = start_entity if start_entity in self._node_index else f'domain:{start_entity}'
            end_id = end_entity if end_entity in self._node_index else f'domain:{end_entity}'
            # Build adjacency dict for rustworkx graph
            node_ids_by_idx: dict[int, str] = {}
            for nid, nidx in self._node_index.items():
                node_ids_by_idx[nidx] = nid
            adj_dict: dict[str, list[str]] = {}
            for node_idx in self._graph.node_indices():
                node_id = node_ids_by_idx.get(node_idx, '')
                neighbors = []
                for neighbor_idx in self._graph.neighbors(node_idx):
                    neighbor_id = node_ids_by_idx.get(neighbor_idx, '')
                    if neighbor_id:
                        neighbors.append(neighbor_id)
                adj_dict[node_id] = neighbors
            path = await find_best_path(adj_dict, start_id, end_id)
            return path
        except Exception as e:
            logger.warning(f'[GraphManager] find_path failed: {e}')
            return []

    def export_html(self, path: str) -> None:
        """
        Export graph to interactive HTML using pyvis.

        Falls back to simple edge-list text export if pyvis unavailable.
        """
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        try:
            from pyvis.network import Network
        except ImportError:
            logger.warning('[GraphManager] pyvis not available, falling back to text export')
            self._export_text(path)
            return
        try:
            net = Network(height='750px', width='100%', bgcolor='#1a1a2e', font_color='white', directed=False)
            net.barnes_hut(gravity=-5000, central_gravity=0.01, spring_length=150, spring_strength=0.02)
            # Build reverse index: idx -> node_id
            node_ids_by_idx: dict[int, str] = {}
            for nid, nidx in self._node_index.items():
                node_ids_by_idx[nidx] = nid
            color_map = {'domain': '#00ff88', 'ipv4': '#ff6b6b', 'ipv6': '#ff8787', 'url': '#ffd93d', 'cve': '#ff4757', 'hash': '#a55eea', 'email': '#26de81'}
            for node_idx in self._graph.node_indices():
                node_id = node_ids_by_idx.get(node_idx, '')
                attrs = self._node_attrs.get(node_idx, {})
                entity_type = attrs.get('entity_type', 'unknown')
                color = color_map.get(entity_type.lower(), '#70a1ff')
                label = attrs.get('label', node_id)
                value = attrs.get('value', '')
                net.add_node(node_id, label=label, title=f"{entity_type}\n{value}", color=color, size=20)
            for edge in self._graph.edge_indices():
                src_idx, dst_idx = self._graph.endpoints(edge)
                src = node_ids_by_idx.get(src_idx, '')
                dst = node_ids_by_idx.get(dst_idx, '')
                edge_data = self._graph.get_edge_data(src_idx, dst_idx) or 'related'
                rel = edge_data if isinstance(edge_data, str) else 'related'
                net.add_edge(src, dst, title=rel, label=rel[:20])
            net.save_graph(path)
            logger.info(f'[GraphManager] Exported HTML graph to {path}')
        except Exception as e:
            logger.warning(f'[GraphManager] HTML export failed: {e}, falling back to text')
            self._export_text(path)

    def _export_text(self, path: str) -> None:
        """Fallback: plain text edge-list export."""
        # Build reverse index
        node_ids_by_idx: dict[int, str] = {}
        for nid, nidx in self._node_index.items():
            node_ids_by_idx[nidx] = nid
        edge_cnt = 0
        with open(path, 'w') as f:
            f.write('# Hledac Entity Graph\n\n')
            f.write(f'# Nodes: {len(self._node_index)}, Edges: (see below)\n\n')
            f.write('## Nodes\n')
            for node_idx in self._graph.node_indices():
                node_id = node_ids_by_idx.get(node_idx, '')
                attrs = self._node_attrs.get(node_idx, {})
                f.write(f'  {attrs.get("name", node_id)}\n')
            f.write('\n## Edges\n')
            for edge in self._graph.edge_indices():
                src_idx, dst_idx = self._graph.endpoints(edge)
                src = node_ids_by_idx.get(src_idx, '')
                dst = node_ids_by_idx.get(dst_idx, '')
                edge_data = self._graph.get_edge_data(src_idx, dst_idx) or 'related'
                rel = edge_data if isinstance(edge_data, str) else 'related'
                f.write(f'  {src} --[{rel}]--> {dst}\n')
                edge_cnt += 1
        logger.info(f'[GraphManager] Exported text graph to {path}')

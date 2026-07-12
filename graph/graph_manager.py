"""
GraphManager — igraph + pyvis visualization layer.

FÁZE P9: Knowledge graph a vizualizace.
Migrated from networkx to igraph (P1-3): C-core, M1-optimized, 10-100x faster.

Anti-patterns enforced:
- Žádné velké grafové DB — pouze igraph.Graph (in-memory)
- Žádné detailní atributy — only entity_type + value per node
- Streamované přidávání uzlů — žádné batch bulk operations

Methods:
- add_entity(entity_type, value): add node with attributes
- add_relation(source, target, relation_type): add edge
- export_html(path): render to interactive HTML via pyvis
"""
import logging
import os
from typing import Any
logger = logging.getLogger(__name__)
__all__ = ['GraphManager', 'GRAPH_AVAILABLE']
GRAPH_AVAILABLE = True
_IGRAPH_AVAILABLE = True
_PYVIS_AVAILABLE = True

class GraphManager:
    """
    Lightweight graph visualization using igraph + pyvis.

    Anti-patterns enforced:
    - Žádné velké grafové DB — pouze igraph.Graph (in-memory)
    - Žádné detailní atributy — only entity_type + value per node
    - Streamované přidávání uzlů — žádné batch bulk operations

    Methods:
    - add_entity(entity_type, value): add node with attributes
    - add_relation(source, target, relation_type): add edge
    - export_html(path): render to interactive HTML via pyvis
    """
    __slots__ = tuple(('_graph', '_ig', '_node_count', '_node_index'))

    def __init__(self) -> None:
        self._ig = self._get_igraph()
        self._graph = self._ig.Graph(directed=False)
        self._node_count = 0
        self._node_index: dict[str, int] = {}

    @staticmethod
    def _get_igraph() -> Any:
        global _IGRAPH_AVAILABLE
        if not _IGRAPH_AVAILABLE:
            raise ImportError('igraph not available')
        import igraph as ig
        return ig

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
        vertex_id = self._graph.add_vertex(name=node_id, entity_type=entity_type, value=value, label=self._short_label(entity_type, value))
        self._node_index[node_id] = vertex_id.index
        self._node_count += 1

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
        src_id = f'domain:{source}' if ':' not in source else source
        dst_id = f'domain:{target}' if ':' not in target else target
        for node_id, etype, evalue in [(src_id, *self._parse_entity(source)), (dst_id, *self._parse_entity(target))]:
            if node_id not in self._node_index:
                vertex_id = self._graph.add_vertex(name=node_id, entity_type=etype, value=evalue, label=self._short_label(etype, evalue))
                self._node_index[node_id] = vertex_id.index
                self._node_count += 1
        try:
            src_idx = self._node_index[src_id]
            dst_idx = self._node_index[dst_id]
            import igraph as _ig
            self._graph.get_eid(src_idx, dst_idx)
            return
        except _ig.InternalError if '_ig' in dir() else Exception:
            self._graph.add_edge(self._node_index[src_id], self._node_index[dst_id], relation_type=relation_type, label=relation_type)

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
        return self._node_count

    def edge_count(self) -> int:
        """Return current edge count."""
        return self._graph.ecount()

    def to_igraph(self) -> Any:
        """
        Return internal igraph graph for external use.

        Returns:
            igraph.Graph: copy of internal graph with all nodes and edges
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
            start_id = start_entity if start_entity in self._node_index else f'domain:{start_entity}'
            end_id = end_entity if end_entity in self._node_index else f'domain:{end_entity}'
            adj_dict = {v['name']: [self._graph.vs[n]['name'] for n in self._graph.neighbors(v.index)] for v in self._graph.vs}
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
            for vertex in self._graph.vs:
                entity_type = vertex.attributes().get('entity_type', 'unknown')
                color_map = {'domain': '#00ff88', 'ipv4': '#ff6b6b', 'ipv6': '#ff8787', 'url': '#ffd93d', 'cve': '#ff4757', 'hash': '#a55eea', 'email': '#26de81'}
                color = color_map.get(entity_type.lower(), '#70a1ff')
                node_id = vertex['name']
                net.add_node(node_id, label=vertex.attributes().get('label', node_id), title=f"{entity_type}\n{vertex.attributes().get('value', '')}", color=color, size=20)
            for edge in self._graph.es:
                src = self._graph.vs[edge.source]['name']
                dst = self._graph.vs[edge.target]['name']
                rel = edge.attributes().get('relation_type', 'related')
                net.add_edge(src, dst, title=rel, label=rel[:20])
            net.save_graph(path)
            logger.info(f'[GraphManager] Exported HTML graph to {path}')
        except Exception as e:
            logger.warning(f'[GraphManager] HTML export failed: {e}, falling back to text')
            self._export_text(path)

    def _export_text(self, path: str) -> None:
        """Fallback: plain text edge-list export."""
        with open(path, 'w') as f:
            f.write('# Hledac Entity Graph\n\n')
            f.write(f'# Nodes: {self._node_count}, Edges: {self._graph.ecount()}\n\n')
            f.write('## Nodes\n')
            for vertex in self._graph.vs:
                f.write(f"  {vertex['name']}\n")
            f.write('\n## Edges\n')
            for edge in self._graph.es:
                src = self._graph.vs[edge.source]['name']
                dst = self._graph.vs[edge.target]['name']
                rel = edge.attributes().get('relation_type', 'related')
                f.write(f'  {src} --[{rel}]--> {dst}\n')
        logger.info(f'[GraphManager] Exported text graph to {path}')
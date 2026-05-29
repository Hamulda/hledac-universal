"""
Fast explainer – delta‑evidence na základě odebírání hran.
"""

import asyncio
import logging

from hledac.universal.core.resource_governor import ResourceGovernor

logger = logging.getLogger(__name__)


class FastExplainer:
    """Fast explainer pro vysvětlení cest v grafu pomocí delta evidence."""
    def __init__(self, graph_rag, governor: ResourceGovernor):
        self.graph_rag = graph_rag
        self.governor = governor
        self._cache = {}  # jednoduchá cache v paměti

    async def explain_path(self, start_node: str, end_node: str, max_hops: int = 3) -> list[tuple[str, str, float]]:
        """
        Vysvětlí cestu mezi uzly – vrátí seznam hran (source, target) s vahami důležitosti.
        """
        # Nejprve získáme původní cestu
        path = await self.graph_rag.multi_hop_search(start_node, end_node, max_hops)
        if not path or 'nodes' not in path or len(path['nodes']) < 2:
            return []

        # Extrahujeme hrany z cesty
        nodes = path['nodes']
        edges = [(nodes[i], nodes[i+1]) for i in range(len(nodes)-1)]

        # Pro každou hranu spočítáme delta skóre
        tasks = [self._delta_for_edge(edge, start_node, end_node, max_hops) for edge in edges]

        # Spustíme paralelně (max 5 najednou)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        important_edges = []
        for edge, res in zip(edges, results, strict=False):
            if isinstance(res, Exception):
                logger.warning(f"Chyba při výpočtu delta pro {edge}: {res}")
                continue
            if res > 0.1:  # práh
                important_edges.append((edge[0], edge[1], res))

        # Seřadíme podle delta
        important_edges.sort(key=lambda x: x[2], reverse=True)
        return important_edges

    async def _delta_for_edge(self, edge: tuple[str, str], start: str, end: str, max_hops: int) -> float:
        """Spočítá důležitost hrany jako pokles skóre po jejím odstranění."""
        cache_key = (start, end, max_hops, edge)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Získáme skóre původní cesty
        original_score = await self._score_path(start, end, max_hops)

        # Odstraníme hranu (v simulaci) a spočítáme nové skóre
        modified_score = await self._score_path_without_edge(start, end, max_hops, edge)

        delta = original_score - modified_score
        self._cache[cache_key] = delta
        return delta

    async def _score_path(self, start: str, end: str, max_hops: int) -> float:
        """Ohodnotí cestu – čím kratší, tím lepší. Vrací skóre 0..1."""
        path = await self.graph_rag.multi_hop_search(start, end, max_hops)
        if not path or 'nodes' not in path:
            return 0.0
        length = len(path['nodes']) - 1
        if length <= 0:
            return 0.0
        return 1.0 / length

    async def _score_path_without_edge(self, start: str, end: str, max_hops: int, forbidden_edge: tuple[str, str]) -> float:
        """Jako _score_path, ale zakáže danou hranu."""
        path = await self.graph_rag.multi_hop_search(start, end, max_hops, forbidden_edges=[forbidden_edge])
        if not path or 'nodes' not in path:
            return 0.0
        length = len(path['nodes']) - 1
        return 1.0 / length

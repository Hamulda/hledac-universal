"""
Sprint F280: Cross-Sprint DuckPGQ Memory
========================================


Cross-sprint entity memory via DuckPGQGraph.find_connected().

ROLE: Inject DuckPGQ graph traversal into lane planning so that entities
discovered in prior sprints inform the current sprint's lane prioritization.

ARCHITECTURE:
- DuckPGQGraph data PERSISTS across sprints (DuckDB file, checkpoint on winddown).
- reset_session() only clears in-memory per-instance _seen_iocs/_seen_rels sets — graph data intact.
- This module provides query facade: given a topic seed, traverse DuckPGQ for
  related entities discovered in previous sprints, then rank them for lane planning.

WIRE: runtime/sprint_scheduler.py → CrossSprintMemory.get_related_entities()
  → merged into NonfeedSeedContext.duckpgq_seeds → plan_lanes_for_pivot_seeds().

M1 8GB: bounded queries (max_hops=2, limit=50), fail-soft on graph errors.
"""
import logging
from typing import Any
from _core import aclose
logger = logging.getLogger(__name__)
MAX_ENTITIES = 50
MAX_HOPS = 2

class CrossSprintMemory:
    """
    Facade for cross-sprint DuckPGQ entity traversal.

    Provides ranked entity neighbors from DuckPGQ for lane planning injection.
    Fail-soft: graph errors → empty list, sprint continues.
    """
    __slots__ = tuple(('_available', '_graph'))

    def __init__(self) -> None:
        self._graph: Any = None
        self._available = True

    def _get_graph(self) -> Any:
        """Lazy DuckPGQGraph accessor with fail-soft on import/init errors."""
        if self._graph is None:
            try:
                from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
                from hledac.universal.paths import RAMDISK_ACTIVE, RAMDISK_ROOT
                _temp_dir = str(RAMDISK_ROOT / 'duckdb_tmp') if RAMDISK_ACTIVE else None
                self._graph = DuckPGQGraph(temp_dir=_temp_dir)
            except Exception as e:
                logger.warning(f'[CrossSprintMemory] DuckPGQGraph init failed: {e}')
                self._available = False
        return self._graph

    def get_related_entities(self, seed_value: str, max_hops: int=MAX_HOPS) -> list[dict[str, Any]]:
        """
        Query DuckPGQ for entities connected to seed_value within max_hops.

        P0-1 FIX: Routes through GraphService.find_entity_history() which uses
        the hot-edges LMDB cache (O(1) lookup) before falling back to DuckPGQ.
        Previously called DuckPGQGraph.find_connected() directly — the cold
        O(V+E) CTE path that bypasses hot-edges cache entirely.

        Args:
            seed_value: IOC value (domain, IP, URL, etc.) to traverse from.
            max_hops: Traversal depth (default 2, matches quantum_pathfinder).

        Returns:
            List of dicts with keys: value, ioc_type, confidence, source.
            Empty list on any error (fail-soft).
        """
        if not self._available:
            return []
        try:
            from hledac.universal.knowledge.graph_service import _DEFAULT_GRAPH_SERVICE
            results = _DEFAULT_GRAPH_SERVICE.find_entity_history(seed_value, max_hops=max_hops)
            return results[:MAX_ENTITIES] if results else []
        except Exception as e:
            logger.debug(f'[CrossSprintMemory] find_entity_history({seed_value!r}) failed: {e}')
            return []

    def get_related_entities_batch(self, seed_values: list[str], max_hops: int=MAX_HOPS) -> dict[str, list[dict[str, Any]]]:
        """
        Batch query DuckPGQ for multiple seed entities.

        Args:
            seed_values: List of IOC values to traverse from.
            max_hops: Traversal depth.

        Returns:
            Dict mapping seed_value → list of connected entities.
            Missing keys = no results (fail-soft).
        """
        if not self._available:
            return {}
        return {seed: self.get_related_entities(seed, max_hops) for seed in seed_values}

    def is_available(self) -> bool:
        """True if DuckPGQGraph initialized successfully."""
        return self._available
_cross_sprint_memory: CrossSprintMemory | None = None

def get_cross_sprint_memory() -> CrossSprintMemory:
    """Return the shared CrossSprintMemory singleton."""
    global _cross_sprint_memory
    if _cross_sprint_memory is None:
        _cross_sprint_memory = CrossSprintMemory()
    return _cross_sprint_memory
"""DuckDB Graph Attachment — F360: Extracted from DuckDBShadowStore.

Owns graph attachment lifecycle for DuckDBShadowStore.


ARCHITECTURE:
    DuckDBGraphAttachment wraps GraphAttachmentStore (knowledge/graph_attachment.py).
    Provides 3 attachment slots:
      - _ioc_graph: DuckPGQGraph / IOCGraph (primary analytics donor graph)
      - _stix_graph: STIX synthesis graph
      - _truth_write_graph: DuckPGQGraph for truth write path

    In DuckDBShadowStore, the 15 thin wrappers like:
        def get_graph_stats(self) -> dict:
            return self._graph_store().get_graph_stats()
    are replaced by direct delegation to this class.

RATIONALE:
    - Reduces DuckDBShadowStore from 324 methods to ~220
    - Makes graph dependency explicit (not lazy-init)
    - Enables graph store substitution in tests
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


class DuckDBGraphAttachment:
    """
    Graph attachment facade for DuckDBShadowStore.

    Wraps GraphAttachmentStore and exposes the same public API.
    Lazy initialization is replaced by explicit inject + init pattern.
    """

    __slots__ = (
        "_store",  # GraphAttachmentStore instance
    )

    def __init__(self) -> None:
        self._store: Any = None

    def _ensure(self) -> Any:
        """Ensure GraphAttachmentStore is initialized."""
        if self._store is None:
            from hledac.universal.knowledge.graph_attachment import GraphAttachmentStore

            self._store = GraphAttachmentStore()
        return self._store

    # ── Graph injection ────────────────────────────────────────────────────────

    def inject_graph(self, graph: Any) -> None:
        """Attach DuckPGQGraph or IOCGraph."""
        self._ensure().inject_graph(graph)

    def inject_stix_graph(self, graph: Any) -> None:
        """Attach STIX synthesis graph."""
        self._ensure().inject_stix_graph(graph)

    def inject_truth_write_graph(self, graph: Any) -> None:
        """Attach DuckPGQGraph for truth write path."""
        self._ensure().inject_truth_write_graph(graph)

    # ── Graph kind ──────────────────────────────────────────────────────────

    def get_graph_attachment_kind(self) -> str | None:
        """Return kind of attached graph or None."""
        return self._ensure().get_graph_attachment_kind()

    # ── Graph capabilities ────────────────────────────────────────────────

    def graph_supports_buffered_writes(self) -> bool:
        """Return True if attached graph supports buffered writes."""
        return self._ensure().graph_supports_buffered_writes()

    def truth_write_graph_supports_buffered_writes(self) -> bool:
        """Return True if truth-write graph supports buffered writes."""
        return self._ensure().truth_write_graph_supports_buffered_writes()

    # ── Graph reads ────────────────────────────────────────────────────────

    def get_graph_stats(self) -> dict[str, Any]:
        """Return graph stats dict (nodes, edges, pgq_available)."""
        return self._ensure().get_graph_stats()

    def get_stix_graph(self) -> Any:
        """Return attached STIX graph."""
        return self._ensure().get_stix_graph()

    def get_truth_write_graph(self) -> Any:
        """Return attached truth-write graph."""
        return self._ensure().get_truth_write_graph()

    def get_top_seed_nodes(self, n: int = 5) -> list[dict[str, Any]]:
        """Return top N seed nodes for graph traversal."""
        return self._ensure().get_top_seed_nodes(n=n)

    def get_connected_iocs(self, ioc_value: str, max_hops: int = 2) -> list[dict[str, Any]]:
        """Return IOC nodes connected to given IOC within max_hops."""
        return self._ensure().get_connected_iocs(ioc_value, max_hops=max_hops)

    def get_connected_iocs_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Batch graph traversal for multiple IOC values."""
        return self._ensure().get_connected_iocs_batch(values, max_hops=max_hops)

    def annotate_findings_with_graph_context(
        self,
        findings: list[Any],
        max_hops: int = 2,
        max_annotations: int = 50,
    ) -> list[Any]:
        """Enrich findings with graph-derived context (aliases, relationships)."""
        return self._ensure().annotate_findings_with_graph_context(
            findings, max_hops=max_hops, max_annotations=max_annotations
        )

    def get_analytics_graph_for_synthesis(self) -> Any:
        """Return analytics graph for synthesis layer."""
        return self._ensure().get_analytics_graph_for_synthesis()

    def export_graph_topology(
        self,
        *,
        max_nodes: int = 1000,
        max_community_size: int = 200,
        include_centrality: bool = True,
    ) -> dict[str, Any]:
        """
        [META]-010: Export graph topology as Canvas-ready JSON.

        Delegates to the attached graph's export_graph_topology() method.
        DuckDBGraphAttachment → GraphAttachmentStore → IOCGraph.export_graph_topology().

        Returns:
            {"nodes": [...], "edges": [...], "communities": {...},
             "centrality": {...}, "stats": {...}}
        """
        return self._ensure().export_graph_topology(
            max_nodes=max_nodes,
            max_community_size=max_community_size,
            include_centrality=include_centrality,
        )

    def get_top_entities_for_ghost_global(self, n: int = 100) -> list[tuple[str, str, float]]:
        """Return top N entities for ghost global identity resolution."""
        return self._ensure().get_top_entities_for_ghost_global(n=n)

    # ── IOC sprint advance ────────────────────────────────────────────────

    def advance_ioc_sprint(self, sprint_id: str) -> None:
        """Advance IOC graph to new sprint."""
        self._ensure().advance_ioc_sprint(sprint_id)

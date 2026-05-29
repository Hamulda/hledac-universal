"""
KnowledgeGraphLayer - COMPOSER/ORCHESTRATOR role
================================================

DEPRECATED MODULE: This module orchestrates graph components but is NOT a truth store.

For authoritative storage use:
- IOCGraph (KuzuDB) for IOC entity truth store
- DuckPGQGraph (DuckDB) for analytics donor backend

This module may be removed in a future sprint.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeGraphLayer:
    """
    Knowledge graph vrstva — COMPOSER/ORCHESTRATOR role.

    NENÍ truth store — pouze orchestruje komponenty:
    - PersistentKnowledgeLayer (deprecated, use IOCGraph for truth)
    - GraphRAGOrchestrator (consumer, not owner)
    - KnowledgeGraphBuilder (helper/extractor)

    Pro truth storage použij: IOCGraph (KuzuDB)
    Pro analytics použij: DuckPGQGraph (DuckDB)
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else Path("storage/knowledge_graph")
        self._kg = None
        self._graph_rag = None
        self._builder = None

    async def initialize(self) -> None:
        """Inicializovat knowledge graph"""
        logger.info("Initializing KnowledgeGraphLayer...")

        try:
            from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer
            self._kg = PersistentKnowledgeLayer(db_path=self.db_path)
            self._kg.initialize()
            logger.info("✓ Knowledge Graph initialized")
        except Exception as e:
            logger.warning(f"Knowledge Graph initialization failed: {e}")

        try:
            from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
            if self._kg:
                self._graph_rag = GraphRAGOrchestrator(self._kg)
                logger.info("✓ GraphRAG initialized")
        except Exception as e:
            logger.warning(f"GraphRAG initialization failed: {e}")

    async def add_entry(
        self,
        url: str,
        content: str,
        title: str = "",
        keywords: list[str] = None,
        metadata: dict[str, Any] = None
    ) -> bool:
        """
        Přidat záznam do knowledge graph.

        Args:
            url: URL zdroje
            content: Obsah
            title: Titulek
            keywords: Klíčová slova
            metadata: Metadata

        Returns:
            True pokud úspěch
        """
        if not self._kg:
            return False

        try:
            # Map add_entry() to add_knowledge() with proper parameter mapping
            node_id = self._kg.add_knowledge(
                content=content,
                node_type=None,  # Will use default FACT type
                metadata={
                    'url': url,
                    'title': title,
                    'keywords': keywords or [],
                    **(metadata or {})
                }
            )
            return True if node_id else False
        except Exception as e:
            logger.error(f"Failed to add entry: {e}")
            return False

    async def query(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """
        Query knowledge graph.

        Args:
            query: Dotaz
            max_results: Maximální počet výsledků

        Returns:
            Seznam výsledků
        """
        if not self._graph_rag:
            return []

        try:
            # GraphRAG multi-hop reasoning
            results = await self._graph_rag.multi_hop_search(query, max_nodes=max_results)
            return results
        except Exception as e:
            logger.error(f"Graph query failed: {e}")
            return []

    async def close(self) -> None:
        """Zavřít knowledge graph"""
        logger.info("Closing KnowledgeGraphLayer...")
        self._kg = None
        self._graph_rag = None
        logger.info("✓ KnowledgeGraphLayer closed")

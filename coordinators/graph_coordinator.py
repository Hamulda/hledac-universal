"""
GraphCoordinator - Delegates graph reasoning to coordinator
======================================================

Implements the stable coordinator interface (start/step/shutdown) for:
- GraphRAG multi-hop reasoning
- Quantum pathfinder execution
- Knowledge graph traversal
- Fingerprint metadata consumption (Sprint 50)

This enables the orchestrator to become a thin "spine" that delegates
graph reasoning to this coordinator.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import asyncio

from .base import UniversalCoordinator

logger = logging.getLogger(__name__)


# Maximum paths to return per step (bounded output)
MAX_RETURNED_PATHS = 20

# Sprint F206X: Bounded pending queries to prevent memory exhaustion
MAX_PENDING_QUERIES = 1000

# Sprint 50: Fingerprint edge types (string constants, not enum)
FINGERPRINT_EDGE_TYPES = {
    'ct_subdomain_of',
    'same_infra_as',
    'source_map_of',
    'open_storage_bucket',
    'onion_mirror_of',
}


@dataclass
class GraphCoordinatorConfig:
    """Configuration for GraphCoordinator."""
    max_walks_per_step: int = 2
    max_steps_per_walk: int = 128
    max_paths_per_step: int = 20
    enable_quantum_pathfinder: bool = True
    enable_graph_rag: bool = True


class GraphCoordinator(UniversalCoordinator):
    """
    Coordinator for graph reasoning delegation.

    Responsibilities:
    - Execute GraphRAG multi-hop queries
    - Run quantum pathfinder walks
    - Return bounded outputs (paths, metrics)
    """

    def __init__(
        self,
        config: Optional[GraphCoordinatorConfig] = None,
        max_concurrent: int = 2,
    ):
        super().__init__(name="GraphCoordinator", max_concurrent=max_concurrent)
        self._config = config or GraphCoordinatorConfig()

        # State
        # Sprint F206X: deque with maxlen prevents unbounded growth
        self._pending_queries: deque = deque(maxlen=MAX_PENDING_QUERIES)
        self._seen_queries: Set[str] = set()  # O(1) membership test
        self._walks_executed: int = 0
        self._paths_returned: int = 0
        self._stop_reason: Optional[str] = None

        # Orchestrator reference (set via start)
        self._orchestrator: Optional[Any] = None
        self._ctx: Dict[str, Any] = {}

        # Sprint 50: Fingerprint edge storage (source, edge_type, target) -> bool (exists)
        self._fingerprint_edges: Set[Tuple[str, str, str]] = set()
        # Sprint 50: favicon hash -> domain index for same_infra_as edges
        self._favicon_index: Dict[str, List[str]] = {}

    def get_supported_operations(self) -> List[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.SYNTHESIS, OperationType.RESEARCH]

    async def handle_request(
        self,
        operation_ref: str,
        decision: Any
    ) -> Any:
        """
        Handle a decision request (required by UniversalCoordinator base).

        For spine pattern, we use start/step/shutdown instead.
        """
        result = await self.step({'decision': decision})
        return result

    async def _do_initialize(self) -> bool:
        """Initialize coordinator."""
        logger.info("GraphCoordinator initialized")
        return True

    async def _do_start(self, ctx: Dict[str, Any]) -> None:
        """
        Start coordinator with context from orchestrator.

        Expected ctx keys:
        - pending_queries: List[str] - queries to process
        - orchestrator: reference to orchestrator instance
        """
        self._ctx = ctx
        self._orchestrator = ctx.get('orchestrator')

        # Load pending queries if provided
        if 'pending_queries' in ctx:
            # Sprint F206X: Convert to bounded deque, populate seen set for O(1) dedup
            incoming = ctx['pending_queries']
            self._pending_queries = deque(incoming, maxlen=MAX_PENDING_QUERIES)
            self._seen_queries = set(incoming)

        logger.info(f"GraphCoordinator started with {len(self._pending_queries)} pending queries")

    async def _do_step(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute one graph reasoning step.

        Process up to max_walks_per_step from pending queries.
        Returns bounded output with paths.
        """
        # Update context
        self._ctx.update(ctx)

        # Add new queries from ctx
        # Sprint F206X: Use set for O(1) membership, bounded deque auto-evicts
        new_queries = ctx.get('new_queries', [])
        for query in new_queries:
            if query not in self._seen_queries:
                self._seen_queries.add(query)
                self._pending_queries.append(query)

        if not self._pending_queries:
            self._stop_reason = "no_pending_queries"
            return self._get_step_result()

        # Process queries
        query = self._pending_queries.popleft()
        self._seen_queries.discard(query)  # Sprint F206X: remove from seen when processed

        # Execute graph reasoning
        result = await self._execute_graph_reasoning(query)

        return self._get_step_result(result)

    def _get_step_result(self, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get bounded step result."""
        paths = result.get('paths', []) if result else []
        paths = paths[:self._config.max_paths_per_step]

        return {
            'walks_executed': self._walks_executed,
            'paths_returned': len(paths),
            'total_paths': self._paths_returned,
            'paths': paths,
            'stop_reason': self._stop_reason,
            'pending_queries': len(self._pending_queries),
        }

    async def _execute_graph_reasoning(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Execute graph reasoning for a query.

        Delegates to orchestrator's GraphRAG or quantum pathfinder.
        """
        if not self._orchestrator:
            logger.warning(f"GraphCoordinator: no orchestrator reference for query")
            return None

        try:
            paths = []

            # Execute GraphRAG if enabled
            if self._config.enable_graph_rag:
                graph_rag = None
                if hasattr(self._orchestrator, '_graph_rag'):
                    graph_rag = self._orchestrator._graph_rag

                if graph_rag and hasattr(graph_rag, 'multi_hop_search'):
                    result = await graph_rag.multi_hop_search(query)
                    if result:
                        paths.extend(result.get('paths', []))

            # Execute quantum pathfinder if enabled
            if self._config.enable_quantum_pathfinder:
                qpf = None
                if hasattr(self._orchestrator, 'quantum_pathfinder'):
                    qpf = self._orchestrator.quantum_pathfinder

                if qpf and hasattr(qpf, 'find_paths'):
                    # Bounded quantum walk
                    walk_result = await qpf.find_paths(
                        query,
                        max_walks=self._config.max_walks_per_step,
                        max_steps=self._config.max_steps_per_walk
                    )
                    if walk_result:
                        self._walks_executed += 1
                        paths.extend(walk_result.get('paths', []))

            # Bound output
            paths = paths[:self._config.max_paths_per_step]
            self._paths_returned += len(paths)

            return {
                'query': query,
                'paths': paths,
                'path_count': len(paths),
            }

        except Exception as e:
            logger.warning(f"GraphCoordinator: failed to execute graph reasoning: {e}")
            return None

    # Sprint 33: JSON-LD entity extraction
    async def add_entities_from_jsonld(self, jsonld_data: List[Dict]) -> None:
        """Extract entities/relations from JSON-LD and add to graph."""
        if not jsonld_data:
            return
        logger.info(f"GraphCoordinator received {len(jsonld_data)} JSON-LD objects for graph ingestion")
        # Placeholder: In future, this will call self._knowledge_layer.add_knowledge(...)
        await asyncio.sleep(0)  # yield to event loop

    async def _do_shutdown(self, ctx: Dict[str, Any]) -> None:
        """Cleanup on shutdown."""
        logger.info(f"GraphCoordinator shutting down: {self._walks_executed} walks, {self._paths_returned} paths")
        self._pending_queries.clear()
        self._seen_queries.clear()  # Sprint F206X: clear seen set too

    # Sprint 50: Fingerprint metadata consumption
    async def consume_fingerprint_metadata(self, url: str, metadata: dict) -> None:
        """Consume fingerprint data from Sprint 46/49 into graph. Idempotent, bounded."""
        if not metadata:
            return
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            edge_count = 0
            MAX_EDGES = 20

            # ct_subdomains -> ct_subdomain_of edges
            for subdomain in metadata.get('ct_subdomains', []):
                if edge_count >= MAX_EDGES:
                    break
                if isinstance(subdomain, str) and subdomain != domain:
                    self._add_edge_if_new(subdomain, 'ct_subdomain_of', domain)
                    edge_count += 1

            # open_storage -> open_storage_bucket edges
            for bucket in metadata.get('open_storage', []):
                if edge_count >= MAX_EDGES:
                    break
                bucket_url = bucket.get('url') if isinstance(bucket, dict) else str(bucket)
                if bucket_url:
                    self._add_edge_if_new(bucket_url, 'open_storage_bucket', domain)
                    edge_count += 1

            # source_map_paths -> source_map_of edges
            for path in metadata.get('source_map_paths', []):
                if edge_count >= MAX_EDGES:
                    break
                if isinstance(path, str):
                    self._add_edge_if_new(path, 'source_map_of', url)
                    edge_count += 1

            # onion_links -> onion_mirror_of edges
            for onion in metadata.get('onion_links', []):
                if edge_count >= MAX_EDGES:
                    break
                if isinstance(onion, str):
                    self._add_edge_if_new(onion, 'onion_mirror_of', domain)
                    edge_count += 1

            # favicon_hash -> same_infra_as edges (same hash = same infrastructure)
            favicon_hash = metadata.get('favicon_hash')
            if favicon_hash and hasattr(self, '_favicon_index'):
                existing = self._favicon_index.get(favicon_hash, [])
                for existing_domain in existing:
                    if edge_count >= MAX_EDGES:
                        break
                    self._add_edge_if_new(domain, 'same_infra_as', existing_domain)
                existing.append(domain)
            elif favicon_hash:
                if not hasattr(self, '_favicon_index'):
                    self._favicon_index: Dict[str, List[str]] = {}
                self._favicon_index.setdefault(favicon_hash, []).append(domain)

            # jarm_hash -> same_infra_as edges (Sprint 51)
            jarm_hash = metadata.get('jarm_hash')
            if jarm_hash:
                # Reuse _favicon_index for JARM (same_infra_as logic)
                if not hasattr(self, '_favicon_index'):
                    self._favicon_index: Dict[str, List[str]] = {}

                existing = self._favicon_index.get(jarm_hash, [])
                for existing_domain in existing:
                    if edge_count >= MAX_EDGES:
                        break
                    self._add_edge_if_new(domain, 'same_infra_as', existing_domain)

                if domain not in existing:
                    existing.append(domain)
                self._favicon_index[jarm_hash] = existing

            logger.debug(f"[GRAPH] consume_fingerprint_metadata: {edge_count} edges added for {url}")

        except Exception as e:
            logger.warning(f"[GRAPH] consume_fingerprint_metadata failed for {url}: {e}")

    def _add_edge_if_new(self, source: str, edge_type: str, target: str) -> None:
        """Add edge only if it doesn't already exist (idempotency)."""
        key = (source, edge_type, target)
        if key not in self._fingerprint_edges:
            self._fingerprint_edges.add(key)
            logger.debug(f"[GRAPH] Added edge: {source} --[{edge_type}]--> {target}")

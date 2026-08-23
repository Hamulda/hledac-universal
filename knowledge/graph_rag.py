"""
GraphRAGOrchestrator - Multi-Hop Reasoning for KuzuDB
=======================================================

ROLE: Consumer/Orchestrator (NOT backend owner)
============================================
Tento modul je consumer/orchestrator pro multi-hop reasoning.
NENÍ owner backend storage → persistent_layer (deprecated!)
NENÍ owner embedding computation → MLXEmbeddingManager singleton
NENÍ owner primary retrieval → rag_engine

Embedding policy: _get_embedder() → MLXEmbeddingManager singleton (shared, ne vlastní)

Graph-based RAG orchestrator optimized for M1 Silicon (8GB RAM).
Enables multi-hop reasoning over disk-based knowledge graph.

Key Features:
    - Multi-hop graph traversal for deep reasoning
    - Disk-based KuzuDB storage (minimal RAM footprint)
    - Semantic search combined with graph traversal
    - Network analysis (centrality, community detection)
    - Evidence relationship analysis
    - Contradiction detection

DEPRECATED - source module deleted:
    - Centrality analysis (degree, betweenness, closeness, eigenvector, PageRank)
    - Community detection
    - Network metrics
    - Key path analysis
"""

import asyncio
import logging
import re
import threading
from collections import deque
from dataclasses import field
from functools import partial
from operator import attrgetter, itemgetter
from typing import Any, Literal

from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.sync_bridge import run_sync_async

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

# [FINAL]-019-07: Capability cost registration for QoS ladder triage.
# GraphRAGOrchestrator: rss_mb=400, peak_mb=600 (embedding + k-hop traversal)
from hledac.universal._core.capability_cost import register_capability_cost

register_capability_cost(
    "graphragorchestrator", rss_mb=400, peak_mb=600, tier="heavy", tags=("graph", "rag", "embedding")
)


def _get_numpy():
    """Lazy getter for numpy with availability check."""
    return np


logger = logging.getLogger(__name__)
from hledac.universal.utils.graph_utils import lazy_ig


def _check_ram_for_igraph() -> bool:
    """M1 8GB: skip igraph if RAM headroom < 500MB."""
    try:
        import psutil

        available_gb = psutil.virtual_memory().available / 1024**3
        if available_gb < 0.5:
            logger.debug(f"GraphRAGOrchestrator: RAM headroom {available_gb:.1f}GB < 0.5GB, skipping igraph")
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def get_degradation_safe_max_hops(requested_hops: int = 2) -> int:
    """
    [FINAL]-019-06: Return the governor-safe maximum hops for GraphRAG.

    Called from multi_hop_search() and find_connections() to cap hops under
    CRITICAL/MINIMAL QoS.  This reduces embedding and traversal cost when the
    governor has flagged memory pressure.

    QoS → max_hops mapping:
        full / thermal / windup:  requested_hops (no cap, default 2)
        battery / emergency:       1  (cap to minimal traversal)

    Thread-safe: delegates to get_current_degradation_level() which reads the
    module-level _last_qos_profile cache updated by apply_decision().
    """
    try:
        from hledac.universal._core.resource_governor import QoSLevel, get_current_degradation_level

        level = get_current_degradation_level()
        if level is QoSLevel.EMERGENCY or level is QoSLevel.BATTERY:
            return 1
        return requested_hops
    except Exception:
        return requested_hops  # fail-open: governor unavailable → use caller's value


class CentralityScores(Struct):
    """Centrality analysis results for a node."""

    node_id: str
    degree: float = 0.0
    betweenness: float = 0.0
    closeness: float = 0.0
    eigenvector: float = 0.0
    pagerank: float = 0.0
    overall_influence: float = 0.0


class Community(Struct):
    """Detected community in the graph."""

    community_id: int
    nodes: list[str] = field(default_factory=list)
    cohesion_score: float = 0.0
    dominant_type: str = "mixed"
    key_characteristics: list[str] = field(default_factory=list)


class GraphContradiction(Struct):
    """Contradiction detected in the graph."""

    node_a_id: str
    node_b_id: str
    node_a_content: str
    node_b_content: str
    contradiction_type: str
    severity: float
    resolution_suggestions: list[str] = field(default_factory=list)


class GraphRAGOrchestrator:
    """
    GraphRAG orchestrator for multi-hop reasoning.

    ROLE: Consumer/Orchestrator (NOT backend owner)
    ================================================
    - multi-hop graph traversal (consumer přes knowledge_layer)
    - NENÍ owner backend storage → persistent_layer (deprecated!)
    - NENÍ owner embedding → MLXEmbeddingManager singleton přes _get_embedder()
    - NENÍ owner primary retrieval → rag_engine

    Performs multi-hop search over knowledge graph to find
    relationships that aren't visible in single documents.
    """

    MAX_QUEUE_LENGTH = 100
    MAX_VISITED_NODES = 500
    MAX_EXPANSION_PER_NODE = 10
    __slots__ = ("_embedder", "_embedder_lock", "_score_semaphore", "_score_semaphore_lock", "knowledge_layer")

    def __init__(self, knowledge_layer) -> None:
        """
        Initialize GraphRAG orchestrator.

        Args:
            knowledge_layer: PersistentKnowledgeLayer instance
        """
        self.knowledge_layer = knowledge_layer
        self._embedder = None
        self._embedder_lock = None
        self._score_semaphore: asyncio.Semaphore | None = None
        self._score_semaphore_lock = None
        logger.info("GraphRAGOrchestrator initialized")

    async def _get_embedder(self):
        """
        Get shared MLXEmbeddingManager singleton (memory-convergent).

        M1 8GB: graph_rag NENÍ embedder owner. Používá sdílený
        MLXEmbeddingManager singleton z core/mlx_embeddings.py.
        Žádné duplikátní RAGEngine() vytváření.
        """
        if self._embedder is None:
            if self._embedder_lock is None:
                self._embedder_lock = asyncio.Lock()
            async with self._embedder_lock:
                if self._embedder is None:
                    try:
                        from hledac.universal._core.mlx_embeddings import get_mlx_embedder

                        self._embedder = get_mlx_embedder()
                        logger.debug("[EMBEDDER] graph_rag using shared MLXEmbeddingManager singleton")
                    except Exception as e:
                        logger.warning(f"Failed to get shared embedder: {e}")
                        return None
        return self._embedder

    async def _get_score_semaphore(self) -> asyncio.Semaphore:
        """Lazy-init semaphore for bounded parallel scoring (M1 8GB safe)."""
        if self._score_semaphore is None:
            if self._score_semaphore_lock is None:
                self._score_semaphore_lock = asyncio.Lock()
            async with self._score_semaphore_lock:
                if self._score_semaphore is None:
                    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore

                    self._score_semaphore = get_semaphore(ConcurrencyCategory.GRAPH_RAG)
        return self._score_semaphore

    async def score_path(
        self, path: list[str], hypothesis: str, hypothesis_emb: list[float] | None = None, max_nodes: int = 10
    ) -> float:
        """
        Score a path in the knowledge graph based on:
        - Path length (shorter is better)
        - Node relevance to hypothesis (via embeddings)
        - Average node credibility

        Args:
            path: List of node IDs forming the path
            hypothesis: The hypothesis to score against
            hypothesis_emb: Pre-computed hypothesis embedding (optional)
            max_nodes: Maximum nodes to score (budget)

        Returns:
            Score between 0 and 1
        """
        import numpy as np

        if len(path) < 2:
            return 0.0
        nodes_to_score = path[:max_nodes]
        length_score = 1.0 / max(1, len(path))
        semaphore = await self._get_score_semaphore()

        async def fetch_node_with_semaphore(node_id: str) -> tuple[str, np.ndarray | None, float | None]:
            """Fetch single node: returns (node_id, embedding, confidence)."""
            async with semaphore:
                try:
                    node = await self.knowledge_layer.get_node(node_id)
                    if node:
                        emb = node.embedding if node.embedding else None
                        conf = None
                        if node.metadata and "confidence" in node.metadata:
                            conf = float(node.metadata["confidence"])
                        return (node_id, emb, conf)
                except Exception:  # noqa: BLE001
                    pass
                return (node_id, None, None)

        fetch_results: list[tuple[str, np.ndarray | None, float | None]] = await parallel_ok(
            *[fetch_node_with_semaphore(n) for n in nodes_to_score], label="graph_rag:score_node_embeddings"
        )
        node_embeddings: list[np.ndarray] = []
        confidences: list[float] = []
        for result in fetch_results:
            if isinstance(result, Exception):
                continue
            _node_id, emb, conf = result
            if emb is not None:
                node_embeddings.append(emb)
            if conf is not None:
                confidences.append(conf)
        relevance_score = 0.5
        try:
            if not node_embeddings:
                relevance_score = 0.5
            else:
                embedder = await self._get_embedder()
                if embedder is None:
                    relevance_score = 0.5
                else:
                    if hypothesis_emb is None:
                        try:
                            emb_result = await asyncio.to_thread(embedder.embed_document, hypothesis)
                            if emb_result is not None and emb_result:
                                hypothesis_emb = (
                                    emb_result.tolist() if hasattr(emb_result, "tolist") else list(emb_result)
                                )
                            else:
                                hypothesis_emb = [0.0] * 384
                        except Exception:
                            hypothesis_emb = [0.0] * 384
                    if hypothesis_emb:
                        hypothesis_arr = np.array(hypothesis_emb)
                        norm_hyp = np.linalg.norm(hypothesis_arr)
                        if norm_hyp > 0:
                            sims = [
                                np.dot(hypothesis_arr, emb) / (norm_hyp * np.linalg.norm(emb) + 1e-08)
                                for emb in node_embeddings
                            ]
                            relevance_score = float(np.mean(sims))
        except Exception as e:
            logger.debug(f"score_path relevance computation failed: {e}")
            relevance_score = 0.5
        credibility = sum(confidences) / len(confidences) if confidences else 0.5
        final_score = 0.4 * length_score + 0.4 * relevance_score + 0.2 * credibility
        return float(max(0.0, min(1.0, final_score)))

    async def score_paths_parallel(self, paths: list[list[str]], hypothesis: str, max_nodes: int = 10) -> list[float]:
        """
        Score multiple paths in parallel with bounded concurrency.

        M1 8GB: Uses Semaphore(4) to limit concurrent scoring operations.
        Each scoring operation fetches embeddings via MLX (I/O bound).

        Args:
            paths: List of paths (each path is a list of node IDs)
            hypothesis: The hypothesis to score against
            max_nodes: Maximum nodes to score per path (budget)

        Returns:
            List of scores (one per path), in same order as input
        """
        if not paths:
            return []
        hypothesis_emb = None
        try:
            embedder = await self._get_embedder()
            if embedder is not None:
                emb_result = await asyncio.to_thread(embedder.embed_document, hypothesis)
                if emb_result is not None and len(emb_result) > 0:
                    hypothesis_emb = emb_result.tolist() if hasattr(emb_result, "tolist") else list(emb_result)
        except Exception as e:
            logger.debug(f"score_paths_parallel: hypothesis embedding failed: {e}")
        semaphore = await self._get_score_semaphore()

        async def score_with_semaphore(path: list[str]) -> float:
            async with semaphore:
                return await self.score_path(path, hypothesis, hypothesis_emb, max_nodes)

        results = await parallel_ok(
            *[score_with_semaphore(path) for path in paths], label="graph_rag:score_paths_parallel"
        )
        return [float(r) if isinstance(r, (int, float)) else 0.0 for r in results]

    async def multi_hop_search(
        self,
        query: str,
        hops: int = 2,
        max_nodes: int = 20,
        timeline: bool = False,
        time_min: str | None = None,
        time_max: str | None = None,
        prefer_recent: bool = True,
        bucket: str = "month",
        max_timeline_points: int = 12,
    ) -> dict[str, Any]:
        """
        Perform multi-hop search over the knowledge graph with path evidence.

        Hop 0: Find starting nodes via semantic search
        Hop 1..N: Traverse graph to find related nodes
        Synthesis: Return paths with novelty filtering

        Args:
            query: Search query
            hops: Number of hops to traverse (default: 2)
            max_nodes: Maximum nodes to return (default: 20)
            timeline: Enable timeline mode (default: False)
            time_min: ISO date/time filter (inclusive)
            time_max: ISO date/time filter (inclusive)
            prefer_recent: Prefer newer evidence in ranking
            bucket: Time bucketing for timeline ("month" or "year")
            max_timeline_points: Max timeline points to return (default: 12, max: 12)

        Returns:
            Dict with:
                - insights: List of relevant facts with path evidence
                - paths: List of graph paths with nodes, relations, evidence
                - summary_text: Human-readable summary
                - novelty_stats: Stats about novelty filtering
                - contested: Whether contradictions were found
                - counter_paths: Alternative paths (if contested)
                - timeline_points: Temporal analysis (if timeline=True)
                - drift_events: Detected drift events (if timeline=True)
                - narratives: Competing narratives (if contested)
        """
        # [FINAL]-019-06: Cap hops under CRITICAL/MINIMAL QoS to reduce traversal cost.
        hops = get_degradation_safe_max_hops(hops)
        logger.info(
            f"🔍 Multi-hop search: query='{query}', hops={hops}, max_nodes={max_nodes}, timeline={timeline}, prefer_recent={prefer_recent}"
        )
        seed_entities: set[str] = set()
        visited: set[str] = set()
        paths: list[dict[str, Any]] = []
        all_facts: list[dict[str, Any]] = []
        initial_results = await self.knowledge_layer.search(query, limit=10)
        logger.info(f"  Hop 0: Found {len(initial_results)} initial nodes")
        seed_doc_entities: set[str] = set()
        if initial_results:
            top_doc = initial_results[0][0]
            seed_doc_entities = self._extract_entities_from_node(top_doc)
            logger.debug(f"  Seed doc entities: {len(seed_doc_entities)}")
        for node, similarity in initial_results:
            node_id = node.id
            if node_id in visited:
                continue
            visited.add(node_id)
            node_entities = self._extract_entities_from_node(node)
            seed_entities.update(node_entities)
            fact = {
                "content": node.content,
                "node_id": node_id,
                "node_type": node.node_type.value,
                "hop": 0,
                "similarity": similarity,
                "path": [node_id],
                "path_content": [node.content],
                "relations": [],
                "metadata": node.metadata,
                "evidence_ids": [node_id],
                "novelty_score": 0.0,
                "novelty_failed": False,
            }
            all_facts.append(fact)
            paths.append(
                {
                    "nodes": [node_id],
                    "node_types": [node.node_type.value],
                    "relations": [],
                    "score": similarity,
                    "evidence_ids": [node_id],
                    "hop": 0,
                }
            )
        # MODERN-36 PERFORMANCE OPTIMIZATION: Parallel hop traversal
        # Run multiple hops concurrently to reduce latency for deep graph queries
        # Limit concurrency to 3 hops at a time to balance parallelism with memory
        _max_concurrent_hops = min(3, hops)
        _hop_semaphore = asyncio.Semaphore(_max_concurrent_hops)
        _visited_lock = asyncio.Lock()

        async def _traverse_hop_async(hop: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            """
            Async wrapper for hop traversal with semaphore-based concurrency control.

            MODERN-36 FIX: Runs _traverse_hop_with_paths in a thread pool executor
            to avoid blocking the event loop during synchronous I/O operations.
            """
            async with _hop_semaphore:
                # Use lock to safely read/write visited set across async boundary
                async with _visited_lock:
                    current_visited = visited.copy()
                    len(visited)

                # MODERN-36 FIX: Run synchronous traversal in thread pool
                # This prevents blocking the event loop during graph traversal
                # which involves I/O-bound get_node and get_related_sync calls
                new_facts, new_paths = await asyncio.to_thread(
                    self._traverse_hop_with_paths, current_visited, hop, max_nodes, seed_entities, seed_doc_entities
                )

                async with _visited_lock:
                    for fact in new_facts:
                        if "node_id" in fact and fact["node_id"] not in visited:
                            visited.add(fact["node_id"])

                return new_facts, new_paths

        if hops > 1:
            import asyncio

            hop_tasks = [_traverse_hop_async(hop) for hop in range(1, hops + 1)]
            # Use gather to run hops in parallel (semaphore limits concurrency)
            hop_results = await asyncio.gather(*hop_tasks, return_exceptions=True)

            for i, result in enumerate(hop_results):
                if isinstance(result, Exception):
                    logger.warning(f"Hop {i + 1} traversal failed: {result}")
                    continue
                new_facts, new_paths = result
                all_facts.extend(new_facts)
                paths.extend(new_paths)
                logger.info(f"  Hop {i + 1}: Found {len(new_facts)} new nodes, {len(new_paths)} new paths")
                if len(visited) >= max_nodes:
                    break
        else:
            # Single hop - use original sequential path
            for hop in range(1, hops + 1):
                new_facts, new_paths = self._traverse_hop_with_paths(
                    visited, hop, max_nodes, seed_entities, seed_doc_entities
                )
                all_facts.extend(new_facts)
                paths.extend(new_paths)
                logger.info(f"  Hop {hop}: Found {len(new_facts)} new nodes, {len(new_paths)} new paths")
                if len(visited) >= max_nodes:
                    break
        all_facts = self._deduplicate_facts(all_facts)
        all_facts = self._rank_facts_with_novelty(all_facts)
        novel_facts = []
        novelty_failed_count = 0
        for fact in all_facts[:max_nodes]:
            if fact.get("novelty_failed", False):
                novelty_failed_count += 1
            novel_facts.append(fact)
        if time_min or time_max:
            novel_facts = self._filter_by_time(novel_facts, time_min, time_max)
        if prefer_recent:
            novel_facts = self._apply_recency_boost(novel_facts)
        contested, primary_paths, counter_paths, narratives = self._detect_contradictions_with_narratives(novel_facts)
        timeline_points = []
        drift_events = []
        if timeline:
            timeline_points = self._generate_timeline(novel_facts, bucket, max_timeline_points)
            drift_events = self._detect_drift(novel_facts, bucket)
        summary_text = self._generate_path_summary(primary_paths, query, contested, counter_paths)
        paths = paths[:max_nodes]
        primary_paths = primary_paths[:10]
        counter_paths = counter_paths[:5]
        logger.info(
            f"[GRAPH MULTIHOP] total_facts={len(all_facts)}, novel_facts={len(novel_facts)}, novelty_failed={novelty_failed_count}, paths={len(paths)}, contested={contested}, counter_paths={len(counter_paths)}, narratives={len(narratives)}, timeline_points={len(timeline_points)}, drift_events={len(drift_events)}"
        )
        result = {
            "insights": primary_paths,
            "paths": paths,
            "summary_text": summary_text,
            "novelty_stats": {
                "total_facts": len(all_facts),
                "novel_facts": len(novel_facts),
                "novelty_failed": novelty_failed_count,
                "seed_entities": len(seed_entities),
            },
            "contested": contested,
            "counter_paths": counter_paths,
            "narratives": narratives,
        }
        if timeline:
            result["timeline_points"] = timeline_points
            result["drift_events"] = drift_events
        return result

    def _run_async_safe(self, coro):
        """
        Safely run an async coroutine synchronously.

        Delegates to run_sync_async() which uses asyncio.Runner (Python 3.11+)
        for the no-loop case. For worker threads with a running loop,
        run_until_complete is safe to use directly.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return run_sync_async(coro)
        if threading.get_ident() != threading.main_thread().ident:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(coro)
        return run_sync_async(coro)

    def multi_hop_search_sync(
        self,
        query: str,
        hops: int = 2,
        max_nodes: int = 20,
        timeline: bool = False,
        time_min: str | None = None,
        time_max: str | None = None,
        prefer_recent: bool = True,
        bucket: str = "month",
        max_timeline_points: int = 12,
    ) -> dict[str, Any]:
        """
        Synchronous version of multi-hop search with path evidence.

        Uses search_sync() for synchronous contexts.

        Args:
            query: Search query
            hops: Number of hops to traverse (default: 2)
            max_nodes: Maximum nodes to return (default: 20)
            timeline: Enable timeline mode (default: False)
            time_min: ISO date/time filter (inclusive)
            time_max: ISO date/time filter (inclusive)
            prefer_recent: Prefer newer evidence in ranking
            bucket: Time bucketing for timeline ("month" or "year")
            max_timeline_points: Max timeline points to return (default: 12)

        Returns:
            Dict with insights, paths, summary_text, novelty_stats, contested, counter_paths,
            timeline_points (if timeline=True), drift_events (if timeline=True), narratives (if contested)
        """
        # [FINAL]-019-06: Cap hops under CRITICAL/MINIMAL QoS.
        hops = get_degradation_safe_max_hops(hops)
        logger.info(
            f"🔍 Multi-hop search (sync): query='{query}', hops={hops}, max_nodes={max_nodes}, timeline={timeline}"
        )
        seed_entities: set[str] = set()
        visited: set[str] = set()
        paths: list[dict[str, Any]] = []
        all_facts: list[dict[str, Any]] = []
        if hasattr(self.knowledge_layer, "search_sync"):
            initial_results = self.knowledge_layer.search_sync(query, limit=10)
        else:
            initial_results = self._run_async_safe(self.knowledge_layer.search(query, limit=10))
        logger.info(f"  Hop 0: Found {len(initial_results)} initial nodes")
        seed_doc_entities: set[str] = set()
        if initial_results:
            top_doc = initial_results[0][0]
            seed_doc_entities = self._extract_entities_from_node(top_doc)
        for node, similarity in initial_results:
            node_id = node.id
            if node_id in visited:
                continue
            visited.add(node_id)
            node_entities = self._extract_entities_from_node(node)
            seed_entities.update(node_entities)
            fact = {
                "content": node.content,
                "node_id": node_id,
                "node_type": node.node_type.value,
                "hop": 0,
                "similarity": similarity,
                "path": [node_id],
                "path_content": [node.content],
                "relations": [],
                "metadata": node.metadata,
                "evidence_ids": [node_id],
                "novelty_score": 0.0,
                "novelty_failed": False,
            }
            all_facts.append(fact)
            paths.append(
                {
                    "nodes": [node_id],
                    "node_types": [node.node_type.value],
                    "relations": [],
                    "score": similarity,
                    "evidence_ids": [node_id],
                    "hop": 0,
                }
            )
        for hop in range(1, hops + 1):
            new_facts, new_paths = self._traverse_hop_with_paths(
                visited, hop, max_nodes, seed_entities, seed_doc_entities
            )
            all_facts.extend(new_facts)
            paths.extend(new_paths)
            logger.info(f"  Hop {hop}: Found {len(new_facts)} new nodes, {len(new_paths)} new paths")
            if len(visited) >= max_nodes:
                break
        all_facts = self._deduplicate_facts(all_facts)
        all_facts = self._rank_facts_with_novelty(all_facts)
        novel_facts = []
        novelty_failed_count = 0
        for fact in all_facts[:max_nodes]:
            if fact.get("novelty_failed", False):
                novelty_failed_count += 1
            novel_facts.append(fact)
        if time_min or time_max:
            novel_facts = self._filter_by_time(novel_facts, time_min, time_max)
        if prefer_recent:
            novel_facts = self._apply_recency_boost(novel_facts)
        contested, primary_paths, counter_paths, narratives = self._detect_contradictions_with_narratives(novel_facts)
        timeline_points = []
        drift_events = []
        if timeline:
            timeline_points = self._generate_timeline(novel_facts, bucket, max_timeline_points)
            drift_events = self._detect_drift(novel_facts, bucket)
        summary_text = self._generate_path_summary(primary_paths, query, contested, counter_paths)
        paths = paths[:max_nodes]
        primary_paths = primary_paths[:10]
        counter_paths = counter_paths[:5]
        logger.info(
            f"[GRAPH MULTIHOP] total_facts={len(all_facts)}, novel_facts={len(novel_facts)}, novelty_failed={novelty_failed_count}, contested={contested}, counter_paths={len(counter_paths)}, narratives={len(narratives)}, timeline_points={len(timeline_points)}"
        )
        result = {
            "insights": primary_paths,
            "paths": paths,
            "summary_text": summary_text,
            "novelty_stats": {
                "total_facts": len(all_facts),
                "novel_facts": len(novel_facts),
                "novelty_failed": novelty_failed_count,
                "seed_entities": len(seed_entities),
            },
            "contested": contested,
            "counter_paths": counter_paths,
            "narratives": narratives,
        }
        if timeline:
            result["timeline_points"] = timeline_points
            result["drift_events"] = drift_events
        return result

    def _traverse_hop(self, visited: set[str], hop: int, max_nodes: int, max_edges: int = 500) -> list[dict[str, Any]]:
        """
        Traverse one hop in the graph with RAM-efficient frontier management.

        Args:
            visited: Set of already visited node IDs
            hop: Current hop number
            max_nodes: Maximum nodes to collect
            max_edges: Maximum edges to traverse (default: 500)

        Returns:
            List of new facts discovered in this hop
        """
        new_facts = []
        edges_traversed = 0
        frontier = deque(list(visited), maxlen=max_nodes * 2)
        for node_id in frontier:
            if edges_traversed >= max_edges or len(visited) >= max_nodes:
                break
            related = self.knowledge_layer.get_related_sync(node_id, max_depth=1)
            edges = related.get("edges", [])
            for edge in edges:
                if edges_traversed >= max_edges:
                    break
                edges_traversed += 1
                related_id = edge.target_id if edge.source_id == node_id else edge.source_id
                if related_id in visited:
                    continue
                related_node = related.get("nodes", {}).get(related_id)
                if not related_node:
                    continue
                visited.add(related_id)
                if len(visited) > max_nodes:
                    break
                source_node = self.knowledge_layer._backend.get_node(node_id)
                if source_node:
                    path = [source_node.content, related_node.content]
                else:
                    path = [related_node.content]
                fact = {
                    "content": related_node.content,
                    "node_type": related_node.node_type.value,
                    "hop": hop,
                    "similarity": 1.0 - hop * 0.2,
                    "path": path,
                    "metadata": related_node.metadata,
                }
                new_facts.append(fact)
        return new_facts

    def _deduplicate_facts(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Remove duplicate facts based on content.

        Args:
            facts: List of facts to deduplicate

        Returns:
            Deduplicated list of facts
        """
        seen = set()
        unique_facts = []
        for fact in facts:
            content = fact["content"].lower().strip()
            if content not in seen:
                seen.add(content)
                unique_facts.append(fact)
        logger.debug(f"Deduplicated: {len(facts)} -> {len(unique_facts)} facts")
        return unique_facts

    def _rank_facts(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Rank facts by relevance (similarity, hop distance, type).

        Args:
            facts: List of facts to rank

        Returns:
            Ranked list of facts
        """

        def calculate_score(fact: dict[str, Any]) -> float:
            similarity = fact.get("similarity", 0.5)
            hop = fact.get("hop", 0)
            node_type = fact.get("node_type", "fact")
            type_bonus = {"fact": 1.0, "entity": 0.9, "concept": 0.8, "event": 0.7, "url": 0.5, "document": 0.6}.get(
                node_type, 0.5
            )
            hop_penalty = max(0, 1.0 - hop * 0.15)
            score = similarity * type_bonus * hop_penalty
            return score

        ranked_facts = sorted(facts, key=calculate_score, reverse=True)
        return ranked_facts

    def ask_with_reasoning(self, question: str, hops: int = 2, max_nodes: int = 20) -> dict[str, Any]:
        """
        Ask a question with multi-hop reasoning.

        Returns both the facts and the reasoning paths.

        Args:
            question: Question to ask
            hops: Number of hops to traverse
            max_nodes: Maximum nodes to return

        Returns:
            Dictionary with facts and reasoning paths
        """
        result = self.multi_hop_search(question, hops=hops, max_nodes=max_nodes)
        facts = result.get("insights", [])
        paths = result.get("paths", [])
        reasoning_paths = []
        for fact in facts:
            if "path_content" in fact and len(fact["path_content"]) > 1:
                path_str = " -> ".join(fact["path_content"])
                reasoning_paths.append(
                    {
                        "path": path_str,
                        "hop": fact.get("hop", 1),
                        "content": fact.get("content", ""),
                        "novelty_score": fact.get("novelty_score", 0.0),
                        "novelty_failed": fact.get("novelty_failed", False),
                    }
                )
        output = {
            "question": question,
            "facts": facts,
            "reasoning_paths": reasoning_paths,
            "graph_paths": paths,
            "summary": result.get("summary_text", ""),
            "novelty_stats": result.get("novelty_stats", {}),
            "fact_count": len(facts),
            "path_count": len(reasoning_paths),
        }
        logger.info(f"🧠 Reasoning complete: {len(facts)} facts, {len(reasoning_paths)} paths")
        return output

    async def find_connections(self, entity1: str, entity2: str, max_hops: int = 3) -> list[dict[str, Any]]:
        """
        Find connection paths between two entities (async, parallel node fetch).

        M1 8GB: Runs BFS in Rust rayon io_pool (2 threads) to avoid blocking event loop.
        Previously used asyncio.to_thread (default executor) → now uses run_in_io_pool.

        Args:
            entity1: First entity name
            entity2: Second entity name
            max_hops: Maximum hops to search

        Returns:
            List of connection paths
        """
        # [FINAL]-019-06: Cap max_hops under CRITICAL/MINIMAL QoS to reduce BFS cost.
        max_hops = get_degradation_safe_max_hops(max_hops)
        import hashlib

        def get_entity_id(name: str) -> str:
            return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

        entity1_id = get_entity_id(entity1)
        entity2_id = get_entity_id(entity2)
        from hledac.universal.runtime.worker_pool import get_rust_pool

        pool = get_rust_pool("io")
        bfs_fn = partial(self._find_paths_bfs, entity1_id, entity2_id, max_hops, [], set())
        paths = await pool.submit(bfs_fn)
        logger.info(f"Found {len(paths)} paths between '{entity1}' and '{entity2}'")
        return paths

    def _find_paths_bfs(
        self, start_id: str, target_id: str, max_hops: int, current_path: list[str], visited: set[str]
    ) -> list[dict[str, Any]]:
        """
        BFS to find paths between nodes (runs in thread pool).

        Returns:
            List of connection paths
        """
        paths: list[dict[str, Any]] = []
        if len(current_path) > max_hops:
            return paths
        current_path.append(start_id)
        visited.add(start_id)
        if start_id == target_id:
            node_contents = [
                node.content
                for node_id in current_path
                if (node := self.knowledge_layer._backend.get_node(node_id)) and node.content
            ]
            paths.append({"path": " -> ".join(node_contents), "length": len(current_path) - 1})
        else:
            related = self.knowledge_layer.get_related(start_id, max_depth=1)
            for related_id in related.get("nodes", {}):
                if related_id not in visited:
                    sub_paths = self._find_paths_bfs(
                        related_id, target_id, max_hops, current_path.copy(), visited.copy()
                    )
                    paths.extend(sub_paths)
        current_path.pop()
        visited.discard(start_id)
        return paths

    def get_statistics(self) -> dict[str, Any]:
        """
        Get GraphRAG orchestrator statistics.

        Returns:
            Dictionary with statistics
        """
        stats = self.knowledge_layer.get_statistics()
        stats["graph_rag_initialized"] = True
        return stats

    def shutdown(self) -> None:
        """Gracefully shutdown the orchestrator and release resources.

        R4.1: Thread pool no longer owned by this class — Rust rayon pools
        (io_pool, cpu_pool) are process-level singletons managed by Rust.
        No explicit shutdown needed from Python side.
        """

    def calculate_centrality(self, node_ids: list[str] | None = None, top_k: int = 10) -> list[CentralityScores]:
        """
        Calculate centrality measures for nodes in the graph.

        Uses igraph C-core when available (50-100x faster than pure-Python).
        Falls back to simplified pure-Python on igraph unavailable / RAM constraint.

        Args:
            node_ids: Specific nodes to analyze (None = all)
            top_k: Return top K most central nodes

        Returns:
            List of CentralityScores sorted by overall influence
        """
        if node_ids is None:
            node_ids = self._get_all_node_ids()
        if not node_ids:
            return []
        adjacency = self._build_adjacency_list(node_ids)
        ig_centrality = self._calculate_centrality_igraph(adjacency, node_ids)
        centrality_scores = []
        n = len(node_ids)
        for node_id in node_ids:
            scores = CentralityScores(node_id=node_id)
            if ig_centrality and node_id in ig_centrality:
                c = ig_centrality[node_id]
                scores.degree = c.get("degree", 0.0)
                scores.betweenness = c.get("betweenness", 0.0)
                scores.closeness = c.get("closeness", 0.0)
                scores.eigenvector = c.get("eigenvector", 0.0)
                scores.pagerank = c.get("pagerank", 0.0)
            else:
                if node_id in adjacency:
                    scores.degree = len(adjacency[node_id]) / max(n - 1, 1)
                scores.betweenness = self._get_centrality_metric(
                    node_id, "betweenness", adjacency, node_ids, ig_centrality
                )
                scores.closeness = self._get_centrality_metric(node_id, "closeness", adjacency, node_ids, ig_centrality)
                scores.eigenvector = self._get_centrality_metric(
                    node_id, "eigenvector", adjacency, node_ids, ig_centrality
                )
                scores.pagerank = self._get_centrality_metric(node_id, "pagerank", adjacency, node_ids, ig_centrality)
            scores.overall_influence = (
                scores.degree * 0.15
                + scores.betweenness * 0.25
                + scores.closeness * 0.2
                + scores.eigenvector * 0.2
                + scores.pagerank * 0.2
            )
            centrality_scores.append(scores)
        centrality_scores.sort(key=attrgetter("overall_influence"), reverse=True)
        logger.info(f"Calculated centrality for {len(centrality_scores)} nodes (igraph={bool(ig_centrality)})")
        return centrality_scores[:top_k]

    def detect_communities(self, num_communities: int = 3) -> list[Community]:
        """
        Detect communities in the knowledge graph.

        Uses igraph C-core label propagation when available (5-10x faster than pure-Python).
        Falls back to pure-Python label propagation on igraph unavailable / RAM constraint.

        Args:
            num_communities: Target number of communities

        Returns:
            List of detected communities
        """
        node_ids = self._get_all_node_ids()
        if len(node_ids) < 3:
            return []
        adjacency = self._build_adjacency_list(node_ids)
        communities = self._label_propagation_igraph(adjacency, node_ids, num_communities)
        if communities is None:
            communities = self._label_propagation(adjacency, node_ids, num_communities)
        enriched_communities = []
        for comm_id, node_list in communities.items():
            community = Community(community_id=comm_id, nodes=node_list)
            community.cohesion_score = self._calculate_community_cohesion(node_list, adjacency)
            type_counts = {}
            for node_id in node_list:
                node = self.knowledge_layer._backend.get_node(node_id)
                if node:
                    node_type = node.node_type.value
                    type_counts[node_type] = type_counts.get(node_type, 0) + 1
            if type_counts:
                community.dominant_type = max(type_counts, key=type_counts.get)
            community.key_characteristics = self._extract_community_characteristics(node_list)
            enriched_communities.append(community)
        enriched_communities.sort(key=attrgetter("cohesion_score"), reverse=True)
        logger.info(f"Detected {len(enriched_communities)} communities")
        return enriched_communities

    def find_contradictions(self, confidence_threshold: float = 0.7) -> list[GraphContradiction]:
        """
        Find contradictions between nodes in the graph.

        DEPRECATED - source module deleted:
        "Step 5: Identify contradictions"
        "Find contradiction edges"
        "Assess severity"

        Args:
            confidence_threshold: Minimum confidence to report

        Returns:
            List of detected contradictions
        """
        contradictions = []
        node_ids = self._get_all_node_ids()
        checked_pairs = set()
        for node_id in node_ids:
            node = self.knowledge_layer._backend.get_node(node_id)
            if not node:
                continue
            related = self.knowledge_layer.get_related(node_id, max_depth=1)
            for related_id, related_node in related.get("nodes", {}).items():
                pair_key = tuple(sorted([node_id, related_id]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                contradiction = self._analyze_contradiction(node, related_node)
                if contradiction and contradiction.severity >= confidence_threshold:
                    contradictions.append(contradiction)
        contradictions.sort(key=attrgetter("severity"), reverse=True)
        logger.info(f"Found {len(contradictions)} contradictions")
        return contradictions

    async def analyze_key_paths(
        self, start_node_id: str, target_node_id: str, max_hops: int = 3
    ) -> list[dict[str, Any]]:
        """
        Analyze key paths between two nodes (async).

        DEPRECATED - source module deleted:
        "Step 6: Analyze key paths in the network"
        "Find shortest paths between central nodes"
        "Look for paths that might be important reasoning chains"
        "Calculate path confidence"

        Args:
            start_node_id: Starting node
            target_node_id: Target node
            max_hops: Maximum path length

        Returns:
            List of paths with confidence scores
        """
        paths = await self.find_connections(
            self._get_node_content(start_node_id) or start_node_id,
            self._get_node_content(target_node_id) or target_node_id,
            max_hops=max_hops,
        )
        for path in paths:
            path_length = path.get("length", 0)
            path["confidence"] = max(0.3, 1.0 - path_length * 0.2)
            path["is_key_path"] = path_length <= 2
        paths.sort(key=attrgetter("get")("confidence", 0), reverse=True)
        return paths

    def calculate_network_metrics(self) -> dict[str, Any]:
        """
        Calculate comprehensive network metrics.

        DEPRECATED - source module deleted:
        "Step 7: Calculate network metrics"
        "Basic metrics"
        "Clustering metrics"
        "Path metrics"
        "Evidence-specific metrics"

        Returns:
            Dictionary of network metrics
        """
        node_ids = self._get_all_node_ids()
        if not node_ids:
            return {}
        adjacency = self._build_adjacency_list(node_ids)
        num_nodes = len(node_ids)
        num_edges = sum(len(neighbors) for neighbors in adjacency.values()) // 2
        max_edges = num_nodes * (num_nodes - 1) // 2
        density = num_edges / max_edges if max_edges > 0 else 0
        avg_degree = 2 * num_edges / num_nodes if num_nodes > 0 else 0
        clustering = self._calculate_clustering_coefficient(adjacency, node_ids)
        avg_path_length = self._calculate_average_path_length(adjacency, node_ids)
        metrics = {
            "num_nodes": num_nodes,
            "num_edges": num_edges,
            "density": density,
            "average_degree": avg_degree,
            "clustering_coefficient": clustering,
            "average_path_length": avg_path_length,
            "connectivity": "high" if density > 0.3 else "medium" if density > 0.1 else "low",
        }
        logger.info(f"Network metrics: {metrics}")
        return metrics

    def _get_all_node_ids(self) -> list[str]:
        """Get all node IDs from knowledge layer."""
        return self.knowledge_layer._backend.get_all_node_ids()

    def _build_adjacency_list(self, node_ids: list[str]) -> dict[str, set[str]]:
        """Build adjacency list for graph analysis."""
        adjacency = {node_id: set() for node_id in node_ids}
        for node_id in node_ids:
            related = self.knowledge_layer.get_related(node_id, max_depth=1)
            for related_id in related.get("nodes", {}).keys():
                if related_id in adjacency:
                    adjacency[node_id].add(related_id)
        return adjacency

    def _get_node_content(self, node_id: str) -> str | None:
        """Get node content by ID."""
        node = self.knowledge_layer._backend.get_node(node_id)
        return node.content if node else None

    def _build_ig_graph(self, adjacency: dict[str, set[str]], all_nodes: list[str]):
        """Build an igraph from adjacency list. M1-optimized, C-core."""
        ig_mod = lazy_ig()
        if ig_mod is None:
            return None
        g = ig_mod.Graph()
        node_map: dict[str, int] = {}
        for node in all_nodes:
            idx = g.add_vertex(node)
            node_map[node] = idx
        for src, neighbors in adjacency.items():
            s_idx = node_map.get(src)
            if s_idx is None:
                continue
            for dst in neighbors:
                d_idx = node_map.get(dst)
                if d_idx is None:
                    continue
                try:
                    edge_id = g.get_eid(s_idx, d_idx, error=False)
                    if edge_id < 0:
                        g.add_edge(s_idx, d_idx)
                except Exception:
                    try:
                        g.add_edge(s_idx, d_idx)
                    except Exception:  # noqa: BLE001
                        pass
        return g

    @staticmethod
    def _normalize_max(values: list[float], fallback: list[float]) -> list[float]:
        """Normalize values to [0,1] by dividing by max, with fallback on error.

        Args:
            values: Raw metric values.
            fallback: Fallback list returned on exception or empty values.

        Returns:
            Normalized values in [0,1], or fallback on error/empty.
        """
        if not values:
            return fallback
        try:
            max_val = max(values)
            if max_val <= 0:
                return fallback
            return [v / max_val for v in values]
        except Exception:
            return fallback

    def _calculate_centrality_igraph(
        self, adjacency: dict[str, set[str]], all_nodes: list[str]
    ) -> dict[str, dict[str, float]]:
        """Calculate all centrality metrics via Rust rayon (primary) or igraph C-core (fallback).

        Returns {node_id: {degree, betweenness, closeness, eigenvector, pagerank}}.
        Falls back to empty dict on error.

        B8-fix: Rust batch_centrality_all computes ALL 5 metrics in a single pass
        over the adjacency list — O(1) call vs the old per-metric igraph calls.
        Betweenness uses Brandes algorithm with parallel source-node dispatch.
        For large graphs (>2000 nodes), betweenness uses sampling approximation.
        """
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust

            _rust_ext = rust.raw.module
            adj_list: list[tuple[str, list[str]]] = [
                (node_id, list(neighbors)) for node_id, neighbors in adjacency.items()
            ]
            rust_result = _rust_ext.batch_centrality_all(adj_list)
            if rust_result:
                return dict(rust_result)
        except Exception:  # noqa: BLE001
            pass
        if not _check_ram_for_igraph():
            return {}
        ig_mod = lazy_ig()
        if ig_mod is None:
            return {}
        g = self._build_ig_graph(adjacency, all_nodes)
        if g is None or g.vcount() == 0:
            return {}
        n = g.vcount()
        scores: dict[str, dict[str, float]] = {}
        try:
            strength_list = list(g.strength(vertices=list(range(n)), weights=None, mode="all", loops=True))
        except Exception:
            strength_list = list(g.degree())
        degree_norm = self._normalize_max(strength_list, [0.0] * n)

        k = min(100, n)
        try:
            between_list = list(g.betweenness(vertices=None, directed=False, weights=None, cutoff=k))
        except Exception:
            between_list = []
        between_norm = self._normalize_max(between_list, [0.0] * n)

        try:
            closeness_list = list(g.closeness(vertices=None, mode="all", cutoff=None))
        except Exception:
            closeness_list = [0.0] * n

        try:
            ev_result = g.eigenvector_centrality(weights=None, directed=False)
        except Exception:
            ev_result = []
        ev_norm = self._normalize_max(list(ev_result), [0.0] * n)

        try:
            pr_list = g.pagerank(weights=None, directed=False, alpha=0.85)
        except Exception:
            pr_list = []
        pr_norm = self._normalize_max(pr_list, [0.0] * n)

        names = g.vs["name"]
        for i, name in enumerate(names):
            scores[str(name)] = {
                "degree": degree_norm[i] if i < len(degree_norm) else 0.0,
                "betweenness": between_norm[i] if i < len(between_norm) else 0.0,
                "closeness": closeness_list[i] if i < len(closeness_list) else 0.0,
                "eigenvector": ev_norm[i] if i < len(ev_norm) else 0.0,
                "pagerank": pr_norm[i] if i < len(pr_norm) else 0.0,
            }
        return scores

    def _get_centrality_metric(
        self,
        node_id: str,
        metric: Literal["betweenness", "closeness", "eigenvector", "pagerank"],
        adjacency: dict[str, set[str]],
        all_nodes: list[str],
        _cached_centrality: dict[str, dict[str, float]] | None = None,
    ) -> float:
        """Extract a named centrality metric, using cache or computing via igraph.

        Args:
            node_id: Target node ID.
            metric: One of the four supported metrics.
            adjacency: Graph adjacency dict.
            all_nodes: List of all node IDs.
            _cached_centrality: Pre-computed centrality dict. When provided, avoids
                redundant re-computation of all metrics.
        """
        if _cached_centrality is not None:
            return _cached_centrality.get(node_id, {}).get(metric, 0.0)
        centrality = self._calculate_centrality_igraph(adjacency, all_nodes)
        return centrality.get(node_id, {}).get(metric, 0.0)

    def _label_propagation_igraph(
        self, adjacency: dict[str, set[str]], node_ids: list[str], num_communities: int
    ) -> dict[int, list[str]] | None:
        """Community detection via igraph C-core label propagation (5-10x faster).

        Returns None on igraph unavailable / RAM constraint.
        """
        if not _check_ram_for_igraph():
            return None
        ig_mod = lazy_ig()
        if ig_mod is None:
            return None
        g = self._build_ig_graph(adjacency, node_ids)
        if g is None or g.vcount() == 0:
            return None
        try:
            membership = g.community_label_propagation()
            communities: dict[int, list[str]] = {}
            for i, name in enumerate(g.vs["name"]):
                label = membership[i] if isinstance(membership, (list, tuple)) else membership.membership[i]
                if label not in communities:
                    communities[label] = []
                communities[label].append(str(name))
            sorted_comms = sorted(communities.items(), key=lambda x: len(x[1]), reverse=True)
            return {i: nodes for i, (_, nodes) in enumerate(sorted_comms[:num_communities])}
        except Exception as e:
            logger.debug(f"GraphRAGOrchestrator: igraph label propagation failed: {e}")
            return None

    def _label_propagation(
        self, adjacency: dict[str, set[str]], node_ids: list[str], num_communities: int
    ) -> dict[int, list[str]]:
        """Simple label propagation for community detection."""
        labels = {node: i for i, node in enumerate(node_ids)}
        for _ in range(10):
            for node in node_ids:
                if not adjacency[node]:
                    continue
                label_counts = {}
                for neighbor in adjacency[node]:
                    label = labels[neighbor]
                    label_counts[label] = label_counts.get(label, 0) + 1
                if label_counts:
                    labels[node] = max(label_counts, key=label_counts.get)
        communities: dict[int, list[str]] = {}
        for node, label in labels.items():
            if label not in communities:
                communities[label] = []
            communities[label].append(node)
        sorted_communities = sorted(communities.items(), key=lambda x: len(x[1]), reverse=True)
        return {i: nodes for i, (_, nodes) in enumerate(sorted_communities[:num_communities])}

    def _calculate_community_cohesion(self, node_list: list[str], adjacency: dict[str, set[str]]) -> float:
        """Calculate cohesion score for a community."""
        if len(node_list) < 2:
            return 1.0
        internal_edges = 0
        possible_edges = len(node_list) * (len(node_list) - 1)
        node_set = set(node_list)
        for node in node_set:
            for neighbor in adjacency.get(node, set()):
                if neighbor in node_set:
                    internal_edges += 1
        internal_edges //= 2
        return internal_edges / possible_edges if possible_edges > 0 else 0.0

    def _extract_community_characteristics(self, node_list: list[str]) -> list[str]:
        """Extract key characteristics of a community."""
        characteristics = []
        type_counts = {}
        for node_id in node_list:
            node = self.knowledge_layer._backend.get_node(node_id)
            if node:
                node_type = node.node_type.value
                type_counts[node_type] = type_counts.get(node_type, 0) + 1
        if type_counts:
            dominant = max(type_counts, key=type_counts.get)
            characteristics.append(f"dominant_type:{dominant}")
            if len(type_counts) > 1:
                characteristics.append("mixed_types")
        characteristics.append(f"size:{len(node_list)}")
        return characteristics

    def _analyze_contradiction(self, node_a: Any, node_b: Any) -> GraphContradiction | None:
        """Analyze if two nodes contradict each other."""
        content_a = node_a.content.lower()
        content_b = node_b.content.lower()
        contradiction_indicators = [
            ("not ", ""),
            ("never ", "always "),
            ("no ", "yes "),
            ("false", "true"),
            ("impossible", "possible"),
        ]
        for neg_a, neg_b in contradiction_indicators:
            has_neg_a = neg_a in content_a if neg_a else neg_a not in content_a
            has_neg_b = neg_b in content_b if neg_b else neg_b not in content_b
            if has_neg_a and (not has_neg_b):
                words_a = set(content_a.split())
                words_b = set(content_b.split())
                common_words = words_a & words_b
                if len(common_words) > 3:
                    return GraphContradiction(
                        node_a_id=node_a.id,
                        node_b_id=node_b.id,
                        node_a_content=node_a.content,
                        node_b_content=node_b.content,
                        contradiction_type="factual",
                        severity=0.7,
                        resolution_suggestions=[
                            "Verify source reliability",
                            "Check temporal context",
                            "Consider scope differences",
                        ],
                    )
        return None

    def _calculate_clustering_coefficient(self, adjacency: dict[str, set[str]], node_ids: list[str]) -> float:
        """Calculate average clustering coefficient."""
        coefficients = []
        for node in node_ids:
            neighbors = adjacency.get(node, set())
            if len(neighbors) < 2:
                continue
            triangles = 0
            for neighbor1 in neighbors:
                for neighbor2 in neighbors:
                    if neighbor1 != neighbor2 and neighbor2 in adjacency.get(neighbor1, set()):
                        triangles += 1
            triangles //= 2
            possible = len(neighbors) * (len(neighbors) - 1) // 2
            if possible > 0:
                coefficients.append(triangles / possible)
        if NUMPY_AVAILABLE and coefficients:
            return float(np.mean(coefficients))
        return sum(coefficients) / len(coefficients) if coefficients else 0.0

    def _calculate_average_path_length(self, adjacency: dict[str, set[str]], node_ids: list[str]) -> float:
        """Calculate average shortest path length."""
        path_lengths = []
        for source in node_ids:
            distances = self._calculate_distances(source, adjacency, node_ids)
            for target, distance in distances.items():
                if source != target:
                    path_lengths.append(distance)
        if NUMPY_AVAILABLE and path_lengths:
            return float(np.mean(path_lengths))
        return sum(path_lengths) / len(path_lengths) if path_lengths else 0.0

    def _extract_entities_from_node(self, node: Any) -> set[str]:
        """
        Extract entity mentions from a node for novelty detection.

        Simple entity extraction based on capitalization patterns
        and known entity markers.

        Args:
            node: Knowledge node to extract entities from

        Returns:
            Set of extracted entity strings
        """
        entities = set()
        content = node.content
        if node.node_type.value == "entity":
            entities.add(node.content.lower().strip())
        capitalized = re.findall("\\b[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*\\b", content)
        for entity in capitalized:
            entities.add(entity.lower().strip())
        if node.metadata:
            if "entities" in node.metadata:
                for ent in node.metadata["entities"]:
                    entities.add(str(ent).lower().strip())
            if "title" in node.metadata:
                title_entities = re.findall("\\b[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*\\b", node.metadata["title"])
                for ent in title_entities:
                    entities.add(ent.lower().strip())
        return entities

    def _traverse_hop_with_paths(
        self,
        visited: set[str],
        hop: int,
        max_nodes: int,
        seed_entities: set[str],
        seed_doc_entities: set[str],
        max_edges: int = 500,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Traverse one hop with full path tracking.

        Args:
            visited: Set of already visited node IDs
            hop: Current hop number
            max_nodes: Maximum nodes to collect
            seed_entities: Entities from seed documents
            seed_doc_entities: Entities from the top seed document only
            max_edges: Maximum edges to traverse

        Returns:
            Tuple of (new_facts, new_paths)
        """
        new_facts = []
        new_paths = []
        edges_traversed = 0
        path_context: dict[str, tuple[list[str], list[str]]] = {}
        for node_id in list(visited):
            node = self.knowledge_layer._backend.get_node(node_id)
            if node:
                path_context[node_id] = ([node_id], [node.content])
        frontier = deque(list(visited), maxlen=max_nodes * 2)
        for node_id in frontier:
            if edges_traversed >= max_edges or len(visited) >= max_nodes:
                break
            related = self.knowledge_layer.get_related_sync(node_id, max_depth=1)
            edges = related.get("edges", [])
            for edge in edges:
                if edges_traversed >= max_edges:
                    break
                edges_traversed += 1
                related_id = edge.target_id if edge.source_id == node_id else edge.source_id
                if related_id in visited:
                    continue
                related_node = related.get("nodes", {}).get(related_id)
                if not related_node:
                    continue
                visited.add(related_id)
                if len(visited) > max_nodes:
                    break
                source_path_ids, source_path_content = path_context.get(node_id, ([node_id], []))
                current_path_ids = source_path_ids + [related_id]
                current_path_content = source_path_content + [related_node.content]
                path_context[related_id] = (current_path_ids, current_path_content)
                related_entities = self._extract_entities_from_node(related_node)
                novel_entities = related_entities - seed_doc_entities
                novelty_score = len(novel_entities) / max(len(related_entities), 1) if related_entities else 0.0
                has_new_entity = novel_entities
                has_multi_hop_path = len(current_path_ids) >= 2
                novelty_failed = not (has_new_entity or has_multi_hop_path)
                if novelty_failed:
                    novelty_score = 0.0
                edge_evidence_id = edge.metadata.get("evidence_id") if edge.metadata else None
                if edge_evidence_id:
                    path_evidence_ids = [edge_evidence_id]
                else:
                    path_evidence_ids = [related_node.metadata.get("evidence_id", related_id)]
                fact = {
                    "content": related_node.content,
                    "node_id": related_id,
                    "node_type": related_node.node_type.value,
                    "hop": hop,
                    "similarity": 1.0 - hop * 0.2,
                    "path": current_path_ids,
                    "path_content": current_path_content,
                    "relations": [edge.edge_type.value],
                    "metadata": related_node.metadata,
                    "evidence_ids": path_evidence_ids,
                    "novelty_score": novelty_score,
                    "novelty_failed": novelty_failed,
                    "novel_entities": list(novel_entities)[:5],
                    "edge_metadata": edge.metadata,
                }
                new_facts.append(fact)
                path_entry = {
                    "nodes": current_path_ids,
                    "node_types": [self._get_node_type(nid) for nid in current_path_ids],
                    "relations": [edge.edge_type.value],
                    "score": 1.0 - hop * 0.2,
                    "evidence_ids": path_evidence_ids,
                    "hop": hop,
                    "novelty_failed": novelty_failed,
                }
                new_paths.append(path_entry)
        return (new_facts, new_paths)

    def _get_node_type(self, node_id: str) -> str:
        """Get node type for a node ID."""
        node = self.knowledge_layer._backend.get_node(node_id)
        return node.node_type.value if node else "unknown"

    def _rank_facts_with_novelty(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Rank facts considering novelty score.

        Args:
            facts: List of facts to rank

        Returns:
            Ranked list with novelty bonus
        """

        def calculate_score(fact: dict[str, Any]) -> float:
            similarity = fact.get("similarity", 0.5)
            hop = fact.get("hop", 0)
            novelty = fact.get("novelty_score", 0.0)
            node_type = fact.get("node_type", "fact")
            type_bonus = {"fact": 1.0, "entity": 0.9, "concept": 0.8, "event": 0.7, "url": 0.5, "document": 0.6}.get(
                node_type, 0.5
            )
            hop_penalty = max(0, 1.0 - hop * 0.15)
            novelty_bonus = 1.0 + novelty * 0.25
            score = similarity * type_bonus * hop_penalty * novelty_bonus
            return score

        ranked_facts = sorted(facts, key=calculate_score, reverse=True)
        return ranked_facts

    def _detect_contradictions(
        self, facts: list[dict[str, Any]]
    ) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Detect contradictions in facts using lightweight heuristics.

        Identifies contradictions when:
        1. Same (subject, predicate) with different objects
        2. Explicit negations in predicates (e.g., "is" vs "is_not")

        Args:
            facts: List of facts to analyze

        Returns:
            Tuple of (contested: bool, primary_paths: list, counter_paths: list)
        """
        claims = self._extract_claims(facts)
        contradictions = self._find_semantic_contradictions(claims)
        contradictions.extend(self._find_negation_contradictions(facts))
        if not contradictions:
            return (False, facts, [])
        return self._build_contradiction_paths(facts, contradictions)

    def _extract_claims(self, facts: list[dict[str, Any]]) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Extract (subject, predicate, object, fact) tuples from facts using relation patterns."""
        claims: list[tuple[str, str, str, dict[str, Any]]] = []
        relation_patterns = [
            (r"(.+?)\s+is\s+(.+?)(?:\.|$)", "is"),
            (r"(.+?)\s+has\s+(.+?)(?:\.|$)", "has"),
            (r"(.+?)\s+located\s+in\s+(.+?)(?:\.|$)", "located_in"),
            (r"(.+?)\s+was\s+(.+?)(?:\.|$)", "was"),
            (r"(.+?)\s+has\s+a\s+(.+?)(?:\.|$)", "has_a"),
        ]
        for fact in facts:
            content = fact.get("content", "").lower().strip()
            if not content:
                continue
            for pattern, predicate in relation_patterns:
                match = re.search(pattern, content)
                if match:
                    subject = match.group(1).strip()
                    obj = match.group(2).strip()
                    claims.append((subject, predicate, obj, fact))
                    break
        return claims

    def _find_semantic_contradictions(
        self, claims: list[tuple[str, str, str, dict[str, Any]]]
    ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
        """Find contradictions from same (subject, predicate) with different objects."""
        contradictions: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        claim_groups: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
        for subject, predicate, obj, fact in claims:
            claim_groups.setdefault((subject, predicate), []).append((obj, fact))
        for (subject, predicate), obj_facts in claim_groups.items():
            if len(obj_facts) < 2:
                continue
            unique_objects = {obj for obj, _ in obj_facts}
            if len(unique_objects) < 2:
                continue
            sorted_facts = sorted(obj_facts, key=lambda x: x[1].get("similarity", 0.5), reverse=True)
            primary_obj, primary_fact = sorted_facts[0]
            counter_obj, counter_fact = sorted_facts[1]
            contradictions.append((primary_fact, counter_fact, f"{subject} {predicate} {primary_obj} vs {counter_obj}"))
        return contradictions

    def _find_negation_contradictions(
        self, facts: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
        """Find contradictions from explicit negation patterns between fact pairs."""
        contradictions: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        negation_patterns = [("is", "is not"), ("has", "has no"), ("can", "cannot"), ("will", "will not")]
        for i, fact_a in enumerate(facts):
            content_a = fact_a.get("content", "").lower()
            for fact_b in facts[i + 1 :]:
                if contradiction := self._check_negation_pair(content_a, fact_a, fact_b, negation_patterns):
                    contradictions.append(contradiction)
        return contradictions

    def _check_negation_pair(
        self, content_a: str, fact_a: dict[str, Any], fact_b: dict[str, Any], negation_patterns: list[tuple[str, str]]
    ) -> tuple[dict[str, Any], dict[str, Any], str] | None:
        """Check if two facts contradict via negation patterns. Returns contradiction tuple or None."""
        content_b = fact_b.get("content", "").lower()
        for pos, neg in negation_patterns:
            a_has_pos = f" {pos} " in f" {content_a} "
            b_has_neg = f" {neg} " in f" {content_b} "
            a_has_neg = f" {neg} " in f" {content_a} "
            b_has_pos = f" {pos} " in f" {content_b} "
            if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
                words_a = set(content_a.split()) - {pos, neg}
                words_b = set(content_b.split()) - {pos, neg}
                if len(words_a & words_b) >= 3:
                    return (fact_a, fact_b, f"negation: {pos} vs {neg}")
        return None

    def _build_contradiction_paths(
        self, facts: list[dict[str, Any]], contradictions: list[tuple[dict[str, Any], dict[str, Any], str]]
    ) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
        """Build primary and counter paths from contradictions."""
        primary_paths: list[dict[str, Any]] = []
        counter_paths: list[dict[str, Any]] = []
        seen_primary_nodes: set[str] = set()
        for primary_fact, counter_fact, reason in contradictions:
            if primary_fact.get("node_id") not in seen_primary_nodes:
                primary_paths.append(primary_fact)
                seen_primary_nodes.add(primary_fact.get("node_id", ""))
            counter_paths.append(
                {**counter_fact, "contradiction_reason": reason, "contradicts": primary_fact.get("node_id")}
            )
        seen_counter_nodes = {c.get("node_id") for c in counter_paths}
        for fact in facts:
            if fact not in primary_paths and fact.get("node_id") not in seen_counter_nodes:
                primary_paths.append(fact)
        logger.info(f"[CONTRADICTION] Found {len(contradictions)} contradictions: {[r for _, _, r in contradictions]}")
        return (True, primary_paths, counter_paths)

    def _generate_path_summary(
        self,
        facts: list[dict[str, Any]],
        query: str,
        contested: bool = False,
        counter_paths: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Generate human-readable summary of graph paths.

        Args:
            facts: List of facts to summarize
            query: Original query
            contested: Whether results contain contradictions
            counter_paths: Alternative paths showing contradictions

        Returns:
            Summary text (Hermes-friendly)
        """
        if not facts:
            return f"No relevant information found for: {query}"
        lines = [f"Graph analysis for: {query}", ""]
        if contested and counter_paths:
            lines.append("⚠️  CONTRADICTORY EVIDENCE DETECTED:")
            lines.append("Multiple sources provide conflicting information:")
            for i, counter in enumerate(counter_paths[:2], 1):
                counter.get("contradiction_reason", "conflict")
                lines.append(f"  Variant {i}: {counter.get('content', '')[:80]}...")
            lines.append("")
        by_hop: dict[int, list[dict[str, Any]]] = {}
        for fact in facts:
            hop = fact.get("hop", 0)
            by_hop.setdefault(hop, []).append(fact)
        for hop in sorted(by_hop.keys()):
            hop_facts = by_hop[hop]
            if hop == 0:
                lines.append(f"Direct matches ({len(hop_facts)}):")
            else:
                lines.append(f"Hop {hop} connections ({len(hop_facts)}):")
            for fact in hop_facts[:3]:
                content = fact["content"][:100] + "..." if len(fact["content"]) > 100 else fact["content"]
                novelty_flag = " [NOVEL]" if fact.get("novelty_score", 0) > 0.3 else ""
                lines.append(f"  • {content}{novelty_flag}")
                if fact.get("path_content") and len(fact["path_content"]) > 1:
                    path_str = " -> ".join([p[:30] + "..." if len(p) > 30 else p for p in fact["path_content"]])
                    lines.append(f"    Path: {path_str}")
                if fact.get("evidence_ids"):
                    evidence_str = ", ".join(fact["evidence_ids"][:2])
                    lines.append(f"    Evidence: {evidence_str}...")
            lines.append("")
        return "\n".join(lines)

    def _filter_by_time(
        self, facts: list[dict[str, Any]], time_min: str | None, time_max: str | None
    ) -> list[dict[str, Any]]:
        """
        Filter facts by time range.

        Args:
            facts: List of facts to filter
            time_min: ISO datetime minimum (inclusive)
            time_max: ISO datetime maximum (inclusive)

        Returns:
            Filtered list of facts
        """
        from datetime import datetime

        def get_timestamp(fact: dict[str, Any]) -> datetime | None:
            """Extract timestamp from fact metadata."""
            metadata = fact.get("metadata", {})
            ts_str = metadata.get("fetched_at") or metadata.get("published_at")
            if ts_str:
                try:
                    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError) as _e:
                    logger.debug("fail-soft suppression: get_timestamp (min): %s", _e, exc_info=True)
            return None

        filtered = []
        min_dt = datetime.fromisoformat(time_min.replace("Z", "+00:00")) if time_min else None
        max_dt = datetime.fromisoformat(time_max.replace("Z", "+00:00")) if time_max else None
        for fact in facts:
            ts = get_timestamp(fact)
            if ts is None:
                filtered.append(fact)
                continue
            if min_dt and ts < min_dt:
                continue
            if max_dt and ts > max_dt:
                continue
            filtered.append(fact)
        return filtered

    def _apply_recency_boost(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Boost scores of more recent facts.

        Args:
            facts: List of facts to boost

        Returns:
            Facts with boosted scores
        """
        from datetime import datetime

        def get_timestamp(fact: dict[str, Any]) -> datetime:
            """Extract timestamp from fact metadata."""
            metadata = fact.get("metadata", {})
            ts_str = metadata.get("fetched_at") or metadata.get("published_at")
            if ts_str:
                try:
                    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError) as _e:
                    logger.debug("fail-soft suppression: get_timestamp (max): %s", _e, exc_info=True)
            return datetime.min

        newest = max((get_timestamp(f) for f in facts), default=datetime.min)
        if newest == datetime.min:
            return facts
        boosted = []
        for fact in facts:
            ts = get_timestamp(fact)
            age_days = (newest - ts).days if ts != datetime.min else 365
            recency_boost = max(0, 1.0 - age_days / 30) * 0.2
            fact_copy = fact.copy()
            fact_copy["similarity"] = fact.get("similarity", 0.5) * (1.0 + recency_boost)
            boosted.append(fact_copy)
        boosted.sort(key=itemgetter("similarity"), reverse=True)
        return boosted

    def _generate_timeline(self, facts: list[dict[str, Any]], bucket: str, max_points: int) -> list[dict[str, Any]]:
        """
        Generate timeline points from facts.

        Args:
            facts: Facts with timestamps
            bucket: Time bucketing ("month" or "year")
            max_points: Maximum timeline points (hard limit: 12)

        Returns:
            List of timeline points
        """
        from collections import defaultdict
        from datetime import datetime

        max_points = min(max_points, 12)

        def get_bucket_key(fact: dict[str, Any]) -> str | None:
            """Get time bucket key for fact."""
            metadata = fact.get("metadata", {})
            ts_str = metadata.get("fetched_at") or metadata.get("published_at")
            if not ts_str:
                return None
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if bucket == "year":
                    return dt.strftime("%Y")
                else:
                    return dt.strftime("%Y-%m")
            except (ValueError, AttributeError):
                return None

        bucket_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in facts:
            key = get_bucket_key(fact)
            if key:
                bucket_facts[key].append(fact)
        sorted_buckets = sorted(bucket_facts.keys())
        timeline_points = []
        for bucket_key in sorted_buckets[:max_points]:
            facts_in_bucket = bucket_facts[bucket_key]
            top_paths = sorted(facts_in_bucket, key=attrgetter("get")("similarity", 0), reverse=True)[:3]
            key_claims = []
            for fact in facts_in_bucket[:5]:
                content = fact.get("content", "")
                key_claims.append(content[:100] + "..." if len(content) > 100 else content)
            evidence_ids = set()
            for fact in facts_in_bucket:
                for eid in fact.get("evidence_ids", []):
                    evidence_ids.add(eid)
                if len(evidence_ids) >= 20:
                    break
            notes = f"{len(facts_in_bucket)} facts, {len(evidence_ids)} unique evidence sources"
            timeline_points.append(
                {
                    "bucket": bucket_key,
                    "top_paths": [
                        {"content": p.get("content", "")[:100], "score": p.get("similarity", 0)} for p in top_paths
                    ],
                    "key_claims": key_claims[:5],
                    "evidence_ids": list(evidence_ids)[:20],
                    "notes": notes,
                }
            )
        return timeline_points

    def _detect_drift(self, facts: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
        """
        Detect drift events - when claims about same (subject, predicate) change over time.

        Args:
            facts: Facts to analyze
            bucket: Time bucketing for detecting change points

        Returns:
            List of drift events (max 10)
        """
        from collections import defaultdict
        from datetime import datetime

        claims_with_ts = []
        for fact in facts:
            claim = self._extract_claim(fact.get("content", ""))
            if not claim:
                continue
            metadata = fact.get("metadata", {})
            ts_str = metadata.get("fetched_at") or metadata.get("published_at")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                bucket_key = ts.strftime("%Y-%m") if bucket == "month" else ts.strftime("%Y")
                claims_with_ts.append((claim, bucket_key, fact))
            except (ValueError, AttributeError):
                continue
        claim_groups: dict[tuple, list[tuple]] = defaultdict(list)
        for (subject, predicate, obj), bucket_key, fact in claims_with_ts:
            claim_groups[subject, predicate].append((obj, bucket_key, fact))
        drift_events = []
        for (subject, predicate), obj_facts in claim_groups.items():
            if len(obj_facts) < 2:
                continue
            obj_facts.sort(key=lambda x: x[1])
            prev_obj = obj_facts[0][0]
            obj_facts[0][1]
            for obj, bucket_key, fact in obj_facts[1:]:
                if obj != prev_obj:
                    drift_events.append(
                        {
                            "subject": subject,
                            "predicate": predicate,
                            "before": prev_obj,
                            "after": obj,
                            "bucket_change": bucket_key,
                            "supporting_evidence_ids": fact.get("evidence_ids", [])[:10],
                            "confidence": fact.get("similarity", 0.5),
                        }
                    )
                    prev_obj = obj
                if len(drift_events) >= 10:
                    break
            if len(drift_events) >= 10:
                break
        return drift_events

    def _extract_claim(self, content: str) -> tuple | None:
        """
        Extract (subject, predicate, object) claim from content.

        Args:
            content: Text content to parse

        Returns:
            Tuple of (subject, predicate, object) or None
        """
        content_lower = content.lower().strip()
        patterns = [
            ("(.+?)\\s+is\\s+(.+?)(?:\\.|$)", "is"),
            ("(.+?)\\s+has\\s+(.+?)(?:\\.|$)", "has"),
            ("(.+?)\\s+was\\s+(.+?)(?:\\.|$)", "was"),
            ("(.+?)\\s+located\\s+in\\s+(.+?)(?:\\.|$)", "located_in"),
            ("(.+?)\\s+located\\s+at\\s+(.+?)(?:\\.|$)", "located_at"),
        ]
        for pattern, predicate in patterns:
            match = re.search(pattern, content_lower)
            if match:
                subject = match.group(1).strip()
                obj = match.group(2).strip()
                return (subject, predicate, obj)
        return None

    def _detect_contradictions_with_narratives(self, facts: list[dict[str, Any]]) -> tuple:
        """
        Detect contradictions and generate competing narratives with confidence.

        Args:
            facts: Facts to analyze

        Returns:
            Tuple of (contested, primary_paths, counter_paths, narratives)
        """
        contested, primary_paths, counter_paths = self._detect_contradictions(facts)
        if not contested:
            return (False, primary_paths, counter_paths, [])
        narratives = self._build_narratives(primary_paths, counter_paths)
        return (contested, primary_paths[:10], counter_paths[:5], narratives[:3])

    def _build_narratives(
        self, primary_paths: list[dict[str, Any]], counter_paths: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Build competing narratives from contradictory evidence.

        Args:
            primary_paths: Primary evidence paths
            counter_paths: Counter evidence paths

        Returns:
            List of narrative objects (max 3)
        """
        if not counter_paths:
            return []
        narratives = []
        primary_evidence = []
        primary_domains = set()
        for fact in primary_paths[:5]:
            primary_evidence.extend(fact.get("evidence_ids", []))
            url = fact.get("metadata", {}).get("url", "")
            if url:
                domain = url.split("/")[2] if "://" in url else url.split("/")[0]
                primary_domains.add(domain)
        primary_confidence = self._calculate_narrative_confidence(primary_paths[:5], primary_evidence, primary_domains)
        primary_summary = self._summarize_narrative(primary_paths[:3])
        narratives.append(
            {
                "narrative_id": "A",
                "summary": primary_summary,
                "support_paths": primary_paths[:5],
                "support_evidence_ids": list(set(primary_evidence))[:25],
                "confidence": primary_confidence,
                "notes": f"supported by {len(primary_domains)} unique source domains, {len(set(primary_evidence))} unique evidence items",
            }
        )
        if counter_paths:
            counter_evidence = []
            counter_domains = set()
            for fact in counter_paths[:5]:
                counter_evidence.extend(fact.get("evidence_ids", []))
                url = fact.get("metadata", {}).get("url", "")
                if url:
                    domain = url.split("/")[2] if "://" in url else url.split("/")[0]
                    counter_domains.add(domain)
            counter_confidence = self._calculate_narrative_confidence(
                counter_paths[:5], counter_evidence, counter_domains
            )
            counter_summary = self._summarize_narrative(counter_paths[:3])
            narratives.append(
                {
                    "narrative_id": "B",
                    "summary": counter_summary,
                    "support_paths": counter_paths[:5],
                    "support_evidence_ids": list(set(counter_evidence))[:25],
                    "confidence": counter_confidence,
                    "notes": f"alternative view supported by {len(counter_domains)} unique source domains",
                }
            )
        return narratives

    def _calculate_narrative_confidence(
        self, paths: list[dict[str, Any]], evidence_ids: list[str], domains: set[str]
    ) -> float:
        """
        Calculate narrative confidence score (0-1).

        Factors:
        - Number of unique evidence sources
        - Domain diversity
        - Recency
        - Echo penalty
        """
        if not paths:
            return 0.0
        unique_evidence = len(set(evidence_ids))
        evidence_score = min(1.0, unique_evidence / 5) * 0.4
        domain_score = min(1.0, len(domains) / 3) * 0.3
        avg_similarity = sum(p.get("similarity", 0.5) for p in paths) / len(paths)
        similarity_score = avg_similarity * 0.2
        content_hashes = set()
        echo_count = 0
        for p in paths:
            metadata = p.get("metadata", {})
            hash_ring = metadata.get("content_hash_ring", [])
            for h in hash_ring:
                if h in content_hashes:
                    echo_count += 1
                content_hashes.add(h)
        echo_penalty = min(0.2, echo_count * 0.05)
        confidence = evidence_score + domain_score + similarity_score - echo_penalty
        return max(0.0, min(1.0, confidence))

    def _summarize_narrative(self, paths: list[dict[str, Any]]) -> str:
        """
        Generate 1-3 sentence summary of narrative.
        """
        if not paths:
            return "No clear narrative found."
        contents = []
        for p in paths[:3]:
            content = p.get("content", "")
            if content:
                first_sentence = content.split(".")[0] + "." if "." in content else content[:100]
                contents.append(first_sentence)
        if len(contents) == 1:
            return contents[0]
        elif len(contents) == 2:
            return f"{contents[0]} Additionally, {contents[1].lower()}"
        else:
            return f"{contents[0]} {contents[1]} This view also suggests {contents[2].lower()}"

    async def multi_hop_search_streaming(self, query: str, hops: int = 2, max_nodes: int = 20):
        """
        Streaming version of multi-hop search that yields nodes as they are discovered.

        Enables early processing of results before full traversal completes.
        Uses asyncio.Queue for backpressure control.

        Args:
            query: Search query
            hops: Number of hops to traverse (default: 2)
            max_nodes: Maximum nodes to return (default: 20)

        Yields:
            Dict representing a discovered node with its metadata
        """
        # [FINAL]-019-06: Cap hops under CRITICAL/MINIMAL QoS.
        hops = get_degradation_safe_max_hops(hops)
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        worker_task = safe_create_task(self._traversal_worker(query, hops, max_nodes, queue))
        try:
            while True:
                node = await queue.get()
                if node is None:
                    break
                yield node
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError as _e:
                logger.debug("fail-soft suppression: multi_hop_search_streaming (cancel): %s", _e, exc_info=True)

    async def _traversal_worker(self, query: str, hops: int, max_nodes: int, queue: asyncio.Queue) -> None:
        """
        Worker that performs graph traversal and pushes discovered nodes to queue.

        Args:
            query: Search query
            hops: Number of hops to traverse
            max_nodes: Maximum nodes to discover
            queue: Queue to push discovered nodes to
        """
        visited: set[str] = set()
        seed_entities: set[str] = set()
        try:
            initial_results = await self.knowledge_layer.search(query, limit=10)
            for node, similarity in initial_results:
                node_id = node.id
                if node_id in visited:
                    continue
                if len(visited) >= max_nodes:
                    break
                visited.add(node_id)
                node_entities = self._extract_entities_from_node(node)
                seed_entities.update(node_entities)
                node_data = {
                    "content": node.content,
                    "node_id": node_id,
                    "node_type": node.node_type.value,
                    "hop": 0,
                    "similarity": similarity,
                    "path": [node_id],
                    "relations": [],
                    "metadata": node.metadata,
                }
                await queue.put(node_data)
            for hop in range(1, hops + 1):
                if len(visited) >= max_nodes:
                    break
                new_facts = self._traverse_hop_with_paths(visited, hop, max_nodes, seed_entities, set())[0]
                for fact in new_facts:
                    if len(visited) >= max_nodes:
                        break
                    await queue.put(fact)
        except asyncio.CancelledError as _e:
            logger.debug("fail-soft suppression: _traversal_worker (cancel): %s", _e, exc_info=True)
        except Exception as e:
            logger.warning(f"Traversal worker error: {e}")
        finally:
            await queue.put(None)

    @staticmethod
    def subgraph_to_chatml_context(
        subgraph: dict[str, Any],
        query_context: str = "",
        max_node_value_len: int = 80,
        max_output_bytes: int = 8192,
    ) -> str:
        """
        Serialize an extracted IOC subgraph into canonical ChatML format.

        Accepts the output of IOCGraph.extract_k_hop_subgraph() and wraps
        the graph data in a system-context ChatML block suitable for
        injection into DeepHermes3 / MLX inference pipelines.

        Token-efficient: 2-char node keys, edges capped at 200, all
        value strings truncated independently. Total output is budgeted
        to max_output_bytes via progressive edge truncation.

        Args:
            subgraph: Dict from IOCGraph.extract_k_hop_subgraph().
            query_context: Optional query string (capped at 200 chars).
            max_node_value_len: Max chars per node/edge value (default 80).
            max_output_bytes: Hard cap on serialized JSON payload (default 8192).

        Returns:
            ChatML-formatted string: <|im_start|>system\n{json}<|im_end|>
        """
        import orjson

        nodes: list[dict[str, Any]] = subgraph.get("nodes", [])
        edges: list[dict[str, Any]] = subgraph.get("edges", [])
        stats: dict[str, Any] = subgraph.get("stats", {})
        seed_value: str = subgraph.get("seed_value", "")
        seed_type: str = subgraph.get("seed_type", "")
        k: int = subgraph.get("k", 0)
        truncated: bool = subgraph.get("truncated", False)

        # Cap query_context independently
        qc = (query_context or "(none)")[:200]

        # Compact node catalog: id → {t, v, c}
        # Cap node count by budget (~120 bytes per node serialized)
        max_node_count = max(1, max_output_bytes // 120)
        node_catalog: dict[str, dict[str, Any]] = {}
        for n in nodes[:max_node_count]:
            val = str(n.get("value", ""))[:max_node_value_len]
            node_catalog[n["id"]] = {
                "t": n.get("ioc_type", "?"),
                "v": val,
                "c": round(n.get("confidence", 1.0), 2),
            }

        # Degree ranking for key-node insights
        degree: dict[str, int] = {}
        for e in edges:
            degree[e["source_id"]] = degree.get(e["source_id"], 0) + 1
            degree[e["target_id"]] = degree.get(e["target_id"], 0) + 1

        key_nodes = sorted(degree.items(), key=lambda x: -x[1])[:10]
        key_node_list: list[dict[str, Any]] = [
            {"id": nid, "deg": d, "v": node_catalog.get(nid, {}).get("v", "?")} for nid, d in key_nodes
        ]

        # Measure fixed overhead (seed, query, topology, key_nodes, empty edges)
        skeleton = {
            "graph": {
                "seed": f"{seed_value[:max_node_value_len]} ({seed_type})",
                "radius": k,
                "query": qc,
                "topology": {
                    "nodes": stats.get("total_nodes", 0),
                    "edges": stats.get("total_edges", 0),
                    "density": stats.get("density", 0),
                    "max_degree": stats.get("max_degree", 0),
                    "truncated": truncated,
                },
                "key_nodes": key_node_list,
                "nodes": node_catalog,
                "edges": [],
            },
        }
        skeleton_blob = orjson.dumps(skeleton, option=orjson.OPT_APPEND_NEWLINE)
        fixed_overhead = len(skeleton_blob) - 2  # minus []
        budget = max(200, max_output_bytes - fixed_overhead)

        # Fill edges within budget (capped at 200)
        compact_edges: list[dict[str, str]] = []
        for e in edges[:200]:
            src_cat = node_catalog.get(e["source_id"], {})
            dst_cat = node_catalog.get(e["target_id"], {})
            compact_edges.append(
                {
                    "s": src_cat.get("v", "?"),
                    "d": dst_cat.get("v", "?"),
                    "st": str(e.get("source_type", "?"))[:40],
                }
            )
            # ~80 bytes per edge (two values + type + JSON overhead)
            if len(compact_edges) * 80 > budget:
                break

        skeleton["graph"]["edges"] = compact_edges
        json_blob: str = orjson.dumps(skeleton, option=orjson.OPT_APPEND_NEWLINE).decode("utf-8")

        return f"<|im_start|>system\n{json_blob}<|im_end|>"

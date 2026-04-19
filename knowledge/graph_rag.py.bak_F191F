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

Extended from evidence_network_analyzer.py comments:
    - Centrality analysis (degree, betweenness, closeness, eigenvector, PageRank)
    - Community detection
    - Network metrics
    - Key path analysis
"""

import asyncio
import concurrent.futures
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

from hledac.universal.legacy.persistent_layer import KnowledgeNode

logger = logging.getLogger(__name__)


@dataclass
class CentralityScores:
    """Centrality analysis results for a node."""
    node_id: str
    degree: float = 0.0
    betweenness: float = 0.0
    closeness: float = 0.0
    eigenvector: float = 0.0
    pagerank: float = 0.0
    overall_influence: float = 0.0


@dataclass
class Community:
    """Detected community in the graph."""
    community_id: int
    nodes: List[str] = field(default_factory=list)
    cohesion_score: float = 0.0
    dominant_type: str = "mixed"
    key_characteristics: List[str] = field(default_factory=list)


@dataclass
class GraphContradiction:
    """Contradiction detected in the graph."""
    node_a_id: str
    node_b_id: str
    node_a_content: str
    node_b_content: str
    contradiction_type: str  # factual, opinion, statistical
    severity: float  # 0-1
    resolution_suggestions: List[str] = field(default_factory=list)


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

    # PHASE 13: Streaming caps for bounded memory
    MAX_QUEUE_LENGTH = 100  # Max items in traversal queue
    MAX_VISITED_NODES = 500  # Max visited nodes to track
    MAX_EXPANSION_PER_NODE = 10  # Max edges to expand per node

    def __init__(self, knowledge_layer):
        """
        Initialize GraphRAG orchestrator.

        Args:
            knowledge_layer: PersistentKnowledgeLayer instance
        """
        self.knowledge_layer = knowledge_layer
        # Shared thread pool for safe async execution (reused across calls)
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Cached embedder for score_path (lazy initialization)
        self._embedder = None
        self._embedder_lock = None
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
                        # Sprint 81 Fáze 4: Sdílený singleton místo RAGEngine()
                        from hledac.universal.core.mlx_embeddings import get_embedding_manager
                        self._embedder = get_embedding_manager()
                        logger.debug("[EMBEDDER] graph_rag using shared MLXEmbeddingManager singleton")
                    except Exception as e:
                        logger.warning(f"Failed to get shared embedder: {e}")
                        return None
        return self._embedder

    async def score_path(
        self,
        path: List[str],
        hypothesis: str,
        hypothesis_emb: Optional[List[float]] = None,
        max_nodes: int = 10
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
        import numpy as np  # Local import for score computation

        if len(path) < 2:
            return 0.0

        nodes_to_score = path[:max_nodes]

        # 1. Path length score (shorter = better)
        length_score = 1.0 / max(1, len(path))

        # 2. Relevance to hypothesis
        try:
            embedder = await self._get_embedder()
            if embedder is None:
                relevance_score = 0.5
            else:
                if hypothesis_emb is None:
                    # MLXEmbeddingManager.embed_document is sync - use asyncio.to_thread
                    try:
                        emb_result = await asyncio.to_thread(embedder.embed_document, hypothesis)
                        if emb_result is not None and len(emb_result) > 0:
                            hypothesis_emb = emb_result.tolist() if hasattr(emb_result, 'tolist') else list(emb_result)
                        else:
                            hypothesis_emb = [0.0] * 384  # Fallback
                    except Exception:
                        hypothesis_emb = [0.0] * 384  # Fallback
                else:
                    hypothesis_emb = np.array(hypothesis_emb)

                node_embeddings = []
                for node_id in nodes_to_score:
                    try:
                        node = await self.knowledge_layer.get_node(node_id)
                        if node and node.embedding:
                            node_embeddings.append(node.embedding)
                    except Exception:
                        continue

                if node_embeddings:
                    # Cosine similarity
                    norm_hyp = np.linalg.norm(hypothesis_emb)
                    if norm_hyp > 0:
                        sims = [
                            np.dot(hypothesis_emb, emb) / (norm_hyp * np.linalg.norm(emb) + 1e-8)
                            for emb in node_embeddings
                        ]
                        relevance_score = float(np.mean(sims))
                    else:
                        relevance_score = 0.5
                else:
                    relevance_score = 0.5
        except Exception as e:
            logger.debug(f"score_path relevance computation failed: {e}")
            relevance_score = 0.5

        # 3. Node credibility
        try:
            scores = []
            for node_id in nodes_to_score:
                try:
                    node = await self.knowledge_layer.get_node(node_id)
                    if node and node.metadata and "confidence" in node.metadata:
                        scores.append(node.metadata["confidence"])
                except Exception:
                    continue
            credibility = sum(scores) / len(scores) if scores else 0.5
        except Exception:
            credibility = 0.5

        # Weighted final score
        final_score = 0.4 * length_score + 0.4 * relevance_score + 0.2 * credibility
        return float(max(0.0, min(1.0, final_score)))

    async def multi_hop_search(
        self,
        query: str,
        hops: int = 2,
        max_nodes: int = 20,
        timeline: bool = False,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        prefer_recent: bool = True,
        bucket: str = "month",
        max_timeline_points: int = 12
    ) -> Dict[str, Any]:
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
        logger.info(f"🔍 Multi-hop search: query='{query}', hops={hops}, max_nodes={max_nodes}, "
                    f"timeline={timeline}, prefer_recent={prefer_recent}")

        # Collect seed entities from initial results for novelty check
        seed_entities: Set[str] = set()
        visited: Set[str] = set()
        paths: List[Dict[str, Any]] = []
        all_facts: List[Dict[str, Any]] = []

        # Hop 0: Initial semantic search
        initial_results = await self.knowledge_layer.search(query, limit=10)
        logger.info(f"  Hop 0: Found {len(initial_results)} initial nodes")

        # Collect seed document entities
        seed_doc_entities: Set[str] = set()
        if initial_results:
            top_doc = initial_results[0][0]
            seed_doc_entities = self._extract_entities_from_node(top_doc)
            logger.debug(f"  Seed doc entities: {len(seed_doc_entities)}")

        # Process initial nodes (hop 0)
        for node, similarity in initial_results:
            node_id = node.id
            if node_id in visited:
                continue
            visited.add(node_id)

            # Track entities from seed nodes
            node_entities = self._extract_entities_from_node(node)
            seed_entities.update(node_entities)

            fact = {
                'content': node.content,
                'node_id': node_id,
                'node_type': node.node_type.value,
                'hop': 0,
                'similarity': similarity,
                'path': [node_id],
                'path_content': [node.content],
                'relations': [],
                'metadata': node.metadata,
                'evidence_ids': [node_id],
                'novelty_score': 0.0,  # Seed nodes have no novelty
                'novelty_failed': False
            }
            all_facts.append(fact)

            # Create path for seed node
            paths.append({
                'nodes': [node_id],
                'node_types': [node.node_type.value],
                'relations': [],
                'score': similarity,
                'evidence_ids': [node_id],
                'hop': 0
            })

        # Multi-hop traversal with path tracking
        for hop in range(1, hops + 1):
            new_facts, new_paths = self._traverse_hop_with_paths(
                visited, hop, max_nodes, seed_entities, seed_doc_entities
            )
            all_facts.extend(new_facts)
            paths.extend(new_paths)
            logger.info(f"  Hop {hop}: Found {len(new_facts)} new nodes, {len(new_paths)} new paths")

            if len(visited) >= max_nodes:
                break

        # Deduplicate and rank
        all_facts = self._deduplicate_facts(all_facts)
        all_facts = self._rank_facts_with_novelty(all_facts)

        # Apply novelty filter
        novel_facts = []
        novelty_failed_count = 0
        for fact in all_facts[:max_nodes]:
            if fact.get('novelty_failed', False):
                novelty_failed_count += 1
            novel_facts.append(fact)

        # Apply time filters if specified
        if time_min or time_max:
            novel_facts = self._filter_by_time(novel_facts, time_min, time_max)

        # Apply recency weighting if prefer_recent
        if prefer_recent:
            novel_facts = self._apply_recency_boost(novel_facts)

        # Detect contradictions (returns contested, primary_paths, counter_paths, narratives)
        contested, primary_paths, counter_paths, narratives = self._detect_contradictions_with_narratives(novel_facts)

        # Generate timeline if requested
        timeline_points = []
        drift_events = []
        if timeline:
            timeline_points = self._generate_timeline(novel_facts, bucket, max_timeline_points)
            drift_events = self._detect_drift(novel_facts, bucket)

        # Generate summary with contradiction note if contested
        summary_text = self._generate_path_summary(primary_paths, query, contested, counter_paths)

        # Filter to top paths with RAM safety
        paths = paths[:max_nodes]
        primary_paths = primary_paths[:10]  # Hard limit
        counter_paths = counter_paths[:5]   # Hard limit

        logger.info(f"[GRAPH MULTIHOP] total_facts={len(all_facts)}, "
                    f"novel_facts={len(novel_facts)}, novelty_failed={novelty_failed_count}, "
                    f"paths={len(paths)}, contested={contested}, counter_paths={len(counter_paths)}, "
                    f"narratives={len(narratives)}, timeline_points={len(timeline_points)}, "
                    f"drift_events={len(drift_events)}")

        result = {
            'insights': primary_paths,
            'paths': paths,
            'summary_text': summary_text,
            'novelty_stats': {
                'total_facts': len(all_facts),
                'novel_facts': len(novel_facts),
                'novelty_failed': novelty_failed_count,
                'seed_entities': len(seed_entities)
            },
            'contested': contested,
            'counter_paths': counter_paths,
            'narratives': narratives
        }

        if timeline:
            result['timeline_points'] = timeline_points
            result['drift_events'] = drift_events

        return result

    def _run_async_safe(self, coro):
        """
        Safely run an async coroutine synchronously.

        Works both when no event loop is running and when a loop is already running.
        Uses shared thread pool for efficiency.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - use asyncio.run
            return asyncio.run(coro)

        # Loop is running - execute in shared thread pool
        fut = self._thread_pool.submit(asyncio.run, coro)
        return fut.result()

    def multi_hop_search_sync(
        self,
        query: str,
        hops: int = 2,
        max_nodes: int = 20,
        timeline: bool = False,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        prefer_recent: bool = True,
        bucket: str = "month",
        max_timeline_points: int = 12
    ) -> Dict[str, Any]:
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
        logger.info(f"🔍 Multi-hop search (sync): query='{query}', hops={hops}, max_nodes={max_nodes}, "
                    f"timeline={timeline}")

        # Collect seed entities from initial results for novelty check
        seed_entities: Set[str] = set()
        visited: Set[str] = set()
        paths: List[Dict[str, Any]] = []
        all_facts: List[Dict[str, Any]] = []

        # Use sync version of search if available, otherwise run async
        if hasattr(self.knowledge_layer, 'search_sync'):
            initial_results = self.knowledge_layer.search_sync(query, limit=10)
        else:
            # Fallback: safe async execution (works under running loop)
            initial_results = self._run_async_safe(
                self.knowledge_layer.search(query, limit=10)
            )

        logger.info(f"  Hop 0: Found {len(initial_results)} initial nodes")

        # Collect seed document entities
        seed_doc_entities: Set[str] = set()
        if initial_results:
            top_doc = initial_results[0][0]
            seed_doc_entities = self._extract_entities_from_node(top_doc)

        # Process initial nodes (hop 0)
        for node, similarity in initial_results:
            node_id = node.id
            if node_id in visited:
                continue
            visited.add(node_id)

            # Track entities from seed nodes
            node_entities = self._extract_entities_from_node(node)
            seed_entities.update(node_entities)

            fact = {
                'content': node.content,
                'node_id': node_id,
                'node_type': node.node_type.value,
                'hop': 0,
                'similarity': similarity,
                'path': [node_id],
                'path_content': [node.content],
                'relations': [],
                'metadata': node.metadata,
                'evidence_ids': [node_id],
                'novelty_score': 0.0,
                'novelty_failed': False
            }
            all_facts.append(fact)

            paths.append({
                'nodes': [node_id],
                'node_types': [node.node_type.value],
                'relations': [],
                'score': similarity,
                'evidence_ids': [node_id],
                'hop': 0
            })

        # Multi-hop traversal with path tracking
        for hop in range(1, hops + 1):
            new_facts, new_paths = self._traverse_hop_with_paths(
                visited, hop, max_nodes, seed_entities, seed_doc_entities
            )
            all_facts.extend(new_facts)
            paths.extend(new_paths)
            logger.info(f"  Hop {hop}: Found {len(new_facts)} new nodes, {len(new_paths)} new paths")

            if len(visited) >= max_nodes:
                break

        # Deduplicate and rank
        all_facts = self._deduplicate_facts(all_facts)
        all_facts = self._rank_facts_with_novelty(all_facts)

        # Apply novelty filter
        novel_facts = []
        novelty_failed_count = 0
        for fact in all_facts[:max_nodes]:
            if fact.get('novelty_failed', False):
                novelty_failed_count += 1
            novel_facts.append(fact)

        # Apply time filters if specified
        if time_min or time_max:
            novel_facts = self._filter_by_time(novel_facts, time_min, time_max)

        # Apply recency weighting if prefer_recent
        if prefer_recent:
            novel_facts = self._apply_recency_boost(novel_facts)

        # Detect contradictions with narratives
        contested, primary_paths, counter_paths, narratives = self._detect_contradictions_with_narratives(novel_facts)

        # Generate timeline if requested
        timeline_points = []
        drift_events = []
        if timeline:
            timeline_points = self._generate_timeline(novel_facts, bucket, max_timeline_points)
            drift_events = self._detect_drift(novel_facts, bucket)

        # Generate summary with contradiction note if contested
        summary_text = self._generate_path_summary(primary_paths, query, contested, counter_paths)

        # Filter to top paths with RAM safety
        paths = paths[:max_nodes]
        primary_paths = primary_paths[:10]
        counter_paths = counter_paths[:5]

        logger.info(f"[GRAPH MULTIHOP] total_facts={len(all_facts)}, "
                    f"novel_facts={len(novel_facts)}, novelty_failed={novelty_failed_count}, "
                    f"contested={contested}, counter_paths={len(counter_paths)}, "
                    f"narratives={len(narratives)}, timeline_points={len(timeline_points)}")

        result = {
            'insights': primary_paths,
            'paths': paths,
            'summary_text': summary_text,
            'novelty_stats': {
                'total_facts': len(all_facts),
                'novel_facts': len(novel_facts),
                'novelty_failed': novelty_failed_count,
                'seed_entities': len(seed_entities)
            },
            'contested': contested,
            'counter_paths': counter_paths,
            'narratives': narratives
        }

        if timeline:
            result['timeline_points'] = timeline_points
            result['drift_events'] = drift_events

        return result

    def _traverse_hop(
        self,
        visited: Set[str],
        hop: int,
        max_nodes: int,
        max_edges: int = 500
    ) -> List[Dict[str, Any]]:
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

        # Use deque with limited size for memory-efficient frontier
        frontier = deque(list(visited), maxlen=max_nodes * 2)

        for node_id in frontier:
            if edges_traversed >= max_edges or len(visited) >= max_nodes:
                break

            # Use sync version for sync traversal
            related = self.knowledge_layer.get_related_sync(node_id, max_depth=1)
            edges = related.get('edges', [])

            for edge in edges:
                if edges_traversed >= max_edges:
                    break
                edges_traversed += 1

                # Get the related node ID from edge
                related_id = edge.target_id if edge.source_id == node_id else edge.source_id

                if related_id in visited:
                    continue

                related_node = related.get('nodes', {}).get(related_id)
                if not related_node:
                    continue

                visited.add(related_id)

                # Early stop if max nodes reached
                if len(visited) > max_nodes:
                    break

                source_node = self.knowledge_layer._backend.get_node(node_id)
                if source_node:
                    path = [source_node.content, related_node.content]
                else:
                    path = [related_node.content]

                fact = {
                    'content': related_node.content,
                    'node_type': related_node.node_type.value,
                    'hop': hop,
                    'similarity': 1.0 - (hop * 0.2),
                    'path': path,
                    'metadata': related_node.metadata
                }
                new_facts.append(fact)

        return new_facts

    def _deduplicate_facts(self, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            content = fact['content'].lower().strip()
            if content not in seen:
                seen.add(content)
                unique_facts.append(fact)

        logger.debug(f"Deduplicated: {len(facts)} -> {len(unique_facts)} facts")
        return unique_facts

    def _rank_facts(self, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Rank facts by relevance (similarity, hop distance, type).

        Args:
            facts: List of facts to rank

        Returns:
            Ranked list of facts
        """
        def calculate_score(fact: Dict[str, Any]) -> float:
            similarity = fact.get('similarity', 0.5)
            hop = fact.get('hop', 0)

            node_type = fact.get('node_type', 'fact')
            type_bonus = {
                'fact': 1.0,
                'entity': 0.9,
                'concept': 0.8,
                'event': 0.7,
                'url': 0.5,
                'document': 0.6
            }.get(node_type, 0.5)

            hop_penalty = max(0, 1.0 - (hop * 0.15))

            score = similarity * type_bonus * hop_penalty
            return score

        ranked_facts = sorted(facts, key=calculate_score, reverse=True)
        return ranked_facts

    def ask_with_reasoning(
        self,
        question: str,
        hops: int = 2,
        max_nodes: int = 20
    ) -> Dict[str, Any]:
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
        facts = result.get('insights', [])
        paths = result.get('paths', [])

        reasoning_paths = []
        for fact in facts:
            if 'path_content' in fact and len(fact['path_content']) > 1:
                path_str = ' -> '.join(fact['path_content'])
                reasoning_paths.append({
                    'path': path_str,
                    'hop': fact.get('hop', 1),
                    'content': fact.get('content', ''),
                    'novelty_score': fact.get('novelty_score', 0.0),
                    'novelty_failed': fact.get('novelty_failed', False)
                })

        output = {
            'question': question,
            'facts': facts,
            'reasoning_paths': reasoning_paths,
            'graph_paths': paths,
            'summary': result.get('summary_text', ''),
            'novelty_stats': result.get('novelty_stats', {}),
            'fact_count': len(facts),
            'path_count': len(reasoning_paths)
        }

        logger.info(f"🧠 Reasoning complete: {len(facts)} facts, {len(reasoning_paths)} paths")
        return output

    def find_connections(
        self,
        entity1: str,
        entity2: str,
        max_hops: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Find connection paths between two entities.

        Args:
            entity1: First entity name
            entity2: Second entity name
            max_hops: Maximum hops to search

        Returns:
            List of connection paths
        """
        import hashlib

        def get_entity_id(name: str) -> str:
            return hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]

        entity1_id = get_entity_id(entity1)
        entity2_id = get_entity_id(entity2)

        paths = []
        self._find_paths_bfs(entity1_id, entity2_id, max_hops, [], set(), paths)

        logger.info(f"Found {len(paths)} paths between '{entity1}' and '{entity2}'")
        return paths

    def _find_paths_bfs(
        self,
        start_id: str,
        target_id: str,
        max_hops: int,
        current_path: List[str],
        visited: Set[str],
        paths: List[Dict[str, Any]]
    ):
        """
        BFS to find paths between nodes.

        Args:
            start_id: Starting node ID
            target_id: Target node ID
            max_hops: Maximum hops remaining
            current_path: Current path being built
            visited: Set of visited node IDs
            paths: List to store found paths
        """
        if len(current_path) > max_hops:
            return

        current_path.append(start_id)
        visited.add(start_id)

        if start_id == target_id:
            node_contents = []
            for node_id in current_path:
                node = self.knowledge_layer._backend.get_node(node_id)
                if node:
                    node_contents.append(node.content)

            paths.append({
                'path': ' -> '.join(node_contents),
                'length': len(current_path) - 1
            })
        else:
            related = self.knowledge_layer.get_related(start_id, max_depth=1)
            for related_id, related_node in related.get('nodes', {}).items():
                if related_id not in visited:
                    self._find_paths_bfs(
                        related_id,
                        target_id,
                        max_hops,
                        current_path.copy(),
                        visited.copy(),
                        paths
                    )

        current_path.pop()
        visited.discard(start_id)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get GraphRAG orchestrator statistics.

        Returns:
            Dictionary with statistics
        """
        stats = self.knowledge_layer.get_statistics()
        stats['graph_rag_initialized'] = True
        return stats

    def shutdown(self) -> None:
        """Gracefully shutdown the orchestrator and release resources."""
        if hasattr(self, '_thread_pool'):
            try:
                self._thread_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    # =============================================================================
    # NETWORK ANALYSIS METHODS (from evidence_network_analyzer.py comments)
    # =============================================================================

    def calculate_centrality(
        self,
        node_ids: Optional[List[str]] = None,
        top_k: int = 10
    ) -> List[CentralityScores]:
        """
        Calculate centrality measures for nodes in the graph.
        
        From evidence_network_analyzer.py comments:
        "Step 3: Perform centrality analysis"
        - Degree centrality
        - Betweenness centrality
        - Closeness centrality
        - Eigenvector centrality
        - PageRank centrality
        
        Args:
            node_ids: Specific nodes to analyze (None = all)
            top_k: Return top K most central nodes
            
        Returns:
            List of CentralityScores sorted by overall influence
        """
        if node_ids is None:
            # Get all nodes from knowledge layer
            stats = self.knowledge_layer.get_statistics()
            node_ids = self._get_all_node_ids()
        
        if not node_ids:
            return []
        
        # Build adjacency list
        adjacency = self._build_adjacency_list(node_ids)
        
        centrality_scores = []
        
        for node_id in node_ids:
            scores = CentralityScores(node_id=node_id)
            
            # Degree centrality (normalized)
            if node_id in adjacency:
                scores.degree = len(adjacency[node_id]) / max(len(node_ids) - 1, 1)
            
            # Betweenness centrality (simplified approximation)
            scores.betweenness = self._calculate_betweenness(node_id, adjacency, node_ids)
            
            # Closeness centrality
            scores.closeness = self._calculate_closeness(node_id, adjacency, node_ids)
            
            # Eigenvector centrality (simplified)
            scores.eigenvector = self._calculate_eigenvector(node_id, adjacency, node_ids)
            
            # PageRank (simplified)
            scores.pagerank = self._calculate_pagerank(node_id, adjacency, node_ids)
            
            # Overall influence score (weighted average)
            scores.overall_influence = (
                scores.degree * 0.15 +
                scores.betweenness * 0.25 +
                scores.closeness * 0.20 +
                scores.eigenvector * 0.20 +
                scores.pagerank * 0.20
            )
            
            centrality_scores.append(scores)
        
        # Sort by overall influence
        centrality_scores.sort(key=lambda x: x.overall_influence, reverse=True)
        
        logger.info(f"Calculated centrality for {len(centrality_scores)} nodes")
        return centrality_scores[:top_k]

    def detect_communities(
        self,
        num_communities: int = 3
    ) -> List[Community]:
        """
        Detect communities in the knowledge graph.
        
        From evidence_network_analyzer.py comments:
        "Step 4: Detect communities in the network"
        "Use community detection algorithms"
        "Louvain community detection"
        
        Args:
            num_communities: Target number of communities
            
        Returns:
            List of detected communities
        """
        node_ids = self._get_all_node_ids()
        if len(node_ids) < 3:
            return []
        
        adjacency = self._build_adjacency_list(node_ids)
        
        # Simple label propagation for community detection
        communities = self._label_propagation(adjacency, node_ids, num_communities)
        
        # Enrich community data
        enriched_communities = []
        for comm_id, node_list in communities.items():
            community = Community(
                community_id=comm_id,
                nodes=node_list
            )
            
            # Calculate cohesion
            community.cohesion_score = self._calculate_community_cohesion(
                node_list, adjacency
            )
            
            # Determine dominant type
            type_counts = {}
            for node_id in node_list:
                node = self.knowledge_layer._backend.get_node(node_id)
                if node:
                    node_type = node.node_type.value
                    type_counts[node_type] = type_counts.get(node_type, 0) + 1
            
            if type_counts:
                community.dominant_type = max(type_counts, key=type_counts.get)
            
            # Identify key characteristics
            community.key_characteristics = self._extract_community_characteristics(
                node_list
            )
            
            enriched_communities.append(community)
        
        # Sort by cohesion
        enriched_communities.sort(key=lambda x: x.cohesion_score, reverse=True)
        
        logger.info(f"Detected {len(enriched_communities)} communities")
        return enriched_communities

    def find_contradictions(
        self,
        confidence_threshold: float = 0.7
    ) -> List[GraphContradiction]:
        """
        Find contradictions between nodes in the graph.
        
        From evidence_network_analyzer.py comments:
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
        
        # Get all edges and check for contradictions
        checked_pairs = set()
        
        for node_id in node_ids:
            node = self.knowledge_layer._backend.get_node(node_id)
            if not node:
                continue
            
            related = self.knowledge_layer.get_related(node_id, max_depth=1)
            
            for related_id, related_node in related.get('nodes', {}).items():
                pair_key = tuple(sorted([node_id, related_id]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                
                # Check for contradiction
                contradiction = self._analyze_contradiction(node, related_node)
                if contradiction and contradiction.severity >= confidence_threshold:
                    contradictions.append(contradiction)
        
        # Sort by severity
        contradictions.sort(key=lambda x: x.severity, reverse=True)
        
        logger.info(f"Found {len(contradictions)} contradictions")
        return contradictions

    def analyze_key_paths(
        self,
        start_node_id: str,
        target_node_id: str,
        max_hops: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Analyze key paths between two nodes.
        
        From evidence_network_analyzer.py comments:
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
        paths = self.find_connections(
            self._get_node_content(start_node_id) or start_node_id,
            self._get_node_content(target_node_id) or target_node_id,
            max_hops=max_hops
        )
        
        # Add confidence scores to paths
        for path in paths:
            path_length = path.get('length', 0)
            # Shorter paths have higher confidence
            path['confidence'] = max(0.3, 1.0 - (path_length * 0.2))
            path['is_key_path'] = path_length <= 2
        
        # Sort by confidence
        paths.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        
        return paths

    def calculate_network_metrics(self) -> Dict[str, Any]:
        """
        Calculate comprehensive network metrics.
        
        From evidence_network_analyzer.py comments:
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
        
        # Calculate density
        max_edges = (num_nodes * (num_nodes - 1)) // 2
        density = num_edges / max_edges if max_edges > 0 else 0
        
        # Calculate average degree
        avg_degree = (2 * num_edges) / num_nodes if num_nodes > 0 else 0
        
        # Clustering coefficient (simplified)
        clustering = self._calculate_clustering_coefficient(adjacency, node_ids)
        
        # Path metrics (average shortest path)
        avg_path_length = self._calculate_average_path_length(adjacency, node_ids)
        
        metrics = {
            'num_nodes': num_nodes,
            'num_edges': num_edges,
            'density': density,
            'average_degree': avg_degree,
            'clustering_coefficient': clustering,
            'average_path_length': avg_path_length,
            'connectivity': 'high' if density > 0.3 else 'medium' if density > 0.1 else 'low'
        }
        
        logger.info(f"Network metrics: {metrics}")
        return metrics

    # =============================================================================
    # HELPER METHODS FOR NETWORK ANALYSIS
    # =============================================================================

    def _get_all_node_ids(self) -> List[str]:
        """Get all node IDs from knowledge layer."""
        # Query the backend directly for all node IDs
        return self.knowledge_layer._backend.get_all_node_ids()

    def _build_adjacency_list(self, node_ids: List[str]) -> Dict[str, Set[str]]:
        """Build adjacency list for graph analysis."""
        adjacency = {node_id: set() for node_id in node_ids}
        
        for node_id in node_ids:
            related = self.knowledge_layer.get_related(node_id, max_depth=1)
            for related_id in related.get('nodes', {}).keys():
                if related_id in adjacency:
                    adjacency[node_id].add(related_id)
        
        return adjacency

    def _get_node_content(self, node_id: str) -> Optional[str]:
        """Get node content by ID."""
        node = self.knowledge_layer._backend.get_node(node_id)
        return node.content if node else None

    def _calculate_betweenness(
        self,
        node_id: str,
        adjacency: Dict[str, Set[str]],
        all_nodes: List[str]
    ) -> float:
        """Calculate betweenness centrality (simplified)."""
        if len(all_nodes) < 3:
            return 0.0
        
        # Count how many shortest paths go through this node
        betweenness_count = 0
        total_paths = 0
        
        for source in all_nodes:
            for target in all_nodes:
                if source != target and source != node_id and target != node_id:
                    # Simple path counting (not true shortest paths)
                    paths_through = self._count_paths_through(source, target, node_id, adjacency)
                    total_paths_through = self._count_all_paths(source, target, adjacency, max_depth=3)
                    
                    if total_paths_through > 0:
                        betweenness_count += paths_through / total_paths_through
                        total_paths += 1
        
        return betweenness_count / max(total_paths, 1)

    def _count_paths_through(
        self,
        source: str,
        target: str,
        through: str,
        adjacency: Dict[str, Set[str]]
    ) -> int:
        """Count paths from source to target that go through 'through'."""
        count = 0
        visited = {source}
        
        def dfs(current: str, path: List[str]):
            nonlocal count
            if current == target and through in path:
                count += 1
                return
            if len(path) > 4:  # Limit depth
                return
            
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    dfs(neighbor, path + [neighbor])
                    visited.discard(neighbor)
        
        dfs(source, [source])
        return count

    def _count_all_paths(
        self,
        source: str,
        target: str,
        adjacency: Dict[str, Set[str]],
        max_depth: int = 3
    ) -> int:
        """Count all paths between two nodes."""
        count = 0
        visited = {source}
        
        def dfs(current: str, depth: int):
            nonlocal count
            if current == target:
                count += 1
                return
            if depth >= max_depth:
                return
            
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    dfs(neighbor, depth + 1)
                    visited.discard(neighbor)
        
        dfs(source, 0)
        return count

    def _calculate_closeness(
        self,
        node_id: str,
        adjacency: Dict[str, Set[str]],
        all_nodes: List[str]
    ) -> float:
        """Calculate closeness centrality."""
        distances = self._calculate_distances(node_id, adjacency, all_nodes)
        
        if not distances:
            return 0.0
        
        total_distance = sum(distances.values())
        n = len(all_nodes)
        
        if total_distance == 0 or n <= 1:
            return 0.0
        
        # Closeness = (n-1) / sum of distances
        return (n - 1) / total_distance

    def _calculate_distances(
        self,
        start: str,
        adjacency: Dict[str, Set[str]],
        all_nodes: List[str]
    ) -> Dict[str, int]:
        """Calculate shortest distances from start to all nodes using BFS."""
        distances = {start: 0}
        queue = [start]
        visited = {start}
        
        while queue:
            current = queue.pop(0)
            current_distance = distances[current]
            
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    distances[neighbor] = current_distance + 1
                    queue.append(neighbor)
        
        return distances

    def _calculate_eigenvector(
        self,
        node_id: str,
        adjacency: Dict[str, Set[str]],
        all_nodes: List[str],
        iterations: int = 10
    ) -> float:
        """Calculate eigenvector centrality (simplified power iteration)."""
        scores = {n: 1.0 for n in all_nodes}
        
        for _ in range(iterations):
            new_scores = {}
            for node in all_nodes:
                score = sum(scores.get(neighbor, 0) for neighbor in adjacency.get(node, set()))
                new_scores[node] = score
            
            # Normalize
            max_score = max(new_scores.values()) if new_scores else 1
            if max_score > 0:
                new_scores = {k: v / max_score for k, v in new_scores.items()}
            
            scores = new_scores
        
        return scores.get(node_id, 0.0)

    def _calculate_pagerank(
        self,
        node_id: str,
        adjacency: Dict[str, Set[str]],
        all_nodes: List[str],
        damping: float = 0.85,
        iterations: int = 10
    ) -> float:
        """Calculate PageRank (simplified)."""
        n = len(all_nodes)
        if n == 0:
            return 0.0
        
        scores = {node: 1.0 / n for node in all_nodes}
        
        for _ in range(iterations):
            new_scores = {}
            for node in all_nodes:
                rank = (1 - damping) / n
                
                # Add contribution from neighbors
                for neighbor in all_nodes:
                    if node in adjacency.get(neighbor, set()):
                        neighbor_out_degree = len(adjacency.get(neighbor, set()))
                        if neighbor_out_degree > 0:
                            rank += damping * scores[neighbor] / neighbor_out_degree
                
                new_scores[node] = rank
            
            scores = new_scores
        
        return scores.get(node_id, 0.0)

    def _label_propagation(
        self,
        adjacency: Dict[str, Set[str]],
        node_ids: List[str],
        num_communities: int
    ) -> Dict[int, List[str]]:
        """Simple label propagation for community detection."""
        # Initialize each node with its own label
        labels = {node: i for i, node in enumerate(node_ids)}
        
        # Propagate labels
        for _ in range(10):  # Iterations
            for node in node_ids:
                if not adjacency[node]:
                    continue
                
                # Count labels of neighbors
                label_counts = {}
                for neighbor in adjacency[node]:
                    label = labels[neighbor]
                    label_counts[label] = label_counts.get(label, 0) + 1
                
                # Assign most common label
                if label_counts:
                    labels[node] = max(label_counts, key=label_counts.get)
        
        # Group nodes by label
        communities: Dict[int, List[str]] = {}
        for node, label in labels.items():
            if label not in communities:
                communities[label] = []
            communities[label].append(node)
        
        # Limit to num_communities largest
        sorted_communities = sorted(
            communities.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        return {i: nodes for i, (_, nodes) in enumerate(sorted_communities[:num_communities])}

    def _calculate_community_cohesion(
        self,
        node_list: List[str],
        adjacency: Dict[str, Set[str]]
    ) -> float:
        """Calculate cohesion score for a community."""
        if len(node_list) < 2:
            return 1.0
        
        internal_edges = 0
        possible_edges = len(node_list) * (len(node_list) - 1)
        
        for node in node_list:
            for neighbor in adjacency.get(node, set()):
                if neighbor in node_list:
                    internal_edges += 1
        
        # Divide by 2 because each edge is counted twice
        internal_edges //= 2
        
        return internal_edges / possible_edges if possible_edges > 0 else 0.0

    def _extract_community_characteristics(self, node_list: List[str]) -> List[str]:
        """Extract key characteristics of a community."""
        characteristics = []
        
        # Count node types
        type_counts = {}
        for node_id in node_list:
            node = self.knowledge_layer._backend.get_node(node_id)
            if node:
                node_type = node.node_type.value
                type_counts[node_type] = type_counts.get(node_type, 0) + 1
        
        # Add characteristics based on dominant types
        if type_counts:
            dominant = max(type_counts, key=type_counts.get)
            characteristics.append(f"dominant_type:{dominant}")
            
            if len(type_counts) > 1:
                characteristics.append("mixed_types")
        
        characteristics.append(f"size:{len(node_list)}")
        
        return characteristics

    def _analyze_contradiction(
        self,
        node_a: KnowledgeNode,
        node_b: KnowledgeNode
    ) -> Optional[GraphContradiction]:
        """Analyze if two nodes contradict each other."""
        content_a = node_a.content.lower()
        content_b = node_b.content.lower()
        
        # Simple contradiction detection
        contradiction_indicators = [
            ('not ', ''),  # negation vs positive
            ('never ', 'always '),
            ('no ', 'yes '),
            ('false', 'true'),
            ('impossible', 'possible'),
        ]
        
        for neg_a, neg_b in contradiction_indicators:
            has_neg_a = neg_a in content_a if neg_a else neg_a not in content_a
            has_neg_b = neg_b in content_b if neg_b else neg_b not in content_b
            
            if has_neg_a and not has_neg_b:
                # Check if they talk about the same subject
                words_a = set(content_a.split())
                words_b = set(content_b.split())
                common_words = words_a & words_b
                
                if len(common_words) > 3:  # Significant overlap
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
                            "Consider scope differences"
                        ]
                    )
        
        return None

    def _calculate_clustering_coefficient(
        self,
        adjacency: Dict[str, Set[str]],
        node_ids: List[str]
    ) -> float:
        """Calculate average clustering coefficient."""
        coefficients = []
        
        for node in node_ids:
            neighbors = adjacency.get(node, set())
            if len(neighbors) < 2:
                continue
            
            # Count triangles
            triangles = 0
            for neighbor1 in neighbors:
                for neighbor2 in neighbors:
                    if neighbor1 != neighbor2 and neighbor2 in adjacency.get(neighbor1, set()):
                        triangles += 1
            
            # Each triangle counted twice
            triangles //= 2
            
            # Possible triangles
            possible = len(neighbors) * (len(neighbors) - 1) // 2
            
            if possible > 0:
                coefficients.append(triangles / possible)
        
        # Use numpy if available, otherwise pure Python fallback
        if NUMPY_AVAILABLE and coefficients:
            return float(np.mean(coefficients))
        return sum(coefficients) / len(coefficients) if coefficients else 0.0

    def _calculate_average_path_length(
        self,
        adjacency: Dict[str, Set[str]],
        node_ids: List[str]
    ) -> float:
        """Calculate average shortest path length."""
        path_lengths = []

        for source in node_ids:
            distances = self._calculate_distances(source, adjacency, node_ids)
            for target, distance in distances.items():
                if source != target:
                    path_lengths.append(distance)

        # Use numpy if available, otherwise pure Python fallback
        if NUMPY_AVAILABLE and path_lengths:
            return float(np.mean(path_lengths))
        return sum(path_lengths) / len(path_lengths) if path_lengths else 0.0

    # =============================================================================
    # PATH EVIDENCE AND NOVELTY FILTER METHODS
    # =============================================================================

    def _extract_entities_from_node(self, node: KnowledgeNode) -> Set[str]:
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

        # Add the node itself if it's an entity
        if node.node_type.value == 'entity':
            entities.add(node.content.lower().strip())

        # Simple pattern: capitalized words (potential proper nouns)
        capitalized = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', content)
        for entity in capitalized:
            entities.add(entity.lower().strip())

        # Extract from metadata if available
        if node.metadata:
            if 'entities' in node.metadata:
                for ent in node.metadata['entities']:
                    entities.add(str(ent).lower().strip())
            if 'title' in node.metadata:
                title_entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', node.metadata['title'])
                for ent in title_entities:
                    entities.add(ent.lower().strip())

        return entities

    def _traverse_hop_with_paths(
        self,
        visited: Set[str],
        hop: int,
        max_nodes: int,
        seed_entities: Set[str],
        seed_doc_entities: Set[str],
        max_edges: int = 500
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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

        # Build path context from visited nodes
        path_context: Dict[str, Tuple[List[str], List[str]]] = {}  # node_id -> (path_ids, path_content)

        # Initialize with hop 0 nodes
        for node_id in list(visited):
            node = self.knowledge_layer._backend.get_node(node_id)
            if node:
                path_context[node_id] = ([node_id], [node.content])

        # Use deque with limited size for memory-efficient frontier
        frontier = deque(list(visited), maxlen=max_nodes * 2)

        for node_id in frontier:
            if edges_traversed >= max_edges or len(visited) >= max_nodes:
                break

            # Use sync version for sync traversal
            related = self.knowledge_layer.get_related_sync(node_id, max_depth=1)
            edges = related.get('edges', [])

            for edge in edges:
                if edges_traversed >= max_edges:
                    break
                edges_traversed += 1

                # Get the related node ID from edge
                related_id = edge.target_id if edge.source_id == node_id else edge.source_id

                if related_id in visited:
                    continue

                related_node = related.get('nodes', {}).get(related_id)
                if not related_node:
                    continue

                visited.add(related_id)

                # Early stop if max nodes reached
                if len(visited) > max_nodes:
                    break

                # Build path
                source_path_ids, source_path_content = path_context.get(node_id, ([node_id], []))
                current_path_ids = source_path_ids + [related_id]
                current_path_content = source_path_content + [related_node.content]

                # Store path context for this node
                path_context[related_id] = (current_path_ids, current_path_content)

                # Calculate novelty score
                related_entities = self._extract_entities_from_node(related_node)
                novel_entities = related_entities - seed_doc_entities

                # Novelty rules:
                # 1. Contains at least 1 entity not in seed document -> novel
                # 2. Path length >= 2 with different relations -> novel
                novelty_score = len(novel_entities) / max(len(related_entities), 1) if related_entities else 0.0

                # Check novelty criteria
                has_new_entity = len(novel_entities) > 0
                has_multi_hop_path = len(current_path_ids) >= 2

                novelty_failed = not (has_new_entity or has_multi_hop_path)

                # Adjust score based on novelty
                if novelty_failed:
                    novelty_score = 0.0

                # Extract evidence_ids from edge metadata or fall back to node metadata
                edge_evidence_id = edge.metadata.get('evidence_id') if edge.metadata else None
                if edge_evidence_id:
                    path_evidence_ids = [edge_evidence_id]
                else:
                    # Fallback: use doc_id from node metadata
                    path_evidence_ids = [related_node.metadata.get('evidence_id', related_id)]

                fact = {
                    'content': related_node.content,
                    'node_id': related_id,
                    'node_type': related_node.node_type.value,
                    'hop': hop,
                    'similarity': 1.0 - (hop * 0.2),
                    'path': current_path_ids,
                    'path_content': current_path_content,
                    'relations': [edge.edge_type.value],
                    'metadata': related_node.metadata,
                    'evidence_ids': path_evidence_ids,
                    'novelty_score': novelty_score,
                    'novelty_failed': novelty_failed,
                    'novel_entities': list(novel_entities)[:5],  # Store for debugging
                    'edge_metadata': edge.metadata  # Include edge metadata for contradiction detection
                }
                new_facts.append(fact)

                # Create path entry with evidence_ids
                path_entry = {
                    'nodes': current_path_ids,
                    'node_types': [self._get_node_type(nid) for nid in current_path_ids],
                    'relations': [edge.edge_type.value],
                    'score': 1.0 - (hop * 0.2),
                    'evidence_ids': path_evidence_ids,
                    'hop': hop,
                    'novelty_failed': novelty_failed
                }
                new_paths.append(path_entry)

        return new_facts, new_paths

    def _get_node_type(self, node_id: str) -> str:
        """Get node type for a node ID."""
        node = self.knowledge_layer._backend.get_node(node_id)
        return node.node_type.value if node else 'unknown'

    def _rank_facts_with_novelty(self, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Rank facts considering novelty score.

        Args:
            facts: List of facts to rank

        Returns:
            Ranked list with novelty bonus
        """
        def calculate_score(fact: Dict[str, Any]) -> float:
            similarity = fact.get('similarity', 0.5)
            hop = fact.get('hop', 0)
            novelty = fact.get('novelty_score', 0.0)

            node_type = fact.get('node_type', 'fact')
            type_bonus = {
                'fact': 1.0,
                'entity': 0.9,
                'concept': 0.8,
                'event': 0.7,
                'url': 0.5,
                'document': 0.6
            }.get(node_type, 0.5)

            hop_penalty = max(0, 1.0 - (hop * 0.15))

            # Novelty bonus: up to 25% boost for highly novel facts
            novelty_bonus = 1.0 + (novelty * 0.25)

            score = similarity * type_bonus * hop_penalty * novelty_bonus
            return score

        ranked_facts = sorted(facts, key=calculate_score, reverse=True)
        return ranked_facts

    def _detect_contradictions(
        self,
        facts: List[Dict[str, Any]]
    ) -> Tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
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
        # Extract claims from facts: (subject, predicate, object, fact)
        claims: List[Tuple[str, str, str, Dict[str, Any]]] = []

        for fact in facts:
            content = fact.get('content', '').lower().strip()
            if not content:
                continue

            # Simple claim extraction: look for (entity, relation, value) patterns
            # Pattern: "X is Y", "X has Y", "X located_in Y", etc.
            # Note: 're' module already imported at file level

            # Common relation patterns
            relation_patterns = [
                r'(.+?)\s+is\s+(.+?)(?:\.|$)',
                r'(.+?)\s+has\s+(.+?)(?:\.|$)',
                r'(.+?)\s+located\s+in\s+(.+?)(?:\.|$)',
                r'(.+?)\s+was\s+(.+?)(?:\.|$)',
                r'(.+?)\s+has\s+a\s+(.+?)(?:\.|$)',
            ]

            for pattern in relation_patterns:
                match = re.search(pattern, content)
                if match:
                    subject = match.group(1).strip()
                    obj = match.group(2).strip()
                    # Determine predicate from pattern
                    if 'is' in pattern:
                        predicate = 'is'
                    elif 'located' in pattern:
                        predicate = 'located_in'
                    elif 'has a' in pattern:
                        predicate = 'has_a'
                    elif 'has' in pattern:
                        predicate = 'has'
                    elif 'was' in pattern:
                        predicate = 'was'
                    else:
                        predicate = 'related_to'

                    claims.append((subject, predicate, obj, fact))
                    break  # Only extract first claim per fact

        # Check for contradictions
        contradictions: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []

        # Group by (subject, predicate)
        claim_groups: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
        for subject, predicate, obj, fact in claims:
            key = (subject, predicate)
            if key not in claim_groups:
                claim_groups[key] = []
            claim_groups[key].append((obj, fact))

        # Find contradictions: same (subject, predicate) with different objects
        for (subject, predicate), obj_facts in claim_groups.items():
            if len(obj_facts) >= 2:
                # Check if objects are different (potential contradiction)
                objects = [obj for obj, _ in obj_facts]
                unique_objects = set(objects)

                if len(unique_objects) >= 2:
                    # Contradiction found!
                    # Select two most confident facts as primary and counter
                    sorted_facts = sorted(
                        obj_facts,
                        key=lambda x: x[1].get('similarity', 0.5),
                        reverse=True
                    )

                    primary_obj, primary_fact = sorted_facts[0]
                    counter_obj, counter_fact = sorted_facts[1]

                    contradictions.append((
                        primary_fact,
                        counter_fact,
                        f"{subject} {predicate} {primary_obj} vs {counter_obj}"
                    ))

        # Check for explicit negation contradictions
        negation_patterns = [
            ('is', 'is not'),
            ('has', 'has no'),
            ('can', 'cannot'),
            ('will', 'will not'),
        ]

        for fact_a in facts:
            content_a = fact_a.get('content', '').lower()
            for fact_b in facts:
                if fact_a is fact_b:
                    continue
                content_b = fact_b.get('content', '').lower()

                for pos, neg in negation_patterns:
                    # Check if A has positive and B has negative (or vice versa)
                    a_has_pos = f' {pos} ' in f' {content_a} '
                    b_has_neg = f' {neg} ' in f' {content_b} '
                    a_has_neg = f' {neg} ' in f' {content_a} '
                    b_has_pos = f' {pos} ' in f' {content_b} '

                    if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
                        # Check for significant overlap in other words
                        words_a = set(content_a.split()) - {pos, neg}
                        words_b = set(content_b.split()) - {pos, neg}
                        overlap = words_a & words_b

                        if len(overlap) >= 3:  # Significant overlap
                            contradictions.append((fact_a, fact_b, f"negation: {pos} vs {neg}"))
                            break

        if not contradictions:
            return False, facts, []

        # Build counter_paths from contradictions
        primary_paths = []
        counter_paths = []

        for primary_fact, counter_fact, reason in contradictions:
            if primary_fact not in primary_paths:
                primary_paths.append(primary_fact)
            counter_paths.append({
                **counter_fact,
                'contradiction_reason': reason,
                'contradicts': primary_fact.get('node_id')
            })

        # Add remaining non-contradicted facts to primary_paths
        for fact in facts:
            if fact not in primary_paths and not any(
                fact.get('node_id') == c.get('node_id') for c in counter_paths
            ):
                primary_paths.append(fact)

        logger.info(f"[CONTRADICTION] Found {len(contradictions)} contradictions: "
                   f"{[r for _, _, r in contradictions]}")

        return True, primary_paths, counter_paths

    def _generate_path_summary(
        self,
        facts: List[Dict[str, Any]],
        query: str,
        contested: bool = False,
        counter_paths: List[Dict[str, Any]] = None
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

        # Add contradiction warning if contested
        if contested and counter_paths:
            lines.append("⚠️  CONTRADICTORY EVIDENCE DETECTED:")
            lines.append("Multiple sources provide conflicting information:")
            for i, counter in enumerate(counter_paths[:2], 1):  # Show top 2 contradictions
                reason = counter.get('contradiction_reason', 'conflict')
                lines.append(f"  Variant {i}: {counter.get('content', '')[:80]}...")
            lines.append("")

        # Group by hop
        by_hop: Dict[int, List[Dict[str, Any]]] = {}
        for fact in facts:
            hop = fact.get('hop', 0)
            by_hop.setdefault(hop, []).append(fact)

        for hop in sorted(by_hop.keys()):
            hop_facts = by_hop[hop]
            if hop == 0:
                lines.append(f"Direct matches ({len(hop_facts)}):")
            else:
                lines.append(f"Hop {hop} connections ({len(hop_facts)}):")

            for fact in hop_facts[:3]:  # Top 3 per hop
                content = fact['content'][:100] + "..." if len(fact['content']) > 100 else fact['content']
                novelty_flag = " [NOVEL]" if fact.get('novelty_score', 0) > 0.3 else ""
                lines.append(f"  • {content}{novelty_flag}")

                # Show path if available
                if fact.get('path_content') and len(fact['path_content']) > 1:
                    path_str = " -> ".join([p[:30] + "..." if len(p) > 30 else p for p in fact['path_content']])
                    lines.append(f"    Path: {path_str}")

                # Show evidence_ids if available
                if fact.get('evidence_ids'):
                    evidence_str = ", ".join(fact['evidence_ids'][:2])  # Limit to 2
                    lines.append(f"    Evidence: {evidence_str}...")

            lines.append("")

        return "\n".join(lines)

    # =============================================================================
    # TEMPORAL ANALYSIS METHODS
    # =============================================================================

    def _filter_by_time(
        self,
        facts: List[Dict[str, Any]],
        time_min: Optional[str],
        time_max: Optional[str]
    ) -> List[Dict[str, Any]]:
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

        def get_timestamp(fact: Dict[str, Any]) -> Optional[datetime]:
            """Extract timestamp from fact metadata."""
            metadata = fact.get('metadata', {})
            # Try fetched_at first, then published_at
            ts_str = metadata.get('fetched_at') or metadata.get('published_at')
            if ts_str:
                try:
                    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
            return None

        filtered = []
        min_dt = datetime.fromisoformat(time_min.replace('Z', '+00:00')) if time_min else None
        max_dt = datetime.fromisoformat(time_max.replace('Z', '+00:00')) if time_max else None

        for fact in facts:
            ts = get_timestamp(fact)
            if ts is None:
                # Include facts without timestamps (conservative)
                filtered.append(fact)
                continue

            if min_dt and ts < min_dt:
                continue
            if max_dt and ts > max_dt:
                continue

            filtered.append(fact)

        return filtered

    def _apply_recency_boost(self, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Boost scores of more recent facts.

        Args:
            facts: List of facts to boost

        Returns:
            Facts with boosted scores
        """
        from datetime import datetime, timedelta

        def get_timestamp(fact: Dict[str, Any]) -> datetime:
            """Extract timestamp from fact metadata."""
            metadata = fact.get('metadata', {})
            ts_str = metadata.get('fetched_at') or metadata.get('published_at')
            if ts_str:
                try:
                    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
            # Default to very old date
            return datetime.min

        # Find newest timestamp
        newest = max((get_timestamp(f) for f in facts), default=datetime.min)
        if newest == datetime.min:
            return facts

        boosted = []
        for fact in facts:
            ts = get_timestamp(fact)
            age_days = (newest - ts).days if ts != datetime.min else 365

            # Recency boost: newer = higher boost (0-20%)
            # Facts from last 30 days get full boost
            recency_boost = max(0, 1.0 - (age_days / 30)) * 0.2

            fact_copy = fact.copy()
            fact_copy['similarity'] = fact.get('similarity', 0.5) * (1.0 + recency_boost)
            boosted.append(fact_copy)

        # Re-sort by boosted similarity
        boosted.sort(key=lambda x: x['similarity'], reverse=True)
        return boosted

    def _generate_timeline(
        self,
        facts: List[Dict[str, Any]],
        bucket: str,
        max_points: int
    ) -> List[Dict[str, Any]]:
        """
        Generate timeline points from facts.

        Args:
            facts: Facts with timestamps
            bucket: Time bucketing ("month" or "year")
            max_points: Maximum timeline points (hard limit: 12)

        Returns:
            List of timeline points
        """
        from datetime import datetime
        from collections import defaultdict

        max_points = min(max_points, 12)  # Hard limit

        def get_bucket_key(fact: Dict[str, Any]) -> Optional[str]:
            """Get time bucket key for fact."""
            metadata = fact.get('metadata', {})
            ts_str = metadata.get('fetched_at') or metadata.get('published_at')
            if not ts_str:
                return None
            try:
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                if bucket == "year":
                    return dt.strftime("%Y")
                else:
                    return dt.strftime("%Y-%m")
            except (ValueError, AttributeError):
                return None

        # Group facts by bucket
        bucket_facts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for fact in facts:
            key = get_bucket_key(fact)
            if key:
                bucket_facts[key].append(fact)

        # Sort buckets chronologically
        sorted_buckets = sorted(bucket_facts.keys())

        # Build timeline points
        timeline_points = []
        for bucket_key in sorted_buckets[:max_points]:
            facts_in_bucket = bucket_facts[bucket_key]

            # Get top paths in bucket (max 3)
            top_paths = sorted(
                facts_in_bucket,
                key=lambda x: x.get('similarity', 0),
                reverse=True
            )[:3]

            # Get key claims (max 5)
            key_claims = []
            for fact in facts_in_bucket[:5]:
                content = fact.get('content', '')
                # Truncate long claims
                key_claims.append(content[:100] + "..." if len(content) > 100 else content)

            # Collect evidence_ids (max 20)
            evidence_ids = set()
            for fact in facts_in_bucket:
                for eid in fact.get('evidence_ids', []):
                    evidence_ids.add(eid)
                if len(evidence_ids) >= 20:
                    break

            # Generate notes
            notes = f"{len(facts_in_bucket)} facts, {len(evidence_ids)} unique evidence sources"

            timeline_points.append({
                'bucket': bucket_key,
                'top_paths': [
                    {
                        'content': p.get('content', '')[:100],
                        'score': p.get('similarity', 0)
                    } for p in top_paths
                ],
                'key_claims': key_claims[:5],
                'evidence_ids': list(evidence_ids)[:20],
                'notes': notes
            })

        return timeline_points

    def _detect_drift(
        self,
        facts: List[Dict[str, Any]],
        bucket: str
    ) -> List[Dict[str, Any]]:
        """
        Detect drift events - when claims about same (subject, predicate) change over time.

        Args:
            facts: Facts to analyze
            bucket: Time bucketing for detecting change points

        Returns:
            List of drift events (max 10)
        """
        from datetime import datetime
        from collections import defaultdict

        # Extract claims with timestamps
        claims_with_ts = []
        for fact in facts:
            claim = self._extract_claim(fact.get('content', ''))
            if not claim:
                continue

            metadata = fact.get('metadata', {})
            ts_str = metadata.get('fetched_at') or metadata.get('published_at')
            if not ts_str:
                continue

            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                bucket_key = ts.strftime("%Y-%m") if bucket == "month" else ts.strftime("%Y")
                claims_with_ts.append((claim, bucket_key, fact))
            except (ValueError, AttributeError):
                continue

        # Group by (subject, predicate)
        claim_groups: Dict[tuple, List[tuple]] = defaultdict(list)
        for (subject, predicate, obj), bucket_key, fact in claims_with_ts:
            claim_groups[(subject, predicate)].append((obj, bucket_key, fact))

        # Detect drift: different objects for same (subject, predicate) in different buckets
        drift_events = []
        for (subject, predicate), obj_facts in claim_groups.items():
            if len(obj_facts) < 2:
                continue

            # Sort by bucket
            obj_facts.sort(key=lambda x: x[1])

            # Check for different objects
            prev_obj = obj_facts[0][0]
            prev_bucket = obj_facts[0][1]

            for obj, bucket_key, fact in obj_facts[1:]:
                if obj != prev_obj:
                    # Drift detected!
                    drift_events.append({
                        'subject': subject,
                        'predicate': predicate,
                        'before': prev_obj,
                        'after': obj,
                        'bucket_change': bucket_key,
                        'supporting_evidence_ids': fact.get('evidence_ids', [])[:10],
                        'confidence': fact.get('similarity', 0.5)
                    })
                    prev_obj = obj

                if len(drift_events) >= 10:  # Hard limit
                    break

            if len(drift_events) >= 10:
                break

        return drift_events

    def _extract_claim(self, content: str) -> Optional[tuple]:
        """
        Extract (subject, predicate, object) claim from content.

        Args:
            content: Text content to parse

        Returns:
            Tuple of (subject, predicate, object) or None
        """
        # Note: 're' module already imported at file level

        content_lower = content.lower().strip()

        # Common relation patterns
        patterns = [
            (r'(.+?)\s+is\s+(.+?)(?:\.|$)', 'is'),
            (r'(.+?)\s+has\s+(.+?)(?:\.|$)', 'has'),
            (r'(.+?)\s+was\s+(.+?)(?:\.|$)', 'was'),
            (r'(.+?)\s+located\s+in\s+(.+?)(?:\.|$)', 'located_in'),
            (r'(.+?)\s+located\s+at\s+(.+?)(?:\.|$)', 'located_at'),
        ]

        for pattern, predicate in patterns:
            match = re.search(pattern, content_lower)
            if match:
                subject = match.group(1).strip()
                obj = match.group(2).strip()
                return (subject, predicate, obj)

        return None

    # =============================================================================
    # NARRATIVE ANALYSIS METHODS
    # =============================================================================

    def _detect_contradictions_with_narratives(
        self,
        facts: List[Dict[str, Any]]
    ) -> tuple:
        """
        Detect contradictions and generate competing narratives with confidence.

        Args:
            facts: Facts to analyze

        Returns:
            Tuple of (contested, primary_paths, counter_paths, narratives)
        """
        # Use existing contradiction detection
        contested, primary_paths, counter_paths = self._detect_contradictions(facts)

        if not contested:
            return False, primary_paths, counter_paths, []

        # Build narratives from contradictory facts
        narratives = self._build_narratives(primary_paths, counter_paths)

        return contested, primary_paths[:10], counter_paths[:5], narratives[:3]

    def _build_narratives(
        self,
        primary_paths: List[Dict[str, Any]],
        counter_paths: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
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

        # Narrative A: Primary (majority/supporting) view
        primary_evidence = []
        primary_domains = set()
        for fact in primary_paths[:5]:
            primary_evidence.extend(fact.get('evidence_ids', []))
            url = fact.get('metadata', {}).get('url', '')
            if url:
                domain = url.split('/')[2] if '://' in url else url.split('/')[0]
                primary_domains.add(domain)

        primary_confidence = self._calculate_narrative_confidence(
            primary_paths[:5],
            primary_evidence,
            primary_domains
        )

        primary_summary = self._summarize_narrative(primary_paths[:3])

        narratives.append({
            'narrative_id': 'A',
            'summary': primary_summary,
            'support_paths': primary_paths[:5],
            'support_evidence_ids': list(set(primary_evidence))[:25],
            'confidence': primary_confidence,
            'notes': f"supported by {len(primary_domains)} unique source domains, "
                     f"{len(set(primary_evidence))} unique evidence items"
        })

        # Narrative B: Counter view (from counter_paths)
        if counter_paths:
            counter_evidence = []
            counter_domains = set()
            for fact in counter_paths[:5]:
                counter_evidence.extend(fact.get('evidence_ids', []))
                url = fact.get('metadata', {}).get('url', '')
                if url:
                    domain = url.split('/')[2] if '://' in url else url.split('/')[0]
                    counter_domains.add(domain)

            counter_confidence = self._calculate_narrative_confidence(
                counter_paths[:5],
                counter_evidence,
                counter_domains
            )

            counter_summary = self._summarize_narrative(counter_paths[:3])

            narratives.append({
                'narrative_id': 'B',
                'summary': counter_summary,
                'support_paths': counter_paths[:5],
                'support_evidence_ids': list(set(counter_evidence))[:25],
                'confidence': counter_confidence,
                'notes': f"alternative view supported by {len(counter_domains)} unique source domains"
            })

        return narratives

    def _calculate_narrative_confidence(
        self,
        paths: List[Dict[str, Any]],
        evidence_ids: List[str],
        domains: Set[str]
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

        # Base score from evidence count (diminishing returns)
        unique_evidence = len(set(evidence_ids))
        evidence_score = min(1.0, unique_evidence / 5) * 0.4

        # Domain diversity score
        domain_score = min(1.0, len(domains) / 3) * 0.3

        # Average similarity score
        avg_similarity = sum(p.get('similarity', 0.5) for p in paths) / len(paths)
        similarity_score = avg_similarity * 0.2

        # Echo penalty: check for duplicate content_hash
        content_hashes = set()
        echo_count = 0
        for p in paths:
            metadata = p.get('metadata', {})
            hash_ring = metadata.get('content_hash_ring', [])
            for h in hash_ring:
                if h in content_hashes:
                    echo_count += 1
                content_hashes.add(h)

        echo_penalty = min(0.2, echo_count * 0.05)

        confidence = evidence_score + domain_score + similarity_score - echo_penalty
        return max(0.0, min(1.0, confidence))

    def _summarize_narrative(self, paths: List[Dict[str, Any]]) -> str:
        """
        Generate 1-3 sentence summary of narrative.
        """
        if not paths:
            return "No clear narrative found."

        # Use first 2-3 facts to build summary
        contents = []
        for p in paths[:3]:
            content = p.get('content', '')
            if content:
                # Truncate to first sentence or 100 chars
                first_sentence = content.split('.')[0] + '.' if '.' in content else content[:100]
                contents.append(first_sentence)

        if len(contents) == 1:
            return contents[0]
        elif len(contents) == 2:
            return f"{contents[0]} Additionally, {contents[1].lower()}"
        else:
            return f"{contents[0]} {contents[1]} This view also suggests {contents[2].lower()}"

    async def multi_hop_search_streaming(
        self,
        query: str,
        hops: int = 2,
        max_nodes: int = 20
    ):
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
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)  # Backpressure limit

        # Start traversal worker
        worker_task = asyncio.create_task(
            self._traversal_worker(query, hops, max_nodes, queue)
        )

        try:
            while True:
                node = await queue.get()
                if node is None:  # Sentinel - traversal complete
                    break
                yield node
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    async def _traversal_worker(
        self,
        query: str,
        hops: int,
        max_nodes: int,
        queue: asyncio.Queue
    ):
        """
        Worker that performs graph traversal and pushes discovered nodes to queue.

        Args:
            query: Search query
            hops: Number of hops to traverse
            max_nodes: Maximum nodes to discover
            queue: Queue to push discovered nodes to
        """
        visited: Set[str] = set()
        seed_entities: Set[str] = set()

        try:
            # Hop 0: Initial semantic search
            initial_results = await self.knowledge_layer.search(query, limit=10)

            # Process initial nodes (hop 0)
            for node, similarity in initial_results:
                node_id = node.id
                if node_id in visited:
                    continue
                if len(visited) >= max_nodes:
                    break

                visited.add(node_id)

                # Extract entities
                node_entities = self._extract_entities_from_node(node)
                seed_entities.update(node_entities)

                # Push to queue
                node_data = {
                    'content': node.content,
                    'node_id': node_id,
                    'node_type': node.node_type.value,
                    'hop': 0,
                    'similarity': similarity,
                    'path': [node_id],
                    'relations': [],
                    'metadata': node.metadata,
                }
                await queue.put(node_data)

            # Multi-hop traversal
            for hop in range(1, hops + 1):
                if len(visited) >= max_nodes:
                    break

                new_facts = self._traverse_hop_with_paths(
                    visited, hop, max_nodes, seed_entities, set()
                )[0]  # Only facts, not paths

                for fact in new_facts:
                    if len(visited) >= max_nodes:
                        break
                    await queue.put(fact)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Traversal worker error: {e}")
        finally:
            await queue.put(None)  # Sentinel to signal completion

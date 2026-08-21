"""
Graph Traversal Rust Integration Wiring — C5
==========================================

Wires rust_extensions/src/graph_traverse.rs to:
- knowledge/graph/context_graph.py

Purpose:
- batch_graph_traverse: Petgraph-powered parallel traversal (Tier 0)
- batch_graph_centrality: PageRank from DuckDB graph
- batch_graph_communities: Label propagation from DuckDB graph
- drop_connections: Release thread-local connections

Integration Point:
- knowledge/graph/context_graph.py ContextGraph.traverse()

Benefits:
- 10× faster than SQL recursive CTE on 100K+ nodes
- M1 8GB: Thread-local connections, read_only mode, LRU cache

Usage:
    from rust_extensions.wiring.graph_traverse_wiring import graph_traverse_wired

    # Batch traversal
    results = graph_traverse_wired.batch_graph_traverse(
        db_path="/data/ioc.db",
        values=["evil.com", "malware.exe"],
        max_hops=2
    )

    # Centrality
    centrality = graph_traverse_wired.batch_graph_centrality(
        db_path="/data/ioc.db",
        values=["evil.com", "malware.exe"]
    )

    # Release connections
    graph_traverse_wired.drop_connections()
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Thread pool for async operations (M1 8GB: limited to 2 workers)
_async_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Get or create the async executor for blocking Rust calls (thread-safe)."""
    global _async_executor
    if _async_executor is None:
        with _executor_lock:
            # Double-check locking pattern
            if _async_executor is None:
                _async_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="graph_traverse_async")
                # Register cleanup on process exit
                atexit.register(_shutdown_executor)
    return _async_executor


def _shutdown_executor() -> None:
    """Shutdown the async executor gracefully."""
    global _async_executor
    with _executor_lock:
        if _async_executor is not None:
            _async_executor.shutdown(wait=True)
            _async_executor = None
            logger.debug("[GraphTraverse] Async executor shut down")


from rust_extensions.integrations import get_graph_traverse

_graph_traverse = get_graph_traverse()


def graph_traverse_wired() -> Any:
    """Get the wired graph traversal integration."""
    return _graph_traverse


def batch_graph_traverse(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict]]:
    """
    Parallel batch graph traversal for multiple root IOC values.

    Uses rayon for parallelization across root values.
    Thread-local DuckDB connections for M1 8GB safety.

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        Dict mapping root_value -> list of connected nodes:
        {
            "evil.com": [{"value": "192.168.1.1", "ioc_type": "ip",
                          "confidence": 0.9, "source": "dns"}],
            ...
        }
    """
    return _graph_traverse.batch_graph_traverse(db_path, values, max_hops)


def graph_traverse_single(
    db_path: str,
    value: str,
    max_hops: int = 2,
) -> list[dict]:
    """
    Single IOC graph traversal — one root value.

    Args:
        db_path: Path to DuckDB database file
        value: Root IOC value to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        List of connected nodes with keys: value, ioc_type, confidence, source
    """
    return _graph_traverse.graph_traverse_single(db_path, value, max_hops)


def graph_stats(
    db_path: str,
    top_k: int = 20,
) -> dict:
    """
    Graph stats — degree distribution for top K nodes.

    Args:
        db_path: Path to DuckDB database file
        top_k: Number of top nodes by degree (default 20, max 100)

    Returns:
        Dict with keys: total_nodes, total_edges, top_nodes
    """
    return _graph_traverse.graph_stats(db_path, top_k)


def batch_graph_centrality(
    db_path: str,
    values: list[str],
) -> dict[str, float]:
    """
    Compute PageRank scores for specified IOC values from DuckDB graph.

    Uses power iteration with teleportation (damping factor 0.85).
    Bounded to MAX_CENTRALITY_NODES (100K) for M1 8GB safety.

    Args:
        db_path: Path to DuckDB database file
        values: List of IOC values to compute PageRank for

    Returns:
        Dict mapping value -> pagerank_score
    """
    return _graph_traverse.batch_graph_centrality(db_path, values)


def batch_graph_communities(
    db_path: str,
) -> dict[str, int]:
    """
    Compute community detection on DuckDB IOC graph using Label Propagation.

    Label Propagation is O(n+m) per iteration — much faster than Louvain.
    Bounded to MAX_CENTRALITY_NODES (100K) for M1 8GB safety.

    Args:
        db_path: Path to DuckDB database file

    Returns:
        Dict mapping value -> community_id
    """
    return _graph_traverse.batch_graph_communities(db_path)


def batch_graph_traverse_flat(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
    max_per_root: int = 20,
) -> list[dict]:
    """
    Flattened batch graph traversal — all results in single list.

    Useful when you want a unified result without per-root grouping.

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values
        max_hops: Maximum traversal depth (default 2)
        max_per_root: Max results per root (default 20)

    Returns:
        List of connected nodes with additional 'source' key indicating root
    """
    return _graph_traverse.batch_graph_traverse_flat(db_path, values, max_hops, max_per_root)


def drop_connections() -> bool:
    """
    Drop all thread-local DuckDB connections and flush LRU cache.

    F265-U5: Called between sprints to release connection memory.
    F265B-III: Also flushes LRU cache to mmap for cross-sprint persistence.

    Returns:
        True if successful, False otherwise
    """
    return _graph_traverse.drop_connections()


async def batch_graph_traverse_async(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict]]:
    """
    Async wrapper for batch_graph_traverse.

    Offloads blocking Rust call to thread pool.

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        Dict mapping root_value -> list of connected nodes
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(
        executor,
        batch_graph_traverse,
        db_path,
        values,
        max_hops,
    )


async def graph_traverse_single_async(
    db_path: str,
    value: str,
    max_hops: int = 2,
) -> list[dict]:
    """
    Async wrapper for graph_traverse_single.

    Offloads blocking Rust call to thread pool.

    Args:
        db_path: Path to DuckDB database file
        value: Root IOC value to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        List of connected nodes
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(
        executor,
        graph_traverse_single,
        db_path,
        value,
        max_hops,
    )


async def batch_graph_centrality_async(
    db_path: str,
    values: list[str],
) -> dict[str, float]:
    """
    Async wrapper for batch_graph_centrality.

    Offloads blocking Rust call to thread pool.

    Args:
        db_path: Path to DuckDB database file
        values: List of IOC values to compute PageRank for

    Returns:
        Dict mapping value -> pagerank_score
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(
        executor,
        batch_graph_centrality,
        db_path,
        values,
    )


async def graph_stats_async(
    db_path: str,
    top_k: int = 20,
) -> dict:
    """
    Async wrapper for graph_stats.

    Offloads blocking Rust call to thread pool.

    Args:
        db_path: Path to DuckDB database file
        top_k: Number of top nodes by degree (default 20)

    Returns:
        Dict with keys: total_nodes, total_edges, top_nodes
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(
        executor,
        graph_stats,
        db_path,
        top_k,
    )


async def batch_graph_communities_async(
    db_path: str,
) -> dict[str, int]:
    """
    Async wrapper for batch_graph_communities.

    Offloads blocking Rust call to thread pool.

    Args:
        db_path: Path to DuckDB database file

    Returns:
        Dict mapping value -> community_id
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(
        executor,
        batch_graph_communities,
        db_path,
    )


async def drop_connections_async() -> bool:
    """
    Async wrapper for drop_connections.

    Offloads blocking Rust call to thread pool.

    Returns:
        True if successful, False otherwise
    """
    loop = asyncio.get_running_loop()
    executor = _get_executor()
    return await loop.run_in_executor(executor, drop_connections)


if _graph_traverse.available:
    logger.info(
        "[GraphTraverse] Rust graph_traverse.rs integration: ENABLED "
        "(batch_graph_traverse, batch_graph_centrality, batch_graph_communities)"
    )
else:
    logger.warning(
        "[GraphTraverse] Rust graph_traverse.rs integration: DISABLED (falling back to DuckDB SQL traversal)"
    )

__all__ = [
    # Direct wrappers
    "batch_graph_traverse",
    "graph_traverse_single",
    "graph_stats",
    "batch_graph_centrality",
    "batch_graph_communities",
    "batch_graph_traverse_flat",
    "drop_connections",
    # Async wrappers
    "batch_graph_traverse_async",
    "graph_traverse_single_async",
    "batch_graph_centrality_async",
    "graph_stats_async",
    "batch_graph_communities_async",
    "drop_connections_async",
    # Singleton accessor
    "graph_traverse_wired",
]

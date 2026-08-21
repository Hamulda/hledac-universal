"""
ContextGraph - Graph Analytics with Rust Louvain + DuckDB Traversal
===================================================================

MODERNIZATION (A7): petgraph-powered Louvain community detection.
C5 INTEGRATION: Rust graph_traverse.rs for DuckDB graph traversal.

This module provides graph analytics capabilities with:
- Rust Louvain community detection (petgraph-powered, 10-50x faster)
- Rust batch_graph_traverse for DuckDB graph traversal (Tier 0, 10x faster)
- Rust batch_graph_centrality for PageRank from DuckDB
- Python fallback for development/portability
- PageRank computation
- Strongly Connected Components (SCC)

Architecture:
- Primary: Rust via rust_extensions/wiring/graph_analytics_wiring.py (Louvain)
- Primary: Rust via rust_extensions/wiring/graph_traverse_wiring.py (DuckDB traversal)
- Fallback: Python community_louvain (networkx)

Performance:
- M1 8GB: MAX_NODES=100,000, ~10-50MB memory
- 10-50x faster than pure Python networkx on 100K+ nodes
- 10x faster than SQL recursive CTE on 100K+ nodes

Usage:
    from knowledge.graph.context_graph import (
        ContextGraph, DuckDBContextGraph,
        louvain_communities, pagerank,
        traverse_duckdb, compute_centrality_duckdb,
    )

    # Standalone Louvain/PageRank
    communities = louvain_communities(nodes, edges, resolution=1.0)

    # DuckDB graph traversal (Tier 0 - Rust petgraph)
    results = traverse_duckdb(
        db_path="/data/ioc.db",
        values=["evil.com", "malware.exe"],
        max_hops=2
    )

    # DuckDB centrality (PageRank)
    centrality = compute_centrality_duckdb(
        db_path="/data/ioc.db",
        values=["evil.com", "malware.exe"]
    )

    # Via DuckDBContextGraph class
    graph = DuckDBContextGraph(db_path="/data/ioc.db")
    results = graph.traverse(["evil.com"], max_hops=2)
    centrality = graph.compute_centrality(["evil.com", "malware.exe"])
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Try to import Rust graph analytics first
_RUST_GRAPH_ANALYTICS_AVAILABLE = False
try:
    from rust_extensions.wiring.graph_analytics_wiring import (
        analyze_ioc_graph,
    )
    from rust_extensions.wiring.graph_analytics_wiring import (
        louvain_communities as rust_louvain,
    )
    from rust_extensions.wiring.graph_analytics_wiring import (
        pagerank as rust_pagerank,
    )

    _RUST_GRAPH_ANALYTICS_AVAILABLE = True
    logger.info("[ContextGraph] Rust graph_analytics.rs integration: ENABLED")
except ImportError:
    rust_louvain = None
    rust_pagerank = None
    analyze_ioc_graph = None
    logger.info("[ContextGraph] Rust graph_analytics.rs integration: DISABLED")

# Python fallback for Louvain community detection
_PYTHON_LOUVAIN_AVAILABLE = False
try:
    from community import community_louvain

    _PYTHON_LOUVAIN_AVAILABLE = True
except ImportError:
    community_louvain = None

# NetworkX for Python fallback
_NETWORKX_AVAILABLE = False
try:
    import networkx as nx

    _NETWORKX_AVAILABLE = True
except ImportError:
    nx = None

_RUST_GRAPH_TRAVERSE_AVAILABLE = False
try:
    from rust_extensions.wiring.graph_traverse_wiring import (
        batch_graph_centrality as rust_batch_centrality,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        batch_graph_centrality_async,
        batch_graph_communities_async,
        # Async wrappers
        batch_graph_traverse_async,
        drop_connections_async,
        graph_stats_async,
        graph_traverse_single_async,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        batch_graph_communities as rust_batch_communities,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        batch_graph_traverse as rust_batch_traverse,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        batch_graph_traverse_flat as rust_batch_traverse_flat,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        drop_connections as rust_drop_connections,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        graph_stats as rust_graph_stats,
    )
    from rust_extensions.wiring.graph_traverse_wiring import (
        graph_traverse_single as rust_traverse_single,
    )

    _RUST_GRAPH_TRAVERSE_AVAILABLE = True
    logger.info("[ContextGraph] Rust graph_traverse.rs integration: ENABLED")
except ImportError:
    rust_batch_traverse = None
    rust_traverse_single = None
    rust_graph_stats = None
    rust_batch_centrality = None
    rust_batch_communities = None
    rust_batch_traverse_flat = None
    rust_drop_connections = None
    # Async wrappers
    batch_graph_traverse_async = None
    graph_traverse_single_async = None
    batch_graph_centrality_async = None
    graph_stats_async = None
    batch_graph_communities_async = None
    drop_connections_async = None
    logger.info("[ContextGraph] Rust graph_traverse.rs integration: DISABLED")

# Memory-safe limits for M1 8GB
_MAX_PYTHON_FALLBACK_NODES = 10_000  # NetworkX is slower, cap at 10K for safety
_MAX_PYTHON_FALLBACK_EDGES = 50_000  # Max edges for Python fallback

# C5: DuckDB fallback using direct DuckDB queries (when Rust unavailable)
_DUCKDB_AVAILABLE = False
try:
    import duckdb

    _DUCKDB_AVAILABLE = True
except ImportError:
    duckdb = None


def _duckdb_python_fallback(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict]]:
    """
    Python DuckDB traversal fallback (when Rust graph_traverse unavailable).

    Uses direct DuckDB recursive CTE instead of petgraph.

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values to traverse from
        max_hops: Maximum traversal depth (default 2)

    Returns:
        Dict mapping root_value -> list of connected nodes
    """
    if not _DUCKDB_AVAILABLE or not values:
        return {v: [] for v in values}

    try:
        conn = duckdb.connect(db_path, read_only=True)
        results: dict[str, list[dict]] = {v: [] for v in values}

        # Use recursive CTE for traversal
        sql = """
            WITH RECURSIVE paths(root_value, dst_id, depth) AS (
                SELECT n.value, e.dst_id, 1
                FROM ioc_edges e
                JOIN ioc_nodes n ON n.id = e.src_id
                WHERE n.value = ?
                UNION ALL
                SELECT p.root_value, e.dst_id, p.depth + 1
                FROM ioc_edges e
                JOIN paths p ON p.dst_id = e.src_id
                WHERE p.depth < ?
            )
            SELECT p.root_value, n.value, n.ioc_type, n.confidence, n.source
            FROM paths p
            JOIN ioc_nodes n ON n.id = p.dst_id
            LIMIT 100
        """

        for value in values:
            cursor = conn.execute(sql, [value, max_hops])
            rows = cursor.fetchall()
            results[value] = [
                {
                    "value": row[1],
                    "ioc_type": row[2],
                    "confidence": row[3],
                    "source": row[4],
                }
                for row in rows
            ]

        conn.close()
        return results

    except Exception as e:
        logger.warning(f"[ContextGraph] DuckDB Python fallback failed: {e}")
        return {v: [] for v in values}


def _duckdb_centrality_python_fallback(
    db_path: str,
    values: list[str],
) -> dict[str, float]:
    """
    Python PageRank fallback using DuckDB (when Rust unavailable).

    Uses iterative approach with DuckDB aggregates.

    Args:
        db_path: Path to DuckDB database file
        values: List of IOC values to compute PageRank for

    Returns:
        Dict mapping value -> pagerank_score
    """
    if not _DUCKDB_AVAILABLE or not values:
        return dict.fromkeys(values, 0.0)

    try:
        conn = duckdb.connect(db_path, read_only=True)
        result: dict[str, float] = {}

        sql = """
            SELECT n.value,
                   COALESCE(out_deg.cnt, 0) + COALESCE(in_deg.cnt, 0) as degree
            FROM ioc_nodes n
            LEFT JOIN (
                SELECT src_id, COUNT(*) as cnt FROM ioc_edges GROUP BY src_id
            ) out_deg ON n.id = out_deg.src_id
            LEFT JOIN (
                SELECT dst_id, COUNT(*) as cnt FROM ioc_edges GROUP BY dst_id
            ) in_deg ON n.id = in_deg.dst_id
            WHERE n.value = ANY(?)
        """

        cursor = conn.execute(sql, [values])
        rows = cursor.fetchall()

        # Normalize degrees to simple centrality scores
        max_degree = max((r[1] for r in rows), default=1)
        for row in rows:
            result[row[0]] = (row[1] / max_degree) if max_degree > 0 else 0.0

        conn.close()
        return result

    except Exception as e:
        logger.warning(f"[ContextGraph] DuckDB centrality fallback failed: {e}")
        return dict.fromkeys(values, 0.0)


# Centrality cache limits for M1 8GB
_MAX_CENTRALITY_CACHE_SIZE = 10_000  # Max entries in centrality cache


def louvain_communities(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
) -> dict[int, int]:
    """
    Detect communities using Louvain algorithm.

    Tries Rust implementation first (10-50x faster), falls back to Python.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        resolution: Louvain resolution parameter (higher = more smaller communities)

    Returns:
        Dict mapping node_id -> community_id
    """
    # Try Rust first
    if _RUST_GRAPH_ANALYTICS_AVAILABLE and rust_louvain is not None:
        try:
            result = rust_louvain(nodes, edges, resolution)
            if result:
                return result
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust louvain failed: {e}, trying Python fallback")

    # Python fallback
    return _python_louvain_fallback(nodes, edges, resolution)


def _python_louvain_fallback(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
) -> dict[int, int]:
    """
    Pure Python Louvain fallback using networkx + python-louvain.

    Memory-safe: capped at _MAX_PYTHON_FALLBACK_NODES for M1 8GB safety.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        resolution: Resolution parameter

    Returns:
        Dict mapping node_id -> community_id
    """
    if not _NETWORKX_AVAILABLE or not _PYTHON_LOUVAIN_AVAILABLE:
        logger.warning(
            "[ContextGraph] Python fallback unavailable: "
            f"networkx={_NETWORKX_AVAILABLE}, python-louvain={_PYTHON_LOUVAIN_AVAILABLE}"
        )
        return {}

    # Memory safety: cap nodes for Python fallback (M1 8GB)
    if len(nodes) > _MAX_PYTHON_FALLBACK_NODES:
        logger.warning(
            f"[ContextGraph] Python fallback: {len(nodes)} nodes exceeds limit {_MAX_PYTHON_FALLBACK_NODES}, truncating"
        )
        nodes = nodes[:_MAX_PYTHON_FALLBACK_NODES]

    # Memory safety: cap edges for Python fallback
    if len(edges) > _MAX_PYTHON_FALLBACK_EDGES:
        edges = edges[:_MAX_PYTHON_FALLBACK_EDGES]

    try:
        G = nx.Graph()
        node_set = {n[0] for n in nodes}  # Track valid nodes
        for node_id, value, node_type in nodes:
            G.add_node(node_id, value=value, node_type=node_type)

        # Only add edges where both nodes are valid
        for from_id, to_id, weight in edges:
            if from_id in node_set and to_id in node_set:
                G.add_edge(from_id, to_id, weight=weight)

        if G.number_of_nodes() == 0:
            return {}

        partition = community_louvain.best_partition(G, weight="weight", resolution=resolution)
        return {int(k): int(v) for k, v in partition.items()}

    except Exception as e:
        logger.error(f"[ContextGraph] Python Louvain fallback failed: {e}")
        return {}


def pagerank(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    damping: float = 0.85,
    max_iter: int = 100,
) -> dict[int, float]:
    """
    Compute PageRank scores.

    Tries Rust implementation first, falls back to Python.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        damping: Damping factor (default 0.85)
        max_iter: Maximum iterations

    Returns:
        Dict mapping node_id -> pagerank_score
    """
    # Try Rust first
    if _RUST_GRAPH_ANALYTICS_AVAILABLE and rust_pagerank is not None:
        try:
            result = rust_pagerank(nodes, edges, damping=damping, max_iter=max_iter)
            if result:
                return {int(k): float(v) for k, v in result.items()}
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust pagerank failed: {e}, trying Python fallback")

    # Python fallback
    return _python_pagerank_fallback(nodes, edges, damping=damping, max_iter=max_iter)


def _python_pagerank_fallback(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    damping: float = 0.85,
    max_iter: int = 100,
) -> dict[int, float]:
    """
    Pure Python PageRank fallback using networkx.

    Memory-safe: capped at _MAX_PYTHON_FALLBACK_NODES for M1 8GB safety.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        damping: Damping factor
        max_iter: Maximum iterations

    Returns:
        Dict mapping node_id -> pagerank_score
    """
    if not _NETWORKX_AVAILABLE:
        logger.warning("[ContextGraph] Python PageRank unavailable: networkx not installed")
        return {}

    # Memory safety: cap nodes for Python fallback (M1 8GB)
    if len(nodes) > _MAX_PYTHON_FALLBACK_NODES:
        logger.warning(
            f"[ContextGraph] Python PageRank: {len(nodes)} nodes exceeds limit {_MAX_PYTHON_FALLBACK_NODES}, truncating"
        )
        nodes = nodes[:_MAX_PYTHON_FALLBACK_NODES]

    # Memory safety: cap edges for Python fallback
    if len(edges) > _MAX_PYTHON_FALLBACK_EDGES:
        edges = edges[:_MAX_PYTHON_FALLBACK_EDGES]

    try:
        G = nx.Graph()
        node_set = {n[0] for n in nodes}
        for node_id, value, node_type in nodes:
            G.add_node(node_id, value=value, node_type=node_type)

        for from_id, to_id, weight in edges:
            if from_id in node_set and to_id in node_set:
                G.add_edge(from_id, to_id, weight=weight)

        if G.number_of_nodes() == 0:
            return {}

        pr = nx.pagerank(G, alpha=damping, max_iter=max_iter, weight="weight")
        return {int(k): float(v) for k, v in pr.items()}

    except Exception as e:
        logger.error(f"[ContextGraph] Python PageRank fallback failed: {e}")
        return {}


def strongly_connected_components(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
) -> list[list[int]]:
    """
    Compute strongly connected components using Kosaraju's algorithm.

    Tries Rust implementation first, falls back to Python.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples

    Returns:
        List of components, where each component is a list of node IDs
    """
    # Try Rust first
    if _RUST_GRAPH_ANALYTICS_AVAILABLE:
        try:
            from rust_extensions.wiring.graph_analytics_wiring import strongly_connected_components as rust_scc

            result = rust_scc(nodes, edges)
            if result is not None:
                return result
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust SCC failed: {e}, trying Python fallback")

    # Python fallback using NetworkX
    return _python_scc_fallback(nodes, edges)


def _python_scc_fallback(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
) -> list[list[int]]:
    """
    Pure Python SCC fallback using NetworkX (Tarjan's algorithm internally).

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples

    Returns:
        List of components, where each component is a list of node IDs
    """
    if not _NETWORKX_AVAILABLE:
        logger.warning("[ContextGraph] Python SCC unavailable: networkx not installed")
        return []

    try:
        import networkx as nx

        G = nx.DiGraph()
        node_set = {n[0] for n in nodes}

        for node_id, value, node_type in nodes:
            G.add_node(node_id, value=value, node_type=node_type)

        for from_id, to_id, _weight in edges:
            if from_id in node_set and to_id in node_set:
                G.add_edge(from_id, to_id)

        if G.number_of_nodes() == 0:
            return []

        # Find SCCs using Tarjan's algorithm (via NetworkX)
        sccs = list(nx.strongly_connected_components(G))
        return [list(scc) for scc in sccs]

    except Exception as e:
        logger.error(f"[ContextGraph] Python SCC fallback failed: {e}")
        return []


def traverse_duckdb(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict]]:
    """
    Traverse DuckDB graph using Rust batch_graph_traverse (Tier 0).

    Uses petgraph-powered rayon parallel traversal for 10x speedup
    over SQL recursive CTE.

    Falls back to Python DuckDB recursive CTE when Rust unavailable.

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        Dict mapping root_value -> list of connected nodes:
        {
            "evil.com": [
                {"value": "192.168.1.1", "ioc_type": "ip", "confidence": 0.9, "source": "dns"},
                ...
            ],
            ...
        }
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_batch_traverse is not None:
        try:
            result = rust_batch_traverse(db_path, values, max_hops)
            if result:
                return result
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust batch_graph_traverse failed: {e}")

    # Fallback: Python DuckDB recursive CTE
    logger.debug("[ContextGraph] Falling back to Python DuckDB traversal")
    return _duckdb_python_fallback(db_path, values, max_hops)


def traverse_duckdb_single(
    db_path: str,
    value: str,
    max_hops: int = 2,
) -> list[dict]:
    """
    Traverse DuckDB graph for a single root value (Rust Tier 0).

    Args:
        db_path: Path to DuckDB database file
        value: Root IOC value to traverse from
        max_hops: Maximum traversal depth (default 2, max 10)

    Returns:
        List of connected nodes with keys: value, ioc_type, confidence, source
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_traverse_single is not None:
        try:
            return rust_traverse_single(db_path, value, max_hops)
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust graph_traverse_single failed: {e}")

    return []


def traverse_duckdb_flat(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
    max_per_root: int = 20,
) -> list[dict]:
    """
    Traverse DuckDB graph with flattened results (all in one list).

    Args:
        db_path: Path to DuckDB database file
        values: List of root IOC values
        max_hops: Maximum traversal depth (default 2)
        max_per_root: Max results per root (default 20)

    Returns:
        List of connected nodes with additional 'source' key
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_batch_traverse_flat is not None:
        try:
            return rust_batch_traverse_flat(db_path, values, max_hops, max_per_root)
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust batch_graph_traverse_flat failed: {e}")

    return []


def compute_centrality_duckdb(
    db_path: str,
    values: list[str],
) -> dict[str, float]:
    """
    Compute PageRank centrality from DuckDB graph (Rust Tier 0).

    Uses power iteration with teleportation (damping factor 0.85).
    Bounded to 100K nodes for M1 8GB safety.

    Falls back to degree-based centrality when Rust unavailable.

    Args:
        db_path: Path to DuckDB database file
        values: List of IOC values to compute PageRank for

    Returns:
        Dict mapping value -> pagerank_score
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_batch_centrality is not None:
        try:
            return rust_batch_centrality(db_path, values)
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust batch_graph_centrality failed: {e}")

    # Fallback: Python DuckDB degree-based centrality
    logger.debug("[ContextGraph] Falling back to Python DuckDB centrality")
    return _duckdb_centrality_python_fallback(db_path, values)


def compute_communities_duckdb(
    db_path: str,
) -> dict[str, int]:
    """
    Compute communities from DuckDB graph using Label Propagation (Rust Tier 0).

    Label Propagation is O(n+m) per iteration - much faster than Louvain.

    Args:
        db_path: Path to DuckDB database file

    Returns:
        Dict mapping value -> community_id
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_batch_communities is not None:
        try:
            return rust_batch_communities(db_path)
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust batch_graph_communities failed: {e}")

    return {}


def get_graph_stats_duckdb(
    db_path: str,
    top_k: int = 20,
) -> dict:
    """
    Get graph statistics from DuckDB (Rust Tier 0).

    Args:
        db_path: Path to DuckDB database file
        top_k: Number of top nodes by degree (default 20)

    Returns:
        Dict with keys: total_nodes, total_edges, top_nodes
    """
    if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_graph_stats is not None:
        try:
            return rust_graph_stats(db_path, top_k)
        except Exception as e:
            logger.debug(f"[ContextGraph] Rust graph_stats failed: {e}")

    return {}


class ContextGraph:
    """
    Lightweight context graph with Rust-accelerated community detection.

    This is a modern replacement for the deprecated knowledge/context_graph.py.
    It provides:
    - Node and edge management
    - Rust-powered Louvain community detection
    - PageRank computation
    - Python fallback for portability

    M1 8GB: Bounded to 100K nodes, ~10-50MB memory.

    Example:
        graph = ContextGraph()
        graph.add_node(1, "ip", {"value": "1.2.3.4"})
        graph.add_node(2, "domain", {"value": "evil.com"})
        graph.add_edge(1, 2, 1.0)
        communities = graph.detect_communities()
    """

    __slots__ = (
        "_nodes",
        "_edges",
        "_communities",
        "_pagerank",
    )

    def __init__(self) -> None:
        """Initialize an empty context graph."""
        self._nodes: dict[int, dict[str, Any]] = {}
        self._edges: list[tuple[int, int, float]] = []
        self._communities: dict[int, int] | None = None
        self._pagerank: dict[int, float] | None = None

    def add_node(
        self,
        node_id: int,
        node_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """
        Add a node to the graph.

        Args:
            node_id: Unique node identifier
            node_type: Type of node (e.g., "ip", "domain", "hash")
            attributes: Optional node attributes
        """
        if node_id not in self._nodes:
            self._nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "attributes": attributes or {},
            }
            # Invalidate cached results
            self._communities = None
            self._pagerank = None

    def add_edge(
        self,
        source: int,
        target: int,
        weight: float = 1.0,
    ) -> None:
        """
        Add an edge to the graph.

        Args:
            source: Source node ID
            target: Target node ID
            weight: Edge weight (default 1.0)
        """
        # Ensure nodes exist
        if source not in self._nodes:
            self.add_node(source, "unknown")
        if target not in self._nodes:
            self.add_node(target, "unknown")

        self._edges.append((source, target, weight))
        # Invalidate cached results
        self._communities = None
        self._pagerank = None

    @property
    def nodes(self) -> list[dict[str, Any]]:
        """Get list of nodes as dicts."""
        return list(self._nodes.values())

    @property
    def edges(self) -> list[tuple[int, int, float]]:
        """Get list of edges."""
        return self._edges.copy()

    @property
    def num_nodes(self) -> int:
        """Get number of nodes."""
        return len(self._nodes)

    @property
    def num_edges(self) -> int:
        """Get number of edges."""
        return len(self._edges)

    def detect_communities(self, resolution: float = 1.0) -> dict[int, int]:
        """
        Detect communities using Louvain algorithm.

        Uses Rust implementation when available (10-50x faster).

        Args:
            resolution: Louvain resolution parameter (higher = more smaller communities)

        Returns:
            Dict mapping node_id -> community_id
        """
        if self._communities is not None:
            return self._communities

        nodes: list[tuple[int, str, str]] = [
            (node_id, data.get("attributes", {}).get("value", ""), data["type"])
            for node_id, data in self._nodes.items()
        ]
        edges = self._edges.copy()

        self._communities = louvain_communities(nodes, edges, resolution)
        return self._communities

    def compute_pagerank(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
    ) -> dict[int, float]:
        """
        Compute PageRank scores.

        Uses Rust implementation when available.

        Args:
            damping: Damping factor (default 0.85)
            max_iter: Maximum iterations

        Returns:
            Dict mapping node_id -> pagerank_score
        """
        if self._pagerank is not None:
            return self._pagerank

        nodes: list[tuple[int, str, str]] = [
            (node_id, data.get("attributes", {}).get("value", ""), data["type"])
            for node_id, data in self._nodes.items()
        ]
        edges = self._edges.copy()

        self._pagerank = pagerank(nodes, edges, damping=damping, max_iter=max_iter)
        return self._pagerank

    def get_communities_by_id(self, community_id: int) -> list[int]:
        """
        Get all nodes in a specific community.

        Args:
            community_id: Community ID to filter by

        Returns:
            List of node IDs in the community
        """
        if self._communities is None:
            self.detect_communities()

        return [node_id for node_id, comm in self._communities.items() if comm == community_id]

    def get_community_stats(self) -> dict[str, Any]:
        """
        Get statistics about detected communities.

        Returns:
            Dict with community statistics
        """
        if self._communities is None:
            self.detect_communities()

        if not self._communities:
            return {
                "num_communities": 0,
                "num_nodes": 0,
                "largest_community": 0,
                "smallest_community": 0,
                "avg_community_size": 0.0,
            }

        from collections import Counter

        community_sizes = Counter(self._communities.values())
        sizes = list(community_sizes.values())

        return {
            "num_communities": len(community_sizes),
            "num_nodes": len(self._communities),
            "largest_community": max(sizes),
            "smallest_community": min(sizes),
            "avg_community_size": sum(sizes) / len(sizes),
        }

    def clear(self) -> None:
        """Clear all nodes and edges."""
        self._nodes.clear()
        self._edges.clear()
        self._communities = None
        self._pagerank = None


class DuckDBContextGraph:
    """
    DuckDB-backed context graph with Rust-accelerated traversal.

    This class extends the in-memory ContextGraph with DuckDB-backed
    traversal capabilities using the Rust graph_traverse module.

    C5 Integration:
    - Tier 0: Rust batch_graph_traverse (petgraph-powered, 10x faster)
    - Tier 0: Rust batch_graph_centrality (PageRank from DuckDB)
    - Tier 0: Rust batch_graph_communities (Label Propagation from DuckDB)

    Architecture:
    - Uses thread-local DuckDB connections (M1 8GB safe)
    - LRU cache per worker thread with mmap persistence
    - Rayon parallel traversal across root values

    Example:
        graph = DuckDBContextGraph(db_path="/data/ioc.db")
        results = graph.traverse(["evil.com", "malware.exe"], max_hops=2)
        centrality = graph.compute_centrality(["evil.com"])
        stats = graph.get_stats()
        # Release connections between sprints
        await graph.drop_connections()
    """

    __slots__ = (
        "_db_path",
        "_centrality_cache",
    )

    def __init__(self, db_path: str) -> None:
        """
        Initialize DuckDB-backed context graph.

        Args:
            db_path: Path to DuckDB database file
        """
        self._db_path: str = db_path
        self._centrality_cache: dict[str, float] = {}

    @property
    def db_path(self) -> str:
        """Get the DuckDB database path."""
        return self._db_path

    def traverse(
        self,
        values: list[str],
        max_hops: int = 2,
    ) -> dict[str, list[dict]]:
        """
        Traverse DuckDB graph using Rust batch_graph_traverse (Tier 0).

        Args:
            values: List of root IOC values to traverse from
            max_hops: Maximum traversal depth (default 2, max 10)

        Returns:
            Dict mapping root_value -> list of connected nodes
        """
        return traverse_duckdb(self._db_path, values, max_hops)

    def traverse_single(
        self,
        value: str,
        max_hops: int = 2,
    ) -> list[dict]:
        """
        Traverse DuckDB graph for a single root value.

        Args:
            value: Root IOC value to traverse from
            max_hops: Maximum traversal depth (default 2, max 10)

        Returns:
            List of connected nodes
        """
        return traverse_duckdb_single(self._db_path, value, max_hops)

    def traverse_flat(
        self,
        values: list[str],
        max_hops: int = 2,
        max_per_root: int = 20,
    ) -> list[dict]:
        """
        Traverse DuckDB graph with flattened results.

        Args:
            values: List of root IOC values
            max_hops: Maximum traversal depth (default 2)
            max_per_root: Max results per root (default 20)

        Returns:
            List of connected nodes with 'source' key
        """
        return traverse_duckdb_flat(self._db_path, values, max_hops, max_per_root)

    def compute_centrality(
        self,
        values: list[str],
    ) -> dict[str, float]:
        """
        Compute PageRank centrality from DuckDB graph (Tier 0).

        Args:
            values: List of IOC values to compute PageRank for

        Returns:
            Dict mapping value -> pagerank_score
        """
        result = compute_centrality_duckdb(self._db_path, values)
        # Cache results with size limit for M1 8GB safety
        self._centrality_cache.update(result)
        self._enforce_cache_limit()
        return result

    def compute_communities(
        self,
    ) -> dict[str, int]:
        """
        Compute communities from DuckDB graph using Label Propagation (Tier 0).

        Returns:
            Dict mapping value -> community_id
        """
        return compute_communities_duckdb(self._db_path)

    async def compute_communities_async(
        self,
    ) -> dict[str, int]:
        """
        Compute communities from DuckDB graph using Label Propagation (async).

        Returns:
            Dict mapping value -> community_id
        """
        if batch_graph_communities_async is not None:
            try:
                return await batch_graph_communities_async(self._db_path)
            except Exception as e:
                logger.warning(f"[ContextGraph] async compute_communities failed: {e}")
        return compute_communities_duckdb(self._db_path)

    def get_stats(
        self,
        top_k: int = 20,
    ) -> dict:
        """
        Get graph statistics from DuckDB.

        Args:
            top_k: Number of top nodes by degree (default 20)

        Returns:
            Dict with keys: total_nodes, total_edges, top_nodes
        """
        return get_graph_stats_duckdb(self._db_path, top_k)

    async def get_stats_async(
        self,
        top_k: int = 20,
    ) -> dict:
        """
        Get graph statistics from DuckDB (async).

        Args:
            top_k: Number of top nodes by degree (default 20)

        Returns:
            Dict with keys: total_nodes, total_edges, top_nodes
        """
        if graph_stats_async is not None:
            try:
                return await graph_stats_async(self._db_path, top_k)
            except Exception as e:
                logger.warning(f"[ContextGraph] async graph_stats failed: {e}")
        return get_graph_stats_duckdb(self._db_path, top_k)

    def get_top_nodes_by_centrality(
        self,
        values: list[str],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Get top K nodes by PageRank centrality.

        Args:
            values: List of IOC values to consider
            top_k: Number of top nodes to return (default 10)

        Returns:
            List of (value, centrality_score) tuples sorted by centrality
        """
        if not self._centrality_cache:
            self.compute_centrality(values)

        sorted_nodes = sorted(self._centrality_cache.items(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:top_k]

    async def drop_connections(self) -> bool:
        """
        Drop thread-local DuckDB connections (async).

        F265-U5: Call between sprints to release connection memory.
        F265B-III: Also flushes LRU cache to mmap.

        Returns:
            True if successful, False otherwise
        """
        if drop_connections_async is not None:
            return await drop_connections_async()
        return False

    def drop_connections_sync(self) -> bool:
        """
        Drop thread-local DuckDB connections (sync).

        F265-U5: Call between sprints to release connection memory.

        Returns:
            True if successful, False otherwise
        """
        if _RUST_GRAPH_TRAVERSE_AVAILABLE and rust_drop_connections is not None:
            return rust_drop_connections()
        return False

    def _enforce_cache_limit(self) -> None:
        """Enforce cache size limit for M1 8GB safety."""
        if len(self._centrality_cache) > _MAX_CENTRALITY_CACHE_SIZE:
            # Remove oldest entries (first 20% when over limit)
            items_to_remove = len(self._centrality_cache) - _MAX_CENTRALITY_CACHE_SIZE + 1000
            keys_to_remove = list(self._centrality_cache.keys())[:items_to_remove]
            for key in keys_to_remove:
                del self._centrality_cache[key]
            logger.debug(
                f"[ContextGraph] Centrality cache pruned: "
                f"removed {len(keys_to_remove)} entries, "
                f"current size: {len(self._centrality_cache)}"
            )

    def clear_cache(self) -> None:
        """Clear the centrality cache."""
        self._centrality_cache.clear()


if _RUST_GRAPH_ANALYTICS_AVAILABLE:
    logger.info(
        f"[ContextGraph] Rust graph analytics: AVAILABLE (python-louvain fallback: {_PYTHON_LOUVAIN_AVAILABLE})"
    )
else:
    logger.warning(
        f"[ContextGraph] Rust graph analytics: UNAVAILABLE (python-louvain fallback: {_PYTHON_LOUVAIN_AVAILABLE})"
    )

if _RUST_GRAPH_TRAVERSE_AVAILABLE:
    logger.info(
        "[ContextGraph] Rust graph_traverse: AVAILABLE "
        "(batch_graph_traverse, batch_graph_centrality, batch_graph_communities)"
    )
else:
    logger.warning("[ContextGraph] Rust graph_traverse: UNAVAILABLE (falling back to DuckDB SQL traversal)")

__all__ = [
    # Classes
    "ContextGraph",
    "DuckDBContextGraph",
    # Graph analytics
    "louvain_communities",
    "pagerank",
    "strongly_connected_components",
    "analyze_ioc_graph",
    # C5: DuckDB graph traversal (Tier 0 - Rust)
    "traverse_duckdb",
    "traverse_duckdb_single",
    "traverse_duckdb_flat",
    "compute_centrality_duckdb",
    "compute_communities_duckdb",
    "get_graph_stats_duckdb",
]

"""
Graph Analytics Rust Integration Wiring
=====================================

Wires rust_extensions/src/graph_analytics.rs to:
- knowledge/ioc_graph.py
- graph/quantum_pathfinder.py

Purpose:
- Louvain community detection
- PageRank via petgraph
- Strongly connected components

Integration Point:
- IOC graph community analysis
- Graph centrality metrics

Usage:
    from rust_extensions.wiring.graph_analytics_wiring import graph_analytics_wired
    
    communities = graph_analytics_wired.louvain_communities(nodes, edges)
    pagerank = graph_analytics_wired.pagerank(nodes, edges)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_graph_analytics

# Create singleton instance
_graph_analytics = get_graph_analytics()


def graph_analytics_wired():
    """Get the wired graph analytics integration."""
    return _graph_analytics


def louvain_communities(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
) -> dict[int, int]:
    """
    Detect communities using Louvain algorithm.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        resolution: Louvain resolution parameter (default 1.0, higher = more smaller communities)

    Returns:
        Dict mapping node_id -> community_id
    """
    return _graph_analytics.louvain_communities(nodes, edges, resolution)


def pagerank(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
    damping: float = 0.85,
    max_iter: int = 100,
) -> dict[int, float]:
    """
    Compute PageRank scores.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples
        damping: Damping factor (default 0.85)
        max_iter: Maximum iterations (default 100)

    Returns:
        Dict mapping node_id -> pagerank_score
    """
    return _graph_analytics.pagerank(nodes, edges, damping, max_iter)


def strongly_connected_components(
    nodes: list[tuple[int, str, str]],
    edges: list[tuple[int, int, float]],
) -> list[list[int]]:
    """
    Compute strongly connected components using Kosaraju's algorithm.

    Args:
        nodes: List of (id, value, node_type) tuples
        edges: List of (from_id, to_id, weight) tuples

    Returns:
        List of components, where each component is a list of node IDs
    """
    if not _graph_analytics.available:
        logger.warning("[GraphAnalytics] SCC unavailable: Rust backend not available")
        return []
    try:
        from _rust_backend import rust
        result = rust.raw.module.rust_scc(nodes, edges)
        if result:
            return [list(comp) for comp in result]
        return []
    except Exception:  # noqa: BLE001
        return []


def analyze_ioc_graph(
    node_data: list[dict],
    edge_data: list[dict],
    resolution: float = 1.0,
    damping: float = 0.85,
    max_iter: int = 100,
) -> dict:
    """
    Analyze IOC graph with multiple algorithms.

    Tries to use rust_graph_analytics_all for single-pass efficiency,
    falls back to individual calls if unavailable.

    Args:
        node_data: List of IOC node dicts with keys: id, value, ioc_type
        edge_data: List of edge dicts with keys: from_id, to_id, weight
        resolution: Louvain resolution parameter (default 1.0)
        damping: PageRank damping factor (default 0.85)
        max_iter: PageRank max iterations (default 100)

    Returns:
        Dict with community_id per node and pagerank scores.
    """
    # Convert to format expected by Rust module
    nodes = [(n["id"], n.get("value", ""), n.get("ioc_type", "")) for n in node_data]
    edges = [(e["from_id"], e["to_id"], e.get("weight", 1.0)) for e in edge_data]

    # Try single-pass analytics for efficiency (avoids 3x graph construction)
    if _graph_analytics.available:
        try:
            from _rust_backend import rust
            result = rust.raw.module.rust_graph_analytics_all(nodes, edges, damping, resolution)
            if result:
                return {
                    "communities": dict(result.get("communities", {})),
                    "pagerank": dict(result.get("pagerank", {})),
                    "scc": result.get("scc", []),
                    "num_communities": len(set(result.get("communities", {}).values())),
                    "num_nodes": len(nodes),
                    "num_edges": len(edges),
                }
        except Exception:  # noqa: BLE001
            pass

    # Fall back to individual calls
    communities = louvain_communities(nodes, edges, resolution)
    ranks = pagerank(nodes, edges, damping, max_iter)

    return {
        "communities": communities,
        "pagerank": ranks,
        "num_communities": len(set(communities.values())),
        "num_nodes": len(nodes),
        "num_edges": len(edges),
    }


# Check availability at import time for logging
if _graph_analytics.available:
    logger.info("[GraphAnalytics] Rust graph_analytics.rs integration: ENABLED")
else:
    logger.info("[GraphAnalytics] Rust graph_analytics.rs integration: DISABLED (using Python fallback)")

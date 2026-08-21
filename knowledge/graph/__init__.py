"""
knowledge/graph/ — C5: DuckDB Graph Traversal Integration

ARCHITECTURE (MOD-17 + C5):
  knowledge/graph/backend_protocol.py — DELETED (0 active callers)
  knowledge/graph/router.py — DELETED (0 active callers)

  Active graph layer:
  - DuckPGQGraph (graph/quantum_pathfinder.py) — primary graph backend
  - rustworkx.PyGraph (graph/graph_manager.py) — visualization only
  - rust_extensions::graph_traverse — C5: petgraph-powered DuckDB traversal (Tier 0)
  - context_graph.py — Rust Louvain + C5 DuckDB traversal (A7 + C5)
  - query_cache.py — B3: TTL-aware graph traversal query cache

  All graph operations go through knowledge/graph_service.py which uses
  DuckPGQGraph directly — no routing needed.

C5: DuckDB Graph Traversal Integration:
  - Tier 0: Rust batch_graph_traverse (petgraph-powered, 10x faster than SQL CTE)
  - Tier 0: Rust batch_graph_centrality (PageRank from DuckDB)
  - Tier 0: Rust batch_graph_communities (Label Propagation from DuckDB)
  - drop_connections: Release thread-local connections between sprints

B3: Graph Cache Integration:
  - Tier 0: Rust LRU cache (graph_cache.rs) via graph_cache_wiring.py
  - TTL Layer: 5-minute TTL with intelligent invalidation
  - IOC Invalidation: Cache cleared when new IOCs are added
"""

from knowledge.graph.context_graph import (
    ContextGraph,
    DuckDBContextGraph,
    compute_centrality_duckdb,
    compute_communities_duckdb,
    get_graph_stats_duckdb,
    louvain_communities,
    pagerank,
    strongly_connected_components,
    # C5: DuckDB traversal
    traverse_duckdb,
    traverse_duckdb_flat,
    traverse_duckdb_single,
)

try:
    from rust_extensions.wiring.graph_analytics_wiring import analyze_ioc_graph
except ImportError:
    analyze_ioc_graph = None

# B3: Import query cache
try:
    from knowledge.graph.query_cache import QueryCache, get_query_cache
except ImportError:
    QueryCache = None
    get_query_cache = None

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
    # B3: Query cache
    "QueryCache",
    "get_query_cache",
]

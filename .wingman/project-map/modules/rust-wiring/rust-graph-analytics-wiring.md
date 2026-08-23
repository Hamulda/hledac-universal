# rust-graph-analytics-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/graph_analytics_wiring.py`  
**Status:** current

## Purpose

Rust-native graph analytics using igraph backend. Supports BFS, PageRank, connected components, and triangle counting.

## Key Functions

| Function | Purpose |
|----------|---------|
| `graph_from_edges(edgelist)` | Build graph from edge list |
| `bfs(start_node, max_depth)` | Breadth-first search |
| `pagerank()` | Compute PageRank |
| `connected_components()` | Find connected components |
| `triangle_count()` | Count triangles |

## Invariants

- [RGA-1] igraph Rust bindings for all graph operations
- [RGA-2] Node IDs must be uint32
- [RGA-3] Max graph size: 10M edges (memory bound)

## M1 Memory Notes

Graph stored in Rust heap. ~50 bytes per edge overhead.

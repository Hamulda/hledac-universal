# Hypothesis Graph

## Metadata

- **Entry Path:** modules/hypothesis-graph
- **Status:** current
- **Source:** graph/hypothesis_graph.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Graph structure for tracking research hypotheses with hidden bridge discovery.

## Source Paths

- `graph/hypothesis_graph.py`

## Key Classes

| Class | Purpose |
|-------|---------|
| `HypothesisNode` | Graph node with label and payload |
| `HypothesisEdge` | Directed edge with weight and rationale |
| `HiddenBridge` | Latent edge discovered by pathfinder |
| `AnomalousCluster` | Cluster with anomalous edge density |

## Bounds

| Limit | Value |
|-------|-------|
| MAX_NODES | 5000 |
| MAX_EDGES | 20000 |

## Graph Operations

- `add_entity()`: Add hypothesis node
- `add_hypothesis_edge()`: Connect nodes
- `find_hidden_bridges()`: Discover latent connections
- `detect_anomalous_clusters()`: Find unusual patterns

## Related Entries

- modules/graph-manager
- modules/research-optimizer

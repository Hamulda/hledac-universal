# Graph Facade

## Metadata

- **Entry Path:** modules/graph-facade
- **Status:** current
- **Source:** runtime/adapters/graph_adapter.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Protocol-based facade for DuckPGQGraph with tiered analytics methods.

## Source Paths

- `runtime/adapters/graph_adapter.py`
- `graph/context_graph.py`

## TIER_A: Analytics

| Method | Purpose |
|--------|---------|
| `upsert_ioc()` | Analytics IOC upsert |
| `find_connected()` | Graph traversal |
| `upsert_relation()` | Add relation edge |
| `upsert_ioc_batch()` | Batch IOC upsert |
| `find_connected_batch()` | Batch traversal |

## TIER_S: Storage

Buffered write methods to DuckDB GraphAttachmentStore.

## DuckPGQGraph Adapter

```python
from runtime.adapters.graph_adapter import DuckPGQGraphAdapter

graph = DuckPGQGraph(...)
adapter = DuckPGQGraphAdapter(graph)
await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id="sprint_1")
```

## Related Entries

- modules/hypothesis-graph
- modules/graph-manager

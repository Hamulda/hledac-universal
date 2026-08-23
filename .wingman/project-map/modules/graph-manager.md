# Graph Manager

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/graph-manager.md` |
| Source Path | `graph/graph_manager.py` |

## Summary

Lightweight graph visualization using rustworkx.PyGraph (in-memory) + pyvis HTML export. Migrated from igraph (P2-5d, 2026-07-17). No graph DB.

## Evidence

- Uses rustworkx.PyGraph (Rust-based, 3-10x faster than igraph on M1)
- Node attributes: entity_type + value only
- export_html(path) renders interactive HTML via pyvis
- Stream-adds nodes, no batch bulk operations

## Use When

- Visualizing entity relationships
- Adding graph analysis capabilities
- Debugging graph structure

## Do Not Use When

- Need persistent graph storage (no graph DB by design)
- Need detailed node attributes (only entity_type + value)

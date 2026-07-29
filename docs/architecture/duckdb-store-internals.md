# DuckDB Store Internals — F360 Architecture Reference

> **Source:** `knowledge/duckdb_store.py` module docstring (extracted 2026-07-29).
> Extracted from `"""..."""` block to keep in-file docstring ≤ 30 lines.
> This file is the canonical home for architectural overview prose.

## Role

`DuckDBShadowStore` — canonical store for sprint-level facts and derived analytics.

⚠️ "Shadow" in the class name refers to historical naming (Sprint 8AO/8AS).
This store IS the canonical sprint facts authority for the analytics subsystem,
not a shadow of anything.

## F360 Architecture

This module contains `DuckDBShadowStore` (monolithic, backward-compatible).
Extracted components live in:

| File | Component | Purpose |
|------|-----------|---------|
| `duckdb_protocol.py` | `DuckDBStoreProtocol` | Interface contract |
| `duckdb_vector_store.py` | `DuckDBVectorStore` | HNSW/vector operations |
| `duckdb_quality_gate.py` | `DuckDBQualityGate` | Quality assessment |
| `duckdb_graph_attachment.py` | `DuckDBGraphAttachment` | Graph attachment |
| `duckdb_wal_manager.py` | `DuckDBWALManager` | WAL + LMDB lifecycle |
| `duckdb_canonical.py` | `DuckDBCanonical` | Future refactor target |

The 15 "DEPRECATED" graph methods (`inject_graph`, `get_graph_stats`, etc.)
are now delegated to `DuckDBGraphAttachment` instead of inline lazy-init.
See `duckdb_graph_attachment.py` for the extracted implementation.

## Facts Hierarchy (3 Tiers)

### Tier 1 — Sprint Facts (DuckDB, durable)

| Table | Description |
|-------|-------------|
| `sprint_delta` | Per-sprint metrics: query, duration, new_findings, dedup_hits, ioc_nodes |
| `sprint_scorecard` | Per-sprint aggregated scores: fpm, ioc_density, synthesis_confidence |
| `source_hit_log` | Per-sprint source attribution: source_type, hit_rate |

### Tier 2 — Shadow Findings (DuckDB, durable)

| Table | Description |
|-------|-------------|
| `canonical_findings` | Finding-level records forwarded from `EvidenceLog.append()` |
| `shadow_runs` | Run-level metadata |

> F272: DuckDB `ioc_graph` table removed; IOC storage via `DuckPGQGraph`
> (`graph/quantum_pathfinder.py`)

### Tier 3 — Cross-Sprint (DuckDB, append-only, pruneable)

| Table | Description |
|-------|-------------|
| `temporal_events` | Time-indexed events for temporal archaeology |

## See Also

- `knowledge/duckdb_protocol.py` — `DuckDBStoreProtocol` interface contract
- `knowledge/duckdb_vector_store.py` — `DuckDBVectorStore` HNSW operations
- `knowledge/duckdb_quality_gate.py` — `DuckDBQualityGate` quality assessment
- `knowledge/duckdb_graph_attachment.py` — `DuckDBGraphAttachment` graph attachment
- `knowledge/duckdb_wal_manager.py` — `DuckDBWALManager` WAL + LMDB lifecycle
- `graph/quantum_pathfinder.py` — `DuckPGQGraph` (IOC graph, analytics donor)

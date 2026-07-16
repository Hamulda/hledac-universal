---
children_hash: 974ec359b3002479bce7ad0d04a5c7fb9e9cdc8c2ded942c5850cbdb24c1748b
compression_ratio: 0.2536134453781513
condensation_order: 1
covers: [brain_module_organization.md, context.md, critical_invariants.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, layer_protocol_and_layerstack.md, project_documentation.md, rust_extensions_code_review.md, sidecar_protocol_registry.md, sprint_lifecycle_pipeline.md]
covers_token_total: 5950
summary_level: d1
token_count: 1509
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries. I need to consolidate these 10 entries about hledac_universal architecture into a coherent overview that:
- Preserves key facts, architectural decisions, and relationships
- References entry names for drill-down
- Targets ~20-30% compression
- Outputs clean markdown without code blocks or XML tags

Let me analyze the entries:

1. **brain_module_organization.md** - PEP 562 facade, 12 lazy-loaded engines, MLX/Apple Silicon support, cold import reduced from 9.7s to 150ms

2. **context.md** - Overview of HTTP/3 lane transport, opportunistic/stealth modes, public fetcher, M1 8GB memory

3. **critical_invariants.md** - 10 critical invariants for M1 8GB stability (async patterns, MLX cache, DuckDB/LMDB, bloom filters, fail-safe)

4. **hledac_universal_architecture.md** - Sprint-based async orchestrator, DuckDB shadow store, tiered sources, M1 8GB optimizations

5. **http_3_lane_and_public_fetcher.md** - HTTP/3 dual strategy (curl_cffi + aioquic), Tor/I2P, URL classification, body hashing

6. **layer_protocol_and_layerstack.md** - Layer Protocol with LayerStack for event propagation, mount/unmount lifecycle, UDS IPC

7. **project_documentation.md** - Comprehensive project docs: OSINT orchestrator on M1 8GB, MLX/Hermes3, sprint cycles, storage trinity

8. **rust_extensions_code_review.md** - Rust extensions review: OnceLock fix, SQL injection patch, GIL false positive, 87 pre-existing errors

9. **sidecar_protocol_registry.md** - 17 sidecar adapters, lazy imports, memory admission, finding output format


10. **sprint_lifecycle_pipeline.md** - Sprint pipeline from CLI through 8 lanes to DuckDB, 12 lifecycle stages

These entries form a cohesive picture of the system architecture. I'll structure the summary to show how these components interrelate, starting with the top-level architecture, then drilling into the sprint pipeline and lifecycle, followed by the critical subsystems like brain module organization, HTTP/3 transport layers, sidecar protocols, and extension architecture. The critical invariants tie everything together, so I'll present those as foundational constraints.
</think>

# Hledac Universal Architecture

## Overview

Hledac Universal is an OSINT orchestrator running on MacBook Air M1 with 8GB unified memory, using MLX/Hermes3 for inference and sprint cycles to process search queries returning structured IoC data. Storage trinity: DuckDB (SQL/canonical facts), LMDB (key-value/entity), LanceDB (ANN/RAG).

## Core Modules

### Runtime
- **sprint_lifecycle_pipeline**: CLI → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB write. 12 lifecycle stages from NOT_SCHEDULED through ACCEPTED. Advisory Log LRU(16) with FIFO eviction, no promotion on hit.
- **layer_protocol_and_layerstack**: Layer Protocol (runtime_checkable) defines mount/unmount/on_event interface. LayerStack manages lifecycle with timeouts (30s/10s/30s). UDS Protocol uses msgspec.msgpack for zero-copy IPC. Event propagation stops if layer returns None or sets halted=True.

### Brain Layer
- **brain_module_organization**: PEP 562 facade reduces cold import from ~9.7s to ~150ms. 12 lazy-loaded engines via `__getattr__`. Hermes3Engine is L1 canonical. ModelManager handles M1 lifecycle. Promotes FACADE export-only pattern. NEREngine requires large RAM. MoE and Distillation are DORMANT.

### Transport
- **http_3_lane_and_public_fetcher**: Dual HTTP/3 strategy: curl_cffi opportunistic + aioquic stealth (~50-80MB resident). LRU cache bounded at 512 entries. Rust URL classification with xxh3_64 cache key. BLAKE3-64 with NEON acceleration for body hashing. Tor/I2P via SOCKS proxies with circuit renewal every 10 requests. QUIC/UDP incompatible with Tor/I2P — H3 auto-skipped for dark web.

### Storage
- **hledac_universal_architecture**: DuckDB shadow store is canonical facts authority. M1 8GB optimizations: 600MB DuckDB limit, 4 threads, Arrow zero-copy, LRU(16) dedup, msgspec.Struct hot-path DTOs. Chunk size: 500, concurrency: 2 for DuckDB inserts.
- **critical_invariants**: 10 invariants for M1 stability. Key: asyncio.gather always with return_exceptions=True, mx.eval([]) before clear_cache(), DuckDB only via async_ingest_findings_batch(), LMDB bulk only via cursor.putmulti(), M1 Metal cache dynamic formula with 1GiB ceiling.

### Extensions
- **rust_extensions_code_review**: OnceLock stores module name string (re-imported per call), SQL injection patched via DuckDB parameterized queries, GIL safety confirmed (held by pyfunction macro), 87 pre-existing errors are PyClass Frozen=False mismatches.

### Sidecars
- **sidecar_protocol_registry**: 17 adapters with registry mechanics, lazy imports, memory admission via GovernorDecision. Priority 7 adapters (lancedb_rag, threat_intel) run early. ram_budget_mb checked before every run. 17 adapters: fediverse(50MB/6), dht(100MB/4), academic(80MB/5), lancedb_rag(60MB/7), threat_intel(40MB/7), and 12 others.

## Key Architectural Decisions

- **Sprint pipeline**: Owner dispatches cycles via SprintScheduler. Tier priority High→Low: surface → structured_ti → deep → archive → other. Nonfeed fallback lanes: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH.
- **Storage trinity**: DuckDB for sprint facts and analytics, DuckPGQGraph for IOC storage, LMDB for payload WAL.
- **MLX patterns**: kv_bits=4, max_kv_size=8192 in mlx_lm.generate(). Metal cache hard limit 1.5 GiB.

## Entry Reference

- `brain_module_organization.md` — Lazy engine loading, Hermes3 canonical
- `critical_invariants.md` — 10 M1 stability rules
- `http_3_lane_and_public_fetcher.md` — HTTP/3 dual strategy, Tor/I2P, hashing
- `layer_protocol_and_layerstack.md` — Event propagation, UDS IPC
- `project_documentation.md` — Comprehensive architecture reference
- `rust_extensions_code_review.md` — Rust extension fixes
- `sidecar_protocol_registry.md` — 17 sidecar adapters
- `sprint_lifecycle_pipeline.md` — Pipeline flow, 12 lifecycle stages
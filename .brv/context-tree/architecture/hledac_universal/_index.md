---
children_hash: 0316087f21f2dbaa4e7b94564197c71ce2d121dbce07668b2dd1423429cd750b
compression_ratio: 0.16646375207526287
condensation_order: 1
covers: [10-critical-invariants-govern-system-stability.md, brain_module_organization.md, context.md, critical-invariants-as-cross-domain-enforcement-mechanism.md, critical_invariants.md, duckdb_kuzu_dual_graph_architecture.md, graphservice_sprint_memory_layer.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, layer_protocol_and_layerstack.md, lazy-loading-reduces-cold-import-by-98.md, project_documentation.md, rust_extensions_code_review.md, sidecar_protocol_registry.md, sprint-lifecycle-cross-domain-contract.md, sprint-pipeline-orchestrates-8-acquisition-lanes.md, sprint_lifecycle_pipeline.md]
covers_token_total: 9035
summary_level: d1
token_count: 1504
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from 20+ child entries in the architecture/hledac_universal domain. I need to condense this into ~20-30% token count while preserving key facts, architectural decisions, and relationships.

Let me organize the key themes:

1. **10 Critical Invariants** - M1 8GB stability rules enforced cross-domain (I1-I10)
2. **Brain Module** - PEP 562 lazy loading, 12 engines, Hermes3 canonical, MLX support
3. **Sprint Lifecycle Pipeline** - 12-stage lifecycle, 8 acquisition lanes, advisory dedup, DuckDB write
4. **DuckDB/Kuzu Dual Graph** - IOCGraph (Kuzu) truth store, DuckPGQGraph analytics donor
5. **HTTP/3 Lane & Public Fetcher** - dual strategy (curl_cffi + aioquic), Tor/I2P, URL classification
6. **Layer Protocol & LayerStack** - event propagation, mount/unmount lifecycle, UDS IPC
7. **Sidecar Protocol Registry** - 17 adapters, lazy imports, memory admission
8. **Rust Extensions** - PyO3 bridges, feed_pipeline, ioc_extractor, url_ops
9. **Lazy Loading** - PEP 562 facades reduce cold import 9.7s → 150ms

Key relationships to preserve:
- Sprint lifecycle is the core orchestration (SprintScheduler.run)
- DuckDB via async_ingest_findings_batch() is canonical write (Invariant I5)
- Critical invariants enforced cross-domain
- GraphService is sprint memory layer

Let me create a clean structural summary.
</think>

# architecture/hledac_universal — Domain Summary

## Overview

Hledac Universal is a sprint-based OSINT orchestrator for MacBook Air M1 8GB. It uses MLX/Hermes3 for inference, DuckDB/LMDB/LanceDB for storage, and curl_cffi/aioquic for HTTP/3 fetching. Entry: `python -m hledac.universal --sprint QUERY`.

---

## Core Architecture

### Sprint Lifecycle Pipeline
12-stage sprint lifecycle orchestrates 8 acquisition lanes (surface → structured_ti → deep → archive → other → CT/WAYBACK/PASSIVE_DNS/PIVOT_EXECUTOR/DOH) through advisory runners → graph accumulation → DuckDB canonical write. Advisory Log uses LRU(16) with FIFO eviction and no promotion on hit. Tier priority: High→Low.

See: `sprint_lifecycle_pipeline.md`, `sprint-pipeline-orchestrates-8-acquisition-lanes.md`, `sprint-lifecycle-cross-domain-contract.md`

### 10 Critical Invariants (M1 8GB Stability)
Cross-domain enforcement mechanism for async patterns, MLX lifecycle, DuckDB/LMDB writes, bloom filters, fail-safe returns:
- **I1**: No bare `except:` — always `except Exception:`
- **I2**: `mx.eval([])` before `mx.metal.clear_cache()`
- **I3**: No `time.sleep()` in async — use `asyncio.sleep()`
- **I4**: No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()`
- **I5**: DuckDB ONLY via `async_ingest_findings_batch()`
- **I6**: LMDB bulk ONLY via `cursor.putmulti()`
- **I7**: URL dedup ONLY via `RotatingBloomFilter`
- **I8**: M1 Metal cache: dynamic formula, 1GiB ceiling on 8GB M1
- **I9**: Fail-safe: sidecars return `[]` on errors
- **I10**: `asyncio.gather` ALWAYS with `return_exceptions=True`

Enforcement distributed: `duckdb_store` (I5), `resource_governor` (I7), `facts/project` CI (all).

See: `critical_invariants.md`, `10-critical-invariants-govern-system-stability.md`, `critical-invariants-as-cross-domain-enforcement-mechanism.md`

---

## Storage Architecture

### DuckDB-Kuzu Dual Graph
- **IOCGraph (KuzuDB)**: Authoritative truth store for entities
- **DuckPGQGraph (DuckDB)**: Analytics donor with buffer_ioc(), flush_buffers(), export_stix_bundle()
- **GraphService**: Sprint memory layer facade with instance-isolated idempotency state

DuckDB config: 600MB limit, 4 threads, chunk size 500, concurrency 2.

See: `duckdb_kuzu_dual_graph_architecture.md`, `graphservice_sprint_memory_layer.md`

### Storage Trinity
- **DuckDB**: SQL/canonical facts, sprint facts
- **LMDB**: Key-value/entity hot-edges cache
- **LanceDB**: ANN/RAG vector search

---

## Brain Module

PEP 562 `__getattr__` facade reduces cold import from ~9.7s to ~150ms. 12 lazy-loaded engines (deephermes3, mlx_dispatcher, batch_scheduler, causal, distillation, DSPy, CoreML, ANE, MLX embeddings, NER, MoE, Distillation). Hermes3Engine is L1 canonical. ModelManager handles M1 lifecycle.

See: `brain_module_organization.md`, `lazy-loading-reduces-cold-import-by-98.md`

---

## Transport & Fetching

### HTTP/3 Lane
Dual strategy: curl_cffi (opportunistic H3 upgrade) + aioquic (stealth H3, ~50-80MB resident, capped at 3 concurrency). LRU cache (512 entries, O(1) eviction). H3 auto-skipped for Tor/I2P (QUIC/UDP incompatibility).

Env gates: `HLEDAC_ENABLE_HTTPX_H3=1` or legacy `HLEDAC_HTTP3=1`.

### Public Fetcher
Rust URL classification (xxh3_64 cache key), BLAKE3-64 body hashing with NEON acceleration, Tor/I2P SOCKS sessions. Browser UA pool: Chrome/Firefox/Safari/Edge. Dark web TLDs: .onion, .i2p, .b32.i2p.

See: `http_3_lane_and_public_fetcher.md`

---

## Layer Protocol

Runtime-checkable Protocol defines mount/unmount/on_event interface. LayerStack manages lifecycle and event propagation in mount order. UDS Protocol for zero-copy IPC via msgspec.msgpack.

Timeouts: mount 30s (rollback on error), unmount 10s (best-effort), on_event 30s per layer.

See: `layer_protocol_and_layerstack.md`

---

## Sidecar Protocol Registry

17 adapters with registry mechanics, lazy imports, memory admission via `GovernorDecision.sidecar_admission`. Priority 7 adapters (lancedb_rag, threat_intel) run early. ram_budget_mb checked before every run.

See: `sidecar_protocol_registry.md`

---

## Rust Extensions (PyO3)

Hot-paths via PyO3 bridges: feed_pipeline, ioc_extractor, url_ops, content_hasher, batch_counters. Key fixes: OnceLock stores `&str` (not `Bound<PyModule>`) for Sync+Send; SQL uses parameterized queries.

See: `rust_extensions_code_review.md`

---

## Related Domains

- `facts/project`: Critical invariants CI (GHOST_INVARIANTS), M1 bounds config
- `data/duckdb_store`: async_ingest_findings_batch canonical write, WAL manager
- `memory/resource_governor`: Hysteresis state machine, asyncio.sleep for I7
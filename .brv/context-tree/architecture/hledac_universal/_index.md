---
children_hash: fd1b6f927bbcc4e55e94341a3596cf5fb607ab646001759edd8e77d5c18b5fc4
compression_ratio: 0.1590650532630055
condensation_order: 1
covers: [10-critical-invariants-govern-system-stability.md, brain_module_organization.md, context.md, critical-invariants-as-cross-domain-enforcement-mechanism.md, critical_invariants.md, duckdb_kuzu_dual_graph_architecture.md, graphservice_sprint_memory_layer.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, issue_17_bounded_inference_pipeline.md, layer_protocol_and_layerstack.md, lazy-loading-reduces-cold-import-by-98.md, project_documentation.md, rust_extensions_code_review.md, sidecar_protocol_registry.md, sprint-lifecycle-cross-domain-contract.md, sprint-pipeline-orchestrates-8-acquisition-lanes.md, sprint_lifecycle_pipeline.md]
covers_token_total: 9669
summary_level: d1
token_count: 1538
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. This is about the Hledac Universal architecture domain. I need to distill the key facts, architectural decisions, and relationships while preserving important details and referencing entry names for drill-down.

Let me organize this by the major architectural themes I see:

1. **Core Architecture** - Entry point, sprint pipeline, directory structure
2. **Critical Invariants** - The 10 stability rules enforced across domains
3. **Brain Module** - ML inference with lazy loading, PEP 562 facade
4. **HTTP/3 Transport** - Dual strategy (curl_cffi + aioquic), public fetcher
5. **Graph Storage** - DuckDB/Kuzu dual graph, DuckPGQGraph
6. **Sidecar Protocol** - 17 adapters, registry mechanics
7. **Layer Protocol** - Event propagation, mount/unmount lifecycle
8. **Rust Extensions** - PyO3 bridges

Key relationships and patterns:
- Sprint pipeline: CLI → 8 lanes → advisory runners → graph → DuckDB
- DuckDB is canonical write target, enforced via async_ingest_findings_batch
- MLX inference with Hermes3, lazy-loaded brain module
- HTTP/3 dual strategy for stealth/opportunistic modes
- 10 critical invariants span async, MLX, storage, fail-safe patterns
- Dual-graph: Kuzu (truth) + DuckPGQGraph (analytics)

I should create a concise summary that preserves these relationships and key facts while referencing entry names.
</think>

# Hledac Universal Architecture

Hledac Universal is an OSINT orchestrator for M1 8GB unified memory, using MLX/Hermes3 inference, sprint cycles, and a storage trinity (DuckDB/LMDB/LanceDB). Entry point: `python -m hledac.universal --sprint QUERY [--duration SECS] [--aggressive]`.

## Core Pipeline

The sprint pipeline flows: **CLI → run_sprint() → SprintScheduler.run() → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write**. Acquisition lanes follow tier priority: surface → structured_ti → deep → archive → other. Advisory Log uses LRU(16) with FIFO eviction, no promotion on hit. Directory structure: `core/` (governance, locks, capabilities), `runtime/` (sprint_scheduler, sidecar_protocol), `knowledge/` (duckdb_store, graph_service/DuckPGQGraph, lancedb_store), `fetching/` (public_fetcher/curl_cffi), `transport/` (http3_lane, tor/i2p/nym), `brain/` (inference_engine, mlx_batched_executor, hypothesis_engine).

**See**: `hledac_universal_architecture.md`, `sprint_lifecycle_pipeline.md`, `sprint-pipeline-orchestrates-8-acquisition-lanes.md`

## Critical Invariants (Cross-Domain Enforcement)

10 critical invariants govern M1 8GB stability, defined in architecture but enforced by duckdb_store (I5), resource_governor (I7), and facts/project CI (GHOST_INVARIANTS):

- **I1**: asyncio.gather ALWAYS with return_exceptions=True
- **I2**: mx.eval([]) before mx.metal.clear_cache()
- **I3**: No time.sleep() in async—use asyncio.sleep()
- **I4**: No asyncio.run() in ThreadPoolExecutor
- **I5**: DuckDB ONLY via async_ingest_findings_batch()
- **I6**: LMDB bulk ONLY via cursor.putmulti()
- **I7**: URL dedup ONLY via RotatingBloomFilter
- **I8**: M1 Metal cache: dynamic formula min(max(available×0.2, 512MiB), 1GiB)
- **I9**: Sidecars return [] on errors
- **I10**: No bare except—always except Exception:

**See**: `critical_invariants.md`, `10-critical-invariants-govern-system-stability.md`, `critical-invariants-as-cross-domain-enforcement-mechanism.md`

## Brain Module (ML Inference)

PEP 562 facade with `__getattr__` reduces cold import from ~9.7s to ~150ms for 12 lazy-loaded engines. Hermes3Engine is L1 canonical. BoundedInferencePipeline implements 3-stage bounded asyncio.Queue pipeline: _prep_executor(3 workers) → MLXWorkerThread(1 serial) → _post_executor(2 workers), total memory < 200KB. NEREngine requires large RAM; MoE and Distillation are DORMANT.

**See**: `brain_module_organization.md`, `issue_17_bounded_inference_pipeline.md`, `lazy-loading-reduces-cold-import-by-98.md`

## HTTP/3 Transport & Public Fetcher

Dual strategy: curl_cffi for opportunistic HTTP/3, aioquic for stealth mode. QUIC/UDP incompatible with Tor/I2P—H3 auto-skipped for dark web. Tor circuit renewal every 10 requests, 2.0× stealth timeout scale. LRU cache bounded at 512 entries. Body hashing via BLAKE3-64 with NEON acceleration, xxHash3 fallback. 11 module-level globals replaced by _SessionManager singleton.

**See**: `http_3_lane_and_public_fetcher.md`, `context.md`

## Graph Storage (Dual-Graph Architecture)

IOCGraph (KuzuDB) is authoritative truth store; DuckPGQGraph (DuckDB) is analytics donor. GraphService is sprint memory layer facade with instance-isolated idempotency state (_seen_iocs, _seen_rels). Since F272: DuckPGQGraph handles buffer_ioc(), flush_buffers(), export_stix_bundle(). LanceDB for vector search, LMDB for hot-edges cache.

**See**: `duckdb_kuzu_dual_graph_architecture.md`, `graphservice_sprint_memory_layer.md`

## Sidecar Protocol Registry

17 adapters with lazy imports and memory admission via GovernorDecision.sidecar_admission. High-priority adapters (lancedb_rag, threat_intel) run at priority 7. Academic adapter skips in aggressive mode. Env var pattern: HLEDAC_ENABLE_* for enabling adapters. ram_budget_mb checked before every run.

**See**: `sidecar_protocol_registry.md`

## Layer Protocol & LayerStack

Runtime-checkable Protocol defining mount/unmount/on_event interface. Timeouts: mount 30s with rollback, unmount 10s best-effort, on_event 30s per layer. UDS Protocol uses msgspec.msgpack for zero-copy IPC. Layers execute in mount order; propagation stops on None or halted=True. Unmount rolls back in reverse order on failure.

**See**: `layer_protocol_and_layerstack.md`

## Rust Extensions (PyO3 Bridge)

PyO3 Rust extensions dominate all hot paths: feed_pipeline, ioc_extractor, url_ops, content_hasher, batch_counters. OnceLock stores &str module name (re-imports on each call). SQL injection patched via DuckDB native parameterized queries. 87 pre-existing compile errors are PyClass Frozen=False mismatches.

**See**: `rust_extensions_code_review.md`, `facts/project/rust_extensions_overview.md`
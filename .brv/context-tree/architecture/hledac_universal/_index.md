---
children_hash: a442d251b5ae268e0ecb668f4e3d47aa0ae17f21f960c681f9b6328b6fccc1dd
compression_ratio: 0.09897675295892866
condensation_order: 1
covers: [10-critical-invariants-govern-system-stability.md, brain_module_organization.md, cache_fragmentation_utils_4_overlapping_implementations.md, circuit_breaker_implementation_issue_41.md, cli_entry_point.md, code_structure_overview.md, context.md, coordinators_architecture.md, critical-invariants-as-cross-domain-enforcement-mechanism.md, critical_invariants.md, deduplication_module_architecture.md, duckdb_kuzu_dual_graph_architecture.md, embedding_pipeline_architecture.md, f186a_canonical_sprint_truth.md, f205b_sidecar_bus_3_stage_execution.md, graphservice_sprint_memory_layer.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, issue_17_bounded_inference_pipeline.md, issue_3_parallel_teardown.md, layer_protocol_and_layerstack.md, lazy-loading-reduces-cold-import-by-98.md, migrate_universal_to_hledac_core.md, performance_configuration.md, project_documentation.md, project_knowledge.md, publicfetcher_module.md, resilience_patterns.md, rust_extensions_architecture.md, rust_extensions_code_review.md, semantic_dedup_architecture.md, semantic_deduplicator.md, sidecar_bus_architecture.md, sidecar_orchestrator_architecture.md, sidecar_protocol_adapters.md, sidecar_protocol_registry.md, sprint-lifecycle-cross-domain-contract.md, sprint-pipeline-orchestrates-8-acquisition-lanes.md, sprint_17_rust_extensions_bug_fixes.md, sprint_data_flow_architecture.md, sprint_lifecycle_pipeline.md, system_architecture.md, vectorstore_architecture.md]
covers_token_total: 21207
summary_level: d1
token_count: 2099
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. This is a knowledge summarization task where I need to condense ~30 child entries about hledac_universal architecture into a structured overview.

Let me analyze the key themes:

1. **Core Architecture**: Sprint-based orchestrator, 8 acquisition lanes, 12-stage lifecycle
2. **Storage Trinity**: DuckDB (canonical), LMDB (WAL), LanceDB (vectors)
3. **ML/Brain Module**: Hermes3 MLX inference, lazy loading, 12 engines
4. **HTTP/Transport**: curl_cffi + httpx + aioquic, Tor/I2P support
5. **Coordinators**: 5-coordinator system (Fetch, Sidecar, Resource, Memory, Execution)
6. **Sidecar System**: 17 adapters, 3-stage execution, F205B
7. **Critical Invariants**: 10 invariants for M1 8GB stability
8. **Performance**: M1 Metal cache, DuckDB limits, memory guards
9. **Rust Extensions**: PyO3 via maturin
10. **Resilience**: Circuit breaker, tenacity retries, fail-safe patterns

Key architectural decisions:
- Entry: `python -m hledac.universal`
- SprintScheduler orchestrates 8 lanes
- DuckDBShadowStore is canonical facts authority
- run_sprint() is sole canonical owner
- PEP 562 facade reduces cold import 9.7s → 150ms
- 3-stage sidecar execution
- Parallel teardown (ISSUE #3)

Let me create a structured summary that preserves these facts while condensing.
</think>

# Architecture Overview: hledac_universal

Hledac Universal is an M1 8GB-optimized OSINT orchestrator using sprint cycles to process queries through tiered acquisition lanes into a DuckDB canonical store. Architecture spans 8 functional layers with 10 critical invariants enforced via GHOST_INVARIANTS CI.

## Core Architecture

**Entry Point**: `python -m hledac.universal` (F186A canonical ownership; deprecated: `core/__main__.py`)

**Sprint Pipeline** (12-stage lifecycle):
1. CLI → `run_sprint()` → `SprintScheduler.run()`
2. 8 acquisition lanes: surface → structured_ti → deep → archive → other + nonfeed fallback (CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH)
3. Advisory runners → Graph accumulation → DuckDB canonical write

**Tier Priority** (high→low): surface → structured_ti → deep → archive → other

## Storage Trinity

- **DuckDB** (canonical facts, 600MB limit, 4 threads): Write ONLY via `async_ingest_findings_batch()`
- **LMDB** (entity WAL, hot-edges cache): Bulk writes via `cursor.putmulti()`
- **LanceDB** (ANN/RAG vectors, 256d MRL text, 1024d image)
- **DuckPGQGraph** (IOC relationships, analytics donor; KuzuDB is truth store per F272)

## Brain Module

**PEP 562 Facade**: Lazy imports reduce cold import from ~9.7s to ~150ms (98% reduction). 12 lazy-loaded engines via `__getattr__`.

**Engines**: Hermes3Engine (L1 canonical), MLX dispatcher, batch scheduler, DSPy optimizer, hypothesis engine. NEREngine requires large RAM. MoE and Distillation are DORMANT.

**BoundedInferencePipeline** (ISSUE-17): 3-stage queue pipeline with `_prep_executor(3) → MLXWorkerThread(1) → _post_executor(2)`. Total queue memory <200KB, M1 8GB safe.

## HTTP/Transport

**Dual HTTP/3 Strategy**:
- curl_cffi (opportunistic): JA3 TLS fingerprinting, HTTP/2
- aioquic (stealth): QUIC for advanced users (~50-80MB resident, cap 3 concurrent)

**Darknet**: Tor (socks5h://127.0.0.1:9050), I2P (socks5://127.0.0.1:7654). QUIC/UDP incompatible with Tor/I2P — H3 auto-skipped.

**Body Hashing**: BLAKE3-64 with NEON acceleration (~5GB/s), xxHash3 fallback, SHA256 final fallback.

## Coordinators (5-Coordinator System)

- **FetchCoordinator**: AIMD window (25 max), BLITZ mode (16 clearnet/8 tor), circuit breaker (FAILURE_THRESHOLD=5, HALF_OPEN_PROBES=3, RECOVERY_TIMEOUT=30s)
- **ResourceCoordinator**: BlitzGCStrategy, AIMD controllers (fetch:25/enrichment:16/extraction:8), BackpressureMonitor
- **MemoryCoordinator**: L1/L2 hierarchy, FAISS semantic, HNSW ANN (m:16, ef:200), LFU eviction
- **SidecarOrchestrator**: Thin facade wiring FindingSidecarBus, SidecarDispatcher, SprintAdvisoryRunner
- **ExecutionCoordinator**: Manages sprint execution

## Sidecar System

**17 Adapters** registered via `@SidecarRegistry.register()` with env gates `HLEDAC_ENABLE_<NAME>=1`:

| Adapter | RAM | Priority | Notes |
|---------|-----|----------|-------|
| Fediverse | 50MB | 6 | |
| DHT | 100MB | 4 | |
| Academic | 80MB | 5 | Skips in aggressive mode |
| LanceDB RAG | 60MB | 7 | |
| DHT Leak Harvest | 150MB | 5 | |
| JA4 Collector | 30MB | 5 | |
| Whois | 30MB | 5 | |
| GitHub Gist | 30MB | 5 | |
| TV News | 40MB | 5 | |
| Leak Sentinel | 30MB | 3 | |
| Passive Fingerprint | 50MB | 4 | |
| Passive Tech Stack | 30MB | 4 | |
| Federated Research | 30MB | 5 | |
| Threat Intel | 40MB | 7 | |
| Shadow Walker | 80MB | 5 | |
| Social Identity Surface | 60MB | 5 | Wiring-only |
| Identity Stitching | 100MB | 5 | Wiring-only |
| Temporal Archaeology | 80MB | 4 | Wiring-only |

**F205B 3-Stage Execution**:
- Stage 1 (light): leak_sentinel, passive_fingerprint, evidence_triage, temporal_archaeology
- Stage 2 (correlation): exposure_correlator, identity_stitching, sprint_diff, rir_correlator, social_identity_surface, wayback_diff
- Stage 3 (derived): kill_chain_tagging, embedding

**ISSUE #3 Parallel Teardown**: 5-branch concurrent teardown (30-90s → 5-15s, 5-7x speedup).

## 10 Critical Invariants (M1 8GB Stability)

| ID | Rule | Enforcement |
|----|------|-------------|
| I1 | asyncio.gather ALWAYS with return_exceptions=True | CI + _check_gathered() |
| I2 | mx.eval([]) before mx.metal.clear_cache() | CI |
| I3 | No time.sleep() in async — use asyncio.sleep() | CI |
| I4 | No asyncio.run() in ThreadPoolExecutor | CI |
| I5 | DuckDB ONLY via async_ingest_findings_batch() | duckdb_store |
| I6 | LMDB bulk ONLY via cursor.putmulti() | CI |
| I7 | URL dedup ONLY via RotatingBloomFilter | CI |
| I8 | M1 Metal cache: dynamic formula (1GiB ceiling) | resource_governor |
| I9 | Sidecars return [] on errors, never throw | CI |
| I10 | No bare except — always except Exception: | CI |

## Performance Configuration

**M1 Metal Cache**: `min(max(available*0.2, 512MiB), 1GiB)` ceiling; wired limit 1.5GiB

**Memory Guards**:
- RSS 5.5GiB: blocks aioquic
- RSS 6.0GB: blocks semantic dedup
- RSS 6.5GB: blocks embeddings

**DuckDB**: 600MB limit, 4 threads, chunk size 500, concurrency 2

**Hot-Path DTOs**: msgspec.Struct with frozen=True, gc=False

**Dedup**: LRU(16) advisory dedup with FIFO eviction; Semantic: 256d embeddings, 0.85-0.95 cosine threshold, Rust SimHash

## Resilience Patterns

- **CircuitBreaker**: AtomicU32 + DashMap in Rust, states CLOSED(0)/OPEN(1)/HALF_OPEN(2)
- **Tenacity**: Exponential backoff with jitter, BLITZ-15 cap at 1.0s
- **Tor Scaling**: 2.0x timeout multiplier
- **Fail-Safe**: Sidecars return empty list, embeddings return zero vectors
- **Advisory Locking**: Sprint concurrency control

## Rust Extensions (PyO3/maturin)

Modules: feed_pipeline, ioc_extractor, url_ops (xxh3_64), content_hasher (BLAKE3+xxHash), batch_counters, mlxcel (external binary, ~1GB RSS savings)

Build: `maturin develop --release`

## Deduplication Architecture

**4 Strategies** in `utils/deduplication.py` (1225 lines):
1. **Semantic**: 256d embeddings, cosine similarity ≥0.85 → remove
2. **Content**: MinHash (128, threshold 0.8), SHA256
3. **Metadata**: Title (0.4), URL (0.3), threshold 0.95
4. **Hybrid**: Combined scoring

**Rust LSH**: O(1) near-duplicate detection, <50ms for 10k signatures

**Memory**: Two-layer cache (float16 L1 + np.memmap L2), LMDB persistence (256MB limit)

## Layer Protocol

**LayerStack**: Mount/unmount/on_event interface with timeouts (30s/10s/30s). UDS IPC via msgspec.msgpack for zero-copy serialization.

**Sprint Flags**: no_communication, no_stealth, no_ghost, no_coordination control layer injection.

## Telemetry

OTel lazy-init via python_otel_bridge.py. M1 disables asyncio instrumentation (5-15% overhead). Instrumentation wraps DuckDB execute() and LMDB env.begin().

## CLI Exit Codes

0=success, 1=runtime error, 2=config error, 3=programmer error, 130=SIGINT

---

**Key Relationships**:
- `sprint_lifecycle_pipeline.md` → `duckdb_store/context.md` (canonical write contract)
- `10-critical-invariants-govern-system-stability.md` → `critical_invariants.md` (I1-I10)
- `brain_module_organization.md` → `lazy-loading-reduces-cold-import-by-98.md`
- `sidecar_bus_architecture.md` → `sidecar_protocol_registry.md` (17 adapters)
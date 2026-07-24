---
children_hash: 85931d242e68d952f7de64a92e878c1b20219240032f06e124eeff888c16d682
compression_ratio: 0.2072072072072072
condensation_order: 1
covers: [10-critical-invariants-govern-system-stability.md, brain_module_organization.md, context.md, critical-invariants-as-cross-domain-enforcement-mechanism.md, critical_invariants.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, layer_protocol_and_layerstack.md, lazy-loading-reduces-cold-import-by-98.md, project_documentation.md, rust_extensions_code_review.md, sidecar_protocol_registry.md, sprint-lifecycle-cross-domain-contract.md, sprint-pipeline-orchestrates-8-acquisition-lanes.md, sprint_lifecycle_pipeline.md]
covers_token_total: 7659
summary_level: d1
token_count: 1587
type: summary
---
<think>
The user wants me to create a structural summary at the d1 (domain) level from the provided knowledge entries. Let me analyze these entries and create a coherent overview.

The entries are all related to the Hledac Universal project, specifically the architecture domain. Let me identify the key themes:

1. **10 Critical Invariants** - A set of 10 stability rules for M1 8GB, enforced across domains
2. **Brain Module Organization** - PEP 562 facade with lazy loading (9.7s → 150ms), 12 engines
3. **Critical Invariants** - Detailed list of I1-I10 with async patterns, MLX cache, DuckDB/LMDB writes
4. **Hledac Universal Architecture** - Sprint-based orchestrator with DuckDB shadow store
5. **HTTP/3 Lane and Public Fetcher** - Dual strategy (curl_cffi + aioquic), Tor/I2P, URL classification
6. **Layer Protocol and LayerStack** - Event-driven architecture with UDS IPC
7. **Lazy Loading** - 98% reduction in cold import time
8. **Project Documentation** - Comprehensive overview with storage trinity
9. **Rust Extensions Code Review** - 4 issues analyzed
10. **Sidecar Protocol Registry** - 17 adapters with memory admission
11. **Sprint Lifecycle Pipeline** - 12 stages, 8 acquisition lanes
12. **Cross-Domain Contracts** - Critical invariants enforced across domains

Let me create a structural summary that preserves key facts, relationships, and patterns.
</think>

# architecture/hledac_universal — Structural Summary

## Core Architecture

**Hledac Universal** is an OSINT orchestrator running on M1 8GB MacBook Air. Entry point: `python -m hledac.universal --sprint "QUERY"` via `runtime/sprint_entrypoint.py` (the `python -m hledac.universal --sprint` form is deprecated).

Core modules: `runtime/`, `brain/`, `fetching/`, `knowledge/`, `transport/`, `layers/`, `rust_extensions/src/`

## Sprint Lifecycle Pipeline

12-stage lifecycle (NOT_SCHEDULED→ACCEPTED) via `SprintScheduler.run()`:
1. run_prelude
2. run_acquisition_lanes (8 lanes)
3. advisory runner
4. graph accumulation
5. DuckDB canonical write

**8 Acquisition Lanes** with tier priority (High→Low):
- surface → structured_ti → deep → archive → other
- Nonfeed fallback: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH

**Advisory Log**: LRU(16) with O(1) hit/miss, FIFO eviction, no promotion on hit.

## 10 Critical Invariants (M1 8GB Stability Rules)

Enforced cross-domain: authored in architecture, enforced by duckdb_store, resource_governor, and CI.

| # | Invariant | Enforcement |
|---|-----------|-------------|
| I1 | No bare `except:` — always `except Exception:` | CI |
| I2 | `asyncio.gather` ALWAYS with `return_exceptions=True` | CI |
| I3 | `mx.eval([])` before `mx.metal.clear_cache()` | CI |
| I4 | No `time.sleep()` in async — use `asyncio.sleep()` | resource_governor |
| I5 | No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()` | resource_governor |
| I6 | DuckDB ONLY via `async_ingest_findings_batch()` | duckdb_store |
| I7 | LMDB bulk ONLY via `cursor.putmulti()` | duckdb_store |
| I8 | URL dedup ONLY via `RotatingBloomFilter` | duckdb_store |
| I9 | M1 Metal cache: `min(max(available*0.2, 512MiB), 1GiB)` ceiling | resource_governor |
| I10 | Sidecars return `[]` on errors, never throw | CI |

## Brain Module (ML Inference)

PEP 562 `__getattr__` facade reduces cold import from ~9.7s to ~150ms (98% reduction). 12 lazy-loaded engines defer import until first attribute access.

- **Canonical engine**: Hermes3Engine (L1)
- **M1 lifecycle**: `ModelManager` handles Metal memory
- **MLX dispatch**: `MLXBatchedExecutor` (P0-2), `MLXWorkerThread` (P0-3)
- **LoRA support**: `mlx_lm.lora` deferred import (LoRA-1)
- **LRU dedup**: Advisory uses LRU(16) with FIFO semantics

## Transport Layer

**HTTP/3 Dual Strategy**:
- Opportunistic: `curl_cffi` with JA3 fingerprinting, HTTP/2 session (200 max connections)
- Stealth: `aioquic` with QUIC, ~50-80MB resident, concurrency capped at 3

**Dark Web**: Tor (.onion, circuit renewal every 10 requests) and I2P (.i2p, .b32.i2p). QUIC/UDP incompatible with Tor/I2P — H3 auto-skipped for dark web.

**Memory guard**: Blocks aioquic at 5.5 GiB RSS.

## Layer Protocol

`layers/layer_protocol.py` defines runtime-checkable Protocol with mount/unmount/on_event. LayerStack manages lifecycle and event propagation in mount order.

Timeouts: mount 30s (rollback on error), unmount 10s (best-effort), on_event 30s per layer. UDS Protocol for zero-copy IPC via msgspec.msgpack.

## Sidecar Protocol Registry

17 adapters with memory admission via `GovernorDecision.sidecar_admission`. Priority 7 adapters (lancedb_rag, threat_intel) run early.

RAM budgets: range from 30MB (leak_sentinel, passive_tech_stack, github_gist, whois) to 100MB (dht, identity_stitching, shadow_walker).

Lazy imports for expensive modules (GLiNER, cryptography) avoid 200+ms boot cost.

## Storage Trinity

| Store | Purpose | Config |
|-------|---------|--------|
| DuckDB | SQL/canonical facts | 600MB limit, 4 threads, chunk=500 |
| LMDB | Key-value/entity WAL | cursor.putmulti() for bulk |
| LanceDB | ANN/RAG vectors | |

DuckDBShadowStore is canonical facts authority. Arrow for zero-copy ingest.

## Rust Extensions

Key files: `rust_extensions/src/{adaptive_scheduler,async_query,pool_run,bloom,dedup_bloom,graph_cache,lancedb_bridge}.rs`

Known issues fixed: OnceLock compile error (store `&str` module name), SQL injection (use parameterized queries). GIL safety confirmed safe (pyfunction macro holds GIL). 87 pre-existing Frozen=False mismatches are unrelated.

## Memory Configuration

- DuckDB: 600MB, 4 threads
- Metal cache: dynamic formula, 1GiB ceiling on 8GB M1
- aioquic block: 5.5 GiB RSS
- msgspec.Struct hot-path DTOs with `frozen=True, gc=False`

## Drill-Down References

- `critical_invariants.md` — Full I1-I10 rule definitions
- `sprint_lifecycle_pipeline.md` — 12-stage lifecycle details
- `brain_module_organization.md` — 12 engines and lazy loading
- `http_3_lane_and_public_fetcher.md` — Transport details
- `sidecar_protocol_registry.md` — 17 adapters
- `rust_extensions_code_review.md` — Code review findings
- `lazy-loading-reduces-cold-import-by-98.md` — Import optimization synthesis
- `critical-invariants-as-cross-domain-enforcement-mechanism.md` — Enforcement cross-domain synthesis
- `sprint-pipeline-orchestrates-8-acquisition-lanes.md` — Pipeline synthesis
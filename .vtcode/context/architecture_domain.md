# Domain: architecture

## Overview

Documents the system architecture for hledac.universal — an OSINT orchestrator that combines Python for orchestration with Rust extensions for performance-critical operations. Key architectural patterns include the 3-lane HTTP transport strategy, DuckDB shadow stores, adaptive resource management, and an iterative sprint lifecycle.

## Key Architectural Decisions

**3-Lane HTTP Transport**: Implements layered fallback via `Http3Lane` trait. Primary cache lane uses Redis with sentinel support; secondary CDN lane handles static assets; tertiary public fetcher lane aggregates multiple sources. Circuit breaker adapts per-lane capacity based on success rates.

**Rust Extensions via PyO3/Maturin**: Critical path regex IOC extraction in Rust (`rust.ioc.extract_iocs_flat`) for performance. DuckDB integration via `duckdb-engine`. LMDB via `lmdb`.

**Storage Trinity**: DuckDB (SQL canonical findings) + LMDB (key-value metadata) + LanceDB (ANN embeddings).

**Sprint Lifecycle Pipeline**: Iterative sprint cycles that process search queries and return structured IoC data. Each sprint coordinates prelude → acquisition lanes → advisory runner → entity upsert → winddown.

**M1 8GB Resource Governor**: Dynamic Metal cache limits (512MiB–1GiB), bounded concurrency, soft ceiling at 5.5GiB RAM.

---

## Child Entries

### context.md
Sprint pipeline flow, storage trinity, brain layer (MLX/Hermes3), key seams, IOC dual-engine architecture.

### critical_invariants
Top 10 invariants including: `asyncio.gather` with `return_exceptions=True`, `mx.eval([])` before `mx.metal.clear_cache()`, no `time.sleep()` in async code, DuckDB write via `async_ingest_findings_batch()`, LMDB bulk write via `putmulti()`, rotating bloom filter for URL dedup, M1 Metal cache dynamic limits, fail-safe sidecars, no bare `except:`.

### http_3_lane_and_public_fetcher
HTTP/3 lane (`transport/http3_lane.py`) with two strategies: `curl_cffi_opportunistic` (default, Alt-Svc driven) and `aioquic_stealth` (opt-in `[http3]` extra). Bounded LRU cache (1024 entries), concurrency max 3, 8s timeout, 24h TTL, 5.5GiB RSS block. Fail-soft invariants: returns `None` on error, cooperative `CancelledError` re-raised.

### sprint_lifecycle_pipeline
```
CLI / __main__.py → run_sprint()
  ├── SprintScheduler.run()
  │   ├── run_prelude() — metrics init
  │   ├── run_acquisition_lanes() — CT, public, passive DNS
  │   ├── run_advisory_runner() — sidecars (IPFS, BGP, dark pivots)
  │   ├── _accumulate_findings_to_graph() — entity upsert
  │   ├── run_winddown() — export, cleanup
  │   └── SprintSchedulerResult
  └── DuckDBShadowStore.async_ingest_findings_batch()
      ├── LMDB metadata (putmulti)
      └── DuckDB canonical records
```

### transport_layers/http_3_lane_implementation
Curl CFFI prewarm pool (4-slot ring buffer, ~60MB resident) + conditional cache (LMDB 16MB, zstd compressed, 304 support). Speculative Alt-Svc probe for H3 LRU priming. Opt-out via env vars.

### duckdb_shadow_stores
DuckDB shadow store (`knowledge/duckdb_store.py`) for canonical findings. In-process mode (default, saves ~200MB RAM). Thread count set to 2 (optimal for thread-local conn bottleneck). Arrow zero-copy ingest enabled by default.

### resource_governor
`M1ResourceGovernor` in `core/resource_governor.py`. Dynamic Metal cache: `min(max(available*0.2, 512MiB), 1GiB)`. Wired limit 1.5GiB. KV cache: `kv_bits=4`, `max_kv_size=8192`. Soft ceiling 5.5GiB → hard cap fetch concurrency.

### sprint_lifecycle_and_testing
Probe tests in `tests/probe_p12_http3_lane/` (48 hermetic tests) and `tests/probe_p14_prewarm_conditional/` (25 hermetic tests). F265B curl CFFI prewarm + conditional cache coverage.

---

## Key Entry Points

| Entry | Location |
|-------|----------|
| SprintScheduler | `runtime/sprint_scheduler.py` |
| DuckDBShadowStore | `knowledge/duckdb_store.py` |
| FetchCoordinator | `coordinators/fetch_coordinator.py` |
| DuckPGQGraph | `knowledge/graph_service.py` |
| Hermes3Engine | `pipeline/live_public_pipeline.py` |
| Http3Lane | `transport/http3_lane.py` |
| M1ResourceGovernor | `core/resource_governor.py` |

---

## Feature Flags Relevant to Architecture

| Flag | Default | Description |
|------|---------|-------------|
| HLEDAC_ENABLE_HTTPX_H3 | 1 | HTTP/3 opportunistic upgrade |
| HLEDAC_DUCKDB_INPROCESS | 1 | DuckDB in-process mode (saves ~200MB) |
| HLEDAC_DUCKDB_THREADS | 2 | DuckDB thread count |
| HLEDAC_CURL_CFFI_PREWARM | 1 | Curl CFFI session prewarm |
| HLEDAC_CONDITIONAL_CACHE | 1 | Conditional cache for SERP |
| HLEDAC_ENABLE_DSPY | 0 | DSPy compiled hypothesis generation |

---

## Hardware Constraints (M1 8GB UMA)

- **RAM budget:** macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB = **6.25GB max**
- **Metal cache limit:** 1.5 GiB
- **KV cache:** `kv_bits=4`, `max_kv_size=8192`
- **Soft ceiling:** 5.5 GiB → hard cap fetch concurrency

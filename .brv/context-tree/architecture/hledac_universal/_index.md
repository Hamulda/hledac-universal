---
children_hash: c96c689857f8febf9d0a875c108c4795ee7149ad1a7c71dfdb824dfe9645a8dc
compression_ratio: 0.21574553828439838
condensation_order: 1
covers: [10-critical-invariants-govern-system-stability.md, brain_module_organization.md, context.md, critical_invariants.md, hledac_universal_architecture.md, http_3_lane_and_public_fetcher.md, layer_protocol_and_layerstack.md, lazy-loading-reduces-cold-import-by-98.md, project_documentation.md, rust_extensions_code_review.md, sidecar_protocol_registry.md, sprint-pipeline-orchestrates-8-acquisition-lanes.md, sprint_lifecycle_pipeline.md]
covers_token_total: 6948
summary_level: d1
token_count: 1499
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries about hledac_universal architecture. I need to synthesize and condense the information while preserving key facts, architectural decisions, and relationships.

Let me analyze the entries:

1. **10-critical-invariants-govern-system-stability** - 10 critical invariants (I1-I10) for M1 8GB stability
2. **brain_module_organization** - PEP 562 facade, lazy loading, 12 engines
3. **context.md** - Overview of HTTP/3, curl_cffi, aioquic, M1 memory
4. **critical_invariants.md** - 10 invariants details
5. **hledac_universal_architecture** - Sprint-based async orchestrator, DuckDB, tiered sources
6. **http_3_lane_and_public_fetcher** - Dual HTTP/3 strategy, Tor/I2P, URL classification
7. **layer_protocol_and_layerstack** - Layer protocol, UDS IPC
8. **lazy-loading-reduces-cold-import-by-98** - PEP 562 + conftest preloading
9. **project_documentation** - Comprehensive project docs
10. **rust_extensions_code_review** - Rust extensions review
11. **sidecar_protocol_registry** - 17 sidecar adapters
12. **sprint-pipeline-orchestrates-8-acquisition-lanes** - 8 acquisition lanes
13. **sprint_lifecycle_pipeline** - 12-stage sprint lifecycle

Key themes:
- M1 8GB memory constraints
- Sprint-based async orchestration
- DuckDB shadow store
- 10 critical invariants
- Lazy loading optimization
- HTTP/3 dual strategy
- 17 sidecar adapters

Let me create a concise structural summary.
</think>

# Hledac Universal Architecture

## System Overview

Hledac Universal is an M1 8GB-optimized OSINT orchestrator that processes queries through sprint cycles, using MLX/Hermes3 for inference and a storage trinity (DuckDB/LMDB/LanceDB) for structured data.

## Core Architecture

**Entry Point**: `python -m hledac.universal --sprint "QUERY"` via `runtime/sprint_entrypoint.py` (replaces deprecated core/__main__.py)

**Sprint Lifecycle Pipeline** (12 stages, NOT_SCHEDULED → ACCEPTED):
1. CLI → run_sprint → SprintScheduler.run()
2. run_prelude → run_acquisition_lanes → advisory runners → graph accumulation → DuckDB async_ingest_findings_batch

**8 Acquisition Lanes** with tier priority (High→Low):
- surface → structured_ti → deep → archive → other
- Nonfeed fallback: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH

**Advisory Log**: LRU(16) with FIFO eviction, no promotion on hit

## 10 Critical Invariants (M1 8GB Stability)

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| I1 | asyncio.gather ALWAYS with return_exceptions=True | CI |
| I2 | mx.eval([]) before mx.metal.clear_cache() | CI |
| I3 | No time.sleep() in async — use asyncio.sleep() | CI |
| I4 | No asyncio.run() in ThreadPoolExecutor — use loop.run_until_complete() | CI |
| I5 | DuckDB ONLY via async_ingest_findings_batch() | CI |
| I6 | LMDB bulk ONLY via cursor.putmulti() | CI |
| I7 | URL dedup ONLY via RotatingBloomFilter | CI |
| I8 | M1 Metal cache: dynamic formula (min(max(available*0.2, 512MiB), 1GiB)) | CI |
| I9 | Sidecars return [] on errors, never throw | CI |
| I10 | No bare except — always except Exception: | CI |

## Brain Module

**PEP 562 Facade** (`brain/__init__.py`): 12 lazy-loaded engines reduce cold import from ~9.7s to ~150ms (98% reduction). Hermes3Engine is L1 canonical. ModelManager handles M1 lifecycle.

**Engines**: deephermes3, MLX dispatcher, batch scheduler, causal, distillation, DSPy, CoreML, ANE, MLX embeddings, NER (requires large RAM), MoE, Distillation (DORMANT)

## Transport Layer

**HTTP/3 Dual Strategy** (`transport/http3_lane.py`):
- **Opportunistic**: curl_cffi with JA3 fingerprinting (Chrome 124, Firefox 133, Safari 17)
- **Stealth**: aioquic QUIC (~50-80 MB resident, concurrency capped at 3)

**Public Fetcher** (`fetching/public_fetcher.py`): Tor/I2P session management, Rust URL classification (xxh3_64 cache key), BLAKE3-64 body hashing with NEON acceleration, HTTP/2 (200 max connections, 100 keepalive)

**Dark Web**: .onion/.i2p/.b32.i2p TLDs detected; QUIC/UDP incompatible with Tor/I2P (H3 auto-skipped)

## Storage Trinity

| Store | Purpose | Config |
|-------|---------|--------|
| DuckDB | SQL/canonical facts | 600MB limit, 4 threads, chunk=500 |
| LMDB | Key-value/entity WAL | cursor.putmulti() for bulk |
| LanceDB | ANN/RAG embeddings | 60MB sidecar budget |

## Sidecar Protocol Registry

17 adapters with priority-based execution and memory admission:

| Priority 7 (Early) | Priority 6 | Priority 5 | Priority 4 | Priority 3 |
|-------------------|------------|------------|------------|------------|
| lancedb_rag, threat_intel | fediverse | academic, tvnews, federated_research, social_identity_surface, github_gist, whois, shadow_walker | dht, alt_protocols, passive_fingerprint, passive_tech_stack, temporal_archaeology | leak_sentinel |

Academic adapter skips in aggressive mode (~50s savings).

## Memory & Resource Governance

**M1 8GB Budget Allocation**:
- DuckDB: 600MB
- Metal cache: dynamic (512MiB–1GiB), wired hard limit 1.5 GiB
- aioquic: blocked at 5.5 GiB RSS
- KV cache: 4-bit quantization, max 8192 tokens

**Pre-flight Guards**: Abort if active-window budget < 30s; minimum sprint = windup_lead + 30s

## Layer Protocol

**LayerStack** (`layers/layer_protocol.py`): Mount/unmount lifecycle, event propagation in mount order, UDS IPC via msgspec.msgpack

**Timeouts**: mount 30s (rollback on error), unmount 10s (best-effort), on_event 30s per layer

## Key Performance Patterns

- msgspec.Struct with frozen=True, gc=False for hot-path DTOs
- orjson fallback for JSON serialization
- LRU(512) with O(1) OrderedDict eviction
- pytest-timeout 30s default, asyncio_mode=auto
- Rust extensions: OnceLock<&str> pattern, parameterized SQL queries

## Related Knowledge

- **facts/project/technology_stack** — Full tech stack details
- **data/duckdb_store** — DuckDB shadow store internals
- **memory/resource_governor** — M1 memory optimization patterns
- **testing/conftest** — Pytest infrastructure and fixtures
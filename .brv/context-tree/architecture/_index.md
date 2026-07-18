---
children_hash: 975666575c685a5800cb705bbb4183ab0e2399dba0ec15c4607ac63a8623dae3
compression_ratio: 0.7711187214611872
condensation_order: 2
covers: [context.md, hledac_universal/_index.md]
covers_token_total: 1752
summary_level: d2
token_count: 1351
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries about hledac_universal architecture. This is the final iteration, so I need to provide my best synthesized summary based on the extensive information provided.

Let me analyze the key themes and synthesize a concise but comprehensive summary:

1. **System Overview**: M1 8GB-optimized OSINT orchestrator with sprint-based query processing
2. **Core Architecture**: Entry point, 12-stage sprint lifecycle, 8 acquisition lanes
3. **10 Critical Invariants**: Table of key stability rules
4. **Brain Module**: PEP 562 lazy loading, 12 engines
5. **Transport Layer**: HTTP/3 dual strategy (curl_cffi + aioquic), Tor/I2P
6. **Storage Trinity**: DuckDB, LMDB, LanceDB
7. **Sidecar Protocol Registry**: 17 adapters with priority system
8. **Memory Governance**: M1 8GB budget allocation
9. **Layer Protocol**: LayerStack with UDS IPC
10. **Performance Patterns**: msgspec, orjson, LRU
11. **Related Knowledge**: Cross-references

I need to synthesize this into a cohesive architectural narrative that captures the essence of the system without overwhelming detail.
</think>

# Hledac Universal Architecture

## System Overview

Hledac Universal is an M1 8GB-optimized OSINT orchestrator that processes queries through sprint cycles, using MLX/Hermes3 for inference and a storage trinity (DuckDB/LMDB/LanceDB) for structured data. It balances memory constraints with high-throughput acquisition through strict invariant enforcement and tiered lane prioritization.

## Core Architecture

**Entry Point**: `python -m hledac.universal --sprint "QUERY"` via `runtime/sprint_entrypoint.py`

**Sprint Lifecycle Pipeline** (12 stages, NOT_SCHEDULED → ACCEPTED): CLI → run_sprint → SprintScheduler.run() → run_prelude → run_acquisition_lanes → advisory runners → graph accumulation → DuckDB async_ingest_findings_batch

**8 Acquisition Lanes** with tier priority (High→Low): surface → structured_ti → deep → archive → other, with nonfeed fallback: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH

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

**Engines**: deephermes3, MLX dispatcher, batch scheduler, causal, distillation, DSPy, CoreML, ANE, MLX embeddings, NER, MoE, Distillation (DORMANT)

## Transport Layer

**HTTP/3 Dual Strategy** (`transport/http3_lane.py`): curl_cffi for opportunistic JA3 fingerprinting (Chrome 124, Firefox 133, Safari 17) and aioquic QUIC for stealth (~50-80 MB resident, concurrency capped at 3). Dark web (.onion/.i2p/.b32.i2p) detected but QUIC/UDP incompatible with Tor/I2P.

**Public Fetcher**: Tor/I2P session management, Rust URL classification with xxh3_64 cache key, BLAKE3-64 body hashing with NEON acceleration, HTTP/2 with 200 max connections and 100 keepalive.

## Storage Trinity

| Store | Purpose | Config |
|-------|---------|--------|
| DuckDB | SQL/canonical facts | 600MB limit, 4 threads, chunk=500 |
| LMDB | Key-value/entity WAL | cursor.putmulti() for bulk |
| LanceDB | ANN/RAG embeddings | 60MB sidecar budget |

## Sidecar Protocol Registry

17 adapters with priority-based execution and memory admission (Priority 7: lancedb_rag, threat_intel → Priority 3: leak_sentinel). Academic adapter skips in aggressive mode (~50s savings).

## Memory & Resource Governance

**M1 8GB Budget**: DuckDB 600MB, Metal cache dynamic (512MiB–1GiB, wired hard limit 1.5 GiB), aioquic blocked at 5.5 GiB RSS, KV cache 4-bit quantization max 8192 tokens.

**Pre-flight Guards**: Abort if active-window budget < 30s; minimum sprint = windup_lead + 30s

## Layer Protocol & Performance

**LayerStack** (`layers/layer_protocol.py`): Mount/unmount lifecycle with UDS IPC via msgspec.msgpack. Mount 30s timeout (rollback on error), unmount 10s (best-effort).

**Hot-path Patterns**: msgspec.Struct with frozen=True/gc=False, orjson fallback, LRU(512) O(1) OrderedDict eviction, pytest-timeout 30s default, asyncio_mode=auto. Rust extensions use OnceLock<&str> pattern with parameterized SQL queries.

## Key Architectural Decisions

- Sprint-based async orchestration enables adaptive query processing
- Lazy loading via PEP 562 achieves 98% cold import reduction
- 10 critical invariants enforced via CI for M1 stability
- Dual HTTP/3 strategy balances stealth and fingerprinting evasion
- Priority-based sidecar registry with memory admission gates
- Tiered storage trinity optimizes for different access patterns
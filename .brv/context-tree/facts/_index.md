---
children_hash: 9b32c6ecc29b70727de3bbbdc1dd65689a6e1e1f669cef65b8a281576a33c8c6
compression_ratio: 0.565843621399177
condensation_order: 2
covers: [project/_index.md]
covers_token_total: 1458
summary_level: d2
token_count: 825
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries. I need to condense the child entries into a structured overview while preserving key facts, relationships, and patterns.

Let me analyze the entries and create a comprehensive domain-level summary.

The entries cover:
1. Project Configuration & Environment
2. Technology Stack
3. Rust Extensions Architecture
4. Hashing Infrastructure
5. Concurrency Patterns
6. Major Issue Fixes
7. PEP 734 Isolation Infrastructure
8. Critical Invariants
9. Async Concurrency
10. Testing & Deployment

I'll create a clean, structured markdown summary that captures all the key information while being concise.
</think>

# facts/project — Project Configuration & Infrastructure

## Overview
Hledac Universal is an OSINT orchestrator for MacBook Air M1 8GB UMA. Core architecture: Python 3.14 + Rust PyO3 extensions + DuckDB/LanceDB/LMDB storage + MLX inference.

## Key Technology Decisions
- **Rust/PyO3**: 30+ modules in `rust_extensions/src/` — consistent FFI strategy for all hot paths
- **MPSC Pool**: crossbeam-channel bounded (2048 × 512B ≈ 1 MiB), ARM LSE atomics ~2-5ns/send
- **Hashing**: `utils/hashing.py` facade with xxh3_64_hex, batch_xxh3_64_hex (10× via NEON SIMD)
- **Storage Trinity**: DuckDB (SQL), LMDB (key-value), LanceDB (ANN embeddings)

## Configuration Constants
- DuckDB: 600MB limit, 1GB max temp, 2 threads (M1 optimal)
- Shodan: 36 req/10s rate limit
- Evidence log: timeout mismatch (1000ms configured vs 30000ms actual)
- M1 8GB: ~6.25GB UMA hard cap, 3.8 GiB swap threshold

## Environment Gates (HLEDAC_*)
14+ gates control: Arrow ingest (ON), DuckDB query cache (OFF), RG ratios (0=absolute GiB), DuckDB in-process (saves ~200MB)

## Critical Invariants
1. DuckDB writes ONLY through `async_ingest_findings_batch()`
2. LMDB bulk via `cursor.putmulti()` — never per-item
3. `mx.eval([])` before `mx.metal.clear_cache()`
4. `asyncio.gather` always with `return_exceptions=True`
5. No `time.sleep()` in async — use `asyncio.sleep()`
6. Use `RotatingBloomFilter` not `ScalableBloomFilter`
7. CAPS.require() for curl_cffi availability checks

## Major Issue Fixes

| Issue | Problem | Solution |
|-------|---------|----------|
| ISSUE-0.2 | httpx fallback without JA3 | CAPS.require(CURL_CFFI) with FAIL-FAST |
| ISSUE-2.3 | 25× context-switch overhead from double threading | rayon_dispatch.rs with crossbeam-channel, ~5μs submission |
| ISSUE-3.2 | DashMap + PyO3 GIL segfaults | parking_lot::RwLock + AHashMap migration |
| ISSUE-5.2 | HNSW quality degradation | Adaptive expansion_add (200/300), native batch API |

## Async Concurrency
`parallel()` in `utils/async_helpers.py` replaces deprecated bounded_gather. 4 exception policies: raise/first/collect/log. I6: CancelledError always re-raised; I7: BaseException re-raised; I8: Exception routed per policy.

## PEP 734 Isolation
`DUCKDB_WRITE` env var gates isolation; `run_db_write()` via ThreadPoolExecutor with stateful self closures pickled via `cloudpickle.dumps()`.

## Testing & Deployment
- pytest: asyncio auto mode, session-scoped loop, `--timeout=30`
- Test baseline: ~89 (sprint_scheduler), ~64 (rust_extensions), 200+ (F206)
- Exit codes: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT
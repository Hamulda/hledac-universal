---
children_hash: d810a210871f3d84ea5482e8bab8bf863a5f23671367273465481d2bd194284d
compression_ratio: 0.16025967413441955
condensation_order: 1
covers: [configuration_constants.md, context.md, environment-gates-control-feature-activation.md, hashing_facade_issue_2.md, hledac_universal_claude_md.md, issue_0_2_curl_cffi_caps_invariants.md, issue_2_3_rayon_dispatch_channel_fix.md, issue_3_2_federatedqtable_parking_lot_migration.md, issue_5_2_rag_hnsw_quality_fix.md, issue_g2_pep_734_isolation_infrastructure.md, parallel_async_helper.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-unifies-all-hot-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 7856
summary_level: d1
token_count: 1259
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries. I need to:

1. Condense the child entries into a structured overview
2. Preserve key facts, relationships, and patterns
3. Reference entry names for drill-down
4. Target ~20-30% compression
5. Preserve entity names, file paths, API signatures, architectural decisions, key relationships
6. Discard verbose examples, repeated explanations, conversational filler
7. Output clean structured markdown

Let me analyze the entries:

**Core Theme**: Hledac Universal project - an OSINT orchestrator for M1 Apple Silicon with Rust/PyO3 extensions, DuckDB storage, and MLX ML inference.

**Key Categories I can identify:**

1. **Project Configuration & Environment** (configuration_constants.md, environment-gates-control-feature-activation.md)
   - DuckDB memory limits, feature gates via HLEDAC_* env vars
   - Evidence log timeout mismatch (1000ms vs 30000ms)

2. **Technology Stack** (technology_stack.md)
   - Python 3.14, MLX, DuckDB, LanceDB, LMDB
   - curl-cffi, PyO3/Rust, pytest

3. **Rust Extensions Architecture** (rust_extensions_overview.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-unifies-all-hot-paths.md)
   - 30+ Rust modules via PyO3
   - MPSC pool, bloom filters, hashing, IOC extraction
   - crossbeam-channel MPSC with ARM LSE atomics
   - Memory budget: 2048 × 512B ≈ 1 MiB

4. **Hashing Infrastructure** (hashing_facade_issue_2.md, xxhash_rust_implementation.md)
   - xxhash-rust for performance-critical operations in utils/hashing.py, routing 7 calls from deduplication.py and 1 from url_dedup.py through the facade while preserving crypto-grade hashing elsewhere

5. **Concurrency Patterns** (parallel_async_helper.md)
   - parallel() helper supporting 4 exception policies and I6/I7/I8 invariants

6. **Issue Fixes**:
   - ISSUE-0.2: CAPS-based curl_cffi availability with FAIL-FAST fallback
   - ISSUE-2.3: Rayon dispatch channel to reduce context-switch overhead from 25× to single-digit microseconds
   - ISSUE-3.2: Migrating from DashMap to parking_lot::RwLock to resolve GIL conflicts
   - ISSUE-5.2: HNSW quality adaptive expansion_add

7. **Isolation Infrastructure** (issue_g2_pep_734_isolation_infrastructure.md)
   - DUCKDB_WRITE + run_db_write() with ThreadPoolExecutor handling stateful self closures via pickle

8. **Project Documentation** (hledac_universal_claude_md.md)
   - 10 critical invariants governing the system

, 50+ feature flags, and pre-flight guards

Now I'll organize this into a structured domain-level summary.
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

## Testing & Deployment
- pytest: asyncio auto mode, session-scoped loop, `--timeout=30`
- Test baseline: ~89 (sprint_scheduler), ~64 (rust_extensions), 200+ (F206)
- Exit codes: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT
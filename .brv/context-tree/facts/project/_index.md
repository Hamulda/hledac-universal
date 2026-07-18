---
children_hash: faba833080a0189c5368783dbf1a83b0b03b9ac278cd26835d8f67cba0fdc850
compression_ratio: 0.2879543834640057
condensation_order: 1
covers: [configuration_constants.md, environment-gates-control-feature-activation.md, hashing_facade_issue_2.md, hledac_universal_claude_md.md, issue_g2_pep_734_isolation_infrastructure.md, parallel_async_helper.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-unifies-all-hot-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 5612
summary_level: d1
token_count: 1616
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. These are all in the `facts/project` domain based on the paths in the entries. I need to:

1. Condense the child entries into a structural overview
2. Preserve key facts, relationships, and patterns
3. Reference child entry names for drill-down
4. Target ~20-30% compression
5. Output clean structured markdown

Let me analyze the entries:

1. **configuration_constants.md** - DuckDB memory limits, shodan rate, env feature gates, evidence_log timeout
2. **environment-gates-control-feature-activation.md** - Synthesis on 14+ HLEDAC_* environment variables
3. **hashing_facade_issue_2.md** - ISSUE #2: Centralized hashing facade with xxh3/batch_xxh3/sha256/blake3
4. **hledac_universal_claude_md.md** - Project docs: conventions, invariants, architecture, feature flags
5. **issue_g2_pep_734_isolation_infrastructure.md** - PEP 734 isolation infrastructure for DuckDB writes
6. **parallel_async_helper.md** - parallel() async concurrency helper replacing deprecated bounded_gather
7. **pyo3-bridges-python-and-rust-across-all-performance-paths.md** - Synthesis on PyO3 as standard FFI
8. **rust-pyo3-unifies-all-hot-paths.md** - Synthesis: 30+ Rust extensions via PyO3/Maturin
9. **rust_extensions_overview.md** - 30+ Rust modules, MPSC pool, crossbeam, feature-gated
10. **technology_stack.md** - Python 3.14, MLX, DuckDB, curl-cffi, Rust via PyO3, pytest
11. **xxhash_rust_implementation.md** - xxhash-rust v0.8 with xxh3/xxh64 features


12. **rust-pyo3-unifies-all-hot-paths.md** - Synthesis: 30+ Rust extensions via PyO3/Maturin

The domain covers project configuration through environment gates and DuckDB constraints, architectural patterns via Rust/PyO3 integration, and concurrency utilities like the parallel helper. Key relationships include PyO3 serving as the FFI bridge for all hot paths, configuration constants establishing memory and threading baselines, and xxh3 being the standard hashing approach throughout.
</think>

# facts/project — Project Facts and Configuration

## Overview
Core project facts, configuration constants, and architecture decisions for **Hledac Universal** — an OSINT orchestrator running on M1 Apple Silicon 8GB.

---

## Configuration & Environment

### DuckDB Configuration
- **Memory**: 600MB limit (GHOST_DUCKDB_MEMORY), max temp 1GB (GHOST_DUCKDB_MAX_TEMP)
- **Threads**: 2 optimal for M1 8GB
- **Evidence log timeout**: Known mismatch (1000ms config vs 30000ms actual)

### Environment Gates (14+ HLEDAC_* variables)
- `HLEDAC_ARROW_INGEST=ON` — Arrow batch ingestion
- `HLEDAC_DUCKDB_QUERY_CACHE=OFF` — Query cache feature gate
- `HLEDAC_DUCKDB_RAMDISK_TEMP=None` — RAM disk for temp files
- `HLEDAC_ARROW_MIN_BATCH=5` — Minimum batch size
- `HLEDAC_RG_USE_RATIOS=0` — Enables absolute GiB mode for memory thresholds

### Rate Limits
- **Shodan**: 36 requests per 10 seconds (360/10)

### Hardware Constraints
- M1 8GB UMA hard cap: ~6.25GB total memory budget
- Swap threshold: 3.8 GiB triggers detection

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.14 (>=3.14,<3.15) |
| ML | MLX framework (darwin only) |
| Storage | DuckDB (analytics), LanceDB (vectors), LMDB (KV) |
| HTTP | curl-cffi (stealth), aioquic (HTTP/3 +50-80MB) |
| Serialization | msgspec.msgpack (zero-copy IPC) |
| Testing | pytest-asyncio, pytest-xdist, pytest-mock |

Platform: **M1 Apple Silicon only** (darwin + arm64)

---

## Rust/PyO3 Architecture

### Core Pattern
PyO3 is the standard FFI for all performance-critical paths — **consistent IPC model** across the codebase.

### Hot Paths (Rust-Accelerated)
- **MPSC batching**: `send_batch()` — ~1µs/event vs 5µs N×calls; ARM LSE atomics ~2-5ns/send
- **Hashing**: xxh3_64_hex via NEON SIMD — ~10× faster than hashlib
- **IOC extraction**: batch_ioc_extract_unified (Rust call, Tier 2)
- **Graph analytics**: DuckPGQGraph — 10-100× speedup over Python igraph

### Rust Extensions
- **30+ modules** in `rust_extensions/src/`
- MPSC pool: 2048 slots × 512B ≈ 1 MiB memory budget
- Replaces `asyncio.Queue(maxsize=500)` from evidence_log.py
- Feature flags: default=core, ml=macOS only, graph, data, advanced, full=CICD
- Build: `maturin develop --release`

### xxHash Implementation
- Crate: xxhash-rust v0.8 with xxh3, const_xxh3, xxh64 features
- Used for: canonical_url_batch (rayon 2-thread parallel), URL fingerprinting
- M1 acceleration via BLAKE3-64 NEON

---

## Key Concurrency Patterns

### parallel() Function
Replaces deprecated `bounded_gather` (rule #2997), `safe_gather_strict`, `safe_gather_ok`, `safe_gather_fire_and_forget`.

**Exception Policies**: `raise`, `first`, `collect` (default), `log`

**Invariants**:
- I6: `asyncio.CancelledError` always re-raised
- I7: `BaseException` (non-Exception) always re-raised
- I8: `Exception` routed per policy

### PEP 734 Isolation
- Infrastructure prepared with `DUCKDB_WRITE` role in `role_based_pools.py`
- `run_db_write()` function for DuckDB write operations
- Current workaround: ThreadPoolExecutor (stateful self closure = pickle problem)
- Full isolation requires standalone functions (not closures)

---

## Critical Conventions (10 Invariants)

| # | Rule |
|---|------|
| 1 | No bare `except` — always `except Exception:` |
| 2 | `asyncio.gather` always with `return_exceptions=True` |
| 3 | `mx.eval([])` before `mx.metal.clear_cache()` |
| 4 | DuckDB writes ONLY through `async_ingest_findings_batch()` |
| 5 | LMDB bulk via `cursor.putmulti()` — never per-item |
| 6 | Fail-safe everywhere — sidecar returns `[]` on errors |
| 7 | No `time.sleep()` in async — use `asyncio.sleep()` |
| 8 | No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()` |
| 9 | Use `RotatingBloomFilter` not `ScalableBloomFilter` |
| 10 | `sys.exit()` propagates verbatim |

**Exit Codes**: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT

---

## Issues Completed

- **ISSUE #2**: Hashing facade at `utils/hashing.py` — xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, blake3_64_hex; 7 calls migrated in deduplication.py, 1 in url_dedup.py
- **ISSUE G2**: PEP 734 isolation infrastructure — 38 tests passed

---

## Related Domains
- `architecture/hledac_universal` — System architecture and 3-lane HTTP strategy
- `data/duckdb_store` — DuckDB storage, WAL manager, query cache
- `memory/resource_governor` — M1 memory management and MPSC optimization
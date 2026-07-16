---
children_hash: 2887a197029901d7528ae0d292a0318d25b0745b655511ab961dbdb06abff8a5
compression_ratio: 0.3016734509271823
condensation_order: 1
covers: [configuration_constants.md, hashing_facade_issue_2.md, hledac_universal_claude_md.md, parallel_async_helper.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 4422
summary_level: d1
token_count: 1334
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries. These are all in the `facts/project` domain. Let me analyze the entries and create a cohesive summary.

Key themes across entries:
1. **Hledac Universal** - an OSINT orchestrator project
2. **Hardware context** - MacBook Air M1 8GB UMA
3. **Technology stack** - Python 3.14, MLX, DuckDB, Rust extensions via PyO3
4. **Storage** - DuckDB, LMDB, LanceDB trinity
5. **Rust extensions** - 30+ modules, MPSC pool, crossbeam channels
6. **Concurrency** - parallel() helper, asyncio patterns
7. **Hashing** - xxhash facade replacing hashlib
8. **Configuration** - environment gates, memory limits, feature flags
9. **Conventions/Invariants** - 10 critical rules for stability

Let me create a well-structured summary that:
- Preserves key facts and architectural decisions
- References entry names for drill-down
- Targets ~20-30% token compression
- Uses clean markdown
</think>

# facts/project — Project Core Facts

Core project knowledge for Hledac Universal OSINT orchestrator running on MacBook Air M1 8GB UMA.

## Hardware & Platform

- **M1 8GB** Apple Silicon (darwin + arm64 only)
- **UMA hard cap**: ~6.25GB total budget with swap_detected threshold at 3.8 GiB

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.14 + Rust PyO3 backend |
| ML | MLX with Hermes-3-Llama-3.2-3B-4bit |
| Storage | DuckDB (~600MB limit), LanceDB (vectors), LMDB (KV) |
| HTTP | curl-cffi (stealth/JA3) + aioquic (HTTP/3) |
| Serialization | msgspec for zero-copy encoding |
| Build | maturin, uv for dependency management |

See: `technology_stack.md`, `rust_extensions_overview.md`

## Storage Trinity

**DuckDB** (analytics SQL) → **LMDB** (key-value) → **LanceDB** (ANN embeddings)

Canonical write path: `async_ingest_findings_batch()`. LMDB bulk via `cursor.putmulti()`.

## Rust Extensions Architecture

30+ modules in `rust_extensions/src/` built via PyO3/Maturin:

| Subsystem | Key Files |
|-----------|-----------|
| Concurrency | `mpsc_pool.rs` — crossbeam MPSC replaces asyncio.Queue(maxsize=500) |
| Bloom filters | `bloom.rs`, `dedup_bloom.rs` |
| Processing | `content_hasher.rs`, `claims_extraction.rs`, `url_ops.rs` |
| I/O | `arrow_batch_builder.rs` |

**MPSC Pool**: 2048 slots × 512B ≈ 1 MiB budget. Uses ARM LSE atomics (ldadd, cas) for ~2-5ns/send.

See: `rust_extensions_overview.md`, `xxhash_rust_implementation.md`

## Hashing Infrastructure

**ISSUE #2**: Centralized `utils/hashing.py` facade replaces hashlib bottleneck.

| Function | Use Case |
|----------|----------|
| `xxh3_64_hex` | Single-item hot paths |
| `batch_xxh3_64_hex` | Bulk operations |
| `sha256_hex`, `blake3_64_hex` | Compatibility |

**Migrated**: 7 calls in `deduplication.py`, 1 blake2b in `url_dedup.py`.

**Preserved crypto-grade**: forensics, security, stealth, vault layers.

Performance: ~10x via Rust NEON SIMD.

See: `hashing_facade_issue_2.md`, `xxhash_rust_implementation.md`

## Async Concurrency

**`parallel()`** in `utils/async_helpers.py` — replaces deprecated `bounded_gather`.

| Policy | Behavior |
|--------|----------|
| `raise` | Propagate first exception |
| `first` | Return first result |
| `collect` (default) | Gather all results + errors |
| `log` | Silent error absorption |

Key invariants: I6 (CancelledError re-raised), I7 (BaseException re-raised), I8 (Exception per policy).

See: `parallel_async_helper.md`

## Configuration & Feature Flags

**Environment Gates**:
- `HLEDAC_ARROW_INGEST=ON`, `HLEDAC_DUCKDB_QUERY_CACHE=OFF`
- `HLEDAC_DUCKDB_RAMDISK_TEMP=None`, `HLEDAC_ARROW_MIN_BATCH=5`
- `HLEDAC_RG_USE_RATIOS=0` (absolute GiB mode)

**Resource Limits**:

| Setting | Value |
|---------|-------|
| DuckDB memory | 600MB |
| DuckDB max temp | 1GB |
| DuckDB threads | 2 (M1 optimal) |
| Shodan rate | 360/10 (36 req/10s) |

**Bug**: evidence_log busy_timeout mismatch (1000ms configured vs 30000ms actual).

See: `configuration_constants.md`

## Critical Invariants (10 Rules for M1 Stability)

1. No bare `except:` — always `except Exception:`
2. `asyncio.gather` always with `return_exceptions=True`
3. `mx.eval([])` before `mx.metal.clear_cache()`
4. DuckDB writes ONLY through `async_ingest_findings_batch()`
5. LMDB bulk via `cursor.putmulti()` — never per-item
6. Fail-safe everywhere — sidecars return `[]` on errors
7. No `time.sleep()` in async — use `asyncio.sleep()`
8. No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()`
9. Use `RotatingBloomFilter` not `ScalableBloomFilter`
10. `sys.exit()` propagates verbatim

See: `hledac_universal_claude_md.md`

## Sprint Lifecycle

`SprintScheduler.run()` → `run_prelude` → `run_acquisition_lanes` → `run_advisory_runner` → `_accumulate_findings_to_graph` → `run_winddown` → `DuckDB async_ingest_findings_batch`

Entry: `python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]`

## Test Baseline

- `test_sprint_scheduler.py`: ~89 tests
- `test_hledac_rust_extensions.py`: ~64 tests
- F206 probe: 200+ tests
- Benchmark threshold: >10% regression fails

## PyO3 FFI Strategy

Single consistent Python↔Rust boundary across all performance paths: hashing facade, MPSC batch sends, crossbeam interop. See: `pyo3-bridges-python-and-rust-across-all-performance-paths.md`
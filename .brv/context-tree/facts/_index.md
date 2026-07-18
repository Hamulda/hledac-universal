---
children_hash: e36b49c0e9a88304a20c78d95508b6480a88f1f123fd5be315157507c8aa5fa0
compression_ratio: 0.7831733483907397
condensation_order: 2
covers: [project/_index.md]
covers_token_total: 1771
summary_level: d2
token_count: 1387
type: summary
---
<think>
The user wants me to create a domain-level structural summary for `facts/project` based on the provided child entries. I need to condense all the entries into a cohesive overview while preserving key facts, relationships, and patterns. Let me synthesize this information into a clean structural summary.

The child entries cover:
1. Configuration constants (DuckDB memory, shodan rate, env gates)
2. Environment gates synthesis
3. Hashing facade issue #2
4. Project documentation reference
5. PEP 734 isolation infrastructure
6. Parallel async helper
7. PyO3 bridges synthesis
8. Rust-PyO3 hot paths synthesis
9. Rust extensions overview
10. Technology stack
11. xxhash implementation

I need to create a ~20-30% compression of this content while maintaining:
- Key facts
- Relationships
- Patterns
- References to child entries for drill-down

Let me create a comprehensive structural summary.
</think>

# facts/project — Project Facts and Configuration

## Overview
Core project facts, configuration constants, and architecture decisions for **Hledac Universal** — an OSINT orchestrator targeting M1 Apple Silicon 8GB with 30+ Rust extensions via PyO3.

---

## Technology Stack

**Platform**: M1 Apple Silicon (darwin + arm64) with 8GB Unified Memory as the primary hardware constraint (~6.25GB total memory budget).

| Layer | Technology | Notes |
|-------|------------|-------|
| Runtime | Python 3.14 (>=3.14,<3.15) | Per-requirement.txt |
| ML | MLX framework | darwin only |
| Storage | DuckDB, LanceDB, LMDB | Analytics, vectors, KV |
| HTTP | curl-cffi, aioquic | Stealth + HTTP/3 (+50-80MB) |
| IPC | msgspec.msgpack | Zero-copy |
| Testing | pytest-asyncio, pytest-xdist | Parallel execution |

---

## Configuration Constants

### DuckDB Memory
- **GHOST_DUCKDB_MEMORY**: 600MB
- **GHOST_DUCKDB_MAX_TEMP**: 1GB
- **Optimal threads**: 2 for M1 8GB

### External Services
- **Shodan rate limit**: 36 req/10s (360/10)

### Hardware Thresholds
- Swap detection: 3.8 GiB
- Evidence log timeout: Known mismatch (config=1000ms, actual=30000ms)

---

## Environment Gates

14+ `HLEDAC_*` variables control feature activation:

| Variable | Default | Purpose |
|----------|---------|---------|
| `HLEDAC_ARROW_INGEST` | ON | Arrow batch ingestion |
| `HLEDAC_DUCKDB_QUERY_CACHE` | OFF | Query cache feature |
| `HLEDAC_DUCKDB_RAMDISK_TEMP` | None | RAM disk for temp files |
| `HLEDAC_ARROW_MIN_BATCH` | 5 | Minimum batch size |
| `HLEDAC_RG_USE_RATIOS` | 0 | Absolute GiB vs ratios |

See: `environment-gates-control-feature-activation.md`

---

## Rust/PyO3 Architecture

### Core Pattern
PyO3 is the **standard FFI** for all performance-critical paths, enabling a consistent IPC model across the codebase. Built with Maturin (`maturin develop --release`).

### 30+ Rust Extensions
Modules in `rust_extensions/src/` with feature-gated compilation:

| Feature | Components |
|---------|------------|
| default/core | MPSC pool, hashing |
| ml | MLX integration |
| graph | DuckPGQGraph |
| data | IOC extraction |
| advanced | Advanced analytics |
| full | CICD (all features) |

### MPSC Pool Design
- **2048 slots × 512B ≈ 1 MiB** memory budget
- `send_batch()` reduces per-send from ~5µs to ~1µs
- ARM LSE atomics: ~2-5ns per send
- Replaces `asyncio.Queue(maxsize=500)` from evidence_log.py

### xxHash Implementation
- Crate: `xxhash-rust v0.8` (xxh3, const_xxh3, xxh64 features)
- NEON SIMD acceleration on M1
- Used for: canonical URLs, URL fingerprinting, deduplication
- See: `xxhash_rust_implementation.md`

### Hot Paths Accelerated
- **Hashing**: xxh3_64_hex via NEON — ~10× faster than hashlib
- **IOC extraction**: batch_ioc_extract_unified (Tier 2)
- **Graph analytics**: DuckPGQGraph — 10-100× speedup over Python igraph

---

## Concurrency Patterns

### parallel() Function
Replaces deprecated `bounded_gather` and related helpers (rule #2997).

**Exception Policies**: `raise`, `first`, `collect`, `log` (default)

**Key Invariants**:
- I6: `asyncio.CancelledError` always re-raised
- I7: `BaseException` (non-Exception) always re-raised
- I8: `Exception` routed per policy

See: `parallel_async_helper.md`

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

| Issue | Description | Status |
|-------|-------------|--------|
| #2 | Hashing facade (xxh3/batch_xxh3/sha256/blake3) | Complete |
| G2 | PEP 734 isolation infrastructure | 38 tests passed |

See: `hashing_facade_issue_2.md`, `issue_g2_pep_734_isolation_infrastructure.md`

---

## Related Domains
- `architecture/hledac_universal` — System architecture, 3-lane HTTP strategy, sprint lifecycle
- `data/duckdb_store` — DuckDB storage, WAL manager, query cache, thread settings
- `memory/resource_governor` — M1 memory management, MPSC optimization, resource governor
- `facts/conventions` — Additional workflow and coding conventions
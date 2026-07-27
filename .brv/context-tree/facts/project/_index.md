---
children_hash: 6ae11ce61ce7bf9a842804a096481a7b20c4e8a6e202cb73fb086b2467a68c5f
compression_ratio: 0.15342368677473647
condensation_order: 1
covers: [caps-capability-registry-for-feature-gating.md, coding_conventions_status.md, configuration_constants.md, context.md, environment-gates-control-feature-activation.md, exit_code_convention.md, git_stash_guard_hook.md, hashing_facade_issue_2.md, hledac_universal_claude_md.md, issue_0_2_curl_cffi_caps_invariants.md, issue_2_3_rayon_dispatch_channel_fix.md, issue_3_2_federatedqtable_parking_lot_migration.md, issue_5_2_rag_hnsw_quality_fix.md, issue_g2_pep_734_isolation_infrastructure.md, known_issues_and_todos.md, parallel_async_helper.md, probe_test_state_correction_2026_07_27.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-ffi-dominates-all-hot-paths.md, rust-pyo3-unifies-all-hot-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 11289
summary_level: d1
token_count: 1732
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the child entries provided. These are all in the `facts/project` domain based on the context tree structure shown.

Let me organize this information into a coherent summary that:
- Preserves key facts, architectural decisions, and relationships
- References entry names for drill-down
- Targets ~20-30% compression
- Uses clean structured markdown

Key themes I can identify:
1. **CAPS Capability Registry** - Feature gating mechanism replacing old availability checks
2. **Coding Conventions** - Documented in CLAUDE.md, asyncio patterns, DuckDB/LMDB patterns
3. **Configuration** - Environment gates (14+), DuckDB limits, memory constraints
4. **Rust/PyO3 FFI** - 30+ modules dominating hot paths (hashing, MPSC, graph analytics)
5. **Exit Codes** - Structured convention (0/1/2/3/130)
6. **Git Guard** - PreToolUse hook blocking destructive commands
7. **Issue Fixes** - Various issues documented (ISSUE-0.2, #2, 2.3, 3.2, 5.2, G2)
8. **Technology Stack** - Python 3.14, M1, DuckDB, MLX, Rust extensions
9. **Known Issues** - Tech debt items
10. **Probe Tests** - State correction

Let me structure this as a domain summary.
</think>

# facts/project — Project Knowledge Summary

Core project facts, conventions, and technical decisions for the Hledac Universal OSINT orchestrator.

## Architecture Patterns

**Rust/PyO3 FFI Dominates Hot Paths** (see `rust-pyo3-ffi-dominates-all-hot-paths.md`, `rust-pyo3-unifies-all-hot-paths.md`)
- 30+ Rust modules in `rust_extensions/src/` via PyO3/Maturin
- Critical paths: MPSC batching (~1µs/event vs 5µs N×calls), xxh3 hashing (10× via NEON SIMD), DuckPGQGraph graph analytics (10-100× vs Python igraph)
- crossbeam-channel MPSC pool: 2048 slots × 512B ≈ 1 MiB memory budget, ARM LSE atomics ~2-5ns/send
- `pyo3-bridges-python-and-rust-across-all-performance-paths.md` — single FFI strategy for hashing, concurrency, storage

**Concurrency Model** (see `parallel_async_helper.md`)
- `parallel()` replaces deprecated `bounded_gather` with 4 exception policies (raise/first/collect/log)
- `parallel()` uses `asyncio.TaskGroup` (3.11+) or `gather+semaphore` fallback
- I6/I7/I8 invariants: CancelledError always re-raised, BaseException always re-raised, Exception per policy

## Feature Gating

**CAPS Capability Registry** (see `caps-capability-registry-for-feature-gating.md`, `issue_0_2_curl_cffi_caps_invariants.md`)
- `CAPS.require(CURL_CFFI)` replaces `is_curl_cffi_available()` with FAIL-FAST fallback
- httpx fallback MUST have JA3 support — no silent degradation

**Environment Gates** (see `environment-gates-control-feature-activation.md`, `configuration_constants.md`)
- 14+ `HLEDAC_*` environment variables control features
- Key gates: `HLEDAC_ARROW_INGEST=ON`, `HLEDAC_DUCKDB_QUERY_CACHE=OFF`, `HLEDAC_RG_USE_RATIOS=0`
- DuckDB: 600MB memory limit, 2 threads on M1 8GB, 1GB max temp
- **Known mismatch**: evidence_log busy_timeout is 1000ms but should be 30000ms

## Technology Stack

**Core Stack** (see `technology_stack.md`, `rust_extensions_overview.md`)
- Python 3.14 (>=3.14,<3.15), restricted to M1 Apple Silicon (darwin + arm64)
- DuckDB for SQL, LMDB for key-value, sqlite-vec for ANN (~5MB), LanceDB alternative
- mlxcel external Rust binary for MLX inference (~1GB RSS savings vs in-process)
- curl-cffi for JA3 fingerprints, nh3 (9× faster than BS4), uvloop (2× I/O speedup)

**Rust Extensions** (`rust_extensions_overview.md`)
- 30+ modules: mpsc_pool, adaptive_scheduler, bloom, dedup_bloom, graph_cache, content_hasher, claims_extraction, url_ops, arrow_batch_builder
- PyO3 0.23 with extension-module + abi3-py39, crossbeam-channel 0.5, xxhash-rust 0.8

## Coding Conventions

**Source of Truth** (see `coding_conventions_status.md`, `hledac_universal_claude_md.md`)
- Conventions in `.claude/CLAUDE.md` (no dedicated `docs/conventions/` directory)
- **No bare except** — always `except Exception:`
- **asyncio.gather** always with `return_exceptions=True`
- **DuckDB writes** ONLY via `async_ingest_findings_batch()`
- **LMDB bulk** via `cursor.putmulti()` — never per-item
- **No `time.sleep()` in async** — use `asyncio.sleep()`
- **No `asyncio.run()` in ThreadPoolExecutor** — use `loop.run_until_complete()`
- **mx.eval([]) before `mx.metal.clear_cache()`**
- **RotatingBloomFilter** not ScalableBloomFilter for URL dedup

## Resolved Issues

| Issue | Summary | Key Files |
|-------|---------|-----------|
| `issue_0_2_curl_cffi_caps_invariants.md` | CAPS.require() availability check | `fetching/curl_cffi_fetch.py` |
| `hashing_facade_issue_2.md` | xxh3_64_hex facade, 7 migrates from hashlib | `utils/hashing.py`, `utils/deduplication.py` |
| `issue_2_3_rayon_dispatch_channel_fix.md` | crossbeam-channel dispatcher eliminates 25× overhead | `rust_extensions/src/rayon_dispatch.rs` |
| `issue_3_2_federatedqtable_parking_lot_migration.md` | DashMap→parking_lot::RwLock fixes PyO3 GIL segfaults | `rust_extensions/src/federated_qtable.rs` |
| `issue_5_2_rag_hnsw_quality_fix.md` | Adaptive expansion_add (200/300), native batch_search API | `knowledge/rag_engine.py`, `tools/hnsw_builder.py` |
| `issue_g2_pep_734_isolation_infrastructure.md` | DUCKDB_WRITE + run_db_write() isolation infrastructure | `role_based_pools.py` |

## Exit Codes

**Convention** (see `exit_code_convention.md`)
- `0` = clean success, `1` = runtime error, `2` = config/validation (F221-ABORT windup guard), `3` = programmer error (NameError, AttributeError, ImportError), `130` = SIGINT
- 6 regression tests in `tests/test_exit_codes.py` validate subprocess exit codes
- `_MAIN_FATAL` prefix required in logs for parser compatibility

## Infrastructure

**Git Stash Guard** (see `git_stash_guard_hook.md`)
- `.claude/hooks/git-stash-guard.sh` as PreToolUse synchronous hook
- Blocks 8 categories: git stash*, reset --hard, checkout --, clean -fd, push --force, branch -D, update-ref -d
- 36 coverage tests; use `/checkpoint create/restore` instead

**Probe Tests** (see `probe_test_state_correction_2026_07_27.md`)
- `tests/probe_p_e2_feed_pipeline/` is the only active probe test
- `probe_p12_http3_lane/` and `probe_p14_prewarm_conditional/` archived in F350M-R cleanup

## Known Tech Debt

| Item | Location | Issue |
|------|----------|-------|
| evidence_log timeout mismatch | `transport/http3_lane.py` | 1000ms → 30000ms |
| shodan rate formula | `runtime/` | 36 → 360 req/10s |
| DuckPGQGraph integration gap | `data/duckdb_store/` | DuckDB vs DuckPGQGraph storage split |
| neqo HTTP/3 not on PyPI | `transport/` | F320-TODO |
| Lock-free AtomicCounter | `rust_extensions/` | Issue #5 not implemented |
| Acquisition prioritization | `runtime/scheduler_v2/acquisition.py` | ctx.graph_stats unused |
| Social identity platform detection | `recon/social_identity_miner.py` | Not implemented |
| Domain filtering | `recon/stealth/_models.py` | Allowlist/blocklist unused |
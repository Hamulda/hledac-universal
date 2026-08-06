---
children_hash: eb33b133d0afa3729046ca2a0b6a91aad0a0164871d19a464ab2e6c923c8b58e
compression_ratio: 0.1202416918429003
condensation_order: 1
covers: [async_helpers_module_architecture.md, caps-capability-registry-for-feature-gating.md, caps-capability-registry-unification.md, coding_conventions_status.md, configuration_constants.md, context.md, duckdb_store_configuration.md, environment-gates-control-feature-activation.md, exit_code_convention.md, git_stash_guard_hook.md, hashing_facade_issue_2.md, heist_04_zstd_dictionary_wired_into_export.md, heist_05_simdjson_zero_alloc_json_path.md, hledac_universal_claude_md.md, issue_0_2_curl_cffi_caps_invariants.md, issue_2_3_rayon_dispatch_channel_fix.md, issue_3_2_federatedqtable_parking_lot_migration.md, issue_5_2_rag_hnsw_quality_fix.md, issue_a5_feature_flag_sprawl_analysis.md, issue_g2_pep_734_isolation_infrastructure.md, known_issues_and_todos.md, parallel_async_helper.md, pep_562_brain_lazy_loading_refactoring.md, probe_test_state_correction_2026_07_27.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-ffi-dominates-all-hot-paths.md, rust-pyo3-ffi-hot-path-strategy.md, rust-pyo3-unifies-all-hot-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 14895
summary_level: d1
token_count: 1791
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. Let me analyze these entries to identify patterns, relationships, and key themes.

Looking at the child entries, they all relate to the "facts/project" domain - covering project configuration, conventions, technology stack, and specific implementation details. Let me organize these by theme:

1. **Rust/PyO3 FFI Architecture** (cross-cutting concern):
   - rust_extensions_overview.md
   - rust-pyo3-ffi-dominates-all-hot-paths.md
   - rust-pyo3-unifies-all-hot-paths.md
   - rust-pyo3-ffi-hot-path-strategy.md
   - pyo3-bridges-python-and-rust-across-all-performance-paths.md

2. **Feature Gating/Capabilities**:
   - caps-capability-registry-for-feature-gating.md
   - caps-capability-registry-unification.md
   - issue_0_2_curl_cffi_caps_invariants.md
   - issue_a5_feature_flag_sprawl_analysis.md
   - context.md (project topic)

3. **Async Concurrency**:
   - async_helpers_module_architecture.md
   - parallel_async_helper.md
   - issue_2_3_rayon_dispatch_channel_fix.md

4. **Configuration & Environment**:
   - configuration_constants.md
   - duckdb_store_configuration.md
   - environment-gates-control-feature-activation.md
   - technology_stack.md

5. **Coding Conventions**:
   - coding_conventions_status.md
   - exit_code_convention.md
   - hledac_universal_claude_md.md

6. **Specific Issues Fixed**:
   - hashing_facade_issue_2.md
   - issue_3_2_federatedqtable_parking_lot_migration.md
   - issue_5_2_rag_hnsw_quality_fix.md
   - issue_g2_pep_734_isolation_infrastructure.md
   - pep_562_brain_lazy_loading_refactoring.md
   - heist_04_zstd_dictionary_wired_into_export.md
   - heist_05_simdjson_zero_alloc_json_path.md

7. **Project State**:
   - known_issues_and_todos.md
   - probe_test_state_correction_2026_07_27.md
   - git_stash_guard_hook.md

Now I'll create a structured summary that preserves key facts and architectural patterns across these domains.
</think>

# facts/project — Hledac Universal Project Facts

## Core Architecture

**Rust/PyO3 FFI Dominance**: 30+ Rust modules in `rust_extensions/src/` power all performance-critical paths via PyO3/Maturin. MPSC pool uses crossbeam bounded channel (2048 slots × 512B ≈ 1 MiB) with ARM LSE atomics for ~2-5ns/send. Hashing uses xxhash-rust v0.8 with NEON SIMD acceleration. Key modules: `mpsc_pool.rs`, `rayon_dispatch.rs`, `bloom.rs`, `dedup_bloom.rs`, `claims_extraction.rs`, `arrow_batch_builder.rs`.

**Storage Trinity**: DuckDB (SQL queries), LMDB (key-value), LanceDB (ANN embeddings). DuckDB configured via `GHOST_*` constants with 600MB limit on M1 8GB. DuckDB writes MUST use `async_ingest_findings_batch()`.

**MLX Inference**: `mlxcel` external Rust binary for production inference (saves ~1GB RSS vs in-process mlx-lm). Uses Hermes-3-Llama-3.2-3B-4bit model.

## Feature Gating

**CAPS Capability Registry**: `CAPS.require(CURL_CFFI)` is canonical availability check replacing `is_curl_cffi_available()`. FAIL-FAST policy. ISSUE-0.2 established this pattern. Growing adoption from 5 to 30+ files needed (see `issue_a5_feature_flag_sprawl_analysis`).

**HLEDAC_* Environment Gates**: 410 distinct env vars exist with 97 ENABLE flags—but only 36 checked at runtime. Gates control Arrow ingest, DuckDB tuning, query cache, resource governor ratios. `HLEDAC_RG_USE_RATIOS=0` enables absolute GiB mode.

## Async Concurrency

**parallel() Function**: Unified async helper in `utils/async_helpers.py` (2840 lines). ISSUE-006 replaced 11 safe_gather variants. Four exception policies: raise, first, collect (default), log. I6/I7/I8 invariants govern routing: CancelledError always re-raised, BaseException always re-raised, Exception routed per policy.

**Rayon Dispatch**: `rayon_dispatch.rs` eliminates 25× context-switch overhead from double threading. 1 dispatcher thread per pool type (cpu/io/mixed) consumes from crossbeam-channel instead of spawning threads per task. ~5μs submission vs ~500μs for thread::spawn.

**Additional helpers**: `safe_wait_for` for TaskGroup cancellation (F320), `first_completed` for PEP 654 asyncio.wait(FIRST_COMPLETED), `rust.dns` replacing aiodns (c-ares).

## Technology Stack

**Platform**: Python 3.14+, Darwin ARM64 (M1 Apple Silicon only), 8GB UMA memory ceiling.

**Key Dependencies**: httpx ≥0.28.0 (HTTP/2), uvloop (2× I/O speedup), nh3 (9× faster than BS4), curl-cffi (JA3 fingerprints), sqlite-vec (primary ANN, ~5MB), dspy ≥3.2.1, transformers ≥5.10.2.

**Rust Crates**: pyo3 0.23 (abi3-py39), crossbeam-channel 0.5, duckdb 1, xxhash-rust 0.8, metal 0.33.

## Coding Conventions

**Conventions Location**: Documented in `.claude/CLAUDE.md` (not dedicated `docs/conventions/`). Invariants enforced via code review.

**Critical Rules**: No bare `except:`. `asyncio.gather` always with `return_exceptions=True`. `mx.eval([])` before `mx.metal.clear_cache()`. No `time.sleep()` in async—use `asyncio.sleep()`. No `asyncio.run()` in ThreadPoolExecutor. `RotatingBloomFilter` not `ScalableBloomFilter`. LMDB bulk via `cursor.putmulti()`.

**Exit Codes**: 0=success, 1=runtime error, 2=config/validation, 3=programmer error, 130=SIGINT. 6 regression tests in `tests/test_exit_codes.py`.

## Issue Fixes & Refactoring

**ISSUE #2 (Hashing)**: Centralized `utils/hashing.py` facade with `xxh3_64_hex`/`batch_xxh3_64_hex`. 7 hashlib calls in `deduplication.py` migrated to xxh3. ~10× speedup via NEON SIMD. Crypto-grade preserved for forensics/security/stealth/vault.

**ISSUE 2.3 (Rayon)**: Channel-based dispatch eliminating double threading overhead. See async concurrency section.

**ISSUE 3.2 (FederatedQTable)**: Migrated from `DashMap` to `parking_lot::RwLock` + AHashMap to fix PyO3 GIL segfaults. Crossbeam shard locking conflicted with PyO3 in async/ThreadPoolExecutor contexts.

**ISSUE 5.2 (RAG HNSW)**: Adaptive `expansion_add` (200/300 based on index size) replacing hard-coded 100. Native `usearch` v2.26+ batch API replacing loop-based search.

**ISSUE G2 (PEP 734)**: DUCKDB_WRITE role + `run_db_write()` in `role_based_pools.py`. Current implementation uses ThreadPoolExecutor due to stateful self closure pickle problem.

**PEP 562 (Brain)**: `brain/__init__.py` lazy loading refactored. 17 engine blocks consolidated into TypedDict registry. 270-line `__getattr__` reduced to ~10 lines.

**HEIST-04/05**: Zstd dictionary compression for STIX export (Dict ID 1). simdjson zero-alloc JSON Pointer extraction.

## Known Issues & State

**Evidence log timeout**: 1000ms configured vs 30000ms actual (needs resolution).

**Shodan rate**: Formula is 360/10=36 but should be 3600/10=360.

**Probe tests**: `probe_p_e2_feed_pipeline` is active. `probe_p12_http3_lane` and `probe_p14_prewarm_conditional` were archived in F350M-R cleanup.

**Git guard**: `.claude/hooks/git-stash-guard.sh` blocks 8 categories of destructive commands. 36 coverage tests.

## Cross-References

See `parallel_async_helper` for exception policies. See `rust_extensions_overview` for PyO3 architecture. See `environment-gates-control-feature-activation` for HLEDAC_* details. See `duckdb_store_configuration` for storage constants.
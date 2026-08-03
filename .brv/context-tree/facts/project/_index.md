---
children_hash: 28015bdad4d19e45108b01aff650dcb6f966fd6411206914aa4a69ad6d792e76
compression_ratio: 0.09835712748403759
condensation_order: 1
covers: [caps-capability-registry-for-feature-gating.md, caps-capability-registry-unification.md, coding_conventions_status.md, configuration_constants.md, context.md, environment-gates-control-feature-activation.md, exit_code_convention.md, git_stash_guard_hook.md, hashing_facade_issue_2.md, heist_04_zstd_dictionary_wired_into_export.md, heist_05_simdjson_zero_alloc_json_path.md, hledac_universal_claude_md.md, issue_0_2_curl_cffi_caps_invariants.md, issue_2_3_rayon_dispatch_channel_fix.md, issue_3_2_federatedqtable_parking_lot_migration.md, issue_5_2_rag_hnsw_quality_fix.md, issue_a5_feature_flag_sprawl_analysis.md, issue_g2_pep_734_isolation_infrastructure.md, known_issues_and_todos.md, parallel_async_helper.md, pep_562_brain_lazy_loading_refactoring.md, probe_test_state_correction_2026_07_27.md, pyo3-bridges-python-and-rust-across-all-performance-paths.md, rust-pyo3-ffi-dominates-all-hot-paths.md, rust-pyo3-ffi-hot-path-strategy.md, rust-pyo3-unifies-all-hot-paths.md, rust_extensions_overview.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 13939
summary_level: d1
token_count: 1371
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries. Let me analyze these entries and identify key themes, relationships, and patterns.

Key themes I can identify:

1. **Rust/PyO3 FFI Architecture**: Multiple entries about the 30+ Rust modules via PyO3, crossbeam-channel MPSC, xxhash hashing, DuckPGQGraph
2. **Feature Gating**: CAPS capability registry, environment gates (HLEDAC_*), feature flag sprawl
3. **Project Conventions**: Exit codes, coding conventions, git stash guard hook
4. **Performance Optimizations**: SIMDjson, Zstd dictionary compression, rayon dispatch channel fix
5. **Known Issues**: evidence_log timeout, shodan rate, DuckPGQGraph integration gap
6. **Technology Stack**: Python 3.14, M1 Apple Silicon, DuckDB, mlxcel, sqlite-vec

Let me create a cohesive structural summary that preserves key facts and relationships while compressing the content.
</think>

# facts/project — Project Knowledge Summary

## Overview

The `facts/project` domain captures project-specific knowledge for Hledac Universal: a Python 3.14 OSINT orchestrator targeting M1 Apple Silicon (8GB UMA).

---

## Architecture: Rust/PyO3 FFI Hot Path Strategy

**30+ Rust modules** in `rust_extensions/src/` handle all performance-critical paths via PyO3/Maturin.

**Core Modules:**
- `mpsc_pool.rs` — crossbeam-channel bounded MPSC (2048×512B≈1MiB), ARM LSE atomics ~2-5ns/send
- `rayon_dispatch.rs` — crossbeam-channel dispatch eliminates 25× context-switch overhead (1 dispatcher per pool vs 2 threads/task)
- `federated_qtable.rs` — Migrated from DashMap→parking_lot::RwLock+AHashMap to fix PyO3 GIL segfaults
- `simdjson_extract.rs` — zero-alloc JSON Pointer via RFC 6901, NDJSON selective streaming
- `bloom.rs`, `dedup_bloom.rs` — RotatingBloomFilter for URL dedup
- `content_hasher.rs`, `url_ops.rs` — xxh3 NEON SIMD hashing

**IOC Extraction:** 3-tier fallback (Python→Rust/Rayon→Rust/PyO3), `batch_ioc_extract_unified`

**DuckPGQGraph:** F272 replacing Python igraph, 10-100× speedup for graph analytics

**HEIST-04:** Zstd dictionary (Dict ID=1) wired into STIX export pipeline

**Dependency Stack:** PyO3 0.23, crossbeam-channel 0.5, xxhash-rust 0.8, msgspec.msgpack for zero-copy IPC

---

## Capability & Feature Gating

**CAPS System:** Canonical capability registry replacing `is_curl_cffi_available()` with `CAPS.require(CURL_CFFI)` (FAIL-FAST policy)

**ISSUE-A5 Analysis:**
- 410 HLEDAC_* env vars, 97 ENABLE_* flags, only 36 checked at runtime
- Three-layer model: Capabilities (keep) → SprintProfile/LaneRegistry (fix) → RuntimeConfig/FlagSpec (validate)

**Key Gates:**
- `HLEDAC_ARROW_INGEST=ON`, `HLEDAC_DUCKDB_QUERY_CACHE=OFF`
- `HLEDAC_RG_USE_RATIOS=0` (absolute GiB vs ratio mode)
- Emergency overrides remain as env vars

---

## Configuration Constants

| Parameter | Value |
|-----------|-------|
| DuckDB memory | 600MB (GHOST_DUCKDB_MEMORY) |
| DuckDB max temp | 1GB |
| DuckDB threads | 2 (M1 optimal) |
| Shodan rate | 36/10s (documented), should be 360 |
| Evidence log timeout | 1000ms configured, 30000ms actual |

**M1 8GB UMA cap:** ~6.25GB total budget, swap threshold 3.8 GiB

---

## Coding Conventions

**Location:** `.claude/CLAUDE.md` (no dedicated `docs/conventions/`)

**Critical Invariants:**
1. No bare `except:` — always `except Exception:`
2. `asyncio.gather()` always with `return_exceptions=True`
3. `mx.eval([])` before `mx.metal.clear_cache()`
4. DuckDB writes ONLY via `async_ingest_findings_batch()`
5. LMDB bulk via `cursor.putmulti()`
6. No `time.sleep()` in async — use `asyncio.sleep()`
7. No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()`
8. Use `RotatingBloomFilter` not `ScalableBloomFilter`

**parallel() Function:** Replaces deprecated `bounded_gather` with 4 policies (raise/first/collect/log), I6/I7/I8 exception routing invariants

---

## Exit Code Convention

**6 regression tests** in `tests/test_exit_codes.py`:

| Code | Meaning |
|------|---------|
| 0 | Clean success |
| 1 | Runtime error |
| 2 | Config/validation error (F221-ABORT windup guard) |
| 3 | Programmer error (NameError, AttributeError, ImportError) |
| 130 | SIGINT (KeyboardInterrupt) |

`sys.exit(N)` must propagate verbatim — envelope has `except SystemExit: raise`

---

## Git Operations

**git-stash-guard.sh:** Synchronous PreToolUse hook blocks 8 categories (stash, reset --hard, force push, clean -fd, branch -D). 36 coverage tests. Safe alternative: `/checkpoint create/restore`.

---

## PEP 562 Brain Lazy Loading

`brain/__init__.py`: 17 engine blocks consolidated into TypedDict registry, 270-line `__getattr__` reduced to ~10 lines, 99.8% structural deduplication.

---

## Known Tech Debt

| Issue | Location | Status |
|-------|----------|--------|
| evidence_log timeout mismatch | transport/http3_lane.py | Open |
| shodan rate 36 vs 360 | config | Open |
| DuckPGQGraph integration gap | data/duckdb_store | Open |
| neqo HTTP/3 not on PyPI | F320-TODO | Pending |
| social identity detection | recon/social_identity_miner.py | Not impl |
| domain allowlist/blocklist | recon/stealth/_models.py | Not impl |

---

## Key Files

- `utils/hashing.py` — centralized facade: `xxh3_64_hex`, `batch_xxh3_64_hex`, `sha256_hex`, `blake3_64_hex`
- `utils/async_helpers.py` — `parallel()` with exception policies
- `utils/simdjson_bridge.py` — lazy Rust import, orjson fallback
- `rust_extensions/Cargo.toml` — PyO3 0.23, crossbeam-channel 0.5, xxhash-rust 0.8
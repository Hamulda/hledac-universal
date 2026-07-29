---
children_hash: e263bfdd861c54aff1100f7f93a1e00a363b18dcad322f2fd4754cebce5c1a3b
compression_ratio: 0.8395867319195215
condensation_order: 2
covers: [project/_index.md]
covers_token_total: 1839
summary_level: d2
token_count: 1544
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about the Hledac Universal project. I need to:

1. Review all child entries and identify key themes, patterns, and relationships
2. Create a condensed structural summary (~20-30% of input)
3. Preserve entity names, file paths, architectural decisions
4. Reference entry names for drill-down
5. Output clean markdown without code blocks

Key themes I'm seeing from the child entries:
- **Feature Gating**: CAPS capability registry, environment gates (HLEDAC_*), feature flag sprawl
- **Rust/PyO3 FFI**: 30+ Rust modules, crossbeam-channel MPSC, xxh3 hashing, DuckPGQGraph
- **Coding Conventions**: Exit codes, async patterns, DuckDB/LMDB patterns, git guard hook
- **Performance**: Hashing facade, rayon dispatch channel fix, MPSC pool
- **Issues Resolved**: ISSUE-0.2 (curl_cffi caps), ISSUE-2 (hashing), ISSUE-2.3 (rayon dispatch), ISSUE-3.2 (parking_lot migration), ISSUE-5.2 (RAG HNSW)
- **Configuration**: DuckDB memory limits, environment variables, technology stack

Let me structure this into a coherent domain summary.
</think>

# facts/project — Project Knowledge Summary

Domain covering project configuration, coding conventions, feature gating, Rust/PyO3 FFI hot paths, and resolved issues.

## Core Architectural Patterns

**Rust/PyO3 FFI Hot Path Strategy**
- 30+ Rust modules in `rust_extensions/src/` via PyO3/Maturin (pyo3 0.23, extension-module + abi3-py39)
- Crossbeam-channel MPSC pool (capacity=2048, 512B/slot ≈ 1MiB budget, ARM LSE atomics ~2-5ns/send)
- Zero-copy IPC via msgspec.msgpack
- `#[pyclass(unsendable)]` required because `Receiver<QueueItem>` is not Send

**Centralized Hashing Facade** (`utils/hashing.py`)
- `xxh3_64_hex`, `batch_xxh3_64_hex`, `sha256_hex`, `blake3_64_hex`
- Migrated 7 calls in `utils/deduplication.py` + 1 in `tools/url_dedup.py` to xxh3
- Crypto-grade hashing preserved for forensics/security/stealth/vault
- Expected ~10× speedup via Rust NEON SIMD

**Parallel Async Concurrency** (`utils/async_helpers.py`)
- `parallel()` replaces deprecated `bounded_gather` (rule #2997)
- 4 exception policies: raise, first, collect, log
- Invariants I6/I7/I8 govern exception routing (CancelledError always re-raised)

**PEP 562 Lazy Loading** (`brain/__init__.py`)
- 17 engine blocks consolidated into TypedDict registry
- 270-line `__getattr__` reduced to ~10 lines (99.8% deduplication)

## Feature Gating Architecture

**CAPS Capability Registry** (`core/capabilities.py`)
- `CAPS.require(CURL_CFFI)` canonical availability check with FAIL-FAST policy
- ISSUE-0.2: replaced `is_curl_cffi_available()` with CAPS-based gating
- Should extend to other optional dependencies

**Environment Variables** (14+ HLEDAC_* gates)
- `HLEDAC_ARROW_INGEST=ON`, `HLEDAC_DUCKDB_QUERY_CACHE=OFF`, `HLEDAC_RG_USE_RATIOS=0`
- `HLEDAC_DUCKDB_MEMORY` (600MB-2GB), `HLEDAC_DUCKDB_THREADS` (2 M1 optimal)

**Feature Flag Sprawl** (ISSUE-A5)
- 410 HLEDAC_* env vars, 97 ENABLE flags, only 36 checked at runtime
- Three-layer model: Capabilities (keep) → SprintProfile/LaneRegistry (fix) → RuntimeConfig/FlagSpec (validate)
- 5 flag presets: MINIMAL/OSINT/RECON/RESEARCH/FULL

## Configuration Constants

- **DuckDB**: 600MB memory limit, 1GB max temp, 2 threads for M1 8GB
- **Shodan**: 36 requests/10s (should be 360)
- **Evidence log**: 1000ms busy_timeout (should be 30000ms)
- **M1 8GB UMA cap**: ~6.25GB total budget, swap threshold 3.8 GiB

## Coding Conventions (`.claude/CLAUDE.md`)

- **Async**: `asyncio.gather` always with `return_exceptions=True`; no `time.sleep()` in async; no `asyncio.run()` in ThreadPoolExecutor
- **Storage**: DuckDB writes via `async_ingest_findings_batch()`; LMDB bulk via `cursor.putmulti()`
- **MLX**: `mx.eval([])` before `mx.metal.clear_cache()`
- **Bloom filters**: `RotatingBloomFilter` (not ScalableBloomFilter)
- **Exceptions**: No bare `except:` — always `except Exception:`
- **Exit codes**: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT

## Resolved Issues

| Issue | Summary |
|-------|---------|
| ISSUE-0.2 | CAPS-based curl_cffi availability with FAIL-FAST fallback |
| ISSUE-2 | Hashing facade replacing hashlib bottleneck (~10× speedup) |
| ISSUE-2.3 | Rayon dispatch channel fix — 1 dispatcher thread per pool type, eliminates 25× context-switch overhead |
| ISSUE-3.2 | `parking_lot::RwLock` + AHashMap replaces DashMap to fix PyO3 GIL segfaults |
| ISSUE-5.2 | RAG HNSW adaptive expansion_add (200/300) + native batch_search API |
| ISSUE-G2 | PEP 734 isolation infrastructure for DuckDB writes |

## Git Protection

**Git Stash Guard Hook** (`.claude/hooks/git-stash-guard.sh`)
- Blocks 8 categories: `git stash*`, `git reset --hard`, `git checkout --`, `git clean -fd`, `git push --force`, `git branch -D`, `git update-ref -d`
- 36 coverage tests validate behavior
- Alternative: `/checkpoint create/restore`

## Technology Stack

- **Python 3.14** (darwin + arm64 only)
- **Storage Trinity**: DuckDB (SQL), LMDB (key-value), sqlite-vec (ANN ~5MB)
- **ML Inference**: mlxcel external Rust binary (~1GB RSS savings vs in-process)
- **HTTP**: curl-cffi (JA3 fingerprints) + aioquic (real QUIC)
- **Parsing**: nh3 (9× faster than BS4), uvloop (2× speedup)

## Known Tech Debt

- Evidence log timeout mismatch (1000ms vs 30000ms)
- Shodan rate formula (36 vs 360)
- DuckPGQGraph integration gap
- neqo HTTP/3 not yet on PyPI
- Lock-free counter (AtomicCounter) not implemented
- Social identity platform detection not implemented

## Related Drill-Down

- `facts/project/rust_extensions_overview.md` — 30+ Rust modules
- `facts/project/hledac_universal_claude_md.md` — Comprehensive project docs
- `facts/project/coding_conventions_status.md` — Convention rules
- `facts/project/issue_0_2_curl_cffi_caps_invariants.md` — CAPS invariants
- `facts/project/issue_2_3_rayon_dispatch_channel_fix.md` — Rayon architecture
- `facts/project/issue_3_2_federatedqtable_parking_lot_migration.md` — RwLock migration
- `facts/project/environment-gates-control-feature-activation.md` — Feature gates
- `facts/project/issue_a5_feature_flag_sprawl_analysis.md` — Flag sprawl analysis
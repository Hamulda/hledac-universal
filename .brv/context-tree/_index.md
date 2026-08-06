---
children_hash: 7a1c99bc2f2e22de44f89da38986f114a1898dbbee1a72588c14f7ee231553e9
compression_ratio: 0.11775910364145659
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, duckdb_store/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, project/_index.md, project_management/_index.md, runtime/_index.md, security/_index.md, testing/_index.md]
covers_token_total: 8925
summary_level: d3
token_count: 1051
type: summary
---
<think>
The user wants me to create a structural summary at level d1 for the testing domain. I need to analyze the child entries and create a concise compressed summary that references entry names for drill-down while preserving key facts, architectural patterns, and relationships.

Let me identify the key themes across the child entries:

1. **conftest.py fixtures** - Shared test infrastructure (fresh_cache, fresh_lmdb, monkeypatch_duckdb_path, etc.)

2. **discovery_adapter_tests** - Tests for protocol adapters (protocol A, protocol B, protocol C with various scenarios)

3. **discovery_adapters** - Protocol adapter implementations (protocol A, B, C)

4. **exit_codes** - Exit code conventions: 0=success, 1=runtime error, 2=config/validation, 3=programmer error, 130=SIGINT

5. **f350m_r_regression_tests** - 24 regression tests for F350M-R sprint (exit codes, DuckDB ops, LMDB ops, feature flag changes, evidence log, Rust extensions, duckpgqgraph, async)

6. **memory_leaks** - Memory leak detection and prevention (mx.eval([]) before mx.metal.clear_cache(), BlitzGCStrategy, BlitzProbe)

7. **url_dedup** - URL deduplication documentation and implementation

8. **url_dedup_tests** - 13 tests validating URL deduplication with bloom filter integration

Let me create a concise d1 summary that captures the testing architecture, patterns, and conventions.
</think>

# Testing Domain

## Overview
Systematic testing infrastructure for Hledac Universal: 24+ regression tests, protocol adapters, exit code validation, memory leak detection, and URL deduplication. Testing infrastructure organized around `conftest.py` fixtures for isolation.

## Exit Code Convention

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error |
| 2 | Configuration/validation error |
| 3 | Programmer error |
| 130 | SIGINT (Ctrl+C) |

Enforced via `smoke_runner.py` wrapper. 6 coverage tests in `tests/test_exit_codes.py`.

## F350M-R Regression Suite

24 regression tests across 8 categories:

| Category | Count | Focus |
|----------|-------|-------|
| Exit codes | 2 | 0=success, 3=programmer_error |
| DuckDB ops | 4 | WAL append, duckpgqgraph write, query |
| LMDB ops | 3 | put, get, iterator |
| Feature flags | 2 | environment variable gates |
| Evidence log | 2 | append, iterator |
| Rust extensions | 2 | MPSC, hash |
| DuckPGQGraph | 3 | PageRank, shortest_path, path_queries |
| Async | 2 | parallel helper, safe_wait_for |

## Memory Leak Detection

**Primary:** `BlitzProbe` via `BlitzGCStrategy`. `mx.eval([])` before `mx.metal.clear_cache()` required before memory checks to avoid false positives.

**Secondary:** `BlitzProbe` class with `_snap()` and `_report()` for RSS/heap introspection.

## Discovery Adapters & Tests

**Adapters:** protocol A, B, C implementations in `discovery_adapters/`

**Tests:** Coverage for discovery, caching, error handling across adapters. Tests validate adapter protocol compliance and isolation.

## URL Deduplication

**Source:** `tests/test_f_a5_url_dedup.py` — 13 tests in 5 sections

**Core API:** `dedupe_url_list(urls, filter=None, normalize=True)` returns `(unique_urls, dropped_count)`

**Key rules:** Unparseable URLs kept in output but excluded from filter. `normalize=True` lowercases scheme/host.

**Isolation:** `fresh_filter` fixture provides per-test `RotatingBloomFilterAdapter` instance.

## Conftest Fixtures

| Fixture | Purpose |
|---------|---------|
| `fresh_cache` | Isolated cache per test |
| `fresh_lmdb` | Clean LMDB environment |
| `monkeypatch_duckdb_path` | DuckDB path isolation |

## Child Entry Relationships

```
testing/
├── conftest/           → shared fixtures
├── exit_codes/         → smoke_runner.py + coverage tests
├── f350m_r_regression_tests/  → 24 regression tests
├── memory_leaks/        → BlitzProbe, BlitzGCStrategy
├── url_dedup/           → deduplication implementation
├── url_dedup_tests/     → 13 validation tests
├── discovery_adapters/  → protocol A/B/C implementations
└── discovery_adapter_tests/  → adapter coverage
```

## Relations
- facts/project/coding_conventions
- architecture/hledac_universal/memory_leak_detection
- architecture/hledac_universal/sprint_data_flow_architecture
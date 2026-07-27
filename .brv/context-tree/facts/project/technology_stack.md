---
title: Technology Stack
summary: 'Hledac Universal tech stack: Python 3.14, M1 Apple Silicon, DuckDB, mlxcel Rust inference, sqlite-vec ANN, with HTTP/stealth, MLX, storage, and linting dependencies'
tags: []
related: [facts/project/rust_extensions_overview.md, facts/project/coding_conventions_status.md, facts/project/known_issues_and_todos.md]
keywords: []
createdAt: '2026-07-11T14:49:36.803Z'
updatedAt: '2026-07-27T13:09:51.267Z'
---
## Reason
Documenting pyproject.toml technology stack for Hledac Universal

## Raw Concept
**Task:**
Document technology stack from pyproject.toml for Hledac Universal project

**Files:**
- pyproject.toml

**Flow:**
Python 3.14 + M1 Apple Silicon -> Core deps (HTTP/stealth, storage, MLX) -> Optional extras (ml, otel, http3) -> Build/Test/Lint config

**Timestamp:** 2026-07-27

**Author:** Hledac Team

## Narrative
### Structure
Technology stack organized into: Project config, Core dependencies (HTTP/stealth, serialization/async, storage, MLX/Apple Silicon, NER/NLP, vector indexes, parsing, documents, numerics, Apple frameworks, crypto, hashing, pattern matching, IPC, data), Optional extras (ml, mlx-embed, http3, otel, observability), Dev dependencies, Testing config, Linting rules, Build system, mlxcel production architecture

### Dependencies
M1 Apple Silicon platform constraint, Python 3.14 requirement, M1 8GB memory ceiling

### Highlights
mlxcel external Rust binary saves ~1GB RSS vs in-process mlx-lm; sqlite-vec primary ANN (~5MB); uvloop 2× speedup on M1; nh3 9× faster than BS4; curl-cffi for JA3 fingerprints; Rust extensions via maturin

### Rules
Rule 1: asyncio.run() forbidden outside __main__, tools/, tests/
Rule 2: pytest asyncio_default_fixture_loop_scope must be session for F350M-R compatibility
Rule 3: BLE001 ruff rule deferred (P1-01 audit in progress, see Issue #32)
Rule 4: pytest addopts includes -n 2 --dist=loadscope --timeout=30 -m 'not parity'

## Facts
- **python_version**: Python 3.14 required (>=3.14,<3.15) [project]
- **platform**: Platform restricted to darwin + arm64 (M1 Apple Silicon only) [project]
- **duckdb_memory_limit**: DuckDB memory limit ~600MB on 8GB M1 [project]
- **httpx_version**: httpx >=0.28.0,<0.30.0 for stable HTTP/2 [project]
- **mlx_lm_metadata_bug**: mlx-lm 0.31.x has transformers>=5.0.0 metadata bug but works with 4.x at runtime [project]
- **pytest_asyncio_config**: pytest-asyncio_default_fixture_loop_scope=session required for F350M-R [project]
- **package_manager**: uv is the package manager [project]
- **rust_build_tool**: maturin for PyO3 Rust extensions [project]
- **mlxcel_inference**: mlxcel is the production inference binary (external Rust process) [project]
- **primary_ann_store**: sqlite-vec is primary ANN store (~5MB vs ~200MB LanceDB) [project]
- **uvloop_speedup**: uvloop provides 2× I/O-bound speedup on M1 [project]
- **nh3_performance**: nh3 Rust HTML sanitizer is 9× faster than BS4 with 4 MB RSS [project]
- **coremltools_python314**: coremltools 8.x and 9.x both lack Python 3.14 wheels [project]
- **dnspython_version**: dnspython 2.7 is EOL; 3.x is async-native [project]
- **xxhash_algorithm**: xxhash uses xxhash.xxh3_64() (compatible with Rust xxh3_64) [project]
- **transformers_version_collision**: transformers>=5.10.2 satisfies both dspy>=3.2.1 and flashrank [project]
- **posix_ipc_platform**: posix-ipc is darwin-only for M1 zero-copy cross-process [project]

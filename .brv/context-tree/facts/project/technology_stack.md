---
title: Technology Stack
summary: Python 3.14, MLX/llm for Apple Silicon, DuckDB analytics, curl-cffi stealth HTTP, Rust extensions via PyO3, pytest with asyncio session-scope
tags: []
related: [facts/project/rust_extensions_overview.md]
keywords: []
createdAt: '2026-07-11T14:49:36.803Z'
updatedAt: '2026-07-16T11:00:35.814Z'
---
## Reason
Document project technology stack from pyproject.toml

## Raw Concept
**Task:**
Document hledac-universal technology stack and pyproject.toml configuration

**Files:**
- pyproject.toml

**Flow:**
Core: Python 3.14 + Rust backend via PyO3. ML: MLX for Apple Silicon. Storage: DuckDB (analytics), LanceDB (vectors), LMDB (KV)

**Timestamp:** 2026-07-16

## Narrative
### Structure
Technology stack includes Python 3.14 with Rust PyO3 backend, MLX for Apple Silicon ML inference, DuckDB for analytics, curl-cffi for stealth HTTP with JA3 fingerprinting, msgspec for fast serialization. Testing via pytest with asyncio auto mode and session-scoped event loop. PyO3 Rust extensions built via maturin.

### Dependencies
uv for dependency management, maturin for Rust extension builds, pytest-xdist for parallel test execution

### Highlights
M1 Apple Silicon only (darwin + arm64). DuckDB ~600MB memory limit on 8GB M1. pytest-benchmark fails at >10% regression. pytest-mock saves 30-50MB via MagicMock lifecycle. httpx >=0.28.0 for stable HTTP/2. mlx-lm 0.31.x metadata bug: specifies transformers>=5.0.0 but works with 4.x and 5.x at runtime.

### Rules
Rule 1: asyncio.run() forbidden outside __main__, tools/, tests/
Rule 2: pytest asyncio_default_fixture_loop_scope must be session for F350M-R compatibility
Rule 3: BLE001 ruff rule deferred (P1-01 audit in progress, see Issue #32)
Rule 4: pytest addopts includes -n 2 --dist=loadscope --timeout=30 -m 'not parity'

## Facts
- **python_version**: Python 3.14 (>=3.14,<3.15) required [project]
- **project_version**: hledac-universal v18.0.0 [project]
- **platform**: M1 Apple Silicon only (darwin + arm64) [project]
- **duckdb_memory**: DuckDB memory limit ~600MB on M1 8GB [project]
- **benchmark_threshold**: pytest-benchmark fails if regression >10% [project]
- **pytest_mock_memory**: pytest-mock saves 30-50MB via MagicMock lifecycle [project]
- **httpx_version**: httpx requires >=0.28.0,<0.30.0 for stable HTTP/2 [project]
- **http3_memory**: aioquic http3 adds 50-80MB resident memory [project]
- **mlx_platform**: mlx-embeddings only for sys_platform==darwin [project]
- **uvloop_speedup**: uvloop provides ~2x I/O-bound speedup on M1 kqueue [project]
- **rust_build**: Rust extensions build: maturin develop --release [project]

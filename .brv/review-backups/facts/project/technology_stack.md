---
title: Technology Stack
summary: Python 3.14 with Rust backend, MLX for Apple Silicon, DuckDB/LanceDB/LMDB storage, key dependencies listed
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:49:36.803Z'
updatedAt: '2026-07-11T14:49:36.803Z'
---
## Reason
Documenting project technology stack from pyproject.toml context

## Raw Concept
**Task:**
Document project technology stack and dependencies

**Files:**
- pyproject.toml

**Flow:**
Core: Python 3.14 + Rust backend via PyO3. ML: MLX for Apple Silicon. Storage: DuckDB (analytics), LanceDB (vectors), LMDB (KV)

**Timestamp:** 2026-07-11

## Narrative
### Structure
Technology stack: Python 3.14 core, Rust backend via PyO3, MLX for Apple Silicon ML, DuckDB/LanceDB/LMDB for storage

### Dependencies
Rust extensions via PyO3 for performance-critical code

### Highlights
mlx-lm for LLM inference, nodriver for browser automation, yara-python for pattern matching, igraph for graph ops

## Facts
- **python_version**: Python version is 3.14 [project]
- **backend_language**: Backend is written in Rust with PyO3 extensions [project]
- **ml_framework**: MLX is used for Apple Silicon machine learning [project]
- **database**: DuckDB is used for analytical queries [project]
- **vector_store**: LanceDB is used for vector storage [project]
- **kv_store**: LMDB is used for key-value storage [project]
- **llm_dependency**: mlx-lm is a key dependency for LLM inference on Apple Silicon [project]
- **json_library**: orjson is used for fast JSON serialization [project]
- **http_client**: curl_cffi is used for HTTP requests [project]
- **bloom_filter**: pybloom_live is used for bloom filters [project]
- **system_utils**: psutil is used for system monitoring [project]
- **graph_library**: igraph is used for graph operations [project]
- **browser_automation**: nodriver is used for browser automation [project]
- **pattern_matching**: yara-python is used for pattern matching [project]
- **serialization**: msgspec is used for serialization [project]
- **rust_integration**: Rust extensions are built via PyO3 [project]

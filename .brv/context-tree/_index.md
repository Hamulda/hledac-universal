---
children_hash: dc8211803d074eac978cfdb0bd85663420ddf46d65f35c5a9cd7ae91745b13d3
compression_ratio: 0.5
condensation_order: 3
covers: [facts/_index.md, testing/_index.md]
covers_token_total: 1320
summary_level: d3
token_count: 660
type: summary
---
<think>
The user wants me to create a domain-level summary (d1) for the `facts/project/` domain. They've provided the child entries and want me to synthesize them into a coherent domain summary that condenses the information while preserving key facts, relationships, and patterns.

Key entries to synthesize:
1. configuration_constants - DuckDB memory (600MB, 2 threads), Shodan rate limits (360/10s), feature flags, evidence_log timeout issue
2. hashing_facade_issue_2 - centralized hashing utility at utils/hashing.py
3. hledac_universal_claude_md - project conventions and entry point
4. technology_stack - Python 3.14, Rust, PyO3, MLX, DuckDB, LanceDB, LMDB, nodriver, yara-python, igraph
5. xxhash_rust_implementation - xxhash-rust v0.8 with M1 NEON SIMD acceleration

Let me create a condensed summary that:
- Provides an overview
- Condenses key facts
- Preserves relationships
- Targets ~20-30% compression
- Is clean markdown
</think>

# Domain: facts/project

## Overview
Project configuration, architecture decisions, and technology stack for the Hledac Universal OSINT orchestrator.

## Core Technology Stack
- **Python 3.14** with **Rust via PyO3/Maturin** for performance-critical paths
- **MLX** + mlx-lm for Apple Silicon ML inference
- **DuckDB** for analytics, **LanceDB** for vectors, **LMDB** for bulk key-value
- **nodriver** (browser automation), **yara-python** (pattern matching), **igraph** (graph analysis)

## DuckDB Configuration
Memory capped at **600MB** with **2 threads** (optimized for 8GB M1 systems). Feature gates via `HLEDAC_ARROW_INGEST`, `HLEDAC_DUCKDB_QUERY_CACHE`, `HLEDAC_DUCKDB_RAMDISK_TEMP`, `HLEDAC_ARROW_MIN_BATCH`. Known issue: evidence_log timeout mismatch (configured 1000ms vs actual 30000ms).

## Hashing Architecture
Centralized facade at `utils/hashing.py` wraps xxhash-rust v0.8 (xxh3/xxh64 with M1 NEON SIMD) and BLAKE3-64. Provides single/batch variants (xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, blake3_64_hex). Dual-lane URL processing uses rayon 2-thread parallelism. Expected 10x speedup on M1 hardware.

## Entry Conventions
Entry point: `python -m hledac.universal --sprint "QUERY" [--duration SECS]`. Async pattern: asyncio.gather with return_exceptions=True. No time.sleep() in async contexts. Call mx.eval([]) before clear_cache(). Storage: async_ingest_findings_batch() for DuckDB, cursor.putmulti() for LMDB bulk ops. RotatingBloomFilter for deduplication.

## Key Relationships
Tech stack enables all architectural decisions. Hashing facade depends on xxhash-rust for performance. Project conventions in hledac_universal_claude_md validated by configuration_constants.
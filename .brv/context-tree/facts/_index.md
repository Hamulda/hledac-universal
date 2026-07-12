---
children_hash: d67c6b3fad6577ad13852562995c706bf03ea52b4134acaa8fa5fc10b3dd74c2
compression_ratio: 0.9175757575757576
condensation_order: 2
covers: [project/_index.md]
covers_token_total: 825
summary_level: d2
token_count: 757
type: summary
---
<think>
The user wants me to create a structural summary at the domain level (d1) for the `facts/project/` domain. They've provided the child entries and want me to synthesize them into a coherent domain summary that condenses the information while preserving key facts, relationships, and patterns.

Let me create a clean markdown summary that:
1. Provides an overview of the domain
2. Condenses the child entries into a structural overview
3. Preserves key relationships
4. Targets ~20-30% compression
5. Is clean structured markdown with no code blocks or XML tags
</think>

# facts/project Domain Summary

## Domain Overview

Contains project configuration, architecture decisions, and technology stack knowledge for the Hledac Universal OSINT orchestrator system. This domain serves as the authoritative reference for technology choices, configuration constants, and core project conventions.

## Core Technology Stack

- **Language**: Python 3.14 with Rust via PyO3 for performance-critical paths
- **ML**: MLX for Apple Silicon acceleration, mlx-lm for inference tasks
- **Storage**: DuckDB (analytics/queries), LanceDB (vector operations), LMDB (bulk key-value)
- **Automation**: nodriver for browser automation, yara-python for pattern matching
- **Graph**: igraph for network analysis and graph operations

## Configuration Constants

DuckDB memory capped at 600MB with 2 threads (optimized for 8GB M1 systems). Shodan API rate limit set to 360 requests per 10 seconds. Feature gates controlled via environment variables: `HLEDAC_ARROW_INGEST`, `HLEDAC_DUCKDB_QUERY_CACHE`, `HLEDAC_DUCKDB_RAMDISK_TEMP`, `HLEDAC_ARROW_MIN_BATCH=5`. Known issue: evidence_log timeout mismatch between configured 1000ms and actual 30000ms.

## Architecture Patterns

Dual HTTP/3 strategy using curl_cffi for fallback and aioquic for native QUIC. Centralized hashing facade at `utils/hashing.py` with xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, and blake3_64_hex functions. Rust xxhash-rust v0.8 with xxh3/const_xxh3/xxh64 features provides M1 NEON SIMD acceleration. Canonical URL processing uses rayon 2-thread parallelism.

## Entry Conventions

Standard entry point: `python -m hledac.universal --sprint "QUERY" [--duration SECS]`. Async patterns: use asyncio.gather with return_exceptions=True, never time.sleep() in async contexts, call mx.eval([]) before clear_cache(). Storage patterns: DuckDB writes via async_ingest_findings_batch(), LMDB bulk operations via cursor.putmulti(). Use RotatingBloomFilter for deduplication. Crypto-grade hashing reserved for forensics/security/stealth/vault use cases.

## Key Relationships

The technology stack provides the foundation for all architectural decisions. The hashing facade depends on xxhash-rust implementation for performance. Project conventions documented in hledac_universal_claude_md reference settings validated by configuration_constants. The 10x speedup expectation for hashing operations relies on Rust NEON SIMD acceleration via xxhash-rust and BLAKE3-64 on M1 hardware.
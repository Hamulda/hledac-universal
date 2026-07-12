---
children_hash: 186e7c3747036b37897a0c705cd5014cf8610cd9f8e6bd07527f4c611ebfd64c
compression_ratio: 0.2827932098765432
condensation_order: 1
covers: [configuration_constants.md, hashing_facade_issue_2.md, hledac_universal_claude_md.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 2592
summary_level: d1
token_count: 733
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. Let me analyze the entries and create a coherent summary.

The entries are all in the `facts/project/` domain and cover:
1. configuration_constants.md - DuckDB settings, shodan rate, env gates
2. hashing_facade_issue_2.md - ISSUE #2 completion with xxh3 facade
3. hledac_universal_claude_md.md - Project conventions, architecture, rules
4. technology_stack.md - Python 3.14, Rust, MLX, DuckDB/LanceDB/LMDB
5. xxhash_rust_implementation.md - xxhash-rust crate usage

I need to create a domain-level summary that:
- Condenses child entries into structural overview
- Preserves key facts, relationships, patterns
- References child entry names for drill-down
- Targets ~20-30% compression
- Preserves entity names, file paths, API signatures, architectural decisions
- Outputs clean structured markdown
- No code blocks or XML tags
</think>

# facts/project/ Domain Summary

## Overview
Contains project configuration, architecture decisions, and technology stack knowledge for Hledac Universal OSINT orchestrator.

## Child Entry Summaries

### configuration_constants.md
DuckDB memory limits (600MB), threads (2 for M1 8GB), shodan rate (360/10s). Environment gates: HLEDAC_ARROW_INGEST, HLEDAC_DUCKDB_QUERY_CACHE, HLEDAC_DUCKDB_RAMDISK_TEMP, HLEDAC_ARROW_MIN_BATCH=5. Known timeout mismatch: evidence_log 1000ms configured vs 30000ms actual.

### hashing_facade_issue_2.md
ISSUE #2 completed: centralized hashing facade at `utils/hashing.py` with xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, blake3_64_hex. Hot paths migrated: 7 calls in `utils/deduplication.py`, 1 in `tools/url_dedup.py`. Crypto-grade hashing preserved for forensics/security/stealth/vault. Expected ~10x speedup via Rust NEON SIMD.

### hledac_universal_claude_md.md
Core conventions: DuckDB writes via async_ingest_findings_batch(), LMDB bulk via cursor.putmulti(), asyncio.gather with return_exceptions=True, mx.eval([]) before clear_cache(), RotatingBloomFilter, no time.sleep() in async, no bare except. Entry: `python -m hledac.universal --sprint "QUERY" [--duration SECS]`. HTTP/3 dual strategy with curl_cffi + aioquic.

### technology_stack.md
Python 3.14 + Rust via PyO3, MLX for Apple Silicon ML, DuckDB/LanceDB/LMDB storage trinity, mlx-lm for inference, nodriver for browser automation, yara-python for pattern matching, igraph for graph operations.

### xxhash_rust_implementation.md
xxhash-rust v0.8 with xxh3/const_xxh3/xxh64 features. Used in canonical_url_batch (rayon 2-thread parallel) and URL fingerprinting. M1 NEON acceleration via BLAKE3-64.

## Key Relationships
- **hashing_facade** depends on **xxhash_rust_implementation** for Rust NEON SIMD performance
- **hledac_universal_claude_md** references conventions validated by **configuration_constants**
- **technology_stack** provides the foundation for all other entries
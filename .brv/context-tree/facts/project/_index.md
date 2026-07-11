---
children_hash: c13d3dfb7f273ce3c44097e21dacb6a7a5ccd3d1fc2afa90eb42a205b9fbec84
compression_ratio: 0.4192903548225887
condensation_order: 1
covers: [configuration_constants.md, hledac_universal_claude_md.md, technology_stack.md, xxhash_rust_implementation.md]
covers_token_total: 2001
summary_level: d1
token_count: 839
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries. These are all from the `facts/project` domain in the context tree. Let me analyze the key facts, relationships, and patterns across these entries:

1. **configuration_constants.md** - DuckDB settings, Shodan rate, environment gates, evidence_log timeout
2. **hledac_universal_claude_md.md** - Comprehensive project documentation with 10 invariants, feature flags, architecture
3. **technology_stack.md** - Python 3.14, Rust/PyO3, MLX, DuckDB/LanceDB/LMDB storage
4. **xxhash_rust_implementation.md** - xxhash-rust for URL hashing with NEON acceleration

Key patterns and relationships:
- Hardware: M1 8GB UMA - important constraint
- Python 3.14 + Rust backend via PyO3
- Storage trinity: DuckDB, LanceDB, LMDB
- ML: MLX for Apple Silicon
- 10 critical invariants for stability
- 50+ feature flags
- Pre-flight guards with --force override
- HTTP/3 dual strategy
- Exit code conventions
- DuckDB specific: 600MB limit, 2 threads for M1, async_ingest_findings_batch only write path

Let me create a condensed structural summary that preserves these facts and relationships.
</think>

# Project Configuration & Technology Stack (facts/project)

## Overview

Core project facts documenting Hledac Universal OSINT orchestrator's configuration constants, technology stack, and critical conventions.

## Technology Stack

- **Runtime**: Python 3.14 + Rust backend via PyO3
- **ML**: MLX for Apple Silicon (Hermes-3-Llama-3.2-3B-4bit)
- **Storage Trinity**: DuckDB (analytics), LanceDB (vectors), LMDB (key-value)
- **Key Dependencies**: mlx-lm, curl_cffi, orjson, msgspec, nodriver, yara-python, igraph, pybloom_live, psutil

## DuckDB Configuration

| Setting | Value |
|---------|-------|
| Memory limit | 600MB (GHOST_DUCKDB_MEMORY) |
| Max temp | 1GB (GHOST_DUCKDB_MAX_TEMP) |
| Thread count | 2 (optimal for M1 8GB) |
| Write path | `async_ingest_findings_batch()` only |

## Environment Feature Gates

- `HLEDAC_ARROW_INGEST`: ON
- `HLEDAC_DUCKDB_QUERY_CACHE`: OFF (F320-2)
- `HLEDAC_DUCKDB_RAMDISK_TEMP`: None
- `HLEDAC_ARROW_MIN_BATCH`: 5
- `HLEDAC_RG_USE_RATIOS`: 0 (enables absolute GiB mode)

## Hardware Constraints (M1 8GB UMA)

- Hard cap: ~6.25GB total budget
- Swap threshold: 3.8 GiB (F265D)
- NEON acceleration via BLAKE3-64 for body hashing

## External Service Limits

- Shodan rate: 36 requests per 10 seconds (360/10 formula)

## Critical Invariants (10 rules)

1. No bare `except:` — always `except Exception:`
2. `asyncio.gather` always with `return_exceptions=True`
3. `mx.eval([])` before `mx.metal.clear_cache()`
4. DuckDB writes ONLY via `async_ingest_findings_batch()`
5. LMDB bulk via `cursor.putmulti()` — never per-item
6. Fail-safe: return `[]` on errors
7. No `time.sleep()` in async — use `asyncio.sleep()`
8. No `asyncio.run()` in ThreadPoolExecutor — use `loop.run_until_complete()`
9. Use `RotatingBloomFilter` not `ScalableBloomFilter`
10. `sys.exit()` propagates verbatim

## Known Issues

- evidence_log timeout mismatch: configured 1000ms vs actual 30000ms

## Related Entries

- `duckdb_thread_count_and_settings` — detailed DuckDB threading
- `duckdb_shadow_store` — DuckDB storage implementation
- `configuration_constants` — all environment gates
- `hledac_universal_architecture` — sprint lifecycle pipeline
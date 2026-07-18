---
children_hash: f8bc71604990e63cae6b189c3e4ad35788b57954c90216a7c4276491de3e6b4e
compression_ratio: 0.0921496227323808
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 6229
summary_level: d3
token_count: 574
type: summary
---
<think>
The user wants me to create a structural summary at the d1 level (domain level) for the memory_leaks domain. Looking at the structure, I need to create a concise ~20-30% compression that captures the core theme, architecture, key facts, and relationships from the child entries.

From the child entries provided:
1. **context.md** - Overview of the domain
2. **f350m_r_memory_leak_fixes.md** - Sprint f350mr memory leak fixes (4 major fixes)
3. **uma_memory_management.md** - M1 8GB unified memory management
4. **m1_8gb_ram_priority_optimizations.md** - Priority optimizations for M1 8GB RAM
5. **m1resourcegovernor_implementation.md** - Resource governor implementation details
6. **rust_mpsc_architecture_issue_007.md** - Rust MPSC architecture fix

The core theme is M1 8GB RAM optimization and memory leak fixes. I need to synthesize this into a concise domain summary.

Let me create a structured summary:
</think>

# memory — Domain Summary

## Core Theme
M1 8GB Unified Memory (UMA) is the primary system constraint. The domain covers resource governor implementation, memory leak fixes, and M1-specific optimizations across storage, hashing, and ML pipelines.

## Architecture

### Resource Governor
5-state hysteresis machine (NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED) with concurrency presets. Pre-flight guards abort sprints if memory pressure exceeds thresholds.

### Memory Budget
- DuckDB: 600MB (hard limit)
- Metal cache: 512MiB–1GiB dynamic (wired 1.5 GiB max)
- aioquic HTTP/3: Blocked at 5.5 GiB RSS
- KV cache: 4-bit quantization, max 8192 tokens
- Total headroom: ~2.25GB

## Key Fixes (f350mr)

| Issue | Fix |
|-------|-----|
| `asyncio.Task` leaks | `loop.run_until_complete()` in shutdown, not `asyncio.run()` |
| Hermes3Engine | `mx.eval([])` before `mx.metal.clear_cache()` |
| DuckDB threads | Capped at 2 for M1 8GB |
| Evidence log | Shared MPSC pool (max_workers=4) via ISSUE-007 |

## Rust MPSC Architecture (Issue-007)
2048 slots × 512B = 1 MiB per crossbeam channel. `send_batch()` reduces latency from ~5µs to ~1µs per event.

## Related Entries
- `architecture/hledac_universal` — Sprint lifecycle
- `data/duckdb_store` — DuckDB memory limits
- `facts/project/technology_stack` — M1 constraints
- `testing/conftest` — Memory profiling fixtures
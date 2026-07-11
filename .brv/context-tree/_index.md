---
children_hash: aa3e0b3a3396e67713911f706c0c16931361b20a57cc1c9be300950e0322c4f1
compression_ratio: 0.8203592814371258
condensation_order: 3
covers: [testing/_index.md]
covers_token_total: 501
summary_level: d3
token_count: 411
type: summary
---
<think>
The user is asking me to create a domain-level structural summary for the "architecture" domain based on the provided context tree structure. They've given me detailed information about what the domain contains and want me to output a concise summary.

Let me craft a clean, concise summary that captures the essence of the architecture domain while being much shorter than the input (~20-30% token reduction).
</think>

# Domain: architecture

Core architecture for hledac.universal — a Rust-extended Python IOC processing engine with multi-lane HTTP transport, DuckDB shadow stores, and adaptive memory management.

## Key Components

**3-Lane HTTP Transport**
- Cache lane (primary): Redis/sentinel fallback
- CDN lane (secondary)
- Public fetcher lane (tertiary)
- Adaptive circuit breaker per lane

**Rust Extensions**
- Async execution via PyO3/Maturin
- Cross-platform builds: macOS x86/x64, Linux x64
- Compiled to `hledac_cext-[sys].so`

**DuckDB Shadow Stores**
- Query cache with IOC extraction
- Configurable thread count settings
- Extended schema for shadow operations

**Resource Governor**
- UMA-based memory management
- Adaptive scheduler with cooldown/retry logic
- Per-mode resource limits (discover, feed, hunt, trace)

**Sprint Lifecycle Pipeline**
- Feature flags: `f221_abort_on_windup` guard, `mlx_memory_pressure`
- Exit code testing and memory leak fixes

## Related Topics
- `data/duckdb_store` — DuckDB persistence layer
- `memory/resource_governor` — UMA memory management
- `facts/project/technology_stack` — Python, Rust, PyO3, Maturin, DuckDB
- `testing/` — Exit codes, memory leaks, test infrastructure
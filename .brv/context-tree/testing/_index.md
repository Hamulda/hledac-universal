---
children_hash: ba3cc80e0186bbd806d53792c1899451d7e80a6408e60cece4af6eee04279486
compression_ratio: 0.3990740740740741
condensation_order: 2
covers: [exit_codes/_index.md, memory_leaks/_index.md]
covers_token_total: 1080
summary_level: d2
token_count: 431
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about the hledac_universal architecture. I need to preserve key facts, architectural decisions, and relationships while compressing to ~20-30% of input tokens.

Key information to preserve:
- 3-lane HTTP architecture (cache, CDN, public fetcher)
- Rust extensions for async execution
- DuckDB shadow stores
- Resource governor with UMA
- Sprint lifecycle pipeline
- Project docs, tech stack

Let me create a concise summary.
</think>

# Domain: architecture

## Overview
Core architecture for hledac.universal — a Rust-extended Python IOC processing engine with 3-lane HTTP transport, DuckDB shadow stores, and adaptive memory management.

## Key Components

**3-Lane HTTP Transport**
- Cache lane (primary): Redis/sentinel fallback
- CDN lane (secondary)
- Public fetcher lane (tertiary)
- Implements `Http3Lane` trait with adaptive circuit breaker

**Rust Extensions**
- Async execution via PyO3/Maturin
- Cross-platform build: macOS x86/x64, Linux x64
- Compiled to `hledac_cext-[sys].so`

**DuckDB Shadow Stores**
- Query cache with IOC extraction
- Thread count settings for performance
- Shadow store extended schema

**Resource Governor**
- UMA-based memory management
- Adaptive scheduler with cooldown/retry logic
- Per-mode limits (discover, feed, hunt, trace)

**Sprint Lifecycle**
- Sprint F350M-R: Exit codes, memory leaks, conftest.py
- Feature flags: `f221_abort_on_windup` guard, `mlx_memory_pressure`

## Related Topics
- `data/duckdb_store` — DuckDB persistence
- `memory/resource_governor` — UMA memory management
- `facts/project/technology_stack` — Python, Rust, PyO3, Maturin, DuckDB
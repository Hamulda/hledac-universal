---
children_hash: e5ffa8b97caefff72e70c96e1fd3f35b7963197382855fabfda8f4af214523bb
compression_ratio: 0.39405747686312714
condensation_order: 3
covers: [facts/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 2053
summary_level: d3
token_count: 809
type: summary
---
<think>
The user has provided a detailed domain summary for the `architecture/` domain and its child topics. They want me to create a structural summary at level d1 (domain level) that synthesizes this information.

Based on the input, I need to create a clean markdown summary that:
1. Provides an overview of the architecture domain
2. Condenses the child entries into a structural overview
3. Preserves key relationships and architectural decisions
4. Targets ~20-30% compression

The child entries cover:
- hledac_universal architecture (context.md, critical_invariants, http_3_lane_and_public_fetcher, sprint_lifecycle_pipeline)
- transport_layers (http_3_lane_implementation)
- DuckDB shadow stores
- Resource governor
- Sprint lifecycle and testing

Let me create a clean d1 domain summary for `architecture/` that synthesizes this.
</think>

# Domain: architecture

## Overview

Documents the system architecture for hledac.universal — an OSINT orchestrator that combines Python for orchestration with Rust extensions for performance-critical operations. Key architectural patterns include the 3-lane HTTP transport strategy, DuckDB shadow stores, adaptive resource management, and an iterative sprint lifecycle.

## Key Architectural Decisions

**3-Lane HTTP Transport**: Implements layered fallback via `Http3Lane` trait. Primary cache lane uses Redis with sentinel support; secondary CDN lane handles static assets; tertiary public fetcher lane aggregates multiple sources. Circuit breaker adapts per-lane capacity based on success rates.

**Rust Extensions via PyO3/Maturin**: Critical paths (hashing, network I/O) implemented in Rust and compiled to `hledac_cext-[sys].so`. macOS builds support both x64 and ARM64. Python async event loop integrates Rust futures.

**DuckDB Shadow Stores**: Analytics layer with query cache and IOC extraction. Thread count tuned per hardware (2 threads on 8GB M1 systems). Supports both in-memory and persistent modes.

**UMA Resource Governor**: Memory management using User Memory Allocator patterns with adaptive throttling. Hysteresis state machine (ok → soft_warn → warn → critical → emergency) controls concurrency presets and fetch limits.

**Sprint Lifecycle Pipeline**: Automated sprints drive continuous integration. Each sprint produces exit codes, memory leak validation, and feature flag evolution.

## Domain Relationships

```
architecture/
├── hledac_universal/          # Parent architecture, critical invariants
├── transport_layers/           # HTTP 3-lane implementation
├── data/duckdb_store/          # DuckDB shadow stores (related domain)
└── memory/resource_governor/   # Resource governor (related domain)
```

## Key Entries

- `hledac_universal/` — System overview, HTTP transport, sprint lifecycle
- `transport_layers/http_3_lane_implementation` — Lane trait and circuit breaker
- `data/duckdb_store/` — Query cache, thread settings, IOC extraction
- `memory/resource_governor/` — UMA memory management, adaptive scheduler

## Entry Conventions

- Rust extension source in `rust_extensions/src/`
- Python entry point: `python -m hledac.universal`
- DuckDB configured via `duckdb_config` section
- Resource governor thresholds defined in `resource_governor.py`
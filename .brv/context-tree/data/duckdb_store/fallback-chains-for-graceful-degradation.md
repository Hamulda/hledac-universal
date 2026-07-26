---
confidence: 0.7
sources: [data/duckdb_store/_index.md, transport_layers/_index.md, facts/project/_index.md]
synthesized_at: '2026-07-26T11:44:30.880Z'
type: synthesis
title: Fallback Chains for Graceful Degradation
summary: IOC extraction (3-tier), HTTP/3 (2-strategy), and DuckDB (in-process vs isolated) use fallback chains rather than hard failures
tags: [fallback, graceful-degradation, resilience, architecture]
related: []
keywords: [fallback, graceful-degradation, tier, degradation, resilience, optional-dependency]
createdAt: '2026-07-26T11:44:30.880Z'
updatedAt: '2026-07-26T11:44:30.880Z'
---

# Fallback Chains for Graceful Degradation

The architecture prefers graceful degradation: curl_cffi → aioquic → httpx for HTTP; Python → PyO3 → Rayon for IOC; in-process DuckDB vs isolated writer. This pattern reduces fragility but complicates testing.

## Evidence

- **data/duckdb_store**: IOC extraction 3-tier fallback: batch_ioc_extract_unified_python → Rayon/PyO3 → pure Python
- **transport_layers**: HTTP/3: curl_cffi_opportunistic (default) → aioquic_stealth (opt-in)
- **facts/project**: DUCKDB_WRITE env var gates isolation; ThreadPoolExecutor with cloudpickle for stateful closures

---
confidence: 0.85
sources: [facts/project/_index.md, duckdb_store/_index.md, hledac_universal/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-18T00:18:19.623Z'
type: synthesis
title: Environment Gates Control Feature Activation
summary: 14+ HLEDAC_* environment variables gate Arrow ingest, DuckDB tuning, query cache, and resource governor.
tags: [config, feature-flags, environment, gates]
related: []
keywords: [hledac-env, feature-gates, environment-variables, duckdb-config, arrow-ingest, query-cache]
createdAt: '2026-07-18T00:18:19.623Z'
updatedAt: '2026-07-18T00:18:19.623Z'
---

# Environment Gates Control Feature Activation

Feature activation is exclusively via environment variables with consistent HLEDAC_ prefix. Key gates: HLEDAC_ARROW_INGEST (ON), HLEDAC_DUCKDB_QUERY_CACHE (OFF), HLEDAC_RG_USE_RATIOS (0=absolute GiB), HLEDAC_DUCKDB_INPROCESS (True saves ~200MB).

## Evidence

- **facts/project**: HLEDAC_ARROW_INGEST=ON, HLEDAC_DUCKDB_QUERY_CACHE=OFF, HLEDAC_RG_USE_RATIOS=0
- **duckdb_store**: HLEDAC_DUCKDB_MEMORY (600MB-2GB), HLEDAC_DUCKDB_THREADS (2 M1 optimal)
- **hledac_universal**: Feature flags reference: 50+ flags documented, gate behavior clearly defined
- **memory/resource_governor**: HLEDAC_RG_USE_RATIOS for absolute vs ratio-based memory mode

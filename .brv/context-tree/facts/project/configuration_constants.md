---
title: Configuration Constants
summary: 'Project configuration: DuckDB memory limits, shodan rate formula, env feature gates, evidence_log timeout'
tags: []
related: [facts/project/environment-gates-control-feature-activation.md]
keywords: []
createdAt: '2026-07-11T14:50:41.390Z'
updatedAt: '2026-07-11T14:50:41.390Z'
---
## Reason
Document key configuration constants and environment gates

## Raw Concept
**Task:**
Document project configuration constants and environment variable gates

**Changes:**
- F320-2: DuckDB query cache feature gate
- F266-2.3: IOC extraction optimization

## Narrative
### Structure
Environment gates control feature behavior: HLEDAC_ARROW_INGEST=ON, HLEDAC_DUCKDB_QUERY_CACHE=OFF, HLEDAC_DUCKDB_RAMDISK_TEMP=None, HLEDAC_ARROW_MIN_BATCH=5

### Dependencies
HLEDAC_RG_USE_RATIOS=0 enables absolute GiB mode for thresholds

### Highlights
Evidence_log has known timeout mismatch (1000ms configured vs 30000ms actual) needing resolution

## Facts
- **duckdb_memory_limit**: DuckDB memory limit is 600MB (GHOST_DUCKDB_MEMORY) [project]
- **duckdb_max_temp**: DuckDB max temp is 1GB (GHOST_DUCKDB_MAX_TEMP) [project]
- **duckdb_threads**: DuckDB optimal thread count is 2 for M1 8GB [project]
- **shodan_rate**: Shodan rate limit is 36 requests per 10 seconds (360/10) [project]
- **evidence_log_timeout_mismatch**: evidence_log configured busy_timeout is 1000ms but actual is 30000ms [project]
- **m1_8gb_uma_cap**: M1 8GB UMA hard cap is approximately 6.25GB total budget [project]
- **swap_threshold**: swap_detected threshold is 3.8 GiB (F265D) [project]

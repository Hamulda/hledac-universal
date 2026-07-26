---
title: Known Issues and TODOs
summary: 'Active tech debt: busy_timeout 1s vs 30s, shodan rate 36 vs 360, DuckPGQGraph gap'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:44.120Z'
updatedAt: '2026-07-26T11:18:44.120Z'
---
## Reason
Documenting known tech debt items from codebase review

## Raw Concept
**Task:**
Document known TODOs and issues in the codebase

**Files:**
- transport/http3_lane.py
- core/locks.py
- runtime/scheduler_v2/acquisition.py
- recon/social_identity_miner.py
- recon/stealth/_models.py

**Timestamp:** 2026-07-26

## Narrative
### Structure
Known TODOs tracked in codebase with specific file locations and expected fixes

### Highlights
8 known issues documented with file paths, current values, and expected values where applicable

### Rules
Rule 1: probe_* directories may be archived or removed - verify existence before relying on them

## Facts
- **evidence_log_timeout**: evidence_log busy_timeout is 1000ms but should be 30000ms [project]
- **shodan_rate**: shodan rate formula is 360/10=36 but should be 3600/10=360 [project]
- **duckpgqgraph_integration**: DuckPGQGraph integration gap: DuckDB for sprint facts vs DuckPGQGraph for IOC storage [project]
- **neqo_http3**: neqo not yet on PyPI (F320-TODO) - HTTP/3 via neqo pending [project]
- **lock_free_counter**: Rust AtomicCounter (issue #5) not implemented for lock-free counter [project]
- **acquisition_prioritization**: Prioritization using ctx.graph_stats not implemented in acquisition.py [project]
- **social_identity_detection**: Platform detection for social identity mining not implemented [project]
- **domain_filtering**: Domain allowlist/blocklist checks not implemented in stealth models [project]

---
title: Phase 3 Documentation and Knowledge Gaps
summary: 'Phase 3 KB audit (2026-07-27): documents exist in CLAUDE.md/AGENTS.md/GHOST_INVARIANTS.md, gaps remain in conventions/integrations/testing guides, probe_tests gap is stale'
tags: []
related: [knowledge_base/audit/documentation_coverage_assessment_2026_07_27.md]
keywords: []
createdAt: '2026-07-27T13:12:15.005Z'
updatedAt: '2026-07-27T13:12:15.005Z'
---
## Reason
Document Phase 3 KB audit findings - documentation coverage and gaps

## Raw Concept
**Task:**
Phase 3 KB audit - document documentation coverage and gaps

**Changes:**
- Confirmed existing docs: CLAUDE.md, AGENTS.md, GHOST_INVARIANTS.md, CONTEXT.md
- Confirmed missing: docs/conventions/, docs/integrations/, testing guide
- Stale gap resolved: probe_p12/ and probe_p14/ no longer exist
- docs/ directory sparse - only 3 items

**Files:**
- .claude/CLAUDE.md
- docs/ISSUE-038-LAYERS-REORGANIZATION.md
- docs/ioc_types.md

**Flow:**
Phase 1 + 3 exploration -> Gap identification -> Gap confirmation -> Documentation

**Timestamp:** 2026-07-27

**Author:** KB Audit Session

## Narrative
### Structure
KB audit organized by: EXISTS docs, MISSING docs, Knowledge Gaps, Conventions analysis, Integrations analysis, Testing Coverage

### Dependencies
References prior KB audits from 2026-07-11 and 2026-07-16

### Highlights
CLAUDE.md is comprehensive (critical invariants, feature flags, exit codes, anti-patterns). AGENTS.md covers RTK/workbench patterns. Stale probe_tests gap cleared - old dirs removed.

### Rules
Rule 1: Conventions only exist in CLAUDE.md sections - no dedicated docs/conventions/
Rule 2: Integrations docs missing for Tor/I2P, Shodan/Censys, DuckDB/LanceDB
Rule 3: Testing has 230+ test files but no dedicated testing guide
Rule 4: probe_tests gap (2026-07-11) was stale - old probe_p12/ and probe_p14/ cleaned up

## Facts
- **documentation_main**: CLAUDE.md contains comprehensive project overview, critical invariants, feature flags, exit code convention [project]
- **conventions_gap**: docs/conventions/ directory does not exist - conventions only in CLAUDE.md sections [project]
- **integrations_gap**: docs/integrations/ directory does not exist - HTTP/3/Tor/I2P scattered in CLAUDE.md [project]
- **probe_tests_stale_gap**: probe_p12/ and probe_p14/ directories no longer exist in codebase [project]
- **docs_sparsity**: docs/ directory contains only 3 items (2 docs + .DS_Store) [project]
- **test_coverage**: 230+ test files exist with organization in tests/, tests/cli/, tests/rust/, tests/unit/ [project]

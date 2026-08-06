---
children_hash: a3f764163556b341bf4497b796d1f945d00eb6b49e88a292ec8480a29f72031f
compression_ratio: 0.36192062170413547
condensation_order: 1
covers: [context.md, documentation_coverage_assessment_2026_07_16.md, documentation_coverage_assessment_2026_07_27.md, duckpgqgraph-flagged-as-weak-despite-core-role.md, kb_audit_2026_07_11.md, kb_audit_gap_fix_2026_07_26.md, kb_audit_gap_status_2026_07_26.md, knowledge_audit_2026_07_26.md, phase_3_documentation_and_knowledge_gaps.md]
covers_token_total: 3603
summary_level: d1
token_count: 1304
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about knowledge base audits. I need to:

1. Condense the child entries into a structural overview
2. Preserve key facts, relationships, and patterns
3. Reference entry names for drill-down
4. Target ~20-30% compression
5. Preserve entity names, file paths, architectural decisions, key relationships
6. Discard verbose examples, repeated explanations, secondary detail, conversational filler
7. Output clean structured markdown
8. NOT wrap in code blocks or XML tags
9. NOT use any tools

Let me analyze the entries:

1. **context.md** - Overview of the audit topic, key concepts (gap analysis, stale content, documentation coverage, content maintenance)

2. **documentation_coverage_assessment_2026_07_16.md** - Full assessment with EXCELLENT (9 areas), GOOD (3), WEAK (6). Key configs documented.

3. **documentation_coverage_assessment_2026_07_27.md** - Updates after adding docs/integrations/ and docs/conventions/. Missing ADR, API ref, contribution guide.

4. **duckpgqgraph-flagged-as-weak-despite-core-role.md** - Synthesis noting DuckPGQGraph is central but WEAK in docs.

5. **kb_audit_2026_07_11.md** - Initial audit finding 4 gaps: exit codes, naming/conventions, integrations, probe_tests

6. **kb_audit_gap_fix_2026_07_26.md** - Gap fix status: 2 resolved, 2 open

7. **kb_audit_gap_status_2026_07_26.md** - Tracking 4 items, conventions exist in CLAUDE.md

8. **knowledge_audit_2026_07_26.md** - Timestamp entry

9. **phase_3_documentation_and_knowledge_gaps.md** - Phase 3 audit findings


10. **Documentation Inventory** - Established foundation includes CLAUDE.md, AGENTS.md, GHOST_INVARIANTS.md, and CONTEXT.md. Several gaps identified: ADR documentation, API reference material, and contribution guidelines remain absent. The coverage assessment reveals strong documentation in critical invariants, feature flags, hardware constraints, storage architecture, sprint pipeline, HTTP/3 approach, and DuckDB configuration. Testing patterns, Rust extensions, and Brain module documentation are solid but need expansion. Sidecar protocol, DuckPGQGraph, Evidence log, Pre-flight guards, Layer protocol, and WAL/IPC validation are poorly documented or missing entirely.

Resolved documentation gaps include naming conventions (now in CLAUDE.md), exit codes (in tests/test_exit_codes.py), and stale probe_tests entries (removed). Two significant gaps persist: integrations documentation remains fragmented across the codebase with no dedicated docs/integrations/ section, and probe_tests content hasn't been established. DuckPGQGraph presents a particular risk since it serves as the analytics donor yet lacks adequate coverage despite its central role. The audit was conducted on 2026-07-26.
</think>

# knowledge_base/audit

## Overview
Domain for knowledge base audits tracking documentation coverage, identifying gaps, and monitoring remediation progress.

## Audit History
- **2026-07-11**: Initial audit (kb_audit_2026_07_11.md) identified 4 gaps
- **2026-07-16**: Documentation coverage assessment (documentation_coverage_assessment_2026_07_16.md)
- **2026-07-26**: Gap fixes applied; second audit (kb_audit_gap_fix_2026_07_26.md, kb_audit_gap_status_2026_07_26.md, knowledge_audit_2026_07_26.md)
- **2026-07-27**: Phase 3 audit (phase_3_documentation_and_knowledge_gaps.md, documentation_coverage_assessment_2026_07_27.md)

## Key Audit Findings

### EXCELLENT Coverage (9 areas)
Critical invariants, 50+ feature flags, M1 8GB hardware constraints, storage trinity (5 layers), sprint pipeline (8 lanes), HTTP/3 dual strategy (curl_cffi + aioquic), DuckDB config (600MB, 4 threads), KV cache (kv_bits=4, max_kv_size=8192)

### GOOD Coverage (needs expansion)
Testing patterns, Rust extensions, Brain module

### WEAK/MISSING (6 areas)
Sidecar protocol, DuckPGQGraph (core analytics donor but undocumented), Evidence log MPSC, Pre-flight guards, Layer protocol, WAL/IPC validation

### Critical Gap: DuckPGQGraph
DuckPGQGraph serves as analytics donor for IOC graph, STIX graph, and truth_write_graph with methods: path_queries, PageRank, shortest_path. Flagged as WEAK despite central role (duckpgqgraph-flagged-as-weak-despite-core-role.md).

## Gap Remediation Status

### RESOLVED
| Gap | Resolution |
|-----|------------|
| Naming/error-handling conventions | Documented in CLAUDE.md (snake_case, no bare except, asyncio.gather with return_exceptions=True, etc.) |
| Exit codes | tests/test_exit_codes.py + smoke_runner.py |
| Stale probe_tests reference | probe_p12/ and probe_p14/ cleaned up |

### OPEN
| Gap | Status |
|-----|--------|
| Integrations documentation | Created docs/integrations/ (2026-07-27) with tor-i2p-transport.md, duckdb-lancedb.md, rust-extensions.md |
| probe_tests | .brv/context-tree/testing/probe_tests/ empty |

## Missing Documentation
- **docs/adr/**: No architecture decision records
- **docs/api/**: No dedicated API reference
- **Contribution guide**: Does not exist

## Key Relationships
- Related to: facts/project/technology_stack, testing/exit_codes, knowledge_base/maintenance
- Audit findings inform: architecture/*, sprint_* contexts
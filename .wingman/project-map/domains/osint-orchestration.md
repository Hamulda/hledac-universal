# OSINT Orchestration

## Metadata

| Field | Value |
| --- | --- |
| Kind | domain |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `domains/osint-orchestration.md` |

## Summary

Core business domain: autonomous OSINT orchestrator for sprint-based intelligence gathering. M1 8GB optimized.

## Key Capabilities

- Sprint pipeline: Discovery → Dedup → Fetch → Match → Enrich → Store
- Pivot lanes: DOH, CT, WAYBACK, PASSIVE_DNS, BGP, PUBLIC
- Feed pipeline: RSS/Atom live monitoring
- RAG search: sqlite-vec backed (LanceDB deprecated)
- Graph analytics: rustworkx + pyvis
- IOC extraction: dual-path (hot Rust, cold Python)

## Evidence

- autonomous_orchestrator.py (main orchestrator)
- SprintLifecycleManager
- PivotLanePlanner for pivot expansion
- DuckDBShadowStore for sprint facts

## Use When

- Any OSINT orchestration work
- Understanding business domain

## Do Not Use When

- Understanding M1-specific optimizations (see M1-memory-domain)

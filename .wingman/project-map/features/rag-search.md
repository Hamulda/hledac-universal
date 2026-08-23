# RAG Search

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/rag-search.md` |
| Source Path | `advanced_rag/rag_orchestrator.py` |

## Summary

Bounded dual-engine RAG with sqlite-vec as primary (M1 native) and LanceDB deprecated fallback. research_and_answer() is the single public API. Bounded: MAX_SOURCES, MAX_TOKENS, MAX_CANDIDATES.

## Evidence

- Primary: utils.sqlite_vec_helpers.SqliteVecStore (zero-process, ~5MB)
- Deprecated: LanceDB fallback (>1.5GB headroom required, P6-4 migration pending)
- Dual-backend: sqlite-vec + LanceDB via RAGOrchestrator
- HLEDAC_ENABLE_ADVANCED_RAG=0 default (dormant)
- Fail-safe: any exception → empty result + warning log

## Use When

- RAG-based research answering
- Vector similarity search

## Do Not Use When

- M1 8GB production (LanceDB deprecated, sqlite-vec recommended)

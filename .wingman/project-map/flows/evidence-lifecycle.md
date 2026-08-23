# Evidence Lifecycle

## Metadata

| Field | Value |
| --- | --- |
| Kind | flow |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `flows/evidence-lifecycle.md` |

## Summary

Evidence creation, enrichment, and storage lifecycle.

## Flow

```
PageResult + PatternHits (MatchStage output)
  └─→ EnrichStage (AIMD-parallel, ceiling=16)
        ├─→ Text enrichment
        ├─→ CanonicalFinding construction
        └─→ StoreStage queue
  └─→ StoreStage
        └─→ DuckDBShadowStore.submit_findings()
              ├─→ URL/content hash dedup
              ├─→ Semantic dedup (semantic_deduplicator.py)
              └─→ batch_ioc_extract_unified (hot path, Rust)
```

## Evidence

- AIMD-parallel enrichment in EnrichStage
- DuckDBShadowStore handles all dedup layers
- Hot-path IOC extraction in Rust (no Python overhead)

## Use When

- Understanding how raw pages become stored findings
- Debugging evidence quality issues

## Do Not Use When

- Understanding fetch transport (see fetch-pipeline)

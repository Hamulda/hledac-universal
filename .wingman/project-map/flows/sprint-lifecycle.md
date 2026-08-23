# Sprint Lifecycle

## Metadata

| Field | Value |
| --- | --- |
| Kind | flow |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `flows/sprint-lifecycle.md` |

## Summary

End-to-end sprint execution flow from CLI entry to evidence storage.

## Flow

```
CLI (cli/parser.py)
  └─→ build_runtime() (composition_root.py)
        ├─→ DuckDB bootstrap (duckdb_pool.py)
        ├─→ MLX prewarm (capabilities.py)
        ├─→ Layer stack assembly (layers/)
        └─→ SprintLifecycleManager
  └─→ Pipeline Orchestrator
        └─→ Stage chain: Discovery → Dedup → Fetch → Match → Enrich → Store
              └─→ DuckDBShadowStore.submit_findings()
                    └─→ batch_ioc_extract_unified (hot path, Rust)
```

## Evidence

- __main__.py:main() → cli.parser.main() → asyncio.run(async_main())
- build_runtime() is synchronous, caller owns loop lifecycle
- DuckDBShadowStore.async_ingest_findings_batch() is the canonical write path
- IOC extraction hot path bypasses Python via hledac_rust_extensions

## Use When

- Understanding sprint execution from start to finish
- Debugging sprint startup or shutdown

## Do Not Use When

- Debugging individual stage internals (see respective stage docs)

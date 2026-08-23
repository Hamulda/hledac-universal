# IOC Extractor (DEPRECATED)

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | deprecated |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/ioc-extractor.md` |
| Source Path | `forensics/ioc_extractor.py` |

## Summary

DEPRECATED (F350M-R). Re-exports from `knowledge.ioc_processor`. Kept only for backward compatibility.

## Migration

```
from forensics.ioc_extractor import fast_ioc_extract
↓ REPLACE WITH
from knowledge.ioc_processor import fast_ioc_extract
```

Hot path (duckdb_store): use `batch_ioc_extract_unified` from `hledac_rust_extensions`.
Cold path: import from `knowledge.ioc_processor`.

## Use When

- Maintaining backward compatibility only
- Do NOT use for new code

## Do Not Use When

- Writing new IOC extraction code

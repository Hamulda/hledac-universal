# Evidence Writer

## Metadata

| Field | Value |
| --- | --- |
| Kind | utility |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `utilities/evidence-writer.md` |
| Source Path | `evidence/_writer.py` |

## Summary

Evidence persistence and archival. EvidenceLog for runtime evidence, _archiver for long-term storage.

## Evidence

- EvidenceLog: runtime evidence collection
- _archiver.py: long-term evidence archival
- Evidence root: EVIDENCE_ROOT (from paths.py)

## Use When

- Writing evidence during pipeline execution
- Archiving evidence for later analysis

## Do Not Use When

- Storing structured findings (use DuckDBShadowStore)

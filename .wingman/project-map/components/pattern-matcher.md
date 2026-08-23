# Pattern Matcher

## Metadata

| Field | Value |
| --- | --- |
| Kind | component |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `components/pattern-matcher.md` |
| Source Path | `knowledge/assertions.py`, `pipeline/_match_stage.py` |

## Summary

Pattern-based finding detection. SSOT for pattern matching — no regex fallback. Used in feed pipeline and match stage.

## Evidence

- PatternMatcher is single source of truth
- Offloaded, bounded concurrency
- CanonicalFinding per PatternHit
- Empty matcher registry = valid zero-findings state

## Use When

- Pattern-based IOC or finding detection
- Feed pipeline scanning

## Do Not Use When

- Custom regex-based detection (not supported — PatternMatcher is SSOT)

# Bounded Collections

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/bounded-collections.md` |
| Source Path | `_core/bounded_collections.py` |

## Summary

Zero-overhead wrappers over collections.deque with explicit maxlen for unbounded-list prevention. Every list-typed field that grows without limit is a memory leak vector on M1 8GB UMA.

## Evidence

- BoundedList[T](maxlen=N) — explicit bounded list
- Replaces unbounded list-typed fields
- Usage: `from _core.bounded_collections import BoundedList`

## Use When

- Any list that can grow without bound
- Preventing memory leaks in M1 8GB context
- Making bounds explicit in type signatures

## Do Not Use When

- Small, fixed-size collections (use plain list)

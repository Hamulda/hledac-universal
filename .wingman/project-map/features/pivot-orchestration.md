# Pivot Orchestration

## Metadata

- **Entry Path:** features/pivot-orchestration
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** feature

## Summary

Dynamic query pivoting based on discovered entities and cross-referencing.

## Source Paths

- `coordinators/research_optimizer.py`
- `graph/hypothesis_graph.py`

## Pivot Types

| Type | Trigger | Example |
|------|---------|---------|
| Entity | New IoC found | IP → Domain → ASN |
| Pattern | Similar findings | Hash family identification |
| Temporal | Time correlation | Same-day events |
| Geographic | Location overlap | Same country/AS |

## Research Loop

1. Initial query execution
2. IoC extraction
3. Entity correlation
4. Pivot opportunity identification
5. New query generation
6. Repeat until exhaustion

## Related Entries

- features/sprint-pipeline
- modules/research-optimizer

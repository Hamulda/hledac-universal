# Bounded Queue Stage Chain

## Metadata

| Field | Value |
| --- | --- |
| Kind | pattern |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `patterns/bounded-queue-stage-chain.md` |
| Source Path | `pipeline/_pipeline_orchestrator.py`, `pipeline/_stage_protocol.py` |

## Summary

Pipeline stages connected via bounded queues with TaskGroup on stage boundaries. Each stage communicates through BoundedStageQueue, no stage throws exception into TaskGroup.

## Evidence

- BoundedStageQueue with explicit maxsize between every stage
- TaskGroup cancellation: Ctrl-C → graceful shutdown of all stages
- 100 items/batch bound for M1 8GB safety
- Rust pipeline_compose for functor-style composition

## Use When

- Building new pipeline stages
- Processing streaming data with bounded memory

## Do Not Use When

- Unbounded pipeline processing (will OOM on M1 8GB)

# Pipeline Orchestrator

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/pipeline-orchestrator.md` |
| Source Path | `pipeline/_pipeline_orchestrator.py` |

## Summary

Orchestrates all stages in an AsyncIterator[Stage] chain using TaskGroup on stage boundaries and bounded queues between stages. Uses Rust pipeline_compose module via asyncio.to_thread() for functor-style composition.

## Evidence

- Stage chain: DiscoveryStage → DedupStage → FetchStage → MatchStage → EnrichStage → StoreStage
- Uses rust_extensions.wiring.pipeline_compose_wiring for Rust integration
- BATCH_SIZE = 100 for M1 8GB safety
- Invariant: no stage throws exception into TaskGroup

## Use When

- Adding a new pipeline stage
- Understanding the data flow through the system
- Debugging pipeline failures

## Do Not Use When

- Changing fetch logic (see fetch_coordinator)
- Changing CLI entry (see cli/parser)

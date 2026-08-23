# Sprint Pipeline

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/sprint-pipeline.md` |
| Source Paths | `pipeline/_pipeline_orchestrator.py`, `pipeline/_*_stage.py` |

## Summary

End-to-end OSINT sprint pipeline: Discovery → Dedup → Fetch → Match → Enrich → Store. TaskGroup on stage boundaries, bounded queues between stages, Rust pipeline_compose for functor-style composition.

## Stage Chain

```
DiscoveryStage → DedupStage → FetchStage → MatchStage → EnrichStage → StoreStage
```

## Evidence

- BATCH_SIZE = 100 for M1 8GB safety
- DedupStage: RotatingBloomFilter + Rust pipeline_filter_async/map_async
- EnrichStage: AIMD-paralel enrichment, ceiling=16 on M1 8GB
- StoreStage: bounded queue (128), submits to DuckDB via store.submit_findings()
- No stage throws exception into TaskGroup
- Rust pipeline_compose_wiring integration via asyncio.to_thread()

## Use When

- Running a full OSINT sprint
- Adding a new pipeline stage
- Understanding data flow through the system

## Do Not Use When

- Incremental single-URL fetch (see fetch_coordinator)
- Feed-only pipeline (see feed_orchestrator)

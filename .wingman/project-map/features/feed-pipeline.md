# Feed Pipeline

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/feed-pipeline.md` |
| Source Paths | `pipeline/_feed_orchestrator.py`, `pipeline/live_feed_pipeline.py` |

## Summary

Live RSS/Atom feed pipeline v2 with pattern-backed findings. Orchestrated version using StageOrchestrator: fetch_feed → assemble → scan → dedup → build_feed. PatternMatcher is SSOT — no regex fallback.

## Evidence

- Stages: FetchFeedStage → AssembleStage → ScanStage → DedupStage → BuildFeedStage
- PatternMatcher-driven per entry
- HTML→text normalization (word-boundary safe, entity-safe)
- Public/passive-only, no autonomous operations, no LLM
- store=None is valid no-op

## Use When

- Processing RSS/Atom feeds
- Running live feed monitoring

## Do Not Use When

- Full sprint pipeline (see sprint-pipeline)
- Non-feed data sources (see pivot-lane-planner)

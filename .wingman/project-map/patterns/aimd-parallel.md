# AIMD Parallel Processing

## Metadata

| Field | Value |
| --- | --- |
| Kind | pattern |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `patterns/aimd-parallel.md` |
| Source Path | `pipeline/_enrich_stage.py`, `coordinators/aimd_controllers.py` |

## Summary

Additive Increase / Multiplicative Decrease for adaptive concurrency. AIMD controllers for backpressure and worker count adaptation. Ceiling=16 on M1 8GB.

## Evidence

- AIMD controllers in aimd_controllers.py
- EnrichStage uses AIMD-parallel enrichment
- Concurrency adapts to system load

## Use When

- Adaptive concurrency control
- Backpressure handling in pipeline stages

## Do Not Use When

- Fixed concurrency (AIMD adds overhead for simple cases)

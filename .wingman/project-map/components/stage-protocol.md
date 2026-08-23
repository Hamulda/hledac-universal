# Stage Protocol

## Metadata

| Field | Value |
| --- | --- |
| Kind | component |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `components/stage-protocol.md` |
| Source Path | `pipeline/_stage_protocol.py` |

## Summary

Protocol defining pipeline stage interface. BoundedStageQueue, StageContext, StageMetrics, Stage. All pipeline stages implement this.

## Evidence

- Stage interface for all pipeline stages
- BoundedStageQueue: queue with explicit maxsize between stages
- StageContext: shared context across stages
- StageMetrics: per-stage telemetry

## Use When

- Implementing a new pipeline stage
- Understanding stage boundaries

## Do Not Use When

- Understanding specific stage behavior

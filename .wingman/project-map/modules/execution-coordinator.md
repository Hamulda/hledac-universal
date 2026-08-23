# Execution Coordinator

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/execution-coordinator.md` |
| Source Path | `coordinators/execution_coordinator.py` |

## Summary

Universal Execution Coordinator combining DeepSeek R1 (GhostDirector, Parallel, Ray Cluster) and Hermes3 for multi-backend execution, dynamic task generation, and distributed task distribution.

## Evidence

- Multi-backend execution: GhostDirector → Parallel → Ray
- Dynamic task generation based on confidence
- Mission-based execution via GhostDirector
- Distributed task distribution via Ray cluster
- Parallel task optimization with priorities

## Use When

- Understanding the multi-backend execution model
- Adding new execution backends
- Debugging execution failures

## Do Not Use When

- Understanding the fetch pipeline (see fetch_coordinator)
- Understanding the pipeline stages (see pipeline_orchestrator)

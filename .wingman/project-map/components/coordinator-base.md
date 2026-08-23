# Coordinator Base

## Metadata

| Field | Value |
| --- | --- |
| Kind | component |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `components/coordinator-base.md` |
| Source Path | `coordinators/base.py` |

## Summary

Base class / protocol for all coordinators. Stable interface: start() / step() / shutdown(). All coordinators (fetch, execution, memory, monitoring, graph, etc.) implement this interface.

## Evidence

- Start/step/shutdown is the stable coordinator contract
- FetchCoordinator, ExecutionCoordinator, MemoryCoordinator, MonitoringCoordinator, GraphCoordinator all implement it

## Use When

- Building a new coordinator
- Understanding coordinator interface contract

## Do Not Use When

- Understanding specific coordinator behavior (see respective coordinator)

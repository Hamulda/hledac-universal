# Coordinator Interface Contract

## Metadata

| Field | Value |
| --- | --- |
| Kind | contract |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `contracts/coordinator-interface.md` |

## Summary

All coordinators MUST implement start() / step() / shutdown() interface. Orchestrator becomes thin spine delegating to coordinators.

## Contract

```
start()     — initialize coordinator, begin processing
step()      — process one unit of work
shutdown()  — graceful shutdown, release resources
```

## Evidence

- Stable coordinator interface in coordinators/base.py
- FetchCoordinator, ExecutionCoordinator, MemoryCoordinator, MonitoringCoordinator, GraphCoordinator all implement it

## Use When

- Building a new coordinator
- Integrating coordinator with orchestrator

## Do Not Use When

- Building pipeline stages (different interface — see stage-protocol)

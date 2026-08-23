# Monitoring Coordinator

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/monitoring-coordinator.md` |
| Source Path | `coordinators/monitoring_coordinator.py` |

## Summary

Universal Monitoring Coordinator combining DeepSeek R1 advanced monitoring with Hermes3 patterns and M1 memory-aware monitoring with pressure detection.

## Evidence

- AdvancedMonitoring + Watchdog + psutil metrics
- Memory-aware monitoring with pressure detection
- Integrated monitoring coordination

## Use When

- Understanding monitoring architecture
- Adding new monitoring metrics
- Debugging monitoring issues

## Do Not Use When

- Changing fetch or execution logic (see respective coordinators)

---
children_hash: 072c7ece7e7136789e40c11e298d7188db92c7081b8e1e4c03ac9ae1658b8ba0
compression_ratio: 0.4829329962073325
condensation_order: 1
covers: [acquisition_cycleresult_structure.md, acquisition_exit_paths.md, acquisition_orchestrator_lifecycle.md, context.md]
covers_token_total: 791
summary_level: d1
token_count: 382
type: summary
---
<think>
The user wants me to create a concise structural summary of the runtime domain, compressing the child entries into a single overview.
</think>

# Runtime Domain

## Overview
Acquisition orchestrator in `runtime/scheduler_v2/acquisition.py` managing cycle execution with stable and aggressive modes.

## CycleResult Structure
`CycleResult(msgspec.Struct, frozen=True)` with 10 fields:
- `cycle_ok`, `empty_work_items`, `aggressive_mode`
- `feed_results`: (ok, count)
- `public_results`: (ok, count, timeout)
- `ct_results`: (ok, count)
- `aimd_window`, `aimd_successes`, `aimd_failures`, `error`

## Execution Modes
- **Stable**: 2-branch TaskGroup (FEED || PUBLIC)
- **Aggressive**: 3-branch TaskGroup (FEED + PUBLIC + CT)
- `_branch_timeout = max((remaining - _safety_floor) / 3, 5.0)`

## Adaptive Max Cycles
```
effective_max_cycles = max(50, min(300, int((sprint_duration - windup_lead) / cycle_time_ema)))
```

## Exit Paths
1. `hard_deadline` — `_check_hard_deadline()` returns False
2. `stop_requested` — `ctx._stop_requested` set
3. `lifecycle_abort` — `_runner.abort_requested`
4. `max_cycles_reached` — `cycles_started >= effective_max_cycles`
5. `terminal` — normal completion

## Lifecycle Flow
`run() -> while not terminal: check_deadline -> pre_windup -> drain_patterns -> pressure_relief -> windup_guard -> run_one_cycle`

## Key Dependencies
- AIMD telemetry from `ctx._cycle._aimd_telemetry` or FetchCoordinator
- Synthesis sidecar runs in WINDUP phase (F259, `HLEDAC_ENABLE_HERMES_SYNTHESIS` env gate)
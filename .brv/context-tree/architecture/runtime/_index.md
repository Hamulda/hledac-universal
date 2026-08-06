---
children_hash: 5a89c95b23a3f710473c0a12e1df9318c8ef25a96b87563cd07af17d6ab44e69
compression_ratio: 0.2718560074801309
condensation_order: 1
covers: [acquisition_cycleresult_structure.md, acquisition_exit_paths.md, acquisition_orchestrator_lifecycle.md, context.md, f186a_canonical_sprint_truth.md, f350m_r_scheduler_v2.md, meaningful_run_detection.md, report_serialization.md, resource_governor.md, shared_state_management.md, sprint_lifecycle.md, sprint_seed_state.md, sprint_tiers.md, sprintflags_configuration.md]
covers_token_total: 4278
summary_level: d1
token_count: 1163
type: summary
---
<think>
The user wants me to create a structural summary of the runtime domain knowledge entries. Let me analyze the key facts, relationships, and patterns across all entries.

Key themes:
1. Sprint lifecycle orchestration with tiers
2. Acquisition orchestrator with stable/aggressive modes
3. Canonical sprint entry boundary (F186A)
4. Scheduler v2 migration
5. Resource governance for M1 8GB
6. Report serialization optimization
7. Meaningful run detection
8. Shared state management
9. Sprint seed state for deterministic replay

Let me create a concise summary that preserves these key points and references the entry names.
</think>

# Runtime Domain

Acquisition lifecycle orchestrator with stable/aggressive modes, AIMD telemetry, and synthesis sidecar

## Sprint Lifecycle Orchestration

- **Sprint entry**: `python -m hledac.universal --sprint QUERY [--duration SECS] [--aggressive]`
- **States**: pending → acquiring → acquired → releasing → released
- **8 acquisition lanes**: surface, structured_ti, deep, archive, CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH
- **Winddown**: calls `DuckDBShadowStore.async_ingest_findings_batch()` for findings

See: `sprint_lifecycle.md`, `sprint_tiers.md`, `f186a_canonical_sprint_truth.md`

## Sprint Tiers

| Tier | Duration | Hermès | Windup Lead |
|------|----------|--------|-------------|
| quick | 60-179s | no | 0s |
| standard | 180-299s | yes | 30s |
| deep | 300-599s | yes | 30s |
| thorough | 600s+ | yes | 30s |

- MIN_ACTIVE_WINDOW_S = 30s minimum (F221-ABORT)
- Tier priority: surface → structured_ti → deep → archive → other

See: `sprint_tiers.md`

## Canonical Sprint Boundary (F186A)

`run_sprint()` is sole canonical sprint owner. Canonical path: `main() --sprint → run_sprint()`.

- Owns all pre-sprint checks, UMA wiring, sprint_delta reporting
- `run_pre_sprint_checks()` in pre-flight boundary
- `write_sprint_delta()` in teardown boundary
- `_runtime_truth()` and `_is_meaningful_run()` part of canonical run boundary

See: `f186a_canonical_sprint_truth.md`

## Acquisition Orchestrator

- **Stable mode**: 2-branch (FEED || PUBLIC)
- **Aggressive mode**: 3-branch TaskGroup (FEED + PUBLIC + CT)
- **Branch timeout**: `max((remaining - _safety_floor) / 3, 5.0)`
- **Adaptive max cycles**: `max(50, min(300, int((sprint_duration - windup_lead) / cycle_time_ema)))`
- **Synthesis sidecar**: runs in WINDUP phase (HLEDAC_ENABLE_HERMES_SYNTHESIS env gate)

See: `acquisition_orchestrator_lifecycle.md`, `acquisition_cycleresult_structure.md`, `acquisition_exit_paths.md`

## CycleResult Structure

`CycleResult(msgspec.Struct, frozen=True)` with 10 fields:
- `cycle_ok`, `empty_work_items`, `aggressive_mode`
- `feed_results` (ok, count), `public_results` (ok, count, timeout), `ct_results` (ok, count)
- `aimd_window`, `aimd_successes`, `aimd_failures`, `error`

See: `acquisition_cycleresult_structure.md`

## Exit Paths

5 exit conditions: `hard_deadline`, `stop_requested`, `lifecycle_abort`, `max_cycles_reached`, `terminal`

See: `acquisition_exit_paths.md`

## Scheduler V2 Architecture (F350M-R)

- Production: `SprintSchedulerV2` from `scheduler_v2.scheduler`
- v1 archived to `sprint_scheduler_v1_archived.py`
- `PivotTask` relocated to `runtime/pivot_types.py`
- `sprint_scheduler.py` is backward-compatible re-export shim

See: `f350m_r_scheduler_v2.md`

## Resource Governor

`M1ResourceGovernor` - advisory safety layer for M1 8GB sprints:
- EMA-based adaptive branch concurrency (alpha=0.3)
- 5-tier UMA state machine
- Sidecar admission blocks HEAVY/CRITICAL under critical/emergency UMA
- Fails soft - never blocks sprint

See: `resource_governor.md`

## Meaningful Run Detection

`_is_meaningful_run()` classifies runs:
- Hardware-limited smoke: zero cycles + swap/UMA critical
- Smoke: < 30s + < 3 cycles
- Not meaningful: < 180s + no findings + no hits
- Meaningful: pattern hits ≥ 15

See: `meaningful_run_detection.md`

## Report Serialization (F500-O)

- Uses `orjson.dumps` with `OPT_INDENT_2` or `OPT_APPEND_NEWLINE`
- `OPT_SERIALIZE_NUMPY` auto-detected
- `HLEDAC_REPORT_PRETTY_PRINT=1` enables indented output
- `AcqReportPayload` is msgspec.Struct (~200+ fields, eq=False)

See: `report_serialization.md`

## Sprint Seed State

Global `_current_sprint_seed_state` enables deterministic cognitive replay:
- Set by `run_sprint()` at sprint start
- Coordinators access via `get_sprint_seed_state()`
- Enables reproducible sprint behavior

See: `sprint_seed_state.md`

## Shared State

Cross-lane coordination via `Arc<Mutex<T>>`:
- `CacheLock` uses `parking_lot::Mutex`
- `EventEmitter` broadcasts via `broadcast::channel`

See: `shared_state_management.md`
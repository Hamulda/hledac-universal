# Sprint F226A — Mission Intent Runtime Wiring

## Status: COMPLETE

Mission intent is now operational. It influences existing acquisition planning and pivot scoring without creating a new scheduler framework.

## What Changed

### acquisition_strategy.py
1. **NonfeedPlanDebug dataclass** — Added 4 new telemetry fields:
   - `mission_runtime_applied: bool` — True when intent is known (not unknown/org_recon)
   - `mission_lane_priority: tuple[str, ...]` — Set to `mission_required_lanes`
   - `mission_pivot_boost_applied: bool` — True when mission intent is known
   - `mission_feed_cap_reason: str | None` — Always None (FEED capping driven by nonfeed_diagnostic, not mission intent)

2. **build_acquisition_plan()** — `_nonfeed_debug` initialization now includes F226A fields:
   - `mission_runtime_applied = _intent not in (UNKNOWN, ORG_RECON)`
   - `mission_lane_priority = _required_lanes`
   - `mission_pivot_boost_applied = _intent not in (UNKNOWN, ORG_RECON)`
   - `mission_feed_cap_reason = None`

3. **nonfeed_profile_expected_lanes** — Changed from hardcoded `nonfeed_diagnostic`-only set to: if mission intent is known, uses `mission_required_lanes`; otherwise falls back to `()`.

4. **build_acquisition_report()** — Added F226A fields to dict serialization using `getattr(nd, ...)`.

### sprint_scheduler.py
- **Line 1829**: `generate_pivot_candidates_from_query(query)` → `generate_pivot_candidates_from_query(query, mission_intent=_mission_intent)`
- Reads `mission_intent` from `nonfeed_plan_debug.mission_intent` before calling pivot generation
- Sets `mission_pivot_boost_applied = True` when known mission intent is present

### pivot_planner.py
- `generate_pivot_candidates_from_query()` already accepts `mission_intent` parameter (F225D)
- `score_pivot_for_mission()` already applies boost multipliers per mission type
- No changes needed — signature was already compatible

## Invariants Maintained

| Test | What it verifies |
|------|-------------------|
| `test_stealth_not_enabled_by_mission_intent` | STEALTH lane never enabled by mission intent |
| `test_hardware_critical_blocks_heavy_lanes_regardless_of_mission` | Hardware state still blocks lanes regardless of intent |
| `test_no_network_in_acquisition_strategy` | No network I/O in module |
| `test_no_network_in_pivot_planner` | No network I/O in module |
| `test_no_mlx_import_in_modules` | No MLX imports |

## Mission Intent → Lane Priority Mapping

| Intent | Required Lanes | Optional Lanes | mission_runtime_applied |
|--------|----------------|----------------|------------------------|
| domain_recon | PUBLIC, CT, PIVOT_EXECUTOR | WAYBACK, PASSIVE_DNS | True |
| infra_recon | PUBLIC, CT, PIVOT_EXECUTOR | PASSIVE_DNS, WAYBACK | True |
| cve_recon | PUBLIC, CT, PIVOT_EXECUTOR | WAYBACK, PASSIVE_DNS | True |
| wallet_recon | PUBLIC, PIVOT_EXECUTOR | BLOCKCHAIN, CT | True |
| person_recon | PUBLIC, PIVOT_EXECUTOR | CT, PASSIVE_DNS | True |
| unknown | (safe lanes only via _SAFE_LANES/_SAFE_OPTIONAL) | — | False |

## Pivot Boost Multipliers (from F225D, pre-existing)

| Mission | Pivot types boosted | Multiplier |
|---------|---------------------|------------|
| domain_recon | domain, archive, graph | 1.25× |
| infra_recon | domain, archive, graph | 1.20× |
| wallet_recon | graph (hash) | 1.30× |
| cve_recon | graph, archive | 1.10–1.15× |
| person_recon | leak, identity | 1.25× |

## Test Results

```
tests/probe_f225a_mission_intent: 23 passed
probe_f226a_mission_runtime: 22 passed
tests/probe_f225d_pivot_planner_value: 38 passed
---
Total: 83 passed
```

## ABORT CONDITIONS — All Cleared

| Condition | Status |
|-----------|--------|
| new mission controller framework | ❌ Not created |
| stealth/browser enabled | ❌ STEALTH not enabled by mission intent |
| live network in tests | ❌ All modules verified no network I/O |
| feed disabled globally | ❌ FEED stays enabled for all mission types |
| scheduler ownership rewrite | ❌ Scheduler unchanged |
| new persistent schema | ❌ No new schema introduced |

## Files Modified

- `runtime/acquisition_strategy.py` — telemetry fields, nonfeed_profile_expected_lanes logic, report dict
- `runtime/sprint_scheduler.py` — mission_intent passed to generate_pivot_candidates_from_query
- `runtime/pivot_planner.py` — no changes (signature already compatible)
- `probe_f226a_mission_runtime/test_mission_runtime.py` — new probe tests

## Backups

- `runtime/acquisition_strategy.py.bak_F226A_MISSION_RUNTIME`
- `runtime/sprint_scheduler.py.bak_F226A_MISSION_RUNTIME`
- `runtime/pivot_planner.py.bak_F226A_MISSION_RUNTIME`
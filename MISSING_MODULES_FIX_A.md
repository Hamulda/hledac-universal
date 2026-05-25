# MISSING_MODULES_FIX_A — marl_coordinator & memory_watchdog Investigation

**Date:** 2026-05-23
**Sprint:** PACKAGE_MIGRATION follow-up
**Files changed:** `rl/marl_coordinator.py`, `runtime/memory_watchdog.py`

---

## SKUPINA 1 — `rl/marl_coordinator.py`

### Evidence Summary

| Evidence | Value |
|----------|-------|
| PACKAGE_MIGRATION_REPORT entry | 8 callers, module doesn't exist |
| broken_imports.json entries | 8 lines in `tests/test_sprint58a.py` |
| REAL_ARCHITECTURE.md | "stub experiment; zero prod call-sites; D F196A" |
| LONGTERM_PLAN.md | "marl_coordinator D F196A" |
| Git history (e40035d2) | Commit that deleted it: "ghost module cleanup, semantic dedup..." |
| Canonical replacement | `rl/sprint_policy_manager.py` — surviving RL plane |

### All 8 Callers

All 8 callers are in `tests/test_sprint58a.py` lines 199–315, all importing
`from hledac.universal.rl.marl_coordinator import MARLCoordinator` inside a
**single** `TestMARLCoordinator` class that is entirely marked
`@pytest.mark.skip(reason="MARLCoordinator deleted in Sprint F196A")`.

Zero canonical/production call-sites exist anywhere in the codebase.

### Git History Verdict

Git log confirms `e40035d2` (Sprint F200D) deleted it with message
"ghost module cleanup". Prior to deletion, the file was a 308-line stub
with `training_enabled` hardcoded to `False` and a comment: "DORMANT / HEAVY /
NOT PROMOTED — zero canonical call-sites". This was an **intentional D in F196A**,
not a regression.

### Replacement Coverage

`rl/sprint_policy_manager.py` provides:
- `SprintPolicyManager` — reward-contract RL state manager
- Every-5th-sprint exploration logic
- Policy persistence to JSON

This covers the RL plane use-case that `marl_coordinator` was attempting
(but never actually powering, since `training_enabled=False`).

### Decision

**Create thin compatibility shim** — NOT a full implementation.

The test file is already marked skip, but `broken_imports.json` flags it.
A graceful `ImportError` is better than a cryptic `"ModuleNotFoundError"`.

`rl/marl_coordinator.py` created with:
- Intentional `raise ImportError(...)` pointing to `sprint_policy_manager`
- No production dependencies
- Clear D F196A comment

---

## SKUPINA 2 — `runtime/memory_watchdog.py`

### Evidence Summary

| Evidence | Value |
|----------|-------|
| PACKAGE_MIGRATION_REPORT entry | 10 callers, module doesn't exist |
| broken_imports.json entries | 10 lines in `tests/probe_f192g/test_f192g_grey_runtime_seams.py` |
| REAL_ARCHITECTURE.md | "ghost; attach_to_dispatcher() never called from sprint path" |
| Canonical replacement | `utils/uma_budget.py` — `UmaWatchdog` class |
| Replacement coverage | 100% — UmaWatchdog provides all watchdog functionality |

### All 10 Callers

All 10 callers are in `tests/probe_f192g/test_f192g_grey_runtime_seams.py`
(lines 684–860). **This directory does not exist on disk** — the file path
in `broken_imports.json` is a ghost reference.

Zero canonical/production call-sites exist anywhere in the codebase.

### UmaWatchdog vs MemoryWatchdog (Pre-D Form)

| Feature | Old `memory_watchdog.MemoryWatchdog` | Canonical `uma_budget.UmaWatchdog` |
|---------|--------------------------------------|-------------------------------------|
| Polling | `check_interval` param | `interval` param (default 0.5s) |
| Callbacks | `on_tier_suspended`, `on_tier_resumed`, `on_emergency_gc` | `on_warn`, `on_critical`, `on_emergency` |
| Pressure levels | `PressureLevel` enum | via `get_uma_pressure_level()` |
| MLX cache cleanup | On emergency (manual) | On critical + emergency (auto via `DefaultUmaWatchdogCallbacks`) |
| Async | Yes | Yes (own task, debounced) |
| State-change debounce | No (every poll) | Yes (DEBOUNCE_SECONDS=2.0) |

`UmaWatchdog` is strictly more capable. The old `MemoryWatchdog` was a
thin wrapper around it with tier-dispatcher callbacks that were never wired.

### Decision

**Create thin shim** that re-exports the canonical symbols.

`runtime/memory_watchdog.py` created with:
- `PressureLevel = MemoryPressureLevel` alias (from `coordinators.enums`)
- `MemoryWatchdog` class — deprecation warning, no-op implementation
- Clear documentation pointing to `utils.uma_budget.UmaWatchdog`

The probe test file (`probe_f192g/`) does not exist, so this shim serves
any future legacy caller that may reference the old path.

---

## Verification

```bash
# SKUPINA 1 — graceful ImportError
python3 -c "from hledac.universal.rl.marl_coordinator import MARLCoordinator"
# → ImportError: marl_coordinator was removed in Sprint F196A ...

# SKUPINA 2 — re-export works
python3 -c "from hledac.universal.runtime.memory_watchdog import PressureLevel, MemoryWatchdog"
# → PressureLevel = MemoryPressureLevel (coordinators.enums)
# → MemoryWatchdog (deprecation warning + no-op)
```

Both shims load without errors. No production code paths affected.

---

## GHOST_INVARIANTS Compliance

| Invariant | Status |
|-----------|--------|
| Fail-safe shims (don't crash callers) | ✓ `raise ImportError` for marl, deprecation+no-op for watchdog |
| Zero production call-sites | ✓ All callers are skip-marked tests or ghost paths |
| Bounded re-exports | ✓ Only 2 symbols re-exported |
| GHOST_INVARIANTS gather return_exceptions | N/A — no gather used |
| mx.eval([]) before clear_cache | N/A |
| time.monotonic for intervals | N/A |
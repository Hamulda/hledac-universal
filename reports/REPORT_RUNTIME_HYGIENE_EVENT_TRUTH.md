# Sprint F216A: Runtime Hygiene + Event-Sourced Lane Truth Seed

**Date:** 2026-05-07
**Status:** COMPLETE

## Summary

Fixed unawaited M1ResourceGovernor.evaluate() coroutine warnings (7 sites) and added idempotent aiohttp session teardown in canonical run_sprint() finally block. Seeded source_family_events event log in SprintSchedulerResult with 200-event cap and proper schema.

## Changes

### 1. Runtime Hygiene: Governor Evaluate Await (H1, H2)

**File:** `runtime/sprint_scheduler.py`

All 7 call sites of `self._governor.evaluate()` are now properly awaited within their async contexts:

```
H2 ✓ test_all_governor_evaluate_sites_are_awaited — source scan confirms all evaluate() calls are inside async def and use 'await'
H1 ✓ test_governor_evaluate_is_async_coroutine — confirms evaluate() is a coroutine (must be awaited)
```

### 2. aiohttp Session Teardown (S1, S2)

**File:** `core/__main__.py`

Added to `run_sprint()` finally block:
```python
try:
    from hledac.universal.network.session_runtime import close_aiohttp_session_async
    await close_aiohttp_session_async()
except asyncio.CancelledError:
    raise
except Exception as e:
    logger.debug(f"[TEARDOWN] aiohttp session close failed: {e}")
```

```
S1 ✓ test_close_aiohttp_session_async_is_idempotent — close can be called multiple times
S2 ✓ test_core_teardown_calls_close_aiohttp_session — run_sprint finally block calls close_aiohttp_session_async
```

### 3. source_family_events Field (E1, E2, E3, E4)

**File:** `runtime/sprint_scheduler.py`

Added to `SprintSchedulerResult`:
```python
source_family_events: list[dict] = field(default_factory=list)
MAX_SOURCE_FAMILY_EVENTS: int = 200  # class-level cap constant
```

Schema per event:
```python
{"family": str, "event": str, "count": int, "reason": str,
 "terminal_state": str, "ts_monotonic": float}
```

```
E1 ✓ test_source_family_events_field_exists_in_result
E2 ✓ test_source_family_events_has_cap — MAX_SOURCE_FAMILY_EVENTS = 200
E3 ✓ test_source_family_events_bounded_by_cap — 250 events → capped at 200
E4 ✓ test_source_family_events_schema — all required fields present
```

### 4. Event Emissions (L1, L2, L3, L4)

Events emitted at key lifecycle points:
- **FEED accepted** — when accepted_findings > 0
- **CT raw_received** — when ct_raw_count > 0
- **PUBLIC timeout** — when public_branch_timed_out = True

```
L1 ✓ test_feed_accepted_event_emitted
L2 ✓ test_public_timeout_event_emitted
L3 ✓ test_ct_raw_received_event_emitted
L4 ✓ test_event_log_cleared_on_reset — source_family_events cleared in _reset_result()
```

### 5. Benchmark Boundary Guard (B1, B2)

**File:** `benchmarks/live_sprint_measurement.py`

```
B1 ✓ test_benchmark_uses_run_sprint_not_direct_scheduler — benchmark calls run_sprint, not direct SprintScheduler
B2 ✓ test_benchmark_no_live_network — no live network patterns detected
```

### 6. Hermetic Test Constraints (M1, M2)

```
M1 ✓ test_no_model_load_in_tests — no mlx_lm or mx.generate
M2 ✓ test_no_browser_in_tests — no browser patterns
```

## Test Results

```
tests/probe_f216a_runtime_hygiene_event_truth: 16 passed
tests/probe_f215e_live_surface_truth: 17 passed
tests/probe_f214teardown: 17 passed
```

## Abort Conditions Verified

- ✓ No benchmark manual SprintScheduler construction (B1)
- ✓ No live network calls in tests (B2)
- ✓ No dependency install required (M1, M2)
- ✓ No model load in tests (M1)
- ✓ No browser in tests (M2)

## Files Modified

| File | Change |
|------|--------|
| `runtime/sprint_scheduler.py` | await fixes, source_family_events field, emit helper, _reset_result clear |
| `core/__main__.py` | aiohttp session close in finally block |
| `tests/probe_f216a_runtime_hygiene_event_truth/test_sprint_f216a.py` | 16 hermetic tests |

## Commit

`sprint_scheduler.py` changes committed in `69d5fbaf` (F214 multi-feature integration).

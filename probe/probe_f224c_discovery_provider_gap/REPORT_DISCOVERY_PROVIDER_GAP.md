# Sprint F224C — Discovery Planner Provider Gap Closure

## Goal
Make provider capability gaps explicit and non-misleading. Discovery planner should never treat stub providers as normal planned providers unless explicitly allowed.

## Problem
Audit found discovery_planner had:
- `commoncrawl_cdx`: TODO using wayback adapter — no real endpoint
- `feed_pivots`: stub returns `feed_pivots_no_pipeline_context`
- `ct_pivots`: real via `call_crtsh`
- No explicit capability state tracking
- `provider_status_debug` missing providers skipped during exploration

## Changes Made

### discovery_planner.py

**New: `ProviderCapabilityState` Enum**
```
PRODUCTION     — Fully wired, real endpoint, production-safe
ADVISORY_STUB  — Placeholder adapter, endpoint NOT implemented
NOT_WIRED      — No pipeline context / adapter wired
DISABLED       — Explicitly disabled (circuit breaker, env, etc.)
```

**New: `get_provider_state()` — gap-closure priority logic**
1. `is_stub=True AND NOT requires_context` → ADVISORY_STUB (commoncrawl_cdx)
2. `requires_context=True` → NOT_WIRED (feed_pivots, becomes PRODUCTION when context available)
3. `production_enabled=False` → DISABLED
4. Otherwise → PRODUCTION

**New: `ProviderStatusDebug` dataclass**
```python
@dataclass
class ProviderStatusDebug:
    provider: str
    state: ProviderCapabilityState
    selected: bool
    reason: str  # human-readable skip/select reason
```

**Updated: `DiscoveryPlan` adds `provider_status_debug`**
```python
@dataclass
class DiscoveryPlan:
    plans: list[ProviderPlan]
    estimated_total_ms: float
    remaining_budget_ms: float
    provider_status_debug: list[ProviderStatusDebug] = field(default_factory=list)
```

**Updated: `plan()` adds `pipeline_context_available` param**
- Default `False` → `feed_pivots` = NOT_WIRED
- When `True` → `feed_pivots` = PRODUCTION

**Updated: `_run_commoncrawl_cdx()` stub behavior**
- With `include_stub=False` (default): returns `error_type="stub_not_production"`
- With `include_stub=True`: returns explicit `stub_not_production` with `commoncrawl_cdx_no_real_endpoint`

**Updated: Exploration now logs skipped providers**
- When exploration selects a mid-tier provider, the original top pick is logged with `exploration_skipped_original_score=N`

**Updated: `execute()` passes `include_stub` to commoncrawl_cdx runner**

## Provider State Map (final)

| Provider | State | Selected by default |
|---|---|---|
| ddg_mojeek | PRODUCTION | ✓ |
| historical_frontier | PRODUCTION | ✓ |
| wayback_cdx | PRODUCTION | ✓ |
| ct_pivots | PRODUCTION | ✓ |
| commoncrawl_cdx | ADVISORY_STUB | ✗ |
| feed_pivots | NOT_WIRED (PRODUCTION when pipeline_context=True) | ✗ |

## Test Results

```
9 passed in 1.77s
```

Tests cover:
- T1: feed_pivots NOT selected by default (NOT_WIRED)
- T2: commoncrawl_cdx NOT selected by default (ADVISORY_STUB)
- T3: include_stub_providers allows commoncrawl_cdx but result is stub_not_production
- T4: ct_pivots remains selectable (PRODUCTION)
- T5: provider_status_debug includes ALL providers with explicit reasons
- T6: plan() is idempotent — no live network, no MLX load
- T7: feed_pivots selected when pipeline_context_available=True
- State machine: get_provider_state() mapping correctness
- Execution: stub plan completes without real network calls

## Files Modified
- `discovery/discovery_planner.py` — capability state, debug output, exploration fix

## Files Created
- `tests/probe_f224c_discovery_provider_gap/test_discovery_provider_gap.py`
- `probe_f224c_discovery_provider_gap/REPORT_DISCOVERY_PROVIDER_GAP.md`
- `probe_f224c_discovery_provider_gap/discovery_provider_gap.json`
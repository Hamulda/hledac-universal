# P0: Contextvars Migration — Implementation Plan

## Status: READY TO IMPLEMENT

## Current State
- `sprint_scheduler.py`: **32,856 lines**
- ContextVars already partially used (L231-238) — working pattern exists
- 50+ instance dicts in `SprintScheduler.__init__` (L5324-5430)
- 7 module-level globals

## Strategy: Incremental Context Extraction

### Phase 1: Add SprintRunContext dataclass (L240 area)

```python
# After line 239 (_advisory_log_suppressed_total_var)

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .runtime.sprint_scheduler import SprintScheduler

@dataclass
class SprintRunContext:
    """Per-sprint mutable state — replaces instance dicts in SprintScheduler."""
    # Dedup state
    seen_hashes: dict[str, bool] = field(default_factory=dict)
    entries_per_source: dict[str, int] = field(default_factory=dict)
    hits_per_source: dict[str, int] = field(default_factory=dict)
    
    # Source economics
    source_weights: dict[str, float] = field(default_factory=dict)
    novelty_bonuses: dict[str, float] = field(default_factory=dict)
    feed_accepted_per_source: dict[str, int] = field(default_factory=dict)
    source_economics: dict[str, 'SourceEconomics'] = field(default_factory=dict)
    
    # Pivot state
    pivot_stats: dict[str, int] = field(default_factory=lambda: {"total": 0, "processed": 0, "errors": 0})
    pivot_rewards: dict[str, list[float]] = field(default_factory=dict)
    recent_iocs: list[dict] = field(default_factory=list)
    
    # Fetch telemetry
    fetch_latency_ema: dict[str, float] = field(default_factory=dict)
    
    # Arrow batch
    arrow_batch: list[dict] = field(default_factory=list)
    
    # Sprint result ref
    result: 'SprintSchedulerResult' = field(default=None)
    
# Module-level ContextVar
_sprint_run_ctx: contextvars.ContextVar[SprintRunContext] = contextvars.ContextVar(
    "_sprint_run_ctx", default=None
)

def get_sprint_ctx() -> SprintRunContext:
    """Get current sprint context or raise."""
    ctx = _sprint_run_ctx.get()
    if ctx is None:
        raise RuntimeError("SprintRunContext not established — call within sprint run()")
    return ctx

def reset_sprint_ctx() -> None:
    """Reset context (call between sprints / for testing)."""
    _sprint_run_ctx.set(None)
```

### Phase 2: Wrap run() to establish context (L6520)

```python
async def run(self, lifecycle, sources, ...) -> SprintSchedulerResult:
    # Establish fresh context
    token = _sprint_run_ctx.set(SprintRunContext())
    try:
        # Existing logic unchanged — just access state via get_sprint_ctx()
        self._sprint_depth += 1
        # ... rest of run()
    finally:
        _sprint_run_ctx.reset(token)
        self._sprint_depth -= 1
```

### Phase 3: Migrate one dict at a time

Start with lowest-risk dicts:
1. `_fetch_latency_ema` — used only in `_update_latency_ema()`
2. `_novelty_bonuses` — simple dict
3. `_source_weights` — simple dict

Pattern for migration:
```python
# OLD (direct instance attr)
self._novelty_bonuses[source_type] = has_bonus

# NEW (via context)
get_sprint_ctx().novelty_bonuses[source_type] = has_bonus
```

## Python 3.14 Compatibility Notes

| Pattern | Python 3.14 Change | Action |
|---------|-------------------|--------|
| `contextvars.ContextVar` | No changes | Already compatible |
| `asyncio.Task` + contextvars | No changes | Already compatible |
| `__del__` in WALManager | Deprecated | Already uses atexit |

## Test Coverage Required

Before starting, verify test coverage:
```bash
pytest tests/test_sprint_scheduler.py -v --tb=short -q
```

## Files to Modify

1. `runtime/sprint_scheduler.py` — add SprintRunContext + contextvar + wrap run()
2. `tests/test_sprint_scheduler.py` — add context isolation tests

## Effort: 3 days (incremental, test after each phase)

# ANALYSIS: Outdated Import + Missing Watchdog

**Date:** 2026-05-30
**Scope:** `execution/ghost_executor.py`, `coordinators/monitoring_coordinator.py`
**Role:** PURE ANALYSIS — no fixes, no stubs

---

## Verdict A: StealthOrchestrator (`ghost_executor.py:566`)

### Classification: **DEAD CODE — safe to delete**

### Evidence

| Aspect | Finding |
|--------|---------|
| Import location | `execution/ghost_executor.py:566` |
| Namespace | `hledac.outdated.stealth_toolkit` — never existed |
| Fail-soft | **YES** — wrapped in `try/except ModuleNotFoundError` |
| Usage | Line 665: `if stealth_mgr:` — then `pass` (no-op) |
| Canonical path | `hledac.universal.stealth.stealth_manager` (active) |
| Canonical alternative | `layers/stealth_layer.py` (StealthBrowser, DetectionEvader, etc.) |

### Call Chain Analysis

```
ghost_executor.py:566
    from hledac.outdated.stealth_toolkit.stealth_orchestrator import StealthOrchestrator as _SO

ghost_executor.py:549-577 (_get_stealth_manager)
    → try: self._stealth_manager = _SO()
    → except ModuleNotFoundError: self._stealth_manager = None
    → return self._stealth_manager (or None)

ghost_executor.py:663-668 (_action_google)
    stealth_mgr = await self._get_stealth_manager()
    if stealth_mgr:
        pass  # ← NO ACTUAL IMPLEMENTATION, just falls through
    # Then proceeds to _ddgs_search() regardless
```

### Why Dead Code

1. **Namespace never existed** — `hledac.outdated.*` is documented as "module never existed" (`docs/sprints/PERMANENTLY_SHIMMED.md`)
2. **No functional call** — `pass` statement means the result is never used
3. **Fail-soft already handles unavailability** — returns `None` gracefully
4. **Canonical stealth exists** — `layers/stealth_layer.py` with StealthBrowser, DetectionEvader, CaptchaSolver

### Recommendation

```python
# DELETE lines 549-577 (_get_stealth_manager method)
# DELETE line 663-668 (stealth_mgr check + pass)
# DELETE self._stealth_manager = None at line 987
# DELETE self._stealth_manager = None at line 487 (init)
```

**Risk:** NONE — the code path is a stub that does nothing.

---

## Verdict B: Watchdog (`monitoring_coordinator.py:189`)

### Classification: **SHIM ALREADY EXISTS — no action needed**

### Evidence

| Aspect | Finding |
|--------|---------|
| Import path | `from _shims.core_watchdog import Watchdog` |
| Shims location | `_shims/core_watchdog.py:16` |
| Implementation | Adapter wrapping `hledac.universal.utils.uma_budget.UmaWatchdog` |
| Fail-soft | **YES** — wrapped in `try/except ImportError` |

### Shims Architecture

```
monitoring_coordinator.py:189
    from _shims.core_watchdog import Watchdog

_shims/core_watchdog.py:16-60
    class Watchdog:
        def __init__(threshold_mb=None, check_interval=None, callback=None)
            → wraps UmaWatchdog(callbacks=UmaWatchdogCallbacks, interval=interval)

utils/uma_budget.py
    class UmaWatchdog (canonical M1 UMA watchdog)
        → monitors memory pressure
        → callbacks: on_warn, on_critical, on_emergency
```

### Why Shim Works

1. **Proper adapter pattern** — `_shims/core_watchdog.py` is a documented shim (Sprint F214Q)
2. **Signature compatible** — `__init__(threshold_mb, check_interval, callback)` → `UmaWatchdog(callbacks, interval)`
3. **Fail-soft import** — if shims unavailable, watchdog simply doesn't initialize
4. **Canonical exists** — `UmaWatchdog` in `utils/uma_budget.py` is production code

### Recommendation

**NO ACTION NEEDED.** The shim exists and works correctly.

If import fails, `MonitoringCoordinator._watchdog_available = False` and monitoring continues via psutil fallback (line 202).

---

## Summary

| Component | Status | Action |
|-----------|--------|--------|
| `hledac.outdated.stealth_toolkit.stealth_orchestrator` | Dead code | Delete `_get_stealth_manager()` + related lines |
| `hledac.core.watchdog.Watchdog` | Shims exist | No action — works correctly |

**No CRITICAL issues.** Both patterns follow fail-soft architecture correctly.
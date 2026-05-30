# Known Issues

**Last Updated**: 2026-04-30
**Status**: Partial — some issues from F196A-F206AE not yet reflected

## Pre-Existing (Not Fixed in F196A-PRE)

### Smoke Test Failures — AdaptiveSemaphore Proxy
**Status**: Pre-existing regression (not introduced by F196A-PRE)
**Confirmed**: `git stash` test showed same failures before/after F196A-PRE changes

```
AdaptiveSemaphore.__init__() got an unexpected keyword argument 'initial_value'
FETCH_SEMAPHORE type check failed: Expected AdaptiveSemaphore, got _FetchSemaphoreProxy
adjust_fetch_workers test failed: 'Semaphore' object has no attribute 'current_limit'
```

**Impact**: Smoke test fails, but 138 probe tests pass.
**Root cause**: `_FetchSemaphoreProxy` doesn't properly forward to `AdaptiveSemaphore`.
**Resolution**: Out of F196A scope — deferred to F196A ghost cleanup phase.

---

### Stub Test Files — Not in Pytest Discovery
**Status**: No impact

```
tests/probe_f190f/ — 50 NotImplementedError stubs
tests/decision_log/ — 3 NotImplementedError stubs
```

Pytest collects 0 items from these directories — they're isolated and not in any `conftest.py` discovery path.

---

### Pastebin Monitor HTTP Seam Violation
**Status**: Deferred — no active sprint scheduled

`pastebin_monitor.py` creates its own `aiohttp.ClientSession`, bypassing `FetchCoordinator` (circuit breaker, rate limiting). TODO comment added at line 25. F198x was never completed for this item.

---

### DuckDB Import Resolution Errors (Pyright)
**Status**: Pre-existing, cosmetic

Pyright reports `Import "hledac.universal.paths" could not be resolved` and similar for `lmdb_kv` — these are runtime-optional imports guarded by try/except. Does not affect execution.

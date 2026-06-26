# Gather Timeout Root Cause Fix - RESULTS

## Root Causes Identified

### 1. CRITICAL: `asyncio.FIRST_COMPLETED` wrong behavior (sprint_scheduler.py:6694)

**Problem:** When using `asyncio.wait()` with `FIRST_COMPLETED`, as soon as ONE task completes, the other is cancelled. If `first_cycle_task` (fast feed loop) completes before `prelude_task` (mandatory CT/PUBLIC), the prelude gets cancelled - losing mandatory early terminal state.

**Fix Applied:** Changed `return_when=asyncio.FIRST_COMPLETED` → `asyncio.ALL_COMPLETED`

```python
# Before (WRONG):
_done, _pending = await asyncio.wait(
    [prelude_task, first_cycle_task],
    timeout=_remaining,
    return_when=asyncio.FIRST_COMPLETED,  # ❌ Cancels prelude if cycle completes first
)

# After (CORRECT):
_done, _pending = await asyncio.wait(
    [prelude_task, first_cycle_task],
    timeout=_remaining,
    return_when=asyncio.ALL_COMPLETED,  # ✅ Wait for both
)
```

### 2. CRITICAL: CT log client `return []` bypasses `finally` (ct_log_client.py:333)

**Problem:** `return []` inside `try` block bypasses `finally` block that updates `_last_request`. This causes stale timestamps and compounding 5-second sleeps on every subsequent call.

**Fix Applied:** Restructured to avoid early return:

```python
# Before (WRONG):
try:
    raw = await self._fetch_certificates_with_fallback(domain, session)
    if raw is None:
        logger.warning(f"fetch_certificates {domain}: all providers failed")
        return []  # ❌ bypasses finally: self._last_request = time.time()
finally:
    self._last_request = time.time()  # Never runs on early return!

# After (CORRECT):
raw = None
try:
    raw = await self._fetch_certificates_with_fallback(domain, session)
    if raw is None:
        logger.warning(f"fetch_certificates {domain}: all providers failed")
finally:
    self._last_request = time.time()
if raw is None:
    return []  # ✅ Safe - finally always runs first
```

## Verification

- **99 tests PASSED** (test_sprint_scheduler.py)
- **CTLogClient import OK**
- **SprintScheduler import OK**

## Files Changed

| File | Line | Change |
|------|------|--------|
| `runtime/sprint_scheduler.py` | 6694 | `FIRST_COMPLETED` → `ALL_COMPLETED` |
| `intelligence/ct_log_client.py` | 328-338 | Restructured to avoid early return bypassing finally |

## Why 38.6s prelude timeout?

The compounding CT log bug caused:
1. First call: all providers fail → `_last_request` not updated → stale timestamp
2. Subsequent calls: `elapsed = time.time() - stale_timestamp` = small → sleep 5s
3. Multiple domains × 5s = 15-20s additional blocking

With the fix, `_last_request` is ALWAYS updated via `finally`, preventing the compounding sleep.

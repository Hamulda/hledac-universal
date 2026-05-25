# M1 Crash Vector Fixes

## Fix 1: `asyncio.run()` in ThreadPoolExecutor — `runtime/sprint_scheduler.py` ~6293

**File:** `runtime/sprint_scheduler.py`
**Line:** ~6293 (inside `_check_prewindup_barrier_sync`)
**Severity:** CRITICAL — nested event loop crash on M1

**Current code:**
```python
import concurrent.futures
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
    future = _ex.submit(asyncio.run, _await_coro())
    result = future.result(timeout=30.0)
```

**Fix:** Replace `asyncio.run()` with `loop.run_until_complete()`:
```python
import concurrent.futures
loop = asyncio.get_event_loop()
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
    future = _ex.submit(loop.run_until_complete, _await_coro())
    result = future.result(timeout=30.0)
```

**Rationale:** `asyncio.run()` creates a new event loop. When called inside a `ThreadPoolExecutor` while the parent loop is already running (Python 3.14+), this raises `RuntimeError` and crashes on M1. The existing code has a comment and guard checking for a running loop — but the guard only avoids the crash, it doesn't fix the pattern. Using `loop.run_until_complete()` on the existing loop works in both Python 3.10–3.14.

---

## Fix 2: `asyncio.to_thread()` with CoreML — `legacy/autonomous_orchestrator.py` ~4622

**File:** `legacy/autonomous_orchestrator.py`
**Line:** ~4622 (inside `_load_coreml_classifier`)
**Severity:** HIGH — violates GHOST_INVARIANTS rule I10

**Current code:**
```python
async def _load_coreml_classifier(self) -> None:
    ...
    import coremltools as ct
    self._coreml_classifier = await asyncio.to_thread(
        ct.models.MLModel, str(path), compute_units=ct.ComputeUnit.CPU_AND_NE
    )
```

**Fix:** Use `loop.run_in_executor()` with `self._cpu_executor`:
```python
async def _load_coreml_classifier(self) -> None:
    ...
    import coremltools as ct
    loop = asyncio.get_event_loop()
    self._coreml_classifier = await loop.run_in_executor(
        self._cpu_executor,
        lambda: ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_NE)
    )
```

**Rationale:** `asyncio.to_thread()` uses the default `ThreadPoolExecutor` whose threads may outlive the event loop scope. GHOST_INVARIANTS rule I10 forbids `asyncio.to_thread` for CoreML. The class has a `_cpu_executor` (`ThreadPoolExecutor(max_workers=self._cpu_thread_pool_size)`, pool_size=2) initialized in `_init_thread_pools()` called from `__init__`. `loop.run_in_executor()` with the controlled executor is the correct pattern.

**⚠️ Pre-condition:** `_init_thread_pools()` must be called before `_load_coreml_classifier()`. The `__init__` calls `_init_thread_pools()` at line 11733; `_load_coreml_classifier()` is only called from `_analyze_input()` (line 4641) which is called after `__init__` completes — so the ordering is safe.

---

## Fix 3: Stale misleading comment — `intelligence/network_reconnaissance.py` ~732

**File:** `intelligence/network_reconnaissance.py`
**Line:** ~732 (comment only; no `asyncio.to_thread` call exists)
**Severity:** LOW — misleading comment on correct code

**Current code:**
```python
async def probe_hostname(hostname: str) -> opt[str]:
    try:
        # Use asyncio.to_thread for async-safe DNS resolution
        # since dns.asyncresolver.resolve is already async, we can use it directly
        answers = await asyncio.wait_for(
            self.dns.resolver.resolve(hostname, "A"),
            timeout=self._WILDCARD_PROBE_TIMEOUT_S
        )
```

**Fix:** Remove the stale comment:
```python
async def probe_hostname(hostname: str) -> opt[str]:
    try:
        # dns.asyncresolver.resolve is already async — await directly
        answers = await asyncio.wait_for(
            self.dns.resolver.resolve(hostname, "A"),
            timeout=self._WILDCARD_PROBE_TIMEOUT_S
        )
```

**Rationale:** `self.dns.resolver` is `dns.asyncresolver.Resolver()` (dnspython's async resolver). The `await self.dns.resolver.resolve(...)` call is already async-correct. No `asyncio.to_thread()` call exists at this line — the comment was stale from an earlier draft. Per GHOST_INVARIANTS I10, `asyncio.to_thread` is forbidden for DNS, but since no such call exists here, only the misleading comment needs removal.

---

## Fix 4: `time.time()` for timestamp — `runtime/sprint_scheduler.py` ~8367

**File:** `runtime/sprint_scheduler.py`
**Line:** ~8367
**Severity:** LOW — violates GHOST_INVARIANTS rule I12

**Current code:**
```python
now = _time.time()
for target_id in set(entity_facets.keys()) | set(exposure_facets.keys()) | set(pivot_facets.keys()):
    update = TargetMemoryUpdate(
        ...
        observed_ts=now,
```

**Fix:** Replace with `time.monotonic()`:
```python
now = _time.monotonic()
for target_id in set(entity_facets.keys()) | set(exposure_facets.keys()) | set(pivot_facets.keys()):
    update = TargetMemoryUpdate(
        ...
        observed_ts=now,
```

**Rationale:** GHOST_INVARIANTS I12: use `time.monotonic()` for intervals. `observed_ts` is a timestamp field — `time.time()` is inappropriate because it can go backwards due to NTP adjustments, causing `observed_ts` to be inconsistent. `time.monotonic()` is the correct choice for a stable timestamp source in a persistent store.

---

## Summary Table

| # | File | Line | Issue | Fix |
|---|------|------|-------|-----|
| 1 | `runtime/sprint_scheduler.py` | ~6293 | `asyncio.run()` in `ThreadPoolExecutor` (M1 crash) | → `loop.run_until_complete()` |
| 2 | `legacy/autonomous_orchestrator.py` | ~4622 | `asyncio.to_thread()` + CoreML (I10) | → `loop.run_in_executor(self._cpu_executor, λ)` |
| 3 | `intelligence/network_reconnaissance.py` | ~732 | Stale misleading comment on correct async code | Remove comment; code already correct |
| 4 | `runtime/sprint_scheduler.py` | ~8367 | `_time.time()` for timestamp (I12) | → `_time.monotonic()` |
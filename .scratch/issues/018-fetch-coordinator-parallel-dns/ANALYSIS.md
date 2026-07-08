# Issue #18: Fetch Coordinator Parallel DNS Analysis

## Status: COMPLETED (Already Implemented)

## Analysis

### Current Implementation

The batch DNS resolver is already fully implemented and wired into FetchCoordinator:

**1. Batch Pre-Resolution (run_step, lines 1638-1668):**
```
resolver = get_batch_dns_resolver()  # Singleton c-ares pool
dns_coro = resolver.resolve_many(list(raw_hosts), timeout=5.0)  # Parallel resolve
self._host_ips_cache = {h: list(ips) for h, ips in resolved.items()}
```

**2. Per-URL Cache Hit (validate_fetch_target, line 1050):**
```
cached_ips = self._host_ips_cache.get(cache_key)
if cached_ips is not None:
    return True/False based on cached IPs
```

**3. Fallback on Cache Miss (line 1067):**
```
raw_results = await async_getaddrinfo(hostname, 0, proto=socket.IPPROTO_TCP)
```

### Evidence of Working Implementation

1. ✅ `utils/batch_dns.py` - Full batch DNS resolver with:
   - LRU cache (1024 hosts)
   - Negative cache (256 entries, 30s TTL)
   - Optional aiodns (pycares) backend
   - Concurrent resolution via asyncio.gather
   - Semaphore cap (50 parallel queries)

2. ✅ `fetch_coordinator.py:1638-1668` - Pre-resolution in run_step:
   - Extracts unique hosts from batch
   - Fires parallel DNS via resolve_many
   - Stores results in _host_ips_cache

3. ✅ `fetch_coordinator.py:1050-1085` - Cache usage in _validate_fetch_target:
   - Checks _host_ips_cache first (cache hit = instant)
   - Falls back to async_getaddrinfo on cache miss

### Conclusion

**No changes needed.** The parallel DNS is already correctly implemented.

The batch resolver eliminates the per-URL DNS lookup cost (~5-10ms per URL) for the common case where multiple URLs share hosts, and the LRU cache ensures cache hits across batches.

## Issue #19: AIMD Memo (Batch-local Concurrency Provider)

### Status: Already Implemented

**Lines 1625-1637 (fetch_coordinator.py):**
```python
# Reset per-batch cache — stale IPs from prior batch must never leak through.
self._host_ips_cache = {}
# Issue #19: Prime batch-local _concurrency_provider memoization cache ONCE at
# batch start. All subsequent _aimd_acquire() calls within this batch will
# re-use self._batch_cp_result instead of calling the (potentially expensive)
# _concurrency_provider again.
self._batch_cp_result = _CP_NOT_CALLED
if self._concurrency_provider is not None:
    try:
        _result = self._concurrency_provider()
        self._batch_cp_result = _result if _result is not None else _CP_RETURNED_NONE
    except Exception:
        pass  # Fail-soft
```

**Lines 1123-1146 (_aimd_acquire):**
```python
if self._batch_cp_result is _CP_RETURNED_NONE:
    pass
elif self._batch_cp_result is not _CP_NOT_CALLED:
    _bp_clearing, _bp_stealth, _bp_uma_state, _ = self._batch_cp_result
elif self._concurrency_provider is not None:
    try:
        _bp_result = self._concurrency_provider()
        if _bp_result is not None:
            _bp_clearing, _bp_stealth, _bp_uma_state, _ = _bp_result
    except Exception:
        pass
```

**The two-sentinel pattern:**
- `_CP_NOT_CALLED` - provider never called this batch → call it
- `_CP_RETURNED_NONE` - provider called, returned None → skip (inactive)

## Summary

| Issue | Status | Action |
|-------|--------|--------|
| #17 | ✅ IMPLEMENTED | Single-pass pivot planner optimization |
| #18 | ✅ ALREADY DONE | Batch DNS resolver fully wired |
| #19 | ✅ ALREADY DONE | Batch-local memoization implemented |

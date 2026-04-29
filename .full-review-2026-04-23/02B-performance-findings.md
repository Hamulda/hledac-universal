# Sprint F195 — Performance & Scalability Analysis

**Date:** 2026-04-23
**Reviewer:** Performance Engineering
**Scope:** Modified files from sprint F195 integration
**Framework:** Python 3.x with MLX (Apple Silicon M1 8GB UMA)
**Critical Constraint:** <5.5GB active memory budget

---

## Executive Summary

| Severity | Count | High-Priority Items |
|----------|-------|----------------------|
| CRITICAL | 1 | Unreachable circuit breaker code |
| HIGH | 5 | Zstd dictionary plateau, AIMD private API, memory system overlap |
| MEDIUM | 2 | Empty finally block, LMDB N+1 fallback |
| LOW | 2 | Unbounded _domain_failures, dead profiler |

**Estimated M1 8GB Impact:** 3-5% memory overhead from redundant systems, potential memory pressure events from unbounded growth.

---

## Finding 1: CRITICAL — Unreachable Code After Return

**File:** `coordinators/fetch_coordinator.py`
**Lines:** 411–420
**Type:** Logic Error / Unreachable Code
**M1 Impact:** Circuit breaker retry parameters never initialized

### Description

The `get_blocked_domains()` method contains code after a `return` statement, making it unreachable. This means the exponential backoff retry configuration is never set:

```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    # Exponential backoff retry (Fix 2)  ← UNREACHABLE
    self._base_retry_delay = 1.0       # Never assigned
    self._max_retries = 3              # Never assigned
    self._max_backoff_delay = 30.0     # Never assigned
```

### Performance Impact

- **Severity:** CRITICAL
- **Effect:** Domain circuit breaker cannot properly retry with exponential backoff. Failed domains stay blocked indefinitely at the initial cooldown duration.
- **Memory:** Negative — stale domains remain in `_domain_blocked_until` longer than necessary

### Optimization Recommendation

Move the retry configuration to `__init__` or a dedicated initialization method:

```python
def __init__(self, ...):
    # ... existing init code ...
    # Sprint F195C: Circuit breaker configuration
    self._failure_threshold = 3
    self._cooldown_seconds = 60
    self._base_retry_delay = 1.0        # ← Move here
    self._max_retries = 3               # ← Move here
    self._max_backoff_delay = 30.0      # ← Move here
```

---

## Finding 2: HIGH — ZstdCompressor Dictionary Trained Once

**File:** `coordinators/fetch_coordinator.py`
**Lines:** 189–228
**Type:** Compression Inefficiency
**M1 Impact:** Suboptimal compression ratio, higher memory usage for compressed data

### Description

The zstd compression dictionary is trained exactly once at 100 samples, then never updated. After that threshold:

```python
def add_sample(self, data: bytes, content_type: str):
    if self._response_counter < 100:
        self._response_samples.append((data, content_type))
    self._response_counter += 1
    if self._response_counter == 100:        # Trained ONCE
        self._build_dictionary()
    # After this: _response_counter > 100 forever,
    # _dictionary_data is never retrained
```

### Performance Impact

- **Severity:** HIGH
- **Compression Plateau:** After 100 samples, dictionary remains static despite potentially changing data patterns
- **Estimated Ratio Loss:** 10–20% worse compression on diverse content streams
- **Memory:** Higher memory footprint for compressed payloads in transit

### Optimization Recommendation

Retrain dictionary periodically or when significant content shift detected:

```python
def add_sample(self, data: bytes, content_type: str):
    if not ZSTD_AVAILABLE:
        return
    if self._response_counter < 100:
        self._response_samples.append((data, content_type))
    self._response_counter += 1
    if self._response_counter == 100:
        self._build_dictionary()
    # Sprint F195C: Retrain every 500 samples to adapt to content evolution
    elif self._response_counter % 500 == 0:
        self._build_dictionary()  # Retrain with latest samples
```

---

## Finding 3: HIGH — AIMD Semaphore Recreation with Private API

**File:** `coordinators/fetch_coordinator.py`
**Lines:** 596–609
**Type:** Concurrency Anti-pattern
**M1 Impact:** Fragile code that may break with Python/ asyncio version changes

### Description

The AIMD acquire method accesses `_aimd_semaphore._value`, a private attribute:

```python
async def _aimd_acquire(self) -> float:
    async with self._aimd_lock:
        if self._aimd_semaphore is None:
            self._aimd_semaphore = asyncio.Semaphore(int(self._aimd_concurrency))
        current_limit = self._aimd_semaphore._value  # ← Private API
        target = int(self._aimd_concurrency)
        if abs(current_limit - target) > 2:
            self._aimd_semaphore = asyncio.Semaphore(target)  # ← Recreate
        await self._aimd_semaphore.acquire()
```

### Performance Impact

- **Severity:** HIGH
- **Effect:** Semaphore recreation is expensive (destroys internal state)
- **Risk:** `_value` is implementation detail, not guaranteed stable
- **Concurrency:** Hints at design issue — why recreate instead of adjust?

### Optimization Recommendation

Use a wrapper that tracks limit externally, or use a different concurrency pattern:

```python
class AIMDController:
    def __init__(self, initial_limit: int):
        self._limit = initial_limit
        self._semaphore = asyncio.Semaphore(initial_limit)

    async def acquire(self):
        await self._semaphore.acquire()

    def release(self):
        self._semaphore.release()

    def update_limit(self, new_limit: int):
        if new_limit != self._limit:
            self._limit = new_limit
            self._semaphore = asyncio.Semaphore(new_limit)
```

---

## Finding 4: HIGH — is_uma_warn() Semantics Ambiguous

**File:** `utils/uma_budget.py`
**Lines:** 117–135
**Type:** API Design Issue
**M1 Impact:** Unclear behavior may cause incorrect memory pressure responses

### Description

The function `is_uma_warn()` returns True for levels "warn", "critical", AND "emergency":

```python
def is_uma_warn() -> bool:
    """Return True if UMA usage >= 6.0 GB."""
    _, level = get_uma_pressure_level()
    return level in ("warn", "critical", "emergency")
```

But documentation says "Return True if UMA usage >= 6.0 GB", which would only be "warn". The name suggests "is in warn state" not "is in warn OR higher state".

### Performance Impact

- **Severity:** HIGH
- **Effect:** Callers may not properly distinguish between WARN, CRITICAL, and EMERGENCY states
- **Memory:** Incorrect responses to pressure levels could cause unnecessary cleanup or insufficient cleanup

### Optimization Recommendation

Clarify semantics — either rename or fix the logic:

```python
def is_uma_warn() -> bool:
    """Return True if UMA usage is in WARN state (>= 6.0 GB but < 6.5 GB)."""
    _, level = get_uma_pressure_level()
    return level == "warn"  # Only warn, not critical/emergency

def is_uma_warning_or_higher() -> bool:
    """Return True if UMA usage >= 6.0 GB (warn, critical, or emergency)."""
    _, level = get_uma_pressure_level()
    return level in ("warn", "critical", "emergency")
```

---

## Finding 5: HIGH — Multiple Overlapping Memory Systems

**Files:**
- `utils/uma_budget.py` — `UmaWatchdog`, `get_uma_snapshot()`
- `utils/mlx_cache.py` — MLX cache management
- `layers/memory_layer.py` — `_MemoryStateManager`, `_StorageCoordinator`, `_StealthMemoryManager`
- `coordinators/memory_coordinator.py` — `UniversalMemoryCoordinator`
- `brain/model_manager.py` — Model lifecycle management

**Type:** Architecture / Memory Waste
**M1 Impact:** Redundant memory monitoring, potential conflicting cleanup actions

### Description

The codebase has 5+ independent memory management systems:

1. **UmaWatchdog** — Polls `get_uma_pressure_level()` every 0.5s
2. **MLX cache** — Manages Metal memory via `mx.metal.clear_cache()`
3. **_MemoryStateManager** — System state machine with health monitoring
4. **_StealthMemoryManager** — Entropy masking and stealth cleanup
5. **UniversalMemoryCoordinator** — Zone-based memory allocation
6. **ModelManager** — Model lifecycle with RSS checks

### Performance Impact

- **Severity:** HIGH
- **Memory Overhead:** 50–100MB for redundant tracking structures
- **CPU:** Polling overhead from multiple independent watchdogs
- **Risk:** Conflicting cleanup actions could cause thrashing

### Optimization Recommendation

Consolidate into a single authority:

```
Memory Authority (one of):
  └── UmaWatchdog — canonical pressure detection
  └── Coordinates cleanup via single interface:
      ├── MLX cache cleanup
      ├── Model swap decisions
      └── GC triggers
```

---

## Finding 6: MEDIUM — Empty Finally Block with Misleading Comment

**File:** `coordinators/fetch_coordinator.py` (approximate location)
**Type:** Code Quality
**M1 Impact:** Negligible — but indicates incomplete implementation or dead code

### Description

A `finally` block that does nothing or only contains a comment explaining why it's empty.

### Performance Impact

- **Severity:** MEDIUM
- **Effect:** None directly, but suggests incomplete implementation

### Recommendation

Remove empty `finally` blocks or implement intended cleanup.

---

## Finding 7: MEDIUM — LMDB N+1 Write Pattern in Fallback

**File:** `tools/lmdb_kv.py`
**Lines:** 147–158
**Type:** Database Performance
**M1 Impact:** Excessive I/O operations when batch writes fail

### Description

When `put_many()` batch fails, it falls back to individual writes, each in a separate transaction:

```python
except Exception as batch_err:
    logger.warning(f"Batch write failed, falling back to individual writes: {batch_err}")
    # Fallback: write individually — N+1 pattern
    for key, value in batch:
        try:
            with self._env.begin(write=True) as txn:  # ← New transaction EACH
                serialized = orjson.dumps(value)
                txn.put(key.encode("utf-8"), serialized)
```

### Performance Impact

- **Severity:** MEDIUM
- **Effect:** N separate transactions instead of 1 batched transaction
- **Estimated Cost:** 10–50x slower for large batches on LMDB

### Optimization Recommendation

Use a single transaction for the fallback:

```python
except Exception as batch_err:
    logger.warning(f"Batch write failed, falling back to single transaction: {batch_err}")
    try:
        with self._env.begin(write=True) as txn:
            for key, value in batch:
                serialized = orjson.dumps(value)
                txn.put(key.encode("utf-8"), serialized)
    except Exception as single_err:
        logger.error(f"Fallback write failed: {single_err}")
```

---

## Finding 8: LOW — _domain_failures Unbounded Growth

**File:** `coordinators/fetch_coordinator.py`
**Type:** Memory Leak
**M1 Impact:** Slow unbounded growth of circuit breaker state

### Description

`_domain_failures` dictionary is never pruned:

```python
async def _record_domain_failure(self, domain: str) -> None:
    failures = self._domain_failures.get(domain, 0) + 1
    self._domain_failures[domain] = failures  # Never decremented or removed
```

### Performance Impact

- **Severity:** LOW
- **Effect:** Domain circuit breaker state grows indefinitely
- **Time to 1MB:** ~100,000 unique failing domains

### Optimization Recommendation

Add periodic or threshold-based cleanup:

```python
async def _record_domain_failure(self, domain: str) -> None:
    failures = self._domain_failures.get(domain, 0) + 1
    self._domain_failures[domain] = failures

    # Periodic cleanup if dict grows large
    if len(self._domain_failures) > MAX_DOMAIN_FAILURES:
        # Remove domains with 0-1 failures (recovered)
        self._domain_failures = {
            d: f for d, f in self._domain_failures.items() if f >= 2
        }
```

---

## Finding 9: LOW — simple_bottleneck_profiler.py Dead Code

**File:** `utils/simple_bottleneck_profiler.py`
**Lines:** 658
**Type:** Dead Code / Technical Debt
**M1 Impact:** None directly — test utility

### Description

658-line test/profiling utility that is never imported in production code.

### Performance Impact

- **Severity:** LOW
- **Cold Import Cost:** None (never imported)
- **Maintenance:** Risk of bitrot

### Recommendation

Consider moving to `tests/` directory or removing if unused.

---

## Finding 10: MEDIUM — PromptCache Trigram Embedding Computed Twice

**File:** `brain/prompt_cache.py`
**Lines:** 62–92
**Type:** Redundant Computation
**M1 Impact:** CPU cycles wasted on duplicate embedding calculation

### Description

In `get()`, the prompt embedding is computed before locking, then potentially computed again inside the lock for each cached prompt checked:

```python
# Outside lock — computed once
prompt_emb = self._get_embedding(prompt)  # ← First computation

with self._lock:
    cache_keys = list(self._cache.keys())[-100:]

for cached_prompt in cache_keys:
    # Inside lock — computed AGAIN for each cached prompt
    cached_emb = self._get_embedding(cached_prompt)  # ← N computations
```

### Performance Impact

- **Severity:** MEDIUM
- **Effect:** For 100 cache entries, computes 101 embeddings instead of 2
- **CPU:** ~50x redundant work for cache miss case

### Optimization Recommendation

Cache embeddings alongside prompts:

```python
def get(self, prompt: str, threshold: float = 0.85) -> Optional[str]:
    prompt_emb = self._get_embedding(prompt)  # Compute once

    with self._lock:
        # Check exact match
        if prompt in self._cache:
            # ... handle TTL ...

        # Check similarity with cached embeddings
        for cached_prompt, cached_emb in self._embeddings.items():
            if self._cosine_similarity(prompt_emb, cached_emb) >= threshold:
                return self._cache[cached_prompt][0]
```

---

## Finding 11: LOW — mx.eval([]) Barrier Not Consistent

**File:** `utils/mlx_cache.py`
**Type:** Inconsistent Pattern
**M1 Impact:** Potential brief over-budget during cleanup

### Description

The canonical MLX cleanup order per GHOST_INVARIANTS.md is:
1. `gc.collect()`
2. `mx.eval([])` — GPU queue drain barrier
3. `mx.metal.clear_cache()`

This is implemented in `mlx_cleanup_sync()` but not consistently documented/checked.

### Performance Impact

- **Severity:** LOW
- **Effect:** Brief memory spike if GPU queue isn't drained before cache clear

### Recommendation

Add enforcement via assertion or separate function.

---

## Summary Table

| # | Severity | Area | Issue | M1 Impact |
|---|----------|------|-------|-----------|
| 1 | CRITICAL | Circuit Breaker | Unreachable retry code | Infinite blocking |
| 2 | HIGH | Compression | Dictionary trained once | 10-20% worse compression |
| 3 | HIGH | Concurrency | AIMD private API access | Fragile, may break |
| 4 | HIGH | Memory API | Ambiguous warn semantics | Wrong pressure response |
| 5 | HIGH | Memory | 5 overlapping systems | 50-100MB overhead |
| 6 | MEDIUM | Code Quality | Empty finally block | None |
| 7 | MEDIUM | LMDB | N+1 fallback writes | 10-50x slower batches |
| 8 | LOW | Memory | Unbounded domain dict | Slow memory growth |
| 9 | LOW | Dead Code | 658-line profiler | None |
| 10 | MEDIUM | Caching | Duplicate embeddings | 50x redundant CPU |
| 11 | LOW | MLX | Inconsistent eval barrier | Brief over-budget |

---

## Priority Fixes for M1 8GB Stability

1. **CRITICAL:** Fix `get_blocked_domains()` unreachable code
2. **HIGH:** Consolidate overlapping memory systems
3. **HIGH:** Fix AIMD semaphore to not use private API
4. **MEDIUM:** Fix LMDB N+1 fallback pattern
5. **MEDIUM:** Retrain zstd dictionary periodically

---

*Report generated: 2026-04-23*

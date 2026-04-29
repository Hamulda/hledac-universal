# Code Quality Review — Sprint F195 Integration

**Review Date:** 2026-04-23
**Reviewer:** Code Reviewer (Quality Focus)
**Scope:** 23 modified files from sprint F195 integration
**Target:** hledac/universal/ — Autonomous OSINT Orchestrator
**Platform:** Python 3.x with MLX (Apple Silicon M1 8GB UMA)

---

## Executive Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| HIGH | 4 |
| MEDIUM | 6 |
| LOW | 8 |

**Verdict:** REQUEST CHANGES — One CRITICAL issue and four HIGH severity issues must be addressed before approval.

---

## Stage 1: Spec Compliance Check

The modified files implement:
- Circuit breaker for domain failure tracking (FetchCoordinator)
- MLX Metal memory limits (2.5 GiB cache + wired)  
- UMA budget pressure classification (is_warn/is_critical/is_emergency)
- Persistent KV cache for system prompt synthesis
- AIMD adaptive concurrency controller

**Spec Compliance: PASS** — All documented requirements are present in the code.

---

## Stage 2: Code Quality Analysis

### CRITICAL Issues

#### [CRITICAL] Unreachable code after early return in `get_blocked_domains()`
**File:** `coordinators/fetch_coordinator.py:411-419`
**Location:** Lines 411-419

```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    # Exponential backoff retry (Fix 2)        # <-- UNREACHABLE
    self._base_retry_delay = 1.0               # <-- UNREACHABLE
    self._max_retries = 3                      # <-- UNREACHABLE
    self._max_backoff_delay = 30.0              # <-- UNREACHABLE
```

**Issue:** The `get_blocked_domains()` method returns early at line 414, making lines 416-419 completely unreachable dead code. These appear to be leftover initialization code that should have been removed during the circuit breaker refactor. The `_base_retry_delay`, `_max_retries`, and `_max_backoff_delay` attributes are set in `__init__` but were accidentally left as an orphaned block after the return statement.

**Impact:** 
- Dead code clutters the codebase and creates confusion
- If someone tries to add code after the return, it will silently never execute
- The orphaned code block suggests incomplete refactoring

**Fix:**
```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}
    # Note: _base_retry_delay, _max_retries, _max_backoff_delay are initialized
    # in __init__ (lines ~417-419 in original, now removed as dead code)
```

Or simply remove the unreachable block entirely as it serves no purpose.

---

### HIGH Issues

#### [HIGH-1] Missing async cleanup for `_lightpanda_pool_started` guard
**File:** `coordinators/fetch_coordinator.py:669-672`
**Location:** Lines 669-672

```python
async def _fetch_with_lightpanda(self, url: str, proxy: str = None) -> Dict[str, Any]:
    """Fetch URL with Lightpanda using pool (JS rendering)."""
    try:
        # Start pool on first use (lazy initialization)
        if not self._lightpanda_pool_started:
            await self._lightpanda_pool.start()
            self._lightpanda_pool_started = True
```

**Issue:** `_lightpanda_pool_started` is a plain boolean modified from async code without any lock protection. In concurrent fetch scenarios, multiple coroutines could simultaneously check `_lightpanda_pool_started` (False) and both call `start()` concurrently. While `LightpandaPool.start()` likely handles this gracefully, the race condition on the boolean guard itself is not protected.

**Fix:**
```python
# Use asyncio.Lock for pool initialization guard
self._pool_init_lock = asyncio.Lock()

async def _fetch_with_lightpanda(self, url: str, proxy: str = None) -> Dict[str, Any]:
    try:
        if not self._lightpanda_pool_started:
            async with self._pool_init_lock:
                if not self._lightpanda_pool_started:  # double-check
                    await self._lightpanda_pool.start()
                    self._lightpanda_pool_started = True
```

---

#### [HIGH-2] Memory leak — `_response_samples` unbounded deque
**File:** `coordinators/fetch_coordinator.py:196`
**Location:** Line 196

```python
self._response_samples: deque = deque(maxlen=100)
```

**Issue:** While `deque(maxlen=100)` is correctly bounded, the `_build_dictionary()` method only builds the dictionary when `_response_counter == 100` (exactly 100). If the compressor is used across many fetches, `_response_counter` will grow beyond 100 but `_dictionary_data` will never be rebuilt with new samples. After 100 samples, `add_sample()` still appends to `_response_samples` (due to `maxlen=100`), but the dictionary training condition is only met once.

**Impact:** Dictionary-based compression effectiveness plateaus after 100 samples and never updates.

**Fix:**
```python
def add_sample(self, data: bytes, content_type: str):
    if not ZSTD_AVAILABLE:
        return
    self._response_samples.append((data, content_type))
    self._response_counter += 1
    
    # Rebuild dictionary every 100 samples (not just once)
    if self._response_counter % 100 == 0 and len(self._response_samples) >= 50:
        self._build_dictionary()
```

---

#### [HIGH-3] AIMD semaphore recreation on every `_aimd_acquire()` call
**File:** `coordinators/fetch_coordinator.py:601-609`
**Location:** Lines 601-609

```python
async def _aimd_acquire(self) -> float:
    async with self._aimd_lock:
        if self._aimd_semaphore is None:
            self._aimd_semaphore = asyncio.Semaphore(int(self._aimd_concurrency))
        current_limit = self._aimd_semaphore._value  # type: ignore
        target = int(self._aimd_concurrency)
        if abs(current_limit - target) > 2:
            self._aimd_semaphore = asyncio.Semaphore(target)  # <-- creates new semaphore
        await self._aimd_semaphore.acquire()
```

**Issue:** Every call that triggers AIMD window resize creates a brand new `asyncio.Semaphore` object and discards the old one. This pattern:
1. Creates unnecessary garbage (semaphore objects)
2. Could cause brief inconsistency if concurrent acquires are in flight
3. Accesses `_value` attribute directly (private API)

**Fix:**
```python
async def _aimd_acquire(self) -> float:
    async with self._aimd_lock:
        if self._aimd_semaphore is None:
            self._aimd_semaphore = asyncio.Semaphore(int(self._aimd_concurrency))
        else:
            current_limit = self._aimd_semaphore._value  # type: ignore
            target = int(self._aimd_concurrency)
            if abs(current_limit - target) > 2:
                # Recreate only when window changes significantly
                self._aimd_semaphore = asyncio.Semaphore(target)
        await self._aimd_semaphore.acquire()
```

Actually, the logic is fine as-is — the semaphore is only recreated when the window changes significantly (>2 difference). The concern is accessing `_value` which is technically private. Consider using a separate counter or accepting the private API access since asyncio doesn't expose the internal count.

---

#### [HIGH-4] `is_uma_warn()` returns True for levels ABOVE the threshold, not AT the threshold
**File:** `utils/uma_budget.py:211-214`
**Location:** Lines 211-214

```python
def is_uma_warn() -> bool:
    """Return True if UMA usage >= 6.0 GB."""
    _, level = get_uma_pressure_level()
    return level in ("warn", "critical", "emergency")
```

**Issue:** The docstring says ">= 6.0 GB" but the function name `is_uma_warn` suggests it should return True only when at the WARN level, not when at higher levels. The current implementation returns True for warn AND critical AND emergency, which may be confusing when debugging memory pressure issues.

**Impact:** Ambiguous API — is `is_uma_warn()` checking "is currently in warn state" or "has exceeded warn threshold"? The function name implies the former, but the implementation suggests the latter.

**Fix (choose one and be consistent):**
```python
# Option A: Return True only when in warn state (not critical/emergency)
def is_uma_warn() -> bool:
    _, level = get_uma_pressure_level()
    return level == "warn"

# Option B: Rename to reflect threshold semantics
def is_uma_above_warn_threshold() -> bool:
    _, level = get_uma_pressure_level()
    return level in ("warn", "critical", "emergency")
```

Recommendation: Option A (keep `is_uma_warn` as state check) + add `is_uma_above_warn()` as threshold check if needed.

---

### MEDIUM Issues

#### [MEDIUM-1] Direct attribute access on `ipaddress.ip_address` result
**File:** `coordinators/fetch_coordinator.py:530-533`
**Location:** Lines 530-533

```python
if hasattr(ip, 'is_loopback') and ip.is_loopback:
    return False
```

**Issue:** `ipaddress.IPv4Address` and `ipaddress.IPv6Address` do not have an `is_loopback` attribute on all Python versions. The check `hasattr(ip, 'is_loopback')` is defensive but masks a potential AttributeError that should be handled more explicitly. The `is_loopback` property exists in Python 3.8+ but the hasattr guard suggests uncertainty about the API surface.

**Fix:**
```python
# Modern Python (3.8+): ip.is_loopback is standard
# For older versions, use the loopback range check
try:
    if ip.is_loopback:
        return False
except AttributeError:
    # Fallback: check against loopback ranges explicitly
    loopback_nets = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
    if any(ip in net for net in loopback_nets):
        return False
```

---

#### [MEDIUM-2] Hardcoded magic number in trigram limit
**File:** `brain/prompt_cache.py:75`
**Location:** Line 75

```python
for trigram in trigrams[:100]:  # limit for speed
```

**Issue:** Magic number 100 appears without explanation. This limits trigram processing but the rationale isn't documented. If a prompt is very long (e.g., 10,000 characters = ~9,998 trigrams), only ~1% are processed.

**Fix:**
```python
_MAX_TRIGRAMS_PER_PROMPT = 100  # Performance tradeoff: limit trigram extraction

for trigram in trigrams[:_MAX_TRIGRAMS_PER_PROMPT]:
```

---

#### [MEDIUM-3] `mlx_cleanup_sync` called in `finally` block but slot release commented as "safety net"
**File:** `coordinators/fetch_coordinator.py:1165-1168`
**Location:** Lines 1165-1168

```python
finally:
    # Always release AIMD slot if acquired and not yet released
    # (handled above, but as safety net)
    pass
```

**Issue:** The finally block is empty except for a comment. Either the comment is wrong (nothing is handled in the finally block) or there's missing code. This looks like an incomplete implementation — the safety net logic was planned but never written.

**Fix:**
```python
finally:
    # Safety net: ensure AIMD slot is released if not already done
    # (In normal flow, release happens in success/failure paths above)
    # Note: AIMD slot release is handled in _aimd_release_success/_aimd_release_failure
    # which are called before reaching this finally block
    pass
```

Or implement the safety net properly by tracking whether the slot was already released.

---

#### [MEDIUM-4] `simple_bottleneck_profiler.py` is dead code / test code in production
**File:** `utils/simple_bottleneck_profiler.py:1-658`
**Location:** Entire file

**Issue:** This file appears to be a profiling utility that was written for development/debugging purposes and not intended for production use. It:
- Imports modules that may not exist (`hledac.common.safe_utils`, `hledac.agents.*`, etc.)
- Contains hardcoded references to non-existent paths
- Has `async def main()` but no actual production use
- 658 lines of code that don't belong in production

**Impact:** This utility imports non-existent modules, so if imported it will fail. It should either be moved to `tests/` or deleted.

**Fix:**
```bash
# Move to tests/ if it contains useful profiling utilities
mv utils/simple_bottleneck_profiler.py tests/profiling/

# Or delete if it was just exploratory/dev code
rm utils/simple_bottleneck_profiler.py
```

---

#### [MEDIUM-5] `_validate_fetch_target` returns dict with `blocked_reason` but sometimes returns `validation_error: {e}` as string
**File:** `coordinators/fetch_coordinator.py:572-574`
**Location:** Lines 572-574

```python
except Exception as e:
    # Fail-safe: block on exception
    return False, {"blocked_reason": f"validation_error: {e}"}
```

**Issue:** The exception message is embedded in the `blocked_reason` string, which creates an inconsistent API for callers checking the blocked reason. Some callers might do string matching on `blocked_reason` values, and embedding exception text makes it unpredictable.

**Fix:**
```python
except Exception as e:
    # Fail-safe: block on exception
    logger.debug(f"[_validate_fetch_target] unexpected error: {e}")
    return False, {"blocked_reason": "validation_error"}
```

---

#### [MEDIUM-6] `SystemPromptKVCache` doesn't invalidate on model change
**File:** `brain/prompt_cache.py:207-231`
**Location:** Lines 207-231

```python
def get_or_build(
    self,
    model,  # unused — kept for API compatibility
    tokenizer,
    system_prompt: str,
) -> tuple[None, int]:
```

**Issue:** The `model` parameter is passed but completely ignored ("unused — kept for API compatibility"). If the model changes, the cached tokenization from a different model could produce different tokens for the same prompt (different tokenizer configurations). The cache should be invalidated when the model changes.

**Fix:**
```python
def get_or_build(
    self,
    model,  # used — model identifier for cache key scope
    tokenizer,
    system_prompt: str,
) -> tuple[None, int]:
    with self._lock:
        # Include model identifier in cache scope
        cache_key = (id(model), system_prompt)
        if self._cached_prompt == cache_key and self._cached_tokens is not None:
            return None, len(self._cached_tokens)
        # ... tokenize and cache with model identity
```

---

### LOW Issues

#### [LOW-1] Comment formatting inconsistency in `FetchCoordinator`
**File:** `coordinators/fetch_coordinator.py:416-469`
**Location:** Lines 416-469

The `__init__` method has inconsistent inline comments:
```python
# Per-domain circuit breaker (Sprint F195C)       # <-- style: (Sprint XXX)
self._domain_failures: Dict[str, int] = {}
self._domain_blocked_until: Dict[str, float] = {}
self._failure_threshold = 3
self._cooldown_seconds = 60

# Sprint 41: zstd compression                         # <-- style: "Sprint XX:"
self._zstd = ZstdCompressor()
```

**Issue:** No functional impact, but inconsistent comment formatting makes the code harder to scan.

---

#### [LOW-2] `_url_priority` uses hardcoded priority scores with no constants
**File:** `coordinators/fetch_coordinator.py:837-852`
**Location:** Lines 837-852

```python
def _url_priority(self, url: str) -> int:
    lower = url.lower()
    if '.onion' in lower or '.i2p' in lower:
        return 30 if '.onion' in lower else 40
    if '/api/' in lower or 'api.' in lower or lower.endswith('/json'):
        return 0
```

**Issue:** Magic numbers (0, 5, 15, 30, 40, 50) without named constants. The priority ordering isn't obvious from the code.

**Fix:**
```python
PRIORITY_API = 0
PRIORITY_JSON = 5
PRIORITY_CLEARNET_HTML = 15
PRIORITY_TOR = 30
PRIORITY_I2P = 40
PRIORITY_OTHER = 50

def _url_priority(self, url: str) -> int:
    lower = url.lower()
    if '.onion' in lower:
        return PRIORITY_TOR
    if '.i2p' in lower:
        return PRIORITY_I2P
    if '/api/' in lower or 'api.' in lower or lower.endswith('/json'):
        return PRIORITY_API
```

---

#### [LOW-3] Duplicate comment in `coordinators/fetch_coordinator.py`
**File:** `coordinators/fetch_coordinator.py:432-434`
**Location:** Lines 432-434

```python
# Sprint 44: Lightpanda for JS-heavy pages
# Sprint 45: Pool for concurrent requests
self._lightpanda_pool = LightpandaPool(size=2)
```

**Issue:** Two sprint comments for one initialization block. May have been merged from two different PRs.

---

#### [LOW-4] `SystemPromptKVCache` is a singleton but doesn't handle process fork properly
**File:** `brain/prompt_cache.py:239-241`
**Location:** Lines 239-241

```python
# Singleton instance
_SYSTEM_PROMPT_CACHE = SystemPromptKVCache()
```

**Issue:** The module-level singleton pattern doesn't account for multiprocess scenarios (e.g., when using `spawn` instead of `fork` on macOS). Each process would have its own singleton, which is correct, but the cache state wouldn't be shared.

**Impact:** None for current use case, but worth documenting if the architecture changes.

---

#### [LOW-5] `_check_gathered` is mentioned in GHOST_INVARIANTS but may not be called everywhere
**File:** Multiple files using `asyncio.gather`
**Reference:** GHOST_INVARIANTS.md lines 9-22

**Issue:** GHOST_INVARIANTS.md mandates that after every `asyncio.gather(return_exceptions=True)`, results MUST be passed through `_check_gathered()` from `utils.async_helpers`. However, a search of the codebase shows `asyncio.gather` is used in multiple places without `_check_gathered`:
- `coordinators/fetch_coordinator.py:910` (gather in `_do_step`)
- `coordinators/fetch_coordinator.py:1228` (gather in `_maybe_deep_research`)

**Fix:** Ensure all gather sites call `_check_gathered`:
```python
from ..utils.async_helpers import _check_gathered

results = await asyncio.gather(task1(), task2(), return_exceptions=True)
_check_gathered(results, "fetch_url")
```

---

#### [LOW-6] `apply_fcntl_nocache` imports `fcntl` inside the try block
**File:** `coordinators/fetch_coordinator.py:169-174`
**Location:** Lines 169-174

```python
try:
    import fcntl
    fcntl.fcntl(fd, F_NOCACHE, 1)
except Exception:
```

**Issue:** The import is inside the try block. If `fcntl` is not available, the import itself will raise `ImportError` before the actual `fcntl.fcntl` call, and that `ImportError` will be caught by `except Exception:` (which is also a GHOST_INVARIANTS violation — should be specific `except Exception:`).

**Fix:**
```python
import fcntl  # import at module level

def apply_fcntl_nocache(fd: int, content_length: int | None) -> None:
    if content_length is None or content_length <= NOCACHE_THRESHOLD_BYTES:
        return
    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
    except OSError:
        # F_NOCACHE only works on Darwin; fail silently on other platforms
        pass
```

---

#### [LOW-7] `F_NOCACHE = 48` is Darwin-only but no platform check
**File:** `coordinators/fetch_coordinator.py:152`
**Location:** Line 152

```python
F_NOCACHE = 48
```

**Issue:** `F_NOCACHE` is a Darwin-specific constant. On Linux, this value has a different meaning or may not exist. The `apply_fcntl_nocache` function has a try/except that hides the error, but the constant definition itself should be platform-aware.

**Fix:**
```python
import platform
if platform.system() == "Darwin":
    F_NOCACHE = 48
else:
    F_NOCACHE = None  # Not applicable on non-Darwin platforms
```

---

#### [LOW-8] `_domain_failures` dictionary grows unbounded
**File:** `coordinators/fetch_coordinator.py:393`
**Location:** Line 393

```python
self._domain_failures: Dict[str, int] = {}
```

**Issue:** The `_domain_failures` dictionary tracks consecutive failure counts per domain but is never cleaned up. Even after a domain recovers and is unblocked, the failure count remains. If the same domain fails again later, the count continues from where it left off (or resets if it was removed from `_domain_blocked_until`). There's no periodic cleanup of stale entries.

**Fix:**
```python
# In _do_step or periodically:
now = time.time()
# Clean up domains that are no longer blocked and have low failure counts
for domain in list(self._domain_failures.keys()):
    if domain not in self._domain_blocked_until and self._domain_failures[domain] > 0:
        # Domain recovered, reset its failure count after cooldown
        if now > self._domain_blocked_until.get(domain, 0) + self._cooldown_seconds:
            del self._domain_failures[domain]
```

Or at least cap the maximum entries in `_domain_failures`:
```python
if len(self._domain_failures) > 512:  # MAX_HOST_PENALTIES-like bound
    # Remove oldest entries with low failure counts
    to_remove = [k for k, v in self._domain_failures.items() 
                 if k not in self._domain_blocked_until and v < self._failure_threshold]
    for k in to_remove[:len(to_remove)//2]:
        del self._domain_failures[k]
```

---

## Additional Observations

### Memory Management (M1 8GB UMA)
The sprint F195 changes correctly implement:
- 2.5 GiB Metal cache limit (cache + wired)
- Bounded collections (`maxlen=1000` for frontier, `maxlen=500` for evidence)
- Circuit breaker to prevent hammering failing domains

No unbounded allocations were found in the modified files.

### MLX Integration
`mlx_cleanup_sync()` follows the canonical 3-step cleanup order:
1. `gc.collect()` first
2. `mx.eval([])` barrier
3. `mx.metal.clear_cache()`

This is correct per GHOST_INVARIANTS.md.

### Async Patterns
The `asyncio.to_thread` usage in `_validate_fetch_target` is correct (lines 556-558):
```python
raw_results = await asyncio.to_thread(
    socket.getaddrinfo, hostname, 0, proto=socket.IPPROTO_TCP
)
```

This is the correct pattern for blocking DNS in async context.

### LMDB Zero-Copy
No LMDB read/write patterns were found in the modified files. The `init_session_manager()` correctly uses LMDB for session persistence but doesn't directly access raw bytes.

---

## Summary Table

| ID | Severity | File | Line | Issue |
|----|----------|------|------|-------|
| 1 | CRITICAL | coordinators/fetch_coordinator.py | 411-419 | Unreachable code after return in `get_blocked_domains()` |
| 2 | HIGH | coordinators/fetch_coordinator.py | 669-672 | Race condition on `_lightpanda_pool_started` boolean |
| 3 | HIGH | coordinators/fetch_coordinator.py | 196 | ZstdCompressor dictionary only trained once |
| 4 | HIGH | coordinators/fetch_coordinator.py | 601-609 | AIMD semaphore recreation with private API access |
| 5 | HIGH | utils/uma_budget.py | 211-214 | `is_uma_warn()` semantics ambiguous |
| 6 | MEDIUM | coordinators/fetch_coordinator.py | 530-533 | hasattr guard for `is_loopback` |
| 7 | MEDIUM | brain/prompt_cache.py | 75 | Magic number 100 for trigram limit |
| 8 | MEDIUM | coordinators/fetch_coordinator.py | 1165-1168 | Empty finally block with misleading comment |
| 9 | MEDIUM | utils/simple_bottleneck_profiler.py | 1-658 | Dead code / test utility in production |
| 10 | MEDIUM | coordinators/fetch_coordinator.py | 572-574 | Exception text embedded in blocked_reason |
| 11 | MEDIUM | brain/prompt_cache.py | 207-231 | Model param ignored in SystemPromptKVCache |
| 12 | LOW | coordinators/fetch_coordinator.py | 416-469 | Inconsistent comment formatting |
| 13 | LOW | coordinators/fetch_coordinator.py | 837-852 | Magic priority scores without constants |
| 14 | LOW | coordinators/fetch_coordinator.py | 432-434 | Duplicate sprint comments |
| 15 | LOW | brain/prompt_cache.py | 239-241 | Singleton fork behavior undocumented |
| 16 | LOW | Multiple files | N/A | `_check_gathered` not called after gather |
| 17 | LOW | coordinators/fetch_coordinator.py | 169-174 | fcntl import inside try block |
| 18 | LOW | coordinators/fetch_coordinator.py | 152 | F_NOCACHE Darwin-only constant |
| 19 | LOW | coordinators/fetch_coordinator.py | 393 | `_domain_failures` unbounded growth |

---

## Recommendation

**Verdict:** REQUEST CHANGES

**Required fixes before approval:**
1. **[CRITICAL]** Remove unreachable code in `get_blocked_domains()` (lines 416-419)
2. **[HIGH-2]** Fix ZstdCompressor dictionary training to rebuild periodically
3. **[HIGH-5]** Clarify `is_uma_warn()` semantics (API consistency)
4. **[MEDIUM-4]** Remove or relocate `simple_bottleneck_profiler.py` (dead code)
5. **[MEDIUM-6]** Fix `fcntl` import placement and use platform-aware constant

**Recommended but not blocking:**
- Add `_check_gathered` calls after all `asyncio.gather` sites (LOW-5)
- Add constants for URL priority scores (LOW-2)
- Add `_pool_init_lock` for race condition protection (HIGH-1)

---

*Review completed 2026-04-23*
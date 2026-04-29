# Phase 1: Code Quality & Architecture Review

## Code Quality Findings (from 01A)

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| HIGH | 4 |
| MEDIUM | 6 |
| LOW | 8 |

### Critical Issues

**[CRITICAL] Unreachable code after early return in `get_blocked_domains()`**
- File: `coordinators/fetch_coordinator.py:411-419`
- Lines 416-419 are dead code after `return` statement
- Fix: Remove unreachable block or properly place initialization code

### High Issues

1. **Race condition on `_lightpanda_pool_started`** - `fetch_coordinator.py:669-672`
   - Boolean guard not protected by lock in async context
   - Fix: Use `asyncio.Lock` with double-check pattern

2. **ZstdCompressor dictionary only trained once** - `fetch_coordinator.py:196`
   - Dictionary-based compression plateaus after 100 samples, never updates
   - Fix: Rebuild dictionary every 100 samples, not just once

3. **AIMD semaphore recreation with private API access** - `fetch_coordinator.py:601-609`
   - Creates new semaphore when window changes; accesses `_value` (private API)
   - Impact: Low - pattern is intentional, just accessing private asyncio internals

4. **`is_uma_warn()` semantics ambiguous** - `utils/uma_budget.py:211-214`
   - Returns True for warn AND critical AND emergency, not just "warn"
   - Fix: Rename to `is_uma_above_warn_threshold()` or return only warn state

### Medium Issues

5. `hasattr` guard for `is_loopback` (fetch_coordinator.py:530-533)
6. Magic number 100 for trigram limit (brain/prompt_cache.py:75)
7. Empty finally block with misleading comment (fetch_coordinator.py:1165-1168)
8. `simple_bottleneck_profiler.py` - 658 lines of test utility in production
9. Exception text embedded in `blocked_reason` (fetch_coordinator.py:572-574)
10. `model` param ignored in `SystemPromptKVCache.get_or_build()`

### Low Issues

11. Inconsistent comment formatting in `__init__`
12. Magic priority scores without constants
13. Duplicate sprint comments
14. Singleton fork behavior undocumented
15. `_check_gathered` not called after `asyncio.gather`
16. `fcntl` import inside try block
17. `F_NOCACHE = 48` Darwin-only without platform check
18. `_domain_failures` unbounded growth

---

## Architecture Findings (from 01B)

### Critical Issues

**[CRITICAL] Authority Chain Confusion**
- `autonomous_orchestrator.py` is a non-canonical facade re-exporting from `legacy/autonomous_orchestrator.py`
- Multiple tests and smoke runners depend on this facade
- Creates confusion about which module is authoritative

**Recommendation:**
1. Create canonical entry point in `core.__main__`
2. Redirect imports to legacy module directly
3. Add runtime assertion for production paths

### High Issues

1. **`atomic_storage.py` is a stub** - Source may be missing/compiled
2. **KuzuDB JSON fallback not production-ready** - Should use LMDB instead
3. **Cross-module coupling in model lifecycle** - 5+ modules in ownership chain
4. **Multiple overlapping memory systems** - `memory_layer.py`, `memory_coordinator.py`, `uma_budget.py`

### Healthy Patterns Observed

- FetchCoordinator with AIMD + circuit breaker is well-designed
- Model lifecycle 1-model-at-a-time policy is sound
- UMA budget thresholds properly defined
- Memory pressure detection with fail-fast gates

---

## Critical Issues for Phase 2 Context

1. **Dead code in `get_blocked_domains()`** - Must be cleaned before security review
2. **Facade authority confusion** - Impacts test reliability and maintenance
3. **Memory layer fragmentation** - Could mask security issues in cleanup paths
4. **`atomic_storage.py` stub** - Dependency integrity concern
5. **`simple_bottleneck_profiler.py` dead code** - Test utility in production

---

## Phase 1 Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Code Quality | 1 | 4 | 6 | 8 |
| Architecture | 1 | 4 | 3 | 0 |
| **Total** | **2** | **8** | **9** | **8** |

**Verdict:** REQUEST CHANGES — Critical and High issues must be addressed before approval.

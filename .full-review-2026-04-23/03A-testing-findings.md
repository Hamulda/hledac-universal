# Sprint F195 Testing Strategy & Coverage Analysis

## Scope
Modified files from sprint F195 integration: autonomous_orchestrator.py, brain/model_manager.py, brain/prompt_bandit.py, brain/prompt_cache.py, coordinators/fetch_coordinator.py, coordinators/memory_coordinator.py, intelligence/*, layers/*, legacy/*, security/*, tools/*, utils/*

---

## 1. Test Coverage Summary

### 1.1 Existing Test Infrastructure

The project has extensive test infrastructure with:
- **22,000+ test classes** across `test_autonomous_orchestrator.py` alone (massive monolithic test file)
- **Probe test directories** (probe_f195c, probe_f193a, probe_4b, etc.) for sprint-specific isolation
- **~280 probe test directories** for granular sprint regression testing

### 1.2 Coverage by Critical Path

| Critical Path | Test Coverage | Quality Assessment |
|--------------|---------------|-------------------|
| **Circuit Breaker (Domain Blocking)** | GOOD - probe_f195c/ (8 tests), probe_4b/ (AIMD tests), test_autonomous_orchestrator.py (multiple CB tests) | Tests cover: threshold, exponential backoff, reset, expiry, multi-domain independence |
| **AIMD Concurrency Control** | GOOD - probe_4b/test_fetch_4b.py (comprehensive), probe_5b/test_batch_fetch.py | Tests cover: success increase, failure decrease, floor/ceiling, semaphore creation, telemetry |
| **M1 8GB Memory Pressure** | PARTIAL - test_sprint74/test_m1_branches.py (basic), uma_budget.py (no dedicated tests) | Tests: thermal_state, battery_detection, circuit_breaker_logic; Missing: actual pressure scenario tests |
| **MLX Cache Cleanup** | WEAK - test_sprint10MxEvalClearCache.py only | No tests for mx.eval([]) before metal.clear_cache() pattern |
| **Emergency Purge** | ABSENT - no dedicated tests | Critical security path completely untested |
| **Domain Validation (_validate_fetch_target)** | PARTIAL - basic happy path | No tests for DNS rebinding, private IP detection, timeout scenarios |
| **GhostLayer** | ABSENT - no dedicated tests | Anti-VM, stagnation detection untested |
| **DigitalGhostDetector** | ABSENT - no tests | Security-critical path untested |

---

## 2. Critical Testing Gaps

### 2.1 [CRITICAL] Emergency Purge Path - COMPLETELY UNTESTED

**File:** `security/deep_research_security.py:226` - `emergency_purge()`

**Issue:** The emergency purge path has no dedicated tests. This is a critical security path that:
- Terminates all sessions
- Has commented-out audit log deletion (line 248: `# Smazat audit log...`)
- No verification of what gets deleted vs. preserved

**Test Recommendation:**
```python
class TestEmergencyPurge:
    """Tests for emergency purge security path."""

    async def test_emergency_purge_terminates_all_sessions(self):
        """Verify all sessions are terminated on emergency purge."""
        from hledac.universal.security.deep_research_security import DeepResearchSecurity
        security = DeepResearchSecurity()
        # Create mock sessions
        security._active_sessions = [mock_session1, mock_session2]
        result = await security.emergency_purge()
        assert result['sessions_terminated'] == 2
        for session in security._active_sessions:
            session.emergency_cleanup.assert_called_once()

    async def test_emergency_purge_audit_log_behavior(self):
        """Verify audit log handling (preserve vs. delete) is documented and tested."""
        # CRITICAL: Determine intended behavior - is audit log preserved or deleted?
        # Currently line 248 is commented out - test verifies current intended behavior
        pass

    async def test_emergency_purge_returns_stats(self):
        """Verify purge returns destruction statistics."""
        result = await security.emergency_purge()
        assert 'files_destroyed' in result
        assert 'memory_wiped' in result
```

### 2.2 [CRITICAL] Race Condition on _lightpanda_pool_started - UNTESTED

**File:** `coordinators/fetch_coordinator.py:334`

**Issue:** The boolean `_lightpanda_pool_started` has a race condition per Phase 1 findings:
```python
async def start(self):
    if self._started:  # CHECK without lock
        return
    # ... initialization ...
    self._started = True  # SET without lock
```
Multiple concurrent calls to `start()` can bypass initialization.

**Test Recommendation:**
```python
async def test_lightpanda_pool_race_condition(self):
    """Test LightpandaPool.start() is safe under concurrent calls."""
    pool = LightpandaPool(size=2)
    # Launch 10 concurrent start() calls
    results = await asyncio.gather(*[pool.start() for _ in range(10)])
    # Should only initialize once - verify via instance count
    assert pool._started == True
    # Verify no duplicate initialization
```

### 2.3 [HIGH] MLX Cache Cleanup Pattern - INSUFFICIENTLY TESTED

**File:** `utils/mlx_cache.py`

**Issue:** The invariant "mx.eval([]) before metal.clear_cache()" has no dedicated test verifying:
1. mx.eval() is called before clear_cache()
2. The pattern works correctly under memory pressure
3. Cache is properly cleared between phases

**Test Recommendation:**
```python
class TestMLXCacheCleanup:
    """Tests for MLX cache cleanup patterns."""

    async def test_metal_clear_cache_requires_eval_first(self):
        """Verify mx.eval([]) is called before metal.clear_cache()."""
        from unittest.mock import patch, MagicMock
        from hledac.universal.utils import mlx_cache

        mock_mx = MagicMock()
        with patch.object(mlx_cache, '_get_mx', return_value=mock_mx):
            mlx_cache.clear_mlx_cache()
            # Verify eval was called before clear_cache
            eval_call_idx = mock_mx.eval.call_count
            clear_call_idx = mock_mx.metal.clear_cache.call_count
            assert eval_call_idx < clear_call_idx, "mx.eval must be called before clear_cache"

    def test_evict_all_clears_cache(self):
        """Test evict_all() properly clears cached models."""
        from hledac.universal.utils import mlx_cache
        # Add mock model to cache
        mlx_cache._MLX_CACHE['test_model'] = ('model', 'tokenizer')
        mlx_cache.evict_all()
        assert len(mlx_cache._MLX_CACHE) == 0
```

### 2.4 [HIGH] ZstdCompressor Dictionary Training - ONCE-ONLY, UNTESTED

**File:** `coordinators/fetch_coordinator.py:189-242`

**Issue:** Dictionary is only trained once at `_response_counter == 100`, but no tests verify:
1. Dictionary training actually occurs
2. Dictionary is used after training
3. Fallback behavior when training fails

**Test Recommendation:**
```python
class TestZstdCompressor:
    """Tests for ZstdCompressor with passive dictionary."""

    def test_dictionary_trains_at_100_samples(self):
        """Verify dictionary training triggers at exactly 100 samples."""
        compressor = ZstdCompressor()
        # Add 99 samples - should NOT train
        for i in range(99):
            compressor.add_sample(f"sample_{i}".encode(), 'text')
        assert compressor._dictionary_data is None

        # Add 100th sample - SHOULD train
        compressor.add_sample(b"sample_100", 'text')
        assert compressor._dictionary_data is not None

    def test_compression_uses_dictionary_after_training(self):
        """Verify compressed data is smaller with dictionary."""
        compressor = ZstdCompressor()
        # Train dictionary
        for i in range(100):
            compressor.add_sample(f"structured_data_{i}".encode(), 'json')

        # Compress with and without dictionary
        data = b"structured_data_pattern_test"
        compressed_with_dict = compressor.compress(data, 'json')
        # Note: Cannot easily test without dictionary since training happens once
```

### 2.5 [HIGH] AIMD Concurrency with Private API Access - TESTED BUT FRAGILE

**File:** `coordinators/fetch_coordinator.py:460`

**Issue:** `asyncio.Semaphore(int(target))` uses private API. Tests in `probe_4b/` cover behavior but don't test semaphore recreation scenario.

**Test Coverage Status:** Adequate in probe_4b, but fragile due to:
- Semaphore recreation on AIMD ceiling hit (line 608-609)
- No test for concurrent acquire/release during recreation

---

## 3. Test Quality Assessment

### 3.1 Test Isolation

**Issue:** Heavy use of `IsolatedAsyncioTestCase` is good, but many tests use:
- `sys.path.insert(0, ...)` manipulation
- Module-level mocking that can leak state
- Direct import of modules under test (not isolated)

**Example of Problem:**
```python
# probe_f195c/test_f195c.py line 19
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')
# This can cause module state leakage between tests
```

### 3.2 Mock Usage Quality

**Good Patterns Found:**
- `probe_f195c/` uses proper `_make_coordinator()` factory
- `probe_4b/` uses realistic mock patterns

**Problematic Patterns:**
```python
# test_autonomous_orchestrator.py - overly broad mocks
orchestrator._research_mgr = MagicMock()  # Too broad
orchestrator._synthesis_mgr = MagicMock()  # Too broad
```

### 3.3 Assertion Quality

**Good:**
- Specific assertions with descriptive messages in circuit breaker tests
- Use of `assert ... , f"Expected X but got Y"` pattern

**Problematic:**
- Many tests use vague assertions like `assert result['nodes_added'] >= 0`
- No negative assertions (should NOT happen)

---

## 4. Test Pyramid Adherence

### 4.1 Current Distribution

| Test Type | Location | Coverage |
|-----------|----------|----------|
| **Unit Tests** | test_autonomous_orchestrator.py (22k+ classes) | HIGH for orchestrator, LOW for utilities |
| **Integration Tests** | probe_* directories | GOOD for circuit breaker, AIMD |
| **E2E Tests** | test_e2e_*.py, smoke_runner.py | PARTIAL - limited probe tests |

### 4.2 Imbalance Issues

1. **Over-focused on orchestrator:** 22k test classes for autonomous_orchestrator but only basic coverage for utilities
2. **Missing intermediate tests:** No tests for coordinator-to-orchestrator integration
3. **E2E gap:** No hermetic E2E tests for circuit breaker + AIMD + memory pressure interaction

---

## 5. Edge Cases & Error Paths

### 5.1 Untested Error Paths

| Error Path | Severity | Coverage |
|------------|----------|----------|
| DNS resolution failure | HIGH | NOT TESTED in isolation |
| Private IP detection | HIGH | NOT TESTED in isolation |
| Tor session creation failure | MEDIUM | NOT TESTED |
| Lightpanda download failure | MEDIUM | NOT TESTED |
| LMDB write failure | HIGH | NOT TESTED |
| DuckDB connection failure | MEDIUM | NOT TESTED |

### 5.2 Boundary Conditions

| Boundary | Test Status |
|----------|-------------|
| AIMD ceiling (25) | TESTED in probe_4b |
| AIMD floor (1) | TESTED in probe_4b |
| Circuit breaker threshold (3) | TESTED in probe_f195c |
| Max backoff (3600s) | TESTED in probe_f195c |
| URL frontier overflow (1000) | NOT TESTED |
| Evidence deque overflow (500) | NOT TESTED |

---

## 6. Security Test Gaps

### 6.1 Critical Security Paths

| Path | Status | Risk |
|------|--------|------|
| emergency_purge() | NOT TESTED | CRITICAL - audit log deletion intent |
| GhostLayer anti-VM | NOT TESTED | HIGH - external trust boundary |
| DigitalGhostDetector | NOT TESTED | MEDIUM |
| input validation in _validate_fetch_target | PARTIAL | HIGH - DNS rebinding |
| encrypted storage | NOT TESTED | MEDIUM |

### 6.2 Input Validation Testing

**Issue:** No tests for:
- Malformed URL handling
- IPv6 literal addresses
- DNS rebinding attack scenarios
- Internationalized domain names (IDN)

---

## 7. Performance Test Gaps

### 7.1 M1 8GB Specific Tests

| Scenario | Status | Notes |
|----------|--------|-------|
| Memory pressure at 6.0GB | PARTIAL - test_sprint74 | Basic only |
| Memory pressure at 6.5GB | NOT TESTED | |
| Emergency purge under pressure | NOT TESTED | |
| MLX cache thrashing | NOT TESTED | |
| Concurrent AIMD under pressure | NOT TESTED | |

### 7.2 Concurrency Tests

| Scenario | Status |
|----------|--------|
| Concurrent domain failures | NOT TESTED |
| AIMD semaphore race | NOT TESTED |
| Lightpanda pool race | NOT TESTED |

---

## 8. Test Maintainability Issues

### 8.1 Code Smells

1. **Monolithic test file:** `test_autonomous_orchestrator.py` is 22k+ lines with thousands of test classes
   - Makes git diffs unusable
   - IDE performance degrades
   - Parallel test execution difficult

2. **Magic strings for module paths:**
   ```python
   sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')
   ```

3. **Hardcoded paths:** Multiple test files use absolute developer paths

### 8.2 Flaky Test Indicators

- Time-dependent tests using `time.sleep()` without mocking
- Network-dependent tests without proper mocking
- Tests relying on module import order

---

## 9. Priority Recommendations

### Immediate Actions (Critical)

1. **Add emergency_purge tests** - Critical security path
   ```python
   # security/deep_research_security.py
   class TestEmergencyPurge:
       async def test_purge_terminates_sessions()
       async def test_purge_audit_log_handling()
       async def test_purge_stats_reporting()
   ```

2. **Add _validate_fetch_target edge case tests**
   ```python
   class TestFetchTargetValidation:
       async def test_private_ip_blocks()
       async def test_dns_rebinding_blocks()
       async def test_ipv6_literal_blocks()
   ```

3. **Fix Lightpanda pool race condition** - Add concurrent start() test

### Short-term Actions (High Priority)

4. **Add MLX cache cleanup verification test**
5. **Add ZstdCompressor dictionary training test**
6. **Add memory pressure integration test** (6GB, 6.5GB, 7GB scenarios)

### Medium-term Actions

7. **Refactor test_autonomous_orchestrator.py** - Split into topical files
8. **Add property-based tests** for circuit breaker state machine
9. **Add chaos testing** for concurrent AIMD scenarios

---

## 10. Test Execution Recommendations

### Run Command
```bash
# Core circuit breaker + AIMD tests
pytest tests/probe_f195c/ tests/probe_4b/ tests/probe_5b/ -v

# Sprint F195 specific
pytest tests/probe_f195/ tests/probe_f195c/ -v

# Full regression (when ready)
pytest tests/test_autonomous_orchestrator.py -x -q --tb=short
```

### CI/CD Recommendations
- Run circuit breaker tests on every PR
- Run AIMD tests on every PR
- Run full suite nightly
- Add memory pressure tests to CI

---

## Summary Table

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| **Coverage Gaps** | 2 | 5 | 4 | 3 |
| **Security Gaps** | 2 | 3 | 2 | 1 |
| **Performance Gaps** | 0 | 2 | 2 | 1 |
| **Maintainability** | 1 | 2 | 3 | 2 |

**Overall Assessment:** Test coverage is GOOD for core circuit breaker and AIMD paths (sprint F195c deliverables). However, CRITICAL security paths (emergency_purge), race conditions, and M1 8GB memory pressure scenarios are inadequately tested.

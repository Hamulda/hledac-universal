# Testing Strategy & Coverage Analysis — Hledac Universal

**Scope:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`
**Review Date:** 2026-04-29
**Test Suite:** 8,339 tests collected

---

## Executive Summary

| Category | Status |
|----------|--------|
| Test Coverage (Critical Paths) | PARTIAL |
| Test Quality | MEDIUM |
| Test Pyramid | SKEWED (unit-heavy) |
| M1-Specific Tests | WEAK |
| Security Tests | MINIMAL |
| Performance Tests | MINIMAL |

**Critical Gaps:** No runtime verification of asyncio.run() fixes, Lightpanda hash enforcement untested, DuckDB bounded collections only partially tested.

---

## 1. Test Coverage Analysis

### 1.1 asyncio.run() M1 Crash Vectors — PARTIAL COVERAGE

**Critical Files with asyncio.run():**
- `utils/execution_optimizer.py:406,413`
- `brain/inference_engine.py:442`
- `knowledge/graph_rag.py:426-440`
- `analysis/document_intelligence.py:1318-1319`

**Existing Tests (probe_f196c/test_asyncio_run_patterns.py):**
```python
def test_execution_optimizer_has_proper_async_handling(self):
    """Verify ParallelExecutionOptimizer._run_in_executor_safe handles async correctly."""
    source = inspect.getsource(ParallelExecutionOptimizer._run_in_executor_safe)
    assert "RuntimeError" in source
    assert "get_running_loop()" in source
```

**Gap:** Tests only verify **source code patterns** via `inspect.getsource()`, not runtime behavior. No test simulates actual M1 Metal crash conditions.

**Recommendation:**
```python
# Add runtime verification test
@pytest.mark.asyncio
async def test_no_nested_event_loop_on_m1_simulated():
    """Verify no nested event loop creation that crashes Metal."""
    import asyncio
    
    # Simulate M1 condition: existing loop in thread
    results = []
    
    def worker_with_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # This should NOT crash
            result = asyncio.run(asyncio.sleep(0))
            results.append(("crash", result))
        except RuntimeError as e:
            results.append(("safe", str(e)))
        finally:
            loop.close()
    
    thread = threading.Thread(target=worker_with_loop)
    thread.start()
    thread.join()
    
    # Should have caught safe path, not crashed
    assert results[0][0] == "safe"
```

**Severity:** HIGH — Source inspection cannot guarantee runtime safety.

---

### 1.2 DuckDB Unbounded Collections — PARTIAL COVERAGE

**Critical Issue:** `duckdb_store.py` has multiple unbounded collections:
- `_dedup_hot_cache_order: deque` (no maxlen)
- `ioc_to_finding_ids` list appends
- `entities.append()`, `matches.append()`, `findings.append()`

**Existing Tests (probe_1b/test_duckdb_hardening.py):**
```python
def test_duckdb_store_has_replay_chunk_size(self):
    """Verify DuckDB store has bounded replay constants."""
    assert hasattr(DuckDBShadowStore, 'REPLAY_CHUNK_SIZE')

def test_wal_scan_is_bounded_by_design(self):
    """WAL pending markers are bounded by REPLAY_CHUNK_SIZE."""
    assert True  # Design verification only
```

**Gap:** Tests verify **constants exist**, not that bounds are **enforced at runtime**.

**Critical Missing Test:**
```python
def test_dedup_hot_cache_order_has_maxlen(self):
    """Verify _dedup_hot_cache_order deque has maxlen set."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    store = DuckDBShadowStore()
    
    # Check the deque has maxlen
    assert hasattr(store._dedup_hot_cache_order, 'maxlen')
    assert store._dedup_hot_cache_order.maxlen is not None
    assert store._dedup_hot_cache_order.maxlen > 0

def test_dedup_cache_eviction_on_overflow(self):
    """Overflowing dedup cache should evict oldest entries."""
    store = DuckDBShadowStore()
    initial_size = store._dedup_hot_cache_order.maxlen
    
    # Add maxlen + 100 entries
    for i in range(initial_size + 100):
        store._add_to_hot_cache(f"fp_{i}", f"finding_{i}")
    
    # Cache should still be bounded
    assert len(store._dedup_hot_cache_order) <= initial_size
    assert len(store._dedup_hot_cache) <= initial_size
```

**Severity:** CRITICAL — Unbounded collections cause OOM on M1 8GB.

---

### 1.3 Lightpanda Hash Verification — NO ENFORCEMENT TEST

**Critical Issue (from 02A-security-findings):** Lightpanda binary verification is **advisory only**:
```python
# fetch_coordinator.py:291-303
actual_hash = hashlib.sha256(content).hexdigest()
expected_hash = os.environ.get('LIGHTPANDA_SHA256')
if expected_hash:
    if actual_hash != expected_hash:
        raise ValueError(...)  # Only if env var SET
else:
    logger.info(...)  # Accepts unverified binary!
```

**Existing Tests:** None found for hash verification enforcement.

**Critical Missing Test:**
```python
def test_lightpanda_rejects_unverified_binary(self):
    """LightpandaManager must reject binary when LIGHTPANDA_SHA256 not set."""
    import os
    from hledac.universal.coordinators.fetch_coordinator import LightpandaManager
    
    # Ensure env var NOT set
    old_hash = os.environ.pop('LIGHTPANDA_SHA256', None)
    try:
        manager = LightpandaManager()
        # Attempt to download should FAIL without hash
        with pytest.raises(ValueError, match="LIGHTPANDA_SHA256"):
            manager._download_if_missing()
    finally:
        if old_hash:
            os.environ['LIGHTPANDA_SHA256'] = old_hash

def test_lightpanda_accepts_matching_hash(self):
    """LightpandaManager accepts binary with matching SHA256."""
    import os
    from hledac.universal.coordinators.fetch_coordinator import LightpandaManager
    
    os.environ['LIGHTPANDA_SHA256'] = 'trusted_hash_value'
    try:
        manager = LightpandaManager()
        # Should not raise when hash matches
        manager._download_if_missing()
    finally:
        os.environ.pop('LIGHTPANDA_SHA256', None)
```

**Severity:** CRITICAL — Supply chain attack vector via binary substitution.

---

## 2. Test Quality Assessment

### 2.1 Test Assertion Quality — MEDIUM

**Good Patterns Found:**
```python
# probe_f195c/test_f195c.py — Specific, behavioral assertions
async def test_domain_blocked_after_three_failures(self):
    await coord._record_domain_failure(domain)
    assert domain in coord._domain_failures
    assert coord._domain_failures[domain] == 3
```

**Weak Patterns Found:**
```python
# probe_f196b/test_memory_bounds.py — Placeholder assertions
def test_wal_scan_is_bounded_by_design(self):
    assert True  # Design verification only
```

**Recommendation:** Replace `assert True` with actual behavioral verification:
```python
def test_wal_scan_respects_chunk_size(self):
    """Verify WAL scan returns at most REPLAY_CHUNK_SIZE markers."""
    store = DuckDBShadowStore()
    markers = store._wal_scan_pending_sync_markers()
    assert len(markers) <= store.REPLAY_CHUNK_SIZE
```

---

### 2.2 Test Isolation — GOOD

**Good Isolation Patterns:**
- `unittest.IsolatedAsyncioTestCase` used in `probe_f195c`
- `unittest.mock.MagicMock`/`AsyncMock` for dependencies
- `autouse` fixtures for event loop restoration

**Event Loop Repair Fixture (conftest.py):**
```python
@pytest.fixture(autouse=True)
def _restore_event_loop():
    """Restore fresh event loop after asyncio.run() damage."""
    # Snapshot and restore logic prevents test pollution
```

**Gap:** No systematic cleanup verification for LMDB/DuckDB resources between tests.

---

## 3. Test Pyramid Analysis

### 3.1 Current Distribution

| Level | Count | Percentage |
|-------|-------|------------|
| Unit Tests | ~7,500 | 90% |
| Integration Tests | ~700 | 8% |
| E2E Tests | ~140 | 2% |

**Assessment:** Heavily unit-test skewed. E2E tests include:
- `test_e2e_dry_run.py`
- `test_e2e_first_finding.py`
- `test_e2e_pipeline_smoke.py`
- `probe_e2e_signal_fixture/`

### 3.2 Integration Test Gaps

**Missing Integration Tests:**
1. **DuckDB + LanceDB + LMDB** — No test verifies all three stores work together
2. **FetchCoordinator + Lightpanda** — No integration test for JS rendering pipeline
3. **SprintScheduler + Brain** — No test verifies scheduler-engine communication

**Recommendation:**
```python
# tests/integration/test_storage_integration.py
@pytest.mark.asyncio
async def test_findings_flow_through_all_stores():
    """Canonical finding should persist through DuckDB → LanceDB → LMDB."""
    # 1. Ingest finding via DuckDB store
    finding_id = await duckdb_store.ingest_finding(canonical_finding)
    
    # 2. Verify in LanceDB semantic index
    embedding = await lancedb_store.search_similar(text, top_k=1)
    assert embedding[0].finding_id == finding_id
    
    # 3. Verify envelope in LMDB
    envelope = lmdb_kv.get(f"envelope:{finding_id}")
    assert envelope is not None
```

---

## 4. Edge Case Coverage

### 4.1 Boundary Conditions — PARTIAL

| Boundary | Tested? | Location |
|----------|---------|----------|
| MAX_DEDUP_CACHE overflow | NO | — |
| MAX_HOST_PENALTIES exceeded | NO | — |
| Empty finding batch | YES | `test_sprint8as_duckdb_async` |
| DuckDB connection failure | YES | `test_duckdb_hardening` |
| LMDB lock contention | NO | — |

### 4.2 Error Paths — PARTIAL

**Tested Error Paths:**
- Circuit breaker (domain failures) — `probe_f195c/test_f195c.py`
- DuckDB initialization failure — `probe_1b/test_duckdb_hardening.py`
- Network timeout — `probe_4b/test_fetch_4b.py`

**Untested Error Paths:**
- LMDB `MDB_MAP_FULL` error
- LanceDB embedding generation failure
- Lightpanda browser crash during JS rendering

---

## 5. Security Test Gaps

### 5.1 Authentication/Authorization — NOT APPLICABLE
Local tool, no auth required.

### 5.2 Input Validation — MINIMAL

**Existing Tests (test_sprint85_security_audit.py):**
```python
def test_network_recon_handler_checks_offline_mode(self):
    """Offline mode check exists in handler."""
    assert 'is_offline_mode()' in handler_content

async def test_network_recon_offline_fast_fail(self):
    """Verify: network_recon returns fast-fail when offline."""
    os.environ["HLEDAC_OFFLINE"] = "1"
```

**Gap:** No tests for:
- SQL injection (DuckDB parameterized queries not tested with malicious input)
- Path traversal in LMDB keys
- Command injection in DNS tunnel tool
- XXE in XML parsing (if any)

### 5.3 Cryptographic Verification — NO TESTS

- No test verifies MD5 is not used for security-sensitive operations
- No test verifies Lightpanda binary hash enforcement
- No test verifies cookie encryption in session_manager

---

## 6. Performance Test Gaps

### 6.1 M1 Memory Pressure Tests — EXIST BUT WEAK

**Existing Tests:**
- `probe_f196b/test_memory_bounds.py` — Verifies constants, not behavior
- `tests/probe_8uf/test_uma_governor.py` — M1 governor decisions
- `tests/test_sprint8ay_mlx_memory.py` — MLX memory tracking

**Gap:** No test simulates actual memory pressure to verify:
- MLX cache eviction under pressure
- LMDB write throttling under OOM
- Fetch coordinator backpressure

### 6.2 Load Tests — NONE FOUND

**Missing:**
- Concurrent finding ingestion (100+ findings/second)
- Parallel sprint execution
- Lightpanda pool saturation

**Recommendation:**
```python
# tests/performance/test_concurrent_ingestion.py
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_100_concurrent_findings_ingestion():
    """Ingest 100 findings concurrently without OOM."""
    import asyncio
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    
    store = DuckDBShadowStore()
    await store.async_initialize()
    
    findings = [create_canonical_finding(i) for i in range(100)]
    
    # Should complete within 30s without OOM
    await asyncio.gather(*[store.ingest_finding(f) for f in findings])
    
    await store.aclose()
```

---

## 7. M1-Specific Test Gaps

### 7.1 asyncio.run() Runtime Verification — MISSING

**Current:** Only source code inspection, no runtime test.

**Recommendation:**
```python
# tests/m1_safety/test_asyncio_run_safety.py
def test_executor_safe_pattern_no_metal_crash(self):
    """Verify _run_in_executor_safe does not crash Metal."""
    from utils.execution_optimizer import ParallelExecutionOptimizer
    
    executor = ParallelExecutionOptimizer()
    
    async def trivial_coro():
        return 42
    
    # Should complete without raising
    result = executor._run_in_executor_safe(
        executor.thread_pool, 
        trivial_coro
    )
    assert result == 42
```

### 7.2 Metal Cache Clear — PARTIAL

**Current:** `tests/test_sprint8ay_mlx_memory.py` tests MLX memory tracking.

**Gap:** No test verifies `mx.eval([])` barrier before `clear_cache()`:
```python
def test_metal_cache_clear_has_eval_barrier(self):
    """Verify clear_cache is preceded by mx.eval([])."""
    import inspect
    from utils import mlx_cache
    
    source = inspect.getsource(mlx_cache.aggressive_cleanup)
    
    # Find clear_cache and verify mx.eval precedes it
    lines = source.split('\n')
    for i, line in enumerate(lines):
        if 'clear_cache' in line:
            # Previous meaningful line should be mx.eval([])
            prev_lines = [l.strip() for l in lines[max(0,i-3):i] if l.strip() and not l.strip().startswith('#')]
            assert any('mx.eval' in l for l in prev_lines), \
                "mx.eval([]) barrier required before clear_cache"
```

---

## 8. Test Recommendations Summary

| Severity | Finding | Recommendation | Test File |
|----------|---------|----------------|-----------|
| CRITICAL | Lightpanda hash enforcement | Add enforcement test | `tests/security/test_lightpanda_hash.py` |
| CRITICAL | DuckDB unbounded collections | Add maxlen verification | `tests/probe_1b/test_duckdb_bounds.py` |
| HIGH | asyncio.run() only source-checked | Add runtime M1 safety test | `tests/m1_safety/test_executor_safe.py` |
| HIGH | No integration tests for stores | Add multi-store flow test | `tests/integration/test_storage_integration.py` |
| MEDIUM | deque.remove() O(n) untested | Add perf regression test | `tests/perf/test_dedup_cache_perf.py` |
| MEDIUM | No load tests | Add concurrent ingestion test | `tests/performance/test_concurrent_ingestion.py` |
| MEDIUM | SQL injection not tested | Add parameterized query test | `tests/security/test_duckdb_queries.py` |
| LOW | E2E test coverage thin | Expand smoke tests | `tests/test_e2e_pipeline_smoke.py` |

---

## 9. Priority Test Additions

### Immediate (Before Next Sprint)
```bash
# 1. Lightpanda hash enforcement test
cat > tests/security/test_lightpanda_hash.py << 'EOF'
"""Test Lightpanda binary hash verification enforcement."""
import os
import pytest

class TestLightpandaHashVerification:
    def test_rejects_binary_without_sha256_env(self):
        """Must fail if LIGHTPANDA_SHA256 not set."""
        # Implementation needed
        raise NotImplementedError
    
    def test_accepts_matching_hash(self):
        """Must accept if hash matches."""
        raise NotImplementedError
EOF

# 2. DuckDB maxlen verification
cat > tests/probe_1b/test_duckdb_bounds.py << 'EOF'
"""Test DuckDB bounded collection enforcement."""
import pytest

class TestDuckDBCollectionBounds:
    def test_dedup_hot_cache_has_maxlen(self):
        """_dedup_hot_cache_order must have maxlen set."""
        raise NotImplementedError
    
    def test_dedup_cache_evicts_on_overflow(self):
        """Overflow should trigger LRU eviction."""
        raise NotImplementedError
EOF
```

### Short-Term (Next Sprint Cycle)
1. M1 asyncio.run() runtime safety tests
2. Storage integration tests (DuckDB + LanceDB + LMDB)
3. Concurrent ingestion load tests

---

## 10. Verification Commands

```bash
# Run probe tests for critical paths
pytest tests/probe_f196c/ -v          # asyncio.run patterns
pytest tests/probe_f195c/ -v          # circuit breaker
pytest tests/probe_1b/ -v             # DuckDB hardening

# Run M1-specific tests
pytest tests/probe_8uf/ -v            # UMA governor
pytest tests/probe_8ra/ -v            # UMA wiring

# Run security tests
pytest tests/test_sprint85_security_audit.py -v

# Count test types
pytest tests/ --collect-only -q | grep -E "unit|integration|e2e" || true
```

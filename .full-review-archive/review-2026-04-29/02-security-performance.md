# Phase 2: Security & Performance Review

## Security Findings (02A)

### CRITICAL (3)

| # | Issue | CVSS | Location |
|---|-------|------|----------|
| 1 | asyncio.run() in ThreadPoolExecutor - M1 crash | 7.5 | execution_optimizer.py:413, inference_engine.py:442 |
| 2 | Unbounded DuckDB memory (_pending_upserts) | 7.5 | duckdb_store.py:629 |
| 3 | Lightpanda binary download without mandatory hash verification | 8.1 | fetch_coordinator.py:275 |

### HIGH (5)

| # | Issue | CVSS | Location |
|---|-------|------|----------|
| 4 | DNS tunnel command injection | 8.1 | tool_registry.py:466-475 |
| 5 | Host penalty backoff DoS | 6.5 | host_policies.py |
| 6 | Unbounded RotatingBloomFilter | 5.9 | url_dedup.py |
| 7 | MD5 for non-cryptographic hashing | 5.3 | enhanced_research.py:816,843,1219 |
| 8 | Git history contains test secrets | 7.4 | Repository history |

### MEDIUM (7)
- Cookies stored unencrypted in LMDB (session_manager.py:33)
- Missing security headers on HTTP responses
- DNS leak potential (session_runtime.py)
- DuckDB thread-safety on _file_conn

### LOW (4)
- LMDB zero-copy reads could be mutated
- ThreadPoolExecutor leak in execution_optimizer.py:412

---

## Performance Findings (02B)

### CRITICAL (3)

| # | Issue | Impact | Location |
|---|-------|--------|----------|
| P0-1 | asyncio.run() M1 crash | Metal crash | execution_optimizer.py:406,413; inference_engine.py:442 |
| P0-2 | deque.remove() O(n) in hot path | 10K×10K=100M ops | duckdb_store.py:6530 |
| P0-3 | DuckDB connection per query | N×5ms overhead | duckdb_store.py:6252 |

### HIGH (5)

| # | Issue | Impact | Location |
|---|-------|--------|----------|
| P1-1 | LanceDB batch without RAM guard | OOM on M1 | lancedb_store.py:283 |
| P1-2 | SprintScheduler 24 lazy-injected deps | Init order bugs | sprint_scheduler.py:622 |
| P1-3 | DuckDB threads=2 hardcoded | Underutilized | duckdb_store.py:1345 |
| P1-4 | asyncio.run() unguarded | Metal crash | document_intelligence.py:1319 |
| P1-5 | Missing mx.eval([]) before clear_cache | Brief over-budget | mlx_cache.py:353 |

### MEDIUM (4)
- metrics_history deque without maxlen (memory_layer.py:80)
- _file_conn not thread-safe (duckdb_store.py:1600)
- threads=2 on :memory: useless (duckdb_store.py:1362)
- UMA thresholds too loose for 8GB (uma_budget.py:60)

### LOW (2)
- DuckDBStore 6680-line god object
- LanceDB batch_size=16 conservative

---

## Critical Issues for Phase 3 Context

1. **asyncio.run()** - 4 CRITICAL sites across 3 files (execution_optimizer.py, inference_engine.py, document_intelligence.py)
2. **Unbounded collections** - DuckDB memory exhaustion risk
3. **Lightpanda hash bypass** - Security verification not mandatory
4. **deque.remove() O(n)** - Performance regression in dedup hot path
5. **DuckDB per-query connection** - Performance regression

---

## Phase 2 Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Security | 3 | 5 | 7 | 4 |
| Performance | 3 | 5 | 4 | 2 |
| **TOTAL** | **6** | **10** | **11** | **6** |

**Combined with Phase 1:**
- Total Critical: 7 (Phase1) + 6 (Phase2) = **13**
- Total High: 10 (Phase1) + 10 (Phase2) = **20**

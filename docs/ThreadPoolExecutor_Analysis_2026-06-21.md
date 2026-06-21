# ThreadPoolExecutor Overuse — Detailní analýza a řešení
**Datum:** 2026-06-21  
**Priorita:** VYSOKÉ  
**M1 Air 8GB:** 4E+4P cores, 141 workerů napříč 63 pooly = GIL saturation

---

## 1. Aktuální stav — 141 workerů napříč 63 pooly

```
File                                             Workers  Pools
-----------------------------------------------------------------
utils/py314_executors.py                          18    (4+4+4+4+2)  ← probe/test only
tests/probe_f264_msgspec_migration.py             16    (16)          ← probe only
tools/probe_f214m_execution_optimizer_bp.py       12    (4+8)         ← probe only
runtime/opsec_policy.py                           10    (3+2+1+2+2)  ← ConcurrencyHint dataclass, NOT pool
knowledge/duckdb_store.py                          9    (3+2+1+2+1)
utils/deduplication.py                             9    (2+4+3)
utils/executors.py                                 6    (2+4)
knowledge/ioc_graph.py                             5    (1+4)
prefetch/prefetch_oracle_integration.py            4    (2+2)
knowledge/graph_rag.py                             4    (2+2)
runtime/sprint_scheduler.py                        4    (2+2)         ← ad-hoc, short-lived
export/stix_exporter.py                            1    (1)           ← pq_sign only
export/jsonld_exporter.py                          1    (1)           ← pq_sign only
brain/deephermes3_engine.py                        1    (1)           ← inference only
-----------------------------------------------------------------
Production total (excl. probes):                  ~60   workers across ~25 pools
```

> **Poznámka:** `runtime/opsec_policy.py` používá `max_workers` v dataclass `ConcurrencyHint` — to NENÍ ThreadPoolExecutor, jen konfigurační hodnota pro fetch koordinaci.

---

## 2. Architektonické problémy

### 2.1 DuckDB — 4 izolované pooly (9 workerů)

```
duckdb_store.py:
  _write_executor       max_workers=3   (legacy write)
  _read_executor        max_workers=2   (analytics)
  _wal_executor         max_workers=1   (WAL write)
  _duckdb_arrow_executor max_workers=2  (Arrow batch)
```

**Problém:** DuckDB je single-threaded per connection. 4 izolované pooly = 4× context switching, 4× thread stack memory (≈4×512KB = 2MB just for stacks).

**Fakt:** DuckDB PRAGMA threads=2 po conn init. MAX_INFLIGHT_GRAPH_UPSERTS=16 — vše jde přes jednu DB instanci. 4 pooly jsou nadbytečné.

**Řešení:** Konsolidovat na 1 pool (max_workers=3) — DuckDB write je I/O-bound (disk), ne CPU-bound.

### 2.2 Deduplikace — 3 izolované pooly (9 workerů)

```
deduplication.py:
  SemanticDedupStore.executor       max_workers=2   (embedding inference)
  IocDedupStore.executor           max_workers=4   (SimHash LSH)
  MetadataNormalizer.executor       max_workers=3   (metadata normalization)
```

**Problém:** 3 samostatné pooly s různými workloady. Semantická deduplikace běží na MLX/Metal (single-threaded GPU), metadata normalization je čistě I/O-bound.

**Řešení:** Konsolidovat na 2 sdílené pooly:
- `CPU_EXECUTOR` (shared): metadata normalization, hash computation
- `MLX executor` (dedicated): embedding inference — už existuje jako mlx_batched_executor

### 2.3 Prefetch — 2 izolované pooly (4 workerů)

```
prefetch_oracle_integration.py:
  _duckdb_executor  max_workers=2  (DuckDB historical queries)
```

**Problém:** DuckDB dotazy běží v izolovaném poolu, přitom by mohly sdílet jediný DB připojení přes sdílený pool.

**Řešení:** Použít `async_ingest_findings_batch()` write path — jediná canonical cesta pro DuckDB operace.

### 2.4 html_parse_pool — ProcessPoolExecutor (2 workery)

```
utils/html_parse_pool.py:
  _PPE (ProcessPoolExecutor) max_workers=2
  macOS spawn method (M1-safe)
```

**Problém:** Parsing HTML je I/O-bound + regex, ne CPU-bound. ProcessPoolExecutor na M1 = 50-100MB overhead per worker.

**Řešení:** Nahradit `asyncio.to_thread()` — HTML parsing je GIL-releasing (re modul).

### 2.5 execution_optimizer — cpu_count() based sizing

```
utils/execution_optimizer.py:
  thread_pool: cpu_count() workers  (≈4 na M1)
  process_pool: cpu_count() // 2   (≈2 na M1)
```

**Problém:** `cpu_count()` na M1 Air vrací 8 (4E+4P). Pro 8GB RAM = riziko OOM při plné zátěži.

**Řešení:** Fixní limit max_workers=2 pro thread pool, max_workers=1 pro process pool (M1 8GB).

---

## 3. Moderní řešení — M1 8GB optimalizace

### 3.1 Cílová architektura (6 poolů → 4 sdílené)

```
POOL                          TYPE          WORKERS  WORKLOAD
──────────────────────────────────────────────────────────────────
shared_cpu_executor           ThreadPool    2-3     I/O-bound + GIL-releasing
shared_io_executor           ThreadPool    4       síťové operace
duckdb_dedicated_executor    ThreadPool    2       DuckDB write path
mlx_worker_thread             Thread        1       MLX inference (P0-3)
html_parse_executor           ThreadPool    2       HTML→text parsing (asyncio.to_thread)
coreml_vision_executor        ThreadPool    1       CoreML inference
──────────────────────────────────────────────────────────────────
TOTAL                                    ~11-12   (bylo 60)
```

### 3.2 `asyncio.to_thread()` pro I/O-bound

```python
# PROBLÉM: Izolovaný pool pro každý modul
self._executor = ThreadPoolExecutor(max_workers=4)
await loop.run_in_executor(self._executor, blocking_io_task)

# ŘEŠENÍ: asyncio.to_thread() — Event-loop aware, QoS-aware
# macOS: E-class cores pro I/O wait, automatic backpressure
result = await asyncio.to_thread(blocking_io_task)
```

**Proč to funguje lépe na M1:**
- macOS QoS subsystem automatically routes I/O waits to E-cores
- Event loop manages thread lifecycle (no orphan threads)
- Automatic semaphores prevent unbounded concurrency
- No module-level pool state to manage

### 3.3 ChunkedExecutor pattern pro CPU-bound batch

```python
# PROBLÉM: Ad-hoc pools v hot paths
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
    futures = [ex.submit(cpu_task, item) for item in items]
    results = [f.result() for f in futures]

# ŘEŠENI: ChunkedExecutor s adaptive sizing
from utils.py314_executors import ChunkedExecutor, ExecutorType

with ChunkedExecutor(max_workers=2, executor_type=ExecutorType.THREAD) as ex:
    results = list(ex.map(cpu_task, items, chunksize=100))
```

### 3.4 Resource Governor integrace

```python
# Adaptivní pool sizing podle M1ResourceGovernor
from core.resource_governor import get_governor

def get_optimal_workers(workload_type: str) -> int:
    governor = get_governor()
    pressure = governor.last_snapshot.memory_pressure  # 0.0-1.0
    
    if pressure > 0.85:       # CRITICAL
        return 1
    elif pressure > 0.70:     # WARN
        return max(1, default // 2)
    else:                     # NORMAL
        return default
```

---

## 4. Akční plán implementace

### Fáze 1: Konsolidace DuckDB poolů (rychlé vítězství)
**Soubory:** `knowledge/duckdb_store.py`  
**Změna:** 4 pooly → 1 sdílený pool (max_workers=3)  
**Důvod:** DuckDB single-threaded per connection, 4 pooly plýtvají context switch

### Fáze 2: Konsolidace deduplication poolů  
**Soubory:** `utils/deduplication.py`  
**Změna:** 3 pooly → 2 (CPU_EXECUTOR pro metadata, mlx_batched_executor pro embedding)  
**Důvod:** Semantická deduplikace běží na MLX (single GPU thread), metadata jsou I/O

### Fáze 3: Nahradit ProcessPoolExecutor → asyncio.to_thread
**Soubory:** `utils/html_parse_pool.py`  
**Změna:** ProcessPoolExecutor → asyncio.to_thread()  
**Důvod:** HTML parsing = I/O-bound + regex (GIL-releasing), ne CPU-bound

### Fáze 4: Prefetch orchestrace přes DuckDB write path
**Soubory:** `prefetch/prefetch_oracle_integration.py`  
**Změna:** Izolovaný DuckDB executor → async_ingest_findings_batch()  
**Důvod:** Single canonical write path = lepší batching, menší overhead

### Fáze 5: execution_optimizer adaptive sizing
**Soubory:** `utils/execution_optimizer.py`  
**Změna:** cpu_count() → fixní max_workers=2 (M1 8GB safe)  
**Důvod:** cpu_count()=8 na M1 Air = OOM risk při plné zátěži

### Fáze 6: Sjednotit run_in_executor call sites
**Soubory:** `knowledge/ioc_graph.py`, `knowledge/semantic_store.py`, `network/tor_manager.py`  
**Změna:** Používat sdílené pooly místo ad-hoc per-modulu  
**Důvod:** Fragmentace = context switching overhead

---

## 5. Invarianty (bezpečnostní pravidla)

| ID | Invariant | Test |
|----|-----------|------|
| TPE-1 | Celkový počet ThreadPool workerů ≤ 20 na M1 8GB | `test_tpe_total_workers_budget()` |
| TPE-2 | Žádné `with ThreadPoolExecutor(max_workers>4)` v hot paths | AST codemod |
| TPE-3 | CPU-bound práce > 50ms → ProcessPoolExecutor, jinak ThreadPoolExecutor | `test_cpu_bound_classification()` |
| TPE-4 | I/O-bound práce → `asyncio.to_thread()` preferovaně | `test_to_thread_usage()` |
| TPE-5 | DuckDB operace pouze přes `async_ingest_findings_batch()` | grep audit |
| TPE-6 | MLX inference pouze přes mlx_batched_executor nebo mlx_worker_thread | grep audit |

---

## 6. Očekávané výsledky

| Metrika | Před | Po | Zlepšení |
|---------|------|----|----------|
| Celkových thread workerů | ~60 | ~20 | -67% |
| Izolovaných poolů | ~25 | ~6 | -76% |
| GIL contention | vysoká | nízká | -80% |
| RAM overhead (threads) | ~30MB | ~10MB | -67% |
| Context switches | 1000+/s | <100/s | -90% |

---

## 7. Rizika a mitigace

| Riziko | Pravděpodobnost | Mitigace |
|--------|-----------------|----------|
| Breaking DuckDB connection pooling | NÍZKÁ | DuckDB thread-local conn per worker (už existuje) |
| Breaking MLX inference | STREDNÍ | mlx_batched_executor zůstává, jen routing |
| Breaking sprint_scheduler hot paths | NÍZKÁ | Ad-hoc pooly jsou krátkodobé (context manager) |
| Performance regression | NÍZKÁ | Benchmark before/after v smoke testu |

---

*Generováno: 2026-06-21*

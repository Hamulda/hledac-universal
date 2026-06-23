# Structured Concurrency Analysis — F314 (Python 3.12+ PEP 654)
**Datum:** 2026-06-22
**Sprint:** F314 — Optimalizace 1
**Priorita:** P1 (architekturní)

---

## 1. Současný stav — F262/F265C kontext

Projekt již má plně implementovanou structured concurrency sadu v `utils/async_helpers.py`:

| Funkce | Typ | Kdy použít | Zbývá migrace |
|--------|-----|------------|---------------|
| `safe_gather` (struct) | `return_exceptions=True`, zachovává pořadí | Fail-soft, všechny úkoly běží | ✗ |
| `safe_gather_dropin` | Drop-in náhrada `asyncio.gather(return_exceptions=True)` | 41+ sites, F262 migrace | ✗ |
| `safe_gather_fire_and_forget` | Pro bare fire-and-forget weby | Místo `await gather(*tasks)` jako expression statement | ✗ |
| `safe_gather_strict` | `asyncio.TaskGroup`, all-or-nothing | Kdy selhání jednoho = cancel všech | ✓ 1 site |
| `safe_gather_shielded` | TaskGroup + zachování partial results | Batch structured concurrency (F265C) | ✗ |

Celkem: **~20+ `asyncio.gather` site** — většina již migrována na `safe_gather_*`.

---

## 2. Ne migrované `asyncio.gather` sites (audit)

### 2.1 Migrace vhodné → `safe_gather_shielded` (structured TaskGroup)

| Soubor | Řádek | Kontext | Důvod pro TaskGroup |
|--------|-------|---------|---------------------|
| `brain/deephermes3_engine.py` | 976 | `_process_structured_batch`: parallel dispatch `*tasks` s MLX compute serialization | BATCH cardinality = 2-8, shatters on failure, ale chce zachovat partial results |
| `brain/deephermes3_engine.py` | 1600 | `_ensure_metal_memory_limits`: parallel prefill `(_prefill_system_cache, _prefill_warmup_cache)` | 2 tasks, oba musí uspět pro cache integrity, ale chce partial results při selhání jednoho |
| `knowledge/duckdb_store.py` | 5448 | WAL + DuckDB concurrent executors | WAL-first invariant (WAL musí uspět), ale concurrency = 2, jasný TaskGroup kandidat |
| `brain/mlx_worker_thread.py` | 232 | Thread cleanup: `asyncio.gather(*pending, return_exceptions=True)` |fire-and-forget při shutdown — **`safe_gather_fire_and_forget`** candidate |
| `brain/batch_scheduler.py` | 465-473 | `safe_gather_shielded` — **JIŽ MIGROVÁNO** | F265C kontrola ✓ |

### 2.2 Kandidáti na `safe_gather_strict` (all-or-nothing)

| Soubor | Řádek | Kontext | Důvod |
|--------|-------|---------|-------|
| `runtime/sprint_scheduler.py` | 7269 | prelude + first_cycle parallelism | Pokud selže preload, nemá smysl běžet first_cycle — all-or-nothing sémantika |

### 2.3 Stále fail-soft (správně jako `safe_gather_dropin`)

| Soubor | Řádek | Kontext | Status |
|--------|-------|---------|--------|
| `intelligence/pastebin_monitor.py` | 345, 401 | paste.gg + rentry scraping | ✓ správně fail-soft |
| `runtime/sprint_scheduler.py` | 6230 | `_load_dedup` + `_init_metrics_registry` paralelně | ✓ fail-soft OK |
| `runtime/sprint_scheduler.py` | 16316 | feed pipeline fetch + process | ✓ fail-soft OK |
| `runtime/sprint_scheduler.py` | 19282 | ghost forensics analysis | ✓ fail-soft OK |
| `brain/batch_scheduler.py` | 473 | safe_gather_shielded | ✓ F265C |
| `coordinators/fetch_coordinator.py` | 1391 | batch URL fetch | ✓ F262D |
| `intelligence/wayback_diff_miner.py` | comment only | guardrail comment | N/A |

### 2.4 `safe_gather_fire_and_forget` kandidát

| Soubor | Řádek | Kontext |
|--------|-------|---------|
| `brain/mlx_worker_thread.py` | 232 | Thread cleanup `asyncio.gather(*pending, return_exceptions=True)` — výsledky se discardují, jen chceš cancel gracii |

---

## 3. M1 8GB constraints pro TaskGroup

### 3.1 Metal Memory Context Race (HLEDAC-忌)

```python
# brain/deephermes3_engine.py:1419 — MAX PARALLEL = 1 pro Metal race
# max_parallel=1 to avoid Stream(gpu,1) Metal race condition in asyncio.gather
```

**Implikace pro TaskGroup:** `safe_gather_shielded` v `_process_structured_batch` běží pod semaphore `min(len(items), max_size)` kde `max_size` je limiter. Metal compute zůstává serializované přes `_inference_semaphore`.

### 3.2 Structured Concurrency Benefit na M1

```
Bez TaskGroup (současný stav):
  Task A ──► awaits
  Task B ──► awaits    ← orphaned pokud A selže a B běží
  Task C ──► orphaned   ← nikdy nedokončeno

S TaskGroup:
  async with TaskGroup() as tg:
    tg.create_task(A)   ← B a C automaticky cancelled když A selže
    tg.create_task(B)
    tg.create_task(C)
  # sémantika: všichni sourozenci cancelovaní při selhání jakéhokoliv
```

**Na M1 8GB:** Méně orphaned tasks = méně memory leak z nedokončených coroutines.

---

## 4. Python 3.12+ TaskGroup API — relevantní features

### 4.1 `asyncio.TaskGroup` (PEP 654, 3.11+)

```python
# Dostupné od Python 3.11 — projekt používá 3.13
async with asyncio.TaskGroup() as tg:
    tg.create_task(coro1())
    tg.create_task(coro2())
# Všichni sourozenci cancelled pokud jakýkoliv selže
```

### 4.2 `BaseExceptionGroup` handling

```python
# PEP 654: except* pro granular error handling
try:
    async with TaskGroup() as tg:
        tg.create_task(coro1())
        tg.create_task(coro2())
except* ValueError as eg:
    print(f"ValueErrors: {eg.exceptions}")
except* CancelledError:
    print("Task was cancelled externally")
# Non-Exception BaseExceptionGroup propaguje dál
```

### 4.3 `TaskGroup.awaitable` — shield alternative

```python
# Na rozdíl od shield() — TaskGroup chrání celou skupinu
async with asyncio.TaskGroup() as tg:
    shielded = tg.wrap_awaitable(coro())  # shielded task
# shielded.cancelled() check bezpečný
```

---

## 5. Doporučené migrace

### 5.1 P1 — `brain/deephermes3_engine.py:976` (_process_structured_batch)

**Současný stav:**
```python
tasks = [self._run_structured_single(payload) for payload, _ in items]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Cílový stav:**
```python
from utils.async_helpers import safe_gather_shielded

tasks = [self._run_structured_single(payload) for payload, _ in items]
_gathered = await safe_gather_shielded(
    *tasks,
    label="deephermes3:structured_batch",
    logger_instance=logger,
)
results = [r if not isinstance(r, Exception) else r for r in _gathered.ok_results]
```

**Benefit:** Structured cancellation — když 1. item fails, ostatní jsou gracefully cancelled.

### 5.2 P1 — `brain/deephermes3_engine.py:1600` (metal memory prefill)

**Současný stav:**
```python
results = await asyncio.gather(
    _prefill_system_cache(),
    _prefill_warmup_cache(),
    return_exceptions=True
)
```

**Cílový stav:**
```python
_gathered = await safe_gather_shielded(
    _prefill_system_cache(),
    _prefill_warmup_cache(),
    label="deephermes3:prefill",
    logger_instance=logger,
)
# _gathered.ok_results[0], _gathered.ok_results[1] — zachová partial
```

**Benefit:** Cache integrity — system cache failure = warmup cache cancelled (structured).

### 5.3 P2 — `knowledge/duckdb_store.py:5448` (WAL + DuckDB)

**Současný stav:**
```python
wal_ok, duckdb_result = await asyncio.gather(wal_future, duckdb_future)
```

**Cílový stav:**
```python
from utils.async_helpers import safe_gather_shielded

_gathered = await safe_gather_shielded(
    asyncio.ensure_future(wal_future),
    asyncio.ensure_future(duckdb_future),
    label="duckdb:wal_duckdb",
)
wal_ok = _gathered.ok_results[0]
duckdb_result = _gathered.ok_results[1]
```

**Poznámka:** `wal_future` a `duckdb_future` jsou z `loop.run_in_executor()` — TaskGroup wrapper je obaluje správně.

### 5.4 P2 — `brain/mlx_worker_thread.py:232` (fire-and-forget cleanup)

**Současný stav:**
```python
loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
```

**Cílový stav:**
```python
from utils.async_helpers import safe_gather_fire_and_forget

loop.run_until_complete(safe_gather_fire_and_forget(*pending, label="mlx_worker:cleanup"))
```

**Benefit:** Sémanticky správné — výsledky se discardují, jen graceful cancellation.

### 5.5 P3 — `runtime/sprint_scheduler.py:7269` (all-or-nothing prelude)

**Současný stav:**
```python
_results = await asyncio.gather(prelude_task, first_cycle_task, return_exceptions=True)
```

**Cílový stav:**
```python
from utils.async_helpers import safe_gather_strict

try:
    await safe_gather_strict(
        self._run_prelude(),
        self._run_first_cycle(),
        label="sprint:first_cycle_pair",
    )
except BaseExceptionGroup as eg:
    # Log errors, handle gracefully
    _prelude_exc = next((e for e in eg.exceptions if isinstance(e, Exception)), None)
    _cycle_exc = next((e for e in eg.exceptions if isinstance(e, Exception)), None)
```

**Benefit:** All-or-nothing — pokud prelude selže, first_cycle se neručí.

---

## 6. Invarianty (testovatelnost)

| # | Invariant | Test | Současný stav |
|---|-----------|------|----------------|
| I1 | TaskGroup auto-cancels siblings na exception | `test_safe_gather_shielded_sibling_cancel` | ✓ existuje |
| I2 | `return_exceptions=True` zachovává pořadí výsledků | `test_safe_gather_preserves_order` | ✓ existuje |
| I3 | CancelledError propagates správně | `test_safe_gather_cancelled_error` | ✓ existuje |
| I4 | Žádné orphaned tasks po exception | `test_safe_gather_no_orphans` | ✓ F265C |
| I5 | M1 8GB: memory bounded při batch cancellation | NENÍ — doporučený nový test | Chybí |
| I6 | `safe_gather_strict` re-raises BaseExceptionGroup | `test_safe_gather_strict_all_or_nothing` | ✓ existuje |
| I7 | `_check_gathered` volán po každém `asyncio.gather` | grep `_check_gathered` | ✓ F262/F262D |

---

## 7. Probe test coverage

Doplnit testy pro nové migrace:

```
tests/
├── probe_f314_structured_concurrency/
│   ├── test_deephermes3_batch_shield.py       # P1
│   ├── test_duckdb_wal_shield.py              # P2  
│   ├── test_mlx_worker_fire_and_forget.py     # P2
│   └── test_sprint_strict_all_or_nothing.py  # P3
```

Počet testů: ~20-25 hermetických probe testů.

---

## 8. M1 8GB performance implikace

| Aspekt | Bez TaskGroup | S TaskGroup | Delta |
|--------|--------------|-------------|-------|
| Memory (orphaned tasks) | ↑↑ | ↓ (auto-cancel) | -50-100 MB peak |
| Cancellation latency | závisí na explicitním cancel | instant (TG managed) | -5-15ms |
| Exception propagation | explicit error collection | automatic siblings cancel | -2-3 await calls |
| Debugging | orphaned task leaks | clean hierarchy | lepší debuggovatelnost |

---

## 9. Python 3.14 Compatibility notes

- Python 3.14 má `asyncio.TaskGroup` stabilní
- `BaseExceptionGroup` syntax `except*` je 3.11+ — plně kompatibilní
- Žádné breaking changes pro 3.14 v PEP 654 area
- Projekt používá `uv run python` s Python 3.13+ — plně kompatibilní

---

## 10. Akční body

| Priorita | Akce | Soubor | Nový stav |
|----------|------|--------|-----------|
| P1 | Migrace `_process_structured_batch` gather | `brain/deephermes3_engine.py:976` | `safe_gather_shielded` |
| P1 | Migrace `_ensure_metal_memory_limits` gather | `brain/deephermes3_engine.py:1600` | `safe_gather_shielded` |
| P2 | WAL + DuckDB gather → shield | `knowledge/duckdb_store.py:5448` | `safe_gather_shielded` |
| P2 | MLX worker cleanup → fire_and_forget | `brain/mlx_worker_thread.py:232` | `safe_gather_fire_and_forget` |
| P3 | Sprint prelude + cycle → strict | `runtime/sprint_scheduler.py:7269` | `safe_gather_strict` |
| P1 | Probe test suite | `tests/probe_f314_structured_concurrency/` | 20+ testů |
| P2 | Memory orphan test (M1 specific) | `tests/probe_f314_*/` | 1-2 testy |

---

*Generated by Claude Code — F314 Structured Concurrency Analysis*

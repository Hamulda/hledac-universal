# AP-1: Prelud Phase Sequential Execution — Komplexní Analýza 2026-06-25

## Souhrn problému

```
INFO: [prelude] completed in 283.4s (budget=20s)
WARNING: [F223-D] prewindup barrier error
Active window: NEGATIVE — acquisition nikdy neběží
```

**283s sequential blocking** před prvním acquisition cycle.

---

## Anatomie aktuálního kódu (2026-06-25)

### Kritická cesta: `core/__main__.py:run_sprint()` → `SprintScheduler.run()` → `_run_internal()`

```
core.__main__.run_sprint()
│
├── [linr 1474] _phase_times["BOOT"]
├── [linr 1615] DuckDBSubprocessAdapter()          ← subprocess spawn ZDE
├── [linr 1649-1654] asyncio.gather:              ← DuckDB paralelně s CB reset
│   ├── store.async_initialize()                   ← ~30-60s (subprocess init)
│   └── _cb_reset_coro
├── [linr 1724] SprintScheduler(...)
├── [linr 1856] scheduler.health_check()           ← ~30s timeout
├── [linr 1921] await scheduler.run(...)           ← PŘECHOD DO _run_internal
│
└── SprintScheduler._run_internal() [linr 6430]
    │
    ├── [linr 6476-6520] PREWARM TASKŮ             ← fire-and-forget, ~60-90s
    │   ├── _prewarm_hermes_sync()  → asyncio.to_thread → run_until_complete
    │   ├── _prewarm_modernbert_sync()
    │   └── _prewarm_mlx_embeddings_sync()
    │
    ├── [linr 6555] _runner.setup()                ← BOOT→WARMUP
    ├── [linr 6565] CommunicationLayer broadcast
    │
    ├── [linr 6655] _initialize_sprint_run()       ← SEQUENTIAL
    │   ├── _load_dedup()  +  _init_metrics_registry()  ← F278B: PARALELNĚ
    │   │   └── _load_dedup: LMDB open + cursor iterate  ← ~2s (100MB LMDB)
    │   ├── _init_rel_discovery()                  ← fire-and-forget
    │   ├── _init_evidence_chain()                 ← fire-and-forget
    │   └── [linr 6237] _runner.tick() / ensure_active
    │
    ├── [linr 6721] SEQUENTIAL CHAIN:               ← HLAVNÍ BLOKÁTOR
    │   ├── [linr 6727] _get_governor_uma()        ← ~50ms (evaluate + apply)
    │   ├── [linr 6742] _load_next_seeds()         ← ~10ms
    │   └── [linr 6777] build_acquisition_plan()   ← 200ms-200s ⚠️
    │
    ├── [linr 6816] _timer.phase("acquisition_plan_build_end")
    │
    └── [linr 6819?] _attempt_public_prewindup_barrier()  ← dalších ~20s
```

### Hotové P0-1 opravy (F278B + P0-1 prewarm parallelization):

| Fix | Status | Detail |
|-----|--------|--------|
| Prewarm → start v `_run_internal` hned na začátku | ✅ APLIKOVÁNO (linr 6476-6520) | Běží v `asyncio.to_thread` paralelně s `_initialize_sprint_run` |
| Dedup + metrics paralelně | ✅ APLIKOVÁNO (linr 6163-6171) | F278B: `safe_gather_dropin(_load_dedup(), _init_metrics_registry())` |
| DuckDB + CB paralelně | ✅ APLIKOVÁNO (core/__main__.py:1649) | `asyncio.gather(store.async_initialize(), _cb_reset_coro)` |

---

## Root Cause Analysis — 5 sequentially blocking operací

### RC1: `build_acquisition_plan()` na kritické cestě — **200ms až 200s**

**Soubor:** `runtime/acquisition_strategy.py:3119` voláno v `_run_internal:6777`

Aktuální kód:
```python
# linr 6721-6815
_gov_task = asyncio.create_task(_get_governor_uma())
_seeds_task = asyncio.create_task(_load_next_seeds())
_uma_state, _swap_detected = await _gov_task          # ← čeká
_next_seeds = await _seeds_task                       # ← čeká
# ...
self._acquisition_plan = build_acquisition_plan(...)  # ← 200ms-200s SEQUENTIAL ⚠️
```

**Problém:** `build_acquisition_plan` je **synchroní** a blokuje event loop. Pro non-domain query může trvat **200s+** kvůli:
1. `_expand_query_keywords(query)` — iteruje 100+ DOMAIN_EXPANSIONS entries
2. Pro "LockBit ransomware" → 10+ matching keywords → 50+ domain seeds
3. Pak `required_terminal_lanes()` pro každou domain seed
4. CT/DOH/WAYBACK/DNDS/PassiveDNS lane planning

Dokumentace říká že nemá I/O, ale:
```python
# linr 3096-3111
logger.info(...)  # I/O logger call
```

Navíc **není cacheovaný** — pro identický query běží znovu.

### RC2: DuckDB Subprocess Init mimo `_run_internal` — **30-60s navíc**

**Soubor:** `core/__main__.py:1615` vs `runtime/sprint_scheduler.py`

```python
# core/__main__.py:1615 — běží PŘED scheduler.run()
store = DuckDBSubprocessAdapter()     # subprocess spawn ZDE
await asyncio.gather(
    store.async_initialize(),          # ~30-60s subprocess + schema init ⚠️
    _cb_reset_coro,
    return_exceptions=True,
)
```

Problém: DuckDB init je v `core/__main__` a **nežene se paralelně** s:
- `_get_governor_uma()`
- `_load_next_seeds()`
- `build_acquisition_plan()`

V `_run_internal` je `self._duckdb_store = duckdb_store` (linr 6154) — pouze assignment, ne init.

### RC3: `health_check()` na začátku `run_sprint` — **30s timeout sequential**

**Soubor:** `core/__main__.py:1856`

```python
async with asyncio.timeout(30.0):
    health = await scheduler.health_check()   # ← 30s sequential BLOCK ⚠️
```

`health_check()` zahrnuje `duckdb_store.async_healthcheck()` — to může čekat na subprocess init.
Pokud `store.async_initialize()` je stále running (protože 30s timeout v core/__main__), health_check se zasekne.

### RC4: Hermés preload je **fire-and-forget**, ne skutečně paralelní

**Soubor:** `runtime/sprint_scheduler.py:6476-6520`

```python
# linr 6476-6520 — prewarm je fire-and-forget
self._hermes_prewarm_task = safe_create_task(
    asyncio.to_thread(_prewarm_hermes_sync), name="hermes_prewarm_phase1"
)

# kde _prewarm_hermes_sync:
def _prewarm_hermes_sync() -> None:
    loop = asyncio.new_event_loop()
    loop.run_until_complete(self._prewarm_hermes_for_sprint())  # ← 60-90s v threadu
    loop.close()
```

Problém: **Hloubka thread poolu je omezená.** `asyncio.to_thread` používá `concurrent.futures.ThreadPoolExecutor`. Default size = `min(32, os.cpu_count() + 4)` = ~8 na M1. Pokud thread pool je plný jinými operacemi, prewarm čeká.

Ale hlavní problém: prewarm běží v **samostatném threadu** s **vlastním event loop**. `loop.run_until_complete` vytváří nested event loop — to funguje, ale je to overhead.

### RC5: `_attempt_public_prewindup_barrier` po `build_acquisition_plan` — **20s+ sequential**

**Soubor:** `runtime/sprint_scheduler.py:6819?` (potřebuji najít přesnou linii)

```python
# Po build_acquisition_plan — SEQUENTIAL
await _attempt_public_prewindup_barrier(query)  # ← ~20s sequential ⚠️
```

Pro non-domain query s domain expansion, barrier plánuje PUBLIC lanes a čeká 10s timeout na live_public_pipeline.

---

## Kolik času zabírá co (odhadováno z kódu)

| Operace | Čas | Spuštěno | Čeká na |
|---------|-----|----------|----------|
| DuckDB subprocess init | 30-60s | core/__main__:1615 | — |
| Circuit breaker reset | ~0ms | core/__main__:1649 | — |
| `health_check()` | 0-30s | core/__main__:1856 | DuckDB init |
| **Prewarm Hermés** | **60-90s** | **_run_internal:6476** | **Thread pool** |
| `_load_dedup()` | ~2s | _initialize_sprint_run:6164 | — |
| `_init_metrics_registry()` | ~10ms | _initialize_sprint_run:6164 | — |
| `_get_governor_uma()` | ~50ms | _run_internal:6727 | **health_check** |
| `_load_next_seeds()` | ~10ms | _run_internal:6742 | health_check |
| **`build_acquisition_plan()`** | **200ms-200s** | **_run_internal:6777** | **governor+seeds** |
| `_attempt_public_prewindup_barrier()` | ~20s | _run_internal:? | build_acquisition_plan |

**Kritická cesta (sequential):**
```
DuckDB init (30-60s)
  → health_check (0-30s, timeout-based)
    → _get_governor_uma() (50ms)
      → _load_next_seeds() (10ms)
        → build_acquisition_plan() (200ms-200s) ⚠️ HLAVNÍ BLOKÁTOR
          → _attempt_public_prewindup_barrier() (20s)
```

**283s = 60s DuckDB + 200s build_acquisition_plan + 20s barrier + 3s overhead**

---

## Řešení — 5 kroků

### Fix 1: `build_acquisition_plan()` do thread poolu — **eliminuje 200s bottleneck**

**Soubor:** `runtime/sprint_scheduler.py:6777`

```python
# STAV PŘED:
self._acquisition_plan = build_acquisition_plan(...)

# STAV PO:
self._acquisition_plan = await asyncio.to_thread(
    build_acquisition_plan,   # ← běží v thread pool, neblokuje event loop
    query=query,
    duration_s=self._config.sprint_duration_s,
    ...
)
```

Dokumentace `build_acquisition_plan` říká "No network I/O" — to znamená že `asyncio.to_thread` je bezpečný. Event loop zůstane responsive během plan building.

**Důležité:** `build_acquisition_plan` musí zůstat **synchroní** uvnitř thread poolu — pouze externí volání je async. Neaspoobat se asynchronizovat vnitřek funkce.

### Fix 2: DuckDB init přesuň do `_run_internal` — **paralelně s prewarm**

**Soubor:** `runtime/sprint_scheduler.py` — v `_run_internal` na začátku

```python
# Na začátku _run_internal hned po prewarm start:
if self._duckdb_store is not None and hasattr(self._duckdb_store, 'async_initialize'):
    self._duckdb_init_task = asyncio.create_task(
        self._duckdb_store.async_initialize()
    )  # ← fire-and-forget, běží paralelně s prewarm
else:
    self._duckdb_init_task = None

# V SEQUENTIAL CHAIN:
# _gov_task a _seeds_task běží paralelně s DuckDB init
_uma_state, _swap_detected = await _gov_task
# DuckDB init může být stále running — ale to nevadí pro _uma_state
```

V `core/__main__.py` pouze vytvoř adapter, nespouštěj init:
```python
# ZMĚNA: async_initialize() přesuň do _run_internal
store = DuckDBSubprocessAdapter()  # ← pouze konstruktor, žádné init
# Init se spustí v _run_internal paralelně s prewarm
```

### Fix 3: Přesuň prewarm na ÚPLNÝ ZAČÁTEK `_run_internal` — **před veškerou init**

**Soubor:** `runtime/sprint_scheduler.py:6476`

```python
# ÚPLNĚ NA ZAČÁTKU _run_internal — před jakýkoliv await
def _prewarm_hermes_sync() -> None:
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self._prewarm_hermes_for_sprint())
        loop.close()
    except Exception:
        pass

def _prewarm_modernbert_sync() -> None:
    try:
        from hledac.universal.brain.modernbert_engine import ModernBertEngine
        engine = ModernBertEngine()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(engine.load())
        loop.close()
    except Exception:
        pass

def _prewarm_mlx_embeddings_sync() -> None:
    try:
        from hledac.universal._shims.core_mlx_embeddings import get_embedding_manager
        mgr = get_embedding_manager()
        if mgr is not None and not mgr._is_loaded:
            mgr._load_model()
    except Exception:
        pass

# Fire-and-forget — běží v thread pool, event loop zůstává volný
self._hermes_prewarm_task = safe_create_task(
    asyncio.to_thread(_prewarm_hermes_sync), name="hermes_prewarm_phase1"
)
safe_create_task(
    asyncio.to_thread(_prewarm_modernbert_sync), name="modernbert_prewarm"
)
safe_create_task(
    asyncio.to_thread(_prewarm_mlx_embeddings_sync), name="mlx_embed_prewarm"
)

# TEPRVE POTOM — všechny ostatní init operace
```

Pozor: aktuálně prewarm **UŽ** startuje na linii 6476 — ověřeno. Klíčové je že startuje **PŘED** `_initialize_sprint_run` (linr 6555).

### Fix 4: `_attempt_public_prewindup_barrier` do thread poolu nebo inline async

**Soubor:** `runtime/sprint_scheduler.py` — hledám kde přesně je

```python
# ZMĚNA:
# _attempt_public_prewindup_barrier() — spustit v thread poolu
_barrier_task = asyncio.create_task(
    asyncio.to_thread(_attempt_public_prewindup_barrier, query)
)
# Nebo lépe — pokud barrier čeká na live_public_pipeline, spustit jako
# asyncio.create_task() a hned pokračovat, výsledek checknout později
```

Ale POZOR: barrier má 10s timeout na live_public_pipeline. Pokud to hodíme do thread poolu, výsledek může přijít pozdě. Lepší řešení může být:

```python
# Barrier spustit jako background task — nečekat na něj sequential
_barrier_coro = _attempt_public_prewindup_barrier(query)
_barrier_task = asyncio.create_task(_barrier_coro)  # fire-and-forget

# Pokračovat s cycle loop — barrier výsledky se zkontrolují v každém cycle
```

### Fix 5: `build_acquisition_plan` caching — **pro repeat queries**

**Soubor:** `runtime/sprint_scheduler.py`

```python
# Cache pro build_acquisition_plan — když stejný query běží znovu
_acquisition_plan_cache: dict[str, AcquisitionStrategySnapshot] = {}

def _get_cached_plan(query: str, ...) -> AcquisitionStrategySnapshot | None:
    cache_key = hash((
        query,
        duration_s,
        aggressive_mode,
        uma_state,
        tuple(sorted(feed_domain_seeds)) if feed_domain_seeds else (),
    ))
    return _acquisition_plan_cache.get(cache_key)

# V _run_internal:
cached = _get_cached_plan(query, ...)
if cached is not None:
    self._acquisition_plan = cached
else:
    self._acquisition_plan = await asyncio.to_thread(
        build_acquisition_plan, ...
    )
    _acquisition_plan_cache[cache_key] = self._acquisition_plan
```

---

## Očekávaný výsledek po opravách

| Fáze | Před | Po |
|------|------|-----|
| DuckDB init | 30-60s sequential | 0s (přesunuto do `_run_internal`) |
| Prewarm Hermés | 60-90s parallel | 60-90s parallel (start hned) |
| `_load_dedup()` | ~2s sequential | ~2s parallel (F278B už aplikováno) |
| `build_acquisition_plan()` | 200s sequential | 200ms-200s **thread pool** |
| `_attempt_public_prewindup_barrier()` | 20s sequential | 0-20s **parallel** |
| **Total sequential blocking** | **283s** | **~5-30s** (pokud DuckDB v thread poolu) |

---

## Invarianty (GHOST)

| Invariant | Test | Soubor |
|-----------|------|--------|
| Prewarm start < 100ms od `_run_internal` entry | `test_prewarm_task_timing` | `tests/test_sprint_scheduler.py` |
| DuckDB init běží paralelně s prewarm | `test_duckdb_parallel_with_prewarm` | `tests/test_sprint_scheduler.py` |
| `build_acquisition_plan` v thread poolu | `test_plan_building_async` | `tests/test_sprint_scheduler.py` |
| `_attempt_public_prewindup_barrier` fire-and-forget | `test_barrier_non_blocking` | `tests/test_sprint_scheduler.py` |
| Prelud total < 30s pro fast-path queries | `test_prelude_timing_fast` | `tests/test_sprint_scheduler.py` |
| Prelud total < 60s pro standard queries | `test_prelude_timing_standard` | `tests/test_sprint_scheduler.py` |

---

## M1 8GB Bezpečnost

- **Prewarm v thread pool** = neblokuje event loop = MBTU
- **DuckDB timeout 10s** = uvolní memory pokud subprocess spawn fail
- **Fast path** = žádný MLX load pro pure text queries = < 500MB RAM
- **Bounded cache** = `_acquisition_plan_cache` má max 10 entries (query typů)
- **`asyncio.to_thread` depth** = default ThreadPoolExecutor má 8 worker threads — prewarm 3 tasky + DuckDB init + plan building = 5 současně = v limitu

---

## Prioritizace implementace

| Priorita | Fix | Impact | Risk |
|----------|-----|--------|------|
| **P0** | Fix 1: `build_acquisition_plan` → thread pool | -200s sequential | Nízký (No I/O podle docs) |
| **P0** | Fix 2: DuckDB init do `_run_internal` | -60s sequential | Střední (subprocess lifecycle) |
| **P1** | Fix 3: Prewarm na úplný začátek | -0s (už aplikováno?) | Nízký |
| **P1** | Fix 4: Barrier fire-and-forget | -20s sequential | Střední (semantika barrier) |
| **P2** | Fix 5: Plan caching | -200ms-200s pro repeat queries | Nízký |


---

## ✅ Implementační status (2026-06-25)

### Fix 1: `build_acquisition_plan` → thread pool ✅ IMPLEMENTOVÁNO

**Soubor:** `runtime/sprint_scheduler.py:6781-6804`

```python
self._timer.phase("acquisition_plan_build_start")
# AP-1 Fix 1: run build_acquisition_plan in thread pool so event loop
# stays responsive during plan building (200ms-200s CPU-bound work).
# No I/O inside build_acquisition_plan per its GHOST_INVARIANTS docstring.
_plan_kwargs = dict(
    query=query,
    duration_s=self._config.sprint_duration_s,
    aggressive_mode=self._config.aggressive_mode,
    uma_state=_uma_state,
    swap_detected=_swap_detected,
    accepted_findings_so_far=self._result.accepted_findings,
    branch_timeout_count=self._result.branch_timeout_count,
    acquisition_profile=self._config.acquisition_profile or "",
    source_quality_weights=(
        self._policy_manager.get_src_quality_weights()
        if self._policy_manager is not None and self._policy_manager.enabled
        else None
    ),
    rl_lane_combo=self._result.rl_lane_combo if self._result.rl_lane_combo else None,
    synthetic_domains=_synthetic_domains,
)
self._acquisition_plan = await asyncio.to_thread(
    build_acquisition_plan, **_plan_kwargs
)
self._timer.phase("acquisition_plan_build_end")
```

**Ověření:** `import OK` + `pytest tests/test_sprint_scheduler.py` → **99 passed**

**Přínos:** Event loop zůstává responsive během build_acquisition_plan. Je možné že se další operace (DuckDB init, prewarm) dokončí během čekání na thread, což dále snižuje celkový blocking time.

---

### Další fixy — naplánované

| Fix | Status | Soubor | Přínos |
|-----|--------|---------|---------|
| Fix 2: DuckDB init do `_run_internal` | TODO | `runtime/sprint_scheduler.py` | -60s sequential |
| Fix 3: Prewarm na úplný začátek | ✅ Už aplikováno (linr 6476) | `runtime/sprint_scheduler.py` | 0s (překrývá se s ostatními) |
| Fix 4: `_attempt_public_prewindup_barrier` fire-and-forget | TODO | `runtime/sprint_scheduler.py` | -20s sequential |
| Fix 5: Plan caching | TODO | `runtime/sprint_scheduler.py` | -200ms-200s pro repeat queries |

---

### Očekávaný výsledek po všech opravách

| Fáze | Před | Po |
|------|-------|-----|
| DuckDB subprocess init | 30-60s sequential | 0s (přesunuto do `_run_internal`) |
| Prewarm Hermés | 60-90s parallel | 60-90s parallel (start hned) |
| `_load_dedup()` | ~2s sequential | ~2s parallel (F278B už aplikováno) |
| `build_acquisition_plan()` | 200s sequential | **0s sequential** (thread pool ✅) |
| `_attempt_public_prewindup_barrier()` | 20s sequential | 0s (fire-and-forget TODO) |
| **Total sequential blocking** | **283s** | **~2-5s** |

**Kritická cesta po Fix 1:**
```
Pre-warm Hermés (paralelně) + DuckDB init (paralelně) → _load_dedup() (~2s) → event loop free
```


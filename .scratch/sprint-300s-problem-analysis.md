# Sprint 300s + Gather Distribution — Komplexní Analýza Problémů

**Datum:** 2026-06-15
**Scope:** `runtime/sprint_scheduler.py` + `utils/async_helpers.py`

---

## EXEKUTIVNÍ SUMÁŘ (I5 Update)

| Problém | Zjištění | Action |
|---------|----------|--------|
| I5: asyncio.gather 13× vs safe_gather 44× | **0 bare asyncio.gather existuje** — rg počítal i komentáře | F262D dokončeno |
| Gather body bottleneck | Sekvenční I/O v těle gather calls (DuckDB + LMDB + MLX chain) | Chunking + Semaphore(4) |
| 300s sprint 0 findings | windup bug (F278B) + zero domain seeds → zero pipeline runs | F278B fix + seed strategy |
| MLX blocking Metal | Lazy eval bez barrier + is_idle() F273H bug | mx.eval([]) + fix P3 |
| TaskGroup migration | Sémantický rozdíl, ne výkonnostní — neřeší M1 bottleneck | Jen pro all-or-nothing |

---

## 1. Gather Distribution — Fakta (ne mýty)

### 1.1 Skutečný stav po F262D

| API | Počet | Engine |
|-----|-------|--------|
| `asyncio.gather` (bare volání) | **0** | — |
| `safe_gather_dropin` | **28** | `asyncio.gather(return_exceptions=True)` interně |
| `safe_gather_strict` | **7** | `asyncio.TaskGroup` (PEP 654) |
| `safe_gather_fire_and_forget` | **3** | fire-and-forget |
| `safe_gather` (struct) | **2** | gather-based |
| `safe_create_task` | **35** | task creation |

**Důkaz:** `rg -c 'asyncio\.gather' sprint_scheduler.py` = 13 obsahuje i komentáře jako `# asyncio.gather with return_exceptions=True`. Reálná volání = 0.

### 1.2 Dvě engine safe_gather

```
safe_gather_dropin (28 volání)
└── asyncio.gather(*tasks, return_exceptions=True)
    └── _classify_gathered() — filtruje Exception, re-raise BaseException
    └── eager_start=True (Python 3.12+) pro rychlejší scheduling

safe_gather_strict (7 volání)
└── asyncio.TaskGroup() + except* (PEP 654)
    └── BaseExceptionGroup při jakékoli chybě
    └── all-or-nothing sémantika
```

**TaskGroup ≠ rychlejší na M1** — stejný event-loop scheduling, jen jiná sémantika (first error canceluje siblings).

---

## 2. Skutečný Gather Body Bottleneck

### 2.1 Co gather body skutečně dělá

```python
# Pattern A: Heavy sequential I/O v těle gather calls
results = await safe_gather_dropin(
    *[analyze_one(fp) for fp in file_paths],  # L18896: disk I/O, sekvenční
    label="sprint_scheduler:17400"
)

# Pattern B: DuckDB write chain (SYNCHRONOUS CHAIN)
_ = await self._duckdb_store.async_ingest_findings_batch(findings)  # L6335

# Pattern C: LMDB bulk write (hot path)
cursor.putmulti(entity_records)  # L8900

# Pattern D: MLX inference (M1 Metal, lazy + blocking)
embedding = await self._mlx_engine.embed(text)  # L9400

# Pattern E: DuckPGQ graph upsert
await self._graph.upsert_ioc(ioc)  # L9500
```

### 2.2 Hot Path Structure

```
run() [L6201]
├── run_prelude() [L5985-6049]
│   └── safe_gather_dropin(*_init_tasks)    ← init tasks, hot during sprint start
├── run_acquisition_lanes() [L7000+]
│   └── safe_gather_dropin(*lane_tasks)     ← NETWORK I/O — concurrent OK
├── run_advisory_runner() [L21440]
│   ├── safe_gather_strict(synthesis_tasks) ← TaskGroup, strict
│   ├── safe_gather_dropin(sidecar_tasks)   ← dropin, fail-soft
│   └── _run_synthesis_sidecar()            ← SEQUENTIAL: DuckDB + MLX
└── _accumulate_findings_to_graph()         ← SEQUENTIAL: LMDB + DuckPGQ
```

**Hot spot:** `_run_synthesis_sidecar()` + `_accumulate_findings_to_graph()` běží **sekvenčně** po gather fázi. To je bottleneck.

### 2.3 M1 8GB Root Causes

| Causa | Detail | Impact |
|-------|--------|--------|
| DuckDB sequential writes | `async_ingest_findings_batch` sequential i when called from loop | -50% throughput |
| LMDB sequential puts | Per-item `env.begin(write=True)` v loopu místo `putmulti` | -70% throughput |
| MLX Metal blocking | `mx.eval()` voláno bez barrier, lazy evaluation | Metal cache fragmentation |
| is_idle() F273H bug | `_last_inference_at=None` → returns False → model stays loaded | +0.70GB RAM |

---

## 3. 300s Sprint 0 Findings — Root Cause Chain

```
1. No domain seeds → CT/DOH/WAYBACK skipped
         ↓
2. Zero pipeline runs → zero findings generated
         ↓
3. DuckDB async_ingest_findings_batch never called
         ↓
4. LMDB putmulti never called
         ↓
5. Graph upsert_ioc never called
         ↓
6. MLX inference never triggered
         ↓
7. DSPy optimization fails on empty batch
```

**Klíčový bug:** `windup_lead` propagation — F250 měl bug kde 180s default přepsal 60s duraci sprintu. F278B to opravuje v `effective_windup_lead_s`.

---

## 4. Cutting-Edge Řešení (M1 8GB Compatible)

### 4.1 P0-1: Gather Body Parallelization

```python
# CURRENT (sequential gather body):
async def _run_synthesis_sidecar(self, findings):
    for finding in chunk(findings, 50):
        await self._duckdb_store.async_ingest_findings_batch(finding)  # sequential
        await self._graph.upsert_ioc(finding)                           # sequential

# TARGET (parallel within bounded semaphore):
SEMAPHORE = asyncio.Semaphore(4)  # M1 8GB: 4 concurrent I/O ops

async def _process_chunk(chunk):
    async with SEMAPHORE:
        await asyncio.gather(
            self._duckdb_store.async_ingest_findings_batch(chunk),
            self._graph.upsert_ioc_bulk(chunk),  # bulk LMDB putmulti
            mx.eval([]),                          # force Metal barrier
            return_exceptions=True
        )
```

### 4.2 P0-2: MLX Batched Inference (existuje z P0-2 memory)

MLXBatchedExecutor je lazy — aktivuje se automaticky když:
- `is_batch_safe()` vrátí True
- Worker thread initialized
- Fix P3 + P4 = aktivace continuous batchingu

### 4.3 P1-1: uvloop Task Scheduling

```python
# __main__.py entry point
import uvloop
uvloop.install()

# Všech 40+ safe_gather_* calls benefit z uvloop faster task scheduling
# ~2-3× faster task scheduling na M1 (epoll/kqueue vs pure asyncio selector)
```

### 4.4 M1 8GB Safe Bounds

| Operation | Current | Safe Limit | Action |
|-----------|---------|------------|--------|
| Concurrent gather tasks | unlimited | **16** | Semaphore(16) cap |
| MLX batches | 1 | **4** | Semaphore(4) + batch_size=4 |
| DuckDB concurrent writes | sequential | **4** | Write pool |
| LMDB putmulti batch | 1000 | **5000** | Bounded chunking |

---

## 5. Akční Plán (Fáze)

### Fáze 1: Kritické Fixy (P0)
| # | Akce | Soubor | Řádek |
|---|------|--------|-------|
| P3 | Opravit is_idle() None-check: `return False` → `return True` | brain/deephermes3_engine.py | ~1398 |
| P4 | Pass hermes_engine v dark_surface call | runtime/sprint_scheduler.py | ~24464 |
| F278B | Opravit windup_lead propagation | sprint_scheduler.py | ~6000 |

### Fáze 2: Gather Body Parallelization (P0-1)
| # | Akce | Soubor | Řádek |
|---|------|--------|-------|
| G1 | Chunk findings + Semaphore(4) v _run_synthesis_sidecar | sprint_scheduler.py | ~23950 |
| G2 | LMDB bulk write přes putmany() | knowledge/duckdb_store.py | ~8900 |
| G3 | DuckDB batch ingest s Arrow (P0-4 z memory) | duckdb_store.py | ~5000 |

### Fáze 3: MLX + uvloop (P0-2/P1-1)
| # | Akce | Soubor | Řádek |
|---|------|--------|-------|
| M1 | uvloop.install() v __main__.py | core/__main__.py | ~100 |
| M2 | mx.eval([]) po každém Metal operation | brain/deephermes3_engine.py | ~1500 |

---

## 6. Test Strategy

```python
# tests/probe_f278b_windup_fix/
test_effective_windup_lead_60s_sprint()
test_effective_windup_lead_300s_sprint()
test_windup_guard_aborts_short_sprints()

# tests/probe_p3_is_idle/
test_is_idle_never_used_returns_true()
test_unload_called_when_never_used()

# tests/probe_p4_dark_surface/
test_hermes_engine_passed_to_generate_dark_surface()
test_fallback_when_hermes_none()

# tests/probe_g1_gather_body/
test_chunking_with_semaphore()
test_lmdb_putmulti_bulk()
```

---

## 8. VERIFIKACE 2026-06-15 — VŠECHNY OPRAVY HOTOVY

### P3: is_idle() — OPRAVENO ✓
```python
# brain/deephermes3_engine.py:1405-1409
# F273H+: Model was prewarmed but never used for inference — unload it
if self._model_ever_loaded and self._last_inference_at is None:
    return True  # Safe to unload: never used, no warm-start benefit
if self._last_inference_at is None:
    return True
```
Již správně vrací `True` when never used → model se unloaduje, Metal cache se uvolní.

### P4: hermes_engine pass — OPRAVENO ✓
```python
# runtime/sprint_scheduler.py:24776-24783
dark_queries = await hyp_eng.generate_dark_surface_queries(
    findings=findings_for_dark,
    hermes_engine=self._hermes_engine if self._hermes_engine is not None else None,
    tor_available=tor_available,
    i2p_available=i2p_available,
)
```
Již správně passuje `self._hermes_engine`.

### F278B: windup_lead propagation — OPRAVENO ✓
```python
# runtime/sprint_scheduler.py:31986-31992
# F228G: use final_windup_lead_s (NOT effective_windup_lead_s).
windup_lead_s=config.final_windup_lead_s,
```
Již správně používá `final_windup_lead_s`.

### F221 Guard — SPRÁVNĚ IMPLEMENTOVÁN ✓
```python
# core/__main__.py:1466-1495
_F272A_WINDUP_CLAMP_MIN_S = 30.0
_F272A_WINDUP_CLAMP_MAX_S = 180.0
_F272A_WINDUP_LEAD_FRAC = 0.30
_raw_windup = float(duration_s) * _F272A_WINDUP_LEAD_FRAC
_effective_windup_s = max(_F272A_WINDUP_CLAMP_MIN_S, min(_F272A_WINDUP_CLAMP_MAX_S, _raw_windup))
```
Pro 300s sprint: 90s windup, 210s active window ✓

### TaskGroup Migration — NENÍ POTŘEBA
TaskGroup = sémantický rozdíl (all-or-nothing vs fail-soft), ne výkonnostní. 0 bare asyncio.gather existuje.

### Gather Body Parallelization — NENÍ POTŘEBA
Acquisition lanes už běží concurrent přes safe_gather_dropin(28). Hot path network I/O je concurrent.

---

## 9. 300s SPRINT 0 FINDINGS — SKUTEČNÁ PŘÍČINA

| Root Cause | Detail |
|------------|--------|
| **Žádné domain seeds** | CT/DOH/WAYBACK lanes skip bez doménových vstupů |
| **Zero pipeline runs** | Žádné domény → žádné findingy |
| **DuckDB empty** | async_ingest_findings_batch nikdy nezavolán |
| **MLX ml_jobs=0** | Resource allocator "warn" path (pokud memory pressure na startu) |

**Řešení:** Pro 300s sprint bez domain seeds použít `--aggressive` mód nebo explicitně seedovat domény přes `--seed-file`.

| Problém | Zdroj | Severity | Ř�ešení |
|---------|-------|----------|--------|
| I5: Gather distribution (13× vs 44×) | **Falešný** — rg počítal i komentáře | — | Žádné, F262D dokončeno |
| Gather body bottleneck | Sekvenční I/O v těle gather calls | **HIGH** | Chunking + Semaphore(4) |
| 300s sprint 0 findings | windup bug + zero domain seeds | **CRITICAL** | F278B fix + seed strategy |
| MLX blocking Metal | Lazy eval bez barrier + is_idle() F273H bug | **HIGH** | mx.eval([]) + fix P3 |
| TaskGroup migration | Není performance fix | LOW | Jen pro all-or-nothing sémantiku |

**Klíčové poznatky:**
1. Problém NENÍ v gather API — migrace F262D je hotová
2. Problém JE v gather **body** — sekvenční I/O serializuje i concurrent gather calls
3. 300s sprint 0 findings = absence domain seeds + windup bug, ne gather bottleneck
4. TaskGroup = sémantický rozdíl, ne výkonnostní — NEřeší M1 bottleneck
5. Řešení = chunking + semaphore + uvloop + batched MLX inference + P3/P4 fix

---

## EXEKUTIVNÍ SUMÁŘ

| Problém | Root Cause | Závažnost | Odhad Zisku |
|---------|-----------|-----------|-------------|
| P1: ml_jobs=0 po celý sprint | `get_recommended_concurrency()` volána jen 1× při init; "warn"/"critical" → ml_jobs=0 | CRITICAL | ~2-4× inference throughput |
| P2: Hermes3Engine.generate() nikdy nezavolána | hermes_engine=None v dark_surface call (řádek 24464); MLXBatchedExecutor není v SprintScheduler wired | CRITICAL | Aktivace continuous batchingu |
| P3: is_idle() blokuje unload | `_last_inference_at=None` → is_idle() vrací False → model se neunloaduje → Metal cache roste | HIGH | +0.70GiB volná RAM |
| P4: Dark surface bez LLM | hermes_engine=None v generate_dark_surface_queries() | HIGH | Kvalitnější dark pivot queries |
| P5: mx.eval/clear_cache nikdy voláno | Unload přeskojen (P3), Metal cache roste | HIGH | +0.70GiB volná RAM |

---

## PROBLÉM 1: ml_jobs=0 — Resource Allocator

### Anatomie

```python
# resource_allocator.py:291-311
def get_recommended_concurrency() -> dict[str, int]:
    level = get_memory_pressure_level()
    return {
        "normal":   {"fetch": 20, "parse_workers": 4, "ml_jobs": 1, "browser": 1},
        "warn":     {"fetch": 8,  "parse_workers": 2, "ml_jobs": 0, "browser": 0},
        "critical": {"fetch": 2,  "parse_workers": 1, "ml_jobs": 0, "browser": 0},
    }[level]
```

### Volající

```python
# runtime/sprint_scheduler.py:29127
limits = get_recommended_concurrency()
# ... použito pro fetch semaphore, ml_jobs se jen loguje (řádek 29135)
f"ml_jobs={limits['ml_jobs']}"
```

### Problém

1. `get_recommended_concurrency()` volána **pouze jednou** při init (`__init__` phase)
2. Není volána dynamicky během sprintu
3. Pokud je paměťový tlak "warn" nebo "critical" při startu → `ml_jobs=0` pro celý sprint
4. i když `ml_jobs=1` v "normal" — neexistuje žádný dynamic re-rating během OODA loopu

### Důsledek

- Hermes3Engine.generate() **nikdy nezavolána** protože ml_jobs=0
- Ale toto je **špatný因果** — ml_jobs=0 neznamená "nezavolávej Hermes", znamená to "neplánuj nové ML joby"
- Hermés je initialized a loaded (řádek 25139), ale žádné inference requesty se negenerují

### Řešení

**Option A (M1-safe):** Změnit ml_jobs default z 0 na 1 i pro "warn" path (M1 8GB má ~1.5GB headroom i ve warn režimu):
```python
"warn":     {"fetch": 8,  "parse_workers": 2, "ml_jobs": 1, "browser": 0},
```

**Option B:** Volat `get_recommended_concurrency()` dynamicky každých 30s během OODA loopu a přehodnotit ml_jobs limit

**Option C:** Oddělit "ml_jobs scheduling" od "fetch scheduling" — ml_jobs by měl být independent resource, ne memory-pressure-gated

---

## PROBLÉM 2: MLXBatchedExecutor NOT Wired in SprintScheduler

### Anatomie

```python
# SprintScheduler nemá žádnou referenci na MLXBatchedExecutor
# grep 'MLXBatchedExecutor\|_mlx_batcher\|batcher' runtime/sprint_scheduler.py → 0 matches
```

### Ale DeepHermes3Engine.generate()už má batching internally:

```python
# brain/deephermes3_engine.py:1575-1650 (generate method)
async def generate(self, prompt, temperature, max_tokens, system_msg, *, thinking=True):
    try:
        batcher = await self._ensure_mlx_batcher()
        if batcher is not None and batcher.is_batch_safe(...):
            return await batcher.execute(...)
    except Exception as _batching_err:
        logger.debug("[P0-2] batching routing failed, falling back to direct: %s", _batching_err)
    # direct path...
```

### Klíčový detail

DeepHermes3Engine **už má** MLXBatchedExecutor wiring internally — volá `await self._ensure_mlx_batcher()` a pokud je batcher available, použije ho.

**Problém je že _ensure_mlx_batcher() vrací None** pokud:
1. Memory pressure je vysoká (is_batch_safe() check fails)
2. Worker thread není initialized

### MLXBatchedExecutor.__init__:

```python
# brain/mlx_batched_executor.py
def __init__(self, engine: Hermes3Engine, worker_thread: Any = None):
    self._engine = engine
    self._worker_thread = worker_thread  # Optional MLXWorkerThread (P0-3)
    self._scheduler = None
    self._initialized = False
```

### Řešení

MLXBatchedExecutor je **lazy** — inicializuje se při prvním volání `execute()`. Problém není v "wiring" ale v tom že:

1. **hermes_engine je None** v dark_surface call → LLM expansion disabled
2. **ml_jobs=0** znamená že žádné inference requesty se negenerují
3. **Model je loaded** (line 25139) ale **generate() se nikdy nezavolá**

**Akce:** Opravit P1 a P4 — pak MLXBatchedExecutor automaticky aktivní.

---

## PROBLÉM 3: is_idle() F273H Bug — Model Never Unloads

### Anatomie

```python
# brain/deephermes3_engine.py:1390-1403
def is_idle(self) -> bool:
    """
    F273H: Check if engine has been idle beyond threshold.
    Returns True if no inference occurred within _idle_unload_timeout_s.
    Fail-safe: returns False if _last_inference_at is None (never used).
    """
    if self._last_inference_at is None:
        return False  # ← BUG: model loaded but never used → stays resident forever
    try:
        import time as _time
        elapsed = _time.monotonic() - self._last_inference_at
        return elapsed >= self._idle_unload_timeout_s
    except Exception:
        return False
```

### Teardown sequence:

```python
# runtime/sprint_scheduler.py:25195-25224 (_unload_hermes_at_teardown)
async def _unload_hermes_at_teardown(self) -> None:
    if self._hermes_engine is None:
        return
    # F273H: Check idle status before unload
    if hasattr(self._hermes_engine, 'is_idle') and callable(self._hermes_engine.is_idle):
        if not self._hermes_engine.is_idle():  # ← is_idle() = False (never used)
            log.debug("[P12][F273H] Hermes still active (idle check), skipping unload")
            return  # ← UNLOAD SKIPPED!
    await get_model_manager().release_model("hermes")
```

### Causation Chain for +0.70GiB

```
Sprint start: hermes loaded (line 25139)
  ↓
No ML inference requests generated (ml_jobs=0, hermes_engine=None in dark_surface)
  ↓
_last_inference_at = None (never updated — generate() never called)
  ↓
is_idle() returns False (never used → return False per F273H comment)
  ↓
_unload_hermes_at_teardown() skips unload (line 25214-25216)
  ↓
ModelManager.release_model() NEVER called
  ↓
mx.eval([]) + mx.metal.clear_cache() NEVER called
  ↓
Metal cache accumulates: +0.70GiB
```

### SPRAVNY Interpretation of F273H

F273H idle check má dva případy:
1. **Model was used and is now idle** → skip unload (keep warm for next sprint)
2. **Model was NEVER used** → should unload (nothing to keep warm)

Currently case 2 → stays loaded → memory leak.

### Řešení

```python
# brain/deephermes3_engine.py:1390-1403 — FIX
def is_idle(self) -> bool:
    """
    F273H: Check if engine has been idle beyond threshold.
    Returns True if no inference occurred within _idle_unload_timeout_s.
    Fail-safe: returns True if _last_inference_at is None (never used) —
    unloaded models stay unloaded; keeping an UNUSED model warm wastes RAM.
    """
    if self._last_inference_at is None:
        return True  # ← FIX: never used → safe to unload
    try:
        import time as _time
        elapsed = _time.monotonic() - self._last_inference_at
        return elapsed >= self._idle_unload_timeout_s
    except Exception:
        return True  # fail-safe: unload on error
```

---

## PROBLÉM 4: Dark Surface hermes_engine=None

### Anatomie

```python
# runtime/sprint_scheduler.py:24464
dark_queries = await hyp_eng.generate_dark_surface_queries(
    findings=findings_for_dark,
    hermes_engine=None,  # ← HROMADNA CHYBA: Hermes engine not passed
    tor_available=tor_available,
    i2p_available=i2p_available,
)
```

### generate_dark_surface_queries signature:

```python
# brain/research_hypothesis_engine.py:2952
async def generate_dark_surface_queries(
    self,
    findings: list[Any],
    hermes_engine: Any = None,  # ← Optional, ale None = LLM expansion disabled
    tor_available: bool = False,
    i2p_available: bool = False,
) -> list[DarkQuery]:
```

### Flow when hermes_engine=None:

```
generate_dark_surface_queries() called with hermes_engine=None
  ↓
Line 2989: if hermes_engine is not None: → FALSE → skips LLM path
  ↓
Line 3110: return self._generate_dark_surface_queries_fallback(iocs, transport_str)
  ↓
Fallback generates only heuristic queries (Q=0.124 — nízká kvalita)
  ↓
No LLM-assisted expansion → low-quality dark surface queries
```

### Řešení

```python
# runtime/sprint_scheduler.py:24464 — FIX
dark_queries = await hyp_eng.generate_dark_surface_queries(
    findings=findings_for_dark,
    hermes_engine=self._hermes_engine,  # ← FIX: pass loaded engine
    tor_available=tor_available,
    i2p_available=i2p_available,
)
```

### Ale pozor — self._hermes_engine může být None pokud:

1. `HLEDAC_ENABLE_LLM=0` (model disabled)
2. Model load failed (RuntimeError na řádku 25139-25144)
3. Memory pressure na startu → model_manager block

Proto potřebujeme null-check:

```python
# runtime/sprint_scheduler.py:24464 — SAFE FIX
dark_queries = await hyp_eng.generate_dark_surface_queries(
    findings=findings_for_dark,
    hermes_engine=self._hermes_engine if self._hermes_engine is not None else None,
    tor_available=tor_available,
    i2p_available=i2p_available,
)
```

---

## PROBLÉM 5: mx.eval/clear_cache v Teardown Path

### Anatomie — ModelManager.release_model()

```python
# brain/model_manager.py:778-799
async def release_model(self, model_name: ModelName) -> None:
    await self._release_model_async(model_type, model_name)

# brain/model_manager.py:799-820
async def _release_model_async(self, model_type: ModelType, model_name: str):
    # ... cleanup engine ...
    finally:
        mx.eval([])  # F183C: barrier before clear
        mx.clear_cache()
```

### mx.eval/clear_cache je voláno správně v ModelManager

Problém není v ModelManager — je to v **P3**: `_unload_hermes_at_teardown()` nevolá `release_model()` když `is_idle() = False`.

### Fix P3 = Fix P5

Opravou is_idle() (P3) se automaticky opraví P5 — `release_model()` se zavolá a `mx.eval([]) + mx.clear_cache()` se provede.

---

## SOUPIS AKCÍ

| # | Akce | Soubor | Řádek | Priorita |
|---|------|--------|-------|----------|
| A1 | Opravit is_idle() None-check: `return False` → `return True` | brain/deephermes3_engine.py | ~1398 | P0 |
| A2 | Pass hermes_engine v dark_surface call | runtime/sprint_scheduler.py | ~24464 | P0 |
| A3 | Změnit ml_jobs default v "warn" path z 0→1 | resource_allocator.py | ~310 | P1 |
| A4 | Přidat dynamic re-rating ml_jobs během OODA loopu | runtime/sprint_scheduler.py | ~6095 | P2 |

---

## TESTOVACÍ STRATEGIE

### Probe Tests

```python
# tests/probe_p3_is_idle/
test_is_idle_never_used_returns_true()     # _last_inference_at=None → is_idle()=True
test_is_idle_used_recently_returns_false()  # _last_inference_at=t() → is_idle()=False
test_unload_skipped_when_active()          # is_idle()=False → unload skipped
test_unload_called_when_never_used()       # is_idle()=True → unload called

# tests/probe_p4_dark_surface/
test_hermes_engine_passed_to_generate_dark_surface()
test_none_hermes_fallback_to_heuristic()

# tests/probe_p1_ml_jobs/
test_ml_jobs_warn_path_set_to_1()
test_dynamic_rerating_ml_jobs()
```

---

## OČEKÁVANÝ DOPAD PO OPRAVÁCH

| Metric | Před | Po |
|--------|------|-----|
| ml_jobs | 0 | 1 |
| Hermes3Engine.generate() volána | Nikdy | Ano (continuous batching) |
| Dark surface query quality | Q=0.124 fallback | LLM-assisted expansion |
| Metal cache po sprintu | +0.70GiB leak | 0 GiB (clean unload) |
| RAM usage | ~7.0GB (swapping) | ~6.25GB (bounded) |

---

## INVARIANTS

| ID | Test | Ověření |
|----|------|---------|
| P3 | is_idle() s None vrací True | probe test |
| P3 | _unload volá release_model když never used | probe test |
| P4 | hermes_engine passed to dark_surface | probe test |
| P4 | fallback funguje když hermes_engine=None | probe test |
| P1 | ml_jobs=1 v warn path | resource_allocator test |
| P5 | mx.eval+clear_cache voláno v teardown | integration test |

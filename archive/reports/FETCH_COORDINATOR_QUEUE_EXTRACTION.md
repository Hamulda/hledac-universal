# FETCH_COORDINATOR_QUEUE_EXTRACTION.md

> **Sprint F-EXTRACT-2: Phase 2 of GOD_OBJECT_ANALYSIS decomposition.**
> Datum: 2026-06-04
> Cíl: extrakce `enqueue_pivot` + `enqueue_hypothesis_pivot` z `SprintScheduler`
> do třídy `FetchCoordinator` (pivot queue management).
> Scope: PURE STRUCTURAL EXTRACTION — žádná změna business logiky.

---

## 1. EXECUTIVE SUMMARY

Phase 2 úspěšně dokončena. Obě metody (104 + 76 LOC) přesunuty z `SprintScheduler`
do `FetchCoordinator` v `coordinators/fetch_coordinator.py`. Metody jsou
používány jako **callback** v `_run_public_discovery_in_cycle` a `windup_engine`,
takže delegation wrappery na `SprintScheduler` jsou nutné pro zachování API.

**Výsledky:**
- ✅ **88/88 testů pass** (`tests/test_sprint_scheduler.py`, 4:07 runtime)
- ✅ **15/17 F193B testů pass** — 2 pre-existující failure (pipeline source string-matching, ne regresní)
- ✅ **37/38 P12 + e2e testů pass** — 1 pre-existující pipeline `UnboundLocalError: asyncio` na ř. 3770
- ✅ Provider-based DI — coordinator nikdy nedrží referenci na `SprintScheduler`
- ✅ GHOST_INVARIANTS zachovány (bounded, fail-soft, no MLX, no asyncio.run in TPE)

---

## 2. LIST OF EXTRACTED METHODS

### 2.1 Tabulka extrahovaných metod

| # | Metoda | LOC originál | Async | Callerů | Stav |
|---|--------|-------------:|:-----:|--------:|------|
| 1 | `enqueue_pivot` | 104 | S | 5 (4 interní + 1 windup) | ✅ EXTRACTED |
| 2 | `enqueue_hypothesis_pivot` | 76 | S | 1 (pipeline callback) | ✅ EXTRACTED |
| **Σ** | | **180** | | | **2 extracted** |

### 2.2 Interní callery `enqueue_pivot` v `SprintScheduler` (4 sites)

| Line | Kontext |
|------|---------|
| 17836 | `_run_pivot_planner_advisory` — hypothesis enqueue from string |
| 25731 | `_execute_pivot` (hypothesis_probe case) |
| 25841 | `_buffer_ioc` (re-enqueue s degree=2) |
| 26041 | `_speculative_prefetch_seeds` (OODA loop) |

### 2.3 Externí callery

| Soubor | Line | Kontext |
|--------|-----:|---------|
| `runtime/windup_engine.py` | 194 | `scheduler.enqueue_pivot(...)` (BoundedW hypothesis loop) |
| `pipeline/live_public_pipeline.py` | 4818 | `enqueue_hypothesis_pivot(...)` callback (F193B) |
| `tests/test_e2e_dry_run.py` | 53 | `scheduler.enqueue_pivot = AsyncMock()` (instance attribute mock) |
| `tests/test_sprint_f193b_hypothesis_feedback.py` | 195,197,205 | Instance attribute mock + has-checks |
| `tests/test_sprint_p12_hypothesis.py` | 650,663 | P12 hypothesis burst |

---

## 3. DEPENDENCY INJECTION PATTERN

### 3.1 Provider matrix (9 nových providerů)

Všechny providery jsou `Callable[[...], ...]` výchozí na `lambda: None`
(žádný crash při použití mimo SprintScheduler kontext, např. test fixture).

| Provider | Signature | Purpose |
|----------|-----------|---------|
| `pivot_queue_provider` | `Callable[[], Any]` | Returns `asyncio.PriorityQueue[PivotTask]` |
| `pivot_stats_provider` | `Callable[[], dict]` | Returns pivot stats dict (mutable ref) |
| `hypothesis_query_count_provider` | `Callable[[], int]` | Read hypothesis query count |
| `hypothesis_query_count_setter` | `Callable[[int], None]` | Write hypothesis query count (mutates `scheduler._hypothesis_query_count` via `setattr`) |
| `hypothesis_depth_provider` | `Callable[[], int]` | Read hypothesis depth |
| `hypothesis_depth_setter` | `Callable[[int], None]` | Write hypothesis depth |
| `sprint_config_provider` | `Callable[[], Any]` | Returns `SprintSchedulerConfig` (pro cap check) |
| `adaptive_priority_provider` | `Callable[[str, float], float]` | Delegates to `SprintScheduler._get_adaptive_priority(tt, base)` |
| `enqueue_pivot_provider` | `Callable[..., Any]` | Callback to `SprintScheduler.enqueue_pivot` (test-patchable) |

### 3.2 Klíčový edge case: `enqueue_pivot_provider` callback

**Problém:** Test `test_hypothesis_ioc_type_maps_correctly` patchuje
`scheduler.enqueue_pivot = mock_enqueue` a pak volá
`scheduler.enqueue_hypothesis_pivot(...)` a očekává, že se zavolá mock.

**Řešení:** Lambda provider `enqueue_pivot_provider=lambda **kw: self.enqueue_pivot(**kw)`
je **bound to `self` (SprintScheduler) při konstrukci**. Pozdější
`scheduler.enqueue_pivot = mock` změní atribut, takže `self.enqueue_pivot(...)`
v provideru najde mock. Python late-binding na `self.enqueue_pivot` toto umožňuje.

```python
# V SprintScheduler.__init__:
self._fetch_coordinator = _FC(
    ...
    enqueue_pivot_provider=lambda **kw: self.enqueue_pivot(**kw),
)
```

V `FetchCoordinator.enqueue_hypothesis_pivot`:
```python
self._enqueue_pivot_provider(
    ioc_value=ioc_value,
    ioc_type=ioc_type,
    confidence=confidence,
    degree=float(depth),
    task_type=None,
)
```

### 3.3 State ownership pattern

State (`_pivot_queue`, `_pivot_stats`, `_hypothesis_query_count`,
`_hypothesis_depth`) **zůstává na `SprintScheduler`** z důvodu:

1. **Test contract:** `hasattr(scheduler, "_hypothesis_depth")` musí být True
2. **Direct attribute access:** `scheduler._hypothesis_query_count == 1` musí fungovat
3. **Test patches:** `scheduler._hypothesis_query_count = 5` musí fungovat

Setters (`hypothesis_query_count_setter`) používají `setattr(self, ...)`
v SprintScheduler → mutuje přímo instanci, ne vytváří nový atribut.

### 3.4 Wiring v SprintScheduler.__init__

```python
# Inicializace po _hypothesis_query_count = 0
from hledac.universal.coordinators.fetch_coordinator import (
    FetchCoordinator as _FC,
)
self._fetch_coordinator = _FC(
    pivot_queue_provider=lambda: getattr(self, "_pivot_queue", None),
    pivot_stats_provider=lambda: getattr(self, "_pivot_stats", None),
    hypothesis_query_count_provider=lambda: getattr(self, "_hypothesis_query_count", 0),
    hypothesis_query_count_setter=lambda v: setattr(self, "_hypothesis_query_count", v),
    hypothesis_depth_provider=lambda: getattr(self, "_hypothesis_depth", 0),
    hypothesis_depth_setter=lambda v: setattr(self, "_hypothesis_depth", v),
    sprint_config_provider=lambda: self._config,
    adaptive_priority_provider=lambda tt, base: self._get_adaptive_priority(tt, base),
    enqueue_pivot_provider=lambda **kw: self.enqueue_pivot(**kw),
)
```

`getattr(self, "_pivot_queue", None)` je defensivní — poskytuje `None` fallback
pokud by stav nebyl inicializován (test fixtures, partial init).

---

## 4. DELEGATION WRAPPERS (SprintScheduler)

Každá extrahovaná metoda má v `SprintScheduler` tenký delegation wrapper
(regular `def`, **ne `@property`**, kvůli callback contractu):

```python
def enqueue_pivot(
    self,
    ioc_value: str,
    ioc_type: str,
    confidence: float,
    degree: float = 1.0,
    task_type: str | None = None,
) -> None:
    """Sprint F-EXTRACT-2: Delegation wrapper to FetchCoordinator.enqueue_pivot."""
    return self._fetch_coordinator.enqueue_pivot(
        ioc_value=ioc_value, ioc_type=ioc_type, confidence=confidence,
        degree=degree, task_type=task_type,
    )

def enqueue_hypothesis_pivot(
    self,
    ioc_value: str,
    ioc_type: str = "hypothesis",
    confidence: float = 0.7,
    depth: int = 1,
) -> bool:
    """Sprint F-EXTRACT-2: Delegation wrapper to FetchCoordinator.enqueue_hypothesis_pivot."""
    return self._fetch_coordinator.enqueue_hypothesis_pivot(
        ioc_value=ioc_value, ioc_type=ioc_type,
        confidence=confidence, depth=depth,
    )
```

**Callback contract:** `_run_public_discovery_in_cycle` (sprint_scheduler.py:15067)
předává `enqueue_hypothesis_pivot=self.enqueue_hypothesis_pivot` jako callback do
pipeline. Wrapper musí zůstat `regular def` (ne `@property`), protože pipeline
ho volá jako `enqueue_hypothesis_pivot(ioc_value=...)`.

---

## 5. BEFORE / AFTER METRICS

### 5.1 SprintScheduler

| Metrika | Before | After | Delta |
|---------|-------:|------:|------:|
| Total LOC | 29 870 | 29 772 | **−98** |
| Total methods (def + async def) | 165 | 163 | **−2** |
| Public surface (`enqueue_pivot`, `enqueue_hypothesis_pivot`) | preserved | preserved | 0 |

> **Pozn.:** Wrappery jsou kratší než originály (50 LOC vs 180 LOC), ale
> +1 řádek pro `from ... import FetchCoordinator as _FC` v `__init__`
> a 17 řádků pro 9 provider lambdas. Čistý delta: ~98 řádků.

### 5.2 FetchCoordinator (extenze existující třídy)

| Metrika | Value |
|---------|------:|
| Total LOC (soubor) | 1 865 (was 1 707) |
| LOC přidané (2 metody + 9 provider parametrů + Callable import) | +158 |
| Methods | 41 + 2 = **43** (was 41) |
| Constructor params | 2 + 9 = **11** (was 2) |
| Properties | 0 (providery přes `self._X_provider()` direct call, ne `@property`) |

### 5.3 Cross-třídní coupling

- **SprintScheduler → FetchCoordinator**: 1 reference (`self._fetch_coordinator`)
- **FetchCoordinator → SprintScheduler**: 0 references (vše přes providery)
- **Cyclomatic coupling**: Nízký — providery jsou flat, single-purpose

### 5.4 F-EXTRACT-1 STATUS: NOT RE-APPLIED

> **DŮLEŽITÉ:** F-EXTRACT-1 (4 leaf FETCH metody: `_run_ct_to_passivedns_pivot_advisory`,
> `_run_bgp_advisory_sidecar`, `_run_wayback_cdx_deep_sidecar`, `_sensitive_query_transport`)
> **byl ztracen** (pravděpodobně git-stash-guard hook nebo compaction revert).
> F-EXTRACT-2 se proto přidal do **existující** třídy `FetchCoordinator(UniversalCoordinator)`
> (F-EXTRACT-0 třída, 41 metod), místo do nové F-EXTRACT-1 třídy, jak původní
> plán předpokládal.

**Doporučení pro budoucí sprint:** Re-apply F-EXTRACT-1 standalone (obnovit
4 leaf FETCH metody do nové `FetchCoordinator` třídy), pak refactorovat
F-EXTRACT-2 metody do stejné třídy (sloučení obou fází).

---

## 6. GHOST_INVARIANTS — ZACHOVÁNY

| Invariant | Jak zachován |
|-----------|--------------|
| `gather(return_exceptions=True)` + `_check_gathered` | Netýká se (sync metody) |
| `mx.eval([])` před `mx.metal.clear_cache()` | Netýká se (žádný MLX) |
| Žádné `time.sleep()` v async | Metody jsou sync |
| Žádné `asyncio.run()` v ThreadPoolExecutor | Netýká se (žádné TPE) |
| DuckDB write přes `async_ingest_findings_batch()` | Netýká se (žádný DuckDB write) |
| LMDB bulk write přes `cursor.putmulti()` | Netýká se (žádný LMDB) |
| RotatingBloomFilter pro URL dedup | Netýká se (žádný URL dedup) |
| M1 Metal cache limit 2.5 GiB | Netýká se (žádný MLX) |
| Fail-safe everywhere | `except (asyncio.QueueFull, Exception): pass` |
| Žádné bare `except:` | Všude `except Exception:` nebo konkrétní typ |
| **Bounded** | `maxsize=200` na `_pivot_queue`; cap check na hypothesis |
| **Provider-based DI** | Konzistentní s F-EXTRACT-1, lazy resolution, zero-copy refs |

---

## 7. VALIDATION

### 7.1 py_compile (oba soubory)

```bash
$ python3 -m py_compile coordinators/fetch_coordinator.py  # OK
$ python3 -m py_compile runtime/sprint_scheduler.py        # OK
```

### 7.2 Hlavní test suite

```bash
$ uv run pytest tests/test_sprint_scheduler.py -q --tb=no
88 passed, 22 warnings in 247.58s (0:04:07)
```

**100 % pass rate, 0 regresí.**

### 7.3 F193B testy (specificky cílené)

```bash
$ uv run pytest tests/test_sprint_f193b_hypothesis_feedback.py -v --tb=short
... 15 passed, 2 failed ...
```

**2 failure jsou pre-existující pipeline source-matching test bugs** (testují
`pipeline/live_public_pipeline.py` source přes `inspect.getsource()` + 5000-char
window; 5000-char window nestačí, fail-string je mimo okno). NEJSOU způsobeny
F-EXTRACT-2 refaktorem.

Důkaz: Failures jsou v `TestSprintF193BPipelineIntegration::test_pipeline_fails_soft_without_callback`
a `TestSprintF193BSeamInterface::test_pipeline_calls_callback_after_tot`. Obě
kontrolují **string** v `inspect.getsource(async_run_live_public_pipeline)`,
ne runtime chování. Selhávají na `p12_start + 5000` okně, které neobsahuje
hledaný string (který je v souboru, jen o pár řádků dále).

### 7.4 P12 + e2e testy

```bash
$ uv run pytest tests/test_sprint_p12_hypothesis.py tests/test_e2e_dry_run.py
... 37 passed, 1 failed ...
```

**1 failure je pre-existující** `UnboundLocalError: asyncio` na
`pipeline/live_public_pipeline.py:3770`. Nesouvisí s F-EXTRACT-2 (chyba je
v `live_public_pipeline.py`, ne v `SprintScheduler`).

### 7.5 Signatures zachovány 1:1

| Metoda | Originál signatura | FetchCoordinator signatura |
|--------|---------------------|----------------------------|
| `enqueue_pivot` | `(self, ioc_value, ioc_type, confidence, degree=1.0, task_type=None) -> None` | ✓ identická |
| `enqueue_hypothesis_pivot` | `(self, ioc_value, ioc_type="hypothesis", confidence=0.7, depth=1) -> bool` | ✓ identická |

---

## 8. KNOWLEDGE GRAPH IMPACT

`graphify-out/graph.json` bude vyžadovat update po commitu:

| Změna | Typ |
|-------|-----|
| `FetchCoordinator.__init__` +9 params | Update signature |
| `FetchCoordinator.enqueue_pivot` | New method on existing class |
| `FetchCoordinator.enqueue_hypothesis_pivot` | New method on existing class |
| `SprintScheduler._fetch_coordinator` | New attribute + instantiation |
| 9 provider lambdas v SprintScheduler | New edges to FetchCoordinator.__init__ |
| 2 wrapper methods (SprintScheduler) | Edges → FetchCoordinator methods |
| `Callable` import do `fetch_coordinator.py` | New import edge |

**Run after commit:**
```bash
graphify update .
```

---

## 9. NEXT STEPS

1. **F-EXTRACT-1 RE-APPLY** (1 sprint, 877 LOC, vysoká priorita): Znovu
   extrahovat 4 leaf FETCH metody do **nové** `FetchCoordinator` třídy
   (původní plán). Sloučit s F-EXTRACT-2 → vznikne jedna koherentní
   třída s 6+ metodami.
2. **F-EXTRACT-3** (3-5 sprintů): `AnalysisCoordinator` + 16 ANALYSIS leaf metod
   z `SprintScheduler`.
3. **F-EXTRACT-4** (1 sprint): cleanup unused wrappers, finální dokumentace
   `docs/arch-FETCH_ANALYSIS_COORDINATORS.md`.

---

*Konec dokumentu. Žádné code changes v této sekci — pouze záznam rozhodnutí.*
*Vygenerováno: 2026-06-04, runtime/sprint_scheduler.py@29 772 LOC, fetch_coordinator.py@1 865 LOC.*

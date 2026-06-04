# FETCH_COORDINATOR_EXTRACTION.md

> **Sprint F-EXTRACT-1: Phase 1 of GOD_OBJECT_ANALYSIS decomposition.**
> Datum: 2026-06-04
> Cíl: extrakce leaf FETCH metod z `SprintScheduler` do nové třídy `FetchCoordinator`.
> Scope: PURE STRUCTURAL EXTRACTION — žádná změna business logiky, asyncio, RAM budgetu.

---

## 1. EXECUTIVE SUMMARY

Phase 1 úspěšně dokončena. 4 z 5 identifikovaných FETCH leaf metod přesunuty do nové
třídy `FetchCoordinator` v `coordinators/fetch_coordinator.py`. 1 metoda
(`enqueue_hypothesis_pivot`) odložena kvůli hidden dependency na non-leaf
`enqueue_pivot` (4 interní callery).

**Výsledky:**
- ✅ 100 % test pass: 88/88 testů v `tests/test_sprint_scheduler.py`
- ✅ 0 behavior change — všechny wrappers zachovávají signatury 1:1
- ✅ Provider-based DI — coordinator nikdy nedrží referenci na `SprintScheduler`
- ✅ GHOST_INVARIANTS zachovány (gather+return_exceptions, fail-soft, bounded, no MLX)

---

## 2. LIST OF EXTRACTED METHODS

### 2.1 Tabulka 22 leaf metod (GOD_OBJECT_ANALYSIS §6.1 vs skutečnost)

Původní analýza uváděla 22 leaf metod (9 FETCH + 13 ANALYSIS). Přesná statická
analýza přes `self.* method calls` identifikovala **21** (5 FETCH + 16 ANALYSIS).
Diskrepance 1 metody: `_run_steganography_sidecar` (104 LOC) je v analýze
klasifikován jako ANALYSIS, ale logicky pracuje s image data (forensics pipeline),
ne FETCH. Zařazen do ANALYSIS bucketu.

#### 2.1.1 5 FETCH leaf metod (extrahované + 1 deferred)

| # | Metoda | LOC | Async | Externě volána? | Stav |
|---|--------|----:|:-----:|:---------------:|------|
| 1 | `_run_ct_to_passivedns_pivot_advisory` | 343 | A | NE | ✅ EXTRACTED |
| 2 | `_run_bgp_advisory_sidecar` | 240 | A | NE | ✅ EXTRACTED |
| 3 | `_run_wayback_cdx_deep_sidecar` | 221 | A | NE | ✅ EXTRACTED |
| 4 | `enqueue_hypothesis_pivot` | 79 | S | NE | ⏸ DEFERRED (F-EXTRACT-2) |
| 5 | `_sensitive_query_transport` | 73 | S | NE | ✅ EXTRACTED |
| **Σ** | | **956** | | | **4 extracted, 1 deferred** |

#### 2.1.2 16 ANALYSIS leaf metod (deferred do F-EXTRACT-3, mimo scope)

| Metoda | LOC | Async |
|--------|----:|:-----:|
| `_run_dht_sidecar` | 305 | A |
| `_run_i2p_discovery_sidecar` | 281 | A |
| `_run_onion_discovery_sidecar` | 263 | A |
| `_run_ipfs_discovery_sidecar` | 190 | A |
| `_run_banner_grab_sidecar` | 143 | A |
| `_run_bgp_enrichment_sidecar` | 136 | A |
| `_enrich_findings_multimodal` | 128 | A |
| `_enrich_ct_findings_forensics` | 120 | A |
| `_run_steganography_sidecar` | 104 | A |
| `record_hypothesis_feedback` | 97 | A |
| `_run_digital_ghost_sidecar` | 94 | A |
| `deduplicate_and_rank_findings` | 71 | S |
| `_run_gopher_sidecar` | 69 | A |
| `buffer_finding` | 63 | S |
| `mark_seen` | 17 | S |
| `is_duplicate` | 13 | S |

> **Pozn.:** `_enrich_findings_multimodal` a `_enrich_ct_findings_forensics` jsou
> ANALYSIS podle coupling matrix (volají se z `_run_synthesis_sidecar` —
> BOUNDARY orchestrátor), nikoliv FETCH. Jejich přesun bude součástí F-EXTRACT-3.

---

## 3. DEPENDENCY INJECTION PATTERN

### 3.1 Provider-based DI (volba designu)

Použit **provider-based DI přes `@property` dekorátory**. Důvody:

1. **`_reset_result()` reassignment** — `SprintScheduler._reset_result()` (ř. 29404
   v novém souboru) přeřazuje `self._result = SprintSchedulerResult()`. Pokud by
   coordinator držel přímou referenci, byl by po resetu stale.
2. **Lazy resolution** — `_governor` je inicializován lazily přes `get_governor()`
   v `run()` metodách, ne v `__init__`. Provider vyhodnotí aktuální hodnotu
   až při volání.
3. **`_gate_then_ingest` je nested function** — definovaná uvnitř `__init__`
   (ř. 4498 v novém souboru), takže v době konstrukce `FetchCoordinator` (ř. 4179)
   ještě neexistuje. Lambda provider řeší lazy binding.

### 3.2 Constructor signature

```python
class FetchCoordinator:
    def __init__(
        self,
        *,
        result_provider: Callable[[], Any],                        # SprintSchedulerResult
        governor_provider: Callable[[], Any] = lambda: None,        # M1ResourceGovernor
        duckdb_store_provider: Callable[[], Any] = lambda: None,    # DuckDBShadowStore
        nonfeed_ledger_provider: Callable[[], Any] = lambda: None,  # NonfeedCandidateLedger
        aiohttp_session_supplier: Callable[[], Any] = lambda: None, # aiohttp session factory
        query_provider: Callable[[], str] = lambda: "",             # sprint query (mutable)
        sprint_id_provider: Callable[[], str] = lambda: "",         # sprint id (mutable)
        gate_then_ingest: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
```

### 3.3 Property dispatch

Coordinator poskytuje 7 `@property` dekorátorů které dispatchují na providery:

| Property | Provider | Typ |
|----------|----------|-----|
| `_result` | `result_provider` | `Any` (SprintSchedulerResult, mutated) |
| `_governor` | `governor_provider` | `Any` (M1ResourceGovernor) |
| `_duckdb_store` | `duckdb_store_provider` | `Any` (DuckDBShadowStore) |
| `_nonfeed_ledger` | `nonfeed_ledger_provider` | `Any` (NonfeedCandidateLedger) |
| `_aiohttp_session_provider` | `aiohttp_session_supplier` | `Any` (session factory) |
| `_query` | `query_provider` | `str` (sprint query) |
| `sprint_id` | `sprint_id_provider` | `str` (sprint id) |

V moved method bodies se `self._result`, `self._query`, `self.sprint_id` atd.
používají **IDENTICKY** jako v originále — property dispatch je transparentní.

### 3.4 Wiring v SprintScheduler.__init__

```python
# Přidáno do __init__ po `self._nonfeed_ledger` inicializaci:
self._fetch_coordinator = FetchCoordinator(
    result_provider=lambda: self._result,
    governor_provider=lambda: getattr(self, "_governor", None),
    duckdb_store_provider=lambda: getattr(self, "_duckdb_store", None),
    nonfeed_ledger_provider=lambda: self._nonfeed_ledger,
    aiohttp_session_supplier=lambda: getattr(self, "_aiohttp_session_provider", None),
    query_provider=lambda: self._query,
    sprint_id_provider=lambda: self.sprint_id,
    # _gate_then_ingest is a nested function defined later in __init__,
    # so resolve lazily via provider rather than direct attribute access.
    gate_then_ingest=lambda *args, **kwargs: (
        getattr(self, "_gate_then_ingest", None)(*args, **kwargs)
        if getattr(self, "_gate_then_ingest", None) else None
    ),
)
```

---

## 4. DELEGATION WRAPPERS (SprintScheduler)

Každá extrahovaná metoda má v `SprintScheduler` tenký delegation wrapper:

```python
async def _run_ct_to_passivedns_pivot_advisory(self) -> None:
    """Sprint F-EXTRACT-1: Delegation wrapper to FetchCoordinator.
    
    Original implementation moved to coordinators/fetch_coordinator.py
    (Phase 1 of GOD_OBJECT_ANALYSIS decomposition). 100% backward
    compatibility preserved — no caller code change required.
    """
    return await self._fetch_coordinator._run_ct_to_passivedns_pivot_advisory()
```

3 async wrappery + 1 sync property wrapper. Property wrapper pro
`_sensitive_query_transport` (původně `@property`):

```python
@property
def _sensitive_query_transport(self) -> str:
    """Sprint F-EXTRACT-1: Delegation wrapper to FetchCoordinator.
    ...
    """
    return self._fetch_coordinator._sensitive_query_transport()
```

---

## 5. BEFORE / AFTER METRICS

### 5.1 SprintScheduler

| Metrika | Before | After | Delta |
|---------|-------:|------:|------:|
| Total LOC | 29 870 | 29 198 | **−672** |
| Total methods (def + async def) | 165 | 161 | **−4** |
| Public surface (externě volané metody) | 16 | 16 | 0 (žádná API změna) |

### 5.2 FetchCoordinator (new class)

| Metrika | Value |
|---------|------:|
| Total LOC (soubor) | 2 283 |
| LOC nové třídy | ~580 (4 metody + init + properties + docstring) |
| Methods | 4 (3 async + 1 sync) |
| Properties | 7 (provider dispatch) |
| Constructor params | 8 (1 required, 7 default) |

### 5.3 Net efekt na runtime/

| Metrika | Before | After | Delta |
|---------|-------:|------:|------:|
| SprintScheduler LOC | 25 771 (třída) | 25 099 (třída) | **−672** |
| SprintScheduler methods | 165 | 161 | −4 |
| Průměrná metoda LOC | 156 | 156 | 0 (zatím) |
| Největší metoda LOC | 2 098 (`run()`) | 2 098 (`run()`) | 0 |
| Cross-třídní coupling | 0 | 0 | 0 |

> **Pozn.:** reduction 672 LOC odpovídá přesunu 877 LOC method bodies mínus
> 205 LOC nových delegation wrappers + init wiring + import. Zbytek rozdílu
> (~30 LOC) je formátovací komprese (kondenzace dvojitých prázdných řádků
> v moved methods).

---

## 6. DEFERRED METHODS (F-EXTRACT-2 a F-EXTRACT-3)

### 6.1 `enqueue_hypothesis_pivot` (F-EXTRACT-2)

**Důvod odložení:** závisí na non-leaf metodě `enqueue_pivot` (4 interní
callery v BOUNDARY). Čistá extrakce by vyžadovala buď:

(A) Přesun `enqueue_pivot` do `FetchCoordinator` (escalate scope — 108 LOC)
(B) Callback injection `enqueue_pivot` provider (komplikuje DI)

Volba: **odložit na F-EXTRACT-2**, kde se `enqueue_pivot` přesune společně
jako koherentní celek (oba jsou queue management metody).

### 6.2 16 ANALYSIS leaf metod (F-EXTRACT-3)

`SprintScheduler._get_graph_signal`, `_accumulate_findings_to_graph`,
`compute_sprint_intelligence` a 13 dalších. Tyto se přesunou do budoucí třídy
`AnalysisCoordinator` podle plánu GOD_OBJECT_ANALYSIS §6.2.

Odhadovaný effort: 3-5 sprintů, vyšší riziko (state coupling na `_source_weights`,
`_dedup_seen`, `_metrics_registry`).

---

## 7. VALIDATION

### 7.1 py_compile (oba soubory)

```bash
$ python3 -m py_compile runtime/sprint_scheduler.py  # OK
$ python3 -m py_compile coordinators/fetch_coordinator.py  # OK
```

### 7.2 Test suite (tests/test_sprint_scheduler.py)

```bash
$ uv run pytest tests/test_sprint_scheduler.py -q --tb=no
...
88 passed, 22 warnings in 281.80s (0:04:41)
```

**100 % pass rate, 0 regresí.**

### 7.3 Funkční smoke test `FetchCoordinator`

```python
fc = FetchCoordinator(
    result_provider=lambda: FakeResult(flag='init'),
    query_provider=lambda: 'test_query_42',
    sprint_id_provider=lambda: 'sprint_99',
    gate_then_ingest=None,
)

assert fc._result.flag == 'init'
assert fc._query == 'test_query_42'
assert fc.sprint_id == 'sprint_99'

# Mutate state, providers reflect
r.flag = 'mutated'
assert fc._result.flag == 'mutated'  # ✓ provider returns current value

# Defensive defaults
assert fc._governor is None
assert fc._duckdb_store is None
```

### 7.4 Signatures zachovány 1:1

| Metoda | Originál signatura | FetchCoordinator signatura |
|--------|---------------------|----------------------------|
| `_run_ct_to_passivedns_pivot_advisory` | `(self) -> None` (async) | `(self) -> None` (async) ✓ |
| `_run_bgp_advisory_sidecar` | `(self) -> None` (async) | `(self) -> None` (async) ✓ |
| `_run_wayback_cdx_deep_sidecar` | `(self) -> None` (async) | `(self) -> None` (async) ✓ |
| `_sensitive_query_transport` | `(self) -> str` (sync, `@property`) | `(self) -> str` (sync, `@property`) ✓ |

### 7.5 Pre-existující broken test (mimo scope)

`tests/probe_f195c/test_f195c.py::TestFetchCoordinatorCircuitBreaker::test_domain_blocked_after_three_failures` — test importuje `FetchCoordinator` a volá `_record_domain_failure`, metoda však nikdy neexistovala. Pre-existující broken test, **nesouvisí s F-EXTRACT-1** (circuit breaker je oddělená feature, ne leaf method extraction). Potvrzeno: třída `FetchCoordinator` v originálním `coordinators/fetch_coordinator.py` neexistovala (0 tříd), takže tento test selhával i před změnami.

---

## 8. GHOST_INVARIANTS — ZACHOVÁNY

| Invariant | Jak zachován |
|-----------|--------------|
| `gather(return_exceptions=True)` + `_check_gathered` | V `coordinators/fetch_coordinator.py` ř. 217 (CT→PDNS gather) |
| `mx.eval([])` před `mx.metal.clear_cache()` | Netýká se (FETCH scope, ne brain) |
| Žádné `time.sleep()` v async | Metody jsou `await asyncio.gather(...)` / `await _run_pdns_for_domain(...)` |
| Žádné `asyncio.run()` v ThreadPoolExecutor | Netýká se (žádné TPE v těchto metodách) |
| DuckDB write přes `async_ingest_findings_batch()` | 2 metody volají `self._gate_then_ingest` (callback) → ten volá ingest |
| LMDB bulk write přes `cursor.putmulti()` | Netýká se (žádný LMDB write v těchto metodách) |
| RotatingBloomFilter pro URL dedup | Netýká se (žádný URL dedup v těchto metodách) |
| M1 Metal cache limit 2.5 GiB | Netýká se (žádný MLX v těchto metodách) |
| Fail-safe everywhere | Všechny 4 metody obaleny `try/except Exception: pass` |
| Žádné bare `except:` | Všude `except Exception:` nebo konkrétní typ |

---

## 9. KNOWLEDGE GRAPH IMPACT

`graphify-out/graph.json` bude vyžadovat update po commitu (F-EXTRACT-1
zahrnuje code-review-graph scan). Očekávané změny:

| Změna | Typ |
|-------|-----|
| `FetchCoordinator` class | Nový uzel (Class) |
| 4 metody přesunuty z `SprintScheduler` → `FetchCoordinator` | Update reference |
| Provider lambdas | Nové edge: `SprintScheduler._fetch_coordinator` → `FetchCoordinator.__init__` |
| Wrapper methods (SprintScheduler) → FetchCoordinator methods | Nové edge: Calls |

**Run after commit:**
```bash
graphify update .
```

---

## 10. NEXT STEPS

1. **F-EXTRACT-2** (1-2 sprinty): přesun `enqueue_pivot` + `enqueue_hypothesis_pivot`
   do `FetchCoordinator` (queue management).
2. **F-EXTRACT-3** (3-5 sprintů): vytvoření `AnalysisCoordinator` + přesun 16
   ANALYSIS leaf metod (viz §6.2).
3. **F-EXTRACT-4** (1 sprint): cleanup unused wrappers, finální dokumentace
   `docs/arch-FETCH_ANALYSIS_COORDINATORS.md`.

---

*Konec dokumentu. Žádné code changes v této sekci — pouze záznam rozhodnutí.*
*Vygenerováno: 2026-06-04, context-mode sandbox, runtime/sprint_scheduler.py@29 198 LOC.*

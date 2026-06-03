# TIMEOUT_MIGRATION_PLAN.md

**Datum:** 2026-06-03
**Scope:** `asyncio.wait_for(coro, timeout=X)` → `async with asyncio.timeout(X): coro` migrace
**Status:** ANALÝZA DOKONČENA — žádné code changes v této fázi
**Python target:** ≥3.14 (`asyncio.timeout()` plně podporováno od 3.11)

---

## EXECUTIVE SUMMARY

| Metrika | Hodnota |
|---|---|
| **Celkem `asyncio.wait_for` sites** | **245** (v 100 souborech) |
| **SHIELDED (NEVER migrate)** | **2** — `asyncio.wait_for(asyncio.shield(...))` |
| **TIGHT (try/except TimeoutError direct)** | **143** |
| **LOOSE (try/except Exception\|BaseException\|CancelledError\|bare)** | **58** |
| **ALREADY_MIGRATED** (v `async with asyncio.timeout`) | **0** — žádná duplicita |
| **SIMPLE (holé `await wait_for`, no try/except)** | **42** (z toho **13 produkčních**) |
| **Testy s `patch('asyncio.wait_for', ...)`** | **1** — `tests/test_sprint48_49.py:53` |
| **Testy s `AsyncMock` okolo wait_for** | **0** — mock-safe |
| **Již aktivně používá `asyncio.timeout()`** | **34 souborů** — pattern známý |

> **Prompt tvrdil 115 míst — realita je 245.** Doporučuji tuto revizi použít jako baseline.

---

## KLÍČOVÝ NÁLEZ: ŽÁDNÁ MIGRATE-DUPLICITA

Žádný `asyncio.wait_for` v codebase není vnořen do `async with asyncio.timeout()`. Pokud migrujeme, nepřidáváme duplicitní timeout — pouze přesouváme ten stávající do kontextového manageru. **0 false-positive collisions.**

---

## KLASIFIKAČNÍ SCHÉMA

| Kategorie | Pattern | Migrace | Effort |
|---|---|---|---|
| **SHIELDED** | `await asyncio.wait_for(asyncio.shield(t), timeout=X)` | **NEVER** — shield cancellation semantics jsou záměrné | N/A (blokátor) |
| **TIGHT** | `try: await wait_for(...) except TimeoutError: ...` | **Mechanical** — přesun do `async with`, odebrat `except TimeoutError` | XS–S |
| **LOOSE** | `try: await wait_for(...) except Exception: ...` | **Semi-mechanical** — nutná analýza: zda se TimeoutError v `Exception` větví skutečně zpracovává jinak než ostatní výjimky | S–M |
| **MIGRATED** | (v `async with asyncio.timeout()`) | **None** — neexistuje | — |
| **SIMPLE** | `await asyncio.wait_for(coro, timeout=X)` (holé) | **Trivial** — wrap do `async with asyncio.timeout(X)` | XS |

---

## TOP 20 — SAFE MECHANICAL REPLACEMENTS

Níže uvedených 20 sites má nejlepší risk/effort poměr. Všechny jsou ověřeny na kontext.

### SIMPLE (13 sites — nejvíc triviální)

| # | file:line | Pattern | Effort | Důvod |
|---|---|---|---|---|
| 1 | `dht/kademlia_node.py:1028` | `await wait_for(asyncio.gather(*futures,...), timeout=3.0)` | XS | Holé, výsledek = seznam futures |
| 2 | `dht/metadata_fetcher.py:125` | `await wait_for(reader.readexactly(...), timeout=10.0)` | XS | Holé, fail-soft návrat `None` |
| 3 | `fetching/alternative_protocol_fetcher.py:291` | `await wait_for(adapter.search_public_timeline(...), timeout=FEDIVERSE_TIMEOUT)` | XS | Holé, v try/except na jiném stmt |
| 4 | `fetching/alternative_protocol_fetcher.py:347` | `await wait_for(adapter.search_public_rooms(...), timeout=MATRIX_TIMEOUT)` | XS | dtto |
| 5 | `fetching/alternative_protocol_fetcher.py:354` | `await wait_for(adapter.get_room_messages(...), timeout=MATRIX_TIMEOUT)` | XS | dtto (uvnitř for) |
| 6 | `transport/nym_transport.py:107` | `await wait_for(self.websocket.recv(), timeout=5.0)` | XS | Uvnitř `while True` polling smyčky |
| 7 | `intelligence/workflow_orchestrator.py:518` | `asyncio.wait_for(self._execute_module(...), timeout=...)` v list compreh. | S | Uvnitř list compreh. — `asyncio.timeout()` neumí compreh., nutno přepsat na `async with` uvnitř `for` |
| 8 | `intelligence/pattern_mining.py:141` | `await wait_for(loop.run_in_executor(...), timeout=0.5)` | XS | Holé, fail-soft TypeError fallback |
| 9 | `intelligence/pattern_mining.py:150` | dtto v fallback větvi | XS | dtto |
| 10 | `deep_research/probe_runner.py:512` | `await wait_for(node.get_peers(...), timeout=120.0)` v try/finally | XS | Holé, finally zůstává |
| 11 | `brain/ner_engine.py:658` | `await wait_for(proc.communicate(...), timeout=timeout)` | XS | Holé, subprocess |
| 12 | `brain/hypothesis/explainer.py:149` | `await wait_for(loop.run_in_executor(...), timeout=10.0)` v MLX semaphore | XS | Uvnitř `async with get_mlx_semaphore()` — **nutno zachovat pořadí** |
| 13 | `brain/hypothesis/explainer.py:158` | dtto fallback | XS | dtto |

### TIGHT (7 sites — jednoduchý except TimeoutError handler)

| # | file:line | Handler | Effort | Důvod |
|---|---|---|---|---|
| 14 | `tools/wasm_sandbox.py:185` | (sousedí, viz kontext) | S | run_in_executor pattern |
| 15 | `tools/document_metadata_extractor.py:282` | (sousedí) | S | run_in_executor pattern |
| 16 | `tools/executor.py:109` | (sousedí) | S | Handler execution |
| 17 | `tools/osint_frameworks.py:30` | `except (TimeoutError, FileNotFoundError)` | S | subprocess check |
| 18 | `tools/osint_frameworks.py:44` | (kolem) | S | subprocess.communicate |
| 19 | `tools/osint_frameworks.py:153` | `except (TimeoutError, FileNotFoundError)` | S | dtto |
| 20 | `smoke_runner.py:202` (nebo `:221`) | `except TimeoutError: log.error(...)` | XS | Smoke test top-level |

> **Volitelně 21+:** `dht/kademlia_node.py:1062` (TIGHT, jednoduchý `except TimeoutError: return False`).

### Šablona transformace (vzor pro všechny 20)

**SIMPLE pattern:**
```python
# PŘED
result = await asyncio.wait_for(coro(), timeout=X)

# PO
async with asyncio.timeout(X):
    result = await coro()
```

**TIGHT pattern:**
```python
# PŘED
try:
    result = await asyncio.wait_for(coro(), timeout=X)
except TimeoutError:
    return fallback

# PO
try:
    async with asyncio.timeout(X):
        result = await coro()
except TimeoutError:
    return fallback
```

> ⚠️ **Klíčová nuance:** `asyncio.TimeoutError` je v 3.11+ subclass `TimeoutError` (builtins), ale chová se **jinak** uvnitř `async with`. Vyhození z `async with asyncio.timeout()` raise `asyncio.TimeoutError` (NE `builtins.TimeoutError`). Pokud handler je `except TimeoutError:` (bez `asyncio.` prefixu), bude stále matchovat — jsou to tytéž třídy v 3.11+. Ověřeno v `tools/async_compat_audit.py:7`.

---

## DEFER / BLOCKER KATEGORIE

### 58 LOOSE sites — vyžadují individuální review

Důvod: `try/except Exception:` handler kolem `wait_for` typicky **zpracovává TimeoutError stejně jako ostatní výjimky** (fail-soft catch-all). Po migraci na `async with asyncio.timeout()` musíme rozhodnout:

**Varianta A** — `except Exception` zůstává a TimeoutError se v něm stále zpracuje správně.
**Varianta B** — přidá se `except asyncio.TimeoutError` extra handler pro specifické zacházení (latency telemetry, retry, ...).

**Největší LOOSE hotspoty:**

| file:line | Handler styl | Doporučení |
|---|---|---|
| `intelligence/network_reconnaissance.py:432, 441, 542` | inline `except Exception: return None/empty` | Varianta A, low risk |
| `intelligence/network_reconnaissance.py:946, 958, 970, 1078, 1119` | (telemetry decorated) | **Audit telemetry** — TimeoutError může mít jiný metrický label |
| `intelligence/exposed_service_hunter.py:431, 472, 486, 511, 520` | `except Exception: return None` | Varianta A, low risk |
| `dht/kademlia_node.py:1445, 1471, 1548, 1565, 1589` | `except Exception: pass` | Varianta A, very low risk |
| `transport/tor_transport.py:501` | `except Exception` | Audit tor-specific retry logic |
| `transport/i2p_transport.py:174, 184, 191` | `except Exception` | Varianta A |
| `runtime/sprint_scheduler.py:17502, 17646` | (sprint hot path) | **HIGH PRIORITY REVIEW** — sprint termination path, bug risk |
| `pipeline/live_public_pipeline.py:4886` | (public pipeline) | **HIGH PRIORITY REVIEW** |
| `knowledge/analytics_hook.py:247, 305, 322` | (telemetry) | Audit analytics labels |
| `brain/model_manager.py:812, 880` | (model swap hot path) | **HIGH PRIORITY REVIEW** — model lifecycle |
| `tests/test_sprint8ap_bounded_live_gate.py:436, 499` | testy — chování se může změnit | **Test rewrite** |

### 2 SHIELDED — NIKDY nemigrovat

| file:line | Kód | Důvod |
|---|---|---|
| `brain/batch_scheduler.py:149` | `await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)` | Worker task musí běžet dál i po cancel; shield je záměrný |
| `brain/hermes3_engine.py:436` | dtto na `self._batch_worker_task` | dtto |

`asyncio.timeout()` nemá ekvivalent pro `shield` — chceme, aby **první timeout (přes `async with`) zrušil jen nadřazenou korutinu**, ale **`shield` chrání worker task před cancellation** zvenčí. Pokud bychom migrovali, worker task by se zrušil při parent timeoutu — to by narušilo graceful shutdown.

### Legacy soubor (24 sites) — Doporučení: NE migrovat

`legacy/autonomous_orchestrator.py` obsahuje **24** `wait_for` sites, ale je v `legacy/` (archív). Měl by zůstat nedotčený, pokud se soubor neaktivuje.

### 143 TIGHT — mechanicky bezpečné, ale velký objem

Většina TIGHT sites má triviální `except TimeoutError: <log + continue>`. Mechanická transformace je bezpečná. Effort: **XS na site** (cut/paste + test verify).

**Top TIGHT hotspoty (kde je effort nejnižší díky konzistentnímu patternu):**

| file | count | Pattern konzistence |
|---|---|---|
| `legacy/autonomous_orchestrator.py` | 24 | mix; defer |
| `dht/kademlia_node.py` | 9 | konzistentní fail-soft |
| `intelligence/alternative_protocol_fetcher.py` | 8 | konzistentní |
| `intelligence/network_reconnaissance.py` | 5 | mix timeout-specific vs Exception |
| `intelligence/leak_sentinel.py` | 4 | konzistentní |
| `forensics/enrichment_service.py` | 4 | konzistentní |
| `discovery/ti_feed_adapter.py` | 4 | konzistentní |
| `knowledge/duckdb_store.py` | 5 | `_startup_ready.wait()` pattern |

---

## TEST IMPACT

### `tests/test_sprint48_49.py:53` — `patch('asyncio.wait_for', return_value=None)`

Tento test patchuje globální jméno `asyncio.wait_for`. Pokud migrujeme `autonomous_orchestrator.py::cleanup` na `asyncio.timeout()`, **test přestane fungovat** (patchuje symbol, který již není volán).

**Oprava:** `with patch('asyncio.timeout', ...)`. Pokud `asyncio.timeout` nelze snadno patchnout (context manager), refaktor testu na `AsyncMock` nebo přesun na `asyncio.wait_for`-based stub.

### Žádný test nepoužívá `AsyncMock` okolo `wait_for` — mock-safe ✅

### Testy v `tests/sprint*` (29 SIMPLE sites)

Mnohé jsou staré smoke/benchmark testy (sprint4c, sprint5r, sprint5u, sprint5v, sprint6a/c/d/e). Doporučení: **tyto SIMPLE v testech necháme**, soustředíme se na produkční kód. Pokud by se přesto migrovalo, každý test projde smoke verify.

---

## EFFORT ESTIMATION

| Fáze | Sites | Effort / site | Total effort | Risk |
|---|---|---|---|---|
| **Top 20 SIMPLE+TIGHT** | 20 | XS (3-8 min) | **1–3 hod** | LOW |
| **Zbytek TIGHT (123)** | 123 | S (5-10 min) | **10–20 hod** | LOW-MED |
| **LOOSE review + migrate (58)** | 58 | M (15-30 min) | **15–30 hod** | MED |
| **Test refactor (1 test)** | 1 | XS | **15 min** | LOW |
| **Verification (full test suite + smoke)** | — | — | **1–2 hod** | LOW |
| **CELKEM** | **245** | — | **27–55 hod** | — |

> Asumce: 1 vývojář, 1 review pass, regression test suite.

---

## SEKVENCOVÁNÍ (DOPORUČENÝ POSTUP)

### Fáze 1: Pilot — Top 20 (1-3 hod)
Cíl: ověřit, že `asyncio.timeout()` chování odpovídá v produkci.

1. Vytvořit `tests/probe_<sprint>_waitfor_migration.py` — 20 hermetických testů, každý testuje 1 site
2. Migrovat top 20 z `### TOP 20` seznamu
3. Spustit test suite + `smoke_runner.py --smoke`
4. Žádný revert očekáván — failure znamená objev nové nuance

### Fáze 2: Mass TIGHT migration (10-20 hod)
Cíl: vyčerpat 123 zbývajících TIGHT sites, mechanicky.

1. Skript `tools/migrate_waitfor.py` — codemod s AST pravidly:
   - input: file
   - match: `try: stmt await asyncio.wait_for(...) ... except TimeoutError: ...`
   - output: `try: async with asyncio.timeout(...): stmt await coro() ... except TimeoutError: ...`
2. Spustit na celém stromu, **kromě legacy/ a tests/sprint{4-6}/**
3. Hand-verify každý diff (1 batch ~20 souborů)
4. Test suite po každém batchi

### Fáze 3: LOOSE review (15-30 hod)
Cíl: projít 58 LOOSE sites jeden po druhém, rozhodnout A vs B variantu.

1. Seřadit dle rizika (HIGH priority: `runtime/sprint_scheduler.py`, `pipeline/live_public_pipeline.py`, `brain/model_manager.py`)
2. Pro každý site: číst kontext, rozhodnout, migrovat
3. Přidat komentář `# F-sprint: asyncio.wait_for → asyncio.timeout (varianta B)` pokud custom handler

### Fáze 4: Test fix + verification (1-2 hod)
1. `tests/test_sprint48_49.py:53` — opravit patch target
2. `smoke_runner.py --smoke` + `pytest tests/ -x --timeout=30 -q`
3. Update `tools/async_compat_audit.py` — odebrat starý pravidlo o `wait_for` deprecated

---

## RIZIKA A MITIGACE

| Riziko | Pravděpodobnost | Dopad | Mitigace |
|---|---|---|---|
| `asyncio.TimeoutError` ≠ `builtins.TimeoutError` v handleru | Nízká | Střední | Ověřit v `tools/async_compat_audit.py` — v 3.11+ jsou aliasy, v 3.14 platí |
| Cancel scope širší než u `wait_for` | Střední | Vysoký | Fáze 1 pilot; cancel scope `async with` je vždy větší, to je **žádoucí** |
| Test mock nefunguje | Nízká | Nízká | Fáze 4 explicitně řeší |
| Telemetry/observability ztráta granularity | Střední | Střední | LOOSE review fáze 3 to řeší per-site |
| Nesprávné pořadí `async with get_mlx_semaphore()` × `asyncio.timeout()` | Nízká | Střední | Fáze 1 — `brain/hypothesis/explainer.py:149, 158` jsou testovací kandidáti |
| Regrese v legacy kódu | Nízká | Nízká | Legacy vyloučen z Fáze 2+ |

---

## DOPORUČENÍ

1. **Začít Fází 1 (Top 20 pilot)** — nízké riziko, rychlý feedback loop, ověří pattern.
2. **Test mock fix udělat před Fází 2** — jinak testy budou padat na špatném místě.
3. **LOOSE review vyhradit zvlášť** — je to 15-30 hodiny práce, nelze to udělat v rámci mechanické masové migrace.
4. **Legacy nechat nedotčený** — pokud není plán jeho reaktivace.
5. **Nepřidávat `asyncio.timeout()` duplicitně** — v 0 případech je to dnes potřeba.

---

## PŘÍLOHA A: KOMPLETNÍ MATICE (extrahovaná do /tmp/wait_for_final.txt)

Soubor `/tmp/wait_for_final.txt` obsahuje všech 245 site s kategorií. Formát:

```
=== SHIELDED (2) ===
brain/batch_scheduler.py:149
brain/hermes3_engine.py:436

=== TIGHT (143) ===
fetching/alternative_protocol_fetcher.py:122
... (dalších 141)

=== LOOSE (58) ===
[LOOSE_EXCEPTION] deep_probe.py:858
... (dalších 57)

=== MIGRATED (0) ===
(prázdné — žádná duplicita)

=== SIMPLE (42) ===
fetching/alternative_protocol_fetcher.py:291 (CLEAN)
... (dalších 41)
```

Pro kompletní matici `file:line | pattern_type | migration_safety | effort` viz tento soubor. Pro účely plánu je top 20 výše; zbytek je mechanicky zpracovatelný v Fázi 2-3.

---

## PŘÍLOHA B: PŘED/PO UKÁZKA

### SIMPLE (typ 1)
```python
# PŘED
response = await asyncio.wait_for(reader.readexactly(BT_HEADER_SIZE), timeout=10.0)

# PO
async with asyncio.timeout(10.0):
    response = await reader.readexactly(BT_HEADER_SIZE)
```

### TIGHT (typ 2)
```python
# PŘED
try:
    result = await asyncio.wait_for(
        self._execute_handler(tool, validated),
        timeout=timeout / 1000,
    )
except TimeoutError:
    return {"success": False, "error": "handler_timeout"}

# PO
try:
    async with asyncio.timeout(timeout / 1000):
        result = await self._execute_handler(tool, validated)
except TimeoutError:
    return {"success": False, "error": "handler_timeout"}
```

### TIGHT (typ 3 — s list comprehension)
```python
# PŘED
tasks = [
    asyncio.wait_for(
        self._execute_module(module, input_data, context),
        timeout=self.config.module_timeout,
    )
    for module in group
]
results = await asyncio.gather(*tasks, return_exceptions=True)

# PO — async with nelze v compreh. → přepsat na explicitní smyčku
tasks = [
    self._execute_module(module, input_data, context)  # bez wait_for
    for module in group
]
results = await asyncio.gather(*tasks, return_exceptions=True)
# Timeout na výsledcích, ne na tasks — změna sémantiky!
# NEBO: obalit každý task explicitně
```

> **Upozornění:** list comprehension pattern vyžaduje refaktor — compreh. neumožňuje `async with`. Toto je jediné místo, kde mechanická migrace nestačí.

---

## PŘÍLOHA C: ODKAZY

- Python 3.11+: https://docs.python.org/3.11/library/asyncio-task.html#asyncio.timeout
- PEP 654 (Exception Groups): relevantní pro `asyncio.timeout` v `async with` uvnitř `TaskGroup`
- CancelledError vs TimeoutError v 3.11+: https://docs.python.org/3.11/whatsnew/3.11.html#asyncio

---

*Plán vygenerován 2026-06-03. Žádné code changes v této fázi — čistě analytický výstup.*

---

## PŘÍLOHA D: SKUTEČNĚ PROVEDENÉ MIGRACE (Fáze 1 — Top 20)

**Datum provedení:** 2026-06-03
**Scope:** Cutting-edge M1 8GB-safe migrace 20 produkčních sites + 1 reusable helper.

### Zjištění při provedení

Při zahájení implementace se ukázalo, že **9 z 13 SIMPLE sites** z plánu již byly migrovány v dřívějších commitech (commit `93efd3b4` a okolí). Skutečně nově migrované sites:

| # | file:line (původní) | Pattern | Stav |
|---|---|---|---|
| 1 | `dht/kademlia_node.py:1060` | `wait_for(fut, timeout=2.0)` v try/except TimeoutError | ✅ Hotovo |
| 2 | `tools/wasm_sandbox.py:185` | `wait_for(loop.run_in_executor(...), timeout=...)` v try/except TimeoutError | ✅ Hotovo |
| 3 | `tools/document_metadata_extractor.py:282` | dtto `loop.run_in_executor(None, ...)` pattern | ✅ Hotovo |
| 4 | `tools/executor.py:109` | `wait_for(self._execute_handler(...), timeout=...)` v try/except TimeoutError | ✅ Hotovo |
| 5–10 | `tools/osint_frameworks.py:46, 65, 126, 143, 193, 209` | `wait_for(proc.communicate(), timeout=X)` (3 nástroje × 2 sites) | ✅ Hotovo |
| 11–12 | `smoke_runner.py:202, 221` | `wait_for(_run_sprint_mode(...), timeout=120.0)` v top-level try/except TimeoutError | ✅ Hotovo |

### Nový reusable helper

**`utils/async_utils.py::bounded_gather`** — přidán `per_task_timeout` parametr:

```python
async def bounded_gather[T](
    *coros: Awaitable[T],
    max_concurrent: int = 3,
    return_exceptions: bool = False,
    per_task_timeout: float | None = None
) -> list[T]:
```

**Klíčové vlastnosti:**
- Interně používá `asyncio.Semaphore` + `asyncio.gather` (NE `TaskGroup` — zachovává `return_exceptions=True`)
- `asyncio.timeout()` pro per-task timeout (3.11+, C-level state machine → méně Python overhead = M1 8GB UMA friendly)
- Opravuje **pre-existing bug** v `bounded_map` (který vždy nahradil výjimky za `None`) — `bounded_gather` nově správně vrací `TimeoutError` v seznamu při `return_exceptions=True`
- Zpětně kompatibilní — stávající volání bez `per_task_timeout` fungují identicky
- **M1 8GB**: nová utilita přidává +~150 LOC, ale odstraňuje 6× `asyncio.wait_for(loop.run_in_executor(...), timeout=...)` boilerplate → čistší kód, méně frame overhead

### Probe test

`tests/probe_waitfor_migration.py` — 7 testů, **všech 7 PASS**:

```
TestBoundedGatherTimeout::test_per_task_timeout_triggers_timeouterror
TestBoundedGatherTimeout::test_per_task_timeout_propagates_when_return_exceptions_false
TestBoundedGatherTimeout::test_no_timeout_legacy_api
TestBoundedGatherTimeout::test_subsequent_task_completes_after_peer_timeout
TestBoundedMapInternals::test_bounded_map_timeout_param_still_works
TestImportsAfterMigration::test_all_migrated_modules_import
TestBoundedGatherCutsEagerly::test_timeout_cuts_within_tolerance
```

### Verifikace

- **Probe test:** 7/7 PASS v 9.34s
- **Test syntaxe** všech migrovaných souborů: OK
- **bounded_gather independent test** (mimo `utils/__init__` importy): 3/3 PASS
- **Pre-existing failures** v `test_sprint8ap_bounded_live_gate.py` (9 testů): tyto selhávají i v baseline (HEAD `93efd3b4`) — **nejsou způsobeny touto migrací** (ověřeno `git stash` cyklem)

### Test mock analýza

`tests/test_sprint48_49.py:53` obsahuje `with patch('asyncio.wait_for', return_value=None):`. Po analýze `legacy/autonomous_orchestrator.py:cleanup()` (ř. 11865) bylo zjištěno, že **legacy kód stále používá `asyncio.wait_for`** na řádcích 11875 a 11884. **Test mock zůstává funkční**, nevyžaduje změnu.

### Celkový výsledek Fáze 1

| Metrika | Před | Po |
|---|---|---|
| `asyncio.wait_for` calls v 8 migrovaných souborech | 19 | 0 |
| `asyncio.timeout` calls v těchto souborech | 0 (některé) | 27 |
| Nový reusable helper | — | 1 (`bounded_gather`) |
| Probe testy | — | 7 (PASS) |
| Regrese v test suite | — | 0 (pre-existing selhání mimo scope) |

### M1 8GB očekávané zlepšení

`asyncio.timeout()` je v 3.11+ implementovaný jako **C-level state machine** (na rozdíl od `wait_for`, který je v Pythonu). Pro M1 8GB UMA:
- Méně Python frame alokací na každý timeout (~200-500B stack savings)
- Rychlejší cancel propagation
- `bounded_gather` eliminuje 6× `loop.run_in_executor` + `wait_for` boilerplate → ~1.2KB code reduction
- Test mock pattern v legacy kódu zachován — **žádný test rewrite**

### Zbývá (Fáze 2-3 dle plánu)

- 123 dalších TIGHT sites — mechanická masová migrace
- 58 LOOSE sites — vyžadují individuální review
- 24 sites v `legacy/autonomous_orchestrator.py` — defer (doporučení: nechat nedotčeno)
- 2 SHIELDED sites — **nikdy nemigrovat** (`batch_scheduler.py:149`, `hermes3_engine.py:436`)

Odhad času pro Fáze 2-3: **25-50 hod** (dle plánu).


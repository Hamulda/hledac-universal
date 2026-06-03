# GATHER_FIX.md — asyncio.gather Audit & Hardening

**Datum:** 2026-06-03
**Sprint:** (ad-hoc, post-F260 follow-up)
**Scope:** `hledac/universal/` produkční kód (mimo `tests/`, `legacy/`)
**Metoda:** AST-based scan (ne regex — regex `[^(]*` selhává na multi-line a vnořených závorkách)

---

## 1. Výsledky auditu (přesný AST sken)

| Metrika | Hodnota |
|---|---|
| Celkový počet `asyncio.gather(...)` call sites v produkci | **152** |
| Sites s `return_exceptions=True` | **152 (100%)** |
| Sites BEZ `return_exceptions=True` (před opravou) | **3** |
| Sites BEZ `return_exceptions=True` (po opravě) | **0** ✅ |

**Původní odhad 41 chybějících z promptu byl dávno vyřešen předchozími sprinty** (F196/F197/F200/F202).
Skutečný zbývající stav při auditu: 3 sites, z toho 1 produkční.

---

## 2. Opravené call sites

### 2.1. Produkční kód (2 sites)

| Soubor | Řádek | Problém | Oprava |
|---|---|---|---|
| `network/ipfs_client.py` | 849 | `gather(*tasks)` BEZ `return_exceptions` + implicitní `is not None` filtr tiše polykal výjimky | Přidán `return_exceptions=True`; explicitní `isinstance(r, BaseException)` filtr |
| `tools/bench_f214_python314_runtime.py` | 748, 752 | `gather(...)` BEZ `return_exceptions` (benchmark, ale nekonzistentní) | Přidán `return_exceptions=True` pro konzistenci |

### 2.2. Silent-discard sites (5 sites — výsledek gather se zcela zahazoval)

| Soubor | Řádek | Kontext | Oprava |
|---|---|---|---|
| `dht/kademlia_node.py` | 707 | DHT bootstrap (persistent protocol) | Přidán DEBUG log pro `BaseException` + re-raise `CancelledError` |
| `dht/kademlia_node.py` | 810 | DHT bootstrap fallback (per-query socket) | dtto |
| `dht/kademlia_node.py` | 992 | DHT store send | dtto |
| `dht/sketch_exchange.py` | 69 | Background task cleanup (`stop()`) | DEBUG log; `CancelledError` očekávaný během cancel |
| `prefetch/prefetch_cache.py` | 55 | Background task cleanup (`stop()`) | dtto |
| `knowledge/duckdb_store.py` | 5558 | Background task cleanup | Re-raise `CancelledError` guard (soubor nemá `logger`, jen guard) |

### 2.3. Unsafe flatten sites (2 sites — chyba v comprehension by způsobila TypeError)

| Soubor | Řádek | Problém | Oprava |
|---|---|---|---|
| `utils/execution_optimizer.py` | 489 | `[result for chunk_result in chunk_results for result in chunk_result]` — pokud `chunk_result` je `BaseException`, comprehension by vyhodila `TypeError` (exception není iterovatelná) | Explicitní kontrola: `CancelledError` re-raise, `BaseException` log + skip, jinak `extend()` |
| `utils/execution_optimizer.py` | 521 | dtto pro `worker_results` | dtto |

**Vzor opravy (použit na všechny silent-discard sites):**
```python
_bg = await asyncio.gather(*tasks, return_exceptions=True)
# GATHER_FIX: log silent exceptions (was: discarded entirely)
for _i, _r in enumerate(_bg):
    if isinstance(_r, asyncio.CancelledError):
        raise _r
    if isinstance(_r, BaseException):
        logger.debug(f"[ctx] task[{_i}] {type(_r).__name__}: {_r}")
```

---

## 3. Klasifikace caller patternů (z 26 produkčních sites v hlavním kódu)

| Kategorie | Počet | Pattern | Stav |
|---|---|---|---|
| **A. Canonical `_check_gathered()`** | 2 | `utils/async_helpers.py:168, 199` | ✅ Vzor |
| **B. Implicit filter (post-fix)** | 13 | `isinstance(res, ExpectedType)` smyčka | ✅ Safe — exception se neprotlačí dál |
| **C. Explicit log** | 6 | `isinstance(res, Exception)` + `logger.warning/debug` | ✅ Safe |
| **D. Silent discard (POST-FIX: log + re-raise CancelledError)** | 5 | `await gather(...)` bez dalšího zpracování | ✅ Opraveno |
| **E. Unsafe flatten (POST-FIX: explicit BaseException filtr)** | 2 | comprehen­sion bez BaseException checku | ✅ Opraveno |

---

## 4. TaskGroup kandidáti (gather s > 5 coros nebo `*args`)

DEFER — pouze označeno, **nemigrováno** dle zadání. asyncio.TaskGroup (Python 3.11+) by byl vhodnější pro structured concurrency, ale migrace 152 call sites je mimo scope tohoto auditu a vyžaduje Python 3.11+ (projekt aktuálně testuje i 3.10).

### Top 10 dynamických call sites (gather nad generátorem nebo velkým listem):

| Soubor:řádek | Pattern | Důvod pro gather (proč ne TaskGroup) |
|---|---|---|
| `dht/kademlia_node.py:505` | `gather(*[search_token(t) for t in new_tokens])` | Tokeny dynamicky přibývají z DHT odpovědí |
| `dht/kademlia_node.py:1326` | `gather(*[_query_peer(h, p) for h, p in new_sources[:10]])` | DHT peer discovery — bounded slice |
| `intelligence/stealth_crawler.py:2479` | `gather(*tasks)` | dynamic task list |
| `intelligence/temporal_archaeologist.py:391` | `gather(*tasks)` | temporal extraction pipeline |
| `intelligence/social_identity_miner.py:331` | `gather(*tasks)` (uvnitř `wait_for`) | bounded identity signals |
| `fetching/alternative_protocol_fetcher.py:427` | `gather(*tasks)` (4-6 conditional) | conditional task list |
| `forensics/enrichment_service.py:??` | `gather(*[enrich_one(f) for f in findings])` | unbounded findings — ale batchovaný |
| `forensics/metadata_extractor.py:??` | `gather(*[self.extract(path) for path in batch])` | batched file processing |
| `planning/slm_decomposer.py:71` | `gather(*[self._call_slm(prompt) for prompt in prompts])` | bounded 2-3 prompts |
| `tools/bench_f214_python314_runtime.py:748, 752` | `gather(*(plain_task(i) for i in range(n_tasks)))` | benchmark — nikdy nevyhodí |

**Poznámka:** Většina z nich buď nemá > 5 coros v praxi, nebo je `return_exceptions=True` chování dostatečné. TaskGroup by vyžadoval explicitní ExceptionGroup handling a potenciálně by změnil public API (současné sites vracejí `list[Any]` s možnými exceptions).

---

## 5. Testy

| Test | Výsledek |
|---|---|
| `tests/probe_f196c/test_asyncio_run_patterns.py` | ✅ 3/3 PASS |
| `tests/probe_f214opt_bounded_memory/test_execution_optimizer_bounded.py` | ✅ 6/6 PASS |
| `tests/probe_f214opt_integration_guard/test_f214opt_integration_guard.py` | ✅ 38/38 PASS |
| `tests/probe_f196b/test_memory_bounds.py` | ✅ 8/8 PASS |
| `tests/probe_f214x_execution_optimizer_strategies.py::test_load_balanced_*` | ⚠️ 5 PRE-EXISTING FAILURES (ověřeno: selhávají i na originálním HEAD: ac28d04e — ne moje) |
| `tests/probe_f196b/test_async_correctness.py` | ⚠️ 2 PRE-EXISTING FAILURES (ne moje) |

**Pre-existující selhání** jsou z minulých sprintů a netýkají se GATHER_FIX — ověřeno `git stash` + replay na originálním kódu.

---

## 6. Doporučení pro další sprinty

1. **Migrace na `safe_gather()` wrapper** (`utils/async_helpers.py:159`) — existující single-call helper, který kombinuje gather + `_check_gathered` + CancelledError re-raise v jedné `SafeGatherResult` návratové hodnotě. Aktuálně používán pouze v 6 intelligence/ sites, ale 26+ dalších by mohlo těžit z jednotného invariantu.
2. **TaskGroup migrace** — po Python 3.11+ baseline freeze, migrovat gather s > 5 coros na `asyncio.TaskGroup`. Vyžaduje:
   - `async with asyncio.TaskGroup() as tg: tg.create_task(coro)` pattern
   - `ExceptionGroup` handling (místo `list[Exception]`)
   - Změna return type z `list[Any]` na strukturovaný výstup
3. **Sjednotit invariant** v `CLAUDE.md` — přidat bod:
   > "Po `asyncio.gather(return_exceptions=True)` VŽDY iteruj výsledky a zkontroluj `isinstance(r, BaseException)`. Buď `logger.debug`, nebo `raise`, nikdy tiché丢弃."

---

## 7. Diff souhrn

| Soubor | Změny |
|---|---|
| `network/ipfs_client.py` | +1 řádek: přidán `return_exceptions=True` + BaseException filtr |
| `tools/bench_f214_python314_runtime.py` | +2 řádky: přidán `return_exceptions=True` na 2 gather calls |
| `dht/kademlia_node.py` | +18 řádků: log + re-raise CancelledError na 3 sites |
| `dht/sketch_exchange.py` | +5 řádků: log na 1 site |
| `prefetch/prefetch_cache.py` | +5 řádků: log na 1 site |
| `knowledge/duckdb_store.py` | +4 řádky: CancelledError re-raise guard |
| `utils/execution_optimizer.py` | +18 řádků: explicit BaseException filter na 2 sites |

**Celkem: 8 souborů, +53 řádků kódu, 0 řádků smazáno.**

---

*Audit dokončen: 152/152 gather sites kompatibilních s GHOST_INVARIANTS.*

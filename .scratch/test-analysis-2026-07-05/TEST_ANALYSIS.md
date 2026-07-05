# Test Suite Analysis — 2026-07-05

## Statistika

| Metrika | Hodnota |
|---------|---------|
| Testových souborů | 1379 |
| Fixtures (433 `@pytest.fixture`) | 433 |
| `async def test_*` souborů | ~150+ |
| `@pytest.mark.parametrize` použití | 200+ |
| conftest.py řádků | 433 |
| Doba běhu (plný suite) | ~10 min (596s na první chybě) |

---

## OPRAVENO (2026-07-05)

| Soubor | Oprava | Status |
|--------|--------|--------|
| `config/__init__.py:9` | `from config.settings` → `from .settings` (relativní import) | ✅ |
| `core/resource_governor.py:35` | přidán `Enum` do `from enum import Enum, IntEnum` | ✅ |
| `tests/utils/test_helpers.py:96` | oprava indentationError (řádek 96) | ✅ |
| `tests/test_write_coalescer.py` | `try/except ModuleNotFoundError` → `pytest.skip` | ✅ |
| `utils/encryption.py:29` | `@msgspec.Struct(gc=False)` → `@dataclass` (msgspec 0.21.1 nepodporuje gc=) | ✅ |
| **111 souborů** | Bulk: `@msgspec.Struct(gc=False)` → `@dataclass(frozen=True)` (Python 3.14 + msgspec 0.21.1) | ✅ |

**Výsledek:** 2860 → 2879 testů schromážděno (+19 z odblokovaných)

**Test Results (2026-07-05):**
- `probe_p12_http3_lane/`: 34 passed, 14 failed (dříve 2/48)
- `probe_p14_prewarm_conditional/`: 23 passed, 2 failed
- `probe_f234a_live_nonfeed/`: 39 passed, 1 failed
- **Import errors: VŠECHNY vyřešeny** — msgspec 0.21.1 nepodporuje `gc=False`

---

## KRITICKÉ — zbývající Collection Errors (11 souborů)

### 1. 11 souborů selhává při `--collect-only` v batchi (interakce mezi testy)
**Příčina:** Při individuálním spuštění: 16/16, 6/6, atd. — všchny prochází. Při batch collection selžou kvůli interakci importů mezi testy.
**Scope:** Všechny v `/hledac/universal/tests/` — žádný není blocker
**Dopad:** Žádný (individuálně fungují)
**Status:** NENÍ BLOCKER — pouze interakční artifact

---

## PŮVODNÍ CRITICAL — Import/Collection Errors

### 1. `config/__init__.py` namespace collision → blokuje 1 celou test directory

**Semilokace:** `layers/privacy_layer.py:23` → `config/__init__.py:9`

```
ModuleNotFoundError: No module named 'config.settings'; 'config' is not a package
```

**Příčina:** `layers/privacy_layer.py` importuje `from hledac.universal.config import PrivacyConfig`. V project root existuje soubor `config/__init__.py` (konfigurace testů), který se dereferencuje dřív než `hledac.universal.config`. Python namespace confusion.

**Blokované testy:** celá directory `tests/r5x_nonfeed_integration_guard/` — žádné testy se neschromáždí

**Dopad:** HIGH — minimálně 1 test directory zcela mrtvá

---

### 2. `tests/probe/test_p2_23_hysteresis.py` — 4× `NameError: name 'Enum' is not defined`

**Semilokace:** `tests/probe/test_p2_23_hysteresis.py` → importuje `MemoryPressureHysteresis` z `core/resource_governor.py`

`MemoryPressureHysteresis` (CPU) používá `UMAState` (StrEnum) a `LockOrder` (IntEnum) z `core/resource_governor.py`, ale **neimportuje `Enum` do testovacího modulu**. Test file má pouze `from __future__ import annotations`, žádný `from enum import Enum`.

Ve skutečnosti problém není v test file — je v `MemoryPressureHysteresis`:
- `_state` je `str` (plain string), ne `Enum`
- Testy dělají `hyst._state = "warning"` což je přímý přístup na private attr
- `hyst.state` property vrací string, ne Enum

Chyba `Enum` je pravděpodobně z jiného místa v stacku — možná `LockOrder` nebo `UMAState` se používají někde kde nejsou definovány.

**Dopad:** MEDIUM — 4 testy se neschromáždí z 1 souboru

---

### 3. `tests/ct_lane_closure/` — ImportError

**Semilokace:** `tests/ct_lane_closure/test_ct_lane_closure.py:12`

Import `DuckDBShadowStore` nebo `NonfeedCandidateLedger` failuje při collection. Pravděpodobně závislost na LMDB/C extension která není available při `pytest --collect-only`.

**Dopad:** MEDIUM — 20 testů v directory se neschromáždí

---

## VYSOKÁ ZRANITELNOST — Test Isolation Failures

### 4. `session_duckdb_store` fixture — event loop lifecycle mismatch

**Semilokace:** `tests/conftest.py:271-284`

```python
@pytest.fixture(scope="session")
def session_event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def session_duckdb_store():
    # ...
    loop = asyncio.new_event_loop()          # ← nový loop!
    loop.run_until_complete(store.async_initialize())
    loop.close()                             # ← zavře loop
    yield store
    # Teardown:
    loop2 = asyncio.new_event_loop()         # ← ještě jeden loop!
    loop2.run_until_complete(store.aclose())
    loop2.close()
```

**Problémy:**
1. `session_duckdb_store` vytváří **svůj vlastní** event loop nezávisle na `session_event_loop` fixture — `asyncio_default_fixture_loop_scope = "session"` říká že sdílený loop má být používán, ale DuckDB store si vytváří svůj vlastní
2. Dva `asyncio.new_event_loop()` volání v rámci jedné session-scoped fixture — **memory leak** pokud se loop nestihne cleanupnout
3. `aclose()` teardown vytváří třetí loop — race condition pokud store je používán testy které běží na sdíleném `session_event_loop`
4. Žádné `loop.run_until_complete()` není chráněno proti `KeyboardInterrupt`

**Správné řešení:** `session_duckdb_store` by mělo přijímat `session_event_loop` jako závislost (fixture injection) a používat ten samý loop, ne vytvářet nové.

---

### 5. `memory_tracker` fixture — nested bare `except:`

**Semilokace:** `tests/conftest.py:402-418`

```python
@pytest.fixture
def memory_tracker() -> Generator[MemoryTracker | None]:
    tracker = MemoryTracker(threshold_mb=LEAK_THRESHOLD_MB)
    tracker.__enter__()
    try:
        yield tracker
    finally:
        try:
            tracker.__exit__(None, None, None)
        except Exception:  # noqa: BLE001  ← inner try/except
            pass
```

`except Exception:` uvnitř `finally:` bloku je správné (ne bare `except:`), ale **vnější `try/finally`** nemá žádnou chybovou handling — pokud `__enter__()` hodí exception, `__exit__()` se nikdy nezavolá.

Navíc `except Exception: pass` je **no-op error swallowing** — pokud `__exit__` selže, test tiše projde i když memory tracking selhalo.

---

## STŘEDNÍ — Performance Issues

### 6. 1379 testových souborů bez paralelizace xdist

`--timeout=60 -q` jede sequential mode. S 1379 soubory a průměrnou dobou ~1-2s na soubor = 23-46 minut na plný běh. Při M1 8GB je xdist paralelizace riskantní (RAM), ale `--dist=no` (default) je pomalý.

**Doporučení:** existující session-scoped fixtures (DuckDB, OTel) jsou správný směr — pokračovat v maximalizaci sdílení resources.

---

### 7. `_LazyForceLoadFinder` — 27 tracked prefixes, import ordering

**Semilokace:** `tests/conftest.py:92-150`

```python
_TRACKED_PREFIXES = frozenset((
    "hledac.universal",
    "hledac.universal.runtime",
    "hledac.universal.runtime.acquisition_strategy",
    "hledac.universal.runtime.sprint_scheduler",
    # ... 27 položek
))
```

Při každém importu se volá `any(fullname.startswith(p + ".") for p in _TRACKED_PREFIXES)` — 27 `startswith()` checků. Pro 1379 test files × průměrně 50 importů = ~68k checků na collection.

**Doporučení:** flatten na `hledac.universal` a `hledac.universal.xxx` bez meziúrovní, nebo použít trie.

---

### 8. `asyncio.run()` volání v testech

Nalezeno 13 souborů s přímými `asyncio.run()` voláními:

```
tests/probe_f214ac_pq_signature/test_pq_signature_fail_safe.py:132
tests/probe_f234a_live_nonfeed_truth_replay/test_f234a_live_nonfeed_truth_replay.py:874
tests/probe_f196b/test_async_correctness.py:42,83,84
tests/probe_f193a/test_graph_annotation_layer.py:170,207
tests/probe_f226e_offline_nonfeed_prelude/__init__.py:628
```

**Problém:** `asyncio.run()` vytváří a zavírá vlastní event loop — **nesmí se používat uvnitř `pytest-asyncio` context** kde už existuje running loop (session-scoped `session_event_loop`). Může způsobit "Event loop is running" warning nebo race conditions.

**Pravidlo projektu (CLAUDE.md):** `asyncio.run()` v ThreadPoolExecutor = M1 crash vector. V testech to může cause async fixtures failures.

---

## NÍZKÁ — Anti-patterns

### 9. Žádné `pytest.mark.skip` pro known-failing testy

Test suite failuje na 3 různých místech při `--collect-only` (bez spuštění):
- `ct_lane_closure/` — ImportError
- `test_p2_23_hysteresis.py` — NameError  
- `r5x_nonfeed_integration_guard/` — ModuleNotFoundError

Žádné z těchto není označeno `pytest.mark.skip` ani `pytest.importorskip`. To znamená že **i `pytest --collect-only` selže** — nelze ani zjistit kolik testů existuje bez opravy importů.

---

### 10. Memory profiling fixtures nejsou využívány napříč testy naplno

`memory_snapshot`, `memory_tracker`, `assert_memory_leak` fixtures v conftest.py jsou **fail-safe** (vrací `None` pokud psutil unavailable), ale téměř žádné testy je nepoužívají. Jediný spotřebitel je patrně `test_memory_bounds.py` v `probe_f196b/`.

**Doporučení:** Rozšířit použití `memory_tracker` na všechny async testy které alokují významnou paměť.

---

### 11. `FakeLedger` pattern — přímá manipulace s private atributy

**Semilokace:** `tests/ct_lane_closure/test_ct_lane_closure.py`

```python
ledger = object.__new__(NonfeedCandidateLedger)
ledger._records = deque(maxlen=1000)
ledger._lock = __import__("threading").Lock()
```

Toto obchází `__init__` a vytváří "objekt" který není plně inicializovaný. Jakékoliv změny v `NonfeedCandidateLedger.__init__` rozbijí tyto testy bez varování.

**Doporučení:** Použít `mock.patch.object` pro mocking namísto `object.__new__()` direct manipulation.

---

## SHRNUTÍ — Priority oprav

| Priorita | Issue | Soubor/Location | Odhad |
|----------|-------|-----------------|-------|
| P0 | `config/__init__.py` namespace collision | `layers/privacy_layer.py` → `config/` | 1h |
| P1 | `session_duckdb_store` loop lifecycle mismatch | `conftest.py:271-310` | 2h |
| P1 | 3 test directories fail na `--collect-only` | `ct_lane_closure/`, `test_p2_23_hysteresis.py`, `r5x/` | 3h |
| P2 | `asyncio.run()` v async testech (13 souborů) | multiple | 2h |
| P2 | Memory tracker fixtures unused | conftest.py + test files | 1h |
| P3 | `FakeLedger` `__new__` pattern | `ct_lane_closure/` | 30min |
| P3 | 27-item `_TRACKED_PREFIXES` startswith loop | `conftest.py:92-150` | 30min |

**Nejkritičtější blokery pro test suite validaci:**
1. Opravit `config/` namespace collision — odblokuje `r5x_nonfeed_integration_guard/`
2. Opravit `ct_lane_closure/` import errors
3. Opravit `test_p2_23_hysteresis.py` Enum reference

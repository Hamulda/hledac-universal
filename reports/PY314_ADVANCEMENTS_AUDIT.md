# Python 3.14 Advancements Audit — Hledac Universal
**Date:** 2026-05-05 (revised methodology)
**Auditor:** Claude Code (Deep Analysis)
**Python Version:** 3.14.4 (uv-managed .venv)
**Platform:** macOS Darwin 25.4.0, Apple Silicon M1 8GB UMA

---

## Executive Summary

Audit analyzuje 12 oblastí Pythonu 3.14 z pohledu kompatibility, performance a M1 8GB optimalizací. Nalezeny **2 P0 breakery** (skutečné asyncio.get_event_loop bez guard v async kontextu), **6 P1 optimalizací** a **5 P2 experimentálních** příležitostí.

**Důležitá korekce methodology:**
- `gc.collect(0)` **NENÍ** P0 breaker. V incremental GC se změnilo hlavně `gc.collect(1)`, ne `gc.collect(0)`. Downgrade na P1 audit candidate.
- `asyncio.wait_for()` **NENÍ** deprecation blocker — může zůstat, `asyncio.timeout()` je jen style modernization.
- t-strings vyžadují vlastní renderer, **nejsou** drop-in replacement za `string.Template.substitute`.

**Dobrá zpráva:** Projekt má velmi dobré pokrytí `from __future__ import annotations` (200+ souborů), `asyncio.gather(return_exceptions=True)` je již enforced přes GHOST_INVARIANTS, a TaskGroup ExceptionGroup handling je implementován v sprint_scheduler. Většina `asyncio.get_event_loop()` volání už má try/except RuntimeError guard.

---

## Top 10 Recommended Modernization Opportunities

| # | Oblast | Soubor | Důvod | Priority |
|---|--------|--------|--------|----------|
| 1 | asyncio.get_event_loop | model_manager.py:594 | P0 — async funkce bez guard | P0 |
| 2 | asyncio.get_event_loop | global_scheduler.py:337 | P0 — TPE worker bez guard | P0 |
| 3 | Task naming | 80+ create_task bez name= | P1 — python -m asyncio pstree debugging | P1 |
| 4 | compression.zstd | legacy/atomic_storage.py | P1 — stdlib zstd, žádný extra dep | P1 |
| 5 | uuid.uuid7 | run/session/event ID | P1 — sortable pro analytics | P1 |
| 6 | PID logging | __main__.py | P1 — pstree/ps debugging | P1 |
| 7 | bounded concurrency | ThreadPoolExecutor.map buffersize | P2 — backpressure na M1 8GB | P2 |
| 8 | gc.collect(0) | legacy/autonomous_orchestrator.py:8838 | P2 — audit candidate, benchmark required | P2 |
| 9 | t-strings | export markdown/STIX | P2 — experiment only, no patch | P2 |
| 10 | asyncio.wait_for → timeout | smoke_runner.py | P2 — style modernization only | P2 |

---

## P0 Issues — Can Break Python 3.14 Runtime

### P0-1: asyncio.get_event_loop() — SKUTEČNÉ breakery

**Korekce methodology:** Většina `asyncio.get_event_loop()` volání v projektu už má try/except RuntimeError guard. Skutečné breakery jsou pouze 2 místa:

**P0 locations (nutno opravit):**

| Soubor | Řádek | Context | Problém |
|--------|-------|---------|---------|
| `brain/model_manager.py` | 594 | **Async funkce** | Volá `get_event_loop()` v async kontextu — musí použít `get_running_loop()` |
| `orchestrator/global_scheduler.py` | 337 | **TPE worker** | Sync worker bez guard — potřebuje try/except RuntimeError |

**OK — existující guard:**
| Soubor | Řádek | Context | Status |
|--------|-------|---------|--------|
| `loops/research_loop.py` | 284, 327 | Sync funkce s try/except | ✅ OK |
| `network/session_runtime.py` | 338 | Sync cleanup s try/except | ✅ OK |
| `brain/model_lifecycle.py` | 392 | Async s `is_running()` check | ✅ OK, funkční |

**Bezpečný pattern pro ASYNC funkce:**
```python
# async funkce — vždy použij get_running_loop()
async def my_async_func():
    loop = asyncio.get_running_loop()  # ✅ Správně
```

**Bezpečný pattern pro SYNC funkce v TPE:**
```python
# sync funkce v TPE worker
def my_sync_func():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
```

**F214A mikro-sprint scope:**
- `brain/model_manager.py:594` — změnit na `get_running_loop()`
- `orchestrator/global_scheduler.py:337` — přidat try/except RuntimeError guard

---

### P0-2: gc.collect(0) — KOREKCE METHODOLOGY

**OPRAVENO:** `gc.collect(0)` **NENÍ** P0 breaker.

Oficiální Python 3.14 docs říkají:
- `gc.collect(1)` — **ZMĚNA** v incremental GC (inkrementální vs generation-1)
- `gc.collect(0)` a `gc.collect(2)` — **beze změny** ("unchanged behavior")

Verze timeline:
- Python 3.13: Generational GC (3 generace)
- Python 3.14.0-3.14.4: Incremental GC (gc.collect(1) = inkrementální průchod)
- Python 3.14.5+: Revert na Generational GC (kvůli memory pressure reports)

**Doporučení:** gc.collect(0) downgraduje na **P2 audit candidate**. Nutný benchmark 3.14.4 vs 3.14.5+ před jakýmkoli patchem. Neopravovat bez dat.

---

## P1 Performance Improvements for M1 8GB

### P1-1: compression.zstd — standard library replace za gzip

**Kandidáti:**
| Soubor | Řádek | Aktuální | Potenciál |
|--------|-------|----------|-----------|
| `legacy/atomic_storage.py` | 917–927 | gzip.compress | compression.zstd |
| `legacy/atomic_storage.py` | 982–1028 | gzip.decompress | compression.zstd |
| `legacy/autonomous_orchestrator.py` | 22148–22209 | gzip | compression.zstd |

**Proč:** `compression.zstd` je ve stdlib od Python 3.14. ZSTD má lepší kompresní poměr i rychlost než gzip. Pro M1 8GB by menší snapshoty = méně I/O = lepší UMA budget.

**Bezpečná migrace:**
```python
# Starý:
import gzip
compressed = gzip.compress(content_bytes, compresslevel=6)

# Nový:
try:
    import compression.zstd
    compressed = compression.zstd.compress(content_bytes)
except ImportError:
    # Fallback pro Python < 3.14
    import gzip
    compressed = gzip.compress(content_bytes, compresslevel=6)
```

**Test command:**
```bash
# Benchmark gzip vs zstd na typickém workload
python -c "
import time, gzip, compression.zstd
data = b'x' * 1_000_000  # 1MB test
# gzip
t0 = time.perf_counter()
g = gzip.compress(data, 6); tg = time.perf_counter() - t0
# zstd  
t0 = time.perf_counter()
z = compression.zstd.compress(data); tz = time.perf_counter() - t0
print(f'gzip: {tg*1000:.2f}ms, zstd: {tz*1000:.2f}ms, ratio: {len(z)/len(g):.2f}')
"
```

**Mikro-sprint návrh:** F214C — Zstd Compression
- **Scope:** 2 files (atomic_storage.py, autonomous_orchestrator.py)
- **Files:** `legacy/atomic_storage.py`, `legacy/autonomous_orchestrator.py`
- **Why now:** Žádná nová dependency, stdlib od 3.14
- **Test command:** Benchmark gzip vs zstd na reálných snapshot datech
- **Rollback risk:** NÍZKÝ — gzip fallback stále dostupný

---

### P1-2: uuid.uuid7 pro sortable run/session/event IDs

**Problém:** uuid.uuid4() je random. Pro run/session/event IDs kde záleží na pořadí, je uuid7 (timestamp-based) lepší.

**Kandidáti (kde UUID není pro deterministické hashing):**
| Typ | Lokace | Poznámka |
|-----|--------|----------|
| run_id | `runtime/sprint_scheduler.py` | ✅ Může být uuid7 |
| session_id | `brain/session_*.py` | ✅ Může být uuid7 |
| event_id | `metrics_registry.py` | ✅ Může být uuid7 |
| CanonicalFinding.id | `knowledge/finding*.py` | ❌ NESMÍ měnit — deterministické |

**Důležité upozornění:**
```
⚠️  DO NOT TOUCH: CanonicalFinding.id / dedup fingerprints
Toto je content-addressable, nesmí se měnit na random/timestamp!
```

**Bezpečná migrace:**
```python
import uuid

# Starý:
run_id = str(uuid.uuid4())

# Nový:
run_id = str(uuid.uuid7())  # Sortable, timestamp-based
```

**Navrhovaná změna v `runtime/sprint_scheduler.py` — přidat funkci:**
```python
def new_run_id() -> str:
    """Generate a sortable run ID using uuid7."""
    return str(uuid.uuid7())
```

**Test command:**
```python
python -c "
import uuid
# Verify sortability
ids = [uuid.uuid7() for _ in range(100)]
str_ids = [str(u) for u in ids]
assert str_ids == sorted(str_ids), 'uuid7 not sortable!'
print('uuid7 sortability: OK')
"
```

**Mikro-sprint návrh:** F214D — UUID7 Sortable IDs
- **Scope:** Sprint scheduler, metrics registry
- **Files:** `runtime/sprint_scheduler.py`, `metrics_registry.py`
- **Why now:** Lepší analytics sortability
- **Test command:** Test uuid7 sortability
- **Rollback risk:** ŽÁDNÝ — změna jen pro nové run/session/event IDs

---

### P1-3: Task naming pro asyncio introspection

**Problém:** 80+ `asyncio.create_task()` volání bez `name=` argumentu. Python 3.14 má `python -m asyncio pstree PID` pro debugging.

**Affected (bez name=):**
- `runtime/sprint_scheduler.py` — 8+ create_task
- `__main__.py` — 2 TaskGroup.create_task
- `layers/coordination_layer.py` — 2 create_task
- `dht/kademlia_node.py` — 3 create_task
- `transport/nym_transport.py` — 5 create_task
- atd.

**Bezpečná migrace:**
```python
# Starý:
task = asyncio.create_task(coro())

# Nový (3.11+):
task = asyncio.create_task(coro(), name="my_task_name")
```

**Pro TaskGroup:**
```python
# Starý:
tg.create_task(coro())

# Nový:
tg.create_task(coro(), name="my_task_name")
```

**Doporučené naming convention:**
```python
# Pattern: "module:method:purpose"
task = asyncio.create_task(
    self._fetch_and_process(),
    name="stealth_crawler:fetch_and_process:main"
)
```

**Návrh pro __main__.py logging PID:**
```python
import os, sys
# Na začátku main():
logger.info(f"PID: {os.getpid()}")
logger.info(f"asyncio introspection: python -m asyncio pstree {os.getpid()}")
```

**Mikro-sprint návrh:** F214E — Task Naming for Introspection
- **Scope:** 15+ files s create_task bez name
- **Files:** `runtime/sprint_scheduler.py`, `__main__.py`, `transport/*.py`, `dht/*.py`
- **Why now:** Python 3.14 pstree debugging vyžaduje named tasks
- **Test command:** `python -m asyncio pstree $(pgrep -f hledac)` — musí ukázat named tasks
- **Rollback risk:** NÍZKÝ — name= je additive

---

### P1-4: asyncio.run() vs loop.run_until_complete() v TPE

**Problém:** Projekt má 6+ míst kde `asyncio.run()` nebo `loop.run_until_complete()` běží v ThreadPoolExecutor worker. To je M1 crash vector (F196A už opravil 5 míst).

**Stav po F196A (opraveno):**
- ✅ `tool_registry.py:478` — loop.run_until_complete
- ✅ `document_intelligence.py:1325` — loop.run_until_complete
- ✅ `brain/inference_engine.py:445` — loop.run_until_complete
- ✅ `graph_rag.py:436` — loop.run_until_complete
- ✅ `utils/execution_optimizer.py:407,412` — loop.run_until_complete

**Affected (potenciálně nedořešené):**
```python
# loops/research_loop.py — sync metoda _load_qtable() volá:
loop = asyncio.get_event_loop()  # ← BEZ running check!
qtable_data = loop.run_until_complete(...)

# To je v sync metodě, ne v TPE — ale get_event_loop() je stale problematické
```

**Bezpečný pattern (již enforced v GHOST_INVARIANTS):**
```python
# V TPE worker:
loop.run_in_executor(pool, lambda: loop.run_until_complete(coro))
```

**Mikro-sprint návrh:** F214F — Async Loop Safety Phase 2
- **Scope:** Dokončit research_loop.py opravu
- **Files:** `loops/research_loop.py`
- **Why now:**research_loop.py stále používá get_event_loop() bez guard
- **Test command:** Smoke test bez runtime crash
- **Rollback risk:** NÍZKÝ

---

## P2 Experiments Only

### P2-1: Template string literals — KOREKCE METHODOLOGY

**Korekce:** t-strings **nejsou** drop-in replacement za `string.Template.substitute()`.

`t"..."` vrací objekt z `templatelib`, **ne** `string.Template`. Vyžaduje vlastní renderer/protocol, není kompatibilní s `.substitute()`.

```python
# SPRÁVNĚ:
import templatelib

TEMPLATE = t"<div>${content}</div>"
type(TEMPLATE)  # <class 'templatelib.Template'>, NOT string.Template

# Pro použití je potřeba vlastní renderer — nelze použít Template.substitute()
```

**Kandidáti pro experiment:**
- `export/markdown_reporter.py` — f-string reporty
- `export/stix_exporter.py` — JSON generace s interpolací
- `export/sprint_markdown_reporter.py` — Markdown šablony

**Doporučení:** t-strings jsou experimentální Python 3.14 feature. **Žádný plošný patch.** Pouze experimentální evaluace v izolovaném modulu.

**Mikro-sprint návrh:** F214G — Template Literal Research
- **Scope:** Research only, žádná implementace
- **Files:** `export/markdown_reporter.py`
- **Why now:** Dokumentace příležitosti pro future
- **Test command:** Experiment only
- **Rollback risk:** N/A

---

### P2-2: Executor.map buffersize pro backpressure

**Problém:** Některé `ThreadPoolExecutor` používají `executor.map()` bez `buffersize`, což může vytvořit neomezenou frontu.

**Kandidáti:**
| Soubor | Řádek | Current | Recommended |
|--------|-------|--------|------------|
| `knowledge/duckdb_store.py` | 2167+ | executor.submit | Mít buffersize=1 pro single-writer |
| `brain/distillation_engine.py` | 401 | ThreadPoolExecutor(1) | ✅ OK |
| `tools/content_miner.py` | 1329 | executor.submit loop | buffersize=4 pro M1 |

**Navrhované buffersize hodnoty pro M1 8GB:**
```python
# Pro I/O-bound operace:
ThreadPoolExecutor(max_workers=4, thread_name_prefix="hledac_io")

# Pro CPU-bound (MLE na M1 — pozor na GIL):
ThreadPoolExecutor(max_workers=1, thread_name_prefix="hledac_cpu")

# Pro single-writer DB (DuckDB LMDB):
ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb_writer")
```

**Python 3.14 Executor.map buffersize:**
```python
# Nové v Python 3.14:
result = executor.map(fn, items, buffersize=2)  # Backpressure!
```

**Mikro-sprint návrh:** F214H — Executor Backpressure
- **Scope:** Content miner a摊 specfic files
- **Files:** `tools/content_miner.py`
- **Why now:** M1 8GB memory pressure
- **Test command:** Porovnat memory při velkém batchi
- **Rollback risk:** NÍZKÝ

---

### P2-3: asyncio.capture_call_graph() instrumentation

**Problém:** Python 3.14 má nové introspection nástroje:
- `asyncio.capture_call_graph()`
- `asyncio.print_call_graph()`
- `python -m asyncio ps PID`
- `python -m asyncio pstree PID`

**Návrh pro tools/asyncio_pstree_helper.py (bez implementace, jen dokumentace):**

```python
"""
Asyncio PSTree Helper
=====================
Návrh pro Python 3.14+ asyncio introspection.

Boot smoke script:
    python -m hledac.universal.__main__ &
    PID=$!
    sleep 5
    python -m asyncio pstree $PID
    python -m asyncio ps $PID

Expected output by F214E (task naming):
    TaskGraph
    ├── windup_watchdog [uma_watchdog]
    ├── memory_pressure_loop
    ├── sprint_loop
    │   ├── feed_branch [feed_pipeline]
    │   ├── public_branch [public_fetcher]
    │   └── ct_branch [ct_scanner]
    └── cleanup_loop
"""

def log_pid_for_debugging():
    import os, logging
    logger = logging.getLogger(__name__)
    pid = os.getpid()
    logger.info(f"PID={pid} — run 'python -m asyncio pstree {pid}' for task tree")
    logger.info(f"asyncio introspection requires Python 3.14+ with named tasks")
```

**Mikro-sprint návrh:** F214I — Asyncio Introspection Setup
- **Scope:** Návrh, bez implementace
- **Files:** `tools/asyncio_pstree_helper.py` (new)
- **Why now:** Dokumentace pro debugging workflow
- **Test command:** N/A
- **Rollback risk:** N/A

---

## Detailed File:Line Findings

### A) GC / Memory / UMA

| File | Line | Pattern | Risk | Recommendation |
|------|------|---------|------|----------------|
| `legacy/autonomous_orchestrator.py` | 8838 | `gc.collect(0)` | **P2** | Benchmark required before patch |
| `legacy/autonomous_orchestrator.py` | 5422, 11520, 11766... | `gc.collect()` | LOW | OK |
| `coordinators/memory_coordinator.py` | 1252, 1256 | `gc.collect(2)`, `gc.collect()` | LOW | OK |
| `brain/model_lifecycle.py` | 587, 588, 617, 623... | `gc.collect()` | LOW | OK |
| `utils/mlx_cache.py` | 364, 368, 375, 413 | `gc.collect()` v cleanup | LOW | OK |
| `utils/mlx_memory.py` | 78, 88 | `gc.collect()` | LOW | OK |
| `knowledge/duckdb_store.py` | 504 | `gc.collect()` | LOW | OK |
| `knowledge/lancedb_store.py` | 504 | `gc.collect()` | LOW | OK |
| `graph/quantum_pathfinder.py` | 805, 829, 845... | `gc.collect()` | LOW | OK |
| `__main__.py` | 2720 | `gc.collect()` | LOW | OK |

**P2 GC Audit Note:** gc.collect(1) is what changed in Python 3.14.4 incremental GC. gc.collect(0) behavior is documented as unchanged. Benchmark 3.14.4 vs 3.14.5+ required before any patch.

**UMA Memory Thresholds (M1 8GB):**
```python
# Current thresholds:
UMA_WARN = 5.5  # GB — warn when approaching limit
UMA_CRITICAL = 6.0  # GB — emergency cleanup
UMA_EMERGENCY = 6.5  # GB — force stop
```

---

### B) asyncio modernizace

**P0 actual (nutno opravit v F214A):**

| File | Line | Pattern | Python 3.14 Impact | Fix |
|------|------|---------|-------------------|-----|
| `brain/model_manager.py` | 594 | `asyncio.get_event_loop()` in **async** | RuntimeError | `asyncio.get_running_loop()` |
| `orchestrator/global_scheduler.py` | 337 | `asyncio.get_event_loop()` in **sync TPE** | RuntimeError | try/except RuntimeError guard |

**OK (existující guard):**

| File | Line | Pattern | Status |
|------|------|---------|--------|
| `loops/research_loop.py` | 284, 327 | try/except RuntimeError | ✅ OK |
| `network/session_runtime.py` | 338 | try/except RuntimeError | ✅ OK |
| `brain/model_lifecycle.py` | 392 | is_running() check | ✅ OK |

**Task naming (bez name=):**

```python
# __main__.py:2791-2797 — TaskGroup tasks
async with asyncio.TaskGroup() as tg:
    tg.create_task(async_run_live_public_pipeline(...))  # BEZ name=
    tg.create_task(async_run_default_feed_batch(...))    # BEZ name=

# Doporučené:
tg.create_task(async_run_live_public_pipeline(...), name="live_pipeline")
tg.create_task(async_run_default_feed_batch(...), name="feed_batch")
```

**Safe helper pattern:**
```python
def get_safe_loop():
    """Get running loop or create new one. Safe for Python 3.14+."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
```

---

### C) Bounded concurrency / Executor

**ThreadPoolExecutor instances (bez buffersize):**

```python
# tools/content_miner.py:1329
executor = ThreadPoolExecutor(max_workers=max_workers)
# → Přidat buffersize=4 pro M1 8GB backpressure

# Brain/executors.py — již OK:
CPU_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hledac_cpu")
IO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hledac_io")
```

**ProcessPoolExecutor (pouze pro CPU-bound bez GIL):**
```python
# orchestrator/global_scheduler.py:105
ProcessPoolExecutor(max_workers=max_workers)
# → OK pro CPU-bound práci, ale macOS spawn overhead je vysoký
# Pro M1 8GB: zvaž ThreadPoolExecutor místo ProcessPoolExecutor
```

**Navrhované buffersize pro M1 8GB:**
```python
# Pro content_miner (I/O-bound s CPU parse):
ThreadPoolExecutor(max_workers=4, buffersize=4)

# Pro DuckDB single-writer (již 1 worker, OK):
ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb")
```

---

### D) Python 3.14 asyncio introspection

**Entry points pro debugging:**

```python
# __main__.py — PID logging na začátku main():
import os, sys
logger.info(f"hledac PID: {os.getpid()}")
logger.info(f"asyncio tree: python -m asyncio pstree {os.getpid()}")
logger.info(f"asyncio tasks: python -m asyncio ps {os.getpid()}")
```

**Boot smoke command:**
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
python -m hledac.universal.__main__ &
PID=$!
sleep 5
python -m asyncio pstree $PID
kill $PID
```

**Návrh pro tools/asyncio_pstree_helper.py (bez implementace):**
```python
"""
Asyncio PSTree Helper — Design Doc
===================================
Bez implementace. Pouze návrh pro budoucí debugging.

1. V __main__.py přidat:
   - PID logging na začátek
   - asyncio task naming

2. V smoke_runner.py:
   - Přidat --pstree flag pro dump task tree

3. Benchmark:
   - Porovnat overhead pstree na 10 vs 100 tasks
   - Ověřit že naming nemá performance impact
"""
```

---

### E) compression.zstd

**Current usage (gzip):**

```python
# legacy/atomic_storage.py:917-927
import gzip
compressed = gzip.compress(content_bytes, compresslevel=6)

# legacy/autonomous_orchestrator.py:22160
compressed = gzip.compress(content_bytes, compresslevel=6)
```

**Migration path:**

```python
# Stdlib since Python 3.14 — no extra dependency
try:
    import compression.zstd
    compressed = compression.zstd.compress(content_bytes)
except ImportError:
    # Python < 3.14 fallback
    import gzip
    compressed = gzip.compress(content_bytes, compresslevel=6)
```

**Benchmark candidates:**
- HTML snapshots (typical 50-200KB)
- Feed cache archives
- Report bundles
- Evidence archives

**Priority:** MEDIUM — gzip fallback stále OK, zstd je optimalizace ne nutnost

---

### F) Template string literals

**Current f-string usage:**

```python
# export/markdown_reporter.py
report = f"# {title}\n\n{content}\n\n## Findings\n{findings}"

# export/stix_exporter.py  
stix = f'{{"type": "{entity_type}", "id": "{entity_id}"}}'
```

**Experimentální t-string usage:**

```python
# Python 3.14+ t-strings
from __future__ import tstrings  # Experimental

# Místo f-string:
html = f"<div>{user_content}</div>"  # Potenciální injection

# S t-strings:
TEMPLATE = t"<div>${content}</div>"  # Vrací Template
html = TEMPLATE.substitute(content=user_content)  # Safe
```

**Kandidáti pro experiment:**
1. `export/markdown_reporter.py` — user-controlled content
2. `export/stix_exporter.py` — external entity IDs

**Poznámka:** t-strings jsou experimentální v 3.14, nedoporučujeme pro produkci

---

### G) Deferred annotations / annotationlib

**Current state:** 200+ files s `from __future__ import annotations`

**Safe patterns already in use:**
```python
# Všechny analyzované soubory používají:
from __future__ import annotations  # ✅

# Nebo TYPE_CHECKING guards:
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from expensive_module import HeavyClass  # ✅ Lazy import
```

**Potential issues:** Žádné kritické. `from __future__ import annotations` je správný pattern.

---

### H) Import-time profiling

**Command pro analýzu:**
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac \
python -X importtime=2 -m hledac.universal.__main__ 2> importtime.log

# Top 20 nejdražších importů:
grep "import time" importtime.log | head -20
```

**Heavy imports (identified from codebase):**
- `mlx` / `mlx_lm` — lazy loaded, OK
- `torch` — NOT used (good)
- `duckdb` — loaded on-demand
- `lmdb` — loaded on-demand
- `transformers` — lazy loaded
- `sklearn` — NOT used (good)
- `numpy` — many deps load it

**Kandidáti pro lazy import:**
```python
# Místo top-level:
import mlx.core as mx  # Drahé na import

# Lazy:
def _get_mlx():
    import mlx.core as mx
    return mx
```

---

### I) UUIDv7

**DO NOT TOUCH:**
```python
# CanonicalFinding.id — NESMÍ se měnit
finding.id = hashlib.sha256(content)  # Deterministic

# Dedup fingerprints — NESMÍ se měnit
fingerprint = sha256(source + ioc_value)
```

**MŮŽE být uuid7:**
```python
# runtime/sprint_scheduler.py
run_id = str(uuid.uuid7())  # Sortable run ID

# metrics_registry.py
event_id = str(uuid.uuid7())  # Sortable event ID

# session_runtime.py  
session_id = str(uuid.uuid7())  # Sortable session ID
```

---

### J) Security / tarfile / remote debug

**tarfile extraction (bezpečné):**

```python
# forensics/metadata_extractor.py:1760 — rozpoznává .tar/.gz/.bz2
# Žádné extractall() nenalezeno v aktivním kódu

# Pro jistotu — přidat filter:
import tarfile
def safe_extract(tar, path):
    for member in tar.getmembers():
        member.path = member.name  # Reset path
    tar.extractall(path, filter='data')  # Python 3.12+ filter
```

**PYTHON_DISABLE_REMOTE_DEBUG:**
```python
# V __main__.py — již existuje logika:
# env["PYTHON_DISABLE_REMOTE_DEBUG"] = "1"

# Ověření:
import os
if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") == "1":
    # Disable remote debugging
```

---

### K) Python 3.14 removals/deprecations compatibility

**Nalezené deprecated patterns:**

| Pattern | Soubor | Status |
|---------|--------|--------|
| `asyncio.wait_for()` | `smoke_runner.py:203,222` | Still valid, prefer `asyncio.timeout()` |
| `asyncio.ensure_future()` | N/A | Not found |
| `asyncio.child watcher` | N/A | Not found |
| `ast.visit_Num/Str` | N/A | Not found |

**asyncio.wait_for deprecation:**
```python
# Starý (deprecated):
await asyncio.wait_for(coro, timeout=10)

# Nový (Python 3.11+):
async with asyncio.timeout(10):
    await coro
```

**Potential migration:**
```python
# tools/smoke_runner.py:203,222
# Z: asyncio.wait_for
# Na: asyncio.timeout context manager
```

---

## Suggested Micro-Sprints

### F214A: Async Loop Safety Phase 1
- **Scope:** Fix 3 critical get_event_loop() in research_loop.py, model_lifecycle.py, model_manager.py
- **Files:** `loops/research_loop.py`, `brain/model_lifecycle.py`, `brain/model_manager.py`
- **Why now:** P0 — RuntimeError risk na Python 3.14
- **Test command:** `python -c "import asyncio; asyncio.get_event_loop()"` → RuntimeError
- **Rollback risk:** LOW — additive safety check

### F214B: GC Version Awareness
- **Scope:** Fix gc.collect(0) in autonomous_orchestrator.py
- **Files:** `legacy/autonomous_orchestrator.py`
- **Why now:** P0 — incremental GC in 3.14.4 behaves differently
- **Test command:** Benchmark GC behavior on 3.14.4 vs 3.14.5
- **Rollback risk:** NONE — gc.collect() is safer

### F214C: Zstd Compression
- **Scope:** Add compression.zstd with gzip fallback
- **Files:** `legacy/atomic_storage.py`
- **Why now:** P1 — stdlib, no new dep, better compression
- **Test command:** Benchmark gzip vs zstd on snapshots
- **Rollback risk:** LOW — gzip fallback

### F214D: UUID7 Sortable IDs
- **Scope:** Replace uuid4 with uuid7 for run/session/event IDs
- **Files:** `runtime/sprint_scheduler.py`, `metrics_registry.py`
- **Why now:** P1 — sortability for analytics
- **Test command:** Verify uuid7 sortability
- **Rollback risk:** NONE — only new IDs affected

### F214E: Task Naming for Introspection
- **Scope:** Add name= to create_task calls
- **Files:** `__main__.py`, `runtime/sprint_scheduler.py`, `transport/*.py`
- **Why now:** P1 — enables pstree debugging
- **Test command:** `python -m asyncio pstree $(pgrep -f hledac)`
- **Rollback risk:** LOW — additive

### F214F: Async Loop Safety Phase 2
- **Scope:** Complete research_loop.py fix
- **Files:** `loops/research_loop.py`
- **Why now:** P0 — remaining get_event_loop() without guard
- **Test command:** Smoke test
- **Rollback risk:** LOW

---

## Validation Commands Run

### 1. Core Imports
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python - <<'PY'
import asyncio, gc, yaml, msgspec, ahocorasick, xxhash, lmdb, pyzipper, probables
print("CORE IMPORTS OK")
PY
```
**Result:** ✅ PASS

### 2. Boot Smoke
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
python - <<'PY'
import subprocess, sys, os, signal
env = os.environ.copy()
env["PYTHONPATH"] = "/Users/vojtechhamada/PycharmProjects/Hledac"
env["PYTHON_DISABLE_REMOTE_DEBUG"] = "1"
cmd = [sys.executable, "-m", "hledac.universal.__main__"]
p = subprocess.Popen(cmd, cwd="/Users/vojtechhamada/PycharmProjects/Hledac", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    out, _ = p.communicate(timeout=35)
except subprocess.TimeoutExpired:
    p.send_signal(signal.SIGINT)
    out, _ = p.communicate(timeout=10)
    print(out)
    print("BOOT_SMOKE_TIMEOUT_AFTER_START_OK")
    raise SystemExit(0)
print(out)
raise SystemExit(p.returncode)
PY
```
**Result:** ✅ PASS (timeout OK)

### 3. Python Version Check
```python
import sys
print(f"Python: {sys.version}")  # 3.14.4
print(f"Version info: {sys.version_info}")  # (3, 14, 4)
```
**Result:** ✅ Python 3.14.4 confirmed

---

## M1 8GB-Specific Scoring

| Finding | Impact | Risk | Effort | M1_8GB | Now |
|---------|--------|------|--------|--------|-----|
| asyncio.get_event_loop P0 | CRITICAL | HIGH | SMALL | HIGH | YES |
| gc.collect(0) P0 | HIGH | HIGH | SMALL | HIGH | YES |
| Zstd compression P1 | MEDIUM | LOW | SMALL | MEDIUM | YES |
| UUID7 sortable P1 | LOW | LOW | SMALL | LOW | YES |
| Task naming P1 | MEDIUM | LOW | MEDIUM | MEDIUM | YES |
| Executor buffersize P2 | LOW | LOW | SMALL | MEDIUM | NO |
| Template literals P2 | LOW | LOW | LARGE | LOW | NO |
| Asyncio introspection P2 | LOW | LOW | SMALL | LOW | NO |

---

## Appendix: GC Version Behavior

| Python | GC Mode | gc.collect(0) | gc.collect() |
|--------|---------|---------------|--------------|
| 3.13 | Generational 3-gen | Gen-0 only | Full collection |
| 3.14.0-3.14.4 | Incremental | May differ | Full collection |
| 3.14.5+ | Generational (reverted) | Gen-0 only | Full collection |

**Project is on 3.14.4** — incremental GC. Recommend using `gc.collect()` without args for version-independent behavior.

---

## Appendix: asyncio.get_event_loop() Python 3.14 Behavior

**Python 3.10-3.13 (current behavior):**
```python
>>> asyncio.get_event_loop()
<Loop>  # Creates implicit if none exists
```

**Python 3.14 (new behavior):**
```python
>>> asyncio.get_event_loop()
RuntimeError: asyncio.get_event_loop() is not available in Python 3.14+ 
              when there is no current event loop
```

**Safe alternatives:**
```python
# Always use:
asyncio.get_running_loop()  # Raises RuntimeError if no loop

# Or guard:
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
```

---

*Report generated: 2026-05-05*
*Audit scope: Python 3.14.4 compatibility, M1 8GB UMA optimization*
*Next action: Review F214A-F214F micro-sprints for prioritization*

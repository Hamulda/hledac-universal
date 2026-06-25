# DuckDB Dual-Write Architecture — Komplexní Analýza + Implementace

## 1. Aktuální Architektura (Tři Módy)

```
DuckDBSubprocessAdapter (main entry point, P1-1)
├── _legacy_writer: DuckDBShadowStore (in-process, aktivní na M1)
│     ├── _wal_manager: WALManager (LMDB mmap, main process)
│     ├── _coalescer: WriteCoalescer (initialized, used by active paths)
│     └── async_ingest_findings_batch → quality gate + WAL + Arrow INSERT
└── _duckdb_proxy: DuckDBProxy (subprocess, DEAD na M1)
      └── Arrow IPC via POSIX shm (nikdy reached na M1)
```

**Módy určené env var:**

| Mode | Env | M1 8GB | Behavior |
|------|-----|---------|----------|
| `inprocess` | `HLEDAC_DUCKDB_INPROCESS=1` | Opt-in | DuckDB v main process, ~200MB úspora |
| `subprocess` | `HLEDAC_DUCKDB_SUBPROCESS=1` (default non-M1) | OFF (default) | DuckDBWriterWorker subprocess |
| `legacy` | `HLEDAC_DUCKDB_SUBPROCESS=0` | -- | DuckDBShadowStore přímo |

**M1 8GB default:** `_subprocess_mode = False` (řádek 91: `return os.environ.get("HLEDAC_DUCKDB_SUBPROCESS", "0") == "1"`)

→ Na M1 jede vše v `DuckDBShadowStore` přes `_legacy_ingest` path.

---

## 2. Canonical Write Path — Jak to Skutečně Funfuje

### 2.1 `async_ingest_findings_batch` (Hlavní Path)

```
Entry: async_ingest_findings_batch(findings)
  │
  ├─→ CHUNK_SIZE = 1024 (M1-safe)
  │
  ├─→ Per chunk (pipeline, maxsize=2 queue):
  │     │
  │     ├─→ Quality gate (_assess_finding_quality_batch) — Rust rayon parallel
  │     │     on thread pool (run_in_executor)
  │     │
  │     ├─→ Separate accepted vs rejected
  │     │
  │     ├─→ _piped_storage (async task on executor):
  │     │     │
  │     │     ├─→ len >= _ARROW_MIN_BATCH(5) ?
  │     │     │     ├─→ async_record_canonical_findings_batch_arrow
  │     │     │     │     ├─→ WAL (LMDB put_many_sync)
  │     │     │     │     └─→ DuckDB Arrow INSERT (zero-copy via C Data Interface)
  │     │     │     └─→ async_record_canonical_findings_batch (legacy fallback)
  │     │
  │     └─→ Queue backpressure (q.full() → await q.join())
  │
  └─→ Merge all results, fire graph update async
```

**Arrow path already exists and is wired!** `async_record_canonical_findings_batch_arrow` (řádek 5679+) je volán z `_piped_storage` když `len(findings) >= _ARROW_MIN_BATCH(5)`.

### 2.2 `drain_and_get_accepted` — Coalescer Path

```
Entry: drain_and_get_accepted(findings)
  │
  ├─→ store._coalescer.submit(findings) → queue.put(findings)
  │
  └─→ store._coalescer.drain_and_get_accepted(findings)
        │
        ├─→ await self.submit(findings) — add to queue
        ├─→ await self._flush(await self._queue.get()) — blocking drain
        └─→ returns results
```

`submit()` přidá do fronty, `drain_and_get_accepted` pak zavolá `_flush` který čeká na item z fronty.

**Ale pozor:** `drain_and_get_accepted` na store (řádek 4216-4217):
```python
if self._coalescer is not None:
    return await self._coalescer.drain_and_get_accepted(findings)
```

A `WriteCoalescer.drain_and_get_accepted` (write_coalescer.py:315-350):
```python
async def drain_and_get_accepted(self, findings):
    await self.submit(findings)  # add to queue
    return await self._flush(await self._queue.get())  # drain + flush
```

Tohle je **synchronous blocking drain** — čeká na flush, neguje async batching benefit.

---

## 3. Co je Špatně (Korekce Původní Analýzy)

### Problém 1: Redundantní Batching ✅ (CORRECT)

`async_ingest_findings_batch` má interní pipelining s `CHUNK_SIZE=1024` a `Queue(maxsize=2)`. `WriteCoalescer` přidává další vrstvu queue nad to. To je dvojitá komplexita bez jasného přínosu.

### Problém 2: Synchronous Drain Negates Async Batching ✅ (CORRECT)

`drain_and_get_accepted` čeká synchronně na flush — ne background batching. Callers (`live_public_pipeline`, `live_feed_pipeline`) to používají jako synchronized merge path.

### Problém 3: DuckDBProxy Subprocess Dead na M1 ✅ (CORRECT)

Subprocess path se nikdy nezavolá na M1. `_duckdb_proxy` zůstává `None`.

### Problém 4: Arrow IPC — Už WIRED ✅ (CORRECTION)

Arrow path **existuje a je aktivní**! `async_record_canonical_findings_batch_arrow` je volán z `_piped_storage`. Není to dead code — je to primary path pro batche >= 5 items.

---

## 4. Návrh Řešení — Fázovaná Implementace

### Fáze 1: Odstranit DuckDBProxy Subprocess Dead Code

**Soubory k úpravě:**
- `knowledge/duckdb_subprocess_adapter.py` — remove `_duckdb_proxy` path pro M1 builds

```python
# duckdb_subprocess_adapter.py — ZMĚNA
# V async_ingest_findings_batch, remove subprocess path:
# Smazat řádky ~289-316 (proxy.ingest_batch + _legacy_ingest_fallback)
# M1 vždy jede přes _legacy_ingest
```

**Ale pozor:** `DuckDBSubprocessAdapter` je entry point pro `core/__main__.py`. Pokud ho používá, musíme zachovat compatibility.

### Fáze 2: Vyčistit WriteCoalescer — Option A (Remove)

**Change:** `drain_and_get_accepted` ve `DuckDBShadowStore` už nebude volat coalescer, rovnou zavolá `async_ingest_findings_batch`:

```python
# duckdb_store.py:4216-4221 — CHANGE
# Before:
if self._coalescer is not None:
    return await self._coalescer.drain_and_get_accepted(findings)
# Coalescer not available — direct write (fail-safe fallback)
try:
    return await self.async_ingest_findings_batch(findings)

# After:
# WriteCoalescer removed — direct async_ingest_findings_batch (already has internal batching)
return await self.async_ingest_findings_batch(findings)
```

**PRO:** Jednodušší kód, žádná redundantní queue
**CONTRA:** Ztrácíme async submit+flush separation (i když se stejně nepoužívala správně)

### Fáze 3: Přidat Python 3.14 No-GIL Arrow Streaming

S PEP 749 (no-GIL Python), můžeme mít DuckDB v dedikovaném vlákně bez GIL contention:

```python
# knowledge/arrow_duckdb_streamer.py — NEW FILE
"""
Arrow Streaming DuckDB Writer pro Python 3.14+ No-GIL.
DuckDB běží v dedicated thread, hlavní thread posílá Arrow batche.
Zero-copy přes PyArrow C Data Interface.
"""
import asyncio
import threading
import pyarrow as pa
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

class ArrowDuckDBStreamer:
    """
    No-GIL Arrow streaming writer.
    
    Python 3.14 PEP 749 umožňuje true threading bez GIL contention.
    DuckDB thread přijímá Arrow batche přes queue, zapisuje bez blokování hlavního thread.
    """
    
    def __init__(self, db_path: str, schema: pa.Schema, queue_size: int = 16):
        self._schema = schema
        self._queue: asyncio.Queue[pa.RecordBatch] = asyncio.Queue(maxsize=queue_size)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb_writer")
        self._conn_future: asyncio.Future = None
        self._started = False
        
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._conn_future = loop.run_in_executor(self._executor, self._init_connection)
        await self._started_future
        
    def _init_connection(self):
        import duckdb
        self._conn = duckdb.connect(database=self._db_path, read_only=False)
        # Create schema
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS canonical_finding_id_seq;
            CREATE TABLE IF NOT EXISTS canonical_findings (
                finding_id VARCHAR PRIMARY KEY,
                query VARCHAR,
                source_type VARCHAR,
                confidence FLOAT,
                timestamp DOUBLE,
                provenance_json VARCHAR
            )
        """)
        
    async def ingest_batch(self, batch: pa.RecordBatch) -> list[dict]:
        """
        Ingest Arrow RecordBatch — zero-copy do DuckDB.
        
        Non-blocking: hlavní thread pokračuje hned po queue.put.
        DuckDB thread zpracuje batch async.
        """
        await self._queue.put(batch)
        # Return placeholder — actual results come via separate result queue
        return [...]
```

---

## 5. Akční Plán Implementace

### Krok 1: DuckDBProxy Subprocess Cleanup (Low Risk, 30 min)

**File:** `knowledge/duckdb_subprocess_adapter.py`

Remove subprocess path — na M1 vždy `_legacy_ingest`:
- Odebrat `_get_proxy`, `_duckdb_proxy` usage
- Odebrat `proxy.prewarm()` call
- Smazat `_legacy_ingest_fallback` (subprocess fallback už nebude potřeba)

**Test:** `uv run pytest tests/test_duckdb_store.py -x -q` (pokud existuje)

### Krok 2: WriteCoalescer Remove z drain_and_get_accepted (Low Risk, 20 min)

**File:** `knowledge/duckdb_store.py` (~řádek 4216)

```python
# SIMPLIFIED — coalescer removed
return await self.async_ingest_findings_batch(findings)
```

**Důvod:** `async_ingest_findings_batch` už má interní pipeline s chunking (1024) a backpressure (Queue maxsize=2). WriteCoalescer je redundantní.

**Test:** Live pipeline smoke test

### Krok 3: WriteCoalescer Complete Remove (Medium Risk, 2h)

**Files:** 
- `knowledge/duckdb_store.py` — remove `_coalescer` initialization and usage
- `storage/write_coalescer.py` — move to `storage/write_coalescer_DEPRECATED.py`
- `pipeline/live_feed_pipeline.py` — remove `drain_and_get_accepted` usage, use `async_ingest_findings_batch` directly
- `pipeline/live_public_pipeline.py` — same

**Test:** Full sprint smoke test

---

## 6. Rizika

| Krok | Riziko | Mitigace |
|------|--------|----------|
| DuckDBProxy remove | Breaking `core/__main__.py` wiring | Verify `DuckDBSubprocessAdapter` still works as drop-in |
| Coalescer remove | Live pipeline breaks if not all call sites updated | Update all `drain_and_get_accepted` call sites first |
| Arrow path already exists | -- | Není potřeba měnit |

---

## 7. Shrnutí

**Co je špatně:**
1. Redundantní batching (WriteCoalescer + async_ingest_findings_batch chunking)
2. Synchronous drain negates async batching benefit
3. DuckDBProxy subprocess dead code on M1

**Co už je vyřešeno:**
- Arrow zero-copy path JE aktivní (volán z `_piped_storage`)
- Pipeline s backpressure JE implementován (Queue maxsize=2)

**Co implementovat:**
1. Remove DuckDBProxy subprocess dead code (M1 only)
2. Remove WriteCoalescer — `drain_and_get_accepted` přímo volá `async_ingest_findings_batch`
3. Update live pipeline call sites

---

*Analysis updated: 2026-06-25*
*Author: Claude Code analysis*

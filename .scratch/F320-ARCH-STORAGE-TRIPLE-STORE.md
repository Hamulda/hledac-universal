# F320 - Triple Storage SSOT Refactor: duckdb_store.py

**Status:** In Progress
**Date:** 2026-07-03
**Root Cause:** DuckDB (durable) + LMDB (cache) + LanceDB (vec) vyvíjeny nezávisle

---

## PROBLÉM 1: Triple Storage Engine bez jasného SSOT

### Analýza

`duckdb_store.py` (9 751 řádků) obsahuje 3 classes na jednom místě:
- `_DuckDBQueryExecutor` (TYPE_CHECKING stub L99 + real L2024)
- `_DuckDBShadowStore` (L947-9716 — 8 769 řádků!)
- `_DuckDBQueryExecutor` MIXIN (druhá instance L2024)

**Import-time načítá 12 subsystemů najednou:**
```
orjson, msgspec, pyarrow, duckdb, BoundedTaskSet, TargetProfileSummary,
TargetMemory, DuckPGQGraph, semantic_store_buffer, finding_envelope,
graph_attachment, rust_backend, lmdb_kv, dedup
```

### Cutting-Edge Řešení: Trait-Based Storage Abstrakce (PEP 544)

```python
# === NEW FILE: knowledge/stores/protocols.py ===

from typing import Protocol, Iterator, AsyncIterator
from abc import abstractmethod

class FindingStore(Protocol):
    """PEP 544 Protocol — Finding storage abstraction."""
    
    async def append(self, finding: "CanonicalFinding") -> None: ...
    
    async def append_batch(self, findings: list["CanonicalFinding"]) -> list: ...
    
    def query(self, filter: "FindingFilter") -> Iterator["Finding"]: ...
    
    async def query_async(self, filter: "FindingFilter") -> AsyncIterator["Finding"]: ...


class HotCacheStore(Protocol):
    """LMDB-based read-through cache for hot findings."""
    
    def lookup(self, fingerprint: str) -> str | None: ...
    
    def store(self, fingerprint: str, finding_id: str) -> None: ...
    
    def get_stats(self) -> dict: ...


class VectorStore(Protocol):
    """LanceDB ANN vector store for semantic RAG."""
    
    async def upsert_embeddings(self, embeddings: list[tuple[str, list[float]]]) -> None: ...
    
    async def search_similar(self, query_embedding: list[float], k: int = 10) -> list[dict]: ...
```

---

## PROBLÉM 2: duckdb_subprocess_writer.py — Existuje, ale Nepoužívá se

### Analýza

`duckdb_subprocess_writer.py` (1 394 řádků) **už obsahuje Arrow IPC zero-copy**:
- `_findings_to_arrow_batch()` — konverze CanonicalFinding → Arrow batch
- `_arrow_batch_to_shm()` — zero-copy přenos přes POSIX shared memory
- `DuckDBWriterWorker` + `DuckDBProxy` — subprocess writer pattern

**Problém:** `duckdb_store.py` používá `async_record_canonical_findings_batch_arrow`
(L5972-6216), ale **obchází** `duckdb_subprocess_writer` a dělá Arrow serializaci
přímo v hlavním procesu.

Komentář na L7886 potvrzuje:
```python
# Build dicts for Rust (matches duckdb_subprocess_writer pattern).
```

### Řešení: Integrace DuckDBProxy jako Subprocess Writer

```python
# === MODIFIED: knowledge/duckdb_store.py ===
# V async_ingest_findings_batch nahradit:
# 
# PUVODNI (v main procesu, serializace pres GIL):
#   _sync_record_canonical_findings_batch_arrow(findings)
#
# NOVA (pres subprocess, zero-copy SHM):
#   await self._duckdb_proxy.ingest_batch(findings)
#
# DuckDBProxy uz ma:
#   async def ingest_batch(self, findings: list[Any]) -> list[dict]
#   - _findings_to_arrow_batch (zero-copy)
#   - _arrow_batch_to_shm (POSIX SHM)
#   - subprocess worker s DuckDB connection (M1 8GB friendly)
```

---

## PROBLÉM 3: Connection-Per-Task Nenativizováno

### Analýza

`duckdb_store.py` používá `_shared_executor` (ThreadPoolExecutor) pro všechny
synchronní DuckDB operace. Chybí:
- `asyncio.to_thread` pro Python 3.12+ async I/O
- `DuckDBPool` s `thread_local` connections (max 2 threads = M1 P-core ceiling)
- Connection lifecycle management

```python
# POUZIVA (stare):
self._shared_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="duckdb_sync"
)

# CHYBI (nove):
# DuckDBPool s connection-per-task pres asyncio.to_thread
```

### Cutting-Edge Řešení: asyncio.to_thread + DuckDBPool

```python
# === NEW FILE: knowledge/stores/duckdb_pool.py ===

import asyncio
import duckdb
from contextlib import asynccontextmanager
from typing import AsyncIterator

class DuckDBPool:
    """
    M1 8GB optimalizovany DuckDB connection pool.
    
    Max 2 connections = M1 4P-core ceiling (F265-U5).
    asyncio.to_thread pro zero-GIL blocking I/O.
    """
    
    def __init__(self, db_path: str | None = None, max_workers: int = 2):
        self._db_path = db_path
        self._max_workers = max_workers
        self._local = asyncio.local()
    
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[duckdb.DuckDBPyConnection]:
        """Acquire connection from pool via asyncio.to_thread."""
        conn = await asyncio.to_thread(self._get_connection)
        try:
            yield conn
        finally:
            await asyncio.to_thread(self._release_connection, conn)
    
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = duckdb.connect(self._db_path)
        return self._local.conn
    
    def _release_connection(self, conn: duckdb.DuckDBPyConnection) -> None:
        # Connection stays open in thread-local for reuse
        pass
```

---

## PROBLÉM 4: DuckDBFindingStore Není Izolovaná Vrátva

### Analýza

Všechny 3 třídy v jednom souboru:
- Žádná Protocol-based abstrakce
- LMDB dedup je roztroušen ( `_dedup_lmdb` vs `_dedup_manager` vs `getattr`)
- WAL je v `_DuckDBShadowStore`, ne v samostatném `LMDBHotCacheStore`

### Cutting-Edge Řešení: Kompozitní Store přes Dataclass

```python
# === NEW FILE: knowledge/stores/composite_store.py ===

from dataclasses import dataclass, field
from typing import Protocol
from .finding_store import FindingStore, HotCacheStore, VectorStore

@dataclass
class CompositeFindingStore:
    """
    Kompozitni store delegujici na specializovane implementace.
    
    M1 8GB: kazdy store ma vlastni bounded resource budget.
    """
    
    duckdb_store: FindingStore          # Durable canonical writes
    hot_cache: HotCacheStore           # LMDB read-through cache
    vector_store: VectorStore | None = None  # LanceDB ANN (optional)
    
    # Bounded executors (M1 8GB ceiling)
    _executor: Any = field(default=None, repr=False)
    
    async def append(self, finding: CanonicalFinding) -> None:
        # 1. Check hot cache first (zero-copy LMDB lookup)
        fp = finding.fingerprint
        if self.hot_cache.lookup(fp):
            return  # Already cached
        
        # 2. Write to DuckDB (durable)
        await self.duckdb_store.append(finding)
        
        # 3. Update hot cache (async, non-blocking)
        await self.hot_cache.store(fp, finding.finding_id)
    
    async def query(self, filter: FindingFilter) -> Iterator[Finding]:
        # Query hot cache first, then DuckDB
        ...
```

---

## IMPLEMENTACE: Fáze 1-3

### Fáze 1: Protocol Abstractions (1 den)
```
knowledge/stores/
  __init__.py              # Export protocols + implementations
  protocols.py             # PEP 544 FindingStore, HotCacheStore, VectorStore
  duckdb_pool.py           # asyncio.to_thread + DuckDBPool
```

### Fáze 2: DuckDBProxy Integrace (1 den)
```
knowledge/stores/
  duckdb_finding_store.py   # Vlacejici duckdb_subprocess_writer.Arrow IPC
  composite_store.py       # Kompozitni delegace
```

### Fáze 3: Migration (2 dny)
```
knowledge/
  duckdb_store.py          # Refaktor na use kompozitni store
  lancedb_store.py         # Implementuje VectorStore protocol
  lmdb_hot_cache.py        # Implementuje HotCacheStore protocol
```

---

## INVARIANTS

| Test | Ověření |
|------|---------|
| `test_triple_store_protocol` | FindingStore Protocol contract |
| `test_arrow_ipc_zero_copy` | Subprocess writer path |
| `test_connection_pool_m1` | Max 2 connections, asyncio.to_thread |
| `test_composite_store_delegation` | správná delegace na jednotlivé store |

---

## M1 8GB OPTIMALIZACE

| Komponenta | Limit | Důvod |
|------------|-------|-------|
| DuckDB connections | 2 | M1 4P-core ceiling |
| ThreadPoolExecutor | 2 workers | F265-U5 thread-local |
| Arrow batch size | 2048 rows | Optimal pro M1 cache |
| LMDB map size | 16 MB | Bounded jak v F265B |
| LanceDB | Optional (RAM > 5GB) | advanced_rag fallback |

---

## REFERENCES

- F265-U5: Thread-Local Conn Pool — `sprint-f265-u5-threadlocal-conn-pool.md`
- F265B: Arrow IPC Prewarm — `sprint-f265b-prewarm-conditional.md`
- K5: Circular Import Protocols — `k5-circular-import-protocols.md`

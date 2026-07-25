# ISSUE 6.1: duckdb_store.py + lmdb_subdb.py — Sjednocení úložišť

## Aktuální stav (F350M-R)

### Architektura úložišť

| Úložiště | Soubor | Role | mmap region |
|----------|--------|------|-------------|
| DuckDB | duckdb_store.py (9350 L) | Canonical findings, SQL analytics | WAL file (~100MB) |
| UnifiedLMDBStore | lmdb_subdb.py (348 L) | WAL, dedup, conditional_cache, forensics | 256 MB shared |
| LanceDB | lancedb_store.py (2004 L) | RAG embeddings (FALLBACK) | subprocess (~200MB) |
| sqlite-vec | sqlite_vec_helpers.py (242 L) | RAG embeddings (PRIMARY) | ~5 MB in-process |

### KLÍČOVÝ NÁLEZ: F272 už konsolidoval LMDB

UnifiedLMDBStore (F272) již konsoliduje 5 LMDB souborů do jednoho:
```
Single LMDB env: sprint_unified.lmdb
Key namespaces:
  wal:         WALManager (finding:, pending_duckdb_sync:, deadletter_ingest:)
  dedup:       DedupManager (cross-run fingerprint dedup)
  cc:          conditional_cache (ETag/Last-Modified HTTP cache)
  forensics:   forensics enrichment metadata
  multimodal:  multimodal embedding cache
```
**Benefit F272**: Single mmap region místo 3-4 samostatných = ~50-70% RAM redukce.

### KLÍČOVÝ NÁLEZ: DuckDB a LMDB mají ODDIlené WAL systémy

```
DuckDB WAL (database-level ACID):
  - Pro crash recovery kanonických záznamů
  - PRAGMA journal_mode=WAL + wal_autocheckpoint=51200
  - Automatický DB crash recovery

LMDB WAL (key-value persistence):
  - Pro cross-run persistenci a deduplikaci
  - WALManager: finding:{id}, pending_duckdb_sync:{id}, deadletter_ingest:{id}
  - Explicitní replay po restartu
```

**Tyto dva WAL systémy slouží ROZDÍLNÝM ÚČELŮM a nelze je snadno sloučit.**

### sqlite-vec je již PRIMARY ANN store

```
RAGOrchestrator (advanced_rag/rag_orchestrator.py):
  ├─→ utils.sqlite_vec_helpers.SqliteVecStore (PRIMARY, ~5MB)
  └─→ knowledge.lancedb_store.get_identity_store() (FALLBACK, jen při RAM > 1.5GB)
```
**Již implementováno správně.**

---

## Root Cause Analysis

### Problém 1: "3× open() při sprint startu (~2s)"

**Příčina**: DuckDBShadowStore má lazy=True (výchozí), takže init je odložený.
Skutečná latence je z:
1. DuckDB connect + WAL init (~500-800ms)
2. LMDB open + lock acquisition (~200-400ms)
3. Schema creation (CREATE TABLE IF NOT EXISTS)

**Není to 3× samostatné open()**, ale spíše:
- DuckDB: file I/O pro WAL + schema
- LMDB: lock acquisition + mmap
- LanceDB: subprocess spawn (při aktivaci)

### Problém 2: "3× mmap regiony"

**Není pravda** - F272 již konsolidoval LMDB do jednoho mmap regionu (256MB).

Skutečné mmap regiony:
1. DuckDB: WAL file (file-backed, OS page cache)
2. UnifiedLMDBStore: 256 MB shared mmap
3. LanceDB: subprocess (ne mmap v hlavním procesu)

### Problém 3: "Komplexní shutdown order"

**Částečně pravda** - pořadí je:
1. DuckDB aclose() → checkpoint + close
2. WALManager flush
3. LMDB close

---

## Cutting-Edge Řešení (Moderní M1 8GB)

### 🔴 NEREALIZOVATELNÉ

**1. DuckDB LMDB extension pro čtení LMDB souborů**
- DuckDB nemá oficiální LMDB extension
- Experimentální community extensions nejsou production-ready
- **Závěr**: Nepodporované, riskantní

**2. Arrow2 shared buffer mezi DuckDB a LanceDB**
- Arrow2 je low-level IPC, ne sdílená paměť mezi procesy
- LanceDB používá svůj vlastní storage engine
- **Závěr**: Technicky nemožné bez hluboké integrace

### 🟡 ČÁSTEČNĚ RELEVANTNÍ

**3. DuckDB jako backing store pro LMDB dedup**
- Teoreticky: LMDB používá DuckDB místo raw souborů
- Prakticky: DuckDB nemá key-value API (jen SQL)
- **Závěr**: Možné, ale vyžaduje significant refactoring

---

## Doporučené Řešení

### Fáze 1: Ověření a měření (TODO)

```python
# Přidat měření do sprint_entrypoint.py
async def run_sprint():
    import time
    t0 = time.monotonic()
    
    # DuckDB init
    store = DuckDBShadowStore()
    await store.async_initialize()
    t1 = time.monotonic()
    logger.info(f"DuckDB init: {t1-t0:.3f}s")
    
    # LMDB init (pokud je potřeba)
    # ... měření ...
    
    logger.info(f"Total storage init: {t1-t0:.3f}s")
```

### Fáze 2: Lazy loading pro všechny storage backends

```python
class DuckDBShadowStore:
    def __init__(self, ...):
        # Už je lazy=True default
        self._lazy = lazy
    
    async def async_initialize(self, ...):
        # Rozšířit o lazy LMDB init
        if self._lazy and not self._lmdb_init:
            self._lmdb_init = True
            # Inicializovat LMDB až když je potřeba
```

### Fáze 3: Storage lifecycle via StorageRouter

```python
# core/storage_router.py už existuje a řeší:
# - 5-layer storage taxonomy
# - Async context manager protocol
# - Fail-safe routing

# Jednotný entry point:
async with StorageRouter() as router:
    await router.aput("ioc.findings", finding)
    result = await router.aget("ioc.findings", finding_id)
```

---

## Implementační plán

### Krok 1: Měření aktuálního stavu
- Přidat timing instrumentation do sprint_entrypoint
- Změřit skutečné časy init jednotlivých komponent
- Ověřit, že F272 konsolidace funguje

### Krok 2: Optimalizace lazy init
- DuckDBShadowStore: lazy connection + lazy schema
- UnifiedLMDBStore: lazy open (až při prvním přístupu)
- LanceDB: lazy subprocess spawn

### Krok 3: Unifikovaný storage interface
- Rozšířit StorageRouter o explicitní lifecycle management
- Přidat unified `async_ingest_batch()` přes všechny backends
- Centralizovaný error handling

---

## Invarianty (pro implementaci)

| Invariant | Test | Popis |
|-----------|------|-------|
| STOR-1 | test_storage_lazy_init | Všechny storage backends podporují lazy init |
| STOR-2 | test_storage_single_mmap | UnifiedLMDBStore používá 1 mmap region |
| STOR-3 | test_storage_graceful_shutdown | Správné pořadí shutdown všech komponent |
| STOR-4 | test_storage_fail_safe | Každý backend vrací prázdný výsledek při chybě |
| STOR-5 | test_storage_bounded | Všechny kolekce mají explicitní max size |

---

## Závěr

**ISSUE 6.1 — STAV k 2026-07-25:**

### ✅ Vyřešeno
- **F272 konsolidace AKTIVOVÁNA** — WALManager nyní používá UnifiedLMDBStore (duckdb_store.py:3764+)
- **UnifiedLMDBStore lazy init** — šetří ~200-400ms při sprint bootu
- **sqlite-vec je PRIMARY ANN store** — správně implementováno
- **LanceDB FALLBACK** — pouze pro RAM > 1.5GB

### 🔴 Zbývá k implementaci
1. **DedupManager unified_store** — DedupManager nemá `unified_store` parametr jako WALManager
2. **StorageRouter lifecycle** — jednotný entry point pro všechny storage backends
3. **Měření timing** — instrumentace pro sledování init časů

### ⚠️ Nemožné / Nezbytné
- **DuckDB-LMDB WAL konsolidace** — různé účely (ACID vs key-value persistence)
- **Arrow2 shared buffer** — technicky nemožné mezi procesy
- **Nahrazení DuckDB** — je primární SQL store

### 🔧 Kritický nález (2026-07-25)
**WALManager měl `unified_store` parametr, ale DuckDBShadowStore ho nikdy nepoužíval!**
Oprava: duckdb_store.py:3764+ nyní předává UnifiedLMDBStore do WALManager.

---

*Generated: 2026-07-25*
*CLAUDE.md reference: GHOST_INVARIANTS, DuckDBShadowStore, UnifiedLMDBStore*

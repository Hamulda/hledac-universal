# ISSUE 6.1 Implementation Plan

## Analýza a závěry

### Aktuální stav

**DuckDBShadowStore (duckdb_store.py):**
- `lazy=True` default - odkládá connect na první use
- `_init_connection()` běží ve ThreadPoolExecutor
- WAL mode: `PRAGMA journal_mode=WAL`, `wal_autocheckpoint=51200`
- hard_memory_limit = 1GB

**UnifiedLMDBStore (lmdb_subdb.py):**
- **NEMÁ lazy init** - otevírá okamžitě v `__init__` (řádek 105: `open_lmdb_with_guard`)
- Single 256MB mmap pro 5 namespaces
- Závisí na `open_lmdb_with_guard` z `lmdb_boot_guard`

### Identifikované problémy

1. **UnifiedLMDBStore.init() není lazy** - okamžitě otevírá mmap
2. **DuckDBShadowStore._init_connection() volá LMDB init同步ně** - v thread poolu
3. **Chybí unified timing/metrics** pro storage initialization

### Řešení

#### Krok 1: Unified Storage Initialization Probe

Přidám timing instrumentation do storage initialization:

```python
# runtime/sprint_entrypoint.py
async def _measure_storage_init(store: DuckDBShadowStore) -> dict[str, float]:
    import time
    timings = {}
    
    t0 = time.monotonic()
    await store.async_initialize()
    timings['duckdb_init'] = time.monotonic() - t0
    
    # LMDB init je součástí DuckDB init přes WALManager
    # Závisí na tom, kdy se poprvé přistupuje k LMDB
    
    return timings
```

#### Krok 2: Lazy LMDB Init (P0)

```python
# knowledge/lmdb_subdb.py - Přidat lazy init do UnifiedLMDBStore

class UnifiedLMDBStore:
    __slots__ = ("_env", "_map_size", "_closed", "_initialized", "_path")
    
    def __init__(self, path: Any, *, map_size: int | None = None, lazy: bool = True) -> None:
        self._path = path
        self._map_size = map_size or _UNIFIED_MAP_SIZE
        self._env = None
        self._closed = False
        self._initialized = False
        self._lazy = lazy
    
    def _ensure_init(self) -> None:
        """Lazy initialization - otevře LMDB až při prvním přístupu."""
        if self._initialized:
            return
        if self._closed:
            raise RuntimeError("Store is closed")
        
        import os
        os.makedirs(self._path, exist_ok=True)
        
        from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
        self._env = open_lmdb_with_guard(
            self._path,
            map_size=self._map_size,
            writemap=False,
            metasync=True,
            readahead=False,
        )
        self._initialized = True
        logger.debug(f"[LMDB-UNIFIED] Opened at {self._path}, map_size={self._map_size / (1024*1024):.0f}MB")
    
    @property
    def env(self) -> Any:
        if self._env is None:
            self._ensure_init()
        return self._env
```

#### Krok 3: DuckDBShadowStore Lazy WALManager Init (P1)

```python
# knowledge/duckdb_store.py - Lazy WALManager initialization

async def async_initialize(self, ...):
    # ... existing code ...
    
    # WALManager init - lazy, až když je potřeba
    if self._wal_manager is None and not self._lazy:
        _wal_root = self._db_path.parent if self._db_path else None
        if _wal_root is not None:
            self._wal_manager = WALManager(wal_path=str(_wal_root / "shadow_wal.lmdb"))
            self._wal_manager.initialize()
    
    # DedupManager init - lazy
    if self._dedup_manager is None and not self._lazy:
        self._dedup_manager = DedupManager()
```

#### Krok 4: DuckDB LMDB Shared Path (P2 - Cutting Edge)

DuckDB a LMDB mohou sdílet stejnou cestu pro data, ale ne stejný soubor:

```python
# paths.py - sdílené úložiště pro DuckDB + LMDB
DUCKDB_STORE_ROOT = Path("~/.hledac/duckdb_store")  # DuckDB .duckdb file
LMDB_STORE_ROOT = Path("~/.hledac/lmdb_store")       # LMDB .lmdb directory

# Pro mmap optimalizaci - sdílený parent adresář
SHARED_STORAGE_ROOT = Path("~/.hledac/shared")
```

**Toto neintegruje přímo DuckDB a LMDB** (různé účely), ale umožňuje:
- Sdílený parent adresář pro OS cache locality
- Easier backup/migration
- Unified storage metrics

---

## Invariant Tabulka

| Invariant | Test | Popis |
|----------|------|-------|
| STOR-LAZY-1 | `test_lmdb_subdb_lazy_init` | UnifiedLMDBStore neotevírá mmap v `__init__` |
| STOR-LAZY-2 | `test_duckdb_lazy_wal_init` | WALManager se inicializuje lazy |
| STOR-SHUTDOWN-1 | `test_storage_graceful_shutdown` | Správné pořadí: DuckDB → WAL → LMDB |
| STOR-FAILSAFE-1 | `test_storage_fail_safe` | Všude try/except, žádné exceptions |

---

## Final Command

```bash
pytest tests/test_storage*.py tests/test_duckdb*.py -x -q
```

---

*Generated: 2026-07-24*

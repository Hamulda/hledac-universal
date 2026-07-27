# DuckDB / LanceDB / LMDB Storage Integration

> **Source:** Agent exploration of `knowledge/` + `core/storage_router.py`, July 2026
> **Canonical write:** `DuckDBShadowStore.async_ingest_findings_batch()` — the SOLE write path

---

## 1. Storage Architecture

The system implements a **5-layer hot-to-cold storage stack** (`core/storage_router.py`):

| Layer | Technology | Purpose | M1 8GB Cap |
|-------|-----------|---------|------------|
| **HOT** | SqliteVecStore | float16 embeddings (256d), M1-native | 512 MB |
| **WARM** | LanceDB | IVF-PQ quantized entity embeddings (opt-in) | 8 GB |
| **COLD** | DuckDB | Canonical findings, IOC history, columnar SQL | 16 GB |
| **KEYVALUE** | LMDB | WAL, dedup, q-tables, hot-edges cache | 128 MB |
| **STRING** | diskcache | URLs, HTML, safetensors | 256 GB |

### Data Kind Routing

```
"embedding.float16[256]"  → HOT (SqliteVecStore)
"embedding.float16[384]"  → HOT (SqliteVecStore)
"ioc.findings"            → COLD (DuckDB)
"graph.ioc"              → COLD (DuckDB via DuckPGQGraph)
"graph.edges_hot"         → KEYVALUE (LMDB)
"qtable.federated"        → KEYVALUE (LMDB)
"kv.persistent"           → KEYVALUE (LMDB)
"url.normalized"          → STRING (diskcache)
```

---

## 2. DuckDB — Canonical Write Path

### Primary Entry Point

**File:** `knowledge/duckdb_store.py`
**Class:** `DuckDBShadowStore` (line 1775)
**Canonical write:** `async def async_ingest_findings_batch()` (line 7457)

```python
async def async_ingest_findings_batch(
    self,
    findings: list[CanonicalFinding],
) -> list[FindingQualityDecision | ActivationResult]:
```

> **CRITICAL INVARIANT:** This is the **SOLE canonical write path** for DuckDB.
> No other module writes to `canonical_findings` directly.

### CanonicalFinding DTO

**`knowledge/duckdb_store.py:1418-1459`**

```python
class CanonicalFinding(msgspec.Struct, frozen=True, gc=False):
    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float
    provenance: tuple[str, ...] = ()   # non-optional, default = ()
    payload_text: str | None = None
```

### DuckDB Schema

**`knowledge/duckdb_store.py:1672`** (`_SCHEMA_SQL`):

```sql
CREATE TABLE IF NOT EXISTS canonical_findings (
    id              VARCHAR PRIMARY KEY,
    query           VARCHAR,
    source_type     VARCHAR,
    confidence      DOUBLE,
    ts              DOUBLE,
    provenance_json TEXT,
    payload_text    TEXT,
    UNIQUE (id),
    UNIQUE (query, source_type)
);
CREATE INDEX IF NOT EXISTS idx_canonical_findings_ts ON canonical_findings(ts DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_findings_query ON canonical_findings(query);
```

Additional tables: `shadow_runs`, `sprint_delta`, `source_hit_log`, `sprint_scorecard`,
`target_profiles`, `hypothesis_feedback`, `target_memory`, `dht_metadata`,
`research_sessions`, `entity_observations`, `ioc_cooccurrence`, `research_episodes`,
`hypothesis_tracking`.

### Write Flow

```
Acquisition Lane
    │
    ▼
DuckDBShadowStore.async_ingest_findings_batch()     ← SINGLE CANONICAL WRITE PATH
    │
    ├─► [1] DuckDB INSERT (canonical_findings)
    │         └─► _pending_accepted_findings → Arrow batch → DuckDB
    │
    ├─► [2] LMDB WAL write (shadow_wal.lmdb via LMDBKVStore)
    │
    └─► [3] Background: _graph_ingest_findings()    ← line 2217
              │
              ▼
         DuckPGQGraph.upsert_ioc() / upsert_ioc_batch()
         (extracts IOCs → graph nodes + edges via DuckPGQ)
```

### DuckDB Env Config

| Env Var | Default | Description |
|---------|---------|-------------|
| `HLEDAC_DUCKDB_INPROCESS` | `1` | In-process DuckDB (saves ~200MB RAM vs subprocess) |
| `HLEDAC_DUCKDB_THREADS` | `2` | Thread count, capped at 4 for M1 |
| `HLEDAC_DUCKDB_STORE` | `~/.hledac/duckdb_store` or RAMDISK | Store path |
| `HLEDAC_DUCKDB_RAMDISK_TEMP` | auto | Temp scratch space |
| `HLEDAC_DUCKDB_MIN_FLUSH` | `50` | Minimum batch size before flush |
| `HLEDAC_DUCKDB_MAX_FLUSH_INTERVAL` | `1.0` | Max seconds between flushes |

**Path resolution** (`paths.py:402-403`):
```python
_DUCKDB_STORE_DEFAULT = str(RAMDISK_ROOT / 'duckdb_store') if RAMDISK_ACTIVE else '~/.hledac/duckdb_store'
DUCKDB_STORE_ROOT: Path = Path(os.environ['HLEDAC_DUCKDB_STORE']) if 'HLEDAC_DUCKDB_STORE' in os.environ else Path(_DUCKDB_STORE_DEFAULT)
```

### DuckDB Initialization

- `async_initialize_schema()` — `duckdb_store.py:3931` (applies `_SCHEMA_SQL`)
- `ensure_connected()` — `duckdb_store.py:3975` (legacy sync connect)
- DuckDB thread cap: `min(os.environ_threads, 4)` at `config/settings.py:184`

---

## 3. LanceDB — Entity / Identity Store

### Class

**File:** `knowledge/lancedb_store.py:326`

```python
class LanceDBIdentityStore:
    def __init__(self, uri: str = str(_DEFAULT_URI), orchestrator=None)
```

**Role:** Identity/entity store for semantic similarity search.
Document grounding belongs to `rag_engine` — **not** LanceDB.

### Collection Schema

| Collection | Embedding Dim | Default Engine |
|-----------|--------------|----------------|
| `entities` (LanceDBIdentityStore) | 256d float32 | SqliteVecStore (HOT) |
| `academic_papers` (AcademicPaper) | 384d float32 | FastEmbed BAAI/bge-small-en-v1.5 |

### IVF-PQ Quantization (M1 8GB bounded)

**`knowledge/lancedb_store.py:432-440`**

```python
_ivfpq_enabled: bool = os.environ.get('HLEDAC_LANCEDB_QUANTIZE', '1') != '0'
_ivfpq_num_partitions: int = max(8, min(256, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS', '256'))))
_ivfpq_num_sub_vectors: int = max(4, min(64, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS', '8'))))
_ivfpq_nprobes: int = max(1, min(64, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NPROBES', '8'))))
```

### LanceDB Env Config

| Env Var | Default | Description |
|---------|---------|-------------|
| `HLEDAC_LANCEDB_QUANTIZE` | `1` (ON) | IVF-PQ quantization |
| `HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS` | `256` | IVF-PQ partitions |
| `HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS` | `8` | Sub-vectors per partition |
| `HLEDAC_LANCEDB_IVFPQ_NPROBES` | `8` | Search probes (~3% of 256) |
| `HLEDAC_LANCEDB_STORE` | auto | Custom LanceDB path |

### LanceDB Fallback Chain

```
HLEDAC_VECTORS=lancedb
    │
    ├─► LanceDBIdentityStore (WARM) — opt-in, IVF-PQ quantized
    │
    └─► SqliteVecStore (HOT) — always available, float16 256d, ~5MB
```

---

## 4. LMDB — KeyValue Layer

### Paths

**`paths.py:392-393`**

```python
LMDB_ROOT: Path = DB_ROOT / 'lmdb'         # ~/.hledac/db/lmdb
SPRINT_LMDB_ROOT: Path = LMDB_ROOT / 'sprint'
```

### LMDB Within DuckDB

**`knowledge/duckdb_store.py:1797-1801, 1919-1920`**

| LMDB Key | Purpose | Path |
|----------|---------|------|
| `shadow_wal.lmdb` | Write-Ahead Log for DuckDB durability | `SPRINT_LMDB_ROOT / 'shadow_wal.lmdb'` |
| `shadow_dedup.lmdb` | Finding deduplication bloom filter state | `SPRINT_LMDB_ROOT / 'shadow_dedup.lmdb'` |

Both accessed via `LMDBKVStore` (`tools/lmdb_kv.py:43`) or `AsyncLMDBKVStore` (`tools/lmdb_kv.py:241`).

### Other LMDB Uses

- **`knowledge/_query_cache.py`** (line 42): L2 query cache — 5000 entries, TTL 300s, 16 MB map
- **`knowledge/sprint_seeds_store.py`** (line 33): Cross-sprint quantum pathfinder seeds — 256 MB map

### LMDB KV Store API

**`tools/lmdb_kv.py`**

```python
class LMDBKVStore:                    # line 43
    def get(self, key: str) -> dict | None
    def put(self, key: str, value: dict) -> bool
    def put_many(self, items: list[tuple[str, dict]]) -> list[bool]  # single txn

class AsyncLMDBKVStore:               # line 241
    async def get(self, key: str) -> dict | None
    async def put(self, key: str, value: dict) -> bool
```

**`paths.open_lmdb()`** (line 325) — separate context manager for raw LMDB opens.

---

## 5. DuckPGQGraph — IOC Graph

**File:** `knowledge/graph_service.py`

```python
class DuckPGQGraph:
    async def upsert_ioc(self, ioc: IOC, ...)     # line 135
    async def upsert_ioc_batch(self, iocs: list[IOC], ...)  # line 233
    async def find_connected(self, node_id: str, ...) -> list[str]  # line 450
```

Fed by `_graph_ingest_findings()` in `duckdb_store.py:2217` after canonical batch write.

---

## 6. Storage Router

**File:** `core/storage_router.py`

```python
class StorageRouter:
    """5-layer hot-to-cold storage coordinator"""
    # Manages lifecycle via async context manager
    # Dispatches to correct backend by data_kind

async def get_storage_router() -> StorageRouter:  # line 549
```

Protocol contract: `AsyncStorageBackendProtocol` (line 78).

---

## 7. Key Files Summary

| File | Class/Function | Line | Role |
|------|---------------|------|------|
| `knowledge/duckdb_store.py` | `DuckDBShadowStore` | 1775 | Canonical SQL store, WAL, dedup |
| `knowledge/duckdb_store.py` | `CanonicalFinding` | 1418 | msgspec DTO for all findings |
| `knowledge/duckdb_store.py` | `async_ingest_findings_batch()` | 7457 | **Single canonical write** |
| `knowledge/duckdb_store.py` | `_SCHEMA_SQL` | 1672 | Full DuckDB schema |
| `knowledge/duckdb_store.py` | `_graph_ingest_findings()` | 2217 | Feeds graph post-write |
| `knowledge/lancedb_store.py` | `LanceDBIdentityStore` | 326 | Entity identity / similarity |
| `knowledge/lancedb_store.py` | `_ivfpq_*` params | 432-440 | IVF-PQ config |
| `knowledge/graph_service.py` | `DuckPGQGraph` | varies | IOC graph with DuckPGQ |
| `core/storage_router.py` | `StorageRouter` | line TBD | 5-layer coordinator |
| `core/storage_router.py` | `AsyncStorageBackendProtocol` | 78 | Protocol contract |
| `paths.py` | `LMDB_ROOT` | 392 | LMDB base path |
| `paths.py` | `DUCKDB_STORE_ROOT` | 403 | DuckDB base path |
| `paths.py` | `open_lmdb()` | 325 | LMDB context manager |
| `tools/lmdb_kv.py` | `LMDBKVStore` | 43 | Sync LMDB wrapper |
| `tools/lmdb_kv.py` | `AsyncLMDBKVStore` | 241 | Async LMDB wrapper |
| `config/settings.py` | `DuckDBSettings` | 171-187 | DuckDB config dataclass |

---

## 8. M1 8GB-Specific Constraints

| Constraint | Value | Location |
|-----------|-------|----------|
| DuckDB threads cap | `min(ENV, 4)` | `config/settings.py:184` |
| DuckDB in-process default | `True` (saves ~200MB) | `config/settings.py:183` |
| LanceDB IVF-PQ default | ON | `lancedb_store.py:432` |
| LanceDB IVF-PQ partitions | 256 (M1-bounded) | `lancedb_store.py:434` |
| LanceDB IVF-PQ nprobes | 8 (~3% of 256) | `lancedb_store.py:439` |
| LMDB WAL map size | managed via `lmdb_map_size_mb` | `storage_config.py:147` |
| HOT tier cap | 512 MB (SqliteVecStore) | `storage_router.py` |
| KEYVALUE tier cap | 128 MB LMDB | `storage_router.py` |

---

## 9. Critical Invariants

1. **`async_ingest_findings_batch` is the SOLE canonical write path** — no other module writes to `canonical_findings` directly
2. **DuckDB feeds the graph** via `_graph_ingest_findings()` as a background task after batch persists
3. **LMDB is internal to DuckDBShadowStore** — used for WAL and dedup; not a separate canonical store
4. **LanceDB is opt-in** (via `HLEDAC_VECTORS=lancedb`); primary HOT store is SqliteVecStore
5. **DuckDB is always-on, always-in-process** by default (`HLEDAC_DUCKDB_INPROCESS=1`)

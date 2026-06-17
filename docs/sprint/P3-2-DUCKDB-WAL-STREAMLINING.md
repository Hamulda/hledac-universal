# P3-2: DuckDB WAL Streaming + Read Replicas

## Current State

### Write Path
```
async_ingest_findings_batch()
  └─→ _sync_insert_findings_bulk() [ThreadPoolExecutor, 1 worker]
        └─→ insert_findings_bulk_arrow() [Arrow zero-copy] OR executemany()
  └─→ WALManager.wal_write_finding() [LMDB, separate thread]
  └─→ On DuckDB fail: wal_write_pending_sync_marker() [LMDB pending recovery]
```

### Connection Architecture
- `_file_conn` — persistent single write connection (reused, not thread-safe per se, single worker access enforced)
- `_write_executor` (1 worker) — serialize writes
- `_read_executor` (2 workers) — analytics queries
- `_wal_executor` (1 worker) — LMDB WAL operations
- `_duckdb_arrow_executor` (1 worker) — Arrow ingest

### WALManager (wal.py)
- LMDB-backed WAL for pending sync markers + deadletters
- NOT DuckDB's native WAL — separate LMDB store
- Scope: `finding:{id}`, `pending_duckdb_sync:{id}`, `deadletter_ingest:{id}`

### DuckDB Native WAL
- **NOT enabled** currently
- DuckDB 1.0+: `PRAGMA enable_wal=true` → creates `.wal` file
- Checkpoint: `PRAGMA checkpoint` → flush WAL to main DB
- Read replica: `ATTACH DATABASE 'db' AS replica READ ONLY`

---

## Problem Analysis

### 1. DuckDB Native WAL (Write-Ahead Log)
**What it gives:**
- Crash safety: WAL replay on restart (like LMDB but DuckDB-native)
- Durability levels: `PRAGMA synchronous` (0=off, 1=normal, 2=full)
- Better crash recovery than current LMDB pending-marker approach

**What it doesn't give:**
- Not true streaming replication (no real replica lag < 1s)
- WAL files grow unbounded without CHECKPOINT
- CHECKPOINT is blocking on the connection

**M1 8GB considerations:**
- `PRAGMA wal_autocheckpoint=1000` (pages, ~16MB default) — bounded
- WAL file: 64MB map in LMDB ≈ similar memory for DuckDB WAL
- Checkpoint frequency: balance durability vs I/O

### 2. Read Replicas
**What it gives:**
- Analytics queries don't block writes
- Parallel read path for concurrent lane queries
- `READ ONLY` connections on replica DB

**What it doesn't give:**
- Replica lag (eventual consistency, ms-s)
- Not true streaming (WAL-based, not logical replication)
- Extra file descriptor + storage overhead

**M1 8GB considerations:**
- Extra DuckDB connection ~10-20MB
- File-backed replica doubles storage temporarily during CHECKPOINT
- `ATTACH` is read-only but still uses memory for query planning

---

## Implementation Plan

### Phase 1: DuckDB Native WAL (DuckDBShadowStore)

**File:** `knowledge/duckdb_store.py`

```python
# In _init_connection() — after existing PRAGMAs:
conn.execute("PRAGMA enable_wal=true")
conn.execute("PRAGMA wal_autocheckpoint=1000")  # ~16MB, M1 bounded
conn.execute("PRAGMA synchronous=1")  # normal — crash-safe, not full-block
```

**Checkpoint strategy:**
```python
# Background task — every N inserts or T seconds:
async def _checkpoint_task():
    while True:
        await asyncio.sleep(60)  # 60s checkpoint interval
        await loop.run_in_executor(self._executor, self._sync_checkpoint)

def _sync_checkpoint():
    if self._db_path:
        conn = self._file_conn
    else:
        conn = self._persistent_conn
    try:
        conn.execute("PRAGMA checkpoint")
    except Exception:
        pass  # fail-safe
```

**Integration with existing WALManager:**
- LMDB WALManager stays (pending sync markers, deadletters)
- DuckDB native WAL adds crash-safety for DuckDB itself
- Dual WAL: LMDB (DuckDB failure tracking) + DuckDB WAL (DuckDB crash recovery)

### Phase 2: Read Replica (DuckDBShadowStore)

**Architecture:**
```
Primary: db_path (write + read via _file_conn)
Replica: db_path + "_replica" (read via _read_executor)
```

**Implementation:**

```python
# In __init__:
self._replica_conn: Any | None = None
self._replica_path: Path | None = None

# In _init_connection() — after primary init:
if self._db_path:
    self._replica_path = self._db_path.parent / f"{self._db_path.stem}_replica{self._db_path.suffix}"
    # Replica is just the same DB file — DuckDB can read from it while primary writes
    # No ATTACH needed for file-based — concurrent read via separate connection

# Read path — analytics queries use replica when available:
async def async_query_recent_findings(self, limit: int = 10) -> list[dict[str, Any]]:
    if self._replica_conn is not None and not self._closed:
        conn = self._replica_conn  # read from replica
    else:
        conn = self._file_conn  # fallback to primary
    return await loop.run_in_executor(self._read_executor, self._sync_query_findings, limit, conn)
```

**Replica refresh:**
- DuckDB file-based: replica IS the same file (readers see committed data via OS buffering)
- For true replica lag < 1ms: would need DuckDB 1.1+ logical replication (not implemented)
- Current approach: shared file, separate connection — works for analytics

### Phase 3: WAL Streaming (Future)

**For true streaming WAL (not in scope for P3-2):**
- DuckDB 1.1+ experimental: `EXPORT DATABASE TO 'dir' (FORMAT=csv)`
- Or: custom WAL tail via `pg_wal` equivalent (DuckDB stores WAL in `.wal` file)
- Or: PostgreSQL-compatible logical replication (roadmap)

**P3-2 scope:** DuckDB WAL + checkpoint task + read replica via shared file

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| DuckDB WAL mode | `enable_wal=true` | Crash-safe, DuckDB-native, no extra deps |
| Checkpoint strategy | 60s interval or N records | Bounded WAL growth, M1 safe |
| Sync level | `synchronous=1` (normal) | Crash-safe without full-block latency |
| Read replica | Shared file, separate conn | Simple, no replication lag, M1 cheap |
| Replica path | `{stem}_replica{suffix}` | Side-by-side, easy discovery |
| WAL autocheckpoint | 1000 pages (~16MB) | M1 8GB bounded |

---

## Files to Modify

| File | Change |
|------|--------|
| `knowledge/duckdb_store.py` | WAL PRAGMAs, checkpoint task, replica connection |
| `knowledge/wal.py` | No changes (LMDB WAL independent) |
| `tests/probe_pXX_duckdb_wal/` | New probe tests |

---

## Invariants (P3-2)

| Test | Description |
|------|-------------|
| `test_duckdb_wal_enabled` | `PRAGMA enable_wal=true` persists after reconnect |
| `test_duckdb_wal_checkpoint` | `PRAGMA checkpoint` succeeds without blocking reads |
| `test_duckdb_wal_autocheckpoint` | WAL autocheckpoint at 1000 pages |
| `test_duckdb_wal_crash_recovery` | WAL replay recovers uncommitted data after kill |
| `test_read_replica_conn` | `_replica_conn` is separate from `_file_conn` |
| `test_read_replica_query` | Analytics queries use replica when available |
| `test_replica_consistency` | Replica sees same committed data as primary |
| `test_wal_bounded_growth` | WAL file stays ≤ 64MB under sustained write load |

---

## M1 8GB Safety

- WAL map: 16MB autocheckpoint × 4 = 64MB max WAL file
- Replica connection: +10-20MB
- Checkpoint task: no extra memory (just timer)
- Total: ~80MB overhead — within budget

---

## Dependencies

None — DuckDB native WAL, no new packages.

---

## References

- DuckDB WAL: https://duckdb.org/docs/configuration/pragmas#wal-related-pragmas
- DuckDB 1.1 replication: https://duckdb.org/docs/guides/performance/how_to_speed_up_queries
- Python 3.14 asyncio stream: https://docs.python.org/3.14/library/asyncio.stream.html
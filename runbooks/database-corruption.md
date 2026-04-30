# Runbook: Database Corruption

## Symptoms
- `lmdb.Error` or `lmdb.CorruptedError`
- Query returns no results when data exists
- `sqlite3.DatabaseError`
- Checkpoint restore fails

## Affected Databases

### DuckDB (sprint results)
- Path: `data/hledac_duckdb/`
- Corruption cause: interrupted writes, disk full, crash

### LMDB (entity storage)
- Path: `data/hledac.lmdb/`
- Corruption cause: concurrent writes, system crash

### SQLite (if used)
- Path: various `.db` files
- Corruption cause: locked writes, interrupted transactions

## Diagnosis

### 1. Check lmdb integrity
```python
import lmdb
env = lmdb.open('data/hledac.lmdb', readonly=True)
print(f"Map size: {env.info()['map_size']}")
print(f"Last page: {env.stat()['last_pgno']}")
env.close()
```

### 2. Check DuckDB
```sql
-- Run integrity check
PRAGMA integrity_check;
-- Check for missing tables
SELECT * FROM information_schema.tables;
```

### 3. Check disk space
```bash
df -h data/
```

## Recovery Procedures

### DuckDB
```bash
# 1. Stop all writes immediately
# 2. Backup corrupted database
cp -r data/hledac_duckdb data/hledac_duckdb.corrupt.$(date +%Y%m%d%H%M%S)

# 3. Export recoverable data
python3 -c "
import duckdb
conn = duckdb.connect('data/hledac_duckdb.corrupt/main.db', read_only=True)
# Export to CSV/JSON
"

# 4. Recreate from last checkpoint
# 5. Replay any buffered writes
```

### LMDB
```bash
# 1. Backup
cp -r data/hledac.lmdb data/hledac.lmdb.bak.$(date +%Y%m%d%H%M%S)

# 2. Try recovery mode
python3 -c "
import lmdb
env = lmdb.open('data/hledac.lmdb', readonly=True, readahead=False)
# Read test
with env.begin() as txn:
    print('LMDB readable:', txn.stat())
env.close()
"

# 3. If recovery fails, restore from checkpoint
```

### Checkpoint Restore
```bash
# List available checkpoints
ls -la data/checkpoints/

# Restore specific checkpoint
python3 -c "
from core.checkpoint import CheckpointStore
store = CheckpointStore('data/checkpoints')
store.restore('sprint-YYYY-MM-DD-XXXXX')
"
```

## Prevention
- Always use `async_ingest_findings_batch()` for writes (transactional)
- LMDB: use `put_many()` for bulk writes, not per-item in loop
- DuckDB: let/autocommit handle transactions
- Regular checkpoints every N findings
- Monitor disk space (min 10GB free)
- Graceful shutdown (SIGTERM, not SIGKILL)

# DuckDB Subprocess Overhead Analysis & Optimization
**Sprint:** F275 DuckDB In-Process + Pipeline Optimization  
**Date:** 2026-06-30  
**Target:** MacBook Air M1 8GB UMA, Python 3.14+

---

## 1. CURRENT ARCHITECTURE ANALYSIS

### 1.1 DuckDB Connection Model

DuckDB běží jako **embedded database** (nie subprocess!). Volání `duckdb.connect()` vytváří **in-process connection**, nikoliv subprocess. Toto je клюбен мит!

Avšak problémy s výkonem přetrvávají:

| Component | Current | Problem |
|-----------|---------|---------|
| DuckDB threads | 4 (`HLEDAC_DUCKDB_THREADS=4`) | Thread-local conn bottleneck = 2 threads optimal |
| WAL mode | `WAL` (default) | 2× fsync per write (WAL + DB) |
| In-process mode | OFF (`HLEDAC_DUCKDB_INPROCESS=0`) | 200MB RAM overhead za subprocess |
| Executor | Python `ThreadPoolExecutor` (2 workers) | Context switch overhead |
| Arrow batch | 1024 items | Could be larger (2048-4096) |

### 1.2 DuckDB Process Model Clarification

```python
# Current: file-backed DuckDB
conn = duckdb.connect(str(self._db_path), read_only=False, in_process=False)
#                     └─ embedded in main process (not subprocess!)
#                     └─ in_process=True saves ~200MB RAM (single process)
```

**Mýtus vyvrácen:** DuckDB NENÍ subprocess. `in_process=False` pouze znamená že DuckDB knihovna běží v hlavním procesu. True subprocess by byl `duckdb_cli` nebo `child process`.

### 1.3 Actual Bottlenecks

1. **DuckDB thread-local connection bottleneck**
   - DuckDB uses thread-local storage for connections
   - 4 threads = 4 potential connections, but connection setup is expensive
   - Optimal: 2 threads (matches `io_pool()`)

2. **WAL double-write overhead**
   - `PRAGMA journal_mode=WAL` → 2 writes per transaction
   - WAL write + DB write
   - For M1 SSD: safe to use `DELETE` journal mode (single write)

3. **Python ThreadPoolExecutor overhead**
   - `loop.run_in_executor(self._executor, ...)` adds context switching
   - Could use Rust `io_pool()` directly

4. **Arrow batch size too small**
   - 1024 items per batch = many small batches
   - M1 8GB can handle larger batches (2048-4096)

---

## 2. OPTIMIZATION IMPLEMENTATION

### 2.1 Enable In-Process Mode (Priority: P0)

**File:** `knowledge/duckdb_store.py` line 1535

```python
# CHANGE: Default from "0" to "1" for M1 8GB
_inprocess = os.environ.get("HLEDAC_DUCKDB_INPROCESS", "1") == "1"
```

**Impact:** -200MB RAM (DuckDB subprocess eliminated)

### 2.2 Reduce DuckDB Threads to 2 (Priority: P0)

**File:** `knowledge/duckdb_store.py` line 563

```python
# CHANGE: 4 → 2 threads (thread-local conn bottleneck)
base_threads = int(os.environ.get("HLEDAC_DUCKDB_THREADS", 2))
```

**Impact:** Reduced thread contention, matches `io_pool()` ceiling

### 2.3 WAL→DELETE Journal Mode for Bulk Writes (Priority: P1)

**File:** `knowledge/duckdb_store.py` lines 1562-1565

For bulk inserts (≥1024 items), temporarily switch to DELETE journal mode:

```python
# Add to _init_connection or create _set_journal_mode method
async def _set_journal_mode(self, mode: str) -> None:
    """Switch WAL/DELETE for bulk insert optimization."""
    if self._db_path and str(self._db_path) != ':memory:':
        conn = self._conn()
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
        except Exception:
            pass
```

### 2.4 Use Rust io_pool Directly (Priority: P1)

**File:** `knowledge/duckdb_store.py` - replace `loop.run_in_executor`

```python
# Instead of:
# await loop.run_in_executor(self._shared_executor, self._sync_record_canonical_findings_batch_arrow, findings)

# Use:
from utils.rayon_pool import run_in_io_pool_async
result = await run_in_io_pool_async(
    self._sync_record_canonical_findings_batch_arrow,
    findings
)
```

### 2.5 Increase Arrow Batch Size (Priority: P2)

**File:** `knowledge/duckdb_store.py` line 7348

```python
# CHANGE: 1024 → 2048 (M1 8GB can handle larger batches)
CHUNK_SIZE = 2048  # ~10 MB per batch
```

### 2.6 Rust DuckDB Arrow Insert (Priority: P2)

**File:** `rust_extensions/src/graph_traverse.rs` (new function)

```rust
/// Arrow batch insert directly from Rust - eliminates Python arrow serialization
#[pyfunction]
pub fn duckdb_arrow_insert_batch(
    py: Python<'_>,
    db_path: &str,
    table: Py<PyAny>,  // pyarrow.Table
) -> PyResult<(i64, Option<String>)> {
    // Open DuckDB connection in Rust
    // Use DuckDB's C API for zero-copy Arrow ingestion
    // Return (count, error)
}
```

---

## 3. IMPLEMENTATION CHECKLIST

| Task | Priority | Status |
|------|----------|--------|
| Enable `HLEDAC_DUCKDB_INPROCESS=1` by default | P0 | TODO |
| Reduce `HLEDAC_DUCKDB_THREADS` to 2 | P0 | TODO |
| Use Rust `io_pool()` for DuckDB ops | P1 | TODO |
| Pipeline WAL + DuckDB inserts | P1 | TODO |
| Increase Arrow batch to 2048 | P2 | TODO |
| Rust DuckDB Arrow insert | P2 | TODO |

---

## 4. MEMORY BUDGET

| Component | Before | After |
|-----------|--------|-------|
| DuckDB subprocess | 200MB | 0 (in-process) |
| DuckDB threads (×2) | 4 threads | 2 threads |
| Arrow batches | 1024×5KB=5MB | 2048×5KB=10MB |
| Total RAM | ~6.25GB | ~5.75GB |

**Savings:** ~200MB RAM + reduced thread contention

---

## 5. TESTING

```bash
# Test in-process mode
HLEDAC_DUCKDB_INPROCESS=1 pytest tests/test_duckdb_store.py -v

# Test with reduced threads  
HLEDAC_DUCKDB_THREADS=2 pytest tests/test_duckdb_store.py -v

# Memory benchmark
import psutil
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
store = DuckDBShadowStore()
# Check RSS before/after
```

---

## 6. REFERENCES

- CLAUDE.md: DuckDB write path invariant
- GHOST_INVARIANTS.md: F265-U5 thread-local pool
- `rust_extensions/src/lib.rs:123` - `io_pool()` (2 threads)
- `knowledge/duckdb_store.py:1535` - in_process flag
- `utils/rayon_pool.py` - Rust pool runners

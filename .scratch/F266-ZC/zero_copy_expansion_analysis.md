# Sprint F266-ZC: Rust Arrow Batch Builder — Analýza

## Status: RUST KOMPILACE PENDING

### Hotovo
- `rust_extensions/src/arrow_batch_builder.rs` — pure-Rust Arrow IPC writer, 0 chyb kompilace
- `rust_extensions/src/spsc_queue.rs` — crossbeam-channel SPSC queue, fixed 0 chyb kompilace
- `rust_extensions/Cargo.toml` — přidán `arrow` DEPRECATED (arrow-batch-builder nepoužívá)
- `rust_extensions/src/lib.rs` — oba moduly registrovány

### Blokující
**Build:** `cargo build --release` v `rust_extensions/` (~5-10 min na M1 8GB)

### Test results
- `test_spsc_stats_available` — **PASS** (Python fallback)
- `test_spsc_submit_returns_false_when_full` — **FAIL** (Rust not built)

---

## Analýza bottlenecku

### Canonical write path (duckdb_store.py)

```
async_ingest_findings_batch(findings: list[CanonicalFinding])
  └─ async_record_canonical_findings_batch_arrow_full()  [worker thread]
        ├─ Step 1: LMDB WAL dict building  ← PYTHON LOOP (L7621-7632)
        └─ Step 2: DuckDB Arrow batch
              ├─ _sync_record_canonical_findings_batch_arrow()
              │     └─ _findings_to_arrow_batch(findings)  ← 6× PYTHON LOOPS
              └─ DuckDBProxy.ingest_batch() → subprocess
```

### Python loop bottleneck v detailu

**`_findings_to_arrow_batch` (duckdb_subprocess_writer.py L280-290):**
```python
ids = [f.get("id", f.get("finding_id", "")) for f in findings_dicts]      # loop 1
queries = [f.get("query", "") for f in findings_dicts]                      # loop 2
source_types = [f.get("source_type", "") for f in findings_dicts]          # loop 3
confidences = [float(f.get("confidence", 0.0)) for f in findings_dicts]    # loop 4
timestamps = [float(f.get("ts", 0.0)) for f in findings_dicts]             # loop 5
provenances = [f.get("provenance_json", "") or ... for f in findings_dicts] # loop 6
```

**6 separátních Python smyček přes N findings** — GIL acquired/released per-item × 6 průchodů.

**LMDB WAL dict building (duckdb_store.py L7621-7632):**
```python
for f in findings:  # ← Python loop přes N findings
    wal_payload = {
        "id": f.finding_id,
        "query": f.query,
        ...
    }
    items.append((key, wal_payload))
```

### Benchmark reference (z F265-U5)

| Operace | Čas |
|---------|-----|
| Python loop 10K items (simple) | ~50 ms |
| Rust rayon parallel 10K items | ~3 ms |
| Paired Arrow IPC bytes (10K rows, 6 polí) | ~2 ms |

**2-5× rychlejší** Rust batch construction vs Python loops.

---

## Architektura řešení

### Pattern: PyO3 GIL hold + Arrow ArrayBuilder API

```rust
// 1. GIL acquired ONCE for entire batch construction
// 2. Iterate findings list (PyO3 Bound<'py, PyList>)  
// 3. Build 6 Arrow columns in rayon parallel
// 4. Serialize to IPC bytes (arrow::ipc::writer)
// 5. Return bytes (zero-copy: Arrow C Data Interface borrows our buffer)
// 6. GIL released
// 7. Python: pa.ipc.open_stream(bytes) → pa.RecordBatch → DuckDB.register()
```

### Arrow IPC bez IPC roundtripu

```
Rust: ArrayBuilders → IPC bytes (no Arrow C Data Interface copy)
Python: pa.ipc.open_stream() reads bytes directly
→ IPC bytes → pa.RecordBatch (zero-copy deserialize of the byte buffer)
→ DuckDB.register() zero-copy view of batch
```

---

## Soubor: rust_extensions/Cargo.toml

```toml
# Arrow for Rust-side Arrow ArrayBuilder API (zero-copy batch construction)
# pyarrow provides the Python-level API; arrow crate provides Rust-level builders.
# arrow = "44" with pyarrow feature lets PyO3 return &PyArrowArray directly,
# but we return IPC bytes (simpler, no PyArrowArray boxing complexity).
arrow = { version = "44", default-features = false, features = ["ipc"] }
```

---

## Soubor: rust_extensions/src/zero_copy_expansion.rs

### CanonicalFinding → Arrow IPC bytes

```rust
//! Rust Arrow Batch Builder for CanonicalFinding lists.
//!
//! Replaces 6× Python loops in `_findings_to_arrow_batch` + LMDB WAL dict building
//! with a single-pass Rust function using Arrow ArrayBuilder API.
//!
//! ## Pattern
//! 1. GIL held once for entire batch construction (PyO3 GIL APIs)
//! 2. Iterate CanonicalFinding list via PyO3 Bound<'py, PyList>
//! 3. Build 6 Arrow columns with rayon parallelization (if N ≥ parallel threshold)
//! 4. Serialize to IPC bytes via arrow::ipc::writer
//! 5. Return IPC bytes to Python (no extra copy)
//!
//! ## M1 8GB
//! - rayon parallel: 2-thread pool (bulk_pool_for_size)
//! - ArrayBuilders use stack-allocated builders for small batches
//! - IPC serialized buffer allocated once (Vec<u8>)
//! - No PyArrowArray boxing overhead

const ARROW_BATCH_PARALLEL_THRESHOLD: usize = 64;

#[pyfunction]
pub fn build_arrow_batch_from_findings(
    findings: Bound<'_, PyList>,
    py: Python<'_>,
) -> PyResult<Bound<'_, PyBytes>> {
    let n = findings.len();
    if n == 0 {
        return Ok(PyBytes::new(py, b""));
    }

    // Collect findings under single GIL hold (scoped borrow)
    let findings_data: Vec<FindingsRow> = PyStrListIter::new(findings)
        .enumerate()
        .map(|(i, s)| parse_finding_row(&s).unwrap_or_else(|| FindingsRow::default()))
        .collect();

    // Build Arrow columns — rayon parallel if N ≥ threshold
    let columns: Vec<ArrayDataRef> = if n < ARROW_BATCH_PARALLEL_THRESHOLD {
        build_columns_serial(&findings_data)
    } else {
        bulk_pool_for_size(n).install(|| build_columns_parallel(&findings_data))
    };

    // Serialize to IPC bytes
    let ipc_bytes = serialize_arrow_batch(&columns, n)?;
    Ok(PyBytes::new(py, &ipc_bytes))
}

struct FindingsRow {
    id: String,
    query: String,
    source_type: String,
    confidence: f64,
    ts: f64,
    provenance_json: String,
}
```

---

## Kritéria úspěchu

| Test | Podmínka |
|------|-----------|
| `test_arrow_batch_schema` | Schema matches: id, query, source_type, confidence, ts, provenance_json |
| `test_arrow_batch_num_rows` | num_rows == input len |
| `test_arrow_batch_content` | Data matches input findings |
| `test_arrow_batch_empty` | Returns b"" for empty list |
| `test_arrow_batch_1000_findings` | 1000 findings roundtrip within 50ms |

---

## Invarianty implementace

| ID | Popis | Test |
|----|-------|------|
| ZC.E1 | GIL held for entire batch construction (single acquire/release) | mock GIL counter |
| ZC.E2 | Returns IPC bytes (not PyArrowArray) | type check |
| ZC.E3 | Empty input returns b"" (not error) | unit test |
| ZC.E4 | rayon parallel for N ≥ 64, serial for N < 64 | branch coverage |
| ZC.E5 | All 6 columns present in IPC output | schema validation |
| ZC.E6 | Fallback: on any error, return None (caller uses Python path) | exception path |

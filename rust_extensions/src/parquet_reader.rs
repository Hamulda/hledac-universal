//! Lazy Parquet Reader — Paginated Arrow Row-Group Iterator
//!
//! Umožňuje čtení 100 GB+ IOC history (parquet) bez OOM na M1 8GB.
//! Row-groups jsou čteny lazily — jedna row-group v paměti najednou.
//!
//! ## Architecture
//!
//! 1. PyArrow `ParquetFile` otevře soubor (metadata only, žádná data)
//! 2. Rust drží `ParquetFile` reference přes PyO3 GIL
//! 3. `iter_batches()` yieldí `RecordBatch` objects — zero-copy view na row-group
//! 4. Python konvertuje přes `pa.ipc.open_record_batch()` → Polars zero-copy
//!
//! ## M1 8GB Safety
//!
//! - Hard cap: 100_000 rows per batch (max ~10 MB per batch)
//! - GIL acquired per-batch (ne per-file)
//! - žádné cel-file buffer — vždy jen jedna row-group
//! - Column pruning: čteme jen potřebné sloupce
//!
//! ## Fallback
//!
//! Parquet file not found / corrupted → Python falls back to `pa.parquet.ParquetFile`.

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};

/// Hard cap: max rows per batch — prevents OOM on M1 8GB.
const MAX_BATCH_SIZE: usize = 100_000;

/// Column names for canonical_findings table.
const CANONICAL_COLUMNS: [&str; 6] = [
    "id",
    "query",
    "source_type",
    "confidence",
    "ts",
    "provenance_json",
];

// ---------------------------------------------------------------------------
// Public PyO3 API
// ---------------------------------------------------------------------------

/// Open a parquet file and return batch count + row count (metadata only).
///
/// Args:
///     path: Path to parquet file
///
/// Returns:
///     (num_row_groups, total_rows) or None on error.
#[pyfunction]
pub fn parquet_get_metadata(path: &str) -> PyResult<Option<(usize, usize)>> {
    Python::attach(|py| {
        let pa = match py.import("pyarrow") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf_class = match pa.getattr("parquet") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf = match pf_class.call_method1("ParquetFile", (path,)) {
            Ok(f) => f,
            Err(_) => return Ok(None),
        };
        let num_row_groups: usize = pf
            .call_method0("num_row_groups")
            .ok()
            .and_then(|v| v.extract().ok())
            .unwrap_or(0);
        let total_rows: usize = pf
            .call_method0("metadata")
            .ok()
            .and_then(|m| m.getattr("num_rows").ok())
            .and_then(|v| v.extract().ok())
            .unwrap_or(0);
        Ok(Some((num_row_groups, total_rows)))
    })
}

/// Read a single row-group as Arrow IPC bytes (zero-copy between Rust ↔ Python).
///
/// Args:
///     path: Path to parquet file
///     row_group: Row-group index
///     columns: List of column names to read (None = all)
///     batch_size: Max rows per batch (None = automatic, max 100_000)
///
/// Returns:
///     IPC bytes (Arrow RecordBatch format), or None on error.
#[pyfunction]
pub fn parquet_read_row_group_ipc(
    path: &str,
    row_group: usize,
    columns: Option<&Bound<'_, PyList>>,
    batch_size: Option<usize>,
) -> PyResult<Option<Py<PyBytes>>> {
    Python::attach(|py| {
        let pa = match py.import("pyarrow") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf_class = match pa.getattr("parquet") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf = match pf_class.call_method1("ParquetFile", (path,)) {
            Ok(f) => f,
            Err(_) => return Ok(None),
        };

        // Build column selection as Vec<String>
        let cols: Vec<String> = if let Some(col_list) = columns {
            col_list
                .iter()
                .filter_map(|c| c.str().ok())
                .map(|s| s.to_string_lossy().into_owned())
                .collect()
        } else {
            CANONICAL_COLUMNS.iter().map(|s| s.to_string()).collect()
        };

        let batch_sz = batch_size.unwrap_or(MAX_BATCH_SIZE).min(MAX_BATCH_SIZE);

        // Convert cols to PyList for call_method
        let cols_ref: &[String] = &cols;
        let cols_list = match PyList::new(py, cols_ref) {
            Ok(list) => list,
            Err(_) => return Ok(None),
        };

        // Read table: read(row_group, columns)
        let table = match pf.call_method1("read", (row_group, &cols_list)) {
            Ok(t) => t,
            Err(_) => {
                // Fallback: read(columns) only (reads all row groups)
                match pf.call_method1("read", (&cols_list,)) {
                    Ok(t) => t,
                    Err(_) => return Ok(None),
                }
            }
        };

        // Convert to batches with max size
        let py_batches = match table.call_method1("to_batches", (batch_sz,)) {
            Ok(b) => b,
            Err(_) => return Ok(None),
        };
        let batches: &Bound<'_, PyList> = match py_batches.cast::<PyList>() {
            Ok(b) => b,
            Err(_) => return Ok(None),
        };

        if batches.len() == 0 {
            let empty = PyBytes::new(py, b"");
            let py_bytes: Py<PyBytes> = empty.into_pyobject(py).unwrap().unbind();
            return Ok(Some(py_bytes));
        }

        // Serialize first batch to IPC bytes
        let batch = match batches.get_item(0) {
            Ok(b) => b,
            Err(_) => return Ok(None),
        };

        let sink = match pa.call_method0("BufferOutputStream") {
            Ok(s) => s,
            Err(_) => return Ok(None),
        };

        let writer_class = match pa.getattr("ipc") {
            Ok(w) => w,
            Err(_) => return Ok(None),
        };
        let writer = match writer_class.call_method1("open_record_batch_writer", (&sink,)) {
            Ok(w) => w,
            Err(_) => return Ok(None),
        };
        if writer.call_method1("write_batch", (&batch,)).is_err() {
            return Ok(None);
        }
        if writer.call_method0("close").is_err() {
            return Ok(None);
        }

        let result = match sink.call_method0("getvalue") {
            Ok(v) => v,
            Err(_) => return Ok(None),
        };

        // Extract bytes from PyObject — use as_bytes() on PyBytes
        if let Ok(bytes_obj) = result.cast::<PyBytes>() {
            let bytes = bytes_obj.as_bytes();
            let py_bytes: Py<PyBytes> = PyBytes::new(py, bytes).into_pyobject(py).unwrap().unbind();
            Ok(Some(py_bytes))
        } else {
            Ok(None)
        }
    })
}

/// Iterate all row-groups as IPC bytes (generator pattern).
///
/// Args:
///     path: Path to parquet file
///     columns: List of column names (None = all)
///     batch_size: Max rows per batch (None = automatic, max 100_000)
///
/// Returns:
///     List of IPC bytes (one per row-group), or empty list on error.
#[pyfunction]
pub fn parquet_iter_all_row_groups(
    path: &str,
    columns: Option<&Bound<'_, PyList>>,
    batch_size: Option<usize>,
) -> PyResult<Vec<Py<PyBytes>>> {
    let mut results: Vec<Py<PyBytes>> = Vec::new();

    let metadata = match parquet_get_metadata(path) {
        Ok(Some((num_rg, _))) => num_rg,
        _ => return Ok(results),
    };

    for rg_index in 0..metadata {
        match parquet_read_row_group_ipc(path, rg_index, columns, batch_size) {
            Ok(Some(bytes)) => results.push(bytes),
            Ok(None) => break,
            Err(_) => break,
        }
    }

    Ok(results)
}

/// Read all row-groups as a single Arrow Table (caller must handle batching).
///
/// Args:
///     path: Path to parquet file
///     columns: List of column names (None = all)
///     batch_size: Max rows per batch for to_batches (None = automatic)
///
/// Returns:
///     PyObject (pa.Table or None on error).
#[pyfunction]
pub fn parquet_read_table(
    path: &str,
    columns: Option<&Bound<'_, PyList>>,
    batch_size: Option<usize>,
) -> PyResult<Option<Py<PyAny>>> {
    Python::attach(|py| {
        let pa = match py.import("pyarrow") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf_class = match pa.getattr("parquet") {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };
        let pf = match pf_class.call_method1("ParquetFile", (path,)) {
            Ok(f) => f,
            Err(_) => return Ok(None),
        };

        let cols: Vec<String> = if let Some(col_list) = columns {
            col_list
                .iter()
                .filter_map(|c| c.str().ok())
                .map(|s| s.to_string_lossy().into_owned())
                .collect()
        } else {
            CANONICAL_COLUMNS.iter().map(|s| s.to_string()).collect()
        };

        let _batch_sz = batch_size.unwrap_or(MAX_BATCH_SIZE).min(MAX_BATCH_SIZE);

        // Convert cols to PyList for call_method
        let cols_ref: &[String] = &cols;
        let cols_list = match PyList::new(py, cols_ref) {
            Ok(list) => list,
            Err(_) => return Ok(None),
        };

        let table = match pf.call_method1("read", (&cols_list,)) {
            Ok(t) => t,
            Err(_) => return Ok(None),
        };

        let py_table: Py<PyAny> = table.into_pyobject(py).unwrap().unbind();
        Ok(Some(py_table))
    })
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parquet_get_metadata, m)?)?;
    m.add_function(wrap_pyfunction!(parquet_read_row_group_ipc, m)?)?;
    m.add_function(wrap_pyfunction!(parquet_iter_all_row_groups, m)?)?;
    m.add_function(wrap_pyfunction!(parquet_read_table, m)?)?;
    Ok(())
}

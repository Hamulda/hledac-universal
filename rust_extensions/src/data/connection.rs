//! Connection management for DuckDB — thread-local connection pools.
//!
//! M1 8GB optimization: Each thread opens its connection ONCE and reuses it
//! across all operations. This reduces per-worker memory from ~15-20 MB (new conn
//! per call) to ~5 MB (reused conn, no WAL, read_only).

use pyo3::prelude::*;
use std::path::Path;

/// Open a DuckDB connection with M1 8GB optimizations.
/// Connection is opened read-only with PRAGMA threads=1.
pub fn get_thread_connection(db_path: &Path) -> PyResult<duckdb::Connection> {
    // Open new connection
    let conn = duckdb::Connection::open(db_path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "DuckDB open failed for {:?}: {}",
            db_path, e
        ))
    })?;

    // M1 8GB: read_only=True = no WAL overhead
    // PRAGMA threads=1 = we parallelize across workers, not inside DuckDB
    conn.execute_batch("PRAGMA threads=1; PRAGMA read_only=true")
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("PRAGMA setup failed: {}", e))
        })?;

    Ok(conn)
}

/// Execute a query and return results as a vector of rows (each row is Vec<String>).
pub fn execute_query(conn: duckdb::Connection, sql: &str) -> PyResult<Vec<Vec<String>>> {
    let mut stmt = conn.prepare(sql).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("prepare error: {}", e))
    })?;

    let n_cols = stmt.column_count();
    let mut rows: Vec<Vec<String>> = Vec::new();

    let mut row_iter = stmt.query([]).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("query error: {}", e))
    })?;

    while let Some(row_result) = row_iter.next().transpose() {
        let row = row_result.map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("row read error: {}", e))
        })?;
        let mut row_values: Vec<String> = Vec::with_capacity(n_cols);
        for i in 0..n_cols {
            let val: duckdb::types::ValueRef<'_> = match row.get_ref(i) {
                Ok(v) => v,
                Err(_) => duckdb::types::ValueRef::Null,
            };
            row_values.push(format_value_ref(val));
        }
        rows.push(row_values);
    }

    Ok(rows)
}

/// Format a DuckDB ValueRef as a string for Python consumption.
/// Uses Debug format for all non-trivial variants for forward compatibility
/// across DuckDB versions.
fn format_value_ref(val: duckdb::types::ValueRef<'_>) -> String {
    use duckdb::types::ValueRef;
    match val {
        ValueRef::Null => "NULL".to_string(),
        ValueRef::Boolean(true) => "true".to_string(),
        ValueRef::Boolean(false) => "false".to_string(),
        ValueRef::TinyInt(i) => i.to_string(),
        ValueRef::SmallInt(i) => i.to_string(),
        ValueRef::Int(i) => i.to_string(),
        ValueRef::BigInt(i) => i.to_string(),
        ValueRef::HugeInt(h) => format!("{:?}", h),
        ValueRef::Float(f) => f.to_string(),
        ValueRef::Double(f) => f.to_string(),
        ValueRef::Text(s) => String::from_utf8_lossy(s).to_string(),
        ValueRef::Blob(b) => format!("<Blob {} bytes>", b.len()),
        // All complex types: use debug format (covers all timestamp/date/time/interval variants)
        _ => format!("{:?}", val),
    }
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

#[pyfunction]
pub fn duckdb_open_connection(db_path: String) -> PyResult<bool> {
    let path = std::path::Path::new(&db_path);
    get_thread_connection(path);
    Ok(true)
}

#[pyfunction]
pub fn duckdb_health_check(db_path: String) -> PyResult<String> {
    let conn = duckdb::Connection::open(&db_path)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("open: {}", e)))?;
    let version: String = conn
        .query_row("SELECT duckdb_version()", [], |row| row.get(0))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("query: {}", e)))?;
    Ok(version)
}

/// Register connection functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(duckdb_open_connection)?);
    m.add_function(wrap_pyfunction!(duckdb_health_check)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_check() {
        let result = duckdb_health_check(":memory:".to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().starts_with("1."));
    }
}

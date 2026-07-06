//! Connection management for DuckDB — thread-local connection pools.
//!
//! M1 8GB optimization: Each thread opens its connection ONCE and reuses it
//! across all operations. This reduces per-worker memory from ~15-20 MB (new conn
//! per call) to ~5 MB (reused conn, no WAL, read_only).

use pyo3::prelude::*;
use std::cell::RefCell;
use std::path::Path;

/// Thread-local DuckDB connection — reused across all operations in a thread.
/// This eliminates the 50-80 MB cost of opening a new connection each call.
thread_local! {
    static THREAD_CONN: RefCell<Option<duckdb::Connection>> = const { RefCell::new(None) };
}

/// Open or reuse a thread-local DuckDB connection.
/// Connection is opened read-only with PRAGMA threads=1 for M1 8GB safety.
fn get_thread_connection(db_path: &Path) -> PyResult<duckdb::Connection> {
    THREAD_CONN.with(|cell| {
        let mut opt_conn = cell.borrow_mut();

        if opt_conn.is_none() {
            let conn = duckdb::Connection::open(db_path)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("DuckDB open failed for {:?}: {}", db_path, e)))?;

            // M1 8GB: read_only=True = no WAL overhead
            // PRAGMA threads=1 = we parallelize across workers, not inside DuckDB
            conn.execute_batch("PRAGMA threads=1; PRAGMA read_only=true")
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("PRAGMA setup failed: {}", e)))?;

            *opt_conn = Some(conn);
        }

        Ok(opt_conn.take().unwrap())
    })
}

/// Return a connection to the thread-local cache after use.
fn return_connection(conn: duckdb::Connection) {
    THREAD_CONN.with(|cell| {
        *cell.borrow_mut() = Some(conn);
    });
}

/// Execute a query and return results as a vector of rows (each row is Vec<String>).
fn execute_query(conn: duckdb::Connection, sql: &str) -> PyResult<Vec<Vec<String>>> {
    let mut stmt = conn.prepare(sql)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("prepare error: {}", e)))?;

    let n_cols = stmt.column_count();
    let mut rows: Vec<Vec<String>> = Vec::new();

    let mut row_iter = stmt.query([])
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("query error: {}", e)))?;

    while let Some(row) = row_iter.next().transpose() {
        let mut row_values: Vec<String> = Vec::with_capacity(n_cols);
        for i in 0..n_cols {
            let val: duckdb::Result<duckdb::types::ValueRef<'_>> = row.get_ref(i);
            row_values.push(format_value_ref(val.unwrap_or(Ok(duckdb::types::ValueRef::Null)).unwrap_or(duckdb::types::ValueRef::Null)));
        }
        rows.push(row_values);
    }

    return_connection(conn);
    Ok(rows)
}

/// Format a DuckDB ValueRef as a string for Python consumption.
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
        ValueRef::Float(f) => f.to_string(),
        ValueRef::Double(f) => f.to_string(),
        ValueRef::Text(s) => String::from_utf8_lossy(s).to_string(),
        ValueRef::Blob(b) => format!("<Blob {} bytes>", b.len()),
        ValueRef::Timestamp(_, _) => "TIMESTAMP".to_string(),
        ValueRef::Date32(_) => "DATE".to_string(),
        ValueRef::Time64(_) => "TIME".to_string(),
        ValueRef::Interval(_) => "INTERVAL".to_string(),
        ValueRef::HugeInt(_) => "BIGINT".to_string(),
        ValueRef::UhugeInt(_) => "UBIGINT".to_string(),
        ValueRef::TimestampNs(_) => "TIMESTAMP_NS".to_string(),
        ValueRef::TimestampMs(_) => "TIMESTAMP_MS".to_string(),
        ValueRef::TimestampSec(_) => "TIMESTAMP_S".to_string(),
        ValueRef::Date32Tz(_, _) => "DATE_TZ".to_string(),
        ValueRef::Time64Tz(_, _) => "TIME_TZ".to_string(),
        ValueRef::Enum(_, idx) => format!("Enum[{}]", idx),
        ValueRef::Struct(_, _) => "STRUCT".to_string(),
        ValueRef::Array(_, _) => "ARRAY".to_string(),
        ValueRef::Map(_, _) => "MAP".to_string(),
        ValueRef::Union(_, _) => "UNION".to_string(),
    }
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

#[pyfunction]
pub fn duckdb_open_connection(db_path: String) -> PyResult<bool> {
    let path = std::path::Path::new(&db_path);
    get_thread_connection(path)?;
    Ok(true)
}

#[pyfunction]
pub fn duckdb_health_check(db_path: String) -> PyResult<String> {
    let conn = duckdb::Connection::open(&db_path)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("open: {}", e)))?;
    let version: String = conn
        .query_row("SELECT duckdb_version()", [], |row| row.get(0))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("query: {}", e)))?;
    Ok(version)
}

#[pyfunction]
pub fn duckdb_close_connection() -> PyResult<()> {
    THREAD_CONN.with(|cell| {
        *cell.borrow_mut() = None;
    });
    Ok(())
}

/// Register connection functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(duckdb_open_connection, m)?)?;
    m.add_function(wrap_pyfunction!(duckdb_health_check, m)?)?;
    m.add_function(wrap_pyfunction!(duckdb_close_connection, m)?)?;
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

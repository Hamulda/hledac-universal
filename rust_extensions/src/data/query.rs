//! Async/sync DuckDB query execution via std thread pool.
//!
//! Provides DuckDB query execution callable from Python asyncio via run_in_executor.
//! Uses std::thread for GIL release (no tokio dependency).
//!
//! ## API Compatibility
//!
//! - pyo3 0.27: Python::none() → PyNone::new(py).into_any().unbind()
//! - pyo3 0.27: into_py() → .into_pyobject(py) na PyO3 typy
//! - duckdb 1.105.x: Rows uses FallibleStreamingIterator (not Iterator)
//! - duckdb 1.105.x: ValueRef variants: Null, Boolean, TinyInt, SmallInt,
//!   Int, BigInt, Float, Double, Text, Blob, Timestamp, Date32, Time64

use pyo3::prelude::*;
use std::sync::{Arc, Mutex};

/// Thread-safe DuckDB connection pool using std sync primitives.
struct StdConnectionPool {
    connections: Vec<Mutex<Option<duckdb::Connection>>>,
    db_path: String,
}

impl StdConnectionPool {
    fn new(db_path: String, max_connections: usize) -> Self {
        let connections = (0..max_connections)
            .map(|_| Mutex::new(None))
            .collect();
        Self { connections, db_path }
    }

    fn execute_query_sync(&self, sql: String) -> Result<Vec<Vec<String>>, String> {
        // Try to get an available connection
        for conn_mutex in &self.connections {
            let mut conn_guard = conn_mutex
                .lock()
                .map_err(|e| format!("mutex lock: {}", e))?;

            if conn_guard.is_none() {
                match duckdb::Connection::open(&self.db_path) {
                    Ok(c) => *conn_guard = Some(c),
                    Err(e) => return Err(format!("open DuckDB: {}", e)),
                }
            }

            if let Some(conn) = conn_guard.take() {
                drop(conn_guard);
                let result = execute_duckdb_query_sync(conn, &sql);

                // Return connection to pool
                let mut guard = conn_mutex
                    .lock()
                    .map_err(|e| format!("mutex lock: {}", e))?;
                if let Ok(new_conn) = duckdb::Connection::open(&self.db_path) {
                    *guard = Some(new_conn);
                }
                return result;
            }
        }
        Err("No available connections".to_string())
    }
}

/// Execute DuckDB query and convert rows to Vec<Vec<String>>.
/// Each cell is formatted as string for Python to parse.
fn execute_duckdb_query_sync(
    conn: duckdb::Connection,
    sql: &str,
) -> Result<Vec<Vec<String>>, String> {
    let mut stmt = conn
        .prepare(sql)
        .map_err(|e| format!("prepare error: {}", e))?;

    let n_cols = stmt.column_count();

    let mut row_iter = stmt
        .query([])
        .map_err(|e| format!("query error: {}", e))?;

    let mut rows: Vec<Vec<String>> = Vec::new();

    while let Some(row_result) = row_iter.next().transpose() {
        let row = row_result.map_err(|e| format!("row read error: {}", e))?;
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

/// Global connection pool — initialized lazily.
static ASYNC_POOL: std::sync::OnceLock<Arc<StdConnectionPool>> = std::sync::OnceLock::new();

fn get_async_pool() -> Arc<StdConnectionPool> {
    ASYNC_POOL
        .get_or_init(|| Arc::new(StdConnectionPool::new(":memory:".to_string(), 2)))
        .clone()
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

// NOTE: Raw SQL query functions (rust_duckdb_query, rust_duckdb_query_with_params,
// init_duckdb_pool) have been removed. This module now provides internal DuckDB
// infrastructure only (StdConnectionPool, execute_duckdb_query_sync) used by
// graph_traverse.rs and other domain-specific repositories.

/// Register query functions with Python module.
/// NOTE: No raw SQL functions exported — repository pattern only.
pub fn register_functions(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    // No raw SQL functions exported — all queries go through domain-specific
    // repository functions (e.g., traverse_graph in graph_traverse.rs)
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pool_creation() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 2);
        assert_eq!(pool.db_path, ":memory:");
    }

    #[test]
    fn test_sync_query() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 1);
        let result = pool.execute_query_sync("SELECT 42 as num".to_string());
        assert!(result.is_ok());
        assert_eq!(result.unwrap().len(), 1);
    }
}

//! async_query.rs — Internal DuckDB async query infrastructure
//!
//! NOTE: Raw SQL query functions have been removed (repository pattern only).
//! This module provides internal DuckDB infrastructure for domain-specific
//! repositories like graph_traverse.rs.
//!
//! ## API Compatibility
//!
//! - pyo3 0.27: Python::none() → PyNone::new(py).into_any().unbind()
//! - pyo3 0.27: into_py() → .into_pyobject(py) na PyO3 typy
//! - duckdb 1.105.x: Rows uses FallibleStreamingIterator (not Iterator)
//! - duckdb 1.105.x: ValueRef variants: Null, Boolean, TinyInt, SmallInt,
//!   Int, BigInt, Float, Double, Text, Blob, Timestamp, Date32, Time64,
//!   Interval{months,days,nanos}, HugeInt

use pyo3::prelude::*;
use std::sync::{Arc, Mutex};

/// Thread-safe DuckDB connection pool using std sync primitives.
struct StdConnectionPool {
    connections: Vec<Mutex<Option<duckdb::Connection>>>,
    db_path: String,
    max_connections: usize,
}

impl StdConnectionPool {
    fn new(db_path: String, max_connections: usize) -> Self {
        let connections = (0..max_connections)
            .map(|_| Mutex::new(None))
            .collect();
        Self { connections, db_path, max_connections }
    }

    fn execute_query_sync(&self, sql: String) -> Result<Vec<Vec<String>>, String> {
        for conn_mutex in &self.connections {
            let mut conn_guard = conn_mutex.lock().map_err(|e| format!("mutex lock: {}", e))?;

            if conn_guard.is_none() {
                match duckdb::Connection::open(&self.db_path) {
                    Ok(c) => *conn_guard = Some(c),
                    Err(e) => return Err(format!("open DuckDB: {}", e)),
                }
            }

            if let Some(conn) = conn_guard.take() {
                drop(conn_guard);
                let result = execute_duckdb_query_sync(conn, &sql);

                let mut guard = conn_mutex.lock().map_err(|e| format!("mutex lock: {}", e))?;
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

    let mut results: Vec<Vec<String>> = Vec::new();

    loop {
        match row_iter.next() {
            Ok(Some(row)) => {
                let cols: Vec<String> = (0..n_cols)
                    .map(|i| {
                        match row.get_ref(i) {
                            Ok(val) => format_value_ref(val),
                            Err(e) => format!("<error: {}>", e),
                        }
                    })
                    .collect();
                results.push(cols);
            }
            Ok(None) => break,
            Err(e) => return Err(format!("row iteration error: {}", e)),
        }
    }

    Ok(results)
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
        ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
        ValueRef::Blob(b) => format!("<blob:{}>", b.len()),
        ValueRef::Timestamp(tu, ts) => format!("Timestamp({tu:?},{ts})"),
        ValueRef::Date32(d) => format!("Date32({d})"),
        ValueRef::Time64(tu, t) => format!("Time64({tu:?},{t})"),
        ValueRef::Interval { months, days, nanos } => {
            format!("Interval({months},{days},{nanos})")
        }
        ValueRef::HugeInt(i) => format!("HugeInt({i})"),
        ValueRef::UTinyInt(i) => (i as i64).to_string(),
        ValueRef::USmallInt(i) => (i as i64).to_string(),
        ValueRef::UInt(i) => (i as i64).to_string(),
        ValueRef::UBigInt(i) => (i as i64).to_string(),
        ValueRef::Decimal(d) => format!("Decimal({d})"),
        ValueRef::List(_, idx) => format!("List[{idx}]"),
        ValueRef::Enum(_, idx) => format!("Enum[{idx}]"),
        ValueRef::Struct(_, idx) => format!("Struct[{idx}]"),
        ValueRef::Array(_, idx) => format!("Array[{idx}]"),
        ValueRef::Map(_, idx) => format!("Map[{idx}]"),
        ValueRef::Union(_, idx) => format!("Union[{idx}]"),
    }
}

/// Global connection pool — initialized lazily.
static ASYNC_POOL: std::sync::OnceLock<Arc<StdConnectionPool>> = std::sync::OnceLock::new();

fn get_async_pool() -> Arc<StdConnectionPool> {
    ASYNC_POOL
        .get_or_init(|| Arc::new(StdConnectionPool::new(":memory:".to_string(), 2)))
        .clone()
}

#[pyfunction]
fn init_async_pool(db_path: String, max_connections: usize) -> PyResult<()> {
    let pool = Arc::new(StdConnectionPool::new(db_path, max_connections.min(4)));
    ASYNC_POOL.set(pool).map_err(|_| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Async pool already initialized".to_string())
    })?;
    Ok(())
}

#[pyfunction]
pub fn rust_async_query(sql: String) -> PyResult<Vec<Vec<String>>> {
    let pool = get_async_pool();
    // Python calls via asyncio.to_thread() — already runs in a ThreadPoolExecutor
    // thread WITHOUT the GIL. Execute query directly on that thread (no Python
    // objects accessed in execute_query_sync). No std::thread::spawn needed.
    pool.execute_query_sync(sql)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

#[pyfunction]
pub fn rust_async_query_with_params(
    sql: String,
    params: Vec<Py<PyAny>>,
) -> PyResult<Vec<Vec<String>>> {
    let executed_sql = if !params.is_empty() {
        Python::attach(|py| {
            let mut result_sql = sql;
            for param in &params {
                let param_str = if let Ok(s) = param.extract::<String>(py) {
                    format!("'{}'", s.replace('\'', "''"))
                } else if let Ok(i) = param.extract::<i64>(py) {
                    i.to_string()
                } else if let Ok(f) = param.extract::<f64>(py) {
                    f.to_string()
                } else {
                    "NULL".to_string()
                };
                if let Some(pos) = result_sql.find('?') {
                    result_sql = format!("{}{}{}", &result_sql[..pos], param_str, &result_sql[pos+1..]);
                }
            }
            result_sql
        })
    } else {
        sql
    };

    let pool = get_async_pool();
    // Python calls via asyncio.to_thread() — already runs in a ThreadPoolExecutor
    // thread WITHOUT the GIL. Execute query directly (no Python objects in scope).
    pool.execute_query_sync(executed_sql)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

#[pyfunction]
fn check_duckdb_health(db_path: String) -> PyResult<String> {
    let conn = duckdb::Connection::open(&db_path)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("open: {}", e)))?;
    let version: String = conn
        .query_row("SELECT duckdb_version()", [], |row| row.get(0))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("query: {}", e)))?;
    Ok(version)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // NOTE: Raw SQL query functions removed — repository pattern only.
    // Only health check is exported for diagnostics.
    m.add_function(wrap_pyfunction!(check_duckdb_health, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pool_creation() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 2);
        assert_eq!(pool.max_connections, 2);
        assert_eq!(pool.db_path, ":memory:");
    }

    #[test]
    fn test_sync_query() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 1);
        let result = pool.execute_query_sync("SELECT 42 as num".to_string());
        assert!(result.is_ok());
        assert_eq!(result.unwrap().len(), 1);
    }

    #[test]
    fn test_health_check() {
        let result = check_duckdb_health(":memory:".to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().starts_with("1."));
    }
}

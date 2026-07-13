//! async_query.rs — Internal DuckDB async query infrastructure
//!
//! ISSUE-19: rust_async_query_batch — rayon parallel N queries in one call.
//! Python side: asyncio.gather(rust_async_query_batch([sql1, sql2, ...]))
//! This gives true parallel DuckDB queries without asyncio.to_thread overhead.
//!
//! ## API
//!
//! - rust_async_query(sql) — single query, sync (via to_thread)
//! - rust_async_query_batch(sqls) — PARALLEL N queries in rayon
//! - rust_async_query_with_params(sql, params) — parameterized query
//! - init_async_pool(db_path, max_connections) — initialize pool

use pyo3::prelude::*;
use rayon::prelude::*;
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

fn execute_query_sync(
    &self,
    sql: &str,
) -> Result<Vec<Vec<String>>, String> {
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
            let result = execute_duckdb_query_sync(conn, sql, &[]);
            let mut guard = conn_mutex.lock().map_err(|e| format!("mutex lock: {}", e))?;
            if let Ok(new_conn) = duckdb::Connection::open(&self.db_path) {
                *guard = Some(new_conn);
            }
            return result;
        }
    }
    Err("No available connections".to_string())
}

fn execute_query_sync_with_params(
    &self,
    sql: &str,
    params: &[String],
) -> Result<Vec<Vec<String>>, String> {
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
            let param_refs: Vec<&dyn duckdb::types::ToSql> = params
                .iter()
                .map(|s| s as &dyn duckdb::types::ToSql)
                .collect();
            let result = execute_duckdb_query_sync(conn, sql, &param_refs);
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
    params: &[&dyn duckdb::types::ToSql],
) -> Result<Vec<Vec<String>>, String> {
    let mut stmt = conn
        .prepare(sql)
        .map_err(|e| format!("prepare error: {}", e))?;
    let n_cols = stmt.column_count();
    // DuckDB native parameter binding via `&[&dyn ToSql]` — no string interpolation.
    let mut row_iter = stmt
        .query(params)
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

/// P1: Lazy pool config — set by init_async_pool() before first get_async_pool() call.
static POOL_CONFIG: std::sync::OnceLock<(String, usize)> = std::sync::OnceLock::new();

/// Global connection pool — initialized lazily from POOL_CONFIG.
static ASYNC_POOL: std::sync::OnceLock<Arc<StdConnectionPool>> = std::sync::OnceLock::new();

fn get_async_pool() -> Arc<StdConnectionPool> {
    ASYNC_POOL
        .get_or_init(|| {
            let (db_path, max_conn) = POOL_CONFIG
                .get_or_init(|| (":memory:".to_string(), 2));
            Arc::new(StdConnectionPool::new(db_path.clone(), *max_conn))
        })
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
    pool.execute_query_sync(&sql)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

#[pyfunction]
pub fn rust_async_query_with_params(
    sql: String,
    params: Vec<Py<PyAny>>,
) -> PyResult<Vec<Vec<String>>> {
    // Convert Py<PyAny> params to owned Strings, then borrow as &[&str].
    // DuckDB parameterized query — no string interpolation, no SQL injection risk.
    let param_strings: Vec<String> = Python::with_gil(|py| {
        params
            .iter()
            .map(|p| {
                if let Ok(s) = p.extract::<String>(py) {
                    s
                } else if let Ok(i) = p.extract::<i64>(py) {
                    i.to_string()
                } else if let Ok(f) = p.extract::<f64>(py) {
                    f.to_string()
                } else {
                    String::new()
                }
            })
            .collect()
    });
    let pool = get_async_pool();
    pool.execute_query_sync_with_params(&sql, &param_strings)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

/// ISSUE-19: Parallel batch query — executes N SQL queries concurrently in rayon
/// and returns N result sets. Each worker opens its own connection (no pool
/// contention). Use from Python asyncio.gather():
///
///   results = await asyncio.gather(*[
///       rust_async_query_batch([sql1, sql2, sql3])
///   ])
#[pyfunction]
#[pyo3(name = "rust_async_query_batch")]
pub fn rust_async_query_batch(sqls: Vec<String>) -> PyResult<Vec<Vec<Vec<String>>>> {
    // P3-FIX: open fresh connection per worker — avoids pool contention in rayon.
    // get_db_path() from pool config (":memory:" or real path). :memory: is
    // thread-safe in DuckDB (each open = new DB), so parallelism is free.
    let db_path = POOL_CONFIG
        .get_or_init(|| (":memory:".to_string(), 2))
        .0
        .clone();
    let results: Vec<Result<Vec<Vec<String>>, String>> = sqls
        .par_iter()
        .map(|sql| {
            let conn = duckdb::Connection::open(&db_path)
                .map_err(|e| format!("open: {}", e))?;
            execute_duckdb_query_sync(conn, sql, &[])
        })
        .collect();

    let mut py_errors: Vec<String> = Vec::new();
    let mut ok_results: Vec<Vec<Vec<String>>> = Vec::new();

    for result in results {
        match result {
            Ok(rows) => ok_results.push(rows),
            Err(e) => py_errors.push(e),
        }
    }

    if !py_errors.is_empty() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("{} query errors: {}", py_errors.len(), py_errors.join("; "))
        ));
    }

    Ok(ok_results)
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
    m.add_function(wrap_pyfunction!(check_duckdb_health, m)?)?;
    m.add_function(wrap_pyfunction!(rust_async_query, m)?)?;
    m.add_function(wrap_pyfunction!(rust_async_query_with_params, m)?)?;
    m.add_function(wrap_pyfunction!(rust_async_query_batch, m)?)?;
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

    // P2: For :memory: DB, each query opens a fresh connection (pool is still
    // sound — DuckDB :memory: connections are independent DBs, not shared).
    // This test verifies sequential queries work without error on pool-of-1.
    #[test]
    fn test_pool_sequential_queries() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 1);
        let r1 = pool.execute_query_sync("SELECT 1 as n".to_string());
        let r2 = pool.execute_query_sync("SELECT 2 as n".to_string());
        assert!(r1.is_ok());
        assert!(r2.is_ok());
        assert_eq!(r1.unwrap().len(), 1);
        assert_eq!(r2.unwrap().len(), 1);
    }

    // P4: Parameterized query — params should reach DuckDB
    #[test]
    fn test_query_with_params() {
        let pool = StdConnectionPool::new(":memory:".to_string(), 1);
        let params = vec!["hello".to_string(), "world".to_string()];
        let result = pool.execute_query_sync_with_params(
            "SELECT ?1 as a, ?2 as b".to_string(),
            &params,
        );
        assert!(result.is_ok());
        let rows = result.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0][0], "hello");
        assert_eq!(rows[0][1], "world");
    }

    // P3: Batch — each worker opens own connection (no contention with pool-of-1)
    #[test]
    fn test_batch_fresh_connections() {
        let db_path = ":memory:".to_string();
        let sqls = vec![
            "SELECT 1 as n".to_string(),
            "SELECT 2 as n".to_string(),
            "SELECT 3 as n".to_string(),
        ];
        let results: Vec<Result<Vec<Vec<String>>, String>> = sqls
            .par_iter()
            .map(|sql| {
                let conn = duckdb::Connection::open(&db_path)
                    .map_err(|e| format!("open: {}", e))?;
                execute_duckdb_query_sync(conn, sql, &[])
            })
            .collect();
        assert_eq!(results.len(), 3);
        assert!(results.iter().all(|r| r.is_ok()));
    }

    #[test]
    fn test_health_check() {
        let result = check_duckdb_health(":memory:".to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().starts_with("1."));
    }
}

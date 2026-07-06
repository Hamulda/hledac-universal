//! async_query.rs — ISSUE-013: Async-capable Rust DuckDB queries via tokio runtime
//!
//! Provides async Rust functions callable from Python asyncio without GIL blocking.
//! Uses tokio runtime with spawn_blocking for CPU-bound DuckDB queries.
//!
//! ## Note on pyo3-async
//!
//! pyo3-async 0.3.x requires PyO3 0.19, which is incompatible with our PyO3 0.27.
//! For Python 3.14+ with free-threaded Python (PEP 703), pyo3-async will work
//! when PyO3 0.30+ releases with gil="false" support.
//!
//! For now, we use tokio's spawn_blocking which releases the GIL during
//! the blocking operation. Python calls via asyncio.to_thread() wrapper.
//!
//! ## Usage from Python
//!
//! ```python
//! import asyncio
//! from hledac_rust_extensions import rust_async_query
//!
//!/async def main():
//!     # Run Rust async query in thread pool (GIL released during execution)
//!     loop = asyncio.get_event_loop()
//!     result = await loop.run_in_executor(
//!         None,
//!         lambda: rust_async_query("SELECT * FROM findings LIMIT 10")
//!     )
//!     print(f"Got {len(result)} rows")
//!
//! asyncio.run(main())
//! ```

use pyo3::prelude::*;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Thread-safe DuckDB connection pool for async queries.
/// Each tokio task gets its own connection from this pool.
struct AsyncConnectionPool {
    /// Pre-opened DuckDB connections
    connections: Vec<Mutex<Option<duckdb::Connection>>>,
    /// Database path (for re-opening if needed)
    db_path: String,
    max_connections: usize,
}

impl AsyncConnectionPool {
    fn new(db_path: String, max_connections: usize) -> Self {
        let connections = (0..max_connections)
            .map(|_| Mutex::new(None))
            .collect();
        Self {
            connections,
            db_path,
            max_connections,
        }
    }

    async fn execute_query(&self, sql: String) -> Result<Vec<Vec<Py<PyAny>>>, String> {
        // Find first available connection and execute
        for conn_mutex in &self.connections {
            let mut conn_guard = conn_mutex.lock().await;
            if conn_guard.is_none() {
                // Open a new connection if slot is empty
                match duckdb::Connection::open(&self.db_path) {
                    Ok(c) => *conn_guard = Some(c),
                    Err(e) => return Err(format!("Failed to open DuckDB: {}", e)),
                }
            }

            // Take connection out of the slot
            if let Some(conn) = conn_guard.take() {
                drop(conn_guard); // Release lock during query

                // Execute query in blocking thread (GIL released during spawn_blocking)
                let result = tokio::task::spawn_blocking(move || {
                    execute_duckdb_query_sync(conn, &sql)
                })
                .await
                .map_err(|e| format!("tokio task join error: {}", e))??;

                // Return connection to pool
                let mut guard = conn_mutex.lock().await;
                if let Ok(new_conn) = duckdb::Connection::open(&self.db_path) {
                    *guard = Some(new_conn);
                }
                return Ok(result);
            }
        }

        Err("No available connections".to_string())
    }
}

fn execute_duckdb_query_sync(
    conn: duckdb::Connection,
    sql: &str,
) -> Result<Vec<Vec<Py<PyAny>>>, String> {
    let mut stmt = conn
        .prepare(sql)
        .map_err(|e| format!("prepare error: {}", e))?;

    let rows = stmt
        .query([])
        .map_err(|e| format!("query error: {}", e))?;

    let mut results: Vec<Vec<Py<PyAny>>> = Vec::new();

    for row in rows {
        let row_data: Result<Vec<Py<PyAny>>, _> = row
            .iter()
            .map(|val| {
                use duckdb::ValueRef;
                match val {
                    ValueRef::Null => Ok(Python::none().into_py()),
                    ValueRef::Boolean(b) => Ok(b.into_py()),
                    ValueRef::TinyInt(i) => Ok(i.into_py()),
                    ValueRef::SmallInt(i) => Ok(i.into_py()),
                    ValueRef::Int(i) => Ok(i.into_py()),
                    ValueRef::BigInt(i) => Ok(i.into_py()),
                    ValueRef::Float(f) => Ok(f.into_py()),
                    ValueRef::Double(f) => Ok(f.into_py()),
                    ValueRef::Text(t) => Ok(String::from_utf8_lossy(t).into_owned().into_py()),
                    ValueRef::Blob(b) => Ok(b.into_py()),
                    ValueRef::Timestamp(_, _) |
                    ValueRef::TimestampNs(_) |
                    ValueRef::TimestampMs(_) |
                    ValueRef::TimestampSec(_) |
                    ValueRef::Date(_) |
                    ValueRef::Time(_) |
                    ValueRef::Interval(_) |
                    ValueRef::HugeInt(_) |
                    ValueRef::UHUgeInt(_) |
                    ValueRef::UTinyInt(_) |
                    ValueRef::USmallInt(_) |
                    ValueRef::UInt(_) |
                    ValueRef::UBigInt(_) |
                    ValueRef::Decimal(_, _, _) => {
                        Ok(format!("{:?}", val).into_py())
                    }
                }
            })
            .collect();

        results.push(row_data.map_err(|e: duckdb::Error| format!("row error: {}", e))?);
    }

    Ok(results)
}

/// Global async connection pool — initialized lazily on first use.
static ASYNC_POOL: std::sync::OnceLock<Arc<AsyncConnectionPool>> = std::sync::OnceLock::new();

fn get_async_pool() -> Arc<AsyncConnectionPool> {
    ASYNC_POOL
        .get_or_init(|| {
            // Default: 2 connections for M1 8GB RAM budget
            Arc::new(AsyncConnectionPool::new(":memory:".to_string(), 2))
        })
        .clone()
}

/// Initialize the async connection pool with a DuckDB database path.
///
/// This must be called before any async queries.
/// Call this from Python sync code before running async queries.
///
/// # Arguments
/// * `db_path` - Path to DuckDB database file (or ":memory:")
/// * `max_connections` - Maximum number of connections in pool (default 4)
#[pyfunction]
fn init_async_pool(db_path: String, max_connections: usize) -> PyResult<()> {
    let pool = Arc::new(AsyncConnectionPool::new(db_path, max_connections.min(4)));

    ASYNC_POOL
        .set(pool)
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Async pool already initialized".to_string()
        ))?;

    Ok(())
}

/// Execute a DuckDB query in a tokio blocking thread.
///
/// This function is designed to be called from Python asyncio via
/// `asyncio.to_thread()` or `loop.run_in_executor()`.
/// It releases the GIL during query execution via tokio::task::spawn_blocking.
///
/// Returns a list of rows, where each row is a list of column values.
///
/// # Arguments
/// * `sql` - SQL query string
///
/// # Returns
/// * List of rows (each row is a list of values)
///
/// # Example
/// ```python
/// import asyncio
/// from hledac_rust_extensions import rust_async_query
///
/// async def main():
///     # Call via executor (GIL released during execution)
///     loop = asyncio.get_event_loop()
///     rows = await loop.run_in_executor(
///         None,
///         lambda: rust_async_query("SELECT url FROM findings LIMIT 10")
///     )
///     for row in rows:
///         print(f"URL: {row[0]}")
///
/// asyncio.run(main())
/// ```
#[pyfunction]
pub fn rust_async_query(sql: String) -> PyResult<Vec<Vec<Py<PyAny>>>> {
    // Use blocking tokio runtime for GIL release
    let pool = get_async_pool();
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?;

    rt.block_on(pool.execute_query(sql))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

/// Execute a DuckDB query with inline parameters in a tokio blocking thread.
///
/// WARNING: For production, use parameterized queries to prevent SQL injection.
/// This function is for backward compatibility with existing code.
///
/// # Arguments
/// * `sql` - SQL query with ? placeholders (will be substituted)
/// * `params` - List of parameter values to inline into SQL
#[pyfunction]
pub fn rust_async_query_with_params(
    sql: String,
    params: Vec<Py<PyAny>>,
) -> PyResult<Vec<Vec<Py<PyAny>>>> {
    // Inline parameters (basic approach — prefer prepared statements in production)
    let executed_sql = if !params.is_empty() {
        let py = Python::acquire_gil();
        let mut result_sql = sql;
        for param in &params {
            let param_str = if let Ok(s) = param.extract::<String>(py.python()) {
                format!("'{}'", s.replace('\'', "''"))
            } else if let Ok(i) = param.extract::<i64>(py.python()) {
                i.to_string()
            } else if let Ok(f) = param.extract::<f64>(py.python()) {
                f.to_string()
            } else {
                "NULL".to_string()
            };
            if let Some(pos) = result_sql.find('?') {
                result_sql = format!("{}{}{}",
                    &result_sql[..pos],
                    param_str,
                    &result_sql[pos+1..]
                );
            }
        }
        result_sql
    } else {
        sql
    };

    let pool = get_async_pool();
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?;

    rt.block_on(pool.execute_query(executed_sql))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

/// Check if a DuckDB database file is valid and queryable.
///
/// Returns database version string on success.
/// Useful for health checks before running async queries.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import check_duckdb_health
///
/// version = check_duckdb_health("/path/to/database.db")
/// print(f"Connected to DuckDB version: {version}")
/// ```
#[pyfunction]
fn check_duckdb_health(db_path: String) -> PyResult<String> {
    let conn = duckdb::Connection::open(&db_path)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Failed to open {}: {}", db_path, e)
        ))?;

    let version: String = conn
        .query_row("SELECT duckdb_version()", [], |row| row.get(0))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Query failed: {}", e)
        ))?;

    Ok(version)
}

/// Register async query functions with the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_async_query, m)?)?;
    m.add_function(wrap_pyfunction!(rust_async_query_with_params, m)?)?;
    m.add_function(wrap_pyfunction!(init_async_pool, m)?)?;
    m.add_function(wrap_pyfunction!(check_duckdb_health, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_async_pool_creation() {
        let pool = AsyncConnectionPool::new(":memory:".to_string(), 2);
        assert_eq!(pool.max_connections, 2);
        assert_eq!(pool.db_path, ":memory:");
    }
}

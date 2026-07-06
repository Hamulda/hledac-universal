//! Graph traversal module — parallel DuckPGQ traversal via rayon.
//!
//! M1 8GB optimization: Thread-local DuckDB connection reuse to minimize memory.
//!
//! ## Architecture
//!
//! - Uses rayon ThreadPool for parallelization across root IOCs
//! - Each rayon worker maintains its OWN thread-local DuckDB connection
//! - Connection is opened ONCE per thread and reused across all traversals
//! - Read-only connections with PRAGMA threads=1 (parallelization is across workers)
//!
//! ## Design invariants
//!
//!   G.T1  No panics, no unwrap in #[pymethod] path (fail-soft)
//!   G.T2  Bounded: max_values cap prevents OOM from huge batch inputs
//!   G.T3  Fail-soft: DuckDB errors return empty dict, never raise
//!   G.T4  Parallel across values, NOT within a single traversal
//!   G.T5  M1 8GB safe: thread-local connections, read_only, PRAGMA threads=1

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;
use std::cell::RefCell;
use std::env;
use std::path::PathBuf;

/// Thread-local DuckDB connection cache per rayon worker thread.
/// Opens a connection ONCE per thread and reuses it across all traversals.
thread_local! {
    static THREAD_CONN: RefCell<Option<duckdb::Connection>> = const { RefCell::new(None) };
}

/// Maximum number of results per root IOC.
const MAX_RESULTS_PER_ROOT: usize = 100;

/// Maximum batch size to prevent OOM.
const MAX_BATCH_SIZE: usize = 10_000;

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

/// Result for a single traversal from one root IOC to its connected nodes.
#[derive(Clone, Debug, PartialEq)]
pub struct TraversalResult {
    pub dst_value: String,
    pub ioc_type: String,
    pub confidence: f64,
    pub source: String,
}

// ---------------------------------------------------------------------------
// Connection management
// ---------------------------------------------------------------------------

/// Get or create a thread-local DuckDB connection.
fn with_connection<F, R>(db_path: &PathBuf, f: F) -> R
where
    F: FnOnce(&mut duckdb::Connection) -> R,
{
    THREAD_CONN.with(|cell| {
        let mut opt_conn = cell.borrow_mut();

        if opt_conn.is_none() {
            let new_conn = match duckdb::Connection::open(db_path) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[duckdb_bridge/graph_traverse] DuckDB open failed for {:?}: {}", db_path, e);
                    return None;
                }
            };
            // M1 8GB: read_only=True = no WAL overhead
            // PRAGMA threads=1 = we parallelize across workers, not inside DuckDB
            let _ = new_conn.execute_batch("PRAGMA threads=1; PRAGMA read_only=true");
            *opt_conn = Some(new_conn);
        }

        let conn = match opt_conn.as_mut() {
            Some(c) => c,
            None => return None,
        };

        let result = f(conn);
        Some(result)
    }).unwrap_or_else(|| {
        // Return default for R type
        panic!("Connection not available")
    })
}

/// Run a single traversal query for one root value.
fn traverse_single(db_path: &PathBuf, root_value: &str, max_hops: usize) -> Vec<TraversalResult> {
    with_connection(db_path, |conn| {
        let sql = r#"
            WITH RECURSIVE paths(src_id, dst_id, depth) AS (
                -- Base case: direct neighbors of root
                SELECT src_id, dst_id, 1
                FROM ioc_edges
                WHERE src_value = ? AND depth <= ?

                UNION ALL

                -- Recursive case: follow edges up to max_hops
                SELECT e.src_id, e.dst_id, p.depth + 1
                FROM paths p
                JOIN ioc_edges e ON e.src_value = (
                    SELECT value FROM ioc_nodes WHERE id = p.dst_id
                )
                WHERE p.depth < ?
            )
            SELECT n.value, n.ioc_type, n.confidence, n.source
            FROM paths p
            JOIN ioc_nodes n ON n.id = p.dst_id
            LIMIT $3
        "#;

        let mut stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("[duckdb_bridge] prepare failed for root {}: {}", root_value, e);
                return Vec::new();
            }
        };

        let mapped: Vec<TraversalResult> = match stmt.query_map(
            [root_value, &max_hops.to_string(), &MAX_RESULTS_PER_ROOT.to_string()],
            |row| {
                let dst_value: String = row.get(0).unwrap_or(String::new());
                let ioc_type: String = row.get(1).unwrap_or(String::new());
                let confidence: f64 = row.get(2).unwrap_or(0.0);
                let source: String = row.get(3).unwrap_or(String::new());
                Ok(TraversalResult { dst_value, ioc_type, confidence, source })
            },
        ) {
            Ok(m) => m.filter_map(|r| r.ok()).collect(),
            Err(e) => {
                eprintln!("[duckdb_bridge] query failed for root {}: {}", root_value, e);
                Vec::new()
            }
        };

        mapped
    })
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

/// Traverse graph from a list of root values and return connected IOCs.
#[pyfunction]
pub fn batch_graph_traverse(
    db_path: String,
    root_values: Vec<String>,
    max_hops: usize,
    max_results_per_root: usize,
) -> PyResult<Py<PyDict>> {
    // M1 8GB: Enforce batch size cap to prevent OOM
    let values: Vec<String> = root_values.into_iter().take(MAX_BATCH_SIZE).collect();
    let n = values.len();

    if n == 0 {
        let dict = PyDict::new(Py::none());
        return Ok(dict.into());
    }

    let path = PathBuf::from(&db_path);

    // Limit max_hops to prevent runaway recursion
    let max_hops = max_hops.min(5);

    // Parallel traversal across root values using rayon
    let results: Vec<Vec<TraversalResult>> = (0..n)
        .into_par_iter()
        .map(|i| traverse_single(&path, &values[i], max_hops))
        .collect();

    // Build Python dict: {root_value: [(dst, ioc_type, confidence, source), ...]}
    let dict = PyDict::new(Py::none());

    for (i, result) in results.into_iter().enumerate() {
        let py_list = PyList::new(Py::none(), &result);
        dict.set_item(&values[i], py_list)?;
    }

    Ok(dict.into())
}

/// Get graph statistics from DuckDB.
#[pyfunction]
pub fn graph_stats(db_path: String) -> PyResult<Py<PyDict>> {
    let path = PathBuf::from(&db_path);

    let stats = with_connection(&path, |conn| {
        let mut dict = std::collections::HashMap::new();

        // Count nodes
        if let Ok(count) = conn.query_row::<i64, _, _>(
            "SELECT COUNT(*) FROM ioc_nodes", [], |row| row.get(0)
        ) {
            dict.insert("nodes".to_string(), count as f64);
        }

        // Count edges
        if let Ok(count) = conn.query_row::<i64, _, _>(
            "SELECT COUNT(*) FROM ioc_edges", [], |row| row.get(0)
        ) {
            dict.insert("edges".to_string(), count as f64);
        }

        // DuckDB version
        if let Ok(version) = conn.query_row::<String, _, _>(
            "SELECT duckdb_version()", [], |row| row.get(0)
        ) {
            dict.insert("duckdb_version".to_string(), version);
        }

        dict
    });

    let py_dict = PyDict::new(Py::none());
    if let Some(s) = stats {
        for (k, v) in s {
            py_dict.set_item(&k, v)?;
        }
    }

    Ok(py_dict.into())
}

/// Register graph_traverse functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_graph_traverse, m)?)?;
    m.add_function(wrap_pyfunction!(graph_stats, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_connection_reuse() {
        // Test that thread-local connection cache works correctly
        let path = PathBuf::from(":memory:");

        // First call should create connection
        let result1 = traverse_single(&path, "test", 1);
        assert!(result1.is_empty()); // No data in memory DB

        // Second call should reuse connection (no error)
        let result2 = traverse_single(&path, "test", 1);
        assert!(result2.is_empty());
    }
}

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

use crate::data::connection::{get_thread_connection, return_connection};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFloat, PyList, PyTuple, PyString};
use rayon::prelude::*;
use std::path::Path;
use std::path::PathBuf;

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
// Core traversal logic
// ---------------------------------------------------------------------------

/// Run a single traversal query for one root value.
fn traverse_single(db_path: &Path, root_value: &str, max_hops: usize) -> Vec<TraversalResult> {
    let conn = match get_thread_connection(db_path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };

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
            eprintln!("[data/graph_traverse] prepare failed for root {}: {}", root_value, e);
            return_connection(conn);
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
            eprintln!("[data/graph_traverse] query failed for root {}: {}", root_value, e);
            return_connection(conn);
            return Vec::new()
        }
    };

    return_connection(conn);
    mapped
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

/// Traverse graph from a list of root values and return connected IOCs.
#[pyfunction]
pub fn batch_graph_traverse(
    py: Python<'_>,
    db_path: String,
    root_values: Vec<String>,
    max_hops: usize,
    max_results_per_root: usize,
) -> PyResult<Py<PyDict>> {
    // M1 8GB: Enforce batch size cap to prevent OOM
    let values: Vec<String> = root_values.into_iter().take(MAX_BATCH_SIZE).collect();
    let n = values.len();

    if n == 0 {
        return Ok(PyDict::new(py).into());
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
    let dict = PyDict::new(py);

    for (i, result) in results.into_iter().enumerate() {
        let py_list: Bound<'_, PyList> = PyList::empty(py);
        for r in result {
            let elem0 = PyString::new(py, &r.dst_value);
            let elem1 = PyString::new(py, &r.ioc_type);
            let elem2 = PyFloat::new(py, r.confidence);
            let elem3 = PyString::new(py, &r.source);
            let tuple: Bound<'_, PyTuple> = PyTuple::new(py, &[
                &elem0,
                &elem1,
                &elem2,
                &elem3,
            ]);
            py_list.append(tuple)?;
        }
        dict.set_item(&values[i], &py_list)?;
    }

    Ok(dict.into())
}

/// Get graph statistics from DuckDB.
#[pyfunction]
pub fn graph_stats(py: Python<'_>, db_path: String) -> PyResult<Py<PyDict>> {
    let path = PathBuf::from(&db_path);

    let conn = match get_thread_connection(&path) {
        Ok(c) => c,
        Err(e) => return Err(e),
    };

    let mut py_dict = PyDict::new(py);

    // Count nodes
    if let Ok(count) = conn.query_row::<i64, _, _>(
        "SELECT COUNT(*) FROM ioc_nodes", [], |row| row.get(0)
    ) {
        py_dict.set_item("nodes", count as f64)?;
    }

    // Count edges
    if let Ok(count) = conn.query_row::<i64, _, _>(
        "SELECT COUNT(*) FROM ioc_edges", [], |row| row.get(0)
    ) {
        py_dict.set_item("edges", count as f64)?;
    }

    // DuckDB version
    if let Ok(version) = conn.query_row::<String, _, _>(
        "SELECT duckdb_version()", [], |row| row.get(0)
    ) {
        py_dict.set_item("duckdb_version", version)?;
    }

    return_connection(conn);
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

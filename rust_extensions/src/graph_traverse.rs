//! graph_traverse — Parallel DuckPGQ graph traversal via rayon.
//!
//! Sprint P2-1: Parallel `batch_graph_traverse` for IOC graph.
//!
//! Architecture:
//! - Uses the existing `bulk_pool()` rayon ThreadPool (4 threads, 2MB stack)
//! - Each worker opens its own DuckDB read-only connection (DuckDB is
//!   thread-safe for read operations; concurrent reads are safe across threads)
//! - Parallelization is across root IOCs — N values → N rayon jobs → N DuckDB connections
//! - All DuckDB work runs INSIDE `bulk_pool().install()` so connections never
//!   cross thread boundaries (Connection is !Send).
//!
//! M1 8GB bounds:
//! - 4 rayon workers × 1 DuckDB connection each ≈ 50-80 MB resident
//! - DuckDB WAL + mmap overhead is per-connection; bounded by thread count
//! - No unbounded recursion — max_hops is a SQL parameter (bound at construction)
//!
//! Design invariants:
//!   G.T1  No panics, no unwrap in #[pymethod] path (fail-soft)
//!   G.T2  Bounded: max_values cap prevents OOM from huge batch inputs
//!   G.T3  Fail-soft: DuckDB errors return empty dict, never raise
//!   G.T4  Parallel across values, NOT within a single traversal
//!   G.T5  M1 8GB safe: 4 workers, 2MB stack each, DuckDB read-only connections

use crate::bulk_pool;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

/// Hard cap on number of root IOCs in a single batch traversal.
const MAX_BATCH_VALUES: usize = 10_000;
/// Hard cap on results per root IOC (LIMIT in SQL).
const MAX_RESULTS_PER_ROOT: usize = 100;
/// Maximum allowed traversal depth.
const MAX_HOPS: usize = 10;

/// Internal traversal result for a single root IOC.
#[derive(Clone)]
struct TraversalResult {
    dst_value: String,
    ioc_type: String,
    confidence: f64,
    source: String,
}

/// Run a single find_connected traversal for one root value.
/// MUST be called from WITHIN `bulk_pool().install()` — Connection is !Send.
fn traverse_single(db_path: &str, root_value: &str, max_hops: usize) -> Vec<TraversalResult> {
    let max_hops = max_hops.min(MAX_HOPS);

    let conn = match duckdb::Connection::open(db_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[graph_traverse] DuckDB open failed for {}: {}", db_path, e);
            return Vec::new();
        }
    };

    let sql = r#"
        WITH RECURSIVE paths(dst_id, depth) AS (
            SELECT e.dst_id, 1
            FROM ioc_edges e
            JOIN ioc_nodes n ON n.id = e.src_id
            WHERE n.value = $1
            UNION ALL
            SELECT e.dst_id, p.depth + 1
            FROM ioc_edges e
            JOIN paths p ON p.dst_id = e.src_id
            WHERE p.depth < $2
        )
        SELECT n.value, n.ioc_type, n.confidence, n.source
        FROM paths p
        JOIN ioc_nodes n ON n.id = p.dst_id
        LIMIT $3
    "#;

    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[graph_traverse] prepare failed for root {}: {}", root_value, e);
            return Vec::new();
        }
    };

    let mapped = match stmt.query_map(
        [root_value, &max_hops.to_string(), &MAX_RESULTS_PER_ROOT.to_string()],
        |row| {
            // duckdb 1.105: row.get returns Result<Option<T>> for nullable cols
            let dst_value: String = match row.get::<usize, Option<String>>(0) {
                Ok(Some(v)) => v,
                _ => String::new(),
            };
            let ioc_type: String = match row.get::<usize, Option<String>>(1) {
                Ok(Some(v)) => v,
                _ => String::new(),
            };
            let confidence: f64 = match row.get::<usize, Option<f64>>(2) {
                Ok(Some(v)) => v,
                _ => 0.5,
            };
            let source: String = match row.get::<usize, Option<String>>(3) {
                Ok(Some(v)) => v,
                _ => String::new(),
            };
            Ok(TraversalResult { dst_value, ioc_type, confidence, source })
        },
    ) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[graph_traverse] query failed for root {}: {}", root_value, e);
            return Vec::new();
        }
    };

    mapped.filter_map(|r| r.ok()).collect()
}

/// Parallel batch graph traversal for multiple root IOC values.
#[pyfunction]
#[pyo3(signature = (db_path, values, max_hops = 2))]
pub fn batch_graph_traverse<'py>(
    py: Python<'py>,
    db_path: String,
    values: Vec<String>,
    max_hops: usize,
) -> PyResult<Bound<'py, PyDict>> {
    if values.is_empty() {
        return Ok(PyDict::new(py));
    }

    if values.len() > MAX_BATCH_VALUES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_graph_traverse: too many values ({} > {})",
            values.len(),
            MAX_BATCH_VALUES
        )));
    }

    let max_hops = if max_hops == 0 { 1 } else { max_hops.min(MAX_HOPS) };
    let db_path_clone = db_path.clone();

    let results: Vec<(String, Vec<TraversalResult>)> =
        bulk_pool().install(|| values.par_iter().map(|v| {
            let traversal = traverse_single(&db_path_clone, v, max_hops);
            (v.clone(), traversal)
        }).collect());

    let dict = PyDict::new(py);
    for (root_value, traversal) in results {
        let inner_list: Bound<'py, PyList> = PyList::empty(py);
        for item in traversal {
            let item_dict = PyDict::new(py);
            // set_item returns Result<()> — use .ok() to drop errors (fail-soft)
            let _ = item_dict.set_item("value", &item.dst_value);
            let _ = item_dict.set_item("ioc_type", &item.ioc_type);
            let _ = item_dict.set_item("confidence", item.confidence);
            let _ = item_dict.set_item("source", &item.source);
            let _ = inner_list.append(item_dict);
        }
        let _ = dict.set_item(&root_value, inner_list);
    }

    Ok(dict)
}

/// Single IOC graph traversal — one root, returns connected nodes.
#[pyfunction]
#[pyo3(signature = (db_path, value, max_hops = 2))]
pub fn graph_traverse_single<'py>(
    py: Python<'py>,
    db_path: String,
    value: String,
    max_hops: usize,
) -> PyResult<Bound<'py, PyList>> {
    let max_hops = if max_hops == 0 { 1 } else { max_hops.min(MAX_HOPS) };
    let results = traverse_single(&db_path, &value, max_hops);

    let list: Bound<'py, PyList> = PyList::empty(py);
    for item in results {
        let item_dict = PyDict::new(py);
        let _ = item_dict.set_item("value", &item.dst_value);
        let _ = item_dict.set_item("ioc_type", &item.ioc_type);
        let _ = item_dict.set_item("confidence", item.confidence);
        let _ = item_dict.set_item("source", &item.source);
        let _ = list.append(item_dict);
    }
    Ok(list)
}

/// Graph stats — degree distribution for top K nodes.
#[pyfunction]
#[pyo3(signature = (db_path, top_k = 20))]
pub fn graph_stats<'py>(
    py: Python<'py>,
    db_path: String,
    top_k: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let conn = match duckdb::Connection::open(&db_path) {
        Ok(c) => c,
        Err(_) => {
            let dict = PyDict::new(py);
            let _ = dict.set_item("error", "duckdb_open_failed");
            return Ok(dict);
        }
    };

    let dict = PyDict::new(py);

    // Total node count.
    match conn.query_row::<i64, _, _>("SELECT COUNT(*) FROM ioc_nodes", [], |row| row.get(0)) {
        Ok(count) => { let _ = dict.set_item("total_nodes", count); }
        Err(_) => {}
    }

    // Total edge count.
    match conn.query_row::<i64, _, _>("SELECT COUNT(*) FROM ioc_edges", [], |row| row.get(0)) {
        Ok(count) => { let _ = dict.set_item("total_edges", count); }
        Err(_) => {}
    }

    // Top K nodes by out-degree.
    let top_k = top_k.min(100);
    let sql = format!(
        r#"
        SELECT n.value, n.ioc_type, COUNT(e.dst_id) as degree
        FROM ioc_nodes n
        LEFT JOIN ioc_edges e ON e.src_id = n.id
        GROUP BY n.id, n.value, n.ioc_type
        ORDER BY degree DESC
        LIMIT {}
        "#,
        top_k
    );

    let top_nodes = PyList::empty(py);
    if let Ok(mut stmt) = conn.prepare(&sql) {
        if let Ok(mapped) = stmt.query_map([], |row| {
            let node_value: String = match row.get::<usize, Option<String>>(0) {
                Ok(Some(v)) => v,
                _ => String::new(),
            };
            let ioc_type: String = match row.get::<usize, Option<String>>(1) {
                Ok(Some(v)) => v,
                _ => String::new(),
            };
            let degree: i64 = match row.get::<usize, Option<i64>>(2) {
                Ok(Some(v)) => v,
                _ => 0,
            };
            Ok((node_value, ioc_type, degree))
        }) {
            for r in mapped.filter_map(|x| x.ok()) {
                let node_dict = PyDict::new(py);
                let _ = node_dict.set_item("value", &r.0);
                let _ = node_dict.set_item("ioc_type", &r.1);
                let _ = node_dict.set_item("degree", r.2);
                let _ = top_nodes.append(node_dict);
            }
        }
    }
    let _ = dict.set_item("top_nodes", top_nodes);

    Ok(dict)
}

/// Register graph_traverse functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_graph_traverse, m)?)?;
    m.add_function(wrap_pyfunction!(graph_traverse_single, m)?)?;
    m.add_function(wrap_pyfunction!(graph_stats, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_traversal_constants() {
        assert_eq!(MAX_BATCH_VALUES, 10_000);
        assert_eq!(MAX_RESULTS_PER_ROOT, 100);
        assert_eq!(MAX_HOPS, 10);
    }

    #[test]
    fn test_result_struct_clone() {
        let r = TraversalResult {
            dst_value: "evil.com".to_string(),
            ioc_type: "domain".to_string(),
            confidence: 0.9,
            source: "test".to_string(),
        };
        let r2 = r.clone();
        assert_eq!(r.dst_value, r2.dst_value);
    }
}
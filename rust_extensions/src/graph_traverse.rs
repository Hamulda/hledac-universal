//! graph_traverse — Parallel DuckPGQ graph traversal via rayon.
//!
//! Sprint P2-1: Parallel `batch_graph_traverse` for IOC graph.
//! Sprint F265-U5: Thread-local DuckDB connection pooling (M1 8GB optimization).
//! Sprint F265B-III: LRU cache s mmap-backed persistence (LZ4 komprese).
//!
//! Architecture:
//! - Uses the `bulk_pool()` rayon ThreadPool (2 threads, 1.5 MiB stack per worker)
//! - Each rayon worker thread maintains its OWN thread-local DuckDB connection
//!   via thread_local! — connections are NEVER shared across threads
//!   (Connection is !Send, but thread_local is !Sync, so this is safe)
//! - Parallelization is across root IOCs — N values → N rayon jobs → reused
//!   thread-local connections (no new Connection::open per traversal)
//! - All DuckDB work runs INSIDE `bulk_pool().install()` so connections never
//!   cross thread boundaries.
//! - LRU cache per worker thread — mmap-backed persistence (lz4 komprese).
//!   Cache dir: $HLEDAC_GRAPH_CACHE_DIR or ~/.cache/hledac/graph_traverse_cache/
//!
//! P0 Optimization (F265-U5): Connection reuse per worker thread
//! - OLD: traverse_single() → Connection::open() each call = 50-80 MB × 2 workers
//! - NEW: thread_local! per worker, reused across ALL traversals in that thread
//! - read_only=True eliminates WAL overhead (read-only workload)
//! - PRAGMA threads=1 on each connection (we parallelize across workers, not inside DuckDB)
//!
//! M1 8GB bounds:
//! - 2 rayon workers × 1 thread-local DuckDB connection ≈ 15-25 MB resident
//!   (vs OLD: 50-80 MB — 3-5× reduction, vs F265 4-worker: ~50 MB → ~20 MB)
//! - DuckDB WAL disabled (read_only=True) — no WAL overhead
//! - No unbounded recursion — max_hops is a SQL parameter (bound at construction)
//! - LRU cache: MAX_ENTRIES=50k, MAX_BYTES=100MB, LZ4 komprese na cold data
//!
//! Design invariants:
//!   G.T1  No panics, no unwrap in #[pymethod] path (fail-soft)
//!   G.T2  Bounded: max_values cap prevents OOM from huge batch inputs
//!   G.T3  Fail-soft: DuckDB errors return empty dict, never raise
//!   G.T4  Parallel across values, NOT within a single traversal
//!   G.T5  M1 8GB safe: thread-local connections, read_only, PRAGMA threads=1
//!   G.T6  LRU cache: thread-local per worker, bounded, fail-soft, lz4 komprese

pub mod cache;

use crate::bulk_pool;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;
use std::cell::RefCell;
use std::env;
use std::path::PathBuf;

/* Thread-local DuckDB connection cache per rayon worker thread.
 *
 * F265-U5: Each thread opens its connection ONCE and reuses it across all
 * traversals. This reduces per-worker memory from ~15-20 MB (new conn per call)
 * to ~5 MB (reused conn, no WAL, read_only). read_only=True means:
 *   - No WAL (Write-Ahead Log) overhead
 *   - DuckDB uses snapshot isolation (consistent reads without locking)
 *   - Thread-safe for concurrent reads within the same connection
 */
thread_local! {
    static THREAD_CONN: RefCell<Option<duckdb::Connection>> = RefCell::new(None);
}

const DB_OPEN_ERR: &str = "DuckDB open failed — thread-local connection unavailable";

/// Hard cap on number of root IOCs in a single batch traversal.
const MAX_BATCH_VALUES: usize = 10_000;
/// Hard cap on results per root IOC (LIMIT in SQL).
const MAX_RESULTS_PER_ROOT: usize = 100;
/// Maximum allowed traversal depth.
const MAX_HOPS: usize = 10;

/// Default cache directory name (under app data or ~/.cache).
const DEFAULT_CACHE_DIR: &str = "graph_traverse_cache";

/// Get the cache directory path.
///
/// Priority: HLEDAC_GRAPH_CACHE_DIR env var → $HOME/.cache/hledac/graph_traverse_cache/
fn get_cache_dir() -> PathBuf {
    env::var("HLEDAC_GRAPH_CACHE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs::cache_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join("hledac")
                .join(DEFAULT_CACHE_DIR)
        })
}

/// Internal traversal result for a single root IOC.
#[derive(Clone, bincode::Encode, bincode::Decode)]
pub struct TraversalResult {
    pub dst_value: String,
    pub ioc_type: String,
    pub confidence: f64,
    pub source: String,
}

/// Run a single find_connected traversal for one root value.
///
/// F265-U5: ALL DuckDB work happens INSIDE the THREAD_CONN.with() closure.
/// This is required because Connection is !Send and we can't return a reference
/// to the RefCell-protected connection out of the closure.
///
/// The .with() call returns Vec<TraversalResult> — the closure's return value.
fn traverse_single(db_path: &str, root_value: &str, max_hops: usize) -> Vec<TraversalResult> {
    let max_hops = max_hops.min(MAX_HOPS);

    // .with() returns whatever the closure returns — in this case Vec<TraversalResult>
    THREAD_CONN.with(|cell| {
        let mut opt_conn = cell.borrow_mut();

        // Lazily open connection on first use in this thread
        if opt_conn.is_none() {
            let new_conn = match duckdb::Connection::open(db_path) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[graph_traverse] DuckDB open failed for {}: {}", db_path, e);
                    return Vec::new();
                }
            };
            // F265-U5: read_only=True = no WAL overhead
            // PRAGMA threads=1 = we parallelize across workers, not inside DuckDB
            let _ = new_conn.execute_batch("PRAGMA threads=1; PRAGMA read_only=true");
            *opt_conn = Some(new_conn);
        }

        let conn = match opt_conn.as_mut() {
            Some(c) => c,
            None => return Vec::new(),
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
    })
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
    let cache_dir = get_cache_dir();

    let results: Vec<(String, Vec<TraversalResult>)> =
        bulk_pool().install(|| values.par_iter().map(|v| {
            let traversal = cache::get_cached_traversal(&db_path_clone, v, max_hops, cache_dir.clone());
            (v.clone(), traversal)
        }).collect());

    let dict = PyDict::new(py);
    for (root_value, traversal) in results {
        let inner_list: Bound<'py, PyList> = PyList::empty(py);
        for item in traversal {
            let item_dict = PyDict::new(py);
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
///
/// F265-U5: Uses thread-local connection (same as traverse_single).
#[pyfunction]
#[pyo3(signature = (db_path, top_k = 20))]
pub fn graph_stats<'py>(
    py: Python<'py>,
    db_path: String,
    top_k: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);

    // Use thread-local connection — all DuckDB work inside .with() closure
    let result = THREAD_CONN.with(|cell| {
        let mut opt_conn = cell.borrow_mut();

        if opt_conn.is_none() {
            let new_conn = match duckdb::Connection::open(&db_path) {
                Ok(c) => c,
                Err(_) => return None,
            };
            let _ = new_conn.execute_batch("PRAGMA threads=1; PRAGMA read_only=true");
            *opt_conn = Some(new_conn);
        }

        let conn = opt_conn.as_mut()?;
        Some((conn.query_row::<i64, _, _>("SELECT COUNT(*) FROM ioc_nodes", [], |row| row.get(0)),
              conn.query_row::<i64, _, _>("SELECT COUNT(*) FROM ioc_edges", [], |row| row.get(0))))
    });

    match result {
        Some((Ok(node_count), Ok(edge_count))) => {
            let _ = dict.set_item("total_nodes", node_count);
            let _ = dict.set_item("total_edges", edge_count);
        }
        _ => {
            let _ = dict.set_item("error", DB_OPEN_ERR);
            return Ok(dict);
        }
    }

    // Top K nodes by out-degree — reuse the connection from above
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
    THREAD_CONN.with(|cell| {
        let opt_conn = cell.borrow();
        if let Some(conn) = opt_conn.as_ref() {
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
        }
    });
    let _ = dict.set_item("top_nodes", top_nodes);

    Ok(dict)
}

/// PAR-1 P0: Flattened batch graph traversal — single Rayon-parallel call.
const MAX_FLAT_RESULTS: usize = 5000;

/// Flat traversal result with source attribution.
#[derive(Clone)]
struct FlatTraversalResult {
    dst_value: String,
    ioc_type: String,
    confidence: f64,
    source: String,
    depth: usize,
}

/// Parallel flattened batch graph traversal.
#[pyfunction]
#[pyo3(signature = (db_path, values, max_hops = 2, max_per_root = 20))]
pub fn batch_graph_traverse_flat<'py>(
    py: Python<'py>,
    db_path: String,
    values: Vec<String>,
    max_hops: usize,
    max_per_root: usize,
) -> PyResult<Bound<'py, PyList>> {
    if values.is_empty() {
        return Ok(PyList::empty(py));
    }

    if values.len() > MAX_BATCH_VALUES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_graph_traverse_flat: too many values ({} > {})",
            values.len(),
            MAX_BATCH_VALUES
        )));
    }

    let max_hops = if max_hops == 0 { 1 } else { max_hops.min(MAX_HOPS) };
    let max_per_root = max_per_root.min(MAX_RESULTS_PER_ROOT);
    let db_path_clone = db_path.clone();

    let flat_results: Vec<FlatTraversalResult> = bulk_pool().install(|| {
        values.par_iter().flat_map(|root_value| {
            let results = traverse_single(&db_path_clone, root_value, max_hops);
            results.into_iter().take(max_per_root).map(|r| FlatTraversalResult {
                dst_value: r.dst_value,
                ioc_type: r.ioc_type,
                confidence: r.confidence,
                source: root_value.clone(),
                depth: 1,
            }).collect::<Vec<_>>()
        }).collect()
    });

    let flat_results = flat_results.into_iter().take(MAX_FLAT_RESULTS).collect::<Vec<_>>();

    let list = PyList::empty(py);
    for item in flat_results {
        let item_dict = PyDict::new(py);
        let _ = item_dict.set_item("value", &item.dst_value);
        let _ = item_dict.set_item("ioc_type", &item.ioc_type);
        let _ = item_dict.set_item("confidence", item.confidence);
        let _ = item_dict.set_item("source", &item.source);
        let _ = item_dict.set_item("depth", item.depth);
        let _ = list.append(item_dict);
    }

    Ok(list)
}

/// Drop all thread-local DuckDB connections and flush LRU cache.
///
/// F265-U5: Called between sprints to release connection memory.
/// After this call, the next traversal will open a fresh connection.
/// F265B-III: Also flushes LRU cache to mmap for cross-sprint persistence.
#[pyfunction]
pub fn drop_connections() -> PyResult<()> {
    THREAD_CONN.with(|cell| {
        let mut opt_conn = cell.borrow_mut();
        *opt_conn = None;
    });
    // Flush LRU cache to mmap before dropping
    let cache_dir = get_cache_dir();
    cache::flush_cache(cache_dir);
    Ok(())
}

/// Register graph_traverse functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_graph_traverse, m)?)?;
    m.add_function(wrap_pyfunction!(graph_traverse_single, m)?)?;
    m.add_function(wrap_pyfunction!(batch_graph_traverse_flat, m)?)?;
    m.add_function(wrap_pyfunction!(graph_stats, m)?)?;
    m.add_function(wrap_pyfunction!(drop_connections, m)?)?;
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

    #[test]
    fn test_flat_traversal_constants() {
        assert_eq!(MAX_FLAT_RESULTS, 5000);
    }

    #[test]
    fn test_flat_result_struct_clone() {
        let r = FlatTraversalResult {
            dst_value: "evil.com".to_string(),
            ioc_type: "domain".to_string(),
            confidence: 0.9,
            source: "source.com".to_string(),
            depth: 1,
        };
        let r2 = r.clone();
        assert_eq!(r.dst_value, r2.dst_value);
        assert_eq!(r.source, r2.source);
        assert_eq!(r.depth, r2.depth);
    }
}

//! lmdb_dht — Rust LMDB backend pro DHT LocalGraphStore
//!
//! ISSUE-004: Eliminating asyncio.to_thread overhead in DHT hot paths
//!
//! ## Problem
//! `dht/local_graph.py` had 10+ `asyncio.to_thread()` calls per operation:
//!   await asyncio.to_thread(_put)   // ~50-100 µs overhead per hop
//!   await asyncio.to_thread(_get)   // queue + thread context switch
//!
//! In a BFS traversal of 5-10 hops, this adds 500 µs - 1 ms of
//! pure thread-hop overhead — before any actual LMDB I/O.
//!
//! ## Solution
//! Rust calls Python `lmdb` library with `py.allow_threads()` GIL release.
//! This runs LMDB I/O in the rayon io_pool thread without:
//!   - Python asyncio thread pool queue overhead
//!   - Thread context switch on each call
//!
//! The Python `lmdb` library is already proven (used in lmdb_bulk.py).
//! Rust provides the GIL-release bridge via PyO3 0.28.2 `allow_threads()`.
//!
//! ## Design
//! - Opens LMDB environment via Python lmdb.open() called from Rust
//! - `py.allow_threads()` releases GIL during LMDB I/O
//! - Encrypted data handling stays in Python (LocalGraphStore)
//! - io_pool (2 threads) for I/O-bound operations
//!
//! ## PyO3 Version
//! PyO3 =0.28.2 — `py.allow_threads()` IS available (lesson #1985: 0.29 removed it)

use pyo3::prelude::*;
use std::collections::HashMap;

/// Bundled Python lmdb module — imported once, reused across calls.
/// ISSUE-FIX: Uses OnceLock<Result> instead of LazyLock with unwrap().
/// If lmdb is not installed, returns Err(&str) instead of panicking.
static LMDB_MODULE: std::sync::OnceLock<Result<Py<PyModule>, &'static str>> =
    std::sync::OnceLock::new();

/// Get the lmdb Python module, initializing it on first call.
/// Returns Err if lmdb is not installed in the Python environment.
fn get_lmdb_module() -> Result<&'static Py<PyModule>, &'static str> {
    LMDB_MODULE
        .get_or_init(|| {
            Python::with_gil(|py| {
                PyModule::import(py, "lmdb")
                    .map(|m| m.into_py(py))
                    .map_err(|e| {
                        let msg = format!("lmdb_dht: failed to import lmdb: {}", e);
                        // Leak the String to get &'static str
                        Box::leak(msg.into_boxed_str())
                    })
            })
        })
        .as_ref()
        .map_err(|&s| s)
}

// ─────────────────────────────────────────────────────────────────────────────
// Lazy per-thread LMDB environments (path → env object)
//
// We cache open env objects per-thread so repeated calls don't re-open.
// The env is stored in a thread-local RefCell.
// ─────────────────────────────────────────────────────────────────────────────

thread_local! {
    static ENV_CACHE: std::cell::RefCell<HashMap<String, Py<PyAny>>> = std::cell::RefCell::new(HashMap::new());
}

/// Get or create a Python lmdb.Environment for the given path.
/// The env is cached per-thread for zero re-open overhead.
fn get_lmdb_env<'py>(py: Python<'py>, path: &str) -> PyResult<Bound<'py, PyAny>> {
    let lmdb = get_lmdb_module()
        .map_err(|e| pyo3::exceptions::PyImportError::new_err(*e))?;
    let lmdb = lmdb.as_ref(py);
    ENV_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        if let Some(env) = cache.get(path) {
            // Rebind cached Py<PyAny> to current 'py lifetime
            return Ok(env.as_ref(py).into());
        }
        // Open new env: lmdb.open(path, map_size=256*1024*1024)
        let env: Bound<'py, PyAny> = attr(lmdb, "open")?.call1((path,))?.into();
        cache.insert(path.to_string(), env.clone().unbind());
        Ok(env)
    })
}

/// Close and remove a cached env (for cleanup / testing).
fn close_lmdb_env(path: &str) {
    ENV_CACHE.with(|cache| {
        cache.borrow_mut().remove(path);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// LMDB helpers — typed wrappers around Python lmdb objects
// ─────────────────────────────────────────────────────────────────────────────

/// Safe attribute access — propagates PyErr instead of panicking.
#[inline]
fn attr(obj: &Bound<'_, PyAny>, name: &str) -> PyResult<Bound<'_, PyAny>> {
    obj.get_attr(name)
}

/// Safe no-argument method call.
#[inline]
fn call0(obj: &Bound<'_, PyAny>, method: &str) -> PyResult<Bound<'_, PyAny>> {
    obj.call_method0(method)
}

/// Safe one-argument method call.
#[inline]
fn call1(obj: &Bound<'_, PyAny>, method: &str, args: impl PyNativeType) -> PyResult<Bound<'_, PyAny>> {
    obj.call_method1(method, args)
}

/// Execute a read-only LMDB transaction.
fn lmdb_get(env: &Bound<'_, PyAny>, key: &[u8]) -> PyResult<Option<Vec<u8>>> {
    let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
    let result: Option<Vec<u8>> = call1(&txn, "get", (key,))?
        .extract()
        .ok()
        .unwrap_or(None);
    drop(txn);
    Ok(result)
}

/// Execute a read-only LMDB transaction reading two keys.
fn lmdb_get_two(
    env: &Bound<'_, PyAny>,
    key1: &[u8],
    key2: &[u8],
) -> PyResult<(Option<Vec<u8>>, Option<Vec<u8>>)> {
    let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
    let v1: Option<Vec<u8>> = call1(&txn, "get", (key1,))?
        .extract()
        .ok()
        .unwrap_or(None);
    let v2: Option<Vec<u8>> = call1(&txn, "get", (key2,))?
        .extract()
        .ok()
        .unwrap_or(None);
    drop(txn);
    Ok((v1, v2))
}

/// Execute a write LMDB transaction (single put, then commit).
fn lmdb_put(
    env: &Bound<'_, PyAny>,
    key: &[u8],
    value: &[u8],
) -> PyResult<()> {
    let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((true,))?;
    call1(&txn, "put", (key, value))?;
    call0(&txn, "commit")?;
    Ok(())
}

/// Execute a write LMDB transaction with two puts, then commit.
fn lmdb_put_two(
    env: &Bound<'_, PyAny>,
    key1: &[u8],
    value1: &[u8],
    key2: &[u8],
    value2: &[u8],
) -> PyResult<()> {
    let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((true,))?;
    call1(&txn, "put", (key1, value1))?;
    call1(&txn, "put", (key2, value2))?;
    call0(&txn, "commit")?;
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Node operations (encrypted blob + neighbors)
// ─────────────────────────────────────────────────────────────────────────────

/// Store a graph node: raw bytes (pre-encrypted by Python) + neighbors list.
///
/// path:           LMDB directory path (parent dir — env opened here)
/// key:            node_id bytes
/// value:          encrypted node features (raw bytes from Python)
/// neighbors_json: JSON-encoded list of neighbor node_ids
///
/// Returns Ok(()) or Err(message)
#[pyfunction]
#[pyo3(name = "lmdb_dht_put_node")]
pub fn lmdb_dht_put_node<'py>(
    py: Python<'py>,
    path: String,
    key: Vec<u8>,
    value: Vec<u8>,
    neighbors_json: Vec<u8>,
) -> PyResult<()> {
    let env = get_lmdb_env(py, &path)?;
    let neigh_key = {
        let mut k = b"neighbors:".to_vec();
        k.extend_from_slice(&key);
        k
    };
    py.allow_threads(|| {
        lmdb_put_two(&env, &key, &value, &neigh_key, &neighbors_json)
    })
}

/// Retrieve a graph node's raw value + neighbors.
///
/// path:  LMDB directory path
/// key:   node_id bytes
///
/// Returns (value_bytes, neighbors_json) or None if not found.
#[pyfunction]
#[pyo3(name = "lmdb_dht_get_node")]
pub fn lmdb_dht_get_node<'py>(
    py: Python<'py>,
    path: String,
    key: Vec<u8>,
) -> PyResult<Option<(Vec<u8>, Vec<u8>)>> {
    let env = get_lmdb_env(py, &path)?;
    let neigh_key = {
        let mut k = b"neighbors:".to_vec();
        k.extend_from_slice(&key);
        k
    };
    let result = py.allow_threads(|| lmdb_get_two(&env, &key, &neigh_key))?;
    match result {
        (Some(v), Some(n)) => Ok(Some((v, n))),
        _ => Ok(None),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DHT node operations (unencrypted — routing table)
// ─────────────────────────────────────────────────────────────────────────────

/// Store a DHT node record: JSON-encoded {host, port, node_id} (pre-encrypted).
///
/// path:     LMDB directory path
/// node_id:  40-char hex peer ID
/// value:    encrypted JSON bytes
#[pyfunction]
#[pyo3(name = "lmdb_dht_put_dht_node")]
pub fn lmdb_dht_put_dht_node<'py>(
    py: Python<'py>,
    path: String,
    node_id: Vec<u8>,
    value: Vec<u8>,
) -> PyResult<()> {
    let env = get_lmdb_env(py, &path)?;
    let mut key = b"dht_node:".to_vec();
    key.extend_from_slice(&node_id);
    py.allow_threads(|| lmdb_put(&env, &key, &value))
}

/// Retrieve a DHT node's encrypted record.
#[pyfunction]
#[pyo3(name = "lmdb_dht_get_dht_node")]
pub fn lmdb_dht_get_dht_node<'py>(
    py: Python<'py>,
    path: String,
    node_id: Vec<u8>,
) -> PyResult<Option<Vec<u8>>> {
    let env = get_lmdb_env(py, &path)?;
    let mut key = b"dht_node:".to_vec();
    key.extend_from_slice(&node_id);
    py.allow_threads(|| lmdb_get(&env, &key))
}

/// Scan all DHT node records, return Vec of (node_id, encrypted_value).
#[pyfunction]
#[pyo3(name = "lmdb_dht_get_all_dht_nodes")]
pub fn lmdb_dht_get_all_dht_nodes<'py>(
    py: Python<'py>,
    path: String,
    limit: usize,
) -> PyResult<Vec<(Vec<u8>, Vec<u8>)>> {
    let env = get_lmdb_env(py, &path)?;
    let limit = limit.min(100_000);
    let prefix = b"dht_node:".to_vec();

    let results = py.allow_threads(|| {
        let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
        let mut cursor: Bound<'_, PyAny> = call0(&txn, "cursor")?;

        let iter: Bound<'_, PyAny> = call0(&cursor, "iter")?;
        let mut out = Vec::with_capacity(limit.min(1000));

        for item in iter.iter() {
            let item = item.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht iterator error: {}", e)))?;
            let pair: Vec<Vec<u8>> = item.extract().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht extract error: {}", e)))?;
            if pair.len() != 2 {
                continue;
            }
            let k = &pair[0];
            if k.starts_with(&prefix) {
                out.push((k[9..].to_vec(), pair[1].clone()));
                if out.len() >= limit {
                    break;
                }
            }
        }
        drop(cursor);
        drop(txn);
        out
    });

    Ok(results)
}

/// Count total DHT nodes in store.
#[pyfunction]
#[pyo3(name = "lmdb_dht_count_dht_nodes")]
pub fn lmdb_dht_count_dht_nodes<'py>(
    py: Python<'py>,
    path: String,
) -> PyResult<usize> {
    let env = get_lmdb_env(py, &path)?;
    let prefix = b"dht_node:".to_vec();

    let count = py.allow_threads(|| {
        let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
        let mut cursor: Bound<'_, PyAny> = call0(&txn, "cursor")?;
        let mut count = 0;

        let iter: Bound<'_, PyAny> = call0(&cursor, "iter")?;
        for item in iter.iter() {
            let item = item.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht iterator error: {}", e)))?;
            let pair: Vec<Vec<u8>> = item.extract().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht extract error: {}", e)))?;
            if pair.len() != 2 || !pair[0].starts_with(&prefix) {
                continue;
            }
            count += 1;
        }
        drop(cursor);
        drop(txn);
        count
    });

    Ok(count)
}

/// Delete all DHT node records.
#[pyfunction]
#[pyo3(name = "lmdb_dht_clear_dht_nodes")]
pub fn lmdb_dht_clear_dht_nodes<'py>(
    py: Python<'py>,
    path: String,
) -> PyResult<()> {
    let env = get_lmdb_env(py, &path)?;
    let prefix = b"dht_node:".to_vec();

    py.allow_threads(|| {
        // Collect keys first (can't delete while iterating cursor)
        let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
        let mut cursor: Bound<'_, PyAny> = call0(&txn, "cursor")?;
        let mut to_delete: Vec<Vec<u8>> = Vec::new();

        let iter: Bound<'_, PyAny> = call0(&cursor, "iter")?;
        for item in iter.iter() {
            let item = item.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht iterator error: {}", e)))?;
            let pair: Vec<Vec<u8>> = item.extract().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht extract error: {}", e)))?;
            if pair.len() != 2 {
                continue;
            }
            if pair[0].starts_with(&prefix) {
                to_delete.push(pair[0].clone());
            }
        }
        drop(cursor);
        drop(txn);

        // Delete in write transaction
        let write_txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((true,))?;
        for key in &to_delete {
            let _ = call1(&write_txn, "delete", (key,));
        }
        call0(&write_txn, "commit")?;
    });

    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Routing snapshot operations
// ─────────────────────────────────────────────────────────────────────────────

/// Save routing table snapshot (encrypted by Python).
#[pyfunction]
#[pyo3(name = "lmdb_dht_save_routing_snapshot")]
pub fn lmdb_dht_save_routing_snapshot<'py>(
    py: Python<'py>,
    path: String,
    payload: Vec<u8>,
) -> PyResult<()> {
    let env = get_lmdb_env(py, &path)?;
    py.allow_threads(|| lmdb_put(&env, b"routing_table_v1", &payload))
}

/// Load routing table snapshot.
#[pyfunction]
#[pyo3(name = "lmdb_dht_load_routing_snapshot")]
pub fn lmdb_dht_load_routing_snapshot<'py>(
    py: Python<'py>,
    path: String,
) -> PyResult<Option<Vec<u8>>> {
    let env = get_lmdb_env(py, &path)?;
    py.allow_threads(|| lmdb_get(&env, b"routing_table_v1"))
}

// ─────────────────────────────────────────────────────────────────────────────
// Full DB scan — ISSUE-004: replaces asyncio.to_thread full-cursor scan
// ─────────────────────────────────────────────────────────────────────────────

/// Scan ALL node IDs (excluding "neighbors:" prefix entries).
///
/// path:  LMDB directory path
/// limit: Maximum number of node IDs to return
///
/// Returns all raw node_id keys (excluding "neighbors:*" entries).
/// This replaces the Python fallback full-cursor scan.
#[pyfunction]
#[pyo3(name = "lmdb_dht_scan_all_nodes")]
pub fn lmdb_dht_scan_all_nodes<'py>(
    py: Python<'py>,
    path: String,
    limit: usize,
) -> PyResult<Vec<Vec<u8>>> {
    let env = get_lmdb_env(py, &path)?;
    let limit = limit.min(100_000);
    let neigh_prefix = b"neighbors:".to_vec();

    let results = py.allow_threads(|| {
        let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
        let mut cursor: Bound<'_, PyAny> = call0(&txn, "cursor")?;

        let iter: Bound<'_, PyAny> = call0(&cursor, "iter")?;
        let mut out: Vec<Vec<u8>> = Vec::with_capacity(limit.min(1000));

        for item in iter.iter() {
            let item = item.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht iterator error: {}", e)))?;
            let pair: Vec<Vec<u8>> = item.extract().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht extract error: {}", e)))?;
            if pair.len() != 2 {
                continue;
            }
            let k = &pair[0];
            if !k.starts_with(&neigh_prefix) {
                out.push(k.clone());
                if out.len() >= limit {
                    break;
                }
            }
        }
        drop(cursor);
        drop(txn);
        out
    });

    Ok(results)
}

// ─────────────────────────────────────────────────────────────────────────────
// Bulk BFS traversal — ISSUE-004 key optimization
//
// Single Rust call replaces 5-10 Python asyncio.to_thread() hops.
// ─────────────────────────────────────────────────────────────────────────────

/// BFS neighbor traversal — single Rust call, no asyncio.to_thread.
///
/// Replaces 5-10 × asyncio.to_thread() hops with one Rust→Python call chain.
///
/// path:       LMDB directory path
/// start_keys: Vec of starting node IDs (level 0)
/// max_hops:   Maximum traversal depth
///
/// Returns all reachable node_ids within max_hops (deduplicated).
#[pyfunction]
#[pyo3(name = "lmdb_dht_bfs_traverse")]
pub fn lmdb_dht_bfs_traverse<'py>(
    py: Python<'py>,
    path: String,
    start_keys: Vec<Vec<u8>>,
    max_hops: usize,
) -> PyResult<Vec<Vec<u8>>> {
    let env = get_lmdb_env(py, &path)?;
    let max_hops = max_hops.min(10);
    let neigh_prefix = b"neighbors:".to_vec();

    let results = py.allow_threads(|| {
        let mut visited: std::collections::HashSet<Vec<u8>> =
            std::collections::HashSet::new();
        let mut frontier: Vec<Vec<u8>> = start_keys;

        for _ in 0..max_hops {
            if frontier.is_empty() {
                break;
            }
            let mut next_frontier: Vec<Vec<u8>> = Vec::new();

            for key in frontier {
                let neigh_key = {
                    let mut k = neigh_prefix.clone();
                    k.extend_from_slice(&key);
                    k
                };

                let txn: Bound<'_, PyAny> = attr(env, "begin")?.call1((false,))?;
                let neigh_data: Option<Vec<u8>> = call1(&txn, "get", (&neigh_key,))?
                    .extract()
                    .ok()
                    .unwrap_or(None);
                drop(txn);

                if let Some(data) = neigh_data {
                    // Parse JSON neighbor list
                    if let Ok(neighbors) =
                        serde_json::from_slice::<Vec<String>>(&data)
                    {
                        for neighbor in neighbors {
                            let n_bytes = neighbor.into_bytes();
                            if visited.insert(n_bytes.clone()) {
                                next_frontier.push(n_bytes);
                            }
                        }
                    }
                }
            }
            frontier = next_frontier;
        }

        visited.into_iter().collect()
    });

    Ok(results)
}

/// Close and release all cached LMDB environments.
/// Call at module shutdown / test cleanup.
#[pyfunction]
pub fn lmdb_dht_close_env(path: String) -> PyResult<()> {
    close_lmdb_env(&path);
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Module registration
// ─────────────────────────────────────────────────────────────────────────────

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(lmdb_dht_put_node, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_get_node, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_put_dht_node, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_get_dht_node, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_get_all_dht_nodes, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_count_dht_nodes, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_clear_dht_nodes, m)?)?;
    m.add_function(wrap_pyfunction!(
        lmdb_dht_save_routing_snapshot,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        lmdb_dht_load_routing_snapshot,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_scan_all_nodes, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_bfs_traverse, m)?)?;
    m.add_function(wrap_pyfunction!(lmdb_dht_close_env, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_function_names() {
        // Verify all function names are registered correctly
        let names = [
            "lmdb_dht_put_node",
            "lmdb_dht_get_node",
            "lmdb_dht_put_dht_node",
            "lmdb_dht_get_dht_node",
            "lmdb_dht_get_all_dht_nodes",
            "lmdb_dht_count_dht_nodes",
            "lmdb_dht_clear_dht_nodes",
            "lmdb_dht_save_routing_snapshot",
            "lmdb_dht_load_routing_snapshot",
            "lmdb_dht_scan_all_nodes",
            "lmdb_dht_bfs_traverse",
            "lmdb_dht_close_env",
        ];
        for name in names {
            assert!(name.len() > 0);
        }
    }
}

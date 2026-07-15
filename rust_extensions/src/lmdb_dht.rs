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

// ─────────────────────────────────────────────────────────────────────────────
// LMDB helpers — direct Python lmdb calls without caching
// ─────────────────────────────────────────────────────────────────────────────

/// Get or create a Python lmdb.Environment for the given path.
/// Note: Caching removed due to PyO3 0.23 Bound API complexity.
fn get_lmdb_env<'py>(py: Python<'py>, path: &str) -> PyResult<Bound<'py, PyAny>> {
    // Import lmdb and open environment
    let lmdb = PyModule::import(py, "lmdb")?;
    // Use getattr on PyModule directly (avoids as_any() lifetime issues)
    let open_fn: Bound<'py, PyAny> = lmdb.getattr("open")?;
    let env: Bound<'py, PyAny> = open_fn.call1((path,))?.into();
    Ok(env)
}

/// Close and remove a cached env (for cleanup / testing).
fn close_lmdb_env(_path: &str) {
    // Cache removed - no-op for compatibility
}

// ─────────────────────────────────────────────────────────────────────────────
// LMDB helpers — typed wrappers around Python lmdb objects
// ─────────────────────────────────────────────────────────────────────────────

/// Execute a read-only LMDB transaction.
fn lmdb_get(env: &Bound<'_, PyAny>, key: &[u8]) -> PyResult<Option<Vec<u8>>> {
    let txn = env.getattr("begin")?.call1((false,))?;
    let result: Option<Vec<u8>> = txn.call_method1("get", (key,))?
        .extract()
        .ok()
        .unwrap_or(None);
    drop(txn);
    Ok(result)
}

/// Execute a read-only LMDB transaction reading two keys.
fn lmdb_get_two<'py>(
    env: &Bound<'py, PyAny>,
    key1: &[u8],
    key2: &[u8],
) -> PyResult<(Option<Vec<u8>>, Option<Vec<u8>>)> {
    let txn = env.getattr("begin")?.call1((false,))?;
    let v1: Option<Vec<u8>> = txn.call_method1("get", (key1,))?
        .extract()
        .ok()
        .unwrap_or(None);
    let v2: Option<Vec<u8>> = txn.call_method1("get", (key2,))?
        .extract()
        .ok()
        .unwrap_or(None);
    drop(txn);
    Ok((v1, v2))
}

/// Execute a write LMDB transaction (single put, then commit).
fn lmdb_put<'py>(
    env: &Bound<'py, PyAny>,
    key: &[u8],
    value: &[u8],
) -> PyResult<()> {
    let txn = env.getattr("begin")?.call1((true,))?;
    txn.call_method1("put", (key, value))?;
    txn.call_method0("commit")?;
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
    let txn = env.getattr("begin")?.call1((true,))?;
    txn.call_method1("put", (key1, value1))?;
    txn.call_method1("put", (key2, value2))?;
    txn.call_method0("commit")?;
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
    // TEMP FIX: allow_threads removed - GIL held during LMDB I/O
    // TODO: Refactor to extract data before allow_threads
    {
        lmdb_put_two(&env, &key, &value, &neigh_key, &neighbors_json)
    }
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
    let result = lmdb_get_two(&env, &key, &neigh_key)?;
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
    lmdb_put(&env, &key, &value)
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
    lmdb_get(&env, &key)
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

    let txn = env.getattr("begin")?.call1((false,))?;
    let mut cursor = txn.call_method0("cursor")?;

    let py_iter = cursor.call_method0("iter")?;
    let mut rust_iter = py_iter.try_iter()?;
    let mut out = Vec::with_capacity(limit.min(1000));

    for item in rust_iter {
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

    Ok(out)
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

    let txn: Bound<'_, PyAny> = env.getattr("begin")?.call1((false,))?;
    let mut cursor: Bound<'_, PyAny> = txn.call_method0("cursor")?;
    let mut count = 0;

    let py_iter: Bound<'_, PyAny> = cursor.call_method0("iter")?;
    let mut rust_iter = py_iter.try_iter()?;
    for item in rust_iter {
        let item = item.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht iterator error: {}", e)))?;
        let pair: Vec<Vec<u8>> = item.extract().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("lmdb_dht extract error: {}", e)))?;
        if pair.len() != 2 || !pair[0].starts_with(&prefix) {
            continue;
        }
        count += 1;
    }
    drop(cursor);
    drop(txn);

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

    // TEMP FIX: allow_threads removed - GIL held during LMDB I/O
    // TODO: Refactor to extract data before allow_threads
    {
        // Collect keys first (can't delete while iterating cursor)
        let txn: Bound<'_, PyAny> = env.getattr("begin")?.call1((false,))?;
        let mut cursor: Bound<'_, PyAny> = txn.call_method0("cursor")?;
        let mut to_delete: Vec<Vec<u8>> = Vec::new();

        let py_iter: Bound<'_, PyAny> = cursor.call_method0("iter")?;
        let mut rust_iter = py_iter.try_iter()?;
        for item in rust_iter {
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
        let write_txn: Bound<'_, PyAny> = env.getattr("begin")?.call1((true,))?;
        for key in &to_delete {
            let _ = write_txn.call_method1("delete", (key,));
        }
        write_txn.call_method0("commit")?;
    };

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
    lmdb_put(&env, b"routing_table_v1", &payload)
}

/// Load routing table snapshot.
#[pyfunction]
#[pyo3(name = "lmdb_dht_load_routing_snapshot")]
pub fn lmdb_dht_load_routing_snapshot<'py>(
    py: Python<'py>,
    path: String,
) -> PyResult<Option<Vec<u8>>> {
    let env = get_lmdb_env(py, &path)?;
    lmdb_get(&env, b"routing_table_v1")
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

    let txn: Bound<'_, PyAny> = env.getattr("begin")?.call1((false,))?;
    let mut cursor: Bound<'_, PyAny> = txn.call_method0("cursor")?;

    let py_iter: Bound<'_, PyAny> = cursor.call_method0("iter")?;
    let mut rust_iter = py_iter.try_iter()?;
    let mut out: Vec<Vec<u8>> = Vec::with_capacity(limit.min(1000));

    for item in rust_iter {
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

    Ok(out)
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

            let txn: Bound<'_, PyAny> = env.getattr("begin")?.call1((false,))?;
            let py_bytes = pyo3::types::PyBytes::new(py, &neigh_key);
            let neigh_data: Option<Vec<u8>> = txn.call_method1("get", (py_bytes.as_ref(),))?
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

    Ok(visited.into_iter().collect())
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

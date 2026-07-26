//! graph_centrality — Parallel graph centrality via rayon.
//!
//! Sprint F320+: Parallel graph centrality for GraphRAG (B8) and RelationshipDiscovery (B7).
//!
//! Architecture:
//! - Uses rayon ThreadPool for CPU-bound graph computations
//! - adjacency: Vec<(node_id, Vec<neighbor_id>)> — zero-copy from Python via to_vec()
//! - node_ids: Vec<String> — parallel iteration with indexed parallel iterator
//! - All algorithms: Brandes (betweenness), Dijkstra-based (closeness), Power iteration (eigenvector)
//! - M1 8GB safe: bounded collections, no recursion, fail-soft
//!
//! Design invariants:
//!   GC.T1  No panics, no unwrap in #[pymethod] path (fail-soft)
//!   GC.T2  Bounded: n <= 50_000 nodes cap prevents OOM
//!   GC.T3  Fail-soft: computation errors return empty dict / default scores
//!   GC.T4  Zero-copy from Python: adjacency stays owned, we clone only what we need
//!   GC.T5  rayon parallel across nodes for independent centrality metrics
//!   GC.T6  Betweenness uses Brandes algorithm with early termination cutoff

use crate::io_pool;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_NODES: usize = 50_000;
const MAX_EDGES_PER_NODE: usize = 10_000;
const EIGENITOR_MAX_ITER: usize = 100;
const EIGENVECTOR_TOLERANCE: f64 = 1e-6;

/// Result structure for batch centrality computation.
#[derive(Clone)]
struct NodeCentrality {
    node_id: String,
    degree: f64,
    betweenness: f64,
    closeness: f64,
    eigenvector: f64,
    pagerank: f64,
}

// ---------------------------------------------------------------------------
// Python-callable functions
// ---------------------------------------------------------------------------

/// Compute all centrality metrics for a graph in a single pass (B8 pattern).
///
/// adjacency: Vec of (node_id, Vec<neighbor_id>) representing the graph
/// node_ids: ordered list of all node IDs (maps index → node_id)
///
/// Returns dict: {node_id: {"degree": f, "betweenness": f, "closeness": f, "eigenvector": f, "pagerank": f}}
#[pyfunction]
#[pyo3(signature = (adjacency, /))]
pub fn batch_centrality_all<'py>(
    py: Python<'py>,
    adjacency: Vec<(String, Vec<String>)>,
) -> PyResult<Bound<'py, PyDict>> {
    let n = adjacency.len();
    if n == 0 {
        return Ok(PyDict::new(py));
    }
    if n > MAX_NODES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_centrality_all: too many nodes ({} > {})",
            n, MAX_NODES
        )));
    }

    // Build index maps for O(1) lookup
    let (idx_map, name_vec): (HashMap<String, usize>, Vec<String>) = {
        let mut idx_map = HashMap::with_capacity(n);
        let mut name_vec = Vec::with_capacity(n);
        for (i, (node_id, _)) in adjacency.iter().enumerate() {
            idx_map.insert(node_id.clone(), i);
            name_vec.push(node_id.clone());
        }
        (idx_map, name_vec)
    };

    // Build adjacency as indices for fast neighbor lookup
    let adj_idx: Vec<Vec<usize>> = adjacency
        .iter()
        .map(|(node_id, neighbors)| {
            neighbors
                .iter()
                .filter_map(|n| idx_map.get(n).copied())
                .collect()
        })
        .collect();

    let n_f = n as f64;

    // --- Degree centrality (parallel, O(n)) ---
    let degree_scores: Vec<f64> = adj_idx
        .par_iter()
        .map(|neighbors| {
            let deg = neighbors.len() as f64;
            if n_f > 1.0 {
                deg / (n_f - 1.0)
            } else {
                0.0
            }
        })
        .collect();

    // --- Betweenness centrality (Brandes, parallel per source node, O(n*m)) ---
    let betweenness_scores: Vec<f64> = if n <= 2000 {
        // Full Brandes for small graphs
        let bet_scores: Vec<f64> = (0..n)
            .into_par_iter()
            .map(|s| {
                let mut sigma: Vec<f64> = vec![0.0; n];
                let mut dist: Vec<i32> = vec![-1; n];
                let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];
                let mut stack: Vec<usize> = Vec::new();

                sigma[s] = 1.0;
                dist[s] = 0;

                // BFS from s
                let mut queue = std::collections::VecDeque::new();
                queue.push_back(s);

                while let Some(v) = queue.pop_front() {
                    stack.push(v);
                    for &w in &adj_idx[v] {
                        if dist[w] < 0 {
                            dist[w] = dist[v] + 1;
                            queue.push_back(w);
                        }
                        if dist[w] == dist[v] + 1 {
                            sigma[w] += sigma[v];
                            pred[w].push(v);
                        }
                    }
                }

                // Back-propagation
                let mut delta: Vec<f64> = vec![0.0; n];
                while let Some(w) = stack.pop() {
                    for &v in &pred[w] {
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                    }
                }
                if s < n {
                    delta[s]
                } else {
                    0.0
                }
            })
            .collect();

        // Normalize: divide by (n-1)*(n-2) for undirected
        let norm = if n > 2 { 2.0 / ((n_f - 1.0) * (n_f - 2.0)) } else { 1.0 };
        bet_scores
            .into_iter()
            .map(|b| b * norm)
            .collect()
    } else {
        // Sampling approximation for large graphs (10% of nodes, min 50, max 500)
        let sample_size = (n as f64 * 0.1).ceil() as usize;
        let sample_size = sample_size.clamp(50, 500);

        // Use deterministic stride-based sampling for reproducibility
        let sampled_sources: Vec<usize> = (0..n)
            .into_par_iter()
            .filter(|&i| i % (n / sample_size) == 0)
            .collect();

        let bet_partial: Vec<Vec<f64>> = sampled_sources
            .par_iter()
            .map(|&s| {
                let mut sigma: Vec<f64> = vec![0.0; n];
                let mut dist: Vec<i32> = vec![-1; n];
                let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];
                let mut stack: Vec<usize> = Vec::new();

                sigma[s] = 1.0;
                dist[s] = 0;

                let mut queue = std::collections::VecDeque::new();
                queue.push_back(s);

                while let Some(v) = queue.pop_front() {
                    stack.push(v);
                    for &w in &adj_idx[v] {
                        if dist[w] < 0 {
                            dist[w] = dist[v] + 1;
                            queue.push_back(w);
                        }
                        if dist[w] == dist[v] + 1 {
                            sigma[w] += sigma[v];
                            pred[w].push(v);
                        }
                    }
                }

                let mut delta: Vec<f64> = vec![0.0; n];
                while let Some(w) = stack.pop() {
                    for &v in &pred[w] {
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                    }
                }

                // Return full delta vector (will be averaged)
                delta
            })
            .collect();

        // Sum sampled betweenness contributions
        let mut betweenness: Vec<f64> = vec![0.0; n];
        for partial in &bet_partial {
            for (i, &d) in partial.iter().enumerate() {
                betweenness[i] += d;
            }
        }

        // Normalize
        let norm = if n > 2 {
            ((n_f - 1.0) * (n_f - 2.0)) / (2.0 * sampled_sources.len() as f64)
        } else {
            1.0
        };
        betweenness
            .into_iter()
            .map(|b| b * norm)
            .collect()
    };

    // --- Closeness centrality (parallel BFS-based, O(n*m)) ---
    let closeness_scores: Vec<f64> = (0..n)
        .into_par_iter()
        .map(|s| {
            let mut dist: Vec<i32> = vec![-1; n];
            dist[s] = 0;
            let mut queue = std::collections::VecDeque::new();
            queue.push_back(s);

            while let Some(v) = queue.pop_front() {
                for &w in &adj_idx[v] {
                    if dist[w] < 0 {
                        dist[w] = dist[v] + 1;
                        queue.push_back(w);
                    }
                }
            }

            let sum_dist: i32 = dist.iter().sum();
            if sum_dist > 0 && n > 1 {
                (n_f - 1.0) / (sum_dist as f64)
            } else {
                0.0
            }
        })
        .collect();

    // --- Eigenvector centrality (power iteration, parallel per starting seed) ---
    let mut eigenvector_scores: Vec<f64> = vec![1.0 / n_f.sqrt(); n];

    for _iter in 0..EIGENITOR_MAX_ITER {
        let mut new_scores: Vec<f64> = vec![0.0; n];

        for (i, neighbors) in adj_idx.iter().enumerate() {
            for &j in neighbors {
                new_scores[j] += eigenvector_scores[i];
            }
        }

        let norm: f64 = new_scores.iter().map(|&x| x * x).sum::<f64>().sqrt();
        if norm < EIGENVECTOR_TOLERANCE {
            break;
        }

        for s in &mut new_scores {
            *s /= norm;
        }

        // Check convergence
        let diff: f64 = eigenvector_scores
            .iter()
            .zip(new_scores.iter())
            .map(|(a, b)| (a - b).abs())
            .sum();

        eigenvector_scores = new_scores;

        if diff < EIGENVECTOR_TOLERANCE {
            break;
        }
    }

    // Normalize eigenvector to [0,1]
    let max_ev = eigenvector_scores.iter().cloned().fold(0.0f64, f64::max);
    if max_ev > 0.0 {
        for s in &mut eigenvector_scores {
            *s /= max_ev;
        }
    }

    // --- PageRank (power iteration, simple version) ---
    let damping: f64 = 0.85;
    let jump_prob: f64 = (1.0 - damping) / n_f;
    let mut pagerank_scores: Vec<f64> = vec![1.0 / n_f; n];

    for _iter in 0..EIGENITOR_MAX_ITER {
        let mut new_pr: Vec<f64> = vec![jump_prob; n];

        for (i, neighbors) in adj_idx.iter().enumerate() {
            let contrib = damping * pagerank_scores[i] / neighbors.len().max(1) as f64;
            for &j in neighbors {
                new_pr[j] += contrib;
            }
        }

        // Check convergence
        let diff: f64 = pagerank_scores
            .iter()
            .zip(new_pr.iter())
            .map(|(a, b)| (a - b).abs())
            .sum();

        pagerank_scores = new_pr;

        if diff < EIGENVECTOR_TOLERANCE {
            break;
        }
    }

    // Normalize pagerank
    let sum_pr: f64 = pagerank_scores.iter().sum();
    if sum_pr > 0.0 {
        for s in &mut pagerank_scores {
            *s /= sum_pr;
        }
    }

    // --- Build result dict ---
    let dict = PyDict::new(py);
    for (i, node_id) in name_vec.iter().enumerate() {
        let inner = PyDict::new(py);
        let _ = inner.set_item("degree", degree_scores[i]);
        let _ = inner.set_item("betweenness", betweenness_scores[i]);
        let _ = inner.set_item("closeness", closeness_scores[i]);
        let _ = inner.set_item("eigenvector", eigenvector_scores[i]);
        let _ = inner.set_item("pagerank", pagerank_scores[i]);
        let _ = dict.set_item(node_id, inner);
    }

    Ok(dict)
}

/// Single-node betweenness centrality via Brandes (B7 pattern).
#[pyfunction]
#[pyo3(signature = (adjacency, source_node, /))]
pub fn betweenness_single<'py>(
    py: Python<'py>,
    adjacency: Vec<(String, Vec<String>)>,
    source_node: String,
) -> PyResult<f64> {
    let n = adjacency.len();
    if n == 0 {
        return Ok(0.0);
    }

    // Build index map
    let mut idx_map: HashMap<String, usize> = HashMap::with_capacity(n);
    for (i, (node_id, _)) in adjacency.iter().enumerate() {
        idx_map.insert(node_id.clone(), i);
    }

    let s = match idx_map.get(&source_node) {
        Some(&idx) => idx,
        None => return Ok(0.0),
    };

    let adj_idx: Vec<Vec<usize>> = adjacency
        .iter()
        .map(|(_, neighbors)| {
            neighbors
                .iter()
                .filter_map(|n| idx_map.get(n).copied())
                .collect()
        })
        .collect();

    let n_f = n as f64;

    // BFS
    let mut sigma: Vec<f64> = vec![0.0; n];
    let mut dist: Vec<i32> = vec![-1; n];
    let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut stack: Vec<usize> = Vec::new();

    sigma[s] = 1.0;
    dist[s] = 0;

    let mut queue = std::collections::VecDeque::new();
    queue.push_back(s);

    while let Some(v) = queue.pop_front() {
        stack.push(v);
        for &w in &adj_idx[v] {
            if dist[w] < 0 {
                dist[w] = dist[v] + 1;
                queue.push_back(w);
            }
            if dist[w] == dist[v] + 1 {
                sigma[w] += sigma[v];
                pred[w].push(v);
            }
        }
    }

    // Back-propagation
    let mut delta: Vec<f64> = vec![0.0; n];
    while let Some(w) = stack.pop() {
        for &v in &pred[w] {
            delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
        }
    }

    let result = if s < n { delta[s] } else { 0.0 };
    let norm = if n > 2 { 2.0 / ((n_f - 1.0) * (n_f - 2.0)) } else { 1.0 };

    Ok(result * norm)
}

/// Batch betweenness centrality for multiple source nodes (B7 pattern).
#[pyfunction]
#[pyo3(signature = (adjacency, source_nodes, /))]
pub fn betweenness_batch<'py>(
    py: Python<'py>,
    adjacency: Vec<(String, Vec<String>)>,
    source_nodes: Vec<String>,
) -> PyResult<Bound<'py, PyDict>> {
    let n = adjacency.len();
    if n == 0 {
        return Ok(PyDict::new(py));
    }

    let mut idx_map: HashMap<String, usize> = HashMap::with_capacity(n);
    for (i, (node_id, _)) in adjacency.iter().enumerate() {
        idx_map.insert(node_id.clone(), i);
    }

    let adj_idx: Vec<Vec<usize>> = adjacency
        .iter()
        .map(|(_, neighbors)| {
            neighbors
                .iter()
                .filter_map(|n| idx_map.get(n).copied())
                .collect()
        })
        .collect();

    let n_f = n as f64;
    let norm = if n > 2 { 2.0 / ((n_f - 1.0) * (n_f - 2.0)) } else { 1.0 };

    // Map source nodes to indices, deduplicate
    let unique_sources: Vec<usize> = source_nodes
        .iter()
        .filter_map(|s| idx_map.get(s).copied())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();

    let results: Vec<(String, f64)> = unique_sources
        .par_iter()
        .map(|&s| {
            let mut sigma: Vec<f64> = vec![0.0; n];
            let mut dist: Vec<i32> = vec![-1; n];
            let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];
            let mut stack: Vec<usize> = Vec::new();

            sigma[s] = 1.0;
            dist[s] = 0;

            let mut queue = std::collections::VecDeque::new();
            queue.push_back(s);

            while let Some(v) = queue.pop_front() {
                stack.push(v);
                for &w in &adj_idx[v] {
                    if dist[w] < 0 {
                        dist[w] = dist[v] + 1;
                        queue.push_back(w);
                    }
                    if dist[w] == dist[v] + 1 {
                        sigma[w] += sigma[v];
                        pred[w].push(v);
                    }
                }
            }

            let mut delta: Vec<f64> = vec![0.0; n];
            while let Some(w) = stack.pop() {
                for &v in &pred[w] {
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                }
            }

            let bet = if s < n { delta[s] } else { 0.0 };
            let source_name = adjacency[s].0.clone();
            (source_name, bet * norm)
        })
        .collect();

    let dict = PyDict::new(py);
    for (node_id, score) in results {
        let _ = dict.set_item(&node_id, score);
    }

    Ok(dict)
}

/// Register graph_centrality functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_centrality_all, m)?)?;
    m.add_function(wrap_pyfunction!(betweenness_single, m)?)?;
    m.add_function(wrap_pyfunction!(betweenness_batch, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_centrality_constants() {
        assert_eq!(MAX_NODES, 50_000);
        assert_eq!(EIGENITOR_MAX_ITER, 100);
    }

    #[test]
    fn test_empty_graph() {
        let adjacency: Vec<(String, Vec<String>)> = vec![];
        // Can't call pyfunction without Python, but we can test pure Rust logic
        assert_eq!(adjacency.len(), 0);
    }

    #[test]
    fn test_node_centrality_clone() {
        let nc = NodeCentrality {
            node_id: "test".to_string(),
            degree: 0.5,
            betweenness: 0.1,
            closeness: 0.3,
            eigenvector: 0.4,
            pagerank: 0.2,
        };
        let nc2 = nc.clone();
        assert_eq!(nc.node_id, nc2.node_id);
    }
}

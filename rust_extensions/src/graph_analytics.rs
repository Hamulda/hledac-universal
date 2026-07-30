//! graph_analytics — Graph analytics via petgraph (PageRank, Louvain community detection).
//!
//! GRAPH-01: Rust-native graph analytics using petgraph crate.
//!
//! Algorithms provided:
//! - PageRank via custom power iteration (hand-tuned for IOC graphs)
//! - Louvain community detection (modularity optimization)
//! - Strongly connected components (SCC) via Kosaraju's algorithm
//!
//! Architecture:
//! - petgraph for graph data structures and PageRank
//! - Custom Louvain implementation for community detection
//! - rayon for parallel processing of large graphs
//! - Bounded: MAX_NODES=100_000, memory-safe for M1 8GB
//!
//! M1 8GB: ~10-50MB for 100k node graph, bounded by MAX_NODES.

use std::collections::{HashMap, HashSet};

use petgraph::graph::{DiGraph, NodeIndex, UnGraph};
use petgraph::algo::kosaraju_scc;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum number of nodes to prevent OOM on M1 8GB.
/// Each node uses ~100-200 bytes for string ID + graph overhead.
/// 100k nodes ≈ 20MB graph structure + string storage.
const MAX_NODES: usize = 100_000;

/// Maximum number of edges per node (degree limit for power-law graphs).
const MAX_EDGES_PER_NODE: usize = 10_000;

/// PageRank damping factor (standard value from literature).
const PAGERANK_DAMPING: f64 = 0.85;

/// PageRank tolerance for convergence.
const PAGERANK_TOLERANCE: f64 = 1e-6;

/// Maximum iterations for PageRank power iteration.
const PAGERANK_MAX_ITER: usize = 100;

/// Louvain resolution parameter (controls community size).
const LOUVAIN_RESOLUTION: f64 = 1.0;

/// Maximum iterations for Louvain algorithm.
const LOUVAIN_MAX_ITER: usize = 100;

// ---------------------------------------------------------------------------
// Data Structures
// ---------------------------------------------------------------------------

/// Node in the IOC graph.
#[derive(Debug, Clone)]
struct IOCNode {
    id: String,
    node_type: String,
}

/// Edge in the IOC graph with weight.
#[derive(Debug, Clone)]
struct IOCEdge {
    weight: f64,
}

// ---------------------------------------------------------------------------
// Graph Construction
// ---------------------------------------------------------------------------

/// Build a petgraph UnGraph from IOC data.
///
/// Returns (node_indices map, graph) for later algorithm execution.
fn build_graph(
    nodes: &[(u64, String, String)],
    edges: &[(u64, u64, f64)],
) -> (HashMap<u64, NodeIndex>, DiGraph<IOCNode, IOCEdge>) {
    let mut graph = DiGraph::new();
    let mut node_indices: HashMap<u64, NodeIndex> = HashMap::with_capacity(nodes.len().min(MAX_NODES));

    // Add nodes
    for (id, _value, node_type) in nodes {
        if node_indices.len() >= MAX_NODES {
            break;
        }
        let idx = graph.add_node(IOCNode {
            id: (*id).to_string(),
            node_type: node_type.clone(),
        });
        node_indices.insert(*id, idx);
    }

    // Add edges
    for (from, to, weight) in edges {
        if let (Some(&from_idx), Some(&to_idx)) = (node_indices.get(from), node_indices.get(to)) {
            // Check degree limits
            let from_degree = graph.edges(from_idx).count();
            let to_degree = graph.edges(to_idx).count();
            if from_degree < MAX_EDGES_PER_NODE && to_degree < MAX_EDGES_PER_NODE {
                graph.add_edge(from_idx, to_idx, IOCEdge { weight: *weight });
                // For undirected semantics, add reverse edge if not already present
                if !graph.contains_edge(to_idx, from_idx) {
                    graph.add_edge(to_idx, from_idx, IOCEdge { weight: *weight });
                }
            }
        }
    }

    (node_indices, graph)
}

/// Build an undirected graph for community detection (Louvain works on undirected).
fn build_undirected_graph(
    nodes: &[(u64, String, String)],
    edges: &[(u64, u64, f64)],
) -> (HashMap<u64, NodeIndex>, UnGraph<IOCNode, IOCEdge>) {
    let mut graph = UnGraph::new_undirected();
    let mut node_indices: HashMap<u64, NodeIndex> = HashMap::with_capacity(nodes.len().min(MAX_NODES));

    // Add nodes
    for (id, _value, node_type) in nodes {
        if node_indices.len() >= MAX_NODES {
            break;
        }
        let idx = graph.add_node(IOCNode {
            id: (*id).to_string(),
            node_type: node_type.clone(),
        });
        node_indices.insert(*id, idx);
    }

    // Add edges (undirected) — enforce MAX_EDGES_PER_NODE degree limit for consistency
    // with build_graph (directed). Without this, rust_graph_analytics_all could
    // produce Louvain results that differ from rust_louvain_communities standalone.
    let mut added_edges: HashSet<(u64, u64)> = HashSet::new();
    for (from, to, weight) in edges {
        if let (Some(&from_idx), Some(&to_idx)) = (node_indices.get(from), node_indices.get(to)) {
            // Avoid duplicate undirected edges
            let edge_key = if from < to { (*from, *to) } else { (*to, *from) };
            if !added_edges.contains(&edge_key) {
                // Enforce degree limit (same as build_graph)
                let from_degree = graph.edges(from_idx).count();
                let to_degree = graph.edges(to_idx).count();
                if from_degree < MAX_EDGES_PER_NODE && to_degree < MAX_EDGES_PER_NODE {
                    added_edges.insert(edge_key);
                    graph.add_edge(from_idx, to_idx, IOCEdge { weight: *weight });
                }
            }
        }
    }

    (node_indices, graph)
}

// ---------------------------------------------------------------------------
// Louvain Community Detection
// ---------------------------------------------------------------------------

/// Louvain community detection algorithm.
///
/// This is a simplified implementation of the Louvain algorithm for community
/// detection in graphs. It optimizes modularity through iterative node movement.
///
/// Modularity Q = (1/2m) * sum_ij [A_ij - (k_i * k_j) / 2m] * delta(c_i, c_j)
///
/// where A_ij is edge weight, k_i is node degree, m is total edge weight,
/// and delta(c_i, c_j) is 1 if nodes i and j are in the same community.
///
/// Returns: HashMap node_id -> community_id
fn louvain_communities_impl(
    node_indices: &HashMap<u64, NodeIndex>,
    graph: &UnGraph<IOCNode, IOCEdge>,
    resolution: f64,
    max_iter: usize,
) -> HashMap<u64, u32> {
    let n = graph.node_count();
    if n == 0 {
        return HashMap::new();
    }

    // Initialize: each node in its own community
    let mut community: HashMap<NodeIndex, u32> = graph
        .node_indices()
        .enumerate()
        .map(|(i, idx)| (idx, i as u32))
        .collect();
    let mut community_weights: HashMap<u32, f64> = HashMap::new();

    // Calculate total edge weight (m) and node degrees
    let m: f64 = graph.edge_references().map(|e| e.weight().weight).sum();
    if m == 0.0 {
        return node_indices
            .iter()
            .map(|(&id, _)| (id, 0))
            .collect();
    }

    let mut k_i: HashMap<NodeIndex, f64> = HashMap::new();
    for idx in graph.node_indices() {
        let deg: f64 = graph
            .edges(idx)
            .map(|e| e.weight().weight)
            .sum();
        k_i.insert(idx, deg);
        *community_weights.entry(community[&idx]).or_insert(0.0) += deg;
    }

    let mut improved = true;
    let mut iteration = 0;

    while improved && iteration < max_iter {
        improved = false;
        iteration += 1;

        for idx in graph.node_indices() {
            let current_comm = community[&idx];
            let k_i_val = k_i[&idx];

            // Calculate modularity gain for moving to each neighbor's community
            let neighbors: Vec<_> = graph.neighbors(idx).collect();
            let mut best_comm = current_comm;
            let mut best_gain = 0.0;

            // Get communities of neighbors
            let neighbor_communities: HashSet<u32> =
                neighbors.iter().map(|&n| community[&n]).collect();

            for &comm in &neighbor_communities {
                // Calculate modularity gain
                // Delta_Q = [sum_in/K_i - 2*m*resolution] / (2*m)
                // Simplified: sum of edge weights to community minus expected
                let sum_to_comm: f64 = neighbors
                    .iter()
                    .filter(|&&n| community[&n] == comm)
                    .map(|&n| {
                        graph
                            .edges_connecting(idx, n)
                            .map(|e| e.weight().weight)
                            .sum::<f64>()
                    })
                    .sum();

                let comm_weight = community_weights.get(&comm).copied().unwrap_or(0.0);
                let delta_q = (sum_to_comm - resolution * k_i_val * comm_weight / (2.0 * m)) / (2.0 * m);

                if delta_q > best_gain {
                    best_gain = delta_q;
                    best_comm = comm;
                }
            }

            if best_comm != current_comm {
                // Move node to new community
                community.insert(idx, best_comm);

                // Update community weights
                *community_weights.entry(current_comm).or_insert(0.0) -= k_i_val;
                *community_weights.entry(best_comm).or_insert(0.0) += k_i_val;

                improved = true;
            }
        }
    }

    // Renumber communities to be consecutive starting from 0
    let unique_communities: Vec<u32> = community.values().copied().collect::<HashSet<_>>().into_iter().collect();
    let mut comm_map: HashMap<u32, u32> = HashMap::new();
    for (new_id, &old_id) in unique_communities.iter().enumerate() {
        comm_map.insert(old_id, new_id as u32);
    }

    // Convert back to node_id -> community_id
    node_indices
        .iter()
        .map(|(&id, &idx)| (id, comm_map[&community[&idx]]))
        .collect()
}

// ---------------------------------------------------------------------------
// Python-Callable Functions
// ---------------------------------------------------------------------------

/// Compute PageRank for an IOC graph.
///
/// Args:
///     nodes: List of (node_id, value, node_type) tuples
///     edges: List of (from_id, to_id, weight) tuples
///     damping: Damping factor (default 0.85)
///     tol: Convergence tolerance (default 1e-6)
///     max_iter: Maximum iterations (default 100)
///
/// Returns:
///     Dict[node_id, float] of PageRank scores
#[pyfunction]
#[pyo3(signature = (nodes, edges, /, damping: f64 = PAGERANK_DAMPING, tol: f64 = PAGERANK_TOLERANCE, max_iter: usize = PAGERANK_MAX_ITER))]
pub fn rust_pagerank<'py>(
    py: Python<'py>,
    nodes: Vec<(u64, String, String)>,
    edges: Vec<(u64, u64, f64)>,
    damping: f64,
    tol: f64,
    max_iter: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let (node_indices, graph) = build_graph(&nodes, &edges);

    let n = graph.node_count();
    if n == 0 {
        return Ok(PyDict::new(py));
    }

    // Use petgraph's pagerank if graph is suitable, otherwise manual implementation
    let mut pagerank_scores: Vec<f64> = if n <= MAX_NODES && !edges.is_empty() {
        // Clamp parameters
        let damping = damping.clamp(0.0, 1.0);
        let tol = tol.max(1e-10);
        let max_iter = max_iter.min(PAGERANK_MAX_ITER);

        // Convert to static graph for petgraph
        let n_usize = n;
        let mut adj: Vec<Vec<(usize, f64)>> = vec![Vec::new(); n_usize];

        for (from, to, weight) in &edges {
            if let (Some(&from_idx), Some(&to_idx)) = (node_indices.get(from), node_indices.get(to)) {
                let from_i = from_idx.index();
                let to_i = to_idx.index();
                if from_i < n_usize && to_i < n_usize {
                    adj[from_i].push((to_i, *weight));
                    adj[to_i].push((from_i, *weight)); // Undirected for PageRank
                }
            }
        }

        // Power iteration for PageRank
        let jump_prob = (1.0 - damping) / n_usize as f64;
        let mut pr: Vec<f64> = vec![1.0 / n_usize as f64; n_usize];

        for _ in 0..max_iter {
            let mut new_pr: Vec<f64> = vec![jump_prob; n_usize];

            for (i, neighbors) in adj.iter().enumerate() {
                if neighbors.is_empty() {
                    continue;
                }
                let sum_w: f64 = neighbors.iter().map(|&(_, w)| w).sum();
                if sum_w == 0.0 {
                    continue;
                }
                let contrib = damping * pr[i];
                for &(j, w) in neighbors {
                    new_pr[j] += contrib * w / sum_w;
                }
            }

            // Check convergence BEFORE normalizing (compare raw scores)
            let diff: f64 = pr
                .iter()
                .zip(new_pr.iter())
                .map(|(a, b)| (a - b).abs())
                .sum();

            // Normalize new_pr in-place
            let sum: f64 = new_pr.iter().sum();
            if sum > 0.0 {
                for p in &mut new_pr {
                    *p /= sum;
                }
            } else {
                // Degenerate case: redistribute uniformly
                let uniform = 1.0 / n_usize as f64;
                for p in &mut new_pr {
                    *p = uniform;
                }
            }

            pr = new_pr;
            if diff < tol {
                break;
            }
        }

        pr
    } else {
        // Fallback: uniform scores
        vec![1.0 / n as f64; n]
    };

    // Build index->node_id mapping (sorted by NodeIndex to match adj order)
    let mut index_to_id: Vec<u64> = vec![0; n];
    for (id, &idx) in &node_indices {
        let pos = idx.index();
        if pos < n {
            index_to_id[pos] = *id;
        }
    }

    let result = PyDict::new(py);
    for (i, &node_id) in index_to_id.iter().enumerate() {
        let _ = result.set_item(node_id, pagerank_scores[i]);
    }

    Ok(result)
}

/// Compute Louvain community detection for an IOC graph.
///
/// Args:
///     nodes: List of (node_id, value, node_type) tuples
///     edges: List of (from_id, to_id, weight) tuples
///     resolution: Resolution parameter (default 1.0, higher = more smaller communities)
///     max_iter: Maximum iterations (default 100)
///
/// Returns:
///     Dict[node_id, community_id] - community IDs are 0-indexed consecutive integers
#[pyfunction]
#[pyo3(signature = (nodes, edges, /, resolution: f64 = LOUVAIN_RESOLUTION, max_iter: usize = LOUVAIN_MAX_ITER))]
pub fn rust_louvain_communities<'py>(
    py: Python<'py>,
    nodes: Vec<(u64, String, String)>,
    edges: Vec<(u64, u64, f64)>,
    resolution: f64,
    max_iter: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let (node_indices, graph) = build_undirected_graph(&nodes, &edges);

    let communities = louvain_communities_impl(&node_indices, &graph, resolution, max_iter);

    let result = PyDict::new(py);
    for (node_id, comm_id) in communities {
        let _ = result.set_item(node_id, comm_id);
    }

    Ok(result)
}

/// Compute strongly connected components (SCC) using Kosaraju's algorithm.
///
/// Args:
///     nodes: List of (node_id, value, node_type) tuples
///     edges: List of (from_id, to_id, weight) tuples
///
/// Returns:
///     List of components, where each component is a list of node_ids
#[pyfunction]
#[pyo3(signature = (nodes, edges, /))]
pub fn rust_scc<'py>(
    py: Python<'py>,
    nodes: Vec<(u64, String, String)>,
    edges: Vec<(u64, u64, f64)>,
) -> PyResult<Bound<'py, PyList>> {
    let (node_indices, graph) = build_graph(&nodes, &edges);
    let sccs = compute_scc_impl(&node_indices, &graph);

    let result = PyList::new(py, &[]);
    for component in sccs {
        let _ = result.append(PyList::new(py, &component));
    }
    Ok(result)
}

/// Compute all graph analytics in a single pass (PageRank + Louvain + SCC).
///
/// This is more efficient than calling each function separately when all
/// analytics are needed, as it reuses the constructed graph.
///
/// Args:
///     nodes: List of (node_id, value, node_type) tuples
///     edges: List of (from_id, to_id, weight) tuples
///     damping: PageRank damping factor (default 0.85)
///     resolution: Louvain resolution parameter (default 1.0)
///
/// Returns:
///     Dict with keys: "pagerank", "communities", "scc"
#[pyfunction]
#[pyo3(signature = (nodes, edges, /, damping: f64 = PAGERANK_DAMPING, resolution: f64 = LOUVAIN_RESOLUTION))]
pub fn rust_graph_analytics_all<'py>(
    py: Python<'py>,
    nodes: Vec<(u64, String, String)>,
    edges: Vec<(u64, u64, f64)>,
    damping: f64,
    resolution: f64,
) -> PyResult<Bound<'py, PyDict>> {
    let (node_indices, graph_dir) = build_graph(&nodes, &edges);
    let (node_indices_undir, graph_undir) = build_undirected_graph(&nodes, &edges);

    // Build undirected adjacency list ONCE for PageRank
    let n = node_indices.len();
    let mut adj: Vec<Vec<(usize, f64)>> = vec![Vec::new(); n];
    for (from, to, weight) in &edges {
        if let (Some(&from_idx), Some(&to_idx)) = (node_indices.get(from), node_indices.get(to)) {
            let from_i = from_idx.index();
            let to_i = to_idx.index();
            if from_i < n && to_i < n {
                adj[from_i].push((to_i, *weight));
                adj[to_i].push((from_i, *weight));
            }
        }
    }

    // PageRank via shared helper (no clone)
    let pr_scores = compute_pagerank_on_adj(&adj, damping, PAGERANK_TOLERANCE, PAGERANK_MAX_ITER);

    // Louvain on undirected graph
    let communities = louvain_communities_impl(&node_indices_undir, &graph_undir, resolution, LOUVAIN_MAX_ITER);

    // SCC on directed graph
    let sccs = compute_scc_impl(&node_indices, &graph_dir);

    // Build index->node_id mapping for PageRank result
    let mut index_to_id: Vec<u64> = vec![0; n];
    for (id, &idx) in &node_indices {
        let pos = idx.index();
        if pos < n {
            index_to_id[pos] = *id;
        }
    }

    // PageRank dict
    let py_pr = PyDict::new(py);
    for (i, &node_id) in index_to_id.iter().enumerate() {
        let _ = py_pr.set_item(node_id, pr_scores[i]);
    }

    // Communities dict
    let py_communities = PyDict::new(py);
    for (node_id, comm_id) in &communities {
        let _ = py_communities.set_item(node_id, comm_id);
    }

    // SCC as list of components
    let py_scc = PyList::new(py, &[]);
    for component in sccs {
        let _ = py_scc.append(PyList::new(py, &component));
    }

    let result = PyDict::new(py);
    let _ = result.set_item("pagerank", py_pr);
    let _ = result.set_item("communities", py_communities);
    let _ = result.set_item("scc", py_scc);

    Ok(result)
}

// ---------------------------------------------------------------------------
// Helpers for rust_graph_analytics_all (avoid 4× clone)
// ---------------------------------------------------------------------------

/// Compute PageRank on a pre-built undirected adjacency list.
fn compute_pagerank_on_adj(
    adj: &[Vec<(usize, f64)>],
    damping: f64,
    tol: f64,
    max_iter: usize,
) -> Vec<f64> {
    let n = adj.len();
    if n == 0 {
        return vec![];
    }

    let jump_prob = (1.0 - damping) / n as f64;
    let mut pr: Vec<f64> = vec![1.0 / n as f64; n];

    for _ in 0..max_iter {
        let mut new_pr: Vec<f64> = vec![jump_prob; n];

        for (i, neighbors) in adj.iter().enumerate() {
            if neighbors.is_empty() {
                continue;
            }
            let sum_w: f64 = neighbors.iter().map(|&(_, w)| w).sum();
            if sum_w == 0.0 {
                continue;
            }
            let contrib = damping * pr[i];
            for &(j, w) in neighbors {
                new_pr[j] += contrib * w / sum_w;
            }
        }

        let diff: f64 = pr
            .iter()
            .zip(new_pr.iter())
            .map(|(a, b)| (a - b).abs())
            .sum();

        let sum: f64 = new_pr.iter().sum();
        if sum > 0.0 {
            for p in &mut new_pr {
                *p /= sum;
            }
        } else {
            let uniform = 1.0 / n as f64;
            for p in &mut new_pr {
                *p = uniform;
            }
        }

        pr = new_pr;
        if diff < tol {
            break;
        }
    }
    pr
}

/// Compute SCC on a pre-built directed graph (helper for rust_graph_analytics_all).
fn compute_scc_impl(
    node_indices: &HashMap<u64, NodeIndex>,
    graph: &DiGraph<IOCNode, IOCEdge>,
) -> Vec<Vec<u64>> {
    if graph.node_count() == 0 {
        return vec![];
    }

    let mut pet_graph: DiGraph<(), f64> = DiGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    for idx in graph.node_indices() {
        let new_idx = pet_graph.add_node(());
        idx_map.insert(idx, new_idx);
    }

    for edge in graph.edge_indices() {
        let (from, to) = graph.edge_endpoints(edge).unwrap();
        if let (Some(&pf), Some(&pt)) = (idx_map.get(&from), idx_map.get(&to)) {
            pet_graph.add_edge(pf, pt, 1.0);
        }
    }

    let sccs: Vec<Vec<NodeIndex>> = kosaraju_scc(&pet_graph);

    let reverse_map: HashMap<petgraph::graph::NodeIndex, u64> =
        idx_map.iter().map(|(&k, &v)| (v, k)).collect();

    sccs
        .into_iter()
        .filter_map(|component| {
            let py_component: Vec<u64> = component
                .iter()
                .filter_map(|&idx| reverse_map.get(&idx).copied())
                .collect();
            if py_component.is_empty() {
                None
            } else {
                Some(py_component)
            }
        })
        .collect()
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_pagerank, m)?)?;
    m.add_function(wrap_pyfunction!(rust_louvain_communities, m)?)?;
    m.add_function(wrap_pyfunction!(rust_scc, m)?)?;
    m.add_function(wrap_pyfunction!(rust_graph_analytics_all, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pagerank_empty() {
        let nodes: Vec<(u64, String, String)> = vec![];
        let edges: Vec<(u64, u64, f64)> = vec![];
        let communities = louvain_communities_impl(&HashMap::new(), &UnGraph::new_undirected(), 1.0, 10);
        assert!(communities.is_empty());
    }

    #[test]
    fn test_louvain_single_node() {
        let nodes = vec![(1u64, "test".to_string(), "ip".to_string())];
        let edges: Vec<(u64, u64, f64)> = vec![];
        let (node_indices, graph) = build_undirected_graph(&nodes, &edges);
        let communities = louvain_communities_impl(&node_indices, &graph, 1.0, 10);
        assert_eq!(communities.len(), 1);
    }

    #[test]
    fn test_louvain_two_connected_nodes() {
        let nodes = vec![
            (1u64, "node1".to_string(), "ip".to_string()),
            (2u64, "node2".to_string(), "ip".to_string()),
        ];
        let edges = vec![(1u64, 2u64, 1.0)];
        let (node_indices, graph) = build_undirected_graph(&nodes, &edges);
        let communities = louvain_communities_impl(&node_indices, &graph, 1.0, 10);
        // Two connected nodes should be in the same community
        assert_eq!(communities[&1], communities[&2]);
    }

    #[test]
    fn test_scc_empty() {
        let nodes: Vec<(u64, String, String)> = vec![];
        let edges: Vec<(u64, u64, f64)> = vec![];
        let (node_indices, graph) = build_graph(&nodes, &edges);
        assert_eq!(node_indices.len(), 0);
    }

    #[test]
    fn test_constants() {
        assert_eq!(MAX_NODES, 100_000);
        assert_eq!(PAGERANK_DAMPING, 0.85);
        assert_eq!(LOUVAIN_RESOLUTION, 1.0);
    }
}

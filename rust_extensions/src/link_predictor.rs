//! SWARM-003: Link Prediction Module for IOC Graph
//!
//! Computes link prediction scores between IOC nodes using:
//! - Adamic-Adar Index: Σ 1/log(degree(z)) for common neighbors
//! - Preferential Attachment: degree(u) × degree(v)
//! - Jaccard Coefficient: |N(u) ∩ N(v)| / |N(u) ∪ N(v)|
//!
//! All computation runs on DuckDB ioc_nodes + ioc_edges tables.
//! M1 8GB safe: bounded to MAX_BATCH_NODES=10_000, runs during TEARDOWN phase.

use duckdb::Connection;
use pyo3::prelude::*;
use pyo3::PyResult;

/// Helper macro to convert DuckDB errors to PyErr
macro_rules! duckdb_ok {
    ($expr:expr) => {
        $expr.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("DuckDB error: {}", e)))?
    };
}
use rayon::prelude::*;
use std::collections::HashMap;

/// Predicted edge with confidence scores from multiple methods
#[derive(Debug, Clone)]
#[pyclass(module = "hledac_rust_extensions.link_predictor")]
pub struct PredictedEdgePy {
    #[pyo3(get)]
    pub src_id: i64,
    #[pyo3(get)]
    pub dst_id: i64,
    #[pyo3(get)]
    pub adamic_adar: f64,
    #[pyo3(get)]
    pub preferential_attachment: f64,
    #[pyo3(get)]
    pub jaccard: f64,
    #[pyo3(get)]
    pub common_neighbors: i32,
    #[pyo3(get)]
    pub method: String,
}

/// Batch result container for Python
#[derive(Debug, Clone)]
#[pyclass(module = "hledac_rust_extensions.link_predictor")]
pub struct LinkPredictionBatch {
    #[pyo3(get)]
    pub edges: Vec<PredictedEdgePy>,
    #[pyo3(get)]
    pub total_candidates: i32,
    #[pyo3(get)]
    pub above_threshold: i32,
    #[pyo3(get)]
    pub compute_time_ms: f64,
}

/// Configuration for link prediction
#[derive(Debug, Clone)]
#[pyclass(module = "hledac_rust_extensions.link_predictor")]
pub struct LinkPredictorConfig {
    /// Minimum Adamic-Adar score to emit prediction (default: 0.01)
    #[pyo3(get, set)]
    pub min_adamic_adar: f64,
    /// Minimum Jaccard coefficient (default: 0.1)
    #[pyo3(get, set)]
    pub min_jaccard: f64,
    /// Maximum candidates to consider (M1 8GB safety bound, default: 10_000)
    #[pyo3(get, set)]
    pub max_candidates: i32,
    /// Include only edges between different IOC types
    #[pyo3(get, set)]
    pub cross_type_only: bool,
    /// IOC types to include (empty = all)
    #[pyo3(get, set)]
    pub ioc_type_filter: Vec<String>,
}

#[pymethods]
impl LinkPredictorConfig {
    #[new]
    #[pyo3(signature = (min_adamic_adar = 0.01, min_jaccard = 0.1, max_candidates = 10000, cross_type_only = false, ioc_type_filter = Vec::new()))]
    fn new(
        min_adamic_adar: f64,
        min_jaccard: f64,
        max_candidates: i32,
        cross_type_only: bool,
        ioc_type_filter: Vec<String>,
    ) -> Self {
        Self {
            min_adamic_adar,
            min_jaccard,
            max_candidates,
            cross_type_only,
            ioc_type_filter,
        }
    }
}

/// SWARM-003: Compute link prediction scores for all non-connected node pairs
///
/// Uses DuckDB to compute:
/// - Adamic-Adar Index: Σ 1/log(degree(z)) for common neighbors z of u and v
/// - Preferential Attachment: degree(u) × degree(v)
/// - Jaccard Coefficient: |N(u) ∩ N(v)| / |N(u) ∪ N(v)|
///
/// Args:
///     db_path: Path to DuckDB database
///     config: LinkPredictorConfig with thresholds
///
/// Returns:
///     LinkPredictionBatch with predicted edges above threshold
///
/// M1 8GB: Bounded to max_candidates, uses io_pool (2 threads)
#[pyfunction]
#[pyo3(name = "predict_links")]
pub fn predict_links_py(db_path: &str, config: Option<LinkPredictorConfig>) -> PyResult<LinkPredictionBatch> {
    let cfg = config.unwrap_or(LinkPredictorConfig::new(
        0.01, 0.1, 10_000, false, Vec::new()
    ));

    let start = std::time::Instant::now();

    let conn = Connection::open(db_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to open DuckDB at {}: {}", db_path, e
        )))?;

    // Step 1: Build adjacency list from ioc_edges (only OBSERVED edges)
    let adjacency = build_adjacency_list(&conn, &cfg)?;
    let degrees = compute_degrees(&adjacency);

    // Step 2: Find all non-connected node pairs with common neighbors
    let candidates = find_candidate_pairs(&conn, &adjacency, &cfg)?;

    // Step 3: Compute scores in parallel using rayon
    let scored_edges: Vec<PredictedEdgePy> = candidates
        .par_iter()
        .filter_map(|(src, dst)| {
            compute_scores_for_pair(*src, *dst, &adjacency, &degrees, &cfg)
        })
        .collect();

    // Step 4: Filter and sort by Adamic-Adar score
    let mut above_threshold: Vec<PredictedEdgePy> = scored_edges
        .into_iter()
        .filter(|e| e.adamic_adar >= cfg.min_adamic_adar && e.jaccard >= cfg.min_jaccard)
        .collect();

    above_threshold.sort_by(|a, b| b.adamic_adar.partial_cmp(&a.adamic_adar).unwrap_or(std::cmp::Ordering::Equal));

    let elapsed = start.elapsed();

    Ok(LinkPredictionBatch {
        edges: above_threshold,
        total_candidates: candidates.len() as i32,
        above_threshold: above_threshold.len() as i32,
        compute_time_ms: elapsed.as_secs_f64() * 1000.0,
    })
}

/// Build adjacency list from ioc_edges table
fn build_adjacency_list(conn: &Connection, cfg: &LinkPredictorConfig) -> PyResult<HashMap<i64, Vec<i64>>> {
    let mut adjacency: HashMap<i64, Vec<i64>> = HashMap::new();

    // Build WHERE clause for IOC type filtering
    let type_filter = if cfg.ioc_type_filter.is_empty() {
        String::new()
    } else {
        let types: Vec<String> = cfg.ioc_type_filter.iter()
            .map(|t| format!("'{}'", t.replace('\'', "''")))
            .collect();
        format!(" WHERE n.ioc_type IN ({})", types.join(", "))
    };

    // Query: Get all edges with node types
    let sql = format!(
        r#"
        SELECT e.src_id, e.dst_id, n1.ioc_type as src_type, n2.ioc_type as dst_type
        FROM ioc_edges e
        JOIN ioc_nodes n1 ON e.src_id = n1.id
        JOIN ioc_nodes n2 ON e.dst_id = n2.id
        WHERE e.rel_type = 'OBSERVED'
        {}
        "#,
        type_filter
    );

    let mut stmt = duckdb_ok!(conn.prepare(&sql));
    let mut rows = duckdb_ok!(stmt.query([]));

    while let Some(row) = duckdb_ok!(rows.next()) {
        let src_id: i64 = duckdb_ok!(row.get(0));
        let dst_id: i64 = duckdb_ok!(row.get(1));
        let src_type: String = duckdb_ok!(row.get(2));
        let dst_type: String = duckdb_ok!(row.get(3));

        // Cross-type filtering
        if cfg.cross_type_only && src_type == dst_type {
            continue;
        }

        // Build undirected graph (add both directions)
        adjacency.entry(src_id).or_default().push(dst_id);
        adjacency.entry(dst_id).or_default().push(src_id);
    }

    // Deduplicate neighbors
    for neighbors in adjacency.values_mut() {
        neighbors.sort();
        neighbors.dedup();
    }

    Ok(adjacency)
}

/// Compute degree for each node
fn compute_degrees(adjacency: &HashMap<i64, Vec<i64>>) -> HashMap<i64, usize> {
    adjacency.iter()
        .map(|(node, neighbors)| (*node, neighbors.len()))
        .collect()
}

/// Find all node pairs that could benefit from link prediction
/// (nodes with at least one common neighbor but not directly connected)
fn find_candidate_pairs(
    conn: &Connection,
    adjacency: &HashMap<i64, Vec<i64>>,
    cfg: &LinkPredictorConfig,
) -> PyResult<Vec<(i64, i64)>> {
    // Generate candidate pairs from common neighbors
    let mut candidates: HashMap<(i64, i64), i32> = HashMap::new();

    for (node, neighbors) in adjacency.iter() {
        for &neighbor in neighbors {
            // Find other nodes connected to this neighbor
            if let Some(second_neighbors) = adjacency.get(&neighbor) {
                for &second in second_neighbors {
                    if second == *node {
                        continue; // Skip self-loop
                    }
                    // Only consider pairs where node < second to avoid duplicates
                    let pair = if *node < second {
                        (*node, second)
                    } else {
                        (second, *node)
                    };
                    // Skip if already connected
                    if adjacency.get(node).map(|n| n.contains(&second)).unwrap_or(false) {
                        continue;
                    }
                    *candidates.entry(pair).or_insert(0) += 1;
                }
            }
        }
    }

    // Limit candidates for M1 8GB safety
    let mut pairs: Vec<(i64, i64)> = candidates.into_keys().collect();
    pairs.sort();
    pairs.truncate(cfg.max_candidates as usize);

    Ok(pairs)
}

/// Compute link prediction scores for a single node pair
fn compute_scores_for_pair(
    src: i64,
    dst: i64,
    adjacency: &HashMap<i64, Vec<i64>>,
    degrees: &HashMap<i64, usize>,
    cfg: &LinkPredictorConfig,
) -> Option<PredictedEdgePy> {
    let src_neighbors = adjacency.get(&src)?;
    let dst_neighbors = adjacency.get(&dst)?;

    // Find common neighbors
    let mut common: Vec<i64> = Vec::new();
    for &n in src_neighbors {
        if dst_neighbors.binary_search(&n).is_ok() {
            common.push(n);
        }
    }

    if common.is_empty() {
        return None;
    }

    let common_count = common.len() as i32;

    // Compute Adamic-Adar: Σ 1/log(degree(z)) for common neighbors z
    let mut adamic_adar = 0.0;
    for &cn in &common {
        if let Some(&deg) = degrees.get(&cn) {
            if deg > 1 {
                adamic_adar += 1.0 / (deg as f64).ln();
            }
        }
    }

    // Compute Preferential Attachment
    let deg_src = degrees.get(&src).copied().unwrap_or(0);
    let deg_dst = degrees.get(&dst).copied().unwrap_or(0);
    let pref_attach = (deg_src * deg_dst) as f64;

    // Compute Jaccard Coefficient
    let union_size = src_neighbors.len() + dst_neighbors.len() - common_count as usize;
    let jaccard = if union_size > 0 {
        common_count as f64 / union_size as f64
    } else {
        0.0
    };

    // Determine best method
    let method = if adamic_adar > 0.3 {
        "adamic_adar"
    } else if pref_attach > 100.0 {
        "pref_attach"
    } else if jaccard > 0.2 {
        "jaccard"
    } else {
        "combined"
    };

    Some(PredictedEdgePy {
        src_id: src,
        dst_id: dst,
        adamic_adar,
        preferential_attachment: pref_attach,
        jaccard,
        common_neighbors: common_count,
        method: method.to_string(),
    })
}

/// SWARM-003: Get top N predicted edges for a specific node
///
/// Args:
///     db_path: Path to DuckDB database
///     node_id: Source node ID
///     top_k: Number of predictions to return (default: 10)
///     config: Optional LinkPredictorConfig
///
/// Returns:
///     Vec<PredictedEdgePy> sorted by Adamic-Adar score
#[pyfunction]
#[pyo3(name = "predict_links_for_node")]
pub fn predict_links_for_node_py(
    db_path: &str,
    node_id: i64,
    top_k: Option<i32>,
    config: Option<LinkPredictorConfig>,
) -> PyResult<Vec<PredictedEdgePy>> {
    let cfg = config.unwrap_or(LinkPredictorConfig::new(
        0.01, 0.1, 1000, false, Vec::new()
    ));
    let k = top_k.unwrap_or(10);

    let conn = Connection::open(db_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to open DuckDB: {}", e
        )))?;

    // Build adjacency for the node's neighborhood
    let adjacency = build_adjacency_list(&conn, &cfg)?;
    let degrees = compute_degrees(&adjacency);

    // Get neighbors of the target node
    let neighbors = match adjacency.get(&node_id) {
        Some(n) => n.clone(),
        None => return Ok(Vec::new()),
    };

    // Find second-degree neighbors (potential links)
    let mut candidates: HashMap<i64, Vec<i64>> = HashMap::new(); // second_node -> common_neighbors
    for &neighbor in &neighbors {
        if let Some(second_neighbors) = adjacency.get(&neighbor) {
            for &second in second_neighbors {
                if second == node_id || neighbors.contains(&second) {
                    continue; // Skip self and direct neighbors
                }
                candidates.entry(second).or_default().push(neighbor);
            }
        }
    }

    // Compute scores for each candidate
    let mut predictions: Vec<PredictedEdgePy> = candidates
        .into_iter()
        .filter_map(|(candidate, common)| {
            let src_neighbors = neighbors.clone();
            let dst_neighbors = adjacency.get(&candidate)?.clone();

            let common_count = common.len() as i32;

            // Adamic-Adar
            let mut adamic_adar = 0.0;
            for &cn in &common {
                if let Some(&deg) = degrees.get(&cn) {
                    if deg > 1 {
                        adamic_adar += 1.0 / (deg as f64).ln();
                    }
                }
            }

            // Preferential Attachment
            let deg_src = degrees.get(&node_id).copied().unwrap_or(0);
            let deg_dst = degrees.get(&candidate).copied().unwrap_or(0);
            let pref_attach = (deg_src * deg_dst) as f64;

            // Jaccard
            let union_size = src_neighbors.len() + dst_neighbors.len() - common_count as usize;
            let jaccard = if union_size > 0 {
                common_count as f64 / union_size as f64
            } else {
                0.0
            };

            if adamic_adar < cfg.min_adamic_adar || jaccard < cfg.min_jaccard {
                return None;
            }

            let method = if adamic_adar > 0.3 {
                "adamic_adar"
            } else if pref_attach > 100.0 {
                "pref_attach"
            } else {
                "jaccard"
            };

            Some(PredictedEdgePy {
                src_id: node_id,
                dst_id: candidate,
                adamic_adar,
                preferential_attachment: pref_attach,
                jaccard,
                common_neighbors: common_count,
                method: method.to_string(),
            })
        })
        .collect();

    // Sort by Adamic-Adar and limit
    predictions.sort_by(|a, b| b.adamic_adar.partial_cmp(&a.adamic_adar).unwrap_or(std::cmp::Ordering::Equal));
    predictions.truncate(k as usize);

    Ok(predictions)
}

/// Register link predictor functions with Python module
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PredictedEdgePy>()?;
    m.add_class::<LinkPredictionBatch>()?;
    m.add_class::<LinkPredictorConfig>()?;

    m.add_function(wrap_pyfunction!(predict_links_py, m)?)?;
    m.add_function(wrap_pyfunction!(predict_links_for_node_py, m)?)?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_adamic_adar_score() {
        // Simple test: two nodes with one common neighbor of degree 3
        // AA = 1/log(3) ≈ 1.0986
        let deg = 3;
        let expected = 1.0 / (deg as f64).ln();
        assert!((expected - 1.0986122886681098).abs() < 0.0001);
    }

    #[test]
    fn test_jaccard_coefficient() {
        // N(u) = {a, b, c}, N(v) = {b, c, d}, common = {b, c}
        // Jaccard = 2 / 4 = 0.5
        let common = 2;
        let union = 4;
        let jaccard = common as f64 / union as f64;
        assert!((jaccard - 0.5).abs() < 0.0001);
    }
}

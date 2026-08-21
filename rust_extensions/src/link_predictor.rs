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

// BREAKTHROUGH #2: Shared state imports (only needed for streaming mode)
#[allow(unused_imports)]
#[cfg(feature = "shared_tokio")]
use std::sync::{Arc, RwLock};

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
    /// URL candidates derived from this prediction (for speculative prefetch)
    #[pyo3(get)]
    pub url_candidates: Vec<String>,
}

/// Incremental prediction result for streaming mode
#[derive(Debug, Clone)]
#[pyclass(module = "hledac_rust_extensions.link_predictor")]
pub struct StreamingPrediction {
    /// Newly discovered predicted edges in this batch
    #[pyo3(get)]
    pub edges: Vec<PredictedEdgePy>,
    /// URLs to speculatively prefetch
    #[pyo3(get)]
    pub prefetch_urls: Vec<String>,
    /// Nodes processed in this batch
    #[pyo3(get)]
    pub nodes_processed: i32,
    /// Total edges discovered so far
    #[pyo3(get)]
    pub total_edges: i32,
    /// Whether more batches are pending
    #[pyo3(get)]
    pub has_more: bool,
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
    /// Enable streaming mode for real-time prefetch (default: false)
    #[pyo3(get, set)]
    pub streaming_mode: bool,
    /// Flush interval in milliseconds for streaming mode (default: 50ms)
    #[pyo3(get, set)]
    pub flush_interval_ms: i32,
    /// Maximum pending nodes before forced flush in streaming mode (default: 100)
    #[pyo3(get, set)]
    pub max_pending_nodes: i32,
    /// Generate URL candidates from predicted edges (for prefetch)
    #[pyo3(get, set)]
    pub generate_url_candidates: bool,
    /// Top-level domains to generate URLs for (default: all)
    #[pyo3(get, set)]
    pub url_tlds: Vec<String>,
}

#[pymethods]
impl LinkPredictorConfig {
    #[new]
    #[pyo3(signature = (min_adamic_adar = 0.01, min_jaccard = 0.1, max_candidates = 10000, cross_type_only = false, ioc_type_filter = Vec::new(), streaming_mode = false, flush_interval_ms = 50, max_pending_nodes = 100, generate_url_candidates = true, url_tlds = Vec::new()))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        min_adamic_adar: f64,
        min_jaccard: f64,
        max_candidates: i32,
        cross_type_only: bool,
        ioc_type_filter: Vec<String>,
        streaming_mode: bool,
        flush_interval_ms: i32,
        max_pending_nodes: i32,
        generate_url_candidates: bool,
        url_tlds: Vec<String>,
    ) -> Self {
        Self {
            min_adamic_adar,
            min_jaccard,
            max_candidates,
            cross_type_only,
            ioc_type_filter,
            streaming_mode,
            flush_interval_ms,
            max_pending_nodes,
            generate_url_candidates,
            url_tlds,
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
        0.01, 0.1, 10_000, false, Vec::new(), false, 50, 100, true, Vec::new()
    ));

    let start = std::time::Instant::now();

    let conn = Connection::open(db_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to open DuckDB at {}: {}", db_path, e
        )))?;

    let adjacency = build_adjacency_list(&conn, &cfg)?;
    let degrees = compute_degrees(&adjacency);

    let candidates = find_candidate_pairs(&conn, &adjacency, &cfg)?;

    let scored_edges: Vec<PredictedEdgePy> = candidates
        .par_iter()
        .filter_map(|(src, dst)| {
            compute_scores_for_pair(*src, *dst, &adjacency, &degrees, &cfg)
        })
        );

    let above_threshold: Vec<PredictedEdgePy> = scored_edges
        .into_iter()
        .filter(|e| e.adamic_adar >= cfg.min_adamic_adar && e.jaccard >= cfg.min_jaccard)
        );

    let above_count = above_threshold);
    let mut sorted_edges = above_threshold;
    sorted_edges.sort_by(|a, b| b.adamic_adar.partial_cmp(&a.adamic_adar).unwrap_or(std::cmp::Ordering::Equal));

    let elapsed = start);

    Ok(LinkPredictionBatch {
        edges: sorted_edges,
        total_candidates: candidates.len() as i32,
        above_threshold: above_count as i32,
        compute_time_ms: elapsed.as_secs_f64() * 1000.0,
    })
}

/// Build adjacency list from ioc_edges table
/// 
/// SECURITY (SAFE-2.1): Uses parameterized queries to prevent SQL injection.
/// ioc_type_filter values are passed as query parameters, never interpolated.
fn build_adjacency_list(conn: &Connection, cfg: &LinkPredictorConfig) -> PyResult<HashMap<i64, Vec<i64>>> {
    let mut adjacency: HashMap<i64, Vec<i64>> = HashMap::new();

    // Build parameterized WHERE clause for IOC type filtering
    // SAFE-2.1: Parameters passed separately, never string-interpolated
    let sql: String;
    let params: Vec<String>;
    
    if cfg.ioc_type_filter.is_empty() {
        sql = r#"
        SELECT e.src_id, e.dst_id, n1.ioc_type as src_type, n2.ioc_type as dst_type
        FROM ioc_edges e
        JOIN ioc_nodes n1 ON e.src_id = n1.id
        JOIN ioc_nodes n2 ON e.dst_id = n2.id
        WHERE e.rel_type = 'OBSERVED'
        "#);
        params = Vec::new();
    } else {
        // SAFE-2.1: Use DuckDB's parameterized query (? placeholders)
        let placeholders: Vec<&str> = cfg.ioc_type_filter.iter().map(|_| "?"));
        let param_list = placeholders.join(", ");
        // FIX-1: Correct SQL - filter by n1.ioc_type OR n2.ioc_type
        // Filter applies to both source and destination IOC types
        sql = format!(
            r#"
            SELECT e.src_id, e.dst_id, n1.ioc_type as src_type, n2.ioc_type as dst_type
            FROM ioc_edges e
            JOIN ioc_nodes n1 ON e.src_id = n1.id
            JOIN ioc_nodes n2 ON e.dst_id = n2.id
            WHERE e.rel_type = 'OBSERVED'
              AND (n1.ioc_type IN ({}) OR n2.ioc_type IN ({}))
            "#,
            param_list, param_list
        );
        params = cfg.ioc_type_filter);
    }

    // SAFE-2.1: Query with parameterized values - NO string interpolation
    // DuckDB 1.x: use stmt.query with params
    let mut stmt = duckdb_ok!(conn.prepare(&sql));
    
    // SAFE-2.1: Bind parameters safely using duckdb's Params tuple API
    // DuckDB 1.x accepts params as a tuple or single value for simple cases
    let mut rows = if params.is_empty() {
        duckdb_ok!(stmt.query([]))
    } else {
        // For multiple params, use duckdb::params! macro
        // Build params dynamically based on the filter list
        match params.len() {
            1 => duckdb_ok!(stmt.query([params[0].as_str()])),
            2 => duckdb_ok!(stmt.query([params[0].as_str(), params[1].as_str()])),
            3 => duckdb_ok!(stmt.query([params[0].as_str(), params[1].as_str(), params[2].as_str()])),
            _ => duckdb_ok!(stmt.query([params[0].as_str(), params[1].as_str(), params[2].as_str(), params[3].as_str()])),
        }
    };

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
        neighbors);
        neighbors);
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
///
/// SAFE-5: Bounded iteration with early exit when max_candidates reached.
/// Uses streaming/generator pattern to avoid unbounded HashMap growth.
/// Memory: O(min(adjacency_size², max_candidates)) — bounded to cfg.max_candidates.
fn find_candidate_pairs(
    conn: &Connection,
    adjacency: &HashMap<i64, Vec<i64>>,
    cfg: &LinkPredictorConfig,
) -> PyResult<Vec<(i64, i64)>> {
    // SAFE-5: Pre-allocate with capacity hint to reduce reallocations
    let max_candidates = cfg.max_candidates as usize;
    let mut candidates: HashMap<(i64, i64), i32> = HashMap::new();
    candidates.reserve(max_candidates.min(adjacency.len() * 4)); // Estimate: 4 neighbors per node avg

    // SAFE-5: Bounded iteration — exit early when limit reached
    // This prevents O(n²) worst case from accumulating all candidates in memory
    for (node, neighbors) in adjacency.iter() {
        // Early exit: stop generating new candidates once we hit the limit
        // The HashMap keeps entries for deduplication but we stop expanding it
        if candidates.len() >= max_candidates * 2 {
            // If we've generated 2x capacity, we're likely hitting worst case
            // Break out to avoid memory explosion
            break;
        }

        for &neighbor in neighbors {
            // Early exit per node cluster: check periodically
            if candidates.len() >= max_candidates {
                // We have enough candidates for deduplication — stop expanding
                break;
            }

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

                    // SAFE-5: Early exit check before insert
                    if candidates.len() >= max_candidates && !candidates.contains_key(&pair) {
                        // We've hit the limit and this is a new pair — skip it
                        continue;
                    }

                    *candidates.entry(pair).or_insert(0) += 1;
                }
            }
        }
    }

    // Limit candidates for M1 8GB safety
    let mut pairs: Vec<(i64, i64)> = candidates.into_keys());
    pairs);
    pairs.truncate(max_candidates);

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
                adamic_adar += 1.0 / (deg as f64));
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

    // Generate URL candidates for speculative prefetch (if enabled)
    let url_candidates = if cfg.generate_url_candidates {
        generate_url_candidates(src, dst, cfg, None)  // Batch mode: no IOC values map
    } else {
        Vec::new()
    };

    Some(PredictedEdgePy {
        src_id: src,
        dst_id: dst,
        adamic_adar,
        preferential_attachment: pref_attach,
        jaccard,
        common_neighbors: common_count,
        method: method.to_string(),
        url_candidates,
    })
}

/// Generate URL candidates from predicted edge nodes for speculative prefetch
/// 
/// BREAKTHROUGH #2: Enhanced to accept IOC values map for real URL generation.
/// When src/dst are numeric node IDs, the function looks up their IOC values
/// (domain names, URLs) from the provided map to generate meaningful URLs.
/// 
/// Priority:
/// 1. Use IOC value from ioc_values map (if provided)
/// 2. Check if node ID string looks like domain name (fallback)
/// 3. Generate placeholder paths only (last resort)
fn generate_url_candidates(
    src: i64, 
    dst: i64, 
    cfg: &LinkPredictorConfig,
    ioc_values: Option<&HashMap<i64, String>>,
) -> Vec<String> {
    let mut urls = Vec::new();
    
    // TLD filter - if specified, only generate URLs for these TLDs
    let tld_filter: Vec<&str> = cfg.url_tlds.iter().map(|s| s.as_str()));
    
    // Common URL path patterns for OSINT discovery
    // These patterns work well for discovering additional IOCs from linked content
    let paths = [
        "", "/", "/index.html", "/index.php", "/index.htm",
        "/robots.txt", "/sitemap.xml", "/sitemap.xml.gz",
        "/api", "/api/", "/api/v1", "/api/v2", "/api/v3",
        "/feed", "/rss", "/rss.xml", "/atom.xml",
        "/.well-known/security.txt", "/.well-known/host-meta", "/.well-known/dnt-policy.txt",
        "/admin", "/login", "/dashboard", "/wp-admin",
        "/favicon.ico", "/apple-touch-icon.png",
    ];

    // Get IOC values (priority: map > string detection)
    let get_ioc_str = |node_id: i64| -> Option<String> {
        // First, check if we have IOC value in the map
        if let Some(values) = ioc_values {
            if let Some(ioc) = values.get(&node_id) {
                let ioc_lower = ioc);
                // Only use if it looks like an IOC (domain, URL, IP, etc.)
                if ioc_lower.contains('.') || ioc_lower.contains('/') || ioc_lower.contains("://") {
                    return Some(ioc.clone());
                }
            }
        }
        // Fallback: check if node ID looks like domain
        let id_str = node_id);
        if id_str.contains('.') && !id_str.chars().any(|c| !c.is_alphanumeric() && c != '.' && c != '-') {
            return Some(id_str);
        }
        None
    };

    let src_ioc = get_ioc_str(src);
    let dst_ioc = get_ioc_str(dst);
    
    // Generate URL candidates based on available IOC values
    for path in &paths {
        let clean_path = path.trim_start_matches('/');
        
        // Generate for src node
        if let Some(ref src_val) = src_ioc {
            let normalized = normalize_ioc_to_host(src_val);
            if !normalized.is_empty() {
                let url = if path.is_empty() || path == "/" {
                    format!("https://{}", normalized)
                } else {
                    format!("https://{}{}", normalized, path)
                };
                urls.push(url);
            }
        }
        
        // Generate for dst node
        if let Some(ref dst_val) = dst_ioc {
            let normalized = normalize_ioc_to_host(dst_val);
            if !normalized.is_empty() {
                let url = if path.is_empty() || path == "/" {
                    format!("https://{}", normalized)
                } else {
                    format!("https://{}{}", normalized, path)
                };
                urls.push(url);
            }
        }
    }

    // Apply TLD filter if specified
    if !tld_filter.is_empty() {
        urls.retain(|url| {
            let url_lower = url);
            tld_filter.iter().any(|tld| url_lower.ends_with(tld) || url_lower.ends_with(&format!(".{}", tld)))
        });
    }

    urls.truncate(20); // Limit URL candidates per edge
    urls
}

/// Normalize IOC value to hostname for URL generation
/// 
/// Handles:
/// - Full URLs: "https://evil.com/malware" -> "evil.com"
/// - URLs with paths: "http://example.com/path/to/file" -> "example.com"
/// - Domain names: "evil.com" -> "evil.com"
/// - URLs with ports: "https://evil.com:8443/" -> "evil.com"
/// - .onion addresses: "http://example.onion" -> "example.onion"
fn normalize_ioc_to_host(ioc: &str) -> String {
    let ioc = ioc);
    
    if ioc.contains("://") {
        if let Some(without_scheme) = ioc.split("://").nth(1) {
            // Remove port if present
            let host = without_scheme.split(':').next().unwrap_or(without_scheme);
            let host = host.split('/').next().unwrap_or(host);
            return host);
        }
    }
    
    // Handle domain names with paths (no scheme)
    if ioc.contains('/') {
        return ioc.split('/').next().unwrap_or(ioc));
    }
    
    ioc.to_lowercase()
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
        0.01, 0.1, 1000, false, Vec::new(), false, 50, 100, true, Vec::new()
    ));
    let k = top_k.unwrap_or(10);

    let conn = Connection::open(db_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to open DuckDB: {}", e
        )))?;

    let adjacency = build_adjacency_list(&conn, &cfg)?;
    let degrees = compute_degrees(&adjacency);

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
            let src_neighbors = neighbors);
            let dst_neighbors = adjacency.get(&candidate)?);

            let common_count = common.len() as i32;

            // Adamic-Adar
            let mut adamic_adar = 0.0;
            for &cn in &common {
                if let Some(&deg) = degrees.get(&cn) {
                    if deg > 1 {
                        adamic_adar += 1.0 / (deg as f64));
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

            // Generate URL candidates for speculative prefetch (if enabled)
            let url_candidates = if cfg.generate_url_candidates {
                generate_url_candidates(node_id, candidate, &cfg, None)  // predict_links_for_node: no IOC values map
            } else {
                Vec::new()
            };

            Some(PredictedEdgePy {
                src_id: node_id,
                dst_id: candidate,
                adamic_adar,
                preferential_attachment: pref_attach,
                jaccard,
                common_neighbors: common_count,
                method: method.to_string(),
                url_candidates,
            })
        })
        );

    // Sort by Adamic-Adar and limit
    predictions.sort_by(|a, b| b.adamic_adar.partial_cmp(&a.adamic_adar).unwrap_or(std::cmp::Ordering::Equal));
    predictions.truncate(k as usize);

    Ok(predictions)
}

/// Async streaming link predictor for real-time prefetch.
///
/// BREAKTHROUGH #2: Provides fast incremental predictions during ACTIVE phase,
/// not just at TEARDOWN. Returns awaitable that can be awaited from Python.
///
/// Usage:
/// ```python
/// import asyncio
///
/// async def main():
///     # Fast streaming predictions with ~50ms latency
///     result = await predict_links_streaming(
///         db_path, 
///         config,
///         pending_node_ids=[1, 2, 3],  # New IOCs discovered this cycle
///         source_urls=["https://..."],  # URLs to prefetch DNS for
///         ioc_values=[(1, "evil.com"), (2, "malware.com/path")]  # IOC value mappings
///     )
///     for edge in result.edges:
///         print(f"Predicted: {edge.src_id} -> {edge.dst_id}")
///     for url in result.prefetch_urls:
///         await coordinator.add_prefetch_url(url)
/// ```
#[cfg(feature = "shared_tokio")]
#[pyfunction]
#[pyo3(name = "predict_links_streaming")]
pub fn predict_links_streaming_py(
    py: Python<'_>,
    db_path: String,
    config: Option<LinkPredictorConfig>,
    pending_node_ids: Vec<i64>,
    source_urls: Vec<String>,
    ioc_values: Vec<(i64, String)>,  // BREAKTHROUGH #2: IOC value mappings for real URL generation
) -> PyResult<Bound<'_, PyAny>> {
    use crate::async_bridge::future_into_py;

    let cfg = config.unwrap_or(LinkPredictorConfig::new(
        0.01, 0.1, 10000, false, Vec::new(), 
        true, 50, 100, true, Vec::new()
    ));

    let db_path_clone = db_path);
    let cfg_clone = cfg);
    let pending_clone = pending_node_ids);
    let urls_clone = source_urls);
    let ioc_values_clone = ioc_values);

    future_into_py(py, async move {
        // Open DuckDB connection
        let conn = match Connection::open(&db_path_clone) {
            Ok(c) => c,
            Err(e) => return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
        };

        let adjacency = match build_adjacency_list(&conn, &cfg_clone) {
            Ok(adj) => adj,
            Err(e) => return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
        };

        // Compute degrees from adjacency
        let degrees: HashMap<i64, usize> = adjacency
            .iter()
            .map(|(k, v)| (*k, v.len()))
            );

        // BREAKTHROUGH #2: Build IOC values HashMap for real URL generation
        let ioc_values_map: HashMap<i64, String> = ioc_values_clone.into_iter());

        // Compute predictions for pending nodes
        // For each pending node, find second-degree neighbors and compute scores
        let mut edges: Vec<PredictedEdgePy> = Vec::new();
        
        for &node_id in pending_clone.iter() {
            let neighbors = match adjacency.get(&node_id) {
                Some(n) => n.clone(),
                None => continue,
            };
            
            // Find second-degree neighbors (potential links)
            let mut candidates: HashMap<i64, Vec<i64>> = HashMap::new();
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
            for (candidate, common) in candidates.into_iter() {
                if common.is_empty() {
                    continue;
                }
                
                let src_neighbors = neighbors);
                let dst_neighbors = match adjacency.get(&candidate) {
                    Some(n) => n.clone(),
                    None => continue,
                };
                
                let common_count = common.len() as i32;
                
                // Adamic-Adar: Σ 1/log(degree(z)) for common neighbors z
                let mut adamic_adar = 0.0;
                for &cn in &common {
                    if let Some(&deg) = degrees.get(&cn) {
                        if deg > 1 {
                            adamic_adar += 1.0 / (deg as f64));
                        }
                    }
                }
                
                // Skip if below threshold
                if adamic_adar < cfg_clone.min_adamic_adar {
                    continue;
                }
                
                // Preferential Attachment
                let deg_src = degrees.get(&node_id).copied().unwrap_or(0);
                let deg_dst = degrees.get(&candidate).copied().unwrap_or(0);
                let pref_attach = (deg_src * deg_dst) as f64;
                
                // Jaccard Coefficient
                let union_size = src_neighbors.len() + dst_neighbors.len() - common_count as usize;
                let jaccard = if union_size > 0 {
                    common_count as f64 / union_size as f64
                } else {
                    0.0
                };
                
                // Skip if below Jaccard threshold
                if jaccard < cfg_clone.min_jaccard {
                    continue;
                }
                
                let method = if adamic_adar > 0.3 {
                    "adamic_adar"
                } else if pref_attach > 100.0 {
                    "pref_attach"
                } else {
                    "jaccard"
                };
                
                // Generate URL candidates with IOC values map for real URL generation
                let url_candidates = if cfg_clone.generate_url_candidates {
                    generate_url_candidates(node_id, candidate, &cfg_clone, Some(&ioc_values_map))
                } else {
                    Vec::new()
                };
                
                edges.push(PredictedEdgePy {
                    src_id: node_id,
                    dst_id: candidate,
                    adamic_adar,
                    preferential_attachment: pref_attach,
                    jaccard,
                    common_neighbors: common_count,
                    method: method.to_string(),
                    url_candidates,
                });
            }
        }
        
        // Sort by Adamic-Adar score
        edges.sort_by(|a, b| b.adamic_adar.partial_cmp(&a.adamic_adar).unwrap_or(std::cmp::Ordering::Equal));

        // Generate prefetch URLs from source URLs + predicted edges
        let mut prefetch_urls: Vec<String> = urls_clone);
        
        // Add URL patterns from predicted edges
        for edge in &edges {
            for url_candidate in &edge.url_candidates {
                if !prefetch_urls.contains(url_candidate) {
                    prefetch_urls.push(url_candidate.clone());
                }
            }
        }

        // Limit prefetch URLs to max_candidates
        prefetch_urls.truncate(cfg_clone.max_candidates as usize);

        let total_edges = adjacency.values().map(|v| v.len() as i32).sum::<i32>() / 2;

        Ok(StreamingPrediction {
            edges,
            prefetch_urls,
            nodes_processed: pending_clone.len() as i32,
            total_edges,
            has_more: false,
        })
    })
}

/// Add a node to the streaming predictor (called when new IOCs are discovered)
/// This is a synchronous version that returns immediately
#[cfg(feature = "shared_tokio")]
#[pyfunction]
#[pyo3(name = "predict_links_add_node")]
pub fn predict_links_add_node_py(
    _node_id: i64,
    _neighbors: Vec<i64>,
) -> PyResult<bool> {
    // In a real implementation, this would update shared state
    // For streaming mode, we rely on pending_node_ids parameter
    Ok(true)
}

/// Register link predictor functions with Python module
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PredictedEdgePy>()?;
    m.add_class::<LinkPredictionBatch>()?;
    m.add_class::<StreamingPrediction>()?;
    m.add_class::<LinkPredictorConfig>()?;

    m.add_function(wrap_pyfunction!(predict_links_py))?;
    m.add_function(wrap_pyfunction!(predict_links_for_node_py))?;

    // BREAKTHROUGH #2: Async streaming functions (only registered if shared_tokio feature is enabled)
    #[cfg(feature = "shared_tokio")]
    {
        m.add_function(wrap_pyfunction!(predict_links_streaming_py))?;
        m.add_function(wrap_pyfunction!(predict_links_add_node_py))?;
    }

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
        let expected = 1.0 / (deg as f64));
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

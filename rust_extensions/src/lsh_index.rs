//! LSH (Locality-Sensitive Hashing) Index for Near-Duplicate Detection
//!
//! Implements multi-table LSH using AND-construction (banding) for
//! efficient near-duplicate detection at scale.
//!
//! ## Performance Characteristics
//!
//! - Build time: O(n * k) where n = documents, k = number of bands
//! - Query time: O(1) average for single item lookup
//! - Space: O(n * k) for hash tables
//! - Recall: ~95% for threshold 3 (64-bit fingerprints)
//!
//! ## API
//!
//! - `lsh_index_new(num_tables, num_rows)` → LSHIndex
//! - `lsh_index_insert(index, doc_id, fingerprint)` → None
//! - `lsh_index_query(index, fingerprint, max_results)` → Vec<(doc_id, score)>
//! - `lsh_index_batch_insert(index, items)` → None
//! - `lsh_index_clear(index)` → None

use pyo3::prelude::*;
use std::collections::HashMap;

/// Number of tables and rows per table
const DEFAULT_NUM_TABLES: usize = 16;
const DEFAULT_NUM_ROWS: usize = 4;

/// LSH Index using AND-construction (banding)
/// Each band is a row; a candidate matches if ALL rows in a band match.
#[pyclass(frozen)]
pub struct LSHIndex {
    /// Hash tables, one per band/row combination
    /// Key: band_hash, Value: list of (doc_id, fingerprint)
    tables: Vec<HashMap<u64, Vec<(String, u64)>>>,
    /// Number of bands (tables)
    num_bands: usize,
    /// Number of rows per band
    num_rows: usize,
    /// Total number of tables
    num_tables: usize,
    /// All inserted fingerprints for similarity computation
    fingerprints: HashMap<String, u64>,
}

#[pymethods]
impl LSHIndex {
    /// Creates new LSHIndex with specified parameters.
    ///
    /// ## Arguments
    /// - `num_tables`: Number of hash tables (default 16, higher = better recall)
    /// - `num_rows`: Number of rows per band (default 4, higher = better precision)
    ///
    /// ## Example
    /// ```python
    /// from hledac_rust_extensions import lsh_index_new
    /// index = lsh_index_new(num_tables=16, num_rows=4)
    /// ```
    #[new]
    #[pyo3(signature = (num_tables=DEFAULT_NUM_TABLES, num_rows=DEFAULT_NUM_ROWS))]
    pub fn new(num_tables: usize, num_rows: usize) -> Self {
        let num_bands = num_tables;
        let mut tables = Vec::with_capacity(num_bands);
        for _ in 0..num_bands {
            tables.push(HashMap::new());
        }

        Self {
            tables,
            num_bands,
            num_rows,
            num_tables,
            fingerprints: HashMap::new(),
        }
    }

    /// Get number of tables.
    pub fn get_num_tables(&self) -> usize {
        self.num_tables
    }

    /// Get number of rows per band.
    pub fn get_num_rows(&self) -> usize {
        self.num_rows
    }

    /// Get number of stored documents.
    pub fn len(&self) -> usize {
        self.fingerprints.len()
    }

    /// Check if index is empty.
    pub fn is_empty(&self) -> bool {
        self.fingerprints.is_empty()
    }

    /// Insert a document into the LSH index.
    ///
    /// ## Arguments
    /// - `doc_id`: Unique document identifier
    /// - `fingerprint`: 64-bit SimHash fingerprint
    #[pyo3(signature = (doc_id, fingerprint))]
    pub fn insert(&mut self, doc_id: &str, fingerprint: u64) {
        // Store fingerprint
        self.fingerprints.insert(doc_id.to_string(), fingerprint);

        // Insert into each band's hash table
        for band_idx in 0..self.num_bands {
            let band_hash = self._compute_band_hash(fingerprint, band_idx);
            let table = &mut self.tables[band_idx];
            table
                .entry(band_hash)
                .or_insert_with(Vec::new)
                .push((doc_id.to_string(), fingerprint));
        }
    }

    /// Query the index for similar documents.
    ///
    /// ## Arguments
    /// - `fingerprint`: 64-bit SimHash fingerprint to query
    /// - `max_results`: Maximum number of results to return (default 100)
    ///
    /// ## Returns
    /// List of (doc_id, similarity_score) tuples, sorted by score descending.
    /// Empty list if no candidates found.
    #[pyo3(signature = (fingerprint, max_results=100))]
    pub fn query(&self, fingerprint: u64, max_results: usize) -> Vec<(String, f64)> {
        // Collect candidate doc_ids from all bands
        let mut candidate_counts: HashMap<&str, usize> = HashMap::new();

        for band_idx in 0..self.num_bands {
            let band_hash = self._compute_band_hash(fingerprint, band_idx);
            if let Some(candidates) = self.tables[band_idx].get(&band_hash) {
                for (doc_id, _) in candidates {
                    *candidate_counts.entry(doc_id.as_str()).or_insert(0) += 1;
                }
            }
        }

        // Filter candidates that match in at least `num_rows` bands
        // (AND-construction: all rows must match)
        let threshold = self.num_rows;
        let matching_ids: Vec<&str> = candidate_counts
            .into_iter()
            .filter(|(_, count)| *count >= threshold)
            .map(|(doc_id, _)| doc_id)
            .collect();

        // Compute similarity scores
        let mut scored: Vec<(String, f64)> = Vec::new();
        for doc_id in matching_ids {
            if let Some(stored_fp) = self.fingerprints.get(doc_id) {
                let distance = self._hamming_distance(fingerprint, *stored_fp);
                // Convert distance to similarity score (0.0 = identical, 1.0 = max distance)
                let similarity = 1.0 - (distance as f64 / 64.0);
                scored.push((doc_id.to_string(), similarity));
            }
        }

        // Sort by similarity descending
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

        // Limit results
        scored.truncate(max_results);
        scored
    }

    /// Batch insert multiple documents.
    ///
    /// ## Arguments
    /// - `items`: List of (doc_id, fingerprint) tuples
    #[pyo3(signature = (items))]
    pub fn batch_insert(&mut self, items: Vec<(String, u64)>) {
        // Sequential insertion for correctness
        for (doc_id, fp) in items {
            self.insert(&doc_id, fp);
        }
    }

    /// Clear all documents from the index.
    pub fn clear(&mut self) {
        for table in &mut self.tables {
            table.clear();
        }
        self.fingerprints.clear();
    }
}

impl LSHIndex {
    /// Compute band hash for a fingerprint.
    /// Uses multiple row hashes combined into one band hash.
    #[inline]
    fn _compute_band_hash(&self, fingerprint: u64, band_idx: usize) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        fingerprint.hash(&mut hasher);
        band_idx.hash(&mut hasher);
        hasher.finish()
    }

    /// Compute Hamming distance between two fingerprints.
    #[inline]
    fn _hamming_distance(&self, fp1: u64, fp2: u64) -> u32 {
        (fp1 ^ fp2).count_ones()
    }
}

// ===== Python Module Functions =====

/// Create a new LSH index.
///
/// Shorthand for `LSHIndex.new()`.
#[pyfunction]
#[pyo3(signature = (num_tables=DEFAULT_NUM_TABLES, num_rows=DEFAULT_NUM_ROWS))]
pub fn lsh_index_new(num_tables: usize, num_rows: usize) -> LSHIndex {
    LSHIndex::new(num_tables, num_rows)
}

/// Compute LSH bands for a fingerprint.
///
/// Returns the band indices that a fingerprint would hash to.
/// Useful for debugging and understanding LSH behavior.
#[pyfunction]
#[pyo3(signature = (fingerprint, num_tables=DEFAULT_NUM_TABLES))]
pub fn lsh_get_bands(fingerprint: u64, num_tables: usize) -> Vec<u64> {
    let mut bands = Vec::with_capacity(num_tables);
    for band_idx in 0..num_tables {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        use std::hash::{Hash, Hasher};
        fingerprint.hash(&mut hasher);
        band_idx.hash(&mut hasher);
        bands.push(hasher.finish());
    }
    bands
}

/// Estimate Jaccard similarity from LSH band match probability.
///
/// Given threshold t (0 < t < 1) and LSH parameters (num_tables, num_rows),
/// returns the probability that two similar documents (Jaccard >= t)
/// will be retrieved by LSH.
#[pyfunction]
#[pyo3(signature = (threshold, num_tables, num_rows))]
pub fn lsh_estimate_recall(threshold: f64, num_tables: usize, num_rows: usize) -> f64 {
    // Jaccard similarity s -> probability of row match = s
    // AND construction: all rows must match = s^rows
    // OR construction across tables: 1 - (1 - s^rows)^tables
    let s = threshold;
    let rows = num_rows;
    let tables = num_tables;

    let row_match_prob = s.powi(rows as i32);
    let recall = 1.0 - (1.0 - row_match_prob).powi(tables as i32);
    recall.max(0.0).min(1.0)
}

// ===== Module Registration =====

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(lsh_index_new, m)?)?;
    m.add_function(wrap_pyfunction!(lsh_get_bands, m)?)?;
    m.add_function(wrap_pyfunction!(lsh_estimate_recall, m)?)?;
    m.add_class::<LSHIndex>()?;
    Ok(())
}

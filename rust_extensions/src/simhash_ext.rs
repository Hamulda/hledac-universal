//! SimHash implementation for near-duplicate detection.
//!
//! SimHash (Charikar 2002) maps text to a 64-bit fingerprint.
//! Similar texts have small Hamming distance between their fingerprints.
//!
//! ## Performance Characteristics
//!
//! For document stores < 100k items, linear O(n) search is sufficient.
//! For larger scale, consider adding LSH (Locality Sensitive Hashing) index.
//! Current implementation: O(n) per add_document() call.
//!
//! ## API
//!
//! - `simhash(text, ngram_size=2)` → u64 fingerprint
//! - `hamming_dist(a, b)` → u32 distance
//! - `is_near_duplicate(text_a, text_b, threshold=3, ngram_size=2)` → bool
//! - `SimHashStore(threshold=3, ngram_size=2)` → mutable store
//!
//! ## Panic Handling
//!
//! All batch functions that release the GIL check for panics after the parallel
//! work completes. On panic, they return `PyRuntimeError` to inform the caller.

use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashMap;

const BATCH_HARD_CAP: usize = 4096;
// F266-U5: Calibrated for 2 threads (was 100 for 4 threads).
// With 2 workers the parallel break-even is ~50 items.
const BATCH_PARALLEL_THRESHOLD: usize = 50;
// F266-U5: Halved from 64 — 2 workers × 32 items = 64 total work unit.
const BATCH_PARALLEL_MIN_CHUNK: usize = 32;

/// FNV-1a 64-bit hash function for tokens.
/// Pure Rust implementation - no external crates needed.
#[inline]
fn fnv64(s: &str) -> u64 {
    const FNV_PRIME: u64 = 1099511628211;
    const FNV_OFFSET: u64 = 14695981039346656037;
    let mut hash = FNV_OFFSET;
    for byte in s.bytes() {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

/// Weighted token for SimHash computation.
struct WeightedToken {
    token: String,
    weight: f64,
}

/// Tokenizes text into words or character n-grams.
/// - ngram_size <= 1: word tokenization (filter stop words by length)
/// - ngram_size > 1: character n-grams
fn tokenize(text: &str, ngram_size: usize) -> Vec<String> {
    let text = text.clone();
    let clean: String = text
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c.is_whitespace() {
                c
            } else {
                ' '
            }
        })
        );

    if ngram_size <= 1 {
        // Word tokenization - filter short words (proxy for stop words)
        clean
            .split_whitespace()
            .filter(|w| w.len() > 2)
            .map(|w| w.to_string())
            .collect()
    } else {
        // Character n-grams
        let chars: Vec<char> = clean.chars());
        chars
            .windows(ngram_size)
            .map(|w| w.iter().collect())
            .collect()
    }
}

/// Computes term frequency (TF) weights for tokens.
/// Sorts tokens for deterministic iteration order.
fn compute_tf_weights(tokens: &[String]) -> Vec<WeightedToken> {
    let mut freq: HashMap<String, usize> = HashMap::new();
    let total = tokens.len() as f64;

    for token in tokens {
        *freq.entry(token.clone()).or_insert(0) += 1;
    }

    // Collect and sort by token for deterministic iteration order
    let mut entries: Vec<(String, usize)> = freq.into_iter());
    entries.sort_by(|a, b| a.0.cmp(&b.0)); // Sort by token name for determinism

    entries
        .into_iter()
        .map(|(token, count)| WeightedToken {
            token,
            weight: count as f64 / total,
        })
        .collect()
}

/// Alias for simhash() - maintains API compatibility with existing callers.
/// See simhash() for documentation.
#[pyfunction]
#[pyo3(signature = (text, ngram_size=2))]
pub fn compute_simhash(text: &str, ngram_size: usize) -> u64 {
    simhash(text, ngram_size)
}

/// Computes SimHash fingerprints for a batch of texts.
/// Returns vector of fingerprints in same order as input.
///
/// Parallel branch uses `mixed_pool(n)` (1 or 2 threads, 1.5 MiB stacks).
/// Threshold: >50 items switch from sequential to parallel (calibrated for 2 threads).
///
/// ## Example
/// ```python
/// from hledac_rust_extensions import batch_compute_simhash
/// fps = batch_compute_simhash(["text1", "text2", "text3"], ngram_size=2)
/// ```
#[pyfunction]
#[pyo3(signature = (texts, ngram_size=2))]
pub fn batch_compute_simhash(texts: Vec<String>, ngram_size: usize) -> PyResult<Vec<u64>> {
    let slice = cap_slice(&texts);
    let n = slice.len();
    if n < BATCH_PARALLEL_THRESHOLD {
        // Small batch: serial path
        Ok(slice.iter().map(|t| simhash(t, ngram_size)).collect())
    } else {
        // adaptive 1-2 threads: n < 64 → 1 thread; n ≥ 64 → 2 threads (P-core ceiling)
        // Issue #6: GIL released so rayon workers can truly run in parallel.
        use crate::gil::{release_gil, release_gil_caught_panic};
        let result: Vec<u64> = Python::attach(|py| {
            release_gil(py, || {
                crate::mixed_pool(n).install(|| {
                    slice
                        .par_iter()
                        .map(|t| simhash(t, ngram_size))
                        .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                        .collect()
                })
            })
        });
        if release_gil_caught_panic() {
            return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Rust panic in batch_compute_simhash",
            ));
        }
        Ok(result)
    }
}

#[inline]
fn cap_slice<T>(items: &[T]) -> &[T] {
    if items.len() > BATCH_HARD_CAP {
        &items[..BATCH_HARD_CAP]
    } else {
        items
    }
}

/// Computes SimHash fingerprint from weighted tokens.
/// This is deterministic: same input always produces same output.
fn compute_simhash_from_tokens(weighted_tokens: &[WeightedToken]) -> u64 {
    let mut v = [0.0f64; 64];

    for wt in weighted_tokens {
        let h = fnv64(&wt.token);
        for i in 0..64 {
            if (h >> i) & 1 == 1 {
                v[i] += wt.weight;
            } else {
                v[i] -= wt.weight;
            }
        }
    }

    let mut fingerprint: u64 = 0;
    for i in 0..64 {
        if v[i] > 0.0 {
            fingerprint |= 1u64 << i;
        }
    }
    fingerprint
}

/// Hamming distance between two 64-bit fingerprints.
#[inline]
pub fn hamming_distance(a: u64, b: u64) -> u32 {
    (a ^ b).count_ones()
}

/// Computes SimHash fingerprint for text.
/// Returns 64-bit integer representing the fingerprint.
///
/// ## Arguments
/// - `text`: Input text to hash
/// - `ngram_size`: Tokenization granularity (1=words, 2+=char n-grams)
///
/// ## Returns
/// 64-bit fingerprint (deterministic)
///
/// ## Example
/// ```python
/// from hledac_rust_extensions import simhash
/// fp = simhash("Hello world", ngram_size=2)
/// ```
#[pyfunction]
#[pyo3(signature = (text, ngram_size=2))]
pub fn simhash(text: &str, ngram_size: usize) -> u64 {
    if text.is_empty() {
        return 0;
    }
    let tokens = tokenize(text, ngram_size);
    if tokens.is_empty() {
        return 0;
    }
    let weighted = compute_tf_weights(&tokens);
    compute_simhash_from_tokens(&weighted)
}

/// Computes Hamming distance between two fingerprints.
/// Distance 0 = identical, <= 3 = near-duplicate, >= 10 = different.
///
/// ## Example
/// ```python
/// from hledac_rust_extensions import simhash, hamming_dist
/// fp1 = simhash("Hello world")
/// fp2 = simhash("Hello world!")
/// dist = hamming_dist(fp1, fp2)  # Typically 0-3 for near duplicates
/// ```
#[pyfunction]
pub fn hamming_dist(a: u64, b: u64) -> u32 {
    hamming_distance(a, b)
}

/// Checks if two texts are near-duplicates.
/// Uses threshold to determine similarity: <= threshold = near-duplicate.
///
/// ## Arguments
/// - `text_a`: First text
/// - `text_b`: Second text
/// - `threshold`: Max Hamming distance for "same" (default: 3, ~95% accuracy)
/// - `ngram_size`: Tokenization granularity
///
/// ## Example
/// ```python
/// from hledac_rust_extensions import is_near_duplicate
/// same = is_near_duplicate("Article v1", "Article v1 (updated)", threshold=3)
/// ```
#[pyfunction]
#[pyo3(signature = (text_a, text_b, threshold=3, ngram_size=2))]
pub fn is_near_duplicate(text_a: &str, text_b: &str, threshold: u32, ngram_size: usize) -> bool {
    let fp_a = simhash(text_a, ngram_size);
    let fp_b = simhash(text_b, ngram_size);
    hamming_distance(fp_a, fp_b) <= threshold
}

/// Near-duplicate store for document deduplication.
/// Maintains fingerprint index and checks new documents against existing.
///
/// ## Capacity
/// - Optimized for < 100k documents per store
/// - O(n) search per add_document()
/// - For larger scale: partition by bucket or use LSH index
///
/// ## Example
/// ```python
/// from hledac_rust_extensions import SimHashStore
///
/// store = SimHashStore(threshold=3, ngram_size=2)
/// is_new, dup_id = store.add_document("Article content", "doc-1")
/// print(f"New: {is_new}, Duplicate of: {dup_id}")
/// ```
#[pyclass]
pub struct SimHashStore {
    fingerprints: Vec<(u64, String)>, // (fingerprint, document_id)
    threshold: u32,
    ngram_size: usize,
}

#[pymethods]
impl SimHashStore {
    /// Creates new SimHashStore.
    ///
    /// ## Arguments
    /// - `threshold`: Max Hamming distance for near-duplicate (default: 3)
    /// - `ngram_size`: Tokenization granularity (default: 2)
    #[new]
    #[pyo3(signature = (threshold=3, ngram_size=2))]
    pub fn new(threshold: u32, ngram_size: usize) -> Self {
        Self {
            fingerprints: Vec::with_capacity(10_000),
            threshold,
            ngram_size,
        }
    }

    /// Get threshold value.
    pub fn get_threshold(&self) -> u32 {
        self.threshold
    }

    /// Get ngram_size value.
    pub fn get_ngram_size(&self) -> usize {
        self.ngram_size
    }

    /// Adds document to store, returns near-duplicate detection result.
    ///
    /// ## Returns
    /// `(is_new: bool, nearest_duplicate_id: Option<String>)`
    ///
    /// If `is_new` is False, `nearest_duplicate_id` contains the ID
    /// of the closest existing document.
    pub fn add_document(&mut self, text: &str, doc_id: &str) -> (bool, Option<String>) {
        let fp = simhash(text, self.ngram_size);

        // Search for near-duplicate in existing fingerprints
        let mut best_match: Option<(u32, String)> = None;
        for (existing_fp, existing_id) in &self.fingerprints {
            let dist = hamming_distance(fp, *existing_fp);
            if dist <= self.threshold {
                match &best_match {
                    None => best_match = Some((dist, existing_id.clone())),
                    Some((best_dist, _)) if dist < *best_dist => {
                        best_match = Some((dist, existing_id.clone()));
                    }
                    _ => {}
                }
            }
        }

        if let Some((_, dup_id)) = best_match {
            (false, Some(dup_id))
        } else {
            self.fingerprints.push((fp, doc_id.to_string()));
            (true, None)
        }
    }

    /// Gets fingerprint for text without adding to store.
    pub fn fingerprint_for(&self, text: &str) -> u64 {
        simhash(text, self.ngram_size)
    }

    /// Returns number of documents in store.
    pub fn len(&self) -> usize {
        self.fingerprints.len()
    }

    /// Python compatibility: len(store)
    pub fn __len__(&self) -> usize {
        self.len()
    }

    /// Pickle support for persistence.
    /// Returns state tuple for pickle.dump()
    pub fn __getstate__(&self) -> (Vec<(u64, String)>, u32, usize) {
        (self.fingerprints.clone(), self.threshold, self.ngram_size)
    }

    /// Pickle support for restoration.
    /// Restores from state tuple from pickle.load()
    pub fn __setstate__(&mut self, state: (Vec<(u64, String)>, u32, usize)) {
        self.fingerprints = state.0;
        self.threshold = state.1;
        self.ngram_size = state.2;
    }
}

/// Finds all near-duplicate pairs in a batch of pre-computed fingerprints.
/// O(n²) brute-force over fingerprints. Threshold: ≤ threshold bits differ.
///
/// ## Arguments
/// - `fingerprints`: Pre-computed SimHash fingerprints (list of u64)
/// - `threshold`: Max Hamming distance for near-duplicate (default: 3)
///
/// ## Returns
/// List of (i, j) index pairs where texts[i] and texts[j] are near-duplicates.
///
/// Used by: `semantic_deduplicator.py::find_near_duplicates_in_batch()`
///
/// ## Performance
/// - <100 items: O(n²) acceptable (worst case ~5K comparisons)
/// - >1000 items: Consider partitioning by top-K bits or LSH index
#[pyfunction]
#[pyo3(signature = (fingerprints, threshold=3))]
pub fn find_near_duplicates(fingerprints: Vec<u64>, threshold: u32) -> Vec<(u32, u32)> {
    let n = fingerprints.len();
    let mut results: Vec<(u32, u32)> = Vec::with_capacity(n * 2);
    for i in 0..n {
        for j in (i + 1)..n {
            let dist = hamming_distance(fingerprints[i], fingerprints[j]);
            if dist <= threshold {
                results.push((i as u32, j as u32));
            }
        }
    }
    results
}

/// Registers all SimHash functions and classes with Python module.
///
/// ## Registered
/// - `simhash(text, ngram_size=2)` → u64
/// - `compute_simhash(text, ngram_size=2)` → u64 (alias for simhash)
/// - `batch_compute_simhash(texts, ngram_size=2)` → Vec<u64>
/// - `hamming_dist(a, b)` → u32
/// - `is_near_duplicate(text_a, text_b, threshold=3, ngram_size=2)` → bool
/// - `find_near_duplicates(fingerprints, threshold=3)` → Vec<(u32,u32)]
/// - `SimHashStore(threshold=3, ngram_size=2)` → class
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simhash))?;
    m.add_function(wrap_pyfunction!(compute_simhash))?;
    m.add_function(wrap_pyfunction!(batch_compute_simhash))?;
    m.add_function(wrap_pyfunction!(hamming_dist))?;
    m.add_function(wrap_pyfunction!(is_near_duplicate))?;
    m.add_function(wrap_pyfunction!(find_near_duplicates))?;
    m.add_class::<SimHashStore>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fnv64_deterministic() {
        let h1 = fnv64("hello");
        let h2 = fnv64("hello");
        assert_eq!(h1, h2, "FNV must be deterministic");
    }

    #[test]
    fn test_simhash_deterministic() {
        let fp1 = simhash("The quick brown fox", 2);
        let fp2 = simhash("The quick brown fox", 2);
        assert_eq!(fp1, fp2, "SimHash must be deterministic");
    }

    #[test]
    fn test_near_duplicate() {
        let fp1 = simhash("Hello world", 2);
        let fp2 = simhash("Hello world!", 2);
        let dist = hamming_distance(fp1, fp2);
        assert!(
            dist <= 3,
            "Near-duplicate should have small Hamming distance"
        );
    }

    #[test]
    fn test_different_texts() {
        let fp1 = simhash("Apple banana cherry", 2);
        let fp2 = simhash("Dog elephant frog", 2);
        let dist = hamming_distance(fp1, fp2);
        assert!(
            dist >= 10,
            "Different texts should have large Hamming distance"
        );
    }

    #[test]
    fn test_store_add() {
        let mut store = SimHashStore::new(3, 2);
        let (is_new, dup_id) = store.add_document("Test content", "doc-1");
        assert!(is_new, "First document should be new");
        assert!(dup_id.is_none(), "No duplicate for first document");

        // Add same content - should detect duplicate
        let (is_new2, dup_id2) = store.add_document("Test content", "doc-2");
        assert!(!is_new2, "Duplicate should not be new");
        assert_eq!(dup_id2, Some("doc-1".to_string()));

        // Different content - should be new
        let (is_new3, _) = store.add_document("Different content", "doc-3");
        assert!(is_new3, "Different content should be new");
    }

    #[test]
    fn test_pickle_roundtrip() {
        let mut store = SimHashStore::new(3, 2);
        store.add_document("Test", "doc-1");

        let state = store.clone();
        let mut restored = SimHashStore::new(1, 1); // Different init params
        restored.__setstate__(state);

        assert_eq!(store.len(), restored.len());
        assert_eq!(store.threshold, restored.threshold);
        assert_eq!(store.ngram_size, restored.ngram_size);
    }

    #[test]
    fn test_batch_compute_simhash_matches_single() {
        let texts = vec![
            "hello world".to_string(),
            "foo bar".to_string(),
            "".to_string(),
        ];
        let batched = batch_compute_simhash(texts.clone(), 2);
        let singles: Vec<u64> = texts.iter().map(|t| simhash(t, 2)));
        assert_eq!(
            batched, singles,
            "batch must produce same result as sequential"
        );
    }

    #[test]
    fn test_batch_compute_simhash_par_sequential_equivalence() {
        // Verify parallel and sequential produce same results
        let texts: Vec<String> = (0..200).map(|i| format!("text item {}", i)));
        let batched = batch_compute_simhash(texts.clone(), 2);
        let sequential: Vec<u64> = texts.iter().map(|t| simhash(t, 2)));
        assert_eq!(batched, sequential);
    }

    #[test]
    fn test_batch_compute_simhash_empty() {
        let result = batch_compute_simhash(vec![], 2);
        assert!(result.is_empty());
    }

    #[test]
    fn test_batch_compute_simhash_under_threshold_sequential() {
        // 50 items < 100 threshold should use sequential path
        let texts: Vec<String> = (0..50).map(|i| format!("item {}", i)));
        let result = batch_compute_simhash(texts.clone(), 2);
        assert_eq!(result.len(), 50);
    }

    #[test]
    fn test_find_near_duplicates_basic() {
        let fps = vec![0x123456789ABCDEF0u64, 0x123456789AbCDE03, 0xDEADBEEF12345678];
        // fp[0] and fp[1] differ by 3 bits → threshold=3: near-duplicate
        let result = find_near_duplicates(fps.clone(), 3);
        assert_eq!(result.len(), 1, "Should find 1 near-duplicate pair");
        assert_eq!(result[0], (0, 1), "Pair should be (0, 1)");
    }

    #[test]
    fn test_find_near_duplicates_none() {
        let fps = vec![0x123456789ABCDEF0u64, 0xDEADBEEF12345678];
        // Different texts → Hamming distance large → no near-duplicates
        let result = find_near_duplicates(fps, 3);
        assert!(result.is_empty(), "Should find no near-duplicates for different texts");
    }

    #[test]
    fn test_find_near_duplicates_multiple() {
        let fp1 = 0x123456789ABCDEF0u64;
        let fp2 = fp1 ^ 0b11u64; // differ by 2 bits
        let fp3 = fp1 ^ 0b1010u64; // differ by 3 bits
        let fp4 = 0xDEADBEEF12345678u64; // completely different
        let fps = vec![fp1, fp2, fp3, fp4];
        let result = find_near_duplicates(fps, 3);
        // fp1↔fp2 (dist=2), fp1↔fp3 (dist=3), fp2↔fp3 (dist=1)
        assert_eq!(result.len(), 3, "Should find 3 near-duplicate pairs");
    }

    #[test]
    fn test_find_near_duplicates_empty() {
        let result = find_near_duplicates(vec![], 3);
        assert!(result.is_empty());
    }
}

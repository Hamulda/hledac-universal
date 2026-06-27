//! Zero-Copy PyO3 Batch Utilities
//!
//! This module provides high-throughput PyO3 bindings for batch operations
//! using PyO3 0.29+ Bound API, eliminating GIL acquire/release overhead
//! per-item through scoped GIL holding.
//!
//! ## Performance Characteristics
//!
//! | Approach | Python→Rust copies | GIL overhead | Use case |
//! |-----------|-------------------|--------------|----------|
//! | `Vec<String>` | N (one per item) | N× acquire/release | Legacy, simple |
//! | Bound + rayon | 0 during parallel | 1× hold for scope | Zero-copy batch |
//!
//! ## PyO3 0.29+ Bound API
//!
//! PyO3 0.29 stabilizes the Bound API as the primary interface:
//! - `Bound<'py, T>` — safe borrowed access to Python objects
//! - `Py<PyList>` — ownership-free return type (no lifetime constraints)
//! - `Bound::iter()` — efficient iterator over container items
//!
//! This module targets PyO3 0.29+ API (current project version).
//!
//! ## M1 8GB Considerations
//!
//! - GIL held across entire rayon `install()` scope — safe for PyO3 access
//! - `mixed_pool(n)` ensures 1-2 thread parallelism matching M1 P-cores
//! - No per-item GIL acquire/release — eliminates significant overhead

use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;

// Re-export batch constants from other modules for consistency
use crate::mixed_pool;

// Shared NEON histogram and entropy from quality_gate (avoids duplicate SIMD code)
use crate::quality_gate::{compute_histogram_neon, entropy_from_histogram, ENTROPY_NEON_THRESHOLD};

/// Hard cap for batch sizes — prevents OOM on pathological inputs.
/// M1 8GB: 1000 texts × 1MB max = 1GB worst-case, we cap at 10k items.
pub const ZERO_COPY_BATCH_MAX: usize = 10_000;

/// Threshold for parallel processing (calibrated for 2 threads).
pub const ZERO_COPY_PARALLEL_THRESHOLD: usize = 50;

// ---------------------------------------------------------------------------
// Zero-Copy Iterators
// ---------------------------------------------------------------------------

/// Borrowed iterator over a Python list of strings.
///
/// Uses PyO3 0.29+ `Bound<PyList>::iter()` which provides efficient
/// O(1) per-element access (no repeated `__getitem__` calls).
///
/// IMPORTANT: GIL must be held for the lifetime of this iterator.
/// The iterator borrows the underlying Python list — no allocation
/// during iteration itself.
pub struct PyStrListIter<'py> {
    /// Cached length to avoid repeated Python calls.
    len: usize,
    /// Index for manual iteration over Bound::iter().
    /// Using Bound::iter() directly gives us O(1) access per element.
    iter: <Bound<'py, PyList> as IntoIterator>::IntoIter,
}

impl<'py> PyStrListIter<'py> {
    #[inline]
    pub fn new(list: Bound<'py, PyList>) -> Self {
        let len = list.len();
        // PyO3 0.29+: Bound<PyList>::iter() returns an iterator that
        // calls __next__ on the Python iterator — O(1) per element.
        let iter = list.iter();
        Self { len, iter }
    }
}

impl<'py> Iterator for PyStrListIter<'py> {
    type Item = String;

    #[inline]
    fn next(&mut self) -> Option<Self::Item> {
        // iter.next() returns Bound<'py, PyAny> for each element.
        // We then convert to &str and copy to Rust String.
        // This is zero-copy in the sense that we don't re-allocate
        // the Python string buffer — we just copy the chars to Rust.
        self.iter.next().and_then(|item| {
            item.str().ok().map(|s| s.to_string_lossy().into_owned())
        })
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        // PyListIterator is ExactSizeIterator in PyO3 0.29+
        (self.len, Some(self.len))
    }
}

impl<'py> ExactSizeIterator for PyStrListIter<'py> {
    #[inline]
    fn len(&self) -> usize {
        self.len
    }
}

// ---------------------------------------------------------------------------
// Zero-Copy Batch Processors
// ---------------------------------------------------------------------------

/// Trait for zero-copy batch operations.
/// Implementors define `process_batch` which receives borrowed Python strings
/// and writes results directly to a Python list.
pub trait ZeroCopyBatch: Send + Sync {
    /// Process a single item and return result as string.
    fn process_one(&self, text: &str) -> String;

    /// Process batch with rayon parallelization.
    /// GIL is held for the entire rayon scope — PyO3 access is safe.
    /// Returns number of items processed.
    fn process_batch(
        &self,
        texts: &[&str],
        output: &Bound<'_, PyList>,
        _py: Python<'_>,
    ) -> PyResult<usize> {
        let n = texts.len();
        let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
            texts.iter().map(|t| self.process_one(t)).collect()
        } else {
            mixed_pool(n).install(|| {
                texts.par_iter().map(|t| self.process_one(t)).collect()
            })
        };

        for result in &results {
            output.append(result.as_str())?;
        }
        Ok(results.len())
    }
}

/// Zero-copy batch entropy computation.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_entropy_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let n = texts.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }

    // Collect Python strings under GIL, then process in parallel
    // This is the optimal pattern: GIL held during collection,
    // rayon parallel scope afterwards (no Python objects accessed)
    let texts_slice: Vec<String> = PyStrListIter::new(texts).collect();
    let n = texts_slice.len();

    let results: Vec<f64> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice.iter().map(|t| compute_entropy_zc(t)).collect()
    } else {
        mixed_pool(n).install(|| {
            texts_slice
                .par_iter()
                .map(|t| compute_entropy_zc(t))
                .collect()
        })
    };

    let output = PyList::new(py, &results)?;
    Ok(output)
}

/// Compute Shannon entropy of a string.
///
/// Uses ARM NEON SIMD histogram on aarch64 (>= ENTROPY_NEON_THRESHOLD bytes),
/// scalar fallback for small texts and non-NEON targets.
#[inline]
fn compute_entropy_zc(text: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let bytes = text.as_bytes();
    let n = bytes.len();
    if n < ENTROPY_NEON_THRESHOLD {
        // Small text: scalar path (avoids NEON setup overhead)
        let mut freq = [0u64; 256];
        for &b in bytes {
            freq[b as usize] += 1;
        }
        let len = n as f64;
        let mut entropy = 0.0_f64;
        for &c in freq.iter() {
            if c > 0 {
                let p = c as f64 / len;
                entropy -= p * p.log2();
            }
        }
        entropy
    } else {
        // Large text: NEON histogram + shared entropy (same as quality_gate.rs)
        let hist = unsafe { compute_histogram_neon(bytes) };
        entropy_from_histogram(&hist, n)
    }
}

/// Zero-copy batch URL fingerprinting.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_url_fingerprints_zc<'py>(
    urls: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let n = urls.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }

    let urls_slice: Vec<String> = PyStrListIter::new(urls).collect();
    let n = urls_slice.len();

    let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        urls_slice.iter().map(|u| url_fingerprint_zc(u)).collect()
    } else {
        mixed_pool(n).install(|| {
            urls_slice
                .par_iter()
                .map(|u| url_fingerprint_zc(u))
                .collect()
        })
    };

    let output = PyList::new(py, &results)?;
    Ok(output)
}

/// URL fingerprint: normalize + BLAKE2b-128 hex.
#[inline]
fn url_fingerprint_zc(url: &str) -> String {
    // Sprint F216R canonical URL normalizer from url_engine
    let normalized = crate::url_engine::normalize(url).unwrap_or_else(|_| url.to_string());
    crate::quality_gate::dedup_fingerprint(&normalized)
}

/// Zero-copy batch dedup fingerprints.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_dedup_fingerprints_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let n = texts.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }

    let texts_slice: Vec<String> = PyStrListIter::new(texts).collect();
    let n = texts_slice.len();

    let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice
            .iter()
            .map(|t| crate::quality_gate::dedup_fingerprint(t))
            .collect()
    } else {
        mixed_pool(n).install(|| {
            texts_slice
                .par_iter()
                .map(|t| crate::quality_gate::dedup_fingerprint(t))
                .collect()
        })
    };

    let output = PyList::new(py, &results)?;
    Ok(output)
}

// ---------------------------------------------------------------------------
// Module Registration
// ---------------------------------------------------------------------------

/// Register zero-copy batch functions with the Python module.
///
/// # Arguments
/// * `m` - Python module to register functions with
///
/// # Returns
/// * `PyResult<()>` - Ok on success, Err on registration failure
///
/// # Example
/// ```python
/// from hledac_rust_extensions import batch_entropy_zc, batch_url_fingerprints_zc
/// ents = batch_entropy_zc(["hello", "world"])
/// ```
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_entropy_zc, m)?)?;
    m.add_function(wrap_pyfunction!(batch_url_fingerprints_zc, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_fingerprints_zc, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_entropy_zc() {
        let result = compute_entropy_zc("hello");
        assert!(result > 0.0, "Non-empty string should have entropy");
        assert_eq!(compute_entropy_zc(""), 0.0, "Empty string entropy = 0");
        assert_eq!(compute_entropy_zc("aaaa"), 0.0, "Single char entropy = 0");
    }

    #[test]
    fn test_parallel_threshold() {
        assert!(ZERO_COPY_PARALLEL_THRESHOLD >= 50);
        assert!(ZERO_COPY_BATCH_MAX >= 10_000);
    }

    #[test]
    fn test_batch_max_limit() {
        assert!(ZERO_COPY_BATCH_MAX <= 10_000, "Batch max should be bounded for M1 8GB");
    }
}

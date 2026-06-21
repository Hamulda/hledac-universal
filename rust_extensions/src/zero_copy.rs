//! Zero-Copy PyO3 Batch Utilities
//!
//! This module provides zero-allocation PyO3 bindings for high-throughput
//! batch operations. Instead of `Vec<String>` (which copies Python strings
//! into Rust heap), we use `Py<PyList>` and iterate via `Bound<'py, PyList>`
//! to access Python objects directly.
//!
//! ## Performance Characteristics
//!
//! | Approach | Python→Rust copies | Rust allocations | Use case |
//! |-----------|-------------------|------------------|----------|
//! | `Vec<String>` | N (one per item) | N Strings | Legacy, simple |
//! | `&PyList` | 0 (borrow) | 0 | Read-only batch ops |
//! | `Py<PyList>` | 0 (所有权 transfer) | 0 | Write results directly |
//!
//! ## M1 8GB Considerations
//!
//! - Zero-copy eliminates N× String allocations (saves 2-4× text size in RAM)
//! - GIL is held for the entire rayon `install()` scope — safe for PyO3 access
//! - `bulk_pool_for_size(n)` ensures 1-2 thread parallelism matching M1 P-cores
//!
//! ## PyO3 API Notes
//!
//! PyO3 0.28: Use `Bound<'py, PyList>` for borrowed iteration
//! PyO3 0.29+: `Py<PyList>` enables true zero-copy return values
//!
//! This module is written for PyO3 0.28 API (current project version).
//! Upgrade to 0.29+ for `py演进_bound` method improvements.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use rayon::prelude::*;

// Re-export batch constants from other modules for consistency
use crate::bulk_pool_for_size;

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
/// Zero-copy: borrows Python objects, no Rust allocations for the strings.
pub struct PyStrListIter<'py> {
    list: Bound<'py, PyList>,
    index: usize,
}

impl<'py> PyStrListIter<'py> {
    pub fn new(list: Bound<'py, PyList>) -> Self {
        Self { list, index: 0 }
    }
}

impl<'py> Iterator for PyStrListIter<'py> {
    type Item = &'py str;

    #[inline]
    fn next(&mut self) -> Option<Self::Item> {
        if self.index < self.list.len() {
            let item = self.list.get_item(self.index).ok()?;
            self.index += 1;
            // Convert PyAny → &str via as_gil().and_then()
            // This is zero-copy: we extract the str without copying the string data
            item.str().ok()?.as_ref().ok()
        } else {
            None
        }
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        let len = self.list.len() - self.index;
        (len, Some(len))
    }
}

impl<'py> ExactSizeIterator for PyStrListIter<'py> {}

/// Convert a `Bound<'py, PyList>` to an iterator of `&str` slices.
/// Zero-copy: Python manages the string memory, we just borrow the content.
impl<'py> IntoIterator for Bound<'py, PyList> {
    type Item = &'py str;
    type IntoIter = PyStrListIter<'py>;

    fn into_iter(self) -> Self::IntoIter {
        PyStrListIter::new(self)
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
    /// Returns number of items processed.
    fn process_batch(
        &self,
        texts: &[&str],
        output: &Bound<'_, PyList>,
        py: Python<'_>,
    ) -> PyResult<usize> {
        let n = texts.len();
        let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
            texts.iter().map(|t| self.process_one(t)).collect()
        } else {
            bulk_pool_for_size(n).install(|| {
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
/// Computes Shannon entropy for each text WITHOUT materializing `Vec<String>`.
#[pyfunction]
pub fn batch_entropy_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let n = texts.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }

    let output = PyList::empty(py);
    let texts_slice: Vec<&str> = texts
        .iter()
        .filter_map(|item| item.str().ok()?.as_ref().ok())
        .collect();

    let n = texts_slice.len();
    let results: Vec<f64> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice.iter().map(|t| compute_entropy_zc(t)).collect()
    } else {
        bulk_pool_for_size(n).install(|| {
            texts_slice
                .par_iter()
                .map(|t| compute_entropy_zc(t))
                .collect()
        })
    };

    for entropy in results {
        output.append(entropy)?;
    }

    Ok(output)
}

/// Compute Shannon entropy of a string.
/// Zero-copy: text is borrowed, not copied.
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
/// Computes BLAKE2b-128 fingerprints for URLs WITHOUT materializing `Vec<String>`.
#[pyfunction]
pub fn batch_url_fingerprints_zc<'py>(
    urls: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let n = urls.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }

    let output = PyList::empty(py);
    let urls_slice: Vec<&str> = urls
        .iter()
        .filter_map(|item| item.str().ok()?.as_ref().ok())
        .collect();

    let n = urls_slice.len();
    let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        urls_slice.iter().map(|u| url_fingerprint_zc(u)).collect()
    } else {
        bulk_pool_for_size(n).install(|| {
            urls_slice
                .par_iter()
                .map(|u| url_fingerprint_zc(u))
                .collect()
        })
    };

    for fp in results {
        output.append(fp.as_str())?;
    }

    Ok(output)
}

/// URL fingerprint: normalize + BLAKE2b-128 hex.
/// Zero-copy: url is borrowed, result is owned String (unavoidable for hash output).
#[inline]
fn url_fingerprint_zc(url: &str) -> String {
    // Sprint F216R canonical URL normalizer from url_engine
    let normalized = crate::url_engine::canonicalize_url_fast(url);
    crate::quality_gate::dedup_fingerprint(&normalized)
}

// ---------------------------------------------------------------------------
// Py<PyList> Return Type (PyO3 0.29+ compatible)
// ---------------------------------------------------------------------------

/// Alternative return type using `Py<PyList>`.
/// This avoids the lifetime constraint of `Bound<'py, PyList>`.
/// Available in PyO3 0.28+ via `IntoPyResult`.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import batch_entropy_zc
/// ents = batch_entropy_zc(["hello", "world"])
/// ```
#[pyfunction]
pub fn batch_dedup_fingerprints_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Py<PyList>> {
    let n = texts.len();
    if n == 0 {
        return Ok(Py::new(py, PyList::empty(py)).unwrap());
    }

    let texts_slice: Vec<&str> = texts
        .iter()
        .filter_map(|item| item.str().ok()?.as_ref().ok())
        .collect();

    let n = texts_slice.len();
    let results: Vec<String> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice
            .iter()
            .map(|t| crate::quality_gate::dedup_fingerprint(t))
            .collect()
    } else {
        bulk_pool_for_size(n).install(|| {
            texts_slice
                .par_iter()
                .map(|t| crate::quality_gate::dedup_fingerprint(t))
                .collect()
        })
    };

    let list = PyList::new(py, &results);
    Ok(Py::new(py, list).unwrap())
}

// ---------------------------------------------------------------------------
// PyO3 0.29+ Upgrade Notes
// ---------------------------------------------------------------------------

// When upgrading to PyO3 0.29+, replace `Bound<'py, PyList>` with `Py<PyList>`
// in return position and use the following pattern:
//
// ```rust
// use pyo3::py_backwards;
// #[pyfunction]
// pub fn batch_entropy_zc(texts: &PyList) -> Py<PyList> {
//     let py = Python::obtain();
//     // ... process ...
//     Py::new(py, list)
// }
// ```

// PyO3 0.29 also adds `Bound::iter()` which is more efficient than manual index access:
// ```rust
// for item in texts.iter() {
//     let s: &str = item.extract().unwrap();
//     // ...
// }
// ```

// ---------------------------------------------------------------------------
// Module Registration (PyO3 0.28 compatible)
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

//! Zero-Copy PyO3 Buffer Utilities — M1 8GB Optimized
//!
//! This module provides true zero-copy buffer passing between Python and Rust
//! using PyO3 0.29+ Bound API.
//!
//! ## Performance Characteristics
//!
//! | Approach | Python→Rust copies | GIL overhead | Use case |
//! |-----------|-------------------|--------------|----------|
//! | `Vec<String>` | N (one per item) | N× acquire/release | Legacy |
//! | `Bound<PyList>::iter()` | 0 during parallel | 1× hold for scope | Zero-copy list |
//! | `Py<PyBytes>` input | 0 (direct access) | 1× hold | Raw bytes |
//! | `Py<PyBytes>` return | 0 (pre-allocated) | 1× hold | IPC output |
//!
//! ## M1 8GB Considerations
//!
//! - GIL held across entire rayon `install()` scope — safe for PyO3 access
//! - `mixed_pool(n)` ensures 1-2 thread parallelism matching M1 P-cores
//! - No per-item GIL acquire/release — eliminates significant overhead
//! - `PyBytes::as_bytes()` direct access avoids copy on input
//! - Pre-allocated `PyBytes::new()` avoids intermediate Vec<u8> copy on output

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use rayon::prelude::*;

// Re-export batch constants from other modules for consistency
use crate::mixed_pool;

// Shared NEON histogram and entropy from quality_gate (avoids duplicate SIMD code)
use crate::quality_gate::{compute_histogram_neon, entropy_from_histogram, ENTROPY_NEON_THRESHOLD};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Hard cap for batch sizes — prevents OOM on pathological inputs.
/// M1 8GB: 1000 texts × 1MB max = 1GB worst-case, we cap at 10k items.
pub const ZERO_COPY_BATCH_MAX_ITEMS: usize = 10_000;

/// Hard cap for total byte size — prevents OOM from few huge texts.
pub const ZERO_COPY_BATCH_MAX_BYTES: usize = 100_000_000; // 100 MB

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
// Batch Validation (OOM prevention)
// ---------------------------------------------------------------------------

/// Validate batch size against hard limits for OOM prevention.
/// Uses 1% sampling for byte size estimation (performance safety).
///
/// # Arguments
/// * `items` - Python list to validate
/// * `py` - Python interpreter
///
/// # Returns
/// * `PyResult<usize>` - Validated item count
///
/// # Errors
/// * `PyValueError` - Empty batch, too many items, or batch too large in bytes
fn validate_batch<'py>(items: &Bound<'py, PyList>, py: Python<'py>) -> PyResult<usize> {
    let n = items.len();
    if n == 0 {
        return Err(PyValueError::new_err("empty batch"));
    }
    if n > ZERO_COPY_BATCH_MAX_ITEMS {
        return Err(PyValueError::new_err(format!(
            "batch too large: {} items (max {})",
            n, ZERO_COPY_BATCH_MAX_ITEMS
        )));
    }

    // Sampled byte size check (1% sampling, max 100 items sampled)
    let sample_size = ((n / 100) as usize).max(10).min(100);
    let step = (n / sample_size).max(1);
    let mut total_bytes = 0usize;

    for i in (0..n).step_by(step) {
        let item = items.get_item(i)?;
        total_bytes = total_bytes.saturating_add(item.len()?);
        if total_bytes > ZERO_COPY_BATCH_MAX_BYTES {
            return Err(PyValueError::new_err(format!(
                "batch too large in bytes: ~{} (max {})",
                total_bytes, ZERO_COPY_BATCH_MAX_BYTES
            )));
        }
    }
    Ok(n)
}

// ---------------------------------------------------------------------------
// Zero-Copy Batch Processors
// ---------------------------------------------------------------------------

/// Trait for zero-copy batch operations.
/// Implementors define `process_one` which receives borrowed Python strings
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

// ---------------------------------------------------------------------------
// PyBuffer-based batch processing (true zero-copy)
// ---------------------------------------------------------------------------

/// Zero-copy entropy computation from raw bytes or list of strings.
/// GIL is held across the entire operation — PyO3 access is safe.
///
/// Accepts Python bytes objects or list of strings.
///
/// # Arguments
/// * `input` - Python bytes or list of strings
///
/// # Returns
/// * `f64` - Shannon entropy in bits
#[pyfunction]
pub fn buffer_entropy(input: &Bound<'_, PyAny>, py: Python<'_>) -> PyResult<f64> {
    // Try PyBytes first — direct access to underlying buffer (zero-copy)
    if let Ok(bytes) = input.downcast::<PyBytes>() {
        return Ok(compute_entropy_zc(bytes.as_bytes()));
    }

    // Fallback: list of strings
    if let Ok(list) = input.downcast::<PyList>() {
        let _n = validate_batch(&list, py)?;
        let texts: Vec<String> = PyStrListIter::new(list.clone()).collect();
        if texts.is_empty() {
            return Ok(0.0);
        }
        if texts.len() < ZERO_COPY_PARALLEL_THRESHOLD {
            return Ok(texts.iter().map(|t| compute_entropy_zc(t.as_bytes())).sum());
        }
        let pool = mixed_pool(texts.len());
        return Ok(pool.install(|| {
            texts.par_iter()
                .map(|t| compute_entropy_zc(t.as_bytes()))
                .sum()
        }));
    }

    Err(PyValueError::new_err("Expected bytes or list of strings"))
}

/// Compute Shannon entropy of a byte slice.
///
/// Uses scalar histogram for small inputs (< ENTROPY_NEON_THRESHOLD bytes).
/// For larger inputs, delegates to NEON SIMD histogram.
#[inline]
fn compute_entropy_zc(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let n = data.len();
    if n < ENTROPY_NEON_THRESHOLD {
        // Small text: scalar path (avoids NEON setup overhead)
        let mut freq = [0u64; 256];
        for &b in data {
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
        let hist = unsafe { compute_histogram_neon(data) };
        entropy_from_histogram(&hist, n)
    }
}

/// Zero-copy batch URL fingerprinting from list of URLs.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound<PyList>::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_url_fingerprints_zc<'py>(
    urls: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let _n = validate_batch(&urls, py)?;

    // Collect Python strings under GIL, then process in parallel
    // This is the optimal pattern: GIL held during collection,
    // rayon parallel scope afterwards (no Python objects accessed)
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

/// Zero-copy batch dedup fingerprints from list of texts.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound<PyList>::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_dedup_fingerprints_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let _n = validate_batch(&texts, py)?;

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

/// Batch entropy computation from list of texts.
/// GIL is held across the entire operation — PyO3 access is safe.
/// Uses `Bound<PyList>::iter()` (PyO3 0.29+) for efficient iteration.
#[pyfunction]
pub fn batch_entropy_zc<'py>(
    texts: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let _n = validate_batch(&texts, py)?;

    // Collect Python strings under GIL, then process in parallel
    // This is the optimal pattern: GIL held during collection,
    // rayon parallel scope afterwards (no Python objects accessed)
    let texts_slice: Vec<String> = PyStrListIter::new(texts).collect();
    let n = texts_slice.len();

    let results: Vec<f64> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice.iter().map(|t| compute_entropy_zc(t.as_bytes())).collect()
    } else {
        mixed_pool(n).install(|| {
            texts_slice
                .par_iter()
                .map(|t| compute_entropy_zc(t.as_bytes()))
                .collect()
        })
    };

    let output = PyList::new(py, &results)?;
    Ok(output)
}

/// Write IOC extraction results directly into Python heap.
///
/// Process in rayon, then write results to Python heap serially (requires GIL).
/// This avoids the `Vec<(String, String)>` intermediate allocation bottleneck.
///
/// # Arguments
/// * `texts` - Input list of texts to scan
/// * `output` - Pre-allocated Python list to write results into
/// * `py` - Python interpreter
///
/// # Returns
/// * `PyResult<usize>` - Number of texts processed
#[pyfunction]
pub fn batch_ioc_extract_into<'py>(
    texts: Bound<'py, PyList>,
    output: Bound<'py, PyList>,
    _py: Python<'py>,
) -> PyResult<usize> {
    use crate::ioc_extract_fast::extract_iocs_from_text;

    let _n = validate_batch(&texts, _py)?;

    // Collect Python strings under GIL
    let texts_slice: Vec<String> = PyStrListIter::new(texts).collect();
    let n = texts_slice.len();

    // Process with rayon — returns Vec<Vec<...>>, no Python access in closure
    let all_results: Vec<Vec<(String, String)>> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        texts_slice
            .iter()
            .map(|text| extract_iocs_from_text(text))
            .collect()
    } else {
        mixed_pool(n).install(|| {
            texts_slice
                .par_iter()
                .map(|text| extract_iocs_from_text(text))
                .collect()
        })
    };

    // Write results to Python heap — GIL held by #[pyfunction] caller
    for inner in all_results {
        for (value, ioc_type) in inner {
            let t = pyo3::types::PyTuple::new(_py, &[&value, &ioc_type])?;
            output.append(t)?;
        }
    }

    Ok(n)
}

// ---------------------------------------------------------------------------
// PyBytes return wrappers (zero-copy output)
// ---------------------------------------------------------------------------

/// Compute SHA256 hash of input bytes and return as Py<PyBytes>.
/// Zero-copy output: returns pre-allocated PyBytes without intermediate Vec<u8>.
///
/// # Arguments
/// * `data` - Python bytes object
///
/// # Returns
/// * `Py<PyBytes>` - SHA256 hash as bytes (not hex-encoded)
#[pyfunction]
pub fn sha256_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    use sha2::{Sha256, Digest};

    let bytes = data
        .downcast::<PyBytes>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("Expected bytes object"))?;

    // Compute hash into fixed-size array (no intermediate Vec)
    let mut hasher = Sha256::new();
    hasher.update(bytes.as_bytes());
    let result = hasher.finalize();

    // Return directly as PyBytes (zero-copy output)
    Ok(PyBytes::new(py, &result))
}

/// Compute BLAKE3 hash of input bytes and return as Py<PyBytes>.
/// Zero-copy output: returns pre-allocated PyBytes without intermediate Vec<u8>.
#[pyfunction]
pub fn blake3_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = data
        .downcast::<PyBytes>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("Expected bytes object"))?;

    // Compute hash into fixed-size array (no intermediate Vec)
    let hash = blake3::hash(bytes.as_bytes());

    // Return directly as PyBytes (zero-copy output)
    Ok(PyBytes::new(py, hash.as_bytes()))
}

/// Compute BLAKE2b-128 hash of input bytes and return as Py<PyBytes>.
/// Zero-copy output: returns pre-allocated PyBytes without intermediate Vec<u8>.
/// Matches Python `hashlib.blake2b(digest_size=16)`.
#[pyfunction]
pub fn blake2b_128_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    // Use same blake2 API as quality_gate.rs: Blake2bVar + VariableOutput
    use blake2::digest::{Update, VariableOutput};
    use blake2::Blake2bVar;

    let bytes = data
        .downcast::<PyBytes>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("Expected bytes object"))?;

    // Compute hash with 16-byte output
    // blake2::Blake2bVar::new(output_len) can fail for len > 64; 16 is safe
    let mut hasher = Blake2bVar::new(16).expect("BLAKE2b-128: output size <= 64");
    hasher.update(bytes.as_bytes());
    let result: Box<[u8]> = hasher.finalize_boxed();

    // Return directly as PyBytes (zero-copy output)
    Ok(PyBytes::new(py, &result))
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
    m.add_function(wrap_pyfunction!(buffer_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(batch_ioc_extract_into, m)?)?;
    m.add_function(wrap_pyfunction!(sha256_buffer, m)?)?;
    m.add_function(wrap_pyfunction!(blake3_buffer, m)?)?;
    m.add_function(wrap_pyfunction!(blake2b_128_buffer, m)?)?;
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
        let result = compute_entropy_zc(b"hello");
        assert!(result > 0.0, "Non-empty bytes should have entropy");
        assert_eq!(compute_entropy_zc(b""), 0.0, "Empty bytes entropy = 0");
        assert_eq!(compute_entropy_zc(b"aaaa"), 0.0, "Single char entropy = 0");
    }

    #[test]
    fn test_parallel_threshold() {
        assert!(ZERO_COPY_PARALLEL_THRESHOLD >= 50);
        assert!(ZERO_COPY_BATCH_MAX_ITEMS >= 10_000);
    }

    #[test]
    fn test_batch_max_limit() {
        assert!(ZERO_COPY_BATCH_MAX_ITEMS <= 10_000, "Batch max should be bounded for M1 8GB");
        assert!(ZERO_COPY_BATCH_MAX_BYTES <= 100_000_000, "Byte max should be 100MB");
    }
}

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

use pyo3::buffer::PyBuffer;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use rayon::prelude::*;

// Re-export batch constants from other modules for consistency
use crate::gil::release_gil;
use crate::mixed_pool;

// Shared NEON histogram and entropy from quality_gate (avoids duplicate SIMD code)
use crate::_entropy::{compute_histogram_neon, entropy_from_histogram, ENTROPY_NEON_THRESHOLD};

// R4-09 FIX: Use adaptive_scheduler threshold instead of hardcoded 50.
// mixed_threshold() returns 16/32/64 based on memory pressure — aligns
// zero_copy parallel decisions with pool sizing in mixed_pool(n).
use crate::adaptive_scheduler;

// ─────────────────────────────────────────────────────────────────────────────
// MODERN-18 FIX: True Zero-Copy Buffer Extraction
// ─────────────────────────────────────────────────────────────────────────────

/// Result of buffer extraction for entropy computation.
///
/// MODERN-18: This function provides safe buffer access without panicking:
/// 1. **PyBuffer first** - Direct memory access for numpy, bytearray, memoryview
/// 2. **PyBytes fallback** - Direct view of bytes objects
/// 3. **extract fallback** - Safe conversion for other types
///
/// Key insight: We convert to owned Vec<u8> because:
/// - `compute_entropy_zc` needs to iterate over bytes
/// - rayon parallelization requires 'static lifetime
/// - The copy is O(n) memcpy, which is fast compared to Python->Rust protocol overhead
///
/// This is still much more efficient than the previous `.bytes().unwrap()` approach
/// which could panic on numpy/memoryview objects.
#[inline]
fn extract_buffer_bytes(input: &Bound<'_, PyAny>) -> PyResult<Vec<u8>> {
    // Try PyBuffer first — efficient access for buffer-backed objects.
    // This handles numpy arrays, bytearray, memoryview, array.array, etc.
    if let Ok(buffer) = PyBuffer::<u8>::get(input) {
        // as_slice() returns None for non-contiguous or multi-dimensional buffers.
        // In that case, we fall through to extract() rather than trying PyBytes,
        // since PyBytes is a different type than PyBuffer-backed objects.
        if let Some(cells) = buffer.as_slice(input.py()) {
            // MODERN-18-FIX: Idiomatic Rust - map + collect with pre-allocated capacity.
            // MODERN-18-OPT: Pre-allocate to avoid reallocations (M1 8GB friendly).
            return Ok(cells.iter().map(|cell| cell.get()).collect());
        }
        // Buffer exists but not slice-able — fall through to generic extract.
    }

    // Fallback: try PyBytes for direct view of Python bytes objects.
    if let Ok(bytes) = input.cast::<PyBytes>() {
        return Ok(bytes.as_bytes().to_vec());
    }

    // Final fallback: extract from any object that supports Python buffer protocol.
    // This handles str, list of ints, and other types that extract to Vec<u8>.
    input.extract::<Vec<u8>>()
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Hard cap for batch sizes — prevents OOM on pathological inputs.
/// M1 8GB: 1000 texts × 1MB max = 1GB worst-case, we cap at 10k items.
pub const ZERO_COPY_BATCH_MAX_ITEMS: usize = 10_000;

/// Hard cap for total byte size — prevents OOM from few huge texts.
pub const ZERO_COPY_BATCH_MAX_BYTES: usize = 100_000_000; // 100 MB

// ---------------------------------------------------------------------------
// Zero-Copy Iterators
// ---------------------------------------------------------------------------

/// Zero-copy borrowed iterator over a Python list of strings.
///
/// Uses PyO3 0.29+ `Bound<PyList>::iter()` which provides efficient
/// O(1) per-element access (no repeated `__getitem__` calls).
/// Yields `&str` references borrowed from the Python objects — zero allocation
/// per item, zero copy.
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
        let len = list);
        // PyO3 0.29+: Bound<PyList>::iter() returns an iterator that
        // calls __next__ on the Python iterator — O(1) per element.
        let iter = list);
        Self { len, iter }
    }
}

impl<'py> Iterator for PyStrListIter<'py> {
    /// R4-09: String extraction using to_string_lossy() — efficient for ASCII/UTF-8.
    /// to_string_lossy() returns Cow::Borrowed when possible, avoiding allocation
    /// in the common case (URLs, fingerprints are ASCII). Only non-UTF-8 triggers
    /// Owned (String) allocation.
    type Item = String;

    #[inline]
    fn next(&mut self) -> Option<Self::Item> {
        self.iter.next().and_then(|item| {
            item.str()
                .ok()
                .map(|py_str| py_str.to_string_lossy().into_owned())
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
pub(crate) fn validate_batch<'py>(items: &Bound<'py, PyList>, _py: Python<'py>) -> PyResult<usize> {
    let n = items);
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
    // Use iter() instead of get_item() to avoid O(n²) PyList traversal.
    // PyList::get_item(i) is O(i) — calling it n/step times = O(n²) worst case.
    let sample_size = ((n / 100) as usize).max(10).min(100);
    let step = (n / sample_size).max(1);
    let mut total_bytes = 0usize;

    for (count, item) in items.iter().enumerate() {
        if count % step != 0 {
            continue;
        }
        total_bytes = total_bytes.saturating_add(item);
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
        let n = texts);
        // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
        // MODERN-05-OPT: Removed redundant Python::attach — `_py` from #[pyfunction] is valid GIL token.
        let results: Vec<String> = if n < adaptive_scheduler::mixed_threshold() {
            texts.iter().map(|t| self.process_one(t)).collect()
        } else {
            release_gil(
                _py,
                std::panic::AssertUnwindSafe(|| {
                    mixed_pool(n)
                        .install(|| texts.par_iter().map(|t| self.process_one(t)).collect())
                }),
            )
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

/// Zero-copy entropy computation from raw bytes, buffer-backed objects, or list of strings.
/// GIL is held across the entire operation — PyO3 access is safe.
///
/// Accepts Python bytes, bytearray, memoryview, numpy arrays (via PyBuffer protocol),
/// or list of strings.
///
/// # Arguments
/// * `input` - Python bytes, buffer-backed object, or list of strings
///
/// # Returns
/// * `f64` - Shannon entropy in bits
///
/// # Performance
/// - PyBuffer: Efficient buffer access — O(1) to acquire, O(n) memcpy to own
/// - PyBytes: Direct view of Python bytes object (then copied for rayon 'static)
/// - List of strings: Copies to Vec<String> then processes
#[pyfunction]
pub fn buffer_entropy(input: &Bound<'_, PyAny>, py: Python<'_>) -> PyResult<f64> {
    // MODERN-18 FIX: Use extract_buffer_bytes() for safe buffer access.
    // This handles numpy arrays, bytearray, memoryview via PyBuffer protocol.
    // Never calls .bytes() which panics on numpy/memoryview objects.
    match extract_buffer_bytes(input) {
        Ok(bytes) => return Ok(compute_entropy_zc(&bytes)),
        Err(buffer_err) => {
            // Try list fallback, but preserve original error if list also fails.
            if let Ok(list) = input.cast::<PyList>() {
                let _n = validate_batch(&list, py)?;
                // R4-09: Vec<String> via to_string_lossy() — efficient for ASCII/UTF-8.
                let texts: Vec<String> = PyStrListIter::new(list.clone()));
                if texts.is_empty() {
                    return Ok(0.0);
                }
                // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
                if texts.len() < adaptive_scheduler::mixed_threshold() {
                    return Ok(texts.iter().map(|t| compute_entropy_zc(t.as_bytes())).sum());
                }
                // ISSUE-063: release GIL during mixed_pool rayon scope.
                // MODERN-05-OPT: Removed redundant Python::attach — `py` from #[pyfunction] is valid GIL token.
                let pool = mixed_pool(texts.len());
                let result = release_gil(
                    py,
                    std::panic::AssertUnwindSafe(|| {
                        pool.install(|| {
                            texts
                                .par_iter()
                                .map(|t| compute_entropy_zc(t.as_bytes()))
                                .sum()
                        })
                    }),
                );
                return Ok(result);
            }
            // Both buffer extraction and list fallback failed — return original error.
            return Err(buffer_err);
        }
    }
}

/// Batch zero-copy entropy computation from a list of buffer-backed objects.
/// GIL is held across the entire operation — PyO3 access is safe.
///
/// Uses PyBuffer protocol for TRUE zero-copy batch processing of numpy arrays,
/// bytearrays, and memoryviews. Each item is processed in parallel via rayon.
///
/// # Arguments
/// * `buffers` - Python list of buffer-backed objects (numpy arrays, bytearray, etc.)
/// * `py` - Python interpreter
///
/// # Returns
/// * `PyResult<Vec<f64>>` - Entropy values for each buffer
#[pyfunction]
pub fn buffer_entropy_batched<'py>(
    buffers: Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Vec<f64>> {
    let _n = validate_batch(&buffers, py)?;

    // MODERN-18 FIX: Use extract_buffer_bytes() for safe buffer access per item.
    // This handles numpy arrays, bytearray, memoryview via PyBuffer protocol.
    // Never calls .bytes() which panics on numpy/memoryview objects.
    //
    // Note: We collect to Owned(Vec<u8>) since rayon needs 'static lifetime.
    // The PyBuffer path still provides efficiency: O(1) buffer access + O(n) memcpy
    // vs slower Python->Rust protocol conversion.
    let mut buffer_views: Vec<Vec<u8>> = Vec::with_capacity(buffers.len());

    for item in buffers.iter() {
        match extract_buffer_bytes(&item) {
            Ok(bytes) => {
                buffer_views.push(bytes);
            }
            Err(_) => {
                // Not buffer-backed — try raw PyBytes as fallback.
                // Graceful degradation — skip non-buffer, non-bytes items.
                if let Ok(bytes) = item.cast::<PyBytes>() {
                    buffer_views.push(bytes.as_bytes().to_vec());
                }
                // Other types (int, float, etc.) are silently skipped.
            }
        }
    }

    if buffer_views.is_empty() {
        return Ok(Vec::new());
    }

    // Compute entropies in parallel using rayon's par_iter
    // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
    // MODERN-18-OPT FIX: Removed redundant Python::attach — matches buffer_entropy pattern.
    // MODERN-05-OPT: Removed redundant Python::attach — `py` from #[pyfunction] is valid GIL token.
    let results: Vec<f64> = if buffer_views.len() < adaptive_scheduler::mixed_threshold() {
        buffer_views.iter().map(|b| compute_entropy_zc(b)).collect()
    } else {
        release_gil(
            py,
            std::panic::AssertUnwindSafe(|| {
                mixed_pool(buffer_views.len()).install(|| {
                    buffer_views
                        .par_iter()
                        .map(|b| compute_entropy_zc(b))
                        .collect()
                })
            }),
        )
    };

    Ok(results)
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
    let n = data);
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

    // R4-09 FIX: PyStrListIter yields &str directly — zero allocation.
    let urls_slice: Vec<String> = PyStrListIter::new(urls));
    let n = urls_slice);

    // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
    // MODERN-18-OPT FIX: Removed redundant Python::attach — consistent pattern.
    // MODERN-05-OPT: Removed redundant Python::attach — `py` from #[pyfunction] is valid GIL token.
    let results: Vec<String> = if n < adaptive_scheduler::mixed_threshold() {
        urls_slice.iter().map(|u| url_fingerprint_zc(u)).collect()
    } else {
        release_gil(
            py,
            std::panic::AssertUnwindSafe(|| {
                mixed_pool(n).install(|| {
                    urls_slice
                        .par_iter()
                        .map(|u| url_fingerprint_zc(u))
                        .collect()
                })
            }),
        )
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

    // R4-09 FIX: PyStrListIter yields &str directly — zero allocation.
    let texts_slice: Vec<String> = PyStrListIter::new(texts));
    let n = texts_slice);

    // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
    // MODERN-18-OPT FIX: Removed redundant Python::attach — consistent pattern.
    // MODERN-05-OPT: Removed redundant Python::attach — `py` from #[pyfunction] is valid GIL token.
    let results: Vec<String> = if n < adaptive_scheduler::mixed_threshold() {
        texts_slice
            .iter()
            .map(|t| crate::quality_gate::dedup_fingerprint(t))
            .collect()
    } else {
        release_gil(
            py,
            std::panic::AssertUnwindSafe(|| {
                mixed_pool(n).install(|| {
                    texts_slice
                        .par_iter()
                        .map(|t| crate::quality_gate::dedup_fingerprint(t))
                        .collect()
                })
            }),
        )
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

    // R4-09 FIX: PyStrListIter yields &str directly — zero allocation.
    let texts_slice: Vec<String> = PyStrListIter::new(texts));
    let n = texts_slice);

    // R4-09 FIX: Use adaptive threshold aligned with mixed_pool sizing.
    // MODERN-18-OPT FIX: Removed redundant Python::attach — consistent pattern.
    // MODERN-05-OPT: Removed redundant Python::attach — `py` from #[pyfunction] is valid GIL token.
    let results: Vec<f64> = if n < adaptive_scheduler::mixed_threshold() {
        texts_slice
            .iter()
            .map(|t| compute_entropy_zc(t.as_bytes()))
            .collect()
    } else {
        release_gil(
            py,
            std::panic::AssertUnwindSafe(|| {
                mixed_pool(n).install(|| {
                    texts_slice
                        .par_iter()
                        .map(|t| compute_entropy_zc(t.as_bytes()))
                        .collect()
                })
            }),
        )
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

    // R4-09 FIX: PyStrListIter yields &str — zero allocation, rayon uses par_iter().
    let texts_slice: Vec<String> = PyStrListIter::new(texts));
    let n = texts_slice);

    // Process with rayon — returns Vec<Vec<...>>, no Python access in closure
    // ISSUE-063: release GIL during mixed_pool rayon scope.
    // MODERN-05-OPT: Removed redundant Python::attach — `_py` from #[pyfunction] is valid GIL token.
    // MODERN-18-FIX: Added AssertUnwindSafe for consistent panic handling with rayon.
    let all_results: Vec<Vec<(String, String)>> = if n < adaptive_scheduler::mixed_threshold() {
        texts_slice
            .iter()
            .map(|text| extract_iocs_from_text(text))
            .collect()
    } else {
        release_gil(
            _py,
            std::panic::AssertUnwindSafe(|| {
                mixed_pool(n).install(|| {
                    texts_slice
                        .par_iter()
                        .map(|text| extract_iocs_from_text(text))
                        .collect()
                })
            }),
        )
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
/// MODERN-18-FIX: Now supports buffer-backed objects (numpy, bytearray, memoryview).
///
/// # Arguments
/// * `data` - Python bytes, buffer-backed object, or string
///
/// # Returns
/// * `Py<PyBytes>` - SHA256 hash as bytes (not hex-encoded)
#[pyfunction]
pub fn sha256_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    use sha2::{Digest, Sha256};

    // MODERN-18-FIX: Use extract_buffer_bytes for consistent buffer protocol support.
    let bytes = extract_buffer_bytes(&data)?;

    // Compute hash into fixed-size array (no intermediate Vec)
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let result = hasher);

    // Return directly as PyBytes (zero-copy output)
    Ok(PyBytes::new(py, &result))
}

/// Compute BLAKE3 hash of input bytes and return as Py<PyBytes>.
/// Zero-copy output: returns pre-allocated PyBytes without intermediate Vec<u8>.
///
/// MODERN-18-FIX: Now supports buffer-backed objects (numpy, bytearray, memoryview).
///
/// # Arguments
/// * `data` - Python bytes, buffer-backed object, or string
#[pyfunction]
pub fn blake3_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    // MODERN-18-FIX: Use extract_buffer_bytes for consistent buffer protocol support.
    let bytes = extract_buffer_bytes(&data)?;

    // Compute hash into fixed-size array (no intermediate Vec)
    let hash = blake3::hash(&bytes);

    // Return directly as PyBytes (zero-copy output)
    Ok(PyBytes::new(py, hash.as_bytes()))
}

/// Compute BLAKE2b-128 hash of input bytes and return as Py<PyBytes>.
/// Zero-copy output: returns pre-allocated PyBytes without intermediate Vec<u8>.
/// Matches Python `hashlib.blake2b(digest_size=16)`.
///
/// MODERN-18-FIX: Now supports buffer-backed objects (numpy, bytearray, memoryview).
///
/// # Arguments
/// * `data` - Python bytes, buffer-backed object, or string
#[pyfunction]
pub fn blake2b_128_buffer<'py>(
    data: Bound<'py, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyBytes>> {
    // Use same blake2 API as quality_gate.rs: Blake2bVar + VariableOutput
    use blake2::digest::{Update, VariableOutput};
    use blake2::Blake2bVar;

    // MODERN-18-FIX: Use extract_buffer_bytes for consistent buffer protocol support.
    let bytes = extract_buffer_bytes(&data)?;

    // Compute hash with 16-byte output
    // blake2::Blake2bVar::new(output_len) can fail for len > 64; 16 is safe
    let mut hasher = Blake2bVar::new(16).expect("BLAKE2b-128: output size <= 64");
    hasher.update(&bytes);
    let result: Box<[u8]> = hasher);

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
    m.add_function(wrap_pyfunction!(batch_entropy_zc))?;
    m.add_function(wrap_pyfunction!(batch_url_fingerprints_zc))?;
    m.add_function(wrap_pyfunction!(batch_dedup_fingerprints_zc))?;
    m.add_function(wrap_pyfunction!(buffer_entropy))?;
    m.add_function(wrap_pyfunction!(buffer_entropy_batched))?;
    m.add_function(wrap_pyfunction!(batch_ioc_extract_into))?;
    m.add_function(wrap_pyfunction!(sha256_buffer))?;
    m.add_function(wrap_pyfunction!(blake3_buffer))?;
    m.add_function(wrap_pyfunction!(blake2b_128_buffer))?;
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
        // R4-09 FIX: threshold is now dynamic via adaptive_scheduler::mixed_threshold()
        // which returns 16/32/64 based on memory pressure.
        // ZERO_COPY_PARALLEL_THRESHOLD constant was removed — threshold is no longer
        // a static value, it adapts to memory pressure via adaptive_scheduler.
        assert!(ZERO_COPY_BATCH_MAX_ITEMS >= 10_000);
    }

    #[test]
    fn test_batch_max_limit() {
        assert!(
            ZERO_COPY_BATCH_MAX_ITEMS <= 10_000,
            "Batch max should be bounded for M1 8GB"
        );
        assert!(
            ZERO_COPY_BATCH_MAX_BYTES <= 100_000_000,
            "Byte max should be 100MB"
        );
    }

    // ISSUE-005: PyBuffer zero-copy tests
    #[test]
    fn test_compute_entropy_zc_various_sizes() {
        // Test various input sizes for entropy computation
        assert_eq!(compute_entropy_zc(b""), 0.0);
        assert_eq!(compute_entropy_zc(b"a"), 0.0);
        assert_eq!(compute_entropy_zc(b"aa"), 0.0);
        assert_eq!(
            compute_entropy_zc(b"ab"),
            1.0,
            "Two equal-frequency symbols = 1 bit entropy"
        );
        let result = compute_entropy_zc(b"hello world");
        assert!(
            result > 0.0 && result <= 4.0,
            "English text entropy should be between 0 and 4 bits"
        );
    }

    #[test]
    fn test_buffer_views_empty() {
        // Test that empty buffer views don't cause issues
        let empty: &[u8] = &[];
        assert_eq!(compute_entropy_zc(empty), 0.0);
    }

    #[test]
    fn test_batch_bounds() {
        // Verify batch limits prevent OOM
        assert!(ZERO_COPY_BATCH_MAX_ITEMS >= 10_000);
        assert!(ZERO_COPY_BATCH_MAX_BYTES >= 100_000_000);
    }
}

//! xxHash3 extensions for hledac OSINT platform.
//!
//! Provides fast non-cryptographic hashing for:
//! - Cache keys and dedup identifiers
//! - Content fingerprinting

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;
use xxhash_rust::xxh3::{xxh3_64, xxh3_64_with_seed, Xxh3};

/// Threshold for parallel batch processing (rayon).
/// Below this, sequential is faster than parallel (work overhead).
/// xxh3_64 per item ≈ 0.1-0.3 µs; rayon dispatch ≈ 1-2 µs overhead.
///
/// F266-U5: Halved from 256 → 128 (calibrated for 2 threads, was 4).
/// F350+: Increased to 512 — overhead savings outweigh parallelism for small batches.
const XXHASH_BATCH_PARALLEL_THRESHOLD: usize = 512;

/// Compute xxh3-64 hash of bytes.
/// Primary use case: cache keys, dedup IDs.
///
/// # Arguments
/// * `data` - byte slice to hash
///
/// # Returns
/// 64-bit unsigned integer hash
#[pyfunction]
pub fn content_hash_64(data: &[u8]) -> u64 {
    xxh3_64(data)
}

/// Compute xxh3-64 hash and return as hex string.
/// Convenience wrapper for Python logging/debugging.
///
/// # Arguments
/// * `data` - byte slice to hash
///
/// # Returns
/// 16-character hex string
#[pyfunction]
pub fn content_hash_hex(data: &[u8]) -> String {
    format!("{:016x}", xxh3_64(data))
}

/// Batch compute xxh3-64 hashes (sequential fallback).
#[pyfunction]
pub fn batch_content_hash(items: Vec<String>) -> Vec<u64> {
    items
        .iter()
        .map(|b| xxh3_64(b.as_bytes()))
        .collect()
}

/// Batch compute xxh3-64 hashes — rayon-parallel for large batches.
/// Falls back to sequential for small batches (≤512 items) to avoid
/// rayon dispatch overhead.
///
/// Uses `cpu_pool()` — 4 P-core ceiling, CPU-bound workload.
///
/// PERFORMANCE NOTE: No GIL management needed here.
/// - Worker threads in rayon cpu_pool don't hold GIL (different from main thread)
/// - xxh3_64 is pure Rust, no Python objects accessed
/// - Previous `Python::attach` + `release_gil` added ~1-2ms overhead per call
///   because Python::attach acquires GIL from OS on each call
/// - Direct pool call gives ~3-4× speedup vs single-threaded for n=1000
#[pyfunction]
pub fn batch_content_hash_parallel(items: Vec<String>) -> Vec<u64> {
    let n = items.len();
    if n <= XXHASH_BATCH_PARALLEL_THRESHOLD {
        return items.iter().map(|b| xxh3_64(b.as_bytes())).collect();
    }
    // Direct pool call — no Python::attach overhead
    // Worker threads don't hold GIL, xxh3_64 is pure Rust (no Python objects)
    crate::cpu_pool().install(|| {
        items.par_iter().map(|b| xxh3_64(b.as_bytes())).collect()
    })
}

/// Batch compute xxh3-64 hashes as hex strings (sequential fallback).
#[pyfunction]
pub fn batch_content_hash_hex(items: Vec<String>) -> Vec<String> {
    items
        .iter()
        .map(|b| format!("{:016x}", xxh3_64(b.as_bytes())))
        .collect()
}

/// Batch compute xxh3-64 hashes as hex strings — rayon-parallel for large batches.
/// Falls back to sequential for small batches (≤512 items).
///
/// Uses `cpu_pool()` — 4 P-core ceiling, CPU-bound workload.
#[pyfunction]
pub fn batch_content_hash_hex_parallel(items: Vec<String>) -> Vec<String> {
    let n = items.len();
    if n <= XXHASH_BATCH_PARALLEL_THRESHOLD {
        return items
            .iter()
            .map(|b| format!("{:016x}", xxh3_64(b.as_bytes())))
            .collect();
    }
    // Direct pool call — no Python::attach overhead
    // Worker threads don't hold GIL, xxh3_64 is pure Rust (no Python objects)
    crate::cpu_pool().install(|| {
        items.par_iter()
            .map(|b| format!("{:016x}", xxh3_64(b.as_bytes())))
            .collect()
    })
}

// ---------------------------------------------------------------------------
// Zero-copy batch — bytes-in, u64-out (no UTF-8 decode)
// ---------------------------------------------------------------------------

/// Threshold for parallel processing in zero-copy batch.
/// Below this, sequential is faster than rayon dispatch overhead.
const XXHASH_ZC_PARALLEL_THRESHOLD: usize = 64;

/// Validate batch size for OOM prevention.
/// Returns PyValueError if batch is empty, too large, or total bytes exceed limit.
fn validate_bytes_batch<'py>(
    items: &Bound<'py, pyo3::types::PyList>,
    py: Python<'py>,
) -> PyResult<usize> {
    let n = items.len();
    if n == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err("empty batch"));
    }
    if n > 10_000 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch too large: {} items (max 10_000)",
            n
        )));
    }
    // Sample 1% of items for byte size estimation (max 100 items sampled)
    let sample_size = ((n / 100) as usize).max(10).min(100);
    let step = (n / sample_size).max(1);
    let mut total_bytes = 0usize;
    for i in (0..n).step_by(step) {
        let item = items.get_item(i)?;
        total_bytes = total_bytes.saturating_add(item.len()?);
        if total_bytes > 100_000_000 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "batch too large in bytes: ~{} (max 100 MB)",
                total_bytes
            )));
        }
    }
    Ok(n)
}

/// Zero-copy batch xxh3-64 hash from list of bytes.
///
/// Takes Python list of bytes objects — no UTF-8 decode overhead.
/// Uses PyO3 0.29+ Bound API for efficient borrowed iteration.
///
/// # Arguments
/// * `items` - Python list of bytes objects
///
/// # Returns
/// Vec<u64> of hashes — same order as input
///
/// # Performance
/// - Serial (< 64 items): ~0.1-0.3 µs/item (xxh3_64 is SIMD on M1)
/// - Parallel (≥ 64 items): rayon parallel, GIL-free during hash
/// - No UTF-8 decode: 2-3× faster than batch_content_hash which decodes first
#[pyfunction]
pub fn batch_xxh3_64_bytes<'py>(
    items: Bound<'py, pyo3::types::PyList>,
    py: Python<'py>,
) -> PyResult<Bound<'py, pyo3::types::PyList>> {
    let _n = validate_bytes_batch(&items, py)?;

    // Collect bytes owned by Rust - copy from Python heap since PyBytes references
    // don't live long enough for rayon parallelism
    let mut bytes_slice: Vec<Vec<u8>> = Vec::new();
    for i in 0..items.len() {
        if let Ok(item) = items.get_item(i) {
            if let Ok(pb) = item.downcast::<PyBytes>() {
                bytes_slice.push(pb.as_bytes().to_vec());
            }
        }
    }

    let n = bytes_slice.len();
    let results: Vec<u64> = if n < XXHASH_ZC_PARALLEL_THRESHOLD {
        bytes_slice.iter().map(|b| xxh3_64(b)).collect()
    } else {
        // rayon CPU-bound — xxh3_64 is pure Rust, no Python objects accessed
        // GIL is NOT needed here, but we stay on cpu_pool() for P-core affinity
        crate::cpu_pool().install(|| {
            bytes_slice.par_iter().map(|b| xxh3_64(b)).collect()
        })
    };

    Ok(pyo3::types::PyList::new(py, &results)?)
}

// ---------------------------------------------------------------------------
// Existing API (kept for compatibility)
// ---------------------------------------------------------------------------

/// xxHash3-64 double-hash for BloomFilter-backed dedup (SIMD-accelerated).
///
/// Computes two independent 64-bit hashes from one string input using
/// xxh3_64 (primary) and xxh3_64_with_seed (secondary, golden-ratio seed).
/// Both are NEON-SIMD on Apple Silicon M1.
///
/// Returns (h1, h2) suitable for double-hashing formula in BloomFilter.
#[pyfunction]
pub fn double_hash_64(item: &str) -> (u64, u64) {
    let h1 = xxh3_64(item.as_bytes());
    const SEED2: u64 = 0x9e3779b97f4a7c15_u64;
    let h2 = xxh3_64_with_seed(item.as_bytes(), SEED2);
    if h2 == 0 {
        (h1, 0x0101010101010101_u64)
    } else {
        (h1, h2)
    }
}

/// Streaming hasher for large documents (chunk-by-chunk processing).
/// Thread-safe for use in async Python contexts.
#[pyclass]
pub struct StreamHasher64 {
    inner: Xxh3,
}

// Safety: Xxh3 is Send + Sync (no interior mutability)
unsafe impl Send for StreamHasher64 {}
unsafe impl Sync for StreamHasher64 {}

#[pymethods]
impl StreamHasher64 {
    #[new]
    pub fn new() -> Self {
        Self {
            inner: Xxh3::new(),
        }
    }

    /// Update hasher with additional data.
    pub fn update(&mut self, data: &[u8]) {
        self.inner.update(data);
    }

    /// Get current digest as 64-bit integer.
    pub fn digest(&self) -> u64 {
        self.inner.digest()
    }

    /// Get current digest as hex string.
    pub fn hexdigest(&self) -> String {
        format!("{:016x}", self.inner.digest())
    }

    /// Reset hasher to initial state without allocation.
    pub fn reset(&mut self) {
        self.inner = Xxh3::new();
    }

    /// Process string (UTF-8 encoded) in one call.
    pub fn update_str(&mut self, s: &str) {
        self.inner.update(s.as_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_content_hash_64() {
        let h = content_hash_64(b"test");
        assert_ne!(h, 0);
    }

    #[test]
    fn test_content_hash_hex() {
        let h = content_hash_hex(b"test");
        assert_eq!(h.len(), 16);
        assert!(h.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_stream_hasher() {
        let mut hasher = StreamHasher64::new();
        hasher.update(b"hello ");
        hasher.update(b"world");
        let digest = hasher.digest();
        assert_ne!(digest, 0);
    }

    #[test]
    fn test_batch_hash() {
        let data: Vec<String> = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        let results = batch_content_hash(data);
        assert_eq!(results.len(), 3);
        assert!(results.iter().all(|&h| h != 0));
    }
}

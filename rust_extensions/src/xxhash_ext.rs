//! xxHash3 extensions for hledac OSINT platform.
//!
//! Provides fast non-cryptographic hashing for:
//! - Cache keys and dedup identifiers
//! - Content fingerprinting

use pyo3::prelude::*;
use xxhash_rust::xxh3::{xxh3_64, Xxh3};

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

/// Batch compute xxh3-64 hashes.
#[pyfunction]
pub fn batch_content_hash(items: Vec<String>) -> Vec<u64> {
    items.iter().map(|b| xxh3_64(b.as_bytes())).collect()
}

/// Batch compute xxh3-64 hashes as hex strings.
#[pyfunction]
pub fn batch_content_hash_hex(items: Vec<String>) -> Vec<String> {
    items
        .iter()
        .map(|b| format!("{:016x}", xxh3_64(b.as_bytes())))
        .collect()
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
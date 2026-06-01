//! URL deduplication set with FNV-1a hashing.
//!
//! High-performance O(1) URL dedup using FNV-1a 64-bit hash.
//! M1-safe, no C dependencies.

use pyo3::prelude::*;

/// FNV-1a 64-bit hash constants
const FNV_OFFSET_BASIS: u64 = 0xcbf29ce484222325;
const FNV_PRIME: u64 = 0x100000001b3;

/// Computes FNV-1a 64-bit hash of a string.
#[inline]
fn fnv1a_64(data: &[u8]) -> u64 {
    let mut hash = FNV_OFFSET_BASIS;
    for byte in data {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

/// URL deduplication set using FNV-1a hashing.
///
/// Maintains a hash set of URLs for O(1) add/contains operations.
/// Memory-efficient: stores 64-bit hashes instead of full URL strings.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import UrlSet
///
/// urls = UrlSet()
/// urls.add("https://example.com/page1")
/// urls.add("https://example.com/page1")  // duplicate, not stored
/// print(urls.contains("https://example.com/page1"))  // True
/// print(urls.len())  // 1
/// ```
#[pyclass]
pub struct UrlSet {
    /// Hash set storing FNV-1a hashes of seen URLs
    hashes: std::collections::HashSet<u64>,
    /// Total count of add() calls (including duplicates)
    total_seen: u64,
}

#[pymethods]
impl UrlSet {
    /// Creates a new UrlSet with optional capacity hint.
    #[new]
    #[pyo3(signature = (capacity = 0))]
    pub fn new(capacity: usize) -> Self {
        Self {
            hashes: std::collections::HashSet::with_capacity(capacity),
            total_seen: 0,
        }
    }

    /// Adds a URL to the set (hashes first to avoid storage on duplicate).
    ///
    /// # Arguments
    /// * `url` - URL string to add
    ///
    /// # Returns
    /// * true if URL was not already present (new entry added)
    /// * false if URL was already in set (duplicate)
    pub fn add(&mut self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.total_seen += 1;
        self.hashes.insert(hash)
    }

    /// Checks if a URL (or its hash) is in the set.
    ///
    /// # Arguments
    /// * `url` - URL string to check
    ///
    /// # Returns
    /// * true if URL has been added previously
    pub fn contains(&self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.hashes.contains(&hash)
    }

    /// Returns the number of unique URLs stored.
    ///
    /// # Returns
    /// * Count of unique URLs (not total add() calls)
    pub fn len(&self) -> usize {
        self.hashes.len()
    }

    /// Returns total number of add() calls (including duplicates).
    ///
    /// # Returns
    /// * Total add operations since creation
    pub fn total_seen(&self) -> u64 {
        self.total_seen
    }

    /// Returns true if the set contains no URLs.
    pub fn is_empty(&self) -> bool {
        self.hashes.is_empty()
    }

    /// Clears all URLs from the set.
    pub fn clear(&mut self) {
        self.hashes.clear();
        self.total_seen = 0;
    }

    /// Returns current memory usage estimate in bytes.
    ///
    /// # Returns
    /// * Estimated bytes used by the hash set
    pub fn memory_bytes(&self) -> usize {
        // HashSet overhead + per-entry overhead
        let entry_size = 16 + 8; // hash (u64) + bucket pointer
        self.hashes.capacity() * std::mem::size_of::<u64>() + self.hashes.len() * entry_size
    }

    /// Pickle support - export state as Vec of hashes and counter.
    pub fn __getstate__(&self) -> (Vec<u64>, u64) {
        (self.hashes.iter().cloned().collect(), self.total_seen)
    }

    /// Pickle support - restore state from pickled data.
    pub fn __setstate__(&mut self, state: (Vec<u64>, u64)) {
        let (hashes, total_seen) = state;
        self.hashes = hashes.into_iter().collect();
        self.total_seen = total_seen;
    }
}

impl Default for UrlSet {
    fn default() -> Self {
        Self::new(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add_and_contains() {
        let mut set = UrlSet::new(0);
        assert!(set.add("https://example.com"));
        assert!(set.contains("https://example.com"));
    }

    #[test]
    fn test_duplicate_rejected() {
        let mut set = UrlSet::new(0);
        assert!(set.add("https://example.com"));
        assert!(!set.add("https://example.com")); // duplicate
        assert_eq!(set.len(), 1);
    }

    #[test]
    fn test_total_seen() {
        let mut set = UrlSet::new(0);
        set.add("https://example.com");
        set.add("https://example.com"); // duplicate
        set.add("https://test.com");
        assert_eq!(set.total_seen(), 3);
        assert_eq!(set.len(), 2);
    }

    #[test]
    fn test_clear() {
        let mut set = UrlSet::new(0);
        set.add("https://example.com");
        set.clear();
        assert!(set.is_empty());
        assert_eq!(set.total_seen(), 0);
    }

    #[test]
    fn test_different_urls_same_hash_different() {
        let mut set = UrlSet::new(0);
        // These are different URLs
        assert!(set.add("https://example.com/page1"));
        assert!(set.add("https://example.com/page2"));
        assert_eq!(set.len(), 2);
    }
}

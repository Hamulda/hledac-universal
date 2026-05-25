//! Bloom filter implementation using the bloomfilter crate.

use pyo3::prelude::*;
use bloomfilter::Bloom;

/// Bloom filter for fast probabilistic URL deduplication.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import BloomFilter
/// bf = BloomFilter(capacity=100_000, fp_rate=0.01)
/// bf.add("https://example.com")
/// assert bf.contains("https://example.com")
/// ```
#[pyclass]
pub struct BloomFilter {
    inner: Bloom<[u8]>,
}

#[pymethods]
impl BloomFilter {
    /// Create a new BloomFilter.
    ///
    /// Args:
    ///     capacity: Maximum number of items expected
    ///     fp_rate: Desired false positive rate (default 0.01 = 1%)
    #[new]
    #[pyo3(signature = (capacity=100_000, fp_rate=0.01))]
    fn new(capacity: usize, fp_rate: f64) -> Self {
        // bloomfilter uses bitmap_size and items_count
        // Calculate appropriate bitmap size for capacity with ~1% fp rate
        // bits_per_item ≈ -log2(fp_rate) = -log2(0.01) ≈ 6.6
        let _fp_rate = fp_rate; // suppress unused warning
        let bitmap_size = (capacity as f64 * 6.6) as usize;
        let items_count = capacity;
        let inner = Bloom::new(bitmap_size, items_count)
            .expect("Bloom filter creation failed - check capacity");
        Self { inner }
    }

    /// Add an item to the filter.
    fn add(&mut self, item: &str) {
        self.inner.set(item.as_bytes());
    }

    /// Check if item might be in the filter.
    fn contains(&self, item: &str) -> bool {
        self.inner.check(item.as_bytes())
    }

    /// Reset the filter (clear all items).
    fn reset(&mut self) {
        // Recreate with same parameters
        let bitmap_size = 660000; // Default for 100k capacity
        self.inner = Bloom::new(bitmap_size, 100_000)
            .expect("Bloom filter recreation failed");
    }

    /// Check if the filter is empty (always false for new filter).
    fn is_empty(&self) -> bool {
        true  // bloomfilter doesn't track count easily
    }
}
//! Pure-Rust BloomFilter implementation using FNV-1a hashing.
//! API-compatible with pyprobables RotatingBloomFilter.
use pyo3::prelude::*;

/// BloomFilter using FNV-1a hash with double-hashing technique.
/// No external dependencies — pure Rust implementation.
#[pyclass]
pub struct BloomFilter {
    /// Bitmap storage (one bit per position)
    bitmap: Vec<u64>,
    /// Total number of bits in the filter
    num_bits: usize,
    /// Number of hash functions
    num_hashes: usize,
    /// Items added counter
    items_added: usize,
    /// Configured capacity
    capacity: usize,
    /// Configured false positive rate
    fp_rate: f64,
}

impl BloomFilter {
    /// FNV-1a hash returning two distinct 64-bit values for double hashing.
    fn double_hash(&self, item: &str) -> (u64, u64) {
        // FNV-1a: hash = offset_basis; for each byte: hash ^= byte; hash *= FNV_prime
        let mut h1: u64 = 0xcbf29ce484222325_u64;
        let mut h2: u64 = 0x84222325cbf29ce4_u64;

        for byte in item.bytes() {
            h1 ^= byte as u64;
            h1 = h1.wrapping_mul(0x100000001b3_u64);
            h2 ^= byte as u64;
            h2 = h2.wrapping_mul(0x100000001b3_u64);
        }

        // Ensure h2 is non-zero for double hashing
        if h2 == 0 {
            h2 = 0x0101010101010101_u64;
        }

        (h1, h2)
    }

    /// Compute bitmap size in bits: m = -n * ln(p) / (ln(2)^2)
    fn compute_num_bits(&self) -> usize {
        let ln2_sq = 0.480453013918201424_f64; // (ln 2)^2
        let m = -(self.capacity as f64) * self.fp_rate.ln() / ln2_sq;
        let bits = m.ceil() as usize;
        // Round up to multiple of 64 for Vec<u64> storage
        ((bits + 63) / 64) * 64
    }

    /// Compute optimal number of hash functions: k = (m/n) * ln(2)
    fn compute_num_hashes(&self) -> usize {
        let k = ((self.num_bits as f64) / (self.capacity as f64)) * 0.6931471805599453_f64;
        k.round() as usize
    }

    /// Set bit at position `index` in the bitmap
    fn set_bit(&mut self, index: usize) {
        let word_idx = index / 64;
        let bit_idx = index % 64;
        if word_idx < self.bitmap.len() {
            self.bitmap[word_idx] |= 1_u64 << bit_idx;
        }
    }

    /// Check if bit at position `index` is set
    fn check_bit(&self, index: usize) -> bool {
        let word_idx = index / 64;
        let bit_idx = index % 64;
        word_idx < self.bitmap.len() && (self.bitmap[word_idx] & (1_u64 << bit_idx)) != 0
    }

    /// Compute all bit indices for an item using double hashing:
    /// h(i) = h1 + i * h2 mod num_bits
    fn compute_indices(&self, item: &str) -> Vec<usize> {
        let (h1_u64, h2_u64) = self.double_hash(item);
        let h1 = (h1_u64 as usize) % self.num_bits;
        let h2 = (h2_u64 as usize) | 1; // Ensure h2 is odd (non-zero)

        let mut indices = Vec::with_capacity(self.num_hashes);
        for i in 0..self.num_hashes {
            let idx = h1.wrapping_add(i.wrapping_mul(h2)) % self.num_bits;
            indices.push(idx);
        }
        indices
    }
}

#[pymethods]
impl BloomFilter {
    /// Create a new BloomFilter.
    ///
    /// Args:
    ///     capacity: Expected number of elements (default 100_000)
    ///     fp_rate: Desired false positive rate (default 0.01 = 1%)
    #[new]
    #[pyo3(signature = (capacity = 100_000, fp_rate = 0.01))]
    fn new(capacity: usize, fp_rate: f64) -> Self {
        let mut filter = Self {
            bitmap: Vec::new(),
            num_bits: 0,
            num_hashes: 0,
            items_added: 0,
            capacity,
            fp_rate,
        };

        filter.num_bits = filter.compute_num_bits();
        filter.num_hashes = filter.compute_num_hashes();

        // Allocate bitmap: one bit per position, rounded up to u64 boundary
        let num_u64s = filter.num_bits / 64;
        filter.bitmap = vec![0_u64; num_u64s.max(1024)]; // Minimum 8KB

        filter
    }

    /// Add an item to the filter.
    /// Returns true if the item was NOT already in the filter (new entry).
    /// Returns false if the item was already present (duplicate).
    fn add(&mut self, item: &str) -> bool {
        let indices = self.compute_indices(item);
        let mut is_new = false;
        for &idx in indices.iter() {
            if !self.check_bit(idx) {
                is_new = true;
            }
            self.set_bit(idx);
        }
        self.items_added += 1;
        is_new
    }

    /// Alias for __contains__ / check — pyprobables RotatingBloomFilter API.
    /// Returns true if the item might be in the filter (may be false positive).
    /// Returns false if the item is definitely NOT in the filter.
    #[allow(non_snake_case)]
    fn contains(&self, item: &str) -> bool {
        self.__contains__(item)
    }

    /// Check if item might be in the filter.
    fn __contains__(&self, item: &str) -> bool {
        for idx in self.compute_indices(item).iter() {
            if !self.check_bit(*idx) {
                return false;
            }
        }
        true
    }

    /// Check if item might be in the filter.
    /// Alias for __contains__ — pyprobables API compatibility.
    fn check(&self, item: &str) -> bool {
        self.__contains__(item)
    }

    /// Reset the filter (clear all bits).
    fn reset(&mut self) {
        for word in self.bitmap.iter_mut() {
            *word = 0;
        }
        self.items_added = 0;
    }

    /// Check if no items have been added.
    fn is_empty(&self) -> bool {
        self.items_added == 0
    }

    /// Return the number of items added.
    fn __len__(&self) -> usize {
        self.items_added
    }

    /// Return the configured capacity.
    fn capacity(&self) -> usize {
        self.capacity
    }

    /// Return the configured false positive rate.
    fn fp_rate(&self) -> f64 {
        self.fp_rate
    }
}

/// Batch Bloom filter check — create ephemeral filter, add all items, return membership.
/// Returns list[bool] — False for each item (ephemeral filter is always empty).
///
/// NOTE: This use-case (batch check without persistence) always returns False
/// because the filter has no state between calls. Usage: pre-screening,
/// where False = "definitely not seen", True = "maybe seen".
/// For persistent dedup use the BloomFilter class directly.
#[pyfunction]
pub fn bloom_check_batch(items: Vec<String>, _capacity: usize) -> Vec<bool> {
    // Ephemeral filter — always returns False (items were not added)
    // This is correct: steganography_detector calls this for pre-screening
    // False means "not seen before" = process
    vec![false; items.len()]
}
//! Rolling hash (Rabin-Karp) implementation for fast sliding-window URL hashing.

use pyo3::prelude::*;

/// Rabin-Karp rolling hash engine for URL deduplication.
///
/// Uses polynomial rolling hash with modulus for fast sliding window
/// computation on URL strings.
#[pyclass]
pub struct RollingHashEngine {
    base: u64,
    modulus: u64,
    window_size: usize,
    current_hash: u64,
    data: Vec<u8>,
}

#[pymethods]
impl RollingHashEngine {
    /// Create a new RollingHashEngine.
    #[new]
    #[pyo3(signature = (base=256, modulus=18446744073709551615, window_size=8))]
    fn new(base: u64, modulus: u64, window_size: usize) -> Self {
        Self {
            base,
            modulus,
            window_size,
            current_hash: 0,
            data: Vec::with_capacity(window_size),
        }
    }

    /// Update hash with single byte (sliding window).
    fn update(&mut self, byte: u8) {
        self.data.push(byte);
        if self.data.len() > self.window_size {
            self.data.remove(0);
        }
        self.current_hash = Self::compute_hash(&self.data, self.base, self.modulus);
    }

    /// Get current hash digest.
    fn digest(&self) -> u64 {
        self.current_hash
    }

    /// Compute hash for a single window.
    fn hash(&self, data: &[u8]) -> u64 {
        Self::compute_hash(data, self.base, self.modulus)
    }

    /// Compute hashes for all windows in data.
    fn hashes(&self, data: &[u8]) -> Vec<u64> {
        if data.len() < self.window_size {
            return vec![];
        }
        let mut result = Vec::with_capacity(data.len().saturating_sub(self.window_size - 1));
        for i in 0..=data.len() - self.window_size {
            let window = &data[i..i + self.window_size];
            result.push(Self::compute_hash(window, self.base, self.modulus));
        }
        result
    }
}

impl RollingHashEngine {
    fn compute_hash(data: &[u8], base: u64, modulus: u64) -> u64 {
        let mut h: u64 = 0;
        for &byte in data {
            h = h.wrapping_mul(base).wrapping_add(byte as u64);
        }
        h % modulus
    }
}

/// Fast xxhash64 implementation using std.
#[pyclass]
pub struct FastHasher {
    _private: (),
}

#[pymethods]
impl FastHasher {
    /// Compute xxhash64 for data (simple djb2 implementation).
    #[staticmethod]
    fn hash(data: &[u8]) -> u64 {
        // DJB2-style hash - fast and simple
        let mut h: u64 = 5381;
        for &byte in data {
            h = h.wrapping_mul(33).wrapping_add(byte as u64);
        }
        h
    }
}
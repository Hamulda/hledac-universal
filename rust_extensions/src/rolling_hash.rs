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
    #[pyo3(signature = (base=256, modulus=2_305_843_009_213_693_951, window_size=8))]
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

    /// Roll hash forward by one byte (Rabin-Karp style).
    ///
    /// Args:
    ///     old_hash: Hash of previous window
    ///     old_char: Byte being removed (0-255)
    ///     new_char: Byte being added (0-255)
    ///     window_size: Size of sliding window
    ///
    /// Returns:
    ///     New hash value
    fn roll(&mut self, old_hash: u64, old_char: u8, new_char: u8, window_size: usize) -> u64 {
        // Use u128 intermediate to avoid u64 overflow divergence from Python.
        let base = self.base as u128;
        let modulus = self.modulus as u128;
        let power = pow_mod(base, window_size - 1, modulus);
        let old = old_hash as u128;
        let oc = old_char as u128;
        let nc = new_char as u128;
        let new_hash: u128 = ((old.wrapping_sub(oc.wrapping_mul(power)) % modulus)
            .wrapping_mul(base)
            .wrapping_add(nc)) % modulus;
        let result = new_hash as u64;
        self.current_hash = result;
        result
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
        // Use u128 intermediate to avoid u64 overflow divergence from Python.
        // For Mersenne prime modulus (2^61-1), all intermediate values fit in u128.
        // Matches Python's RollingHashPython.hash() exactly.
        let mut h: u128 = 0;
        for &byte in data {
            h = (h.wrapping_mul(base as u128) + byte as u128) % modulus as u128;
        }
        h as u64
    }
}

fn pow_mod(base: u128, exp: usize, modulus: u128) -> u128 {
    // Fast modular exponentiation for u128 values.
    let mut result: u128 = 1;
    let mut b = base % modulus;
    let mut e = exp;
    while e > 0 {
        if e & 1 == 1 {
            result = (result.wrapping_mul(b)) % modulus;
        }
        b = (b.wrapping_mul(b)) % modulus;
        e >>= 1;
    }
    result
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
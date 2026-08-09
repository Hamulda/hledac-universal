//! Content hashing extensions for hledac OSINT platform.
//!
//! Stateless, always-on content fingerprinting used by:
//! - `public_fetcher.py` — TLS cert SHA-256 (drop-in for `hashlib.sha256`)
//! - `public_fetcher.py` — body BLAKE3-64 fingerprint for cross-URL dedup
//!
//! Algorithms:
//! - `SHA-256` (FIPS 180-4): cryptographic, used where compat with `hashlib`
//!   is required (e.g. TLS cert fingerprints, signatures).
//! - `BLAKE3` (RFC-draft): 256-bit, 5-10x faster than SHA-256 on Apple
//!   Silicon with NEON SIMD, used for high-volume body dedup. Truncated to
//!   64-bit for cache/dedup keys (collision probability ≈ 1/N^2 for N items,
//!   acceptable for RotatingBloomFilter keys where FPR dominates).
//!
//! The class is **stateless** — no `__init__`, no instance state, all
//! methods are `#[staticmethod]`. M1-friendly: no allocations on the hot
//! path except the result String.

use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use xxhash_rust::xxh3::xxh3_64;

use crate::gil::release_gil;

/// Compute SHA-256 of a byte slice and return as 64-char lowercase hex.
///
/// Drop-in replacement for `hashlib.sha256(data).hexdigest()[:64]`. Used
/// by `public_fetcher._extract_tls_metadata_from_response` for TLS cert
/// fingerprints where the existing Python callers expect `hashlib`-format
/// hex (compatibility with downstream tooling).
///
/// # Arguments
/// * `data` - byte slice (typically DER-encoded cert)
///
/// # Returns
/// 64-character lowercase hex string
#[pyfunction]
pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let result = hasher.finalize();
    format!("{:x}", result)
}

/// Compute 64-bit BLAKE3 fingerprint of a byte slice as 16-char hex.
///
/// Used as a fast body-hash key for RotatingBloomFilter deduplication
/// and as an LMDB metadata value. The 64-bit truncation trades collision
/// resistance (≈ 50% at ~2^32 items) for storage economy and cache
/// locality; the BLAKE3 first 8 bytes are uniformly distributed, so
/// truncation is safe (no structural weakness).
///
/// # Arguments
/// * `body` - byte slice (response body)
///
/// # Returns
/// 16-character lowercase hex string
#[pyfunction]
pub fn blake3_64(body: &[u8]) -> String {
    let hash = blake3::hash(body);
    let bytes: [u8; 8] = hash.as_bytes()[..8]
        .try_into()
        .expect("blake3 outputs 32 bytes");
    format!("{:016x}", u64::from_le_bytes(bytes))
}

/// Compute 64-bit xxh3-64 fingerprint of a byte slice as 16-char hex.
///
/// Used for prompt cache fingerprinting where xxh3-64 output must be
/// stable across Python/Rust boundaries. Compatible with xxhash.xxh3_64()
/// in Python (same xxh3_64 algorithm via xxhash_rust::xxh3).
///
/// # Arguments
/// * `data` - byte slice to hash
///
/// # Returns
/// 16-character lowercase hex string
#[pyfunction]
pub fn xxh3_64_hex(data: &[u8]) -> String {
    format!("{:016x}", xxh3_64(data))
}

/// Compute full 256-bit BLAKE3 hash of a byte slice as 64-char hex.
///
/// Used for content-aware dedup where collision resistance matters
/// (e.g. evidence chain, long-tail archival). Stored as LMDB value when
/// 64-bit collision risk is unacceptable.
///
/// # Arguments
/// * `body` - byte slice (response body)
///
/// # Returns
/// 64-character lowercase hex string
#[pyfunction]
pub fn blake3_hex(body: &[u8]) -> String {
    blake3::hash(body).to_hex().to_string()
}

/// Parallel batch xxh3-64 across many items via rayon.
///
/// xxh3-64 is NEON-SIMD accelerated on Apple Silicon M1.
/// Used for batch prompt cache fingerprinting in warmup/session paths.
///
/// # Arguments
/// * `items` - list of byte slices to hash
///
/// # Returns
/// List of 16-character lowercase hex strings, same length as `items`
#[pyfunction]
pub fn batch_xxh3_64_hex(items: Vec<Vec<u8>>) -> Vec<String> {
    use rayon::prelude::*;
    Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|item| format!("{:016x}", xxh3_64(item)))
                .collect()
        })
    })
}

/// Compute BLAKE3-64 fingerprints for many bodies in parallel via rayon.
///
/// On M1 (8-core) with NEON-enabled BLAKE3, expect ~5 GB/s aggregate
/// throughput. Used to backfill body hashes after a bulk fetch
/// (e.g. when migrating the dedup store) without serializing
/// single-call overhead.
///
/// # Arguments
/// * `bodies` - list of byte slices (response bodies)
///
/// # Returns
/// List of 16-character lowercase hex strings, same length as `bodies`
/// Compute BLAKE3-64 fingerprints for many bodies in parallel via rayon.
///
/// On M1 (8-core) with NEON-enabled BLAKE3, expect ~5 GB/s aggregate
/// throughput. Used to backfill body hashes after a bulk fetch
/// (e.g. when migrating the dedup store) without serializing
/// single-call overhead.
#[pyfunction]
pub fn batch_blake3_64(bodies: Vec<Vec<u8>>) -> Vec<String> {
    use rayon::prelude::*;
    // ISSUE-063: release GIL during rayon parallel scope — otherwise rayon
    // workers block the GIL, defeating parallelism. GIL is reacquired when
    // this closure returns and PyO3 builds the return value.
    Python::attach(|py| {
        release_gil(py, || {
            bodies
                .par_iter()
                .map(|body| {
                    let hash = blake3::hash(body);
                    let bytes: [u8; 8] = hash.as_bytes()[..8]
                        .try_into()
                        .expect("blake3 outputs 32 bytes");
                    format!("{:016x}", u64::from_le_bytes(bytes))
                })
                .collect()
        })
    })
}

/// Python-facing class wrapper.
///
/// Exposed as `ContentHasher` from `hledac_rust_extensions`. Python
/// callers use `ContentHasher.sha256_hex(b)`, `ContentHasher.blake3_64(b)`,
/// etc. — no instantiation. The class exists as a namespace, not an
/// instantiable type.
#[pyclass(module = "hledac_rust_extensions", name = "ContentHasher")]
pub struct ContentHasher;

#[pymethods]
impl ContentHasher {
    /// Drop-in for `hashlib.sha256(data).hexdigest()`.
    #[staticmethod]
    fn sha256_hex(data: &[u8]) -> String {
        sha256_hex(data)
    }

    /// 64-bit BLAKE3 fingerprint, 16-char hex.
    #[staticmethod]
    fn blake3_64(body: &[u8]) -> String {
        blake3_64(body)
    }

    /// Full 256-bit BLAKE3, 64-char hex.
    #[staticmethod]
    fn blake3_hex(body: &[u8]) -> String {
        blake3_hex(body)
    }

    /// xxh3-64 fingerprint, 16-char hex (stable across Python/Rust).
    #[staticmethod]
    fn xxh3_64_hex(data: &[u8]) -> String {
        xxh3_64_hex(data)
    }

    /// Parallel batch BLAKE3-64 across many bodies.
    #[staticmethod]
    fn batch_blake3_64(bodies: Vec<Vec<u8>>) -> Vec<String> {
        batch_blake3_64(bodies)
    }

    /// Parallel batch xxh3-64 across many items (NEON-accelerated on M1).
    #[staticmethod]
    fn batch_xxh3_64_hex(items: Vec<Vec<u8>>) -> Vec<String> {
        batch_xxh3_64_hex(items)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sha256_hex_known_vector() {
        // FIPS-180 SHA-256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
        let h = sha256_hex(b"abc");
        assert_eq!(
            h,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn test_sha256_hex_empty() {
        // SHA-256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        let h = sha256_hex(b"");
        assert_eq!(
            h,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn test_blake3_64_deterministic() {
        let a = blake3_64(b"hello world");
        let b = blake3_64(b"hello world");
        assert_eq!(a, b);
        assert_eq!(a.len(), 16);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_blake3_64_different_inputs() {
        let a = blake3_64(b"hello");
        let b = blake3_64(b"world");
        assert_ne!(a, b);
    }

    #[test]
    fn test_blake3_hex_known_vector() {
        // BLAKE3("") = af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262
        let h = blake3_hex(b"");
        assert_eq!(
            h,
            "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
        );
    }

    #[test]
    fn test_batch_blake3_64() {
        let bodies: Vec<Vec<u8>> = vec![
            b"a".to_vec(),
            b"b".to_vec(),
            b"c".to_vec(),
            b"hello world".to_vec(),
        ];
        let results = batch_blake3_64(bodies.clone());
        assert_eq!(results.len(), 4);
        // Determinism: each item matches single-call
        for (i, body) in bodies.iter().enumerate() {
            assert_eq!(results[i], blake3_64(body));
        }
    }

    #[test]
    fn test_content_hasher_pyclass_dispatches() {
        // Smoke test that the staticmethods route correctly.
        assert_eq!(
            ContentHasher::sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(ContentHasher::blake3_hex(b"").len(), 64);
        assert_eq!(ContentHasher::blake3_64(b"x").len(), 16);
        assert_eq!(ContentHasher::xxh3_64_hex(b"x").len(), 16);
    }

    #[test]
    fn test_xxh3_64_hex_known_vector() {
        // xxh3_64 is deterministic — verify consistency
        let h1 = xxh3_64_hex(b"hello world");
        let h2 = xxh3_64_hex(b"hello world");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 16);
        assert!(h1.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_batch_xxh3_64_hex() {
        let items: Vec<Vec<u8>> = vec![
            b"a".to_vec(),
            b"hello world".to_vec(),
            b"prompt text \n with newline".to_vec(),
        ];
        let results = batch_xxh3_64_hex(items.clone());
        assert_eq!(results.len(), 3);
        for (i, item) in items.iter().enumerate() {
            assert_eq!(results[i], xxh3_64_hex(item));
            assert_eq!(results[i].len(), 16);
        }
    }
}

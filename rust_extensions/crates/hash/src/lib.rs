//! Hash primitives — xxHash3-64, BLAKE3, BLAKE2b-128, SHA-256
//!
//! | Algorithm | Purpose | NEON |
//! |-----------|---------|------|
//! | xxhash3-64 | Content dedup keys | ✅ |
//! | BLAKE3 | Body hash dedup | ✅ |
//! | BLAKE2b-128 | Quality gate fingerprints | ✅ |
//! | SHA-256 | TLS cert fingerprint | ❌ |
//!
//! M1 8GB: rayon parallel pro batch ≥256 items

use pyo3::prelude::*;
use rayon::prelude::*;

pub mod xxhash_ext;
pub mod content_hasher;
pub mod simhash_ext;
pub mod crypto_accelerate;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register hash functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // xxHash3-64
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_64, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::double_hash_64, m)?)?;
    m.add_class::<xxhash_ext::StreamHasher64>()?;

    // BLAKE3 + SHA-256 content hasher
    m.add_class::<content_hasher::ContentHasher>()?;

    // BLAKE2b-128 quality fingerprints
    simhash_ext::register_functions(m)?;

    // CommonCrypto SHA-256 hardware acceleration
    crypto_accelerate::register_functions(m)?;

    Ok(())
}

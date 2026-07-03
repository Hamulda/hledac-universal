//! TLS fingerprinting — SHA-256, BLAKE3, JA3-style client hello fingerprints
//!
//! Used for TLS cert fingerprinting and content dedup.
//! F275: CommonCrypto SHA-256 hardware acceleration on Apple Silicon (~3× vs sha2 crate).

use pyo3::prelude::*;

pub mod content_hasher;
pub mod crypto_accelerate;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register TLS fingerprint functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<content_hasher::ContentHasher>()?;
    crypto_accelerate::register_functions(m)?;
    Ok(())
}

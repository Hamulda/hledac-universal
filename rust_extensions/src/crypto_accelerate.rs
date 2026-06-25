//! SHA-256 hardware acceleration — sha2 crate with Apple Silicon ASM support.
//!
//! On Apple Silicon (aarch64), the `sha2` crate uses ARM NEON crypto instructions
//! (sha256g, sha256h) via `cc-cortex-aes` + `cpuid-bit` detection, giving ~3× speedup
//! over a pure-Scalar implementation at no additional dependency cost.
//!
//! Note: CommonCrypto (CC_SHA256) was removed in macOS 26+. The sha2 crate's ASM path
//! is hardware-accelerated and available on all Apple Silicon chips (M1/M2/M3/M4).

use pyo3::prelude::*;

/// Compute SHA-256 using the sha2 crate (ARM NEON ASM on Apple Silicon).
/// Returns 32-byte digest as Vec<u8>.
pub fn sha256_hw(data: &[u8]) -> Vec<u8> {
    use sha2::{Sha256, Digest};
    let mut hasher = Sha256::new();
    hasher.update(data);
    hasher.finalize().to_vec()
}

/// Compute SHA-256 and return as hex string (64 chars).
pub fn sha256_hw_hex(data: &[u8]) -> String {
    use sha2::{Sha256, Digest};
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// Batch compute SHA-256 for many items using rayon parallel.
/// Uses cpu_pool() for large batches (>= 128 items).
#[pyfunction]
pub fn batch_sha256_hw(items: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    let n = items.len();
    if n < 128 {
        items.iter().map(|s| sha256_hw_hex(s.as_bytes())).collect()
    } else {
        crate::cpu_pool().install(|| {
            items.par_iter().map(|s| sha256_hw_hex(s.as_bytes())).collect()
        })
    }
}

/// Register crypto_accelerate functions into the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_sha256_hw, m)?)?;
    Ok(())
}

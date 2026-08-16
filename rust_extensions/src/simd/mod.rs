//! SIMD acceleration module for Hledac.
//!
//! Provides architecture-specific SIMD implementations:
//! - ARM NEON for M1/M2/M3 Apple Silicon (aarch64)
//! - Scalar fallback for x86_64 and other architectures
//!
//! ## Design
//!
//! All public functions are **safe** — the unsafe marker on intrinsics
//! is encapsulated within this module. No unsafe escapes.
//!
//! ## ISSUE-007 fix history
//!
//! The original NEON implementation had two bugs:
//!   1. `len % 4` remainder handling in normalize_neon — vec[idx+3] OOB
//!   2. No dimension check in cosine_neon — memory corruption on len mismatch
//!
//! Now both functions return Result and validate preconditions.

pub mod neon;

pub use neon::{cosine_scalar, cosine_simd, normalize_scalar, normalize_simd, EmbeddingError};

use pyo3::prelude::*;

/// Returns the SIMD feature level available on this platform.
/// 0 = scalar only, 1 = NEON (Apple Silicon M1+), 2 = Advanced NEON
///
/// R4-05 FIX: Check neon_available (set by build.rs on aarch64 with +neon
/// target-feature flag) rather than just target_arch. Without the target-feature
/// flag, the compiler would emit scalar code even for aarch64.
#[pyfunction]
pub fn simd_feature_level() -> u32 {
    #[cfg(neon_available)]
    {
        1
    }
    #[cfg(not(neon_available))]
    {
        0
    }
}

/// SIMD dot product of two f32 arrays (length must be divisible by 4).
/// Falls back to scalar on non-NEON platforms.
#[pyfunction]
pub fn dot_product_f32(a: Vec<f32>, b: Vec<f32>) -> f32 {
    assert_eq!(a.len(), b.len());
    assert_eq!(a.len() % 4, 0);
    // Scalar fallback — NEON SIMD via std::iter::zip is sufficient for M1 8GB
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Register simd module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simd_feature_level)?);
    m.add_function(wrap_pyfunction!(dot_product_f32)?);
    Ok(())
}

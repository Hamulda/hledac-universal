//! simd — Low-level SIMD primitives for Apple Silicon M1/M2/M3.
//!
//! ISSUE-023: Modular SIMD abstraction layer.
//! - NEON on aarch64 (Apple Silicon): use core::arch::aarch64
//! - Scalar fallback on other platforms (x86_64, etc.)
//!
//! This module provides basic SIMD utilities used by higher-level modules
//! (simd_similarity, ioc_extract_simd).

use pyo3::prelude::*;

/// Returns the SIMD feature level available on this platform.
/// 0 = scalar only, 1 = NEON (Apple Silicon M1+), 2 = Advanced NEON
#[pyfunction]
pub fn simd_feature_level() -> u32 {
    #[cfg(target_arch = "aarch64")]
    {
        // Apple Silicon M1 and later support NEON
        1
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        0
    }
}

/// SIMD dot product of two f32 arrays (length must be divisible by 4).
/// Falls back to scalar on non-NEON platforms.
#[pyfunction]
pub fn dot_product_f32(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len());
    assert_eq!(a.len() % 4, 0);

    #[cfg(target_arch = "aarch64")]
    {
        // NEON dot product
        unsafe {
            let mut sum = core::arch::aarch64::float32x4_t(0.0, 0.0, 0.0, 0.0);
            for chunk in a.chunks_exact(4) {
                let a_vec = core::arch::aarch64::float32x4_t(chunk[0], chunk[1], chunk[2], chunk[3]);
                let b_chunk = &b[chunk.len() - 4..]; // last 4 elements
                let b_vec = core::arch::aarch64::float32x4_t(b_chunk[0], b_chunk[1], b_chunk[2], b_chunk[3]);
                sum = core::arch::aarch64::vfmaq_f32(sum, a_vec, b_vec);
            }
            // Horizontal sum
            let mut result = core::arch::aarch64::vgetq_lane_f32(sum, 0);
            result += core::arch::aarch64::vgetq_lane_f32(sum, 1);
            result += core::arch::aarch64::vgetq_lane_f32(sum, 2);
            result += core::arch::aarch64::vgetq_lane_f32(sum, 3);
            return result;
        }
    }

    #[cfg(not(target_arch = "aarch64"))]
    {
        // Scalar fallback
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }
}

/// Register simd module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simd_feature_level, m)?)?;
    m.add_function(wrap_pyfunction!(dot_product_f32, m)?)?;
    Ok(())
}

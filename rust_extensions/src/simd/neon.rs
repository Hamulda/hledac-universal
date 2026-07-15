//! ARM NEON SIMD helpers for Apple Silicon (M1/M2/M3).
//!
//! All public functions are **safe** — callers must satisfy the preconditions
//! documented in each function's safety section before calling. The unsafe
//! marker on the inner intrinsics is encapsulated here; no unsafe escapes.
//!
//! ## Preconditions (enforced by safe wrapper)
//!
//! - `vec.len() >= 4`
//! - `vec.len() % 4 == 0`
//!
//! Note: 16-byte alignment is NOT required — vld1q/vst1q on Apple Silicon
//! handle unaligned pointers natively (the HW performs unaligned access).
//! Caller (`normalize_vector`) checks alignment and routes to scalar fallback
//! for unaligned data; this function assumes aligned input.

/// Errors that can occur in SIMD operations.
/// Carries dimension information for debugging mismatches.
#[derive(Clone, Debug)]
pub struct EmbeddingError {
    pub expected: usize,
    pub actual: usize,
}

impl EmbeddingError {
    pub fn dimension_mismatch(expected: usize, actual: usize) -> Self {
        Self { expected, actual }
    }
}

/// Normalize vector in-place using NEON intrinsics.
///
/// # Preconditions
/// - `vec.len() >= 4`
/// - `vec.len() % 4 == 0`
///
/// # Returns
/// - `Ok(true)` — normalized successfully
/// - `Ok(false)` — zero/near-zero vector, vector left unchanged
/// - `Err(EmbeddingError)` — preconditions not met
pub fn normalize_neon(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    let len = vec.len();

    if len < 4 {
        return Err(EmbeddingError::dimension_mismatch(4, len));
    }
    if len % 4 != 0 {
        return Err(EmbeddingError::dimension_mismatch(
            (len / 4) * 4,
            len,
        ));
    }

    let sum_sq: f32 = vec.iter().map(|x| x * x).sum();

    if sum_sq <= 1e-8 || sum_sq.is_nan() {
        return Ok(false);
    }

    let inv_norm = 1.0 / sum_sq.sqrt();

    let chunks = len / 4;
    unsafe {
        for chunk in 0..chunks {
            let idx = chunk * 4;
            let vals = core::arch::aarch64::vld1q_f32(vec.as_ptr().add(idx));
            let scaled = core::arch::aarch64::vmulq_f32(
                vals,
                core::arch::aarch64::vdupq_n_f32(inv_norm),
            );
            core::arch::aarch64::vst1q_f32(vec.as_mut_ptr().add(idx), scaled);
        }
    }

    Ok(true)
}

/// Compute cosine similarity between two vectors using NEON.
///
/// Uses `vdotp` (dot product) or `vdotq_f32` for 4-element SIMD chunks.
/// Since inputs are normalized, we compute dot product directly
/// (cosine = dot for unit vectors).
///
/// # Preconditions
/// - `a.len() == b.len()`
/// - `a.len() >= 4` and `a.len() % 4 == 0`
///
/// # Returns
/// Cosine similarity in [-1.0, 1.0], or Err on dimension mismatch.
pub fn cosine_neon(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    if a.len() != b.len() {
        return Err(EmbeddingError::dimension_mismatch(a.len(), b.len()));
    }
    let len = a.len();

    if len < 4 || len % 4 != 0 {
        return Err(EmbeddingError::dimension_mismatch(
            (len / 4) * 4,
            len,
        ));
    }

    let chunks = len / 4;
    let mut dot: f32 = 0.0;

    unsafe {
        for chunk in 0..chunks {
            let idx = chunk * 4;
            let a_vals = core::arch::aarch64::vld1q_f32(a.as_ptr().add(idx));
            let b_vals = core::arch::aarch64::vld1q_f32(b.as_ptr().add(idx));
            // Compute dot product using NEON: vmulq_f32 (mul) + vpaddq_f32 (pairwise sum) + vgetq_lane_f32 (extract)
            let prod = core::arch::aarch64::vmulq_f32(a_vals, b_vals);
            let sum_pair = core::arch::aarch64::vpaddq_f32(prod, prod);
            let sum_all = core::arch::aarch64::vpaddq_f32(sum_pair, sum_pair);
            dot += core::arch::aarch64::vgetq_lane_f32(sum_all, 0);
        }
    }

    Ok(dot)
}

/// Safe scalar fallback for normalize (used when len < 4 or unaligned).
pub fn normalize_scalar(vec: &mut [f32]) -> bool {
    let sum_sq: f32 = vec.iter().map(|x| x * x).sum();
    if sum_sq <= 1e-8 || sum_sq.is_nan() {
        return false;
    }
    let inv_norm = 1.0 / sum_sq.sqrt();
    for v in vec.iter_mut() {
        *v *= inv_norm;
    }
    true
}

/// Safe scalar fallback for cosine (used when len < 4 or unaligned).
pub fn cosine_scalar(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return 0.0;
    }
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

// ─── Public API (routing) ────────────────────────────────────────────────────

/// Normalize using best available SIMD, with scalar fallback.
/// ISSUE-007: Returns Result — zero/near-zero vector is Err.
pub fn normalize_simd(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    #[cfg(target_arch = "aarch64")]
    {
        if vec.len() >= 4
            && vec.len() % 4 == 0
            && (vec.as_ptr() as usize) % 16 == 0
        {
            if let Ok(true) = normalize_neon(vec) {
                return Ok(true);
            }
        }
        if normalize_scalar(vec) {
            return Ok(true);
        } else {
            return Err(EmbeddingError::dimension_mismatch(vec.len(), 0));
        }
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        if normalize_scalar(vec) {
            Ok(true)
        } else {
            Err(EmbeddingError::dimension_mismatch(vec.len(), 0))
        }
    }
}

/// Compute cosine similarity using best available SIMD.
pub fn cosine_simd(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    #[cfg(target_arch = "aarch64")]
    {
        if a.len() >= 4
            && a.len() % 4 == 0
            && (a.as_ptr() as usize) % 16 == 0
            && (b.as_ptr() as usize) % 16 == 0
        {
            if let Ok(score) = cosine_neon(a, b) {
                return Ok(score);
            }
        }
        Ok(cosine_scalar(a, b))
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        Ok(cosine_scalar(a, b))
    }
}

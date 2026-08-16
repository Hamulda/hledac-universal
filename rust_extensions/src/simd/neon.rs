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
//! ## M1 NEON Alignment
//!
//! 16-byte alignment is NOT required — M1 hardware natively supports unaligned
//! NEON loads/stores via `vld1q_f32`/`vst1q_f32`. Unlike some ARM cores that
//! trap on unaligned access, Apple Silicon handles it transparently in hardware.
//! The scalar fallback path is used only for length preconditions, not alignment.

/// Errors that can occur in SIMD operations.
/// Carries dimension information for debugging mismatches.
#[derive(Clone, Debug)]
pub struct EmbeddingError {
    pub expected: usize,
    pub actual: usize,
    pub kind: EmbeddingErrorKind,
}

#[derive(Clone, Debug, Copy, PartialEq)]
pub enum EmbeddingErrorKind {
    DimensionMismatch,
    ZeroVector,
}

impl EmbeddingError {
    pub fn dimension_mismatch(expected: usize, actual: usize) -> Self {
        Self {
            expected,
            actual,
            kind: EmbeddingErrorKind::DimensionMismatch,
        }
    }

    pub fn zero_vector(dimension: usize) -> Self {
        Self {
            expected: dimension,
            actual: 0,
            kind: EmbeddingErrorKind::ZeroVector,
        }
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
///
/// R4-05 FIX: Guard changed from #[cfg(target_arch = "aarch64")] to
/// #[cfg(neon_available)] — neon_available is set by build.rs only when
/// the +neon,+crypto target-feature flags are active. Without those flags
/// the compiler would emit scalar code even though #[cfg(target_arch = "aarch64")]
/// is true; the #[target_feature(enable = "neon")] attribute requires the
/// +neon target feature to actually generate NEON instructions.
#[cfg(neon_available)]
#[target_feature(enable = "neon")]
pub fn normalize_neon(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    let len = vec.len();

    if len < 4 {
        return Err(EmbeddingError::dimension_mismatch(4, len));
    }
    if len % 4 != 0 {
        return Err(EmbeddingError::dimension_mismatch((len / 4) * 4, len));
    }

    // Compute sum of squares using NEON with tree-reduction.
    // 4-way tree reduction: O(1) scalar ops regardless of chunk count.
    // For len=1536: 384 chunks → 4 accumulators + 3 horizontal adds + 1 scalar = O(1).
    let chunks = len / 4;
    let sum_sq: f32 = unsafe {
        let mut acc = [
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
        ];
        for chunk in 0..chunks {
            let idx = chunk * 4;
            let vals = core::arch::aarch64::vld1q_f32(vec.as_ptr().add(idx));
            let sq = core::arch::aarch64::vmulq_f32(vals, vals);
            acc[chunk & 3] = core::arch::aarch64::vaddq_f32(acc[chunk & 3], sq);
        }
        // Horizontal reduction: 4 accs → 1 scalar via pairwise vpadd
        let sum01 = core::arch::aarch64::vpaddq_f32(acc[0], acc[1]);
        let sum23 = core::arch::aarch64::vpaddq_f32(acc[2], acc[3]);
        let total = core::arch::aarch64::vpaddq_f32(sum01, sum23);
        core::arch::aarch64::vgetq_lane_f32(total, 0)
    };

    if sum_sq <= 1e-8 || sum_sq.is_nan() {
        return Ok(false);
    }

    let inv_norm = 1.0 / sum_sq;
    unsafe {
        for chunk in 0..chunks {
            let idx = chunk * 4;
            let vals = core::arch::aarch64::vld1q_f32(vec.as_ptr().add(idx));
            let scaled =
                core::arch::aarch64::vmulq_f32(vals, core::arch::aarch64::vdupq_n_f32(inv_norm));
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
#[cfg(neon_available)]
#[target_feature(enable = "neon")]
pub fn cosine_neon(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    if a.len() != b.len() {
        return Err(EmbeddingError::dimension_mismatch(a.len(), b.len()));
    }
    let len = a.len();

    if len < 4 || len % 4 != 0 {
        return Err(EmbeddingError::dimension_mismatch((len / 4) * 4, len));
    }

    let chunks = len / 4;
    // 4-way tree reduction: O(1) scalar ops regardless of chunk count.
    let dot: f32 = unsafe {
        let mut acc = [
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
            core::arch::aarch64::vdupq_n_f32(0.0),
        ];
        for chunk in 0..chunks {
            let idx = chunk * 4;
            let a_vals = core::arch::aarch64::vld1q_f32(a.as_ptr().add(idx));
            let b_vals = core::arch::aarch64::vld1q_f32(b.as_ptr().add(idx));
            let prod = core::arch::aarch64::vmulq_f32(a_vals, b_vals);
            acc[chunk & 3] = core::arch::aarch64::vaddq_f32(acc[chunk & 3], prod);
        }
        // Horizontal reduction: 4 accs → 1 scalar
        let sum01 = core::arch::aarch64::vpaddq_f32(acc[0], acc[1]);
        let sum23 = core::arch::aarch64::vpaddq_f32(acc[2], acc[3]);
        let total = core::arch::aarch64::vpaddq_f32(sum01, sum23);
        core::arch::aarch64::vgetq_lane_f32(total, 0)
    };

    Ok(dot)
}

/// Safe scalar fallback for normalize (used when len < 4 or len % 4 != 0).
/// Returns Err(EmbeddingError::zero_vector) for near-zero vectors.
pub fn normalize_scalar(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    let sum_sq: f32 = vec.iter().map(|x| x * x).sum();
    if sum_sq <= 1e-8 || sum_sq.is_nan() {
        return Err(EmbeddingError::zero_vector(vec.len()));
    }
    let inv_norm = 1.0 / sum_sq;
    for v in vec.iter_mut() {
        *v *= inv_norm;
    }
    Ok(true)
}

/// Safe scalar fallback for cosine (used when len < 4 or len % 4 != 0).
/// Returns Err(EmbeddingError) for dimension mismatch — callers get consistent
/// error regardless of which implementation (NEON or scalar) was attempted.
pub fn cosine_scalar(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    if a.len() != b.len() {
        return Err(EmbeddingError::dimension_mismatch(a.len(), b.len()));
    }
    Ok(a.iter().zip(b.iter()).map(|(x, y)| x * y).sum())
}

// ─── Public API (routing) ────────────────────────────────────────────────────

/// Normalize using best available SIMD, with scalar fallback.
/// ISSUE-007: Returns Result — zero/near-zero vector is Err(EmbeddingErrorKind::ZeroVector).
///
/// On aarch64 with NEON: tries NEON first (unsafe but fast), falls back to scalar.
/// On other arches: scalar only.
#[cfg(neon_available)]
pub fn normalize_simd(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    if vec.len() >= 4 && vec.len() % 4 == 0 {
        match unsafe { normalize_neon(vec) } {
            Ok(true) => return Ok(true),
            // near-zero vector — don't double-compute in scalar fallback
            Ok(false) => return Err(EmbeddingError::zero_vector(vec.len())),
            Err(_e) => {
                // NEON precondition failure — fall through to scalar
            }
        }
    }
    // Scalar path: handles both length-precondition fallback and near-zero case
    normalize_scalar(vec)
}

/// Scalar-only normalize for non-aarch64 platforms or aarch64 without NEON.
#[cfg(not(neon_available))]
pub fn normalize_simd(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
    normalize_scalar(vec)
}

/// Compute cosine similarity using best available SIMD.
///
/// On aarch64 with NEON: tries NEON first (unsafe but fast), falls back to scalar.
/// On other arches: scalar only.
#[cfg(neon_available)]
pub fn cosine_simd(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    if a.len() >= 4 && a.len() % 4 == 0 {
        if let Ok(score) = unsafe { cosine_neon(a, b) } {
            return Ok(score);
        }
    }
    cosine_scalar(a, b)
}

/// Scalar-only cosine for non-aarch64 platforms or aarch64 without NEON.
#[cfg(not(neon_available))]
pub fn cosine_simd(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
    cosine_scalar(a, b)
}

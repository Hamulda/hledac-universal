//! Accelerate Module for Hledac — FFI bindings to Apple's Accelerate framework (vDSP/BNNS).
//!
//! ## Architecture
//!
//! ```text
//! Python (NER Engine)          Rust Accelerate FFI              Accelerate/vDSP
//! ────────────────────────────────────────────────────────────────────────
//! batch_cosine(emb1, emb2) ──► accelerate_batch_cosine()  ──► vDSP_dotpr()
//!                          ├── cosine_similarity()             vDSP_normalize()
//!                          └── batch_normalize()              BNNS (Neural Network)
//! ```
//!
//! ## Why Accelerate for NER?
//!
//! NER engine needs fast cosine similarity computation for:
//!   - Entity embedding vs. known entity comparison
//!   - Batch cosine across (batch_size, hidden_dim) matrices
//!   - vDSP provides 5-10× speedup over naive Python loops
//!
//! ## NER Integration Point
//!
//! brain/ner_engine.py → batch_cosine_scores() → Rust accelerate_batch_cosine()
//!
//! ## Feature Gate
//!
//! Always compiled (no feature gate) — uses only system frameworks.
//! On non-macOS: provides scalar fallback with same API.
//!
//! ## M1 8GB Constraints
//!
//! - vDSP works on all Apple Silicon (M1/M2/M3)
//! - No GPU memory required (CPU-based)
//! - BNNS for neural network ops (not used in current NER flow)
//!
//! ## crates.io Alternatives Considered
//!
//! No direct Rust crates provide vDSP FFI. Options:
//!   1. `accelerate` crate — provides BLAS/LAPACK bindings, but no vDSP-specific
//!   2. `cblas` crate — C BLAS bindings, but no vDSP
//!   3. Raw FFI to Accelerate framework — SELECTED
//!
//! Raw FFI approach:
//!   - Links against Accelerate.framework (system framework, always available)
//!   - Provides vDSP functions: dotpr, normalize, maxmg
//!   - Same ABI as Apple C implementation

use parking_lot::RwLock;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::LazyLock;

/// Runtime-detected vDSP availability.
///
/// On macOS 26.5+ (Darwin 25.5+) Apple removed vDSP symbols from
/// Accelerate.framework. Static #[link] doesn't catch this at compile
/// time — we detect at runtime via dladdr() resolution.
static VDSP_AVAILABLE: LazyLock<bool> = LazyLock::new(|| {
    #[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
    {
        // dladdr(addr, &mut Dl_info) returns 0 if symbol not found.
        // vDSP_dotpr is the canonical entry point we always call first.
        let mut info: libc::Dl_info = unsafe { std::mem::zeroed() };
        // SAFETY: dladdr is async-signal-safe; info is valid for output.
        unsafe { libc::dladdr(vDSP_ffi::vDSP_dotpr as *const libc::c_void, &mut info) != 0 }
    }
    #[cfg(any(not(target_os = "macos"), vdsp_unavailable))]
    {
        false
    }
});

/// Accelerate/vDSP function availability
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AccelerateBackend {
    /// Using Apple Accelerate vDSP (available and runtime-detected)
    VDSP,
    /// Using scalar fallback (Linux/Windows or macOS 26.5+)
    Scalar,
}

impl Default for AccelerateBackend {
    fn default() -> Self {
        if *VDSP_AVAILABLE {
            AccelerateBackend::VDSP
        } else {
            AccelerateBackend::Scalar
        }
    }
}

/// Accelerate-specific errors
#[derive(Debug, Clone)]
pub enum AccelerateError {
    DimensionMismatch { expected: usize, actual: usize },
    EmptyInput,
    BackendNotAvailable,
    FFIFailed(String),
}

impl std::fmt::Display for AccelerateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccelerateError::DimensionMismatch { expected, actual } => {
                write!(
                    f,
                    "Dimension mismatch: expected {}, got {}",
                    expected, actual
                )
            }
            AccelerateError::EmptyInput => write!(f, "Input arrays cannot be empty"),
            AccelerateError::BackendNotAvailable => {
                write!(f, "Accelerate backend not available on this platform")
            }
            AccelerateError::FFIFailed(msg) => {
                write!(f, "Accelerate FFI call failed: {}", msg)
            }
        }
    }
}

impl std::error::Error for AccelerateError {}

/// Telemetry for Accelerate operations
static ACCELERATE_TELEMETRY: LazyLock<RwLock<AccelerateTelemetry>> =
    LazyLock::new(|| RwLock::new(AccelerateTelemetry::default()));

#[derive(Default)]
pub struct AccelerateTelemetry {
    pub cosine_calls: u64,
    pub cosine_pairs: u64,
    pub normalize_calls: u64,
    pub v_dsp_fallback_scalar: u64,
    pub errors: u64,
}

/// Get current backend type as string.
/// Uses runtime detection (VDSP_AVAILABLE) — correct even on macOS 26.5+.
fn get_backend_str() -> &'static str {
    if *VDSP_AVAILABLE {
        "vDSP"
    } else {
        "scalar"
    }
}

// ─── vDSP FFI declarations ────────────────────────────────────────────────────
//
// These are raw FFI bindings to Apple's Accelerate framework vDSP functions.
// vDSP is part of the Accelerate framework and provides vector/matrix math.
//
// Gated by vdsp_unavailable cfg (set by build.rs on Darwin 25.5+).
// When vDSP is unavailable (macOS 26.5+), this module is not compiled
// and AccelerateBackend::VDSP falls back to scalar at runtime.

#[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
#[allow(non_snake_case, nonstandard_style)]
mod vDSP_ffi {
    use libc::c_float;

    // vDSP_dotpr: vector dot product
    // float vDSP_dotpr(const float *a, vDSP_Stride aStride, const float *b, vDSP_Stride bStride, float *c, vDSP_Length n);
    #[link(name = "Accelerate", kind = "framework")]
    extern "C" {
        pub fn vDSP_dotpr(
            a: *const c_float,
            a_stride: libc::c_long,
            b: *const c_float,
            b_stride: libc::c_long,
            c: *mut c_float,
            n: libc::c_long,
        );

        pub fn vDSP_normalize(
            a: *const c_float,
            a_stride: libc::c_long,
            b: *mut c_float,
            b_stride: libc::c_long,
            n: libc::c_long,
        ) -> libc::c_long;

        pub fn vDSP_maxmg(
            a: *const c_float,
            a_stride: libc::c_long,
            c: *mut c_float,
            n: libc::c_long,
        );

        pub fn vDSP_vsmul(
            a: *const c_float,
            a_stride: libc::c_long,
            b: *const c_float,
            c: *mut c_float,
            c_stride: libc::c_long,
            n: libc::c_long,
        );
    }
}

/// Compute cosine similarity between two vectors using vDSP.
///
/// Args:
///     a: First vector (len must match b)
///     b: Second vector (len must match a)
///     normalize: If true, normalize both vectors before dot product
///
/// Returns: Cosine similarity score (between -1 and 1)
#[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
#[allow(non_snake_case)]
fn vDSP_cosine(a: &[f32], b: &[f32], normalize: bool) -> Result<f32, AccelerateError> {
    if a.is_empty() || b.is_empty() {
        return Err(AccelerateError::EmptyInput);
    }
    if a.len() != b.len() {
        return Err(AccelerateError::DimensionMismatch {
            expected: a.len(),
            actual: b.len(),
        });
    }

    let n = a.len() as libc::c_long;
    let mut result = vec![0.0_f32; 1];

    if normalize {
        // Normalize a: L2 norm = sqrt(sum(x^2))
        let a_norm_val = vDSP_l2_norm(a)?;
        let inv_a_norm = a_norm_val.recip();

        // Normalize b: L2 norm = sqrt(sum(x^2))
        let b_norm_val = vDSP_l2_norm(b)?;
        let inv_b_norm = b_norm_val.recip();

        // Scale both vectors and compute dot product
        let mut a_scaled = a.to_vec();
        let mut b_scaled = b.to_vec();
        unsafe {
            vDSP_ffi::vDSP_vsmul(a.as_ptr(), 1, &inv_a_norm, a_scaled.as_mut_ptr(), 1, n);
            vDSP_ffi::vDSP_vsmul(b.as_ptr(), 1, &inv_b_norm, b_scaled.as_mut_ptr(), 1, n);
            vDSP_ffi::vDSP_dotpr(
                a_scaled.as_ptr(),
                1,
                b_scaled.as_ptr(),
                1,
                result.as_mut_ptr(),
                n,
            );
        }
        Ok(result[0])
    } else {
        // Direct dot product
        unsafe {
            vDSP_ffi::vDSP_dotpr(a.as_ptr(), 1, b.as_ptr(), 1, result.as_mut_ptr(), n);
        }
        Ok(result[0])
    }
}

/// Scalar fallback for cosine similarity.
fn scalar_cosine(a: &[f32], b: &[f32], normalize: bool) -> Result<f32, AccelerateError> {
    if a.is_empty() || b.is_empty() {
        return Err(AccelerateError::EmptyInput);
    }
    if a.len() != b.len() {
        return Err(AccelerateError::DimensionMismatch {
            expected: a.len(),
            actual: b.len(),
        });
    }

    if normalize {
        // L2 normalize a
        let a_norm: f32 = a.iter().map(|x| x * x).sum();
        let a_norm = a_norm.sqrt().recip();

        // L2 normalize b
        let b_norm: f32 = b.iter().map(|x| x * x).sum();
        let b_norm = b_norm.sqrt().recip();

        // Dot product of normalized vectors
        let sum: f32 = a
            .iter()
            .zip(b.iter())
            .map(|(x, y)| x * a_norm * y * b_norm)
            .sum();
        Ok(sum)
    } else {
        // Direct dot product
        let sum: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
        Ok(sum)
    }
}

/// Compute L2 norm of a vector using vDSP.
///
/// vDSP does not have a direct L2 norm function, so we compute it as:
///   L2 = sqrt(sum(x^2)) = sqrt(dot(x, x))
///
/// Uses vDSP_maxmg for fast L∞ norm check to detect near-zero vectors
/// before the more expensive dot product — saves one vDSP_dotpr call
/// when the vector is effectively zero (common in NER embedding sparse arrays).
#[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
#[allow(non_snake_case)]
fn vDSP_l2_norm(a: &[f32]) -> Result<f32, AccelerateError> {
    if a.is_empty() {
        return Err(AccelerateError::EmptyInput);
    }

    let n = a.len() as libc::c_long;

    // Fast L∞ norm check via vDSP_maxmg — O(n) but avoids expensive dotpr
    // for near-zero vectors (common in NER embedding sparse arrays)
    let mut max_val = vec![0.0_f32; 1];
    unsafe {
        vDSP_ffi::vDSP_maxmg(a.as_ptr(), 1, max_val.as_mut_ptr(), n);
    }
    if max_val[0] < 1e-7 {
        // Near-zero vector — return small epsilon to avoid div-by-zero in callers
        return Ok(1e-8);
    }

    // L2 norm = sqrt(sum(x^2)) = sqrt(dot(x, x))
    let mut result = vec![0.0_f32; 1];
    unsafe {
        vDSP_ffi::vDSP_dotpr(a.as_ptr(), 1, a.as_ptr(), 1, result.as_mut_ptr(), n);
    }

    Ok(result[0].sqrt())
}

/// Scalar L2 norm: sqrt(sum(x^2)) with L∞-based near-zero detection.
///
/// Matches vDSP_l2_norm behavior: uses max(|x|) < 1e-7 threshold
/// to detect near-zero vectors and returns 1e-8 epsilon instead.
fn scalar_l2_norm(a: &[f32]) -> Result<f32, AccelerateError> {
    if a.is_empty() {
        return Err(AccelerateError::EmptyInput);
    }

    // Fast L∞ norm check — O(n) single-pass, avoids sqrt for near-zero vectors
    let max_val = a.iter().map(|x| x.abs()).fold(0.0_f32, f32::max);
    if max_val < 1e-7 {
        // Near-zero vector — return epsilon to match vDSP_l2_norm behavior
        return Ok(1e-8);
    }

    let sum_sq: f32 = a.iter().map(|x| x * x).sum();
    Ok(sum_sq.sqrt())
}

// ─── Python-callable functions ────────────────────────────────────────────────

/// Initialize Accelerate subsystem.
///
/// Returns: (available: bool, backend: str, error_message: Option<String>)
#[pyfunction]
pub fn init() -> (bool, String, Option<String>) {
    let backend = get_backend_str();
    let available = backend == "vDSP";

    {
        let mut telemetry = ACCELERATE_TELEMETRY.write();
        *telemetry = AccelerateTelemetry::default();
    }

    if available {
        (true, backend.to_string(), None)
    } else {
        (
            false,
            backend.to_string(),
            Some("Accelerate vDSP not available".to_string()),
        )
    }
}

/// Get Accelerate backend info.
///
/// Returns: (backend_name: str, available: bool)
#[pyfunction]
pub fn get_backend_info() -> (String, bool) {
    let backend = get_backend_str();
    (backend.to_string(), backend == "vDSP")
}

/// Check if vDSP is available.
#[pyfunction]
pub fn is_vdsp_available() -> bool {
    get_backend_str() == "vDSP"
}

/// Compute cosine similarity between two vectors.
///
/// Args:
///     a: First vector as f32 array
///     b: Second vector as f32 array (must match length of a)
///     normalize: If True, L2-normalize vectors before computing cosine
///
/// Returns: Cosine similarity (between -1 and 1)
#[pyfunction]
pub fn cosine_similarity(a: Vec<f32>, b: Vec<f32>, normalize: bool) -> Result<f32, PyErr> {
    // Update telemetry
    {
        let mut telemetry = ACCELERATE_TELEMETRY.write();
        telemetry.cosine_calls += 1;
        telemetry.cosine_pairs += 1;
    }

    #[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
    {
        vDSP_cosine(&a, &b, normalize)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    #[cfg(any(not(target_os = "macos"), vdsp_unavailable))]
    {
        scalar_cosine(&a, &b, normalize)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }
}

/// Batch cosine similarity between query vectors and candidate vectors.
///
/// Computes cosine similarity for each (query, candidate) pair.
///
/// Args:
///     queries: Query vectors (num_queries, hidden_dim) flattened row-major
///     candidates: Candidate vectors (num_candidates, hidden_dim) flattened row-major
///     num_queries: Number of query vectors
///     num_candidates: Number of candidate vectors
///     hidden_dim: Dimension of each vector
///     normalize: If True, L2-normalize all vectors before computing
///
/// Returns: (num_queries * num_candidates,) cosine scores row-major
#[pyfunction]
pub fn batch_cosine_similarity(
    queries: Vec<f32>,
    candidates: Vec<f32>,
    num_queries: usize,
    num_candidates: usize,
    hidden_dim: usize,
    normalize: bool,
) -> Result<Vec<f32>, PyErr> {
    if queries.is_empty() || candidates.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Input arrays cannot be empty",
        ));
    }

    let expected_queries = num_queries * hidden_dim;
    let expected_candidates = num_candidates * hidden_dim;

    if queries.len() != expected_queries {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Queries size mismatch: expected {}, got {}",
            expected_queries,
            queries.len()
        )));
    }

    if candidates.len() != expected_candidates {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Candidates size mismatch: expected {}, got {}",
            expected_candidates,
            candidates.len()
        )));
    }

    // Update telemetry
    {
        let mut telemetry = ACCELERATE_TELEMETRY.write();
        telemetry.cosine_calls += 1;
        telemetry.cosine_pairs = telemetry
            .cosine_pairs
            .saturating_add((num_queries * num_candidates) as u64);
    }

    let mut results = vec![0.0_f32; num_queries * num_candidates];

    #[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
    {
        // Pre-normalize all vectors using vDSP_normalize — single vDSP call per vector.
        // vDSP_normalize computes L2 norm internally and writes normalized output directly.
        let queries_norm = if normalize {
            let mut norm_queries = vec![0.0_f32; num_queries * hidden_dim];
            for i in 0..num_queries {
                let start = i * hidden_dim;
                let n = hidden_dim as libc::c_long;
                unsafe {
                    vDSP_ffi::vDSP_normalize(
                        queries[start..].as_ptr(),
                        1,
                        norm_queries[start..].as_mut_ptr(),
                        1,
                        n,
                    );
                }
            }
            Some(norm_queries)
        } else {
            None
        };

        let candidates_norm = if normalize {
            let mut norm_candidates = vec![0.0_f32; num_candidates * hidden_dim];
            for i in 0..num_candidates {
                let start = i * hidden_dim;
                let n = hidden_dim as libc::c_long;
                unsafe {
                    vDSP_ffi::vDSP_normalize(
                        candidates[start..].as_ptr(),
                        1,
                        norm_candidates[start..].as_mut_ptr(),
                        1,
                        n,
                    );
                }
            }
            Some(norm_candidates)
        } else {
            None
        };

        let q_ref = queries_norm.as_ref().unwrap_or(&queries);
        let c_ref = candidates_norm.as_ref().unwrap_or(&candidates);

        for qi in 0..num_queries {
            for ci in 0..num_candidates {
                let q_start = qi * hidden_dim;
                let c_start = ci * hidden_dim;
                let q_slice = &q_ref[q_start..q_start + hidden_dim];
                let c_slice = &c_ref[c_start..c_start + hidden_dim];

                let score = vDSP_cosine(q_slice, c_slice, false).unwrap_or(0.0);
                results[qi * num_candidates + ci] = score;
            }
        }
    }

    #[cfg(any(not(target_os = "macos"), vdsp_unavailable))]
    {
        // When normalize=true, scalar_cosine does its own normalization internally.
        // Do NOT pre-normalize here (unlike macOS vDSP path) to avoid double-normalization.
        let normalize_flag = normalize;
        for qi in 0..num_queries {
            for ci in 0..num_candidates {
                let q_start = qi * hidden_dim;
                let c_start = ci * hidden_dim;
                let q_slice = &queries[q_start..q_start + hidden_dim];
                let c_slice = &candidates[c_start..c_start + hidden_dim];

                let score = scalar_cosine(q_slice, c_slice, normalize_flag).unwrap_or(0.0);
                results[qi * num_candidates + ci] = score;
            }
        }

        {
            let mut telemetry = ACCELERATE_TELEMETRY.write();
            telemetry.v_dsp_fallback_scalar += 1;
        }
    }

    Ok(results)
}

/// Normalize a batch of vectors to unit length (L2 normalization).
///
/// Args:
///     vectors: (batch_size, hidden_dim) flattened row-major
///     batch_size: Number of vectors
///     hidden_dim: Dimension of each vector
///
/// Returns: Normalized vectors (same shape)
#[pyfunction]
pub fn batch_normalize(
    vectors: Vec<f32>,
    batch_size: usize,
    hidden_dim: usize,
) -> Result<Vec<f32>, PyErr> {
    if vectors.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Input array cannot be empty",
        ));
    }

    let expected = batch_size * hidden_dim;
    if vectors.len() != expected {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Vectors size mismatch: expected {}, got {}",
            expected,
            vectors.len()
        )));
    }

    {
        let mut telemetry = ACCELERATE_TELEMETRY.write();
        telemetry.normalize_calls += 1;
    }

    let mut result = vectors.clone();

    #[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
    {
        // Use vDSP_normalize — computes L2 norm internally, writes normalized output.
        // Single vDSP call per vector (vs. vDSP_l2_norm + manual scalar div loop).
        for i in 0..batch_size {
            let start = i * hidden_dim;
            let end = start + hidden_dim;
            let slice_in = &vectors[start..end];
            let slice_out = &mut result[start..end];
            let n = hidden_dim as libc::c_long;
            unsafe {
                vDSP_ffi::vDSP_normalize(slice_in.as_ptr(), 1, slice_out.as_mut_ptr(), 1, n);
            }
        }
    }

    #[cfg(any(not(target_os = "macos"), vdsp_unavailable))]
    {
        for i in 0..batch_size {
            let start = i * hidden_dim;
            let end = start + hidden_dim;
            let slice = &mut result[start..end];
            let norm = scalar_l2_norm(slice).unwrap_or(1.0);
            let inv = 1.0 / (norm + 1e-8);
            for v in slice.iter_mut() {
                *v *= inv;
            }
        }

        {
            let mut telemetry = ACCELERATE_TELEMETRY.write();
            telemetry.v_dsp_fallback_scalar += 1;
        }
    }

    Ok(result)
}

/// Get Accelerate telemetry counters.
///
/// Returns: dict with cosine_calls, cosine_pairs, normalize_calls, vDSP_fallback_scalar, errors
#[pyfunction]
pub fn get_telemetry() -> HashMap<String, u64> {
    let telemetry = ACCELERATE_TELEMETRY.read();
    let mut result = HashMap::new();
    result.insert("cosine_calls".to_string(), telemetry.cosine_calls);
    result.insert("cosine_pairs".to_string(), telemetry.cosine_pairs);
    result.insert("normalize_calls".to_string(), telemetry.normalize_calls);
    result.insert(
        "v_dsp_fallback_scalar".to_string(),
        telemetry.v_dsp_fallback_scalar,
    );
    result.insert("errors".to_string(), telemetry.errors);
    result
}

/// Reset Accelerate telemetry counters.
#[pyfunction]
pub fn reset_telemetry() {
    let mut telemetry = ACCELERATE_TELEMETRY.write();
    *telemetry = AccelerateTelemetry::default();
}

// ─── Module registration ──────────────────────────────────────────────────────

/// Register Accelerate module functions with PyO3 module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(init, m)?)?;
    m.add_function(wrap_pyfunction!(get_backend_info, m)?)?;
    m.add_function(wrap_pyfunction!(is_vdsp_available, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(batch_cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(batch_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(get_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(reset_telemetry, m)?)?;

    // Constants
    m.add("BACKEND_VDSP", "vDSP")?;
    m.add("BACKEND_SCALAR", "scalar")?;

    Ok(())
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_backend_detection() {
        let (backend, available) = get_backend_info();
        #[cfg(all(target_os = "macos", not(vdsp_unavailable)))]
        {
            assert_eq!(backend, "vDSP");
            assert!(available);
        }
        #[cfg(any(not(target_os = "macos"), vdsp_unavailable))]
        {
            // scalar fallback on Linux/Windows or macOS 26.5+ (Darwin 25.5+)
            assert_eq!(backend, "scalar");
            assert!(!available);
        }
    }

    #[test]
    fn test_cosine_similarity_same_vector() {
        let a = vec![1.0, 0.0, 0.0];
        let result = scalar_cosine(&a, &a, false);
        assert!(result.is_ok());
        assert!((result.unwrap() - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let a = vec![1.0, 0.0, 0.0];
        let b = vec![0.0, 1.0, 0.0];
        let result = scalar_cosine(&a, &b, false);
        assert!(result.is_ok());
        assert!((result.unwrap() - 0.0).abs() < 1e-6);
    }

    #[test]
    fn test_cosine_similarity_normalized() {
        let a = vec![2.0, 0.0, 0.0]; // Same direction, larger magnitude
        let b = vec![1.0, 0.0, 0.0];
        let result = scalar_cosine(&a, &b, true).unwrap();
        assert!((result - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_dimension_mismatch() {
        let a = vec![1.0, 0.0, 0.0];
        let b = vec![1.0, 0.0];
        let result = scalar_cosine(&a, &b, false);
        assert!(result.is_err());
    }

    #[test]
    fn test_empty_input() {
        let a: Vec<f32> = vec![];
        let b = vec![1.0, 0.0, 0.0];
        let result = scalar_cosine(&a, &b, false);
        assert!(result.is_err());
    }

    #[test]
    fn test_l2_norm() {
        let a = vec![3.0, 4.0, 0.0]; // L2 norm = 5
        let result = scalar_l2_norm(&a);
        assert!(result.is_ok());
        assert!((result.unwrap() - 5.0).abs() < 1e-6);
    }

    #[test]
    fn test_telemetry() {
        reset_telemetry();
        let telemetry = get_telemetry();
        assert_eq!(telemetry.get("cosine_calls"), Some(&0));
        assert_eq!(telemetry.get("errors"), Some(&0));
    }
}

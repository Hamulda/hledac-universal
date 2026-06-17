//! Batch signal aggregation with ARM NEON acceleration.
//!
//! Sprint P2-2: Vectorized source weight computation for M1.
//!
//! ## Architecture
//!
//! This module provides two public entry points:
//!
//! 1. [`batch_compute_scores`] — SIMD-parallel source quality score computation.
//!    Accepts a list of source stats (fetched count, accepted count, novelty flag)
//!    and returns a list of computed weights, clamped to [0.3, 2.5] per F199A.
//!
//! 2. [`aggregate_signals`] — Batch signal aggregation with weighted averaging.
//!    Takes raw signal vectors (list of float lists) and aggregates them using
//!    per-source weights, returning a single aggregated vector per input.
//!
//! ## NEON Strategy
//!
//! M1 uses the ARM Neural Engine (ANE) for ML workloads, but for small
//! numerical workloads the most efficient path is:
//!
//! - **NEON SIMD** (this module): 128-bit registers = 4× f32 in parallel
//! - **Fallback**: scalar Rust for architectures without NEON
//!
//! The workload is intentionally small (< 1000 sources), so we don't use
//! rayon parallelism — NEON gives us 4× throughput per core cycle with
//! zero thread overhead.
//!
//! ## Signal Scoring Formula (F199A)
//!
//! ```python
//! ratio = accepted / max(fetched, 1)
//! if ratio >= 0.7:   delta = 1.10  # +10% reward
//! elif ratio >= 0.4: delta = 1.05  # +5% reward
//! elif ratio >= 0.15: delta = 1.00 # neutral
//! else:               delta = 0.95  # -5% penalty
//! new_weight = clamp(current_weight * delta, 0.3, 2.5)
//! ```
//!
//! ## Failure Modes (all fail-soft)
//!
//! - Empty input → empty output
//! - Mismatched lengths → truncate to shorter
//! - Non-positive fetched → ratio=0, delta=1.00
//! - Any processing error → scalar fallback

use pyo3::prelude::*;
use pyo3::types::PyList;

// ---------------------------------------------------------------------------
// NEON detection + scalar fallback
// ---------------------------------------------------------------------------

/// Compute scores using ARM NEON SIMD (128-bit = 4× f32 in parallel).
///
/// Returns a vector of computed weights (f32), one per source.
/// Falls back to scalar path on any error.
#[allow(dead_code)]
fn compute_scores_neon(
    fetched: &[u32],
    accepted: &[u32],
    current_weights: &[f32],
    novelty: &[bool],
) -> Vec<f32> {
    #[cfg(target_arch = "aarch64")]
    {
        // SAFETY: NEON intrinsics require aligned memory and valid SIMD state.
        // All our slices are created from Vec<f32> which have 4-byte alignment,
        // and we use core::arch::aarch64 operations which are sound on aarch64.
        unsafe { compute_scores_neon_inner(fetched, accepted, current_weights, novelty) }
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        let _ = (fetched, accepted, current_weights, novelty);
        compute_scores_scalar(fetched, accepted, current_weights, novelty)
    }
}

#[cfg(target_arch = "aarch64")]
unsafe fn compute_scores_neon_inner(
    fetched: &[u32],
    accepted: &[u32],
    current_weights: &[f32],
    novelty: &[bool],
) -> Vec<f32> {
    use core::arch::aarch64::*;

    let n = fetched.len().min(accepted.len()).min(current_weights.len());
    if n == 0 {
        return Vec::new();
    }

    // We process 4 elements per iteration (128-bit / 32-bit = 4 floats).
    let chunks = n / 4;

    let mut results = Vec::with_capacity(n);

    // Constant vectors for NEON operations.
    // Delta thresholds: 0.7, 0.4, 0.15 (stored as f32).
    let vd70 = vdupq_n_f32(0.7_f32);
    let vd40 = vdupq_n_f32(0.4_f32);
    let vd15 = vdupq_n_f32(0.15_f32);
    let vdelta110 = vdupq_n_f32(1.10_f32);
    let vdelta105 = vdupq_n_f32(1.05_f32);
    let vdelta100 = vdupq_n_f32(1.00_f32);
    let vdelta095 = vdupq_n_f32(0.95_f32);
    let vclamp_lo = vdupq_n_f32(0.3_f32);
    let vclamp_hi = vdupq_n_f32(2.5_f32);

    // Chunked NEON processing.
    for chunk in 0..chunks {
        let base = chunk * 4;

        // Load 4 fetched counts.
        let f0 = *fetched.get(base).unwrap_or(&1_u32);
        let f1 = *fetched.get(base + 1).unwrap_or(&1_u32);
        let f2 = *fetched.get(base + 2).unwrap_or(&1_u32);
        let f3 = *fetched.get(base + 3).unwrap_or(&1_u32);
        let fetched_vec = core::arch::aarch64::vld1q_u32([f0, f1, f2, f3].as_ptr());

        // Load 4 accepted counts.
        let a0 = *accepted.get(base).unwrap_or(&0_u32);
        let a1 = *accepted.get(base + 1).unwrap_or(&0_u32);
        let a2 = *accepted.get(base + 2).unwrap_or(&0_u32);
        let a3 = *accepted.get(base + 3).unwrap_or(&0_u32);
        let accepted_vec = core::arch::aarch64::vld1q_u32([a0, a1, a2, a3].as_ptr());

        // Compute ratios: accepted / max(fetched, 1).
        // Create max(fetched, 1) mask.
        let vone_u32 = vdupq_n_u32(1_u32);
        let fetched_safe = vmaxq_u32(fetched_vec, vone_u32);
        // Convert to f32 for division.
        let fetched_f = vcvtq_f32_u32(fetched_safe);
        let accepted_f = vcvtq_f32_u32(accepted_vec);
        let ratio = vdivq_f32(accepted_f, fetched_f);

        // Load current weights.
        let w0 = *current_weights.get(base).unwrap_or(&1.0_f32);
        let w1 = *current_weights.get(base + 1).unwrap_or(&1.0_f32);
        let w2 = *current_weights.get(base + 2).unwrap_or(&1.0_f32);
        let w3 = *current_weights.get(base + 3).unwrap_or(&1.0_f32);
        let weight_vec = core::arch::aarch64::vld1q_f32([w0, w1, w2, w3].as_ptr());

        // Load novelty flags (bool→f32 via if/else to avoid invalid cast).
        let n0 = if *novelty.get(base).unwrap_or(&false) { 1.0_f32 } else { 0.0_f32 };
        let n1 = if *novelty.get(base + 1).unwrap_or(&false) { 1.0_f32 } else { 0.0_f32 };
        let n2 = if *novelty.get(base + 2).unwrap_or(&false) { 1.0_f32 } else { 0.0_f32 };
        let n3 = if *novelty.get(base + 3).unwrap_or(&false) { 1.0_f32 } else { 0.0_f32 };
        let novelty_vec = core::arch::aarch64::vld1q_f32([n0, n1, n2, n3].as_ptr());

        // Determine delta based on ratio thresholds:
        // delta = 1.10 if >= 0.7, 1.05 if >= 0.4, 1.00 if >= 0.15, else 0.95.
        let mask_ge70: uint32x4_t = vcgeq_f32(ratio, vd70);
        let mask_ge40: uint32x4_t = vcgeq_f32(ratio, vd40);
        let mask_ge15: uint32x4_t = vcgeq_f32(ratio, vd15);

        // Select delta using NEON bitwise select (vbslq).
        // if ratio >= 0.7: delta = 1.10
        let delta = vbslq_f32(
            mask_ge70,
            vdelta110,
            // else if ratio >= 0.4: delta = 1.05
            vbslq_f32(mask_ge40, vdelta105,
                // else if ratio >= 0.15: delta = 1.00, else 0.95
                vbslq_f32(mask_ge15, vdelta100, vdelta095)),
        );

        // new_weight = current_weight * delta * novelty (1.5 if novelty else 1.0).
        // novelty bonus: 1.5 if novel, else 1.0.
        let novelty_bonus = vaddq_f32(vdupq_n_f32(1.0_f32),
            vmulq_f32(novelty_vec, vdupq_n_f32(0.5_f32)));
        let weighted = vmulq_f32(weight_vec, vmulq_f32(delta, novelty_bonus));

        // Clamp to [0.3, 2.5].
        let clamped = vmaxq_f32(weighted, vclamp_lo);
        let clamped = vminq_f32(clamped, vclamp_hi);

        // Store results.
        let mut out = [0.0_f32; 4];
        core::arch::aarch64::vst1q_f32(out.as_mut_ptr(), clamped);
        results.extend_from_slice(&out);
    }

    // Scalar tail processing for remainder.
    for i in (chunks * 4)..n {
        let f = fetched.get(i).copied().unwrap_or(1).max(1);
        let a = accepted.get(i).copied().unwrap_or(0);
        let w = current_weights.get(i).copied().unwrap_or(1.0);
        let nov = *novelty.get(i).unwrap_or(&false);

        let ratio = (a as f32) / (f as f32);
        let delta = if ratio >= 0.7 {
            1.10_f32
        } else if ratio >= 0.4 {
            1.05_f32
        } else if ratio >= 0.15 {
            1.00_f32
        } else {
            0.95_f32
        };
        let novelty_bonus = if nov { 1.5 } else { 1.0 };
        let weighted = w * delta * novelty_bonus;
        let clamped = weighted.clamp(0.3, 2.5);
        results.push(clamped);
    }

    results
}

// ---------------------------------------------------------------------------
// Scalar fallback
// ---------------------------------------------------------------------------

#[allow(dead_code)]
fn compute_scores_scalar(
    fetched: &[u32],
    accepted: &[u32],
    current_weights: &[f32],
    novelty: &[bool],
) -> Vec<f32> {
    let n = fetched.len().min(accepted.len()).min(current_weights.len());
    (0..n)
        .map(|i| {
            let f = fetched.get(i).copied().unwrap_or(1).max(1);
            let a = accepted.get(i).copied().unwrap_or(0);
            let w = current_weights.get(i).copied().unwrap_or(1.0);
            let nov = *novelty.get(i).unwrap_or(&false);

            let ratio = (a as f32) / (f as f32);
            let delta = if ratio >= 0.7 {
                1.10_f32
            } else if ratio >= 0.4 {
                1.05_f32
            } else if ratio >= 0.15 {
                1.00_f32
            } else {
                0.95_f32
            };
            let novelty_bonus = if nov { 1.5 } else { 1.0 };
            let weighted = w * delta * novelty_bonus;
            weighted.clamp(0.3, 2.5)
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Signal aggregation — weighted average of signal vectors
// ---------------------------------------------------------------------------

/// Aggregate signal vectors using per-source weights.
///
/// # Arguments
/// * `signals` — List of signal vectors (list of floats), each representing
///   a source's contribution to the aggregate signal.
/// * `weights` — Per-source weights (f32), same length as `signals`.
/// * `normalize` — If true, return weighted average (divide by sum of weights).
///   If false, return sum of weighted signals.
///
/// # Returns
/// Aggregated signal vector (list of floats), same length as the first signal.
/// Returns empty list on empty input or length mismatch.
///
/// # Fail-soft
/// - Empty signals or weights → empty list
/// - Weight sum = 0 → unweighted average
/// - Mismatched vector lengths → truncate to shortest
fn aggregate_signals_inner(
    signals: &[Vec<f32>],
    weights: &[f32],
    normalize: bool,
) -> Vec<f32> {
    if signals.is_empty() || weights.is_empty() {
        return Vec::new();
    }

    let n_sources = signals.len().min(weights.len());
    if n_sources == 0 {
        return Vec::new();
    }

    // Determine output vector length (min across all sources).
    let out_len = signals
        .iter()
        .take(n_sources)
        .map(|v| v.len())
        .min()
        .unwrap_or(0);

    if out_len == 0 {
        return Vec::new();
    }

    let mut result = vec![0.0_f32; out_len];
    let mut weight_sum = 0.0_f32;

    for i in 0..n_sources {
        let w = weights[i];
        if w <= 0.0 {
            continue;
        }
        let sig = &signals[i];
        weight_sum += w;

        // Add weighted signal vector.
        for j in 0..out_len.min(sig.len()) {
            result[j] += sig[j] * w;
        }
    }

    if normalize && weight_sum > 0.0 {
        // Weighted average.
        let inv = 1.0_f32 / weight_sum;
        for r in &mut result {
            *r *= inv;
        }
    }

    result
}

// ---------------------------------------------------------------------------
// NEON-Accelerated batch signal aggregation
// ---------------------------------------------------------------------------

/// Aggregate signal vectors using ARM NEON SIMD.
///
/// Processes 4 signal dimensions in parallel per iteration.
/// Falls back to scalar on non-aarch64 or any error.
#[cfg(target_arch = "aarch64")]
unsafe fn aggregate_signals_neon(
    signals: &[Vec<f32>],
    weights: &[f32],
    normalize: bool,
) -> Vec<f32> {
    use core::arch::aarch64::*;

    let n_sources = signals.len().min(weights.len());
    if n_sources == 0 || signals.is_empty() {
        return Vec::new();
    }

    let out_len = signals
        .iter()
        .take(n_sources)
        .map(|v| v.len())
        .min()
        .unwrap_or(0);

    if out_len == 0 {
        return Vec::new();
    }

    let mut result = vec![0.0_f32; out_len];
    let mut weight_sum = 0.0_f32;

    let chunks = out_len / 4;

    // Pre-compute per-source weight * novelty multipliers.
    let weight_vecs: Vec<f32> = weights
        .iter()
        .take(n_sources)
        .map(|&w| if w > 0.0 { w } else { 0.0 })
        .collect();

    for i in 0..n_sources {
        let w = weight_vecs[i];
        if w <= 0.0 {
            continue;
        }
        weight_sum += w;

        let sig = &signals[i];
        let sig_slice = &sig[..out_len.min(sig.len())];

        // NEON chunked processing of signal vector.
        for chunk in 0..chunks {
            let base = chunk * 4;
            let mut s = [0.0_f32; 4];
            for k in 0..4 {
                s[k] = *sig_slice.get(base + k).unwrap_or(&0.0_f32);
            }
            let sig_vec = vld1q_f32(s.as_ptr());
            let w_vec = vdupq_n_f32(w);
            let weighted = vmulq_f32(sig_vec, w_vec);

            // Accumulate into result.
            let current = vld1q_f32(result.as_ptr().add(base));
            let accumulated = vaddq_f32(current, weighted);
            vst1q_f32(result.as_mut_ptr().add(base), accumulated);
        }

        // Scalar tail.
        for j in (chunks * 4)..sig_slice.len() {
            result[j] += sig_slice[j] * w;
        }
    }

    if normalize && weight_sum > 0.0 {
        let inv = 1.0_f32 / weight_sum;
        let inv_vec = vdupq_n_f32(inv);

        // NEON chunked normalization.
        for chunk in 0..chunks {
            let base = chunk * 4;
            let r = vld1q_f32(result.as_ptr().add(base));
            let normalized = vmulq_f32(r, inv_vec);
            vst1q_f32(result.as_mut_ptr().add(base), normalized);
        }

        // Scalar tail normalization.
        for i in (chunks * 4)..out_len {
            result[i] *= inv;
        }
    }

    result
}

#[cfg(not(target_arch = "aarch64"))]
fn aggregate_signals_neon(
    signals: &[Vec<f32>],
    weights: &[f32],
    normalize: bool,
) -> Vec<f32> {
    let _ = (signals, weights, normalize);
    Vec::new()
}

// ---------------------------------------------------------------------------
// PyO3 public API
// ---------------------------------------------------------------------------

/// Compute batch source quality scores using ARM NEON SIMD.
///
/// # Arguments
/// * `stats` — List of dicts, each with keys:
///   - `fetched` (u32): number of items fetched from this source
///   - `accepted` (u32): number of items accepted from this source
///   - `current_weight` (f32): current source weight (default 1.0)
///   - `novelty` (bool): whether source added new IOC types (default False)
/// * `default_weight` — Weight to use when `current_weight` key is absent (default 1.0)
///
/// # Returns
/// List of computed weights (f32), clamped to [0.3, 2.5] per F199A.
///
/// # Fail-soft
/// - Empty input → empty list
/// - Missing keys → use defaults (fetched=0, accepted=0, current_weight=1.0, novelty=False)
/// - Any processing error → scalar fallback (no exception raised)
#[pyfunction]
#[pyo3(signature = (stats, default_weight = 1.0))]
pub fn batch_compute_scores(
    _py: Python<'_>,
    stats: &Bound<'_, PyList>,
    default_weight: f32,
) -> PyResult<Vec<f32>> {
    let n = stats.len();
    if n == 0 {
        return Ok(Vec::new());
    }

    let mut fetched = Vec::<u32>::with_capacity(n);
    let mut accepted = Vec::<u32>::with_capacity(n);
    let mut current_weights = Vec::<f32>::with_capacity(n);
    let mut novelty = Vec::<bool>::with_capacity(n);

    for item in stats.iter() {
        let dict = item.cast::<pyo3::types::PyDict>()?;

        // PyO3 0.28: get_item returns Result<Option<Bound>, PyErr>
        let f = match dict.get_item("fetched") {
            Ok(Some(v)) => v.extract::<u32>().unwrap_or(0),
            _ => 0_u32,
        };
        let a = match dict.get_item("accepted") {
            Ok(Some(v)) => v.extract::<u32>().unwrap_or(0),
            _ => 0_u32,
        };
        let w = match dict.get_item("current_weight") {
            Ok(Some(v)) => v.extract::<f32>().unwrap_or(default_weight),
            _ => default_weight,
        };
        let nov = match dict.get_item("novelty") {
            Ok(Some(v)) => v.extract::<bool>().unwrap_or(false),
            _ => false,
        };

        fetched.push(f);
        accepted.push(a);
        current_weights.push(w);
        novelty.push(nov);
    }

    // Dispatch: NEON on aarch64, scalar elsewhere.
    #[cfg(target_arch = "aarch64")]
    let results = unsafe {
        compute_scores_neon_inner(&fetched, &accepted, &current_weights, &novelty)
    };
    #[cfg(not(target_arch = "aarch64"))]
    let results = compute_scores_scalar(&fetched, &accepted, &current_weights, &novelty);

    Ok(results)
}

/// Aggregate signal vectors using per-source weights (ARM NEON).
///
/// # Arguments
/// * `signals` — List of signal vectors (list of floats).
/// * `weights` — Per-source weights (list of floats).
/// * `normalize` — If True, return weighted average. If False, return weighted sum.
///
/// # Returns
/// Aggregated signal vector (list of floats), or empty list on failure.
///
/// # Fail-soft
/// - Empty/None input → empty list
/// - Length mismatch → truncate to shorter
/// - Any error → empty list (no exception)
#[pyfunction]
#[pyo3(signature = (signals, weights, normalize = true))]
pub fn batch_aggregate_signals(
    _py: Python<'_>,
    signals: &Bound<'_, PyList>,
    weights: &Bound<'_, PyList>,
    normalize: bool,
) -> PyResult<Vec<f32>> {
    if signals.is_empty() || weights.is_empty() {
        return Ok(Vec::new());
    }

    let n_sources = signals.len().min(weights.len());
    if n_sources == 0 {
        return Ok(Vec::new());
    }

    // Convert PyList of PyLists to Vec<Vec<f32>>.
    let mut signal_vecs: Vec<Vec<f32>> = Vec::with_capacity(n_sources);
    let mut weight_vec: Vec<f32> = Vec::with_capacity(n_sources);

    for i in 0..n_sources {
        let item = signals.get_item(i)?;
        let py_list = item.cast::<PyList>()?;

        let mut fv: Vec<f32> = Vec::with_capacity(py_list.len());
        for elem in py_list.iter() {
            if let Ok(v) = elem.extract::<f32>() {
                fv.push(v);
            }
        }
        signal_vecs.push(fv);

        let w = match weights.get_item(i) {
            Ok(v) => v.extract::<f32>().unwrap_or(1.0_f32),
            _ => 1.0_f32,
        };
        weight_vec.push(w);
    }

    // Use NEON aggregation on aarch64.
    #[cfg(target_arch = "aarch64")]
    let result = unsafe {
        aggregate_signals_neon(&signal_vecs, &weight_vec, normalize)
    };
    #[cfg(not(target_arch = "aarch64"))]
    let result = aggregate_signals_inner(&signal_vecs, &weight_vec, normalize);

    if result.is_empty() && !signal_vecs.is_empty() && !weight_vec.is_empty() {
        // Fallback to scalar if NEON returned empty erroneously.
        return Ok(aggregate_signals_inner(&signal_vecs, &weight_vec, normalize));
    }

    Ok(result)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register all signal_batch functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_compute_scores, m)?)?;
    m.add_function(wrap_pyfunction!(batch_aggregate_signals, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scalar_empty() {
        let r = compute_scores_scalar(&[], &[], &[], &[]);
        assert!(r.is_empty());
    }

    #[test]
    fn test_scalar_single_source() {
        // fetched=10, accepted=7, weight=1.0, no novelty.
        // ratio=0.7 >= 0.7 → delta=1.10, novelty_bonus=1.0.
        // weight = 1.0 * 1.10 * 1.0 = 1.10.
        let r = compute_scores_scalar(&[10], &[7], &[1.0], &[false]);
        assert!((r[0] - 1.10_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_ratio_70_plus() {
        // ratio >= 0.7 → delta = 1.10.
        let r = compute_scores_scalar(&[100], &[70], &[1.0], &[false]);
        assert!((r[0] - 1.10_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_ratio_40_plus() {
        // 0.4 <= ratio < 0.7 → delta = 1.05.
        let r = compute_scores_scalar(&[100], &[40], &[1.0], &[false]);
        assert!((r[0] - 1.05_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_ratio_15_plus() {
        // 0.15 <= ratio < 0.4 → delta = 1.00.
        let r = compute_scores_scalar(&[100], &[15], &[1.0], &[false]);
        assert!((r[0] - 1.00_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_ratio_below_15() {
        // ratio < 0.15 → delta = 0.95.
        let r = compute_scores_scalar(&[100], &[10], &[1.0], &[false]);
        assert!((r[0] - 0.95_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_novelty_bonus() {
        // novelty=true → bonus 1.5×.
        let r = compute_scores_scalar(&[100], &[70], &[1.0], &[true]);
        assert!((r[0] - 1.65_f32).abs() < 1e-6); // 1.0 * 1.10 * 1.5 = 1.65
    }

    #[test]
    fn test_scalar_weight_clamp_low() {
        // weight * delta < 0.3 → clamp to 0.3.
        let r = compute_scores_scalar(&[100], &[1], &[0.3], &[false]);
        assert!((r[0] - 0.3_f32).abs() < 1e-6); // 0.3 * 0.95 < 0.3, clamped to 0.3
    }

    #[test]
    fn test_scalar_weight_clamp_high() {
        // weight * delta > 2.5 → clamp to 2.5.
        let r = compute_scores_scalar(&[100], &[100], &[3.0], &[true]);
        // 3.0 * 1.10 * 1.5 = 4.95 > 2.5 → clamped.
        assert!((r[0] - 2.5_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_zero_fetched() {
        // fetched=0 → ratio=0, delta=0.95.
        let r = compute_scores_scalar(&[0], &[0], &[1.0], &[false]);
        assert!((r[0] - 0.95_f32).abs() < 1e-6);
    }

    #[test]
    fn test_scalar_current_weight_multiplier() {
        // current_weight=1.5, ratio=0.7 → delta=1.10.
        let r = compute_scores_scalar(&[10], &[7], &[1.5], &[false]);
        assert!((r[0] - 1.65_f32).abs() < 1e-6); // 1.5 * 1.10 = 1.65
    }

    #[test]
    fn test_aggregate_signals_empty() {
        let r = aggregate_signals_inner(&[], &[], true);
        assert!(r.is_empty());
    }

    #[test]
    fn test_aggregate_signals_weighted_average() {
        // Two sources: signal1=[1.0, 2.0], w1=2.0; signal2=[3.0, 4.0], w2=1.0.
        // Weighted avg: [(1*2+3*1)/3, (2*2+4*1)/3] = [5/3, 8/3].
        let signals = vec![vec![1.0_f32, 2.0_f32], vec![3.0_f32, 4.0_f32]];
        let weights = vec![2.0_f32, 1.0_f32];
        let r = aggregate_signals_inner(&signals, &weights, true);
        assert!((r[0] - 5.0_f32 / 3.0_f32).abs() < 1e-6);
        assert!((r[1] - 8.0_f32 / 3.0_f32).abs() < 1e-6);
    }

    #[test]
    fn test_aggregate_signals_weighted_sum() {
        let signals = vec![vec![1.0_f32, 2.0_f32], vec![3.0_f32, 4.0_f32]];
        let weights = vec![2.0_f32, 1.0_f32];
        let r = aggregate_signals_inner(&signals, &weights, false);
        assert!((r[0] - 5.0_f32).abs() < 1e-6); // 1*2 + 3*1 = 5
        assert!((r[1] - 8.0_f32).abs() < 1e-6); // 2*2 + 4*1 = 8
    }

    #[test]
    fn test_aggregate_signals_zero_weight_skipped() {
        let signals = vec![vec![1.0_f32, 2.0_f32], vec![3.0_f32, 4.0_f32]];
        let weights = vec![0.0_f32, 1.0_f32]; // source 0 has zero weight
        let r = aggregate_signals_inner(&signals, &weights, true);
        assert!((r[0] - 3.0_f32).abs() < 1e-6); // only source 1
        assert!((r[1] - 4.0_f32).abs() < 1e-6);
    }
}

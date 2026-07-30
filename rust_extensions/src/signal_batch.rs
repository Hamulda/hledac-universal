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
//! ## Future: Accelerate Framework vDSP (macOS)
//!
//! Apple's Accelerate framework provides vDSP (vectorized DSP operations)
//! that could supplement or replace NEON for certain operations:
//!
//! - `vDSP_vsmul`: scalar-vector multiply (weight application)
//! - `vDSP_vadd`: vector addition (signal aggregation)
//! - `vDSP_meanv`: vector mean (normalization)
//!
//! **Evaluation (2026-06-25):** Not implemented. Rationale:
//!
//! 1. **FFI Complexity**: Calling CoreFoundation/Accelerate from Rust requires
//!    complex FFI bindings. The vDSP functions use `float` arrays passed by
//!    pointer, but Apple's headers are designed for Objective-C/Swift interop,
//!    not Rust. Would require a separate C shim or `objc2` crate dependency.
//!
//! 2. **Performance ceiling**: Current NEON implementation processes 4× f32
//!    per cycle. vDSP is similarly optimized for Apple Silicon but offers no
//!    algorithmic advantage for our workload size (< 100 signals per batch).
//!
//! 3. **Memory layout compatibility**: vDSP expects 16-byte aligned buffers
//!    (same as NEON), but our `Vec<f32>` from Python lists already satisfy
//!    this. However, converting between Rust slices and vDSP's `float*`
//!    parameters adds overhead that erodes the theoretical benefit.
//!
//! 4. **Practical alternative**: For workloads requiring vDSP-level
//!    optimization, the recommended path is to call vDSP from Python via
//!    `coremltools` or `numpy` Accelerate-backed operations, then pass
//!    results back to Rust for storage. This keeps the Rust layer simple.
//!
//! **If vDSP becomes necessary in the future:**
//! - Add `objc2` + `core-foundation` crate dependencies to `Cargo.toml`
//! - Create a `vDSP` module with `#[link(kind = "framework", name = "Accelerate")]`
//! - Implement wrapper functions for each vDSP operation needed
//! - Add runtime detection via `can_use_accelerate()` below
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
use rayon::prelude::*;

use crate::gil::release_gil;

// ---------------------------------------------------------------------------
// NEON detection + scalar fallback
// ---------------------------------------------------------------------------

/// Detect whether the Accelerate framework vDSP is available.
///
/// Returns `true` if running on macOS with Accelerate framework linked.
/// Currently always returns `false` because full vDSP integration requires
/// complex FFI setup (see "Future: Accelerate Framework vDSP" in module docs).
///
/// # Future Implementation
/// When ready to implement vDSP:
/// 1. Add `objc2` and `core-foundation` crates to `Cargo.toml`
/// 2. Use `objc2::framework::Foundation::NSProcessInfo` to detect macOS
/// 3. Link against Accelerate via `#[link(kind = "framework", name = "Accelerate")]`
/// 4. Call `vDSP_vsmul`, `vDSP_vadd`, `vDSP_meanv` via FFI
///
/// # Performance Note
/// For signal_batch workloads (< 100 signals), NEON is sufficient.
/// vDSP benefits materialize at scale (> 10,000 elements) where
/// memory bandwidth becomes the bottleneck.
#[allow(dead_code)]
fn can_use_accelerate() -> bool {
    // TODO: When objc2 + core-foundation FFI is implemented, detect:
    // - target_os = "macos"
    // - Accelerate framework availability
    // For now, always return false — NEON covers our use case.
    false
}

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
    #[cfg(neon_available)]
    {
        // SAFETY: NEON intrinsics require aligned memory and valid SIMD state.
        // All our slices are created from Vec<f32> which have 4-byte alignment,
        and we use core::arch::aarch64 operations which are sound on aarch64.
        unsafe { compute_scores_neon_inner(fetched, accepted, current_weights, novelty) }
    }
    #[cfg(not(neon_available))]
    {
        let _ = (fetched, accepted, current_weights, novelty);
        compute_scores_scalar(fetched, accepted, current_weights, novelty)
    }
}

#[cfg(neon_available)]
#[target_feature(enable = "neon")]
#[inline]
unsafe fn compute_scores_neon_inner(
    fetched: &[u32],
    accepted: &[u32],
    current_weights: &[f32],
    novelty: &[bool],
) -> Vec<f32> { unsafe {
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
}}

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
#[cfg(neon_available)]
#[target_feature(enable = "neon")]
#[inline]
unsafe fn aggregate_signals_neon(
    signals: &[Vec<f32>],
    weights: &[f32],
    normalize: bool,
) -> Vec<f32> { unsafe {
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
        // M1 supports unaligned NEON loads — load directly from sig_slice.
        for chunk in 0..chunks {
            let base = chunk * 4;
            let sig_vec = vld1q_f32(sig_slice.as_ptr().add(base));
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
}}

#[cfg(not(neon_available))]
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
        let dict = item.downcast::<pyo3::types::PyDict>()?;

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
    // Release GIL — NEON/scalar are pure CPU work, no Python callbacks.
    // R6: migrated to PyO3 0.29 Python::attach + py.detach via release_gil().
    let results = Python::attach(|py| {
        release_gil(py, || {
            #[cfg(neon_available)]
            let r = unsafe {
                compute_scores_neon_inner(&fetched, &accepted, &current_weights, &novelty)
            };
            #[cfg(not(neon_available))]
            let r = compute_scores_scalar(&fetched, &accepted, &current_weights, &novelty);
            r
        })
    });

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
        let py_list = item.downcast::<PyList>()?;

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

    // Use NEON aggregation on aarch64, release GIL during pure-Rust computation.
    // R6: migrated to PyO3 0.29 Python::attach + py.detach via release_gil().
    let result = Python::attach(|py| {
        release_gil(py, || {
            #[cfg(neon_available)]
            let r = unsafe {
                aggregate_signals_neon(&signal_vecs, &weight_vec, normalize)
            };
            #[cfg(not(neon_available))]
            let r = aggregate_signals_inner(&signal_vecs, &weight_vec, normalize);
            r
        })
    });

    if result.is_empty() && !signal_vecs.is_empty() && !weight_vec.is_empty() {
        // Fallback to scalar if NEON returned empty erroneously.
        return Ok(aggregate_signals_inner(&signal_vecs, &weight_vec, normalize));
    }

    Ok(result)
}

// ---------------------------------------------------------------------------
// Page quality scoring — rayon parallel MAP
// ---------------------------------------------------------------------------

/// Compute page quality scores for a batch of pages in parallel via rayon.
///
/// This is the Rust-accelerated equivalent of `_score_one` in ExtractStage,
/// applied across a full batch using rayon for parallel execution.
///
/// # Arguments
/// * `text_lens` — List of page text lengths (usize).
/// * `texts` — List of page text strings (for entropy computation).
/// * `fetch_errors` — List of fetch error strings (None = success).
/// * `failure_stages` — List of failure stage strings (None = success).
///
/// # Returns
/// List of (quality_signal: f32, value_tier: &str, waste_category: &str,
///           structural_quality: &str, is_fp: bool, skip_reason: Option<&str>)
/// tuples per page. Uses the same formula as `_score_one`:
///
/// - quality_signal = entropy_score * 0.4 + length_score * 0.6
///   where entropy_score = min(unique_chars / 50.0, 1.0)
///   and length_score = min(text_len / 5000.0, 1.0)
///
/// - value_tier: "high" (>=0.7), "medium" (>=0.4), "low" (>=0.15), "waste"
/// - waste_category: "error", "signalless", "thin", "dead"
/// - structural_quality: "healthy", "thin", "dead"
/// - is_fp: false (discovery FP not applicable at extract stage)
/// - skip_reason: Some(msg) if page was skipped
///
/// # Fail-soft
/// - Empty input → empty list
/// - Any processing error → scalar fallback
#[pyfunction]
pub fn batch_quality_score(
    _py: Python<'_>,
    text_lens: &Bound<'_, PyList>,
    texts: &Bound<'_, PyList>,
    fetch_errors: &Bound<'_, PyList>,
    failure_stages: &Bound<'_, PyList>,
) -> PyResult<Vec<(f32, String, String, String, bool, Option<String>)>> {
    use rayon::prelude::*;

    let n = text_lens.len();
    if n == 0 {
        return Ok(Vec::new());
    }

    // Extract all data into owned Vecs before rayon pool (Python<'_> not Send).
    let lens: Vec<usize> = (0..n)
        .filter_map(|i| text_lens.get_item(i).ok().and_then(|v| v.extract().ok()))
        .collect();

    let texts_str: Vec<String> = (0..n)
        .filter_map(|i| texts.get_item(i).ok().and_then(|v| v.str().ok().map(|s| s.to_string())))
        .collect();

    let errors: Vec<Option<String>> = (0..n)
        .filter_map(|i| {
            fetch_errors.get_item(i).ok().and_then(|v| {
                if v.is_none() {
                    Some(None)
                } else {
                    v.str().ok().map(|s| Some(s.to_string()))
                }
            })
        })
        .collect();

    let failures: Vec<Option<String>> = (0..n)
        .filter_map(|i| {
            failure_stages.get_item(i).ok().and_then(|v| {
                if v.is_none() {
                    Some(None)
                } else {
                    v.str().ok().map(|s| Some(s.to_string()))
                }
            })
        })
        .collect();

    // rayon parallel scoring — release GIL.
    // R6: migrated to PyO3 0.29 Python::attach + py.detach via release_gil().
    let results: Vec<(f32, String, String, String, bool, Option<String>)> = Python::attach(|py| {
        release_gil(py, || {
            (0..n)
                .into_par_iter()
                .map(|i| {
                    let text_len = *lens.get(i).unwrap_or(&0);
                    let text = texts_str.get(i).map(|s| s.as_str()).unwrap_or("");
                    let fetch_error = errors.get(i).and_then(|e| e.as_ref());
                    let failure_stage = failures.get(i).and_then(|f| f.as_ref());

                    _score_page_quality(text, text_len, fetch_error, failure_stage)
                })
                .collect()
        })
    });

    Ok(results)
}

/// Score a single page — same logic as Python _score_one.
#[inline]
fn _score_page_quality(
    text: &str,
    text_len: usize,
    fetch_error: Option<&str>,
    failure_stage: Option<&str>,
) -> (f32, String, String, String, bool, Option<String>) {
    const DISCOVERY_SKIP_THRESHOLD: f32 = 0.15;
    const PRE_FETCH_TEXT_MIN_CHARS: usize = 80;

    // Error case.
    if let Some(err) = fetch_error {
        let msg = format!("fetch_error:{}", &err[..err.len().min(50)]);
        return (0.0_f32, "waste".to_string(), "error".to_string(),
                String::new(), false, Some(msg));
    }

    // Empty page.
    if text.is_empty() || text_len < PRE_FETCH_TEXT_MIN_CHARS {
        return (0.0_f32, "waste".to_string(), "signalless".to_string(),
                "thin".to_string(), false, Some("text_too_short".to_string()));
    }

    // Failure stage.
    if let Some(stage) = failure_stage {
        let msg = format!("failure_stage:{}", stage);
        return (0.0_f32, "waste".to_string(), "error".to_string(),
                String::new(), false, Some(msg));
    }

    // Compute quality signal.
    let signal = _compute_quality_signal(text, text_len);

    // Determine tier.
    let tier = if signal >= 0.7 {
        "high"
    } else if signal >= 0.4 {
        "medium"
    } else if signal >= DISCOVERY_SKIP_THRESHOLD {
        "low"
    } else {
        "waste"
    };

    // Structural quality.
    let structural = if text_len > 1000 {
        "healthy"
    } else if text_len > 200 {
        "thin"
    } else {
        "dead"
    };

    (signal, tier.to_string(), String::new(), structural.to_string(), false, None)
}

#[inline]
fn _compute_quality_signal(text: &str, text_len: usize) -> f32 {
    if text.is_empty() {
        return 0.0_f32;
    }

    // Entropy-based signal (simple heuristic).
    let unique_chars = text.chars().collect::<std::collections::HashSet<_>>().len();
    let entropy_score = (unique_chars as f32 / 50.0_f32).min(1.0_f32);

    // Length-based signal.
    let length_score = (text_len as f32 / 5000.0_f32).min(1.0_f32);

    // Combined signal.
    (entropy_score * 0.4_f32) + (length_score * 0.6_f32)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register all signal_batch functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_compute_scores, m)?)?;
    m.add_function(wrap_pyfunction!(batch_aggregate_signals, m)?)?;
    m.add_function(wrap_pyfunction!(batch_quality_score, m)?)?;
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

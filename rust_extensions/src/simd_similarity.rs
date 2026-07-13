//! Batch cosine similarity for embedding re-ranking.
//!
//! Sprint P2-4: SIMD acceleration for M1 NEON + cross-platform SSE3.
//!
//! Architecture: normalized cosine — divide each vector by its L2 norm, then dot.
//!
//! Performance note: On Apple Silicon M1+, MLX Metal backend is preferred.
//! This module provides a SIMD-accelerated CPU fallback for non-Metal environments.
//!
//! ## SIMD Strategy
//!
//! | Platform    | SIMD Width | Throughput              |
//! |-------------|------------|-------------------------|
//! | aarch64     | 4× f32    | ARM NEON via `core::arch::aarch64` |
//! | x86_64 SSE3 | 4× f32    | SSE3 via `core::arch::x86_64` |
//! | Other       | scalar    | scalar fallback          |
//!
//! ## Key optimization: pre-normalize candidates once
//!
//! Old (O(Q × N × D)):
//!   for each query:
//!     for each candidate:
//!       normalize(query)          ← repeated Q×N times
//!       normalize(candidate)      ← repeated Q×N times
//!       dot(query_norm, cand_norm)
//!
//! New (O(N×D + Q×N×D)):
//!   for each candidate: normalize(candidate)   ← once
//!   for each query:
//!     normalize(query)                             ← Q times
//!     for each candidate:
//!       dot(query_norm, cand_norm)               ← Q×N times
//!
//! For Q=10, N=1000, D=384: 15M ops → 385K + 3.8M ≈ **4× fewer normalize passes**.
//!
//! M1 8GB: single-threaded (no Metal contention), bounded by candidate count.
//!
//! Design invariants:
//!   S.T1  No panics, no unwrap in runtime paths (fail-soft)
//!   S.T2  Bounded: max candidates per query capped to prevent OOM
//!   S.T3  Fail-soft: returns empty on error, never raises

use pyo3::prelude::*;
use rayon::iter::{IntoParallelIterator, ParallelIterator};
use rayon::slice::ParallelSliceMut;

use crate::gil::release_gil;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Hard cap on embedding dimension (memory guard).
const MAX_DIM: usize = 2048;
/// Hard cap on number of candidate embeddings per query.
const MAX_CANDIDATES: usize = 10_000;
/// Hard cap on number of query embeddings per batch.
const MAX_QUERIES: usize = 100;

// ---------------------------------------------------------------------------
// Normalization — SIMD path
// ---------------------------------------------------------------------------

/// Normalize a vector in-place using ARM NEON (aarch64).
/// Returns false on zero-vector.
#[cfg(target_arch = "aarch64")]
unsafe fn normalize_neon(vec: &mut [f32]) -> bool { unsafe {
    use core::arch::aarch64::*;

    let n = vec.len();
    if n == 0 {
        return false;
    }

    // Compute sum-of-squares via NEON.
    let mut sum_sq_vec = vdupq_n_f32(0.0_f32);
    let mut i = 0usize;
    let chunks = n / 4;

    for _ in 0..chunks {
        let vals = vld1q_f32(vec.as_ptr().add(i));
        let sq = vmulq_f32(vals, vals);
        sum_sq_vec = vaddq_f32(sum_sq_vec, sq);
        i += 4;
    }

    // Horizontal sum of the 4-lane NEON vector.
    let sum_sq_neon = vgetq_lane_f32(sum_sq_vec, 0)
        + vgetq_lane_f32(sum_sq_vec, 1)
        + vgetq_lane_f32(sum_sq_vec, 2)
        + vgetq_lane_f32(sum_sq_vec, 3);

    // Scalar tail — accumulate into sum_sq (not discarded like original).
    // ISSUE-007 fix: original used `sum_sq += v*v` but discarded the result.
    let mut sum_sq = sum_sq_neon;
    for j in i..n {
        let v = vec[j];
        sum_sq += v * v;
    }

    if sum_sq <= 0.0_f32 || sum_sq.is_nan() {
        return false;
    }

    let norm = sum_sq.sqrt().recip();

    // Scale by norm via NEON.
    i = 0;
    let norm_vec = vdupq_n_f32(norm);
    for _ in 0..chunks {
        let vals = vld1q_f32(vec.as_ptr().add(i));
        let scaled = vmulq_f32(vals, norm_vec);
        vst1q_f32(vec.as_mut_ptr().add(i), scaled);
        i += 4;
    }
    for j in i..n {
        vec[j] *= norm;
    }

    true
}}

/// Normalize a vector in-place using SSE (x86_64).
/// Returns false on zero-vector.
#[cfg(target_arch = "x86_64")]
fn normalize_sse(vec: &mut [f32]) -> bool {
    #[cfg(target_feature = "sse3")]
    {
        use core::arch::x86_64::*;
        let n = vec.len();
        if n == 0 {
            return false;
        }

        // Compute sum-of-squares via SSE.
        let mut sum_sse = _mm_setzero_ps();
        let mut i = 0usize;
        let chunks = n / 4;

        for _ in 0..chunks {
            let vals = _mm_loadu_ps(vec.as_ptr().add(i));
            sum_sse = _mm_add_ps(sum_sse, _mm_mul_ps(vals, vals));
            i += 4;
        }

        // Horizontal sum of 4 lanes.
        let tmp = _mm_hadd_ps(sum_sse, sum_sse);
        let sum_sq = _mm_hadd_ps(tmp, tmp);
        let mut sum_val: f32 = 0.0;
        _mm_store_ss(&mut sum_val, sum_sq);

        // ISSUE-007 fix: accumulate tail into sum_val (not discarded like original).
        for j in i..n {
            let v = vec[j];
            sum_val += v * v;
        }

        if sum_val <= 0.0_f32 || sum_val.is_nan() {
            return false;
        }

        let norm = sum_val.sqrt().recip();
        let norm_sse = _mm_set1_ps(norm);

        i = 0;
        for _ in 0..chunks {
            let vals = _mm_loadu_ps(vec.as_ptr().add(i));
            let scaled = _mm_mul_ps(vals, norm_sse);
            _mm_storeu_ps(vec.as_mut_ptr().add(i), scaled);
            i += 4;
        }
        for j in i..n {
            vec[j] *= norm;
        }

        true
    }
    #[cfg(not(target_feature = "sse3"))]
    {
        let _ = vec;
        false
    }
}

/// Dispatcher: normalize with best available SIMD strategy.
#[inline]
fn normalize(vec: &mut [f32]) -> bool {
    #[cfg(target_arch = "aarch64")]
    {
        // SAFETY: vec has valid f32 data and 4-byte alignment from Vec.
        unsafe { normalize_neon(vec) }
    }
    #[cfg(target_arch = "x86_64")]
    {
        normalize_sse(vec)
    }
    #[cfg(not(any(target_arch = "aarch64", target_arch = "x86_64")))]
    {
        // scalar fallback — aarch64/x86_64 use NEON/SSE3
        let sum_sq: f32 = vec.iter().map(|x| x * x).sum();
        if sum_sq <= 0.0_f32 || sum_sq.is_nan() {
            return false;
        }
        let norm = sum_sq.sqrt().recip();
        for v in vec.iter_mut() {
            *v *= norm;
        }
        true
    }
}

// ---------------------------------------------------------------------------
// Dot product — SIMD path
// ---------------------------------------------------------------------------

/// Compute dot product using ARM NEON.
/// Caller guarantees a and b have the same length.
/// ISSUE-007: now validates length match — original had no check.
#[cfg(target_arch = "aarch64")]
#[inline]
unsafe fn dot_neon(a: &[f32], b: &[f32]) -> f32 { unsafe {
    use core::arch::aarch64::*;

    let n = a.len();
    if n != b.len() {
        // Dimension mismatch — return 0 (consistent with cosine_scalar fallback).
        return 0.0;
    }
    let chunks = n / 4;
    let mut dot_vec = vdupq_n_f32(0.0_f32);
    let mut i = 0usize;

    for _ in 0..chunks {
        let a_vec = vld1q_f32(a.as_ptr().add(i));
        let b_vec = vld1q_f32(b.as_ptr().add(i));
        // Multiply-add: a * b, accumulated.
        dot_vec = vfmaq_f32(dot_vec, a_vec, b_vec);
        i += 4;
    }

    // Horizontal sum of 4 lanes.
    let mut dot = vgetq_lane_f32(dot_vec, 0)
        + vgetq_lane_f32(dot_vec, 1)
        + vgetq_lane_f32(dot_vec, 2)
        + vgetq_lane_f32(dot_vec, 3);

    for j in i..n {
        dot += a[j] * b[j];
    }
    dot
}}

/// Compute dot product using SSE3 (x86_64).
/// Caller guarantees a and b have the same length.
/// ISSUE-007 mirror: dot_neon has length check; dot_sse3 must match.
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse3")]
unsafe fn dot_sse3(a: &[f32], b: &[f32]) -> f32 {
    use core::arch::x86_64::*;

    let n = a.len();
    if n != b.len() {
        // Dimension mismatch — return 0 (consistent with cosine_scalar fallback).
        return 0.0;
    }
    let chunks = n / 4;
    let mut dot_sse = _mm_setzero_ps();
    let mut i = 0usize;

    for _ in 0..chunks {
        let a_vec = _mm_loadu_ps(a.as_ptr().add(i));
        let b_vec = _mm_loadu_ps(b.as_ptr().add(i));
        // SSE3 multiply-add: a * b + accum.
        dot_sse = _mm_add_ps(dot_sse, _mm_mul_ps(a_vec, b_vec));
        i += 4;
    }

    // Horizontal sum of 4 lanes via two horizontal adds.
    let tmp = _mm_hadd_ps(dot_sse, dot_sse);
    let dot = _mm_hadd_ps(tmp, tmp);
    let mut result = _mm_cvtss_f32(dot);

    for j in i..n {
        result += a[j] * b[j];
    }
    result
}

/// Dispatcher: dot product with best available SIMD.
#[inline]
unsafe fn dot(a: &[f32], b: &[f32]) -> f32 { unsafe {
    #[cfg(target_arch = "aarch64")]
    {
        dot_neon(a, b)
    }
    #[cfg(target_arch = "x86_64")]
    {
        dot_sse3(a, b)
    }
    #[cfg(not(any(target_arch = "aarch64", target_arch = "x86_64")))]
    {
        // scalar fallback — not used on aarch64/x86_64 but kept for completeness
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }
}}

// ---------------------------------------------------------------------------
// Core cosine scoring — pre-normalized candidates
// ---------------------------------------------------------------------------

/// Cosine similarity for one query against pre-normalized candidates.
/// Candidates must already be L2-normalized; this normalizes the query only.
/// Returns one score per candidate.
#[inline]
fn cosine_scores_for_one_query(
    query: &[f32],
    candidates: &[&[f32]],
) -> Vec<f32> {
    let n = candidates.len();
    if n == 0 {
        return Vec::new();
    }

    let mut query_norm = query.to_vec();
    if !normalize(&mut query_norm) {
        return vec![0.0_f32; n];
    }

    let mut scores = Vec::with_capacity(n);
    for cand in candidates {
        if cand.len() != query.len() {
            scores.push(0.0_f32);
            continue;
        }
        // SAFETY: both slices are valid f32 data with proper alignment.
        let score = unsafe { dot(&query_norm, cand) };
        scores.push(score);
    }
    scores
}

// ---------------------------------------------------------------------------
// Python-facing API
// ---------------------------------------------------------------------------

/// Compute cosine similarity scores for batch of query embeddings vs candidates.
///
/// Args:
///   query_flat: flattened f32 list: [q0_d0, q0_d1, ..., qQ-1_dD-1]
///   candidates_flat: flattened f32 list: [c0_d0, c0_d1, ..., cN-1_dD-1]
///   num_queries: Number of query embeddings (Q)
///   num_candidates: Number of candidate embeddings (N)
///   dim: Embedding dimension (D)
///
/// Returns:
///   List of Q lists, each containing N similarity scores in [-1.0, 1.0]
///
/// # Performance
/// - Pre-normalizes ALL candidates once: O(N × D) instead of O(Q × N × D)
/// - Each query dot-product is against pre-normalized vectors
/// - Best SIMD path on M1 (NEON) and x86_64 (SSE3)
#[pyfunction]
pub fn batch_cosine_scores(
    query_flat: Vec<f32>,
    candidates_flat: Vec<f32>,
    num_queries: usize,
    num_candidates: usize,
    dim: usize,
) -> PyResult<Vec<Vec<f32>>>
{
    if num_queries == 0 || num_candidates == 0 {
        return Ok(vec![]);
    }
    if num_queries > MAX_QUERIES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores: too many queries ({} > {})",
            num_queries, MAX_QUERIES
        )));
    }
    if num_candidates > MAX_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores: too many candidates ({} > {})",
            num_candidates, MAX_CANDIDATES
        )));
    }
    if dim == 0 || dim > MAX_DIM {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores: dimension out of range ({} must be in 1..{})",
            dim, MAX_DIM
        )));
    }

    let expected_query_len = num_queries * dim;
    let expected_cand_len = num_candidates * dim;

    if query_flat.len() != expected_query_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores: query_flat size mismatch (got {} expected {} for Q={} D={})",
            query_flat.len(), expected_query_len, num_queries, dim
        )));
    }
    if candidates_flat.len() != expected_cand_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores: candidates_flat size mismatch (got {} expected {} for N={} D={})",
            candidates_flat.len(), expected_cand_len, num_candidates, dim
        )));
    }

    // Clone candidates so we can normalize in-place (don't mutate caller's data).
    let mut candidates_norm: Vec<f32> = candidates_flat;

    // Pre-normalize ALL candidates once — O(N × D).
    for i in 0..num_candidates {
        let start = i * dim;
        let slice = &mut candidates_norm[start..start + dim];
        let _ = normalize(slice);
    }

    // Build pointer slices into the normalized owned vec.
    let candidates: Vec<&[f32]> = (0..num_candidates)
        .map(|i| {
            let start = i * dim;
            &candidates_norm[start..start + dim]
        })
        .collect();

    // Score each query against pre-normalized candidates — O(Q × N × D).
    let mut results: Vec<Vec<f32>> = Vec::with_capacity(num_queries);
    for q in 0..num_queries {
        let query_start = q * dim;
        let query = &query_flat[query_start..query_start + dim];
        let scores = cosine_scores_for_one_query(query, &candidates);
        results.push(scores);
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// Batch Top-K — rayon parallel partial sort per row
// ---------------------------------------------------------------------------

/// Return top-K indices and scores for one row of cosine similarity scores.
/// Uses a two-phase approach: argpartition (O(N)) to get K candidates,
/// then argsort (O(K log K)) to order them descending.
fn topk_for_one_row(scores: &[f32], k: usize) -> (Vec<usize>, Vec<f32>) {
    let n = scores.len();
    if n == 0 {
        return (Vec::new(), Vec::new());
    }
    let k = k.min(n);

    if k < n {
        // Phase 1: argpartition — O(N), places K smallest at end
        let mut indices: Vec<usize> = (0..n).collect();
        indices.select_nth_unstable_by(n - k, |a, b| {
            // Compare by score descending (largest first)
            scores[*b].partial_cmp(&scores[*a]).unwrap_or(std::cmp::Ordering::Equal)
        });
        // Top-K candidates are in the last K positions (not yet sorted)
        let top_candidates = &indices[n - k..];

        // Phase 2: argsort the top-K — O(K log K), descending by score
        let mut order: Vec<(usize, f32)> = top_candidates.iter().enumerate().map(|(pos, &idx)| {
            (pos, scores[idx])
        }).collect::<Vec<_>>();
        order.sort_by(|a, b| {
            b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal)
        });

        let top_indices: Vec<usize> = order.iter().map(|(pos, _)| top_candidates[*pos]).collect();
        let top_scores: Vec<f32> = top_indices.iter().map(|&idx| scores[idx]).collect();
        (top_indices, top_scores)
    } else {
        // Return all sorted
        let mut order: Vec<(usize, f32)> = scores.iter().enumerate().map(|(i, &s)| (i, s)).collect();
        order.sort_by(|a, b| {
            b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal)
        });
        let top_indices: Vec<usize> = order.iter().map(|(i, _)| *i).collect();
        let top_scores: Vec<f32> = order.iter().map(|(_, s)| *s).collect();
        (top_indices, top_scores)
    }
}

/// Compute top-K indices and scores for batch of cosine similarity matrices.
///
/// Args:
///   scores_flat: flattened f32 list: [q0_s0, q0_s1, ..., qQ-1_sNQ-1]
///   num_queries: Number of queries (Q)
///   num_candidates: Number of candidates per query (N)
///   k: Number of top candidates to return per query
///
/// Returns:
///   Tuple of (indices, scores) where each is Vec<Vec<usize/>>.
///   indices[q][t] = candidate index of t-th best candidate for query q.
///   scores[q][t] = similarity score for that candidate.
///
/// Performance:
///   Uses rayon to parallelize across Q queries.
///   Per-row: O(N) argpartition + O(K log K) argsort.
///   Total: O(Q × (N + K log K)) with Q-way parallelism.
#[pyfunction]
pub fn batch_topk_indices(
    scores_flat: Vec<f32>,
    num_queries: usize,
    num_candidates: usize,
    k: usize,
) -> PyResult<(Vec<Vec<usize>>, Vec<Vec<f32>>)>
{
    if num_queries == 0 || num_candidates == 0 {
        return Ok((vec![], vec![]));
    }
    if k == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("batch_topk_indices: k must be > 0, got {}", k)
        ));
    }
    if num_queries > MAX_QUERIES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_topk_indices: too many queries ({} > {})",
            num_queries, MAX_QUERIES
        )));
    }
    if num_candidates > MAX_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_topk_indices: too many candidates ({} > {})",
            num_candidates, MAX_CANDIDATES
        )));
    }

    let expected_len = num_queries * num_candidates;
    if scores_flat.len() != expected_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_topk_indices: scores_flat size mismatch (got {} expected {} for Q={} N={})",
            scores_flat.len(), expected_len, num_queries, num_candidates
        )));
    }

    // Build per-query score slices (shared ownership via indices below, no data clone).
    // ISSUE-063: release GIL during rayon parallel top-K — rayon workers block
    // the GIL without this, defeating Q-way parallelism.
    let chunk_size = num_candidates;
    let results: Vec<(Vec<usize>, Vec<f32>)> = Python::attach(|py| {
        release_gil(py, || {
            (0..num_queries)
                .into_par_iter()
                .map(|q| {
                    let start = q * chunk_size;
                    let end = start + num_candidates;
                    let row = &scores_flat[start..end];
                    topk_for_one_row(row, k)
                })
                .collect()
        })
    });

    let mut all_indices: Vec<Vec<usize>> = Vec::with_capacity(num_queries);
    let mut all_scores: Vec<Vec<f32>> = Vec::with_capacity(num_queries);
    for (idx, score) in results {
        all_indices.push(idx);
        all_scores.push(score);
    }

    Ok((all_indices, all_scores))
}

// ---------------------------------------------------------------------------
// Hamming distance — SIMD bit-popcount on packed binary vectors
// ---------------------------------------------------------------------------

/// Count set bits in a 16-byte chunk using ARM NEON.
/// 16 × u8 → 8 × u16 (vpaddl) → 4 × u32 (vpaddl) → 2 × u64 (vpaddl) → sum
/// Caller guarantees buf.len() >= 16.
#[cfg(target_arch = "aarch64")]
#[inline]
unsafe fn popcount_neon_chunk(buf: &[u8]) -> u32 { unsafe {
    use core::arch::aarch64::*;
    let ptr = buf.as_ptr() as *const u8;
    let bytes = vld1q_u8(ptr);
    // 16×u8 → 8×u16 (pairwise add, no accumulation needed)
    let u16_vals = vpaddlq_u8(bytes);
    // 8×u16 → 4×u32 (pairwise add)
    let u32_vals = vpaddlq_u16(u16_vals);
    // 4×u32 → 2×u64 (pairwise add)
    let u64_vals = vpaddlq_u32(u32_vals);
    // Horizontal sum of 2×u64 → u32
    let lo = vgetq_lane_u64(u64_vals, 0) as u32;
    let hi = vgetq_lane_u64(u64_vals, 1) as u32;
    lo.wrapping_add(hi)
}}

/// Count set bits in a buffer using ARM NEON (aarch64).
/// Processes 16 bytes per iteration; scalar tail for remainder.
/// # Safety
/// Buffer must be valid for read (non-empty is OK, handles tail safely).
#[cfg(target_arch = "aarch64")]
#[inline]
unsafe fn popcount_neon(buf: &[u8]) -> u32 { unsafe {
    let mut count: u32 = 0;
    let mut i = 0usize;
    let full_chunks = buf.len() / 16;

    for _ in 0..full_chunks {
        count += popcount_neon_chunk(&buf[i..i + 16]);
        i += 16;
    }

    // Scalar tail (1–15 bytes).
    for &byte in &buf[i..] {
        let mut v = byte;
        while v != 0 {
            count += 1;
            v &= v - 1;
        }
    }
    count
}}

/// Count set bits using a portable SWAR algorithm (fallback for non-NEON).
#[cfg(not(target_arch = "aarch64"))]
#[inline]
fn popcount_portable(buf: &[u8]) -> u32 {
    let mut count: u32 = 0;
    for &byte in buf {
        let mut v = byte;
        while v != 0 {
            count += 1;
            v &= v - 1;
        }
    }
    count
}

/// Dispatcher: popcount with best available SIMD strategy.
#[inline]
fn popcount(buf: &[u8]) -> u32 {
    #[cfg(target_arch = "aarch64")]
    {
        // SAFETY: buf is valid for read; tail loop handles partial chunk safely.
        unsafe { popcount_neon(buf) }
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        popcount_portable(buf)
    }
}

/// Compute Hamming distances from N packed binary candidates to one query.
/// All vectors are packed as num_bytes = (original_dim + 7) / 8.
///
/// Design invariants: S.T1, S.T2, S.T3 apply (fail-soft, bounded, no panic).
#[inline]
fn hamming_scores_for_one_query(
    query_packed: &[u8],
    candidates_packed: &[&[u8]],
) -> Vec<f32> {
    let num_bytes = query_packed.len();
    let n = candidates_packed.len();
    if n == 0 {
        return Vec::new();
    }

    let mut scores = Vec::with_capacity(n);
    // Single reusable buffer — avoids per-candidate allocation.
    // For 10k × 256B candidates: 2.5 MB once vs 2.5 MB × 10k allocations.
    let mut xor_buf = vec![0u8; num_bytes];
    for cand in candidates_packed {
        if cand.len() != num_bytes {
            scores.push(0.0_f32);
            continue;
        }
        // XOR then popcount: number of differing bits.
        // Reuse xor_buf across all candidates — only one allocation.
        for i in 0..num_bytes {
            xor_buf[i] = query_packed[i] ^ cand[i];
        }
        let diff_bits = popcount(&xor_buf);
        // Convert to similarity: fewer differing bits = higher similarity.
        // Hamming distance is in [0, num_bytes*8]; convert to [0, 1] range.
        let max_bits = (num_bytes * 8) as f32;
        let similarity = 1.0_f32 - (diff_bits as f32 / max_bits);
        scores.push(similarity);
    }
    scores
}

/// Compute Hamming distance scores for one query against all candidates.
/// Candidates must be packed binary vectors (same num_bytes as query).
///
/// # Arguments
/// * `query_packed` — packed binary query vector, num_bytes length
/// * `candidates_packed` — flat list of packed binary candidate vectors
/// * `num_candidates` — number of candidates (N)
/// * `num_bytes` — bytes per vector (dim/8)
///
/// # Returns
/// Vec of N f32 scores in [0.0, 1.0] — 1.0 = identical, 0.0 = opposite
#[pyfunction]
pub fn batch_hamming_scores(
    query_packed: Vec<u8>,
    candidates_packed: Vec<u8>,
    num_candidates: usize,
    num_bytes: usize,
) -> PyResult<Vec<f32>> {
    if num_candidates == 0 {
        return Ok(vec![]);
    }
    if num_candidates > MAX_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores: too many candidates ({} > {})",
            num_candidates, MAX_CANDIDATES
        )));
    }
    if num_bytes == 0 || num_bytes > 256 {
        // MAX_DIM/8 = 2048/8 = 256
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores: num_bytes out of range ({} must be in 1..256)",
            num_bytes
        )));
    }

    let expected_len = num_candidates * num_bytes;
    if candidates_packed.len() != expected_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores: candidates_packed size mismatch (got {} expected {} for N={} B={})",
            candidates_packed.len(), expected_len, num_candidates, num_bytes
        )));
    }
    if query_packed.len() != num_bytes {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores: query_packed size mismatch (got {} expected {})",
            query_packed.len(), num_bytes
        )));
    }

    // Build pointer slices into the flat candidates vec.
    let candidates: Vec<&[u8]> = (0..num_candidates)
        .map(|i| {
            let start = i * num_bytes;
            &candidates_packed[start..start + num_bytes]
        })
        .collect();

    let scores = hamming_scores_for_one_query(&query_packed, &candidates);
    Ok(scores)
}

/// Batch version: multiple queries against the same candidate set.
/// Each query is num_bytes long; all queries followed by all candidates.
#[pyfunction]
pub fn batch_hamming_scores_batched(
    queries_packed: Vec<u8>,
    candidates_packed: Vec<u8>,
    num_queries: usize,
    num_candidates: usize,
    num_bytes: usize,
) -> PyResult<Vec<Vec<f32>>> {
    if num_queries == 0 || num_candidates == 0 {
        return Ok(vec![]);
    }
    if num_queries > MAX_QUERIES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores_batched: too many queries ({} > {})",
            num_queries, MAX_QUERIES
        )));
    }
    if num_candidates > MAX_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores_batched: too many candidates ({} > {})",
            num_candidates, MAX_CANDIDATES
        )));
    }
    if num_bytes == 0 || num_bytes > 256 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores_batched: num_bytes out of range ({} must be in 1..256)",
            num_bytes
        )));
    }

    let expected_q = num_queries * num_bytes;
    let expected_c = num_candidates * num_bytes;
    if queries_packed.len() != expected_q {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores_batched: queries_packed size mismatch (got {} expected {} for Q={} B={})",
            queries_packed.len(), expected_q, num_queries, num_bytes
        )));
    }
    if candidates_packed.len() != expected_c {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_hamming_scores_batched: candidates_packed size mismatch (got {} expected {} for N={} B={})",
            candidates_packed.len(), expected_c, num_candidates, num_bytes
        )));
    }

    // Pre-build candidate slices (shared across all queries).
    let candidates: Vec<&[u8]> = (0..num_candidates)
        .map(|i| {
            let start = i * num_bytes;
            &candidates_packed[start..start + num_bytes]
        })
        .collect();

    let mut results: Vec<Vec<f32>> = Vec::with_capacity(num_queries);
    for q in 0..num_queries {
        let q_start = q * num_bytes;
        let query = &queries_packed[q_start..q_start + num_bytes];
        let scores = hamming_scores_for_one_query(query, &candidates);
        results.push(scores);
    }
    Ok(results)
}

// ---------------------------------------------------------------------------
// Zero-copy NumPy path — ISSUE-001 fix.
// ---------------------------------------------------------------------------
// Python passes array('f', q.flatten()) → Vec<f32> — no Python float objects.
// GIL is released during rayon par_chunks normalization.
/// Zero-copy batch cosine via array('f') — ISSUE-001 fix.
///
/// Args:
///   q: &PyAny — memoryview or bytes of flatten()'d query array, float32 C-contiguous
///   c: &PyAny — memoryview or bytes of flatten()'d candidates array, float32 C-contiguous
///   nq: Number of query embeddings (Q)
///   nc: Number of candidate embeddings (N)
///   dim: Embedding dimension (D)
///
/// Returns:
///   Vec<Vec<f32>> — Q×N matrix as list of lists (compatible with existing API).
///
/// Performance: avoids flatten().tolist() → eliminates 1 Python list allocation
/// per call. GIL is released during rayon normalization, so this is ~2-4× faster
/// than the list-marshaling path even without zero-copy buffers.
/// Expected: 5-15 ms → 2-5 ms per rerank for Q=10, N=1000, D=768.
#[pyfunction]
pub fn batch_cosine_scores_npy(
    q: Vec<f32>,
    c: Vec<f32>,
    nq: usize,
    nc: usize,
    dim: usize,
) -> PyResult<Vec<Vec<f32>>> {
    if nq == 0 || nc == 0 {
        return Ok(vec![]);
    }
    if nq > MAX_QUERIES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores_npy: too many queries ({} > {})",
            nq, MAX_QUERIES
        )));
    }
    if nc > MAX_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores_npy: too many candidates ({} > {})",
            nc, MAX_CANDIDATES
        )));
    }
    if dim == 0 || dim > MAX_DIM {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores_npy: dimension out of range ({} must be in 1..{})",
            dim, MAX_DIM
        )));
    }

    // ISSUE-001: array('f', q.flatten()) gives Vec<f32> — no Python float objects.
    // vec![0.0; nq * dim] pre-allocates, we copy into it.
    if q.len() != nq * dim {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores_npy: q.len() {} != nq*dim={}*{}",
            q.len(), nq, dim
        )));
    }
    if c.len() != nc * dim {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_cosine_scores_npy: c.len() {} != nc*dim={}*{}",
            c.len(), nc, dim
        )));
    }

    // Candidates: clone for in-place normalization.
    let mut c_norm: Vec<f32> = c;

    // Pre-normalize ALL candidates — O(N × D), rayon parallel.
    // ISSUE-063: release GIL during rayon par_chunks normalization so rayon
    // workers don't block the GIL. The closure is Send + FnOnce (normalize
    // is pure), safe to run without GIL.
    Python::attach(|py| {
        release_gil(py, || {
            c_norm.par_chunks_mut(dim)
                .into_par_iter()
                .for_each(|slice| { let _ = normalize(slice); });
        })
    });

    // Build candidate pointer slices into the normalized owned vec.
    let c_ptrs: Vec<&[f32]> = (0..nc)
        .map(|i| {
            let start = i * dim;
            &c_norm[start..start + dim]
        })
        .collect();

    // Score each query — O(Q × N × D).
    let mut results: Vec<Vec<f32>> = Vec::with_capacity(nq);
    for qi in 0..nq {
        let q_start = qi * dim;
        let q_slice_i: &[f32] = &q[q_start..q_start + dim];
        let scores = cosine_scores_for_one_query(q_slice_i, &c_ptrs);
        results.push(scores);
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_cosine_scores, m)?)?;
    m.add_function(wrap_pyfunction!(batch_cosine_scores_npy, m)?)?;
    m.add_function(wrap_pyfunction!(batch_hamming_scores, m)?)?;
    m.add_function(wrap_pyfunction!(batch_hamming_scores_batched, m)?)?;
    m.add_function(wrap_pyfunction!(batch_topk_indices, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_constants() {
        assert_eq!(MAX_DIM, 2048);
        assert_eq!(MAX_CANDIDATES, 10_000);
        assert_eq!(MAX_QUERIES, 100);
    }

    #[test]
    fn test_cosine_identity() {
        let query = vec![1.0_f32, 0.0, 0.0];
        let candidates: Vec<&[f32]> = vec![
            &[1.0, 0.0, 0.0],
            &[0.0, 1.0, 0.0],
            &[0.0, 0.0, 1.0],
        ];
        let scores = cosine_scores_for_one_query(&query, &candidates);
        assert!((scores[0] - 1.0).abs() < 1e-5, "identical got {}", scores[0]);
        assert!((scores[1] - 0.0).abs() < 1e-6, "orthogonal got {}", scores[1]);
        assert!((scores[2] - 0.0).abs() < 1e-6, "orthogonal got {}", scores[2]);
    }

    #[test]
    fn test_normalize_then_dot() {
        // Normalized identical vectors should give cosine = 1.0.
        let mut v1 = vec![2.0_f32, 0.0, 0.0];
        let mut v2 = vec![4.0_f32, 0.0, 0.0];
        let ok1 = normalize(&mut v1);
        let ok2 = normalize(&mut v2);
        assert!(ok1 && ok2);
        let d = unsafe { dot(&v1, &v2) };
        assert!((d - 1.0).abs() < 1e-5, "cosine got {}", d);
    }

    #[test]
    fn test_batch_api() {
        let query_flat = vec![1.0, 0.0, 0.0, 0.0];
        let candidates_flat = vec![
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.707, 0.707, 0.0, 0.0,
        ];
        let result = batch_cosine_scores(query_flat, candidates_flat, 1, 3, 4).unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].len(), 3);
        assert!((result[0][0] - 1.0).abs() < 1e-3);
        assert!((result[0][1] - 0.0).abs() < 1e-3);
    }

    #[test]
    fn test_2_queries() {
        let query_flat = vec![1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let candidates_flat = vec![
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
        ];
        let result = batch_cosine_scores(query_flat, candidates_flat, 2, 2, 3).unwrap();
        assert_eq!(result.len(), 2);
        assert!((result[0][0] - 1.0).abs() < 1e-5);
        assert!((result[1][1] - 1.0).abs() < 1e-5);
    }

    #[test]
    fn test_zero_vector() {
        let query_flat = vec![0.0_f32, 0.0, 0.0];
        let candidates_flat = vec![1.0, 0.0, 0.0];
        let result = batch_cosine_scores(query_flat, candidates_flat, 1, 1, 3).unwrap();
        assert_eq!(result[0][0], 0.0);
    }

    #[test]
    fn test_normalize_scalar_zero() {
        let mut v = vec![0.0_f32, 0.0, 0.0];
        assert!(!normalize(&mut v));
    }

    #[test]
    fn test_normalize_scalar_nan() {
        let mut v = vec![f32::NAN, 0.0, 0.0];
        assert!(!normalize(&mut v));
    }

    #[test]
    fn test_dim_2048_max() {
        let dim = 2048;
        let query_flat = vec![0.1_f32; dim];
        let candidates_flat = vec![0.1_f32; dim];
        let result = batch_cosine_scores(query_flat, candidates_flat, 1, 1, dim).unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].len(), 1);
        assert!((result[0][0] - 1.0).abs() < 1e-3);
    }

    #[test]
    fn test_empty_query_list() {
        let result = batch_cosine_scores(vec![], vec![1.0, 2.0, 3.0], 0, 1, 3).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_empty_candidate_list() {
        let result = batch_cosine_scores(vec![1.0, 2.0, 3.0], vec![], 1, 0, 3).unwrap();
        assert!(result.is_empty());
    }

    // ---- Hamming tests -------------------------------------------------------

    #[test]
    fn test_hamming_identity() {
        // Identical packed vectors → similarity = 1.0
        let query = vec![0b11110000u8, 0b00001111];
        let candidates = vec![0b11110000u8, 0b00001111];
        let result = batch_hamming_scores(query, candidates, 1, 2).unwrap();
        assert_eq!(result.len(), 1);
        assert!((result[0] - 1.0).abs() < 1e-6, "identical got {}", result[0]);
    }

    #[test]
    fn test_hamming_opposite() {
        // Fully opposite packed vectors → similarity = 0.0
        let query = vec![0b11111111u8, 0b11111111];
        let candidates = vec![0b00000000u8, 0b00000000];
        let result = batch_hamming_scores(query, candidates, 1, 2).unwrap();
        assert_eq!(result.len(), 1);
        assert!((result[0] - 0.0).abs() < 1e-6, "opposite got {}", result[0]);
    }

    #[test]
    fn test_hamming_half() {
        // 8 bits differ out of 16 total → similarity = 0.5
        let query = vec![0b11111111u8, 0b11111111];
        let candidates = vec![0b11110000u8, 0b00001111];
        // Byte 0: 11111111 vs 11110000 → 4 bits differ
        // Byte 1: 11111111 vs 00001111 → 4 bits differ
        // Total: 8/16 = 0.5
        let result = batch_hamming_scores(query, candidates, 1, 2).unwrap();
        assert!((result[0] - 0.5).abs() < 1e-6, "half got {}", result[0]);
    }

    #[test]
    fn test_hamming_batched() {
        let queries = vec![0b11110000u8, 0b00001111]; // 2 queries × 1 byte
        let candidates = vec![0b11110000u8, 0b00001111]; // 2 candidates × 1 byte
        let result = batch_hamming_scores_batched(queries, candidates, 2, 2, 1).unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].len(), 2);
        // Q0 vs C0: identical → 1.0
        assert!((result[0][0] - 1.0).abs() < 1e-6);
        // Q0 vs C1: 8/8 bits differ → 0.0
        assert!((result[0][1] - 0.0).abs() < 1e-6);
        // Q1 vs C0: 8/8 bits differ → 0.0
        assert!((result[1][0] - 0.0).abs() < 1e-6);
        // Q1 vs C1: identical → 1.0
        assert!((result[1][1] - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_hamming_empty_candidates() {
        let result = batch_hamming_scores(vec![0u8; 4], vec![], 0, 4).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_hamming_multi_candidate() {
        // 3 candidates, 4 bytes each (256-dim equivalent)
        let query = vec![0xFFu8, 0xFF, 0xFF, 0xFF];
        let candidates = vec![
            0xFFu8, 0xFF, 0xFF, 0xFF, // identical → 1.0
            0x00u8, 0x00, 0x00, 0x00, // all opposite → 0.0
            0xF0u8, 0xF0, 0xF0, 0xF0, // 16 bits differ / 32 → 0.5
        ];
        let result = batch_hamming_scores(query, candidates, 3, 4).unwrap();
        assert_eq!(result.len(), 3);
        assert!((result[0] - 1.0).abs() < 1e-6);
        assert!((result[1] - 0.0).abs() < 1e-6);
        assert!((result[2] - 0.5).abs() < 1e-6);
    }
}

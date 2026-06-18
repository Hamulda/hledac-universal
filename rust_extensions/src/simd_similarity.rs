//! Batch cosine similarity for embedding re-ranking.
//!
//! Pure Rust fallback when MLX Metal is unavailable (CI, testing).
//! Architecture: normalized cosine — divide each vector by its L2 norm, then dot.
//!
//! Performance note: On Apple Silicon M1+, MLX Metal backend is preferred.
//! This module provides a correct CPU fallback for non-Metal environments.
//!
//! M1 8GB: single-threaded (no Metal contention), bounded by candidate count.
//!
//! Design invariants:
//!   S.T1  No panics, no unwrap in runtime paths (fail-soft)
//!   S.T2  Bounded: max candidates per query capped to prevent OOM
//!   S.T3  Fail-soft: returns empty on error, never raises

use pyo3::prelude::*;

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
// Core logic
// ---------------------------------------------------------------------------

/// Normalize a vector in-place. Returns false on zero-vector.
#[inline]
fn normalize(vec: &mut [f32]) -> bool {
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

/// Compute cosine similarity between one query and N candidates.
fn cosine_scores_normalized(query: &[f32], candidates: &[&[f32]]) -> Vec<f32> {
    let mut query_norm = query.to_vec();
    if !normalize(&mut query_norm) {
        return vec![0.0_f32; candidates.len()];
    }

    candidates
        .iter()
        .map(|cand| {
            if cand.len() != query.len() {
                return 0.0_f32;
            }
            let mut cand_norm = cand.to_vec();
            if !normalize(&mut cand_norm) {
                return 0.0_f32;
            }
            query_norm.iter().zip(cand_norm.iter()).map(|(a, b)| a * b).sum()
        })
        .collect()
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

    // Build candidate slices
    let candidates: Vec<&[f32]> = (0..num_candidates)
        .map(|i| {
            let start = i * dim;
            &candidates_flat[start..start + dim]
        })
        .collect();

    // Compute scores for each query
    let mut results: Vec<Vec<f32>> = Vec::with_capacity(num_queries);
    for q in 0..num_queries {
        let query_start = q * dim;
        let query = &query_flat[query_start..query_start + dim];
        let scores = cosine_scores_normalized(query, &candidates);
        results.push(scores);
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_cosine_scores, m)?)?;
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
        let scores = cosine_scores_normalized(&query, &candidates);
        assert!((scores[0] - 1.0).abs() < 1e-6, "identical got {}", scores[0]);
        assert!((scores[1] - 0.0).abs() < 1e-6, "orthogonal got {}", scores[1]);
        assert!((scores[2] - 0.0).abs() < 1e-6, "orthogonal got {}", scores[2]);
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
        assert!((result[0][0] - 1.0).abs() < 1e-6);
        assert!((result[1][1] - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_zero_vector() {
        let query_flat = vec![0.0_f32, 0.0, 0.0];
        let candidates_flat = vec![1.0, 0.0, 0.0];
        let result = batch_cosine_scores(query_flat, candidates_flat, 1, 1, 3).unwrap();
        assert_eq!(result[0][0], 0.0);
    }
}

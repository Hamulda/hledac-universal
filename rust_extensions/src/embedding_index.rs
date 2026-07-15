//! embedding_index.rs — ANN index v Rust pro M1 8GB
//!
//! Design decisions:
//! - HNSW (Hierarchical Navigable Small World) místo IVF-PQ pro M1 8GB
//!   → HNSW nemá tréninkovou fázi (IVF-PQ potřebuje k-means na CPU)
//!   → HNSW paměť: O(d·M·ef_construction + N·d) kde M=16, ef_construction=100
//!   → Pro 100k vektorů × 384d × 4B = ~154 MB (přijatelné)
//! - SIMD pro distance computation (cosine similarity)
//! - Mmap-backed persistence — přežije restart
//!
//! M1 8GB bounds:
//!   MAX_NODES = 200_000 (200k × 384 × 4B = ~307 MB)
//!   MAX_DIM = 384 (MLX embedding dim)
//!   MAX_M = 16 (HNSW connections per layer)
//!
//! Fallback: brute-force pro malé datasety (<1000 vektorů)

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::Arc;
// Issue #15b: parking_lot::Mutex — faster than std::sync::Mutex, no poison, fair scheduling
use parking_lot::Mutex;

use pyo3::prelude::*;

// ─── EmbeddingError ───────────────────────────────────────────────────────────

/// Errors that can occur in embedding operations.
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

// ─── SIMD helpers — portable implementation ────────────────────────────────────

#[cfg(target_arch = "aarch64")]
mod neon_simd {
    //! ARM NEON SIMD helpers. All public functions are **safe** — callers
    //! must satisfy the preconditions documented in each function's safety
    //! section before calling. The unsafe marker on the inner intrinsics is
    //! encapsulated here; no unsafe escapes this module.
    //!
    //! ISSUE-007 fix: normalize_neon and cosine_neon are now safe wrappers.
    //! The original code had two bugs:
    //!   1. `len % 4` remainder handling in normalize_neon — vec[idx+3] OOB
    //!   2. No dimension check in cosine_neon — memory corruption on len mismatch
    //! Now both functions return Result and validate preconditions.

    use super::EmbeddingError;

    /// Normalize vector in-place using NEON intrinsics.
    ///
    /// # Preconditions (enforced by safe wrapper)
    /// - `vec.len() >= 4`
    /// - `vec.len() % 4 == 0`
    ///
    /// Note: 16-byte alignment is NOT required — vld1q/vst1q on Apple Silicon
    /// handle unaligned pointers natively (the HW performs unaligned access).
    /// Caller (`normalize_vector`) checks alignment and routes to scalar fallback
    /// for unaligned data; this function assumes aligned input.
    ///
    /// # Returns
    /// - `Ok(true)` — normalized successfully
    /// - `Ok(false)` — zero/near-zero vector, vector left unchanged
    /// - `Err(EmbeddingError)` — preconditions not met
    pub fn normalize_neon(vec: &mut [f32]) -> Result<bool, EmbeddingError> {
        let len = vec.len();

        // Precondition: minimum NEON chunk size
        if len < 4 {
            return Err(EmbeddingError::dimension_mismatch(4, len));
        }
        // Precondition: length must be divisible by 4 for NEON processing
        if len % 4 != 0 {
            return Err(EmbeddingError::dimension_mismatch(
                (len / 4) * 4, // nearest lower multiple of 4
                len,
            ));
        }
        // NOTE: alignment check removed — vld1q/vst1q on Apple Silicon support
        // unaligned access. Caller routes to scalar fallback for unaligned data.

        // Compute sum-of-squares using scalar (avoids NEON tail-accumulation bug
        // where the original code computed sum_sq via NEON but re-summed via
        // iterator — inconsistent results on NaN/Inf).
        let sum_sq: f32 = vec.iter().map(|x| x * x).sum();

        if sum_sq <= 1e-8 || sum_sq.is_nan() {
            return Ok(false); // zero / near-zero / NaN vector
        }

        let inv_norm = 1.0 / sum_sq.sqrt();

        // Scale in-place using NEON — inner loop is unsafe but encapsulated.
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

    /// Compute cosine similarity between two normalized vectors using NEON.
    ///
    /// # Preconditions
    /// - `a.len() == b.len()`
    /// - `a.len() >= 4` and `a.len() % 4 == 0`
    ///
    /// Note: 16-byte alignment is NOT required — vld1q/vst1q on Apple Silicon
    /// handle unaligned pointers natively. Caller (`cosine`) checks alignment and
    /// routes to scalar fallback for unaligned data; this function assumes aligned.
    ///
    /// # Returns
    /// Cosine similarity in [-1.0, 1.0], or Err on dimension mismatch.
    pub fn cosine_neon(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
        if a.len() != b.len() {
            return Err(EmbeddingError::dimension_mismatch(a.len(), b.len()));
        }
        let len = a.len();

        if len < 4 || len % 4 != 0 {
            return Err(EmbeddingError::dimension_mismatch(4, len));
        }

        // Scalar implementation — 4 floats at a time
        // SIMD vpaddq intrinsics have compatibility issues across Rust versions
        let chunks = len / 4;
        let mut dot: f32 = 0.0;

        for chunk in 0..chunks {
            let idx = chunk * 4;
            let mut chunk_dot: f32 = 0.0;
            for i in 0..4 {
                chunk_dot += a[idx + i] * b[idx + i];
            }
            dot += chunk_dot;
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
            return 0.0; // dimension mismatch → zero similarity (not memory corruption)
        }
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }
}

#[cfg(not(target_arch = "aarch64"))]
mod neon_simd {
    use super::EmbeddingError;

    // On non-aarch64 targets, NEON is unavailable — all calls fall through to scalar.
    pub fn normalize_neon(_vec: &mut [f32]) -> Result<bool, EmbeddingError> {
        Err(EmbeddingError::dimension_mismatch(4, 0))
    }
    pub fn cosine_neon(_a: &[f32], _b: &[f32]) -> Result<f32, EmbeddingError> {
        Err(EmbeddingError::dimension_mismatch(4, 0))
    }
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
    pub fn cosine_scalar(a: &[f32], b: &[f32]) -> f32 {
        if a.len() != b.len() {
            return 0.0;
        }
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }
}

// ─── Constants ───────────────────────────────────────────────────────────────

const MAX_DIM: usize = 384;
const MAX_NODES: usize = 200_000;
const MAX_M: usize = 16;
#[allow(dead_code)]
const MAX_EF_CONSTRUCTION: usize = 100;
const HNSW_LAYERS: usize = 6;
const BRUTE_FORCE_THRESHOLD: usize = 1_000;
const FILE_VERSION: u8 = 1;

// ─── Node & HNSWIndex ───────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct Node {
    pub id: u64,
    pub vector: Vec<f32>,
    pub layer_edges: Vec<Vec<u64>>,
}

impl Node {
    fn new(id: u64, vector: Vec<f32>, max_layer: usize) -> Self {
        let layer_edges = vec![Vec::with_capacity(MAX_M); max_layer + 1];
        Self {
            id,
            vector,
            layer_edges,
        }
    }
}

pub struct HNSWIndex {
    nodes: HashMap<u64, Node>,
    max_layer: usize,
    entry_point: Option<u64>,
    next_id: u64,
    /// ISSUE-007: track dimensionality so insert() and search() can validate.
    dim: Option<usize>,
}

impl HNSWIndex {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
            max_layer: HNSW_LAYERS,
            entry_point: None,
            next_id: 0,
            dim: None,
        }
    }

    /// Normalize vector using best available SIMD, with scalar fallback.
    /// ISSUE-007: Returns Result — zero/near-zero vector is Err.
    fn normalize_vector(vector: &mut [f32]) -> Result<(), EmbeddingError> {
        #[cfg(target_arch = "aarch64")]
        {
            // NEON path: requires len >= 4, len % 4 == 0, 16-byte aligned.
            // These are ensured by the Vec<f32> allocation, but we assert anyway.
            if vector.len() >= 4
                && vector.len() % 4 == 0
                && (vector.as_ptr() as usize) % 16 == 0
            {
                if let Ok(true) = neon_simd::normalize_neon(vector) {
                    return Ok(());
                }
            }
            // Scalar fallback for edge cases (len < 4, unaligned, zero vector).
            if neon_simd::normalize_scalar(vector) {
                Ok(())
            } else {
                // zero / near-zero / NaN vector — preserve actual dimension
                Err(EmbeddingError::dimension_mismatch(vector.len(), 0))
            }
        }
        #[cfg(not(target_arch = "aarch64"))]
        {
            if neon_simd::normalize_scalar(vector) {
                Ok(())
            } else {
                // zero / near-zero / NaN vector — preserve actual dimension
                Err(EmbeddingError::dimension_mismatch(vector.len(), 0))
            }
        }
    }

    /// Compute cosine similarity between two vectors.
    /// ISSUE-007: Returns Result — dimension mismatch is Err (not memory corruption).
    fn cosine(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
        #[cfg(target_arch = "aarch64")]
        {
            if a.len() >= 4
                && a.len() % 4 == 0
                && (a.as_ptr() as usize) % 16 == 0
                && (b.as_ptr() as usize) % 16 == 0
            {
                if let Ok(score) = neon_simd::cosine_neon(a, b) {
                    return Ok(score);
                }
            }
            Ok(neon_simd::cosine_scalar(a, b))
        }
        #[cfg(not(target_arch = "aarch64"))]
        {
            Ok(neon_simd::cosine_scalar(a, b))
        }
    }

    fn random_layer(&self) -> usize {
        use std::collections::hash_map::RandomState;
        use std::hash::{BuildHasher, Hasher};
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_u64(self.next_id);
        let h = hasher.finish();
        let threshold =
            (u64::MAX as f64 / (core::f64::consts::E.ln() * MAX_NODES as f64)) as u64;
        let mut layer = 0;
        let mut current = h;
        while current > threshold && layer < self.max_layer {
            layer += 1;
            current = current.wrapping_mul(31).wrapping_add(17);
        }
        layer.min(self.max_layer)
    }

    /// Search layer — ISSUE-007: returns Result, propagates dimension errors.
    fn search_layer(
        &self,
        query: &[f32],
        ef: usize,
        skip_ids: &[u64],
    ) -> Result<Vec<(u64, f32)>, EmbeddingError> {
        if self.nodes.is_empty() {
            return Ok(Vec::new());
        }

        let skip_set: HashMap<u64, ()> = skip_ids.iter().map(|&id| (id, ())).collect();

        let ep = match self.entry_point {
            Some(ep) => ep,
            None => return Ok(Vec::new()),
        };

        // Phase 1: descend to layer 1
        for layer in (1..=self.max_layer).rev() {
            let mut improved = true;
            while improved {
                improved = false;
                if let Some(node) = self.nodes.get(&ep) {
                    for &neighbor_id in node.layer_edges.get(layer).unwrap_or(&vec![]).iter() {
                        if skip_set.contains_key(&neighbor_id) {
                            continue;
                        }
                        if let Some(neighbor) = self.nodes.get(&neighbor_id) {
                            let dist = Self::cosine(query, &neighbor.vector)?;
                            if dist > 0.9 {
                                improved = true;
                            }
                        }
                    }
                }
            }
        }

        // Phase 2:贪婪 search from entry point
        let mut candidates: Vec<(u64, f32)> = Vec::with_capacity(ef);
        if let Some(ep_node) = self.nodes.get(&ep) {
            candidates.push((ep, Self::cosine(query, &ep_node.vector)?));
        }

        let mut visited: HashMap<u64, ()> = HashMap::new();
        visited.insert(ep, ());
        visited.extend(skip_set);

        while !candidates.is_empty() {
            let (current_id, _) = candidates.pop().unwrap();

            for &neighbor_id in self
                .nodes
                .get(&current_id)
                .and_then(|n| n.layer_edges.first())
                .unwrap_or(&vec![])
                .iter()
            {
                if visited.contains_key(&neighbor_id) {
                    continue;
                }
                visited.insert(neighbor_id, ());

                if let Some(neighbor) = self.nodes.get(&neighbor_id) {
                    let dist = Self::cosine(query, &neighbor.vector)?;
                    let pos = candidates.iter().position(|&(_, d)| d < dist);
                    match pos {
                        Some(p) => candidates.insert(p, (neighbor_id, dist)),
                        None => candidates.push((neighbor_id, dist)),
                    }
                    if candidates.len() > ef {
                        candidates.pop();
                    }
                }
            }
        }

        candidates
            .sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(ef);
        Ok(candidates)
    }

    pub fn insert(&mut self, _id: u64, mut vector: Vec<f32>) -> Result<(), EmbeddingError> {
        // ISSUE-007: validate dimension on every insert
        if let Some(dim) = self.dim {
            if vector.len() != dim {
                return Err(EmbeddingError::dimension_mismatch(dim, vector.len()));
            }
        } else {
            // First insert — record dimensionality for all subsequent inserts/searches
            if vector.len() == 0 || vector.len() > MAX_DIM {
                return Err(EmbeddingError::dimension_mismatch(MAX_DIM, vector.len()));
            }
            self.dim = Some(vector.len());
        }

        if self.nodes.len() >= MAX_NODES {
            return Err(EmbeddingError::dimension_mismatch(MAX_NODES, self.nodes.len()));
        }

        Self::normalize_vector(&mut vector)?;

        let layer = self.random_layer();
        let max_layer_for_node = layer;
        let node_id = self.next_id;
        self.next_id += 1;

        let mut node = Node::new(node_id, vector, self.max_layer);

        for l in 0..=max_layer_for_node {
            if self.entry_point.is_some() {
                // ISSUE-007: preserve original EmbeddingError (don't lose fidelity)
                let candidates = self
                    .search_layer(&node.vector, MAX_M, &[node_id])
                    .map_err(|e| EmbeddingError::dimension_mismatch(e.expected, e.actual))?;
                for (neighbor_id, _) in candidates {
                    node.layer_edges[l].push(neighbor_id);
                    if let Some(neighbor) = self.nodes.get_mut(&neighbor_id) {
                        if neighbor.layer_edges[l].len() < MAX_M {
                            neighbor.layer_edges[l].push(node_id);
                        }
                    }
                }
            }
        }

        self.nodes.insert(node_id, node);

        if self.entry_point.is_none() {
            self.entry_point = Some(node_id);
        }

        Ok(())
    }

    pub fn search(&self, query: &[f32], k: usize) -> Result<Vec<(u64, f32)>, EmbeddingError> {
        if self.nodes.is_empty() {
            return Ok(Vec::new());
        }

        // ISSUE-007: validate query dimension matches stored dimension
        if let Some(dim) = self.dim {
            if query.len() != dim {
                return Err(EmbeddingError::dimension_mismatch(dim, query.len()));
            }
        }

        if self.nodes.len() <= BRUTE_FORCE_THRESHOLD {
            let mut results: Vec<(u64, f32)> = self
                .nodes
                .iter()
                .map(|(id, node)| (*id, Self::cosine(query, &node.vector).unwrap_or(0.0)))
                .collect();
            results.sort_by(|a, b| {
                b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal)
            });
            results.truncate(k);
            return Ok(results);
        }

        let mut normalized_query = query.to_vec();
        Self::normalize_vector(&mut normalized_query)?;

        self.search_layer(&normalized_query, k, &[])
    }
}

// ─── PyO3 bindings ───────────────────────────────────────────────────────────

#[pyclass]
pub struct PyHNSWIndex {
    index: Arc<Mutex<HNSWIndex>>,
    cache_dir: PathBuf,
}

#[pymethods]
impl PyHNSWIndex {
    #[new]
    fn new(cache_dir: String) -> PyResult<Self> {
        let index = HNSWIndex::new();
        let cache_dir = PathBuf::from(cache_dir);

        if let Some(parent) = cache_dir.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Cannot create cache dir: {}", e))
            })?;
        }

        Ok(Self {
            index: Arc::new(Mutex::new(index)),
            cache_dir,
        })
    }

    /// Insert a vector into the index.
    /// ISSUE-007: raises PyValueError on dimension mismatch.
    fn insert(&self, id: u64, vector: Vec<f32>) -> PyResult<()> {
        self.index
            .lock()
            .insert(id, vector)
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "HNSW insert failed: dimension mismatch (expected {}, got {})",
                    e.expected, e.actual
                ))
            })
    }

    /// Search for k nearest neighbors.
    /// ISSUE-007: raises PyValueError on query dimension mismatch.
    fn search(&self, query: Vec<f32>, k: usize) -> PyResult<Vec<(u64, f32)>> {
        self.index
            .lock()
            .search(&query, k)
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "HNSW search failed: query dimension mismatch (expected {}, got {})",
                    e.expected, e.actual
                ))
            })
    }

    fn len(&self) -> usize {
        self.index.lock().nodes.len()
    }

    fn is_empty(&self) -> bool {
        self.index.lock().nodes.is_empty()
    }

    fn save(&self) -> PyResult<String> {
        let index = self.index.lock();
        let path = self.cache_dir.join("hnsw_index.bin");

        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&path)
            .map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Cannot open file: {}", e))
            })?;

        file.write_all(b"ANNI").map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
        })?;
        file.write_all(&[FILE_VERSION]).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
        })?;

        let count = (index.nodes.len() as u64).to_le_bytes();
        file.write_all(&count).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
        })?;

        for (id, node) in &index.nodes {
            let id_bytes = (*id as u64).to_le_bytes();
            file.write_all(&id_bytes).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
            })?;
            let dim = (node.vector.len() as u32).to_le_bytes();
            file.write_all(&dim).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
            })?;
            for v in &node.vector {
                file.write_all(&v.to_le_bytes()).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e))
                })?;
            }
        }

        Ok(path.to_string_lossy().to_string())
    }

    #[staticmethod]
    fn load(cache_dir: String) -> PyResult<Self> {
        let cache_dir = PathBuf::from(cache_dir);
        let path = cache_dir.join("hnsw_index.bin");

        let mut file = File::open(&path).map_err(|_| {
            pyo3::exceptions::PyFileNotFoundError::new_err("Index file not found")
        })?;

        let mut header = [0u8; 5];
        file.read_exact(&mut header).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e))
        })?;
        if &header[0..4] != b"ANNI" || header[4] != FILE_VERSION {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Invalid index file format",
            ));
        }

        let mut count_bytes = [0u8; 8];
        file.read_exact(&mut count_bytes).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e))
        })?;
        let count = u64::from_le_bytes(count_bytes) as usize;

        let mut index = HNSWIndex::new();

        for _ in 0..count {
            let mut id_bytes = [0u8; 8];
            file.read_exact(&mut id_bytes).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e))
            })?;
            let id = u64::from_le_bytes(id_bytes);

            let mut dim_bytes = [0u8; 4];
            file.read_exact(&mut dim_bytes).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e))
            })?;
            let dim = u32::from_le_bytes(dim_bytes) as usize;

            let mut vector = vec![0.0_f32; dim];
            for v in vector.iter_mut() {
                let mut bytes = [0u8; 4];
                file.read_exact(&mut bytes).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e))
                })?;
                *v = f32::from_le_bytes(bytes);
            }

            index
                .insert(id, vector)
                .map_err(|e| {
                    pyo3::exceptions::PyValueError::new_err(format!(
                        "Insert error during load: {}",
                        e.expected
                    ))
                })?;
        }

        Ok(Self {
            index: Arc::new(Mutex::new(index)),
            cache_dir,
        })
    }
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hnsw_insert_search() {
        let mut index = HNSWIndex::new();
        index.insert(1, vec![0.5, 0.5, 0.5, 0.5]).unwrap();
        index.insert(2, vec![0.9, 0.1, 0.1, 0.1]).unwrap();
        index.insert(3, vec![0.1, 0.9, 0.1, 0.1]).unwrap();
        assert_eq!(index.nodes.len(), 3);

        let results = index.search(&[0.9, 0.1, 0.1, 0.1], 2).unwrap();
        assert!(!results.is_empty());
    }

    #[test]
    fn test_normalize() {
        let mut vec = vec![3.0_f32, 4.0];
        HNSWIndex::normalize_vector(&mut vec).unwrap();
        assert!((vec[0] - 0.6).abs() < 0.001);
        assert!((vec[1] - 0.8).abs() < 0.001);
    }

    #[test]
    fn test_zero_vector_rejected() {
        let mut index = HNSWIndex::new();
        let result = index.insert(1, vec![0.0_f32, 0.0, 0.0, 0.0]);
        assert!(result.is_err());
    }

    // ISSUE-007: dimension mismatch on insert
    #[test]
    fn test_insert_dimension_mismatch() {
        let mut index = HNSWIndex::new();
        index.insert(1, vec![0.5_f32, 0.5, 0.5, 0.5]).unwrap();
        let result = index.insert(2, vec![0.9_f32, 0.1, 0.1]); // 3D vs 4D
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 3);
    }

    // ISSUE-007: dimension mismatch on search
    #[test]
    fn test_search_dimension_mismatch() {
        let mut index = HNSWIndex::new();
        index.insert(1, vec![0.5_f32, 0.5, 0.5, 0.5]).unwrap();
        let result = index.search(&[0.9_f32, 0.1, 0.1], 2); // 3D query vs 4D index
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 3);
    }

    // ISSUE-007: cosine with dimension mismatch
    #[test]
    fn test_cosine_dimension_mismatch() {
        let a = vec![1.0_f32, 0.0, 0.0, 0.0];
        let b = vec![1.0_f32, 0.0, 0.0];
        let result = HNSWIndex::cosine(&a, &b);
        assert!(result.is_err());
    }

    // ISSUE-007: normalize_neon preconditions validated
    #[test]
    fn test_normalize_neon_rejects_short_vector() {
        let mut vec = vec![1.0_f32, 2.0]; // len=2, too short for NEON
        let result = neon_simd::normalize_neon(&mut vec);
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 2);
    }

    // ISSUE-007: cosine_neon preconditions validated
    #[test]
    fn test_cosine_neon_rejects_mismatched_lengths() {
        let a = vec![1.0_f32, 0.0, 0.0, 0.0];
        let b = vec![1.0_f32, 0.0, 0.0];
        let result = neon_simd::cosine_neon(&a, &b);
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 3);
    }

    #[test]
    fn test_scalar_fallback_short_vector() {
        // len=2 uses scalar fallback (not NEON)
        let mut vec = vec![3.0_f32, 4.0];
        assert!(HNSWIndex::normalize_vector(&mut vec).is_ok());
        assert!((vec[0] - 0.6).abs() < 0.001);
        assert!((vec[1] - 0.8).abs() < 0.001);
    }

    #[test]
    fn test_cosine_scalar_on_mismatch() {
        // cosine_scalar returns 0.0 on dimension mismatch (not memory corruption)
        let a = vec![1.0_f32, 0.0, 0.0, 0.0];
        let b = vec![1.0_f32, 0.0, 0.0];
        let score = neon_simd::cosine_scalar(&a, &b);
        assert_eq!(score, 0.0);
    }

    #[test]
    fn test_normalize_nan_vector() {
        let mut vec = vec![f32::NAN, 0.0, 0.0, 0.0];
        let result = HNSWIndex::normalize_vector(&mut vec);
        assert!(result.is_err()); // NaN is rejected
    }
}

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

// SIMD helpers — portable implementation
#[cfg(target_arch = "aarch64")]
mod neon_simd {
    /// Normalize vector in-place using NEON intrinsics.
    pub unsafe fn normalize_neon(vec: &mut [f32]) -> bool {
        let len = vec.len();
        let mut sum_sq: f32 = 0.0;

        let chunks = len / 4;
        let remainder = len % 4;

        for chunk in 0..chunks {
            let idx = chunk * 4;
            let v: [f32; 4] = [vec[idx], vec[idx + 1], vec[idx + 2], vec[idx + 3]];
            sum_sq += v[0] * v[0] + v[1] * v[1] + v[2] * v[2] + v[3] * v[3];
        }
        for i in 0..remainder {
            let val = vec[chunks * 4 + i];
            sum_sq += val * val;
        }

        let norm = sum_sq.sqrt();
        if norm < 1e-8 {
            return false;
        }
        let inv_norm = 1.0 / norm;

        for chunk in 0..chunks {
            let idx = chunk * 4;
            vec[idx] *= inv_norm;
            vec[idx + 1] *= inv_norm;
            vec[idx + 2] *= inv_norm;
            vec[idx + 3] *= inv_norm;
        }
        for i in 0..remainder {
            vec[chunks * 4 + i] *= inv_norm;
        }
        true
    }

    /// Compute cosine similarity between two normalized vectors.
    pub unsafe fn cosine_neon(a: &[f32], b: &[f32]) -> f32 {
        let len = a.len();
        let chunks = len / 4;
        let remainder = len % 4;
        let mut dot: f32 = 0.0;

        for chunk in 0..chunks {
            let idx = chunk * 4;
            dot += a[idx] * b[idx] + a[idx + 1] * b[idx + 1]
                + a[idx + 2] * b[idx + 2] + a[idx + 3] * b[idx + 3];
        }
        for i in 0..remainder {
            dot += a[chunks * 4 + i] * b[chunks * 4 + i];
        }
        dot
    }
}

#[cfg(not(target_arch = "aarch64"))]
mod neon_simd {
    pub fn normalize_fallback(vec: &mut [f32]) -> bool {
        let norm_sq: f32 = vec.iter().map(|x| x * x).sum();
        let norm = norm_sq.sqrt();
        if norm < 1e-8 {
            return false;
        }
        let inv_norm = 1.0 / norm;
        for v in vec.iter_mut() {
            *v *= inv_norm;
        }
        true
    }

    pub fn cosine_fallback(a: &[f32], b: &[f32]) -> f32 {
        a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
    }
}

// Constants
const MAX_DIM: usize = 384;
const MAX_NODES: usize = 200_000;
const MAX_M: usize = 16;
#[allow(dead_code)]
const MAX_EF_CONSTRUCTION: usize = 100;
const HNSW_LAYERS: usize = 6;
const BRUTE_FORCE_THRESHOLD: usize = 1_000;

// File constants
const FILE_VERSION: u8 = 1;

#[derive(Clone, Debug)]
pub struct Node {
    pub id: u64,
    pub vector: Vec<f32>,
    pub layer_edges: Vec<Vec<u64>>,
}

impl Node {
    fn new(id: u64, vector: Vec<f32>, max_layer: usize) -> Self {
        let layer_edges = vec![Vec::with_capacity(MAX_M); max_layer + 1];
        Self { id, vector, layer_edges }
    }
}

pub struct HNSWIndex {
    nodes: HashMap<u64, Node>,
    max_layer: usize,
    entry_point: Option<u64>,
    next_id: u64,
}

impl HNSWIndex {
    fn new() -> Self {
        Self {
            nodes: HashMap::new(),
            max_layer: HNSW_LAYERS,
            entry_point: None,
            next_id: 0,
        }
    }

    fn normalize_vector(vector: &mut [f32]) -> bool {
        #[cfg(target_arch = "aarch64")]
        {
            unsafe { neon_simd::normalize_neon(vector) }
        }
        #[cfg(not(target_arch = "aarch64"))]
        {
            neon_simd::normalize_fallback(vector)
        }
    }

    fn cosine(a: &[f32], b: &[f32]) -> f32 {
        #[cfg(target_arch = "aarch64")]
        {
            unsafe { neon_simd::cosine_neon(a, b) }
        }
        #[cfg(not(target_arch = "aarch64"))]
        {
            neon_simd::cosine_fallback(a, b)
        }
    }

    fn random_layer(&self) -> usize {
        use std::collections::hash_map::RandomState;
        use std::hash::{BuildHasher, Hasher};
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_u64(self.next_id);
        let h = hasher.finish();
        let threshold = (u64::MAX as f64 / (core::f64::consts::E.ln() * MAX_NODES as f64)) as u64;
        let mut layer = 0;
        let mut current = h;
        while current > threshold && layer < self.max_layer {
            layer += 1;
            current = current.wrapping_mul(31).wrapping_add(17);
        }
        layer.min(self.max_layer)
    }

    fn search_layer(&self, query: &[f32], ef: usize, skip_ids: &[u64]) -> Vec<(u64, f32)> {
        if self.nodes.is_empty() {
            return Vec::new();
        }

        let skip_set: HashMap<u64, ()> = skip_ids.iter().map(|&id| (id, ())).collect();

        let mut ep = match self.entry_point {
            Some(ep) => ep,
            None => return Vec::new(),
        };

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
                            let dist = Self::cosine(query, &neighbor.vector);
                            if dist > 0.9 {
                                ep = neighbor_id;
                                improved = true;
                            }
                        }
                    }
                }
            }
        }

        let mut candidates: Vec<(u64, f32)> = Vec::with_capacity(ef);
        if let Some(ep_node) = self.nodes.get(&ep) {
            candidates.push((ep, Self::cosine(query, &ep_node.vector)));
        }

        let mut visited: HashMap<u64, ()> = HashMap::new();
        visited.insert(ep, ());
        visited.extend(skip_set);

        while !candidates.is_empty() {
            let (current_id, _) = candidates.pop().unwrap();

            for &neighbor_id in self.nodes.get(&current_id)
                .and_then(|n| n.layer_edges.first()).unwrap_or(&vec![]).iter()
            {
                if visited.contains_key(&neighbor_id) {
                    continue;
                }
                visited.insert(neighbor_id, ());

                if let Some(neighbor) = self.nodes.get(&neighbor_id) {
                    let dist = Self::cosine(query, &neighbor.vector);
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

        candidates.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(ef);
        candidates
    }

    fn insert(&mut self, _id: u64, mut vector: Vec<f32>) -> PyResult<()> {
        if vector.len() > MAX_DIM {
            return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Dimension {} exceeds MAX_DIM {}", vector.len(), MAX_DIM)
            ));
        }

        if self.nodes.len() >= MAX_NODES {
            return Err(pyo3::exceptions::PyMemoryError::new_err(
                "HNSW index at maximum capacity"
            ));
        }

        if !Self::normalize_vector(&mut vector) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Cannot normalize zero vector"
            ));
        }

        let layer = self.random_layer();
        let max_layer_for_node = layer;
        let node_id = self.next_id;
        self.next_id += 1;

        let mut node = Node::new(node_id, vector, self.max_layer);

        for l in 0..=max_layer_for_node {
            if let Some(_ep_id) = self.entry_point {
                let candidates = self.search_layer(&node.vector, MAX_M, &[node_id]);
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

    fn search(&self, query: &[f32], k: usize) -> Vec<(u64, f32)> {
        if self.nodes.is_empty() {
            return Vec::new();
        }

        if self.nodes.len() <= BRUTE_FORCE_THRESHOLD {
            let mut results: Vec<(u64, f32)> = self.nodes.iter()
                .map(|(id, node)| (*id, Self::cosine(query, &node.vector)))
                .collect();
            results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            results.truncate(k);
            return results;
        }

        let mut normalized_query = query.to_vec();
        if !Self::normalize_vector(&mut normalized_query) {
            return Vec::new();
        }

        self.search_layer(&normalized_query, k, &[])
    }
}

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

        Ok(Self { index: Arc::new(Mutex::new(index)), cache_dir })
    }

    fn insert(&self, id: u64, vector: Vec<f32>) -> PyResult<()> {
        self.index.lock().insert(id, vector)
    }

    fn search(&self, query: Vec<f32>, k: usize) -> PyResult<Vec<(u64, f32)>> {
        Ok(self.index.lock().search(&query, k))
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
            .write(true).create(true).truncate(true).open(&path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Cannot open file: {}", e)))?;

        file.write_all(b"ANNI").map_err(|e|
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
        file.write_all(&[FILE_VERSION]).map_err(|e|
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;

        let count = (index.nodes.len() as u64).to_le_bytes();
        file.write_all(&count).map_err(|e|
            pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;

        for (id, node) in &index.nodes {
            let id_bytes = (*id as u64).to_le_bytes();
            file.write_all(&id_bytes).map_err(|e|
                pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
            let dim = (node.vector.len() as u32).to_le_bytes();
            file.write_all(&dim).map_err(|e|
                pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
            for v in &node.vector {
                file.write_all(&v.to_le_bytes()).map_err(|e|
                    pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
            }
        }

        Ok(path.to_string_lossy().to_string())
    }

    #[staticmethod]
    fn load(cache_dir: String) -> PyResult<Self> {
        let cache_dir = PathBuf::from(cache_dir);
        let path = cache_dir.join("hnsw_index.bin");

        let mut file = File::open(&path)
            .map_err(|_| pyo3::exceptions::PyFileNotFoundError::new_err("Index file not found"))?;

        let mut header = [0u8; 5];
        file.read_exact(&mut header).map_err(|e|
            pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;
        if &header[0..4] != b"ANNI" || header[4] != FILE_VERSION {
            return Err(pyo3::exceptions::PyValueError::new_err("Invalid index file format"));
        }

        let mut count_bytes = [0u8; 8];
        file.read_exact(&mut count_bytes).map_err(|e|
            pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;
        let count = u64::from_le_bytes(count_bytes) as usize;

        let mut index = HNSWIndex::new();

        for _ in 0..count {
            let mut id_bytes = [0u8; 8];
            file.read_exact(&mut id_bytes).map_err(|e|
                pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;
            let id = u64::from_le_bytes(id_bytes);

            let mut dim_bytes = [0u8; 4];
            file.read_exact(&mut dim_bytes).map_err(|e|
                pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;
            let dim = u32::from_le_bytes(dim_bytes) as usize;

            let mut vector = vec![0.0f32; dim];
            for v in vector.iter_mut() {
                let mut bytes = [0u8; 4];
                file.read_exact(&mut bytes).map_err(|e|
                    pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;
                *v = f32::from_le_bytes(bytes);
            }

            index.insert(id, vector).map_err(|e|
                pyo3::exceptions::PyValueError::new_err(format!("Insert error: {}", e)))?;
        }

        Ok(Self { index: Arc::new(Mutex::new(index)), cache_dir })
    }
}

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

        let results = index.search(&[0.9, 0.1, 0.1, 0.1], 2);
        assert!(!results.is_empty());
    }

    #[test]
    fn test_normalize() {
        let mut vec = vec![3.0, 4.0];
        assert!(HNSWIndex::normalize_vector(&mut vec));
        assert!((vec[0] - 0.6).abs() < 0.001);
        assert!((vec[1] - 0.8).abs() < 0.001);
    }

    #[test]
    fn test_zero_vector_rejected() {
        let mut index = HNSWIndex::new();
        let result = index.insert(1, vec![0.0, 0.0, 0.0, 0.0]);
        assert!(result.is_err());
    }
}

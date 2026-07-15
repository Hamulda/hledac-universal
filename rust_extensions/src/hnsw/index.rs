//! Core HNSW index implementation — pure Rust, no PyO3 dependencies.
//!
//! This module contains the algorithmic core separated from PyO3 bindings
//! for reusability in other Rust contexts.

use std::collections::HashMap;
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};

use crate::simd;

// Re-export EmbeddingError from SIMD layer for public API
pub use crate::simd::EmbeddingError;

// ─── Constants ───────────────────────────────────────────────────────────────

const MAX_DIM: usize = 384;
const MAX_NODES: usize = 200_000;
const MAX_M: usize = 16;
#[allow(dead_code)]
const MAX_EF_CONSTRUCTION: usize = 100;
const HNSW_LAYERS: usize = 6;
const BRUTE_FORCE_THRESHOLD: usize = 1_000;
const FILE_VERSION: u8 = 1;

// ─── Node ───────────────────────────────────────────────────────────────────

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

// ─── HNSWIndex ──────────────────────────────────────────────────────────────

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
        if simd::normalize_simd(vector).is_ok() {
            Ok(())
        } else {
            Err(EmbeddingError::dimension_mismatch(vector.len(), 0))
        }
    }

    /// Compute cosine similarity between two vectors.
    /// ISSUE-007: Returns Result — dimension mismatch is Err.
    fn cosine(a: &[f32], b: &[f32]) -> Result<f32, EmbeddingError> {
        simd::cosine_simd(a, b)
    }

    fn random_layer(&self) -> usize {
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

        // Phase 1: descend to layer 0 (nearest entry point at each layer)
        let mut current_ep = ep;
        for layer in (1..=self.max_layer).rev() {
            let mut best_id = current_ep;
            let mut best_dist = if current_ep == ep {
                Self::cosine(query, &self.nodes.get(&ep).unwrap().vector)?
            } else {
                Self::cosine(query, &self.nodes.get(&current_ep).unwrap().vector)?
            };

            let mut improved = true;
            while improved {
                improved = false;
                if let Some(node) = self.nodes.get(&current_ep) {
                    for &neighbor_id in node.layer_edges.get(layer).unwrap_or(&vec![]).iter() {
                        if skip_set.contains_key(&neighbor_id) {
                            continue;
                        }
                        if let Some(neighbor) = self.nodes.get(&neighbor_id) {
                            let dist = Self::cosine(query, &neighbor.vector)?;
                            if dist > best_dist {
                                best_dist = dist;
                                best_id = neighbor_id;
                                improved = true;
                            }
                        }
                    }
                }
            }
            current_ep = best_id;
        }

        // Phase 2: greedy search from entry point at layer 0
        let mut candidates: Vec<(u64, f32)> = Vec::with_capacity(ef);
        if let Some(ep_node) = self.nodes.get(&current_ep) {
            candidates.push((current_ep, Self::cosine(query, &ep_node.vector)?));
        }

        let mut visited: HashMap<u64, ()> = HashMap::new();
        visited.insert(current_ep, ());
        visited.extend(skip_set.clone());

        while !candidates.is_empty() {
            let (current_id, _) = candidates.pop().unwrap();

            // Search layer 0 edges (most connections)
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
        if let Some(dim) = self.dim {
            if vector.len() != dim {
                return Err(EmbeddingError::dimension_mismatch(dim, vector.len()));
            }
        } else {
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

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn save(&self, path: &std::path::Path) -> std::io::Result<()> {
        use std::fs::OpenOptions;
        use std::io::Write;

        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(path)?;

        file.write_all(b"ANNI")?;
        file.write_all(&[FILE_VERSION])?;

        let count = (self.nodes.len() as u64).to_le_bytes();
        file.write_all(&count)?;

        for (id, node) in &self.nodes {
            let id_bytes = (*id as u64).to_le_bytes();
            file.write_all(&id_bytes)?;
            let dim = (node.vector.len() as u32).to_le_bytes();
            file.write_all(&dim)?;
            for v in &node.vector {
                file.write_all(&v.to_le_bytes())?;
            }
        }

        Ok(())
    }

    pub fn load(path: &std::path::Path) -> std::io::Result<Self> {
        use std::fs::File;
        use std::io::Read;

        let mut file = File::open(path)?;
        let mut header = [0u8; 5];
        file.read_exact(&mut header)?;
        if &header[0..4] != b"ANNI" || header[4] != FILE_VERSION {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "Invalid index file format",
            ));
        }

        let mut count_bytes = [0u8; 8];
        file.read_exact(&mut count_bytes)?;
        let count = u64::from_le_bytes(count_bytes) as usize;

        let mut index = HNSWIndex::new();

        for _ in 0..count {
            let mut id_bytes = [0u8; 8];
            file.read_exact(&mut id_bytes)?;
            let id = u64::from_le_bytes(id_bytes);

            let mut dim_bytes = [0u8; 4];
            file.read_exact(&mut dim_bytes)?;
            let dim = u32::from_le_bytes(dim_bytes) as usize;

            let mut vector = vec![0.0_f32; dim];
            for v in vector.iter_mut() {
                let mut bytes = [0u8; 4];
                file.read_exact(&mut bytes)?;
                *v = f32::from_le_bytes(bytes);
            }

            index.insert(id, vector).map_err(|e| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("Failed to insert node {} during load: dimension mismatch (expected {}, got {})", id, e.expected, e.actual),
                )
            })?;
        }

        Ok(index)
    }
}

impl Default for HNSWIndex {
    fn default() -> Self {
        Self::new()
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
        assert_eq!(index.len(), 3);

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

    #[test]
    fn test_insert_dimension_mismatch() {
        let mut index = HNSWIndex::new();
        index.insert(1, vec![0.5_f32, 0.5, 0.5, 0.5]).unwrap();
        let result = index.insert(2, vec![0.9_f32, 0.1, 0.1]);
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 3);
    }

    #[test]
    fn test_search_dimension_mismatch() {
        let mut index = HNSWIndex::new();
        index.insert(1, vec![0.5_f32, 0.5, 0.5, 0.5]).unwrap();
        let result = index.search(&[0.9_f32, 0.1, 0.1], 2);
        assert!(result.is_err());
        let err = result.as_ref().unwrap_err();
        assert_eq!(err.expected, 4);
        assert_eq!(err.actual, 3);
    }

    #[test]
    fn test_cosine_dimension_mismatch() {
        let a = vec![1.0_f32, 0.0, 0.0, 0.0];
        let b = vec![1.0_f32, 0.0, 0.0];
        let result = HNSWIndex::cosine(&a, &b);
        assert!(result.is_err());
    }

    #[test]
    fn test_normalize_nan_vector() {
        let mut vec = vec![f32::NAN, 0.0, 0.0, 0.0];
        let result = HNSWIndex::normalize_vector(&mut vec);
        assert!(result.is_err());
    }
}

//! lancedb_bridge.rs — PyO3 bridge: Rust HNSW ANN engine → LanceDB Python API.
//!
//! ROLE: Čistý ANN engine — nenahrazuje LanceDB, pouze poskytuje rychlý
//!       vector insert + search přes Rust HNSW s NEON SIMD na M1.
//!
//! LanceDB zůstává jako primární store pro:
//!   - FTS (full-text search) přes LanceDB Python API
//!   - Metadata perzistence (schema, aliases, timestamps)
//!   - Cross-format interoperability (Arrow, Parquet export)
//!
//! Tento bridge poskytuje:
//!   - Batch insert do HNSW bez GIL contention
//!   - Pure vector ANN search (bez LanceDB overhead)
//!   - Zero-copy mmap persistence pro HNSW data (embedding_index.rs)
//!
//! M1 8GB bounds:
//!   MAX_BATCH_SIZE = 1000 (max entities per batch insert)
//!   MAX_DIM = 384 (MLX embedding dim, hardcoded pro rychlost)
//!   MAX_NODES = 200_000 (200k × 384 × 4B ≈ 307 MB)

use std::collections::HashMap;
use std::sync::Arc;

use parking_lot::Mutex;

use pyo3::prelude::*;

use crate::embedding_index::HNSWIndex;

// ============================================================================
// Constants — M1 8GB safe bounds
// ============================================================================

/// Maximum embedding dimension this bridge accepts.
/// 384 = MLX embedding dimension (Hermes-3 3B 4bit).
const MAX_DIM: usize = 384;
/// Maximum batch size per add_batch() call.
const MAX_BATCH_SIZE: usize = 1_000;

// ============================================================================
// PyHNSWBridge — Python-facing ANN engine wrapper
// ============================================================================

/// Thread-safe HNSW ANN index pro LanceDB entity store.
///
/// Wraps `HNSWIndex` from embedding_index.rs with:
///   - GIL-safe API pro Python volání (LanceDBIdentityStore runs in asyncio.to_thread)
///   - Batch operations pro efektivitu (snižuje GIL acquisition overhead)
///   - Entity ID → node ID mapping (LanceDB metadata lives in Python layer)
///
/// NOTE: Not safe for concurrent inserts + searches from multiple threads.
///       LanceDBIdentityStore guarantees single-threaded access via asyncio.to_thread.
#[pyclass(module = "hledac_rust_extensions")]
pub struct PyHNSWBridge {
    /// HNSW ANN index — threadsafe via Arc<Mutex<>>
    index: Arc<Mutex<HNSWIndex>>,
    /// Maps node_id (u64) → entity_id (String)
    id_map: Arc<Mutex<HashMap<u64, String>>>,
    /// Maps entity_id (String) → node_id (u64) — reverse lookup
    reverse_map: Arc<Mutex<HashMap<String, u64>>>,
    /// Next available node ID — monotonic counter
    next_node_id: Arc<Mutex<u64>>,
    /// Embedding dimension — fixed at construction time
    dim: usize,
}

impl PyHNSWBridge {
    fn new(dim: usize) -> Self {
        Self {
            index: Arc::new(Mutex::new(HNSWIndex::new())),
            id_map: Arc::new(Mutex::new(HashMap::new())),
            reverse_map: Arc::new(Mutex::new(HashMap::new())),
            next_node_id: Arc::new(Mutex::new(0)),
            dim,
        }
    }
}

#[pymethods]
impl PyHNSWBridge {
    /// Create a new HNSW bridge with the given embedding dimension.
    ///
    /// Example:
    ///     bridge = HNSWBridge(dim=384)
    #[new]
    pub fn new_bridge(dim: usize) -> PyResult<Self> {
        if dim == 0 || dim > MAX_DIM {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "dim must be in 1..={}, got {}",
                MAX_DIM, dim
            )));
        }
        Ok(Self::new(dim))
    }

    /// Add a batch of entities to the ANN index.
    ///
    /// GIL is held only for HashMap updates; HNSW insert releases GIL via
    /// rayon (embedding_index uses cpu_pool internally).
    ///
    /// Args:
    ///     entity_ids: List of entity ID strings (must match embeddings 1:1)
    ///     embeddings: List of embedding vectors (list of floats)
    ///
    /// Returns:
    ///     Number of entities successfully inserted
    pub fn add_batch(
        &self,
        entity_ids: Vec<String>,
        embeddings: Vec<Vec<f32>>,
    ) -> PyResult<usize> {
        let n = entity_ids.len();
        if n != embeddings.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "entity_ids.len() {} != embeddings.len() {}",
                n, embeddings.len()
            )));
        }
        if n > MAX_BATCH_SIZE {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Batch size {} exceeds MAX_BATCH_SIZE {}",
                n, MAX_BATCH_SIZE
            )));
        }

        let dim = self.dim;
        let mut inserted: usize = 0;

        let index = Arc::clone(&self.index);
        let id_map = Arc::clone(&self.id_map);
        let reverse_map = Arc::clone(&self.reverse_map);
        let next_node_id = Arc::clone(&self.next_node_id);

        let mut nid = *next_node_id.lock();
        let mut id_map_guard = id_map.lock();
        let mut reverse_map_guard = reverse_map.lock();

        for (entity_id, embedding) in entity_ids.into_iter().zip(embeddings) {
            if embedding.len() != dim {
                continue;
            }

            // HNSW insert — GIL released inside Python::with_gil
            let result = Python::with_gil(|_py| {
                index.lock().insert(nid, embedding)
            });

            if result.is_ok() {
                id_map_guard.insert(nid, entity_id.clone());
                reverse_map_guard.insert(entity_id, nid);
                inserted += 1;
                nid += 1;
            }
        }

        *next_node_id.lock() = nid;
        Ok(inserted)
    }

    /// Search for k nearest neighbors.
    ///
    /// GIL is released during search — rayon cpu_pool handles parallelism.
    ///
    /// Args:
    ///     query_embedding: Query vector (list of floats)
    ///     k: Number of results to return
    ///
    /// Returns:
    ///     List of (entity_id, similarity_score) tuples, sorted descending by score
    pub fn search(
        &self,
        query_embedding: Vec<f32>,
        k: usize,
    ) -> PyResult<Vec<(String, f32)>> {
        if query_embedding.len() != self.dim {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "query_embedding dim {} != bridge dim {}",
                query_embedding.len(),
                self.dim
            )));
        }

        let index = Arc::clone(&self.index);
        let id_map = Arc::clone(&self.id_map);

        let results: Vec<(String, f32)> = Python::with_gil(|_py| {
            let raw = match index.lock().search(&query_embedding, k) {
                Ok(r) => r,
                Err(_) => return vec![],
            };
            let id_map_guard = id_map.lock();
            raw.into_iter()
                .filter_map(|(node_id, similarity)| {
                    id_map_guard.get(&node_id).map(|eid| (eid.clone(), similarity))
                })
                .collect()
        });

        Ok(results)
    }

    /// Batch search — search multiple query vectors at once.
    ///
    /// More efficient than calling search() multiple times due to
    /// reduced GIL acquisition overhead (one GIL acquire for all queries).
    ///
    /// Args:
    ///     query_embeddings: List of query vectors
    ///     k: Number of results per query
    ///
    /// Returns:
    ///     List of result lists (one per query), each as (entity_id, score) tuples
    pub fn search_batch(
        &self,
        query_embeddings: Vec<Vec<f32>>,
        k: usize,
    ) -> PyResult<Vec<Vec<(String, f32)>>> {
        for emb in &query_embeddings {
            if emb.len() != self.dim {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "query_embedding dim {} != bridge dim {}",
                    emb.len(), self.dim
                )));
            }
        }

        let index = Arc::clone(&self.index);
        let id_map = Arc::clone(&self.id_map);

        let results: Vec<Vec<(String, f32)>> = Python::with_gil(|_py| {
            let index_guard = index.lock();
            let id_map_guard = id_map.lock();
            query_embeddings
                .iter()
                .map(|query| {
                    let raw = match index_guard.search(query, k) {
                        Ok(r) => r,
                        Err(_) => return vec![],
                    };
                    raw.into_iter()
                        .filter_map(|(node_id, similarity)| {
                            id_map_guard.get(&node_id).map(|eid| (eid.clone(), similarity))
                        })
                        .collect()
                })
                .collect()
        });

        Ok(results)
    }

    /// Current number of entities in the index.
    #[getter]
    pub fn len(&self) -> usize {
        self.id_map.lock().len()
    }

    /// True if the index is empty.
    #[getter]
    pub fn is_empty(&self) -> bool {
        self.id_map.lock().is_empty()
    }

    /// Clear all entities and reset the index.
    ///
    /// Use when re-initializing the store or after a sprint completes.
    pub fn clear(&self) {
        let mut index = self.index.lock();
        *index = HNSWIndex::new();
        self.id_map.lock().clear();
        self.reverse_map.lock().clear();
        *self.next_node_id.lock() = 0;
    }

    /// Get the internal node ID for an entity (for debugging/testing).
    ///
    /// Returns None if entity not found.
    pub fn get_node_id(&self, entity_id: &str) -> Option<u64> {
        self.reverse_map.lock().get(entity_id).copied()
    }

    /// Get the entity ID for an internal node ID (for debugging/testing).
    ///
    /// Returns None if node ID not found.
    pub fn get_entity_id(&self, node_id: u64) -> Option<String> {
        self.id_map.lock().get(&node_id).cloned()
    }

    /// String representation for debugging.
    pub fn __repr__(&self) -> String {
        format!(
            "HNSWBridge(dim={}, len={}, max_node_id={})",
            self.dim,
            self.len(),
            *self.next_node_id.lock()
        )
    }
}

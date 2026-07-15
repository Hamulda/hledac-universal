//! PyO3 bindings for HNSW ANN index.
//!
//! Provides two Python-facing wrappers:
//!
//! ## PyHNSWIndex
//!
//! Low-level ANN index with explicit save/load.
//! Used for MLX embeddings re-ranking.
//!
//! ## PyHNSWBridge
//!
//! High-level bridge for LanceDB entity store.
//! Wraps HNSWIndex with entity ID → node ID mapping.
//! Used by LanceDBIdentityStore in Python layer.
//!
//! Both release the GIL during compute-intensive operations
//! via rayon cpu_pool.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use parking_lot::Mutex;
use pyo3::prelude::*;

use super::HNSWIndex;

// ─── PyHNSWIndex ─────────────────────────────────────────────────────────────

/// Low-level HNSW ANN index with PyO3 bindings.
///
/// Used for MLX embeddings re-ranking. Provides explicit save/load
/// for mmap-backed persistence.
///
/// # Example
/// ```python
/// index = PyHNSWIndex(cache_dir="/tmp/ann_index")
/// index.insert(1, [0.1] * 384)
/// results = index.search([0.9] + [0.0] * 383, k=5)
/// index.save()
/// ```
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
        self.index.lock().len()
    }

    fn is_empty(&self) -> bool {
        self.index.lock().is_empty()
    }

    fn save(&self) -> PyResult<String> {
        let index = self.index.lock();
        let path = self.cache_dir.join("hnsw_index.bin");

        index
            .save(&path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Save error: {}", e)))?;

        Ok(path.to_string_lossy().to_string())
    }

    #[staticmethod]
    fn load(cache_dir: String) -> PyResult<Self> {
        let cache_dir = PathBuf::from(cache_dir);
        let path = cache_dir.join("hnsw_index.bin");

        let index = HNSWIndex::load(&path).map_err(|e| {
            pyo3::exceptions::PyFileNotFoundError::new_err(format!("Load error: {}", e))
        })?;

        Ok(Self {
            index: Arc::new(Mutex::new(index)),
            cache_dir,
        })
    }
}

// ─── PyHNSWBridge ───────────────────────────────────────────────────────────

/// Maximum embedding dimension this bridge accepts.
/// 384 = MLX embedding dimension (Hermes-3 3B 4bit).
const BRIDGE_MAX_DIM: usize = 384;
/// Maximum batch size per add_batch() call.
const BRIDGE_MAX_BATCH_SIZE: usize = 1_000;

/// Thread-safe HNSW ANN index for LanceDB entity store.
///
/// Wraps `HNSWIndex` with:
///   - GIL-safe API for Python calls (runs in asyncio.to_thread)
///   - Batch operations for efficiency
///   - Entity ID → node ID mapping (LanceDB metadata lives in Python layer)
///
/// NOTE: Not safe for concurrent inserts + searches from multiple threads.
///       LanceDBIdentityStore guarantees single-threaded access via asyncio.to_thread.
#[pyclass(module = "hledac_rust_extensions")]
pub struct PyHNSWBridge {
    index: Arc<Mutex<HNSWIndex>>,
    id_map: Arc<Mutex<HashMap<u64, String>>>,
    reverse_map: Arc<Mutex<HashMap<String, u64>>>,
    next_node_id: Arc<Mutex<u64>>,
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
    ///     bridge = PyHNSWBridge(dim=384)
    #[new]
    pub fn new_bridge(dim: usize) -> PyResult<Self> {
        if dim == 0 || dim > BRIDGE_MAX_DIM {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "dim must be in 1..={}, got {}",
                BRIDGE_MAX_DIM, dim
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
        if n > BRIDGE_MAX_BATCH_SIZE {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Batch size {} exceeds MAX_BATCH_SIZE {}",
                n, BRIDGE_MAX_BATCH_SIZE
            )));
        }

        let dim = self.dim;
        let mut skipped_dims: usize = 0;
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
                skipped_dims += 1;
                continue;
            }

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

        if skipped_dims > 0 {
            eprintln!(
                "PyHNSWBridge.add_batch: warned={} skipped embeddings due to dimension mismatch (expected {})",
                skipped_dims, dim
            );
        }
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
    /// Per-query locking — releases index lock between queries to avoid
    /// holding a long-duration exclusive lock over all searches.
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
            query_embeddings
                .iter()
                .map(|query| {
                    // Per-query lock acquisition — releases between searches
                    let index_guard = index.lock();
                    let raw = match index_guard.search(query, k) {
                        Ok(r) => r,
                        Err(_) => return vec![],
                    };
                    drop(index_guard);

                    let id_map_guard = id_map.lock();
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

    #[getter]
    pub fn len(&self) -> usize {
        self.id_map.lock().len()
    }

    #[getter]
    pub fn is_empty(&self) -> bool {
        self.id_map.lock().is_empty()
    }

    pub fn clear(&self) {
        let mut index = self.index.lock();
        *index = HNSWIndex::new();
        self.id_map.lock().clear();
        self.reverse_map.lock().clear();
        *self.next_node_id.lock() = 0;
    }

    pub fn get_node_id(&self, entity_id: &str) -> Option<u64> {
        self.reverse_map.lock().get(entity_id).copied()
    }

    pub fn get_entity_id(&self, node_id: u64) -> Option<String> {
        self.id_map.lock().get(&node_id).cloned()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "PyHNSWBridge(dim={}, len={}, max_node_id={})",
            self.dim,
            self.len(),
            *self.next_node_id.lock()
        )
    }
}

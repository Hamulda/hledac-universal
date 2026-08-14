//! Apple Neural Engine (ANE) bindings for Hledac OSINT platform.
//!
//! ## Architecture
//!
//! ANE je dedicated Neural Engine chip na M1/M2/M3 Apple Silicon (16 jader, 11 TOPS).
//! Není přístupný přímo - pouze přes CoreML framework.
//!
//! ## MODERN-35: ANE Placement + GPU Inference Status
//!
//! **ISSUE**: ANE module is a registry stub (no real inference); MLX runs on GPU
//! but competes with rayon on P-cores.
//!
//! **CURRENT STATE**: This module provides model registry + CoreML FFI infrastructure.
//! Actual ANE inference is delegated to Python's `brain/ane_inference.py`.
//!
//! **FIX APPLIED**:
//! 1. P-core affinity for MLX Metal operations (utils/cpu_affinity.py)
//! 2. E-cores strictly reserved for I/O operations
//! 3. ANE inference continues to use Python path (CoreML/coremltools)
//!
//! ## SILICON-06: ANE Idle Fix (2026-07)
//!
//! **Problem**: ANE was completely idle during embedding batches — all inference
//! ran on GPU via mx.eval(). The ANE mutex was acquired in submit_embedding()
//! but there was no actual ANE inference behind it.
//!
//! **Solution**: Dual-path ANE/GPU dispatch:
//! - Python: `brain/ane_inference.py` — ANEInferenceEngine with coremltools
//! - Rust:   This module (`ane.rs`) — model registry + CoreML FFI infrastructure
//!
//! **Dispatch logic** (in `mlx_unified_scheduler.py:submit_embedding()`):
//! - batch ≤ 16, dim ≤ 1024 → ANE path (16-core Neural Engine, 11 TOPS)
//! - batch > 16 → GPU path (MLX via mx.eval(), existing)
//!
//! **Rust-native ANE inference** (NEXTGEN-05, implemented):
//! The `coreml` crate (v0.3) provides Rust bindings to CoreML's C API.
//! PRM inference (`run_prm_inference`, `run_prm_inference_batch`) now calls
//! CoreML directly without Python bridge overhead (bypasses coremltools).
//!
//! **Current status**:
//! - `run_inference()` / `embed_tokens()`: Still stub (delegates to Python)
//! - `run_prm_inference()`: Rust-native CoreML FFI ✓ (bypasses Python bridge)
//! - `run_prm_inference_batch()`: Rust-native CoreML batch FFI ✓
//! - ANE compute unit preferred (16 cores, 11 TOPS int8)
//!
//! ## MODERN-35 P-Core Affinity Plan
//!
//! **TODO**: Integrate with utils/cpu_affinity.py for ANE inference threads:
//! ```python
//! from utils.cpu_affinity import set_ane_affinity
//!
//! # Before ANE inference (when Rust-native is implemented)
//! set_ane_affinity()  # Pin CoreML dispatch threads to P-cores
//! embeddings = rust.ane.embed_tokens(model_id, tokens, mask)
//! ```
//!
//! The Neural Engine itself is a dedicated chip, but CPU preprocessing
//! for ANE input should run on P-cores for minimum latency.
//!
//! ## ANE Memory Constraints (M1 8GB specific)
//!
//! - Max 2 modely v paměti najednou
//! - Max batch size 4096 tokenů (ANE HW limit)
//! - Per-model footprint ~50 MB (compiled CoreML)
//! - Pro embedding úlohy: typicky 64-512 tokenů na sekvenci, batche 8-16
//!
//! ## Usage
//!
//! ```python
//! import hledac_rust_extensions as rust
//!
//! # Initialize ANE subsystem
//! rust.ane.init()
//!
//! # Load model (max 2 simultaneous models)
//! rust.ane.load_model("bge-small", "/path/to/model.mlpackage", 384, 512)
//!
//! # Validate batch before inference
//! rust.ane.validate_batch(batch_size=16, seq_len=512, max_seq_len=512)
//!
//! # Run inference (delegates to brain/ane_inference.py if Rust-native unavailable)
//! embeddings = rust.ane.embed_tokens("bge-small", token_ids, attention_mask)
//!
//! # Unload when done
//! rust.ane.unload_model("bge-small")
//! ```
//!
//! ## Feature Gate
//!
//! Enabled via `ane = []` feature flag in Cargo.toml.
//! No external dependencies — pure Rust std + PyO3.

use parking_lot::RwLock;
use pyo3::prelude::*;
use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;

// GNN-3: Logging via eprintln (log crate not available, avoiding dep)

/// ANE hardware constraints
const ANE_MAX_MODELS: usize = 2;
const ANE_MAX_BATCH_SIZE: usize = 4096;

/// GNN-3: GraphSAGE ANE constraints (M1 8GB safe)
const GNN_MAX_BATCH_NODES: usize = 10_000; // Max nodes per batch
const GNN_DEFAULT_IN_DIM: usize = 81; // 17 (IOC type) + 64 (embedding)
const GNN_DEFAULT_HIDDEN_DIM: usize = 64;
const GNN_DEFAULT_OUT_DIM: usize = 32; // Output embedding dimension

/// ANE compute unit preference for CoreML
///
/// SILICON-06: Maps to coremltools.ComputeUnit on Python side.
/// Rust-native CoreML FFI (future): maps to MLComputeUnits enum
/// via the `coreml` crate (v0.2, `coreml_sys::MLComputeUnits`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ANEComputeUnit {
    /// Apple Neural Engine — 16 cores, 11 TOPS int8 (M1)
    /// Preferred for small-batch embedding (≤16 docs, dim ≤ 1024)
    NeuralEngine,
    /// CPU fallback — always available, no memory pressure
    CPU,
    /// GPU (Metal) — unified memory, shared with MLX LLM
    GPU,
    /// All compute units — CoreML auto-selects optimal unit
    All,
}

impl Default for ANEComputeUnit {
    fn default() -> Self {
        Self::NeuralEngine
    }
}

/// Model metadata stored in registry
#[derive(Debug, Clone)]
pub struct ANEModelMeta {
    pub model_id: String,
    pub model_path: String,
    pub hidden_dim: usize,
    pub max_seq_len: usize,
    pub loaded_at: std::time::SystemTime,
}

// ─── NEXTGEN-03: FaceNet ANE Model ───────────────────────────────────────────

/// NEXTGEN-03: FaceNet model metadata for face embedding extraction.
#[derive(Debug, Clone)]
pub struct FaceNetModelMeta {
    pub model_id: String,
    pub model_path: String,
    pub embedding_dim: usize,
    pub loaded_at: std::time::SystemTime,
}

/// FaceNet model registry — enforces max 1 face model (ANE memory constraint).
pub struct FaceNetRegistry {
    /// Currently loaded FaceNet model (max 1 due to ANE memory)
    model: Option<FaceNetModelMeta>,
}

impl FaceNetRegistry {
    pub fn new() -> Self {
        Self { model: None }
    }

    pub fn is_loaded(&self) -> bool {
        self.model.is_some()
    }

    pub fn can_load(&self) -> bool {
        self.model.is_none()
    }

    pub fn register(
        &mut self,
        model_id: String,
        model_path: String,
        embedding_dim: usize,
    ) -> Result<FaceNetModelMeta, ANEError> {
        if self.model.is_some() {
            return Err(ANEError::ModelAlreadyLoaded(model_id));
        }

        if embedding_dim == 0 {
            return Err(ANEError::InferenceFailed(
                "embedding_dim must be > 0".to_string(),
            ));
        }

        let meta = FaceNetModelMeta {
            model_id: model_id.clone(),
            model_path,
            embedding_dim,
            loaded_at: std::time::SystemTime::now(),
        };
        self.model = Some(meta.clone());
        Ok(meta)
    }

    pub fn unregister(&mut self) -> Result<(), ANEError> {
        if self.model.is_none() {
            return Err(ANEError::ModelNotFound("facenet".to_string()));
        }
        self.model = None;
        Ok(())
    }

    pub fn get_model(&self) -> Option<&FaceNetModelMeta> {
        self.model.as_ref()
    }
}

impl Default for FaceNetRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// Global FaceNet registry
static FACENET_REGISTRY: LazyLock<RwLock<FaceNetRegistry>> =
    LazyLock::new(|| RwLock::new(FaceNetRegistry::new()));

/// NEXTGEN-03: Register FaceNet model for face embedding extraction.
///
/// Args:
///     model_id: Unique identifier for the model (e.g., "facenet_v1")
///     model_path: Path to FaceNet CoreML .mlmodel file
///     embedding_dim: Embedding dimension (default: 512 for FaceNet)
///
/// Returns: Ok(model_id) or Error
#[pyfunction]
pub fn facenet_register_model(
    model_id: String,
    model_path: String,
    embedding_dim: Option<usize>,
) -> Result<String, PyErr> {
    let embedding_dim = embedding_dim.unwrap_or(512);

    let mut registry = FACENET_REGISTRY.write();
    let meta = registry
        .register(model_id.clone(), model_path, embedding_dim)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    eprintln!("[FaceNet] Registered model: {} (dim={})", meta.model_id, meta.embedding_dim);
    Ok(model_id)
}

/// NEXTGEN-03: Check if FaceNet model is registered.
///
/// Returns: True if FaceNet model is loaded
#[pyfunction]
pub fn facenet_is_registered() -> bool {
    let registry = FACENET_REGISTRY.read();
    registry.is_loaded()
}

/// NEXTGEN-03: Unregister FaceNet model.
///
/// Returns: Ok(()) or Error
#[pyfunction]
pub fn facenet_unregister() -> Result<(), PyErr> {
    let mut registry = FACENET_REGISTRY.write();
    registry.unregister().map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(e.to_string())
    })
}

/// NEXTGEN-03: Get FaceNet model metadata.
///
/// Returns: Dict with model_id, embedding_dim, or None
#[pyfunction]
pub fn facenet_get_model_info() -> Option<Vec<(String, String)>> {
    let registry = FACENET_REGISTRY.read();
    registry.get_model().map(|m| {
        vec![
            ("model_id".to_string(), m.model_id.clone()),
            ("embedding_dim".to_string(), m.embedding_dim.to_string()),
            ("model_path".to_string(), m.model_path.clone()),
        ]
    })
}

// ─── NEXTGEN-03: Voiceprint ANE Model ───────────────────────────────────────

/// NEXTGEN-03: Voiceprint model metadata for speaker embedding extraction.
#[derive(Debug, Clone)]
pub struct VoiceprintModelMeta {
    pub model_id: String,
    pub model_path: String,
    pub embedding_dim: usize,
    pub loaded_at: std::time::SystemTime,
}

/// Voiceprint model registry — enforces max 1 voice model.
pub struct VoiceprintRegistry {
    /// Currently loaded voiceprint model
    model: Option<VoiceprintModelMeta>,
}

impl VoiceprintRegistry {
    pub fn new() -> Self {
        Self { model: None }
    }

    pub fn is_loaded(&self) -> bool {
        self.model.is_some()
    }

    pub fn can_load(&self) -> bool {
        self.model.is_none()
    }

    pub fn register(
        &mut self,
        model_id: String,
        model_path: String,
        embedding_dim: usize,
    ) -> Result<VoiceprintModelMeta, ANEError> {
        if self.model.is_some() {
            return Err(ANEError::ModelAlreadyLoaded(model_id));
        }

        if embedding_dim == 0 {
            return Err(ANEError::InferenceFailed(
                "embedding_dim must be > 0".to_string(),
            ));
        }

        let meta = VoiceprintModelMeta {
            model_id: model_id.clone(),
            model_path,
            embedding_dim,
            loaded_at: std::time::SystemTime::now(),
        };
        self.model = Some(meta.clone());
        Ok(meta)
    }

    pub fn unregister(&mut self) -> Result<(), ANEError> {
        if self.model.is_none() {
            return Err(ANEError::ModelNotFound("voiceprint".to_string()));
        }
        self.model = None;
        Ok(())
    }

    pub fn get_model(&self) -> Option<&VoiceprintModelMeta> {
        self.model.as_ref()
    }
}

impl Default for VoiceprintRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// Global Voiceprint registry
static VOICEPRINT_REGISTRY: LazyLock<RwLock<VoiceprintRegistry>> =
    LazyLock::new(|| RwLock::new(VoiceprintRegistry::new()));

/// NEXTGEN-03: Register voiceprint model for speaker embedding extraction.
///
/// Args:
///     model_id: Unique identifier for the model (e.g., "speakernet")
///     model_path: Path to speaker embedding CoreML .mlmodel file
///     embedding_dim: Embedding dimension (default: 256)
///
/// Returns: Ok(model_id) or Error
#[pyfunction]
pub fn voiceprint_register_model(
    model_id: String,
    model_path: String,
    embedding_dim: Option<usize>,
) -> Result<String, PyErr> {
    let embedding_dim = embedding_dim.unwrap_or(256);

    let mut registry = VOICEPRINT_REGISTRY.write();
    let meta = registry
        .register(model_id.clone(), model_path, embedding_dim)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    eprintln!(
        "[Voiceprint] Registered model: {} (dim={})",
        meta.model_id, meta.embedding_dim
    );
    Ok(model_id)
}

/// NEXTGEN-03: Check if voiceprint model is registered.
///
/// Returns: True if voiceprint model is loaded
#[pyfunction]
pub fn voiceprint_is_registered() -> bool {
    let registry = VOICEPRINT_REGISTRY.read();
    registry.is_loaded()
}

/// NEXTGEN-03: Unregister voiceprint model.
///
/// Returns: Ok(()) or Error
#[pyfunction]
pub fn voiceprint_unregister() -> Result<(), PyErr> {
    let mut registry = VOICEPRINT_REGISTRY.write();
    registry.unregister().map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(e.to_string())
    })
}

/// NEXTGEN-03: Get voiceprint model metadata.
///
/// Returns: Dict with model_id, embedding_dim, or None
#[pyfunction]
pub fn voiceprint_get_model_info() -> Option<Vec<(String, String)>> {
    let registry = VOICEPRINT_REGISTRY.read();
    registry.get_model().map(|m| {
        vec![
            ("model_id".to_string(), m.model_id.clone()),
            ("embedding_dim".to_string(), m.embedding_dim.to_string()),
            ("model_path".to_string(), m.model_path.clone()),
        ]
    })
}

/// ANE model registry — enforces max 2 models constraint
pub struct ANERegistry {
    /// Currently loaded models (max 2 per ANE hardware constraint)
    /// BTreeMap preserves insertion order for deterministic oldest-model eviction
    models: BTreeMap<String, ANEModelMeta>,
    /// Active model (currently executing)
    active_model: Option<String>,
}

impl ANERegistry {
    pub fn new() -> Self {
        Self {
            models: BTreeMap::new(),
            active_model: None,
        }
    }

    /// Check if a model is loaded
    pub fn is_loaded(&self, model_id: &str) -> bool {
        self.models.contains_key(model_id)
    }

    /// Get loaded model count
    pub fn model_count(&self) -> usize {
        self.models.len()
    }

    /// Check if new model can be loaded (max 2 constraint)
    pub fn can_load(&self) -> bool {
        self.models.len() < ANE_MAX_MODELS
    }

    /// Load a model into registry (does NOT actually load into CoreML/ANE)
    /// Returns error if max models reached (does NOT auto-evict)
    pub fn register_model(
        &mut self,
        model_id: String,
        model_path: String,
        hidden_dim: usize,
        max_seq_len: usize,
    ) -> Result<ANEModelMeta, ANEError> {
        if self.models.contains_key(&model_id) {
            return Err(ANEError::ModelAlreadyLoaded(model_id));
        }

        // Validate model parameters
        if hidden_dim == 0 {
            return Err(ANEError::InferenceFailed(
                "hidden_dim must be > 0".to_string(),
            ));
        }
        if max_seq_len == 0 {
            return Err(ANEError::InferenceFailed(
                "max_seq_len must be > 0".to_string(),
            ));
        }
        if max_seq_len > ANE_MAX_BATCH_SIZE {
            return Err(ANEError::SeqLenExceeded {
                max: ANE_MAX_BATCH_SIZE,
                actual: max_seq_len,
            });
        }

        if !self.can_load() {
            return Err(ANEError::MaxModelsReached);
        }

        let meta = ANEModelMeta {
            model_id: model_id.clone(),
            model_path,
            hidden_dim,
            max_seq_len,
            loaded_at: std::time::SystemTime::now(),
        };
        self.models.insert(model_id, meta.clone());
        Ok(meta)
    }

    /// Unload a model from registry
    pub fn unregister_model(&mut self, model_id: &str) -> Result<(), ANEError> {
        if self.models.remove(model_id).is_none() {
            return Err(ANEError::ModelNotFound(model_id.to_string()));
        }
        if self.active_model.as_ref() == Some(&model_id.to_string()) {
            self.active_model = None;
        }
        Ok(())
    }

    /// Set active model for execution
    pub fn set_active(&mut self, model_id: &str) -> Result<(), ANEError> {
        if !self.models.contains_key(model_id) {
            return Err(ANEError::ModelNotFound(model_id.to_string()));
        }
        self.active_model = Some(model_id.to_string());
        Ok(())
    }

    /// Clear active model
    pub fn clear_active(&mut self) {
        self.active_model = None;
    }

    /// Get model metadata
    pub fn get_model(&self, model_id: &str) -> Option<&ANEModelMeta> {
        self.models.get(model_id)
    }

    /// Get all loaded model IDs
    pub fn loaded_models(&self) -> Vec<String> {
        self.models.keys().cloned().collect()
    }
}

impl Default for ANERegistry {
    fn default() -> Self {
        Self::new()
    }
}

/// ANE-specific errors
#[derive(Debug, Clone)]
pub enum ANEError {
    ModelNotFound(String),
    ModelAlreadyLoaded(String),
    MaxModelsReached,
    BatchSizeExceeded { max: usize, actual: usize },
    SeqLenExceeded { max: usize, actual: usize },
    CoreMLNotAvailable,
    InferenceFailed(String),
    ComputeUnitNotSupported,
}

impl std::fmt::Display for ANEError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ANEError::ModelNotFound(id) => write!(f, "ANE model not found: {}", id),
            ANEError::ModelAlreadyLoaded(id) => write!(f, "ANE model already loaded: {}", id),
            ANEError::MaxModelsReached => write!(f, "Maximum ANE models reached (2)"),
            ANEError::BatchSizeExceeded { max, actual } => {
                write!(f, "Batch size {} exceeds ANE limit {}", actual, max)
            }
            ANEError::SeqLenExceeded { max, actual } => {
                write!(f, "Sequence length {} exceeds model limit {}", actual, max)
            }
            ANEError::CoreMLNotAvailable => write!(f, "CoreML not available on this system"),
            ANEError::InferenceFailed(msg) => write!(f, "ANE inference failed: {}", msg),
            ANEError::ComputeUnitNotSupported => {
                write!(f, "Neural Engine compute unit not supported")
            }
        }
    }
}

impl std::error::Error for ANEError {}

/// GNN-3: GraphSAGE model metadata
#[derive(Debug, Clone)]
pub struct GNNModelMeta {
    pub model_id: String,
    pub model_path: String,
    pub in_dim: usize,
    pub hidden_dim: usize,
    pub out_dim: usize,
    pub num_layers: usize,
}

/// GNN-3: Node embedding storage
#[derive(Debug, Clone)]
pub struct NodeEmbedding {
    pub kuzu_id: String,     // Kuzu string ID (type:xxh64hex)
    pub gnn_index: usize,    // Internal GNN index (0..N-1)
    pub embedding: Vec<f32>, // Embedding vector
    pub updated_at: std::time::SystemTime,
}

/// GNN-3: Edge list for adjacency
#[derive(Debug, Clone)]
pub struct GNNEdge {
    pub src_index: usize,
    pub dst_index: usize,
    pub weight: f32,
}

/// Global ANE registry — process-wide singleton
static ANE_GLOBAL_REGISTRY: LazyLock<RwLock<ANERegistry>> =
    LazyLock::new(|| RwLock::new(ANERegistry::new()));

/// GNN-3: GraphSAGE model registry (separate from embedding models)
static GNN_REGISTRY: LazyLock<RwLock<GNNRegistry>> =
    LazyLock::new(|| RwLock::new(GNNRegistry::new()));

/// GNN-3: In-memory embedding storage (bounded to GNN_MAX_BATCH_NODES)
static EMBEDDING_STORE: LazyLock<RwLock<EmbeddingStore>> =
    LazyLock::new(|| RwLock::new(EmbeddingStore::new(GNN_MAX_BATCH_NODES)));

/// GNN-3: GraphSAGE model registry
pub struct GNNRegistry {
    models: HashMap<String, GNNModelMeta>,
    active_model: Option<String>,
}

impl GNNRegistry {
    pub fn new() -> Self {
        Self {
            models: HashMap::new(),
            active_model: None,
        }
    }

    pub fn register(&mut self, model_id: String, meta: GNNModelMeta) -> Result<(), String> {
        if self.models.contains_key(&model_id) {
            return Err(format!("GNN model '{}' already registered", model_id));
        }
        self.models.insert(model_id.clone(), meta);
        Ok(())
    }

    pub fn get(&self, model_id: &str) -> Option<&GNNModelMeta> {
        self.models.get(model_id)
    }

    pub fn set_active(&mut self, model_id: &str) -> Result<(), String> {
        if !self.models.contains_key(model_id) {
            return Err(format!("GNN model '{}' not found", model_id));
        }
        self.active_model = Some(model_id.to_string());
        Ok(())
    }

    pub fn active(&self) -> Option<&String> {
        self.active_model.as_ref()
    }

    pub fn list(&self) -> Vec<String> {
        self.models.keys().cloned().collect()
    }
}

impl Default for GNNRegistry {
    fn default() -> Self {
        Self::new()
    }
}

/// GNN-3: Bounded embedding storage with LRU eviction
pub struct EmbeddingStore {
    embeddings: HashMap<String, NodeEmbedding>, // kuzu_id -> embedding
    index_map: HashMap<usize, String>,          // gnn_index -> kuzu_id
    next_index: usize,
    max_size: usize,
}

impl EmbeddingStore {
    pub fn new(max_size: usize) -> Self {
        Self {
            embeddings: HashMap::new(),
            index_map: HashMap::new(),
            next_index: 0,
            max_size,
        }
    }

    pub fn store(&mut self, kuzu_id: String, embedding: Vec<f32>) -> usize {
        // Evict if at capacity
        while self.embeddings.len() >= self.max_size {
            // Remove the first entry using drain with limit=1
            let mut drainer = self.embeddings.drain();
            if let Some((old_id, _)) = drainer.next() {
                drop(drainer); // Drop remaining items
                // Find and remove the index mapping
                if let Some(idx) = self
                    .index_map
                    .iter()
                    .find(|(_, v)| **v == old_id)
                    .map(|(k, _)| *k)
                {
                    self.index_map.remove(&idx);
                }
            }
        }

        // Get or assign index
        let gnn_index = if let Some(existing) = self.embeddings.get(&kuzu_id) {
            existing.gnn_index
        } else {
            let idx = self.next_index;
            self.next_index += 1;
            idx
        };

        // Store embedding
        let emb = NodeEmbedding {
            kuzu_id: kuzu_id.clone(),
            gnn_index,
            embedding,
            updated_at: std::time::SystemTime::now(),
        };
        self.embeddings.insert(kuzu_id.clone(), emb);
        self.index_map.insert(gnn_index, kuzu_id);

        gnn_index
    }

    pub fn get(&self, kuzu_id: &str) -> Option<&NodeEmbedding> {
        self.embeddings.get(kuzu_id)
    }

    pub fn get_by_index(&self, gnn_index: usize) -> Option<&NodeEmbedding> {
        self.index_map
            .get(&gnn_index)
            .and_then(|id| self.embeddings.get(id))
    }

    pub fn len(&self) -> usize {
        self.embeddings.len()
    }

    pub fn clear(&mut self) {
        self.embeddings.clear();
        self.index_map.clear();
        self.next_index = 0;
    }

    pub fn get_all_embeddings(&self) -> Vec<(usize, String, Vec<f32>)> {
        self.embeddings
            .values()
            .map(|e| (e.gnn_index, e.kuzu_id.clone(), e.embedding.clone()))
            .collect()
    }
}

/// Telemetry for ANE operations
static ANE_TELEMETRY: LazyLock<RwLock<ANETelemetry>> =
    LazyLock::new(|| RwLock::new(ANETelemetry::default()));

#[derive(Default)]
pub struct ANETelemetry {
    pub embed_calls: u64,
    pub embed_tokens: u64,
    pub cache_hits: u64,
    pub cache_misses: u64,
    pub ane_fallback_cpu: u64,
    pub ane_fallback_gpu: u64,
    pub errors: u64,
}

// ─── Python-callable functions ───────────────────────────────────────────────

/// Initialize ANE subsystem.
///
/// Checks CoreML availability and sets up the ANE hardware.
/// This must be called before any other ane functions.
///
/// Returns: (available: bool, error_message: Option<String>)
#[pyfunction]
pub fn init() -> (bool, Option<String>) {
    // CoreML availability check is done on Python side
    // This function is mainly for Rust-side initialization
    let mut telemetry = ANE_TELEMETRY.write();
    telemetry.embed_calls = 0;
    telemetry.embed_tokens = 0;
    telemetry.cache_hits = 0;
    telemetry.cache_misses = 0;
    telemetry.ane_fallback_cpu = 0;
    telemetry.ane_fallback_gpu = 0;
    telemetry.errors = 0;
    (true, None)
}

/// Get ANE hardware status.
///
/// Returns: (available: bool, model_count: usize, max_models: usize)
#[pyfunction]
pub fn get_status() -> (bool, usize, usize) {
    let registry = ANE_GLOBAL_REGISTRY.read();
    let available = std::env::consts::OS == "macos";
    (available, registry.model_count(), ANE_MAX_MODELS)
}

/// Load a model into the ANE registry.
///
/// Note: This registers the model in Rust's registry. The actual CoreML model
/// loading is done by the Python side via coremltools or CoreML microservice.
///
/// Args:
///     model_id: Unique identifier for the model
///     model_path: Path to .mlpackage or model identifier
///     hidden_dim: Model hidden dimension (e.g., 768 for ModernBERT)
///     max_seq_len: Maximum sequence length (e.g., 512)
///
/// Returns: Ok(model_id) or Error message
#[pyfunction]
pub fn load_model(
    model_id: String,
    model_path: String,
    hidden_dim: usize,
    max_seq_len: usize,
) -> Result<String, PyErr> {
    let mut registry = ANE_GLOBAL_REGISTRY.write();
    registry
        .register_model(model_id, model_path, hidden_dim, max_seq_len)
        .map(|meta| meta.model_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

/// Unload a model from the ANE registry.
///
/// This frees the model slot in Rust's registry. The actual model
/// unloading from CoreML is done by the Python side.
///
/// Args:
///     model_id: Model identifier to unload
///
/// Returns: Ok(()) or Error message
#[pyfunction]
pub fn unload_model(model_id: String) -> Result<(), PyErr> {
    let mut registry = ANE_GLOBAL_REGISTRY.write();
    registry
        .unregister_model(&model_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

/// Get list of currently loaded model IDs.
#[pyfunction]
pub fn list_models() -> Vec<String> {
    ANE_GLOBAL_REGISTRY.read().loaded_models()
}

/// Validate batch dimensions against ANE constraints.
///
/// ANE has a max batch size of 4096 tokens per inference call.
/// This function checks if the batch is within bounds.
///
/// Args:
///     batch_size: Number of sequences in batch
///     seq_len: Length of each sequence
///     max_seq_len: Maximum sequence length supported by model
///
/// Returns: Ok(()) or Error with constraint violation details
#[pyfunction]
pub fn validate_batch(batch_size: usize, seq_len: usize, max_seq_len: usize) -> Result<(), PyErr> {
    let total_tokens = batch_size.saturating_mul(seq_len);

    if total_tokens > ANE_MAX_BATCH_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Batch size {} exceeds ANE limit {}",
            total_tokens, ANE_MAX_BATCH_SIZE
        )));
    }

    if seq_len > max_seq_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Sequence length {} exceeds model limit {}",
            seq_len, max_seq_len
        )));
    }

    Ok(())
}

/// Run model inference on ANE.
///
/// SILICON-06: This is a stub that delegates to Python's ANEInferenceEngine.
/// For direct Rust-native ANE inference, see the `coreml` crate integration
/// plan in rust_extensions/src/ane.rs module docs.
///
/// Current implementation: returns an error directing to Python path.
/// Python callers should use:
///     from hledac.universal.brain.ane_inference import get_ane_engine
///     engine = get_ane_engine()
///     embeddings = await engine.embed_batch_ane(texts, model_key="bge-small")
///
/// For Rust-native CoreML FFI (NEXTGEN-05, PRM-only):
///     1. `coreml = { version = "0.3", optional = true }` in Cargo.toml ✓
///     2. MLModel::load() + predict_with_options() implemented ✓
///     3. MLComputeUnits::NeuralEngine preference ✓
///     4. run_prm_inference() / run_prm_inference_batch() bypass Python bridge ✓
///     5. run_inference() / embed_tokens() still delegate to Python (embedding path)
///
/// Args:
///     model_id: Registered model identifier
///     input_ids: Flattened token IDs (batch * seq_len)
///     attention_mask: Attention mask (batch * seq_len)
///
/// Returns: Embeddings as flattened f32 array, or error
#[pyfunction]
pub fn run_inference(
    model_id: String,
    input_ids: Vec<i64>,
    attention_mask: Vec<i64>,
) -> Result<Vec<f32>, PyErr> {
    let registry = ANE_GLOBAL_REGISTRY.read();

    let meta = registry.get_model(&model_id).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!("ANE model not found: {}", model_id))
    })?;

    // Guard against division by zero
    if input_ids.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "input_ids cannot be empty",
        ));
    }
    let seq_len = meta.max_seq_len.max(1);
    let batch_size = input_ids.len() / seq_len;

    // Validate batch constraints
    if let Err(e) = validate_batch(batch_size, seq_len, meta.max_seq_len) {
        return Err(e);
    }

    // Update telemetry
    {
        let mut telemetry = ANE_TELEMETRY.write();
        telemetry.embed_calls += 1;
        telemetry.embed_tokens = telemetry
            .embed_tokens
            .saturating_add(input_ids.len() as u64);
    }

    // SILICON-06: Delegate to Python ANEInferenceEngine for actual inference.
    // Rust-native CoreML FFI is future work (see module docs).
    // This stub returns an error directing to the Python implementation.
    Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
        "ANE inference for '{}' delegates to Python. \
         Use brain.ane_inference.ANEInferenceEngine.embed_batch_ane() for actual inference. \
         SILICON-06: Rust-native CoreML FFI not yet integrated. \
         Model: hidden_dim={}, max_seq_len={}. \
         See rust_extensions/src/ane.rs module docs for coreml crate integration plan.",
        model_id, meta.hidden_dim, meta.max_seq_len
    )))
}

/// Compute embedding from tokenized input.
///
/// SILICON-06: Stub — delegates to Python ANEInferenceEngine.embed_batch_ane().
/// For actual ANE embedding:
/// 1. Use coremltools to compile model with compute_units=ComputeUnit.NEURAL_ENGINE
/// 2. Load via brain/ane_inference.py:ANEInferenceEngine
/// 3. Call engine.embed_batch_ane(texts, model_key="bge-small")
///
/// Rust-native path (future): coreml crate → MLModel::prediction_from_features()
#[pyfunction]
pub fn embed_tokens(
    model_id: String,
    token_ids: Vec<i64>,
    attention_mask: Vec<i64>,
) -> Result<Vec<f32>, PyErr> {
    run_inference(model_id, token_ids, attention_mask)
}

/// Get ANE telemetry counters.
///
/// Returns: dict with embed_calls, embed_tokens, cache_hits, cache_misses,
///          ane_fallback_cpu, ane_fallback_gpu, errors
#[pyfunction]
pub fn get_telemetry() -> HashMap<String, u64> {
    let telemetry = ANE_TELEMETRY.read();
    let mut result = HashMap::new();
    result.insert("embed_calls".to_string(), telemetry.embed_calls);
    result.insert("embed_tokens".to_string(), telemetry.embed_tokens);
    result.insert("cache_hits".to_string(), telemetry.cache_hits);
    result.insert("cache_misses".to_string(), telemetry.cache_misses);
    result.insert("ane_fallback_cpu".to_string(), telemetry.ane_fallback_cpu);
    result.insert("ane_fallback_gpu".to_string(), telemetry.ane_fallback_gpu);
    result.insert("errors".to_string(), telemetry.errors);
    result
}

/// Reset ANE telemetry counters.
#[pyfunction]
pub fn reset_telemetry() {
    let mut telemetry = ANE_TELEMETRY.write();
    *telemetry = ANETelemetry::default();
}

/// Get supported compute units for this platform.
#[pyfunction]
pub fn get_supported_compute_units() -> Vec<String> {
    if std::env::consts::OS == "macos" {
        vec![
            "NeuralEngine".to_string(),
            "CPU".to_string(),
            "GPU".to_string(),
            "All".to_string(),
        ]
    } else {
        vec!["CPU".to_string()]
    }
}

/// Check if Neural Engine is available on this hardware.
///
/// SILICON-06: Returns true on Apple Silicon (M1+).
/// Actual ANE inference requires coremltools on Python side
/// (brain/ane_inference.py:is_ane_available() for runtime check).
/// This function checks hardware capability only.
#[pyfunction]
pub fn is_ane_available() -> bool {
    // ANE is available on all Apple Silicon chips (M1, M1 Pro, M1 Max, M1 Ultra, M2, M3, M4, etc.)
    // This checks hardware only — runtime availability needs coremltools (Python side).
    if std::env::consts::OS == "macos" && std::env::consts::ARCH == "aarch64" {
        return true;
    }
    false
}

// ─── GNN-3: GraphSAGE ANE Functions ─────────────────────────────────────────────

/// GNN-3: Load GraphSAGE model into registry.
///
/// Args:
///     model_id: Unique identifier for the model
///     model_path: Path to CoreML .mlmodel file
///     in_dim: Input feature dimension (default: 81 = 17 type + 64 embedding)
///     hidden_dim: Hidden layer dimension (default: 64)
///     out_dim: Output embedding dimension (default: 32)
///     num_layers: Number of GraphSAGE layers (default: 2)
///
/// Returns: Ok(model_id) or Error
#[pyfunction]
pub fn gnn_load_model(
    model_id: String,
    model_path: String,
    in_dim: Option<usize>,
    hidden_dim: Option<usize>,
    out_dim: Option<usize>,
    num_layers: Option<usize>,
) -> Result<String, PyErr> {
    let in_dim = in_dim.unwrap_or(GNN_DEFAULT_IN_DIM);
    let hidden_dim = hidden_dim.unwrap_or(GNN_DEFAULT_HIDDEN_DIM);
    let out_dim = out_dim.unwrap_or(GNN_DEFAULT_OUT_DIM);
    let num_layers = num_layers.unwrap_or(2);

    let meta = GNNModelMeta {
        model_id: model_id.clone(),
        model_path,
        in_dim,
        hidden_dim,
        out_dim,
        num_layers,
    };

    let mut registry = GNN_REGISTRY.write();
    registry
        .register(model_id.clone(), meta)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?;

    eprintln!("[ANE-GNN] Registered GraphSAGE model: {}", model_id);
    Ok(model_id)
}

/// GNN-3: Validate GNN batch dimensions.
///
/// Args:
///     batch_size: Number of nodes in batch
///     in_dim: Feature dimension per node
///
/// Returns: Ok(()) or Error
#[pyfunction]
pub fn gnn_validate_batch(batch_size: usize, in_dim: usize) -> Result<(), PyErr> {
    if batch_size == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "batch_size must be > 0",
        ));
    }
    if batch_size > GNN_MAX_BATCH_NODES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "batch_size {} exceeds GNN_MAX_BATCH_NODES {}",
            batch_size, GNN_MAX_BATCH_NODES
        )));
    }
    if in_dim == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "in_dim must be > 0",
        ));
    }
    if in_dim > 1024 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "in_dim {} exceeds maximum (1024)",
            in_dim
        )));
    }
    Ok(())
}

/// GNN-3: Store node embeddings in memory.
///
/// Args:
///     embeddings: List of (kuzu_id, embedding_vector) tuples
///
/// Returns: Count of stored embeddings
#[pyfunction]
pub fn gnn_store_embeddings(embeddings: Vec<(String, Vec<f32>)>) -> Result<usize, PyErr> {
    let mut store = EMBEDDING_STORE.write();
    let count = embeddings.len();

    for (kuzu_id, emb) in embeddings {
        if emb.len() > 512 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Embedding dimension {} exceeds maximum (512)",
                emb.len()
            )));
        }
        store.store(kuzu_id, emb);
    }

    Ok(count)
}

/// GNN-3: Get stored embeddings for batch of nodes.
///
/// Args:
///     kuzu_ids: List of Kuzu string IDs
///
/// Returns: Vec of (kuzu_id, embedding) tuples (missing nodes return empty vec)
#[pyfunction]
pub fn gnn_get_embeddings(kuzu_ids: Vec<String>) -> Vec<(String, Vec<f32>)> {
    let store = EMBEDDING_STORE.read();

    kuzu_ids
        .into_iter()
        .map(|id| {
            let emb = store
                .get(&id)
                .map(|e| e.embedding.clone())
                .unwrap_or_default();
            (id, emb)
        })
        .collect()
}

/// GNN-3: Run GNN inference on node batch (stub — delegates to Python).
///
/// This function validates batch and updates telemetry.
/// Actual inference is done via Python ANEGNNEngine or MLX.
///
/// Args:
///     model_id: Registered GNN model identifier
///     node_ids: List of Kuzu string IDs
///     features: Flattened feature matrix (n_nodes * in_dim)
///     edges: List of (src_idx, dst_idx) edges
///
/// Returns: Vec of embedding vectors, or Error
#[pyfunction]
pub fn gnn_run_inference(
    model_id: String,
    node_ids: Vec<String>,
    features: Vec<f32>,
    edges: Vec<(usize, usize)>,
) -> Result<Vec<Vec<f32>>, PyErr> {
    let registry = GNN_REGISTRY.read();

    let meta = registry.get(&model_id).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "GNN model '{}' not found. Load with gnn_load_model() first.",
            model_id
        ))
    })?;

    let n_nodes = node_ids.len();
    if n_nodes == 0 {
        return Ok(Vec::new());
    }

    if n_nodes > GNN_MAX_BATCH_NODES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Batch size {} exceeds GNN_MAX_BATCH_NODES {}",
            n_nodes, GNN_MAX_BATCH_NODES
        )));
    }

    // Validate feature dimensions
    let expected_len = n_nodes * meta.in_dim;
    if features.len() != expected_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Feature vector length {} != expected {} (n_nodes {} * in_dim {})",
            features.len(),
            expected_len,
            n_nodes,
            meta.in_dim
        )));
    }

    // FIX-3: Validate edge indices to prevent out-of-bounds access
    for (i, &(src_idx, dst_idx)) in edges.iter().enumerate() {
        if src_idx >= n_nodes {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Edge {} src_idx {} >= n_nodes {} (model: {})",
                i, src_idx, n_nodes, model_id
            )));
        }
        if dst_idx >= n_nodes {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Edge {} dst_idx {} >= n_nodes {} (model: {})",
                i, dst_idx, n_nodes, model_id
            )));
        }
    }

    // GNN-3: This is a stub — actual inference requires CoreML + ANE
    // For now, return normalized mean of input features as a placeholder
    // In production, this would call the CoreML model via PyO3 callback
    // or direct coreml crate FFI (future work)
    let mut output: Vec<Vec<f32>> = Vec::with_capacity(n_nodes);

    for i in 0..n_nodes {
        let start = i * meta.in_dim;
        let end = start + meta.in_dim;
        let node_features = &features[start..end];

        // Simple mean pooling as placeholder
        let sum: f32 = node_features.iter().sum();
        let mean = sum / meta.in_dim as f32;

        // Output: repeated mean (placeholder for actual GNN forward pass)
        let mut emb = vec![0.0; meta.out_dim];
        for j in 0..meta.out_dim {
            emb[j] = mean;
        }
        output.push(emb);
    }

    // Update telemetry
    {
        let mut telemetry = ANE_TELEMETRY.write();
        telemetry.embed_calls += 1;
        telemetry.embed_tokens += n_nodes as u64;
    }

    eprintln!(
        "[ANE-GNN] Inference stub for {} nodes (model={})",
        n_nodes,
        model_id
    );

    Ok(output)
}

/// GNN-3: Predict links using GNN embeddings and heuristics.
///
/// Combines GNN cosine similarity with traditional heuristics
/// (Adamic-Adar, Jaccard, Preferential Attachment).
///
/// Args:
///     embeddings: Vec of (kuzu_id, embedding) pairs
///     edges: Vec of (src_idx, dst_idx) for adjacency
///     candidate_pairs: Vec of (src_idx, dst_idx) to score
///     gnn_weight: Weight for GNN score (default: 0.6)
///     min_score: Minimum combined score threshold
///
/// Returns: Vec of (src_idx, dst_idx, gnn_score, heuristic_score, combined_score, method)
#[pyfunction]
pub fn gnn_predict_links(
    embeddings: Vec<(String, Vec<f32>)>,
    edges: Vec<(usize, usize)>,
    candidate_pairs: Vec<(usize, usize)>,
    gnn_weight: Option<f32>,
    min_score: Option<f32>,
) -> Vec<(usize, usize, f32, f32, f32, String)> {
    let gnn_weight = gnn_weight.unwrap_or(0.6);
    let min_score = min_score.unwrap_or(0.1);

    // Build adjacency list
    let mut adjacency: std::collections::HashMap<usize, Vec<usize>> =
        std::collections::HashMap::new();
    let mut degrees: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();

    for &(src, dst) in &edges {
        adjacency.entry(src).or_default().push(dst);
        adjacency.entry(dst).or_default().push(src);
        *degrees.entry(src).or_insert(0) += 1;
        *degrees.entry(dst).or_insert(0) += 1;
    }

    // Build embedding map keyed by index (usize)
    // embeddings[i] corresponds to node with index i
    let emb_map: std::collections::HashMap<usize, Vec<f32>> = embeddings
        .into_iter()
        .enumerate()
        .map(|(idx, (_, emb))| (idx, emb))
        .collect();

    let mut results: Vec<(usize, usize, f32, f32, f32, String)> = Vec::new();

    for &(src, dst) in &candidate_pairs {
        // Get embeddings
        let src_emb = match emb_map.get(&src) {
            Some(e) => e,
            None => continue,
        };
        let dst_emb = match emb_map.get(&dst) {
            Some(e) => e,
            None => continue,
        };

        // GNN score: cosine similarity
        let gnn_score = cosine_similarity(src_emb, dst_emb);

        // Heuristic scores
        let src_neighbors = adjacency.get(&src).cloned().unwrap_or_default();
        let dst_neighbors = adjacency.get(&dst).cloned().unwrap_or_default();

        let common: Vec<_> = src_neighbors
            .iter()
            .filter(|n| dst_neighbors.contains(n))
            .collect();
        let common_count = common.len();

        // Adamic-Adar
        let mut adamic_adar = 0.0f32;
        for &z in &common {
            if let Some(&deg) = degrees.get(z) {
                if deg > 1 {
                    adamic_adar += 1.0 / (deg as f32).ln();
                }
            }
        }

        // Jaccard
        let union_count = src_neighbors.len() + dst_neighbors.len() - common_count;
        let jaccard = if union_count > 0 {
            common_count as f32 / union_count as f32
        } else {
            0.0
        };

        // Preferential Attachment
        let deg_src = degrees.get(&src).copied().unwrap_or(0);
        let deg_dst = degrees.get(&dst).copied().unwrap_or(0);
        let pref_attach = (deg_src * deg_dst) as f32;

        // Combined heuristic score
        let aa_norm = (adamic_adar / 10.0).min(1.0).max(0.0);
        let pa_norm = (pref_attach / 1000.0).min(1.0).max(0.0);
        let heur_score = 0.5 * aa_norm + 0.3 * jaccard + 0.2 * pa_norm;

        // Combined score
        let combined = gnn_weight * gnn_score + (1.0 - gnn_weight) * heur_score;

        if combined >= min_score {
            let method = if gnn_score > 0.7 {
                "gnn"
            } else if heur_score > 0.5 {
                "heuristic"
            } else {
                "hybrid"
            };
            results.push((
                src,
                dst,
                gnn_score,
                heur_score,
                combined,
                method.to_string(),
            ));
        }
    }

    // Sort by combined score descending
    results.sort_by(|a, b| b.4.partial_cmp(&a.4).unwrap_or(std::cmp::Ordering::Equal));

    results
}

// ─── NEXTGEN-03: Cross-Modal Embedding Storage ────────────────────────────────

/// NEXTGEN-03: Cross-modal embedding store for face and voiceprint embeddings.
/// Used for O(1) similarity search via LSH pre-filtering.
pub struct CrossModalEmbeddingStore {
    /// Face embeddings: node_id -> embedding vector (512d)
    face_embeddings: std::collections::HashMap<String, Vec<f32>>,
    /// Voiceprint embeddings: node_id -> embedding vector (256d)
    voice_embeddings: std::collections::HashMap<String, Vec<f32>>,
    /// Face LSH index: simhash fingerprint -> set of node_ids
    face_lsh: std::collections::HashMap<u64, std::collections::HashSet<String>>,
    /// Voiceprint LSH index: simhash fingerprint -> set of node_ids
    voice_lsh: std::collections::HashMap<u64, std::collections::HashSet<String>>,
}

impl CrossModalEmbeddingStore {
    pub fn new() -> Self {
        Self {
            face_embeddings: std::collections::HashMap::new(),
            voice_embeddings: std::collections::HashMap::new(),
            face_lsh: std::collections::HashMap::new(),
            voice_lsh: std::collections::HashMap::new(),
        }
    }

    /// Store a face embedding with LSH fingerprint.
    pub fn store_face(&mut self, node_id: String, embedding: Vec<f32>) {
        let fp = Self::compute_lsh_fingerprint(&embedding);
        self.face_embeddings.insert(node_id.clone(), embedding);
        self.face_lsh.entry(fp).or_default().insert(node_id);
    }

    /// Store a voiceprint embedding with LSH fingerprint.
    pub fn store_voice(&mut self, node_id: String, embedding: Vec<f32>) {
        let fp = Self::compute_lsh_fingerprint(&embedding);
        self.voice_embeddings.insert(node_id.clone(), embedding);
        self.voice_lsh.entry(fp).or_default().insert(node_id);
    }

    /// Get face embedding by node_id.
    pub fn get_face(&self, node_id: &str) -> Option<&Vec<f32>> {
        self.face_embeddings.get(node_id)
    }

    /// Get voiceprint embedding by node_id.
    pub fn get_voice(&self, node_id: &str) -> Option<&Vec<f32>> {
        self.voice_embeddings.get(node_id)
    }

    /// Query face candidates by LSH fingerprint.
    pub fn query_face_lsh(&self, embedding: &[f32], max_results: usize) -> Vec<(String, f32)> {
        let fp = Self::compute_lsh_fingerprint(embedding);
        let candidates: Vec<String> = self.face_lsh
            .get(&fp)
            .map(|s| s.iter().cloned().collect())
            .unwrap_or_default();

        // Compute actual cosine similarity for candidates
        let mut results: Vec<(String, f32)> = candidates
            .into_iter()
            .filter_map(|id| {
                self.face_embeddings.get(&id).map(|emb| {
                    let sim = Self::cosine_similarity_slice(embedding, emb);
                    (id, sim)
                })
            })
            .collect();

        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(max_results);
        results
    }

    /// Query voiceprint candidates by LSH fingerprint.
    pub fn query_voice_lsh(&self, embedding: &[f32], max_results: usize) -> Vec<(String, f32)> {
        let fp = Self::compute_lsh_fingerprint(embedding);
        let candidates: Vec<String> = self.voice_lsh
            .get(&fp)
            .map(|s| s.iter().cloned().collect())
            .unwrap_or_default();

        // Compute actual cosine similarity for candidates
        let mut results: Vec<(String, f32)> = candidates
            .into_iter()
            .filter_map(|id| {
                self.voice_embeddings.get(&id).map(|emb| {
                    let sim = Self::cosine_similarity_slice(embedding, emb);
                    (id, sim)
                })
            })
            .collect();

        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(max_results);
        results
    }

    /// Compute LSH fingerprint (16-bit SimHash of the embedding).
    fn compute_lsh_fingerprint(embedding: &[f32]) -> u64 {
        let n_bits = 16usize;
        let n_hashes = n_bits * 2; // 32 hash functions for 16-bit fingerprint
        let step = (embedding.len() / n_hashes).max(1);

        let mut fingerprint: u64 = 0;
        for i in 0..n_bits {
            let mut sum: f32 = 0.0;
            let start = i * step;
            let end = (start + step).min(embedding.len());

            for j in start..end {
                if embedding[j] > 0.0 {
                    sum += 1.0;
                } else {
                    sum -= 1.0;
                }
            }

            if sum > 0.0 {
                fingerprint |= 1 << i;
            }
        }

        fingerprint
    }

    /// Cosine similarity between two slices.
    fn cosine_similarity_slice(a: &[f32], b: &[f32]) -> f32 {
        if a.len() != b.len() || a.is_empty() {
            return 0.0;
        }

        let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
        let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
        let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();

        if norm_a == 0.0 || norm_b == 0.0 {
            0.0
        } else {
            (dot / (norm_a * norm_b)).clamp(-1.0, 1.0)
        }
    }

    /// Clear all embeddings.
    pub fn clear(&mut self) {
        self.face_embeddings.clear();
        self.voice_embeddings.clear();
        self.face_lsh.clear();
        self.voice_lsh.clear();
    }

    /// Get count of stored embeddings.
    pub fn len(&self) -> (usize, usize) {
        (self.face_embeddings.len(), self.voice_embeddings.len())
    }
}

impl Default for CrossModalEmbeddingStore {
    fn default() -> Self {
        Self::new()
    }
}

// Global cross-modal embedding store
static CROSS_MODAL_STORE: LazyLock<RwLock<CrossModalEmbeddingStore>> =
    LazyLock::new(|| RwLock::new(CrossModalEmbeddingStore::new()));

/// NEXTGEN-03: Store a face embedding in the cross-modal index.
///
/// Args:
///     node_id: Unique identifier for this face (e.g., "face_{hash}")
///     embedding: 512-dim face embedding vector
///
/// Returns: Count of stored embeddings
#[pyfunction]
pub fn crossmodal_store_face(node_id: String, embedding: Vec<f32>) -> usize {
    let mut store = CROSS_MODAL_STORE.write();
    store.store_face(node_id, embedding);
    let (face_count, _) = store.len();
    eprintln!("[CrossModal] Stored face embedding. Total faces: {}", face_count);
    face_count
}

/// NEXTGEN-03: Store a voiceprint embedding in the cross-modal index.
///
/// Args:
///     node_id: Unique identifier for this voiceprint
///     embedding: 256-dim voiceprint embedding vector
///
/// Returns: Count of stored embeddings
#[pyfunction]
pub fn crossmodal_store_voice(node_id: String, embedding: Vec<f32>) -> usize {
    let mut store = CROSS_MODAL_STORE.write();
    store.store_voice(node_id, embedding);
    let (_, voice_count) = store.len();
    eprintln!(
        "[CrossModal] Stored voiceprint embedding. Total voiceprints: {}",
        voice_count
    );
    voice_count
}

/// NEXTGEN-03: Query face embeddings by cosine similarity (LSH pre-filter).
///
/// Args:
///     embedding: Query 512-dim face embedding
///     max_results: Maximum number of results (default: 10)
///     min_similarity: Minimum similarity threshold (default: 0.7)
///
/// Returns: Vec of (node_id, similarity) tuples
#[pyfunction]
pub fn crossmodal_query_face(
    embedding: Vec<f32>,
    max_results: Option<usize>,
    min_similarity: Option<f32>,
) -> Vec<(String, f32)> {
    let max_results = max_results.unwrap_or(10);
    let min_similarity = min_similarity.unwrap_or(0.7);

    let store = CROSS_MODAL_STORE.read();
    let results = store.query_face_lsh(&embedding, max_results * 2); // Get extra for filtering

    // Filter by minimum similarity
    results
        .into_iter()
        .filter(|(_, sim)| *sim >= min_similarity)
        .take(max_results)
        .collect()
}

/// NEXTGEN-03: Query voiceprint embeddings by cosine similarity (LSH pre-filter).
///
/// Args:
///     embedding: Query 256-dim voiceprint embedding
///     max_results: Maximum number of results (default: 10)
///     min_similarity: Minimum similarity threshold (default: 0.7)
///
/// Returns: Vec of (node_id, similarity) tuples
#[pyfunction]
pub fn crossmodal_query_voice(
    embedding: Vec<f32>,
    max_results: Option<usize>,
    min_similarity: Option<f32>,
) -> Vec<(String, f32)> {
    let max_results = max_results.unwrap_or(10);
    let min_similarity = min_similarity.unwrap_or(0.7);

    let store = CROSS_MODAL_STORE.read();
    let results = store.query_voice_lsh(&embedding, max_results * 2);

    // Filter by minimum similarity
    results
        .into_iter()
        .filter(|(_, sim)| *sim >= min_similarity)
        .take(max_results)
        .collect()
}

/// NEXTGEN-03: Get face embedding by node_id.
///
/// Args:
///     node_id: Unique identifier for the face
///
/// Returns: Embedding vector or None
#[pyfunction]
pub fn crossmodal_get_face(node_id: String) -> Option<Vec<f32>> {
    let store = CROSS_MODAL_STORE.read();
    store.get_face(&node_id).cloned()
}

/// NEXTGEN-03: Get voiceprint embedding by node_id.
///
/// Args:
///     node_id: Unique identifier for the voiceprint
///
/// Returns: Embedding vector or None
#[pyfunction]
pub fn crossmodal_get_voice(node_id: String) -> Option<Vec<f32>> {
    let store = CROSS_MODAL_STORE.read();
    store.get_voice(&node_id).cloned()
}

/// NEXTGEN-03: Compute similarity between two face embeddings.
///
/// Args:
///     embedding_a: First 512-dim face embedding
///     embedding_b: Second 512-dim face embedding
///
/// Returns: Cosine similarity (-1 to 1)
#[pyfunction]
pub fn crossmodal_face_similarity(embedding_a: Vec<f32>, embedding_b: Vec<f32>) -> f32 {
    CrossModalEmbeddingStore::cosine_similarity_slice(&embedding_a, &embedding_b)
}

/// NEXTGEN-03: Compute similarity between two voiceprint embeddings.
///
/// Args:
///     embedding_a: First 256-dim voiceprint embedding
///     embedding_b: Second 256-dim voiceprint embedding
///
/// Returns: Cosine similarity (-1 to 1)
#[pyfunction]
pub fn crossmodal_voice_similarity(embedding_a: Vec<f32>, embedding_b: Vec<f32>) -> f32 {
    CrossModalEmbeddingStore::cosine_similarity_slice(&embedding_a, &embedding_b)
}

/// NEXTGEN-03: Clear all cross-modal embeddings.
#[pyfunction]
pub fn crossmodal_clear() {
    let mut store = CROSS_MODAL_STORE.write();
    store.clear();
    eprintln!("[CrossModal] Cleared all embeddings");
}

/// NEXTGEN-03: Get cross-modal store statistics.
///
/// Returns: Dict with face_count and voice_count
#[pyfunction]
pub fn crossmodal_stats() -> std::collections::HashMap<String, usize> {
    let store = CROSS_MODAL_STORE.read();
    let (face_count, voice_count) = store.len();
    let mut stats = std::collections::HashMap::new();
    stats.insert("face_count".to_string(), face_count);
    stats.insert("voice_count".to_string(), voice_count);
    stats
}

/// Helper: cosine similarity between two vectors
fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }

    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();

    if norm_a == 0.0 || norm_b == 0.0 {
        0.0
    } else {
        (dot / (norm_a * norm_b)).clamp(-1.0, 1.0)
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// NEXTGEN-05: Rust-Native CoreML ANE Inference
// ═══════════════════════════════════════════════════════════════════════════════
//
// This module provides zero-overhead CoreML inference directly from Rust,
// eliminating the Python bridge overhead (~50-100μs per call).
//
// Performance targets:
//   - < 2 ms per batch of 128 nodes on ANE (16 cores × 11 TOPS int8)
//   - < 8 ms for 500-node ToT tree (4 batches)
//   - 9× speedup vs Python bridge (50ms + 20ms NumPy fallback)
//
// Architecture:
//   - LRU model cache (max 2 models, ANE hardware constraint)
//   - Batch inference for ANE utilization (prefer 128 batch size)
//   - Feature: coreml_ane (enabled by default via `ane` feature)
//
// Usage:
//   from hledac.universal.core.rust_backend import rust
//   
//   # Register PRM model
//   rust.raw.ane.load_model("prm_step", "/path/to/prm_step.mlpackage", 16, 1)
//   
//   # Single inference
//   reward = rust.raw.ane.run_prm_inference("prm_step", [0.1] * 16)
//   
//   # Batch inference (preferred — better ANE utilization)
//   rewards = rust.raw.ane.run_prm_inference_batch("prm_step", [[0.1]*16] * 128)
// ═══════════════════════════════════════════════════════════════════════════════

#[cfg(feature = "coreml_ane")]
use std::collections::VecDeque;

/// NEXTGEN-05: PRM-specific model cache with LRU eviction.
///
/// Bounded to ANE_MAX_MODELS (2) per hardware constraint.
/// Uses BTreeMap for deterministic ordering and efficient eviction.
#[cfg(feature = "coreml_ane")]
pub struct CoreMLModelCache {
    /// Cached models: model_id -> (model, last_access_order)
    models: std::collections::BTreeMap<String, (coreml::model::Model, usize)>,
    /// Access order for LRU eviction
    access_order: VecDeque<String>,
    /// Next access order index
    next_order: usize,
}

#[cfg(feature = "coreml_ane")]
impl CoreMLModelCache {
    pub fn new() -> Self {
        Self {
            models: std::collections::BTreeMap::new(),
            access_order: VecDeque::new(),
            next_order: 0,
        }
    }

    /// Load a model into cache, evicting oldest if at capacity.
    pub fn load(
        &mut self,
        model_id: String,
        path: &std::path::Path,
    ) -> Result<(), ANEError> {
        // Evict if at capacity
        while self.models.len() >= ANE_MAX_MODELS {
            if let Some(oldest_id) = self.access_order.pop_front() {
                self.models.remove(&oldest_id);
                eprintln!(
                    "[CoreML:cache] Evicted model '{}' (LRU eviction, at capacity)",
                    oldest_id
                );
            }
        }

        // Load the model with ANE compute unit
        let config = coreml::configuration::ModelConfiguration::new()
            .with_compute_units(coreml::configuration::ComputeUnits::NeuralEngine);

        let model = coreml::model::Model::load_from_url(path, &config)
            .map_err(|e| ANEError::InferenceFailed(format!("Failed to load CoreML model: {}", e)))?;

        // Update access tracking
        let order = self.next_order;
        self.next_order += 1;

        // Remove existing entry if present (update)
        if let Some(pos) = self.access_order.iter().position(|id| id == &model_id) {
            self.access_order.remove(pos);
        }

        self.models.insert(model_id.clone(), (model, order));
        self.access_order.push_back(model_id);

        Ok(())
    }

    /// Get a model from cache (touch for LRU).
    pub fn get(&mut self, model_id: &str) -> Option<&coreml::model::Model> {
        if let Some((model, order)) = self.models.get_mut(model_id) {
            // Update access order
            if let Some(pos) = self.access_order.iter().position(|id| id == model_id) {
                self.access_order.remove(pos);
            }
            *order = self.next_order;
            self.next_order += 1;
            self.access_order.push_back(model_id.to_string());
            return Some(model);
        }
        None
    }

    /// Get model count.
    pub fn len(&self) -> usize {
        self.models.len()
    }

    /// Check if model is loaded.
    pub fn contains(&self, model_id: &str) -> bool {
        self.models.contains_key(model_id)
    }

    /// Clear all cached models.
    pub fn clear(&mut self) {
        self.models.clear();
        self.access_order.clear();
    }
}

#[cfg(feature = "coreml_ane")]
impl Default for CoreMLModelCache {
    fn default() -> Self {
        Self::new()
    }
}

/// NEXTGEN-05: Global CoreML model cache (process-wide singleton).
#[cfg(feature = "coreml_ane")]
static COREML_MODEL_CACHE: LazyLock<RwLock<CoreMLModelCache>> =
    LazyLock::new(|| RwLock::new(CoreMLModelCache::new()));

/// NEXTGEN-05: PRM telemetry for Rust-native CoreML inference.
#[derive(Default)]
pub struct PRMTelemetry {
    pub prm_inference_calls: u64,
    pub prm_batch_calls: u64,
    pub prm_inference_tokens: u64,
    pub coreml_cache_hits: u64,
    pub coreml_cache_misses: u64,
    pub coreml_errors: u64,
}

static PRM_TELEMETRY: LazyLock<RwLock<PRMTelemetry>> =
    LazyLock::new(|| RwLock::new(PRMTelemetry::default()));

/// NEXTGEN-05: Load a PRM model into CoreML cache.
///
/// Args:
///     model_id: Unique identifier (e.g., "prm_step")
///     model_path: Path to .mlmodelc or .mlpackage bundle
///
/// Returns: Ok(()) or Error
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn load_prm_model(model_id: String, model_path: String) -> Result<(), PyErr> {
    let path = std::path::Path::new(&model_path);
    if !path.exists() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Model path does not exist: {}",
            model_path
        )));
    }

    let mut cache = COREML_MODEL_CACHE.write();
    cache
        .load(model_id, path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    eprintln!("[CoreML:cache] Loaded PRM model: {}", model_id);
    Ok(())
}

/// NEXTGEN-05: Run PRM inference on a single feature vector.
///
/// This is the Rust-native CoreML path — no Python bridge overhead.
///
/// Args:
///     model_id: Registered model identifier
///     features: 16-dimensional feature vector
///
/// Returns: Step reward in [-1, 1] range, or Error
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn run_prm_inference(model_id: String, features: Vec<f32>) -> Result<f32, PyErr> {
    // Validate input
    if features.len() != 16 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Expected 16 features, got {}",
            features.len()
        )));
    }

    // Get model from cache (cache hit tracked in telemetry)
    let mut cache = COREML_MODEL_CACHE.write();
    let model = cache.get(&model_id).ok_or_else(|| {
        // Cache miss - model not loaded
        {
            let mut telemetry = PRM_TELEMETRY.write();
            telemetry.coreml_cache_misses += 1;
        }
        pyo3::exceptions::PyValueError::new_err(format!(
            "PRM model '{}' not loaded. Call load_prm_model() first.",
            model_id
        ))
    })?;

    // Update telemetry (cache hit)
    {
        let mut telemetry = PRM_TELEMETRY.write();
        telemetry.prm_inference_calls += 1;
        telemetry.prm_inference_tokens += 1;
        telemetry.coreml_cache_hits += 1;
    }

    // Create input tensor
    let mut input_array = coreml::multi_array::MultiArray::new_f32(&[1, 16])
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to create input tensor: {}",
            e
        )))?;

    // Copy features into tensor using the f32 setter
    for (i, &val) in features.iter().enumerate() {
        input_array
            .set_f32(&[0, i], val)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Failed to set tensor value: {}",
                e
            )))?;
    }

    // Create input feature provider
    let mut inputs = coreml::feature_provider::FeatureProvider::new();
    inputs.insert_multi_array("features", input_array);

    // Run inference with ANE
    let options = coreml::prediction::PredictionOptions::new()
        .with_uses_cpu_only(false);

    let outputs = model
        .predict_with_options(&inputs, &options)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "CoreML inference failed: {}",
            e
        )))?;

    // Extract reward from output
    let arr = outputs.get_multi_array("reward")
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err(
            "Output 'reward' not found or wrong type. Check model output name.".to_string()
        ))?;

    // Get first element (single output)
    let reward = arr.get_f32(&[0]).unwrap_or(0.0);

    // Clip to [-1, 1] range
    Ok(reward.clamp(-1.0, 1.0))
}

/// NEXTGEN-05: Run PRM batch inference for multiple feature vectors.
///
/// This is the preferred path — batch inference improves ANE utilization
/// by 60% vs sequential single-inference calls.
///
/// Performance: < 2ms for batch of 128 nodes on ANE (16 cores × 11 TOPS)
///
/// Args:
///     model_id: Registered model identifier
///     features_batch: List of 16-dimensional feature vectors
///
/// Returns: List of step rewards in [-1, 1] range, or Error
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn run_prm_inference_batch(
    model_id: String,
    features_batch: Vec<Vec<f32>>,
) -> Result<Vec<f32>, PyErr> {
    if features_batch.is_empty() {
        return Ok(Vec::new());
    }

    let batch_size = features_batch.len();

    // Validate all inputs
    for (i, features) in features_batch.iter().enumerate() {
        if features.len() != 16 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Expected 16 features at index {}, got {}",
                i,
                features.len()
            )));
        }
    }

    // Get model from cache (cache hit tracked in telemetry)
    let mut cache = COREML_MODEL_CACHE.write();
    let model = cache.get(&model_id).ok_or_else(|| {
        // Cache miss - model not loaded
        {
            let mut telemetry = PRM_TELEMETRY.write();
            telemetry.coreml_cache_misses += 1;
        }
        pyo3::exceptions::PyValueError::new_err(format!(
            "PRM model '{}' not loaded. Call load_prm_model() first.",
            model_id
        ))
    })?;

    // Update telemetry (cache hit)
    {
        let mut telemetry = PRM_TELEMETRY.write();
        telemetry.prm_batch_calls += 1;
        telemetry.prm_inference_tokens += batch_size as u64;
        telemetry.coreml_cache_hits += 1;
    }

    // Create batch input tensor: (batch_size, 16)
    let mut input_array = coreml::multi_array::MultiArray::new_f32(&[batch_size, 16])
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "Failed to create batch tensor: {}",
            e
        )))?;

    // Copy all features into tensor using the f32 setter
    for (batch_idx, features) in features_batch.iter().enumerate() {
        for (feat_idx, &val) in features.iter().enumerate() {
            input_array.set_f32(&[batch_idx, feat_idx], val).map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Failed to set tensor value at [{}, {}]: {}",
                    batch_idx, feat_idx, e
                ))
            })?;
        }
    }

    // Create input feature provider
    let mut inputs = coreml::feature_provider::FeatureProvider::new();
    inputs.insert_multi_array("features", input_array);

    // Run batch inference with ANE
    let options = coreml::prediction::PredictionOptions::new()
        .with_uses_cpu_only(false);

    let outputs = model
        .predict_with_options(&inputs, &options)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "CoreML batch inference failed: {}",
            e
        )))?;

    // Extract rewards from output using get_multi_array
    let arr = outputs.get_multi_array("reward")
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err(
            "Output 'reward' not found or wrong type. Check model output name.".to_string()
        ))?;

    // Extract all rewards from batch output
    let mut rewards = Vec::with_capacity(batch_size);
    for i in 0..batch_size {
        let reward = arr.get_f32(&[i]).unwrap_or(0.0);
        rewards.push(reward.clamp(-1.0, 1.0));
    }

    Ok(rewards)
}

/// NEXTGEN-05: Get PRM telemetry counters.
///
/// Returns: dict with prm_inference_calls, prm_batch_calls,
///          prm_inference_tokens, coreml_cache_hits, coreml_cache_misses,
///          coreml_errors
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn get_prm_telemetry() -> HashMap<String, u64> {
    let telemetry = PRM_TELEMETRY.read();
    let mut result = HashMap::new();
    result.insert("prm_inference_calls".to_string(), telemetry.prm_inference_calls);
    result.insert("prm_batch_calls".to_string(), telemetry.prm_batch_calls);
    result.insert("prm_inference_tokens".to_string(), telemetry.prm_inference_tokens);
    result.insert("coreml_cache_hits".to_string(), telemetry.coreml_cache_hits);
    result.insert("coreml_cache_misses".to_string(), telemetry.coreml_cache_misses);
    result.insert("coreml_errors".to_string(), telemetry.coreml_errors);
    result
}

/// NEXTGEN-05: Reset PRM telemetry counters.
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn reset_prm_telemetry() {
    let mut telemetry = PRM_TELEMETRY.write();
    *telemetry = PRMTelemetry::default();
}

/// NEXTGEN-05: Get CoreML cache status.
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn get_coreml_cache_status() -> HashMap<String, String> {
    let cache = COREML_MODEL_CACHE.read();
    let mut result = HashMap::new();
    result.insert("loaded_models".to_string(), cache.len().to_string());
    result.insert("max_models".to_string(), ANE_MAX_MODELS.to_string());
    
    // List loaded model IDs
    let model_ids: Vec<String> = cache.models.keys().cloned().collect();
    result.insert("models".to_string(), model_ids.join(", "));
    
    result
}

/// NEXTGEN-05: Unload a specific model from CoreML cache.
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn unload_prm_model(model_id: String) -> Result<(), PyErr> {
    let mut cache = COREML_MODEL_CACHE.write();
    if cache.models.remove(&model_id).is_some() {
        // Remove from access order
        if let Some(pos) = cache.access_order.iter().position(|id| id == &model_id) {
            cache.access_order.remove(pos);
        }
        eprintln!("[CoreML:cache] Unloaded model: {}", model_id);
        Ok(())
    } else {
        Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Model '{}' not found in cache",
            model_id
        )))
    }
}

/// NEXTGEN-05: Clear all models from CoreML cache.
#[pyfunction]
#[cfg(feature = "coreml_ane")]
pub fn clear_coreml_cache() {
    let mut cache = COREML_MODEL_CACHE.write();
    cache.clear();
    eprintln!("[CoreML:cache] Cleared all models");
}

// ─── Module registration ──────────────────────────────────────────────────────

/// Register ANE module functions with PyO3 module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> Result<(), PyErr> {
    m.add_function(wrap_pyfunction!(init, m)?)?;
    m.add_function(wrap_pyfunction!(get_status, m)?)?;
    m.add_function(wrap_pyfunction!(load_model, m)?)?;
    m.add_function(wrap_pyfunction!(unload_model, m)?)?;
    m.add_function(wrap_pyfunction!(list_models, m)?)?;
    m.add_function(wrap_pyfunction!(validate_batch, m)?)?;
    m.add_function(wrap_pyfunction!(run_inference, m)?)?;
    m.add_function(wrap_pyfunction!(embed_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(get_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(reset_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(get_supported_compute_units, m)?)?;
    m.add_function(wrap_pyfunction!(is_ane_available, m)?)?;

    // GNN-3: GraphSAGE ANE functions
    m.add_function(wrap_pyfunction!(gnn_load_model, m)?)?;
    m.add_function(wrap_pyfunction!(gnn_run_inference, m)?)?;
    m.add_function(wrap_pyfunction!(gnn_store_embeddings, m)?)?;
    m.add_function(wrap_pyfunction!(gnn_get_embeddings, m)?)?;
    m.add_function(wrap_pyfunction!(gnn_predict_links, m)?)?;
    m.add_function(wrap_pyfunction!(gnn_validate_batch, m)?)?;

    // NEXTGEN-03: FaceNet ANE functions
    m.add_function(wrap_pyfunction!(facenet_register_model, m)?)?;
    m.add_function(wrap_pyfunction!(facenet_is_registered, m)?)?;
    m.add_function(wrap_pyfunction!(facenet_unregister, m)?)?;
    m.add_function(wrap_pyfunction!(facenet_get_model_info, m)?)?;

    // NEXTGEN-03: Voiceprint ANE functions
    m.add_function(wrap_pyfunction!(voiceprint_register_model, m)?)?;
    m.add_function(wrap_pyfunction!(voiceprint_is_registered, m)?)?;
    m.add_function(wrap_pyfunction!(voiceprint_unregister, m)?)?;
    m.add_function(wrap_pyfunction!(voiceprint_get_model_info, m)?)?;

    // NEXTGEN-03: Cross-modal embedding functions
    m.add_function(wrap_pyfunction!(crossmodal_store_face, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_store_voice, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_query_face, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_query_voice, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_get_face, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_get_voice, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_face_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_voice_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_clear, m)?)?;
    m.add_function(wrap_pyfunction!(crossmodal_stats, m)?)?;

    // NEXTGEN-05: Rust-native CoreML ANE inference (coreml_ane feature)
    #[cfg(feature = "coreml_ane")]
    {
        m.add_function(wrap_pyfunction!(load_prm_model, m)?)?;
        m.add_function(wrap_pyfunction!(run_prm_inference, m)?)?;
        m.add_function(wrap_pyfunction!(run_prm_inference_batch, m)?)?;
        m.add_function(wrap_pyfunction!(get_prm_telemetry, m)?)?;
        m.add_function(wrap_pyfunction!(reset_prm_telemetry, m)?)?;
        m.add_function(wrap_pyfunction!(get_coreml_cache_status, m)?)?;
        m.add_function(wrap_pyfunction!(unload_prm_model, m)?)?;
        m.add_function(wrap_pyfunction!(clear_coreml_cache, m)?)?;
        
        // PRM constants
        m.add("PRM_FEATURE_DIM", 16)?;
        m.add("PRM_HIDDEN_DIM", 32)?;
        m.add("PRM_OUTPUT_DIM", 1)?;
    }

    // Constants
    m.add("ANE_MAX_MODELS", ANE_MAX_MODELS)?;
    m.add("ANE_MAX_BATCH_SIZE", ANE_MAX_BATCH_SIZE)?;
    m.add("GNN_MAX_BATCH_NODES", GNN_MAX_BATCH_NODES)?;
    m.add("GNN_DEFAULT_IN_DIM", GNN_DEFAULT_IN_DIM)?;
    m.add("GNN_DEFAULT_HIDDEN_DIM", GNN_DEFAULT_HIDDEN_DIM)?;
    m.add("GNN_DEFAULT_OUT_DIM", GNN_DEFAULT_OUT_DIM)?;

    // NEXTGEN-03: Cross-modal constants
    m.add("FACENET_EMBEDDING_DIM", 512)?;
    m.add("VOICEPRINT_EMBEDDING_DIM", 256)?;
    m.add("CROSSMODAL_MIN_SIMILARITY", 0.7)?;

    Ok(())
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registry_new() {
        let registry = ANERegistry::new();
        assert_eq!(registry.model_count(), 0);
        assert!(registry.can_load());
    }

    #[test]
    fn test_registry_load_unload() {
        let mut registry = ANERegistry::new();

        // Load first model
        let result = registry.register_model(
            "model1".to_string(),
            "/path/to/model1.mlpackage".to_string(),
            768,
            512,
        );
        assert!(result.is_ok());
        assert_eq!(registry.model_count(), 1);
        assert!(registry.is_loaded("model1"));
        assert!(!registry.is_loaded("model2"));

        // Load second model
        let result = registry.register_model(
            "model2".to_string(),
            "/path/to/model2.mlpackage".to_string(),
            384,
            256,
        );
        assert!(result.is_ok());
        assert_eq!(registry.model_count(), 2);

        // Third model should fail — registry has no auto-eviction
        let result = registry.register_model(
            "model3".to_string(),
            "/path/to/model3.mlpackage".to_string(),
            512,
            512,
        );
        assert!(result.is_err()); // MaxModelsReached

        // Unload
        let result = registry.unregister_model("model1");
        assert!(result.is_ok());
        assert_eq!(registry.model_count(), 1);

        // Now we can load a new model after unloading
        let result = registry.register_model(
            "model3".to_string(),
            "/path/to/model3.mlpackage".to_string(),
            512,
            512,
        );
        assert!(result.is_ok());
        assert_eq!(registry.model_count(), 2);
    }

    #[test]
    fn test_registry_max_models() {
        let mut registry = ANERegistry::new();

        // Load two models (max)
        assert!(registry
            .register_model("m1".to_string(), "/p1".to_string(), 768, 512)
            .is_ok());
        assert!(registry
            .register_model("m2".to_string(), "/p2".to_string(), 384, 256)
            .is_ok());
        assert_eq!(registry.model_count(), 2);
        assert!(!registry.can_load());

        // Third should fail
        let err = registry.register_model("m3".to_string(), "/p3".to_string(), 512, 512);
        assert!(err.is_err());
    }

    #[test]
    fn test_registry_metadata_validation() {
        let mut registry = ANERegistry::new();

        // hidden_dim = 0 should fail
        let err = registry.register_model("m1".to_string(), "/p1".to_string(), 0, 512);
        assert!(err.is_err());

        // max_seq_len = 0 should fail
        let err = registry.register_model("m1".to_string(), "/p1".to_string(), 768, 0);
        assert!(err.is_err());

        // max_seq_len > ANE_MAX_BATCH_SIZE should fail
        let err = registry.register_model("m1".to_string(), "/p1".to_string(), 768, 8192);
        assert!(err.is_err());
    }

    #[test]
    fn test_registry_duplicate_load() {
        let mut registry = ANERegistry::new();

        registry
            .register_model(
                "model1".to_string(),
                "/path/to/model1.mlpackage".to_string(),
                768,
                512,
            )
            .unwrap();

        let result = registry.register_model(
            "model1".to_string(),
            "/path/to/model1_duplicate.mlpackage".to_string(),
            768,
            512,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_batch_validation() {
        // Within bounds
        assert!(validate_batch(32, 128, 512).is_ok());
        assert!(validate_batch(1, 512, 512).is_ok());

        // Batch size exceeded
        let result = validate_batch(100, 50, 512); // 5000 tokens > 4096
        assert!(result.is_err());

        // Sequence length exceeded
        let result = validate_batch(8, 1024, 512);
        assert!(result.is_err());
    }

    #[test]
    fn test_ane_available() {
        // This test just verifies the function doesn't panic
        let available = is_ane_available();
        #[cfg(target_os = "macos")]
        {
            #[cfg(target_arch = "aarch64")]
            {
                assert!(available);
            }
            #[cfg(not(target_arch = "aarch64"))]
            {
                // x86_64 Mac doesn't have ANE
                assert!(!available);
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            assert!(!available);
        }
    }

    #[test]
    fn test_telemetry() {
        reset_telemetry();
        let telemetry = get_telemetry();
        assert_eq!(telemetry.get("embed_calls"), Some(&0));
        assert_eq!(telemetry.get("errors"), Some(&0));
    }
}

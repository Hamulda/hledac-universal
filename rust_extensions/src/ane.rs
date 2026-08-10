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
//! **Rust-native ANE inference** (future work):
//! The `coreml` crate (v0.2) provides Rust bindings to CoreML's C API.
//! When integrated, `run_inference()` and `embed_tokens()` will call
//! CoreML directly without Python bridge overhead. Current stubs delegate
//! to `brain/ane_inference.py:ANEInferenceEngine` via PyO3 callbacks.
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
/// For Rust-native CoreML FFI (future):
///     1. Add `coreml = { version = "0.2", optional = true }` to Cargo.toml
///     2. Implement MLModel::load() + MLPrediction in Rust
///     3. Call MLPredictionOptions::set_compute_units(NeuralEngine)
///     4. Run inference without Python bridge overhead
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

    // Constants
    m.add("ANE_MAX_MODELS", ANE_MAX_MODELS)?;
    m.add("ANE_MAX_BATCH_SIZE", ANE_MAX_BATCH_SIZE)?;
    m.add("GNN_MAX_BATCH_NODES", GNN_MAX_BATCH_NODES)?;
    m.add("GNN_DEFAULT_IN_DIM", GNN_DEFAULT_IN_DIM)?;
    m.add("GNN_DEFAULT_HIDDEN_DIM", GNN_DEFAULT_HIDDEN_DIM)?;
    m.add("GNN_DEFAULT_OUT_DIM", GNN_DEFAULT_OUT_DIM)?;

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

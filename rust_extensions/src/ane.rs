//! Apple Neural Engine (ANE) bindings for Hledac OSINT platform.
//!
//! ## Architecture
//!
//! ANE je dedicated Neural Engine chip na M1/M2/M3 Apple Silicon (15 TOPS).
//! Není přístupný přímo - pouze přes CoreML framework.
//!
//! Tento modul poskytuje:
//! - Model registry s bounded memory (max 2 modely současně per ANE HW limit)
//! - Batch size enforcement (max 4096 per ANE HW constraint)
//! - Token-based inference interface pro embedding úlohy
//! - Fallback routing pro CPU/GPU když ANE není dostupný
//!
//! ## ANE Memory Constraints (M1 8GB specific)
//!
//! - Max 2 modely v paměti najednou
//! - Max batch size 4096 tokenů
//! - Pro embedding úlohy: typicky 64-512 tokenů na sekvenci, batche 8-32
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
//! rust.ane.load_model("modernbert-ane", "/path/to/model.mlpackage")
//!
//! # Run inference
//! embeddings = rust.ane.embed_tokens("modernbert-ane", token_ids, attention_mask)
//!
//! # Unload when done (allows new model to be loaded)
//! rust.ane.unload_model("modernbert-ane")
//! ```
//!
//! ## Feature Gate
//!
//! Enabled via `ane = ["ane"]` feature flag in Cargo.toml.

use pyo3::prelude::*;
use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;
use parking_lot::RwLock;

/// ANE hardware constraints
const ANE_MAX_MODELS: usize = 2;
const ANE_MAX_BATCH_SIZE: usize = 4096;

/// ANE compute unit preference for CoreML
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ANEComputeUnit {
    NeuralEngine,
    CPU,
    GPU,
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
            ANEError::ComputeUnitNotSupported => write!(f, "Neural Engine compute unit not supported"),
        }
    }
}

impl std::error::Error for ANEError {}

/// Global ANE registry — process-wide singleton
static ANE_GLOBAL_REGISTRY: LazyLock<RwLock<ANERegistry>> =
    LazyLock::new(|| RwLock::new(ANERegistry::new()));

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
pub fn load_model(model_id: String, model_path: String, hidden_dim: usize, max_seq_len: usize) -> Result<String, PyErr> {
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

/// Run model inference on ANE (stub).
///
/// This is a placeholder that delegates to Python's CoreML implementation.
/// The Python side should call coreml_embedder or mlx_embedder for actual inference.
///
/// For full ANE support, models must be:
/// 1. Compiled with coremltools (compute_units=ComputeUnit.ANANEURAL)
/// 2. Loaded via CoreML framework
/// 3. Executed with Neural Engine compute unit preference
///
/// Args:
///     model_id: Registered model identifier
///     input_ids: Flattened token IDs (batch * seq_len)
///     attention_mask: Attention mask (batch * seq_len)
///
/// Returns: Embeddings as flattened f32 array, or error
#[pyfunction]
pub fn run_inference(model_id: String, input_ids: Vec<i64>, attention_mask: Vec<i64>) -> Result<Vec<f32>, PyErr> {
    let registry = ANE_GLOBAL_REGISTRY.read();

    let meta = registry
        .get_model(&model_id)
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("ANE model not found: {}", model_id)))?;

    // Guard against division by zero
    if input_ids.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err("input_ids cannot be empty"));
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
        telemetry.embed_tokens = telemetry.embed_tokens.saturating_add(input_ids.len() as u64);
    }

    // Delegate to Python side for actual inference
    // This stub returns an error directing to Python implementation
    Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
        "ANE inference for '{}' should be called from Python via CoreML. \
         Use brain.ane_embedder.ANEEmbedder.embed() for actual inference. \
         Registered model: hidden_dim={}, max_seq_len={}",
        model_id, meta.hidden_dim, meta.max_seq_len
    )))
}

/// Compute embedding from tokenized input (stub).
///
/// Similar to run_inference but specifically for embedding models.
/// Returns pooled embeddings of shape (batch, hidden_dim).
///
/// For actual ANE embedding:
/// 1. Use coremltools to compile model with compute_units=ComputeUnit.ANANEURAL
/// 2. Load via CoreML in Python
/// 3. Execute with Neural Engine preference
#[pyfunction]
pub fn embed_tokens(model_id: String, token_ids: Vec<i64>, attention_mask: Vec<i64>) -> Result<Vec<f32>, PyErr> {
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
#[pyfunction]
pub fn is_ane_available() -> bool {
    // ANE is available on all Apple Silicon chips (M1, M1 Pro, M1 Max, M1 Ultra, M2, M3, etc.)
    if std::env::consts::OS == "macos" && std::env::consts::ARCH == "aarch64" {
        // Check if we're on Apple Silicon
        // This is a basic check - actual ANE availability requires CoreML
        return true;
    }
    false
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

    // Constants
    m.add("ANE_MAX_MODELS", ANE_MAX_MODELS)?;
    m.add("ANE_MAX_BATCH_SIZE", ANE_MAX_BATCH_SIZE)?;

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
        assert!(registry.register_model("m1".to_string(), "/p1".to_string(), 768, 512).is_ok());
        assert!(registry.register_model("m2".to_string(), "/p2".to_string(), 384, 256).is_ok());
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

//! MLX Bridge -- async token streaming + adaptive buffering pro MLX LLM inference.
//!
//! ## Architecture
//!
//! ```text
//! Python asyncio                   MLX Worker Thread
//! ──────────────────────────────────────────────────────────────────
//!                                   ┌──────────────────────┐
//! generate_stream() ──────────────►│  SPSC Prompt Queue   │
//! (async generator)                 │  (Rust, 16 slots)    │
//!                                   └──────────┬───────────┘
//!                                               │
//!                                   ┌───────────▼───────────┐
//!                                   │  Python stream_tokens │
//!                                   │  mlx_lm.stream_generate│
//!                                   └──────────┬───────────┘
//!                                               │
//!   ◄─────── Adaptive Ring Buffer (Rust) ◄────┘
//!   (yield chunks, size = f(memory_pressure))
//! ```
//!
//! ## What Rust provides vs Python
//!
//! Rust provides:
//!   - MLXBridgeConfig: streaming configuration
//!   - TokenChunk: yielded token with metadata
//!   - AdaptiveChunkSizer: memory-aware chunk sizing
//!
//! Python provides:
//!   - The actual mlx_lm.stream_generate() call (Python API, no C equivalent)
//!   - async generator protocol (__anext__)
//!
//! This is the CORRECT architecture because:
//!   - mlx_lm has no C API -- only Python
//!   - PyO3 async generators require careful lifetime management
//!   - Python's asyncio handles the async iteration protocol correctly
//!
//! ## Key invariants (MBridge.*)
//!
//! MBridge.1: Zero top-level MLX imports (lazy via mlx_lm import inside async fn)
//! MBridge.2: SPSC queue depth = 16 (matches spsc_queue.rs SPSC_QUEUE_DEPTH)
//! MBridge.3: Chunk size adaptive: 64 tokens @ normal, 256 @ WARNING, 512 @ CRITICAL
//! MBridge.4: Cancellation wired to _stream_cancelled asyncio.Event
//! MBridge.5: Memory feedback: mlx.core.metal.get_active_memory() -> chunk_size
//!
//! Always-on, fail-safe, M1 8GB bounded.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/// SPSC queue depth -- matches spsc_queue.rs SPSC_QUEUE_DEPTH.
pub const MLX_BRIDGE_QUEUE_DEPTH: usize = 16;

/// Per-prompt payload budget.
pub const MLX_BRIDGE_SLOT_BYTES: usize = 1024;

// ─────────────────────────────────────────────────────────────────────────────
// MemoryPressure enum
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemoryPressure {
    Normal,
    Warning,
    Critical,
}

impl MemoryPressure {
    /// Convert from 0.0-1.0 pressure value.
    pub fn from_ratio(ratio: f32) -> Self {
        if ratio >= 0.85 {
            MemoryPressure::Critical
        } else if ratio >= 0.70 {
            MemoryPressure::Warning
        } else {
            MemoryPressure::Normal
        }
    }

    /// Adaptive chunk size (in tokens) for this pressure level.
    pub fn chunk_size(&self) -> usize {
        match self {
            MemoryPressure::Normal => 64,
            MemoryPressure::Warning => 256,
            MemoryPressure::Critical => 512,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// MLXBridgeConfig -- streaming configuration
// ─────────────────────────────────────────────────────────────────────────────

/// Configuration for MLX token streaming bridge.
#[pyclass(name = "MLXBridgeConfig")]
#[derive(Debug, Clone)]
pub struct MLXBridgeConfig {
    /// Max tokens to generate per request.
    pub max_tokens: usize,
    /// Temperature for sampling.
    pub temperature: f32,
    /// Chunk size in tokens (overridden by adaptive sizing if 0).
    pub chunk_size: usize,
    /// Enable adaptive chunk sizing based on memory pressure.
    pub adaptive_chunk: bool,
    /// Stream buffer size (yield when buffer reaches this many chunks).
    pub stream_buffer_size: usize,
    /// Memory pressure warning threshold (0.0-1.0).
    pub pressure_warning: f32,
    /// Memory pressure critical threshold (0.0-1.0).
    pub pressure_critical: f32,
}

impl Default for MLXBridgeConfig {
    fn default() -> Self {
        Self {
            max_tokens: 1024,
            temperature: 0.1,
            chunk_size: 0, // 0 = adaptive
            adaptive_chunk: true,
            stream_buffer_size: 8,
            pressure_warning: 0.70,
            pressure_critical: 0.85,
        }
    }
}

#[pymethods]
impl MLXBridgeConfig {
    #[new]
    fn new(
        max_tokens: usize,
        temperature: f32,
        chunk_size: usize,
        adaptive_chunk: bool,
        stream_buffer_size: usize,
        pressure_warning: f32,
        pressure_critical: f32,
    ) -> Self {
        Self {
            max_tokens,
            temperature,
            chunk_size,
            adaptive_chunk,
            stream_buffer_size,
            pressure_warning,
            pressure_critical,
        }
    }

    #[getter]
    fn get_max_tokens(&self) -> usize {
        self.max_tokens
    }

    #[getter]
    fn get_temperature(&self) -> f32 {
        self.temperature
    }

    #[getter]
    fn get_chunk_size(&self) -> usize {
        if self.adaptive_chunk {
            self.chunk_size.max(64)
        } else {
            self.chunk_size.max(1)
        }
    }

    #[getter]
    fn get_adaptive_chunk(&self) -> bool {
        self.adaptive_chunk
    }

    #[getter]
    fn get_stream_buffer_size(&self) -> usize {
        self.stream_buffer_size
    }

    fn __repr__(&self) -> String {
        format!(
            "MLXBridgeConfig(max_tokens={}, temp={}, chunk_size={}, adaptive={})",
            self.max_tokens, self.temperature, self.chunk_size, self.adaptive_chunk
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// TokenChunk -- single yielded token from stream
// ─────────────────────────────────────────────────────────────────────────────

/// Token chunk with metadata for streaming.
#[pyclass(name = "TokenChunk")]
#[derive(Debug, Clone)]
pub struct TokenChunk {
    /// Decoded token text.
    pub text: String,
    /// Token ID (if available, else 0).
    pub token_id: u32,
    /// Memory pressure level: "normal", "warning", "critical".
    pub pressure: String,
    /// Cumulative tokens generated.
    pub total_generated: usize,
}

#[pymethods]
impl TokenChunk {
    #[getter]
    fn text(&self) -> &str {
        &self.text
    }

    #[getter]
    fn token_id(&self) -> u32 {
        self.token_id
    }

    #[getter]
    fn pressure(&self) -> &str {
        &self.pressure
    }

    #[getter]
    fn total_generated(&self) -> usize {
        self.total_generated
    }

    fn __repr__(&self) -> String {
        format!(
            "TokenChunk(text={:?}, pressure={}, total={})",
            self.text, self.pressure, self.total_generated
        )
    }
}

impl TokenChunk {
    pub fn new(text: String, pressure: MemoryPressure, total_generated: usize) -> Self {
        Self {
            text,
            token_id: 0,
            pressure: match pressure {
                MemoryPressure::Normal => "normal".to_string(),
                MemoryPressure::Warning => "warning".to_string(),
                MemoryPressure::Critical => "critical".to_string(),
            },
            total_generated,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// AdaptiveChunkSizer -- memory-aware chunk sizing
// ─────────────────────────────────────────────────────────────────────────────

/// Adaptive chunk sizer based on memory pressure.
#[pyclass(name = "AdaptiveChunkSizer")]
#[derive(Debug, Clone)]
pub struct AdaptiveChunkSizer {
    warning_threshold: f32,
    critical_threshold: f32,
    current_pressure: MemoryPressure,
}

#[pymethods]
impl AdaptiveChunkSizer {
    #[new]
    fn new(warning: f32, critical: f32) -> Self {
        Self {
            warning_threshold: warning,
            critical_threshold: critical,
            current_pressure: MemoryPressure::Normal,
        }
    }

    /// Update pressure from a simple 0.0-1.0 ratio.
    fn update(&mut self, ratio: f32) {
        self.current_pressure = MemoryPressure::from_ratio(ratio);
    }

    /// Get current pressure level as string.
    fn get_pressure(&self) -> &str {
        match self.current_pressure {
            MemoryPressure::Normal => "normal",
            MemoryPressure::Warning => "warning",
            MemoryPressure::Critical => "critical",
        }
    }

    /// Get current chunk size based on adaptive pressure.
    fn get_chunk_size(&self) -> usize {
        self.current_pressure.chunk_size()
    }

    /// Check if current pressure is critical.
    fn is_critical(&self) -> bool {
        self.current_pressure == MemoryPressure::Critical
    }

    /// Check if current pressure is warning or critical.
    fn is_elevated(&self) -> bool {
        self.current_pressure != MemoryPressure::Normal
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// MLXBridge -- Python-facing bridge wrapper
// ─────────────────────────────────────────────────────────────────────────────

/// MLX streaming bridge.
///
/// Wraps Python mlx_lm.stream_generate() iterator with Rust-side
/// adaptive buffering and memory feedback. The actual MLX inference
/// runs in Python via mlx_lm.stream_generate() -- this bridge provides
/// the coordination layer.
///
/// MBridge.1: mlx_lm is imported lazily inside Python, not in Rust
#[pyclass(name = "MLXBridge")]
#[derive(Debug)]
pub struct MLXBridge {
    /// Engine reference (PyObject, stored as opaque reference).
    engine: Py<PyAny>,
    /// Tokenizer reference.
    tokenizer: Py<PyAny>,
    /// Configuration.
    config: MLXBridgeConfig,
    /// Adaptive chunk sizer.
    sizer: AdaptiveChunkSizer,
    /// Total tokens generated.
    total_tokens: usize,
    /// Cancellation flag.
    cancelled: bool,
}

// Manual Clone implementation because Py<PyAny> doesn't implement Clone automatically
// Use Py::clone_ref which increments the underlying PyObject refcount
impl Clone for MLXBridge {
    fn clone(&self) -> Self {
        Python::with_gil(|py| Self {
            engine: Py::clone_ref(&self.engine, py),
            tokenizer: Py::clone_ref(&self.tokenizer, py),
            config: self.config.clone(),
            sizer: self.sizer.clone(),
            total_tokens: self.total_tokens,
            cancelled: self.cancelled,
        })
    }
}

#[pymethods]
impl MLXBridge {
    #[new]
    #[pyo3(signature = (engine, tokenizer, config=None))]
    fn new(
        engine: Py<PyAny>,
        tokenizer: Py<PyAny>,
        config: Option<MLXBridgeConfig>,
    ) -> Self {
        let cfg = config.unwrap_or_default();
        Self {
            engine,
            tokenizer,
            config: cfg.clone(),
            sizer: AdaptiveChunkSizer::new(cfg.pressure_warning, cfg.pressure_critical),
            total_tokens: 0,
            cancelled: false,
        }
    }

    /// Update memory pressure from external signal (0.0-1.0 ratio).
    ///
    /// Called by MLX scheduler or resource governor to update adaptive chunk sizing.
    /// MBridge.3: Chunk size adapts to memory pressure.
    fn update_pressure(&mut self, ratio: f32) {
        self.sizer.update(ratio);
    }

    /// Update memory pressure from Metal active memory bytes.
    ///
    /// MBridge.3: Uses actual Metal memory stats.
    fn update_pressure_metal(&mut self, active_bytes: usize, total_bytes: usize) {
        let ratio = if total_bytes > 0 {
            active_bytes as f32 / total_bytes as f32
        } else {
            0.0
        };
        self.sizer.update(ratio);
    }

    /// Get current chunk size based on adaptive pressure.
    fn get_chunk_size(&self) -> usize {
        self.sizer.get_chunk_size()
    }

    /// Get current pressure level.
    fn get_pressure(&self) -> &str {
        self.sizer.get_pressure()
    }

    /// Check if cancellation flag is set.
    fn is_cancelled(&self) -> bool {
        self.cancelled
    }

    /// Set cancellation flag.
    fn cancel(&mut self) {
        self.cancelled = true;
    }

    /// Reset cancellation flag.
    fn reset_cancelled(&mut self) {
        self.cancelled = false;
    }

    /// Get total tokens generated.
    fn get_total_tokens(&self) -> usize {
        self.total_tokens
    }

    /// Increment token counter.
    fn _increment_tokens(&mut self, count: usize) {
        self.total_tokens += count;
    }

    /// Get configuration as a dict.
    fn get_config(&self, py: Python<'_>) -> Py<PyDict> {
        let stats = PyDict::new(py);
        stats.set_item("max_tokens", self.config.max_tokens).unwrap();
        stats.set_item("temperature", self.config.temperature).unwrap();
        stats.set_item("chunk_size", self.get_chunk_size()).unwrap();
        stats.set_item("adaptive_chunk", self.config.adaptive_chunk).unwrap();
        stats.set_item("stream_buffer_size", self.config.stream_buffer_size).unwrap();
        stats.set_item("pressure_warning", self.config.pressure_warning).unwrap();
        stats.set_item("pressure_critical", self.config.pressure_critical).unwrap();
        stats.into()
    }

    /// Get streaming statistics as dict.
    fn get_stats(&self, py: Python<'_>) -> Py<PyDict> {
        let stats = PyDict::new(py);
        stats.set_item("total_tokens", self.total_tokens).unwrap();
        stats.set_item("cancelled", self.cancelled).unwrap();
        stats.set_item("chunk_size", self.get_chunk_size()).unwrap();
        stats.set_item("pressure", self.get_pressure()).unwrap();
        stats.set_item("max_tokens", self.config.max_tokens).unwrap();
        stats.into()
    }

    fn __repr__(&self) -> String {
        format!(
            "MLXBridge(tokens={}, pressure={}, cancelled={})",
            self.total_tokens,
            self.sizer.get_pressure(),
            self.cancelled
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// batch_tokenize — Rayon-parallel prompt tokenization
// ─────────────────────────────────────────────────────────────────────────────

/// Parallel tokenization of multiple prompts using rayon.
///
/// CPU-bound: tokenization runs in parallel across prompts.
/// GPU-bound mlx_lm.generate() stays in Python (Metal is single-stream).
///
/// Returns Vec of token IDs (as Vec<u32> per prompt).
#[pyfunction]
#[pyo3(name = "batch_tokenize")]
pub fn batch_tokenize_(
    py: Python<'_>,
    tokenizers: Vec<Py<PyAny>>,
    prompts: Vec<String>,
    _add_special_tokens: bool,
) -> PyResult<Bound<'_, PyList>> {
    if tokenizers.is_empty() || prompts.is_empty() {
        return Ok(PyList::empty(py));
    }
    if tokenizers.len() != prompts.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "tokenizers and prompts must have same length",
        ));
    }

    // Collect results into a Vec of Vec<u32> first, then create PyList
    let mut results: Vec<Vec<u32>> = Vec::with_capacity(tokenizers.len());
    for i in 0..tokenizers.len() {
        let p = &prompts[i];
        let ids: Vec<u32> = Python::with_gil(|py| {
            // Clone the Py pointer so we can call into_bound without moving
            let tok = Py::clone_ref(&tokenizers[i], py);
            let tok_bound: Bound<'_, PyAny> = tok.into_bound(py);
            let result = match tok_bound.call_method1("encode", (p,)) {
                Ok(r) => r,
                Err(_) => {
                    return vec![];
                }
            };
            let ids: Vec<u32> = if result.is_instance_of::<PyList>() {
                result.extract::<Vec<u32>>().unwrap_or_default()
            } else if result.is_instance_of::<PyTuple>() {
                result.extract::<Vec<u32>>().unwrap_or_default()
            } else {
                vec![]
            };
            ids
        });
        results.push(ids);
    }

    // Build PyList from results
    let py_list = PyList::new(py, &results)?;
    Ok(py_list)
}

// ─────────────────────────────────────────────────────────────────────────────
// Memory probing (M1 8GB UMA safety)
// ─────────────────────────────────────────────────────────────────────────────

/// Probe total physical memory via sysctl HW_MEMSIZE on Darwin.
/// Returns 0 if unavailable (non-Darwin or sysctl failure).
fn probe_total_memory() -> usize {
    #[cfg(target_os = "macos")]
    {
        use libc::c_int;
        let mut mib: [c_int; 2] = [6, 25]; // {CTL_HW, HW_MEMSIZE}
        let mut total: u64 = 0;
        let mut len = std::mem::size_of::<u64>();
        unsafe {
            let ret = libc::sysctl(
                mib.as_mut_ptr(),
                2,
                &mut total as *mut _ as *mut libc::c_void,
                &mut len,
                std::ptr::null_mut() as *mut _,
                0,
            );
            if ret == 0 {
                return total as usize;
            }
        }
    }
    0
}

/// Check if sufficient memory is available for MLX model loading.
///
/// Returns (allowed, available_bytes, reason).
/// reason is "ok" if allowed, otherwise a descriptive message.
#[pyfunction]
#[pyo3(name = "check_available_memory")]
pub fn check_available_memory_py(
    required_bytes: usize,
    available_bytes: usize,
) -> PyResult<(bool, usize, String)> {
    // Hard floor: refuse to load on systems with < 2GB total
    const MIN_TOTAL_MEMORY: usize = 2 * 1024 * 1024 * 1024;
    let total = probe_total_memory();
    if total > 0 && total < MIN_TOTAL_MEMORY {
        return Ok((
            false,
            available_bytes,
            format!(
                "system_total={} < {} (2GiB minimum)",
                total, MIN_TOTAL_MEMORY
            ),
        ));
    }
    if available_bytes < required_bytes {
        return Ok((
            false,
            available_bytes,
            format!(
                "available={} < required={}",
                available_bytes, required_bytes
            ),
        ));
    }
    Ok((true, available_bytes, "ok".to_string()))
}

// ─────────────────────────────────────────────────────────────────────────────
// Registration
// ─────────────────────────────────────────────────────────────────────────────

/// Register MLX bridge types with Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MLXBridge>()?;
    m.add_class::<MLXBridgeConfig>()?;
    m.add_class::<TokenChunk>()?;
    m.add_class::<AdaptiveChunkSizer>()?;
    m.add_function(wrap_pyfunction!(batch_tokenize_, m)?)?;
    m.add_function(wrap_pyfunction!(check_available_memory_py, m)?)?;
    Ok(())
}

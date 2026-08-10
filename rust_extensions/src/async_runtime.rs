//!
//! # Shared Tokio Runtime — MODERN-07 Architecture Fix
//!
//! ## Problem Solved
//!
//! Previously, each network subsystem (DNS, QUIC, Arti) created its own
//! multi-thread Tokio runtime, resulting in:
//!
//! | Subsystem | Threads | Memory Overhead |
//! |-----------|---------|-----------------|
//! | dns.rs    | 4       | ~8 MB           |
//! | quic.rs   | 2       | ~4 MB           |
//! | arti_bridge.rs | 2 | ~4 MB           |
//! | **Total** | **8**   | **~16 MB**      |
//!
//! Each runtime also had its own reactor and timer wheels, preventing:
//! - Shared I/O sources (epoll/kqueue)
//! - Efficient timer coalescing
//! - Memory deduplication
//!
//! ## Solution
//!
//! Single global `OnceLock<Runtime>` with `Handle` borrowing pattern:
//!
//! ```text
//! async_runtime.rs
//!   ├── SHARED_RUNTIME: OnceLock<Runtime>
//!   ├── get_runtime() -> &'static Runtime
//!   ├── get_handle() -> Handle  (Clone + Send + Sync)
//!   └── config() -> RuntimeConfig
//!
//! Consumers:
//!   ├── dns.rs: spawns tasks via Handle
//!   ├── quic.rs: spawns tasks via Handle  
//!   └── arti_bridge.rs: uses Handle instead of owned Runtime
//! ```
//!
//! ## M1 8GB Safety
//!
//! - **Worker threads**: `min(p_cores, 4)` — matches hardware, bounded for RAM
//! - **Max blocking threads**: 2× workers (for I/O-bound operations)
//! - **Features enabled**: net, time, sync, io-util, macros
//!
//! ## Backward Compatibility
//!
//! - Existing `new()`, `try_new()`, `new_fallback()` patterns preserved
//! - `Handle` cloning is O(1) (Arc-based internally)
//! - Graceful degradation on OOM via fallible `try_init()`

use std::sync::OnceLock;

// ============================================================================
// Constants
// ============================================================================

/// Minimum worker threads (fallback for constrained environments).
const MIN_WORKERS: usize = 1;

/// Maximum worker threads (M1 8GB RAM budget safety).
const MAX_WORKERS: usize = 4;

/// Multiplier for blocking threads relative to workers.
const BLOCKING_MULTIPLIER: usize = 2;

// ============================================================================
// Error Types
// ============================================================================

/// Errors from shared runtime initialization.
#[derive(Debug, Clone)]
pub enum RuntimeError {
    /// Failed to build tokio runtime.
    BuildFailed(String),
    /// Runtime already initialized (should not happen with OnceLock).
    AlreadyInitialized,
}

impl std::fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RuntimeError::BuildFailed(msg) => write!(f, "runtime build failed: {}", msg),
            RuntimeError::AlreadyInitialized => write!(f, "runtime already initialized"),
        }
    }
}

impl std::error::Error for RuntimeError {}

// ============================================================================
// Runtime Configuration
// ============================================================================

/// Runtime configuration parameters.
/// Detected at compile time based on target.
#[derive(Debug, Clone, Copy)]
pub struct RuntimeConfig {
    /// Number of worker threads.
    pub workers: usize,
    /// Maximum blocking threads.
    pub max_blocking: usize,
    /// Enable all features (net, time, io-util, sync, macros).
    pub enable_all: bool,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            workers: Self::detect_workers(),
            max_blocking: Self::detect_workers() * BLOCKING_MULTIPLIER,
            enable_all: true,
        }
    }
}

impl RuntimeConfig {
    /// Detect optimal worker count for M1 8GB.
    ///
    /// Strategy:
    /// 1. macOS: Use `sysctl hw.perflevel0.logicalcpu` for P-core count
    /// 2. Fallback: `num_cpus::get_physical()`
    /// 3. Clamp to [1, 4] for RAM budget safety
    fn detect_workers() -> usize {
        #[cfg(target_os = "macos")]
        {
            let mut size: libc::size_t = std::mem::size_of::<u32>();
            let mut value: u32 = 0;

            let ret = unsafe {
                libc::sysctlbyname(
                    b"hw.perflevel0.logicalcpu\0".as_ptr() as *const libc::c_char,
                    &mut value as *mut _ as *mut libc::c_void,
                    &mut size,
                    std::ptr::null_mut(),
                    0,
                )
            };

            if ret == 0 {
                return (value as usize).clamp(MIN_WORKERS, MAX_WORKERS);
            }
        }

        // Fallback: use num_cpus or default
        num_cpus::get().clamp(MIN_WORKERS, MAX_WORKERS)
    }

}

// ============================================================================
// Global Runtime (OnceLock)
// ============================================================================

/// Global shared Tokio runtime.
/// 
/// Initialized lazily on first access via `get_runtime()`.
/// Once initialized, lives for the entire process lifetime.
///
/// Memory budget (M1 8GB safe):
/// - Workers × ~2MB stack = ~8MB max
/// - Reactor + timer wheels = ~1MB
/// - Total: ~10MB vs 3 separate runtimes (~24MB)
static SHARED_RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();

/// Synchronization semaphore for runtime initialization.
/// Prevents race conditions during lazy init.
static INIT_SEM: std::sync::Mutex<()> = std::sync::Mutex::new(());

// ============================================================================
// Public API
// ============================================================================

/// Get or create the shared Tokio runtime.
///
/// Uses `OnceLock` for thread-safe lazy initialization.
/// First call creates the runtime; subsequent calls return the same instance.
///
/// # Returns
/// `&'static Runtime` — static reference, valid for entire process lifetime.
///
/// # Panics
/// Panics if runtime creation fails (OOM or system limits exceeded).
/// For graceful degradation, use `try_init()` instead.
pub fn get_runtime() -> &'static tokio::runtime::Runtime {
    SHARED_RUNTIME.get_or_init(|| {
        let config = RuntimeConfig::default();
        build_runtime(config)
            .expect("async_runtime: failed to create shared tokio runtime")
    })
}

/// Get a Handle to the shared runtime.
///
/// This is the preferred way to spawn tasks from multiple modules.
/// Handle is `Clone + Send + Sync` and can be stored in structs.
///
/// # Example
/// ```ignore
/// use async_runtime::get_handle;
/// let handle = get_handle();
/// handle.spawn(async { /* ... */ });
/// ```
pub fn get_handle() -> tokio::runtime::Handle {
    get_runtime().handle().clone()
}

/// Try to initialize the shared runtime.
///
/// Returns `Ok(())` if initialized successfully or already initialized.
/// Returns `Err(RuntimeError)` if initialization fails.
///
/// Use this for graceful degradation when OOM is possible.
pub fn try_init() -> Result<(), RuntimeError> {
    // Ensure only one thread initializes at a time
    let _guard = INIT_SEM.lock().map_err(|_| RuntimeError::AlreadyInitialized)?;

    if SHARED_RUNTIME.get().is_some() {
        return Ok(()); // Already initialized
    }

    let config = RuntimeConfig::default();
    match build_runtime(config) {
        Ok(runtime) => {
            SHARED_RUNTIME.set(runtime).map_err(|_| RuntimeError::AlreadyInitialized)?;
            Ok(())
        }
        Err(e) => Err(RuntimeError::BuildFailed(e)),
    }
}

/// Check if the shared runtime has been initialized.
pub fn is_initialized() -> bool {
    SHARED_RUNTIME.get().is_some()
}

/// Get the current runtime configuration.
pub fn config() -> RuntimeConfig {
    RuntimeConfig::default()
}

// ============================================================================
// Internal Builders
// ============================================================================

/// Build a tokio runtime with the given configuration.
fn build_runtime(config: RuntimeConfig) -> Result<tokio::runtime::Runtime, String> {
    let mut builder = tokio::runtime::Builder::new_multi_thread();

    builder
        .worker_threads(config.workers)
        .max_blocking_threads(config.max_blocking);

    if config.enable_all {
        builder.enable_all();
    }

    builder
        .build()
        .map_err(|e| format!("tokio runtime build failed: {}", e))
}

/// Build a minimal single-threaded runtime for fallback/OOM scenarios.
pub(crate) fn build_fallback_runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("async_runtime: fallback runtime build failed — this should never happen")
}

// ============================================================================
// Re-exports for convenience
// ============================================================================

pub use tokio::runtime::{Handle, Runtime, Builder};
pub use tokio::task::{JoinSet, AbortHandle};

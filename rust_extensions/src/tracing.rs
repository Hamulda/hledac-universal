//! tracing.rs — Rust-side tracing for OTel-compatible observability (R24, TEL-02 fix)
//!
//! Provides end-to-end trace context propagation Python↔Rust via W3C TraceContext.
//!
//! Architecture (TEL-02):
//!   Python OpenTelemetry SDK (generates trace_id)
//!       ↓ trace_id/span_id passed as hex string parameters
//!   Rust: start_span() → validates, stores in TLS, creates tracing span
//!       ↓ span entered on current thread
//!       ↓ get_current_trace_id() / get_current_span_id() reads from TLS
//!   Python: reads trace_id/span_id from Rust, injects back into OTel context
//!
//! The key insight: Python owns the trace hierarchy. Rust operates as a span
//! processor under Python's OTel context. trace_id/span_id are explicit strings
//! (W3C TraceContext hex format), stored in thread-local storage for fast access.
//!
//! W3C TraceContext format:
//!   trace_id: 32 hex chars (128 bits) — lowercase
//!   span_id:  16 hex chars (64 bits)  — lowercase
//!
//! Env vars:
//!   HLEDAC_TRACING_ENABLED=1  # Enable/disable (default: 1)
//!   HLEDAC_TRACING_SERVICE_NAME # Service name (default: hledac-rust)

use pyo3::prelude::*;
use std::sync::OnceLock;

static TRACING_INIT: OnceLock<bool> = OnceLock::new();
static TRACING_ENABLED: OnceLock<bool> = OnceLock::new();

/// Valid W3C TraceContext trace_id: exactly 32 lowercase hex chars.
const TRACE_ID_LEN: usize = 32;

/// Valid W3C TraceContext span_id: exactly 16 lowercase hex chars.
const SPAN_ID_LEN: usize = 16;

/// Thread-local guard for the currently entered span.
/// Unlike CURRENT_TRACE (which only stores metadata), SPAN_GUARD holds the
/// actual EnteredSpan guard so that span_exit() can properly end the span.
///
/// Pattern:
///   span_enter() → creates span, stores guard in SPAN_GUARD, returns true
///   span_exit()  → drops the guard, span is ended
///   If span_enter returns but span_exit is never called → guard is leaked
///     and span leaks too (but no panic — EnteredSpan has a Drop impl).
thread_local! {
    static SPAN_GUARD: std::cell::RefCell<Option<tracing::span::EnteredSpan>> =
        std::cell::RefCell::new(None);
}

/// Thread-local storage for the span created by start_span — entered by span_enter.
/// This ensures start_span and span_enter share the SAME span object.
thread_local! {
    static STARTED_SPAN: std::cell::RefCell<Option<tracing::Span>> =
        std::cell::RefCell::new(None);
}

/// Thread-local trace context — stores the trace_id and span_id as strings.
/// This is metadata only (used by get_tls_trace_id / get_tls_span_id).
/// Does NOT correspond to an active Rust span — that is SPAN_GUARD's job.
thread_local! {
    static CURRENT_TRACE: std::cell::RefCell<Option<TraceContext>> = std::cell::RefCell::new(None);
}

/// The trace context stored in the thread-local.
#[derive(Clone, Debug)]
struct TraceContext {
    trace_id: String,
    span_id: String,
}

impl TraceContext {
    fn new(trace_id: String, span_id: String) -> Self {
        Self { trace_id, span_id }
    }

    fn is_valid(&self) -> bool {
        self.trace_id.len() == TRACE_ID_LEN && self.span_id.len() == SPAN_ID_LEN
    }
}

/// Set the current trace context on this thread. Used by pool_run.rs.
#[cfg(feature = "otel")]
pub(crate) fn set_tls_trace_context(trace_id: Option<u128>, span_id: Option<u128>) {
    if let (Some(tid), Some(sid)) = (trace_id, span_id) {
        if tid != 0 && sid != 0 {
            let ctx = TraceContext::new(format!("{:032x}", tid), format!("{:016x}", sid));
            CURRENT_TRACE.with(|cell| {
                *cell.borrow_mut() = Some(ctx);
            });
        }
    }
}

/// Get the current trace_id from TLS. Used by pool_run.rs execute_with_optional_span.
#[cfg(feature = "otel")]
pub(crate) fn get_tls_trace_id() -> Option<String> {
    CURRENT_TRACE.with(|cell| cell.borrow().as_ref().map(|ctx| ctx.trace_id.clone()))
}

/// Get the current span_id from TLS.
#[cfg(feature = "otel")]
pub(crate) fn get_tls_span_id() -> Option<String> {
    CURRENT_TRACE.with(|cell| cell.borrow().as_ref().map(|ctx| ctx.span_id.clone()))
}

/// Clear the current trace context on this thread.
#[cfg(feature = "otel")]
pub(crate) fn clear_tls_trace_context() {
    CURRENT_TRACE.with(|cell| {
        *cell.borrow_mut() = None;
    });
}

#[cfg(not(feature = "otel"))]
pub(crate) fn set_tls_trace_context(_trace_id: Option<u128>, _span_id: Option<u128>) {}

#[cfg(not(feature = "otel"))]
pub(crate) fn get_tls_trace_id() -> Option<String> {
    None
}

#[cfg(not(feature = "otel"))]
pub(crate) fn get_tls_span_id() -> Option<String> {
    None
}

#[cfg(not(feature = "otel"))]
pub(crate) fn clear_tls_trace_context() {}

/// Check if tracing is enabled (env var HLEDAC_TRACING_ENABLED != "0").
/// Cached via OnceLock so this is fast on the hot path.
/// Made pub(crate) so pool_run.rs can reuse it instead of duplicating the logic.
pub(crate) fn is_tracing_enabled() -> bool {
    *TRACING_ENABLED.get_or_init(|| {
        std::env::var("HLEDAC_TRACING_ENABLED")
            .map(|v| v != "0")
            .unwrap_or(true)
    })
}

fn get_service_name() -> String {
    std::env::var("HLEDAC_TRACING_SERVICE_NAME").unwrap_or_else(|_| "hledac-rust".to_string())
}

#[cfg(feature = "otel")]
fn init_tracing() -> Result<(), String> {
    use tracing_subscriber::fmt::format::FmtSpan;

    if TRACING_INIT.get().is_some() {
        return Ok(());
    }

    if !is_tracing_enabled() {
        let _ = TRACING_INIT.set(false);
        return Ok(());
    }

    let service_name = get_service_name();

    // TEL-02: Use fmt with JSON output that includes trace_id/span_id fields.
    // Use a simpler approach without EnvFilter for now.
    tracing_subscriber::fmt()
        .with_target(true)
        .with_thread_ids(false)
        .with_file(true)
        .with_line_number(true)
        .with_ansi(false)
        .with_span_events(FmtSpan::CLOSE)
        .try_init()
        ); // Ignore if already initialized

    let _ = TRACING_INIT.set(true);
    println!("[tracing] Initialized: service={}", service_name);
    Ok(())
}

#[cfg(not(feature = "otel"))]
fn init_tracing() -> Result<(), String> {
    Ok(())
}

/// Parse a W3C trace_id from a hex string (32 chars).
#[cfg(feature = "otel")]
fn parse_trace_id_hex(hex: &str) -> Option<String> {
    if hex.len() == TRACE_ID_LEN && hex.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(hex.to_lowercase())
    } else {
        None
    }
}

/// Parse a W3C span_id from a hex string (16 chars).
#[cfg(feature = "otel")]
fn parse_span_id_hex(hex: &str) -> Option<String> {
    if hex.len() == SPAN_ID_LEN && hex.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(hex.to_lowercase())
    } else {
        None
    }
}

/// Generate a new random trace_id (32 hex chars).
#[cfg(feature = "otel")]
fn generate_trace_id_hex() -> String {
    let bytes: [u8; 16] = rand::random();
    hex_encode_16_bytes(bytes)
}

/// Generate a new random span_id (16 hex chars).
#[cfg(feature = "otel")]
fn generate_span_id_hex() -> String {
    let bytes: [u8; 8] = rand::random();
    hex_encode_8_bytes(bytes)
}

/// Encode 16 bytes as 32-char lowercase hex string.
#[cfg(feature = "otel")]
fn hex_encode_16_bytes(bytes: [u8; 16]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut chars = ['0'; 32];
    let mut i = 0;
    while i < 16 {
        chars[i * 2] = HEX[(bytes[i] >> 4) as usize] as char;
        chars[i * 2 + 1] = HEX[(bytes[i] & 0xf) as usize] as char;
        i += 1;
    }
    String::from_iter(chars.iter())
}

/// Encode 8 bytes as 16-char lowercase hex string.
#[cfg(feature = "otel")]
fn hex_encode_8_bytes(bytes: [u8; 8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut chars = ['0'; 16];
    let mut i = 0;
    while i < 8 {
        chars[i * 2] = HEX[(bytes[i] >> 4) as usize] as char;
        chars[i * 2 + 1] = HEX[(bytes[i] & 0xf) as usize] as char;
        i += 1;
    }
    String::from_iter(chars.iter())
}

/// Configure tracing subsystem.
/// Call once at startup before any #[pyfunction] is invoked.
#[pyfunction]
pub fn configure_tracing(
    enabled: bool,
    _otlp_endpoint: Option<String>,
    service_name: Option<String>,
    _debug: bool,
) -> bool {
    if !enabled {
        std::env::set_var("HLEDAC_TRACING_ENABLED", "0");
    }
    if let Some(sn) = service_name {
        std::env::set_var("HLEDAC_TRACING_SERVICE_NAME", sn);
    }

    match init_tracing() {
        Ok(_) => is_tracing_enabled(),
        Err(e) => {
            eprintln!("[tracing] configure_tracing failed: {e}");
            false
        }
    }
}

/// Check if tracing is initialized and active.
#[pyfunction]
pub fn is_tracing_active() -> bool {
    is_tracing_enabled() && TRACING_INIT.get() == Some(true)
}

/// Start a new span with explicit W3C TraceContext trace_id.
/// This is the PRIMARY entry point for Python→Rust trace propagation.
///
/// Args:
///   name: span name
///   trace_id: 32-char hex string (W3C TraceContext format)
///   span_id: 16-char hex string (W3C TraceContext format), or empty to auto-generate
///   traceflags: "01" for sampled, "00" for not sampled (default "01")
///
/// Returns:
///   (trace_id, span_id, is_valid) — is_valid=False if trace_id was malformed
///
/// Python usage:
///   trace_id, span_id, is_valid = rust.tracing.start_span("my_span", trace_id, "", "01")
#[cfg(feature = "otel")]
#[pyfunction]
pub fn start_span(
    name: String,
    trace_id: String,
    span_id: String,
    traceflags: String,
) -> (String, String, bool) {
    if !is_tracing_enabled() {
        return (String::new(), String::new(), false);
    }

    // Validate trace_id (must be exactly 32 hex chars)
    let trace_id = match parse_trace_id_hex(&trace_id) {
        Some(tid) => tid,
        None => return (trace_id, String::new(), false),
    };

    // Validate or generate span_id (docstring: "or empty to auto-generate")
    let span_id = if span_id.is_empty() {
        generate_span_id_hex()
    } else {
        match parse_span_id_hex(&span_id) {
            Some(sid) => sid,
            None => return (trace_id, String::new(), false),
        }
    };
    // Store in TLS so get_current_trace_id/get_current_span_id can retrieve it.
    // NOTE: We do NOT enter the span here — Python calls span_enter() explicitly
    // to make it the current span. This allows the span to outlive start_span()
    // and be entered/closed across separate function calls.
    let ctx = TraceContext::new(trace_id.clone(), span_id.clone());
    CURRENT_TRACE.with(|cell| {
        *cell.borrow_mut() = Some(ctx);
    });

    // Create the span and store it in STARTED_SPAN for span_enter() to pick up and enter.
    // This ensures start_span and span_enter share the SAME span (not two separate spans).
    let span = tracing::info_span!(
        "python_call",
        trace_id = %trace_id,
        span_id = %span_id,
        trace_flags = %traceflags,
        otel.name = %name,
        otel.kind = "internal",
    );
    STARTED_SPAN.with(|cell| {
        *cell.borrow_mut() = Some(span);
    });

    // Return the (possibly auto-generated) span_id as valid
    (trace_id, span_id, true)
}

/// Start a new span with explicit W3C TraceContext trace_id.
/// Stub for non-otel builds.
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn start_span(
    name: String,
    trace_id: String,
    span_id: String,
    traceflags: String,
) -> (String, String, bool) {
    let _ = name;
    let _ = trace_id;
    let _ = span_id;
    let _ = traceflags;
    (String::new(), String::new(), false)
}

/// Enter an existing span (make it the current span).
/// The span_id must have been returned by a previous start_span call.
///
/// Returns: true if span was entered, false if tracing disabled or span_id invalid.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn span_enter(trace_id: String, span_id: String) -> bool {
    if !is_tracing_enabled() {
        return false;
    }

    let trace_id = match parse_trace_id_hex(&trace_id) {
        Some(tid) => tid,
        None => return false,
    };
    let span_id = match parse_span_id_hex(&span_id) {
        Some(sid) => sid,
        None => return false,
    };

    // Store in TLS for get_current_trace_id/get_current_span_id
    let ctx = TraceContext::new(trace_id.clone(), span_id.clone());
    CURRENT_TRACE.with(|cell| {
        *cell.borrow_mut() = Some(ctx);
    });

    // Retrieve the span from start_span and enter it.
    // If start_span wasn't called (no span in STARTED_SPAN), create one as fallback.
    let entered = STARTED_SPAN.with(|cell| cell.borrow_mut().take().map(|span| span.enter()));

    // Store the guard in SPAN_GUARD so it survives until span_exit() is called.
    SPAN_GUARD.with(|cell| {
        let mut guard = cell);
        guard); // Drop any previous guard
        if let Some(e) = entered {
            *guard = Some(e);
        }
    });

    true
}

/// Enter an existing span (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn span_enter(trace_id: String, span_id: String) -> bool {
    let _ = trace_id;
    let _ = span_id;
    false
}

/// Exit the current span (end it as the current span).
/// Drops the SPAN_GUARD, which ends the entered span.
/// After this call, no span is the current span.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn span_exit() {
    SPAN_GUARD.with(|cell| {
        cell.borrow_mut()); // Drop EnteredSpan, span is exited
    });
    CURRENT_TRACE.with(|cell| {
        *cell.borrow_mut() = None;
    });
}

/// Exit the current span (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn span_exit() {}

/// Get current trace_id from TLS.
/// Returns (trace_id, is_valid).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_current_trace_id() -> (String, bool) {
    if !is_tracing_enabled() {
        return (String::new(), false);
    }

    CURRENT_TRACE.with(|cell| match cell.borrow().as_ref() {
        Some(ctx) if ctx.trace_id.len() == TRACE_ID_LEN => (ctx.trace_id.clone(), true),
        _ => (String::new(), false),
    })
}

/// Get current trace_id (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn get_current_trace_id() -> (String, bool) {
    (String::new(), false)
}

/// Get current span_id from TLS.
/// Returns (span_id, is_valid).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_current_span_id() -> (String, bool) {
    if !is_tracing_enabled() {
        return (String::new(), false);
    }

    CURRENT_TRACE.with(|cell| match cell.borrow().as_ref() {
        Some(ctx) if ctx.span_id.len() == SPAN_ID_LEN => (ctx.span_id.clone(), true),
        _ => (String::new(), false),
    })
}

/// Get current span_id (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn get_current_span_id() -> (String, bool) {
    (String::new(), false)
}

/// Record a custom event on the current span.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn add_span_event(name: String, _attributes_json: Option<String>) {
    use tracing::Span;

    if !is_tracing_enabled() {
        return;
    }

    let span = Span::current();
    if !span.is_none() {
        tracing::info!(event = %name, "span event");
    }
}

/// Record a custom event (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn add_span_event(name: String, _attributes_json: Option<String>) {
    let _ = name;
}

/// Generate a new random trace_id (32 hex chars).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn generate_trace_id() -> String {
    generate_trace_id_hex()
}

/// Generate a new random trace_id (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn generate_trace_id() -> String {
    String::new()
}

/// Generate a new random span_id (16 hex chars).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn generate_span_id() -> String {
    generate_span_id_hex()
}

/// Generate a new random span_id (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn generate_span_id() -> String {
    String::new()
}

/// Flush pending spans (no-op for stdout subscriber — stdout is synchronous).
#[pyfunction]
pub fn flush_tracing() {
    // stdout is synchronous, no flush needed
}

/// MODERN-CROSS-2: Async-aware span wrapper for Python asyncio ↔ Rust tokio bridging.
///
/// This provides a scoped span that properly handles async context switches.
/// When an async function awaits, the span remains active across the await point.
///
/// Usage:
/// ```python
/// from hledac.universal.rust_extensions.tracing import async_span_enter, async_span_exit
///
/// async def my_async_fn():
///     trace_id, span_id = async_span_enter("my_operation")
///     try:
///         result = await some_async_operation()
///         return result
///     finally:
///         async_span_exit(trace_id, span_id)
/// ```
///
/// Or with the context manager:
/// ```python
/// from hledac.universal.rust_extensions.tracing import AsyncSpan
///
/// async def my_async_fn():
///     with AsyncSpan("my_operation") as span:
///         result = await some_async_operation()
///         return result
/// ```

use std::sync::atomic::{AtomicU64, Ordering};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Instant;

/// Registry of active async spans for monitoring and cleanup.
/// MODERN-CROSS-2 FIX: Simplified to Mutex<HashMap> — no Option wrapping needed.
static ASYNC_SPAN_REGISTRY: Mutex<HashMap<String, (String, Instant, String)>> = 
    Mutex::new(HashMap::new());

fn get_async_span_registry() -> std::sync::MutexGuard<'static, HashMap<String, (String, Instant, String)>> {
    ASYNC_SPAN_REGISTRY.lock().unwrap()
}

/// Enter an async span with automatic context propagation.
///
/// This creates a span that will remain active across await points in async functions.
/// Unlike span_enter() which is synchronous-only, this stores the span in a registry
/// so it can be properly exited even after multiple await points.
///
/// Returns:
///   (trace_id, span_id, async_span_key) - tuple for tracking the async span
#[cfg(feature = "otel")]
#[pyfunction]
pub fn async_span_enter(name: String) -> (String, String, String) {
    use tracing::Span;

    if !is_tracing_enabled() {
        return (String::new(), String::new(), String::new());
    }

    // Generate a new span ID for this async operation
    let async_span_id = generate_span_id_hex();
    
    // Get current trace context if available
    let (trace_id, _) = get_current_trace_id();
    let trace_id = if trace_id.is_empty() {
        generate_trace_id_hex()
    } else {
        trace_id
    };

    let span = tracing::info_span!(
        "async:{}",
        name,
        operation = %name,
        trace_id = %trace_id,
        span_id = %async_span_id,
        is_async = true
    );
    let _entered = span);

    // Store in thread-local for TLS access
    STARTED_SPAN.with(|cell| {
        *cell.borrow_mut() = Some(span.clone());
    });
    
    // Store in global registry for async monitoring (MODERN-CROSS-2 FIX: simplified registry access)
    let key = format!("{}:{}", trace_id, async_span_id);
    let mut registry = get_async_span_registry();
    registry.insert(key.clone(), (async_span_id.clone(), Instant::now(), name.clone()));

    (trace_id, async_span_id, key)
}

/// Enter an async span (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn async_span_enter(name: String) -> (String, String, String) {
    let _ = name;
    (String::new(), String::new(), String::new())
}

/// Exit an async span by key.
///
/// This properly ends the span and removes it from the registry.
/// The async_span_key is the third element returned by async_span_enter().
#[cfg(feature = "otel")]
#[pyfunction]
pub fn async_span_exit(async_span_key: String, trace_id: String, span_id: String) {
    if !is_tracing_enabled() {
        return;
    }

    // Exit the span guard
    SPAN_GUARD.with(|cell| {
        cell.borrow_mut());
    });

    // Clear TLS context
    CURRENT_TRACE.with(|cell| {
        *cell.borrow_mut() = None;
    });

    // Remove from registry and compute duration (MODERN-CROSS-2 FIX: simplified registry access)
    let mut registry = get_async_span_registry();
    if let Some((_, start_time, name)) = registry.remove(&async_span_key) {
        let duration_ms = start_time.elapsed().as_millis() as u64;
        tracing::info!(
            operation = %name,
            duration_ms = %duration_ms,
            "async_span_completed"
        );
    }
}

/// Exit an async span (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn async_span_exit(async_span_key: String, trace_id: String, span_id: String) {
    let _ = async_span_key;
    let _ = trace_id;
    let _ = span_id;
}

/// Get the count of currently active async spans.
///
/// Useful for monitoring and debugging async operations.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_active_async_span_count() -> usize {
    let registry = get_async_span_registry();
    registry.as_ref().map(|m| m.len()).unwrap_or(0)
}

/// Get active async span count (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn get_active_async_span_count() -> usize {
    0
}

/// Get details of all active async spans (for debugging/monitoring).
///
/// Returns:
///   List of tuples: [(async_span_key, operation_name, elapsed_ms)]
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_active_async_spans() -> Vec<(String, String, u64)> {
    // MODERN-CROSS-2 FIX: Removed unused `to_remove` variable and simplified registry access
    let registry = get_async_span_registry();
    let now = Instant::now();
    let mut result = Vec::with_capacity(registry.len());
    
    // Collect stale spans (> 5 minutes old) for removal
    let mut stale_keys = Vec::new();
    
    for (key, (_, start_time, name)) in registry.iter() {
        let elapsed_ms = now.duration_since(*start_time).as_millis() as u64;
        result.push((key.clone(), name.clone(), elapsed_ms));
        
        // Mark spans older than 5 minutes as stale
        if elapsed_ms > 300_000 {
            stale_keys.push(key.clone());
        }
    }
    
    // Drop read guard before acquiring write lock
    drop(registry);
    
    // Remove stale entries (new guard acquired)
    if !stale_keys.is_empty() {
        let mut registry = get_async_span_registry();
        for key in stale_keys {
            registry.remove(&key);
        }
    }
    
    result
}

/// Get active async spans (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn get_active_async_spans() -> Vec<(String, String, u64)> {
    Vec::new()
}

#[cfg(feature = "otel")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let _ = init_tracing();

    m.add_function(wrap_pyfunction!(configure_tracing))?;
    m.add_function(wrap_pyfunction!(is_tracing_active))?;
    m.add_function(wrap_pyfunction!(start_span))?;
    m.add_function(wrap_pyfunction!(span_enter))?;
    m.add_function(wrap_pyfunction!(span_exit))?;
    m.add_function(wrap_pyfunction!(get_current_trace_id))?;
    m.add_function(wrap_pyfunction!(get_current_span_id))?;
    m.add_function(wrap_pyfunction!(add_span_event))?;
    m.add_function(wrap_pyfunction!(generate_trace_id))?;
    m.add_function(wrap_pyfunction!(generate_span_id))?;
    m.add_function(wrap_pyfunction!(flush_tracing))?;
    
    // MODERN-CROSS-2: Async span support
    m.add_function(wrap_pyfunction!(async_span_enter))?;
    m.add_function(wrap_pyfunction!(async_span_exit))?;
    m.add_function(wrap_pyfunction!(get_active_async_span_count))?;
    m.add_function(wrap_pyfunction!(get_active_async_spans))?;

    Ok(())
}

#[cfg(not(feature = "otel"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}

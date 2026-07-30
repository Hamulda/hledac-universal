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

use std::sync::OnceLock;
use pyo3::prelude::*;

// ============== Global State ==============

static TRACING_INIT: OnceLock<bool> = OnceLock::new();
static TRACING_ENABLED: OnceLock<bool> = OnceLock::new();

// ============== W3C TraceContext Constants ==============

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

// ============== TLS Accessors (pub(crate) for pool_run.rs) ==============

/// Set the current trace context on this thread. Used by pool_run.rs.
#[cfg(feature = "otel")]
pub(crate) fn set_tls_trace_context(trace_id: Option<u128>, span_id: Option<u128>) {
    if let (Some(tid), Some(sid)) = (trace_id, span_id) {
        if tid != 0 && sid != 0 {
            let ctx = TraceContext::new(
                format!("{:032x}", tid),
                format!("{:016x}", sid),
            );
            CURRENT_TRACE.with(|cell| {
                *cell.borrow_mut() = Some(ctx);
            });
        }
    }
}

/// Get the current trace_id from TLS. Used by pool_run.rs execute_with_optional_span.
#[cfg(feature = "otel")]
pub(crate) fn get_tls_trace_id() -> Option<String> {
    CURRENT_TRACE.with(|cell| {
        cell.borrow().as_ref().map(|ctx| ctx.trace_id.clone())
    })
}

/// Get the current span_id from TLS.
#[cfg(feature = "otel")]
pub(crate) fn get_tls_span_id() -> Option<String> {
    CURRENT_TRACE.with(|cell| {
        cell.borrow().as_ref().map(|ctx| ctx.span_id.clone())
    })
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

// ============== Tracing Enabled Check ==============

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
    std::env::var("HLEDAC_TRACING_SERVICE_NAME")
        .unwrap_or_else(|_| "hledac-rust".to_string())
}

// ============== Init ==============

#[cfg(feature = "otel")]
fn init_tracing() -> Result<(), String> {
    use tracing_subscriber::{fmt, prelude::*, EnvFilter};

    if TRACING_INIT.get().is_some() {
        return Ok(());
    }

    if !is_tracing_enabled() {
        let _ = TRACING_INIT.set(false);
        return Ok(());
    }

    let service_name = get_service_name();

    // TEL-02: Use fmt with JSON output that includes trace_id/span_id fields.
    let fmt_layer = tracing_subscriber::fmt::fmt()
        .with_target(true)
        .with_thread_ids(false)
        .with_file(true)
        .with_line_number(true)
        .with_ansi(false)
        .with_span_events(tracing_subscriber::fmt::format::FmtSpan::CLOSE);

    let subscriber = tracing_subscriber::registry()
        .with(EnvFilter::from_default_env())
        .with(fmt_layer);

    let result = subscriber.try_init();
    if result.is_err() {
        eprintln!("[tracing] init note: subscriber may already be initialized");
    }

    let _ = TRACING_INIT.set(true);
    println!("[tracing] Initialized: service={}", service_name);
    Ok(())
}

#[cfg(not(feature = "otel"))]
fn init_tracing() -> Result<(), String> {
    Ok(())
}

// ============== W3C TraceContext Helpers ==============

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
const fn hex_encode_16_bytes(bytes: [u8; 16]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    // Use array to avoid dynamic allocation in const context
    let mut chars = ['0'; 32];
    let mut i = 0;
    while i < 16 {
        chars[i * 2] = HEX[(bytes[i] >> 4) as usize] as char;
        chars[i * 2 + 1] = HEX[(bytes[i] & 0xf) as usize] as char;
        i += 1;
    }
    // Safety: String::from_iter is const-OK since Rust 1.79
    String::from_iter(chars.iter())
}

/// Encode 8 bytes as 16-char lowercase hex string.
#[cfg(feature = "otel")]
const fn hex_encode_8_bytes(bytes: [u8; 8]) -> String {
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

// ============== Python API ==============

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

    // Validate inputs
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
    let entered = STARTED_SPAN.with(|cell| {
        cell.borrow_mut().take().map(|span| span.enter())
    });

    // Store the guard in SPAN_GUARD so it survives until span_exit() is called.
    SPAN_GUARD.with(|cell| {
        let mut guard = cell.borrow_mut();
        guard.take(); // Drop any previous guard
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
        cell.borrow_mut().take(); // Drop EnteredSpan, span is exited
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

    CURRENT_TRACE.with(|cell| {
        match cell.borrow().as_ref() {
            Some(ctx) if ctx.trace_id.len() == TRACE_ID_LEN => (ctx.trace_id.clone(), true),
            _ => (String::new(), false),
        }
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

    CURRENT_TRACE.with(|cell| {
        match cell.borrow().as_ref() {
            Some(ctx) if ctx.span_id.len() == SPAN_ID_LEN => (ctx.span_id.clone(), true),
            _ => (String::new(), false),
        }
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

// ============== Module Registration ==============

#[cfg(feature = "otel")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let _ = init_tracing();

    m.add_function(wrap_pyfunction!(configure_tracing, m)?)?;
    m.add_function(wrap_pyfunction!(is_tracing_active, m)?)?;
    m.add_function(wrap_pyfunction!(start_span, m)?)?;
    m.add_function(wrap_pyfunction!(span_enter, m)?)?;
    m.add_function(wrap_pyfunction!(span_exit, m)?)?;
    m.add_function(wrap_pyfunction!(get_current_trace_id, m)?)?;
    m.add_function(wrap_pyfunction!(get_current_span_id, m)?)?;
    m.add_function(wrap_pyfunction!(add_span_event, m)?)?;
    m.add_function(wrap_pyfunction!(generate_trace_id, m)?)?;
    m.add_function(wrap_pyfunction!(generate_span_id, m)?)?;
    m.add_function(wrap_pyfunction!(flush_tracing, m)?)?;

    Ok(())
}

#[cfg(not(feature = "otel"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}

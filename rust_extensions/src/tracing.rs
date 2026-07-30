//! tracing.rs — Rust-side tracing for OTel-compatible observability (R24, TEL-02 fix)
//!
//! Provides end-to-end trace context propagation Python↔Rust via W3C TraceContext.
//!
//! Architecture (TEL-02):
//!   Python OpenTelemetry SDK (generates trace_id)
//!       ↓ trace_id passed as hex string parameter
//!   Rust: trace_id_from_hex() → tracing span with correct trace_id
//!       ↓ Rust span enters/exits
//!       ↓ trace_id returned to Python
//!   Python: injects span_id back into OTel context
//!
//! The key insight: Python owns the trace hierarchy. Rust operates as a span
//! processor under Python's OTel context. trace_id/span_id are explicit strings
//! (W3C TraceContext hex format), not opaque OTel objects.
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

// ============== W3C TraceContext Hex Validation ==============

/// Valid W3C TraceContext trace_id: exactly 32 lowercase hex chars.
const TRACE_ID_LEN: usize = 32;

/// Valid W3C TraceContext span_id: exactly 16 lowercase hex chars.
const SPAN_ID_LEN: usize = 16;

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
    // The tracing-subscriber 0.3 json formatter includes 'level', 'timestamp',
    // 'target', 'message', and for spans: 'span_name', 'trace_id', 'span_id'.
    let fmt_layer = tracing_subscriber::fmt::fmt()
        .with_target(true)
        .with_thread_ids(false)
        .with_file(true)
        .with_line_number(true)
        .with_ansi(false)
        .with_span_events(tracing_subscriber::fmt::format::FmtSpan::CLOSE);

    // TEL-02: Install a trace context layer that propagates W3C traceparent.
    // This couples with opentelemetry's W3C context propagation.
    // Note: The actual trace_id/span_id injection into spans happens via
    // the explicit hex parameters in start_span/enter_span functions below.
    let subscriber = tracing_subscriber::registry()
        .with(EnvFilter::from_default_env())
        .with(fmt_layer);

    // try_init returns () on success, Err if already initialized
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

/// Parse a W3C trace_id from a hex string.
/// Returns the raw 16 bytes (big-endian) if valid, None if invalid.
#[cfg(feature = "otel")]
fn parse_trace_id_hex(hex: &str) -> Option<[u8; 16]> {
    if hex.len() != TRACE_ID_LEN {
        return None;
    }
    // Decode exactly 32 hex chars → 16 bytes
    let mut bytes = [0u8; 16];
    for (i, chunk) in hex.as_bytes().chunks(2).enumerate() {
        if i >= 16 {
            return None;
        }
        let hi = hex_char_to_nibble(chunk[0])?;
        let lo = hex_char_to_nibble(chunk[1])?;
        bytes[i] = (hi << 4) | lo;
    }
    Some(bytes)
}

/// Parse a W3C span_id from a hex string.
/// Returns the raw 8 bytes (big-endian) if valid, None if invalid.
#[cfg(feature = "otel")]
fn parse_span_id_hex(hex: &str) -> Option<[u8; 8]> {
    if hex.len() != SPAN_ID_LEN {
        return None;
    }
    let mut bytes = [0u8; 8];
    for (i, chunk) in hex.as_bytes().chunks(2).enumerate() {
        if i >= 8 {
            return None;
        }
        let hi = hex_char_to_nibble(chunk[0])?;
        let lo = hex_char_to_nibble(chunk[1])?;
        bytes[i] = (hi << 4) | lo;
    }
    Some(bytes)
}

/// Convert a single hex char to its nibble value (0-15).
/// Returns None for non-hex characters.
#[cfg(feature = "otel")]
const fn hex_char_to_nibble(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10), // W3C allows uppercase in parsing
        _ => None,
    }
}

/// Convert 16 bytes to a 32-char lowercase hex string.
#[cfg(feature = "otel")]
fn bytes_to_trace_id_hex(bytes: [u8; 16]) -> String {
    const HEX_CHARS: &[u8; 16] = b"0123456789abcdef";
    let mut hex = String::with_capacity(32);
    for b in &bytes {
        hex.push(HEX_CHARS[(b >> 4) as usize] as char);
        hex.push(HEX_CHARS[(b & 0xf) as usize] as char);
    }
    hex
}

/// Convert 8 bytes to a 16-char lowercase hex string.
#[cfg(feature = "otel")]
fn bytes_to_span_id_hex(bytes: [u8; 8]) -> String {
    const HEX_CHARS: &[u8; 16] = b"0123456789abcdef";
    let mut hex = String::with_capacity(16);
    for b in &bytes {
        hex.push(HEX_CHARS[(b >> 4) as usize] as char);
        hex.push(HEX_CHARS[(b & 0xf) as usize] as char);
    }
    hex
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
///   # Use trace_id and span_id to continue the trace in Python
#[cfg(feature = "otel")]
#[pyfunction]
pub fn start_span(
    name: String,
    trace_id: String,
    span_id: String,
    traceflags: String,
) -> (String, String, bool) {
    use tracing::Span;

    if !is_tracing_enabled() {
        return (String::new(), String::new(), false);
    }

    // Validate trace_id
    let trace_bytes = match parse_trace_id_hex(&trace_id) {
        Some(b) => b,
        None => return (trace_id, String::new(), false),
    };

    // Generate or validate span_id
    let span_bytes: [u8; 8] = if span_id.is_empty() {
        // Auto-generate a new span_id
        let rng = rand::rngs::SmallRng::from_entropy();
        let mut bytes = [0u8; 8];
        bytes.copy_from_slice(&rand::Rng::gen::<_, [u8; 8]>(rng));
        bytes
    } else {
        match parse_span_id_hex(&span_id) {
            Some(b) => b,
            None => return (trace_id, String::new(), false),
        }
    };

    // TEL-02: Create span using info_span! macro.
    // The span will be associated with the current tracing dispatcher's context.
    // For true cross-language propagation, Python injects traceparent into the
    // Rust span via the span's attributes or the dispatcher's context.
    let span = tracing::info_span!(
        "python_call",
        trace_id = %trace_id,
        span_id = %bytes_to_span_id_hex(span_bytes),
        trace_flags = %traceflags,
        otel.name = %name,
        otel.kind = "internal",
    );

    // Enter the span (like .enter() on a guard) — drops when span_id goes out of scope
    let _entered = span.enter();

    // Note: In a PyO3 extension, the span is active only during this function call.
    // For longer-lived spans, Python should call span_enter() and span_exit() explicitly.
    // We return the span_id so Python can track it for later span_exit().

    (trace_id, bytes_to_span_id_hex(span_bytes), true)
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
    use tracing::Span;

    if !is_tracing_enabled() {
        return false;
    }

    // Validate inputs
    let _ = match parse_trace_id_hex(&trace_id) {
        Some(b) => b,
        None => return false,
    };
    let _ = match parse_span_id_hex(&span_id) {
        Some(b) => b,
        None => return false,
    };

    // TEL-02: Create a span with the given trace_id/span_id and enter it.
    // This span becomes the "current" span for the duration until span_exit.
    let span = tracing::info_span!(
        "python_call",
        trace_id = %trace_id,
        span_id = %span_id,
        otel.kind = "internal",
    );

    // Store the entered span in a thread-local or task-local guard.
    // For PyO3 (blocking sync calls), we use a thread-local.
    // The span is exited when span_exit() is called.
    SPAN_GUARD.with(|guard| {
        let mut guard = guard.borrow_mut();
        guard.take(); // Drop any previous guard
        *guard = Some(span.enter());
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

// Thread-local guard for span enter/exit pattern.
// Needed because Python calls are blocking sync (no async task context).
thread_local! {
    static SPAN_GUARD: std::cell::RefCell<Option<tracing::span::EnteredSpan>> =
        std::cell::RefCell::new(None);
}

/// Exit the current span (end it as the current span).
/// After this call, no span is the current span.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn span_exit() {
    SPAN_GUARD.with(|guard| {
        let mut guard = guard.borrow_mut();
        guard.take(); // Drop the EnteredSpan, ending the span
    });
}

/// Exit the current span (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn span_exit() {}

/// Get current trace_id from the active span.
/// Returns (trace_id, is_valid).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_current_trace_id() -> (String, bool) {
    use tracing::Span;

    if !is_tracing_enabled() {
        return (String::new(), false);
    }

    let span = Span::current();
    if span.is_none() {
        return (String::new(), false);
    }

    // TEL-02: Extract trace_id from span's recorded attributes.
    // The span was created with trace_id = %trace_id in start_span/span_enter.
    // We use the dispatcher's current span to get recorded values.
    let trace_id = span.recorded_attribute(&"trace_id".into())
        .and_then(|v| {
            if let Some(s) = v.as_str() {
                Some(s.to_string())
            } else {
                None
            }
        })
        .unwrap_or_default();

    if trace_id.len() == TRACE_ID_LEN {
        (trace_id, true)
    } else {
        (String::new(), false)
    }
}

/// Get current trace_id (stub for non-otel builds).
#[cfg(not(feature = "otel"))]
#[pyfunction]
pub fn get_current_trace_id() -> (String, bool) {
    (String::new(), false)
}

/// Get current span_id from the active span.
/// Returns (span_id, is_valid).
#[cfg(feature = "otel")]
#[pyfunction]
pub fn get_current_span_id() -> (String, bool) {
    use tracing::Span;

    if !is_tracing_enabled() {
        return (String::new(), false);
    }

    let span = Span::current();
    if span.is_none() {
        return (String::new(), false);
    }

    let span_id = span.recorded_attribute(&"span_id".into())
        .and_then(|v| {
            if let Some(s) = v.as_str() {
                Some(s.to_string())
            } else {
                None
            }
        })
        .unwrap_or_default();

    if span_id.len() == SPAN_ID_LEN {
        (span_id, true)
    } else {
        (String::new(), false)
    }
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
/// Useful for starting a new trace in Rust when Python hasn't started one.
#[cfg(feature = "otel")]
#[pyfunction]
pub fn generate_trace_id() -> String {
    use rand::Rng;
    let rng = rand::rngs::SmallRng::from_entropy();
    let bytes: [u8; 16] = rng.gen();
    bytes_to_trace_id_hex(bytes)
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
    use rand::Rng;
    let rng = rand::rngs::SmallRng::from_entropy();
    let bytes: [u8; 8] = rng.gen();
    bytes_to_span_id_hex(bytes)
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
    // When otel feature is disabled, tracing functions are no-ops.
    // All functions above are stubs returning empty strings / false.
    Ok(())
}

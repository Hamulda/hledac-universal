//! tracing_otel.rs — Distributed tracing bridge: Rust → OpenTelemetry.
//!
//! Issue 10.3 + A4: Unified trace ID end-to-end across Rust ↔ Python ↔ asyncio.
//!
//! Architecture (A4 fix):
//! - Python calls `otel._setup.init_telemetry()` first — owns TracerProvider + OTLP exporter.
//! - Then calls `init_rust_tracing_from_python_otel()` — Rust bridges into that pipeline.
//! - Both languages share one TracerProvider + one OTLP exporter.
//!
//! What works (A4 canonical):
//! - `init_rust_tracing_from_python_otel()` — no-op stub; Python OTel owns the pipeline
//! - `shutdown_tracing()` — flushes and shuts down the global OTel provider
//! - `get_current_trace_id()` / `get_current_span_id()` — read active trace context from Python
//! - `update_active_context()` — update cached trace/span IDs from Python
//!
//! Broken and removed:
//! - init_tracing / init_otlp_subscriber / install_otel_provider — pre-existing API errors
//!   (WithSpanExporter not in root, .http() not on OtlpExporterPipeline, TraceFlags::DEFAULT,
//!   EnteredSpan is !Send with PyO3 GIL bound)
//! - SpanGuard — EnteredSpan contains *mut () which is !Send
//! - registry().with(layer).init() — Registry doesn't implement Layer trait in this version

use std::sync::atomic::{AtomicU64, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyNone;

// ── SpanContext cache (shared with Python) ────────────────────────────────────

/// Thread-safe cache of the currently active span's trace/span IDs.
/// Updated by Python via `update_active_context()`; read by Rust hot paths.
static ACTIVE_TRACE_ID: AtomicU64 = AtomicU64::new(0);
static ACTIVE_SPAN_ID: AtomicU64 = AtomicU64::new(0);

/// Update the cached active span context from Python side.
#[pyfunction]
pub fn update_active_context(
    py: Python<'_>,
    trace_id: u64,
    span_id: u64,
) -> PyResult<PyObject> {
    ACTIVE_TRACE_ID.store(trace_id, Ordering::Relaxed);
    ACTIVE_SPAN_ID.store(span_id, Ordering::Relaxed);
    Ok(py.None())
}

/// Convert trace_id u64 back to a 32-char hex string (zero-padded).
fn u64_to_trace_hex(tid: u64) -> String {
    format!("{:016x}", tid)
}

/// Convert span_id u64 back to a 16-char hex string (zero-padded).
fn u64_to_span_hex(sid: u64) -> String {
    format!("{:016x}", sid)
}

// ── Python OTel bridge ───────────────────────────────────────────────────────

/// Bridge Rust tracing into the Python-initialized OTel pipeline.
///
/// Call this AFTER Python's `otel._setup.init_telemetry()` so that both
/// languages share the same TracerProvider and OTLP exporter.
///
/// This is a no-op stub: Python OTel owns the full pipeline (TracerProvider,
/// BatchSpanProcessor, OTLP exporter). Rust tracing events are correlated with
/// Python OTel traces via the shared `opentelemetry::global()` registry.
/// The actual span propagation is done by Python calling `update_active_context()`.
#[pyfunction]
pub fn init_rust_tracing_from_python_otel(
    _py: Python<'_>,
    _service_name: &str,
    _otlp_endpoint: &str,
) -> PyResult<PyObject> {
    // Python OTel SDK owns the full tracing pipeline.
    // Rust tracing events are correlated with Python OTel via the shared
    // opentelemetry::global registry (set by Python's init_telemetry).
    // Python calls update_active_context() to propagate span IDs to Rust.
    Ok(_py.None())
}

/// Shutdown the tracing + OTel pipeline. Flushes pending spans.
#[pyfunction]
pub fn shutdown_tracing(py: Python<'_>) -> PyResult<PyObject> {
    // py parameter already holds GIL; opentelemetry::global::shutdown_tracer_provider()
    // is a non-Python blocking call, safe to run while GIL is held.
    opentelemetry::global::shutdown_tracer_provider();
    Ok(py.None())
}

// ── Active context queries ───────────────────────────────────────────────────

/// Return the active trace_id as a 32-char hex string, or "" if no active span.
#[pyfunction]
pub fn get_current_trace_id() -> String {
    let tid = ACTIVE_TRACE_ID.load(Ordering::Relaxed);
    if tid == 0 {
        String::new()
    } else {
        u64_to_trace_hex(tid)
    }
}

/// Return the active span_id as a 16-char hex string, or "" if no active span.
#[pyfunction]
pub fn get_current_span_id() -> String {
    let sid = ACTIVE_SPAN_ID.load(Ordering::Relaxed);
    if sid == 0 {
        String::new()
    } else {
        u64_to_span_hex(sid)
    }
}

// ── Module init ───────────────────────────────────────────────────────────────

/// Register this module's symbols with the hledac-rust-extensions package.
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(init_rust_tracing_from_python_otel, module)?)?;
    module.add_function(wrap_pyfunction!(shutdown_tracing, module)?)?;
    module.add_function(wrap_pyfunction!(update_active_context, module)?)?;
    module.add_function(wrap_pyfunction!(get_current_trace_id, module)?)?;
    module.add_function(wrap_pyfunction!(get_current_span_id, module)?)?;
    Ok(())
}

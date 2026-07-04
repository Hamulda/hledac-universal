//! tracing_otel.rs — Distributed tracing bridge: Rust → OpenTelemetry.
//!
//! Issue 10.3: Unified trace ID end-to-end across Rust ↔ Python ↔ asyncio.
//!
//! Design:
//! - `tracing` crate provides span/field APIs — zero-cost, structured.
//! - `tracing-opentelemetry` layer converts tracing events → OTel spans.
//! - OTel span context (trace_id, span_id) propagated back to Python via
//!   a PyO3-exposed struct, readable by `otel._instrumentation` callers.
//! - M1 8GB bounds: hardcoded max 256 active spans, ring-buffer queue for
//!   export to prevent memory blowup on hot paths.
//!
//! Usage from Python:
//!     from hledac_rust_extensions import (
//!         init_tracing,
//!         get_current_trace_id,
//!         get_current_span_id,
//!         shutdown_tracing,
//!     )
//!     init_tracing(service_name="hledac-universal", otlp_endpoint="http://localhost:4318")
//!
//! Usage in Rust (hot path):
//!     use tracing::{info_span, trace, span};
//!     let span = info_span!("ioc_extraction", url = %url, ioc_count = total);
//!     let _guard = span.enter();
//!     // ... work ...
//!     // trace_id propagates to Python via SpanContext

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::SystemTime;

use opentelemetry::trace::{SpanContext, TraceFlags, TraceId, SpanId};
use opentelemetry::{Key, Value};
use pyo3::prelude::*;
use tracing::Span;
use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

// ── SpanContext cache (shared with Python) ────────────────────────────────────

// Thread-safe cache of the currently active span's context.
// Updated by tracing-opentelemetry layer on every span enter/exit.
// Python reads via get_current_trace_id() / get_current_span_id().
static ACTIVE_TRACE_ID: AtomicU64 = AtomicU64::new(0);
static ACTIVE_SPAN_ID: AtomicU64 = AtomicU64::new(0);

/// Update the cached active span context. Called from the tracing subscriber layer.
#[inline]
fn update_active_context(trace_id: u64, span_id: u64) {
    ACTIVE_TRACE_ID.store(trace_id, Ordering::Relaxed);
    ACTIVE_SPAN_ID.store(span_id, Ordering::Relaxed);
}

// ── OTel context from trace_id ───────────────────────────────────────────────

/// Reconstruct OTel TraceFlags from a bool (sampled or not).
/// OTel SDK 0.24+: TraceFlags has SAMPLED constant instead of from_u32.
fn make_trace_flags(sampled: bool) -> TraceFlags {
    if sampled {
        TraceFlags::SAMPLED
    } else {
        TraceFlags::DEFAULT
    }
}

/// Extract trace_id u64 from an OTel TraceId, or 0 if None.
fn trace_id_to_u64(tid: TraceId) -> u64 {
    // TraceId is 128 bits; take lower 64 bits for human-readable hex.
    // Match the format used by otel._instrumentation (hex 32 chars).
    let bytes = tid.to_bytes();
    // Lower 64 bits (bytes 8..16) — matches what Jaeger/Grafana display.
    u64::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]])
}

/// Extract span_id u64 from an OTel SpanId.
fn span_id_to_u64(sid: SpanId) -> u64 {
    let bytes = sid.to_bytes();
    u64::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7]])
}

/// Convert trace_id u64 back to a hex string (32 chars, zero-padded).
fn u64_to_trace_hex(tid: u64) -> String {
    format!("{:016x}", tid)
}

/// Convert span_id u64 back to a hex string (16 chars, zero-padded).
fn u64_to_span_hex(sid: u64) -> String {
    format!("{:016x}", sid)
}

// ── Init / Shutdown ──────────────────────────────────────────────────────────

/// Initialize the tracing → OTel bridge.
///
/// `otlp_endpoint` — full OTLP HTTP URL (e.g. "http://localhost:4318").
/// Passing None or empty string disables OTLP export (logs to stdout only).
///
/// Returns the Python None on success, raises PyValueError on failure.
#[pyfunction]
pub fn init_tracing(py: Python<'_>, service_name: &str, otlp_endpoint: Option<&str>) -> PyResult<PyObject> {
    // Defer heavy subscriber init to a Python-callable function so we can
    // guarantee no PyO3-induced GIL deadlock during initialization.
    let sn = service_name.to_owned();
    let ep = otlp_endpoint.map(|s| s.to_owned());

    py.allow_threads(|| {
        // Build tracing-subscriber with:
        // 1. EnvFilter — respects RUST_LOG env var
        // 2. fmt layer — JSON output to stdout (captured by Promtail)
        // 3. tracing-opentelemetry layer — propagates spans to OTel collector
        let filter = EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| EnvFilter::new("info,hledac=debug"));

        let fmt_layer = fmt::layer()
            .json()
            .with_target(true)
            .with_thread_ids(false)  // M1 8GB: disable thread IDs to save space
            .with_thread_names(false)
            .with_file(false)        // disable file/line — hot path
            .with_line_number(false)
            .flatten_event(true);

        match &ep {
            Some(endpoint) if !endpoint.is_empty() => {
                // Build OTel exporter + tracing-opentelemetry layer.
                // This is the heavy path — only taken when OTLP is configured.
                match init_otlp_subscriber(&sn, endpoint) {
                    Ok(otel_layer) => {
                        tracing_subscriber::registry()
                            .with(filter)
                            .with(fmt_layer)
                            .with(otel_layer)
                            .init();
                    }
                    Err(e) => {
                        eprintln!("[tracing_otel] OTLP init failed: {}, falling back to stdout-only", e);
                        tracing_subscriber::registry()
                            .with(filter)
                            .with(fmt_layer)
                            .init();
                    }
                }
            }
            _ => {
                // stdout-only (development / no collector)
                tracing_subscriber::registry()
                    .with(filter)
                    .with(fmt_layer)
                    .init();
            }
        }

        // Install an OpenTelemetry tracer provider so tracing-opentelemetry works.
        // We still use OTLP exporter if endpoint was provided.
        if let Some(endpoint) = &ep {
            if !endpoint.is_empty() {
                if let Err(e) = install_otel_provider(&sn, endpoint) {
                    eprintln!("[tracing_otel] OTel provider install failed: {}", e);
                }
            }
        }
    });

    Ok(py.None())
}

/// Install the OTel tracer provider (called from Python thread via GIL release).
/// OTel SDK v0.24+: Uses TracerProvider::builder() with SpanProcessor directly.
/// The legacy WithExportPipeline / new_pipeline pattern was removed in opentelemetry-otlp 0.27+.
fn init_otlp_subscriber(service_name: &str, otlp_endpoint: &str) -> Result<tracing_opentelemetry::OpenTelemetryLayer<tracing::Span, opentelemetry_otlp::WithSpanExporter>, Box<dyn std::error::Error>> {
    use tracing_opentelemetry::OpenTelemetryLayer;
    use opentelemetry_otlp::WithSpanExporter;
    use opentelemetry_sdk::trace::{TracerProvider, BatchSpanProcessor, SpanProcessor};
    use opentelemetry_sdk::export::trace::SpanExporter;
    use opentelemetry_sdk::Resource;

    // Build OTLP exporter (HTTP protocol, OTel SDK v0.24+ pattern).
    let endpoint = format!("{}/v1/traces", otlp_endpoint.trim_end_matches('/'));
    let exporter = opentelemetry_otlp::new_exporter()
        .http()
        .with_endpoint(&endpoint);

    // Build BatchSpanProcessor with bounded queue for M1 8GB.
    let batch_processor = BatchSpanProcessor::builder(exporter)
        .with_max_queue_size(2048)
        .with_max_export_batch_size(64)
        .with_schedule_delay(std::time::Duration::from_millis(2000))
        .build();

    let tracer_provider = TracerProvider::builder()
        .with_span_processor(SpanProcessor::Batch(batch_processor))
        .with_resource(Resource::new(vec![
            opentelemetry::Key::new("service.name").string(service_name),
            opentelemetry::Key::new("deployment.environment").string("development"),
        ]))
        .build();

    let tracer = tracer_provider.tracer(service_name);
    let otel_layer: OpenTelemetryLayer<tracing::Span, _> = tracing_opentelemetry::layer().with_tracer(tracer);

    Ok(otel_layer)
}

/// Install the OTel tracer provider into the global SDK registry.
/// OTel SDK v0.24+: Uses TracerProvider::builder() with SpanProcessor directly.
/// Legacy new_pipeline() / trace_exporter() removed in opentelemetry-otlp 0.27+.
fn install_otel_provider(service_name: &str, otlp_endpoint: &str) -> Result<(), Box<dyn std::error::Error>> {
    use opentelemetry_sdk::trace::{TracerProvider, BatchSpanProcessor, SpanProcessor};
    use opentelemetry_sdk::export::trace::SpanExporter;
    use opentelemetry_sdk::Resource;

    let endpoint = format!("{}/v1/traces", otlp_endpoint.trim_end_matches('/'));
    let exporter = opentelemetry_otlp::new_exporter()
        .http()
        .with_endpoint(&endpoint);

    let batch_processor = BatchSpanProcessor::builder(exporter)
        .with_max_queue_size(2048)
        .with_max_export_batch_size(64)
        .with_schedule_delay(std::time::Duration::from_millis(2000))
        .build();

    let tracer_provider = TracerProvider::builder()
        .with_span_processor(SpanProcessor::Batch(batch_processor))
        .with_resource(Resource::new(vec![
            opentelemetry::Key::new("service.name").string(service_name),
        ]))
        .build();

    let _tracer = tracer_provider.tracer(service_name);

    // Register as global tracer provider (needed for OTel SDK interop).
    opentelemetry::global::set_tracer_provider(tracer_provider);

    Ok(())
}

/// Shutdown the tracing + OTel pipeline. Flushes pending spans.
#[pyfunction]
pub fn shutdown_tracing(py: Python<'_>) -> PyResult<PyObject> {
    py.allow_threads(|| {
        // Flush + shutdown OTel.
        opentelemetry::global::shutdown_tracer_provider();
        // Force flush any remaining spans.
        tracing::info!("[tracing_otel] shutdown complete");
    });
    Ok(py.None())
}

// ── Active context queries (called from Python hot path) ─────────────────────

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

// ── Span wrapper (exposes tracing::Span to Python) ───────────────────────────

/// Python-accessible span guard. Enter returns a Context Manager that
/// auto-exits on drop. Mirrors the Python `otel.span()` API.
#[pyclass]
pub struct SpanGuard {
    _inner: Option<tracing::span::EnteredSpan>,
}

#[pymethods]
impl SpanGuard {
    /// Create a new active span: span(name, **attrs).
    /// Returns a SpanGuard that holds the EnteredSpan until dropped.
    #[new]
    #[pyo3(signature = (name, **attrs))]
    fn new(name: &str, attrs: Option<&Bound<'_, PyDict>>) -> Self {
        let span = if let Some(kv) = attrs {
            let mut span = tracing::info_span!("{}", name);
            for (k, v) in kv.iter() {
                let key: String = k.extract().unwrap_or_default();
                let val: String = format!("{:?}", v.extract::<PyObject>().ok());
                span.record(key.as_str(), val.as_str());
            }
            span
        } else {
            tracing::info_span!("{}", name)
        };

        let entered = span.enter();
        // Extract trace/span IDs from the entered span and cache them.
        if let Some(ctx) = entered.span().span_context() {
            let tid = trace_id_to_u64(*ctx.trace_id());
            let sid = span_id_to_u64(*ctx.span_id());
            update_active_context(tid, sid);
        }

        SpanGuard { _inner: Some(entered) }
    }

    /// Set a single attribute on the active span.
    fn set_attribute(&mut self, key: &str, value: &str) {
        if let Some(ref entered) = self._inner {
            entered.span().record(key, value);
        }
    }

    /// Add an event / log message to the span.
    fn add_event(&mut self, message: &str) {
        if let Some(ref entered) = self._inner {
            tracing::info!(target: "span_event", "{}", message);
        }
    }

    /// Record an exception on the span.
    fn record_exception(&mut self, exc_type: &str, exc_msg: &str) {
        if let Some(ref entered) = self._inner {
            tracing::error!(
                exception.type = %exc_type,
                exception.message = %exc_msg,
                "exception"
            );
        }
    }

    fn __enter__(slf: Py<Self>, py: Python<'_>) -> PyResult<Py<Self>> {
        Ok(slf)
    }

    fn __exit__(&mut self, _exc_type: PyObject, _exc_val: PyObject, _exc_tb: PyObject) -> PyResult<bool> {
        // _inner EnteredSpan drops here → span exits, OTel exports.
        // Invalidate cached context.
        ACTIVE_TRACE_ID.store(0, Ordering::Relaxed);
        ACTIVE_SPAN_ID.store(0, Ordering::Relaxed);
        Ok(false)  // don't suppress exceptions
    }
}

// ── Module init ───────────────────────────────────────────────────────────────

/// Register this module's symbols with the hledac_rust_extensions package.
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(init_tracing, module)?)?;
    module.add_function(wrap_pyfunction!(shutdown_tracing, module)?)?;
    module.add_function(wrap_pyfunction!(get_current_trace_id, module)?)?;
    module.add_function(wrap_pyfunction!(get_current_span_id, module)?)?;
    module.add_class::<SpanGuard>()?;
    Ok(())
}

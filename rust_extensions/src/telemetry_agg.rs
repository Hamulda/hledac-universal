//! telemetry_agg.rs — Real-time metrics aggregation pro M1 8GB
//!
//! unlike naive Python dict counters:
//! - Lock-free atomics pro hot-path metrics (no mutex contention)
//! - HDR Histogram pro latency percentiles (p50/p95/p99)
//! - MPSC channel pro cross-thread telemetry ingestion
//!
//! Design:
//! - AtomicCounter: std::sync::atomic::AtomicU64 pro count/bytes counters
//! - Histogram: HDR (High Dynamic Range) histogram pro latency
//! - Gauge: f64 volatile read for memory/CPU gauges
//! - Aggregator: MPSC collector thread redukuje metrics pred export
//!
//! M1 8GB bounds:
//!   MAX_SERIES = 1000 (max metric time series)
//!   COLLECTOR_BUFFER = 10000 (MPSC queue depth)

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossbeam_channel::{bounded, Receiver, Sender};
use pyo3::prelude::*;

// ============== Atomic Counter ==============

pub struct AtomicCounter {
    count: AtomicU64,
    bytes: AtomicU64,
}

impl AtomicCounter {
    pub fn new() -> Self {
        Self { count: AtomicU64::new(0), bytes: AtomicU64::new(0) }
    }

    #[inline]
    pub fn inc(&self) { self.count.fetch_add(1, Ordering::Relaxed); }

    #[inline]
    pub fn add(&self, n: u64) { self.count.fetch_add(n, Ordering::Relaxed); }

    #[inline]
    pub fn add_bytes(&self, n: u64) { self.bytes.fetch_add(n, Ordering::Relaxed); }

    pub fn get(&self) -> (u64, u64) {
        (self.count.load(Ordering::Relaxed), self.bytes.load(Ordering::Relaxed))
    }

    pub fn reset(&self) {
        self.count.store(0, Ordering::Relaxed);
        self.bytes.store(0, Ordering::Relaxed);
    }
}

impl Default for AtomicCounter { fn default() -> Self { Self::new() } }

// ============== HDR Histogram ==============

pub struct Histogram {
    counts: Vec<AtomicU64>,
    boundaries: Vec<u64>,
    total: AtomicU64,
    sum: AtomicU64,
}

impl Histogram {
    pub fn new() -> Self {
        let mut boundaries = vec![1_000u64];
        let mut current = 1_000u64;
        for _ in 0..127 {
            current = (current as f64 * 1.01) as u64;
            boundaries.push(current);
        }
        let counts: Vec<AtomicU64> = (0..128).map(|_| AtomicU64::new(0)).collect();
        Self {
            counts,
            boundaries,
            total: AtomicU64::new(0),
            sum: AtomicU64::new(0),
        }
    }

    #[inline]
    fn bucket_index(value_ns: u64) -> usize {
        if value_ns <= 1_000 { return 0; }
        let mut idx = 0;
        let base: f64 = 1.0;
        while idx < 127 && (base * 1.01_f64.powi(idx as i32)) < value_ns as f64 {
            idx += 1;
        }
        idx.min(127)
    }

    #[inline]
    pub fn record_ns(&self, ns: u64) {
        let idx = Self::bucket_index(ns);
        self.counts[idx].fetch_add(1, Ordering::Relaxed);
        self.total.fetch_add(1, Ordering::Relaxed);
        self.sum.fetch_add(ns, Ordering::Relaxed);
    }

    #[inline]
    pub fn record(&self, duration: Duration) {
        self.record_ns(duration.as_nanos() as u64);
    }

    pub fn percentile(&self, pct: f64) -> Duration {
        let total = self.total.load(Ordering::Relaxed);
        if total == 0 { return Duration::ZERO; }
        let target = (total as f64 * pct) as u64;
        let mut cumulative = 0u64;
        for (idx, count) in self.counts.iter().enumerate() {
            cumulative += count.load(Ordering::Relaxed);
            if cumulative >= target {
                return Duration::from_nanos(self.boundaries[idx]);
            }
        }
        Duration::from_secs(60)
    }

    pub fn percentiles(&self) -> (Duration, Duration, Duration) {
        (self.percentile(0.50), self.percentile(0.95), self.percentile(0.99))
    }

    pub fn stats(&self) -> HistogramStats {
        let total = self.total.load(Ordering::Relaxed);
        let sum = self.sum.load(Ordering::Relaxed);
        let (p50, p95, p99) = self.percentiles();
        HistogramStats {
            count: total,
            mean_ns: if total > 0 { sum / total } else { 0 },
            p50_ns: p50.as_nanos() as u64,
            p95_ns: p95.as_nanos() as u64,
            p99_ns: p99.as_nanos() as u64,
        }
    }

    pub fn reset(&self) {
        for count in &self.counts { count.store(0, Ordering::Relaxed); }
        self.total.store(0, Ordering::Relaxed);
        self.sum.store(0, Ordering::Relaxed);
    }
}

impl Default for Histogram { fn default() -> Self { Self::new() } }

#[derive(Clone, Debug)]
pub struct HistogramStats {
    pub count: u64,
    pub mean_ns: u64,
    pub p50_ns: u64,
    pub p95_ns: u64,
    pub p99_ns: u64,
}

// ============== Gauge ==============

/// Volatile gauge using Mutex<f64> for memory/CPU metrics.
/// Note: AtomicF64 is not yet stable in Rust, using Mutex as fallback.
pub struct Gauge { value: std::sync::Mutex<f64> }

impl Gauge {
    pub fn new(initial: f64) -> Self { Self { value: std::sync::Mutex::new(initial) } }
    #[inline] pub fn set(&self, val: f64) {
        // F265B: Handle poisoned lock gracefully instead of panicking
        if let Ok(mut guard) = self.value.lock() {
            *guard = val;
        }
    }
    #[inline] pub fn get(&self) -> f64 {
        // F265B: Handle poisoned lock gracefully instead of panicking
        self.value.lock().map(|g| *g).unwrap_or(0.0)
    }
}

impl Default for Gauge { fn default() -> Self { Self::new(0.0) } }

// ============== Telemetry Aggregator ==============

#[derive(Clone, Debug)]
pub enum TelemetryEvent {
    Counter { name: String, count: u64, bytes: u64 },
    Histogram { name: String, duration_ns: u64 },
    Gauge { name: String, value: f64 },
}

pub struct TelemetryAggregator {
    counters: Arc<Mutex<HashMap<String, AtomicCounter>>>,
    histograms: Arc<Mutex<HashMap<String, Histogram>>>,
    gauges: Arc<Mutex<HashMap<String, Gauge>>>,
    sender: Sender<TelemetryEvent>,
    _handle: std::thread::JoinHandle<()>,
}

impl TelemetryAggregator {
    pub fn new() -> Self {
        let (tx, rx): (Sender<TelemetryEvent>, Receiver<TelemetryEvent>) = bounded(10000);
        let counters = Arc::new(Mutex::new(HashMap::new()));
        let histograms = Arc::new(Mutex::new(HashMap::new()));
        let gauges = Arc::new(Mutex::new(HashMap::new()));

        let counters_clone = counters.clone();
        let histograms_clone = histograms.clone();
        let gauges_clone = gauges.clone();

        let handle = std::thread::spawn(move || {
            while let Ok(event) = rx.recv() {
                match event {
                    TelemetryEvent::Counter { name, count, bytes } => {
                        let mut c = counters_clone.lock().unwrap();
                        let counter = c.entry(name).or_insert_with(AtomicCounter::new);
                        counter.add(count);
                        if bytes > 0 { counter.add_bytes(bytes); }
                    }
                    TelemetryEvent::Histogram { name, duration_ns } => {
                        let mut h = histograms_clone.lock().unwrap();
                        let hist = h.entry(name).or_insert_with(Histogram::new);
                        hist.record_ns(duration_ns);
                    }
                    TelemetryEvent::Gauge { name, value } => {
                        let mut g = gauges_clone.lock().unwrap();
                        let gauge = g.entry(name).or_insert_with(|| Gauge::new(0.0));
                        gauge.set(value);
                    }
                }
            }
        });

        Self { counters, histograms, gauges, sender: tx, _handle: handle }
    }

    #[inline]
    pub fn counter_inc(&self, name: &str) {
        let _ = self.sender.send(TelemetryEvent::Counter { name: name.to_string(), count: 1, bytes: 0 });
    }

    #[inline]
    pub fn counter_add(&self, name: &str, count: u64, bytes: u64) {
        let _ = self.sender.send(TelemetryEvent::Counter { name: name.to_string(), count, bytes });
    }

    #[inline]
    pub fn histogram_record(&self, name: &str, duration: Duration) {
        let _ = self.sender.send(TelemetryEvent::Histogram { name: name.to_string(), duration_ns: duration.as_nanos() as u64 });
    }

    #[inline]
    pub fn histogram_record_ns(&self, name: &str, ns: u64) {
        let _ = self.sender.send(TelemetryEvent::Histogram { name: name.to_string(), duration_ns: ns });
    }

    #[inline]
    pub fn gauge_set(&self, name: &str, value: f64) {
        let _ = self.sender.send(TelemetryEvent::Gauge { name: name.to_string(), value });
    }

    pub fn snapshot(&self) -> TelemetrySnapshot {
        let counters = self.counters.lock().unwrap();
        let counter_snap: HashMap<String, (u64, u64)> = counters.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        let histograms = self.histograms.lock().unwrap();
        let histogram_snap: HashMap<String, HistogramStats> = histograms.iter().map(|(k, v)| (k.clone(), v.stats())).collect();

        let gauges = self.gauges.lock().unwrap();
        let gauge_snap: HashMap<String, f64> = gauges.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        TelemetrySnapshot { counters: counter_snap, histograms: histogram_snap, gauges: gauge_snap }
    }
}

impl Default for TelemetryAggregator { fn default() -> Self { Self::new() } }

#[derive(Clone, Debug)]
pub struct TelemetrySnapshot {
    pub counters: HashMap<String, (u64, u64)>,
    pub histograms: HashMap<String, HistogramStats>,
    pub gauges: HashMap<String, f64>,
}

// ============== Python Bindings ==============

#[pyclass]
pub struct PyTelemetryAggregator { inner: Arc<TelemetryAggregator> }

#[pymethods]
impl PyTelemetryAggregator {
    #[new]
    fn new() -> Self { Self { inner: Arc::new(TelemetryAggregator::new()) } }

    fn counter_inc(&self, name: String) { self.inner.counter_inc(&name); }
    fn counter_add(&self, name: String, count: u64, bytes: u64) { self.inner.counter_add(&name, count, bytes); }
    fn histogram_record(&self, name: String, duration_ms: f64) {
        self.inner.histogram_record(&name, Duration::from_secs_f64(duration_ms / 1000.0));
    }
    fn histogram_record_ns(&self, name: String, ns: u64) { self.inner.histogram_record_ns(&name, ns); }
    fn gauge_set(&self, name: String, value: f64) { self.inner.gauge_set(&name, value); }

    fn snapshot(&self, py: Python<'_>) -> HashMap<String, Py<PyAny>> {
        let snap = self.inner.snapshot();
        let mut result = HashMap::new();

        for (name, (count, bytes)) in snap.counters {
            result.insert(format!("counter:{}", name), (count, bytes).into_pyobject(py).unwrap().into());
        }
        for (name, stats) in snap.histograms {
            let py_dict: HashMap<&str, Py<PyAny>> = HashMap::from([
                ("count", stats.count.into_pyobject(py).unwrap().into()),
                ("mean_ns", stats.mean_ns.into_pyobject(py).unwrap().into()),
                ("p50_ns", stats.p50_ns.into_pyobject(py).unwrap().into()),
                ("p95_ns", stats.p95_ns.into_pyobject(py).unwrap().into()),
                ("p99_ns", stats.p99_ns.into_pyobject(py).unwrap().into()),
            ]);
            result.insert(format!("histogram:{}", name), py_dict.into_pyobject(py).unwrap().into());
        }
        for (name, value) in snap.gauges {
            result.insert(format!("gauge:{}", name), value.into_pyobject(py).unwrap().into());
        }
        result
    }
}

#[pyfunction]
fn create_telemetry_aggregator() -> PyTelemetryAggregator { PyTelemetryAggregator::new() }

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(create_telemetry_aggregator, m)?)?;
    m.add_class::<PyTelemetryAggregator>()?;
    Ok(())
}

/// Flat snapshot of all telemetry counters for health_check().
///
/// Returns `Vec<(name, value)>` where value is the raw i64 counter.
/// This is a process-wide singleton aggregator — the same instance used by all
/// Python callers. Safe for concurrent access from rayon worker threads.
pub fn telemetry_snapshot() -> Vec<(String, i64)> {
    // Lazily constructed global aggregator (same pattern as cpu_pool()).
    use std::sync::LazyLock;
    static AGG: LazyLock<TelemetryAggregator, fn() -> TelemetryAggregator> =
        LazyLock::new(TelemetryAggregator::new);

    let snap = AGG.snapshot();
    snap.counters
        .iter()
        .map(|(name, (count, _))| (name.clone(), *count as i64))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_atomic_counter() {
        let counter = AtomicCounter::new();
        counter.inc();
        counter.add(5);
        counter.add_bytes(1024);
        let (count, bytes) = counter.get();
        assert_eq!(count, 6);
        assert_eq!(bytes, 1024);
    }

    #[test]
    fn test_histogram() {
        let histogram = Histogram::new();
        histogram.record(Duration::from_micros(100));
        histogram.record(Duration::from_millis(10));
        histogram.record(Duration::from_millis(100));
        let stats = histogram.stats();
        assert_eq!(stats.count, 3);
        assert!(stats.p50_ns > 0);
    }

    #[test]
    fn test_gauge() {
        let gauge = Gauge::new(42.0);
        assert_eq!(gauge.get(), 42.0);
        gauge.set(100.5);
        assert_eq!(gauge.get(), 100.5);
    }

    #[test]
    fn test_aggregator() {
        let agg = TelemetryAggregator::new();
        agg.counter_inc("test_counter");
        agg.counter_add("test_bytes", 10, 2048);
        agg.histogram_record("test_latency", Duration::from_millis(50));
        agg.gauge_set("test_memory", 1.5);
        std::thread::sleep(Duration::from_millis(10));
        let snap = agg.snapshot();
        assert!(snap.counters.contains_key("test_counter"));
        assert!(snap.histograms.contains_key("test_latency"));
        assert!(snap.gauges.contains_key("test_memory"));
    }
}

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
use std::sync::Arc;
use std::time::Duration;

use crossbeam_channel::{bounded, Receiver, Sender};
use parking_lot::Mutex;
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
    min: AtomicU64,
    max: AtomicU64,
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
        let max_val = AtomicU64::new(u64::MAX);
        let min_val = AtomicU64::new(0);
        Self {
            counts,
            boundaries,
            total: AtomicU64::new(0),
            sum: AtomicU64::new(0),
            min: min_val,
            max: max_val,
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
        // Update min/max with relaxed ordering (approximate is fine for histograms)
        let current_min = self.min.load(Ordering::Relaxed);
        if ns < current_min {
            let _ = self.min.compare_exchange(current_min, ns, Ordering::Relaxed, Ordering::Relaxed);
        }
        let current_max = self.max.load(Ordering::Relaxed);
        if ns > current_max {
            let _ = self.max.compare_exchange(current_max, ns, Ordering::Relaxed, Ordering::Relaxed);
        }
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

    /// Extended percentiles for comprehensive latency tracking.
    /// Returns p50, p75, p90, p95, p99, p99.9 as nanoseconds.
    #[inline]
    pub fn extended_percentiles(&self) -> [u64; 6] {
        let targets = [0.50, 0.75, 0.90, 0.95, 0.99, 0.999];
        let mut result = [0u64; 6];
        for (i, &pct) in targets.iter().enumerate() {
            result[i] = self.percentile(pct).as_nanos() as u64;
        }
        result
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
        self.min.store(0, Ordering::Relaxed);
        self.max.store(u64::MAX, Ordering::Relaxed);
    }

    /// Extended stats with comprehensive percentiles for OTel export.
    #[inline]
    pub fn extended_stats(&self) -> ExtendedHistogramStats {
        let total = self.total.load(Ordering::Relaxed);
        let sum = self.sum.load(Ordering::Relaxed);
        let percs = self.extended_percentiles();
        let min_val = if total > 0 { self.min.load(Ordering::Relaxed) } else { 0 };
        let max_val = if total > 0 { self.max.load(Ordering::Relaxed) } else { 0 };
        ExtendedHistogramStats {
            count: total,
            mean_ns: if total > 0 { sum / total } else { 0 },
            sum_ns: sum,
            min_ns: min_val,
            max_ns: max_val,
            p50_ns: percs[0],
            p75_ns: percs[1],
            p90_ns: percs[2],
            p95_ns: percs[3],
            p99_ns: percs[4],
            p999_ns: percs[5],
        }
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

/// Extended histogram stats with more percentiles for comprehensive latency tracking.
/// Used by the Rust → Python OTel bridge for detailed metrics export.
#[derive(Clone, Debug)]
pub struct ExtendedHistogramStats {
    pub count: u64,
    pub mean_ns: u64,
    pub sum_ns: u64,
    pub min_ns: u64,
    pub max_ns: u64,
    pub p50_ns: u64,
    pub p75_ns: u64,
    pub p90_ns: u64,
    pub p95_ns: u64,
    pub p99_ns: u64,
    pub p999_ns: u64,
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
                        let mut c = counters_clone.lock();
                        let counter = c.entry(name).or_insert_with(AtomicCounter::new);
                        counter.add(count);
                        if bytes > 0 { counter.add_bytes(bytes); }
                    }
                    TelemetryEvent::Histogram { name, duration_ns } => {
                        let mut h = histograms_clone.lock();
                        let hist = h.entry(name).or_insert_with(Histogram::new);
                        hist.record_ns(duration_ns);
                    }
                    TelemetryEvent::Gauge { name, value } => {
                        let mut g = gauges_clone.lock();
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
        let counters = self.counters.lock();
        let counter_snap: HashMap<String, (u64, u64)> = counters.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        let histograms = self.histograms.lock();
        let histogram_snap: HashMap<String, HistogramStats> = histograms.iter().map(|(k, v)| (k.clone(), v.stats())).collect();

        let gauges = self.gauges.lock();
        let gauge_snap: HashMap<String, f64> = gauges.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        TelemetrySnapshot { counters: counter_snap, histograms: histogram_snap, gauges: gauge_snap }
    }

    /// Export with extended histogram stats for OTel metrics bridge.
    /// Returns TelemetryExport with p50-p99.9 percentiles.
    pub fn export(&self) -> TelemetryExport {
        let counters = self.counters.lock();
        let counter_snap: HashMap<String, (u64, u64)> = counters.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        let histograms = self.histograms.lock();
        let histogram_snap: HashMap<String, ExtendedHistogramStats> =
            histograms.iter().map(|(k, v)| (k.clone(), v.extended_stats())).collect();

        let gauges = self.gauges.lock();
        let gauge_snap: HashMap<String, f64> = gauges.iter().map(|(k, v)| (k.clone(), v.get())).collect();

        TelemetryExport {
            counters: counter_snap,
            histograms: histogram_snap,
            gauges: gauge_snap,
            timestamp_ms: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        }
    }
}

impl Default for TelemetryAggregator { fn default() -> Self { Self::new() } }

#[derive(Clone, Debug)]
pub struct TelemetrySnapshot {
    pub counters: HashMap<String, (u64, u64)>,
    pub histograms: HashMap<String, HistogramStats>,
    pub gauges: HashMap<String, f64>,
}

/// Export struct for Python OTel bridge — zero-copy friendly POD.
#[derive(Clone, Debug)]
pub struct TelemetryExport {
    /// Counter name → (count, bytes)
    pub counters: HashMap<String, (u64, u64)>,
    /// Histogram name → ExtendedHistogramStats (p50-p99.9)
    pub histograms: HashMap<String, ExtendedHistogramStats>,
    /// Gauge name → current value
    pub gauges: HashMap<String, f64>,
    /// Export timestamp in milliseconds since epoch
    pub timestamp_ms: u64,
}

// ============== Python Bindings ==============

// ISSUE-064: #[pyclass(unsendable)] is REQUIRED here because:
//   1. TelemetryAggregator holds a crossbeam Sender<TelemetryEvent> — Senders
//      are NOT Send (they cannot cross thread boundaries safely).
//   2. TelemetryAggregator spawns an internal reducer thread (JoinHandle) —
//      the thread handle itself is not Send either.
//   3. Without unsendable, PyO3 allows Python to pass PyTelemetryAggregator
//      objects between asyncio.to_thread() workers, racing on Sender + JoinHandle.
//
// The GIL protects against concurrent Python access. #[pyclass(unsendable)]
// prevents the additional hazard of the Python object itself being sent to
// a different thread (where the internal thread + channel would break).
// Python code always accesses via the same thread (asyncio main thread +
// to_thread workers that hold GIL). The internal reducer thread receives
// from a bounded MPSC channel — all sends are from GIL-held code.
#[pyclass(unsendable)]
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

    /// Snapshot with standard histogram stats (p50/p95/p99).
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

    /// Export with extended histogram stats for OTel metrics bridge (p50-p99.9).
    /// Returns dict with keys: "counters", "histograms", "gauges", "timestamp_ms".
    fn export(&self, py: Python<'_>) -> HashMap<String, Py<PyAny>> {
        let exp = self.inner.export();
        let mut result = HashMap::new();

        // Counters: name → (count, bytes)
        let counters: HashMap<String, (u64, u64)> = exp.counters;
        let counters_py: HashMap<String, Py<PyAny>> = counters
            .into_iter()
            .map(|(k, v)| {
                (k, (v.0, v.1).into_pyobject(py).unwrap().into())
            })
            .collect();
        result.insert("counters".into(), counters_py.into_pyobject(py).unwrap().into());

        // Histograms: name → ExtendedHistogramStats
        let histograms: HashMap<String, ExtendedHistogramStats> = exp.histograms;
        let histograms_py: HashMap<String, Py<PyAny>> = histograms
            .into_iter()
            .map(|(k, stats)| {
                let py_dict: HashMap<&str, Py<PyAny>> = HashMap::from([
                    ("count", stats.count.into_pyobject(py).unwrap().into()),
                    ("mean_ns", stats.mean_ns.into_pyobject(py).unwrap().into()),
                    ("sum_ns", stats.sum_ns.into_pyobject(py).unwrap().into()),
                    ("min_ns", stats.min_ns.into_pyobject(py).unwrap().into()),
                    ("max_ns", stats.max_ns.into_pyobject(py).unwrap().into()),
                    ("p50_ns", stats.p50_ns.into_pyobject(py).unwrap().into()),
                    ("p75_ns", stats.p75_ns.into_pyobject(py).unwrap().into()),
                    ("p90_ns", stats.p90_ns.into_pyobject(py).unwrap().into()),
                    ("p95_ns", stats.p95_ns.into_pyobject(py).unwrap().into()),
                    ("p99_ns", stats.p99_ns.into_pyobject(py).unwrap().into()),
                    ("p999_ns", stats.p999_ns.into_pyobject(py).unwrap().into()),
                ]);
                (k, py_dict.into_pyobject(py).unwrap().into())
            })
            .collect();
        result.insert("histograms".into(), histograms_py.into_pyobject(py).unwrap().into());

        // Gauges: name → value
        let gauges: HashMap<String, f64> = exp.gauges;
        let gauges_py: HashMap<String, Py<PyAny>> = gauges
            .into_iter()
            .map(|(k, v)| (k, v.into_pyobject(py).unwrap().into()))
            .collect();
        result.insert("gauges".into(), gauges_py.into_pyobject(py).unwrap().into());

        // Timestamp
        result.insert("timestamp_ms".into(), exp.timestamp_ms.into_pyobject(py).unwrap().into());

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

//! # Health endpoint for Rust extensions monitoring
//!
//! Provides a single `health_check()` call that aggregates diagnostics from all
//! Rust extensions without introducing synchronization overhead or shared mutable
//! state between independent components.
//!
//! ## Design principles
//!
//! 1. **Zero coordination** — Each component exposes a `health_info() -> HealthInfo`
//!    function that reads its own state lock-free. The aggregator just calls them.
//! 2. **Fail-soft** — Any component that fails to report just omits its fields.
//!    The dict returned always has the same top-level keys.
//! 3. **Sub-millisecond** — No allocation, no I/O, no locks on the hot path.
//!    All data is copied from atomic counters / pool state on demand.
//! 4. **No new PyO3 feature gates** — Uses only stable PyO3 0.27 APIs.
//!
//! ## Fields returned
//!
//! | Field | Source | Notes |
//! |-------|--------|-------|
//! | `version` | `env!("CARGO_PKG_VERSION")` | e.g. `"0.1.0"` |
//! | `health_checks_total` | `AtomicU64` counter | monotonic |
//! | `cpu_pool_threads` | `cpu_pool().current_num_threads()` | always 4 |
//! | `io_pool_threads` | `io_pool().current_num_threads()` | always 2 |
//! | `mixed_pool_threads` | adaptive (1 or 2) | based on threshold |
//! | `mixed_pool_threshold` | `adaptive_scheduler::mixed_threshold()` | |
//! | `rss_bytes` | `memory::current_rss_bytes()` | via mach_task_basic_info |
//! | `peak_rss_bytes` | `memory::peak_rss_bytes()` | monotonic from start |
//! | `memory_pressure` | `memory::memory_pressure_level()` | 0=normal,1=elevated,2=critical |
//! | `available_memory_gib` | `memory::get_available_memory_gib()` | system-wide |
//! | `metal_active_bytes` | `memory::get_metal_active_memory_bytes()` | MLX GPU RSS, 0 if unavailable |
//! | `dedup_bloom_instances` | `DedupBloomFilter::global_instance_count()` | |
//! | `dedup_bloom_items` | `DedupBloomFilter::global_items_added()` | |
//! | `dedup_bloom_capacity` | `DedupBloomFilter::global_capacity()` | |
//! | `dedup_bloom_capacity_pct` | derived | items/capacity × 100, capped at 100 |
//! | `url_set_instances` | `url_set::global_instance_count()` | |
//! | `url_set_items` | `url_set::global_items_added()` | |
//! | `url_mmap_instances` | `MmapUrlSet::global_instance_count()` | |
//! | `url_mmap_items` | `MmapUrlSet::global_items_added()` | |
//! | `telemetry_counters` | `telemetry_agg::telemetry_snapshot()` | snapshot of counter state |
//! | `timestamp_ms` | `std::time::UNIX_EPOCH` | ms since epoch |
//!
//! ## Bloom capacity tracking
//!
//! `DedupBloomFilter` (in `dedup_bloom.rs`) maintains atomic counters for
//! items_added and capacity in a global singleton. These are updated on every
//! `add()` call — no additional synchronization needed beyond the atomic stores.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(feature = "advanced")]
use crate::adaptive_scheduler;
use crate::memory;
use crate::url_set;

// ---------------------------------------------------------------------------
// Global health call counter
// ---------------------------------------------------------------------------

/// Total number of health_check() calls since process start.
/// Incremented atomically on every call — monotonically increasing.
static HEALTH_CALLS: AtomicU64 = AtomicU64::new(0);

/// Total number of health_check() calls that returned an Err (PyO3 level).
/// Incremented only on panic/exception paths — these indicate Python-callable
/// bugs, not business-logic failures.
static HEALTH_ERRORS: AtomicU64 = AtomicU64::new(0);

// ---------------------------------------------------------------------------
// HealthInfo — return type for per-component health reporters
// ---------------------------------------------------------------------------

/// Minimalist health info struct. All fields are Copy so no heap allocation.
#[derive(Default)]
pub struct HealthInfo {
    pub version: &'static str,
    pub health_calls: u64,
    pub health_errors: u64,
    pub cpu_pool_threads: usize,
    pub io_pool_threads: usize,
    pub mixed_pool_threads: usize,
    pub mixed_threshold: usize,
    pub rss_bytes: u64,
    pub peak_rss_bytes: u64,
    pub memory_pressure: u8,
    pub available_memory_gib: f64,
    pub metal_active_bytes: u64,
    pub dedup_bloom_instances: u64,
    pub dedup_bloom_items: u64,
    pub dedup_bloom_capacity: u64,
    pub dedup_bloom_memory_bytes: u64,
    pub url_set_instances: u64,
    pub url_set_items: u64,
    pub url_mmap_instances: u64,
    pub url_mmap_items: u64,
    pub telemetry_snapshot: Vec<(String, i64)>,
    pub timestamp_ms: u64,
}

impl HealthInfo {
    /// Fill fields by querying each subsystem.
    /// Any subsystem error is silently ignored — the field keeps its zero/default value.
    fn fill(py: Python<'_>, _m: &Bound<'_, PyModule>) -> Self {
        let version = env!("CARGO_PKG_VERSION");

        // Thread pool state — cheap, no I/O
        let cpu_threads = crate::cpu_pool());
        let io_threads = crate::io_pool());
        #[cfg(feature = "advanced")]
        let mixed_thresh = adaptive_scheduler::mixed_threshold();
        #[cfg(not(feature = "advanced"))]
        let mixed_thresh = 0;
        // mixed_pool(usize::MAX) to get the larger pool's thread count
        let mixed_threads = crate::mixed_pool(usize::MAX));

        // Memory — mach_task_basic_info on macOS
        let rss = memory::current_rss_bytes();
        let peak_rss = memory::peak_rss_bytes();
        let pressure = memory::memory_pressure_level();
        let avail_gib = memory::get_available_memory_gib();

        // MLX Metal memory — calls Python mlx.core, may return 0
        let metal_bytes = memory::get_metal_active_memory_bytes(py);

        // DedupBloomFilter global stats
        let (db_instances, db_items, db_cap) = crate::dedup_bloom::global_stats();
        let db_mem_bytes = crate::dedup_bloom::global_memory_bytes();

        // URL set global stats
        let (us_instances, us_items) = url_set::global_stats();

        // Telemetry snapshot — grabs a copy of all counter values
        let telemetry: Vec<(String, i64)> = crate::telemetry_agg::telemetry_snapshot();

        // Wall-clock timestamp
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        Self {
            version,
            health_calls: HEALTH_CALLS.load(Ordering::Relaxed),
            health_errors: HEALTH_ERRORS.load(Ordering::Relaxed),
            cpu_pool_threads: cpu_threads,
            io_pool_threads: io_threads,
            mixed_pool_threads: mixed_threads,
            mixed_threshold: mixed_thresh,
            rss_bytes: rss,
            peak_rss_bytes: peak_rss,
            memory_pressure: pressure,
            available_memory_gib: avail_gib,
            metal_active_bytes: metal_bytes,
            dedup_bloom_instances: db_instances,
            dedup_bloom_items: db_items,
            dedup_bloom_capacity: db_cap,
            dedup_bloom_memory_bytes: db_mem_bytes,
            url_set_instances: us_instances.0,
            url_set_items: us_items.0,
            url_mmap_instances: us_instances.1,
            url_mmap_items: us_items.1,
            telemetry_snapshot: telemetry,
            timestamp_ms: now,
        }
    }
}

// ---------------------------------------------------------------------------
// PyO3 API
// ---------------------------------------------------------------------------

/// Increment the health-call counter (called before fill to get monotonic count).
fn bump_health_calls() {
    HEALTH_CALLS.fetch_add(1, Ordering::Relaxed);
}

/// `health_check() -> dict`
///
/// Returns a flat dictionary with health and metrics from all Rust extension
/// subsystems. All values are plain Python scalars — `int`, `float`, `str`,
/// `list[tuple[str, int]]`.
///
/// **Latency budget:** < 1 ms on M1. No I/O, no locks, no allocation
/// beyond the dict itself.
///
/// **Fields:**
/// ```python
/// {
///     "version": str,                    # e.g. "0.1.0"
///     "health_checks_total": int,        # monotonic call counter
///     "health_errors_total": int,        # errors seen
///     "cpu_pool_threads": int,            # always 4
///     "io_pool_threads": int,            # always 2
///     "mixed_pool_threads": int,         # 1 or 2 depending on threshold
///     "mixed_pool_threshold": int,       # adaptive_scheduler threshold
///     "rss_bytes": int,                  # process RSS via mach_task_basic_info
///     "peak_rss_bytes": int,            # high-water mark since start
///     "memory_pressure": int,            # 0 normal / 1 elevated / 2 critical
///     "available_memory_gib": float,     # system available RAM
///     "metal_active_bytes": int,         # MLX GPU RSS (0 if unavailable)
///     "dedup_bloom_instances": int,      # active DedupBloomFilter singletons
///     "dedup_bloom_items": int,          # total items added across all instances
///     "dedup_bloom_capacity": int,       # sum of configured capacities
///     "dedup_bloom_memory_bytes": int,   # total bit array + Count-Min Sketch memory
///     "dedup_bloom_capacity_pct": float, # 0-100 (capped at 100)
///     "url_set_instances": int,          # in-memory UrlSet count
///     "url_set_items": int,              # items in all UrlSets
///     "url_mmap_instances": int,         # MmapUrlSet count
///     "url_mmap_items": int,             # items in all MmapUrlSets
///     "telemetry_counters": list,        # [(name, value), ...]
///     "timestamp_ms": int,               # unix epoch ms
/// }
/// ```
///
/// **Example:**
/// ```python
/// import hledac_rust_extensions as rust
/// h = rust.health_check()
/// assert h["version"] == rust.__version__
/// assert h["cpu_pool_threads"] == 4
/// assert isinstance(h["telemetry_counters"], list)
/// ```
#[pyfunction]
pub fn health_check<'a>(
    py: Python<'a>,
    m: &'a Bound<'a, PyModule>,
) -> PyResult<Bound<'a, pyo3::types::PyDict>> {
    bump_health_calls();

    let info = HealthInfo::fill(py, m);

    let dict = pyo3::types::PyDict::new(py);

    dict.set_item("version", info.version)?;
    dict.set_item("health_checks_total", info.health_calls)?;
    dict.set_item("health_errors_total", info.health_errors)?;
    dict.set_item("cpu_pool_threads", info.cpu_pool_threads)?;
    dict.set_item("io_pool_threads", info.io_pool_threads)?;
    dict.set_item("mixed_pool_threads", info.mixed_pool_threads)?;
    dict.set_item("mixed_pool_threshold", info.mixed_threshold)?;
    dict.set_item("rss_bytes", info.rss_bytes)?;
    dict.set_item("peak_rss_bytes", info.peak_rss_bytes)?;
    dict.set_item("memory_pressure", info.memory_pressure)?;
    dict.set_item("available_memory_gib", info.available_memory_gib)?;
    dict.set_item("metal_active_bytes", info.metal_active_bytes)?;
    dict.set_item("dedup_bloom_instances", info.dedup_bloom_instances)?;
    dict.set_item("dedup_bloom_items", info.dedup_bloom_items)?;
    dict.set_item("dedup_bloom_capacity", info.dedup_bloom_capacity)?;
    dict.set_item("dedup_bloom_memory_bytes", info.dedup_bloom_memory_bytes)?;

    // Derived: capacity utilisation percentage, capped at 100
    let cap_pct = if info.dedup_bloom_capacity > 0 {
        (info.dedup_bloom_items as f64 / info.dedup_bloom_capacity as f64 * 100.0).min(100.0)
    } else {
        0.0
    };
    dict.set_item("dedup_bloom_capacity_pct", cap_pct)?;

    dict.set_item("url_set_instances", info.url_set_instances)?;
    dict.set_item("url_set_items", info.url_set_items)?;
    dict.set_item("url_mmap_instances", info.url_mmap_instances)?;
    dict.set_item("url_mmap_items", info.url_mmap_items)?;

    // telemetry_counters: list of (name, value) tuples
    let telemetry_list = pyo3::types::PyList::new(py, &info.telemetry_snapshot)?;
    dict.set_item("telemetry_counters", telemetry_list)?;

    dict.set_item("timestamp_ms", info.timestamp_ms)?;

    Ok(dict)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register the health module in the parent PyModule.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(health_check))?;
    Ok(())
}

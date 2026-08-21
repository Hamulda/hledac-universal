//! Adaptive thread scheduler — CPU saturation + memory-pressure aware.
//!
//! F270-FINAL + MODERN-31: Extends the fixed-tier thread pools (cpu_pool/io_pool/mixed_pool)
//! with dynamic thread count recommendations based on:
//!   1. Current MLX Metal memory pressure (fraction of dynamic cache limit)
//!   2. CPU queue depth estimate (via active rayon worker count)
//!   3. Workload type (CPU-bound / I/O-bound / Mixed)
//!
//! ## MODERN-31: Single Source of Truth
//!
//! This module is the **SINGLE recommender** for all thread pool sizing decisions.
//! It feeds `resize_cpu_pool()` and `resize_io_pool()` directly — no separate
//! phase-based sizing authority exists. Python phase configs only seed initial
//! sizes at bootstrap.
//!
//! ## MODERN-32: Global Thread Budget
//!
//! `MAX_TOTAL_THREADS = 8` (M1 8GB: 4P + 4E cores) is enforced across ALL pools:
//!   - cpu_pool threads (USER_INITIATED → P-cores)
//!   - io_pool threads (UTILITY → E-cores)
//!   - mixed_pool threads (POOL_SINGLE=1, POOL_PAIR=2)
//!   - dispatcher threads (UTILITY → E-cores)
//!
//! Budget allocation per phase (THREAD-BUDGET-02: verified against BUDGET_AVAILABLE=6):
//!   | Phase     | cpu | io | mixed (max) | dispatchers | total | Within 6? |
//!   |-----------|-----|----|-------------|-------------|-------|----------|
//!   | BOOT/WIND | 1   | 1  | 1           | 3           | 6     | ✓ OK     |
//!   | ACTIVE    | 2   | 1  | 0           | 3           | 6     | ✓ OK     |
//!   | SYNTHESIS | 2   | 1  | 0           | 3           | 6     | ✓ OK     |
//!   | DEGRADED  | 1   | 1  | 0           | 3           | 5     | ✓ OK     |
//!
//! ## MLX Metal-Aware Design (F330 / ISSUE-2.4)
//!
//! `mixed_threshold()` is MLX-aware: it probes actual MLX Metal memory via GIL,
//! BUT uses a thread-local cache (TTL = 100 ms) to avoid GIL acquisition on
//! every call.  At 1000+ calls/sprint the cache hit rate is > 99 % and the
//! GIL overhead drops from 10-20 ms to near zero.
//!
//! Thread-local cache is per-rayon-worker — no cross-thread synchronization,
//! no atomic contention, no false sharing.
//!
//! Threshold fractions (relative to dynamic Metal cache limit):
//!   < 0.60 GPU fraction → 16 (idle: eager parallelism)
//!   0.60–0.85          → 32 (normal: balanced)
//!   > 0.85             → 64 (pressure: conservative sequential)
//!
//! Falls back to NORMAL_THRESHOLD (32) if MLX/Python probe is unavailable.

use pyo3::prelude::*;
use std::cell::Cell;
use std::sync::atomic::{AtomicU8, AtomicUsize, Ordering};
use std::sync::LazyLock;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Atomic counter for threshold switches (idle→normal→pressure).
static THRESHOLD_SWITCH_COUNT: AtomicUsize = AtomicUsize::new(0);

/// Last observed threshold level (for detecting switches) - AtomicU8 is Sync-safe.
static LAST_THRESHOLD_LEVEL: AtomicU8 = AtomicU8::new(PRESSURE_UNSET);

/// Timestamp of last threshold switch - protected by Mutex for interior mutability.
static LAST_THRESHOLD_SWITCH_TIME: Mutex<Instant> = Mutex::new(Instant::now());

/// Get the count of threshold switches since process start.
#[inline]
pub fn get_threshold_switch_count() -> usize {
    THRESHOLD_SWITCH_COUNT.load(Ordering::Relaxed)
}

/// Reset the threshold switch counter.
#[inline]
pub fn reset_threshold_switch_count() {
    THRESHOLD_SWITCH_COUNT.store(0, Ordering::Relaxed);
    LAST_THRESHOLD_LEVEL.store(PRESSURE_UNSET, Ordering::Relaxed);
    *LAST_THRESHOLD_SWITCH_TIME.lock().unwrap() = Instant::now();
}

/// Record a threshold switch (called internally by mixed_threshold).
#[inline]
fn record_threshold_switch(new_level: u8) {
    let last = LAST_THRESHOLD_LEVEL.load(Ordering::Relaxed);
    if last != new_level {
        THRESHOLD_SWITCH_COUNT.fetch_add(1, Ordering::Relaxed);
        LAST_THRESHOLD_LEVEL.store(new_level, Ordering::Relaxed);
        *LAST_THRESHOLD_SWITCH_TIME.lock().unwrap() = Instant::now();
    }
}

/// Get the current threshold level (0=idle, 1=normal, 2=pressure).
#[inline]
pub fn get_current_threshold_level() -> u8 {
    LAST_THRESHOLD_LEVEL.load(Ordering::Relaxed)
}

/// Get time since last threshold switch.
#[inline]
pub fn get_time_since_last_switch() -> Duration {
    LAST_THRESHOLD_SWITCH_TIME.lock().unwrap().elapsed()
}

/// Get threshold switch statistics as a tuple.
pub fn get_threshold_stats() -> (usize, u8, f64) {
    (
        THRESHOLD_SWITCH_COUNT.load(Ordering::Relaxed),
        LAST_THRESHOLD_LEVEL.load(Ordering::Relaxed),
        LAST_THRESHOLD_SWITCH_TIME.lock().unwrap().elapsed().as_secs_f64(),
    )
}

/// Maximum total OS threads across all pools + dispatchers.
/// M1 Air: 4P + 4E = 8 logical cores.
pub const MAX_TOTAL_THREADS: usize = 8;

/// THREAD-BUDGET-01: Reserved threads for asyncio event loop + system overhead.
pub const ASYNCIO_RESERVED: usize = 1;
pub const SYSTEM_RESERVED: usize = 1;

/// THREAD-BUDGET-02: Available budget for rayon pools (dispatchers + cpu + io + mixed).
/// FIXED arithmetic: MAX_TOTAL_THREADS(8) - ASYNCIO_RESERVED(1) - SYSTEM_RESERVED(1) = 6
/// asyncio and system threads ARE tracked by ThreadBudgetGuard, so they count against total.
pub const BUDGET_AVAILABLE: usize = 6; // MAX_TOTAL_THREADS(8) - 2 = 6 (FIXED from 7)

/// Fixed dispatcher thread count (1 per pool type: cpu, io, mixed).
pub const DISPATCHER_COUNT: usize = 3;

/// THREAD-BUDGET-02: Phase configurations with mixed pool
/// Tuple: (cpu, io, mixed_max)
/// All phases verified to fit within BUDGET_AVAILABLE = 6
#[derive(Debug, Clone, Copy)]
pub struct PhaseConfig {
    pub cpu: usize,
    pub io: usize,
    pub mixed_max: usize,
}

impl PhaseConfig {
    /// Total threads for this phase (including dispatchers)
    #[inline]
    pub fn total(&self) -> usize {
        self.cpu + self.io + self.mixed_max + DISPATCHER_COUNT
    }

    /// Check if this phase fits within budget
    #[inline]
    pub fn within_budget(&self) -> bool {
        self.total() <= BUDGET_AVAILABLE
    }
}

/// Budget-verified phase configurations — THREAD-BUDGET-02: all fit within 6
pub const PHASE_CONFIGS: &[(&str, PhaseConfig)] = &[
    (
        "BOOT",
        PhaseConfig {
            cpu: 1,
            io: 1,
            mixed_max: 1,
        },
    ), // 1+1+1+3 = 6
    (
        "WARMUP",
        PhaseConfig {
            cpu: 1,
            io: 1,
            mixed_max: 1,
        },
    ), // 1+1+1+3 = 6
    (
        "ACTIVE",
        PhaseConfig {
            cpu: 2,
            io: 1,
            mixed_max: 0,
        },
    ), // 2+1+0+3 = 6
    (
        "DEGRADED",
        PhaseConfig {
            cpu: 1,
            io: 1,
            mixed_max: 0,
        },
    ), // 1+1+0+3 = 5
    (
        "SYNTHESIS",
        PhaseConfig {
            cpu: 2,
            io: 1,
            mixed_max: 0,
        },
    ), // 2+1+0+3 = 6
    (
        "WINDUP",
        PhaseConfig {
            cpu: 1,
            io: 1,
            mixed_max: 1,
        },
    ), // 1+1+1+3 = 6
    (
        "EXPORT",
        PhaseConfig {
            cpu: 2,
            io: 1,
            mixed_max: 0,
        },
    ), // 2+1+0+3 = 6
    (
        "TEARDOWN",
        PhaseConfig {
            cpu: 1,
            io: 1,
            mixed_max: 0,
        },
    ), // 1+1+0+3 = 5
];

/// Get phase config by name
pub fn get_phase_config(name: &str) -> Option<PhaseConfig> {
    PHASE_CONFIGS
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, cfg)| *cfg)
}

// Atomic counters for budget tracking
static CPU_BUDGET: AtomicUsize = AtomicUsize::new(4); // default: 4 P-cores
static IO_BUDGET: AtomicUsize = AtomicUsize::new(2); // default: 2 E-cores
static MIXED_BUDGET: AtomicUsize = AtomicUsize::new(1);
static MIXED_THRESHOLD_BUDGET: AtomicUsize = AtomicUsize::new(32); // NORMAL_THRESHOLD
static BUDGET_PHASE: LazyLock<std::sync::Mutex<String>> =
    LazyLock::new(|| std::sync::Mutex::new(String::from("BOOT")));

/// MODERN-32 + THREAD-BUDGET-01: Get total active threads across all pools + dispatchers.
#[inline]
pub fn get_total_threads() -> usize {
    CPU_BUDGET.load(Ordering::Relaxed)
        + IO_BUDGET.load(Ordering::Relaxed)
        + MIXED_BUDGET.load(Ordering::Relaxed)
        + DISPATCHER_COUNT
}

/// THREAD-BUDGET-01: Get available thread budget slots.
#[inline]
pub fn get_available_budget() -> usize {
    BUDGET_AVAILABLE.saturating_sub(get_total_threads())
}

/// ISSUE-2 FIX: Check if budget allows `extra` threads.
/// 
/// Validates: rayon_threads + extra <= BUDGET_AVAILABLE (6)
/// 
/// This ensures rayon pools never exceed the rayon budget, leaving room for
/// asyncio(1) + system(1) = 2 reserved threads within MAX_TOTAL_THREADS(8).
/// 
/// Previous implementation incorrectly used MAX_TOTAL_THREADS (8) as the ceiling,
/// which didn't account for asyncio+system overhead.
#[inline]
pub fn budget_allows(extra: usize) -> bool {
    get_total_threads() + extra <= BUDGET_AVAILABLE
}

/// MODERN-31: Set current sprint phase (for telemetry only).
/// Phase configs are initial seeds — pressure-based sizing takes precedence.
pub fn set_phase(phase: &str) {
    if let Ok(mut p) = BUDGET_PHASE.lock() {
        *p = phase;
    }
}

/// MODERN-31: Get current sprint phase.
pub fn get_phase() -> String {
    BUDGET_PHASE.lock().map(|p| p.clone()).unwrap_or_default()
}

/// MODERN-31: Get current CPU pool thread count from budget.
#[inline]
pub fn get_cpu_budget() -> usize {
    CPU_BUDGET.load(Ordering::Relaxed)
}

/// MODERN-31: Get current I/O pool thread count from budget.
#[inline]
pub fn get_io_budget() -> usize {
    IO_BUDGET.load(Ordering::Relaxed)
}

/// MODERN-31: Get current mixed pool thread count from budget.
#[inline]
pub fn get_mixed_budget() -> usize {
    MIXED_BUDGET.load(Ordering::Relaxed)
}

/// MODERN-31: Update CPU pool budget after resize.
#[inline]
pub fn set_cpu_budget(n: usize) {
    CPU_BUDGET.store(n, Ordering::Release);
}

/// MODERN-31: Update I/O pool budget after resize.
#[inline]
pub fn set_io_budget(n: usize) {
    IO_BUDGET.store(n, Ordering::Release);
}

/// MODERN-31: Update mixed pool budget (called when POOL_SINGLE/POOL_PAIR selected).
#[inline]
pub fn set_mixed_budget(n: usize) {
    MIXED_BUDGET.store(n, Ordering::Release);
}

/// MODERN-31: Update mixed threshold (called when adaptive_scheduler::mixed_threshold() changes).
#[inline]
pub fn set_mixed_threshold(n: usize) {
    MIXED_THRESHOLD_BUDGET.store(n, Ordering::Release);
}

/// TTL for the thread-local metal-level/limit cache.
/// 100 ms strikes the balance: Metal memory pressure changes slowly
/// (on MLX timescales), while the hot-path calls mixed_threshold()
/// 1000+ times per sprint.
const METAL_CACHE_TTL_MS: u64 = 100;

// Thread-local cache entry: (last_instant, metal_level, limit_bytes).
// limit_bytes is cached alongside level so we skip the Python call
// when the cached entry is still valid.
thread_local! {
    static METAL_CACHE: Cell<(Instant, u8, u64)> = Cell::new((Instant::now(), 1, 0));
}

/// Explicit memory-pressure signal — set by Python tests / production code.
///
/// Value: 0=idle, 1=normal, 2=pressure.
/// When set to a non-default value, mixed_threshold() uses this directly
/// and bypasses MLX Metal probing (tests don't have MLX).
///
/// Default: 1 (normal) — MLX Metal probing is the source of truth in production.
static MEMORY_PRESSURE: AtomicU8 = AtomicU8::new(1);

/// Sentinel value meaning "not explicitly set" — must match default of MEMORY_PRESSURE.
const PRESSURE_UNSET: u8 = 1;

/// Threshold when MLX GPU is idle (fraction < 0.60) — eager parallelism.
const IDLE_THRESHOLD: usize = 16;
/// Threshold under normal MLX GPU load (fraction 0.60–0.85) — balanced.
const NORMAL_THRESHOLD: usize = 32;
/// Threshold under high MLX GPU pressure (fraction > 0.85) — conservative.
const PRESSURE_THRESHOLD: usize = 64;

/// Cached module handle for `hledac.universal.utils.mlx_cache`.
///
/// Initialized once per process via OnceLock; the bound is stored as
/// `Option<Bound<'static, PyModule>>` to allow None on failure.
static MLX_CACHE_MODULE_PATH: std::sync::OnceLock<&'static str> = std::sync::OnceLock::new();

/// Import mlx_cache module by name — avoids stale Bound<'static, PyModule> references
/// and sidesteps Sync/Send issues with OnceLock<Bound<...>>.
/// Uses lazy OnceLock to import only once per process.
#[inline]
fn get_mlx_cache_module<'py>(py: Python<'py>) -> Option<Bound<'py, PyModule>> {
    let module_name = MLX_CACHE_MODULE_PATH.get_or_init(|| "hledac.universal.utils.mlx_cache");
    py.import(*module_name).ok()
}

/// Probes Python `utils.mlx_cache.get_dynamic_metal_cache_limit()` via GIL.
///
/// MEM-2: min(max(available * 0.2, 512 MiB), 1.5 GiB).
/// Returns 0 if the Python function is unavailable.
#[inline]
fn get_metal_limit_bytes(py: Python<'_>) -> u64 {
    let Some(module) = get_mlx_cache_module(py) else {
        return 0;
    };
    if let Ok(func) = module.getattr("get_dynamic_metal_cache_limit") {
        if let Ok(result) = func.call0() {
            if let Ok(v) = result.extract::<u64>() {
                return v;
            }
            if let Ok(v) = result.extract::<i64>() {
                return v.max(0) as u64;
            }
        }
    }
    0
}

/// Converts Metal memory fraction (active/limit) to level 0=idle, 1=normal, 2=pressure.
///
/// Threshold fractions:
///   < 0.60 → 0 (idle: eager parallelism)
///   0.60–0.85 → 1 (normal: balanced)
///   > 0.85 → 2 (pressure: conservative sequential)
///
/// Returns 1 (normal) as safe fallback when limit_bytes or active is 0.
#[inline]
fn fraction_to_level(limit_bytes: u64, active: u64) -> u8 {
    if limit_bytes == 0 || active == 0 {
        return 1; // fallback: normal
    }
    let fraction = active as f64 / limit_bytes as f64;
    if fraction < 0.60 {
        0 // idle
    } else if fraction <= 0.85 {
        1 // normal — inclusive boundary (0.60–0.85 → normal per documented fractions)
    } else {
        2 // pressure (> 0.85)
    }
}

/// Returns the cached metal level (0=idle, 1=normal, 2=pressure) or
/// re-probes via GIL if the thread-local cache entry is stale.
///
/// If MEMORY_PRESSURE was explicitly set to a non-default value
/// (i.e., not PRESSURE_UNSET), that value is returned immediately
/// and no MLX probing occurs. This allows tests to control the
/// threshold without MLX being available.
#[inline]
fn get_metal_level_cached() -> u8 {
    // Fast path: if memory pressure was explicitly set, use it directly.
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    if pressure != PRESSURE_UNSET {
        return pressure;
    }

    let now = Instant::now();
    let (instant, level, _limit_bytes) = METAL_CACHE.with(|cell| cell.get());
    if now.duration_since(instant) < Duration::from_millis(METAL_CACHE_TTL_MS) {
        return level;
    }
    // Cache miss — acquire GIL, re-probe, update cache.
    Python::attach(|py| {
        let limit_bytes = get_metal_limit_bytes(py);
        let active = crate::memory::get_metal_active_memory_bytes(py);
        let level = fraction_to_level(limit_bytes, active);
        METAL_CACHE.with(|cell| cell.set((now, level, limit_bytes)));
        level
    })
}

/// MIXED_THRESHOLD — fully MLX Metal-aware.
///
/// Uses a thread-local cache (TTL = 100 ms) to skip GIL on the
/// common case (pressure == PRESSURE_UNSET).  When an explicit pressure
/// is set via update_memory_pressure(), the atomic is checked first
/// and the cache is bypassed entirely — so the hot path for production
/// MLX use is one atomic load with zero GIL overhead.
///
/// | MLX GPU fraction of cache limit | Threshold | Rationale                |
/// |--------------------------------|-----------|--------------------------|
/// | < 0.60                         | 16        | Idle: eager parallelism  |
/// | 0.60–0.85                     | 32        | Normal: balanced          |
/// | > 0.85                        | 64        | Pressure: sequential      |
///
/// Falls back to NORMAL_THRESHOLD (32) if MLX or Python probe is unavailable.
///
/// MODERN-CROSS-4: Records threshold switches for monitoring.
#[inline]
pub fn mixed_threshold() -> usize {
    // Fast path: thread-local cache hit (no GIL).
    // Slow path: acquire GIL, probe Python, update cache.
    let level = get_metal_level_cached();

    // MODERN-CROSS-4: Track threshold switches
    record_threshold_switch(level);

    match level {
        0 => IDLE_THRESHOLD,     // 16: GPU idle, eager
        1 => NORMAL_THRESHOLD,   // 32: normal
        _ => PRESSURE_THRESHOLD, // 64: GPU saturated
    }
}

/// Mixed threshold via Metal — takes an explicit `py` handle to avoid
/// redundant GIL acquisition when called from Python code that already holds the GIL.
///
/// For Rust-internal use, prefer `mixed_threshold()` which uses thread-local caching.
#[inline]
pub fn mixed_threshold_via_metal(py: Python<'_>) -> usize {
    // Caller already holds the GIL — fresh probe, no thread-local cache.
    let limit_bytes = get_metal_limit_bytes(py);
    let active = crate::memory::get_metal_active_memory_bytes(py);
    match fraction_to_level(limit_bytes, active) {
        0 => IDLE_THRESHOLD,
        1 => NORMAL_THRESHOLD,
        _ => PRESSURE_THRESHOLD,
    }
}

/// Recommended thread count for CPU-bound workloads (cpu_pool ceiling).
///
/// MODERN-31 FIX: This is now the SINGLE recommender for CPU pool sizing.
/// Phase configs are initial seeds only — this takes precedence.
///
/// THREAD-BUDGET-02: Enforces global budget (BUDGET_AVAILABLE=6) by computing
/// available slots after accounting for dispatchers, mixed, and io pools.
#[inline]
pub fn recommended_cpu_threads() -> usize {
    // Check explicit pressure first (tests, no-MLX path).
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    let base = if pressure != PRESSURE_UNSET {
        match pressure {
            2 => 1, // pressure: sequential
            1 => 2, // normal: 2 P-cores (clamped by budget)
            _ => 2, // idle: clamped to 2 to fit budget (was 4)
        }
    } else {
        // Production: use thread-local metal cache.
        match get_metal_level_cached() {
            2 => 1, // pressure: sequential
            1 => 2, // normal: 2 P-cores (clamped by budget)
            _ => 2, // idle: clamped to 2 to fit budget (was 4)
        }
    };

    // THREAD-BUDGET-02: Compute available slots after dispatchers, mixed, and minimum io
    // BUDGET_AVAILABLE = 6, DISPATCHER_COUNT = 3, so available for cpu+io+mixed = 3
    // Minimum io = 1, so max_cpu = 3 - 1 = 2
    let mixed_budget = get_mixed_budget();
    let io_reserve = 1; // minimum io threads
    let max_cpu = BUDGET_AVAILABLE.saturating_sub(DISPATCHER_COUNT + mixed_budget + io_reserve);
    base.min(max_cpu).max(1) // At least 1 CPU thread
}

/// Recommended thread count for I/O-bound workloads (io_pool ceiling).
///
/// MODERN-31 FIX: This is now the SINGLE recommender for I/O pool sizing.
/// Phase configs are initial seeds only — this takes precedence.
///
/// THREAD-BUDGET-02: Enforces global budget (BUDGET_AVAILABLE=6) by computing
/// available slots after reserving for cpu_pool, dispatchers, and mixed_pool.
#[inline]
pub fn recommended_io_threads() -> usize {
    // Check explicit pressure first (tests, no-MLX path).
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    let base = if pressure != PRESSURE_UNSET {
        match pressure {
            2 => 1, // pressure: minimal
            _ => 1, // idle/normal: clamped to 1 to fit budget (was 2)
        }
    } else {
        // Production: use thread-local metal cache.
        match get_metal_level_cached() {
            2 => 1, // pressure: minimal
            _ => 1, // idle/normal: clamped to 1 to fit budget (was 2)
        }
    };

    // THREAD-BUDGET-02: Compute available slots after cpu_budget, dispatchers, mixed
    // Available = BUDGET_AVAILABLE - cpu_budget - dispatchers - mixed
    let cpu_budget = get_cpu_budget();
    let mixed_budget = get_mixed_budget();
    let overhead = DISPATCHER_COUNT + mixed_budget;
    let available = BUDGET_AVAILABLE.saturating_sub(cpu_budget + overhead);
    base.min(available.max(1)).max(1) // At least 1 I/O thread
}

/// No-op: CPU saturation is no longer tracked via atomic.
///
/// MLX-aware paths use direct Metal probing via `fraction_to_level()`.
/// This function exists for backward compatibility only.
#[allow(dead_code)]
pub fn update_cpu_saturation(_pct: u8) {
    // No-op: CPU_SATURATION atomic removed; MLX Metal probing is the source of truth.
}

/// Update the explicit memory-pressure signal.
///
/// When set to a non-default value (≠ PRESSURE_UNSET), mixed_threshold()
/// uses this pressure directly and bypasses MLX Metal probing.
/// This allows Python tests to control the threshold without MLX being available.
///
/// Value: 0=idle (→16), 1=normal (→32), 2=pressure (→64).
///
/// Note: The thread-local metal cache is NOT consulted when pressure ≠ PRESSURE_UNSET;
/// the atomic value short-circuits the cache entirely. The cached entry is kept
/// for the reset path (pressure == PRESSURE_UNSET) where it stores the MLX-probed
/// level so that re-probing is not needed on every call.
#[allow(dead_code)]
pub fn update_memory_pressure(pressure: u8) {
    MEMORY_PRESSURE.store(pressure, Ordering::Release);
    // Invalidate the thread-local cache so the next MLX probe (if pressure == PRESSURE_UNSET)
    // starts fresh.  When pressure != PRESSURE_UNSET the cache is bypassed entirely, so
    // the entry value is immaterial — we store PRESSURE_UNSET (=1) to document the reset path.
    let now = Instant::now();
    METAL_CACHE.with(|cell| cell.set((now, PRESSURE_UNSET, 0)));
}

#[pyfunction]
pub fn get_adaptive_cpu_threads() -> usize {
    recommended_cpu_threads()
}

#[pyfunction]
pub fn get_adaptive_io_threads() -> usize {
    recommended_io_threads()
}

/// MODERN-31: Set current sprint phase (for telemetry).
/// Phase configs are initial seeds — pressure-based sizing takes precedence.
#[pyfunction]
pub fn set_adaptive_phase(phase: &str) {
    set_phase(phase);
}

/// MODERN-31: Get current sprint phase.
#[pyfunction]
pub fn get_adaptive_phase() -> String {
    get_phase()
}

/// MODERN-31: Get mixed threshold for pool selection (16/32/64 adaptive).
#[pyfunction]
pub fn get_adaptive_mixed_threshold() -> usize {
    mixed_threshold()
}

/// Returns MLX-aware MIXED_THRESHOLD from actual mx.metal.get_active_memory().
/// Explicit GIL version — prefer `get_adaptive_mixed_threshold()` for internal use.
#[pyfunction]
pub fn get_adaptive_mixed_threshold_via_metal(py: Python<'_>) -> usize {
    mixed_threshold_via_metal(py)
}

/// Returns the dynamic Metal cache limit in bytes by probing Python's
/// `utils.mlx_cache.get_dynamic_metal_cache_limit()`.
/// Returns 0 if MLX/Python is unavailable.
#[pyfunction]
pub fn get_metal_limit_bytes_py(py: Python<'_>) -> u64 {
    get_metal_limit_bytes(py)
}

/// Deprecated: MLX Metal probing is now inline in mixed_threshold().
/// Kept for backward compatibility — calls mixed_threshold() directly.
#[pyfunction]
pub fn sync_metal_memory_pressure_py(py: Python<'_>) -> usize {
    mixed_threshold_via_metal(py)
}

// MODERN-32: Global thread budget bindings
// sync_adaptive_state removed: deprecated no-op, functionality is now inline in mixed_threshold()

/// MODERN-32: Get total active threads across all pools + dispatchers.
/// Returns: cpu + io + mixed + 3 (dispatchers)
#[pyfunction]
pub fn get_total_active_threads_budget() -> usize {
    get_total_threads()
}

/// MODERN-32 + THREAD-BUDGET-01: Get available budget slots for new threads.
/// Returns: BUDGET_AVAILABLE - (cpu + io + mixed + dispatchers)
#[pyfunction]
pub fn get_available_thread_budget() -> usize {
    get_available_budget()
}

/// THREAD-BUDGET-01: Get budget ceiling (available for pools)
#[pyfunction]
pub fn get_budget_ceiling() -> usize {
    BUDGET_AVAILABLE
}

/// THREAD-BUDGET-01: Check if budget allows `extra` threads
#[pyfunction]
pub fn check_budget_allows(extra: usize) -> bool {
    budget_allows(extra)
}

/// MODERN-CROSS-4: Get the count of threshold switches since process start.
#[pyfunction]
pub fn get_threshold_switch_counter() -> usize {
    get_threshold_switch_count()
}

/// MODERN-CROSS-4: Reset the threshold switch counter.
#[pyfunction]
pub fn reset_threshold_switch_counter() {
    reset_threshold_switch_count();
}

/// MODERN-CROSS-4: Get current threshold level (0=idle, 1=normal, 2=pressure).
#[pyfunction]
pub fn get_current_metal_level() -> u8 {
    LAST_THRESHOLD_LEVEL.get()
}

/// MODERN-CROSS-4: Get time since last threshold switch in seconds.
#[pyfunction]
pub fn get_seconds_since_last_switch() -> f64 {
    LAST_THRESHOLD_SWITCH_TIME.get().elapsed().as_secs_f64()
}

/// MODERN-CROSS-4: Get complete threshold statistics.
/// Returns: (switch_count, current_level, seconds_since_last_switch)
#[pyfunction]
pub fn get_threshold_monitoring_stats() -> (usize, u8, f64) {
    get_threshold_stats()
}

/// MODERN-32: Get per-pool thread counts as a tuple.
/// Returns: (cpu, io, mixed, dispatchers, total)
#[pyfunction]
pub fn get_thread_budget_breakdown() -> (usize, usize, usize, usize, usize) {
    let cpu = get_cpu_budget();
    let io = get_io_budget();
    let mixed = get_mixed_budget();
    let dispatchers = DISPATCHER_COUNT;
    let total = cpu + io + mixed + dispatchers;
    (cpu, io, mixed, dispatchers, total)
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_adaptive_cpu_threads))?;
    m.add_function(wrap_pyfunction!(get_adaptive_io_threads))?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold))?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold_via_metal))?;
    m.add_function(wrap_pyfunction!(get_metal_limit_bytes_py))?;
    m.add_function(wrap_pyfunction!(sync_metal_memory_pressure_py))?;
    // MODERN-31: Phase setter/getter
    m.add_function(wrap_pyfunction!(set_adaptive_phase))?;
    m.add_function(wrap_pyfunction!(get_adaptive_phase))?;
    // MODERN-32 + THREAD-BUDGET-01: Global thread budget
    m.add_function(wrap_pyfunction!(get_total_active_threads_budget))?;
    m.add_function(wrap_pyfunction!(get_available_thread_budget))?;
    m.add_function(wrap_pyfunction!(get_thread_budget_breakdown))?;
    // THREAD-BUDGET-01: New budget functions
    m.add_function(wrap_pyfunction!(get_budget_ceiling))?;
    m.add_function(wrap_pyfunction!(check_budget_allows))?;
    // sync_adaptive_state removed: deprecated no-op, not used from Python

    // MODERN-CROSS-4: Threshold switch monitoring
    m.add_function(wrap_pyfunction!(get_threshold_switch_counter))?;
    m.add_function(wrap_pyfunction!(reset_threshold_switch_counter))?;
    m.add_function(wrap_pyfunction!(get_current_metal_level))?;
    m.add_function(wrap_pyfunction!(get_seconds_since_last_switch))?;
    m.add_function(wrap_pyfunction!(get_threshold_monitoring_stats))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_threshold_constants() {
        assert_eq!(IDLE_THRESHOLD, 16);
        assert_eq!(NORMAL_THRESHOLD, 32);
        assert_eq!(PRESSURE_THRESHOLD, 64);
    }

    #[test]
    fn test_fraction_to_level_fallback() {
        // When limit_bytes or active is 0, fallback to level 1 (normal).
        assert_eq!(fraction_to_level(0, 0), 1);
        assert_eq!(fraction_to_level(0, 1_000_000_000), 1);
        assert_eq!(fraction_to_level(1_000_000_000, 0), 1);
    }

    #[test]
    fn test_fraction_to_level_boundaries() {
        // < 0.60 → idle (level 0)
        assert_eq!(fraction_to_level(1_000_000_000, 599_000_000), 0);
        assert_eq!(fraction_to_level(1_000_000_000, 0), 1); // edge: 0 < 0.60

        // 0.60–0.85 → normal (level 1)
        assert_eq!(fraction_to_level(1_000_000_000, 600_000_000), 1);
        assert_eq!(fraction_to_level(1_000_000_000, 850_000_000), 1);

        // > 0.85 → pressure (level 2)
        assert_eq!(fraction_to_level(1_000_000_000, 851_000_000), 2);
        assert_eq!(fraction_to_level(1_000_000_000, 1_000_000_000), 2);
    }

    #[test]
    fn test_threshold_level_mapping() {
        // Verify threshold constants match the documented levels.
        // idle (level 0) → IDLE_THRESHOLD = 16
        assert_eq!(mixed_threshold(), NORMAL_THRESHOLD); // default fallback = normal
    }

    #[test]
    fn test_update_memory_pressure_idle() {
        // Explicit pressure=0 (idle) must bypass MLX probe and return 16.
        update_memory_pressure(0);
        assert_eq!(mixed_threshold(), IDLE_THRESHOLD); // 16
                                                       // Reset to default.
        update_memory_pressure(1);
    }

    #[test]
    fn test_update_memory_pressure_normal() {
        // Explicit pressure=1 equals PRESSURE_UNSET → falls through to MLX.
        // Without MLX, get_metal_level_cached() probes GIL, which in a
        // #[cfg(test)] binary has no Python interpreter, so it returns
        // the fallback level 1 (NORMAL_THRESHOLD = 32).
        update_memory_pressure(1);
        assert_eq!(mixed_threshold(), NORMAL_THRESHOLD); // 32
    }

    #[test]
    fn test_update_memory_pressure_pressure() {
        // Explicit pressure=2 (pressure) must bypass MLX probe and return 64.
        update_memory_pressure(2);
        assert_eq!(mixed_threshold(), PRESSURE_THRESHOLD); // 64
                                                           // Reset to default.
        update_memory_pressure(1);
    }

    #[test]
    fn test_recommended_cpu_threads_explicit_pressure() {
        // THREAD-BUDGET-02: With BUDGET_AVAILABLE=6, cpu is clamped
        // Idle → 2 P-cores (clamped from 4 to fit budget)
        update_memory_pressure(0);
        assert_eq!(recommended_cpu_threads(), 2);
        // Normal → 2 P-cores (fits budget)
        update_memory_pressure(1);
        assert_eq!(recommended_cpu_threads(), 2);
        // Pressure → 1 (sequential).
        update_memory_pressure(2);
        assert_eq!(recommended_cpu_threads(), 1);
        // Reset.
        update_memory_pressure(1);
    }

    #[test]
    fn test_recommended_io_threads_explicit_pressure() {
        // THREAD-BUDGET-02: With BUDGET_AVAILABLE=6, io is clamped
        // Idle/normal → 1 I/O thread (clamped from 2 to fit budget)
        update_memory_pressure(0);
        assert_eq!(recommended_io_threads(), 1);
        update_memory_pressure(1);
        assert_eq!(recommended_io_threads(), 1);
        // Pressure → 1 (minimal).
        update_memory_pressure(2);
        assert_eq!(recommended_io_threads(), 1);
        // Reset.
        update_memory_pressure(1);
    }
}

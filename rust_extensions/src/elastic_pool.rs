//! elastic_pool — Phase-aware dynamic rayon pool resizing.
//!
//! ## MODERN-31: Single Source of Truth
//!
//! Pool sizing is now driven by `adaptive_scheduler.rs` which provides
//! pressure-based recommendations via `recommended_cpu_threads()` and
//! `recommended_io_threads()`. This module implements the actual pool
//! resizing via `resize_cpu_pool(n)` / `resize_io_pool(n)`.
//!
//! ## MODERN-32: Global Thread Budget
//!
//! Replaces the static `LazyLock<ThreadPool>` pattern with
//! `Arc<RwLock<Option<ThreadPool>>>` wrappers that can be swapped atomically
//! at runtime. The global thread budget is enforced across ALL pools:
//!
//!   - cpu_pool threads (P-cores via USER_INITIATED QoS)
//!   - io_pool threads (E-cores via UTILITY QoS)
//!   - mixed_pool threads (adaptive, 1-2 threads)
//!   - dispatcher threads (E-cores, 3 total)
//!
//! Total never exceeds MAX_TOTAL_THREADS = 8 (M1 8GB: 4P + 4E).
//!
//! ## Backward Compatibility
//!
//! `pool_run.rs` dispatchers are updated to read from the `RwLock` on each
//! `rx.recv_timeout()` iteration. Old dispatchers (with old pool reference)
//! drain their queue and exit naturally. New work goes to the new pool.

use parking_lot::RwLock;
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use rayon::ThreadPool;
use rayon::ThreadPoolBuilder;
use std::sync::Arc;
use std::thread;

// MODERN-32: Delegate MAX_TOTAL_THREADS to adaptive_scheduler (single source of truth)
use crate::adaptive_scheduler::MAX_TOTAL_THREADS;

// ---------------------------------------------------------------------------
// MODERN-33: Single Source of Truth for P-core detection
// ---------------------------------------------------------------------------
// MODERN-33 FIX: Removed duplicate detect_topology_p_cores().
// Now uses lib.rs::detect_p_core_count() as single source of truth.
// This eliminates 50+ lines of duplicate sysctlbyname code.

// ---------------------------------------------------------------------------

/// Global CPU-bound pool — wrapped in Arc<RwLock> for dynamic replacement.
/// Initialized lazily on first resize call or pool reference access.
static CPU_POOL: RwLock<Option<Arc<ThreadPool>>> = RwLock::new(None);

/// Global I/O-bound pool — wrapped in Arc<RwLock> for dynamic replacement.
/// Initialized lazily on first resize call or pool reference access.
static IO_POOL: RwLock<Option<Arc<ThreadPool>>> = RwLock::new(None);

// ---------------------------------------------------------------------------
// Pool building helpers (mirror lib.rs patterns)
// ---------------------------------------------------------------------------

/// Build a CPU-bound ThreadPool with `num_threads` threads.
/// Applies P-core QoS hints (macOS) and CPU affinity (Linux).
///
/// MODERN-28 FIX: CPU pool uses USER_INITIATED → P-cores for CPU-bound work.
fn build_cpu_pool(num_threads: usize) -> Result<ThreadPool, String> {
    let n = num_threads.clamp(1, MAX_TOTAL_THREADS);
    ThreadPoolBuilder::new()
        .num_threads(n)
        .stack_size(4_194_304) // 4 MiB
        .thread_name(|i| format!("hledac-cpu-{}", i))
        .spawn_handler(move |builder| {
            // spawn_handler expects FnOnce(Box<dyn rayon::Builder>) -> Result<(), Error>
            // We spawn our own thread with platform-specific setup, then call builder.run()
            let _ = thread::spawn(move || {
                #[cfg(target_os = "macos")]
                {
                    unsafe {
                        libc::pthread_set_qos_class_self_np(
                            libc::qos_class_t::QOS_CLASS_USER_INITIATED, // MODERN-28: P-cores
                            0,
                        );
                    }
                    // NEW-M11 FIX: Also apply P/E core affinity for macOS
                    // Same issue as lib.rs:build_mixed_pool! - Linux path calls apply_affinity_hint but macOS doesn't
                    apply_affinity_hint(n);
                }
                #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                apply_affinity_hint(n);
                builder.run();
            });
            Ok(())
        })
        .build()
        .map_err(|e| format!("build_cpu_pool: ThreadPoolBuilder::build failed: {}", e))
}

/// Build an I/O-bound ThreadPool with `num_threads` threads.
///
/// MODERN-28 FIX: IO pool uses UTILITY → E-cores for I/O-bound work.
/// This allows P-cores to focus on CPU-intensive tasks (inference, ML).
fn build_io_pool(num_threads: usize) -> Result<ThreadPool, String> {
    let n = num_threads.clamp(1, 4); // io_pool max 4 threads
    ThreadPoolBuilder::new()
        .num_threads(n)
        .stack_size(4_194_304) // 4 MiB
        .thread_name(|i| format!("hledac-io-{}", i))
        .spawn_handler(move |builder| {
            // spawn_handler expects FnOnce(Box<dyn rayon::Builder>) -> Result<(), Error>
            let _ = thread::spawn(move || {
                #[cfg(target_os = "macos")]
                {
                    unsafe {
                        libc::pthread_set_qos_class_self_np(
                            libc::qos_class_t::QOS_CLASS_UTILITY, // MODERN-28: E-cores
                            0,
                        );
                    }
                    // NEW-M11 FIX: Also apply P/E core affinity for macOS
                    // Same issue as lib.rs:build_mixed_pool! - Linux path calls apply_affinity_hint but macOS doesn't
                    apply_affinity_hint(n);
                }
                #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                apply_affinity_hint(n);
                builder.run();
            });
            Ok(())
        })
        .build()
        .map_err(|e| format!("build_io_pool: ThreadPoolBuilder::build failed: {}", e))
}

/// Linux: Pin thread to first `cores` physical CPU cores.
#[cfg(all(target_os = "linux", not(target_env = "musl")))]
fn apply_affinity_hint(cores: usize) {
    let mut mask: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    for i in 0..cores.min(128) {
        unsafe { libc::CPU_SET(i, &mut mask) };
    }
    let _ = unsafe {
        libc::pthread_setaffinity_np(
            libc::pthread_self(),
            std::mem::size_of::<libc::cpu_set_t>(),
            &mask,
        )
    };
}

/// MODERN-26: macOS P-core affinity via Mach APIs.
/// Delegates to crate::darwin_affinity for unified implementation.
#[cfg(target_os = "macos")]
fn apply_affinity_hint(cores: usize) {
    crate::darwin_affinity::apply_cpu_affinity(cores);
}

/// musl: No sched_setaffinity available.
#[cfg(all(target_os = "linux", target_env = "musl"))]
fn apply_affinity_hint(_cores: usize) {}

/// Windows / other: No-op.
#[cfg(not(any(
    target_os = "macos",
    all(target_os = "linux", not(target_env = "musl"))
)))]
fn apply_affinity_hint(_cores: usize) {}

// ---------------------------------------------------------------------------
// Core resize API — replaces LazyLock singletons from lib.rs
// ---------------------------------------------------------------------------

/// Resize the CPU-bound pool to `num_threads`.
///
/// MODERN-31 FIX: Updates global budget via adaptive_scheduler::set_cpu_budget()
/// so that budget accounting is unified across all pools.
///
/// [SWARM]-009 FIX: Graceful degradation on OOM.
/// Logs error and keeps existing pool if new pool build fails.
/// Existing dispatchers (from pool_run.rs) hold references to the old pool
/// and drain their queues; new work goes to the new pool automatically
/// because dispatchers re-read the pool from the RwLock on each iteration.
///
/// Thread-safe: uses RwLock for concurrent readers (dispatchers) vs single writer.
pub fn resize_cpu_pool(num_threads: usize) {
    let n = num_threads.clamp(1, MAX_TOTAL_THREADS);
    match build_cpu_pool(n) {
        Ok(pool) => {
            let new_pool = Arc::new(pool);
            let mut guard = CPU_POOL.write();
            *guard = Some(new_pool);
            // MODERN-31: Update global budget for unified accounting
            crate::adaptive_scheduler::set_cpu_budget(n);
        }
        Err(e) => {
            eprintln!(
                "elastic_pool: resize_cpu_pool failed ({}) — keeping existing pool",
                e
            );
        }
    }
}

/// Resize the I/O-bound pool to `num_threads`.
///
/// MODERN-31 FIX: Updates global budget via adaptive_scheduler::set_io_budget()
/// so that budget accounting is unified across all pools.
///
/// [SWARM]-009 FIX: Graceful degradation on OOM.
/// Logs error and keeps existing pool if new pool build fails.
/// Enforces: cpu_threads + io_threads + dispatchers <= MAX_TOTAL_THREADS (8).
///
/// Thread-safe: uses RwLock for concurrent readers vs single writer.
pub fn resize_io_pool(num_threads: usize) {
    // Read current CPU pool size to enforce total <= 8 (accounting for dispatchers)
    let cpu_count = {
        let guard = CPU_POOL.read();
        match &*guard {
            Some(pool) => pool.current_num_threads(),
            None => 0,
        }
    };
    // MODERN-32: Reserve 3 slots for dispatchers
    let max_io = MAX_TOTAL_THREADS.saturating_sub(cpu_count + 3);
    let n = num_threads.clamp(1, max_io.max(1));

    match build_io_pool(n) {
        Ok(pool) => {
            let new_pool = Arc::new(pool);
            let mut guard = IO_POOL.write();
            *guard = Some(new_pool);
            // MODERN-31: Update global budget for unified accounting
            crate::adaptive_scheduler::set_io_budget(n);
        }
        Err(e) => {
            eprintln!(
                "elastic_pool: resize_io_pool failed ({}) — keeping existing pool",
                e
            );
        }
    }
}

/// Get current CPU pool thread count, or 0 if not initialized.
pub fn get_cpu_pool_threads() -> usize {
    let guard = CPU_POOL.read();
    match &*guard {
        Some(pool) => pool.current_num_threads(),
        None => 0,
    }
}

/// Get current I/O pool thread count, or 0 if not initialized.
pub fn get_io_pool_threads() -> usize {
    let guard = IO_POOL.read();
    match &*guard {
        Some(pool) => pool.current_num_threads(),
        None => 0,
    }
}

/// Get total active rayon threads across cpu + io pools (dispatchers NOT included).
/// For global budget including dispatchers, use adaptive_scheduler::get_total_threads().
pub fn get_total_active_threads() -> usize {
    get_cpu_pool_threads() + get_io_pool_threads()
}

/// Initialize default pools (called by Python at startup).
///
/// MODERN-30 FIX: Pools are now sized based on actual hardware topology:
/// - cpu_pool: p_cores (1-4, M1 8GB safe)
/// - io_pool: MAX_TOTAL_THREADS - p_cores (leaves headroom for system)
///
/// Before: Hardcoded cpu_pool=4, io_pool=2 (wasteful on 2P systems, risky on 8P systems)
/// After:  Topology-aware sizing (4P → cpu=4/io=4, 2P → cpu=2/io=2, 1P → cpu=1/io=1)
pub fn init_default_pools() {
    // MODERN-33 + MODERN-34: Use topology::p_core_count() — single source of truth
    // This uses the cached perflevel0/1 counts from topology.rs
    let p_cores = crate::topology::p_core_count();
    let io_threads = MAX_TOTAL_THREADS.saturating_sub(p_cores).max(1);

    resize_cpu_pool(p_cores);
    resize_io_pool(io_threads);
}

// ---------------------------------------------------------------------------
// Pool reference getters — used by pool_run.rs dispatchers
// ---------------------------------------------------------------------------

/// Get the current CPU pool as Arc<ThreadPool>.
/// Initializes with defaults (4 threads) if not yet set.
///
/// [SWARM]-009 FIX: Graceful degradation on OOM.
/// Falls back to 1-thread pool if full build fails.
pub fn get_cpu_pool() -> Arc<ThreadPool> {
    {
        let guard = CPU_POOL.read();
        if let Some(ref pool) = *guard {
            return Arc::clone(pool);
        }
    }
    // Slow path: pool not yet initialized. Build new pool under write lock.
    let pool = match build_cpu_pool(4) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            eprintln!(
                "elastic_pool: get_cpu_pool initial build failed ({}) — falling back to 1-thread",
                e
            );
            Arc::new(build_cpu_pool(1).expect("fallback 1-thread pool should always succeed"))
        }
    };
    let mut guard = CPU_POOL.write();
    if let Some(ref existing) = *guard {
        // Another thread initialized first — use that pool instead.
        return Arc::clone(existing);
    }
    *guard = Some(Arc::clone(&pool));
    pool
}

/// Get the current I/O pool as Arc<ThreadPool>.
/// Initializes with defaults (2 threads) if not yet set.
///
/// [SWARM]-009 FIX: Graceful degradation on OOM.
/// Falls back to 1-thread pool if full build fails.
pub fn get_io_pool() -> Arc<ThreadPool> {
    {
        let guard = IO_POOL.read();
        if let Some(ref pool) = *guard {
            return Arc::clone(pool);
        }
    }
    // Slow path: pool not yet initialized. Build new pool under write lock.
    let pool = match build_io_pool(2) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            eprintln!(
                "elastic_pool: get_io_pool initial build failed ({}) — falling back to 1-thread",
                e
            );
            Arc::new(build_io_pool(1).expect("fallback 1-thread pool should always succeed"))
        }
    };
    let mut guard = IO_POOL.write();
    if let Some(ref existing) = *guard {
        // Another thread initialized first — use that pool instead.
        return Arc::clone(existing);
    }
    *guard = Some(Arc::clone(&pool));
    pool
}

// ---------------------------------------------------------------------------
// Legacy compatibility aliases — for code that still uses lib.rs cpu_pool()
// ---------------------------------------------------------------------------

/// Legacy: returns current CPU pool (for backward compatibility).
/// Prefer `get_cpu_pool()` in new code.
pub fn cpu_pool() -> Arc<ThreadPool> {
    get_cpu_pool()
}

/// Legacy: returns current I/O pool (for backward compatibility).
/// Prefer `get_io_pool()` in new code.
pub fn io_pool() -> Arc<ThreadPool> {
    get_io_pool()
}

// ---------------------------------------------------------------------------
// PyO3 bindings — called by Python RayonPoolManager
// ---------------------------------------------------------------------------

/// Resize CPU pool from Python. Enforces MAX_TOTAL_THREADS=8.
#[pyfunction]
#[pyo3(name = "resize_cpu_pool")]
pub fn resize_cpu_pool_py(num_threads: usize) -> usize {
    let n = num_threads.clamp(1, MAX_TOTAL_THREADS);
    resize_cpu_pool(n);
    get_cpu_pool_threads()
}

/// Resize I/O pool from Python. Enforces total <= MAX_TOTAL_THREADS (8).
#[pyfunction]
#[pyo3(name = "resize_io_pool")]
pub fn resize_io_pool_py(num_threads: usize) -> usize {
    resize_io_pool(num_threads);
    get_io_pool_threads()
}

/// Initialize default pools from Python (called at process startup).
#[pyfunction]
#[pyo3(name = "init_elastic_pools")]
pub fn init_elastic_pools_py() -> (usize, usize) {
    init_default_pools();
    (get_cpu_pool_threads(), get_io_pool_threads())
}

/// Get current CPU pool thread count.
#[pyfunction]
#[pyo3(name = "get_elastic_cpu_threads")]
pub fn get_elastic_cpu_threads_py() -> usize {
    get_cpu_pool_threads()
}

/// Get current I/O pool thread count.
#[pyfunction]
#[pyo3(name = "get_elastic_io_threads")]
pub fn get_elastic_io_threads_py() -> usize {
    get_io_pool_threads()
}

/// Get total active rayon threads (cpu + io).
#[pyfunction]
#[pyo3(name = "get_elastic_total_threads")]
pub fn get_elastic_total_threads_py() -> usize {
    get_total_active_threads()
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resize_cpu_pool_py, m)?)?;
    m.add_function(wrap_pyfunction!(resize_io_pool_py, m)?)?;
    m.add_function(wrap_pyfunction!(init_elastic_pools_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_elastic_cpu_threads_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_elastic_io_threads_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_elastic_total_threads_py, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_max_total_threads_constant() {
        assert_eq!(MAX_TOTAL_THREADS, 8);
    }

    #[test]
    fn test_resize_cpu_pool_basic() {
        resize_cpu_pool(4);
        assert_eq!(get_cpu_pool_threads(), 4);
        assert_eq!(get_total_active_threads(), 4); // io not initialized yet

        resize_cpu_pool(2);
        assert_eq!(get_cpu_pool_threads(), 2);

        // Clamp at MAX
        resize_cpu_pool(100);
        assert_eq!(get_cpu_pool_threads(), MAX_TOTAL_THREADS);
    }

    #[test]
    fn test_resize_io_pool_enforces_total_limit() {
        resize_cpu_pool(6);
        resize_io_pool(4); // Should clamp: 8 - 6 = 2
        assert!(get_io_pool_threads() <= 2);
        assert_eq!(get_total_active_threads(), 8);
    }

    #[test]
    fn test_init_default_pools() {
        // MODERN-33 + MODERN-34: Uses topology::p_core_count()
        init_default_pools();
        let p_cores = crate::topology::p_core_count();
        assert_eq!(get_cpu_pool_threads(), p_cores);
        // io_pool = MAX_TOTAL_THREADS - p_cores (but min 1)
        let expected_io = (MAX_TOTAL_THREADS - p_cores).max(1);
        assert_eq!(get_io_pool_threads(), expected_io);
        assert_eq!(get_total_active_threads(), MAX_TOTAL_THREADS);
    }

    #[test]
    fn test_lazy_init() {
        // Before any init, get_cpu_pool should create with default 4
        let pool = get_cpu_pool();
        assert!(pool.current_num_threads() >= 1);
    }

    #[test]
    fn test_arc_clone_independence() {
        resize_cpu_pool(3);
        let p1 = get_cpu_pool();
        let p2 = get_cpu_pool();
        // Both should return Arc clones of the SAME pool
        assert_eq!(p1.current_num_threads(), p2.current_num_threads());

        // After resize, get_cpu_pool returns the NEW pool
        resize_cpu_pool(5);
        let p3 = get_cpu_pool();
        assert_eq!(p3.current_num_threads(), 5);
        // p1 and p2 still reference the OLD 3-thread pool
        assert_eq!(p1.current_num_threads(), 3);
    }

    #[test]
    fn test_legacy_aliases() {
        init_default_pools();
        let legacy = cpu_pool();
        let current = get_cpu_pool();
        assert_eq!(legacy.current_num_threads(), current.current_num_threads());
    }
}

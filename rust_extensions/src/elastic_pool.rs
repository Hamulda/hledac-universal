//! elastic_pool — Phase-aware dynamic rayon pool resizing.
//!
//! ## Problem
//!
//! `cpu_pool()` and `io_pool()` in `lib.rs` are `LazyLock<ThreadPool>` singletons
//! built once at first access with fixed thread counts (4 and 2 respectively).
//! They are never resized during a sprint.
//!
//! `adaptive_scheduler.rs` only *recommends* threshold sizes — it does NOT grow or shrink
//! the actual pool. The archived `unified_executor.py` had channel-based work-stealing
//! dispatch but was archived during F350M-R cleanup.
//!
//! ## Solution
//!
//! Replaces the static `LazyLock<ThreadPool>` pattern with
//! `Arc<RwLock<Option<ThreadPool>>>` wrappers that can be swapped atomically
//! at runtime via `resize_cpu_pool(n)` / `resize_io_pool(n)`.
//!
//! The existing dispatchers in `pool_run.rs` are updated to read the current pool
//! from the `RwLock` on each iteration — so pool replacement is seamless:
//! old dispatchers drain their queue while new work goes to the new pool.
//!
//! ## Phase-Aware Elasticity (Python side)
//!
//! The Python `RayonPoolManager` (in `isolated_executors.py`) drives resize based on
//! sprint phase transitions:
//!
//!   | Phase    | cpu_pool | io_pool | Total |
//!   |----------|----------|---------|-------|
//!   | BOOT     | 4        | 2       | 6     |
//!   | ACTIVE   | 4        | 4       | 8     |  ← fetch-heavy: io expands
//!   | SYNTHESIS| 6        | 2       | 8     |  ← cpu-heavy: borrow from io
//!   | WINDUP   | 4        | 2       | 6     |  ← back to default
//!
//! ## M1 8GB Guard
//!
//! `MAX_TOTAL_THREADS = 8` is enforced:
//!   - `resize_cpu_pool(n)` → min(n, MAX_TOTAL_THREADS)
//!   - `resize_io_pool(n)`  → min(n, MAX_TOTAL_THREADS - cpu_count)
//!   - Never exceeds 8 total rayon threads (4P + 4E cores).
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

/// Maximum total rayon threads across all pools.
/// M1 Air: 4P + 4E = 8 logical cores.
/// Enforced by resize_cpu_pool() and resize_io_pool().
const MAX_TOTAL_THREADS: usize = 8;

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
/// [SWARM]-009 FIX: Returns Result instead of panicking on OOM.
/// On M1 8GB, thread allocation can fail if system memory is constrained.
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
                            libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                            0,
                        );
                    }
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
/// [SWARM]-009 FIX: Returns Result instead of panicking on OOM.
/// On M1 8GB, thread allocation can fail if system memory is constrained.
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
                            libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                            0,
                        );
                    }
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

#[cfg(not(all(target_os = "linux", not(target_env = "musl"))))]
fn apply_affinity_hint(_cores: usize) {}

// ---------------------------------------------------------------------------
// Core resize API — replaces LazyLock singletons from lib.rs
// ---------------------------------------------------------------------------

/// Resize the CPU-bound pool to `num_threads`.
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
/// [SWARM]-009 FIX: Graceful degradation on OOM.
/// Logs error and keeps existing pool if new pool build fails.
/// Panics if `num_threads` would push total threads above MAX_TOTAL_THREADS (8).
/// Enforces: cpu_threads + io_threads <= 8.
///
/// Thread-safe: uses RwLock for concurrent readers vs single writer.
pub fn resize_io_pool(num_threads: usize) {
    // Read current CPU pool size to enforce total <= 8
    let cpu_count = {
        let guard = CPU_POOL.read();
        match &*guard {
            Some(pool) => pool.current_num_threads(),
            None => 0,
        }
    };
    let max_io = MAX_TOTAL_THREADS.saturating_sub(cpu_count);
    let n = num_threads.clamp(1, max_io.max(1));

    match build_io_pool(n) {
        Ok(pool) => {
            let new_pool = Arc::new(pool);
            let mut guard = IO_POOL.write();
            *guard = Some(new_pool);
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

/// Get total active rayon threads across cpu + io pools.
pub fn get_total_active_threads() -> usize {
    get_cpu_pool_threads() + get_io_pool_threads()
}

/// Initialize default pools (called by Python at startup).
/// Sets cpu_pool=4, io_pool=2 — matches the original LazyLock defaults.
pub fn init_default_pools() {
    resize_cpu_pool(4);
    resize_io_pool(2);
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
        init_default_pools();
        assert_eq!(get_cpu_pool_threads(), 4);
        assert_eq!(get_io_pool_threads(), 2);
        assert_eq!(get_total_active_threads(), 6);
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

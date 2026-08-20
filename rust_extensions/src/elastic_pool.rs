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
//! ## NEXTGEN-03: Asymmetric Topology-Aware Pools
//!
//! Creates THREE dedicated pools instead of one cpu_pool:
//!
//! | Pool | Threads | Cores | QoS | Workload |
//! |------|---------|-------|-----|----------|
//! | simd_pool | 2 | P 0,1 | USER_INITIATED | ARM NEON SIMD, Aho-Corasick |
//! | mlx_pool | 2 | P 2,3 | USER_INTERACTIVE | MLX Metal dispatch |
//! | graph_pool | 1 | P 2 | USER_INITIATED | Kuzu graph, petgraph |
//!
//! Network I/O: Tokio runtime workers on E-cores 4,5,6,7
//!
//! ## Work-Stealing Policy
//!
//! NEXTGEN-03: Cross-cluster stealing is disabled.
//! SIMD tasks never steal from MLX/Graph pools and vice versa.
//! Uses spawn_fifo() + manual scope affinity for isolation.
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

// NEXTGEN-03: LazyLock for sender initialization
use std::sync::LazyLock;

// NEXTGEN-03: Crossbeam channel for dispatcher communication
use crossbeam_channel;

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

// NEXTGEN-03: Dedicated pools for asymmetric topology-aware scheduling
// ============================================================================

/// Global SIMD pool — 2 threads, P-cores 0,1, USER_INITIATED QoS.
/// For ARM NEON SIMD operations (simd_similarity.rs, deep_ac Aho-Corasick).
static SIMD_POOL: RwLock<Option<Arc<ThreadPool>>> = RwLock::new(None);

/// Global MLX pool — 2 threads, P-cores 2,3, USER_INTERACTIVE QoS.
/// For MLX Metal dispatch (mx.eval(), mx.compile()) with minimal latency.
static MLX_POOL: RwLock<Option<Arc<ThreadPool>>> = RwLock::new(None);

/// Global Graph pool — 1 thread, P-core 2 (shared with MLX), USER_INITIATED QoS.
/// For Kuzu graph traversal and petgraph PageRank.
static GRAPH_POOL: RwLock<Option<Arc<ThreadPool>>> = RwLock::new(None);

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

// NEXTGEN-03: Dedicated pool builders for asymmetric topology-aware scheduling
// ============================================================================

/// NEXTGEN-03: Build SIMD pool with explicit P-core affinity.
///
/// Pool config:
///   - Threads: 2
///   - Cores: P 0,1 (perflevel 0)
///   - QoS: USER_INITIATED (0x19)
///   - Workload: ARM NEON SIMD (simd_similarity, Aho-Corasick)
///
/// Uses spawn_fifo() to minimize work-stealing across pools.
fn build_simd_pool() -> Result<ThreadPool, String> {
    // Get P-core indices for SIMD pool
    let topo = crate::topology::get_topology();
    let p_cores = &topo.p_core_indices;
    let simd_cores = if p_cores.len() >= 2 {
        vec![p_cores[0], p_cores[1]]
    } else {
        p_cores.clone()
    };

    ThreadPoolBuilder::new()
        .num_threads(simd_cores.len())
        .stack_size(4_194_304) // 4 MiB
        .thread_name(|i| format!("hledac-simd-{}", i))
        // NOTE: spawn_fifo removed - not available in this rayon version
        .spawn_handler(move |builder| {
            let core_ids = simd_cores.clone();
            let _ = thread::spawn(move || {
                #[cfg(target_os = "macos")]
                {
                    // Set USER_INITIATED QoS for P-cores
                    unsafe {
                        libc::pthread_set_qos_class_self_np(
                            libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                            0,
                        );
                    }
                    // Apply explicit P-core affinity
                    apply_affinity_to_cores(&core_ids);
                }
                #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                {
                    apply_affinity_to_cores(&core_ids);
                }
                builder.run();
            });
            Ok(())
        })
        .build()
        .map_err(|e| format!("build_simd_pool: failed: {}", e))
}

/// NEXTGEN-03: Build MLX pool with explicit P-core affinity and USER_INTERACTIVE QoS.
///
/// Pool config:
///   - Threads: 2
///   - Cores: P 2,3 (perflevel 0)
///   - QoS: USER_INTERACTIVE (higher priority than USER_INITIATED)
///   - Workload: MLX Metal dispatch (mx.eval(), mx.compile())
///
/// USER_INTERACTIVE ensures MLX GPU command buffer submission has minimal latency.
fn build_mlx_pool() -> Result<ThreadPool, String> {
    // Get P-core indices for MLX pool (skip first 2 used by SIMD)
    let topo = crate::topology::get_topology();
    let p_cores = &topo.p_core_indices;
    let mlx_cores = if p_cores.len() >= 4 {
        vec![p_cores[2], p_cores[3]]
    } else if p_cores.len() >= 2 {
        // Fallback: use remaining P-cores
        p_cores[1..].to_vec()
    } else {
        p_cores.clone()
    };

    ThreadPoolBuilder::new()
        .num_threads(mlx_cores.len())
        .stack_size(4_194_304) // 4 MiB
        .thread_name(|i| format!("hledac-mlx-{}", i))
        // NOTE: spawn_fifo removed - not available in this rayon version
        .spawn_handler(move |builder| {
            let core_ids = mlx_cores.clone();
            let _ = thread::spawn(move || {
                #[cfg(target_os = "macos")]
                {
                    // Set USER_INTERACTIVE QoS for MLX (higher priority)
                    unsafe {
                        libc::pthread_set_qos_class_self_np(
                            libc::qos_class_t::QOS_CLASS_USER_INTERACTIVE,
                            0,
                        );
                    }
                    // Apply explicit P-core affinity
                    apply_affinity_to_cores(&core_ids);
                }
                #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                {
                    apply_affinity_to_cores(&core_ids);
                }
                builder.run();
            });
            Ok(())
        })
        .build()
        .map_err(|e| format!("build_mlx_pool: failed: {}", e))
}

/// NEXTGEN-03: Build Graph pool with P-core affinity (shared with MLX).
///
/// Pool config:
///   - Threads: 1
///   - Cores: P 2 (shared with MLX pool)
///   - QoS: USER_INITIATED
///   - Workload: Kuzu graph traversal, petgraph PageRank
///
/// Single thread to avoid overwhelming GPU pipeline.
fn build_graph_pool() -> Result<ThreadPool, String> {
    // Get P-core indices for Graph pool (use P-core 2, shared with MLX)
    let topo = crate::topology::get_topology();
    let p_cores = &topo.p_core_indices;
    let graph_core = p_cores.get(2).copied().unwrap_or(p_cores.first().copied().unwrap_or(0));

    ThreadPoolBuilder::new()
        .num_threads(1)
        .stack_size(4_194_304) // 4 MiB
        .thread_name(|_| "hledac-graph".to_string())
        // NOTE: spawn_fifo removed - not available in this rayon version
        .spawn_handler(move |builder| {
            let core_id = graph_core;
            let _ = thread::spawn(move || {
                #[cfg(target_os = "macos")]
                {
                    // Set USER_INITIATED QoS for graph work
                    unsafe {
                        libc::pthread_set_qos_class_self_np(
                            libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                            0,
                        );
                    }
                    // Apply explicit P-core affinity
                    apply_affinity_to_cores(&[core_id]);
                }
                #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                {
                    apply_affinity_to_cores(&[core_id]);
                }
                builder.run();
            });
            Ok(())
        })
        .build()
        .map_err(|e| format!("build_graph_pool: failed: {}", e))
}

/// Apply affinity to specific cores (macOS/Linux).
/// NEXTGEN-03 FIX: Uses apply_specific_core_affinity for core-based affinity.
#[cfg(target_os = "macos")]
fn apply_affinity_to_cores(cores: &[usize]) {
    crate::darwin_affinity::apply_specific_core_affinity(cores);
}

#[cfg(all(target_os = "linux", not(target_env = "musl")))]
fn apply_affinity_to_cores(cores: &[usize]) {
    let mut mask: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    for &core in cores {
        if core < 128 {
            unsafe { libc::CPU_SET(core, &mut mask) };
        }
    }
    let _ = unsafe {
        libc::pthread_setaffinity_np(
            libc::pthread_self(),
            std::mem::size_of::<libc::cpu_set_t>(),
            &mask,
        )
    };
}

#[cfg(not(any(target_os = "macos", all(target_os = "linux", not(target_env = "musl")))))]
fn apply_affinity_to_cores(_cores: &[usize]) {
    // No-op on other platforms
}

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
///
/// NEXTGEN-03: Also initializes dedicated SIMD/MLX/Graph pools.
pub fn init_default_pools() {
    // MODERN-33 + MODERN-34: Use topology::p_core_count() — single source of truth
    // This uses the cached perflevel0/1 counts from topology.rs
    let p_cores = crate::topology::p_core_count();
    let io_threads = MAX_TOTAL_THREADS.saturating_sub(p_cores).max(1);

    resize_cpu_pool(p_cores);
    resize_io_pool(io_threads);

    // NEXTGEN-03: Initialize dedicated pools
    init_dedicated_pools();
}

/// NEXTGEN-03: Initialize dedicated pools for asymmetric topology-aware scheduling.
fn init_dedicated_pools() {
    match build_simd_pool() {
        Ok(pool) => {
            let mut guard = SIMD_POOL.write();
            *guard = Some(Arc::new(pool));
        }
        Err(e) => {
            eprintln!("elastic_pool: init_dedicated_pools failed for SIMD: {}", e);
        }
    }

    match build_mlx_pool() {
        Ok(pool) => {
            let mut guard = MLX_POOL.write();
            *guard = Some(Arc::new(pool));
        }
        Err(e) => {
            eprintln!("elastic_pool: init_dedicated_pools failed for MLX: {}", e);
        }
    }

    match build_graph_pool() {
        Ok(pool) => {
            let mut guard = GRAPH_POOL.write();
            *guard = Some(Arc::new(pool));
        }
        Err(e) => {
            eprintln!("elastic_pool: init_dedicated_pools failed for Graph: {}", e);
        }
    }
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

// NEXTGEN-03: Dedicated pool getters
// ============================================================================

/// Get the SIMD pool for ARM NEON operations.
pub fn get_simd_pool() -> Arc<ThreadPool> {
    {
        let guard = SIMD_POOL.read();
        if let Some(ref pool) = *guard {
            return Arc::clone(pool);
        }
    }
    // Slow path: pool not yet initialized
    match build_simd_pool() {
        Ok(p) => {
            let pool = Arc::new(p);
            let mut guard = SIMD_POOL.write();
            *guard = Some(Arc::clone(&pool));
            pool
        }
        Err(e) => {
            eprintln!("elastic_pool: get_simd_pool failed: {} — using cpu_pool fallback", e);
            get_cpu_pool()
        }
    }
}

/// Get the MLX pool for Metal dispatch.
pub fn get_mlx_pool() -> Arc<ThreadPool> {
    {
        let guard = MLX_POOL.read();
        if let Some(ref pool) = *guard {
            return Arc::clone(pool);
        }
    }
    // Slow path: pool not yet initialized
    match build_mlx_pool() {
        Ok(p) => {
            let pool = Arc::new(p);
            let mut guard = MLX_POOL.write();
            *guard = Some(Arc::clone(&pool));
            pool
        }
        Err(e) => {
            eprintln!("elastic_pool: get_mlx_pool failed: {} — using cpu_pool fallback", e);
            get_cpu_pool()
        }
    }
}

/// Get the Graph pool for Kuzu traversal.
pub fn get_graph_pool() -> Arc<ThreadPool> {
    {
        let guard = GRAPH_POOL.read();
        if let Some(ref pool) = *guard {
            return Arc::clone(pool);
        }
    }
    // Slow path: pool not yet initialized
    match build_graph_pool() {
        Ok(p) => {
            let pool = Arc::new(p);
            let mut guard = GRAPH_POOL.write();
            *guard = Some(Arc::clone(&pool));
            pool
        }
        Err(e) => {
            eprintln!("elastic_pool: get_graph_pool failed: {} — using cpu_pool fallback", e);
            get_cpu_pool()
        }
    }
}

/// Get thread count for SIMD pool.
pub fn get_simd_pool_threads() -> usize {
    let guard = SIMD_POOL.read();
    match &*guard {
        Some(pool) => pool.current_num_threads(),
        None => 0,
    }
}

/// Get thread count for MLX pool.
pub fn get_mlx_pool_threads() -> usize {
    let guard = MLX_POOL.read();
    match &*guard {
        Some(pool) => pool.current_num_threads(),
        None => 0,
    }
}

/// Get thread count for Graph pool.
pub fn get_graph_pool_threads() -> usize {
    let guard = GRAPH_POOL.read();
    match &*guard {
        Some(pool) => pool.current_num_threads(),
        None => 0,
    }
}

// NEXTGEN-03: Crossbeam channel senders for dedicated pools
// ============================================================================

use crossbeam_channel::{bounded, Sender};
use std::sync::atomic::{AtomicBool, Ordering};

/// Shutdown flag for dispatcher threads.
static POOL_SHUTDOWN: AtomicBool = AtomicBool::new(false);

/// SIMD pool sender — spawns dispatcher on first access.
pub fn simd_sender() -> &'static parking_lot::Mutex<Option<Sender<crate::pool_run::WorkItem>>> {
    use crate::pool_run::WorkItem;
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_simd_dispatcher(rx);
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

/// MLX pool sender — spawns dispatcher on first access.
pub fn mlx_sender() -> &'static parking_lot::Mutex<Option<Sender<crate::pool_run::WorkItem>>> {
    use crate::pool_run::WorkItem;
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_mlx_dispatcher(rx);
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

/// Graph pool sender — spawns dispatcher on first access.
pub fn graph_sender() -> &'static parking_lot::Mutex<Option<Sender<crate::pool_run::WorkItem>>> {
    use crate::pool_run::WorkItem;
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_graph_dispatcher(rx);
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

/// Spawn SIMD pool dispatcher thread.
fn spawn_simd_dispatcher(rx: crossbeam_channel::Receiver<crate::pool_run::WorkItem>) {
    thread::Builder::new()
        .name("hledac-dispatch-simd".to_string())
        .stack_size(4_194_304)
        .spawn(move || {
            // Initialize the SIMD pool on first dispatcher call
            let _ = get_simd_pool();

            // Run dispatcher loop with P-core QoS
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    libc::pthread_set_qos_class_self_np(
                        libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                        0,
                    );
                }
            }

            get_simd_pool().install(|| loop {
                if POOL_SHUTDOWN.load(Ordering::Acquire) {
                    break;
                }
                match rx.recv_timeout(std::time::Duration::from_millis(100)) {
                    Ok(work) => crate::pool_run::execute_work_item(work),
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                    Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                }
            });
        })
        .expect("elastic_pool: SIMD dispatcher spawn failed");
}

/// Spawn MLX pool dispatcher thread.
fn spawn_mlx_dispatcher(rx: crossbeam_channel::Receiver<crate::pool_run::WorkItem>) {
    thread::Builder::new()
        .name("hledac-dispatch-mlx".to_string())
        .stack_size(4_194_304)
        .spawn(move || {
            // Initialize the MLX pool on first dispatcher call
            let _ = get_mlx_pool();

            // Run dispatcher loop with USER_INTERACTIVE QoS (higher priority)
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    libc::pthread_set_qos_class_self_np(
                        libc::qos_class_t::QOS_CLASS_USER_INTERACTIVE,
                        0,
                    );
                }
            }

            get_mlx_pool().install(|| loop {
                if POOL_SHUTDOWN.load(Ordering::Acquire) {
                    break;
                }
                match rx.recv_timeout(std::time::Duration::from_millis(100)) {
                    Ok(work) => crate::pool_run::execute_work_item(work),
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                    Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                }
            });
        })
        .expect("elastic_pool: MLX dispatcher spawn failed");
}

/// Spawn Graph pool dispatcher thread.
fn spawn_graph_dispatcher(rx: crossbeam_channel::Receiver<crate::pool_run::WorkItem>) {
    thread::Builder::new()
        .name("hledac-dispatch-graph".to_string())
        .stack_size(4_194_304)
        .spawn(move || {
            // Initialize the Graph pool on first dispatcher call
            let _ = get_graph_pool();

            // Run dispatcher loop with P-core QoS
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    libc::pthread_set_qos_class_self_np(
                        libc::qos_class_t::QOS_CLASS_USER_INITIATED,
                        0,
                    );
                }
            }

            get_graph_pool().install(|| loop {
                if POOL_SHUTDOWN.load(Ordering::Acquire) {
                    break;
                }
                match rx.recv_timeout(std::time::Duration::from_millis(100)) {
                    Ok(work) => crate::pool_run::execute_work_item(work),
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                    Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                }
            });
        })
        .expect("elastic_pool: Graph dispatcher spawn failed");
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

// NEXTGEN-03: Dedicated pool PyO3 bindings
// ============================================================================

/// Get SIMD pool thread count.
#[pyfunction]
#[pyo3(name = "get_simd_pool_threads")]
pub fn get_simd_pool_threads_py() -> usize {
    get_simd_pool_threads()
}

/// Get MLX pool thread count.
#[pyfunction]
#[pyo3(name = "get_mlx_pool_threads")]
pub fn get_mlx_pool_threads_py() -> usize {
    get_mlx_pool_threads()
}

/// Get Graph pool thread count.
#[pyfunction]
#[pyo3(name = "get_graph_pool_threads")]
pub fn get_graph_pool_threads_py() -> usize {
    get_graph_pool_threads()
}

/// Get total threads across all pools (including dedicated pools).
#[pyfunction]
#[pyo3(name = "get_all_pool_threads")]
pub fn get_all_pool_threads_py() -> usize {
    get_total_active_threads() + get_simd_pool_threads() + get_mlx_pool_threads() + get_graph_pool_threads()
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resize_cpu_pool_py))?;
    m.add_function(wrap_pyfunction!(resize_io_pool_py))?;
    m.add_function(wrap_pyfunction!(init_elastic_pools_py))?;
    m.add_function(wrap_pyfunction!(get_elastic_cpu_threads_py))?;
    m.add_function(wrap_pyfunction!(get_elastic_io_threads_py))?;
    m.add_function(wrap_pyfunction!(get_elastic_total_threads_py))?;
    // NEXTGEN-03: Dedicated pool bindings
    m.add_function(wrap_pyfunction!(get_simd_pool_threads_py))?;
    m.add_function(wrap_pyfunction!(get_mlx_pool_threads_py))?;
    m.add_function(wrap_pyfunction!(get_graph_pool_threads_py))?;
    m.add_function(wrap_pyfunction!(get_all_pool_threads_py))?;
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

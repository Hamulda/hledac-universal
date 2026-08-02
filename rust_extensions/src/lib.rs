//! hledac-rust-extensions - High-performance Rust extensions for hledac OSINT platform.
//!
//! Provides native-speed implementations of:
//! - Aho-Corasick multi-pattern matching
//! - BloomFilter for URL deduplication
//! - Rolling hash for content fingerprinting
//! - IOC extraction and URL normalization
//! - IOC deduplication (cross-sprint persistence)
//! - xxHash3-64 for non-cryptographic hashing
//! - SimHash for near-duplicate document detection
//! - URL classification by transport class (onion/i2p/freenet/clearnet)
//! - SHA-256 + BLAKE3 content hashing (TLS cert fingerprint, body dedup)
//! - BLAKE2b-128 quality-gate fingerprint + entropy + text normalization (Sprint P1-5)

use pyo3::prelude::*;
use rayon::ThreadPool;
use rayon::ThreadPoolBuilder;
use std::sync::LazyLock;

// ISSUE-014: Removed custom lazy_static! macro — Rust 1.80+ std::sync::LazyLock
// is stable and ships with the 2024 edition. Each module now uses LazyLock directly.

pub mod aho_corasick;
pub mod query_terms; // B4: Aho-Corasick query-context scan + whitespace trim
#[cfg(feature = "bloom")]
pub mod bloom;
pub mod compress;
#[cfg(feature = "advanced")]
pub mod regex_lz4; // LZ4-compressed pattern store for 10k+ patterns
#[cfg(feature = "advanced")]
pub mod content_hasher;
#[cfg(feature = "core")]
pub mod crypto_accelerate;
pub mod xxhash_ext; // xxHash3-64 for non-cryptographic content hashing
// ISSUE-014: adaptive_scheduler is always compiled — used by always-compiled modules
// (claims_extraction, health). Previously gated behind advanced feature.
pub mod adaptive_scheduler;
#[cfg(feature = "data")]
pub mod rolling_hash; // P3-3: Rolling hash for content fingerprinting
pub mod graph_traverse;
#[cfg(feature = "graph")]
pub mod graph_centrality;
#[cfg(feature = "graph")]
pub mod graph_analytics; // GRAPH-01: petgraph-based PageRank + Louvain + SCC
#[cfg(feature = "graph")]
pub mod hot_edges_rs;
pub mod html_parse;
pub mod int_counter_layout;
pub mod xml_sanitize; // R7c: XML sanitization — strip DOCTYPE/ENTITY declarations
pub mod ioc_dedup;
pub mod ioc_patterns;
pub mod ioc_patterns_generated; // Issue #031: generated from ioc_patterns.rs (codegen)
pub mod dns_tunnel; // ISSUE #33: DNS tunneling detection (entropy, n-gram, wavelet)
pub mod ioc_extract;
pub mod ioc_extract_fast;
pub mod ioc_extract_simd; // R4.3: SIMD IOC extraction via regex-automata build_many (NEON on M1)
pub mod ioc_cooccurrence_rs; // Issue 4.1: Rust HashMap<->BitSet co-occurrence engine
pub mod ip_parse; // Sprint P2-3: IP address parsing, classification, CIDR containment
pub mod lmdb_dht; // ISSUE-004: Rust LMDB backend for DHT — eliminates asyncio.to_thread overhead
pub mod madvise;
pub mod os_unfair_lock; // ISSUE 4.3: Darwin os_unfair_lock (~5ns) vs parking_lot::Mutex (~25ns)
pub mod memory;
#[cfg(feature = "metal")]
pub mod metal_compute; // R22: Metal GPU batch matmul for MoE router (CPU fallback always available)
#[cfg(feature = "accelerate")]
pub mod accelerate; // R22: Accelerate/vDSP FFI for NER cosine similarity (scalar fallback on non-macOS)
pub mod quality_gate;
pub mod _entropy; // Shared entropy helpers — broken out to avoid circular quality_gate ↔ zero_copy
#[cfg(feature = "advanced")]
pub mod signal_batch;
#[cfg(feature = "advanced")]
pub mod simd_similarity;
pub mod simhash_ext;
#[cfg(feature = "graph")]
pub mod lsh_index; // F320+: LSH index for O(1) near-duplicate detection at scale
pub mod text_similarity; // R25: Text similarity via trigram Jaccard
pub mod text_norm;
#[cfg(feature = "advanced")]
pub mod feed_decision;
#[cfg(feature = "advanced")]
pub mod feed_pipeline;
#[cfg(feature = "advanced")]
pub mod pipeline_compose; // Multi-stage pipeline operators via rayon
pub mod url_engine;
pub mod url_ops;
pub mod url_set;
pub mod zero_copy;
pub mod serde_json_rs;
#[cfg(feature = "stix")]
pub mod stix_2_1;
#[cfg(feature = "data")]
pub mod arrow_batch_builder;
#[cfg(feature = "data")]
pub mod parquet_reader; // F320+: Lazy parquet reader — paginated Arrow, 100GB+ IOC history bez OOM
pub mod sendfile; // ISSUE 4.4: sendfile(2) zero-copy file-to-socket transfer (Darwin only)
pub mod spsc_queue;
pub mod mpsc_pool; // Bounded MPSC pool — replaces asyncio.Queue in evidence_log
#[cfg(feature = "advanced")]
pub mod federated_qtable; // ISSUE-23: Rust Q-table with rayon parallel batch updates
pub mod simd; // ISSUE-023: Modular SIMD — NEON on M1, scalar fallback
#[cfg(feature = "advanced")]
pub mod graph_cache;    // TinyLFU LRU cache pro graph operations
// dedup_bloom is always compiled — used by health (always compiled).
// Previously gated behind advanced feature but health.rs calls dedup_bloom::global_stats().
pub mod sprint_policies; // RL sprint policy layer
pub mod dedup_bloom;    // Distribuovaný BloomFilter s Count-Min Sketch
pub mod rate_limit;     // ISSUE #016: NVD API rate limiter — token bucket + MPSC
pub mod telemetry_agg;  // Real-time metrics aggregation
pub mod health;         // Issue #22: health_check() endpoint
#[cfg(feature = "otel")]
pub mod tracing;        // R24: OpenTelemetry tracing for Rust-side observability
#[cfg(feature = "advanced")]
pub mod circuit_breaker;
#[cfg(feature = "data")]
pub mod aimd_controller; // ISSUE 2.2: Lock-free AIMD controller replacing Python AIMDWindow + _AIMDSlotController
pub mod claims_extraction; // ISSUE-27: CPU-bound claims extraction (polarity, confidence, sentence split)
pub mod tls_metadata;    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback
#[cfg(feature = "quic")]
pub mod quic;           // QUIC/HTTP3 via quinn + h3 — F350M-R: real HTTP/3 fallback
pub mod tls13;          // TLS 1.3 JA4 fingerprinting + ECH detection via rustls
#[cfg(feature = "pdf")]
pub mod pdf;            // PDF text extraction + IOC extraction via lopdf
#[cfg(feature = "office")]
pub mod office;        // Office document text extraction (.docx, .xlsx, .pptx) via docx-rs + calamine
#[cfg(feature = "dns")]
pub mod dns;            // DoH/DoT/DoQ DNS via hickory-dns — replaces batch_dns.py triplicate paths
pub mod gil;            // F5.2: GIL management — std::thread + rayon pools (ne pyo3-async)
pub mod pool_run;      // R2: Rayon pool runners — GIL wrappers + channel-based dispatch (consolidated)
pub mod mlx_bridge;    // ISSUE #015: MLX async token streaming bridge + adaptive buffering
#[cfg(feature = "ane")]
pub mod ane;           // Apple Neural Engine bindings — model registry, batch validation, telemetry
pub mod collections;    // Bounded ring buffers — recent_iocs ring, M1 8GB safe (dir)
#[cfg(feature = "data")]
pub mod async_query; // R26: Async DuckDB queries via Rust executor
pub mod data;           // DuckDB bridge — isolated module for future cdylib extraction
pub mod onion_validation; // GRAPH-03: .onion v3 address validation (Ed25519 checksum)
#[cfg(feature = "fulltext")]
pub mod fulltext_index;  // ISSUE-011: Tantivy fulltext search (mmap-backed, zero-copy BM25)

// ---------------------------------------------------------------------------
// Rayon thread pools — M1 8GB safe, P/E core optimized
// ---------------------------------------------------------------------------
//
// M1 Air: 4P + 4E cores = 8 logical CPUs.
// MacBook Pro M3 Pro: 6P + 6E = 12 logical CPUs.
//
// ISSUE-008: Two-tier strategy with P-core detection + QoS hints:
//
//   ┌─────────────────────────────────────────────────────────────────────────┐
//   │ WORKLOAD TYPE         │ THREADS        │ POOL          │ MODULES         │
//   ├───────────────────────┼────────────────┼───────────────┼─────────────────┤
//   │ CPU-bound (SIMD/hot)  │ p_cores (1-4) │ cpu_pool()    │ quality_gate,   │
//   │                       │                │               │ xxhash_par, simd │
//   │ I/O-bound (DuckDB)    │ 2              │ io_pool()     │ graph_traverse, │
//   │                       │                │               │ compress         │
//   │ Mixed (IOC extract)   │ 1-2 adaptive   │ mixed_pool()  │ url_ops, ioc_fast, simhash, html_parse  │
//   └───────────────────────┴────────────────┴───────────────┴─────────────────┘
//
// P-core detection:
//   - macOS: sysctl hw.perflevel0.logicalcpu → perf-level P-core count
//   - Linux/Windows: num_cpus::get_physical() fallback
//   - Clamped to [1, 4] for M1 8GB RAM budget safety
//
// QoS hints (macOS): USER_INITIATED → scheduler preferuje P-cores
// Linux: SCHED_BATCH for CPU-bound threads
//
// Calibration (F270 + ISSUE-008):
//   - CPU-bound threshold: 32 items (was 64 for 2-thread)
//   - I/O-bound threshold: 64 items (DuckDB conn setup amortized)
//   - Chunk: p_cores threads × 32 items (CPU-bound)
//   - Chunk: 2 threads × 64 items = 128 (I/O-bound)

// MIXED_THRESHOLD removed — now fully delegated to adaptive_scheduler::mixed_threshold()
// which is pressure-aware (16 idle / 32 normal / 64 pressure).
// MLX Metal-aware threshold: adaptive_scheduler::mixed_threshold_via_metal()

/// Returns the number of performance cores (P-cores) on Apple Silicon.
///
/// Uses `sysctlbyname(2)` directly — NO fork+exec syscall.
/// LazyLock ensures this is called exactly ONCE per process lifetime.
///
/// macOS: hw.perflevel0.logicalcpu → P-core count, clamped to [1, 4]
///         (M1 Air has 4 P-cores, M3 Pro up to 12, clamp protects 8GB RAM budget)
/// Linux/Windows: num_cpus::get_physical() fallback.
///         Clamped to [1, 4] for M1 8GB RAM budget safety.
///
/// MacBook Pro M3 Pro (12 cores) → 6 P-cores → clamp to 4.
#[cfg(target_os = "macos")]
fn detect_p_core_count() -> usize {
    // ISSUE-12 FIX: Use sysctlbyname(2) directly — raw Mach syscall, no fork+exec
    // Previous: Command::new("sysctl").output() → fork()+exec() = ~1-2ms
    // Now: libc::sysctlbyname() → ~100ns (10,000× faster)
    let mut size: libc::size_t = std::mem::size_of::<u32>();
    let mut value: u32 = 0;

    let ret = unsafe {
        libc::sysctlbyname(
            b"hw.perflevel0.logicalcpu\0".as_ptr() as *const libc::c_char,
            &mut value as *mut _ as *mut libc::c_void,
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };

    if ret == 0 {
        return (value as usize).clamp(1, 4);
    }

    // Fallback: total physical CPUs (may include E-cores on big.LITTLE)
    let mut size2: libc::size_t = std::mem::size_of::<u32>();
    let mut value2: u32 = 0;
    let ret2 = unsafe {
        libc::sysctlbyname(
            b"hw.physicalcpu\0".as_ptr() as *const libc::c_char,
            &mut value2 as *mut _ as *mut libc::c_void,
            &mut size2,
            std::ptr::null_mut(),
            0,
        )
    };

    if ret2 == 0 {
        return (value2 as usize).clamp(1, 4);
    }

    // Krajní fallback: 4 P-cores (M1 Air default)
    4
}

#[cfg(not(target_os = "macos"))]
fn detect_p_core_count() -> usize {
    num_cpus::get_physical().clamp(1, 4)
}

/// Nastaví QoS třídu pro macOS scheduler.
/// Volá se uvnitř rayon worker thread (NE v spawn_handler parent).
#[cfg(target_os = "macos")]
fn apply_qos_hint() {
    // ISSUE-FIX: pthread_set_qos_class_np was removed from Apple Silicon support.
    // Use pthread_set_qos_class_self_np instead — sets QoS for current thread.
    // Falls back silently if unavailable (non-fatal).
    unsafe {
        use libc::pthread_set_qos_class_self_np;
        // QoS_CLASS_USER_INITIATED = 0x9, but we use the constant directly
        // to avoid libc version compatibility issues
        let qos = libc::qos_class_t::QOS_CLASS_USER_INITIATED;
        pthread_set_qos_class_self_np(qos, 0);
    }
}

/// Linux: P-core affinity via pthread_setaffinity_np.
/// Pin na prvních `p_cores` fyzických jader.
#[cfg(all(target_os = "linux", not(target_env = "musl")))]
fn apply_affinity_hint(p_cores: usize) {
    // cpu_set_t = [u64; 16] = 1024 bits = max 128 CPU v kernelu
    let mut mask: libc::cpu_set_t = unsafe { std::mem::zeroed() };

    for i in 0..p_cores.min(128) {
        unsafe { libc::CPU_SET(i, &mut mask) };
    }

    // SAFETY: mask is zeroed + valid, pthread_setaffinity_np is async-signal-safe
    let ret = unsafe {
        libc::pthread_setaffinity_np(
            libc::pthread_self(),
            std::mem::size_of::<libc::cpu_set_t>(),
            &mask,
        )
    };
    // Non-fatal: unprivileged users may lack CAP_SYS_NICE
    let _ = ret;
}

#[cfg(all(target_os = "linux", target_env = "musl"))]
fn apply_affinity_hint(_p_cores: usize) {
    // musl: sched_setaffinity not available — skip silently
}

#[cfg(not(any(target_os = "macos", all(target_os = "linux", not(target_env = "musl")))))]
fn apply_affinity_hint(_p_cores: usize) {
    // Windows / other: no-op
}

/// Process-wide singleton — P-core ceiling for CPU-bound work.
///
/// Shared by quality_gate, xxhash_ext parallel, simd_similarity.
///
/// p_cores threads × 4 MiB = 4–16 MB total stack.
/// P-core count = hw.perflevel0.logicalcpu on Apple Silicon (clamped 1-4).
///
/// Thread count is STATIC (set at pool creation):
///   - rayon ThreadPool is a singleton, cannot be reconfigured at runtime
///   - Dynamic thread count handled at CALL SITE via adaptive_scheduler
///     recommended_cpu_threads() + mixed_pool() fallback
///
/// Use when: BLAKE2b, xxhash parallel, cosine similarity on embeddings.
pub(crate) fn cpu_pool() -> &'static ThreadPool {
    static POOL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        let p_cores = detect_p_core_count();

        ThreadPoolBuilder::new()
            .num_threads(p_cores)
            .stack_size(4_194_304) // 4 MiB — SIMD BLAKE2b/xxhash stack safety
            .thread_name(|i| format!("hledac-cpu-{}", i))
            .spawn_handler(move |thread| {
                std::thread::spawn(move || {
                    // QoS / affinity hint uvnitř spawned thread — správné vlákno
                    #[cfg(target_os = "macos")]
                    apply_qos_hint();
                    #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                    apply_affinity_hint(p_cores);
                    thread.run();
                });
                Ok(())
            })
            .build()
            .expect("cpu_pool: ThreadPoolBuilder::build failed (OOM?)")
    });
    &POOL
}

/// Process-wide singleton — 2-thread ceiling for I/O-bound work.
///
/// Shared by graph_traverse (DuckDB read-only), compress.
///
/// 2 threads × 4 MiB = 8 MB total stack.
/// DuckDB thread-local connection is the bottleneck — 2 threads matches the
/// F265-U5 thread-local pool ceiling.
/// QoS hint = USER_INITIATED (stejně jako cpu_pool) — I/O-bound benefituje z P-core.
pub(crate) fn io_pool() -> &'static ThreadPool {
    static POOL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(4_194_304) // 4 MiB — DuckDB stmt compilation stack
            .thread_name(|i| format!("hledac-io-{}", i))
            .spawn_handler(|thread| {
                std::thread::spawn(move || {
                    // QoS hint uvnitř spawned thread (io_pool = 2 threads, P-core benefit)
                    #[cfg(target_os = "macos")]
                    apply_qos_hint();
                    #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                    apply_affinity_hint(2);
                    thread.run();
                });
                Ok(())
            })
            .build()
            .expect("io_pool: ThreadPoolBuilder::build failed (OOM?)")
    });
    &POOL
}

/// Per-call memory-bounded thread pool for mixed workloads.
///
/// Pattern: `mixed_pool(n).install(|| { ... })`
///
/// Threshold is adaptive: 16 (idle), 32 (normal), 64 (memory pressure).
/// Via adaptive_scheduler::mixed_threshold() — CPU + memory aware.
///
/// Returns a 1-thread pool when n < adaptive threshold:
///   Eliminates pool spawn overhead (~0.5ms) for small batches where
///   serial execution is faster than parallel.
///
/// Returns a 2-thread pool when n >= adaptive threshold:
///   Balances thread-spawn overhead vs parallel speedup for IOC extract,
///   URL ops, simhash, html_parse workloads.
///
/// Implementation: two separate `LazyLock<ThreadPool>` statics (POOL_SINGLE /
/// POOL_PAIR), selected by item count. Zero Mutex, zero HashMap.
pub(crate) fn mixed_pool(n_items: usize) -> &'static ThreadPool {
    static POOL_SINGLE: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(1)
            .stack_size(4_194_304) // 4 MiB — mixed workload stack safety
            .thread_name(|i| format!("hledac-mixed-1-{}", i))
            .spawn_handler(|thread| {
                std::thread::spawn(move || {
                    #[cfg(target_os = "macos")]
                    apply_qos_hint();
                    #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                    apply_affinity_hint(1);
                    thread.run();
                });
                Ok(())
            })
            .build()
            .expect("mixed_pool(1): ThreadPoolBuilder::build failed (OOM?)")
    });
    static POOL_PAIR: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(4_194_304) // 4 MiB — mixed workload stack safety
            .thread_name(|i| format!("hledac-mixed-2-{}", i))
            .spawn_handler(|thread| {
                std::thread::spawn(move || {
                    #[cfg(target_os = "macos")]
                    apply_qos_hint();
                    #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                    apply_affinity_hint(2);
                    thread.run();
                });
                Ok(())
            })
            .build()
            .expect("mixed_pool(2): ThreadPoolBuilder::build failed (OOM?)")
    });

    if n_items < adaptive_scheduler::mixed_threshold() {
        &POOL_SINGLE
    } else {
        &POOL_PAIR
    }
}

/// F350M-R 5.5: Set QoS class for the CURRENT thread (calling thread).
///
/// QoS classes on macOS:
///   0x1 = QOS_CLASS_BACKGROUND (lowest priority — vacuum/close threads)
///   0x2 = QOS_CLASS_UTILITY
///   0x3 = QOS_CLASS_DEFAULT
///   0x6 = QOS_CLASS_INTERACTIVE
///   0x9 = QOS_CLASS_USER_INITIATED (highest priority — inference threads)
///
/// B-5 fix: pthread_id removed — pthread_set_qos_class_self_np ALWAYS sets
/// the calling thread, so the target pthread_id parameter was meaningless.
/// Callers (ThreadPoolExecutor workers) invoke this from the target thread,
/// so calling-thread semantics are correct.
///
/// B-13 fix: qos_class_i32_to_qos_class_t() uses a local #[repr(i32)] enum
/// for safe conversion instead of raw transmute, with a compile-time size
/// assertion that sizeof(qos_class_t) == 4 bytes.
///
/// Returns 0 on success, -1 on failure (errno set).
#[cfg(target_os = "macos")]
mod qos_class_helpers {
    use libc::qos_class_t;

    /// macOS QoS class raw values — mirrors libc::qos_class_t layout.
    #[derive(Debug, Clone, Copy)]
    #[repr(i32)]
    pub enum QosClassRaw {
        Background = 0x1,
        Utility = 0x2,
        Default = 0x3,
        Interactive = 0x6,
        UserInitiated = 0x9,
    }

    // Compile-time assertion: qos_class_t must be 4 bytes (i32/u32).
    // Catches libc version changes that alter the type layout.
    const _: () = assert!(std::mem::size_of::<qos_class_t>() == 4,
        "qos_class_t must be 4 bytes (i32); check libc version");

    /// Safely convert a raw i32 QoS class constant to libc::qos_class_t.
    #[inline]
    pub fn qos_class_i32_to_qos_class_t(raw: i32) -> qos_class_t {
        // SAFETY: raw values 0x1/0x2/0x3/0x6/0x9 are valid QoS class discriminants
        // guaranteed by the macOS ABI for qos_class_t (which is a signed int).
        // The #[repr(i32)] enum has identical memory layout.
        let qos: QosClassRaw = match raw {
            0x1 => QosClassRaw::Background,
            0x2 => QosClassRaw::Utility,
            0x3 => QosClassRaw::Default,
            0x6 => QosClassRaw::Interactive,
            0x9 => QosClassRaw::UserInitiated,
            _ => QosClassRaw::Default,
        };
        // transmute from our known-#[repr(i32)] enum to libc::qos_class_t.
        // Both are i32-sized; transmute is sound because QosClassRaw is
        // #[repr(i32)] and qos_class_t verified == 4 bytes above.
        unsafe { std::mem::transmute(qos as i32) }
    }
}

#[cfg(target_os = "macos")]
#[pyfunction]
pub fn apply_current_thread_qos(qos_class: i32) -> i32 {
    use qos_class_helpers::qos_class_i32_to_qos_class_t;
    let qos = qos_class_i32_to_qos_class_t(qos_class);
    // SAFETY: pthread_set_qos_class_self_np sets the QoS of the calling thread
    // (no target pthread needed — that's why pthread_id was removed in B-5).
    unsafe {
        libc::pthread_set_qos_class_self_np(qos, 0);
    }
    0
}

#[cfg(not(target_os = "macos"))]
#[pyfunction]
pub fn apply_current_thread_qos(_qos_class: i32) -> i32 {
    0 // No-op on non-macOS
}

/// B-5 backward-compatible wrapper: ignores pthread_id and delegates to
/// apply_current_thread_qos. Kept so Python callers don't need to change.
#[cfg(target_os = "macos")]
#[pyfunction]
pub fn apply_thread_qos(pthread_id: usize, qos_class: i32) -> i32 {
    let _ = pthread_id as usize; // intentionally ignored (B-5 fix)
    apply_current_thread_qos(qos_class)
}

#[cfg(not(target_os = "macos"))]
#[pyfunction]
pub fn apply_thread_qos(_pthread_id: usize, _qos_class: i32) -> i32 {
    0 // No-op on non-macOS
}


#[cfg(test)]
mod lib_tests {
    use super::*;

    // -------------------------------------------------------------------------
    // cpu_pool tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_cpu_pool_idempotent() {
        let a = cpu_pool() as *const ThreadPool;
        let b = cpu_pool() as *const ThreadPool;
        assert_eq!(a, b, "cpu_pool() must return a stable singleton");
    }

    #[test]
    fn test_cpu_pool_thread_count() {
        // p_cores threads = dynamically detected P-core count (1-4)
        let p_cores = detect_p_core_count();
        assert!(
            p_cores >= 1 && p_cores <= 4,
            "p_cores must be 1-4, got {}",
            p_cores
        );
        assert_eq!(
            cpu_pool().current_num_threads(),
            p_cores,
            "cpu_pool thread count must match detected p_cores"
        );
    }

    #[test]
    fn test_detect_p_core_count_bounds() {
        let n = detect_p_core_count();
        assert!(n >= 1 && n <= 4, "p_core count {} out of range 1-4", n);
    }

    // -------------------------------------------------------------------------
    // io_pool tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_io_pool_idempotent() {
        let a = io_pool() as *const ThreadPool;
        let b = io_pool() as *const ThreadPool;
        assert_eq!(a, b, "io_pool() must return a stable singleton");
    }

    #[test]
    fn test_io_pool_thread_count() {
        // 2 threads = DuckDB thread-local ceiling
        assert_eq!(io_pool().current_num_threads(), 2);
    }

    // -------------------------------------------------------------------------
    // mixed_pool tests (adaptive threshold, pressure=1=normal)
    // -------------------------------------------------------------------------

    #[test]
    fn test_mixed_pool_small() {
        // Set pressure=1 (NORMAL_THRESHOLD=32), threshold=32
        // n=31 < 32 → 1 thread
        adaptive_scheduler::update_memory_pressure(1);
        let pool = mixed_pool(31);
        assert_eq!(pool.current_num_threads(), 1, "n=31 < threshold=32 (normal) → 1 thread");
    }

    #[test]
    fn test_mixed_pool_large() {
        // Set pressure=1 (NORMAL_THRESHOLD=32), threshold=32
        // n=32 >= 32 → 2 threads
        adaptive_scheduler::update_memory_pressure(1);
        let pool = mixed_pool(32);
        assert_eq!(pool.current_num_threads(), 2, "n=32 >= threshold=32 (normal) → 2 threads");
    }

    #[test]
    fn test_mixed_pool_reuse() {
        // Same thread count → same static pool instance (pointer equality)
        adaptive_scheduler::update_memory_pressure(1);
        let a = mixed_pool(10) as *const ThreadPool;
        let b = mixed_pool(10) as *const ThreadPool;
        assert_eq!(a, b, "mixed_pool(10) must reuse POOL_SINGLE");
        let c = mixed_pool(200) as *const ThreadPool;
        let d = mixed_pool(200) as *const ThreadPool;
        assert_eq!(c, d, "mixed_pool(200) must reuse POOL_PAIR");
    }

    #[test]
    fn test_mixed_pool_adaptive_idle() {
        // Idle (pressure=0): threshold=16, n=31 >= 16 → 2 threads
        adaptive_scheduler::update_memory_pressure(0);
        let pool = mixed_pool(31);
        assert_eq!(pool.current_num_threads(), 2, "idle: n=31 >= threshold=16 → 2 threads");
    }

    #[test]
    fn test_mixed_pool_adaptive_pressure() {
        // Pressure (pressure=2): threshold=64, n=31 < 64 → 1 thread
        adaptive_scheduler::update_memory_pressure(2);
        let pool = mixed_pool(31);
        assert_eq!(pool.current_num_threads(), 1, "pressure: n=31 < threshold=64 → 1 thread");
    }

    // batch_sha256 tests (Issue #9: parallel for large batches)
    // -------------------------------------------------------------------------

    #[test]
    fn test_batch_sha256_small_serial() {
        // n=4 < 128 → serial path (cpu_pool not used)
        let input: Vec<String> = (0..4).map(|i| format!("item{}", i)).collect();
        let results = ioc_extract::batch_sha256(input.clone());
        assert_eq!(results.len(), 4);
        // Verify all are valid 64-char hex SHA256
        for h in &results {
            assert_eq!(h.len(), 64);
            assert!(h.chars().all(|c| c.is_ascii_hexdigit()), "invalid hex: {}", h);
        }
        // Two identical inputs produce identical hashes
        assert_eq!(results[0], results[0]);
        assert_ne!(results[0], results[1]);
    }

    #[test]
    fn test_batch_sha256_large_parallel() {
        // n=256 >= 128 → cpu_pool parallel path
        adaptive_scheduler::update_memory_pressure(1); // normal = threshold 32
        let input: Vec<String> = (0..256).map(|i| format!("batch_sha256_item_{}", i)).collect();
        let results = ioc_extract::batch_sha256(input.clone());
        assert_eq!(results.len(), 256);
        for h in &results {
            assert_eq!(h.len(), 64);
            assert!(h.chars().all(|c| c.is_ascii_hexdigit()), "invalid hex: {}", h);
        }
    }

    #[test]
    fn test_batch_sha256_empty() {
        let input: Vec<String> = vec![];
        let results = ioc_extract::batch_sha256(input);
        assert!(results.is_empty());
    }

    #[test]
    fn test_batch_sha256_deterministic() {
        // Same input → same hash (no randomness in SHA256)
        let input: Vec<String> = vec!["deterministic_test".to_string()];
        let a = ioc_extract::batch_sha256(input.clone());
        let b = ioc_extract::batch_sha256(input);
        assert_eq!(a, b);
    }
}

/// Parse a version string like "1.2.3" into a (major, minor, patch) tuple.
/// Falls back to (0, 0, 0) on parse failure.
fn _parse_version(version_str: &str) -> (u64, u64, u64) {
    let parts: Vec<&str> = version_str.trim().split('.').collect();
    let major = parts.get(0).and_then(|s| s.parse().ok()).unwrap_or(0);
    let minor = parts.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
    let patch = parts.get(2).and_then(|s| s.parse().ok()).unwrap_or(0);
    (major, minor, patch)
}

/// __version_info__() -> (u64, u64, u64)
/// Returns the parsed package version as a tuple for Python tuple comparison.
/// Python side can do: `if ext.__version_info__() >= (0, 1, 1): ...`
#[pyfunction]
fn __version_info__() -> (u64, u64, u64) {
    _parse_version(env!("CARGO_PKG_VERSION"))
}

/// __abi_version() -> (u32, u32, u32)
/// Returns the ABI version as a semver-like tuple (major, minor, patch).
///
/// ABI tuple increments whenever the Rust extension's public API changes
/// in a backward-incompatible way (new required arguments, removed functions,
/// changed return types, changed struct layouts). Python code should check
/// this at import time and fail fast if the ABI version doesn't match.
///
/// bumping rules:
///   - major (X.0.0): API removed or changed — old callers MUST update
///   - minor (0.X.0): new optional API added — old callers still work
///   - patch (0.0.X): bug fixes, no API changes — always compatible
///
/// Current ABI version: (1, 0, 0)
const ABI_VERSION: (u32, u32, u32) = (1, 0, 0);

#[pyfunction]
fn __abi_version__() -> (u32, u32, u32) {
    ABI_VERSION
}

/// __py_version__() -> (u32, u32, u32)
/// Returns the Python version the extension was compiled for.
///
/// Python side uses this to detect ABI mismatch when running under
/// a different Python version than the one used to build this binary.
#[cfg(feature = "py-version")]
#[pyfunction]
fn __py_version__() -> (u32, u32, u32) {
    // Parse PYTHON_VERSION environment variable set at build time by maturin.
    // maturin sets PYTHON_VERSION=<major>.<minor>.<patch> when invoking cargo.
    // Fallback to (0, 0, 0) if not set (old build before py-version feature).
    let py_ver = env!("PYTHON_VERSION");
    let parts: Vec<&str> = py_ver.split('.').collect();
    (
        parts.get(0).and_then(|s| s.parse().ok()).unwrap_or(0),
        parts.get(1).and_then(|s| s.parse().ok()).unwrap_or(0),
        parts.get(2).and_then(|s| s.parse().ok()).unwrap_or(0),
    )
}

/// __apple_target__() -> String
/// Returns the compiler target triple this extension was built for.
/// e.g. "aarch64-apple-darwin", "x86_64-apple-darwin", "arm64-apple-ios".
/// Python side uses this to detect M1/M2/M3 vs Intel vs iOS mismatches.
#[pyfunction]
fn __apple_target__() -> String {
    // option_env! returns None if not set (e.g., in maturin cross-compile)
    option_env!("CARGO_CFG_TARGET_triple")
        .map(|s| s.to_string())
        .unwrap_or_else(|| "unknown".to_string())
}

/// __features__() -> Vec<String>
/// Returns the list of enabled Cargo feature flags at build time.
/// Python uses this for feature-aware capability validation against the manifest:
///   features = set(ext.__features__())
///   manifest = json.loads(...)
///   # Filter symbols to only those whose module is in enabled features
///   enabled_symbols = [s for s in manifest["all_lib_rs"]
///                     if _module_for_symbol(s, manifest["lib_rs_symbols"]) in features
///                     or _is_core_symbol(s)]
#[pyfunction]
fn __features__() -> Vec<String> {
    // CARGO_FEATURES_LIST is set by build.rs — format: "feature1,feature2,feature3"
    let env_val = option_env!("CARGO_FEATURES_LIST")
        .map(|s| s.to_string())
        .unwrap_or_default();
    if env_val.is_empty() {
        Vec::new()
    } else {
        env_val.split(',').map(|s| s.to_string()).collect()
    }
}

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Expose package version for Python-side ABI compatibility checking (F275).
    // CARGO_PKG_VERSION is set by Cargo at compile time from Cargo.toml.
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(__version_info__, m)?)?;
    // ABI version for backward-compatibility enforcement (ISSUE-040)
    // Semver-like tuple: (major, minor, patch) — major bump = breaking change
    m.add_function(wrap_pyfunction!(__abi_version__, m)?)?;
    // Python version compiled-for (py-version feature, requires pyo3-build-config)
    #[cfg(feature = "py-version")]
    m.add_function(wrap_pyfunction!(__py_version__, m)?)?;
    // Apple target triple for M1/M2/M3 vs Intel detection
    m.add_function(wrap_pyfunction!(__apple_target__, m)?)?;
    // Feature-aware capability: list of enabled Cargo features at build time
    m.add_function(wrap_pyfunction!(__features__, m)?)?;

    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<aho_corasick::PatternHit>()?;  // Issue #37: zero-copy hit struct

    // B4: Query-context multi-pattern scanner — replaces 4× Python str.find loops
    query_terms::register_functions(m)?;

    #[cfg(feature = "bloom")]
    {
        m.add_class::<bloom::BloomFilter>()?;
        // F266-U1: file-backed mmap Bloom filter (persists across restart).
        bloom::register(m)?;
    }
    // P3-3: ephemeral batch Bloom filter check.
    #[cfg(feature = "bloom")]
    m.add_function(wrap_pyfunction!(bloom::bloom_check_batch, m)?)?;

    // URL dedup via FNV-1a hashing — both in-memory and mmap-backed
    m.add_class::<url_set::MmapUrlSet>()?;
    m.add_class::<url_set::UrlSet>()?;

    // IOC extraction + URL normalization
    dns_tunnel::register_functions(m)?;  // ISSUE #33: entropy, n-gram, wavelet analysis
    // ISSUE-008: ioc_extract provides has_* functions (uses ioc_patterns.rs, single source)
    ioc_extract::register_functions(m)?;
    // Fast IOC extraction: unified Aho-Corasick automaton (single O(n) scan)
    m.add_function(wrap_pyfunction!(ioc_extract_fast::ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified_python, m)?)?;
    // Issue #15: structured entities with positions — replaces Python 25× re.finditer() post-pass
    m.add_function(wrap_pyfunction!(ioc_extract_fast::extract_structured_entities_py, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_extract_structured_entities_py, m)?)?;
    // R4.3: SIMD IOC extraction — regex-automata build_many (NEON on M1, ~5× faster for bulk text ≥4KB)
    ioc_extract_simd::register_functions(m)?;

    // PDF extraction via lopdf — pure Rust PDF parser (~10× vs Python pypdf)
    // Feature-gated: pdf = ["dep:lopdf"] — enables pdf.extract_text(), pdf.extract_iocs()
    #[cfg(feature = "pdf")]
    {
        m.add_function(wrap_pyfunction!(pdf::extract_text, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_text_from_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_text_and_iocs, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_text_and_iocs_from_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_iocs, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_iocs_from_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_metadata, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_metadata_from_bytes, m)?)?;
        m.add_class::<pdf::PdfMetadata>()?;
    }

    // Office document extraction via docx-rs + calamine — pure Rust (~5-10× vs Python)
    // Feature-gated: office = ["dep:docx-rs", "dep:calamine"]
    // Enables: office.extract_text(), office.extract_iocs(), office.extract_text_from_bytes()
    // Python fallback: python-docx + openpyxl in content_miner.py
    #[cfg(feature = "office")]
    {
        m.add_function(wrap_pyfunction!(office::extract_text, m)?)?;
        m.add_function(wrap_pyfunction!(office::extract_text_from_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(office::extract_iocs, m)?)?;
        m.add_function(wrap_pyfunction!(office::extract_iocs_from_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(office::extract_metadata, m)?)?;
        m.add_function(wrap_pyfunction!(office::extract_metadata_from_bytes, m)?)?;
        m.add_class::<office::OfficeMetadata>()?;
    }

    url_engine::register_functions(m)?;
    url_ops::register_functions(m)?;
    m.add_class::<url_ops::UrlClassifyCachePy>()?;

    // IOC deduplication store (cross-sprint persistence)
    ioc_dedup::register_class(m)?;

    // Issue 4.1: Rust-powered co-occurrence engine — 10× faster than Python dict
    // HashMap<String, BitSet> inverted index, rayon parallel across findings batch
    m.add_function(wrap_pyfunction!(ioc_cooccurrence_rs::compute_cooccurrence_edges_py, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_cooccurrence_rs::batch_cooccurrence_edges_py, m)?)?;

    // SimHash for near-duplicate document detection
    simhash_ext::register_functions(m)?;

    // F320+: LSH index for O(1) near-duplicate detection at scale
    #[cfg(feature = "graph")]
    lsh_index::register_functions(m)?;

    // R25: Text similarity via trigram Jaccard — group_similar_texts()
    // ISSUE-025 FIX: restored from git history, was never connected
    text_similarity::register_functions(m)?;

    // xxHash3-64 — ISSUE-005: xxhash_ext has 8 unique functions (no overlap with content_hasher)
    // F320: batch xxh3-64 via rayon — parallel prompt cache fingerprinting (Apple Silicon NEON)
    #[cfg(feature = "advanced")]
    m.add_function(wrap_pyfunction!(content_hasher::batch_xxh3_64_hex, m)?)?;
    #[cfg(feature = "advanced")]
    xxhash_ext::register_functions(m)?;

    // SHA-256 + BLAKE3 content hashing (TLS cert fingerprint, body dedup).
    // NEON-enabled on aarch64 (Apple Silicon), scalar fallback elsewhere.
    #[cfg(feature = "advanced")]
    m.add_class::<content_hasher::ContentHasher>()?;

    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback.
    tls_metadata::register_functions(m)?;

    // TLS 1.3 JA4 fingerprinting + ECH detection via rustls (feature-gated).
    #[cfg(feature = "tls13")]
    tls13::register_functions(m)?;

    // QUIC/HTTP3 via quinn + h3 — F350M-R: true HTTP/3 fallback for fetch_coordinator
    #[cfg(feature = "quic")]
    quic::register(m)?;

    // DoH/DoT/DoQ DNS resolution — replaces batch_dns.py triple-path duplication.
    // F350M-R: rust.dns.resolve_async(), resolve_happy_eyeballs(), prefetch()
    #[cfg(feature = "dns")]
    dns::register_functions(m)?;

    // F275: CommonCrypto SHA-256 hardware acceleration on Apple Silicon (~3× vs sha2 crate).
    crypto_accelerate::register_functions(m)?;
    // adaptive_scheduler is always compiled — no feature gate needed
    adaptive_scheduler::register_functions(m)?;
    rate_limit::register_module(m)?;  // ISSUE #016: NVD token bucket rate limiter
    // F5.2: FeedDominanceGuard + LaneBudgetPool in Rust (zero-copy, no GIL)
    sprint_policies::register(m)?;

    // IntCounterLayout — SoA buffer for hot-path integer counters
    // (drop-in replacement for runtime.int_counter_layout.IntCounterLayout).
    // M1 8GB safe, bounded, fail-soft. Wire format: i64 (signed 8B per slot).
    int_counter_layout::register_functions(m)?;

    // HotEdgeCounterRust — in-memory L1 write buffer for hot edge counts.
    #[cfg(feature = "graph")]
    {
        hot_edges_rs::register_functions(m)?;
    }

    // F265B-IV: Telemetry aggregator — counters, histograms, gauges for sprint reporting
    telemetry_agg::register_functions(m)?;

    // Sprint P1-5: Quality gate compute kernels — BLAKE2b-128 dedup fingerprint,
    // Shannon entropy, text/URL normalization. BLAKE2b-128 output is bit-
    // identical to Python's hashlib.blake2b(digest_size=16) so existing
    // LMDB-persisted fingerprints remain valid (no migration).
    quality_gate::register_functions(m)?;

    // Sprint F265B-III: Unicode NFC/NFD normalization + diacritic stripping.
    text_norm::register_functions(m)?;

    // Issue #7c: XML sanitization — strip DOCTYPE/ENTITY declarations (5× faster than Python).
    m.add_function(wrap_pyfunction!(xml_sanitize::sanitize_xml, m)?)?;
    m.add_function(wrap_pyfunction!(xml_sanitize::batch_sanitize_xml, m)?)?;

    // F273F: Darwin madvise — MADV_FREE_REUSABLE for LMDB/DuckDB mmap regions
    madvise::register_functions(m)?;

    // ISSUE-004: Rust LMDB backend for DHT — eliminates asyncio.to_thread overhead
    lmdb_dht::register_functions(m)?;

    // Sprint P2-3: IP address parsing, classification, and CIDR containment.
    m.add_function(wrap_pyfunction!(ip_parse::parse_ip_fast, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_private_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_public_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::batch_ip_classify, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::cidr_contains, m)?)?;

    // Sprint F265B-III: LMDB page compression (lz4 + zstd) for hot-edges cache.
    // Wire format: [marker=0x00/0x01/0x02][payload] — lz4 fast path, zstd fallback.
    compress::register_functions(m)?;

    // Raw lz4 frame for JSONL streaming pipeline.
    // jsonl_lz4_writer: batch-compress JSONL lines → lz4 frame → disk.
    m.add_function(wrap_pyfunction!(compress::lz4_compress_raw, m)?)?;
    m.add_function(wrap_pyfunction!(compress::lz4_decompress_raw, m)?)?;
    m.add_function(wrap_pyfunction!(compress::lz4_compress_jsonl_batch, m)?)?;
    m.add_function(wrap_pyfunction!(compress::lz4_decompress_jsonl_batch, m)?)?;

    // Sprint P2-1: Parallel DuckPGQ graph traversal via rayon.
    // batch_graph_traverse: parallel across root IOCs, rayon ThreadPool.
    // Each worker opens its own read-only DuckDB connection (thread-safe).
    #[cfg(feature = "data")]
    graph_traverse::register_functions(m)?;

    // R26: Async DuckDB queries via Rust executor — rust_async_query_batch()
    #[cfg(feature = "data")]
    async_query::register(m)?;

    // P3-3: Rabin-Karp rolling hash for sliding-window URL fingerprinting
    #[cfg(feature = "data")]
    rolling_hash::register(m)?;

    // Sprint F266: Streaming HTML parsing via lol_html — link/email/title/meta extraction.
    html_parse::register_functions(m)?;

    // 3A: Native RSS + available-memory probe via sysinfo.
    memory::register_functions(m)?;

    // Sprint P2-2: Batch signal aggregation — ARM NEON-accelerated source weight
    // computation and signal vector aggregation for F199A reward-driven adaptation.
    // Fallback: scalar Rust on non-aarch64.
    #[cfg(feature = "advanced")]
    signal_batch::register_functions(m)?;

    // ISSUE-023: Low-level SIMD primitives (NEON on M1, scalar fallback)
    simd::register(m)?;

    // PAR-1 P1: SIMD-accelerated batch cosine similarity for embedding re-ranking.
    // Fallback for environments without MLX (CI, testing). NEON on AArch64.
    #[cfg(feature = "advanced")]
    simd_similarity::register_functions(m)?;

    // Zero-copy PyO3 batch utilities — Py<PyList> iteration without Vec<String> allocation.
    // PyO3 0.28 API: Bound<'py, PyList> for borrowed iteration.
    // See zero_copy.rs for rationale and PyO3 0.29+ upgrade path.
    zero_copy::register_functions(m)?;

    // Sprint F266: serde_json — Rust-powered JSON serialization for STIX export.
    // Drop-in for Python json.dumps in export/stix_exporter.py (2-4× faster, no GIL).
    serde_json_rs::register_functions(m)?;

    // F350M-R: STIX 2.1 — native Rust STIX bundle encode/decode + jsonschema validation.
    // Replaces runtime/stix_exporter.py json.dumps with serde + jsonschema for 2-4× speedup.
    #[cfg(feature = "stix")]
    stix_2_1::register_functions(m)?;

    // P0: Lock-free SPSC queue for MLX worker thread coordination.
    // Replaces asyncio.run_coroutine_threadsafe + wrap_future overhead.
    spsc_queue::register(m)?;

    // ISSUE 4.4: sendfile(2) zero-copy file-to-socket for HTTP streaming export.
    // Darwin-only: uses ARM zero-copy DMA path. Falls back to read+write on other platforms.
    sendfile::register_functions(m)?;

    // Bounded MPSC pool — replaces asyncio.Queue in evidence_log.py.
    // crossbeam-channel, ~2-5ns send, pipe-wake for async.
    mpsc_pool::register(m)?;

    // F266-ZC: Arrow ArrayBuilder batch construction for CanonicalFinding.
    // Replaces 6× Python list-comprehension loops with single-pass Rust.
    // IPC RecordBatchStream bytes → pa.ipc.open_stream() zero-copy deserialize.
    #[cfg(feature = "data")]
    arrow_batch_builder::register(m)?;

    // R17 FIX: regex_lz4::register(m) COMMENTED OUT — PyRegexLz4Store class registered
    // but ZERO Python callers exist. No production path creates or uses this store.
    // To re-enable: uncomment below (requires HLEDAC_BUILD=advanced).
    // #[cfg(feature = "advanced")]
    // regex_lz4::register(m)?;

    // R18 FIX: parquet_reader::register(m) COMMENTED OUT — 5 functions registered
    // (parquet_get_metadata, parquet_row_group_stats, parquet_read_row_group_ipc,
    // parquet_iter_all_row_groups, parquet_read_table) but ZERO Python call sites.
    // Python-side getter functions (_get_parquet_*) existed but were never invoked.
    // To re-enable: uncomment below (always compiled, no feature gate needed).
    // parquet_reader::register(m)?;

    // R4.1 + R2: Rayon pool runners — GIL wrappers (cpu/io/mixed_pool_run)
    // + channel-based dispatch (rayon_submit_channel/join_channel/abort_channel).
    // Consolidated: rayon_dispatch.rs merged into pool_run.rs.
    pool_run::register_functions(m)?;

    // ISSUE-23: Rust-backed FederatedQTable with rayon parallel batch updates.
    #[cfg(feature = "advanced")]
    federated_qtable::register(m)?;

    // D6: metal_pattern_matcher removed — gpu_batch_keyword_scan() was never called in production.
    // CPU Aho-Corasick fallback in rust ioc_extract/ioc_extract_fast is sufficient.

    // R20-ANN-REMOVED: Rust HNSW ANN bridge for LanceDB — ARCHIVED 2026-07-26.
    // PyHNSWIndex / PyHNSWBridge were NEVER wired to any Python caller (0 imports found).
    // Active ANN stack: rag_engine.py → HNSWVectorIndex (usearch C++/Metal) + lancedb_store.py → LanceDB IVF_PQ.
    // Rust hnsw/ module kept as dormant asset; re-add when DuckDB native vector index lands (ISSUE-023).
    // m.add_class::<hnsw::py_api::PyHNSWBridge>()?;
    // m.add_class::<hnsw::py_api::PyHNSWIndex>()?;

    // R4.4: TinyLFU LRU cache for cross-worker graph results.
    #[cfg(feature = "advanced")]
    m.add_class::<graph_cache::PyGraphLRUCache>()?;

    // F320+: Parallel graph centrality via rayon (B8/B7 Rust acceleration).
    // batch_centrality_all: all 5 metrics in single pass (degree/betweenness/closeness/eigenvector/pagerank).
    // betweenness_batch: parallel Brandes for multiple source nodes.
    #[cfg(feature = "graph")]
    graph_centrality::register_functions(m)?;

    // GRAPH-01: petgraph-based graph analytics (PageRank, Louvain community detection, SCC).
    // rust_pagerank: power iteration PageRank
    // rust_louvain_communities: Louvain modularity optimization
    // rust_scc: Kosaraju strongly connected components
    // rust_graph_analytics_all: all three in single pass
    #[cfg(feature = "graph")]
    graph_analytics::register_functions(m)?;

    // R4.5: Distribuovaný BloomFilter s Count-Min Sketch.
    // dedup_bloom is always compiled — no feature gate needed
    m.add_class::<dedup_bloom::PyDistributedBloomFilter>()?;
    m.add_class::<dedup_bloom::DedupBloomStats>()?;

    // Issue #22: Health endpoint
    health::register(m)?;

    // ISSUE-008: Darwin os_unfair_lock (~5ns) — replaces threading.Lock for short critical sections.
    // Gated on extension-module feature (cdylib build).
    #[cfg(feature = "extension-module")]
    os_unfair_lock::register(m)?;

    // ISSUE-27: Claims extraction — CPU-bound sentence splitting, polarity, confidence.
    // Pre-compiled regexes via LazyLock, mixed_pool adaptive threading.
    claims_extraction::register_functions(m)?;

    // ISSUE 2.2: Lock-free AIMD controller — single AtomicU64 window,
    // replaces Python AIMDWindow + _AIMDSlotController duplication.
    #[cfg(feature = "data")]
    aimd_controller::register(m)?;

    // ISSUE #014: Multi-stage pipeline operators via rayon — zero-copy Arc<T> between stages.
    // Replaces Python async Queue + dict overhead in sidecar_bus.py for 100+ events/sec.
    // Pipeline primitives: MAP, FILTER-MAP, FOLD, COUNT — all parallel via mixed_pool.
    #[cfg(feature = "advanced")]
    pipeline_compose::register(m)?;

    // ISSUE #015: MLX async token streaming bridge — adaptive buffering + memory pressure feedback
    mlx_bridge::register(m)?;

    // ANE: Apple Neural Engine bindings — model registry, batch validation, telemetry
    #[cfg(feature = "ane")]
    ane::register_functions(m)?;

    // R22: Accelerate/vDSP FFI — batch cosine similarity for NER engine
    // Gated because vDSP symbols are unavailable on macOS 26.5+ (Darwin 25.5+)
    // Python fallback: brain/ner_engine.py uses scipy/numpy for cosine similarity
    #[cfg(feature = "accelerate")]
    accelerate::register(m)?;

    // R22: Metal GPU batch matmul — MoE router integration (feature-gated)
    #[cfg(feature = "metal")]
    metal_compute::register(m)?;

    // DuckDB bridge — isolated module for future cdylib extraction (saves ~8 MB .dylib)
    #[cfg(feature = "data")]
    data::register_functions(m)?;

    // ISSUE-6: Bounded ring buffers — recent_iocs ring, M1 8GB safe
    collections::register_functions(m)?;

    // C3: Feed decision classifiers — pure functions for feed signal classification.
    #[cfg(feature = "advanced")]
    feed_decision::register_functions(m)?;
    #[cfg(feature = "advanced")]
    feed_pipeline::register(m)?;

    // F350M-R 5.5: Thread QoS — renamed to apply_current_thread_qos (B-5 fix)
    m.add_function(wrap_pyfunction!(apply_current_thread_qos, m)?)?;

    // F350M-R 5.5: Backward-compatible alias — ignores pthread_id, calls
    // apply_current_thread_qos. The pthread_id was unused (always set calling
    // thread); keeping the name preserves the Python-side API without a bump.
    m.add_function(wrap_pyfunction!(apply_thread_qos, m)?)?;

    // R24: OpenTelemetry tracing
    #[cfg(feature = "otel")]
    tracing::register(m)?;

    // GRAPH-03: .onion v3 address validation — Ed25519 checksum verification
    m.add_function(wrap_pyfunction!(onion_validation::rust_validate_onion_v3, m)?)?;
    m.add_function(wrap_pyfunction!(onion_validation::rust_validate_onion_v3_detailed, m)?)?;
    m.add_function(wrap_pyfunction!(onion_validation::rust_validate_onion_batch, m)?)?;

    // ISSUE-011: Tantivy fulltext search (mmap-backed, zero-copy BM25)
    // Feature-gated: fulltext = ["dep:tantivy"]
    // Enables: fulltext.create_index(), fulltext.search()
    // Replaces Python BM25Index in knowledge/rag_engine.py
    #[cfg(feature = "fulltext")]
    fulltext_index::register(m)?;

    Ok(())
}

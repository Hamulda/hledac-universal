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
// D6: metal_compute and metal_pattern_matcher removed — metal crate (~45s compile, ~3MB dylib)
// was never used in production (gpu_batch_keyword_scan was defined but never called).
// CPU fallback via Aho-Corasick + rayon is sufficient for all workloads.
pub mod memory;
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
#[cfg(feature = "data")]
pub mod arrow_batch_builder;
pub mod parquet_reader; // F320+: Lazy parquet reader — paginated Arrow, 100GB+ IOC history bez OOM
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
// R23-CB-ARCHIVED: circuit_breaker module kept for reference but NOT compiled.
// Python transport.circuit_breaker (threading.Lock) is the wired canonical CB.
// circuit_breaker.rs is NEVER registered and NEVER called — compile-time waste.
#[cfg(feature = "advanced")]
pub mod circuit_breaker;
#[cfg(feature = "data")]
pub mod aimd_controller; // ISSUE 2.2: Lock-free AIMD controller replacing Python AIMDWindow + _AIMDSlotController
pub mod claims_extraction; // ISSUE-27: CPU-bound claims extraction (polarity, confidence, sentence split)
pub mod tls_metadata;    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback
pub mod gil;            // F5.2: GIL management — std::thread + rayon pools (ne pyo3-async)
pub mod pool_run;      // R2: Rayon pool runners — GIL wrappers + channel-based dispatch (consolidated)
pub mod mlx_bridge;    // ISSUE #015: MLX async token streaming bridge + adaptive buffering
pub mod collections;    // Bounded ring buffers — recent_iocs ring, M1 8GB safe
#[cfg(feature = "data")]
pub mod async_query; // R26: Async DuckDB queries via Rust executor
pub mod data;           // DuckDB bridge — isolated module for future cdylib extraction
// ISSUE-026 + R25-DELETE: text_similarity.rs removed — SequenceMatcher in
// metadata_dedup.py is sufficient for short-field comparison. Rust trigram
// Jaccard (group_similar_texts) was never wired from Python.

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

/// Detekuje počet P-cores (performance cores).
///
/// macOS: hw.perflevel0.logicalcpu = počet performance cores v perf clusteru.
/// Linux/Windows: num_cpus::get_physical() fallback.
/// Clamped to [1, 4] for M1 8GB RAM budget safety.
///
/// MacBook Pro M3 Pro (12 jader) → 6 P-cores → clamp to 4.
#[cfg(target_os = "macos")]
fn detect_p_core_count() -> usize {
    use std::process::Command;

    // hw.perflevel0.logicalcpu — Apple Silicon P-core count
    if let Ok(output) = Command::new("sysctl")
        .args(["-n", "hw.perflevel0.logicalcpu"])
        .output()
    {
        let stdout = String::from_utf8_lossy(&output.stdout);
        if let Ok(n) = stdout.trim().parse::<usize>() {
            return n.clamp(1, 4);
        }
    }

    // Fallback: hw.physicalcpu (total physical, may include E-cores on big.LITTLE)
    if let Ok(output) = Command::new("sysctl")
        .args(["-n", "hw.physicalcpu"])
        .output()
    {
        let stdout = String::from_utf8_lossy(&output.stdout);
        if let Ok(n) = stdout.trim().parse::<usize>() {
            return n.clamp(1, 4);
        }
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

/// __abi_version() -> u32
/// Returns the ABI version for Python-side compatibility checking.
///
/// ABI_VERSION increments whenever the Rust extension's public API changes
/// in a backward-incompatible way (new required arguments, removed functions,
/// changed return types, changed struct layouts). Python code should check
/// this at import time and fail fast if the ABI version doesn't match.
///
/// bumping rule: increment on ANY public API change that breaks old callers
/// minor bump (1→2): new optional API added, old callers still work
/// major bump (2→3): API removed or changed, old callers MUST update
///
/// Current ABI version: 1
const ABI_VERSION: u32 = 1;

#[pyfunction]
fn __abi_version__() -> u32 {
    ABI_VERSION
}

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Expose package version for Python-side ABI compatibility checking (F275).
    // CARGO_PKG_VERSION is set by Cargo at compile time from Cargo.toml.
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(__version_info__, m)?)?;
    // ABI version for backward-compatibility enforcement (ISSUE-040)
    // Register ONLY as function — getattr(ext, "__abi_version__") returns the function
    // object, so callable() check in Python handles this correctly.
    // Constant registration is redundant and removed.
    m.add_function(wrap_pyfunction!(__abi_version__, m)?)?;

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
    // ISSUE-026 FIX: restored from git history, was never connected
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

    // P0: Lock-free SPSC queue for MLX worker thread coordination.
    // Replaces asyncio.run_coroutine_threadsafe + wrap_future overhead.
    spsc_queue::register(m)?;

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

    // R4.5: Distribuovaný BloomFilter s Count-Min Sketch.
    // dedup_bloom is always compiled — no feature gate needed
    m.add_class::<dedup_bloom::PyDistributedBloomFilter>()?;

    // Issue #22: Health endpoint
    health::register(m)?;

    // ISSUE-27: Claims extraction — CPU-bound sentence splitting, polarity, confidence.
    // Pre-compiled regexes via LazyLock, mixed_pool adaptive threading.
    claims_extraction::register_functions(m)?;

    // R23-CB-ARCHIVED: circuit_breaker — ARCHIVED 2026-07-26.
    // R23-ROOT: Python transport/circuit_breaker.py uses threading.Lock (canonical CB — safe
    // across asyncio.to_thread workers per surface_id=5257). Rust version existed as parallel
    // alternative but was NEVER called from Python (0 call sites found).
    // transport.circuit_breaker is the WIRED canonical implementation (fetch_coordinator,
    // stealth_browser, sprint_entrypoint all use it).
    // circuit_breaker::register_functions(m)?;

    // ISSUE 2.2: Lock-free AIMD controller — single AtomicU64 window,
    // replaces Python AIMDWindow + _AIMDSlotController duplication.
    #[cfg(feature = "data")]
    aimd_controller::register(m)?;

    // ISSUE-013 + R26-DELETE: async_query.rs removed — IsolatedDuckDBExecutor
    // (isolated_executors.py) is the single source for DuckDB async queries.
    // rust_async_query() was never wired from Python.

    // ISSUE #014: Multi-stage pipeline operators via rayon — zero-copy Arc<T> between stages.
    // Replaces Python async Queue + dict overhead in sidecar_bus.py for 100+ events/sec.
    // Pipeline primitives: MAP, FILTER-MAP, FOLD, COUNT — all parallel via mixed_pool.
    #[cfg(feature = "advanced")]
    pipeline_compose::register(m)?;

    // ISSUE #015: MLX async token streaming bridge — adaptive buffering + memory pressure feedback
    mlx_bridge::register(m)?;

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
Ok(())
}

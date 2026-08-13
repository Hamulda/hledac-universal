//! hledac-rust-extensions - High-performance Rust extensions for hledac OSINT platform.
//!
//! ## Architecture
//!
//! The crate is organized into logical module groups for improved navigability:
//!
//! | Group | Modules | Purpose |
//! |-------|---------|---------|
//! | [`ioc`] | extract, patterns, dedup, cooccurrence | IOC extraction & processing |
//! | [`pools`] | cpu, io, mixed, elastic | Unified thread pool management |
//! | graph | analytics, centrality, cache | Graph algorithms |
//! | network | dns, tls_metadata, ip_parse | Network utilities |
//! | text | html_parse, text_norm, text_similarity | Text processing |
//! | crypto | crypto_accelerate, xxhash_ext, content_hasher | Cryptographic utilities |
//!
//! ## Quick Links
//!
//! - [`ioc::extract`] - Unified IOC extraction facade
//! - [`pools::cpu`] - CPU-bound thread pool
//! - [`pools::io`] - I/O-bound thread pool

#![allow(dead_code)]
#![recursion_limit = "512"]

use pyo3::prelude::*;
use rayon::ThreadPool;
use rayon::ThreadPoolBuilder;
use std::sync::LazyLock;

// ISSUE-014: Removed custom lazy_static! macro — Rust 1.80+ std::sync::LazyLock
// is stable and ships with the 2024 edition. Each module now uses LazyLock directly.

// [META]-004: elastic_pool provides dynamic pool resize (RwLock-wrapped ThreadPools).
// cpu_pool()/io_pool() below delegate to it for backward compatibility with
// existing callers throughout the codebase.

// ============================================================================
// IOC Extraction Group - Unified Facade with Specialized Implementations
// ============================================================================

// Public facade - use this for IOC extraction
pub mod ioc;

// Individual modules (kept for backward compatibility and direct access)
pub mod deobfuscate;
pub mod ioc_cooccurrence_rs; // IOC co-occurrence analysis
pub mod ioc_dedup; // IOC deduplication (cross-sprint persistence)
pub mod ioc_extract; // Standard IOC extraction
pub mod ioc_extract_fast; // Fast Aho-Corasick extraction
pub mod ioc_extract_simd; // SIMD NEON extraction (M1 optimized)
pub mod ioc_patterns; // Pattern definitions (single source of truth)
pub mod ioc_patterns_generated; // Generated patterns (codegen)
pub mod ioc_stream_scan; // Streaming SIMD scanner (mmap/bytes zero-copy) // CyberChef-style IOC deobfuscation

// ============================================================================
// Thread Pool Group - Unified Pool Management
// ============================================================================

// Public facade - use this for pool operations
pub mod pools;

// Individual pool modules (kept for backward compatibility)
pub mod adaptive_scheduler; // Memory-pressure aware thresholds
pub mod elastic_pool; // Phase-aware dynamic resizing
pub mod gil;
pub mod mpsc_pool; // Bounded MPSC queue pool
pub mod pool_run; // GIL wrappers & channel dispatch // GIL management utilities

// ============================================================================
// Graph Analytics Group
// ============================================================================

pub mod consistency_verifier; // META-007: "confident liar" detection
pub mod finding_collapser; // NEXUS-018-04: Pre-LLM synthesis collapser
pub mod graph_analytics; // GRAPH-01: PageRank, Louvain, SCC
pub mod graph_cache; // TinyLFU LRU cache
pub mod graph_centrality; // Centrality metrics
pub mod graph_traverse; // DuckPGQ graph traversal
pub mod hot_edges_rs;
pub mod lsh_index; // LSH near-duplicate detection // Hot edge counter
pub mod link_predictor; // Graph ML link prediction (common neighbors, Adamic-Adar)

// ============================================================================
// Aho-Corasick & Pattern Matching
// ============================================================================

pub mod aho_corasick; // Multi-pattern matching
pub mod query_terms; // Query-context scanning
#[cfg(feature = "deep_ac")]
pub mod aho_corasick_simd; // DEEP-AC: NEON SIMD Aho-Corasick (F-8)

// ============================================================================
// Data Structures & Storage
// ============================================================================

#[cfg(feature = "bloom")]
pub mod bloom; // BloomFilter for URL dedup
pub mod compress; // LZ4/Zstd compression
#[cfg(feature = "advanced")]
pub mod content_hasher; // SHA-256, BLAKE3 hashing
// [M7-FIX] REMOVED: regex_lz4 was a zombie module - declared but never registered
// (registration commented out at line ~1330 due to zero Python callers).
// If needed in future, uncomment the declaration and add `regex_lz4::register(m)?;`
// #[cfg(feature = "advanced")]
// pub mod regex_lz4; // LZ4-compressed pattern store
#[cfg(feature = "data")]
pub mod rolling_hash; // P3-3: Rabin-Karp rolling hash
pub mod serde_json_rs; // JSON serialization (STIX export)
pub mod spsc_queue;
pub mod url_engine; // URL parsing & classification
pub mod url_ops; // URL operations & normalization
pub mod url_set; // Mmap-backed URL set
pub mod xxhash_ext; // xxHash3-64 non-cryptographic hash
pub mod zero_copy; // Zero-copy PyO3 batch utilities // Lock-free SPSC queue

// ============================================================================
// Cryptography & Security
// ============================================================================

// [MODERN-07]: Shared Tokio runtime — consolidates 3 separate runtimes into 1.
// Must be before dns, quic, and arti_bridge which depend on it.
#[cfg(feature = "shared_tokio")]
pub mod async_runtime;

// MODERN-08: pyo3-async-runtimes integration — returns awaitables directly to Python asyncio.
// Eliminates need for asyncio.to_thread() wrappers on the Python side.
#[cfg(feature = "shared_tokio")]
pub mod async_bridge;

// MODERN-16: Hybrid Python↔Rust stealth transport bridge.
// Python keeps curl_cffi JA3/TLS impersonation; Rust owns raw I/O (DNS, QUIC, sockets).
// Bridge via native async FFI + Arrow IPC for bulk transfer.
// Uses tokio::net::lookup_host directly (no hickory-resolver dependency needed).
#[cfg(feature = "stealth_bridge")]
pub mod stealth_bridge;

// NEXTGEN-02: Pre-fetch anti-analysis evasion engine.
// Detects Cloudflare Turnstile, DataDome, Akamai at TLS handshake level.
// Abandoned domains skip entire fetch (0 bandwidth, 0 LLM tokens).
#[cfg(feature = "anti_analysis")]
pub mod anti_analysis;

pub mod circuit_breaker;
pub mod ffi_safe; // [SWARM]-005: Panic-safe FFI wrapper for pyfunction calls
#[cfg(feature = "core")]
pub mod crypto_accelerate; // CommonCrypto SHA-256 (M1 optimized)
pub mod h2_safari_preset; // Safari WebKit HTTP/2 presets
#[cfg(feature = "block2")]
pub mod nw_connection; // Apple Network.framework TCP (requires block2)
pub mod onion_validation; // GRAPH-03: .onion v3 validation
#[cfg(feature = "quic")]
pub mod quic; // QUIC/HTTP3 via Quinn+H3
#[cfg(feature = "tls13")]
pub mod tls13; // TLS 1.3 JA4 fingerprinting
pub mod tls_metadata; // TLS cert metadata extraction // Circuit breaker pattern

// ============================================================================
// Platform-Specific Modules
// ============================================================================

// MODERN-26: macOS CPU affinity via Mach APIs (thread_policy_set).
// Provides P/E core preference hints for Apple Silicon.
// Falls back to QoS class on older Macs.
#[cfg(target_os = "macos")]
pub mod darwin_affinity;

// MODERN-33 + MODERN-34: Apple Silicon P/E core topology detection and affinity.
// Provides cached perflevel0/1 counts and workload-aware affinity helpers.
// Replaces scattered sysctl calls with single source of truth at startup.
#[cfg(target_os = "macos")]
pub mod topology;

// ============================================================================
// Network & Transport (HEIST-02: Embedded Tor)
// ============================================================================

#[cfg(feature = "embedded_tor")]
pub mod arti_bridge; // HEIST-02: In-process Tor via Arti (PyO3 bindings)

// NEXTGEN-01: Native P2P harvesters (IPFS/TOR/I2P) in Tokio runtime
// Feature-gated: ~8MB additional compile, M1 8GB safe
//
// Modules:
//   - harvest(): Unified P2P harvest API (multi-protocol concurrent)
//   - dht_crawl_async(): BitTorrent DHT crawler in native Tokio
//   - ipfs_bitswap_crawl_async(): IPFS Kademlia + BitSwap via libp2p
//   - tor_consensus_scrape_async(): Tor consensus directory scraper
//   - i2p_leaseset_resolve_async(): I2P LeaseSet resolver via SAMv3
//
// Benefits:
//   - No GIL contention on network I/O (vs Python asyncio)
//   - Native async/await in Tokio runtime
//   - SIMD IOC extraction in hot path (via existing ioc_extract_simd)
//   - Arrow IPC streaming to Python (zero-copy)
//
// Memory budget (M1 8GB safe):
//   - libp2p swarm: ~3MB resident
//   - Tokio workers: ~10MB total
//   - Bounded concurrency: max 20 concurrent peers
//
// Python fallback: dht/kademlia_node.py (simulated mode)
#[cfg(feature = "p2p_harvest")]
pub mod p2p_harvest;

// ============================================================================
// Text Processing
// ============================================================================

pub mod html_parse; // HTML parsing & link extraction
pub mod text_norm; // Unicode NFC/NFD normalization
pub mod text_similarity; // R25: Trigram Jaccard similarity
pub mod unicode_fingerprint; // Zero-width & homoglyph fingerprint
pub mod xml_sanitize; // R7c: XML sanitization

// ============================================================================
// System & Platform
// ============================================================================

pub mod int_counter_layout; // SoA buffer for integer counters
pub mod madvise; // Darwin madvise (MADV_FREE_REUSABLE)
#[cfg(feature = "mach")]
pub mod mach_remap; // [NEXUS]-018-03: Mach vm_remap zero-copy remapping
pub mod memory; // Memory statistics via sysinfo
pub mod os_unfair_lock; // ISSUE-4.3: os_unfair_lock (~5ns)
pub mod sendfile; // ISSUE-4.4: sendfile(2) zero-copy

// ============================================================================
// Quality & Signal Processing
// ============================================================================

pub mod _entropy; // Entropy helpers
pub mod claims_extraction; // ISSUE-27: Claims extraction (CPU-bound sentence splitting)
pub mod quality_gate; // Quality gate kernels
pub mod rate_limit; // ISSUE-016: NVD API rate limiter
#[cfg(feature = "advanced")]
pub mod signal_batch; // ARM NEON signal aggregation
#[cfg(feature = "advanced")]
pub mod simd_similarity; // SIMD cosine similarity
pub mod simhash_ext; // SimHash near-duplicate detection
pub mod sprint_policies;
pub mod telemetry_agg; // Real-time metrics aggregation // RL sprint policy layer

// ============================================================================
// Advanced Features (Feature-Gated)
// ============================================================================

#[cfg(feature = "advanced")]
pub mod federated_qtable;
#[cfg(feature = "advanced")]
pub mod feed_decision; // Feed decision classifiers
#[cfg(feature = "advanced")]
pub mod feed_pipeline; // Feed pipeline operators
#[cfg(feature = "advanced")]
pub mod pipeline_compose; // Multi-stage pipeline operators
#[cfg(feature = "advanced")]
pub mod swarm_dag; // SILICON-07: Work-stealing DAG // ISSUE-023: Federated Q-table

// ============================================================================
// Data Processing
// ============================================================================

#[cfg(feature = "data")]
pub mod arrow_batch_builder; // Arrow ArrayBuilder batch construction
#[cfg(feature = "data")]
pub mod arrow_c_data; // MODERN-24: Arrow C Data Interface zero-copy export
// [M7-FIX] REMOVED: parquet_reader was a zombie module - declared but never registered
// (registration commented out at line ~1337 due to zero Python callers).
// If needed in future, uncomment the declaration and add `parquet_reader::register(m)?;`
// #[cfg(feature = "data")]
// pub mod parquet_reader; // F320+: Lazy parquet reader

#[cfg(feature = "data")]
pub mod aimd_controller; // ISSUE-2.2: AIMD controller
#[cfg(feature = "data")]
pub mod async_query; // R26: Async DuckDB queries
pub mod data; // DuckDB bridge
pub mod dedup_bloom; // Distributed BloomFilter + Count-Min Sketch

// ============================================================================
// ML/AI Infrastructure
// ============================================================================

#[cfg(feature = "accelerate")]
pub mod accelerate; // R22: Accelerate/vDSP FFI
#[cfg(feature = "ane")]
pub mod ane; // Apple Neural Engine bindings
#[cfg(feature = "iosurface")]
pub mod iosurface_bridge; // IO-4: IOSurface zero-copy bridge (CVPixelBuffer → Metal)
#[cfg(feature = "metal")]
pub mod metal_compute; // R22: Metal GPU matmul
#[cfg(feature = "metal")]
pub mod metal_hashcrack; // SILICON-01: GPU hash cracking
#[cfg(feature = "metal_shared")]
pub mod metal_shared_buf; // SILICON-04: Shared Metal buffer (guarded by metal_shared feature)
pub mod mlx_bridge; // ISSUE-015: MLX async token streaming
pub mod simd; // ISSUE-023: Modular SIMD (NEON fallback)

// SILICON-02: whisper.cpp speech-to-text via whisper-rs with CoreML/ANE backend.
// Enables: rust.whisper.transcribe() — ANE-accelerated transcription.
// M1 8GB: Only tiny (39 MB) and base (74 MB) models. Bounded to 1 concurrent inf.
// Python fallback: brain/whisper_engine.py uses whispercpp Python package.
#[cfg(feature = "whisper")]
pub mod whisper;

// ============================================================================
// Network Protocols
// ============================================================================

#[cfg(feature = "dns")]
pub mod dns; // DoH/DoT/DoQ DNS resolution
pub mod dns_tunnel; // ISSUE-033: DNS tunneling detection

// ============================================================================
// Document Processing
// ============================================================================

#[cfg(feature = "office")]
pub mod office;
#[cfg(feature = "pdf")]
pub mod pdf; // PDF text extraction // Office document extraction

// ============================================================================
// Database & Search
// ============================================================================

#[cfg(feature = "fulltext")]
pub mod fulltext_index;
pub mod lmdb_dht; // ISSUE-004: Rust LMDB DHT backend
#[cfg(feature = "native_db")]
pub mod native_db; // HEIST-03: Wire-protocol DB extraction // ISSUE-011: Tantivy fulltext search

// DEEP modules - Forensics and Storage
#[cfg(feature = "deep_git")]
pub mod git_forensics; // DEEP-GIT: Git packfile forensics
#[cfg(feature = "deep_warc")]
pub mod warc_parser; // DEEP-WARC: WARC file parser
#[cfg(feature = "deep_unindexed")]
pub mod unindexed_scanner; // DEEP-UNINDEXED: MinIO/rsync/S3 listing

// ============================================================================
// External Integrations
// ============================================================================

#[cfg(feature = "simdjson")]
pub mod simdjson_extract; // HEIST-05: simdjson JSON extraction
#[cfg(feature = "stix")]
pub mod stix_2_1; // STIX 2.1 encode/decode
#[cfg(feature = "otel")]
pub mod tracing; // R24: OpenTelemetry tracing

// IP parsing (network utility)
pub mod ip_parse; // Sprint P2-3: IP parsing & classification

// Health & telemetry
pub mod health; // Issue #22: Health endpoint

// ============================================================================
// Rayon Thread Pools - M1 8GB safe, P/E core optimized
// ============================================================================
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
// MODERN-29 FIX: Added #[pyfunction] for PyO3 FFI export
#[cfg(target_os = "macos")]
#[pyfunction]
pub fn detect_p_core_count() -> usize {
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

// MODERN-29 FIX: Added #[pyfunction] for PyO3 FFI export
#[cfg(not(target_os = "macos"))]
#[pyfunction]
pub fn detect_p_core_count() -> usize {
    num_cpus::get_physical().clamp(1, 4)
}

/// Nastaví QoS třídu pro macOS scheduler.
/// Volá se uvnitř rayon worker thread (NE v spawn_handler parent).
/// MODERN-27/28 FIX: Set QoS class for current thread (P-core for mixed workloads).
///
/// MODERN-28: Mixed pool uses USER_INITIATED → P-cores for CPU-mixed workloads.
/// MODERN-27/28 FIX: Set QoS class for current thread (P-core for mixed workloads).
///
/// MODERN-28: Mixed pool uses USER_INITIATED → P-cores for CPU-mixed workloads.
#[cfg(target_os = "macos")]
fn apply_qos_hint() {
    // ISSUE-FIX: pthread_set_qos_class_np was removed from Apple Silicon support.
    // Use pthread_set_qos_class_self_np instead — sets QoS for current thread.
    // Falls back silently if unavailable (non-fatal).
    unsafe {
        use libc::pthread_set_qos_class_self_np;
        // MODERN-27 FIX: Use libc's QOS_CLASS_USER_INITIATED constant directly.
        // Correct value is 0x19 (not 0x9 as in old code).
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

/// MODERN-26: macOS P-core affinity via Mach APIs.
///
/// Replaces the previous no-op implementation. Uses:
///   1. `thread_policy_set` with `THREAD_PERFORMANCE_PROFILE` (M1+)
///   2. `thread_policy_set` with `THREAD_AFFINITY_POLICY` (fallback for older Macs)
///
/// This provides soft affinity — a hint to the scheduler to prefer P/E cores.
/// Hard pinning requires root privileges and is not attempted.
#[cfg(target_os = "macos")]
fn apply_affinity_hint(p_cores: usize) {
    crate::darwin_affinity::apply_cpu_affinity(p_cores);
}

#[cfg(not(any(
    target_os = "macos",
    all(target_os = "linux", not(target_env = "musl"))
)))]
fn apply_affinity_hint(_p_cores: usize) {
    // Windows / other: no-op
}

/// Process-wide singleton — P-core ceiling for CPU-bound work.
///
/// SHARED by quality_gate, xxhash_ext parallel, simd_similarity.
///
/// [META]-004: Delegates to `elastic_pool::get_cpu_pool()` so that callers
/// (quality_gate, xxhash, etc.) automatically use the current resized pool.
/// Resize via `elastic_pool::resize_cpu_pool(n)` from Python.
pub(crate) fn cpu_pool() -> std::sync::Arc<ThreadPool> {
    crate::elastic_pool::get_cpu_pool()
}

/// Process-wide singleton — 2-thread ceiling for I/O-bound work.
///
/// SHARED by graph_traverse (DuckDB read-only), compress.
///
/// [META]-004: Delegates to `elastic_pool::get_io_pool()` so that callers
/// automatically use the current resized pool.
/// Resize via `elastic_pool::resize_io_pool(n)` from Python.
pub(crate) fn io_pool() -> std::sync::Arc<ThreadPool> {
    crate::elastic_pool::get_io_pool()
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
/// [SWARM]-009 FIX: ThreadPool build helper with graceful error logging.
/// For static LazyLock contexts where panic on OOM is acceptable
/// (program cannot function without thread pools).
macro_rules! build_mixed_pool {
    ($name:expr, $num_threads:expr) => {{
        ThreadPoolBuilder::new()
            .num_threads($num_threads)
            .stack_size(4_194_304) // 4 MiB — mixed workload stack safety
            .thread_name(|i| format!("hledac-{}-{}", $name, i))
            .spawn_handler(|thread| {
                std::thread::spawn(move || {
                    #[cfg(target_os = "macos")]
                    {
                        apply_qos_hint();
                        // NEW-M11 FIX: Also apply P/E core affinity for macOS
                        // This was missing - Linux path correctly called apply_affinity_hint()
                        apply_affinity_hint($num_threads);
                    }
                    #[cfg(all(target_os = "linux", not(target_env = "musl")))]
                    apply_affinity_hint($num_threads);
                    thread.run();
                });
                Ok(())
            })
            .build()
            .unwrap_or_else(|e| {
                eprintln!(
                    concat!(
                        "CRITICAL [SWARM]-009: mixed_pool(",
                        $name,
                        ") ThreadPoolBuilder::build failed: {}"
                    ),
                    e
                );
                panic!(concat!(
                    "Cannot recover: mixed_pool(",
                    $name,
                    ") initialization failed. M1 8GB OOM?"
                ),);
            })
    }};
}

pub(crate) fn mixed_pool(n_items: usize) -> &'static ThreadPool {
    static POOL_SINGLE: LazyLock<ThreadPool, fn() -> ThreadPool> =
        LazyLock::new(|| build_mixed_pool!("mixed-1", 1));
    static POOL_PAIR: LazyLock<ThreadPool, fn() -> ThreadPool> =
        LazyLock::new(|| build_mixed_pool!("mixed-2", 2));

    let threshold = adaptive_scheduler::mixed_threshold();
    let use_pair = n_items >= threshold;

    // MODERN-31: Update global budget when pool selection changes
    adaptive_scheduler::set_mixed_threshold(threshold);
    adaptive_scheduler::set_mixed_budget(if use_pair { 2 } else { 1 });

    if use_pair {
        &POOL_PAIR
    } else {
        &POOL_SINGLE
    }
}

/// MODERN-27 FIX: Set QoS class for the CURRENT thread (calling thread).
///
/// QoS classes on macOS (Darwin/XNU Mach QoS):
///   0x09 = QOS_CLASS_BACKGROUND (lowest priority — vacuum/close threads, E-cores)
///   0x11 = QOS_CLASS_UTILITY (low-latency tolerant — IO/background, E-cores)
///   0x15 = QOS_CLASS_DEFAULT (system default)
///   0x19 = QOS_CLASS_USER_INITIATED (latently responding — inference/ML, P-cores)
///   0x21 = QOS_CLASS_USER_INTERACTIVE (immediate response — UI, P-cores)
///
/// MODERN-27 fix: Corrected ALL values from wrong (0x1/0x2/0x3/0x6/0x9)
/// to actual Darwin qos_class_t values. The previous 0x9 was actually BACKGROUND!
///
/// MODERN-28 fix: P/E core affinity via QoS:
///   - USER_INITIATED/INTERACTIVE → P-cores (performance)
///   - UTILITY/BACKGROUND → E-cores (efficiency)
///
/// B-5 fix: pthread_id removed — pthread_set_qos_class_self_np ALWAYS sets
/// the calling thread, so the target pthread_id parameter was meaningless.
/// Callers (ThreadPoolExecutor workers) invoke this from the target thread,
/// so calling-thread semantics are correct.
///
/// Returns 0 on success, -1 on failure (errno set).
#[cfg(target_os = "macos")]
mod qos_class_helpers {
    use libc::qos_class_t;

    /// MODERN-27 FIX: Correct macOS QoS class raw values — Darwin qos_class_t.
    ///
    /// Prior values (0x1/0x2/0x3/0x6/0x9) were COMPLETELY WRONG!
    /// 0x9 is actually BACKGROUND, not USER_INITIATED.
    #[derive(Debug, Clone, Copy)]
    #[repr(i32)]
    pub enum QosClassRaw {
        Background = 0x09,      // E-cores only
        Utility = 0x11,         // E-cores
        Default = 0x15,         // system default
        UserInitiated = 0x19,   // P-cores (inference/ML)
        UserInteractive = 0x21, // P-cores (UI)
    }

    // Compile-time assertion: qos_class_t must be 4 bytes (i32/u32).
    // Catches libc version changes that alter the type layout.
    const _: () = assert!(
        std::mem::size_of::<qos_class_t>() == 4,
        "qos_class_t must be 4 bytes (i32); check libc version"
    );

    /// MODERN-27 FIX: Safely convert a raw i32 QoS class constant to libc::qos_class_t.
    #[inline]
    pub fn qos_class_i32_to_qos_class_t(raw: i32) -> qos_class_t {
        // SAFETY: raw values are valid Darwin QoS class discriminants
        // guaranteed by the macOS ABI for qos_class_t (which is a signed int).
        // The #[repr(i32)] enum has identical memory layout.
        let qos: QosClassRaw = match raw {
            0x09 => QosClassRaw::Background,
            0x11 => QosClassRaw::Utility,
            0x15 => QosClassRaw::Default,
            0x19 => QosClassRaw::UserInitiated,
            0x21 => QosClassRaw::UserInteractive,
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
        assert_eq!(
            pool.current_num_threads(),
            1,
            "n=31 < threshold=32 (normal) → 1 thread"
        );
    }

    #[test]
    fn test_mixed_pool_large() {
        // Set pressure=1 (NORMAL_THRESHOLD=32), threshold=32
        // n=32 >= 32 → 2 threads
        adaptive_scheduler::update_memory_pressure(1);
        let pool = mixed_pool(32);
        assert_eq!(
            pool.current_num_threads(),
            2,
            "n=32 >= threshold=32 (normal) → 2 threads"
        );
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
        assert_eq!(
            pool.current_num_threads(),
            2,
            "idle: n=31 >= threshold=16 → 2 threads"
        );
    }

    #[test]
    fn test_mixed_pool_adaptive_pressure() {
        // Pressure (pressure=2): threshold=64, n=31 < 64 → 1 thread
        adaptive_scheduler::update_memory_pressure(2);
        let pool = mixed_pool(31);
        assert_eq!(
            pool.current_num_threads(),
            1,
            "pressure: n=31 < threshold=64 → 1 thread"
        );
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
            assert!(
                h.chars().all(|c| c.is_ascii_hexdigit()),
                "invalid hex: {}",
                h
            );
        }
        // Two identical inputs produce identical hashes
        assert_eq!(results[0], results[0]);
        assert_ne!(results[0], results[1]);
    }

    #[test]
    fn test_batch_sha256_large_parallel() {
        // n=256 >= 128 → cpu_pool parallel path
        adaptive_scheduler::update_memory_pressure(1); // normal = threshold 32
        let input: Vec<String> = (0..256)
            .map(|i| format!("batch_sha256_item_{}", i))
            .collect();
        let results = ioc_extract::batch_sha256(input.clone());
        assert_eq!(results.len(), 256);
        for h in &results {
            assert_eq!(h.len(), 64);
            assert!(
                h.chars().all(|c| c.is_ascii_hexdigit()),
                "invalid hex: {}",
                h
            );
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
    // maturin sets PYTHON_VERSION=<major>.<minor>.<patch> when invoking cargo.
    // For non-abi3 builds (PyO3 0.29), this is informational only.
    // Build-time detection via pyo3-build-config in python instead.
    (3, 14, 0)
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
    // SAFE-1 FIX: Set custom panic hook for FFI safety.
    // With panic="unwind" in Cargo.toml, panics can be caught by catch_unwind.
    // The panic hook ensures panics are logged before being caught.
    // This prevents silent failures and helps debugging in production.
    std::panic::set_hook(Box::new(|panic_info| {
        let location = panic_info.location().map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column())).unwrap_or_else(|| "unknown".to_string());
        let message = if let Some(s) = panic_info.payload().downcast_ref::<&str>() {
            s.to_string()
        } else if let Some(s) = panic_info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "Unknown panic payload".to_string()
        };
        eprintln!("[PANIC-HOOK] location={} message=\"{}\"", location, message);
    }));

    // MODERN-08: pyo3-async-runtimes integration.
    // No explicit runtime initialization needed - future_into_py() automatically
    // detects the Python event loop via task-local storage.
    // Python code can now use: `await rust.dns.resolve_async("example.com")`
    // instead of: `asyncio.to_thread(rust.dns.resolve, "example.com")`

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
    m.add_class::<aho_corasick::PatternHit>()?; // Issue #37: zero-copy hit struct

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
    dns_tunnel::register_functions(m)?; // ISSUE #33: entropy, n-gram, wavelet analysis
                                        // ISSUE-008: ioc_extract provides has_* functions (uses ioc_patterns.rs, single source)
    ioc_extract::register_functions(m)?;
    // Fast IOC extraction: unified Aho-Corasick automaton (single O(n) scan)
    m.add_function(wrap_pyfunction!(ioc_extract_fast::ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(
        ioc_extract_fast::batch_ioc_extract_unified,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        ioc_extract_fast::batch_ioc_extract_unified_python,
        m
    )?)?;
    // Issue #15: structured entities with positions — replaces Python 25× re.finditer() post-pass
    m.add_function(wrap_pyfunction!(
        ioc_extract_fast::extract_structured_entities_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        ioc_extract_fast::batch_extract_structured_entities_py,
        m
    )?)?;
    // R4.3: SIMD IOC extraction — regex-automata build_many (NEON on M1, ~5× faster for bulk text ≥4KB)
    ioc_extract_simd::register_functions(m)?;
    // ADVERSARY-003: CyberChef-Pipeline — recursive IOC deobfuscation before SIMD scan
    deobfuscate::register(m)?;
    // HEIST-01: Streaming mmap/bytes IOC scanner — zero-copy, 3-4 GB/s on M1 NEON Teddy
    ioc_stream_scan::register_functions(m)?;

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
        // ISSUE-016: PDF forensics — OCG layers, redaction failures, suppressed annotations
        m.add_function(wrap_pyfunction!(pdf::extract_pdf_forensics, m)?)?;
        m.add_function(wrap_pyfunction!(pdf::extract_pdf_forensics_from_bytes, m)?)?;
        m.add_class::<pdf::PdfMetadata>()?;
        m.add_class::<pdf::PdfForensics>()?;
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
    m.add_function(wrap_pyfunction!(
        ioc_cooccurrence_rs::compute_cooccurrence_edges_py,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        ioc_cooccurrence_rs::batch_cooccurrence_edges_py,
        m
    )?)?;

    // SimHash for near-duplicate document detection
    simhash_ext::register_functions(m)?;

    // F320+: LSH index for O(1) near-duplicate detection at scale
    #[cfg(feature = "graph")]
    lsh_index::register_functions(m)?;

    // [M7-FIX] link_predictor: Graph ML link prediction (common neighbors, Adamic-Adar)
    // Previously undeclared despite having register_functions() and Python callers
    link_predictor::register_functions(m)?;

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

    // NEXUS-018-04: Pre-LLM synthesis map-reduce collapser — deterministic
    m.add_function(wrap_pyfunction!(finding_collapser::collapse_findings, m)?)?;
    m.add_function(wrap_pyfunction!(
        finding_collapser::collapser_is_deterministic,
        m
    )?)?;

    // META-007: Propositional consistency verifier — "confident liar" detection
    m.add_function(wrap_pyfunction!(
        consistency_verifier::check_finding_consistency,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        consistency_verifier::get_contradiction_type_name,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        consistency_verifier::quick_consistency_check,
        m
    )?)?;

    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback.
    tls_metadata::register_functions(m)?;

    // TLS 1.3 JA4 fingerprinting + ECH detection via rustls (feature-gated).
    #[cfg(feature = "tls13")]
    tls13::register_functions(m)?;

    // QUIC/HTTP3 via quinn + h3 — F350M-R: true HTTP/3 fallback for fetch_coordinator
    #[cfg(feature = "quic")]
    quic::register(m)?;

    // MODERN-08: pyo3-async-runtimes integration — async Python functions
    // Exposes async versions of DNS, QUIC, and Arti that return awaitables directly.
    // Usage: `await rust.dns.resolve_async("example.com")` instead of
    // `asyncio.to_thread(rust.dns.resolve, "example.com")`
    // Note: shared_tokio feature is included by dns, quic, embedded_tor, nw_framework.
    // BREAKTHROUGH #2: Also enabled for streaming link prediction (shared_tokio in default).
    #[cfg(any(feature = "dns", feature = "quic", feature = "embedded_tor", feature = "nw_framework", feature = "shared_tokio"))]
    async_bridge::register(m)?;

    // MODERN-16: Stealth bridge — Python curl_cffi JA3 ↔ Rust raw I/O
    // Provides async DNS/QUIC bridges for curl_cffi_fetch.py
    // Uses tokio::net::lookup_host directly (no hickory-resolver needed)
    #[cfg(feature = "stealth_bridge")]
    stealth_bridge::register(m)?;

    // NEXTGEN-02: Pre-fetch anti-analysis evasion engine.
    // Detects Cloudflare Turnstile, DataDome, Akamai at TLS handshake level.
    // Abandoned domains skip entire fetch (0 bandwidth, 0 LLM tokens).
    #[cfg(feature = "anti_analysis")]
    anti_analysis::register(m)?;

    // SILICON-03: Apple Network.framework user-space TCP + hardware TLS
    // MODERN-12: Async bridge returns native Python awaitables (no to_thread needed).
    // Requires block2 feature (Objective-C blocks support).
    #[cfg(feature = "block2")]
    nw_connection::register(m)?;

    // [NEXUS]-018-01: Safari WebKit HTTP/2 SETTINGS presets
    h2_safari_preset::register(m)?;

    // DoH/DoT/DoQ DNS resolution — replaces batch_dns.py triple-path duplication.
    // F350M-R: rust.dns.resolve_async(), resolve_happy_eyeballs(), prefetch()
    #[cfg(feature = "dns")]
    dns::register_functions(m)?;

    // HEIST-02: In-process Tor via Arti — replaces subprocess tor binary.
    // rust.arti_bridge.ArtiNode — full circuit control, connection pooling,
    // circuit pre-building. 3-5× throughput vs subprocess, ~40-50% lower latency.
    #[cfg(feature = "embedded_tor")]
    arti_bridge::register(m)?;

    // NEXTGEN-01: Native P2P harvesters (IPFS/TOR/I2P) in Tokio runtime.
    // rust.p2p_harvest.harvest(): unified multi-protocol search
    // rust.p2p_harvest.dht_crawl_async(): BitTorrent DHT (native Tokio)
    // rust.p2p_harvest.ipfs_bitswap_crawl_async(): IPFS Kademlia + BitSwap
    // rust.p2p_harvest.tor_consensus_scrape_async(): Tor consensus scraper
    // rust.p2p_harvest.i2p_leaseset_resolve_async(): I2P LeaseSet resolver
    #[cfg(feature = "p2p_harvest")]
    p2p_harvest::register(m)?;

    // F275: CommonCrypto SHA-256 hardware acceleration on Apple Silicon (~3× vs sha2 crate).
    crypto_accelerate::register_functions(m)?;

    // MODERN-26: Darwin CPU affinity — apply_pcore_affinity(), apply_ecore_affinity()
    #[cfg(target_os = "macos")]
    darwin_affinity::register(m)?;

    // adaptive_scheduler is always compiled — no feature gate needed
    adaptive_scheduler::register_functions(m)?;

    // MODERN-33 + MODERN-34: Apple Silicon topology detection + workload-aware affinity.
    // Provides cached perflevel0/1 counts and apply_affinity_for_workload() helper.
    // Auto-initializes at module import — topology info is ready for all subsequent calls.
    #[cfg(target_os = "macos")]
    topology::register(m)?;

    // swarm_dag: Work-stealing DAG with ROI-based adaptive pool sizing
    // MODERN-13: Now registered by default (was dead code with register() never called)
    #[cfg(feature = "advanced")]
    swarm_dag::register(m)?;
    rate_limit::register_module(m)?; // ISSUE #016: NVD token bucket rate limiter
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

    // [NEXUS]-018-03: Mach vm_remap zero-copy remapping (opt-in, feature=mach)
    #[cfg(feature = "mach")]
    mach_remap::add_module(m)?;

    // ISSUE-004: Rust LMDB backend for DHT — eliminates asyncio.to_thread overhead
    lmdb_dht::register_functions(m)?;

    // Sprint P2-3: IP address parsing, classification, and CIDR containment.
    m.add_function(wrap_pyfunction!(ip_parse::parse_ip_fast, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_private_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_public_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::batch_ip_classify, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::cidr_contains, m)?)?;

    // Sprint F265B-III: LMDB page compression (lz4 + zstd) for hot-edges cache.
    // Wire format: [marker=0x00/0x01/0x02/0x03][payload] — lz4 fast path, zstd fallback,
    // 0x03 = zstd_with_dict (HEIST-07).
    compress::register_functions(m)?;

    // HEIST-03: Native DB wire-protocol extraction (MongoDB, Redis, Elasticsearch)
    // No external crate deps — pure Rust std + crossbeam-channel.
    // Feature-gated: native_db — compile with --features native_db
    #[cfg(feature = "native_db")]
    native_db::register(m)?;

    // DEEP-GIT: Git forensics crate for packfile analysis
    // Extracts author/committer emails, PGP keyIDs, timestamps, SSH keys
    // Uses mmap, streaming zlib, delta chains — <500ms target for 500MB packfiles
    #[cfg(feature = "deep_git")]
    git_forensics::register_module(m)?;

    // DEEP-WARC: WARC file byte-seek engine for certificate extraction
    // Extracts F-5/F-6/3.4 data (fingerprints, issuer chain, SANs) from WARC files
    // Memory-mapped access with streaming record parsing
    #[cfg(feature = "deep_warc")]
    warc_parser::register_module(m)?;

    // DEEP-UNINDEXED: Unindexed storage scanner (MinIO, rsync, S3)
    // Reuses native_db streaming (50 MB cap at native_db.rs:53)
    #[cfg(feature = "deep_unindexed")]
    unindexed_scanner::register_module(m)?;

    // DEEP-AC: NEON SIMD Aho-Corasick as shared primitive for payload scan
    // F-8: High-performance multi-pattern matching with ARM NEON acceleration
    #[cfg(feature = "deep_ac")]
    aho_corasick_simd::register_module(m)?;

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

    // HEIST-05: simdjson_extract — Zero-alloc JSON Pointer extraction via simd-json.
    // ARM NEON native, 2-4x faster than serde_json on M1. Used for CT log scanning.
    #[cfg(feature = "simdjson")]
    simdjson_extract::register_functions(m)?;

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

    // [META]-004: Elastic pool resize — phase-aware rayon thread pool management
    // (no feature gate, always compiled — used by always-compiled pool_run.rs)
    elastic_pool::register_functions(m)?;

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

    // R4.4: TinyLFU LRU cache for cross-worker graph results.
    #[cfg(feature = "advanced")]
    m.add_class::<graph_cache::PyGraphLRUCache>()?;

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

    // SILICON-02: whisper.cpp transcription via whisper-rs with CoreML/ANE backend.
    // Provides: rust.whisper.transcribe(), is_available(), get_cache_dir()
    // M1 8GB: Only tiny/base models, bounded 1 concurrent inference.
    #[cfg(feature = "whisper")]
    whisper::register(m)?;

    // R22: Accelerate/vDSP FFI — batch cosine similarity for NER engine
    // Gated because vDSP symbols are unavailable on macOS 26.5+ (Darwin 25.5+)
    // Python fallback: brain/ner_engine.py uses scipy/numpy for cosine similarity
    #[cfg(feature = "accelerate")]
    accelerate::register(m)?;

    // R22: Metal GPU batch matmul — MoE router integration (feature-gated)
    #[cfg(feature = "metal")]
    metal_compute::register(m)?;

    // SILICON-04: Shared Metal buffer — zero-copy Rust↔Python↔MLX tensor sharing
    #[cfg(feature = "metal_shared")]
    metal_shared_buf::register(m)?;

    // SILICON-01: Metal GPU opportunistic hash cracking — GPU during I/O wait
    #[cfg(feature = "metal")]
    metal_hashcrack::register(m)?;

    // IO-4: IOSurface zero-copy bridge — CVPixelBuffer → Metal texture
    #[cfg(feature = "iosurface")]
    iosurface_bridge::register(m)?;

    // DuckDB bridge — isolated module for future cdylib extraction (saves ~8 MB .dylib)
    #[cfg(feature = "data")]
    data::register_functions(m)?;

    // ISSUE-6: Bounded ring buffers — recent_iocs ring, M1 8GB safe
    // collections::register_functions(m)?;

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

    // MODERN-30: Export detect_p_core_count for topology-aware pool sizing
    #[cfg(feature = "data")]
    m.add_function(wrap_pyfunction!(detect_p_core_count, m)?)?;

    // R24: OpenTelemetry tracing
    #[cfg(feature = "otel")]
    tracing::register(m)?;

    // GRAPH-03: .onion v3 address validation — Ed25519 checksum verification
    m.add_function(wrap_pyfunction!(
        onion_validation::rust_validate_onion_v3,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        onion_validation::rust_validate_onion_v3_detailed,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        onion_validation::rust_validate_onion_batch,
        m
    )?)?;

    // ISSUE-011: Tantivy fulltext search (mmap-backed, zero-copy BM25)
    // Feature-gated: fulltext = ["dep:tantivy"]
    // Enables: fulltext.create_index(), fulltext.search()
    // Replaces Python BM25Index in knowledge/rag_engine.py
    #[cfg(feature = "fulltext")]
    fulltext_index::register(m)?;

    Ok(())
}

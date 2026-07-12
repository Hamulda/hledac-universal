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
pub mod bloom;
pub mod compress;
pub mod regex_lz4; // LZ4-compressed pattern store for 10k+ patterns
pub mod content_hasher;
pub mod crypto_accelerate;
pub mod adaptive_scheduler;
pub mod async_query; // ISSUE-013: std::thread + rayon pool pro async Rust DuckDB queries
pub mod graph_traverse;
pub mod hot_edges_rs;
pub mod html_parse;
pub mod int_counter_layout;
pub mod ioc_dedup;
pub mod ioc_patterns;
pub mod dns_tunnel; // ISSUE #33: DNS tunneling detection (entropy, n-gram, wavelet)
pub mod ioc_extract;
pub mod ioc_extract_fast;
pub mod ioc_extract_simd; // R4.3: SIMD IOC extraction via regex-automata build_many (NEON on M1)
pub mod ioc_cooccurrence_rs; // Issue 4.1: Rust HashMap<->BitSet co-occurrence engine
pub mod madvise;
pub mod metal_compute;
pub mod metal_pattern_matcher;
pub mod memory;
pub mod ip_parse;
pub mod quality_gate;
pub mod rolling_hash;
pub mod signal_batch;
pub mod simd_similarity;
pub mod simhash_ext;
pub mod lsh_index; // F320+: LSH index for O(1) near-duplicate detection at scale
pub mod text_norm;
pub mod feed_decision;
pub mod feed_pipeline;
pub mod xml_sanitize;
pub mod url_engine;
pub mod url_ops;
pub mod url_set;
pub mod xxhash_ext;
pub mod zero_copy;
pub mod serde_json_rs;
pub mod arrow_batch_builder;
pub mod parquet_reader; // F320+: Lazy parquet reader — paginated Arrow, 100GB+ IOC history bez OOM
pub mod spsc_queue;
pub mod mpsc_pool; // Bounded MPSC pool — replaces asyncio.Queue in evidence_log
pub mod pool_run;
pub mod embedding_index; // ANN HNSW index v Rust (M1 8GB safe)
pub mod lancedb_bridge; // F320+: Rust HNSW bridge → LanceDB Python API (ANN only, zero-copy)
pub mod graph_cache;    // TinyLFU LRU cache pro graph operations
pub mod dedup_bloom;    // Distribuovaný BloomFilter s Count-Min Sketch
pub mod telemetry_agg;  // Real-time metrics aggregation
pub mod health;         // Issue #22: health_check() endpoint
pub mod claims_extraction; // ISSUE-27: CPU-bound claims extraction (polarity, confidence, sentence split)
pub mod sprint_policies;
pub mod tls_metadata;    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback
pub mod gil;            // F5.2: GIL management — std::thread + rayon pools (ne pyo3-async)
pub mod data;           // DuckDB bridge — isolated module for future cdylib extraction

// ---------------------------------------------------------------------------
// Rayon thread pools — M1 8GB safe, P/E core optimized
// ---------------------------------------------------------------------------
//
// M1 Air has 4P + 4E cores = 8 logical CPUs.
//
// Two-tier strategy (F270):
//   ┌─────────────────────────────────────────────────────────────────────────┐
//   │ WORKLOAD TYPE         │ THREADS │ POOL          │ MODULES             │
//   ├───────────────────────┼─────────┼───────────────┼─────────────────────┤
//   │ CPU-bound (SIMD/hot)  │ 4       │ cpu_pool()    │ quality_gate,       │
//   │                       │         │               │ xxhash_par, simd     │
//   │ I/O-bound (DuckDB)    │ 2       │ io_pool()     │ graph_traverse,      │
//   │                       │         │               │ compress             │
//   │ Mixed (IOC extract)   │ 1/2     │ mixed_pool()  │ url_ops, ioc_fast,  │
//   │                       │         │               │ simhash, html_parse  │
//   └───────────────────────┴─────────┴───────────────┴─────────────────────┘
//
// P-core utilization:
//   - CPU-bound: 4 threads = 4 P-cores (100% P-core for compute)
//   - I/O-bound: 2 threads = ceiling for DuckDB thread-local conn bottleneck
//   - Mixed: adaptive 1-2 threads based on batch size
//
// E-core strategy:
//   - macOS automatically steers I/O-bound threads to E-cores via QoS
//   - CPU-bound pool uses 4 threads → OS优先 schedules on P-cores
//   - When P-cores saturated, I/O threads spill to E-cores (acceptable)
//
// Calibration (F270, 2026-06-25):
//   - CPU-bound threshold: 32 items (was 64 for 2-thread)
//   - I/O-bound threshold: 64 items (DuckDB conn setup amortized)
//   - Chunk: 4 threads × 32 items = 128 (CPU-bound)
//   - Chunk: 2 threads × 64 items = 128 (I/O-bound)

// MIXED_THRESHOLD removed — now fully delegated to adaptive_scheduler::mixed_threshold()
// which is pressure-aware (16 idle / 32 normal / 64 pressure).
// MLX Metal-aware threshold: adaptive_scheduler::mixed_threshold_via_metal()

/// Process-wide singleton — 4 P-core ceiling for CPU-bound work.
///
/// Shared by quality_gate, xxhash_ext parallel, simd_similarity.
///
/// 4 threads × 2.5 MiB = 10 MB total stack.
/// For SIMD/hot CPU-bound: SIMD width 4×f32 on NEON = 4× throughput per thread.
///
/// Use when: BLAKE2b, xxhash parallel, cosine similarity on embeddings.
pub(crate) fn cpu_pool() -> &'static ThreadPool {
    static POOL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(4)
            .stack_size(2_621_440)
            .thread_name(|i| format!("hledac-cpu-{}", i))
            .build()
            .expect("cpu_pool: ThreadPoolBuilder::build failed (OOM?)")
    });
    &POOL
}

/// Process-wide singleton — 2-thread ceiling for I/O-bound work.
///
/// Shared by graph_traverse (DuckDB read-only), compress.
///
/// 2 threads × 2.5 MiB = 5 MB total stack.
/// DuckDB thread-local connection is the bottleneck — 2 threads matches the
/// F265-U5 thread-local pool ceiling. E-cores auto-handled by macOS QoS.
pub(crate) fn io_pool() -> &'static ThreadPool {
    static POOL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(2_621_440)
            .thread_name(|i| format!("hledac-io-{}", i))
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
            .stack_size(2_621_440)
            .thread_name(|i| format!("hledac-mixed-1-{}", i))
            .build()
            .expect("mixed_pool(1): ThreadPoolBuilder::build failed (OOM?)")
    });
    static POOL_PAIR: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(2_621_440)
            .thread_name(|i| format!("hledac-mixed-2-{}", i))
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
        // 4 threads = all P-cores for CPU-bound SIMD work
        assert_eq!(cpu_pool().current_num_threads(), 4);
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

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Expose package version for Python-side ABI compatibility checking (F275).
    // CARGO_PKG_VERSION is set by Cargo at compile time from Cargo.toml.
    m.add_function(wrap_pyfunction!(query_terms::scan_query_context, m)?)?;
    m.add_function(wrap_pyfunction!(query_terms::extract_payload_context, m)?)?;

    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(__version_info__, m)?)?;

    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<bloom::BloomFilter>()?;
    // F266-U1: file-backed mmap Bloom filter (persists across restart).
    bloom::register(m)?;
    // P3-3: ephemeral batch Bloom filter check.
    m.add_function(wrap_pyfunction!(bloom::bloom_check_batch, m)?)?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;

    // URL dedup via FNV-1a hashing — both in-memory and mmap-backed
    m.add_class::<url_set::MmapUrlSet>()?;
    m.add_class::<url_set::UrlSet>()?;

    // IOC extraction + URL normalization
    dns_tunnel::register_functions(m)?;  // ISSUE #33: entropy, n-gram, wavelet analysis
    ioc_extract::register_functions(m)?;
    // Fast IOC extraction: unified Aho-Corasick automaton (single O(n) scan)
    m.add_function(wrap_pyfunction!(ioc_extract_fast::ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified_python, m)?)?;
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
    lsh_index::register_functions(m)?;

    // xxHash3-64 for non-cryptographic content hashing (dedup keys, cache IDs)
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_64, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex, m)?)?;
    // F7.2: batch SIMD hashing — rayon-parallel for large batches (≥256 items)
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::double_hash_64, m)?)?;
    m.add_class::<xxhash_ext::StreamHasher64>()?;

    // F320: batch xxh3-64 via rayon — parallel prompt cache fingerprinting (Apple Silicon NEON)
    m.add_function(wrap_pyfunction!(content_hasher::batch_xxh3_64_hex, m)?)?;

    // SHA-256 + BLAKE3 content hashing (TLS cert fingerprint, body dedup).
    // NEON-enabled on aarch64 (Apple Silicon), scalar fallback elsewhere.
    m.add_class::<content_hasher::ContentHasher>()?;

    // Issue B5: TLS cert metadata — single Rust call replacing 5-level Python fallback.
    tls_metadata::register_functions(m)?;

    // F275: CommonCrypto SHA-256 hardware acceleration on Apple Silicon (~3× vs sha2 crate).
    crypto_accelerate::register_functions(m)?;
    adaptive_scheduler::register_functions(m)?;
    // F5.2: FeedDominanceGuard + LaneBudgetPool in Rust (zero-copy, no GIL)
    sprint_policies::register(m)?;

    // IntCounterLayout — SoA buffer for hot-path integer counters
    // (drop-in replacement for runtime.int_counter_layout.IntCounterLayout).
    // M1 8GB safe, bounded, fail-soft. Wire format: i64 (signed 8B per slot).
    int_counter_layout::register_functions(m)?;

    // HotEdgeCounterRust — in-memory L1 write buffer for hot edge counts.
    hot_edges_rs::register_functions(m)?;

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
    graph_traverse::register_functions(m)?;

    // Sprint F266: Streaming HTML parsing via lol_html — link/email/title/meta extraction.
    html_parse::register_functions(m)?;

    // 3A: Native RSS + available-memory probe via sysinfo.
    memory::register_functions(m)?;

    // Sprint P2-2: Batch signal aggregation — ARM NEON-accelerated source weight
    // computation and signal vector aggregation for F199A reward-driven adaptation.
    // Fallback: scalar Rust on non-aarch64.
    signal_batch::register_functions(m)?;

    // PAR-1 P1: SIMD-accelerated batch cosine similarity for embedding re-ranking.
    // Fallback for environments without MLX (CI, testing). NEON on AArch64.
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
    arrow_batch_builder::register(m)?;

    // F350+: LZ4-compressed pattern store for 10k+ patterns (M1 8GB RAM optimization).
    regex_lz4::register(m)?;

    // F320+: Lazy parquet reader — paginated Arrow Row-Group iterator.
    // Enables 100GB+ IOC history reads without OOM on M1 8GB.
    parquet_reader::register(m)?;

    // R4.1: Rayon pool runners — Python-callable wrappers for CPU/IO pools.
    pool_run::register_functions(m)?;

    // R4.2: Metal-accelerated batch pattern matching for IoC scanning.
    // Falls back to Rust NEON Aho-Corasick when Metal unavailable.
    metal_pattern_matcher::register_functions(m)?;

    // F320+: Rust HNSW ANN bridge for LanceDB — pure vector insert + search without
    // LanceDB Python API overhead. Hybrid LanceDB remains for FTS/metadata/persistence.
    m.add_class::<lancedb_bridge::PyHNSWBridge>()?;

    // R4.3: ANN HNSW index for MLX embeddings re-ranking (M1 8GB safe).
    // 200k nodes × 384d × 4B = ~307 MB max.
    m.add_class::<embedding_index::PyHNSWIndex>()?;

    // R4.4: TinyLFU LRU cache for cross-worker graph results.
    m.add_class::<graph_cache::PyGraphLRUCache>()?;

    // R4.5: Distribuovaný BloomFilter s Count-Min Sketch.
    m.add_class::<dedup_bloom::PyDistributedBloomFilter>()?;

    // Issue #22: Health endpoint
    health::register(m)?;

    // ISSUE-27: Claims extraction — CPU-bound sentence splitting, polarity, confidence.
    // Pre-compiled regexes via LazyLock, mixed_pool adaptive threading.
    claims_extraction::register_functions(m)?;

    // ISSUE-013: Async Rust DuckDB queries via std::thread + rayon pool.
    // rust_async_query() se volá z Python asyncio přes asyncio.to_thread().
    async_query::register(m)?;

    // F5.2: GIL management — Python::with_gil() + rayon pools (ne pyo3-async)
    gil::register_functions(m)?;

    // DuckDB bridge — isolated module for future cdylib extraction (saves ~8 MB .dylib)
    data::register_functions(m)?;

    // C3: Feed decision classifiers — pure functions for feed signal classification.
    feed_decision::register_functions(m)?;
feed_pipeline::register(m)?;
Ok(())
}

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

pub mod aho_corasick;
pub mod bloom;
pub mod compress;
pub mod content_hasher;
pub mod graph_traverse;
pub mod hot_edges_rs;
pub mod html_parse;
pub mod int_counter_layout;
pub mod ioc_dedup;
pub mod ioc_extract;
pub mod ioc_extract_fast;
pub mod madvise;
pub mod memory;
pub mod ip_parse;
pub mod quality_gate;
pub mod rolling_hash;
pub mod signal_batch;
pub mod simd_similarity;
pub mod simhash_ext;
pub mod text_norm;
pub mod url_engine;
pub mod url_ops;
pub mod url_set;
pub mod xxhash_ext;
pub mod zero_copy;

// ---------------------------------------------------------------------------
// Rayon thread pools — M1 8GB safe, P-core optimized
// ---------------------------------------------------------------------------
//
// M1 Air has 4P + 4E cores = 8 logical CPUs.
//
// E-core contention problem (solved):
//   - 4-thread pool from F265: all 4 workers could land on E-cores
//     (each E-core ~4× slower per-thread than P-core for compute-bound work).
//   - DuckDB read-only workload: 2 workers optimal (DuckDB thread-local
//     connections are the bottleneck, not CPU — F265-U5 graph_traverse data).
//   - URL/IOC/Hash classify+extract: sub-microsecond per item; 1 worker
//     for batch sizes below the parallel threshold eliminates thread-spawn
//     overhead entirely.
//
// Three-tier strategy:
//   1. bulk_pool()          — process-wide 2-thread singleton for all callers.
//                               2 threads = P-core ceiling (E-core avoidance).
//                               2 × 1.5 MiB = 3 MB total stacks.
//                               Used by: graph_traverse, compress, quality_gate
//                               (large batches where fixed 2-thread cost is amortised).
//   2. bulk_pool_for_size(n) — per-call pool; 1 thread for n < THRESHOLD (64),
//                               2 threads for n ≥ THRESHOLD.  Zero mutex overhead
//                               (two separate statics).  Pattern:
//                               `bulk_pool_for_size(n).install(|| { ... })`.
//                               Used by: url_ops, ioc_extract_fast, simhash_ext,
//                               xxhash_ext, aho_corasick, html_parse, text_norm.
//   3. Serial fallback       — inline for very small batches where even
//                               pool creation overhead outweighs parallel gains.
//
// Thread-count selection (empirically calibrated for 2 threads):
//   ┌──────────────┬────────┬──────────────────────────────────────────────┐
//   │ Workload     │ Threads│ Rationale                                   │
//   ├──────────────┼────────┼──────────────────────────────────────────────┤
//   │ n < 30       │ 1      │ pool spawn ~0.5ms > serial work             │
//   │ n 30–200     │ 1      │ url_ops/simhash: calibrated for 2 threads    │
//   │ n 200–500    │ 2      │ html_parse / xxhash large batches            │
//   │ n ≥ 500      │ 2      │ graph_traverse / compress / quality_gate     │
//   └──────────────┴────────┴──────────────────────────────────────────────┘
//
// Calibration notes (F266-U5, 2026-06-21):
//   - 4 workers → 2 threads: halve parallel thresholds (50→25, 64→32, 256→128)
//   - Chunk size: 2 workers × 64 items = 128 (was 4 workers × 64 = 256)
//   - XXHash: 256 → 128 items for parallel break-even
//   - SimHash: 100 → 50, 64 → 32 chunk
//
// core_affinity: on Linux, worker 0→P-core-0, worker 1→P-core-1.
// On macOS/AArch64 the OS scheduler handles P-core preference — the 2-thread
// ceiling is sufficient. The core_affinity crate adds ~100ms init cost so
// it is only wired on cfg(target_os = "linux").

/// Threshold for switching from 1 to 2 threads in `bulk_pool_for_size()`.
/// Below this, serial is faster (pool spawn ~0.5ms > parallel savings).
const ADAPTIVE_THRESHOLD: usize = 64;

/// Process-wide singleton — 2 P-core ceiling.
///
/// Shared by graph_traverse, compress, quality_gate (large).
/// 2 threads × 1.5 MiB = 3 MB total stack (vs old 8 MB).
/// DuckDB thread-local conn is the bottleneck — 2 threads matches the
/// F265-U5 thread-local pool ceiling.
pub(crate) fn bulk_pool() -> &'static ThreadPool {
    static POOL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(1_572_864) // 1.5 MiB — shallow stack, no deep recursion
            .thread_name(|i| format!("hledac-bulk-{}", i))
            .build()
            .expect("bulk_pool: ThreadPoolBuilder::build failed (OOM?)")
    });
    &POOL
}

/// Per-call memory-bounded thread pool — zero-mutex, two static pools.
///
/// Pattern: `bulk_pool_for_size(n).install(|| { ... })`
///
/// Returns a 1-thread pool for n < ADAPTIVE_THRESHOLD (64):
///   Eliminates pool spawn overhead (~0.5ms) for small batches where
///   serial execution is faster than parallel.
///
/// Returns a 2-thread pool for n ≥ ADAPTIVE_THRESHOLD:
///   Matches P-core count on M1 (4P+4E), below E-core contention threshold.
///
/// Implementation: two separate `LazyLock<ThreadPool>` statics (POOL_SMALL /
/// POOL_LARGE), selected by thread-count.  Zero Mutex, zero HashMap,
/// zero rebuild on thread-count change.
///
/// Calibrated for M1 8GB UMA:
///   - Stack: 1.5 MiB per worker (shallow — no deep recursion in hot paths)
///   - Memory: 1-thread ≈ 1.5 MiB, 2-thread ≈ 3 MiB total
pub(crate) fn bulk_pool_for_size(n_items: usize) -> &'static ThreadPool {
    static POOL_SMALL: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(1)
            .stack_size(1_572_864)
            .thread_name(|i| format!("hledac-scope-1-{}", i))
            .build()
            .expect("bulk_pool_for_size(1): ThreadPoolBuilder::build failed (OOM?)")
    });
    static POOL_LARGE: LazyLock<ThreadPool, fn() -> ThreadPool> = LazyLock::new(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(1_572_864)
            .thread_name(|i| format!("hledac-scope-2-{}", i))
            .build()
            .expect("bulk_pool_for_size(2): ThreadPoolBuilder::build failed (OOM?)")
    });

    if n_items < ADAPTIVE_THRESHOLD {
        &POOL_SMALL
    } else {
        &POOL_LARGE
    }
}

/// Alias for backward compatibility — `bulk_pool_for_size` is the canonical name.
#[deprecated(since = "F266-U5", note = "use bulk_pool_for_size(n) instead")]
pub(crate) fn scoped_pool_for(n_items: usize) -> &'static ThreadPool {
    bulk_pool_for_size(n_items)
}

#[cfg(test)]
mod lib_tests {
    use super::*;

    #[test]
    fn test_bulk_pool_idempotent() {
        let a = bulk_pool() as *const ThreadPool;
        let b = bulk_pool() as *const ThreadPool;
        assert_eq!(a, b, "bulk_pool() must return a stable singleton");
    }

    #[test]
    fn test_bulk_pool_thread_count() {
        let pool = bulk_pool();
        // 2 threads = P-core ceiling
        assert_eq!(pool.current_num_threads(), 2);
    }

    #[test]
    fn test_bulk_pool_for_size_small() {
        let pool = bulk_pool_for_size(30);
        assert_eq!(pool.current_num_threads(), 1, "n < THRESHOLD → 1 thread");
    }

    #[test]
    fn test_bulk_pool_for_size_large() {
        let pool = bulk_pool_for_size(64);
        assert_eq!(pool.current_num_threads(), 2, "n ≥ THRESHOLD → 2 threads");
    }

    #[test]
    fn test_bulk_pool_for_size_reuse() {
        // Same thread count → same static pool instance (pointer equality)
        let a = bulk_pool_for_size(10) as *const ThreadPool;
        let b = bulk_pool_for_size(10) as *const ThreadPool;
        assert_eq!(a, b, "bulk_pool_for_size(10) must reuse POOL_SMALL");
        let c = bulk_pool_for_size(200) as *const ThreadPool;
        let d = bulk_pool_for_size(200) as *const ThreadPool;
        assert_eq!(c, d, "bulk_pool_for_size(200) must reuse POOL_LARGE");
    }

    #[test]
    fn test_scoped_pool_for_deprecated() {
        #[allow(deprecated)]
        let pool = scoped_pool_for(30);
        assert_eq!(pool.current_num_threads(), 1);
    }
}

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<bloom::BloomFilter>()?;
    // F266-U1: file-backed mmap Bloom filter (persists across restart).
    bloom::register(m)?;
    // P3-3: ephemeral batch Bloom filter check.
    m.add_function(wrap_pyfunction!(bloom::bloom_check_batch, m)?)?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;
    m.add_class::<rolling_hash::FastHasher>()?;

    // URL dedup via FNV-1a hashing — both in-memory and mmap-backed
    m.add_class::<url_set::MmapUrlSet>()?;
    m.add_class::<url_set::UrlSet>()?;

    // IOC extraction + URL normalization
    ioc_extract::register_functions(m)?;
    // Fast IOC extraction: unified Aho-Corasick automaton (single O(n) scan)
    m.add_function(wrap_pyfunction!(ioc_extract_fast::ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified_python, m)?)?;
    url_engine::register_functions(m)?;
    url_ops::register_functions(m)?;

    // IOC deduplication store (cross-sprint persistence)
    ioc_dedup::register_class(m)?;

    // SimHash for near-duplicate document detection
    simhash_ext::register_functions(m)?;

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

    // SHA-256 + BLAKE3 content hashing (TLS cert fingerprint, body dedup).
    // NEON-enabled on aarch64 (Apple Silicon), scalar fallback elsewhere.
    m.add_class::<content_hasher::ContentHasher>()?;

    // IntCounterLayout — SoA buffer for hot-path integer counters
    // (drop-in replacement for runtime.int_counter_layout.IntCounterLayout).
    // M1 8GB safe, bounded, fail-soft. Wire format: i64 (signed 8B per slot).
    int_counter_layout::register_functions(m)?;

    // HotEdgeCounterRust — in-memory L1 write buffer for hot edge counts.
    hot_edges_rs::register_functions(m)?;

    // Sprint P1-5: Quality gate compute kernels — BLAKE2b-128 dedup fingerprint,
    // Shannon entropy, text/URL normalization. BLAKE2b-128 output is bit-
    // identical to Python's hashlib.blake2b(digest_size=16) so existing
    // LMDB-persisted fingerprints remain valid (no migration).
    quality_gate::register_functions(m)?;

    // Sprint F265B-III: Unicode NFC/NFD normalization + diacritic stripping.
    text_norm::register_functions(m)?;

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

    Ok(())
}

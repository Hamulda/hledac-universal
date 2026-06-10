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
use std::sync::OnceLock;

pub mod aho_corasick;
pub mod bloom;
pub mod content_hasher;
pub mod int_counter_layout;
pub mod ioc_dedup;
pub mod ioc_extract;
pub mod quality_gate;
pub mod rolling_hash;
pub mod simhash_ext;
pub mod url_engine;
pub mod url_ops;
pub mod url_set;
pub mod xxhash_ext;

// ---------------------------------------------------------------------------
// Shared bounded rayon thread pool — M1 8GB safe
// ---------------------------------------------------------------------------
//
// M1 Air has 4P + 4E cores = 8 logical CPUs. Default rayon global pool
// = 8 workers × 8 MB stack = 64 MB just for thread stacks. Under sustained
// load on a 6.25 GB application budget this is wasteful AND the E-core
// workers (4× slower than P-cores) waste cycles for sub-millisecond work.
//
// This helper returns a process-wide dedicated pool with bounded
// resources that all batch_* functions share:
//
//   * `num_threads(2)` — bound parallelism to 2 workers (≈2 P-cores).
//     2 × 8 MB = 16 MB stacks (75% less than the default 64 MB).
//   * `stack_size(2 MiB)` — reduce per-worker stack to 2 MB. URL/string
//     classification and fingerprint work is shallow — no recursive descent,
//     no large stack frames. 2 MiB is the Rust default for release builds
//     and is plenty here. Total: 2 × 2 MB = 4 MB (94% less than default).
//   * Named threads ("hledac-bulk-N") — visible in `ps`/Instruments for
//     observability during long sprints.
//   * `OnceLock` — lazy init, no startup cost. First call from any batch_*
//     function pays the ~1 ms pool build cost; subsequent calls are O(1)
//     lock-free reads. The static is `Send + Sync` so the pool is shared
//     safely across all callers in the process.
//
// Pattern: `crate::bulk_pool().install(|| { ... par_iter() ... })`.
// We deliberately do NOT call `rayon::ThreadPoolBuilder::build_global()`
// because (a) it panics if any other code path has already initialized
// the global pool, and (b) it would affect every rayon user in the
// process — not just ours.
//
// Fail-soft: the inner `build()` can only fail under resource exhaustion
// (typical OOM). We panic at first call rather than silently fall back to
// the default global pool — that would defeat the M1 8GB memory budget
// and is much harder to diagnose in production.

/// Process-wide bounded rayon pool for batch operations.
///
/// Shared by `url_ops::batch_classify` and `quality_gate::batch_*`.
/// Created lazily on first call; subsequent calls return the same instance.
pub(crate) fn bulk_pool() -> &'static ThreadPool {
    static POOL: OnceLock<ThreadPool> = OnceLock::new();
    POOL.get_or_init(|| {
        ThreadPoolBuilder::new()
            .num_threads(2)
            .stack_size(2 * 1024 * 1024) // 2 MiB per worker stack
            .thread_name(|i| format!("hledac-bulk-{}", i))
            .build()
            .expect("bulk_pool: ThreadPoolBuilder::build failed (OOM?)")
    })
}

#[cfg(test)]
mod lib_tests {
    use super::*;

    #[test]
    fn test_bulk_pool_idempotent() {
        // OnceLock must return the same instance on every call.
        let a = bulk_pool() as *const ThreadPool;
        let b = bulk_pool() as *const ThreadPool;
        assert_eq!(a, b, "bulk_pool() must return a stable singleton");
    }

    #[test]
    fn test_bulk_pool_thread_count() {
        let pool = bulk_pool();
        // num_threads(2) — verify the pool honors the bound.
        assert_eq!(pool.current_num_threads(), 2);
    }
}

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<bloom::BloomFilter>()?;
    // F266-U1: file-backed mmap Bloom filter (persists across restart).
    bloom::register(m)?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;
    m.add_class::<rolling_hash::FastHasher>()?;

    // URL dedup via FNV-1a hashing
    m.add_class::<url_set::UrlSet>()?;

    // IOC extraction + URL normalization
    ioc_extract::register_functions(m)?;
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
    m.add_class::<xxhash_ext::StreamHasher64>()?;

    // SHA-256 + BLAKE3 content hashing (TLS cert fingerprint, body dedup).
    // NEON-enabled on aarch64 (Apple Silicon), scalar fallback elsewhere.
    m.add_class::<content_hasher::ContentHasher>()?;

    // IntCounterLayout — SoA buffer for hot-path integer counters
    // (drop-in replacement for runtime.int_counter_layout.IntCounterLayout).
    // M1 8GB safe, bounded, fail-soft. Wire format: i64 (signed 8B per slot).
    int_counter_layout::register_functions(m)?;

    // Sprint P1-5: Quality gate compute kernels — BLAKE2b-128 dedup fingerprint,
    // Shannon entropy, text/URL normalization. BLAKE2b-128 output is bit-
    // identical to Python's hashlib.blake2b(digest_size=16) so existing
    // LMDB-persisted fingerprints remain valid (no migration).
    quality_gate::register_functions(m)?;

    Ok(())
}

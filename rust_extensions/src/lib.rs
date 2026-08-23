//! hledac-rust-extensions — PyO3 Python extension for hledac
//!
//! This crate provides high-performance Rust implementations of critical
//! security operations used by the hledac tool.

use pyo3::prelude::*;

mod pools;
pub use pools::{cpu_pool, io_pool, mixed_pool};
pub use pools::elastic::{get_pool_metrics, PoolMetrics, PoolPhase};

mod data;
mod ioc;
mod simd;
mod stix_2_1;

// Internal utilities
mod gil;
mod pool_run;
mod qos_class_helpers;

mod _entropy;

// ZOMBIE: No Python callers found (Python fallback: scipy/numpy cosine)
#[allow(dead_code)]
mod accelerate;
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod adaptive_scheduler;

mod aho_corasick;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod aho_corasick_simd;

mod anti_analysis;
mod bloom;
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod circuit_breaker;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod claims_extraction;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod compress;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod consistency_verifier;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod content_hasher;

// C6: Wired to secrets_vault/vault.py via rust.raw
#[allow(dead_code)]
mod crypto_accelerate;

// B4: DedupBloom - Distributed BloomFilter for cross-instance URL dedup
mod dedup_bloom;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod deobfuscate;

mod dns;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod elastic_pool;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod feed_decision;

// ZOMBIE: No callers (replaced by live_feed_pipeline.py)
#[allow(dead_code)]
mod feed_pipeline;
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod ffi_safe;

mod finding_collapser;

#[cfg(feature = "fulltext")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod fulltext_index;

// ORPHANED: Python has its own Git packfile analysis
#[allow(dead_code)]
mod git_forensics;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod graph_analytics;

// B3: Graph Cache - shared LRU cache for graph traversal queries
mod graph_cache;

mod graph_centrality;
mod graph_traverse;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod h2_safari_preset;

// ZOMBIE: No Python callers (Python path exists)
#[allow(dead_code)]
mod health;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod hot_edges_rs;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod html_parse;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod int_counter_layout;
mod ioc_cooccurrence_rs;
mod ioc_dedup;
// ZOMBIE: No Python callers (Python uses ioc module facade)
#[allow(dead_code)]
mod ioc_extract;

mod ioc_extract_fast;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod ioc_extract_simd;

mod ioc_patterns;
mod ioc_patterns_generated;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod ioc_stream_scan;

mod ip_parse;
mod link_predictor;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod lsh_index;

mod memory;

// B6: Native DB - MongoDB/Redis/ES wire-protocol extraction
// python_callers: recon/exposed_service_hunter.py, network/native_extraction.py,
//                 recon/native_db_client.py (Tier 0 MongoDB hello detector)
mod native_db;

// B5: Pipeline composition - wired via pipeline_compose_wiring.py
// Provides MAP/FILTER/FOLD operators with asyncio.to_thread bridge
mod pipeline_compose;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod quality_gate;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod query_terms;

// DEAD: collections_backup exists with register_functions() but is never declared in lib.rs
// No Python callers, no registration call needed
#[allow(dead_code)]
mod collections_backup;

mod rate_limit;

// ZOMBIE: Marked as REMOVED but kept for potential future use
// No Python callers, no registration call needed
#[allow(dead_code)]
mod regex_lz4;

mod rolling_hash;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod serde_json_rs;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod signal_batch;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod simd_similarity;

#[cfg(feature = "simdjson")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod simdjson_extract;

// G4: simhash_ext — ACTIVATED (near-duplicate detection via Rust SIMD)
mod simhash_ext;

mod sprint_policies;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod spsc_queue;

// G5.MPSC_POOL: Wired to fetch_coordinator.py for typed channel queue
mod mpsc_pool;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod telemetry_agg;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod text_norm;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod text_similarity;

// ZOMBIE: No Python callers (Python fallback: tls_metadata)
#[allow(dead_code)]
mod tls13;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod topology;

// ZOMBIE: No Python callers (internal use only)
#[allow(dead_code)]
mod tracing;

mod unicode_fingerprint;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod unindexed_scanner;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod url_engine;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod url_ops;

mod url_set;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod warc_parser;

mod xxhash_ext;

// ZOMBIE: No Python callers
#[allow(dead_code)]
mod zero_copy;

#[cfg(feature = "data")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod arrow_batch_builder;

#[cfg(feature = "data")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod arrow_c_data;

#[cfg(feature = "data")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod arrow_ipc_mmap;

// FIX: aimd_controller was declared as file but never in lib.rs - added now
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod aimd_controller;

// FIX: federated_qtable was declared as file but never in lib.rs - added now
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod federated_qtable;

#[cfg(all(target_os = "macos", feature = "ane"))]
mod ane;

#[cfg(all(target_os = "macos", feature = "metal"))]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod metal_compute;

#[cfg(all(target_os = "macos", feature = "metal"))]
// DEPRECATED: No Python callers (metal_hashcrack module deprecated)
#[allow(dead_code)]
mod metal_hashcrack;

#[cfg(all(target_os = "macos", feature = "metal_shared"))]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod metal_shared_buf;

#[cfg(all(target_os = "macos", feature = "iosurface"))]
mod iosurface_bridge;

#[cfg(all(target_os = "macos", feature = "nw_framework"))]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod nw_connection;

#[cfg(feature = "shared_tokio")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod async_bridge;

#[cfg(feature = "shared_tokio")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod async_query;

#[cfg(feature = "shared_tokio")]
mod async_runtime;

#[cfg(feature = "shared_tokio")]
mod stealth_bridge;

#[cfg(feature = "shared_tokio")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod swarm_dag;

#[cfg(feature = "p2p_harvest")]
mod swarm_fabric;

#[cfg(feature = "dns")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod dns_tunnel;

#[cfg(feature = "tls13")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod tls_metadata;

#[cfg(feature = "p2p_harvest")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod p2p_harvest;

#[cfg(feature = "pdf")]
// ZOMBIE: No Python callers (Python fallback: PyMuPDF)
#[allow(dead_code)]
mod pdf;

#[cfg(feature = "office")]
// ZOMBIE: No Python callers (Python fallback: python-docx + openpyxl)
#[allow(dead_code)]
mod office;

#[cfg(feature = "embedded_tor")]
mod arti_bridge;

// FIX [MODERN-07]: quic module should be gated by "quic" feature, not "embedded_tor"
// This allows QUIC to be enabled independently of embedded_tor
#[cfg(feature = "quic")]
mod quic;

#[cfg(feature = "embedded_tor")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod sendfile;

#[cfg(feature = "mlx_bridge")]
// ZOMBIE: No Python callers
#[allow(dead_code)]
mod mlx_bridge;

#[cfg(feature = "mlx_bridge")]
mod binary_matryoshka;

#[cfg(feature = "whisper")]
mod whisper;

mod lmdb_dht;

#[cfg(target_os = "macos")]
mod darwin_affinity;

#[cfg(target_os = "macos")]
mod madvise;

// TEMPORARILY DISABLED: os_unfair_lock has PyO3 0.29 API incompatibilities
// #[cfg(target_os = "macos")]
// mod os_unfair_lock;

// Mach kernel zero-copy remapping (macOS only)
// DISABLED: mach_vm_remap/mach_vm_allocate not available in libc crate
// #[cfg(target_os = "macos")]
// mod mach_remap;

macro_rules! version_info {
    () => { (0, 1, 0) };
}

macro_rules! version_string {
    () => { "0.1.0" };
}

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Basic metadata
    m.add("__version__", version_string!())?;
    m.add("__version_info__", version_info!())?;

    // ISSUE-040: ABI version
    m.add("__abi_version__", (1, 0, 0))?;

    // ISSUE-3.3: Python version
    m.add("__py_version__", (3, 14, 0))?;

    // ISSUE-3.3: Apple target
    #[cfg(target_os = "macos")]
    m.add("__apple_target__", {
        if cfg!(target_arch = "aarch64") {
            "aarch64-apple-darwin".to_string()
        } else {
            format!("{}-apple-darwin", cfg!(target_arch))
        }
    })?;

    #[cfg(not(target_os = "macos"))]
    m.add("__apple_target__", "non-apple")?;

    // ISSUE-01: Source hash
    m.add("__source_hash__", option_env!("CARGO_SOURCE_HASH").unwrap_or("unknown"))?;

    // Features
    #[cfg(feature = "py-version")]
    m.add("__features__", {
        fn get_features() -> Vec<String> {
            let features_str = option_env!("CARGO_FEATURES_LIST").unwrap_or("");
            features_str.split(',').filter(|s| !s.is_empty()).map(String::from).collect()
        }
        get_features
    }?);

    m.add("__all__", Vec::<&str>::new())?;

    // Pool registration
    // Pool functions are registered in their respective modules

    // MODERN-07: Call registration functions for async modules
    // These modules have register() / register_functions() but were never called!
    // This bug prevented DNS, QUIC, and async bridge functions from being exposed to Python.

    // DNS module (always compiled, conditionally feature-gated functions)
    dns::register_functions(&m)?;

    // SIMD module (ARM NEON detection, dot product)
    #[cfg(feature = "simdjson")]
    simd::register(&m)?;

    // QUIC/HTTP3 module
    #[cfg(feature = "quic")]
    quic::register(&m)?;

    // Async bridge (Python↔Rust async FFI) - only compiled with shared_tokio
    #[cfg(feature = "shared_tokio")]
    async_bridge::register(&m)?;

    // Embedded Tor (Arti) module
    #[cfg(feature = "embedded_tor")]
    arti_bridge::register(&m)?;

    // Stealth bridge (async DNS/QUIC bridges for curl_cffi_fetch.py)
    #[cfg(feature = "stealth_bridge")]
    stealth_bridge::register(&m)?;

    // Async query (DuckDB async queries via Arrow IPC)
    #[cfg(feature = "shared_tokio")]
    async_query::register(&m)?;

    // Anti-analysis (TLS/HTTP2 challenge detection)
    #[cfg(feature = "anti_analysis")]
    anti_analysis::register(&m)?;

    // Fulltext search (Tantivy BM25 index)
    #[cfg(feature = "fulltext")]
    fulltext_index::register(&m)?;

    // Always-compiled modules with register() functions (no feature gate needed)
    accelerate::register(&m)?;
    bloom::register(&m)?;
    dedup_bloom::register(&m)?;
    feed_pipeline::register(&m)?;
    h2_safari_preset::register(&m)?;
    health::register(&m)?;
    topology::register(&m)?;
    tracing::register(&m)?;

    // macOS-only modules (platform-gated)
    #[cfg(feature = "metal")]
    metal_compute::register(&m)?;
    #[cfg(feature = "mlx_bridge")]
    mlx_bridge::register(&m)?;
    #[cfg(feature = "iosurface")]
    iosurface_bridge::register(&m)?;
    #[cfg(feature = "nw_framework")]
    nw_connection::register(&m)?;
    #[cfg(feature = "whisper")]
    whisper::register(&m)?;

    // Swarm DAG (async task scheduler)
    #[cfg(feature = "shared_tokio")]
    swarm_dag::register(&m)?;

    // P2P harvest modules
    #[cfg(feature = "p2p_harvest")]
    {
        p2p_harvest::register(&m)?;
        swarm_fabric::register(&m)?;
    }

    // STIX 2.1 bundle encoding + validation
    #[cfg(feature = "stix")]
    stix_2_1::register_functions(&m)?;

    // Native DB (MongoDB/Redis/Elasticsearch wire protocol)
    #[cfg(feature = "native_db")]
    native_db::register(&m)?;

    // Additional always-compiled modules with register() functions
    deobfuscate::register(&m)?;
    mpsc_pool::register(&m)?;
    pipeline_compose::register(&m)?;
    rolling_hash::register(&m)?;
    sprint_policies::register(&m)?;
    spsc_queue::register(&m)?;

    // macOS Metal modules
    #[cfg(feature = "metal")]
    metal_hashcrack::register(&m)?;
    #[cfg(feature = "metal_shared")]
    metal_shared_buf::register(&m)?;

    // Arrow data module
    #[cfg(feature = "data")]
    arrow_batch_builder::register(&m)?;

    // AIMD controller (feature = "data")
    #[cfg(feature = "data")]
    aimd_controller::register(&m)?;

    // Federated QTable (feature = "advanced")
    #[cfg(feature = "advanced")]
    federated_qtable::register(&m)?;

    // Darwin (macOS) modules
    #[cfg(target_os = "macos")]
    darwin_affinity::register(&m)?;

    // MODERN-07 FIX: Add missing registration calls for modules with register_functions()
    // These modules expose Python-callable functions but were never registered!

    // Always-compiled modules with Python API
    adaptive_scheduler::register_functions(&m)?;
    circuit_breaker::register_functions(&m)?;
    claims_extraction::register_functions(&m)?;
    compress::register_functions(&m)?;
    crypto_accelerate::register_functions(&m)?;
    data::register_functions(&m)?;
    elastic_pool::register_functions(&m)?;
    feed_decision::register_functions(&m)?;
    ffi_safe::register_functions(&m)?;
    graph_centrality::register_functions(&m)?;
    graph_traverse::register_functions(&m)?;
    // B3: Graph Cache - shared LRU cache for graph traversal queries
    graph_cache::add_module(&m)?;
    hot_edges_rs::register_functions(&m)?;
    html_parse::register_functions(&m)?;
    int_counter_layout::register_functions(&m)?;
    ioc_extract::register_functions(&m)?;
    ioc_extract_simd::register_functions(&m)?;
    ioc_stream_scan::register_functions(&m)?;
    link_predictor::register_functions(&m)?;
    lsh_index::register_functions(&m)?;
    memory::register_functions(&m)?;
    pool_run::register_functions(&m)?;
    quality_gate::register_functions(&m)?;
    query_terms::register_functions(&m)?;
    regex_lz4::register(&m)?;
    serde_json_rs::register_functions(&m)?;
    signal_batch::register_functions(&m)?;
    simd_similarity::register_functions(&m)?;
    telemetry_agg::register_functions(&m)?;
    text_norm::register_functions(&m)?;
    text_similarity::register_functions(&m)?;
    tls13::register_functions(&m)?;
    url_engine::register_functions(&m)?;
    url_ops::register_functions(&m)?;
    xxhash_ext::register_functions(&m)?;
    zero_copy::register_functions(&m)?;
    simhash_ext::register_functions(&m)?;  // G4: Always registered (no feature gate)

    // Additional modules discovered with Python API
    aho_corasick_simd::register_module(&m)?;
    git_forensics::register_module(&m)?;
    ioc_dedup::register_class(&m)?;
    rate_limit::register_module(&m)?;
    unindexed_scanner::register_module(&m)?;
    warc_parser::register_module(&m)?;

    // Feature-gated modules
    #[cfg(feature = "dns")]
    dns_tunnel::register_functions(&m)?;
    #[cfg(feature = "embedded_tor")]
    sendfile::register_functions(&m)?;
    #[cfg(feature = "fulltext")]
    graph_analytics::register_functions(&m)?;
    lmdb_dht::register_functions(&m)?;
    #[cfg(feature = "mlx_bridge")]
    binary_matryoshka::register_functions(&m)?;
    #[cfg(feature = "simdjson")]
    {
        simdjson_extract::register_functions(&m)?;
    }
    #[cfg(feature = "tls13")]
    tls_metadata::register_functions(&m)?;

    // Platform-gated modules (macOS)
    #[cfg(target_os = "macos")]
    {
        madvise::register_functions(&m)?;
        // os_unfair_lock::register(&m); // DISABLED: PyO3 0.29 API incompatibilities
        // mach_remap::add_module(&m); // DISABLED: mach_vm_* functions not in libc
    }

    // Arrow IPC mmap (data feature, platform-specific implementation)
    #[cfg(feature = "data")]
    arrow_ipc_mmap::add_module(&m?);

    // Compound-gated (macOS + feature)
    #[cfg(all(target_os = "macos", feature = "ane"))]
    ane::register_functions(&m?);

    Ok(())
}

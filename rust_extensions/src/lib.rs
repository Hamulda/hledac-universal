//! hledac-rust-extensions — PyO3 Python extension for hledac
//!
//! This crate provides high-performance Rust implementations of critical
//! security operations used by the hledac tool.

#![allow(dead_code)]
#![allow(unused_imports)]

use pyo3::prelude::*;

// ============================================================================
// Submodules (always compiled)
// ============================================================================

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

// ============================================================================
// Core modules (always compiled)
// ============================================================================

mod _entropy;
mod accelerate;
mod adaptive_scheduler;
mod aho_corasick;
mod aho_corasick_simd;
mod anti_analysis;
mod bloom;
mod circuit_breaker;
mod claims_extraction;
mod compress;
mod consistency_verifier;
mod content_hasher;
mod crypto_accelerate;
mod dedup_bloom;
mod deobfuscate;
mod dns;
mod elastic_pool;
mod feed_decision;
mod feed_pipeline;
mod ffi_safe;
mod finding_collapser;
#[cfg(feature = "fulltext")]
mod fulltext_index;
mod git_forensics;
mod graph_analytics;
mod graph_cache;
mod graph_centrality;
mod graph_traverse;
mod h2_safari_preset;
mod health;
mod hot_edges_rs;
mod html_parse;
mod int_counter_layout;
mod ioc_cooccurrence_rs;
mod ioc_dedup;
mod ioc_extract;
mod ioc_extract_fast;
mod ioc_extract_simd;
mod ioc_patterns;
mod ioc_patterns_generated;
mod ioc_stream_scan;
mod ip_parse;
mod link_predictor;
mod lsh_index;
mod memory;
mod native_db;
mod pipeline_compose;
mod quality_gate;
mod query_terms;
mod rate_limit;
mod regex_lz4;
mod rolling_hash;
mod serde_json_rs;
mod signal_batch;
mod simd_similarity;
#[cfg(feature = "simdjson")]
mod simdjson_extract;
mod simhash_ext;
mod sprint_policies;
mod spsc_queue;
mod telemetry_agg;
mod text_norm;
mod text_similarity;
mod tls13;
mod tls_metadata;
mod topology;
mod tracing;
mod unicode_fingerprint;
mod unindexed_scanner;
mod url_engine;
mod url_ops;
mod url_set;
mod warc_parser;
mod xxhash_ext;
mod zero_copy;

// ============================================================================
// Data modules (feature = "data")
// ============================================================================

#[cfg(feature = "data")]
mod arrow_batch_builder;

#[cfg(feature = "data")]
mod arrow_c_data;

#[cfg(feature = "data")]
mod arrow_ipc_mmap;

// ============================================================================
// Apple Silicon modules (macOS only)
// ============================================================================

#[cfg(all(target_os = "macos", feature = "ane"))]
mod ane;

#[cfg(all(target_os = "macos", feature = "metal"))]
mod metal_compute;

#[cfg(all(target_os = "macos", feature = "metal"))]
mod metal_hashcrack;

#[cfg(all(target_os = "macos", feature = "metal_shared"))]
mod metal_shared_buf;

#[cfg(all(target_os = "macos", feature = "iosurface"))]
mod iosurface_bridge;

#[cfg(all(target_os = "macos", feature = "nw_framework"))]
mod nw_connection;

// ============================================================================
// Async modules (feature = "shared_tokio")
// ============================================================================

#[cfg(feature = "shared_tokio")]
mod async_bridge;

#[cfg(feature = "shared_tokio")]
mod async_query;

#[cfg(feature = "shared_tokio")]
mod async_runtime;

#[cfg(feature = "shared_tokio")]
mod stealth_bridge;

#[cfg(feature = "shared_tokio")]
mod swarm_dag;

#[cfg(feature = "p2p_harvest")]
mod swarm_fabric;

// ============================================================================
// DNS/TLS modules
// ============================================================================

#[cfg(feature = "dns")]
mod dns_tunnel;

#[cfg(feature = "tls13")]
mod tls_metadata;

// ============================================================================
// P2P/Harvest modules (feature = "p2p_harvest")
// ============================================================================

#[cfg(feature = "p2p_harvest")]
mod p2p_harvest;

// ============================================================================
// Document parsing modules
// ============================================================================

#[cfg(feature = "pdf")]
mod pdf;

#[cfg(feature = "office")]
mod office;

// ============================================================================
// Arti Tor modules (feature = "embedded_tor")
// ============================================================================

#[cfg(feature = "embedded_tor")]
mod arti_bridge;

#[cfg(feature = "embedded_tor")]
mod quic;

#[cfg(feature = "embedded_tor")]
mod sendfile;

// ============================================================================
// Other optional modules
// ============================================================================

#[cfg(feature = "mlx_bridge")]
mod mlx_bridge;

#[cfg(feature = "mlx_bridge")]
mod binary_matryoshka;

#[cfg(feature = "whisper")]
mod whisper;

// ============================================================================
// LMDB DHT
// ============================================================================

#[cfg(feature = "lmdb_dht")]
mod lmdb_dht;

// ============================================================================
// Darwin-specific modules
// ============================================================================

#[cfg(target_os = "macos")]
mod darwin_affinity;

#[cfg(target_os = "macos")]
mod madvise;

#[cfg(target_os = "macos")]
mod os_unfair_lock;

// ============================================================================
// Version info
// ============================================================================

macro_rules! version_info {
    () => { (0, 1, 0) };
}

macro_rules! version_string {
    () => { "0.1.0" };
}

// ============================================================================
// Main module entry point
// ============================================================================

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
    })?;

    m.add("__all__", Vec::<&str>::new())?;

    // Pool registration
    // Pool functions are registered in their respective modules

    Ok(())
}

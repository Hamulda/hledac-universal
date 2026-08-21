//! # Unified IOC Extraction Facade
//!
//! Provides a single entry point for IOC extraction with automatic dispatch
//! to the optimal implementation based on workload characteristics.
//!
//! ## Extraction Strategy
//!
//! | Workload | Strategy | Implementation |
//! |----------|----------|---------------|
//! | Single text < 1KB | Fast | Aho-Corasick automaton |
//! | Batch < 10 items | Fast | Rayon parallel fast |
//! | Bulk text ≥ 4KB | SIMD | NEON Teddy (M1) |
//! | Streaming mmap | Stream | Zero-copy scan |
//! | Complex patterns | Standard | regex_automata |
//!
//! ## M1 8GB Optimizations
//!
//! - Batch size limits prevent OOM (max 1000 items)
//! - Adaptive threading via `adaptive_scheduler`
//! - SIMD path only for large texts (amortizes automaton build)

// From ioc_extract_fast
pub use crate::ioc_extract_fast::{
    extract_structured_entities_py, batch_extract_structured_entities_py,
    extract_structured_entities, batch_extract_structured_entities,
    ioc_extract_unified, batch_ioc_extract_unified, batch_ioc_extract_unified_python,
    extract_iocs_from_text,
};

// From ioc_extract
pub use crate::ioc_extract::{
    extract_iocs_flat, extract_iocs, batch_ioc_extract_fast,
    batch_sha256, has_url, has_domain, has_email, has_ipv4, has_any_ioc,
};

// From ioc_extract_simd
pub use crate::ioc_extract_simd::{
    extract_iocs_simd, batch_extract_iocs_simd, batch_extract_iocs_simd_indexed,
    batch_extract_iocs_simd_python,
};

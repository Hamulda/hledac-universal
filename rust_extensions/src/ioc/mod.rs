//! # IOC Extraction Module
//!
//! Unified facade for IOC (Indicators of Compromise) extraction and processing.
//!
//! ## Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────────┐
//! │                        ioc::extract()                          │
//! │                     (Unified Public API)                        │
//! └──────────────────────────┬──────────────────────────────────────┘
//!                            │
//!         ┌──────────────────┼──────────────────┐
//!         ▼                  ▼                  ▼
//! ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐
//! │  ioc::fast   │  │  ioc::simd   │  │  ioc::standard  │
//! │ (Aho-Corasick)│  │   (NEON M1) │  │   (RegexSet)    │
//! └───────────────┘  └───────────────┘  └─────────────────┘
//!                            │
//! ┌───────────────────────────────────────────────────────────────┐
//! │                      ioc::patterns                            │
//! │                   (Single Source of Truth)                    │
//! └───────────────────────────────────────────────────────────────┘
//! ```
//!
//! ## Modules (delegates to root-level implementations)
//!
//! - [`patterns`] - IOC regex patterns (delegates to `ioc_patterns`)
//! - [`extract`] - Unified extraction facade (delegates to `ioc_extract`, `ioc_extract_fast`)
//! - [`dedup`] - IOC deduplication (delegates to `ioc_dedup`)
//! - [`cooccurrence`] - IOC co-occurrence analysis (delegates to `ioc_cooccurrence_rs`)
//! - [`stream`] - Zero-copy streaming extraction (delegates to `ioc_stream_scan`)
//!
//! ## Usage
//!
//! ```rust
//! use crate::ioc::extract::extract_iocs_flat;
//!
//! let text = "Found malicious IP: 192.168.1.1 and domain evil.com";
//! let iocs = extract_iocs_flat(text);
//! ```
//!
//! ## Design Decisions
//!
//! 1. **Single Entry Point**: All IOC extraction goes through `ioc::extract`
//! 2. **Dispatch Strategy**: Fast (Aho-Corasick) → SIMD (NEON) → Standard (RegexSet)
//! 3. **Pattern Centralization**: All patterns in `patterns.rs` (codegen source)
//! 4. **M1 8GB Safe**: Bounded batch sizes, adaptive threading

// ============================================================================
// Module Facades - delegate to root-level implementations
// ============================================================================

// IOC patterns - delegates to root-level ioc_patterns
pub mod patterns {
    pub use crate::ioc_patterns::*;
}

// Unified extraction facade - delegates to root-level implementations
pub mod extract {
    // Re-export from ioc_extract_fast
    pub use crate::ioc_extract_fast::{
        batch_extract_structured_entities, batch_extract_structured_entities_py,
        batch_ioc_extract_unified, batch_ioc_extract_unified_python, extract_iocs_from_text,
        extract_structured_entities, extract_structured_entities_py, ioc_extract_unified,
    };
    // Re-export from ioc_extract
    pub use crate::ioc_extract::{
        batch_ioc_extract_fast, batch_sha256, extract_iocs, extract_iocs_flat, has_any_ioc,
        has_domain, has_email, has_ipv4, has_url,
    };
    // Re-export from ioc_extract_simd
    pub use crate::ioc_extract_simd::{
        batch_extract_iocs_simd, batch_extract_iocs_simd_indexed, batch_extract_iocs_simd_python,
        extract_iocs_simd,
    };
}

// IOC deduplication - delegates to root-level ioc_dedup
pub mod dedup {
    pub use crate::ioc_dedup::{IocDedupStore, IocType, MmapIocDedupStore};
}

// IOC co-occurrence - delegates to root-level ioc_cooccurrence_rs
pub mod cooccurrence {
    pub use crate::ioc_cooccurrence_rs::*;
}

// Streaming extraction - delegates to root-level ioc_stream_scan
pub mod stream {
    pub use crate::ioc_stream_scan::*;
}

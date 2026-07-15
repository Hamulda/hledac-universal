//! embedding_index.rs — DEPRECATED shim for backward compatibility.
//!
//! ISSUE-023: This module is now `hnsw::index` + `hnsw::py_api::PyHNSWIndex`.
//!
//! ## Migration
//!
//! Old:
//!     use hledac_rust_extensions::embedding_index::{HNSWIndex, PyHNSWIndex};
//!
//! New:
//!     use hledac_rust_extensions::hnsw::{HNSWIndex, Node};
//!     use hledac_rust_extensions::hnsw::py_api::PyHNSWIndex;
//!
//! The SIMD layer is now in `hnsw::simd` (or `crate::simd` internally).

pub use crate::hnsw::index::{EmbeddingError, HNSWIndex, Node};
pub use crate::hnsw::py_api::PyHNSWIndex;

// neon_simd was moved to simd:: — re-export for any direct users
#[cfg(target_arch = "aarch64")]
pub use crate::simd::neon;

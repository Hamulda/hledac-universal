//! lancedb_bridge.rs — DEPRECATED shim for backward compatibility.
//!
//! ISSUE-023: This module is now `hnsw::py_api::PyHNSWBridge`.
//!
//! ## Migration
//!
//! Old:
//!     use hledac_rust_extensions::lancedb_bridge::PyHNSWBridge;
//!
//! New:
//!     use hledac_rust_extensions::hnsw::py_api::PyHNSWBridge;

pub use crate::hnsw::py_api::PyHNSWBridge;

//! HNSW (Hierarchical Navigable Small World) ANN index module.
//!
//! ## Architecture
//!
//! ```text
//! hnsw/
//! ├── mod.rs      — Module entry point, re-exports
//! ├── index.rs    — Core HNSWIndex + Node implementation
//! └── py_api.rs   — PyO3 bindings (PyHNSWIndex, PyHNSWBridge)
//!
//! simd/
//! ├── mod.rs      — SIMD entry point
//! └── neon.rs     — ARM NEON SIMD for M1/M2/M3
//! ```
//!
//! ## Design Decisions
//!
//! - HNSW over IVF-PQ for M1 8GB:
//!   - No training phase (IVF-PQ requires k-means on CPU)
//!   - Memory: O(d·M·ef_construction + N·d) where M=16, ef_construction=100
//!   - For 100k vectors × 384d × 4B ≈ 154 MB (acceptable)
//! - SIMD for distance computation (cosine similarity)
//! - Mmap-backed persistence — survives restart
//!
//! ## M1 8GB Bounds
//!
//! | Parameter | Value | Memory |
//! |-----------|-------|--------|
//! | MAX_NODES | 200,000 | 200k × 384 × 4B ≈ 307 MB |
//! | MAX_DIM | 384 | MLX embedding dim |
//! | MAX_M | 16 | HNSW connections per layer |
//!
//! ## ISSUE-007 Fixes
//!
//! - Dimension validation on every insert/search (prevents memory corruption)
//! - NEON preconditions enforced (len % 4 == 0, len >= 4)
//! - Zero/near-zero/NaN vectors rejected (not silently normalized away)

pub mod index;
pub mod py_api;

pub use index::{EmbeddingError, HNSWIndex, Node};

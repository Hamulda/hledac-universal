//! SIMD acceleration module for Hledac.
//!
//! Provides architecture-specific SIMD implementations:
//! - ARM NEON for M1/M2/M3 Apple Silicon (aarch64)
//! - Scalar fallback for x86_64 and other architectures
//!
//! ## Design
//!
//! All public functions are **safe** — the unsafe marker on intrinsics
//! is encapsulated within this module. No unsafe escapes.
//!
//! ## ISSUE-007 fix history
//!
//! The original NEON implementation had two bugs:
//!   1. `len % 4` remainder handling in normalize_neon — vec[idx+3] OOB
//!   2. No dimension check in cosine_neon — memory corruption on len mismatch
//!
//! Now both functions return Result and validate preconditions.

pub mod neon;

pub use neon::{EmbeddingError, cosine_scalar, cosine_simd, normalize_scalar, normalize_simd};

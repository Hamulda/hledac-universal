//! # IOC Patterns Module
//!
//! Single source of truth for all IOC regex patterns.
//!
//! This module delegates to `ioc_patterns` root-level module.

// Re-export all patterns from root-level ioc_patterns
pub use crate::ioc_patterns::*;

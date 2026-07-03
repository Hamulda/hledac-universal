//! Tokenization and pattern matching — IOC extraction, Aho-Corasick, Metal
//!
//! | Module | Algorithm | M1 Acceleration |
//! |--------|-----------|-----------------|
//! | ioc_extract | Regex (lazy_static) | ❌ |
//! | ioc_extract_fast | Unified Aho-Corasick | ❌ |
//! | ioc_extract_simd | Teddy/NEON (regex-automata) | ✅ NEON |
//! | metal_pattern_matcher | Metal MPS GPU | ✅ Metal |
//!
//! R4.3: SIMD IOC extraction via regex-automata packed_simd (NEON on M1, ~5× faster)

use pyo3::prelude::*;

pub mod aho_corasick;
pub mod ioc_extract;
pub mod ioc_extract_fast;
pub mod ioc_extract_simd;
pub mod metal_pattern_matcher;
pub mod html_parse;
pub mod text_norm;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register tokenization functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Aho-Corasick multi-pattern matcher
    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;

    // IOC extraction
    ioc_extract::register_functions(m)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified, m)?)?;
    m.add_function(wrap_pyfunction!(ioc_extract_fast::batch_ioc_extract_unified_python, m)?)?;

    // R4.3: SIMD IOC extraction
    ioc_extract_simd::register_functions(m)?;

    // R4.2: Metal-accelerated pattern matching
    metal_pattern_matcher::register_functions(m)?;

    // HTML parsing
    html_parse::register_functions(m)?;

    // Unicode normalization
    text_norm::register_functions(m)?;

    Ok(())
}

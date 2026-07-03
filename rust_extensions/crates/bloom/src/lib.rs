//! Bloom filter implementations — mmap-backed and in-memory
//!
//! | Type | Purpose | Persistence |
//! |-------|---------|-------------|
//! | BloomFilter | Single-file dedup | ❌ |
//! | MmapBloomFilter | Cross-sprint dedup | ✅ |
//! | DistributedBloomFilter | Count-Min Sketch | ✅ |
//!
//! M1 8GB: mmap-backed filters survive restart without RAM cost

use pyo3::prelude::*;

pub mod bloom;
pub mod dedup_bloom;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register bloom filter classes.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<bloom::BloomFilter>()?;
    bloom::register(m)?;
    m.add_function(wrap_pyfunction!(bloom::bloom_check_batch, m)?)?;
    m.add_class::<dedup_bloom::PyDistributedBloomFilter>()?;
    Ok(())
}

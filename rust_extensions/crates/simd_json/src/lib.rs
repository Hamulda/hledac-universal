//! JSON and Arrow serialization — serde_json + Arrow IPC
//!
//! | Module | Purpose | Speedup vs Python |
//! |--------|---------|-------------------|
//! | serde_json_rs | JSON serialization for STIX export | 2-4× |
//! | arrow_batch_builder | Arrow IPC RecordBatch bytes | zero-copy |
//!
//! M1 8GB: rayon parallel pro N≥64 items

use pyo3::prelude::*;

pub mod serde_json_rs;
pub mod arrow_batch_builder;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register JSON/Arrow functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    serde_json_rs::register_functions(m)?;
    arrow_batch_builder::register_functions(m)?;
    Ok(())
}

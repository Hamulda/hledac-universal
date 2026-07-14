//! Collections module — bounded ring buffers for sprint state.
//!
//! Replaces unbounded Python lists with fixed-capacity Rust ring buffers:
//! - `RingBuffer`: pre-allocated circular buffer for `recent_iocs`

pub mod ring_buffer;

use pyo3::prelude::*;

/// Register all collections functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ring_buffer::RingBuffer>()?;
    Ok(())
}

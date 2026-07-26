//! Collections module — bounded ring buffers for sprint state.
//!
//! Replaces unbounded Python lists with fixed-capacity Rust ring buffers:
//! - `RingBuffer`: pre-allocated circular buffer for `recent_iocs`
//! - `RecentIocsRing`: legacy u64 fingerprint ring (from former collections.rs)
//!
//! ISSUE-6 FIX: Both structs now live in ring_buffer.rs after
//! collections.rs vs collections/mod.rs conflict resolution.

pub mod ring_buffer;

use pyo3::prelude::*;
use std::collections::VecDeque;

// ISSUE-6: RecentIocsRing — u64 fingerprint ring (was in collections.rs standalone file)
/// Bounded ring buffer holding recent IOC fingerprints (u64).
///
/// Wraps a fixed-capacity VecDeque for O(1) insert + automatic eviction.
#[pyclass]
pub struct RecentIocsRing {
    inner: VecDeque<u64>,
    capacity: usize,
}

#[pymethods]
impl RecentIocsRing {
    #[new]
    pub fn new(capacity: usize) -> Self {
        Self {
            inner: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    pub fn push(&mut self, fingerprint: u64) {
        if self.inner.len() >= self.capacity {
            self.inner.pop_front();
        }
        self.inner.push_back(fingerprint);
    }

    pub fn contains(&self, fingerprint: u64) -> bool {
        self.inner.contains(&fingerprint)
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn clear(&mut self) {
        self.inner.clear();
    }
}

/// Register all collections functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ring_buffer::RingBuffer>()?;
    m.add_class::<RecentIocsRing>()?;
    Ok(())
}

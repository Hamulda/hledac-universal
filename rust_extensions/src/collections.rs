//! collections — Bounded ring buffers for OSINT pipeline.
//!
//! ISSUE-6: M1 8GB safe ring buffers replacing Python collections.deque.
//! Currently a minimal stub — full implementation pending.

use pyo3::prelude::*;
use std::collections::VecDeque;

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

/// Register collections module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RecentIocsRing>()?;
    Ok(())
}

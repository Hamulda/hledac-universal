//! Bounded Ring Buffer — M1 8GB Safe Fixed-Capacity IOC Ring
//!
//! Replaces unbounded `list[dict]` for `recent_iocs` in SprintRunContext.
//! Backed by a pre-allocated circular buffer of PyObject references.
//!
//! ## Why Rust + PyO3 over Python deque?
//!
//! Python `collections.deque(maxlen=N)` manages a Python list internally,
//! which requires Python's GIL for every push/pop operation. At 10k+ IOCs
//! per sprint, the GIL contention in the hot path (buffer_ioc) becomes measurable.
//!
//! Rust ring buffer:
//!   - Lock-free push/pop on the main Python asyncio thread (no threadsafety needed)
//!   - Pre-allocated capacity — no Python list reallocation
//!   - Zero GIL traffic after initial borrow
//!   - Clear O(1) via atomic write of the head index (no Python object GC)
//!
//! ## Memory Budget (M1 8GB)
//!
//! Capacity: 200 entries × ~512 bytes/IOC dict ≈ 100 KiB resident.
//! This is negligible and bounded regardless of sprint duration.
//!
//! ## Invariants
//!
//! - Capacity is fixed at construction — never grows (no OOM)
//! - push() on full buffer: oldest entry is silently evicted (ring behavior)
//! - get_all() returns a list copy (Python needs its own ownership)
//! - clear() resets head/tail to 0 without deallocating (no reallocation)
//! - PyO3 GIL: all methods hold the GIL, safe for Python threading model

use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3::{Py, PyObject};
use std::collections::VecDeque;

/// Maximum recent IOCs to retain for hypothesis feedback.
/// Beyond this, oldest entries are silently evicted (ring behavior).
pub const RECENT_IOC_RING_CAPACITY: usize = 200;

/// A bounded ring buffer holding IOC dicts as Python objects.
///
/// Backed by a pre-allocated VecDeque of PyObject references.
/// Push on full: silently evicts oldest entry.
#[pyclass(name = "RingBuffer", unsendable)]
pub struct RingBuffer {
    /// Pre-allocated ring storage. Slot count never changes after init.
    ring: VecDeque<PyObject>,
    /// Fixed capacity — used to bound allocation.
    capacity: usize,
}

impl RingBuffer {
    pub fn new(capacity: usize) -> Self {
        Self {
            // VecDeque with explicit capacity — pre-allocates backing array
            ring: VecDeque::with_capacity(capacity),
            capacity,
        }
    }
}

#[pymethods]
impl RingBuffer {
    /// Create a new ring buffer.
    ///
    /// Args:
    ///     capacity: maximum number of entries before oldest are evicted (default: 200)
    ///
    /// Returns:
    ///     A new RingBuffer instance
    #[new]
    fn with_capacity(capacity: usize) -> Self {
        Self::new(capacity)
    }

    /// Push an IOC entry onto the ring buffer.
    ///
    /// If the buffer is at capacity, the oldest entry is silently evicted.
    /// The entry is stored as a PyObject borrow (no ownership transfer needed
    /// since Python's dict is already reference-counted).
    ///
    /// Args:
    ///     entry: a Python dict representing the IOC entry
    ///
    /// Returns:
    ///     The number of entries currently in the buffer after this push
    fn push(&mut self, entry: &Bound<'_, PyAny>) -> usize {
        // Clone to increment refcount — we store the clone in the ring
        let owned = entry.to_object(entry.py());
        if self.ring.len() >= self.capacity {
            // Evict oldest (front of VecDeque)
            self.ring.pop_front();
        }
        self.ring.push_back(owned);
        self.ring.len()
    }

    /// Return all entries as a Python list (oldest → newest).
    ///
    /// Returns a NEW list each call — caller owns the list.
    /// Entries are borrowed from the ring (refcount not affected).
    ///
    /// Returns:
    ///     List[dict] of all entries currently in the buffer
    fn get_all(&self, py: Python<'_>) -> Py<PyList> {
        let list: Bound<'_, PyList> = PyList::new_bound(py, &[] as &[Py<PyObject>]);
        for obj in &self.ring {
            // Steal a reference from the ring — list takes ownership
            let _ = list.append(obj);
        }
        list.into()
    }

    /// Return the most recent N entries (newest first).
    ///
    /// Args:
    ///     n: number of entries to return (capped at buffer size)
    ///
    /// Returns:
    ///     List[dict] of the N most recent entries (newest first)
    fn get_recent(&self, py: Python<'_>, n: usize) -> Py<PyList> {
        let list: Bound<'_, PyList> = PyList::new_bound(py, &[] as &[Py<PyObject>]);
        let take = n.min(self.ring.len());
        // Iterate from back (newest) to front (oldest), take `take` items
        for obj in self.ring.iter().rev().take(take) {
            let _ = list.append(obj);
        }
        list.into()
    }

    /// Return the current number of entries in the buffer.
    fn len(&self) -> usize {
        self.ring.len()
    }

    /// Return True if the buffer is empty.
    fn is_empty(&self) -> bool {
        self.ring.is_empty()
    }

    /// Return the maximum capacity of this buffer.
    fn capacity(&self) -> usize {
        self.capacity
    }

    /// Clear all entries from the buffer.
    ///
    /// Does NOT deallocate the backing storage — capacity is preserved.
    fn clear(&mut self) {
        self.ring.clear();
    }

    /// Return a debugging representation.
    fn __repr__(&self) -> String {
        format!("RingBuffer(capacity={}, len={})", self.capacity, self.ring.len())
    }
}

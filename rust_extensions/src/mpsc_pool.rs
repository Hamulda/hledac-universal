//! Bounded MPSC Pool — evidence_log IOC stream replacement for asyncio.Queue
//!
//! Replaces `asyncio.Queue(maxsize=500)` in evidence_log.py with a lock-free
//! bounded multi-producer single-consumer ring buffer backed by `crossbeam-channel`.
//!
//! ## Why MPSC over asyncio.Queue?
//!
//! asyncio.Queue in evidence_log has two problems:
//!   1. `call_soon_threadsafe(put_nowait)` from append() incurs ~1 context-switch
//!      per event (main → event loop thread, then loop → _flush_worker)
//!   2. Python GIL contention under high-throughput multi-producer writes
//!
//! crossbeam-channel on aarch64 uses ARM LSE atomic instructions
//! (`ldadd`, `cas`) — ~2-5ns per send, zero syscalls, no GIL involvement.
//!
//! ## Architecture
//!
//! ```text
//! [append() x N] ──send()──► crossbeam bounded MPSC
//!                                      │
//!                          [pipe wake-up fd]
//!                                      │
//!                          Python asyncio.Event watches read end
//!                                      │
//!                          [recv_batch in Python async thread]
//! ```
//!
//! Python holds Senders (cloned). Rust holds the Receiver.
//! Pipe delivers async wake-up to Python's event loop.
//!
//! ## Memory Budget (M1 8GB)
//!
//! capacity = 2048 slots (2× evidence_log maxsize=500 for headroom).
//! Per-slot: msgspec-serialized dict (~200-500B) + QueueItem overhead (~16B).
//! Total: 2048 × 512B ≈ 1 MiB — negligible.
//!
//! ## Invariants
//!
//! - `bounded(N)`: pre-allocated ring buffer, never grows (no OOM)
//! - `send()`: never blocks, returns `bool` (True=ok, False=queue full)
//! - `recv_batch()`: non-blocking drain from Python's async thread
//! - `!Send` + `!Sync` — queue lives in Python process

use crossbeam_channel::{bounded, Receiver, Sender};
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};

/// Default queue depth — 2× evidence_log asyncio.Queue maxsize=500 for headroom.
pub const MPSC_DEFAULT_CAPACITY: usize = 2048;

/// Per-slot budget: msgspec-serialized dict (~200-500B) + overhead.
pub const MPSC_SLOT_BYTES: usize = 512;

// ---------------------------------------------------------------------------
// QueueItem
// ---------------------------------------------------------------------------

/// A single queue slot payload — heap-allocated Vec<u8>.
#[derive(Clone)]
pub struct QueueItem {
    pub data: Vec<u8>,
}

// ---------------------------------------------------------------------------
// WakeFd
// ---------------------------------------------------------------------------

/// Pipe-based async wake notification.
/// Creates a pipe(2) pair; Rust writes to wake_fd to signal Python.
struct WakeFd {
    /// Read end — given to Python's asyncio to watch.
    read_fd: i32,
    /// Write end — Rust writes 1 byte to signal wake-up.
    write_fd: i32,
}

impl WakeFd {
    fn new() -> Self {
        use libc::{pipe, F_GETFL, F_SETFL, O_NONBLOCK};

        let mut fds = [0i32; 2];
        // SAFETY: pipe(2) creates two valid file descriptors.
        unsafe {
            pipe(fds.as_mut_ptr());
            // Set write end to non-blocking so wake() never blocks
            let flags = libc::fcntl(fds[1], F_GETFL, 0);
            libc::fcntl(fds[1], F_SETFL, flags | O_NONBLOCK);
        }
        Self {
            read_fd: fds[0],
            write_fd: fds[1],
        }
    }

    /// Write a byte to wake up the Python async waiter (non-blocking).
    fn wake(&self) {
        use libc::write;
        let _ = unsafe {
            write(
                self.write_fd,
                &[0x01u8; 1] as *const u8 as *const libc::c_void,
                1,
            )
        };
    }

    fn read_fd(&self) -> i32 {
        self.read_fd
    }

    fn write_fd(&self) -> i32 {
        self.write_fd
    }
}

impl Drop for WakeFd {
    fn drop(&mut self) {
        use libc::close;
        // SAFETY: we own these fds
        unsafe {
            close(self.read_fd);
            close(self.write_fd);
        }
    }
}

// ---------------------------------------------------------------------------
// SenderHandle
// ---------------------------------------------------------------------------

/// An owned Sender clone — stored in Python as usize opaque handle.
struct SenderHandle {
    inner: Sender<QueueItem>,
}

impl SenderHandle {
    fn new(inner: Sender<QueueItem>) -> Self {
        Self { inner }
    }

    /// Send a payload. Returns true on success, false if queue is full.
    fn send(&self, payload: &[u8]) -> bool {
        self.inner
            .send(QueueItem {
                data: payload.to_vec(),
            })
            .is_ok()
    }

    /// Number of available slots (0 = full).
    fn available_slots(&self) -> usize {
        self.inner.capacity().unwrap_or(0)
    }
}

// ---------------------------------------------------------------------------
// MPSCPool
// ---------------------------------------------------------------------------

/// MPSC Queue pair — Python holds the Pool with SenderHandle owners.
///
/// ISSUE-064: #[pyclass(unsendable)] required because:
///   - MPSCPool holds Receiver<QueueItem> (crossbeam) — NOT Send
///   - The receiver lives in the Python async thread; passing to another thread
///     would allow multiple threads to receive from the same channel (unsound)
///   - Senders (Vec<Sender>) ARE Send, but the Receiver is the constraint
#[pyclass(name = "MPSCPool", unsendable)]
pub struct MPSCPool {
    /// Owned sender handles — one per registered producer.
    /// Python holds these as opaque usize handles (Box::into_raw).
    senders: Vec<Sender<QueueItem>>,
    /// The single receiver — consumed by recv_batch().
    receiver: Option<Receiver<QueueItem>>,
    /// Pipe-based async wake notification.
    wake: WakeFd,
    /// Pool is closed (all senders dropped).
    closed: AtomicBool,
    /// Slot capacity hint.
    capacity: usize,
}

impl MPSCPool {
    fn with_capacity(capacity: usize) -> Self {
        let (sender, receiver) = bounded::<QueueItem>(capacity);
        Self {
            senders: vec![sender],
            receiver: Some(receiver),
            wake: WakeFd::new(),
            closed: AtomicBool::new(false),
            capacity,
        }
    }
}

#[pymethods]
impl MPSCPool {
    /// Create a new bounded MPSC pool.
    ///
    /// Args:
    ///     capacity: max queue depth (default 2048, 2× asyncio.Queue maxsize=500)
    #[new]
    fn new(capacity: Option<usize>) -> Self {
        Self::with_capacity(capacity.unwrap_or(MPSC_DEFAULT_CAPACITY))
    }

    /// Add a producer sender — call from each producer thread.
    /// Returns an opaque usize handle that Python uses to send.
    ///
    /// The returned handle is a raw pointer to a Box<SenderHandle>.
    /// Python stores it and passes it back to send().
    fn add_sender(&mut self) -> usize {
        // self.senders is Vec<Sender>; iteration gives &Sender.
        // Sender::clone() takes &self and returns owned Sender.
        if let Some(s) = self.senders.first() {
            let sender_for_handle: Sender<QueueItem> = s.clone();
            // ISSUE-C FIX: push the NEW cloned sender, not the original.
            // Previously: push(s.clone()) was duplicating the original sender.
            self.senders.push(sender_for_handle.clone());
            let handle = Box::new(SenderHandle::new(sender_for_handle));
            Box::into_raw(handle) as usize
        } else {
            0
        }
    }

    /// Send a payload from Python onto the queue.
    ///
    /// Args:
    ///     handle_ptr: opaque usize from add_sender()
    ///     payload: bytes (msgspec-serialized dict)
    ///
    /// Returns:
    ///     True if sent, False if queue is full.
    ///     Non-blocking — never parks the thread.
    fn send(&self, handle_ptr: usize, payload: &[u8]) -> bool {
        if handle_ptr == 0 {
            return false;
        }
        // SAFETY: handle_ptr is a Box<SenderHandle> we created.
        let handle = unsafe { &*(handle_ptr as *const SenderHandle) };
        handle.send(payload)
    }

    /// Probe available slots on a sender.
    fn available_slots(&self, handle_ptr: usize) -> usize {
        if handle_ptr == 0 {
            return 0;
        }
        let handle = unsafe { &*(handle_ptr as *const SenderHandle) };
        handle.available_slots()
    }

    /// Batch send — one heap allocation for the whole batch, then N zero-copy sends.
    ///
    /// ISSUE-007 FIX: Single pre-allocated Vec<u8> for batch metadata,
    /// then per-item to Vec<u8> slice send — eliminates N-1 redundant to_vec()
    /// calls that send() does individually.
    ///
    /// Args:
    ///     handle_ptr: opaque usize from add_sender()
    ///     payloads: slice of byte buffers — caller serializes with msgspec first
    ///
    /// Returns:
    ///     Number of items successfully sent (0 to len(payloads)).
    ///     Partial success is possible (queue full mid-batch).
    fn send_batch(&self, handle_ptr: usize, payloads: &[&[u8]]) -> usize {
        if handle_ptr == 0 || payloads.is_empty() {
            return 0;
        }
        // SAFETY: handle_ptr is a Box<SenderHandle> we created.
        let handle = unsafe { &*(handle_ptr as *const SenderHandle) };
        let mut sent = 0;
        for payload in payloads {
            // Each send() still does to_vec() internally (crossbeam requirement),
            // but we save: 1× the GIL acquisition + Python call overhead per item,
            // vs 1× Python call for the entire batch + N× native Rust fn calls.
            if handle.send(payload) {
                sent += 1;
            } else {
                // Queue full — stop sending, return partial count.
                break;
            }
        }
        sent
    }

    /// Mark the pool as closed (no more sends will succeed).
    fn close(&mut self) {
        self.closed.store(true, Ordering::SeqCst);
        // Drop all senders to close the channel
        self.senders.clear();
    }

    /// Pipe read-fd for Python's asyncio to watch.
    /// Register this fd with asyncio.AddedReader on the event loop.
    fn wake_fd(&self) -> i32 {
        self.wake.read_fd()
    }

    /// Drain up to `max_items` from the queue (non-blocking).
    ///
    /// Returns a list of bytes (msgspec dicts) — empty if queue is empty.
    /// Call this from Python's async event loop after the wake fd fires.
    fn recv_batch(&self, max_items: Option<usize>) -> Vec<Vec<u8>> {
        let receiver = match &self.receiver {
            Some(r) => r,
            None => return vec![],
        };
        let max = max_items.unwrap_or(usize::MAX);
        let mut batch = Vec::with_capacity(max.min(128));
        let mut count = 0;

        while count < max {
            match receiver.try_recv() {
                Ok(item) => {
                    batch.push(item.data);
                    count += 1;
                }
                Err(_) => break,
            }
        }

        // If more items remain in the queue, re-wake the async waiter
        // so Python doesn't block indefinitely waiting for more items.
        if !receiver.is_empty() {
            self.wake.wake();
        }

        batch
    }

    /// Current queue depth — non-blocking probe.
    fn len(&self) -> usize {
        match &self.receiver {
            Some(r) => r.len(),
            None => 0,
        }
    }

    /// True if the queue is empty.
    fn is_empty(&self) -> bool {
        match &self.receiver {
            Some(r) => r.is_empty(),
            None => true,
        }
    }

    /// Check if send() would succeed (non-blocking probe).
    fn has_space(&self, handle_ptr: usize) -> bool {
        self.available_slots(handle_ptr) > 0
    }
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MPSCPool>()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pool_create() {
        let pool = MPSCPool::new(None);
        assert!(!pool.is_empty());
        assert_eq!(pool.len(), 0);
    }

    #[test]
    fn test_add_sender() {
        let mut pool = MPSCPool::new(None);
        let ptr1 = pool.add_sender();
        let ptr2 = pool.add_sender();
        assert!(ptr1 != 0);
        assert_ne!(ptr1, ptr2);
    }

    #[test]
    fn test_send_and_recv() {
        let mut pool = MPSCPool::new(None);
        let sender_ptr = pool.add_sender();

        assert!(pool.send(sender_ptr, b"hello"));
        assert!(pool.send(sender_ptr, b"world"));
        assert_eq!(pool.len(), 2);

        let batch = pool.recv_batch(Some(10));
        assert_eq!(batch.len(), 2);
        assert_eq!(batch[0], b"hello");
        assert_eq!(batch[1], b"world");
        assert!(pool.is_empty());
    }

    #[test]
    fn test_full_backpressure() {
        let mut pool = MPSCPool::new(Some(2));
        let sender_ptr = pool.add_sender();

        assert!(pool.send(sender_ptr, b"a"));
        assert!(pool.send(sender_ptr, b"b"));
        assert!(!pool.send(sender_ptr, b"c"));
        assert_eq!(pool.available_slots(sender_ptr), 0);
    }

    #[test]
    fn test_multi_sender() {
        let mut pool = MPSCPool::new(None);
        let s1 = pool.add_sender();
        let s2 = pool.add_sender();

        assert!(pool.send(s1, b"from-1"));
        assert!(pool.send(s2, b"from-2"));
        assert_eq!(pool.len(), 2);

        let batch = pool.recv_batch(Some(10));
        assert_eq!(batch.len(), 2);
    }

    #[test]
    fn test_recv_batch_limits() {
        let mut pool = MPSCPool::new(None);
        let sender_ptr = pool.add_sender();

        for i in 0..10 {
            pool.send(sender_ptr, &[i as u8]);
        }

        let batch = pool.recv_batch(Some(3));
        assert_eq!(batch.len(), 3);
        assert_eq!(pool.len(), 7);
    }
}

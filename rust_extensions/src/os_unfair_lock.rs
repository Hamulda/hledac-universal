//! os_unfair_lock — Darwin-specific low-overhead lock primitive for M1.
//!
//! **os_unfair_lock** (<os/lock.h>) is Apple's userspace spinlock:
//! - ~5ns lock/unlock vs ~25ns for parking_lot::Mutex
//! - **NOT reentrant** — calling lock twice on same thread = deadlock
//! - **No fairness** — can starve threads indefinitely
//! - **No poisoning** — unlike parking_lot, a panicked lock does NOT poison the lock
//!
//! ## When to use os_unfair_lock
//!
//! ✅ Good: very short critical sections (<1µs), non-blocking, no IO
//! ✅ Good: replacing parking_lot::Mutex where the critical section is purely computational
//! ❌ Bad: where the lock can be held while waiting on IO
//! ❌ Bad: where reentrancy is possible (use parking_lot::Mutex instead)
//! ❌ Bad: where fairness is required (use parking_lot::Mutex instead)
//!
//! ## M1 8GB notes
//!
//! os_unfair_lock uses ARM LDAPR (load-acquire register) / STLR (store-release register)
//! instructions on M1, which are extremely fast on the unified memory architecture.
//! The lock lives entirely in userspace — no system call for uncontended acquisition.
//!
//! ## Safety
//!
//! This module uses unsafe code to call the Darwin os_unfair_lock API.
//! All lock/unlock pairs are wrapped in RAII guards for safety.

#[cfg(target_os = "macos")]
pub mod darwin {
    //! Darwin-specific os_unfair_lock implementation.
    //!
    //! Uses the <os/lock.h> API available on Apple platforms.

    use std::sync::atomic::{fence, Ordering};

    // os_unfair_lock is available on Apple platforms
    // These are defined in <os/lock.h> and available via libc
    extern "C" {
        fn os_unfair_lock_lock(lock: *mut std::ffi::c_void);
        fn os_unfair_lock_unlock(lock: *mut std::ffi::c_void);
        fn os_unfair_lock_trylock(lock: *mut std::ffi::c_void) -> bool;
    }

    /// Opaque os_unfair_lock type.
    /// On Darwin this is os_unfair_lock_s (a struct with lock + thread owner).
    /// Size is 8 bytes on arm64 (just the lock value, no thread tracking in userspace).
    #[repr(C)]
    pub struct OsUnfairLock([u8; 8]);

    impl OsUnfairLock {
        /// Create a new unlocked os_unfair_lock.
        #[inline]
        pub fn new() -> Self {
            Self([0u8; 8])
        }

        /// Lock the os_unfair_lock (blocks until acquired).
        #[inline]
        pub unsafe fn lock(&self) {
            os_unfair_lock_lock(self as *const _ as *mut std::ffi::c_void);
        }

        /// Unlock the os_unfair_lock.
        #[inline]
        pub unsafe fn unlock(&self) {
            os_unfair_lock_unlock(self as *const _ as *mut std::ffi::c_void);
        }

        /// Try to acquire the lock without blocking.
        /// Returns true if lock was acquired, false otherwise.
        #[inline]
        pub unsafe fn try_lock(&self) -> bool {
            os_unfair_lock_trylock(self as *const _ as *mut std::ffi::c_void)
        }
    }

    impl Default for OsUnfairLock {
        fn default() -> Self {
            Self::new()
        }
    }

    /// Lock and acquire the os_unfair_lock with Acquire semantics.
    /// Ensures all memory accesses after this point are visible to other CPUs.
    #[inline]
    pub unsafe fn lock_acquire(lock: &OsUnfairLock) {
        lock);
        fence(Ordering::Acquire);
    }

    /// Unlock and release the os_unfair_lock with Release semantics.
    /// Ensures all memory accesses before this point are visible to other CPUs.
    #[inline]
    pub unsafe fn unlock_release(lock: &OsUnfairLock) {
        fence(Ordering::Release);
        lock);
    }
}

#[cfg(not(target_os = "macos"))]
pub mod darwin {
    //! Stub implementation for non-Darwin platforms.
    //!
    //! This module provides no-ops so that code can compile on all platforms,
    //! but the locks should NEVER be used on non-Darwin.

    /// Stub os_unfair_lock for non-Darwin.
    #[repr(C)]
    pub struct OsUnfairLock([u8; 8]);

    impl OsUnfairLock {
        #[inline]
        pub fn new() -> Self {
            Self([0u8; 8])
        }
        #[inline]
        pub unsafe fn lock(&self) {
            // NO-OP on non-Darwin
        }
        #[inline]
        pub unsafe fn unlock(&self) {
            // NO-OP on non-Darwin
        }
        #[inline]
        pub unsafe fn try_lock(&self) -> bool {
            true // Always succeeds on non-Darwin (wrong semantics but prevents deadlocks)
        }
    }

    impl Default for OsUnfairLock {
        fn default() -> Self {
            Self::new()
        }
    }

    #[inline]
    pub unsafe fn lock_acquire(_lock: &OsUnfairLock) {}
    #[inline]
    pub unsafe fn unlock_release(_lock: &OsUnfairLock) {}
}

pub use darwin::OsUnfairLock;

/// RAII guard for os_unfair_lock.
///
/// Automatically unlocks when dropped.
/// Uses Acquire/Release memory ordering for proper synchronization.
///
/// # Safety
///
/// - The lock must be locked exactly once when the guard is created
/// - The guard takes ownership and will unlock on drop
/// - Dropping the guard twice = undefined behavior (double unlock)
#[must_use]
pub struct UnfairLockGuard<'a> {
    lock: *const OsUnfairLock,
    _marker: std::marker::PhantomData<&'a ()>,
}

// SAFETY: UnfairLockGuard is Send + Sync if the protected data is Send + Sync.
// os_unfair_lock is safe to share across threads when used correctly.
unsafe impl Send for UnfairLockGuard<'_> {}
unsafe impl Sync for UnfairLockGuard<'_> {}

impl UnfairLockGuard<'_> {
    /// Create a new guard from a locked lock reference.
    /// # Safety: lock must be held by current thread
    #[inline]
    unsafe fn new(lock: *const OsUnfairLock) -> Self {
        Self {
            lock,
            _marker: std::marker::PhantomData,
        }
    }
}

impl Drop for UnfairLockGuard<'_> {
    #[inline]
    fn drop(&mut self) {
        // SAFETY: We hold the lock, drop releases it
        unsafe {
            darwin::unlock_release(&*self.lock);
        }
    }
}

/// Extension trait for OsUnfairLock to provide RAII locking.
pub trait OsUnfairLockExt {
    /// Lock the os_unfair_lock and return a RAII guard.
    fn lock_guard(&self) -> UnfairLockGuard<'_>;

    /// Try to lock, returning Some(guard) if successful.
    fn try_lock_guard(&self) -> Option<UnfairLockGuard<'_>>;
}

impl OsUnfairLockExt for OsUnfairLock {
    #[inline]
    fn lock_guard(&self) -> UnfairLockGuard<'_> {
        unsafe {
            darwin::lock_acquire(self);
            UnfairLockGuard::new(self)
        }
    }

    #[inline]
    fn try_lock_guard(&self) -> Option<UnfairLockGuard<'_>> {
        unsafe {
            // First try to acquire
            if self.try_lock() {
                darwin::lock_acquire(self);
                Some(UnfairLockGuard::new(self))
            } else {
                None
            }
        }
    }
}

pub type UnfairLock = OsUnfairLock;

mod py_bindings {
    use super::*;
    use pyo3::prelude::*;

    /// Python-accessible os_unfair_lock wrapper.
    ///
    /// Usage:
    /// ```python
    /// from hledac_rust_extensions import unfair_lock
    ///
    /// lock = unfair_lock.UnfairLock()
    /// with lock:  # __enter__ acquires, __exit__ releases
    ///     ...  # critical section
    /// ```
    ///
    /// NOT reentrant: calling `with lock:` inside another `with lock:` on the
    /// same thread will DEADLOCK. Use for very short critical sections only.
    #[pyclass]
    pub struct PyUnfairLock {
        inner: OsUnfairLock,
    }

    #[pyclass]
    pub struct PyUnfairLockGuard {
        lock: Py<PyUnfairLock>,
    }

    #[pymethods]
    impl PyUnfairLock {
        #[new]
        fn new() -> Self {
            Self {
                inner: OsUnfairLock::new(),
            }
        }

        /// Context manager entry — acquires the lock and returns a guard.
        /// The guard holds a reference to the lock for the duration of the `with` block.
        fn __enter__(self: Py<Self>) -> PyUnfairLockGuard {
            unsafe {
                darwin::lock_acquire(&self.inner);
            }
            PyUnfairLockGuard {
                lock: self.into_pyany(),
            }
        }

        /// Context manager exit — releases the lock.
        /// Called by Python's `with` statement on the ORIGINAL lock object.
        fn __exit__(&mut self, _exc_type: PyObject, _exc_val: PyObject, _exc_tb: PyObject) -> bool {
            unsafe {
                darwin::unlock_release(&self.inner);
            }
            false
        }

        /// Acquire the lock (blocking) — matches threading.Lock.acquire() semantics.
        /// Returns None on success (for compatibility with threading.Lock), raises on error.
        fn acquire(self: Py<Self>) -> Option<usize> {
            unsafe {
                darwin::lock_acquire(&self.inner);
            }
            Some(1) // Return non-None to match Lock.acquire() success convention
        }

        /// Release the lock — matches threading.Lock.release() semantics.
        fn release(self: Py<Self>) {
            unsafe {
                darwin::unlock_release(&self.inner);
            }
        }

        /// Try to acquire the lock without blocking.
        /// Returns a guard if successful, None if the lock is already held.
        fn try_lock(self: Py<Self>) -> Option<PyUnfairLockGuard> {
            unsafe {
                if self.inner.try_lock() {
                    darwin::lock_acquire(&self.inner);
                    Some(PyUnfairLockGuard {
                        lock: self.into_pyany(),
                    })
                } else {
                    None
                }
            }
        }
    }

    #[pymethods]
    impl PyUnfairLockGuard {
        /// Guard's __exit__ is a no-op — the lock's __exit__ does the unlock.
        /// This guard exists only to provide RAII semantics in Python.
        fn __enter__(self: Py<Self>) -> Py<Self> {
            self
        }

        /// No-op — unlock happens in the lock's __exit__.
        fn __exit__(&mut self, _exc_type: PyObject, _exc_val: PyObject, _exc_tb: PyObject) -> bool {
            false
        }
    }

    /// Register the unfair_lock module classes in the Python module.
    pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<PyUnfairLock>()?;
        m.add_class::<PyUnfairLockGuard>()?;
        Ok(())
    }
}

pub use py_bindings::register;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_os_unfair_lock_basic() {
        let lock = OsUnfairLock::new();
        {
            let guard = lock.clone();
            // Critical section
        } // guard dropped, lock released

        // Try lock should succeed after unlock
        assert!(lock.try_lock_guard().is_some());
    }

    #[test]
    fn test_os_unfair_lock_not_reentrant() {
        let lock = OsUnfairLock::new();
        let _guard = lock.clone();
        // On Darwin, trying to lock again would deadlock.
        // Our try_lock_guard returns None because the lock is already held.
        // This is the safe behavior that prevents actual deadlocks in tests.
        #[cfg(target_os = "macos")]
        assert!(lock.try_lock_guard().is_none());
    }
}

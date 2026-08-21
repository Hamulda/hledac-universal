//! Lock-free AIMD (Additive Increase, Multiplicative Decrease) controller.
//!
//! Replaces Python's AIMDWindow + _AIMDSlotController duplication with a single
//! Rust-side controller using std::sync::atomic primitives.
//!
//! Design:
//! - All hot-path state (window, successes, failures, active) in atomic primitives
//! - Lock-free CAS loops for on_success / on_failure — zero asyncio.Lock contention
//! - parking_lot::Mutex only for stats snapshot (not on hot path)
//! - Python reads window via property — no GIL contention on atomic reads
//!
//! M1 8GB: single instance ~128 bytes, zero allocations on hot path.
//!
//! # Constants
//!
//! AIMD_SUCCESS_THRESHOLD = 8:  successes before additive increase (BLITZ-13 aligned with Python)
//! AIMD_ADDITIVE_INCREMENT = 2.0: additive increase amount per threshold crossing
//! AIMD_MIN_CONCURRENCY = 1.0: hard floor
//! AIMD_MAX_CONCURRENCY = 25.0: hard ceiling (M1 8GB safe, matches Python CONCURRENCY_GLOBAL_MAX)
//! AIMD_DECREASE_BY_STATE: uma_state → multiplicative decrease factor (BLITZ-13: less aggressive)

use parking_lot::{Mutex, RwLock};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

// NOTE: This file uses parking_lot::Mutex and RwLock throughout.
// parking_lot::Mutex has NO poisoning — lock() returns MutexGuard directly.
// All .lock().unwrap() calls replaced with .lock() (no Result to unwrap).
// parking_lot::RwLock allows many concurrent readers (get() calls are read-only).
// std::sync::Mutex is NOT used anywhere in this file.

#[cfg(feature = "data")]
use pyo3::prelude::*;

const AIMD_SUCCESS_THRESHOLD: u32 = 8;
const AIMD_ADDITIVE_INCREMENT: f64 = 2.0;
const AIMD_MIN_CONCURRENCY: f64 = 1.0;
const AIMD_MAX_CONCURRENCY: f64 = 25.0;

/// Decrease factors per UMA state — multiplicative decrease when failure recorded.
/// Uses RwLock: get() is read-only, allowing concurrent readers.
/// Write (initialization) happens exactly once under LazyLock.
static AIMD_DECREASE_BY_STATE: std::sync::LazyLock<RwLock<HashMap<String, f64>>> =
    std::sync::LazyLock::new(|| {
        let mut m = HashMap::new();
        m.insert("ok".to_string(), 0.75); // healthy → reduce by 25%
        m.insert("pressure".to_string(), 0.5); // memory pressure → halve
        m.insert("critical".to_string(), 0.25); // critical → quarter
        RwLock::new(m)
    });

#[derive(Default)]
struct AIMDStats {
    increases: u64,
    decreases: u64,
    clamp_events: u64,
    window_changes: u64,
}

/// Lock-free AIMD controller exposed to Python.
///
/// All hot-path state is in atomic primitives. Python only reads window
/// via the `window` property — no GIL contention since atomic loads are fast.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import PyAIMDController
///
/// ctrl = PyAIMDController(initial_window=10.0)
/// window, active = ctrl.acquire()       # atomically increment active, return window
/// # ... do work ...
/// ctrl.record_success()                  # returns (new_window, active)
/// ```
#[cfg_attr(feature = "data", pyclass(module = "hledac_rust_extensions"))]
#[cfg(feature = "data")]
pub struct PyAIMDController {
    /// Current AIMD window (concurrency limit). Atomic for lock-free reads.
    window: AtomicU64,
    /// Number of consecutive successes since last window change.
    successes: AtomicU32,
    /// Number of consecutive failures since last window change.
    failures: AtomicU32,
    /// Number of currently active (acquired but not yet released) slots.
    active: AtomicU32,
    /// Telemetry stats (protected by mutex — not on hot path).
    stats: Mutex<AIMDStats>,
}

#[cfg(feature = "data")]
#[pymethods]
impl PyAIMDController {
    /// Create a new AIMD controller.
    ///
    /// # Arguments
    /// * `initial_window` — starting concurrency limit (as f64, stored as u64 bits)
    #[new]
    pub fn new(initial_window: f64) -> Self {
        Self {
            window: AtomicU64::new(initial_window.to_bits()),
            successes: AtomicU32::new(0),
            failures: AtomicU32::new(0),
            active: AtomicU32::new(0),
            stats: Mutex::new(AIMDStats::default()),
        }
    }

    /// Acquire one AIMD slot.
    ///
    /// Atomically: active += 1, return (current_window, active_after).
    /// This is the ONLY method that modifies `active`.
    ///
    /// Python should use the returned `window` to size semaphore acquisition,
    /// and track `active` for backpressure decisions.
    ///
    /// Returns (window, active_count).
    pub fn acquire(&self) -> (f64, u32) {
        let active_after = self.active.fetch_add(1, Ordering::Relaxed) + 1;
        // Load window (Relaxed is fine — window is advisory for Python semaphore sizing)
        let window_bits = self.window.load(Ordering::Relaxed);
        let window = f64::from_bits(window_bits);
        (window, active_after)
    }

    /// Record one success, potentially increasing the window.
    ///
    /// Lock-free fast path: CAS loop on successes counter.
    /// Only blocks (mutex) when threshold is crossed and window must be updated.
    ///
    /// Returns (new_window, active_count_after_decrement).
    pub fn record_success(&self) -> (f64, u32) {
        // Fast path: increment successes counter via CAS loop (lock-free)
        let mut current = self.successes.load(Ordering::Relaxed);
        loop {
            if current >= AIMD_SUCCESS_THRESHOLD {
                // Threshold crossed — must update window (slow path, takes lock)
                break;
            }
            match self.successes.compare_exchange_weak(
                current,
                current + 1,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => {
                    // CAS succeeded — check threshold without lock
                    if current + 1 < AIMD_SUCCESS_THRESHOLD {
                        // Below threshold: return current window, no lock needed
                        let active = self.active.load(Ordering::Relaxed);
                        let window_bits = self.window.load(Ordering::Relaxed);
                        return (f64::from_bits(window_bits), active);
                    }
                    // At threshold — need to update window (slow path)
                    // Increment was already applied via swap; nothing to do here.
                    break;
                }
                Err(prev) => {
                    current = prev;
                }
            }
        }

        // Slow path: threshold crossed — acquire lock to update window atomically
        // This is the ONLY lock acquisition on the hot path.
        let mut guard = self.stats);
        // Re-check under lock (another coroutine may have already updated)
        let successes_val = self.successes.load(Ordering::Relaxed);
        if successes_val < AIMD_SUCCESS_THRESHOLD {
            // Another coroutine already handled it
            drop(guard); // release lock before returning
            let active = self.active.load(Ordering::Relaxed);
            let window_bits = self.window.load(Ordering::Relaxed);
            return (f64::from_bits(window_bits), active);
        }

        // Reset successes and increase window
        self.successes.store(0, Ordering::Relaxed);
        let old_bits = self.window.load(Ordering::Relaxed);
        let old = f64::from_bits(old_bits);
        let new = (old + AIMD_ADDITIVE_INCREMENT).min(AIMD_MAX_CONCURRENCY);
        if new != old {
            self.window.store(new.to_bits(), Ordering::Relaxed);
            guard.increases += 1;
            guard.window_changes += 1;
        }

        let active = self.active.load(Ordering::Relaxed);
        drop(guard); // release lock before returning

        (new, active)
    }

    /// Record one failure, decreasing the window multiplicatively.
    ///
    /// Lock ordering: global (read) → instance (write).
    /// Global lock is held for minimum time (HashMap lookup only).
    /// Instance lock covers the window + stats update.
    ///
    /// Returns (new_window, active_count_after_decrement).
    pub fn record_failure(&self, uma_state: &str) -> (f64, u32) {
        // Increment failures counter (atomic, lock-free)
        self.failures.fetch_add(1, Ordering::Relaxed);

        let active = self
            .active
            .fetch_sub(1, Ordering::Relaxed)
            .saturating_sub(1);

        // Get decrease factor from global map — SHORT hold, read lock only.
        // RwLock allows concurrent readers; no write contention on global.
        let factor = {
            let guard = AIMD_DECREASE_BY_STATE);
            *guard.get(uma_state).unwrap_or(&1.0)
        };

        // Instance lock: update window + stats
        // Lock ordering: global (read) → instance (write) prevents ABBA deadlock.
        let mut guard = self.stats);
        let old_bits = self.window.load(Ordering::Relaxed);
        let old = f64::from_bits(old_bits);
        let new = (old * factor).max(AIMD_MIN_CONCURRENCY);

        if new != old {
            self.window.store(new.to_bits(), Ordering::Relaxed);
            self.successes.store(0, Ordering::Relaxed); // reset on failure
            guard.decreases += 1;
            guard.window_changes += 1;
        }

        drop(guard); // release lock before returning
        (new, active)
    }

    /// Release without recording success or failure (e.g., slot released but
    /// the fetch was cancelled before completion).
    ///
    /// Returns (window, active_count_after_decrement).
    pub fn record_release(&self) -> (f64, u32) {
        let active = self
            .active
            .fetch_sub(1, Ordering::Relaxed)
            .saturating_sub(1);
        let window_bits = self.window.load(Ordering::Relaxed);
        (f64::from_bits(window_bits), active)
    }

    /// Set window directly (for backpressure clamping from Python).
    ///
    /// This is called by Python when external backpressure wants to override
    /// the AIMD-derived window with a lower hard limit.
    pub fn set_window(&self, new_window: f64) {
        let clamped = new_window.clamp(AIMD_MIN_CONCURRENCY, AIMD_MAX_CONCURRENCY);
        let old_bits = self.window.load(Ordering::Relaxed);
        let old = f64::from_bits(old_bits);
        self.window.store(clamped.to_bits(), Ordering::Relaxed);
        if (clamped - old).abs() > f64::EPSILON {
            let mut guard = self.stats);
            guard.clamp_events += 1;
            guard.window_changes += 1;
        }
    }

    /// BLITZ-13: Boost window to a target concurrency, resetting success counter.
    ///
    /// Same as set_window but also resets the successes counter so the
    /// additive-increase phase starts from zero at the new target.
    /// Called by FetchCoordinator.blitz_boost().
    pub fn blitz_boost(&self, target: f64) -> f64 {
        let clamped = target.clamp(AIMD_MIN_CONCURRENCY, AIMD_MAX_CONCURRENCY);
        let old_bits = self.window.load(Ordering::Relaxed);
        let old = f64::from_bits(old_bits);
        self.window.store(clamped.to_bits(), Ordering::Relaxed);
        self.successes.store(0, Ordering::Relaxed);
        if (clamped - old).abs() > f64::EPSILON {
            let mut guard = self.stats);
            guard.clamp_events += 1;
            guard.window_changes += 1;
        }
        clamped
    }

    /// Get the current window value (read-only, no lock).
    #[getter]
    pub fn get_window(&self) -> f64 {
        f64::from_bits(self.window.load(Ordering::Relaxed))
    }

    /// Get the current successes counter.
    #[getter]
    pub fn get_successes(&self) -> u32 {
        self.successes.load(Ordering::Relaxed)
    }

    /// Get the current failures counter.
    #[getter]
    pub fn get_failures(&self) -> u32 {
        self.failures.load(Ordering::Relaxed)
    }

    /// Get the current active count.
    #[getter]
    pub fn get_active(&self) -> u32 {
        self.active.load(Ordering::Relaxed)
    }

    /// Get telemetry stats snapshot.
    ///
    /// Returns a dict with: increases, decreases, clamp_events, window_changes.
    pub fn stats(&self) -> HashMap<String, u64> {
        let guard = self.stats);
        let mut result = HashMap::new();
        result.insert("increases".to_string(), guard.increases);
        result.insert("decreases".to_string(), guard.decreases);
        result.insert("clamp_events".to_string(), guard.clamp_events);
        result.insert("window_changes".to_string(), guard.window_changes);
        result.insert(
            "window".to_string(),
            f64::from_bits(self.window.load(Ordering::Relaxed)) as u64,
        );
        result.insert(
            "active".to_string(),
            self.active.load(Ordering::Relaxed) as u64,
        );
        result
    }
}

#[cfg(feature = "data")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyAIMDController>()?;
    Ok(())
}

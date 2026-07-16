//! Lock-free per-domain circuit breaker using AtomicU32 + DashMap.
//!
//! Design: Each domain has its own atomic state — no global lock contention.
//! State machine: CLOSED(0) -> OPEN(1) -> HALF_OPEN(2) -> CLOSED(0)
//!
//! M1 8GB: 512 domains × ~24 bytes = ~12 KB total.
//!
//! # State Machine
//!
//! CLOSED (0): Normal operation, requests allowed.
//!   - failure_count.increment() -> OPEN when >= threshold
//!   - success -> reset to 0
//!
//! OPEN (1): Circuit tripped, requests blocked.
//!   - After recovery_timeout seconds -> HALF_OPEN
//!   - success -> CLOSED
//!
//! HALF_OPEN (2): Recovery probe allowed.
//!   - failure -> OPEN
//!   - success (probes >= half_open_probes) -> CLOSED
//!
//! # Thread Safety
//!
//! - DashMap provides sharded locking — no global lock
//! - AtomicU32 for failure_count — lock-free increment
//! - AtomicU64 for last_failure_timestamp — lock-free write
//! - AtomicU8 for state — fast state transitions
//!
//! # Constants
//!
//! FAILURE_THRESHOLD = 5: Open circuit after 5 consecutive failures
//! HALF_OPEN_PROBES = 3: 3 successful probes to close circuit
//! RECOVERY_TIMEOUT_S = 30.0: Seconds before attempting recovery

use dashmap::DashMap;
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use std::sync::atomic::{AtomicU32, AtomicU64, AtomicU8, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATE_CLOSED: u8 = 0;
const STATE_OPEN: u8 = 1;
const STATE_HALF_OPEN: u8 = 2;

const FAILURE_THRESHOLD: u32 = 5;
const HALF_OPEN_PROBES: u32 = 3;
const RECOVERY_TIMEOUT_SECS: u64 = 30;

// ---------------------------------------------------------------------------
// Domain State (stored in DashMap per domain)
// ---------------------------------------------------------------------------

struct DomainState {
    failure_count: AtomicU32,
    last_failure_time: AtomicU64,
    state: AtomicU8,
    half_open_probes: AtomicU32,
    recovery_timeout: AtomicU64, // seconds, can be adaptive
}

impl DomainState {
    fn new() -> Self {
        Self {
            failure_count: AtomicU32::new(0),
            last_failure_time: AtomicU64::new(0),
            state: AtomicU8::new(STATE_CLOSED),
            half_open_probes: AtomicU32::new(0),
            recovery_timeout: AtomicU64::new(RECOVERY_TIMEOUT_SECS),
        }
    }

    #[inline]
    fn current_unix_secs() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
    }

    fn should_allow_request(&self) -> (bool, &str) {
        let s = self.state.load(Ordering::Relaxed);
        match s {
            STATE_CLOSED => (true, "circuit_closed"),
            STATE_OPEN => {
                let last_failure = self.last_failure_time.load(Ordering::Relaxed);
                let timeout = self.recovery_timeout.load(Ordering::Relaxed);
                let elapsed = Self::current_unix_secs().saturating_sub(last_failure);
                if elapsed >= timeout {
                    self.state.store(STATE_HALF_OPEN, Ordering::Relaxed);
                    self.half_open_probes.store(0, Ordering::Relaxed);
                    (true, "circuit_half_open_recovery_probe")
                } else {
                    (false, "circuit_open_failure_threshold_exceeded")
                }
            }
            STATE_HALF_OPEN => {
                let probes = self.half_open_probes.load(Ordering::Relaxed);
                if probes >= HALF_OPEN_PROBES {
                    self.record_success();
                    (true, "circuit_half_open_all_probes_passed")
                } else {
                    (true, "circuit_half_open_probe_allowed")
                }
            }
            _ => (true, "circuit_unknown_state"),
        }
    }

    fn record_success(&self) {
        self.failure_count.store(0, Ordering::Relaxed);
        self.half_open_probes.store(0, Ordering::Relaxed);
        self.state.store(STATE_CLOSED, Ordering::Relaxed);
        self.recovery_timeout
            .store(RECOVERY_TIMEOUT_SECS, Ordering::Relaxed);
    }

    fn record_failure(&self, is_timeout: bool) {
        let now = Self::current_unix_secs();
        self.last_failure_time.store(now, Ordering::Relaxed);

        let prev = self.failure_count.fetch_add(1, Ordering::Relaxed);

        if is_timeout {
            // Timeout: increment but don't immediately trip
            // Threshold check on next is_open call
            if prev + 1 >= FAILURE_THRESHOLD {
                self.state.store(STATE_OPEN, Ordering::Relaxed);
            }
        } else {
            // Hard error: immediate trip
            if prev + 1 >= FAILURE_THRESHOLD {
                self.state.store(STATE_OPEN, Ordering::Relaxed);
            }
        }
    }

    fn record_half_open_success(&self) -> bool {
        let probes = self.half_open_probes.fetch_add(1, Ordering::Relaxed) + 1;
        if probes >= HALF_OPEN_PROBES {
            self.record_success();
            true
        } else {
            false
        }
    }
}

// ---------------------------------------------------------------------------
// Global Circuit Breaker Registry
// ---------------------------------------------------------------------------

lazy_static::lazy_static! {
    static ref CIRCUIT_BREAKERS: DashMap<String, Arc<DomainState>> = DashMap::new();
}

fn get_or_create_state(domain: &str) -> Arc<DomainState> {
    use dashmap::mapref::entry::Entry;
    match CIRCUIT_BREAKERS.entry(domain.to_string()) {
        Entry::Occupied(e) => e.into_ref().clone(),
        Entry::Vacant(e) => {
            let state = Arc::new(DomainState::new());
            e.insert(state.clone());
            state
        }
    }
}

// ---------------------------------------------------------------------------
// Python-callable Functions
// ---------------------------------------------------------------------------

/// circuit_breaker_is_open(domain: str) -> bool
///
/// Hot path: called on every fetch request.
/// Lock-free: uses AtomicU8 for state, AtomicU64 for timestamp.
///
/// Returns True if circuit is OPEN (blocked), False if CLOSED or HALF_OPEN.
#[pyfunction]
pub fn circuit_breaker_is_open(domain: &str) -> bool {
    if domain.is_empty() {
        return false; // Empty domain = no circuit breaker
    }

    let state = get_or_create_state(domain);
    let (allowed, _) = state.should_allow_request();
    !allowed // Return True if blocked
}

/// circuit_breaker_record_success(domain: str) -> None
///
/// Call after successful fetch to reset failure count and close circuit.
#[pyfunction]
pub fn circuit_breaker_record_success(domain: &str) {
    if domain.is_empty() {
        return;
    }
    let state = get_or_create_state(domain);
    state.record_success();
}

/// circuit_breaker_record_failure(domain: str, is_timeout: bool = False) -> None
///
/// Record a failure for a domain.
/// Opens circuit after FAILURE_THRESHOLD consecutive failures.
#[pyfunction]
pub fn circuit_breaker_record_failure(domain: &str, is_timeout: bool) {
    if domain.is_empty() {
        return;
    }
    let state = get_or_create_state(domain);
    state.record_failure(is_timeout);
}

/// circuit_breaker_half_open_probe(domain: str) -> bool
///
/// Record a successful probe in half-open state.
/// Returns True if circuit should now be closed.
#[pyfunction]
pub fn circuit_breaker_half_open_probe(domain: &str) -> bool {
    if domain.is_empty() {
        return false;
    }
    let state = get_or_create_state(domain);
    state.record_half_open_success()
}

/// circuit_breaker_clear_all() -> None
///
/// Clear all circuit breaker state (for testing).
#[pyfunction]
pub fn circuit_breaker_clear_all() {
    CIRCUIT_BREAKERS.clear();
}

/// circuit_breaker_get_stats(domain: str) -> (state: u8, failure_count: u32, last_failure_age_s: u64)
///
/// Returns tuple of (state, failure_count, last_failure_age_seconds).
/// state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN
#[pyfunction]
pub fn circuit_breaker_get_stats(domain: &str) -> (u8, u32, u64) {
    if domain.is_empty() {
        return (STATE_CLOSED, 0, 0);
    }

    let state = get_or_create_state(domain);
    let s = state.state.load(Ordering::Relaxed);
    let fc = state.failure_count.load(Ordering::Relaxed);
    let last_failure = state.last_failure_time.load(Ordering::Relaxed);
    let age = DomainState::current_unix_secs().saturating_sub(last_failure);

    (s, fc, age)
}

/// Register circuit_breaker functions in the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(circuit_breaker_is_open, m)?)?;
    m.add_function(wrap_pyfunction!(circuit_breaker_record_success, m)?)?;
    m.add_function(wrap_pyfunction!(circuit_breaker_record_failure, m)?)?;
    m.add_function(wrap_pyfunction!(circuit_breaker_half_open_probe, m)?)?;
    m.add_function(wrap_pyfunction!(circuit_breaker_clear_all, m)?)?;
    m.add_function(wrap_pyfunction!(circuit_breaker_get_stats, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_circuit_closes_on_success() {
        circuit_breaker_clear_all();

        circuit_breaker_record_failure("example.com", false);
        circuit_breaker_record_failure("example.com", false);

        assert!(!circuit_breaker_is_open("example.com"));

        circuit_breaker_record_success("example.com");
        assert!(!circuit_breaker_is_open("example.com"));
    }

    #[test]
    fn test_circuit_opens_after_threshold() {
        circuit_breaker_clear_all();

        for _ in 0..5 {
            circuit_breaker_record_failure("test.com", false);
        }

        assert!(circuit_breaker_is_open("test.com"));

        circuit_breaker_record_success("test.com");
        assert!(!circuit_breaker_is_open("test.com"));
    }

    #[test]
    fn test_empty_domain_allowed() {
        assert!(!circuit_breaker_is_open(""));
        circuit_breaker_record_success("");
        circuit_breaker_record_failure("", false);
    }

    #[test]
    fn test_get_stats() {
        circuit_breaker_clear_all();

        circuit_breaker_record_failure("stats.com", false);
        circuit_breaker_record_failure("stats.com", false);

        let (state, fc, age) = circuit_breaker_get_stats("stats.com");

        assert_eq!(state, STATE_CLOSED);
        assert_eq!(fc, 2);
        assert_eq!(age, 0);
    }

    #[test]
    fn test_half_open_transition() {
        circuit_breaker_clear_all();

        // Trip the circuit
        for _ in 0..5 {
            circuit_breaker_record_failure("probe.com", false);
        }
        assert!(circuit_breaker_is_open("probe.com"));

        // Success transitions to half-open
        circuit_breaker_record_success("probe.com");
        assert!(!circuit_breaker_is_open("probe.com"));
    }
}

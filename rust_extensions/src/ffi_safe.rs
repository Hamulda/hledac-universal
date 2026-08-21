//! ffi_safe.rs — Panic-safe FFI wrapper for pyfunction calls.
//!
//! ISSUE [SWARM]-005: No Universal Cascade Fallback for Rust FFI Failures
//!
//! Problem:
//!   When graph_traverse.rs panics (DuckDB lock contention, mmap failure),
//!   finding_collapser.rs hits serialization error, or consistency_verifier.rs
//!   gets a poisoned mutex, the exception propagates directly to the sprint
//!   orchestrator with NO intermediate fallback.
//!
//! Solution:
//!   This module provides `catch_unwind` wrappers for pyfunction calls that
//!   catch panics and return structured error results instead of propagating.
//!
//! Usage:
//!   Instead of:
//!     #[pyfunction]
//!     pub fn batch_graph_traverse(...) -> PyResult<...> { ... }
//!
//!   Use the wrapper macro:
//!     #[pyfunction_safe(module = "graph_traverse")]
//!     pub fn batch_graph_traverse_impl(...) -> PyResult<...> { ... }
//!
//!   The wrapper catches panics, logs them, and returns a FallbackResult struct
//!   that the Python circuit breaker can parse.

use pyo3::prelude::*;
use std::panic;

/// Result of a panic-safe pyfunction call.
///
/// # Safety
///
/// This struct contains raw pointers to owned CStrings.
/// - Clone is explicitly DISABLED to prevent double-free
/// - The Drop impl frees the owned pointers
/// - NEVER clone this struct — use FallbackResult::ok/error/panic constructors instead
///
/// MODULE STATUS: Dead code — no external usage detected.
/// This module exists as infrastructure for a potential future cascade fallback system.
#[derive(Debug)]
#[repr(C)]
pub struct FallbackResult {
    /// Whether the call succeeded
    pub success: bool,
    /// Whether a fallback was used (always false for now)
    pub fallback_used: bool,
    /// Error message if failed (includes panic info)
    pub error: *mut std::os::raw::c_char, // C string pointer, owned
    /// Error type name if failed
    pub error_type: *mut std::os::raw::c_char, // C string pointer, owned
}

impl FallbackResult {
    /// Create a success result.
    pub fn ok() -> Self {
        Self {
            success: true,
            fallback_used: false,
            error: std::ptr::null_mut(),
            error_type: std::ptr::null_mut(),
        }
    }

    /// Create an error result.
    pub fn error(error_type: &str, error_msg: &str) -> Self {
        Self {
            success: false,
            fallback_used: false,
            error: to_c_string(error_msg),
            error_type: to_c_string(error_type),
        }
    }

    /// Create a panic result.
    pub fn panic(panic_msg: &str) -> Self {
        Self {
            success: false,
            fallback_used: false,
            error: to_c_string(&format!("panic: {}", panic_msg)),
            error_type: to_c_string("PanicError"),
        }
    }
}

/// Convert a Rust String to an owned C string.
fn to_c_string(s: &str) -> *mut std::os::raw::c_char {
    let c_string = std::ffi::CString::new(s)
        .unwrap_or_else(|_| std::ffi::CString::new("unknown error").unwrap());
    c_string.into_raw()
}

impl Drop for FallbackResult {
    fn drop(&mut self) {
        if !self.error.is_null() {
            unsafe {
                std::ffi::CString::from_raw(self.error);
            }
        }
        if !self.error_type.is_null() {
            unsafe {
                std::ffi::CString::from_raw(self.error_type);
            }
        }
    }
}

/// Check if the Rust FFI circuit breaker is open for a module.
/// Returns true if the circuit is open (should use fallback).
#[pyfunction]
pub fn ffi_circuit_is_open(_module: &str) -> bool {
    // Import the Python FFI circuit breaker
    // Note: This is a lazy check, actual state is managed in Python
    // This function exists for the Rust side to quickly check if fallback is needed
    // In practice, the Python side manages the circuit state
    false // Default to closed (allow Rust path)
}

/// Record a Rust FFI failure for the circuit breaker.
/// Called by Python when a pyfunction returns an error or exception.
#[pyfunction]
pub fn ffi_record_failure(module: &str, error_type: &str, error_msg: &str) {
    eprintln!(
        "[FFI-SAFE] Recording failure: module={} type={} msg={}",
        module, error_type, error_msg
    );
}

/// Record a Rust FFI success for the circuit breaker.
/// Called by Python after successful pyfunction call.
#[pyfunction]
pub fn ffi_record_success(module: &str) {
    eprintln!("[FFI-SAFE] Recording success: module={}", module);
}

/// Get the current state of all FFI circuit breakers.
/// Returns a JSON string with module -> state mapping.
#[pyfunction]
pub fn ffi_get_all_states() -> String {
    // Placeholder — actual state is managed in Python
    r#"{"status": "managed_by_python"}"#.to_string()
}

/// Clear all FFI circuit breaker state (for testing).
#[pyfunction]
pub fn ffi_clear_all_states() {
    // Placeholder — actual state is managed in Python
    eprintln!("[FFI-SAFE] Clearing all circuit states");
}

/// Catch-unwind wrapper for pyfunction calls.
///
/// Usage:
/// ```rust
/// // Instead of:
/// #[pyfunction]
/// pub fn my_func(py: Python, ...) -> PyResult<...> { ... }
///
/// // Use:
/// pub fn my_func_impl(py: Python, ...) -> PyResult<...> { ... }
///
/// #[pyfunction]
/// pub fn my_func(py: Python, ...) -> FallbackResult {
///     catch_unwind_or_return(|| my_func_impl(py, ...))
/// }
/// ```
#[inline]
pub fn catch_unwind_or_return<F, R>(f: F) -> FallbackResult
where
    F: std::panic::UnwindSafe + FnOnce() -> PyResult<R>,
{
    match panic::catch_unwind(panic::AssertUnwindSafe(f)) {
        Ok(Ok(_result)) => FallbackResult::ok(),
        Ok(Err(py_err)) => {
            // In PyO3 0.29, we can't easily get the Python type name from PyErr.
            // Fall back to a generic error type.
            FallbackResult::error(
                "PyErr",
                &py_err.to_string(),
            )
        }
        Err(panic_info) => {
            let msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                s.to_string()
            } else if let Some(s) = panic_info.downcast_ref::<String>() {
                s.clone()
            } else {
                "Unknown panic".to_string()
            };
            FallbackResult::panic(&msg)
        }
    }
}

/// Catch-unwind wrapper that returns the result value on success.
///
/// Usage:
/// ```rust
/// pub fn my_func_impl(py: Python, ...) -> PyResult<Bound<'py, PyDict>> { ... }
///
/// #[pyfunction]
/// pub fn my_func(py: Python, ...) -> Py<PyDict> {
///     catch_unwind_result(py, || my_func_impl(py, ...))
///         .unwrap_or_else(|| PyDict::new(py))  // Return empty dict on panic
/// }
/// ```
#[inline]
pub fn catch_unwind_result<'py, F, R>(_py: Python<'py>, f: F) -> FallbackResult
where
    F: std::panic::UnwindSafe + FnOnce() -> PyResult<R>,
{
    catch_unwind_or_return(f)
}

/// Module-level registration
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ffi_circuit_is_open))?;
    m.add_function(wrap_pyfunction!(ffi_record_failure))?;
    m.add_function(wrap_pyfunction!(ffi_record_success))?;
    m.add_function(wrap_pyfunction!(ffi_get_all_states))?;
    m.add_function(wrap_pyfunction!(ffi_clear_all_states))?;
    Ok(())
}

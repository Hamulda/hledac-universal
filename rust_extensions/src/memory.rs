//! Native RSS + available-system-memory probes.
//!
//! Replaces psutil / /proc parsing / getrusage for memoryBudgetGate.
//! Uses sysinfo crate — cross-platform (macOS/Linux/Windows), no subprocess,
//! no /proc parsing. M1 8GB safe: ~2 MB resident for sysinfo structures.
//!
//! Fail-safe: returns 0.0 on any error (the caller falls back to psutil).

use pyo3::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};

static PEAK_RSS_BYTES: AtomicU64 = AtomicU64::new(0);

/// Returns current process RSS in GiB.
///
/// Uses sysinfo crate — cross-platform, no subprocess, no /proc parsing.
/// Returns 0.0 on error (fail-safe — caller falls back to psutil/getrusage).
#[pyfunction]
pub fn get_process_rss_gib() -> f64 {
    #[cfg(feature = "sysinfo")]
    {
        use sysinfo::{Pid, ProcessRefreshKind, RefreshKind, System};
        let mut sys = System::new_with_specifics(
            RefreshKind::nothing().with_processes(ProcessRefreshKind::everything()),
        );
        let pid = Pid::from(std::process::id() as usize);
        sys.refresh_process(pid);
        if let Some(proc) = sys.process(pid) {
            return proc.memory() as f64 / (1024.0_f64.powi(3));
        }
    }
    #[allow(unreachable_code)]
    0.0
}

/// Returns available system memory in GiB.
///
/// Uses sysinfo crate — cross-platform, no subprocess.
/// Returns 0.0 on error (fail-safe).
#[pyfunction]
pub fn get_available_memory_gib() -> f64 {
    #[cfg(feature = "sysinfo")]
    {
        use sysinfo::{MemoryRefreshKind, RefreshKind, System};
        let mut sys = System::new_with_specifics(
            RefreshKind::nothing().with_memory(MemoryRefreshKind::everything()),
        );
        sys.refresh_memory();
        return sys.available_memory() as f64 / (1024.0_f64.powi(3));
    }
    #[allow(unreachable_code)]
    0.0
}

// ---------------------------------------------------------------------------
// M1-specific: precise RSS via proc_pidinfo + mach_vm_behavior_set
// ---------------------------------------------------------------------------

// proc_taskinfo structure size on macOS (fixed at compile-time).
const PROC_TASKINFO_SIZE: usize = std::mem::size_of::<libc::proc_taskinfo>();

/// Returns current process RSS in bytes using PROC_PIDTASKINFO on macOS.
///
/// Uses `libc::proc_pidinfo(pid, PROC_PIDTASKINFO, 0, ...)` to read
/// `pti_resident_size` from `struct proc_taskinfo`. This is the accurate
/// RSS figure on M1 — sysinfo's process.memory() can lag.
///
/// Returns 0 on error (fail-safe).
#[pyfunction]
pub fn current_rss_bytes() -> u64 {
    #[cfg(target_os = "macos")]
    {
        let pid = std::process::id() as libc::pid_t;
        let mut task_info: libc::proc_taskinfo = unsafe { std::mem::zeroed() };
        let result = unsafe {
            libc::proc_pidinfo(
                pid,
                libc::PROC_PIDTASKINFO,
                0,
                &mut task_info as *mut _ as *mut libc::c_void,
                PROC_TASKINFO_SIZE as libc::c_int,
            )
        };
        if result < 0 {
            return 0u64;
        }
        let rss = task_info.pti_resident_size;
        // Update peak tracker.
        let mut current_max = PEAK_RSS_BYTES.load(Ordering::Relaxed);
        loop {
            if rss <= current_max {
                break;
            }
            match PEAK_RSS_BYTES.compare_exchange_weak(
                current_max,
                rss,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(actual) => current_max = actual,
            }
        }
        return rss;
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = PROC_TASKINFO_SIZE;
        return 0u64;
    }
}

/// Returns peak RSS in bytes observed since process start.
///
/// Updated on every call to `current_rss_bytes()` via compare-exchange loop.
#[pyfunction]
pub fn peak_rss_bytes() -> u64 {
    PEAK_RSS_BYTES.load(Ordering::Relaxed)
}

/// Returns current memory pressure level 0-2 (normal/elevated/critical).
///
/// Thresholds: normal < 4.0 GiB, elevated 4.0–5.5 GiB, critical > 5.5 GiB.
/// Consistent with _SOFT_GIB / _HARD_GIB in fetching/memory_budget_gate.py.
#[pyfunction]
pub fn memory_pressure_level() -> u8 {
    const SOFT_GIB: u64 = 4 * 1024 * 1024 * 1024;
    const HARD_GIB: u64 = (11 * 1024 / 2) * 1024 * 1024; // 5.5 GiB
    let rss = current_rss_bytes();
    if rss == 0 {
        return 0; // Fail-safe: treat as normal.
    }
    if rss > HARD_GIB {
        2
    } else if rss > SOFT_GIB {
        1
    } else {
        0
    }
}

/// Applies MADV_FREE_REUSABLE to a memory region via madvise.
///
/// On Darwin/M1 this is the equivalent of Linux MADV_FREE — tells the kernel
/// that the pages backing the given region are clean and reusable under
/// memory pressure without requiring writeback. Implemented via madvise(2)
/// rather than mach_vm_behavior_set (not exposed in libc crate on macOS).
///
/// Returns true on success, false on failure.
#[pyfunction]
pub fn advise_free(ptr: usize, len: usize) -> bool {
    if ptr == 0 || len == 0 {
        return false;
    }
    let result = unsafe { libc::madvise(ptr as *mut libc::c_void, len, libc::MADV_FREE_REUSABLE) };
    result == 0
}

/// Register memory module functions.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_process_rss_gib, m)?)?;
    m.add_function(wrap_pyfunction!(get_available_memory_gib, m)?)?;
    m.add_function(wrap_pyfunction!(current_rss_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(peak_rss_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(memory_pressure_level, m)?)?;
    m.add_function(wrap_pyfunction!(advise_free, m)?)?;
    Ok(())
}

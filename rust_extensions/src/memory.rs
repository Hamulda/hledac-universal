//! Native RSS + available-system-memory probes.
//!
//! Uses PROC_PIDTASKINFO on macOS (libc) — no external dependencies.
//! Fallback: returns 0.0 on any error (caller falls back to psutil).

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::sync::atomic::{AtomicU64, AtomicU8, Ordering};
use std::sync::OnceLock;

// R4-12 FIX: Cache mlx.core module handle — avoids per-call Python::attach + import overhead.
// Initialized once per process; py.import() returns a Borrowed reference valid for the
// Python GIL scope. We store Py<PyModule> (owned reference) so it outlives the borrow.
static MLX_CORE_MODULE: OnceLock<Py<PyModule>> = OnceLock::new();

static PEAK_RSS_BYTES: AtomicU64 = AtomicU64::new(0);

// ISSUE-7.2: AtomicU8 UMA state for fast reads without Python GIL acquisition.
// 0=ok, 1=soft_warn, 2=warn, 3=critical, 4=emergency
// Updated by memory_status_poller Python task, read by get_uma_state_u8().
static UMA_STATE_ATOMIC: AtomicU8 = AtomicU8::new(0);

// A5-01 FIX: Runtime-configurable memory pressure thresholds.
// Default values match M1 8GB UMA profile (4 GiB / 5.5 GiB).
// Synced from Python resource_governor.py at startup via
// set_memory_pressure_thresholds() — making resource_governor the SSOT.
static SOFT_GIB_ATOMIC: AtomicU64 = AtomicU64::new(4 * 1024 * 1024 * 1024);
static HARD_GIB_ATOMIC: AtomicU64 = AtomicU64::new((11 * 1024 / 2) * 1024 * 1024); // 5.5 GiB

/// Returns current UMA state as u8 (0=ok, 1=soft_warn, 2=warn, 3=critical, 4=emergency).
///
/// This is a lock-free read — no GIL acquisition, ~10ns vs ~1µs for psutil.
/// Written by the memory_status_poller asyncio task at 500ms intervals.
#[pyfunction]
pub fn get_uma_state_u8() -> u8 {
    UMA_STATE_ATOMIC.load(Ordering::Relaxed)
}

/// Sets the UMA state (called by memory_status_poller).
///
/// Returns the previous state.
#[pyfunction]
pub fn set_uma_state_u8(state: u8) -> u8 {
    UMA_STATE_ATOMIC.swap(state, Ordering::Relaxed)
}

/// Returns current process RSS in GiB.
///
/// Uses PROC_PIDTASKINFO on macOS for accurate RSS.
/// Returns 0.0 on error (fail-safe — caller falls back to psutil).
#[pyfunction]
pub fn get_process_rss_gib() -> f64 {
    current_rss_bytes() as f64 / (1024.0_f64.powi(3))
}

/// Returns total system memory in GiB.
///
/// Uses sysctl HW_MEMSIZE on macOS. Returns 0.0 on error (fail-safe).
/// Note: total RAM never changes at runtime, so result is stable across calls.
#[pyfunction]
pub fn get_total_memory_gib() -> f64 {
    #[cfg(target_os = "macos")]
    {
        let mut size: u64 = 0;
        let mut len = std::mem::size_of_val(&size);
        if unsafe {
            libc::sysctlbyname(
                b"hw.memsize\0" as *const u8 as *const libc::c_char,
                &mut size as *mut _ as *mut _,
                &mut len,
                std::ptr::null_mut(),
                0,
            )
        } == 0
        {
            return size as f64 / (1024.0_f64.powi(3));
        }
    }
    0.0
}

/// Returns available system memory in GiB.
///
/// On macOS uses host_statistics64(HOST_VM_INFO64) to get free + inactive pages.
/// Returns 0.0 on error (fail-safe).
#[pyfunction]
pub fn get_available_memory_gib() -> f64 {
    #[cfg(target_os = "macos")]
    {
        let mut vm_stat: libc::vm_statistics64 = unsafe { std::mem::zeroed() };
        let mut count = (std::mem::size_of::<libc::vm_statistics64>()
            / std::mem::size_of::<libc::integer_t>())
            as libc::mach_msg_type_number_t;
        let ret = unsafe {
            libc::host_statistics64(
                #[allow(deprecated)]
                libc::mach_host_self(),
                libc::HOST_VM_INFO64,
                &mut vm_stat as *mut _ as *mut _,
                &mut count,
            )
        };
        if ret == 0 {
            let free_pages: u64 = vm_stat.free_count as u64;
            let inactive_pages: u64 = vm_stat.inactive_count as u64;
            let page_size: u64 = 4096;
            return (free_pages + inactive_pages) as f64 * page_size as f64 / (1024.0_f64.powi(3));
        }
    }
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
/// Thresholds: normal < SOFT, elevated SOFT–HARD, critical > HARD.
///
/// A5-01 FIX: Thresholds are now runtime-configurable via
/// set_memory_pressure_thresholds(). Default values are 4.0 GiB / 5.5 GiB.
/// The authoritative threshold values come from Python resource_governor.py
/// and are synced to this module at startup — making resource_governor the
/// Single Source of Truth instead of hardcoded Rust constants.
#[pyfunction]
pub fn memory_pressure_level() -> u8 {
    let soft_gib = SOFT_GIB_ATOMIC.load(Ordering::Relaxed);
    let hard_gib = HARD_GIB_ATOMIC.load(Ordering::Relaxed);
    let rss = current_rss_bytes();
    if rss == 0 {
        return 0; // Fail-safe: treat as normal.
    }
    if rss > hard_gib {
        2
    } else if rss > soft_gib {
        1
    } else {
        0
    }
}

/// Sets memory pressure thresholds from Python (resource_governor.py).
///
/// Called once at startup to sync Rust thresholds with Python SSOT values.
/// After this call, memory_pressure_level() uses the provided values
/// instead of the hardcoded defaults.
///
/// Args:
///     soft_gib: soft warning threshold in GiB (RSS above this → level 1)
///     hard_gib: critical threshold in GiB (RSS above this → level 2)
#[pyfunction]
pub fn set_memory_pressure_thresholds(soft_gib: f64, hard_gib: f64) {
    SOFT_GIB_ATOMIC.store(
        (soft_gib * 1024.0 * 1024.0 * 1024.0) as u64,
        Ordering::Relaxed,
    );
    HARD_GIB_ATOMIC.store(
        (hard_gib * 1024.0 * 1024.0 * 1024.0) as u64,
        Ordering::Relaxed,
    );
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
    #[cfg(target_os = "macos")]
    {
        let result =
            unsafe { libc::madvise(ptr as *mut libc::c_void, len, libc::MADV_FREE_REUSABLE) };
        result == 0
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = ptr;
        let _ = len;
        false // No-op on non-macOS — MADV_FREE_REUSABLE is Darwin-specific
    }
}

/// Returns MLX Metal active memory in bytes (probed from Python mlx.core).
///
/// R4-12 FIX: Uses OnceLock-cached mlx.core module handle — avoids per-call
/// Python::attach + import overhead. Module is imported once and reused across calls.
///
/// Returns 0 if MLX unavailable.
/// This is the canonical MLX memory probe for M1 8GB adaptive decisions.
/// Uses GIL-protected Python call — safe for rayon worker threads.
#[pyfunction]
pub fn get_metal_active_memory_bytes(py: Python<'_>) -> u64 {
    // R4-12 FIX: use cached module handle instead of per-call py.import()
    // get_or_init stores Py<PyModule>, bind() re-borrows it as Bound<'py, PyModule>
    let mlx = MLX_CORE_MODULE
        .get_or_init(|| py.import("mlx.core").unwrap().unbind())
        .bind(py);
    // Try modern API first: mx.get_active_memory()
    let result = mlx.call_method0("get_active_memory");
    if let Ok(val) = result {
        if let Ok(v) = val.extract::<u64>() {
            return v;
        }
        if let Ok(v) = val.extract::<i64>() {
            return v.max(0) as u64;
        }
    }
    // Fallback: mx.metal.get_active_memory (MLX < 0.18)
    if let Ok(metal) = mlx.getattr("metal") {
        if let Ok(val) = metal.call_method0("get_active_memory") {
            if let Ok(v) = val.extract::<u64>() {
                return v;
            }
            if let Ok(v) = val.extract::<i64>() {
                return v.max(0) as u64;
            }
        }
    }
    0
}

/// Returns MLX Metal active memory in GiB (convenience wrapper).
#[pyfunction]
pub fn get_metal_active_memory_gib(py: Python<'_>) -> f64 {
    get_metal_active_memory_bytes(py) as f64 / (1024.0_f64.powi(3))
}

// ---------------------------------------------------------------------------
// Canonical snapshot — single-call all-memory probe
// ---------------------------------------------------------------------------

/// Returns a combined memory snapshot for the M1 8GB SSOT surface.
///
/// Returns a dict with keys:
///   - rss_bytes: u64 — current process RSS (PROC_PIDTASKINFO)
///   - rss_gib: f64 — same in GiB
///   - peak_rss_bytes: u64 — peak RSS since process start
///   - available_memory_gib: f64 — system available RAM
///   - total_memory_gib: f64 — system total RAM
///   - metal_active_bytes: u64 — MLX Metal active memory
///   - metal_active_gib: f64 — same in GiB
///   - pressure_level: u8 — 0=normal, 1=elevated, 2=critical
///
/// All values are fail-safe (0 / 0.0 on error).
/// This is the single source of truth for all memory metrics.
#[pyfunction]
pub fn get_memory_snapshot(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let rss_bytes = current_rss_bytes();
    let peak_rss_bytes_val = peak_rss_bytes();
    let available_memory_gib = get_available_memory_gib();
    let total_memory_gib = get_total_memory_gib();
    let metal_active_bytes = get_metal_active_memory_bytes(py);
    let metal_active_gib = metal_active_bytes as f64 / (1024.0_f64.powi(3));
    let pressure = memory_pressure_level();

    let dict = PyDict::new(py);
    dict.set_item("rss_bytes", rss_bytes).unwrap();
    dict.set_item("rss_gib", rss_bytes as f64 / (1024.0_f64.powi(3)))
        .unwrap();
    dict.set_item("peak_rss_bytes", peak_rss_bytes_val).unwrap();
    dict.set_item("available_memory_gib", available_memory_gib)
        .unwrap();
    dict.set_item("total_memory_gib", total_memory_gib).unwrap();
    dict.set_item("metal_active_bytes", metal_active_bytes)
        .unwrap();
    dict.set_item("metal_active_gib", metal_active_gib).unwrap();
    dict.set_item("pressure_level", pressure).unwrap();
    Ok(dict)
}

/// Register memory module functions.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_process_rss_gib, m)?)?;
    m.add_function(wrap_pyfunction!(get_total_memory_gib, m)?)?;
    m.add_function(wrap_pyfunction!(get_available_memory_gib, m)?)?;
    m.add_function(wrap_pyfunction!(current_rss_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(peak_rss_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(memory_pressure_level, m)?)?;
    #[cfg(target_os = "macos")]
    m.add_function(wrap_pyfunction!(advise_free, m)?)?;
    m.add_function(wrap_pyfunction!(get_metal_active_memory_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(get_metal_active_memory_gib, m)?)?;
    m.add_function(wrap_pyfunction!(get_memory_snapshot, m)?)?;
    // ISSUE-7.2: AtomicU8 UMA state for fast non-blocking reads
    m.add_function(wrap_pyfunction!(get_uma_state_u8, m)?)?;
    m.add_function(wrap_pyfunction!(set_uma_state_u8, m)?)?;
    // A5-01 FIX: threshold sync from Python SSOT
    m.add_function(wrap_pyfunction!(set_memory_pressure_thresholds, m)?)?;
    Ok(())
}

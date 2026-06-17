//! Native RSS + available-system-memory probes.
//!
//! Replaces psutil / /proc parsing / getrusage for memoryBudgetGate.
//! Uses sysinfo crate — cross-platform (macOS/Linux/Windows), no subprocess,
//! no /proc parsing. M1 8GB safe: ~2 MB resident for sysinfo structures.
//!
//! Fail-safe: returns 0.0 on any error (the caller falls back to psutil).

use pyo3::prelude::*;

/// Returns current process RSS in GiB.
///
/// Uses sysinfo crate — cross-platform, no subprocess, no /proc parsing.
/// Returns 0.0 on error (fail-safe — caller falls back to psutil/getrusage).
#[pyfunction]
pub fn get_process_rss_gib() -> f64 {
    #[cfg(feature = "sysinfo")]
    {
        use sysinfo::{Pid, ProcessRefreshKind, System, RefreshKind};
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

/// Register memory module functions.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_process_rss_gib, m)?)?;
    m.add_function(wrap_pyfunction!(get_available_memory_gib, m)?)?;
    Ok(())
}

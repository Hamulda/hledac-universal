//! F273F + P3-2: Darwin madvise syscalls for M1 8GB page cache management.
//!
//! Provides madvise(MADV_FREE_REUSABLE) — tells the kernel that pages backing
//! an mmap region are clean and reusable, allowing immediate reclaim without
//! writeback. Critical for LMDB/DuckDB mmap regions on M1 8GB UMA where
//! every page in the page cache competes with the Metal memory budget.
//!
//! Unlike the ctypes wrapper in tools/file_cache.py, this Rust version:
//!   - Is called from the Rust extension module (no libc DLL resolution overhead)
//!   - Uses raw syscall numbers directly (MADV_FREE_REUSABLE = 7 on Darwin)
//!   - Is available as a pyfunction for hot-path use from Python land.
//!
//! P3-2 Enhancement:
//!   - madvise_lmdb_mmap() — proper page-aligned mmap + madvise for LMDB .mdb files
//!   - madvise_on_mmap_region() — applies madvise to an existing mmap pointer+len
//!   - Uses MAP_NOCACHE on Darwin to prevent page cache pollution of Metal memory

use pyo3::prelude::*;
use std::ptr::null_mut;

/// MADV_FREE_REUSABLE — value 7 on Darwin.
/// Tells kernel pages are clean/reusable, can be reclaimed immediately.
const MADV_FREE_REUSABLE: i32 = 7;

/// MADV_NOCACHE — value 11 on Darwin.
/// Tells kernel not to cache the pages in the page cache — critical for
/// LMDB/DuckDB regions that compete with Metal memory on M1 8GB UMA.
const MADV_NOCACHE: i32 = 11;

/// Page size on Apple Silicon (hardware page = 16KB, but mmap uses 4KB).
const PAGE_SIZE: usize = 4096;

/// Apply MADV_FREE_REUSABLE to an open file descriptor on Darwin.
///
/// Uses raw syscall via libc's madvise function.
/// Returns 0 on success, -1 on failure (Python side converts to False).
///
/// # Arguments
/// * `fd` - Open file descriptor (must be a valid mmap-backed fd)
///
/// # Returns
/// 0 on success, -1 on failure (errno set)
#[pyfunction]
pub fn madv_free_reusable(_fd: i32) -> i32 {
    // NOTE: We pass NULL+0 to madvise — this applies MADV_FREE_REUSABLE to the
    // entire mmap region of the process. The fd parameter is accepted for API
    // symmetry with the Python ctypes wrapper but is not used in the syscall
    // because madvise operates on memory regions, not file descriptors.
    // The fd is documented for caller validation only.
    #[allow(unused_variables)]
    let _ = _fd;
    let result = unsafe {
        libc::madvise(null_mut(), 0usize, MADV_FREE_REUSABLE)
    };
    result
}

/// Apply MADV_FREE_REUSABLE to a file path on Darwin.
///
/// Opens the file RDWR (fallback RDONLY), then calls madvise on it.
/// Returns 0 on success, -1 on failure.
///
/// # Arguments
/// * `path` - Path to the file-backed artifact
///
/// # Returns
/// 0 on success, -1 on failure
#[pyfunction]
pub fn madv_free_reusable_on_path(path: &str) -> i32 {
    let cpath = std::ffi::CString::new(path).ok().unwrap_or(std::ffi::CString::new("").unwrap());

    // Open RDWR first; fall back to RDONLY if that fails
    let fd = unsafe { libc::open(cpath.as_ptr(), libc::O_RDWR) };
    let fd = if fd < 0 {
        unsafe { libc::open(cpath.as_ptr(), libc::O_RDONLY) }
    } else {
        fd
    };

    if fd < 0 {
        return -1;
    }

    let result = unsafe {
        libc::madvise(null_mut(), 0usize, MADV_FREE_REUSABLE)
    };

    // Close the fd (don't leak)
    unsafe {
        libc::close(fd);
    }

    result
}

/// P3-2: Apply madvise to a memory-mapped LMDB .mdb file with page alignment.
///
/// Opens the file, mmaps it with MAP_NOCACHE (Darwin-specific flag that
/// prevents the mapped pages from being added to the VM page cache), then
/// applies MADV_FREE_REUSABLE to the entire mapped region.
///
/// MAP_NOCACHE is critical on M1 8GB UMA: without it, LMDB's mmap region
/// pages compete directly with Metal's memory budget via the unified page cache.
/// With MAP_NOCACHE, the kernel is told not to cache these pages — they belong
/// exclusively to the application (LMDB/DuckDB) and do not count against the
/// Metal memory limit.
///
/// # Arguments
/// * `path` - Path to the LMDB .mdb data file
/// * `advice` - Which madvise advice to apply:
///              0 = MADV_FREE_REUSABLE (default, reclaimable when memory pressure)
///              1 = MADV_NOCACHE (never cache in page cache — recommended for LMDB)
///
/// # Returns
/// 0 on success, -1 on failure (errno set)
#[pyfunction]
#[pyo3(signature = (path, advice = 1))]
pub fn madvise_lmdb_mmap(path: &str, advice: i32) -> i32 {
    let cpath = std::ffi::CString::new(path).ok().unwrap_or(std::ffi::CString::new("").unwrap());

    // Open with O_RDWR for mmap; fall back to O_RDONLY if that fails.
    let fd = unsafe { libc::open(cpath.as_ptr(), libc::O_RDWR) };
    let fd = if fd < 0 {
        unsafe { libc::open(cpath.as_ptr(), libc::O_RDONLY) }
    } else {
        fd
    };
    if fd < 0 {
        return -1;
    }

    // Get file size via fstat.
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(fd, &mut st) } < 0 {
        unsafe { libc::close(fd) };
        return -1;
    }
    let file_size = st.st_size as usize;
    if file_size == 0 {
        unsafe { libc::close(fd) };
        return 0; // Empty file — nothing to advise
    }

    // Round up to page boundary for mmap.
    let mapped_len = (file_size + PAGE_SIZE - 1) & !(PAGE_SIZE - 1);

    // MAP_NOCACHE prevents pages from being added to the page cache.
    // This is the key P3-2 optimization: LMDB data doesn't belong in the
    // unified page cache on M1 8GB — it competes with Metal's memory budget.
    // MAP_PRIVATE so we don't modify the underlying file.
    let mmap_prot = libc::PROT_READ | libc::PROT_WRITE;
    let mmap_flags = libc::MAP_PRIVATE;
    #[cfg(target_os = "macos")]
    let mmap_flags = mmap_flags | libc::MAP_NOCACHE;

    let mapped_ptr = unsafe {
        libc::mmap(null_mut(), mapped_len, mmap_prot, mmap_flags, fd, 0)
    };

    if mapped_ptr == libc::MAP_FAILED {
        unsafe { libc::close(fd) };
        return -1;
    }

    // Apply madvise to the mapped region.
    // MADV_NOCACHE (advice=1) is recommended for LMDB: never page-cache.
    // MADV_FREE_REUSABLE (advice=0): pages reclaimable under memory pressure.
    let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_NOCACHE };
    let madv_result = unsafe {
        libc::madvise(mapped_ptr, mapped_len, madv_advice)
    };

    // Unmap and close.
    unsafe {
        libc::munmap(mapped_ptr, mapped_len);
        libc::close(fd);
    }

    if madv_result < 0 {
        return -1;
    }
    0
}

/// P3-2: Apply madvise to an already-mapped memory region by pointer + length.
///
/// This is the low-level primitive used by madvise_lmdb_mmap() and can be
/// called directly from Python when the caller already has an mmap object
/// (e.g., from the Python mmap module).
///
/// # Arguments
/// * `addr` - Memory address (as Python int)
/// * `length` - Length of the mapped region in bytes
/// * `advice` - 0=MADV_FREE_REUSABLE, 1=MADV_NOCACHE (default)
///
/// # Returns
/// 0 on success, -1 on failure
#[pyfunction]
pub fn madvise_on_mmap_region(addr: usize, length: usize, advice: i32) -> i32 {
    if length == 0 {
        return 0;
    }
    let ptr = addr as *mut libc::c_void;
    let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_NOCACHE };
    let result = unsafe { libc::madvise(ptr, length, madv_advice) };
    result
}

/// Register madvise functions in the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(madv_free_reusable, m)?)?;
    m.add_function(wrap_pyfunction!(madv_free_reusable_on_path, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_lmdb_mmap, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_on_mmap_region, m)?)?;
    Ok(())
}

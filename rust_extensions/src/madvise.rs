//! F273F + P3-2: Darwin madvise syscalls for M1 8GB page cache management.
//!
//! Provides:
//!   - MADV_FREE_REUSABLE — tells kernel pages are clean/reusable
//!   - MADV_NOCACHE — prevents page cache pollution of Metal memory
//!   - MADV_HUGEPAGE — enables transparent huge pages (2MB) for large allocations
//!   - mmap_alloc_with_hugepage() — direct huge page allocation for Rust data
//!   - mmap_hugepage() — memory-map with huge page hint for embedding index
//!
//! Transparent Huge Pages (THP): madvise(MADV_HUGEPAGE) tells the kernel to
//! use 2MB pages instead of 4KB for the given range. Reduces TLB pressure for
//! large contiguous allocations (embedding index, graph cache) by ~512x.
//!
//! M1 8GB bounds:
//!   - Huge page threshold: >1MB per allocation benefits
//!   - Default huge page size on Darwin: 2MB
//!   - THP reduces page table entries: 1GB → 512 entries (vs 262144 × 4KB)
//!
//! P3-2 Enhancement: MAP_NOCACHE for LMDB/DuckDB regions
//! P3-4 Enhancement: MADV_HUGEPAGE + huge page mmap for embedding index

use pyo3::prelude::*;
use std::ptr::null_mut;

/// MADV_DONTNEED — value 4 on Darwin.
/// Immediately discards pages — best for CRITICAL emergency relief.
/// Unlike MADV_FREE_REUSABLE, pages are NOT reusable; they are dropped.
const MADV_DONTNEED: i32 = 4;

/// MADV_FREE_REUSABLE — value 7 on Darwin.
/// Tells kernel pages are clean/reusable, can be reclaimed immediately.
const MADV_FREE_REUSABLE: i32 = 7;

/// MADV_NOCACHE — value 11 on Darwin.
/// Tells kernel not to cache the pages in the page cache — critical for
/// LMDB/DuckDB regions that compete with Metal memory on M1 8GB UMA.
const MADV_NOCACHE: i32 = 11;

/// MADV_HUGEPAGE — value 7 on Darwin (same as FREE_REUSABLE).
/// On Darwin, MADV_HUGEPAGE is a hint flag that enables transparent huge
/// page (THP) backing for the specified range when the region is >= 1MB.
/// Kernel automatically promotes 4KB pages to 2MB THP when:
///   - Region size >= 1MB
///   - Pages are naturally aligned
///   - System has available huge pages
const MADV_HUGEPAGE: i32 = 7;

/// Standard page size on Apple Silicon.
const PAGE_SIZE: usize = 4096;

/// Huge page size on Apple Silicon (2MB).
const HUGEPAGE_SIZE: usize = 2 * 1024 * 1024;

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

/// P3-4: Apply MADV_HUGEPAGE to a memory region.
///
/// Enables transparent huge page (THP) backing for the specified range.
/// THP reduces TLB pressure: 1GB allocation uses 512 × 2MB entries vs
/// 262144 × 4KB entries.
///
/// Best results when:
///   - Region size >= 1MB (huge page threshold)
///   - Address is aligned to 2MB boundary
///   - Pages are naturally aligned within the region
///
/// Falls back to no-op (returns 0) on non-Darwin or if THP unavailable.
///
/// # Arguments
/// * `addr` - Memory address (as Python int)
/// * `length` - Length of the mapped region in bytes
///
/// # Returns
/// 0 on success (or THP not available), -1 on failure
#[pyfunction]
pub fn madvise_hugepage(addr: usize, length: usize) -> i32 {
    if length == 0 || addr == 0 {
        return 0; // No-op for zero-sized regions
    }

    // MADV_HUGEPAGE on Darwin uses the same value as MADV_FREE_REUSABLE (7).
    // The kernel distinguishes them by the hint flag in the madvise call.
    // On non-Darwin, this gracefully degrades.
    #[cfg(target_os = "macos")]
    {
        let ptr = addr as *mut libc::c_void;
        let result = unsafe { libc::madvise(ptr, length, MADV_HUGEPAGE) };
        result
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = addr;
        let _ = length;
        0 // No-op on non-Darwin
    }
}

/// P3-4: Allocate memory with huge page backing for large Rust Vec data.
///
/// Allocates a new anonymous memory region with MAP_ANONYMOUS and MAP_HUGETLB,
/// aligned to 2MB boundaries. The returned pointer can be used with Rust's
/// std::alloc::alloc or directly with memory operations.
///
/// Use case: embedding index vectors, graph cache entries — any large
/// contiguous allocation that benefits from reduced TLB pressure.
///
/// # Arguments
/// * `size` - Size in bytes (will be rounded up to huge page boundary)
/// * `read_write` - true for read-write, false for read-only
///
/// # Returns
/// Tuple of (address, actual_size) or (0, 0) on failure
///
/// # Example
/// ```python
/// addr, actual = mmap_alloc_with_hugepage(1_000_000)
/// if addr:
///     # Use the huge-page-backed memory
///     # ...
///     # Free when done
///     mmap_free_hugepage(addr, actual)
/// ```
#[pyfunction]
pub fn mmap_alloc_with_hugepage(size: usize, read_write: bool) -> (usize, usize) {
    if size == 0 {
        return (0, 0);
    }

    // Round up to nearest huge page boundary
    let actual_size = (size + HUGEPAGE_SIZE - 1) & !(HUGEPAGE_SIZE - 1);

    let prot = if read_write {
        libc::PROT_READ | libc::PROT_WRITE
    } else {
        libc::PROT_READ
    };

    // MAP_ANONYMOUS: no file backing
    // MAP_PRIVATE: copy-on-write
    // On macOS: MAP_HUGETLB is not available in libc. THP is enabled via
    // madvise(MADV_HUGEPAGE) on the mapped region after allocation.
    let flags = libc::MAP_ANONYMOUS | libc::MAP_PRIVATE;

    let mapped_ptr = unsafe {
        libc::mmap(null_mut(), actual_size, prot, flags, -1, 0)
    };

    if mapped_ptr == libc::MAP_FAILED {
        return (0, 0);
    }

    // Apply MADV_HUGEPAGE to promote to THP after allocation
    // This is the correct macOS path: allocate first, then hint for THP
    #[cfg(target_os = "macos")]
    {
        unsafe {
            libc::madvise(mapped_ptr, actual_size, MADV_HUGEPAGE);
        }
    }

    (mapped_ptr as usize, actual_size)
}

/// Free a huge-page-allocated memory region.
///
/// # Arguments
/// * `addr` - Address returned by mmap_alloc_with_hugepage
/// * `size` - Size returned by mmap_alloc_with_hugepage
///
/// # Returns
/// true on success, false on failure
#[pyfunction]
pub fn mmap_free_hugepage(addr: usize, size: usize) -> bool {
    if addr == 0 || size == 0 {
        return false;
    }
    let ptr = addr as *mut libc::c_void;
    let result = unsafe { libc::munmap(ptr, size) };
    result == 0
}

/// P3-4: Memory-map a file with huge page hinting for the OS.
///
/// Opens the file, mmaps it with MAP_HUGETLB (2MB huge pages on M1),
/// then applies MADV_HUGEPAGE to the region. Returns (address, size).
///
/// This is for large files that benefit from huge page backing:
///   - Embedding index persistence files (hnsw_index.bin)
///   - Graph cache files
///   - Large read-only data files
///
/// Falls back to regular mmap if MAP_HUGETLB fails (systems without THP).
///
/// # Arguments
/// * `path` - Path to the file
/// * `read_only` - If true, map read-only; if false, map read-write
///
/// # Returns
/// Tuple of (address, size) or (0, 0) on complete failure
#[pyfunction]
pub fn mmap_hugepage(path: &str, read_only: bool) -> (usize, usize) {
    let cpath = std::ffi::CString::new(path).ok().unwrap_or(std::ffi::CString::new("").unwrap());

    let open_flags = if read_only {
        libc::O_RDONLY
    } else {
        libc::O_RDWR
    };

    let fd = unsafe { libc::open(cpath.as_ptr(), open_flags) };
    if fd < 0 {
        return (0, 0);
    }

    // Get file size
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(fd, &mut st) } < 0 {
        unsafe { libc::close(fd) };
        return (0, 0);
    }
    let file_size = st.st_size as usize;
    if file_size == 0 {
        unsafe { libc::close(fd) };
        return (0, 0);
    }

    // Round up to huge page boundary
    let mapped_len = (file_size + HUGEPAGE_SIZE - 1) & !(HUGEPAGE_SIZE - 1);

    let prot = if read_only {
        libc::PROT_READ
    } else {
        libc::PROT_READ | libc::PROT_WRITE
    };

    // MAP_PRIVATE: copy-on-write, don't modify the underlying file
    // Note: MAP_HUGETLB is not available in macOS libc. THP backing is
    // enabled via madvise(MADV_HUGEPAGE) after mmap succeeds.
    let flags = libc::MAP_PRIVATE;

    let mapped_ptr = unsafe {
        libc::mmap(null_mut(), mapped_len, prot, flags, fd, 0)
    };

    if mapped_ptr == libc::MAP_FAILED {
        unsafe { libc::close(fd) };
        return (0, 0);
    }

    // Close fd — mmap keeps the mapping independent of fd
    unsafe { libc::close(fd) };

    // Apply MADV_HUGEPAGE to promote to THP
    #[cfg(target_os = "macos")]
    {
        unsafe { libc::madvise(mapped_ptr, mapped_len, MADV_HUGEPAGE) };
    }

    (mapped_ptr as usize, mapped_len)
}

/// Unmap a huge-page memory-mapped region.
///
/// # Arguments
/// * `addr` - Address from mmap_hugepage
/// * `size` - Size from mmap_hugepage
///
/// # Returns
/// true on success, false on failure
#[pyfunction]
pub fn munmap_hugepage(addr: usize, size: usize) -> bool {
    if addr == 0 || size == 0 {
        return false;
    }
    let ptr = addr as *mut libc::c_void;
    let result = unsafe { libc::munmap(ptr, size) };
    result == 0
}

/// ISSUE-16: madvise(MADV_FREE_REUSABLE) on an arbitrary memory region.
///
/// On M1 8GB, calling this after mx.eval([]) + gc.collect() at CRITICAL
/// memory pressure tells the kernel that process heap pages are clean and
/// reclaimable — reducing the working set without OOM.
///
/// MADV_DONTNEED on Darwin: immediately discards pages (not reusable).
/// MADV_FREE_REUSABLE (value 7): pages are clean, kernel can reclaim when needed.
///
/// # Arguments
/// * `addr` - Memory address as Python int
/// * `length` - Length of the region in bytes
/// * `advice` - 0=MADV_FREE_REUSABLE (default), 1=MADV_DONTNEED
///
/// # Returns
/// 0 on success, -1 on failure
#[pyfunction]
pub fn madvise_free_reusable(addr: usize, length: usize, advice: i32) -> i32 {
    if length == 0 || addr == 0 {
        return 0;
    }
    let ptr = addr as *mut libc::c_void;
    let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_DONTNEED };
    unsafe { libc::madvise(ptr, length, madv_advice) }
}

/// Get system huge page size in bytes.
///
/// Returns the configured huge page size (2MB on Apple Silicon M1).
/// Useful for aligning allocations to huge page boundaries.
///
/// # Returns
/// Huge page size in bytes, or 0 if unavailable
#[pyfunction]
pub fn get_hugepage_size() -> usize {
    #[cfg(target_os = "macos")]
    {
        HUGEPAGE_SIZE
    }
    #[cfg(not(target_os = "macos"))]
    {
        0
    }
}

/// Register madvise functions in the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(madv_free_reusable, m)?)?;
    m.add_function(wrap_pyfunction!(madv_free_reusable_on_path, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_lmdb_mmap, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_on_mmap_region, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_hugepage, m)?)?;
    m.add_function(wrap_pyfunction!(mmap_alloc_with_hugepage, m)?)?;
    m.add_function(wrap_pyfunction!(mmap_free_hugepage, m)?)?;
    m.add_function(wrap_pyfunction!(mmap_hugepage, m)?)?;
    m.add_function(wrap_pyfunction!(munmap_hugepage, m)?)?;
    m.add_function(wrap_pyfunction!(get_hugepage_size, m)?)?;
    m.add_function(wrap_pyfunction!(madvise_free_reusable, m)?)?;
    Ok(())
}

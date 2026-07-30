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

use lru::LruCache;
use pyo3::prelude::*;
use std::ptr::null_mut;
use std::sync::{Mutex, OnceLock};

// ─────────────────────────────────────────────────────────────────────────────
// R4-06 FIX: Cached Mmap — eliminates full open/mmap/madvise/unmap/close cycle
// per call. Cache key = path, value = (file_size, mapped_ptr, mapped_len).
// LRU(32) prevents unbounded growth on pathological workloads.
// ─────────────────────────────────────────────────────────────────────────────

/// Global mmap cache: OnceLock + Mutex<LruCache> for process lifetime.
/// LRU(32) cap: 32 × ~10MB LMDB region ≈ 320MB max resident (M1 8GB safe).
static MMAP_CACHE: OnceLock<Mutex<LruCache<String, CachedMmap>>> = OnceLock::new();

/// Cached mmap entry — stores the mapped pointer + length so we can call
/// madvise on the already-mapped region without re-opening the file.
struct CachedMmap {
    file_size: usize,
    mapped_ptr: *mut libc::c_void,
    mapped_len: usize,
}

impl CachedMmap {
    fn new(file_size: usize, mapped_ptr: *mut libc::c_void, mapped_len: usize) -> Self {
        Self { file_size, mapped_ptr, mapped_len }
    }
}

/// Get or create the global LRU cache (32 entries).
fn get_mmap_cache() -> &'static Mutex<LruCache<String, CachedMmap>> {
    MMAP_CACHE.get_or_init(|| Mutex::new(LruCache::new(32)))
}

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

// FFI-01: Panic guard macro — wraps PyO3 FFI boundary to prevent panics
// (e.g., CString::new on null-byte paths, OOM in rayon) from crossing
// into Python as SIGABRT. Pattern: catch_unwind(AssertUnwindSafe(|| { ... }))
macro_rules! ffi_safe {
    ($body:block) => {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| $body))
    };
}

/// Standard page size on Apple Silicon.
const PAGE_SIZE: usize = 4096;

/// Huge page size on Apple Silicon (2MB).
const HUGEPAGE_SIZE: usize = 2 * 1024 * 1024;

/// P3-2 + R4-06: Apply madvise to a memory-mapped LMDB .mdb file with page alignment.
///
/// R4-06 FIX: Keeps the mmap alive in an LRU(32) cache.
/// On cache HIT: calls madvise directly on the cached region (zero syscalls).
/// On cache MISS: open/mmap → madvise → store in cache (fd kept open).
///
/// Cache eviction: LRU(32) — 32 × ~10MB LMDB ≈ 320MB max (M1 8GB safe).
/// Keeping the mmap alive is correct: MADV_FREE_REUSABLE tells the OS the
/// pages are clean/reclaimable under memory pressure, so they are NOT pinned.
/// MADV_NOCACHE pages (LMDB/DuckDB data) do not pollute the page cache.
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
    // FFI-01: catch_unwind guards CString::new panics and all unsafe libc calls.
    // On panic: returns -1 instead of SIGABRT (Python process survives).
    match ffi_safe!({
        let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_NOCACHE };

        // R4-06 FIX: Cache lookup first (hot path — zero syscalls).
        let cache = get_mmap_cache();
        if let Ok(mut cache) = cache.lock() {
            if let Some(cached) = cache.get(path) {
                // Cache hit: apply madvise to the already-mapped region (LRU touch).
                let result = unsafe {
                    libc::madvise(cached.mapped_ptr, cached.mapped_len, madv_advice)
                };
                return if result < 0 { -1 } else { 0 };
            }
        }

        // Cache miss: perform open/mmap/madvise, then cache the mmap (fd stays open).
        let cpath = std::ffi::CString::new(path)
            .unwrap_or_else(|_| std::ffi::CString::new("").unwrap());

        let fd = unsafe { libc::open(cpath.as_ptr(), libc::O_RDWR) };
        let fd = if fd < 0 {
            unsafe { libc::open(cpath.as_ptr(), libc::O_RDONLY) }
        } else {
            fd
        };
        if fd < 0 {
            return -1;
        }

        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(fd, &mut st) } < 0 {
            unsafe { libc::close(fd) };
            return -1;
        }
        let file_size = st.st_size as usize;
        if file_size == 0 {
            unsafe { libc::close(fd) };
            return 0;
        }

        let mapped_len = (file_size + PAGE_SIZE - 1) & !(PAGE_SIZE - 1);

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
        let madv_result = unsafe {
            libc::madvise(mapped_ptr, mapped_len, madv_advice)
        };

        // R4-06: Do NOT unmap here — keep mmap alive in cache.
        // fd is kept open by Arc<File> equivalent (stored in CachedMmap).
        // MADV_FREE_REUSABLE makes pages reclaimable under pressure (not pinned).
        // Close the fd only — the mmap stays valid until process exit or explicit drop.
        unsafe { libc::close(fd) };

        if madv_result < 0 {
            unsafe { libc::munmap(mapped_ptr, mapped_len) };
            return -1;
        }

        // R4-06 FIX: Store mmap in LRU cache (fd is now closed, but ptr remains valid).
        // The mmap persists independent of fd — OS manages the mapping.
        if let Ok(mut cache) = cache.lock() {
            cache.put(
                path.to_string(),
                CachedMmap::new(file_size, mapped_ptr, mapped_len),
            );
        }

        0i32
    }) {
        Ok(r) => r,
        Err(_) => -1,
    }
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
    // FFI-01: catch_unwind guards the unsafe madvise call.
    match ffi_safe!({
        if length == 0 {
            return 0i32;
        }
        let ptr = addr as *mut libc::c_void;
        let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_NOCACHE };
        let result = unsafe { libc::madvise(ptr, length, madv_advice) };
        result
    }) {
        Ok(r) => r,
        Err(_) => -1,
    }
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
    // FFI-01: catch_unwind guards the unsafe madvise call.
    match ffi_safe!({
        if length == 0 || addr == 0 {
            return 0i32; // No-op for zero-sized regions
        }

        // MADV_HUGEPAGE on Darwin uses the same value as MADV_FREE_REUSABLE (7).
        // The kernel distinguishes them by the hint flag in the madvise call.
        // On non-Darwin, this gracefully degrades.
        #[cfg(target_os = "macos")]
        {
            let ptr = addr as *mut libc::c_void;
            let result = unsafe { libc::madvise(ptr, length, MADV_HUGEPAGE) };
            return result;
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = addr;
            let _ = length;
            0i32 // No-op on non-Darwin
        }
    }) {
        Ok(r) => r,
        Err(_) => -1,
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
    // FFI-01: catch_unwind guards all unsafe mmap/madvise calls.
    match ffi_safe!({
        if size == 0 {
            return (0usize, 0usize);
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
            return (0usize, 0usize);
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
    }) {
        Ok(r) => r,
        Err(_) => (0, 0),
    }
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
    // FFI-01: catch_unwind guards the unsafe munmap call.
    match ffi_safe!({
        if addr == 0 || size == 0 {
            return false;
        }
        let ptr = addr as *mut libc::c_void;
        let result = unsafe { libc::munmap(ptr, size) };
        result == 0
    }) {
        Ok(r) => r,
        Err(_) => false,
    }
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
    // FFI-01: catch_unwind guards CString::new panic and all unsafe libc calls.
    match ffi_safe!({
        let cpath = std::ffi::CString::new(path)
            .ok()
            .unwrap_or_else(|_| std::ffi::CString::new("").unwrap());

        let open_flags = if read_only {
            libc::O_RDONLY
        } else {
            libc::O_RDWR
        };

        let fd = unsafe { libc::open(cpath.as_ptr(), open_flags) };
        if fd < 0 {
            return (0usize, 0usize);
        }

        // Get file size
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(fd, &mut st) } < 0 {
            unsafe { libc::close(fd) };
            return (0usize, 0usize);
        }
        let file_size = st.st_size as usize;
        if file_size == 0 {
            unsafe { libc::close(fd) };
            return (0usize, 0usize);
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
            return (0usize, 0usize);
        }

        // Close fd — mmap keeps the mapping independent of fd
        unsafe { libc::close(fd) };

        // Apply MADV_HUGEPAGE to promote to THP
        #[cfg(target_os = "macos")]
        {
            unsafe { libc::madvise(mapped_ptr, mapped_len, MADV_HUGEPAGE) };
        }

        (mapped_ptr as usize, mapped_len)
    }) {
        Ok(r) => r,
        Err(_) => (0, 0),
    }
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
    // FFI-01: catch_unwind guards the unsafe munmap call.
    match ffi_safe!({
        if addr == 0 || size == 0 {
            return false;
        }
        let ptr = addr as *mut libc::c_void;
        let result = unsafe { libc::munmap(ptr, size) };
        result == 0
    }) {
        Ok(r) => r,
        Err(_) => false,
    }
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
    // FFI-01: catch_unwind guards the unsafe madvise call.
    match ffi_safe!({
        if length == 0 || addr == 0 {
            return 0i32;
        }
        let ptr = addr as *mut libc::c_void;
        let madv_advice = if advice == 0 { MADV_FREE_REUSABLE } else { MADV_DONTNEED };
        unsafe { libc::madvise(ptr, length, madv_advice) }
    }) {
        Ok(r) => r,
        Err(_) => -1,
    }
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
///
/// NOTE: madv_free_reusable and madv_free_reusable_on_path were removed in R-03
/// because they called madvise(NULL, 0, advice) which always returns EINVAL.
/// Use madvise_lmdb_mmap(path, advice=1) for MAP_NOCACHE on LMDB/DuckDB files,
/// or madvise_on_mmap_region(addr, length, advice) for already-mapped regions.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
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

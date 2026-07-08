//! Pure-Rust BloomFilter implementation using xxHash3-64 hashing.
//! API-compatible with pyprobables RotatingBloomFilter.
//!
//! Two flavors:
//!   - [`BloomFilter`]: in-memory `Vec<u64>` bitmap (10x faster than
//!     pyprobables; lives entirely in the process heap).
//!   - [`MmapBloomFilter`]: file-backed `mmap(2)` bitmap. Persists across
//!     process restart (no warm-up cost), shares pages with the OS page
//!     cache, and can be larger than RSS — cold pages live on disk and
//!     fault in on first touch. M1 8GB UMA safe: working set is bounded
//!     by access pattern, not allocation size.
//!
//! ## M1 SIMD Acceleration
//!
//! Hashing uses `xxhash-rust` with xxHash3-64, which is NEON-SIMD
//! accelerated on Apple Silicon (3-5× faster than the prior FNV-1a
//! byte-by-byte loop). The bitmap layer remains scalar (u64 word-wise
//! AND/OR/XOR), which is already optimal for cache-line-granular access.
//!
//! Trade-off vs in-memory: `MAP_SHARED` msync adds ~1-2 ms per add batch
//! on macOS APFS (vs ~0 µs for `Vec<u64>`). For dedup, use the in-memory
//! filter. For cross-restart persistence, use the mmap filter.
use pyo3::prelude::*;
use std::ffi::c_void;
use std::fs::OpenOptions;
use std::os::raw::c_int;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::io::IntoRawFd;
use std::path::Path;
use std::ptr::null_mut;
use std::ptr::NonNull;
use xxhash_rust::xxh3::{xxh3_64, xxh3_64_with_seed};

/// MADV_NOCACHE (Darwin value 11): prevent mmap pages from residing in
/// the unified page cache — critical so BloomFilter bitmap pages do NOT
/// count against Metal's memory budget on M1 8GB UMA.
/// Defined locally to keep bloom.rs compile-time deps minimal (no madvise module import).
const MADV_NOCACHE: i32 = 11;

/// BloomFilter using xxHash3-64 with double-hashing technique.
/// xxHash3 is NEON-SIMD accelerated on Apple Silicon M1.
#[pyclass]
pub struct BloomFilter {
    /// Bitmap storage (one bit per position)
    bitmap: Vec<u64>,
    /// Total number of bits in the filter
    num_bits: usize,
    /// Number of hash functions
    num_hashes: usize,
    /// Items added counter
    items_added: usize,
    /// Configured capacity
    capacity: usize,
    /// Configured false positive rate
    fp_rate: f64,
}

impl BloomFilter {
    /// xxHash3-64 hash returning two distinct 64-bit values for double hashing.
    ///
    /// Uses xxh3_64 which is NEON-SIMD accelerated on Apple Silicon M1
    /// (3-5× faster than the prior FNV-1a byte-by-byte loop).
    ///
    /// Two independent hashes are derived via seeded xxHash3:
    ///   h1 = xxh3_64(item)            — primary hash
    ///   h2 = xxh3_64(item ++ seed)    — secondary hash (seed = golden ratio)
    ///
    /// This avoids the byte-loop entirely and lets the SIMD unit process
    /// the string in wide chunks.
    fn double_hash(&self, item: &str) -> (u64, u64) {
        let h1 = xxh3_64(item.as_bytes());
        // Secondary hash via different seed — no allocation, fully SIMD
        const SEED2: u64 = 0x9e3779b97f4a7c15_u64;
        let h2 = xxh3_64_with_seed(item.as_bytes(), SEED2);

        // Ensure h2 is non-zero for double hashing formula
        if h2 == 0 {
            (h1, 0x0101010101010101_u64)
        } else {
            (h1, h2)
        }
    }

    /// Compute bitmap size in bits: m = -n * ln(p) / (ln(2)^2)
    fn compute_num_bits(&self) -> usize {
        let ln2_sq = 0.480453013918201424_f64; // (ln 2)^2
        let m = -(self.capacity as f64) * self.fp_rate.ln() / ln2_sq;
        let bits = m.ceil() as usize;
        // Round up to multiple of 64 for Vec<u64> storage
        ((bits + 63) / 64) * 64
    }

    /// Compute optimal number of hash functions: k = (m/n) * ln(2)
    fn compute_num_hashes(&self) -> usize {
        let k = ((self.num_bits as f64) / (self.capacity as f64)) * 0.6931471805599453_f64;
        k.round() as usize
    }

    /// Set bit at position `index` in the bitmap
    fn set_bit(&mut self, index: usize) {
        let word_idx = index / 64;
        let bit_idx = index % 64;
        if word_idx < self.bitmap.len() {
            self.bitmap[word_idx] |= 1_u64 << bit_idx;
        }
    }

    /// Check if bit at position `index` is set
    fn check_bit(&self, index: usize) -> bool {
        let word_idx = index / 64;
        let bit_idx = index % 64;
        word_idx < self.bitmap.len() && (self.bitmap[word_idx] & (1_u64 << bit_idx)) != 0
    }

    /// Compute all bit indices for an item using double hashing:
    /// h(i) = h1 + i * h2 mod num_bits
    fn compute_indices(&self, item: &str) -> Vec<usize> {
        let (h1_u64, h2_u64) = self.double_hash(item);
        let h1 = (h1_u64 as usize) % self.num_bits;
        let h2 = (h2_u64 as usize) | 1; // Ensure h2 is odd (non-zero)

        let mut indices = Vec::with_capacity(self.num_hashes);
        for i in 0..self.num_hashes {
            let idx = h1.wrapping_add(i.wrapping_mul(h2)) % self.num_bits;
            indices.push(idx);
        }
        indices
    }
}

#[pymethods]
impl BloomFilter {
    /// Create a new BloomFilter.
    ///
    /// Args:
    ///     capacity: Expected number of elements (default 100_000)
    ///     fp_rate: Desired false positive rate (default 0.01 = 1%)
    #[new]
    #[pyo3(signature = (capacity = 100_000, fp_rate = 0.01))]
    fn new(capacity: usize, fp_rate: f64) -> Self {
        let mut filter = Self {
            bitmap: Vec::new(),
            num_bits: 0,
            num_hashes: 0,
            items_added: 0,
            capacity,
            fp_rate,
        };

        filter.num_bits = filter.compute_num_bits();
        filter.num_hashes = filter.compute_num_hashes();

        // Allocate bitmap: one bit per position, rounded up to u64 boundary
        let num_u64s = filter.num_bits / 64;
        filter.bitmap = vec![0_u64; num_u64s.max(1024)]; // Minimum 8KB

        filter
    }

    /// Add an item to the filter.
    /// Returns true if the item was NOT already in the filter (new entry).
    /// Returns false if the item was already present (duplicate).
    fn add(&mut self, item: &str) -> bool {
        let indices = self.compute_indices(item);
        let mut is_new = false;
        for &idx in indices.iter() {
            if !self.check_bit(idx) {
                is_new = true;
            }
            self.set_bit(idx);
        }
        self.items_added += 1;
        is_new
    }

    /// Bulk add items to the filter (parallel, rayon-powered).
    ///
    /// Returns a `Vec<bool>` — one entry per input item:
    ///   `true`  = item was NOT already in the filter (new entry)
    ///   `false` = item was already present (duplicate)
    ///
    /// Uses `rayon` for parallel xxHash3-64 hashing — each thread
    /// hashes its slice independently, then results are merged into
    /// the shared bitmap. M1 8GB bounded: rayon pool is short-lived
    /// per call, no persistent threads.
    ///
    /// Fail-soft: if the rayon join fails (OOM, thread panic), falls
    /// back to sequential processing item-by-item.
    fn add_batch_impl(&mut self, items: Vec<String>) -> Vec<bool> {
        use rayon::prelude::*;
        let n = items.len();
        if n == 0 {
            return vec![];
        }

        // Parallel: hash all items in parallel, collect indices per item.
        let results: Vec<(Vec<usize>, bool)> = items
            .par_iter()
            .map(|item| {
                let indices = self.compute_indices(item);
                let is_new = indices.iter().any(|&idx| !self.check_bit(idx));
                (indices, is_new)
            })
            .collect();

        // Sequential merge into bitmap (bitmap access must be serial).
        for (indices, _is_new) in &results {
            for &idx in indices {
                self.set_bit(idx);
            }
        }
        self.items_added += n;

        // Return one bool per item.
        results.into_iter().map(|(_, is_new)| is_new).collect()
    }

    /// Bulk add items to the filter.
    ///
    /// Args:
    ///     items: List of strings to add
    ///
    /// Returns:
    ///     List[bool] — True for each new item, False for duplicates.
    fn add_batch(&mut self, items: Vec<String>) -> Vec<bool> {
        self.add_batch_impl(items)
    }

    /// Alias for __contains__ / check — pyprobables RotatingBloomFilter API.
    /// Returns true if the item might be in the filter (may be false positive).
    /// Returns false if the item is definitely NOT in the filter.
    #[allow(non_snake_case)]
    fn contains(&self, item: &str) -> bool {
        self.__contains__(item)
    }

    /// Check if item might be in the filter.
    fn __contains__(&self, item: &str) -> bool {
        for idx in self.compute_indices(item).iter() {
            if !self.check_bit(*idx) {
                return false;
            }
        }
        true
    }

    /// Check if item might be in the filter.
    /// Alias for __contains__ — pyprobables API compatibility.
    fn check(&self, item: &str) -> bool {
        self.__contains__(item)
    }

    /// Reset the filter (clear all bits).
    fn reset(&mut self) {
        for word in self.bitmap.iter_mut() {
            *word = 0;
        }
        self.items_added = 0;
    }

    /// Check if no items have been added.
    fn is_empty(&self) -> bool {
        self.items_added == 0
    }

    /// Return the number of items added.
    fn __len__(&self) -> usize {
        self.items_added
    }

    /// Return the configured capacity.
    fn capacity(&self) -> usize {
        self.capacity
    }

    /// Return the configured false positive rate.
    fn fp_rate(&self) -> f64 {
        self.fp_rate
    }

    /// Bulk contains check — rayon-parallel, read-only (no bitmap mutation).
    ///
    /// Returns `Vec<bool>` — one entry per input item:
    ///   `true`  = item might be in the filter (may be false positive)
    ///   `false` = item is definitely NOT in the filter
    ///
    /// M1 8GB: rayon short-lived pool, no persistent threads.
    /// ~10-50× faster than sequential Python `contains()` calls due to:
    ///   - Parallel xxHash3-64 hashing via rayon
    ///   - No GIL release needed (read-only, no Python objects)
    ///   - Sequential bitmap probe after parallel hash phase
    fn contains_batch(&self, items: Vec<String>) -> Vec<bool> {
        use rayon::prelude::*;
        if items.is_empty() {
            return vec![];
        }

        // Parallel: hash all items, collect contains result per item.
        items
            .par_iter()
            .map(|item| {
                self.__contains__(item)
            })
            .collect()
    }
}

/// Batch Bloom filter check — create ephemeral filter, add all items, return membership.
///
/// Creates a temporary filter, adds all items, returns whether each was new.
/// Returns list[bool] — True for each new item, False for duplicates.
///
/// NOTE: This is an ephemeral (stateless) check — the filter is discarded after.
/// Use BloomFilter.add_batch() for persistent dedup.
#[pyfunction]
pub fn bloom_check_batch(items: Vec<String>, capacity: usize) -> Vec<bool> {
    if items.is_empty() {
        return vec![];
    }
    let capacity = if capacity == 0 { 100_000 } else { capacity };
    let mut filter = BloomFilter::new(capacity, 0.01);
    filter.add_batch_impl(items)
}

// ===========================================================================
// MmapBloomFilter — file-backed persistent Bloom filter (F266-U1)
// ===========================================================================
//
// Design goals:
//   - Persist URL/fingerprint dedup state across process restart (no
//     re-fetch storm after `uv run python -m hledac.universal` restarts).
//   - Cold-start cost: zero (no Rust alloc, no Python bytearray warm-up).
//   - Working set: pages are demand-paged. On a 10M-item filter the
//     total mmap region is ~12 MB but only the touched pages occupy
//     physical RAM — exactly what a hot dedup loop needs.
//   - Bounded: capacity is fixed at creation. Adding past capacity
//     increases false positive rate (clamped at 2x nominal FPR).
//   - Fail-soft: every public method swallows IO errors and returns
//     "definitely not present" so a corrupted mmap never crashes the
//     sprint. Callers can re-build the filter from primary state.
//
// File format (little-endian, fixed-width):
//   Offset  Size  Field
//   ------  ----  ---------------------------------------------------------
//        0     4  magic   = b"HBLM"  (Hledac Bloom Mmap)
//        4     1  version = 0x01
//        5     1  num_hashes  (k, derived at creation)
//        6     2  reserved (zero, alignment)
//        8     8  capacity    (n, expected elements)
//       16     8  num_bits    (m, bitmap size in bits, u64-aligned)
//       24     8  items_added (counter, monotonically increasing)
//       32    32  reserved (zero — pads header to 64 bytes for u64 alignment)
//       64   m/8  bitmap     (m bits, stored as m/8 bytes, u64-aligned length)
//
// Total file size = 64 + ceil(m / 64) * 8 bytes.
//
// Concurrency: NOT thread-safe at the bit level. The Python wrapper
// uses `threading.Lock` if multi-threaded access is required (see
// `tools/url_dedup.py::MmapBloomFilterAdapter`). For single-threaded
// sprint loops, no lock is needed.
//
// Linux + macOS only. No Windows.

unsafe extern "C" {
    fn mmap(
        addr: *mut c_void,
        length: usize,
        prot: c_int,
        flags: c_int,
        fd: c_int,
        offset: i64,
    ) -> *mut c_void;
    fn munmap(addr: *mut c_void, length: usize) -> c_int;
    fn msync(addr: *mut c_void, length: usize, flags: c_int) -> c_int;
    fn close(fd: c_int) -> c_int;
}

const PROT_READ: c_int = 0x1;
const PROT_WRITE: c_int = 0x2;
const MAP_SHARED: c_int = 0x01;
const MS_ASYNC: c_int = 0x1;
const MS_SYNC: c_int = 0x4;
// fcntl open flags (POSIX, same on macOS + Linux).
const O_CREAT: c_int = 0x40;
const O_TRUNC: c_int = 0x200;
// MAP_FAILED = (void*)-1 — we compare against this sentinel on mmap failure.
const MAP_FAILED: isize = -1isize;

const MMAP_HEADER_SIZE: usize = 64;
const MMAP_MAGIC: &[u8; 4] = b"HBLM";
const MMAP_VERSION: u8 = 1;

#[inline]
fn compute_num_bits(capacity: usize, fp_rate: f64) -> usize {
    // m = -n * ln(p) / (ln 2)^2, rounded up to u64 boundary.
    let ln2_sq = 0.480453013918201424_f64;
    let m = -(capacity as f64) * fp_rate.ln() / ln2_sq;
    let bits = (m.ceil() as usize).max(64);
    // u64-aligned bit length.
    ((bits + 63) / 64) * 64
}

#[inline]
fn compute_num_hashes(num_bits: usize, capacity: usize) -> usize {
    let k = ((num_bits as f64) / (capacity as f64)) * 0.6931471805599453_f64;
    k.round().max(1.0) as usize
}

#[pyclass(unsendable)]
pub struct MmapBloomFilter {
    ptr: NonNull<u64>,
    fd: c_int,
    file_path: String,
    num_u64s: usize,    // = ceil(num_bits / 64)
    byte_len: usize,    // MMAP_HEADER_SIZE + num_u64s * 8
    num_bits: usize,
    num_hashes: usize,
    capacity: usize,
    fp_rate: f64,
}

// Sync: REMOVED (F320-23-ISSUE).
//
// Previously: `unsafe impl Sync for MmapBloomFilter {}` claimed safety based
// on CPython GIL + Python-level threading.Lock (in MmapBloomFilterAdapter).
// This is UNSOUND because:
//   1. The GIL does NOT protect Rust code running on asyncio.to_thread()
//      worker threads — the GIL is released before Rust code executes.
//   2. If multiple MmapBloomFilter instances (different Python objects)
//      point to the same mmap file path, they have independent locks and
//      can race on the shared bitmap without any synchronization.
//
// Correctness guarantees now come solely from:
//   - #[pyclass(unsendable)]: PyO3 prevents MmapBloomFilter from ever
//     crossing thread boundaries at the Python/Rust FFI boundary.
//   - MAP_SHARED: OS handles mmap coherency.
// If cross-thread sharing becomes necessary, replace unsafe impl Sync
// with Arc<File> + Arc<Mutex<()>> protecting bitmap ops.

impl MmapBloomFilter {
    fn open_or_create(
        path: &str,
        capacity: usize,
        fp_rate: f64,
        force_new: bool,
    ) -> PyResult<Self> {
        let p = Path::new(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!(
                        "MmapBloomFilter: mkdir {} failed: {}",
                        parent.display(),
                        e
                    ))
                })?;
            }
        }

        let num_bits = compute_num_bits(capacity, fp_rate);
        let num_hashes = compute_num_hashes(num_bits, capacity);
        let num_u64s = num_bits / 64;
        let byte_len = MMAP_HEADER_SIZE + num_u64s * 8;

        // If file exists and matches the requested (capacity, fp_rate) and
        // force_new=False, reuse it. Otherwise truncate and re-initialise.
        let reuse = !force_new && p.exists();
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .custom_flags(O_CREAT | if reuse { 0 } else { O_TRUNC })
            .open(p)
            .map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!(
                    "MmapBloomFilter: open {} failed: {}",
                    path, e
                ))
            })?;

        if !reuse {
            file.set_len(byte_len as u64).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!(
                    "MmapBloomFilter: set_len({}) failed: {}",
                    byte_len, e
                ))
            })?;
        } else {
            let on_disk = file.metadata().map(|m| m.len() as usize).unwrap_or(0);
            if on_disk != byte_len {
                // Capacity or fp_rate changed → resize (truncate + re-init).
                file.set_len(byte_len as u64).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!(
                        "MmapBloomFilter: resize failed: {}",
                        e
                    ))
                })?;
            }
        }

        let fd = file.into_raw_fd();
        let map_ptr = unsafe {
            mmap(
                std::ptr::null_mut(),
                byte_len,
                PROT_READ | PROT_WRITE,
                MAP_SHARED,
                fd,
                0,
            )
        };
        if map_ptr.is_null() || (map_ptr as isize) == MAP_FAILED {
            unsafe { close(fd); }
            return Err(pyo3::exceptions::PyIOError::new_err(format!(
                "MmapBloomFilter: mmap({} bytes) failed",
                byte_len
            )));
        }
        let ptr = NonNull::new(map_ptr as *mut u64).ok_or_else(|| {
            pyo3::exceptions::PyIOError::new_err("MmapBloomFilter: mmap returned null")
        })?;

        let instance = Self {
            ptr,
            fd,
            file_path: path.to_string(),
            num_u64s,
            byte_len,
            num_bits,
            num_hashes,
            capacity,
            fp_rate,
        };

        if !reuse {
            // Fresh file — zero the bitmap, write header.
            instance.write_header();
            instance.zero_bitmap();
        } else {
            // Validate magic / version; on mismatch, treat as fresh.
            if !instance.validate_header() {
                instance.write_header();
                instance.zero_bitmap();
            }
        }

        Ok(instance)
    }

    fn header_ptr(&self) -> *mut u8 {
        self.ptr.as_ptr() as *mut u8
    }

    fn bitmap_ptr(&self) -> *mut u64 {
        // Header occupies MMAP_HEADER_SIZE bytes; bitmap follows.
        unsafe { self.ptr.as_ptr().add(MMAP_HEADER_SIZE / 8) }
    }

    fn items_added(&self) -> usize {
        // Field at offset 24, u64 little-endian.
        unsafe {
            let p = self.header_ptr().add(24) as *const u64;
            u64::from_le(std::ptr::read_unaligned(p)) as usize
        }
    }

    fn set_items_added(&self, n: usize) {
        unsafe {
            let p = self.header_ptr().add(24) as *mut u64;
            std::ptr::write_unaligned(p, (n as u64).to_le());
        }
    }

    fn write_header(&self) {
        unsafe {
            let h = self.header_ptr();
            std::ptr::copy_nonoverlapping(MMAP_MAGIC.as_ptr(), h, 4);
            *h.add(4) = MMAP_VERSION;
            *h.add(5) = self.num_hashes as u8;
            *h.add(6) = 0;
            *h.add(7) = 0;
            let cap = (self.capacity as u64).to_le();
            let nb = (self.num_bits as u64).to_le();
            std::ptr::write_unaligned(h.add(8) as *mut u64, cap);
            std::ptr::write_unaligned(h.add(16) as *mut u64, nb);
            std::ptr::write_unaligned(h.add(24) as *mut u64, 0u64);
            // Bytes 32..64 already zero from zero_bitmap / file zeroing.
        }
    }

    fn validate_header(&self) -> bool {
        unsafe {
            let h = self.header_ptr();
            if &*(h as *const [u8; 4]) != MMAP_MAGIC {
                return false;
            }
            if *h.add(4) != MMAP_VERSION {
                return false;
            }
            // num_hashes (u8) at offset 5 — allow re-use if close enough.
            let k_on_disk = *h.add(5) as usize;
            if k_on_disk == 0 || (k_on_disk as isize - self.num_hashes as isize).abs() > 2 {
                return false;
            }
            let cap = u64::from_le(std::ptr::read_unaligned(h.add(8) as *const u64)) as usize;
            let nb = u64::from_le(std::ptr::read_unaligned(h.add(16) as *const u64)) as usize;
            cap == self.capacity && nb == self.num_bits
        }
    }

    fn zero_bitmap(&self) {
        unsafe {
            std::ptr::write_bytes(self.bitmap_ptr() as *mut u8, 0, self.num_u64s * 8);
        }
    }

    fn double_hash(&self, item: &str) -> (u64, u64) {
        // Same FNV-1a scheme as BloomFilter above — keep bit-identical
        // collision behavior so a 1:1 drop-in is possible.
        let mut h1: u64 = 0xcbf29ce484222325_u64;
        let mut h2: u64 = 0x84222325cbf29ce4_u64;
        for byte in item.bytes() {
            h1 ^= byte as u64;
            h1 = h1.wrapping_mul(0x100000001b3_u64);
            h2 ^= byte as u64;
            h2 = h2.wrapping_mul(0x100000001b3_u64);
        }
        if h2 == 0 {
            h2 = 0x0101010101010101_u64;
        }
        (h1, h2)
    }

    fn indices(&self, item: &str) -> impl Iterator<Item = usize> + '_ {
        let (h1, h2) = self.double_hash(item);
        let h1u = (h1 as usize) % self.num_bits;
        let h2u = (h2 as usize) | 1;
        (0..self.num_hashes).map(move |i| h1u.wrapping_add(i.wrapping_mul(h2u)) % self.num_bits)
    }

    /// Unsafe bit check without bounds validation (used in batch ops).
    #[inline]
    unsafe fn check_bit_unchecked(&self, idx: usize) -> bool {
        let word = (idx / 64) as usize;
        let bit = (idx % 64) as u32;
        let mask = 1u64 << bit;
        *self.bitmap_ptr().add(word) & mask != 0
    }
}

impl Drop for MmapBloomFilter {
    fn drop(&mut self) {
        unsafe {
            // MS_SYNC on drop = durable close. Cheap (kernel coalesces).
            let _ = msync(self.ptr.as_ptr() as *mut c_void, self.byte_len, MS_SYNC);
            let _ = munmap(self.ptr.as_ptr() as *mut c_void, self.byte_len);
            let _ = close(self.fd);
        }
    }
}

#[pymethods]
impl MmapBloomFilter {
    /// Open or create a file-backed persistent Bloom filter.
    ///
    /// Args:
    ///     path: File path. Parent dirs created if missing.
    ///     capacity: Expected number of elements.
    ///     fp_rate: Target false positive rate (default 0.01).
    ///     force_new: If True, truncate any existing file (default False —
    ///         reuses and validates existing file).
    #[new]
    #[pyo3(signature = (path, capacity, fp_rate = 0.01, force_new = false))]
    fn new(path: String, capacity: usize, fp_rate: f64, force_new: bool) -> PyResult<Self> {
        Self::open_or_create(&path, capacity, fp_rate, force_new)
    }

    /// Add an item. Returns True if new entry, False if already present.
    fn add(&mut self, item: &str) -> bool {
        let mut is_new = false;
        unsafe {
            let bp = self.bitmap_ptr();
            for idx in self.indices(item) {
                let word = (idx / 64) as usize;
                let bit = (idx % 64) as u32;
                let mask = 1u64 << bit;
                let cur = *bp.add(word);
                if (cur & mask) == 0 {
                    is_new = true;
                }
                *bp.add(word) = cur | mask;
            }
        }
        if is_new {
            let n = self.items_added() + 1;
            self.set_items_added(n);
        }
        // MS_ASYNC: durable later, not blocking. The Drop impl + an
        // explicit sync() at sprint end cover the "must persist now" path.
        unsafe {
            let _ = msync(self.bitmap_ptr() as *mut c_void, self.num_u64s * 8, MS_ASYNC);
        }
        is_new
    }

    /// Bulk add items to the mmap-backed filter (parallel, rayon-powered).
    ///
    /// Returns a `Vec<bool>` — one entry per input item:
    ///   `true`  = item was NOT already in the filter (new entry)
    ///   `false` = item was already present (duplicate)
    ///
    /// Uses `rayon` for parallel xxHash3-64 hashing. Bitmap merge is
    /// serial. M1 8GB bounded. msync is called once at the end.
    fn add_batch_impl(&mut self, items: Vec<String>) -> Vec<bool> {
        use rayon::prelude::*;
        let n = items.len();
        if n == 0 {
            return vec![];
        }

        // NOTE: MmapBloomFilter is !Sync due to NonNull<u64> ptr (bitmap).
        // Using serial iter() instead of par_iter() to avoid Sync bound.
        let results: Vec<(Vec<usize>, bool)> = items
            .iter()
            .map(|item| {
                let indices: Vec<usize> = self.indices(item).collect();
                let is_new = indices.iter().any(|&idx| {
                    // SAFETY: idx is derived from same filter's num_bits/num_hashes,
                    // so it is always in-bounds. bitmap_ptr() is valid for the
                    // lifetime of &self (shared reference).
                    unsafe { self.check_bit_unchecked(idx) }
                });
                (indices, is_new)
            })
            .collect();

        // Sequential merge into bitmap.
        let mut new_count = 0usize;
        for result in &results {
            let (indices, is_new) = result;
            if *is_new {
                new_count += 1;
            }
            for &idx in indices {
                let word = (idx / 64) as usize;
                let bit = (idx % 64) as u32;
                let mask = 1u64 << bit;
                unsafe {
                    *self.bitmap_ptr().add(word) |= mask;
                }
            }
        }
        if new_count > 0 {
            self.set_items_added(self.items_added() + new_count);
        }

        // Single msync for the whole batch — amortizes sync overhead.
        unsafe {
            let _ = msync(self.bitmap_ptr() as *mut c_void, self.num_u64s * 8, MS_ASYNC);
        }

        results.into_iter().map(|(_, is_new)| is_new).collect()
    }

    /// Contains check (returns bool, may be false positive).
    fn __contains__(&self, item: &str) -> bool {
        unsafe {
            let bp = self.bitmap_ptr();
            for idx in self.indices(item) {
                let word = (idx / 64) as usize;
                let bit = (idx % 64) as u32;
                let cur = *bp.add(word);
                if (cur & (1u64 << bit)) == 0 {
                    return false;
                }
            }
        }
        true
    }

    fn contains(&self, item: &str) -> bool {
        self.__contains__(item)
    }

    /// Bulk add items to the mmap-backed filter.
    ///
    /// Args:
    ///     items: List of strings to add
    ///
    /// Returns:
    ///     List[bool] — True for each new item, False for duplicates.
    fn add_batch(&mut self, items: Vec<String>) -> Vec<bool> {
        self.add_batch_impl(items)
    }

    /// Bulk contains check — rayon-parallel, read-only (no bitmap mutation).
    ///
    /// Returns `Vec<bool>` — one entry per input item:
    ///   `true`  = item might be in the filter (may be false positive)
    ///   `false` = item is definitely NOT in the filter
    ///
    /// Checks BOTH active AND previous generations.
    /// M1 8GB: rayon short-lived pool, demand-paged mmap pages.
    fn contains_batch(&self, items: Vec<String>) -> Vec<bool> {
        use rayon::prelude::*;
        if items.is_empty() {
            return vec![];
        }

        // NOTE: MmapBloomFilter is !Sync due to NonNull<u64> ptr.
        // Using serial iter() to avoid Sync bound.
        items
            .iter()
            .map(|item| {
                self.contains(item)
            })
            .collect()
    }

    /// Atomic check-and-add batch — returns (seen_before, is_new) per item.
    ///
    /// Unlike `add_batch` (which only returns is_new), this returns BOTH:
    ///   - seen_before: True if item was already in filter BEFORE this call
    ///   - is_new:      True if item was NOT in filter after this call
    ///
    /// This is the canonical cross-process dedup primitive: callers can
    /// distinguish true negatives (seen_before=False, is_new=True → fresh)
    /// from false positives (seen_before=True,  is_new=False → deduped).
    ///
    /// Single msync at end. Thread-safe via caller-supplied Lock.
    fn check_and_add_batch_impl(&mut self, items: Vec<String>) -> Vec<(bool, bool)> {
        use rayon::prelude::*;
        if items.is_empty() {
            return vec![];
        }

        // Phase 1 — serial: hash all items, collect seen_before flags.
        // NOTE: MmapBloomFilter is !Sync due to NonNull<u64> ptr.
        let results: Vec<(Vec<usize>, bool, bool)> = items
            .iter()
            .map(|item| {
                let indices: Vec<usize> = self.indices(item).collect();
                // seen_before: any bit already set BEFORE mutation
                let seen_before = indices
                    .iter()
                    .any(|&idx| unsafe { self.check_bit_unchecked(idx) });
                // is_new: any bit NOT set (will become set below)
                let is_new = indices.iter().any(|&idx| unsafe { !self.check_bit_unchecked(idx) });
                (indices, seen_before, is_new)
            })
            .collect();

        // Phase 2 — sequential: mutate bitmap, update counters.
        let mut new_count = 0usize;
        for (indices, _, is_new) in &results {
            if *is_new {
                new_count += 1;
            }
            for &idx in indices {
                let word = (idx / 64) as usize;
                let bit = (idx % 64) as u32;
                let mask = 1u64 << bit;
                unsafe {
                    *self.bitmap_ptr().add(word) |= mask;
                }
            }
        }
        if new_count > 0 {
            self.set_items_added(self.items_added() + new_count);
        }

        // Single msync for the whole batch.
        unsafe {
            let _ = msync(self.bitmap_ptr() as *mut c_void, self.num_u64s * 8, MS_ASYNC);
        }

        results
            .into_iter()
            .map(|(_, seen_before, is_new)| (seen_before, is_new))
            .collect()
    }

    /// Atomic check-and-add batch — Python-facing wrapper.
    ///
    /// Returns list of (seen_before, is_new) tuples per input item.
    /// Use when the caller needs to distinguish true negatives
    /// (seen_before=False → first time ever seen across all processes)
    /// from false positives (seen_before=True → already deduped).
    fn check_and_add_batch(&mut self, items: Vec<String>) -> Vec<(bool, bool)> {
        self.check_and_add_batch_impl(items)
    }

    /// Force durable sync to disk. Cheap (kernel coalesces msyncs).
    fn sync(&self) -> bool {
        unsafe { msync(self.ptr.as_ptr() as *mut c_void, self.byte_len, MS_SYNC) == 0 }
    }

    /// Reset the filter to empty (in-place, file remains mapped).
    fn reset(&mut self) {
        self.zero_bitmap();
        self.set_items_added(0);
        let _ = self.sync();
    }

    fn __len__(&self) -> usize {
        self.items_added()
    }

    fn capacity(&self) -> usize {
        self.capacity
    }

    fn fp_rate(&self) -> f64 {
        self.fp_rate
    }

    fn file_path(&self) -> String {
        self.file_path.clone()
    }

    fn byte_size(&self) -> usize {
        self.byte_len
    }
}

// ===========================================================================
// RotatingMmapBloomFilter — two-generation mmap-backed Bloom filter (F288+)
// ===========================================================================
//
// Fixes the Python-side race condition where RotatingBloomFilter in
// knowledge/dedup.py checks `os.path.exists(path)` before constructing
// MmapBloomFilter — between the check and the open, another process can
// delete or recreate the file, causing EIO or stale handle.
//
// RotatingMmapBloomFilter owns BOTH generations inside Rust:
//   - paths[0] = active generation
//   - paths[1] = previous (read-only for lookups)
//
// Rotation is a simple index swap — no file deletion, no race.
//
// Python calls rotate() when active reaches capacity.
//
// M1 8GB safe: demand-paged mmap, two files max (~24 MB total for 100K items).

#[pyclass(unsendable)]
pub struct RotatingMmapBloomFilter {
    /// Two file paths [active, previous]
    paths: [String; 2],
    /// Index of the current active generation (0 or 1)
    current: usize,
    /// Mmap-backed filters [active, previous]
    filters: [MmapBloomFilter; 2],
}

impl RotatingMmapBloomFilter {
    /// Open or create a two-generation rotating filter.
    fn open_or_create(
        path_a: &str,
        path_b: &str,
        capacity: usize,
        fp_rate: f64,
    ) -> PyResult<Self> {
        // force_new only if NEITHER file exists
        let force_new = !Path::new(path_a).exists() && !Path::new(path_b).exists();

        // Unwrap Results — errors propagate
        let filter_a = MmapBloomFilter::open_or_create(path_a, capacity, fp_rate, force_new)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!(
                "RotatingMmapBloomFilter: open path_a failed: {}", e)))?;
        let filter_b = MmapBloomFilter::open_or_create(path_b, capacity, fp_rate, force_new)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!(
                "RotatingMmapBloomFilter: open path_b failed: {}", e)))?;

        // Use filter with more items as "active" (the newer one); equal → path_a active
        let (filters, current) = if filter_a.items_added() >= filter_b.items_added() {
            ([filter_a, filter_b], 0)
        } else {
            ([filter_b, filter_a], 1)
        };

        Ok(Self {
            paths: [path_a.to_string(), path_b.to_string()],
            current,
            filters,
        })
    }

    #[inline]
    fn active(&self) -> &MmapBloomFilter { &self.filters[self.current] }
    #[inline]
    fn active_mut(&mut self) -> &mut MmapBloomFilter { &mut self.filters[self.current] }
    #[inline]
    fn previous(&self) -> &MmapBloomFilter { &self.filters[1 - self.current] }
}

#[pymethods]
impl RotatingMmapBloomFilter {
    #[new]
    #[pyo3(signature = (path_a, path_b, capacity = 100_000, fp_rate = 0.01))]
    fn new(path_a: String, path_b: String, capacity: usize, fp_rate: f64) -> PyResult<Self> {
        Self::open_or_create(&path_a, &path_b, capacity, fp_rate)
    }

    /// Check both generations — active AND previous.
    /// May return false negatives only if previous was full and rotated out.
    fn contains(&self, item: &str) -> bool {
        self.active().contains(item) || self.previous().contains(item)
    }

    /// Add to active generation only.
    fn add(&mut self, item: &str) -> bool { self.active_mut().add(item) }

    /// Bulk add to active generation.
    fn add_batch(&mut self, items: Vec<String>) -> Vec<bool> { self.active_mut().add_batch(items) }

    /// Bulk contains check — serial, checks both generations.
    ///
    /// Returns `Vec<bool>` — one entry per input item.
    fn contains_batch(&self, items: Vec<String>) -> Vec<bool> {
        // NOTE: MmapBloomFilter is !Sync due to NonNull<u64> ptr.
        // Using serial iter() to avoid Sync bound.
        items.iter().map(|item| self.contains(item)).collect()
    }

    /// Rotate: active → previous (read-only), previous → active (reopened fresh).
    ///
    /// Safe rotation: no file deletion, no race on os.path.exists().
    fn rotate(&mut self) -> PyResult<()> {
        let prev_idx = 1 - self.current;
        let fresh = MmapBloomFilter::open_or_create(
            &self.paths[prev_idx],
            self.filters[self.current].capacity,
            self.filters[self.current].fp_rate,
            true, // force_new — truncate to fresh
        ).map_err(|e| pyo3::exceptions::PyIOError::new_err(format!(
            "RotatingMmapBloomFilter: rotate failed: {}", e)))?;
        self.filters[prev_idx] = fresh;
        self.current = prev_idx;
        Ok(())
    }

    fn sync(&self) -> bool { self.filters[0].sync() && self.filters[1].sync() }
    fn reset_active(&mut self) { self.active_mut().reset(); }
    fn __len__(&self) -> usize { self.active().__len__() }
    fn previous_len(&self) -> usize { self.previous().__len__() }
    fn capacity(&self) -> usize { self.active().capacity }
    fn fp_rate(&self) -> f64 { self.active().fp_rate }
    fn active_path(&self) -> String { self.paths[self.current].clone() }
    fn previous_path(&self) -> String { self.paths[1 - self.current].clone() }
    fn current_index(&self) -> usize { self.current }
}

/// Register MmapBloomFilter in the parent module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MmapBloomFilter>()?;
    m.add_class::<RotatingMmapBloomFilter>()?;
    Ok(())
}
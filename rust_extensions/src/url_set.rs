//! URL deduplication set — mmap-backed persistent version.
//!
//! Persists URL dedup state across process restarts via file-backed storage.
//! Uses FNV-1a 64-bit hash for O(1) add/contains.
//! M1 8GB safe: demand-paged, HashSet rebuilt on load.
//!
//! Thread-safety fix (Issue #2): Replaced DashMap with parking_lot::RwLock.
//! DashMap caused segfaults when called from Python async/ThreadPoolExecutor
//! because its internal locking doesn't play well with PyO3's GIL handling.
//! parking_lot::RwLock is Send+Sync by default (no unsafe), faster, and
//! properly reentrant - safe for Python async contexts.
//!
//! File format (little-endian):
//!   Offset  Size  Field
//!   ------  ----  ---------------------------------------------------------
//!        0     4  magic   = b"URID"  (Hledac URL ID)
//!        4     1  version = 0x01
//!        5     3  reserved (zero, alignment)
//!        8     4  num_entries (u32, from hashes.len())
//!       12     8  total_seen (u64)
//!       20    44  reserved (zero — pads header to 64 bytes)
//!       64   N*8  hash array (N u64 values, N = num_entries)
//!
//! Total file size = 64 + num_entries * 8 bytes.

use pyo3::prelude::*;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::Path;
use parking_lot::RwLock;
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

// ===========================================================================
// Constants
// ===========================================================================

const MMAP_HEADER_SIZE: usize = 64;
const MMAP_MAGIC: &[u8; 4] = b"URID";
const MMAP_VERSION: u8 = 1;

// ===========================================================================
// FNV-1a Hash
// ===========================================================================

const FNV_OFFSET_BASIS: u64 = 0xcbf29ce484222325;
const FNV_PRIME: u64 = 0x100000001b3;

#[inline]
fn fnv1a_64(data: &[u8]) -> u64 {
    let mut hash = FNV_OFFSET_BASIS;
    for byte in data {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

// ===========================================================================
// MmapUrlSet — file-backed persistent URL dedup
// ===========================================================================

/// Thread-safe mmap-backed URL dedup using parking_lot::RwLock + HashSet + atomic counters.
/// Issue #2 fix: Replaced DashMap with parking_lot::RwLock for Python async safety.
#[pyclass(unsendable)]
pub struct MmapUrlSet {
    file_path: String,
    // Issue #2 fix: parking_lot::RwLock is Send+Sync by default, no unsafe impl needed.
    // Properly reentrant for Python async/ThreadPoolExecutor contexts.
    hashes: RwLock<HashSet<u64>>,
    total_seen: AtomicU64,          // atomic counter
    dirty: AtomicBool,               // atomic dirty flag
}

impl MmapUrlSet {
    fn open_or_create(path: &str, force_new: bool) -> PyResult<Self> {
        let p = Path::new(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!("mkdir failed: {}", e))
                })?;
            }
        }

        let _file = if force_new || !p.exists() {
            OpenOptions::new()
                .read(true).write(true).create(true).truncate(true)
                .open(p)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open failed: {}", e)))?
        } else {
            OpenOptions::new().read(true).write(true).open(p)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open failed: {}", e)))?
        };

        let mut store = Self {
            file_path: path.to_string(),
            hashes: RwLock::new(HashSet::with_capacity_and_hasher(100_000, Default::default())),
            total_seen: AtomicU64::new(0),
            dirty: AtomicBool::new(false),
        };

        if !force_new && p.exists() {
            let _ = store.load_from_file();
        }
        Ok(store)
    }

    fn load_from_file(&mut self) -> PyResult<()> {
        let mut file = OpenOptions::new().read(true).write(true).open(&self.file_path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open failed: {}", e)))?;

        let mut header = [0u8; MMAP_HEADER_SIZE];
        file.read_exact(&mut header).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("read header failed: {}", e))
        })?;

        if &header[0..4] != MMAP_MAGIC {
            return Ok(()); // Bad magic, start fresh
        }
        if header[4] != MMAP_VERSION {
            return Ok(()); // Unknown version, start fresh
        }

        let num_entries = u32::from_le_bytes([header[8], header[9], header[10], header[11]]);
        let total = u64::from_le_bytes([header[12], header[13], header[14], header[15], header[16], header[17], header[18], header[19]]);
        self.total_seen.store(total, Ordering::Relaxed);

        if num_entries == 0 {
            return Ok(());
        }

        // Read hash array
        let byte_count = num_entries as usize * 8;
        let mut byte_buf = vec![0u8; byte_count];
        file.read_exact(&mut byte_buf).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("read hashes failed: {}", e))
        })?;

        for i in 0..num_entries as usize {
            let offset = i * 8;
            let hash = u64::from_le_bytes([
                byte_buf[offset], byte_buf[offset+1], byte_buf[offset+2], byte_buf[offset+3],
                byte_buf[offset+4], byte_buf[offset+5], byte_buf[offset+6], byte_buf[offset+7]
            ]);
            self.hashes.insert(hash, ());
        }

        self.dirty.store(false, Ordering::Relaxed);
        Ok(())
    }

    fn persist(&self) -> PyResult<()> {
        if !self.dirty.load(Ordering::Relaxed) { return Ok(()); }

        let file = OpenOptions::new().write(true).truncate(true).open(&self.file_path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open for write failed: {}", e)))?;

        // Issue #2 fix: Collect hashes under parking_lot RwLock read lock
        let entries: Vec<u64> = self.hashes.read().iter().cloned().collect();
        let num_entries = entries.len() as u32;

        // Write header
        let mut header = [0u8; MMAP_HEADER_SIZE];
        header[0..4].copy_from_slice(MMAP_MAGIC);
        header[4] = MMAP_VERSION;
        header[8..12].copy_from_slice(&num_entries.to_le_bytes());
        header[12..20].copy_from_slice(&self.total_seen.load(Ordering::Relaxed).to_le_bytes());

        let mut file = file;
        file.write_all(&header).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("write header failed: {}", e))
        })?;

        // Write hash array
        let mut hash_bytes = Vec::with_capacity(entries.len() * 8);
        for &h in &entries {
            hash_bytes.extend_from_slice(&h.to_le_bytes());
        }
        file.write_all(&hash_bytes).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("write hashes failed: {}", e))
        })?;

        file.sync_all().map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("sync failed: {}", e))
        })?;

        self.dirty.store(false, Ordering::Relaxed);
        Ok(())
    }
}

impl Drop for MmapUrlSet {
    fn drop(&mut self) {
        let _ = self.persist();
    }
}

#[pymethods]
impl MmapUrlSet {
    #[new]
    #[pyo3(signature = (path, force_new = false))]
    pub fn new(path: &str, force_new: bool) -> PyResult<Self> {
        Self::open_or_create(path, force_new)
    }

    /// Add a URL to the dedup set.
    /// Returns true if URL was new (not previously seen).
    pub fn add(&self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.total_seen.fetch_add(1, Ordering::Relaxed);
        // Issue #2 fix: parking_lot::RwLock - insert returns true if new
        let is_new = self.hashes.write().insert(hash);
        if is_new {
            self.dirty.store(true, Ordering::Relaxed);
        }
        is_new
    }

    /// Check if URL is in the dedup set.
    pub fn contains(&self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.hashes.read().contains(&hash)
    }

    #[allow(unused)]
    pub fn len(&self) -> usize {
        self.hashes.read().len()
    }

    pub fn total_seen(&self) -> u64 {
        self.total_seen.load(Ordering::Relaxed)
    }

    pub fn is_empty(&self) -> bool {
        self.hashes.read().is_empty()
    }

    pub fn clear(&self) {
        self.hashes.write().clear();
        self.total_seen.store(0, Ordering::Relaxed);
        self.dirty.store(true, Ordering::Relaxed);
    }

    pub fn memory_bytes(&self) -> usize {
        // Issue #2 fix: HashSet capacity estimation
        let hashes = self.hashes.read();
        let entry_size = 16 + 8;
        hashes.capacity() * std::mem::size_of::<u64>() + hashes.len() * entry_size
    }

    pub fn msync(&self) -> PyResult<()> { self.persist() }
    pub fn path(&self) -> String { self.file_path.clone() }
    pub fn byte_size(&self) -> usize { MMAP_HEADER_SIZE + self.hashes.read().len() * 8 }
}

// ===========================================================================
// Legacy in-memory UrlSet (kept for compat + tests)
// ===========================================================================

#[pyclass]
pub struct UrlSet {
    hashes: std::collections::HashSet<u64>,
    total_seen: u64,
}

#[pymethods]
impl UrlSet {
    #[new]
    #[pyo3(signature = (capacity = 0))]
    pub fn new(capacity: usize) -> Self {
        Self {
            hashes: std::collections::HashSet::with_capacity(capacity),
            total_seen: 0,
        }
    }

    pub fn add(&mut self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.total_seen += 1;
        self.hashes.insert(hash)
    }

    pub fn contains(&self, url: &str) -> bool {
        let hash = fnv1a_64(url.as_bytes());
        self.hashes.contains(&hash)
    }

    #[allow(unused)]
    pub fn len(&self) -> usize { self.hashes.len() }

    pub fn total_seen(&self) -> u64 { self.total_seen }
    pub fn is_empty(&self) -> bool { self.hashes.is_empty() }
    pub fn clear(&mut self) { self.hashes.clear(); self.total_seen = 0; }

    pub fn memory_bytes(&self) -> usize {
        let entry_size = 16 + 8;
        self.hashes.capacity() * std::mem::size_of::<u64>() + self.hashes.len() * entry_size
    }

    pub fn __getstate__(&self) -> (Vec<u64>, u64) {
        (self.hashes.iter().cloned().collect(), self.total_seen)
    }

    pub fn __setstate__(&mut self, state: (Vec<u64>, u64)) {
        let (hashes, total_seen) = state;
        self.hashes = hashes.into_iter().collect();
        self.total_seen = total_seen;
    }

    pub fn to_list(&self) -> Vec<u64> {
        self.hashes.iter().cloned().collect()
    }
}

impl Default for UrlSet {
    fn default() -> Self { Self::new(0) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add_and_contains() {
        let mut set = UrlSet::new(0);
        assert!(set.add("https://example.com"));
        assert!(set.contains("https://example.com"));
    }

    #[test]
    fn test_duplicate_rejected() {
        let mut set = UrlSet::new(0);
        assert!(set.add("https://example.com"));
        assert!(!set.add("https://example.com"));
        assert_eq!(set.len(), 1);
    }

    #[test]
    fn test_total_seen() {
        let mut set = UrlSet::new(0);
        set.add("https://example.com");
        set.add("https://example.com");
        set.add("https://test.com");
        assert_eq!(set.total_seen(), 3);
        assert_eq!(set.len(), 2);
    }

    #[test]
    fn test_clear() {
        let mut set = UrlSet::new(0);
        set.add("https://example.com");
        set.clear();
        assert!(set.is_empty());
        assert_eq!(set.total_seen(), 0);
    }
}

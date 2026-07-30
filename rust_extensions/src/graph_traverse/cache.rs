//! graph_traverse/cache.rs — LRU cache s mmap-backed persistence pro graph traversal.
//!
//! Sprint F265-U5: Thread-local DuckDB connection pooling.
//! Sprint F265B-III: LZ4 komprese pro cold data.
//!
//! Architecture:
//! - Per-worker thread-local LRU (žádný cross-worker sync, ideální pro rayon)
//! - Mmap-backed persistence — cache přežije restart processu
//! - Lazy load on first access — mmapped file otevřen až když je potřeba
//! - Bounded: MAX_ENTRIES=50k, MAX_BYTES=100MB, M1 8GB safe
//! - LZ4 komprese na cold data (entry payload)
//!
//! Design invariants:
//!   C.T1  Thread-local: každý rayon worker má vlastní cache instanci
//!   C.T2  Bounded: MAX_ENTRIES=50k, MAX_BYTES=100MB — OOM protection
//!   C.T3  Lazy init: mmap file otevřen/creován až na první get_or_insert
//!   C.T4  Fail-soft: jakákoliv chyba → cache miss (traverse bez cache)
//!   C.T5  Flush na drop_connections() — konzistentní s existing cleanup pattern

use bincode;
use lz4_flex::block::{compress as lz4_compress, decompress as lz4_decompress};
use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::panic; // FFI-05: catch_unwind for mmap across FFI boundary
use std::path::PathBuf;

use super::TraversalResult;

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/// Maximum cached (root_value, max_hops) → results entries.
const MAX_CACHE_ENTRIES: usize = 50_000;
/// Maximum total byte size of cache file on disk.
const MAX_CACHE_BYTES: usize = 100 * 1024 * 1024; // 100 MB
/// Magic header for cache file format version.
const CACHE_MAGIC: u32 = 0x4754_5256; // "GTRV" — Graph TRAVersal
/// Current cache file format version.
const CACHE_VERSION: u8 = 1;
/// File name for the mmap cache.
const CACHE_FILE: &str = "traversal_cache.lz4";

// ─────────────────────────────────────────────────────────────────────────────
// Cache key
// ─────────────────────────────────────────────────────────────────────────────

/// Cache key: (root_value, max_hops).
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(crate) struct CacheKey {
    root_value: String,
    max_hops: usize,
}

impl CacheKey {
    fn new(root_value: String, max_hops: usize) -> Self {
        Self { root_value, max_hops }
    }

    fn file_path(cache_dir: &PathBuf) -> PathBuf {
        cache_dir.join(CACHE_FILE)
    }

    /// Serialized byte size of this key (key_bytes_len + key_bytes + max_hops).
    fn serialized_len(&self) -> usize {
        4 + self.root_value.len() + 4
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Cache file header
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct CacheHeader {
    magic: u32,
    version: u8,
    entry_count: u32,
    data_size: u64,
}

impl CacheHeader {
    const SIZE: usize = 4 + 1 + 4 + 8; // 17 bytes

    #[allow(dead_code)]
    fn is_valid(&self) -> bool {
        self.magic == CACHE_MAGIC && self.version == CACHE_VERSION
    }

    fn serialize(&self) -> Vec<u8> {
        let mut result = Vec::with_capacity(Self::SIZE);
        result.extend_from_slice(&self.magic.to_le_bytes());
        result.push(self.version);
        result.extend_from_slice(&self.entry_count.to_le_bytes());
        result.extend_from_slice(&self.data_size.to_le_bytes());
        result
    }

    fn deserialize(data: &[u8]) -> Option<Self> {
        if data.len() < Self::SIZE {
            return None;
        }
        let magic = u32::from_le_bytes(data[0..4].try_into().ok()?);
        let version = data[4];
        let entry_count = u32::from_le_bytes(data[5..9].try_into().ok()?);
        let data_size = u64::from_le_bytes(data[9..17].try_into().ok()?);
        Some(Self { magic, version, entry_count, data_size })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// LRU cache state (in-memory)
// ─────────────────────────────────────────────────────────────────────────────

pub struct LRUCache {
    /// O(1) lookup: key → value
    entries: HashMap<CacheKey, Vec<TraversalResult>>,
    /// LRU order: front = least recently used
    lru_order: VecDeque<CacheKey>,
    /// Monotonic counter for LRU ordering.
    counter: u64,
    /// Current byte size of cache file.
    current_bytes: usize,
    /// Whether cache has been modified since last flush.
    dirty: bool,
    /// App data directory (for mmap file path).
    cache_dir: PathBuf,
}

impl LRUCache {
    fn new(cache_dir: PathBuf) -> Self {
        Self {
            entries: HashMap::new(),
            lru_order: VecDeque::new(),
            counter: 0,
            current_bytes: 0,
            dirty: false,
            cache_dir,
        }
    }

    /// Get cached result or insert via `fetch` closure.
    /// Returns owned Vec (not reference) to avoid borrow checker issues.
    pub(crate) fn get_or_insert<F>(&mut self, key: CacheKey, fetch: F) -> Vec<TraversalResult>
    where
        F: FnOnce() -> Vec<TraversalResult>,
    {
        // Fast path: cache hit
        if let Some(value) = self.entries.get(&key) {
            // Update LRU — move to back (most recently used)
            self.lru_order.retain(|k| k != &key);
            self.lru_order.push_back(key.clone());
            self.counter += 1;
            return value.clone();
        }

        // Cache miss — fetch and insert
        let value = fetch();
        if value.is_empty() {
            // Don't cache empty results — legitimate "no connections found"
            self.lru_order.push_back(key);
            self.counter += 1;
            return Vec::new();
        }

        self.insert(key, value.clone());
        value
    }

    fn insert(&mut self, key: CacheKey, value: Vec<TraversalResult>) {
        // Estimate serialized + compressed size
        let Ok(serialized) = bincode::encode_to_vec(&value, bincode::config::standard()) else {
            return;
        };
        let compressed = lz4_compress(&serialized);
        let entry_bytes = compressed.len();

        // Evict LRU entries if at capacity
        while self.entries.len() >= MAX_CACHE_ENTRIES
            || self.current_bytes + entry_bytes > MAX_CACHE_BYTES
        {
            if let Some(old_key) = self.lru_order.pop_front() {
                if let Some(v) = self.entries.remove(&old_key) {
                    if let Ok(old_serialized) = bincode::encode_to_vec(&v, bincode::config::standard()) {
                        let old_compressed = lz4_compress(&old_serialized);
                        self.current_bytes = self.current_bytes.saturating_sub(old_compressed.len());
                    }
                    self.dirty = true;
                }
            } else {
                break;
            }
        }

        // Insert new entry
        self.current_bytes += entry_bytes;
        self.entries.insert(key, value);
        self.counter += 1;
        self.dirty = true;
    }

    /// Load cache from mmap file (if exists and valid).
    ///
    /// FFI-05 fix: `map_mut` can panic if the file is deleted between
    /// `open()` and `map_mut()` (TOCTOU race). Panic crossing the FFI boundary
    /// is UB on macOS (Mach ports don't support Rust unwinding). We use
    /// `catch_unwind` as a safety net AND re-verify file existence before
    /// mapping to close the TOCTOU gap.
    pub fn load_from_mmap(&mut self) {
        let path = CacheKey::file_path(&self.cache_dir);
        let Ok(mut file) = OpenOptions::new().read(true).write(true).open(&path) else {
            return;
        };
        let Ok(metadata) = file.metadata() else {
            return;
        };
        if metadata.len() < CacheHeader::SIZE as u64 {
            return;
        }

        let mut header_buf = vec![0u8; CacheHeader::SIZE];
        if file.read_exact(&mut header_buf).is_err() {
            return;
        }
        let Some(header) = CacheHeader::deserialize(&header_buf) else {
            return;
        };
        if !header.is_valid() {
            return;
        }

        // FFI-05: Re-verify file still exists before mmap to close TOCTOU race.
        // If another process truncates/deletes the file between the earlier
        // `file.metadata()` and this call, `map_mut` would panic.
        let Ok(current_metadata) = file.metadata() else {
            return;
        };
        if current_metadata.len() < CacheHeader::SIZE as u64 {
            return;
        }

        // FFI-05: Wrap `map_mut` in `catch_unwind`. If a panic occurs
        // (e.g., due to memory pressure or TOCTOU race), we catch it and
        // return gracefully instead of propagating panic across the FFI boundary.
        let mmap = match panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            unsafe { memmap2::MmapMut::map_mut(&file) }
        })) {
            Ok(Ok(m)) => m,
            Ok(Err(e)) => {
                eprintln!("[graph_traverse/cache] load_from_mmap: mmap error: {}", e);
                return;
            }
            Err(_) => {
                // Panic was caught — file may have been deleted/truncated by another process.
                // Return gracefully (cache miss) rather than propagate panic across FFI.
                eprintln!("[graph_traverse/cache] load_from_mmap: map_mut panicked (file race?), skipping cache load");
                return;
            }
        };

        let data_size = header.data_size as usize;
        if data_size > mmap.len() {
            return;
        }

        // Parse entries sequentially after header
        let mut offset = CacheHeader::SIZE;
        let mut loaded = 0u32;

        while offset + 4 <= data_size && loaded < header.entry_count {
            // Read key_len
            let key_len = u32::from_le_bytes(
                mmap[offset..offset + 4].try_into().unwrap_or([0u8; 4])
            ) as usize;
            offset += 4;

            if offset + key_len + 4 + 4 > data_size {
                break;
            }

            // Read key_value
            let key_value = String::from_utf8(
                mmap[offset..offset + key_len].to_vec()
            ).unwrap_or_default();
            offset += key_len;

            // Read max_hops
            let max_hops = u32::from_le_bytes(
                mmap[offset..offset + 4].try_into().unwrap_or([0u8; 4])
            ) as usize;
            offset += 4;

            // Read value_len
            let value_len = u32::from_le_bytes(
                mmap[offset..offset + 4].try_into().unwrap_or([0u8; 4])
            ) as usize;
            offset += 4;

            if offset + value_len + 8 > data_size {
                break;
            }

            // Read compressed_value
            let compressed_value = mmap[offset..offset + value_len].to_vec();
            offset += value_len;

            // Read counter (unused for now, but we still need to skip it)
            offset += 8;

            // Decompress and deserialize
            let decompressed = lz4_decompress(&compressed_value, compressed_value.len() * 4);
            if let Ok(decomp) = decompressed {
                if let Ok((results, _)) = bincode::decode_from_slice::<Vec<TraversalResult>, _>(
                    &decomp,
                    bincode::config::standard(),
                ) {
                    let key = CacheKey { root_value: key_value, max_hops };
                    self.entries.insert(key.clone(), results);
                    self.lru_order.push_back(key);
                    self.current_bytes += compressed_value.len() + 256; // rough estimate
                    loaded += 1;
                }
            }
        }

        self.dirty = false;
    }

    /// Serialize current cache state to mmap file.
    pub fn flush(&mut self) {
        if !self.dirty || self.entries.is_empty() {
            return;
        }

        let path = CacheKey::file_path(&self.cache_dir);

        // Ensure parent directory exists
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        let mut file = match OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(&path)
        {
            Ok(f) => f,
            Err(e) => {
                eprintln!("[graph_traverse/cache] flush: cannot open cache file: {}", e);
                return;
            }
        };

        // Collect entries sorted by LRU order (oldest first)
        let entries_sorted: Vec<_> = self.lru_order
            .iter()
            .filter_map(|k| self.entries.get(k).map(|v| (k.clone(), v.clone())))
            .collect();

        // Serialize entries
        let mut all_data = Vec::new();
        for (key, value) in entries_sorted {
            if let Ok(serialized) = bincode::encode_to_vec(&value, bincode::config::standard()) {
                let compressed = lz4_compress(&serialized);
                let entry_len = key.serialized_len() + 4 + compressed.len() + 8;

                // [key_value_len:u32][key_value][max_hops:u32][value_len:u32][compressed][counter:u64]
                let mut entry = Vec::with_capacity(entry_len);
                let key_bytes = key.root_value.as_bytes();
                entry.extend_from_slice(&(key_bytes.len() as u32).to_le_bytes());
                entry.extend_from_slice(key_bytes);
                entry.extend_from_slice(&(key.max_hops as u32).to_le_bytes());
                entry.extend_from_slice(&(compressed.len() as u32).to_le_bytes());
                entry.extend_from_slice(&compressed);
                entry.extend_from_slice(&0u64.to_le_bytes()); // counter (unused on load)
                all_data.extend_from_slice(&entry);
            }
        }

        // Truncate to MAX_CACHE_BYTES if needed
        let all_data = if all_data.len() > MAX_CACHE_BYTES {
            // Keep newest entries (from the back)
            let mut result = Vec::with_capacity(MAX_CACHE_BYTES);
            let mut size = 0;
            let mut seen = std::collections::HashSet::new();
            for entry in all_data.chunks(all_data.len() / self.entries.len().max(1)).rev() {
                if size + entry.len() > MAX_CACHE_BYTES {
                    break;
                }
                // Simple dedup by checking first 4 bytes (key_len)
                let key_sig = &entry[..4];
                if seen.insert(key_sig.to_vec()) {
                    result.extend_from_slice(entry);
                    size += entry.len();
                }
            }
            result.reverse();
            result
        } else {
            all_data
        };

        // Write header + data
        let header = CacheHeader {
            magic: CACHE_MAGIC,
            version: CACHE_VERSION,
            entry_count: self.entries.len() as u32,
            data_size: (CacheHeader::SIZE + all_data.len()) as u64,
        };

        let mut file_data = header.serialize();
        file_data.extend_from_slice(&all_data);

        if let Err(e) = file.write_all(&file_data) {
            eprintln!("[graph_traverse/cache] flush: write failed: {}", e);
            return;
        }

        if let Err(e) = file.set_len(file_data.len() as u64) {
            eprintln!("[graph_traverse/cache] flush: set_len failed: {}", e);
        }

        self.dirty = false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Thread-local cache accessor
// ─────────────────────────────────────────────────────────────────────────────

use std::cell::RefCell;

thread_local! {
    static THREAD_CACHE: RefCell<Option<LRUCache>> = RefCell::new(None);
}

/// Get or create the thread-local LRU cache for this rayon worker.
fn with_cache<F, R>(cache_dir: PathBuf, f: F) -> R
where
    F: FnOnce(&mut LRUCache) -> R,
{
    THREAD_CACHE.with(|cell| {
        let mut opt_cache = cell.borrow_mut();
        if opt_cache.is_none() {
            let mut cache = LRUCache::new(cache_dir.clone());
            cache.load_from_mmap();
            *opt_cache = Some(cache);
        }
        let cache = opt_cache.as_mut().unwrap();
        f(cache)
    })
}

/// Get cached traversal result or compute + cache it.
pub fn get_cached_traversal(
    db_path: &str,
    root_value: &str,
    max_hops: usize,
    cache_dir: PathBuf,
) -> Vec<TraversalResult> {
    let key = CacheKey::new(root_value.to_string(), max_hops);
    with_cache(cache_dir, |cache| {
        cache.get_or_insert(key, || {
            super::traverse_single(db_path, root_value, max_hops)
        })
    })
}

/// Flush all dirty entries to mmap (called from drop_connections).
pub fn flush_cache(cache_dir: PathBuf) {
    with_cache(cache_dir, |cache| {
        cache.flush();
    });
}

/// Drop all thread-local cache entries (called from drop_connections).
#[allow(unused_variables)]
pub fn drop_cache(cache_dir: PathBuf) {
    THREAD_CACHE.with(|cell| {
        let mut opt_cache = cell.borrow_mut();
        if let Some(mut cache) = opt_cache.take() {
            cache.flush();
        }
    });
}
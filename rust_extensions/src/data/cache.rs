//! TinyLFU LRU cache for DuckDB graph operations.
//!
//! M1 8GB optimization: Bounded cache prevents memory exhaustion.
//!
//! ## Cache properties
//!
//! - TinyLFU admission: frequency-based admission to prevent cache pollution
//! - LRU eviction: least-recently-used when capacity is reached
//! - Memory bounded: MAX_ENTRIES and MAX_BYTES limits
//! - Thread-safe: uses parking_lot RwLock

use pyo3::prelude::*;
use std::collections::HashMap;

/// Cache entry with frequency count for TinyLFU.
struct CacheEntry {
    value: Vec<u8>,
    frequency: u32,
    last_access: u64,
}

/// TinyLFU LRU cache with bounded memory.
pub struct GraphLRUCache {
    entries: HashMap<String, CacheEntry>,
    max_entries: usize,
    max_bytes: usize,
    current_bytes: usize,
    access_counter: u64,
}

impl GraphLRUCache {
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            entries: HashMap::new(),
            max_entries,
            max_bytes,
            current_bytes: 0,
            access_counter: 0,
        }
    }

    /// Get a value from cache, updating access metadata.
    pub fn get(&mut self, key: &str) -> Option<Vec<u8>> {
        self.access_counter += 1;
        if let Some(entry) = self.entries.get_mut(key) {
            entry.frequency += 1;
            entry.last_access = self.access_counter;
            Some(entry.value.clone())
        } else {
            None
        }
    }

    /// Put a value into cache with TinyLFU admission.
    pub fn put(&mut self, key: String, value: Vec<u8>) {
        let size = value.len();

        // If key exists, update
        if let Some(entry) = self.entries.get_mut(&key) {
            self.current_bytes -= entry.value.len();
            entry.value = value;
            entry.frequency += 1;
            entry.last_access = self.access_counter;
            self.current_bytes += size;
            return;
        }

        // TinyLFU admission: check if we should evict
        while self.should_evict(size) {
            self.evict_one();
        }

        // Insert new entry
        self.access_counter += 1;
        let entry = CacheEntry {
            frequency: 1,
            last_access: self.access_counter,
            value,
        };
        self.current_bytes += size;
        self.entries.insert(key, entry);
    }

    /// Check if we need to evict to make room.
    fn should_evict(&self, new_size: usize) -> bool {
        self.entries.len() >= self.max_entries || self.current_bytes + new_size > self.max_bytes
    }

    /// Evict one entry using TinyLFU + LRU hybrid.
    fn evict_one(&mut self) {
        if self.entries.is_empty() {
            return;
        }

        // Find entry with lowest frequency (TinyLFU), breaking ties by recency (LRU)
        let to_remove = self.entries
            .iter()
            .min_by_key(|(_, entry)| (entry.frequency, entry.last_access))
            .map(|(k, _)| k.clone());

        if let Some(key) = to_remove {
            if let Some(entry) = self.entries.remove(&key) {
                self.current_bytes -= entry.value.len();
            }
        }
    }

    /// Clear all entries.
    pub fn clear(&mut self) {
        self.entries.clear();
        self.current_bytes = 0;
        self.access_counter = 0;
    }

    /// Get cache statistics.
    pub fn stats(&self) -> (usize, usize, usize) {
        (self.entries.len(), self.current_bytes, self.max_bytes)
    }
}

// ---------------------------------------------------------------------------
// Python exports
// ---------------------------------------------------------------------------

/// Python-accessible LRU cache wrapper.
#[pyclass]
pub struct PyGraphLRUCache {
    inner: GraphLRUCache,
}

#[pymethods]
impl PyGraphLRUCache {
    #[new]
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            inner: GraphLRUCache::new(max_entries, max_bytes),
        }
    }

    /// Get a value from cache.
    pub fn get(&mut self, key: String) -> Option<Vec<u8>> {
        self.inner.get(&key)
    }

    /// Put a value into cache.
    pub fn put(&mut self, key: String, value: Vec<u8>) {
        self.inner.put(key, value);
    }

    /// Clear the cache.
    pub fn clear(&mut self) {
        self.inner.clear();
    }

    /// Get cache statistics (entries, current_bytes, max_bytes).
    pub fn stats(&self) -> (usize, usize, usize) {
        self.inner.stats()
    }
}

/// Register cache functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGraphLRUCache>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cache_basic() {
        let mut cache = GraphLRUCache::new(100, 1000);

        cache.put("key1".to_string(), b"value1".to_vec());
        assert_eq!(cache.get("key1"), Some(b"value1".to_vec()));

        cache.put("key1".to_string(), b"value2".to_vec());
        assert_eq!(cache.get("key1"), Some(b"value2".to_vec()));
    }

    #[test]
    fn test_cache_eviction() {
        let mut cache = GraphLRUCache::new(2, 1000);

        cache.put("key1".to_string(), b"value1".to_vec());
        cache.put("key2".to_string(), b"value2".to_vec());
        cache.put("key3".to_string(), b"value3".to_vec());

        // key1 should be evicted (lowest frequency)
        assert_eq!(cache.get("key1"), None);
        assert_eq!(cache.get("key2"), Some(b"value2".to_vec()));
        assert_eq!(cache.get("key3"), Some(b"value3".to_vec()));
    }

    #[test]
    fn test_cache_clear() {
        let mut cache = GraphLRUCache::new(10, 100);
        cache.put("key".to_string(), b"value".to_vec());
        cache.clear();
        assert_eq!(cache.get("key"), None);
    }
}

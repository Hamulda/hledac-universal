//! graph_lru.rs — LRU cache s frequency-based admission pro graph operations na M1 8GB
//!
//! unlike graph_traverse/cache.rs (per-worker thread-local):
//! - Shared napříč worker threads (Arc<Mutex<>>)
//! - Pro cross-sprint persistence malých graph result setů
//! - Count-Min Sketch pro frequency estimation (použito pro admission)
//! - LRU eviction (nejstarší entry)
//!
//! M1 8GB bounds:
//!   MAX_ENTRIES = 50_000
//!   MAX_BYTES = 50 * 1024 * 1024 (50 MB)
//!   LOAD_FACTOR = 0.7 (HashMap rehash threshold)

use std::collections::{HashMap, VecDeque};
use std::hash::{Hash, Hasher};
use std::marker::PhantomData;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

/// LRU entry with metadata
#[derive(Clone, Debug)]
struct CacheEntry<V> {
    value: V,
    frequency: u32,       // Access frequency counter (Count-Min)
    last_access: u64,     // Monotonic timestamp
    size_bytes: usize,    // Estimated memory size
}

impl<V: Clone> CacheEntry<V> {
    fn new(value: V, size_bytes: usize, monotonic_counter: &mut u64) -> Self {
        *monotonic_counter += 1;
        Self {
            value,
            frequency: 1,
            last_access: *monotonic_counter,
            size_bytes,
        }
    }

    fn access(&mut self, monotonic_counter: &mut u64) {
        self.frequency = self.frequency.saturating_add(1);
        *monotonic_counter += 1;
        self.last_access = *monotonic_counter;
    }
}

/// Frequency estimator using Count-Min Sketch
/// Provides approximate frequency counts for admission decisions.
struct FrequencyEstimator {
    /// Count-Min Sketch table (4 rows × 8192 buckets)
    sketch: Vec<Vec<u32>>,
    #[allow(dead_code)]
    depth: usize,
    buckets: usize,
    seeds: Vec<u64>,
    #[allow(dead_code)]
    _phantom: PhantomData<ahash::AHasher>,
}

impl FrequencyEstimator {
    fn new() -> Self {
        // 4 hash functions × 8192 buckets = 32KB table
        Self {
            sketch: vec![vec![0u32; 8192]; 4],
            depth: 4,
            buckets: 8192,
            seeds: vec![0x1234_5678, 0xDEAD_BEEF, 0xCAFE_F00D, 0x8BAD_F00D],
            _phantom: PhantomData,
        }
    }

    /// Estimate frequency of an item from Count-Min Sketch
    fn estimate(&self, item: &[u8]) -> u32 {
        self.seeds.iter()
            .enumerate()
            .map(|(i, &seed)| {
                let mut hasher = ahash::AHasher::default();
                hasher.write(item);
                hasher.write_u64(seed);
                let h = hasher.finish();
                let bucket = (h as usize) % self.buckets;
                self.sketch[i][bucket]
            })
            .min()
            .unwrap_or(0)
    }

    /// Record an access (increment counters)
    fn record(&mut self, item: &[u8]) {
        for (i, &seed) in self.seeds.iter().enumerate() {
            let mut hasher = ahash::AHasher::default();
            hasher.write(item);
            hasher.write_u64(seed);
            let h = hasher.finish();
            let bucket = (h as usize) % self.buckets;
            // Conservative update: only increment if this is the minimum
            let min_val = self.sketch.iter()
                .enumerate()
                .filter(|(j, _)| *j != i)
                .map(|(_, row)| row[bucket])
                .min()
                .unwrap_or(u32::MAX);

            if self.sketch[i][bucket] <= min_val {
                self.sketch[i][bucket] = self.sketch[i][bucket].saturating_add(1);
            }
        }
    }
}

impl Default for FrequencyEstimator {
    fn default() -> Self {
        Self::new()
    }
}

/// LRU cache with frequency-based admission
/// Uses Count-Min Sketch for frequency estimation to make admission decisions.
/// Eviction is LRU (oldest entry), NOT frequency-based.
pub struct GraphLRUCache<K: Clone + Hash + Eq, V: Clone> {
    /// HashMap for O(1) lookup
    entries: HashMap<K, CacheEntry<V>>,
    /// LRU order queue (oldest first)
    lru_order: VecDeque<K>,
    /// Frequency estimator for admission decisions
    freq_estimator: FrequencyEstimator,
    /// Maximum entries
    max_entries: usize,
    /// Maximum bytes
    max_bytes: usize,
    /// Current byte size
    current_bytes: usize,
    /// Monotonic counter for ordering
    counter: u64,
}

impl<K: Clone + Hash + Eq + std::fmt::Display, V: Clone> GraphLRUCache<K, V> {
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            entries: HashMap::with_capacity(
                (max_entries as f64 * 1.2) as usize,
            ),
            lru_order: VecDeque::with_capacity(max_entries),
            freq_estimator: FrequencyEstimator::new(),
            max_entries,
            max_bytes,
            current_bytes: 0,
            counter: 0,
        }
    }

    /// Estimate size of a value in bytes
    fn estimate_size(value: &V) -> usize {
        // Conservative estimate: 100 bytes base + pointer size
        std::mem::size_of_val(value) + 100
    }

    /// Get an entry or insert via factory
    pub fn get_or_insert<F>(&mut self, key: &K, factory: F) -> V
    where
        F: FnOnce() -> V,
    {
        let key_bytes = key.to_string();

        if let Some(entry) = self.entries.get_mut(key) {
            entry.access(&mut self.counter);
            self.freq_estimator.record(key_bytes.as_bytes());
            return entry.value.clone();
        }

        // Cache miss
        let value = factory();
        let size = Self::estimate_size(&value);

        // Frequency-based admission check
        // Compare new item's historical frequency vs minimum frequency in cache
        let new_freq = self.freq_estimator.estimate(key_bytes.as_bytes());
        let current_min_freq = self.entries.values()
            .map(|e| e.frequency)
            .min()
            .unwrap_or(0);

        // Admit if new item has equal or higher frequency than least frequent in cache
        if new_freq < current_min_freq && !self.entries.is_empty() {
            // Reject: too infrequent (new item has no history AND cache is full of frequent items)
            return value;
        }

        // Evict if necessary
        self.evict_until(size).ok();

        // Insert new entry
        let key_clone = key.clone();
        let entry = CacheEntry::new(value.clone(), size, &mut self.counter);
        self.current_bytes += size;

        self.entries.insert(key_clone.clone(), entry);
        self.lru_order.push_back(key_clone);
        self.freq_estimator.record(key_bytes.as_bytes());

        value
    }

    /// Evict entries until we have space (LRU eviction)
    fn evict_until(&mut self, needed_bytes: usize) -> Result<(), ()> {
        while self.entries.len() >= self.max_entries
            || self.current_bytes + needed_bytes > self.max_bytes
        {
            if self.lru_order.is_empty() {
                return Err(());
            }

            // Pop LRU entry (oldest first)
            if let Some(old_key) = self.lru_order.pop_front() {
                if let Some(entry) = self.entries.remove(&old_key) {
                    self.current_bytes = self.current_bytes.saturating_sub(entry.size_bytes);
                }
            } else {
                break;
            }
        }
        Ok(())
    }

    /// Get current size
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Check if empty
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Clear the cache
    pub fn clear(&mut self) {
        self.entries.clear();
        self.lru_order.clear();
        self.current_bytes = 0;
        self.counter = 0;
        self.freq_estimator = FrequencyEstimator::new();
    }
}

// Python wrapper
#[pyclass]
pub struct PyGraphLRUCache {
    cache: Arc<Mutex<GraphLRUCache<String, Vec<u8>>>>,
}

#[pymethods]
impl PyGraphLRUCache {
    #[new]
    fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            cache: Arc::new(Mutex::new(
                GraphLRUCache::new(max_entries, max_bytes)
            )),
        }
    }

    fn get(&self, key: String) -> Option<Vec<u8>> {
        self.cache.lock().unwrap()
            .entries.get(&key)
            .map(|e| e.value.clone())
    }

    fn put(&self, key: String, value: Vec<u8>) -> bool {
        let mut cache = self.cache.lock().unwrap();
        let size = GraphLRUCache::<String, Vec<u8>>::estimate_size(&value);
        let key_bytes = key.clone();

        // Frequency-based admission check
        let new_freq = cache.freq_estimator.estimate(key_bytes.as_bytes());
        let current_min = cache.entries.values()
            .map(|e| e.frequency)
            .min()
            .unwrap_or(0);

        if new_freq < current_min && !cache.entries.is_empty() {
            return false;
        }

        // Evict if needed
        cache.evict_until(size).ok();

        let entry = CacheEntry::new(value, size, &mut cache.counter);
        cache.current_bytes += size;
        let key_for_lru = key.clone();
        cache.entries.insert(key, entry);
        cache.lru_order.push_back(key_for_lru);
        cache.freq_estimator.record(key_bytes.as_bytes());

        true
    }

    fn len(&self) -> usize {
        self.cache.lock().unwrap().len()
    }

    fn is_empty(&self) -> bool {
        self.cache.lock().unwrap().is_empty()
    }

    fn clear(&self) {
        self.cache.lock().unwrap().clear();
    }

    fn stats(&self) -> HashMap<String, usize> {
        let cache = self.cache.lock().unwrap();
        let mut stats = HashMap::new();
        stats.insert("entries".to_string(), cache.entries.len());
        stats.insert("bytes".to_string(), cache.current_bytes);
        stats.insert("max_entries".to_string(), cache.max_entries);
        stats.insert("max_bytes".to_string(), cache.max_bytes);
        stats
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frequency_admission() {
        let mut est = FrequencyEstimator::new();

        // First item always has 0 frequency
        let freq1 = est.estimate(b"item1");
        assert_eq!(freq1, 0);

        est.record(b"item1");
        est.record(b"item1");

        // After recording, should have higher estimate
        let freq1_after = est.estimate(b"item1");
        assert!(freq1_after > 0);
    }

    #[test]
    fn test_lru_cache() {
        let mut cache: GraphLRUCache<String, Vec<u8>> =
            GraphLRUCache::new(3, 1024 * 1024);

        cache.get_or_insert(&"k1".to_string(), || vec![1, 2, 3]);
        cache.get_or_insert(&"k2".to_string(), || vec![4, 5, 6]);
        cache.get_or_insert(&"k3".to_string(), || vec![7, 8, 9]);

        assert_eq!(cache.len(), 3);

        // Access k1 to make it recent
        cache.get_or_insert(&"k1".to_string(), || vec![1, 2, 3]);

        // Add k4, should evict k2 (least recent)
        cache.get_or_insert(&"k4".to_string(), || vec![10, 11, 12]);

        assert_eq!(cache.len(), 3);
        assert!(cache.entries.contains_key(&"k1".to_string()));
        assert!(!cache.entries.contains_key(&"k2".to_string()));
    }

    #[test]
    fn test_size_estimation() {
        let v: Vec<u8> = (0..100).collect();
        let size = GraphLRUCache::<String, Vec<u8>>::estimate_size(&v);
        assert!(size >= 100);
    }
}

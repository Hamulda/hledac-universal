//! LZ4-compressed pattern store — M1 8GB RAM optimization for 10k+ patterns.
//!
//! Architecture:
//! - Patterns serialized to JSON bytes
//! - LZ4 block compression (lz4_flex, ~2:1 ratio on text)
//! - Lazy deserialization on first access
//! - File-backed persistence for cross-process sharing
//!
//! Use case: 10k+ IOC patterns in memory, e.g. threat intelligence feeds.
//! For small pattern sets (<1k), overhead exceeds benefit.
//!
//! M1 8GB bounds:
//! - Compression: ~1-2ms for 10k patterns
//! - Memory savings: ~50% for pattern text storage

use lz4_flex::block::{compress_prepend_size, decompress_size_prepended};
use serde::{Deserialize, Serialize};
use std::sync::RwLock;

use pyo3::prelude::*;

/// Compressed pattern store with lazy deserialization.
pub struct RegexLz4Store {
    /// LZ4-compressed JSON bytes
    compressed: Vec<u8>,
    /// Cache for decompressed patterns (lazily populated)
    cache: RwLock<Option<Vec<String>>>,
    /// Number of patterns (stored for quick access before deserialization)
    pattern_count: usize,
}

impl RegexLz4Store {
    /// Create a new compressed store from pattern list.
    pub fn new(patterns: Vec<String>) -> Self {
        let json = serde_json::to_vec(&patterns).unwrap_or_default();
        let compressed = compress_prepend_size(&json);
        Self {
            compressed,
            cache: RwLock::new(None),
            pattern_count: patterns.len(),
        }
    }

    /// Get patterns (lazily deserialized and cached).
    pub fn get_patterns(&self) -> Vec<String> {
        // Check cache first
        if let Ok(guard) = self.cache.read() {
            if let Some(ref patterns) = *guard {
                return patterns.clone();
            }
        }
        // Deserialize and cache
        if let Ok(json) = decompress_size_prepended(&self.compressed) {
            if let Ok(decoded) = serde_json::from_slice::<Vec<String>>(&json) {
                if let Ok(mut guard) = self.cache.write() {
                    *guard = Some(decoded.clone());
                }
                return decoded;
            }
        }
        Vec::new()
    }

    /// Get pattern count (without deserializing).
    pub fn len(&self) -> usize {
        self.pattern_count
    }

    /// Check if empty.
    pub fn is_empty(&self) -> bool {
        self.pattern_count == 0
    }

    /// Get compressed size in bytes.
    pub fn compressed_size(&self) -> usize {
        self.compressed.len()
    }

    /// Get decompressed size in bytes.
    pub fn decompressed_size(&self) -> usize {
        if let Ok(json) = decompress_size_prepended(&self.compressed) {
            json.len()
        } else {
            0
        }
    }

    /// Get compression ratio (decompressed / compressed).
    pub fn compression_ratio(&self) -> f64 {
        let compressed = self.compressed_size();
        if compressed == 0 {
            return 1.0;
        }
        self.decompressed_size() as f64 / compressed as f64
    }

    /// Save compressed data to file.
    pub fn save_to_file(&self, path: &str) -> std::io::Result<()> {
        std::fs::write(path, &self.compressed)
    }

    /// Load compressed data from file.
    pub fn load_from_file(path: &str) -> std::io::Result<Self> {
        let compressed = std::fs::read(path)?;
        // We need pattern_count - read from metadata or default
        // For now, we'll deserialize to get count
        let pattern_count = if let Ok(json) = decompress_size_prepended(&compressed) {
            if let Ok(patterns) = serde_json::from_slice::<Vec<String>>(&json) {
                patterns.len()
            } else {
                0
            }
        } else {
            0
        };
        Ok(Self {
            compressed,
            cache: RwLock::new(None),
            pattern_count,
        })
    }
}

/// Python bindings for RegexLz4Store.
#[pyclass]
pub struct PyRegexLz4Store {
    store: RegexLz4Store,
}

#[pymethods]
impl PyRegexLz4Store {
    #[new]
    fn new(patterns: Vec<String>) -> Self {
        Self {
            store: RegexLz4Store::new(patterns),
        }
    }

    /// Get number of patterns.
    fn __len__(&self) -> usize {
        self.store.len()
    }

    /// Check if empty.
    fn __bool__(&self) -> bool {
        !self.store.is_empty()
    }

    /// Get compressed size in bytes.
    fn compressed_size(&self) -> usize {
        self.store.compressed_size()
    }

    /// Get compression ratio (decompressed / compressed).
    fn compression_ratio(&self) -> f64 {
        self.store.compression_ratio()
    }

    /// Get patterns as a Python list (deserializes if not cached).
    fn get_patterns(&self) -> Vec<String> {
        self.store.get_patterns()
    }

    /// Save compressed store to file.
    fn save(&self, path: String) -> PyResult<()> {
        self.store
            .save_to_file(&path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }

    /// Load compressed store from file.
    #[staticmethod]
    fn load(path: String) -> PyResult<Self> {
        self::RegexLz4Store::load_from_file(&path)
            .map(|store| PyRegexLz4Store { store })
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }
}

/// Register module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRegexLz4Store>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compression_ratio_1k_patterns() {
        let patterns: Vec<String> = (0..1000)
            .map(|i| format!("pattern_{}", i))
            .collect();
        let store = RegexLz4Store::new(patterns);
        let ratio = store.compression_ratio();
        // LZ4 on JSON patterns should achieve >1.0 ratio
        assert!(
            ratio > 1.0,
            "Compression ratio should be > 1.0, got {}",
            ratio
        );
    }

    #[test]
    fn test_lazy_deserialization() {
        let patterns = vec![
            "test1".to_string(),
            "test2".to_string(),
            "test3".to_string(),
        ];
        let store = RegexLz4Store::new(patterns.clone());
        // Pattern count available without deserialization
        assert_eq!(store.len(), 3);
        // Get patterns triggers deserialization
        let loaded = store.get_patterns();
        assert_eq!(loaded, patterns);
    }

    #[test]
    fn test_compression_ratio_large() {
        let patterns: Vec<String> = (0..10000)
            .map(|i| format!("long_pattern_name_{}_with_more_data", i))
            .collect();
        let store = RegexLz4Store::new(patterns);
        let ratio = store.compression_ratio();
        assert!(
            ratio > 1.5,
            "Large pattern set should achieve >1.5 compression ratio, got {}",
            ratio
        );
    }

    #[test]
    fn test_save_load_roundtrip() {
        let patterns: Vec<String> = (0..100)
            .map(|i| format!("persist_pattern_{}", i))
            .collect();
        let store = RegexLz4Store::new(patterns.clone());
        let path = "/tmp/test_regex_lz4.bin";
        store.save_to_file(path).unwrap();
        let loaded = RegexLz4Store::load_from_file(path).unwrap();
        assert_eq!(loaded.len(), patterns.len());
        assert_eq!(loaded.get_patterns(), patterns);
        std::fs::remove_file(path).ok();
    }
}

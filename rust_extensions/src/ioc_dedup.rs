//! High-performance IOC deduplication store.
//!
//! Deduplicates IOC values across sprints with normalization support.
//! Uses ahash for fast HashMap operations and xxh3-64 for key hashing.

use ahash::AHashMap;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use xxhash_rust::xxh3::xxh3_64;

/// IOC types matching ioc_extract.rs classification
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IocType {
    Ip,
    Ipv6,
    Domain,
    Url,
    Md5,
    Sha1,
    Sha256,
    Email,
    Cve,
    Unknown,
}

impl IocType {
    fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "ip" | "ipv4" => IocType::Ip,
            "ipv6" => IocType::Ipv6,
            "domain" | "fqdn" => IocType::Domain,
            "url" => IocType::Url,
            "md5" => IocType::Md5,
            "sha1" => IocType::Sha1,
            "sha256" | "sha2" => IocType::Sha256,
            "email" => IocType::Email,
            "cve" => IocType::Cve,
            _ => IocType::Unknown,
        }
    }
}

/// Normalizes IOC value according to type rules.
fn normalize_ioc(value: &str, ioc_type: &IocType) -> String {
    if value.is_empty() {
        return String::new();
    }

    match ioc_type {
        IocType::Domain => {
            let lower = value.to_lowercase();
            lower.strip_prefix("www.").unwrap_or(&lower).to_string()
        }
        IocType::Md5 | IocType::Sha1 | IocType::Sha256 => value.to_lowercase(),
        IocType::Cve => value.to_uppercase(),
        IocType::Ip => {
            value
                .split('.')
                .map(|octet| {
                    octet
                        .parse::<u8>()
                        .map(|n| n.to_string())
                        .unwrap_or_else(|_| octet.to_string())
                })
                .collect::<Vec<_>>()
                .join(".")
        }
        IocType::Ipv6 => value.to_lowercase(),
        _ => value.to_string(),
    }
}

/// Metadata about an IOC entry
#[derive(Debug, Clone)]
struct IocEntry {
    normalized_value: String,
    ioc_type: IocType,
    first_seen_sprint: u32,
    last_seen_sprint: u32,
    occurrence_count: u32,
    confidence_max: f32,
}

/// High-performance IOC deduplication store.
#[pyclass]
pub struct IocDedupStore {
    entries: AHashMap<u64, IocEntry>,
    current_sprint: u32,
    total_seen: u64,
    total_deduped: u64,
}

#[pymethods]
impl IocDedupStore {
    #[new]
    #[pyo3(signature = (sprint_id = 0))]
    pub fn new(sprint_id: u32) -> Self {
        Self {
            entries: AHashMap::with_capacity(50_000),
            current_sprint: sprint_id,
            total_seen: 0,
            total_deduped: 0,
        }
    }

    /// Add IOC — returns True if NEW, False if duplicate.
    #[pyo3(signature = (value, ioc_type_str, confidence = 0.5))]
    pub fn add(&mut self, value: &str, ioc_type_str: &str, confidence: f32) -> bool {
        self.total_seen += 1;
        if value.is_empty() {
            return false;
        }

        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());

        if let Some(entry) = self.entries.get_mut(&key) {
            entry.last_seen_sprint = self.current_sprint;
            entry.occurrence_count += 1;
            if confidence > entry.confidence_max {
                entry.confidence_max = confidence;
            }
            self.total_deduped += 1;
            false
        } else {
            self.entries.insert(key, IocEntry {
                normalized_value: normalized,
                ioc_type,
                first_seen_sprint: self.current_sprint,
                last_seen_sprint: self.current_sprint,
                occurrence_count: 1,
                confidence_max: confidence,
            });
            true
        }
    }

    /// Batch add — returns list of bool (True = new).
    pub fn add_batch(&mut self, items: Vec<(String, String, f32)>) -> Vec<bool> {
        let mut results = Vec::with_capacity(items.len());
        for (value, ioc_type_str, confidence) in items {
            results.push(self.add(&value, &ioc_type_str, confidence));
        }
        results
    }

    /// Check if IOC exists.
    pub fn contains(&self, value: &str, ioc_type_str: &str) -> bool {
        if value.is_empty() {
            return false;
        }
        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());
        self.entries.contains_key(&key)
    }

    /// Advance to next sprint.
    pub fn advance_sprint(&mut self, new_sprint_id: u32) {
        self.current_sprint = new_sprint_id;
    }

    pub fn len(&self) -> usize { self.entries.len() }
    pub fn is_empty(&self) -> bool { self.entries.is_empty() }

    /// Returns (total_seen, total_deduped, unique_count).
    pub fn stats(&self) -> (u64, u64, u64) {
        (self.total_seen, self.total_deduped, self.entries.len() as u64)
    }

    /// Get all IOC values of specified type.
    pub fn get_by_type(&self, ioc_type_str: &str) -> Vec<String> {
        let target_type = IocType::from_str(ioc_type_str);
        self.entries
            .values()
            .filter(|e| e.ioc_type == target_type)
            .map(|e| e.normalized_value.clone())
            .collect()
    }

    /// Get entries with full metadata: (value, first_sprint, last_sprint, count, confidence).
    pub fn get_entries_by_type(&self, ioc_type_str: &str) -> Vec<(String, u32, u32, u32, f32)> {
        let target_type = IocType::from_str(ioc_type_str);
        self.entries
            .values()
            .filter(|e| e.ioc_type == target_type)
            .map(|e| (
                e.normalized_value.clone(),
                e.first_seen_sprint,
                e.last_seen_sprint,
                e.occurrence_count,
                e.confidence_max,
            ))
            .collect()
    }

    /// Serialize state - returns binary bytes for simple persistence.
    pub fn get_state_bytes(&self) -> Vec<u8> {
        // Simple binary format: entries count (4 bytes) + entries + counters
        let mut bytes = Vec::with_capacity(1024);

        // Write entries
        let count = self.entries.len() as u32;
        bytes.extend_from_slice(&count.to_le_bytes());

        for (k, e) in self.entries.iter() {
            bytes.extend_from_slice(&k.to_le_bytes());
            let val_bytes = e.normalized_value.as_bytes();
            bytes.extend_from_slice(&(val_bytes.len() as u32).to_le_bytes());
            bytes.extend_from_slice(val_bytes);
            bytes.push(e.ioc_type as u8);
            bytes.extend_from_slice(&e.first_seen_sprint.to_le_bytes());
            bytes.extend_from_slice(&e.last_seen_sprint.to_le_bytes());
            bytes.extend_from_slice(&e.occurrence_count.to_le_bytes());
            bytes.extend_from_slice(&e.confidence_max.to_le_bytes());
        }

        // Write counters
        bytes.extend_from_slice(&self.current_sprint.to_le_bytes());
        bytes.extend_from_slice(&self.total_seen.to_le_bytes());
        bytes.extend_from_slice(&self.total_deduped.to_le_bytes());

        bytes
    }

    /// Restore state from binary bytes.
    pub fn set_state_from_bytes(&mut self, data: &[u8]) -> bool {
        if data.len() < 4 {
            return false;
        }

        let mut pos = 0;
        let count = u32::from_le_bytes([data[0], data[1], data[2], data[3]]);
        pos += 4;

        for _ in 0..count {
            if pos + 4 > data.len() { return false; }
            let k = u64::from_le_bytes([
                data[pos], data[pos+1], data[pos+2], data[pos+3],
                data[pos+4], data[pos+5], data[pos+6], data[pos+7]
            ]);
            pos += 8;

            if pos + 4 > data.len() { return false; }
            let val_len = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]) as usize;
            pos += 4;

            if pos + val_len > data.len() { return false; }
            let normalized = String::from_utf8_lossy(&data[pos..pos+val_len]).to_string();
            pos += val_len;

            if pos >= data.len() { return false; }
            let ioc_type_byte = data[pos];
            pos += 1;

            let ioc_type = match ioc_type_byte {
                0 => IocType::Ip,
                1 => IocType::Ipv6,
                2 => IocType::Domain,
                3 => IocType::Url,
                4 => IocType::Md5,
                5 => IocType::Sha1,
                6 => IocType::Sha256,
                7 => IocType::Email,
                8 => IocType::Cve,
                _ => IocType::Unknown,
            };

            if pos + 16 > data.len() { return false; }
            let first = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]);
            let last = u32::from_le_bytes([data[pos+4], data[pos+5], data[pos+6], data[pos+7]]);
            let occurrence = u32::from_le_bytes([data[pos+8], data[pos+9], data[pos+10], data[pos+11]]);
            let conf_bytes = [data[pos+12], data[pos+13], data[pos+14], data[pos+15]];
            let confidence = f32::from_le_bytes(conf_bytes);
            pos += 16;

            self.entries.insert(k, IocEntry {
                normalized_value: normalized,
                ioc_type,
                first_seen_sprint: first,
                last_seen_sprint: last,
                occurrence_count: occurrence,
                confidence_max: confidence,
            });
        }

        if pos + 12 > data.len() { return false; }
        self.current_sprint = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]);
        self.total_seen = u64::from_le_bytes([
            data[pos+4], data[pos+5], data[pos+6], data[pos+7],
            data[pos+8], data[pos+9], data[pos+10], data[pos+11]
        ]);
        self.total_deduped = u64::from_le_bytes([
            data[pos+12], data[pos+13], data[pos+14], data[pos+15],
            data[pos+16], data[pos+17], data[pos+18], data[pos+19]
        ]);

        true
    }

    /// Pickle compatibility - returns bytes for pickle.
    #[allow(clippy::incorrect_clone_on_copy)]
    pub fn __getstate__<'py>(&self, py: Python<'py>) -> Py<PyBytes> {
        PyBytes::new(py, &self.get_state_bytes()).into()
    }

    pub fn __setstate__(&mut self, _py: Python<'_>, state: &Bound<'_, PyBytes>) -> PyResult<()> {
        let bytes = state.as_bytes();
        if !self.set_state_from_bytes(bytes) {
            return Err(pyo3::exceptions::PyValueError::new_err("Invalid state data"));
        }
        Ok(())
    }

    pub fn clear(&mut self) {
        self.entries.clear();
        self.total_seen = 0;
        self.total_deduped = 0;
    }

    pub fn get_sprint(&self) -> u32 { self.current_sprint }

    /// Get raw bytes for external persistence (e.g., LMDB).
    pub fn to_bytes(&self) -> Vec<u8> {
        self.get_state_bytes()
    }
}

/// Create IocDedupStore from raw bytes.
#[pyfunction]
pub fn ioc_dedup_from_bytes(data: Vec<u8>) -> PyResult<IocDedupStore> {
    let mut store = IocDedupStore::new(0);
    if !store.set_state_from_bytes(&data) {
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid state data"));
    }
    Ok(store)
}

/// Register IocDedupStore with Python module.
pub fn register_class(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<IocDedupStore>()?;
    m.add_function(wrap_pyfunction!(ioc_dedup_from_bytes, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_domain_normalization() {
        assert_eq!(normalize_ioc("WWW.EXAMPLE.COM", &IocType::Domain), "example.com");
        assert_eq!(normalize_ioc("www.example.org", &IocType::Domain), "example.org");
    }

    #[test]
    fn test_hash_normalization() {
        assert_eq!(normalize_ioc("ABC123DEF456", &IocType::Md5), "abc123def456");
        assert_eq!(normalize_ioc("cve-2024-12345", &IocType::Cve), "CVE-2024-12345");
    }

    #[test]
    fn test_ip_normalization() {
        assert_eq!(normalize_ioc("192.168.001.001", &IocType::Ip), "192.168.1.1");
    }

    #[test]
    fn test_ioc_dedup_store() {
        let mut store = IocDedupStore::new(1);
        assert!(store.add("evil.com", "domain", 0.9));
        assert!(!store.add("evil.com", "domain", 0.95)); // dup
        assert!(store.add("evil.com", "url", 0.8)); // different type
        assert_eq!(store.len(), 2);
        assert_eq!(store.stats(), (3, 1, 2));
    }

    #[test]
    fn test_batch_add() {
        let mut store = IocDedupStore::new(1);
        let results = store.add_batch(vec![
            ("domain1.com".to_string(), "domain".to_string(), 0.9),
            ("domain1.com".to_string(), "domain".to_string(), 0.8), // dup
        ]);
        assert_eq!(results, vec![true, false]);
    }
}

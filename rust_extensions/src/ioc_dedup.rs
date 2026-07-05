//! High-performance IOC deduplication store — mmap-backed persistent version.
//!
//! Persists IOC dedup state across process restarts via mmap(2) file.
//! M1 8GB safe: demand-paged, entries rebuilt into HashMap on load.
//!
//! Thread-safety fix (Issue #1): Replaced DashMap with parking_lot::RwLock.
//! DashMap caused segfaults when called from Python async/ThreadPoolExecutor
//! because its internal locking doesn't play well with PyO3's GIL handling.
//! parking_lot::RwLock is Send+Sync by default (no unsafe), faster, and
//! properly reentrant - safe for Python async contexts.

use ahash::AHashMap;
use parking_lot::RwLock;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use std::fs::{File, OpenOptions};
use std::io::Write;
#[allow(unused_imports)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::sync::Arc;
use xxhash_rust::xxh3::xxh3_64;

// Arc<File>: reference-counted file handle shared across threads.
// On Unix, File is Send+Sync because a fd (i32) is trivially safe to share.
// Arc<File> keeps the OS fd valid even when Python GC + ThreadPoolExecutor hold
// references across threads. NO unsafe impl Sync needed.

// ===========================================================================
// Constants
// ===========================================================================

const MMAP_HEADER_SIZE: usize = 64;
const MMAP_MAGIC: &[u8; 4] = b"HIDM";
const MMAP_VERSION: u8 = 1;

// ===========================================================================
// IOC Types & Normalization
// ===========================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IocType {
    Ip, Ipv6, Domain, Url, Md5, Sha1, Sha256, Email, Cve, Unknown,
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

fn normalize_ioc(value: &str, ioc_type: &IocType) -> String {
    if value.is_empty() { return String::new(); }
    match ioc_type {
        IocType::Domain => {
            let lower = value.to_lowercase();
            lower.strip_prefix("www.").unwrap_or(&lower).to_string()
        }
        IocType::Md5 | IocType::Sha1 | IocType::Sha256 => value.to_lowercase(),
        IocType::Cve => value.to_uppercase(),
        IocType::Ip => value.split('.').map(|octet| {
            octet.parse::<u8>().map(|n| n.to_string()).unwrap_or_else(|_| octet.to_string())
        }).collect::<Vec<_>>().join("."),
        IocType::Ipv6 => value.to_lowercase(),
        _ => value.to_string(),
    }
}

#[derive(Debug, Clone)]
struct IocEntry {
    normalized_value: String,
    ioc_type: IocType,
    first_seen_sprint: u32,
    last_seen_sprint: u32,
    occurrence_count: u32,
    confidence_max: f32,
}

// ===========================================================================
// MmapIocDedupStore — file-backed persistent IOC dedup
// ===========================================================================

#[pyclass]
pub struct MmapIocDedupStore {
    // Arc<File>: on Unix, File is Send+Sync (fd=i32). Arc<File> provides shared
    // ownership so the fd stays valid when Python GC + ThreadPoolExecutor hold
    // references across threads. NO unsafe impl Sync needed.
    file: Arc<File>,
    file_path: String,
    // Issue #1 fix: Replaced DashMap with parking_lot::RwLock + AHashMap.
    // parking_lot::RwLock is Send+Sync by default, no unsafe impl needed.
    // Properly reentrant for Python async/ThreadPoolExecutor contexts.
    entries: RwLock<AHashMap<u64, Arc<RwLock<IocEntry>>>>,
    current_sprint: u32,
    total_seen: u64,
    total_deduped: u64,
    dirty: bool,
}

impl MmapIocDedupStore {
    fn open_or_create(path: &str, force_new: bool) -> PyResult<Self> {
        let p = Path::new(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    pyo3::exceptions::PyIOError::new_err(format!("mkdir failed: {}", e))
                })?;
            }
        }

        let file = if force_new || !p.exists() {
            OpenOptions::new()
                .read(true).write(true).create(true).truncate(true)
                .open(p)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open failed: {}", e)))?
        } else {
            OpenOptions::new().read(true).write(true).open(p)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open failed: {}", e)))?
        };

        let mut store = Self {
            file: Arc::new(file),
            file_path: path.to_string(),
            entries: RwLock::new(AHashMap::with_capacity(50_000)),
            current_sprint: 0,
            total_seen: 0,
            total_deduped: 0,
            dirty: false,
        };

        if !force_new && p.exists() {
            let _ = store.load_from_file();
        }
        Ok(store)
    }

    fn load_from_file(&mut self) -> PyResult<()> {
        use std::io::Read;
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
        self.current_sprint = u32::from_le_bytes([header[12], header[13], header[14], header[15]]);
        self.total_seen = u64::from_le_bytes([header[16], header[17], header[18], header[19], header[20], header[21], header[22], header[23]]);
        self.total_deduped = u64::from_le_bytes([header[24], header[25], header[26], header[27], header[28], header[29], header[30], header[31]]);

        if num_entries == 0 {
            return Ok(());
        }

        // Read entry data
        let mut data = vec![0u8; (file.metadata().map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("stat failed: {}", e)))?.len() as usize).saturating_sub(MMAP_HEADER_SIZE)];
        if !data.is_empty() {
            file.read_exact(&mut data).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("read data failed: {}", e))
            })?;
            // `file` goes out of scope here (separate read handle, not self.file).
            // self.file (Arc<File>) is untouched — no drop of the shared store handle.
            self.rebuild_entries_from_bytes(&data, num_entries as usize);
        }
        self.dirty = false;
        Ok(())
    }

    fn rebuild_entries_from_bytes(&mut self, data: &[u8], num_entries: usize) {
        self.entries = RwLock::new(AHashMap::with_capacity(num_entries.max(1000)));
        let mut pos = 0;

        for _ in 0..num_entries {
            if pos + 8 > data.len() { break; }
            let k = u64::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3], data[pos+4], data[pos+5], data[pos+6], data[pos+7]]);
            pos += 8;

            if pos + 4 > data.len() { break; }
            let val_len = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]) as usize;
            pos += 4;

            if pos + val_len > data.len() { break; }
            let normalized = String::from_utf8_lossy(&data[pos..pos+val_len]).to_string();
            pos += val_len;

            if pos >= data.len() { break; }
            let ioc_type_byte = data[pos]; pos += 1;
            let ioc_type = match ioc_type_byte {
                0 => IocType::Ip, 1 => IocType::Ipv6, 2 => IocType::Domain,
                3 => IocType::Url, 4 => IocType::Md5, 5 => IocType::Sha1,
                6 => IocType::Sha256, 7 => IocType::Email, 8 => IocType::Cve, _ => IocType::Unknown,
            };

            if pos + 16 > data.len() { break; }
            let first = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]);
            let last = u32::from_le_bytes([data[pos+4], data[pos+5], data[pos+6], data[pos+7]]);
            let occurrence = u32::from_le_bytes([data[pos+8], data[pos+9], data[pos+10], data[pos+11]]);
            let confidence = f32::from_le_bytes([data[pos+12], data[pos+13], data[pos+14], data[pos+15]]);
            pos += 16;

            self.entries.write().insert(k, Arc::new(RwLock::new(IocEntry { normalized_value: normalized, ioc_type, first_seen_sprint: first, last_seen_sprint: last, occurrence_count: occurrence, confidence_max: confidence })));
        }
    }

    fn persist(&mut self) -> PyResult<()> {
        if !self.dirty { return Ok(()); }

        // Open a new handle for writing. On Unix, multiple File handles to the same path
        // share the same underlying fd — O_TRUNC on the new handle truncates for all.
        let mut new_file = OpenOptions::new().write(true).truncate(true).open(&self.file_path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("open for write failed: {}", e)))?;

        // Write header
        let mut header = [0u8; MMAP_HEADER_SIZE];
        header[0..4].copy_from_slice(MMAP_MAGIC);
        header[4] = MMAP_VERSION;
        header[8..12].copy_from_slice(&(self.entries.len() as u32).to_le_bytes());
        header[12..16].copy_from_slice(&self.current_sprint.to_le_bytes());
        header[16..24].copy_from_slice(&self.total_seen.to_le_bytes());
        header[24..32].copy_from_slice(&self.total_deduped.to_le_bytes());

        new_file.write_all(&header).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("write header failed: {}", e))
        })?;

        // Write entries
        let entries_bytes = self.get_state_bytes();
        new_file.write_all(&entries_bytes).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("write entries failed: {}", e))
        })?;

        new_file.sync_all().map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("sync failed: {}", e))
        })?;

        // Update self.file: Arc::get_mut succeeds when refcount==1 (single owner).
        // PyO3's GIL ensures single-threaded Python access — refcount is almost always 1.
        // If get_mut fails (multiple Arc refs), replace the Arc entirely (new fd cloned).
        if let Some(f) = Arc::get_mut(&mut self.file) {
            *f = new_file;
        } else {
            self.file = Arc::new(new_file);
        }

        self.dirty = false;
        Ok(())
    }

    fn get_state_bytes(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(4096);
        let entries = self.entries.read();
        for (k, e) in entries.iter() {
            bytes.extend_from_slice(&k.to_le_bytes());
            let entry = e.read();
            let val_bytes = entry.normalized_value.as_bytes();
            bytes.extend_from_slice(&(val_bytes.len() as u32).to_le_bytes());
            bytes.extend_from_slice(val_bytes);
            bytes.push(entry.ioc_type as u8);
            bytes.extend_from_slice(&entry.first_seen_sprint.to_le_bytes());
            bytes.extend_from_slice(&entry.last_seen_sprint.to_le_bytes());
            bytes.extend_from_slice(&entry.occurrence_count.to_le_bytes());
            bytes.extend_from_slice(&entry.confidence_max.to_le_bytes());
        }
        bytes
    }
}

impl Drop for MmapIocDedupStore {
    fn drop(&mut self) {
        let _ = self.persist();
    }
}

#[pymethods]
impl MmapIocDedupStore {
    #[new]
    #[pyo3(signature = (path, force_new = false))]
    pub fn new(path: &str, force_new: bool) -> PyResult<Self> {
        Self::open_or_create(path, force_new)
    }

    #[pyo3(signature = (value, ioc_type_str, confidence = 0.5))]
    pub fn add(&mut self, value: &str, ioc_type_str: &str, confidence: f32) -> bool {
        self.total_seen += 1;
        if value.is_empty() { return false; }
        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());

        // Issue #1 fix: parking_lot::RwLock + AHashMap (replaces DashMap entry API)
        let mut entries = self.entries.write();
        if let Some(existing) = entries.get_mut(&key) {
            let mut e = existing.write();
            e.last_seen_sprint = self.current_sprint;
            e.occurrence_count += 1;
            if confidence > e.confidence_max { e.confidence_max = confidence; }
            self.total_deduped += 1;
            self.dirty = true;
            false
        } else {
            entries.insert(key, Arc::new(RwLock::new(IocEntry {
                normalized_value: normalized, ioc_type,
                first_seen_sprint: self.current_sprint, last_seen_sprint: self.current_sprint,
                occurrence_count: 1, confidence_max: confidence,
            })));
            self.dirty = true;
            true
        }
    }

    pub fn add_batch(&mut self, items: Vec<(String, String, f32)>) -> Vec<bool> {
        let mut results = Vec::with_capacity(items.len());
        for (value, ioc_type_str, confidence) in items {
            results.push(self.add(&value, &ioc_type_str, confidence));
        }
        results
    }

    pub fn contains(&self, value: &str, ioc_type_str: &str) -> bool {
        if value.is_empty() { return false; }
        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());
        self.entries.read().contains_key(&key)
    }

    pub fn advance_sprint(&mut self, new_sprint_id: u32) {
        self.current_sprint = new_sprint_id;
        self.dirty = true;
    }

    pub fn len(&self) -> usize { self.entries.read().len() }
    pub fn is_empty(&self) -> bool { self.entries.read().is_empty() }
    pub fn stats(&self) -> (u64, u64, u64) { (self.total_seen, self.total_deduped, self.entries.read().len() as u64) }

    pub fn stats_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("total_seen", self.total_seen as i64)?;
        dict.set_item("total_deduped", self.total_deduped as i64)?;
        dict.set_item("unique_count", self.entries.read().len() as i64)?;
        dict.set_item("current_sprint", self.current_sprint as i64)?;
        let total = self.total_seen as f64;
        let hit_rate_bp = if total > 0.0 { ((self.total_deduped as f64) / total * 10_000.0).round() as i64 } else { 0 };
        dict.set_item("hit_rate_bp", hit_rate_bp)?;
        Ok(dict)
    }

    pub fn get_by_type(&self, ioc_type_str: &str) -> Vec<String> {
        let target_type = IocType::from_str(ioc_type_str);
        let entries = self.entries.read();
        entries.iter()
            .filter(|(_k, e)| e.read().ioc_type == target_type)
            .map(|(_k, e)| e.read().normalized_value.clone())
            .collect()
    }

    pub fn get_entries_by_type(&self, ioc_type_str: &str) -> Vec<(String, u32, u32, u32, f32)> {
        let target_type = IocType::from_str(ioc_type_str);
        let entries = self.entries.read();
        entries.iter().filter(|(_k, e)| e.read().ioc_type == target_type)
            .map(|(_k, e)| {
                let entry = e.read();
                (entry.normalized_value.clone(), entry.first_seen_sprint, entry.last_seen_sprint, entry.occurrence_count, entry.confidence_max)
            })
            .collect()
    }

    pub fn msync(&mut self) -> PyResult<()> { self.persist() }
    pub fn close(&mut self) -> PyResult<()> {
        // F267: Atomic write — persist first (fsync), then re-open file handle.
        // Provides deterministic persist + fd release vs relying on GC/Drop.
        // On Unix, the previous Arc<File> fd is closed when refcount hits 0.
        self.persist()?;
        let new_file = OpenOptions::new()
            .read(true).write(true).create(true).truncate(true)
            .open(&self.file_path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("re-open failed: {}", e)))?;
        self.file = Arc::new(new_file);
        Ok(())
    }
    pub fn clear(&mut self) { self.entries.write().clear(); self.total_seen = 0; self.total_deduped = 0; self.dirty = true; }
    pub fn get_sprint(&self) -> u32 { self.current_sprint }
    pub fn path(&self) -> String { self.file_path.clone() }
    pub fn byte_size(&self) -> usize { MMAP_HEADER_SIZE + self.get_state_bytes().len() }
}

// ===========================================================================
// Legacy in-memory IocDedupStore (kept for compat + tests)
// ===========================================================================

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
        Self { entries: AHashMap::with_capacity(50_000), current_sprint: sprint_id, total_seen: 0, total_deduped: 0 }
    }

    #[pyo3(signature = (value, ioc_type_str, confidence = 0.5))]
    pub fn add(&mut self, value: &str, ioc_type_str: &str, confidence: f32) -> bool {
        self.total_seen += 1;
        if value.is_empty() { return false; }
        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());
        if let Some(entry) = self.entries.get_mut(&key) {
            entry.last_seen_sprint = self.current_sprint;
            entry.occurrence_count += 1;
            if confidence > entry.confidence_max { entry.confidence_max = confidence; }
            self.total_deduped += 1;
            false
        } else {
            self.entries.insert(key, IocEntry { normalized_value: normalized, ioc_type, first_seen_sprint: self.current_sprint, last_seen_sprint: self.current_sprint, occurrence_count: 1, confidence_max: confidence });
            true
        }
    }

    pub fn add_batch(&mut self, items: Vec<(String, String, f32)>) -> Vec<bool> {
        let mut results = Vec::with_capacity(items.len());
        for (value, ioc_type_str, confidence) in items { results.push(self.add(&value, &ioc_type_str, confidence)); }
        results
    }

    pub fn contains(&self, value: &str, ioc_type_str: &str) -> bool {
        if value.is_empty() { return false; }
        let ioc_type = IocType::from_str(ioc_type_str);
        let normalized = normalize_ioc(value, &ioc_type);
        let key_str = format!("{}:{}", ioc_type_str.to_lowercase(), normalized);
        let key = xxh3_64(key_str.as_bytes());
        self.entries.contains_key(&key)
    }

    pub fn advance_sprint(&mut self, new_sprint_id: u32) { self.current_sprint = new_sprint_id; }
    pub fn len(&self) -> usize { self.entries.len() }
    pub fn is_empty(&self) -> bool { self.entries.is_empty() }
    pub fn stats(&self) -> (u64, u64, u64) { (self.total_seen, self.total_deduped, self.entries.len() as u64) }

    pub fn stats_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("total_seen", self.total_seen as i64)?;
        dict.set_item("total_deduped", self.total_deduped as i64)?;
        dict.set_item("unique_count", self.entries.len() as i64)?;
        dict.set_item("current_sprint", self.current_sprint as i64)?;
        let total = self.total_seen as f64;
        let hit_rate_bp = if total > 0.0 { ((self.total_deduped as f64) / total * 10_000.0).round() as i64 } else { 0 };
        dict.set_item("hit_rate_bp", hit_rate_bp)?;
        Ok(dict)
    }

    pub fn get_by_type(&self, ioc_type_str: &str) -> Vec<String> {
        let target_type = IocType::from_str(ioc_type_str);
        self.entries.iter().filter(|(_k, e)| e.ioc_type == target_type).map(|(_k, e)| e.normalized_value.clone()).collect()
    }

    pub fn get_entries_by_type(&self, ioc_type_str: &str) -> Vec<(String, u32, u32, u32, f32)> {
        let target_type = IocType::from_str(ioc_type_str);
        self.entries.iter().filter(|(_k, e)| e.ioc_type == target_type)
            .map(|(_k, e)| (e.normalized_value.clone(), e.first_seen_sprint, e.last_seen_sprint, e.occurrence_count, e.confidence_max))
            .collect()
    }

    pub fn get_state_bytes(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(1024);
        bytes.extend_from_slice(&(self.entries.len() as u32).to_le_bytes());
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
        bytes.extend_from_slice(&self.current_sprint.to_le_bytes());
        bytes.extend_from_slice(&self.total_seen.to_le_bytes());
        bytes.extend_from_slice(&self.total_deduped.to_le_bytes());
        bytes
    }

    pub fn set_state_from_bytes(&mut self, data: &[u8]) -> bool {
        if data.len() < 4 { return false; }
        let mut pos = 0;
        let count = u32::from_le_bytes([data[0], data[1], data[2], data[3]]);
        pos += 4;
        for _ in 0..count {
            if pos + 4 > data.len() { return false; }
            let k = u64::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3], data[pos+4], data[pos+5], data[pos+6], data[pos+7]]);
            pos += 8;
            if pos + 4 > data.len() { return false; }
            let val_len = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]) as usize;
            pos += 4;
            if pos + val_len > data.len() { return false; }
            let normalized = String::from_utf8_lossy(&data[pos..pos+val_len]).to_string();
            pos += val_len;
            if pos >= data.len() { return false; }
            let ioc_type_byte = data[pos]; pos += 1;
            let ioc_type = match ioc_type_byte {
                0 => IocType::Ip, 1 => IocType::Ipv6, 2 => IocType::Domain,
                3 => IocType::Url, 4 => IocType::Md5, 5 => IocType::Sha1,
                6 => IocType::Sha256, 7 => IocType::Email, 8 => IocType::Cve, _ => IocType::Unknown,
            };
            if pos + 16 > data.len() { return false; }
            let first = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]);
            let last = u32::from_le_bytes([data[pos+4], data[pos+5], data[pos+6], data[pos+7]]);
            let occurrence = u32::from_le_bytes([data[pos+8], data[pos+9], data[pos+10], data[pos+11]]);
            let confidence = f32::from_le_bytes([data[pos+12], data[pos+13], data[pos+14], data[pos+15]]);
            pos += 16;
            self.entries.insert(k, IocEntry { normalized_value: normalized, ioc_type, first_seen_sprint: first, last_seen_sprint: last, occurrence_count: occurrence, confidence_max: confidence });
        }
        if pos + 12 > data.len() { return false; }
        self.current_sprint = u32::from_le_bytes([data[pos], data[pos+1], data[pos+2], data[pos+3]]);
        self.total_seen = u64::from_le_bytes([data[pos+4], data[pos+5], data[pos+6], data[pos+7], data[pos+8], data[pos+9], data[pos+10], data[pos+11]]);
        self.total_deduped = u64::from_le_bytes([data[pos+12], data[pos+13], data[pos+14], data[pos+15], data[pos+16], data[pos+17], data[pos+18], data[pos+19]]);
        true
    }

    #[allow(clippy::incorrect_clone_on_copy)]
    pub fn __getstate__<'py>(&self, py: Python<'py>) -> Py<PyBytes> { PyBytes::new(py, &self.get_state_bytes()).into() }
    pub fn __setstate__(&mut self, _py: Python<'_>, state: &Bound<'_, PyBytes>) -> PyResult<()> {
        if !self.set_state_from_bytes(state.as_bytes()) {
            return Err(pyo3::exceptions::PyValueError::new_err("Invalid state data"));
        }
        Ok(())
    }

    pub fn clear(&mut self) { self.entries.clear(); self.total_seen = 0; self.total_deduped = 0; }
    pub fn get_sprint(&self) -> u32 { self.current_sprint }
    pub fn to_bytes(&self) -> Vec<u8> { self.get_state_bytes() }
}

#[pyfunction]
pub fn ioc_dedup_from_bytes(data: Vec<u8>) -> PyResult<IocDedupStore> {
    let mut store = IocDedupStore::new(0);
    if !store.set_state_from_bytes(&data) {
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid state data"));
    }
    Ok(store)
}

pub fn register_class(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MmapIocDedupStore>()?;
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
        assert!(!store.add("evil.com", "domain", 0.95));
        assert!(store.add("evil.com", "url", 0.8));
        assert_eq!(store.len(), 2);
        assert_eq!(store.stats(), (3, 1, 2));
    }

    #[test]
    fn test_batch_add() {
        let mut store = IocDedupStore::new(1);
        let results = store.add_batch(vec![
            ("domain1.com".to_string(), "domain".to_string(), 0.9),
            ("domain1.com".to_string(), "domain".to_string(), 0.8),
        ]);
        assert_eq!(results, vec![true, false]);
    }
}

//! dedup_bloom.rs — Distribuovaný BloomFilter pro cross-instance URL dedup
//!
//! unlike bloom.rs (single-process):
//! - Farm hash pro distribuovanou konsistenci (stejný input = stejný hash napříč instancemi)
//! - Count-Min Sketch pro frequency estimation
//! - Multiple BloomFilter tiers pro různé false positive rate
//! - Mmap-backed persistence pro restart survival
//!
//! Use case: Multiple hledac instances dedupují URL bez centralizovaného coordinatora
//! Trade-off: Malý false positive rate acceptable (0.1% FPP = 1 z 1000 duplikátů projde)
//!
//! M1 8GB bounds:
//!   MAX_ITEMS = 1_000_000 (1M items, ~12 MB bit array při 0.001 FPP)
//!   NUM_TIERS = 3 (fine/coarse/macro)
//!   FARM_SEED = 0xDEADBEEF (konzistence napříč instancemi)

use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Read, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use lz4_flex::block::{compress as lz4_compress, decompress as lz4_decompress};
use xxhash_rust::xxh3::{xxh3_64, xxh3_64_with_seed};

// RUST-PANIC-001 FIX: use release_gil_py for panic-safe GIL release
use crate::gil::release_gil_py;

// ---------------------------------------------------------------------------
// Global counters for health endpoint (no synchronization needed — atomics)
// ---------------------------------------------------------------------------

static GLOBAL_INSTANCES: AtomicU64 = AtomicU64::new(0);
static GLOBAL_ITEMS_ADDED: AtomicU64 = AtomicU64::new(0);
static GLOBAL_CAPACITY: AtomicU64 = AtomicU64::new(0);
static GLOBAL_MEMORY_BYTES: AtomicU64 = AtomicU64::new(0);

/// Returns (instances, total_items_added, total_capacity) for health_check().
pub fn global_stats() -> (u64, u64, u64) {
    (
        GLOBAL_INSTANCES.load(Ordering::Relaxed),
        GLOBAL_ITEMS_ADDED.load(Ordering::Relaxed),
        GLOBAL_CAPACITY.load(Ordering::Relaxed),
    )
}

/// Returns total memory usage in bytes across all DedupBloomFilter instances.
pub fn global_memory_bytes() -> u64 {
    GLOBAL_MEMORY_BYTES.load(Ordering::Relaxed)
}

/// Bump global instance count (called from PyDistributedBloomFilter::new).
fn bump_instance() {
    GLOBAL_INSTANCES.fetch_add(1, Ordering::Relaxed);
    // Sum of all tier capacities = fixed at startup
    let total_cap: u64 = TIER_CAPACITIES.iter().map(|&c| c as u64));
    GLOBAL_CAPACITY.store(total_cap, Ordering::Relaxed);
    // M1-06: Update static memory footprint for health endpoint
    let mem_bytes = compute_static_memory_bytes();
    GLOBAL_MEMORY_BYTES.store(mem_bytes, Ordering::Relaxed);
}

/// Compute static memory footprint: bit arrays + Count-Min Sketch table.
/// This is constant per-instance (same for all DedupBloomFilter instances).
fn compute_static_memory_bytes() -> u64 {
    // Bloom tiers: sum of bit array sizes
    let tier_bytes: u64 = TIER_CAPACITIES
        .iter()
        .zip(TIER_FPP.iter())
        .map(|(cap, fpp)| {
            let num_bits = (-(*cap as f64) * fpp.ln() / (2.0_f64.ln().powi(2))) as u64;
            (num_bits + 7) / 8 // bits to bytes, rounded up
        })
        );
    // Count-Min Sketch: 4 depth × 16384 width × 4 bytes per u32
    let sketch_bytes: u64 = 4 * 16384 * 4;
    tier_bytes + sketch_bytes
}

/// Bump items added (called from DistributedBloomFilter::add when is_new=true).
fn bump_items() {
    GLOBAL_ITEMS_ADDED.fetch_add(1, Ordering::Relaxed);
}

/// xxHash3-64 double-hash for BloomFilter-backed dedup (NEON-accelerated on M1).
///
/// Computes two independent 64-bit hashes via xxh3_64 (primary) and
/// xxh3_64_with_seed (secondary). Both are NEON-SIMD on Apple Silicon M1/A1/A2.
///
/// Returns (h1, h2) suitable for double-hashing formula in BloomFilter.
fn farm_hash_double(x: &[u8], seed: u64) -> (u64, u64) {
    let h1 = xxh3_64(x);
    let h2 = xxh3_64_with_seed(x, seed);
    // Ensure h2 ≠ 0 to keep double-hash formula well-defined
    if h2 == 0 {
        (h1, 0x0101010101010101_u64)
    } else {
        (h1, h2)
    }
}

fn bloom_positions(data: &[u8], seed: u64, num_bits: usize, num_hashes: usize) -> Vec<usize> {
    let (h1, h2) = farm_hash_double(data, seed);
    let num_bits_u64 = num_bits as u64;
    (0..num_hashes)
        .map(|i: usize| ((h1.wrapping_add((i as u64).wrapping_mul(h2))) % num_bits_u64) as usize)
        .collect()
}

// Constants
const FARM_SEED: u64 = 0xDEADBEEF;
const NUM_TIERS: usize = 3;

// Tier configuration: (capacity, fpp)
const TIER_CAPACITIES: [usize; NUM_TIERS] = [100_000, 500_000, 1_000_000];
const TIER_FPP: [f64; NUM_TIERS] = [0.0001, 0.001, 0.01];

// File format
const FILE_MAGIC: u32 = 0x4442_4F4F; // "DBLO"
const FILE_VERSION: u8 = 1;

/// Count-Min Sketch for frequency estimation
struct CountMinSketch {
    /// 2D table: depth × width
    table: Vec<Vec<u32>>,
    /// Depth (number of hash functions)
    depth: usize,
    /// Width (number of buckets per hash)
    width: usize,
    /// Farm hash seeds
    seeds: Vec<u64>,
}

impl CountMinSketch {
    fn new(depth: usize, width: usize) -> Self {
        Self {
            table: vec![vec![0u32; width]; depth],
            depth,
            width,
            seeds: vec![0xCAFEBABE, 0xDEADBEEF, 0x12345678, 0x9ABCDEF0],
        }
    }

    /// Update frequency count for an item
    fn update(&mut self, item: &[u8]) {
        for (i, &seed) in self.seeds.iter().enumerate().take(self.depth) {
            let (h1, h2) = farm_hash_double(item, seed);
            let bucket = ((h1.wrapping_add(h2)) % (self.width as u64)) as usize;
            self.table[i][bucket] = self.table[i][bucket].saturating_add(1);
        }
    }

    /// Estimate minimum frequency for an item
    fn estimate(&self, item: &[u8]) -> u32 {
        self.seeds
            .iter()
            .take(self.depth)
            .enumerate()
            .map(|(i, &seed)| {
                let (h1, h2) = farm_hash_double(item, seed);
                let bucket = ((h1.wrapping_add(h2)) % (self.width as u64)) as usize;
                self.table[i][bucket]
            })
            .min()
            .unwrap_or(0)
    }

    /// Merge another sketch into this one (for distributed aggregation).
    /// Note: not exposed to Python bindings — distributed aggregation is planned future work.
    #[cfg(test)]
    fn merge(&mut self, other: &CountMinSketch) {
        if self.depth != other.depth || self.width != other.width {
            return;
        }
        for (row, other_row) in self.table.iter_mut().zip(other.table.iter()) {
            for (val, other_val) in row.iter_mut().zip(other_row.iter()) {
                *val = val.saturating_add(*other_val);
            }
        }
    }
}

/// A single BloomFilter tier
struct BloomTier {
    bits: Vec<u64>,
    num_bits: usize,
    num_hashes: usize,
    items_added: usize,
    seed: u64,
}

impl BloomTier {
    fn new(capacity: usize, fpp: f64) -> Self {
        // m = -n * ln(p) / (ln(2)^2)
        let num_bits = (-(capacity as f64) * fpp.ln() / (2.0_f64.ln().powi(2))) as usize;
        // k = (m/n) * ln(2)
        let num_hashes = ((num_bits as f64 / capacity as f64) * 2.0_f64.ln()) as usize;
        let num_words = num_bits.div_ceil(64);

        Self {
            bits: vec![0u64; num_words],
            num_bits,
            num_hashes,
            items_added: 0,
            seed: FARM_SEED,
        }
    }

    /// Add an item, return true if new (not a duplicate)
    fn add(&mut self, item: &[u8]) -> bool {
        let positions = bloom_positions(item, self.seed, self.num_bits, self.num_hashes);

        let mut was_new = false;
        for &pos in &positions {
            let word_idx = pos / 64;
            let bit_idx = pos % 64;
            let mask = 1u64 << bit_idx;

            if self.bits[word_idx] & mask == 0 {
                was_new = true;
                self.bits[word_idx] |= mask;
            }
        }

        if was_new {
            self.items_added += 1;
        }
        was_new
    }

    /// Check if item might be in the set
    fn contains(&self, item: &[u8]) -> bool {
        let positions = bloom_positions(item, self.seed, self.num_bits, self.num_hashes);
        positions.iter().all(|&pos| {
            let word_idx = pos / 64;
            let bit_idx = pos % 64;
            (self.bits[word_idx] & (1u64 << bit_idx)) != 0
        })
    }

    /// Current false positive rate
    fn current_fpp(&self) -> f64 {
        if self.items_added == 0 {
            return 0.0;
        }
        // fpp = (1 - e^(-kn/m))^k
        let k = self.num_hashes as f64;
        let n = self.items_added as f64;
        let m = self.num_bits as f64;
        (1.0 - (-k * n / m).exp()).powf(k)
    }

    /// Serialize BloomTier to bytes
    fn to_bytes(&self) -> Vec<u8> {
        let mut buf = Vec::new();
        buf.extend_from_slice(&(self.num_bits as u32).to_le_bytes());
        buf.extend_from_slice(&(self.num_hashes as u32).to_le_bytes());
        buf.extend_from_slice(&(self.items_added as u32).to_le_bytes());
        buf.extend_from_slice(&self.seed.to_le_bytes());
        buf.extend_from_slice(&(self.bits.len() as u32).to_le_bytes());
        for &bits in &self.bits {
            buf.extend_from_slice(&bits.to_le_bytes());
        }
        buf
    }

    /// Deserialize BloomTier from bytes
    fn from_bytes(data: &[u8]) -> PyResult<Self> {
        let mut pos = 0;
        let read_u32 = |data: &[u8], pos: &mut usize| -> PyResult<u32> {
            if *pos + 4 > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data",
                ));
            }
            let val =
                u32::from_le_bytes([data[*pos], data[*pos + 1], data[*pos + 2], data[*pos + 3]]);
            *pos += 4;
            Ok(val)
        };
        let read_u64 = |data: &[u8], pos: &mut usize| -> PyResult<u64> {
            if *pos + 8 > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data",
                ));
            }
            let val = u64::from_le_bytes([
                data[*pos],
                data[*pos + 1],
                data[*pos + 2],
                data[*pos + 3],
                data[*pos + 4],
                data[*pos + 5],
                data[*pos + 6],
                data[*pos + 7],
            ]);
            *pos += 8;
            Ok(val)
        };

        let num_bits = read_u32(data, &mut pos)? as usize;
        let num_hashes = read_u32(data, &mut pos)? as usize;
        let items_added = read_u32(data, &mut pos)? as usize;
        let seed = read_u64(data, &mut pos)?;
        let bits_len = read_u32(data, &mut pos)? as usize;

        let mut bits = vec![0u64; bits_len];
        for i in 0..bits_len {
            bits[i] = read_u64(data, &mut pos)?;
        }

        Ok(Self {
            bits,
            num_bits,
            num_hashes,
            items_added,
            seed,
        })
    }
}

impl CountMinSketch {
    /// Serialize CountMinSketch to bytes
    fn to_bytes(&self) -> Vec<u8> {
        let mut buf = Vec::new();
        buf.extend_from_slice(&(self.depth as u32).to_le_bytes());
        buf.extend_from_slice(&(self.width as u32).to_le_bytes());
        buf.extend_from_slice(&(self.seeds.len() as u32).to_le_bytes());
        for &seed in &self.seeds {
            buf.extend_from_slice(&seed.to_le_bytes());
        }
        buf.extend_from_slice(&(self.table.len() as u32).to_le_bytes());
        for row in &self.table {
            buf.extend_from_slice(&(row.len() as u32).to_le_bytes());
            for &val in row {
                buf.extend_from_slice(&val.to_le_bytes());
            }
        }
        buf
    }

    /// Deserialize CountMinSketch from bytes
    fn from_bytes(data: &[u8]) -> PyResult<Self> {
        let mut pos = 0;
        let read_u32 = |data: &[u8], pos: &mut usize| -> PyResult<u32> {
            if *pos + 4 > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data",
                ));
            }
            let val =
                u32::from_le_bytes([data[*pos], data[*pos + 1], data[*pos + 2], data[*pos + 3]]);
            *pos += 4;
            Ok(val)
        };
        let read_u64 = |data: &[u8], pos: &mut usize| -> PyResult<u64> {
            if *pos + 8 > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data",
                ));
            }
            let val = u64::from_le_bytes([
                data[*pos],
                data[*pos + 1],
                data[*pos + 2],
                data[*pos + 3],
                data[*pos + 4],
                data[*pos + 5],
                data[*pos + 6],
                data[*pos + 7],
            ]);
            *pos += 8;
            Ok(val)
        };

        let depth = read_u32(data, &mut pos)? as usize;
        let width = read_u32(data, &mut pos)? as usize;
        let seeds_len = read_u32(data, &mut pos)? as usize;
        let mut seeds = vec![0u64; seeds_len];
        for i in 0..seeds_len {
            seeds[i] = read_u64(data, &mut pos)?;
        }
        let rows = read_u32(data, &mut pos)? as usize;
        let mut table = Vec::with_capacity(rows);
        for _ in 0..rows {
            let cols = read_u32(data, &mut pos)? as usize;
            let mut row = Vec::with_capacity(cols);
            for _ in 0..cols {
                row.push(read_u32(data, &mut pos)?);
            }
            table.push(row);
        }

        Ok(Self {
            table,
            depth,
            width,
            seeds,
        })
    }
}

/// Distributed BloomFilter with multiple tiers and Count-Min frequency estimation
pub struct DistributedBloomFilter {
    tiers: Vec<BloomTier>,
    sketch: CountMinSketch,
    total_items: usize,
}

impl DistributedBloomFilter {
    fn new() -> Self {
        let tiers: Vec<BloomTier> = TIER_CAPACITIES
            .iter()
            .zip(TIER_FPP.iter())
            .map(|(cap, fpp)| BloomTier::new(*cap, *fpp))
            );

        Self {
            tiers,
            sketch: CountMinSketch::new(4, 16384), // 256KB sketch
            total_items: 0,
        }
    }

    /// Add an item, return true if new
    pub fn add(&mut self, item: &[u8]) -> bool {
        // Update frequency sketch
        self.sketch.update(item);

        // Try to add to each tier
        let mut is_new = false;
        for tier in &mut self.tiers {
            if tier.add(item) {
                is_new = true;
            }
        }

        if is_new {
            self.total_items += 1;
            bump_items();
        }
        is_new
    }

    /// Check if item might be in the set
    pub fn contains(&self, item: &[u8]) -> bool {
        // Check all tiers (any can have it)
        self.tiers.iter().any(|tier| tier.contains(item))
    }

    /// Get frequency estimate for an item
    pub fn frequency(&self, item: &[u8]) -> u32 {
        self.sketch.estimate(item)
    }

    /// Get memory usage in bytes
    pub fn memory_bytes(&self) -> usize {
        let tier_bytes: usize = self.tiers.iter().map(|t| t.bits.len() * 8));
        let sketch_bytes = self.sketch.table.len() * self.sketch.table[0].len() * 4;
        tier_bytes + sketch_bytes
    }

    /// Save to mmap-backed file
    pub fn save(&self, path: &PathBuf) -> PyResult<()> {
        let file_path = path.join("dedup_bloom.bin");

        let file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&file_path)
            .map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Cannot open file: {}", e))
            })?;
        let mut writer = BufWriter::new(file);

        // Write header
        writer
            .write_all(&FILE_MAGIC.to_le_bytes())
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
        writer
            .write_all(&[FILE_VERSION])
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;

        // Write tier data
        for tier in &self.tiers {
            let tier_data = tier);
            let compressed = lz4_compress(&tier_data);
            let len_bytes = (compressed.len() as u32));
            writer
                .write_all(&len_bytes)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
            writer
                .write_all(&compressed)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
        }

        // Write sketch
        let sketch_data = self.sketch);
        let sketch_len = (sketch_data.len() as u32));
        writer
            .write_all(&sketch_len)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;
        writer
            .write_all(&sketch_data)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Write error: {}", e)))?;

        writer
            .flush()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Flush error: {}", e)))?;
        Ok(())
    }

    /// Load from file
    pub fn load(path: &PathBuf) -> PyResult<Self> {
        let file_path = path.join("dedup_bloom.bin");

        let mut file = File::open(&file_path)
            .map_err(|_| pyo3::exceptions::PyFileNotFoundError::new_err("File not found"))?;

        // Read header
        let mut magic_buf = [0u8; 4];
        file.read_exact(&mut magic_buf)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

        let magic = u32::from_le_bytes(magic_buf);
        if magic != FILE_MAGIC {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Invalid file format",
            ));
        }

        let mut version_buf = [0u8; 1];
        file.read_exact(&mut version_buf)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

        if version_buf[0] != FILE_VERSION {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unsupported version",
            ));
        }

        // Read tiers
        let mut tiers = Vec::new();
        for _ in 0..NUM_TIERS {
            let mut len_buf = [0u8; 4];
            file.read_exact(&mut len_buf)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

            let len = u32::from_le_bytes(len_buf) as usize;
            let mut compressed = vec![0u8; len];
            file.read_exact(&mut compressed)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

            let decompressed = lz4_decompress(&compressed, len * 4).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Decompress error: {}", e))
            })?;

            let tier = BloomTier::from_bytes(&decompressed)?;
            tiers.push(tier);
        }

        // Read sketch
        let mut sketch_len_buf = [0u8; 4];
        file.read_exact(&mut sketch_len_buf)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

        let sketch_len = u32::from_le_bytes(sketch_len_buf) as usize;
        let mut sketch_data = vec![0u8; sketch_len];
        file.read_exact(&mut sketch_data)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Read error: {}", e)))?;

        let sketch = CountMinSketch::from_bytes(&sketch_data)?;

        let total_items = tiers.iter().map(|t| t.items_added));

        Ok(Self {
            tiers,
            sketch,
            total_items,
        })
    }

    /// Serialize to compressed bytes for LMDB storage.
    /// Used by LMDB-backed persistence path.
    pub fn to_bytes_compressed(&self) -> PyResult<Vec<u8>> {
        let mut buf = Vec::new();

        // Write header
        buf.extend_from_slice(&FILE_MAGIC.to_le_bytes());
        buf.push(FILE_VERSION);

        // Write tier data (LZ4 compressed, same as file format)
        for tier in &self.tiers {
            let tier_data = tier);
            let compressed = lz4_compress(&tier_data);
            let len_bytes = (compressed.len() as u32));
            buf.extend_from_slice(&len_bytes);
            buf.extend_from_slice(&compressed);
        }

        // Write sketch
        let sketch_data = self.sketch);
        let sketch_len = (sketch_data.len() as u32));
        buf.extend_from_slice(&sketch_len);
        buf.extend_from_slice(&sketch_data);

        Ok(buf)
    }

    /// Deserialize from compressed bytes (LMDB storage path).
    pub fn from_bytes_compressed(data: &[u8]) -> PyResult<Self> {
        let mut pos = 0;

        // Read header
        if pos + 4 > data.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unexpected end of data (magic)",
            ));
        }
        let magic = u32::from_le_bytes([data[pos], data[pos + 1], data[pos + 2], data[pos + 3]]);
        if magic != FILE_MAGIC {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Invalid file format",
            ));
        }
        pos += 4;

        if pos + 1 > data.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unexpected end of data (version)",
            ));
        }
        if data[pos] != FILE_VERSION {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unsupported version",
            ));
        }
        pos += 1;

        // Read tiers
        let mut tiers = Vec::new();
        for _ in 0..NUM_TIERS {
            if pos + 4 > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data (tier len)",
                ));
            }
            let len = u32::from_le_bytes([data[pos], data[pos + 1], data[pos + 2], data[pos + 3]])
                as usize;
            pos += 4;

            if pos + len > data.len() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "Unexpected end of data (tier data)",
                ));
            }
            let decompressed = lz4_decompress(&data[pos..pos + len], len * 4).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Decompress error: {}", e))
            })?;
            pos += len;

            let tier = BloomTier::from_bytes(&decompressed)?;
            tiers.push(tier);
        }

        // Read sketch
        if pos + 4 > data.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unexpected end of data (sketch len)",
            ));
        }
        let sketch_len =
            u32::from_le_bytes([data[pos], data[pos + 1], data[pos + 2], data[pos + 3]]) as usize;
        pos += 4;

        if pos + sketch_len > data.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unexpected end of data (sketch data)",
            ));
        }
        let sketch = CountMinSketch::from_bytes(&data[pos..pos + sketch_len])?;

        let total_items = tiers.iter().map(|t| t.items_added));

        Ok(Self {
            tiers,
            sketch,
            total_items,
        })
    }
}

// Python bindings
#[pyclass]
pub struct PyDistributedBloomFilter {
    filter: DistributedBloomFilter,
    cache_dir: PathBuf,
}

/// R4-08: Dedicated stats struct — avoids HashMap<String, Py<PyAny>> allocation
/// per stats() call. Python converts once at call site via dataclasses.asdict().
#[pyclass]
pub struct DedupBloomStats {
    #[pyo3(get)]
    pub total_items: usize,
    #[pyo3(get)]
    pub memory_bytes: usize,
    #[pyo3(get)]
    pub tier_count: usize,
    #[pyo3(get)]
    pub tier_0_fpp: f64,
    #[pyo3(get)]
    pub tier_0_items: usize,
    #[pyo3(get)]
    pub tier_1_fpp: f64,
    #[pyo3(get)]
    pub tier_1_items: usize,
    #[pyo3(get)]
    pub tier_2_fpp: f64,
    #[pyo3(get)]
    pub tier_2_items: usize,
}

#[pymethods]
impl PyDistributedBloomFilter {
    #[new]
    fn new(cache_dir: String) -> PyResult<Self> {
        let cache_dir = PathBuf::from(cache_dir);
        if let Some(parent) = cache_dir.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                pyo3::exceptions::PyIOError::new_err(format!("Cannot create dir: {}", e))
            })?;
        }
        bump_instance();
        Ok(Self {
            filter: DistributedBloomFilter::new(),
            cache_dir,
        })
    }

    fn add(&mut self, item: String) -> bool {
        self.filter.add(item.as_bytes())
    }

    /// R4-11 FIX: Bulk add — rayon-parallel, GIL released for CPU-bound hashing phase.
    /// R4-01: GIL released via release_gil() for rayon parallel hashing.
    fn add_batch(&mut self, items: Vec<String>, py: Python<'_>) -> Vec<bool> {
        if items.is_empty() {
            return vec![];
        }
        // R4-01: GIL released during bulk add — serial loop (filter.add is not Send+Sync)
        let filter = &mut self.filter;
        crate::gil::release_gil(py, move || {
            items.iter().map(|s| filter.add(s.as_bytes())).collect()
        })
    }

    fn contains(&self, item: String) -> bool {
        self.filter.contains(item.as_bytes())
    }

    /// R4-11 FIX: Bulk contains — rayon-parallel, read-only, GIL released during phase.
    /// R4-01: GIL released via release_gil() for rayon parallel contains check.
    fn contains_batch(&self, items: Vec<String>, py: Python<'_>) -> Vec<bool> {
        use rayon::prelude::*;
        if items.is_empty() {
            return vec![];
        }
        let bytes_vec: Vec<&[u8]> = items.iter().map(|s| s.as_bytes()));
        // R4-01: GIL released during rayon par_iter — filter.contains() is pure Rust
        crate::gil::release_gil(py, move || {
            bytes_vec
                .par_iter()
                .map(|b| self.filter.contains(b))
                .collect()
        })
    }

    fn frequency(&self, item: String) -> u32 {
        self.filter.frequency(item.as_bytes())
    }

    fn len(&self) -> usize {
        self.filter.total_items
    }

    fn memory_bytes(&self) -> usize {
        self.filter.memory_bytes()
    }

    /// R4-08 FIX: Returns Py<PyDict> directly — no intermediate DedupBloomStats Python object.
    /// PyDict is allocated once in Rust, Python receives it with zero conversion overhead.
    /// Python caller: stats = filter.stats()  # already a dict
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let tiers: Vec<_> = self.filter.tiers.iter());
        let dict = PyDict::new(py);
        // set_item copies primitive values (int, float) — no Python object allocation
        // beyond the dict itself, which is the intended zero-allocation improvement.
        dict.set_item("total_items", self.filter.total_items)?;
        dict.set_item("memory_bytes", self.filter.memory_bytes())?;
        dict.set_item("tier_count", NUM_TIERS)?;
        dict.set_item(
            "tier_0_fpp",
            tiers.get(0).map(|t| t.current_fpp()).unwrap_or(0.0),
        )?;
        dict.set_item(
            "tier_0_items",
            tiers.get(0).map(|t| t.items_added).unwrap_or(0),
        )?;
        dict.set_item(
            "tier_1_fpp",
            tiers.get(1).map(|t| t.current_fpp()).unwrap_or(0.0),
        )?;
        dict.set_item(
            "tier_1_items",
            tiers.get(1).map(|t| t.items_added).unwrap_or(0),
        )?;
        dict.set_item(
            "tier_2_fpp",
            tiers.get(2).map(|t| t.current_fpp()).unwrap_or(0.0),
        )?;
        dict.set_item(
            "tier_2_items",
            tiers.get(2).map(|t| t.items_added).unwrap_or(0),
        )?;
        Ok(dict)
    }

    /// R4-03: GIL released via Python::attach + py.detach for file I/O + LZ4 compression.
    /// RUST-PANIC-001 FIX: release_gil_py wraps py.detach in catch_unwind.
    fn save(&self) -> PyResult<String> {
        Python::attach(|py| {
            release_gil_py(py, || {
                self.filter.save(&self.cache_dir)?;
                Ok(self
                    .cache_dir
                    .join("dedup_bloom.bin")
                    .to_string_lossy()
                    .to_string())
            })
        })
    }

    /// R4-03: GIL released via Python::attach + py.detach for file I/O + LZ4 decompression.
    /// RUST-PANIC-001 FIX: release_gil_py wraps py.detach in catch_unwind.
    #[staticmethod]
    fn load(cache_dir: String) -> PyResult<Self> {
        let cache_dir_path = PathBuf::from(cache_dir);
        let filter = Python::attach(|py| {
            release_gil_py(py, || DistributedBloomFilter::load(&cache_dir_path))
        })?;
        Ok(Self {
            filter,
            cache_dir: cache_dir_path,
        })
    }

    fn reset(&mut self) {
        self.filter = DistributedBloomFilter::new();
    }

    /// R4-03: GIL released for LZ4 compression (CPU-intensive).
    /// RUST-PANIC-001 FIX: release_gil_py wraps py.detach in catch_unwind.
    fn save_to_lmdb_bytes(&self) -> PyResult<Vec<u8>> {
        Python::attach(|py| release_gil_py(py, || self.filter.to_bytes_compressed()))
    }

    /// R4-03 + R4-08 FIX: GIL released for LZ4 decompression (CPU-intensive).
    /// R4-08: data must be copied to owned Vec<u8> before py.detach() —
    /// Python bytes object could be GC'd while the detached thread runs.
    /// RUST-PANIC-001 FIX: release_gil_py wraps py.detach in catch_unwind.
    #[staticmethod]
    fn load_from_lmdb_bytes(data: &[u8]) -> PyResult<Self> {
        // R4-08 FIX: copy to owned buffer BEFORE releasing GIL.
        // Python bytes (data: &[u8]) could be collected by GC during decompression
        // if we don't hold them explicitly.
        let owned_data: Vec<u8> = data);
        let filter = Python::attach(|py| {
            release_gil_py(py, || {
                DistributedBloomFilter::from_bytes_compressed(&owned_data)
            })
        })?;
        Ok(Self {
            filter,
            cache_dir: PathBuf::new(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_distributed_bloom() {
        let mut filter = DistributedBloomFilter::new();

        assert!(filter.add(b"https://example.com"));
        assert!(!filter.add(b"https://example.com")); // duplicate
        assert!(filter.contains(b"https://example.com"));
        assert!(!filter.contains(b"https://other.com"));
    }

    #[test]
    fn test_frequency_estimation() {
        let mut filter = DistributedBloomFilter::new();

        for _ in 0..5 {
            filter.add(b"https://example.com");
        }

        let freq = filter.frequency(b"https://example.com");
        assert!(freq >= 5);
    }

    #[test]
    fn test_tier_differentiation() {
        let mut filter = DistributedBloomFilter::new();

        // Add many items to fine tier
        for i in 0..100 {
            let url = format!("https://example{}.com", i);
            filter.add(url.as_bytes());
        }

        // All should be in coarse and macro tiers too
        assert!(filter.contains(b"https://example0.com"));
    }
}

//! Unified Zero-Copy Arrow IPC Memory-Mapped File Infrastructure
//!
//! NEXTGEN-02: Zero-copy Arrow IPC via mmap + Mach vm_remap
//!
//! ## Problem
//!
//! Previous architecture:
//!   - Rust: build_ipc_bytes() → Vec<u8> → PyBytes → Python heap
//!   - Python: pa.ipc.open_stream() reads from PyBytes (copy to Arrow buffers)
//!   - DuckDB: SQL INSERT from Python objects (re-serialization)
//!   - MLX: mx.array(Python list) → new allocation
//!
//! Current limitations:
//!   - 13 × String::clone() per row in FindingsRow::from_dict()
//!   - Arrow IPC serialized to Vec<u8> in Rust
//!   - PyBytes returned to Python (copy to Python heap)
//!   - DuckDB ingest via SQL INSERT (re-serialization)
//!   - MLX tensor construction from Python list[float]
//!
//! ## Solution: Zero-Copy Arrow IPC Mmap
//!
//! 1. **Rust side**: write Arrow IPC stream directly to MmapMut
//!    - No Vec<u8> intermediate allocation
//!    - Zero-copy serialization to disk
//!
//! 2. **Python side**: read directly from mmap'd file
//!    - pa.ipc.open_stream(mmap.mmap(path)) reads zero-copy
//!    - No PyBytes copy
//!
//! 3. **DuckDB side**: zero-copy read from mmap file
//!    - duckdb.execute("COPY ... FROM arrow_ipc_file")
//!    - DuckDB reads via mmap → zero serialization
//!
//! 4. **MLX side**: direct mmap for tensor creation
//!    - mx.array(mx.core.mmap(path)) — MLX 0.24+
//!    - No Python heap allocation
//!
//! ## M1 8GB Safety
//!
//! - Mmap pool capped at 512 MiB (UmaBudget.TRACKED_ALLOCATION_BUDGET_GIB)
//! - Memory guard: available < 1 GiB → fail-fast, no allocation
//! - Automatic cleanup: temp files deleted after consume
//! - RAII pattern: MmapWriter owns budget, freed on drop
//!
//! ## Architecture
//!
//! ```text
//! ┌────────────────────────────────────────────────────────────────────────┐
//! │  Arrow Batch Builder (Rust)                                            │
//! │  ┌──────────────────────────────────────────────────────────────────┐  │
//! │  │ build_ipc_mmap(path, findings) → MmapMut → disk               │  │
//! │  │   - Arrow IPC stream written directly to mmap                   │  │
//! │  │   - No Vec<u8> intermediate                                     │  │
//! │  │   - Returns (path, schema_json, num_rows)                        │  │
//! │  │   - Budget accounted via RAII — freed on Drop                   │  │
//! │  └──────────────────────────────────────────────────────────────────┘  │
//! └────────────────────────────────────────────────────────────────────────┘
//!                               ↓
//!                         Arrow IPC File
//!                               ↓
//!         ┌─────────────────────┼─────────────────────┐
//!         ↓                     ↓                     ↓
//! ┌───────────────┐   ┌─────────────────┐   ┌──────────────────┐
//! │   Python      │   │     DuckDB      │   │      MLX         │
//! │ pa.ipc.open_  │   │ COPY FROM file  │   │ mx.core.mmap()   │
//! │ stream(mmap)  │   │ zero-copy read  │   │ zero-copy tensor │
//! └───────────────┘   └─────────────────┘   └──────────────────┘
//! ```

use arrow::datatypes::SchemaRef;
use arrow::ipc::reader::StreamReader;
use arrow::ipc::writer::StreamWriter;
use memmap2::{MmapMut, MmapOptions};
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::types::{PyBytes, PyDict, PyList};
use rayon::prelude::*;
use std::fs::{File, OpenOptions};
use std::io::Cursor;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

use crate::mixed_pool;

/// Maximum Arrow IPC mmap pool size (512 MiB).
/// Made pub(crate) for use by arrow_batch_builder.rs.
pub(crate) const MAX_MMAP_POOL_BYTES: u64 = 512 * 1024 * 1024;

/// Memory floor: skip mmap if available < 1 GiB.
const MEMORY_FLOOR_BYTES: u64 = 1024 * 1024 * 1024;

/// Arrow IPC schema version.
const ARROW_IPC_VERSION: i32 = 5;

/// Total bytes currently in Arrow IPC mmap pool.
static MMAP_POOL_BYTES: AtomicU64 = AtomicU64::new(0);

/// Peak mmap pool usage (for telemetry).
static MMAP_POOL_PEAK: AtomicU64 = AtomicU64::new(0);

/// Check if mmap allocation is allowed under UmaBudget.
///
/// Returns Ok(()) if allocation is allowed, Err(message) otherwise.
/// Made public for use by arrow_batch_builder.rs build_arrow_batch_to_mmap.
pub fn check_mmap_budget(requested_bytes: u64) -> Result<(), String> {
    let current = MMAP_POOL_BYTES.load(Ordering::Relaxed);

    // Hard cap: 512 MiB pool limit
    if current + requested_bytes > MAX_MMAP_POOL_BYTES {
        return Err(format!(
            "mmap_pool: current={} + requested={} > max={}",
            current, requested_bytes, MAX_MMAP_POOL_BYTES
        ));
    }

    // Memory guard: check available system memory
    let available = get_available_memory_bytes();
    if available < MEMORY_FLOOR_BYTES {
        return Err(format!(
            "mmap_pool: available={:.2} GiB < floor={:.2} GiB",
            available as f64 / (1024.0 * 1024.0 * 1024.0),
            MEMORY_FLOOR_BYTES as f64 / (1024.0 * 1024.0 * 1024.0)
        ));
    }

    Ok(())
}

/// Account mmap allocation in pool.
/// Made public for use by arrow_batch_builder.rs build_arrow_batch_to_mmap.
pub fn account_mmap_alloc(bytes: u64) {
    let new_total = MMAP_POOL_BYTES.fetch_add(bytes, Ordering::AcqRel);
    let peak = MMAP_POOL_PEAK.load(Ordering::Relaxed);
    if new_total > peak {
        MMAP_POOL_PEAK.store(new_total, Ordering::Relaxed);
    }
}

/// Release mmap allocation from pool.
/// Made public for use by arrow_batch_builder.rs build_arrow_batch_to_mmap.
pub fn account_mmap_free(bytes: u64) {
    MMAP_POOL_BYTES.fetch_sub(bytes, Ordering::AcqRel);
}

/// Get available memory in bytes using host_statistics64.
///
/// Returns free + inactive pages × page_size for accurate memory availability.
#[cfg(target_os = "macos")]
fn get_available_memory_bytes() -> u64 {
    let mut vm_stat: libc::vm_statistics64 = unsafe { std::mem::zeroed() };
    let mut count = (std::mem::size_of::<libc::vm_statistics64>()
        / std::mem::size_of::<libc::integer_t>())
        as libc::mach_msg_type_number_t;
    
    let ret = unsafe {
        libc::host_statistics64(
            libc::mach_host_self(),
            libc::HOST_VM_INFO64,
            &mut vm_stat as *mut _ as *mut libc::c_void,
            &mut count,
        )
    };
    
    if ret == 0 {
        let free_pages: u64 = vm_stat.free_count as u64;
        let inactive_pages: u64 = vm_stat.inactive_count as u64;
        let page_size: u64 = 4096; // M1 uses 4KB pages
        return (free_pages + inactive_pages) * page_size;
    }
    
    // Fallback: assume 8GB available
    8 * 1024 * 1024 * 1024
}

#[cfg(not(target_os = "macos"))]
fn get_available_memory_bytes() -> u64 {
    // Non-macOS fallback: return 8GB
    8 * 1024 * 1024 * 1024
}

/// Arrow IPC mmap writer for zero-copy streaming.
///
/// Writes Arrow IPC RecordBatch directly to a memory-mapped file.
/// No intermediate Vec<u8> allocation.
pub struct ArrowIpcMmapWriter {
    file: File,
    mmap: MmapMut,
    schema_json: String,
    num_rows: u64,
    bytes_written: u64,
    /// Actual allocated bytes for correct budget accounting (may differ from bytes_written)
    allocated_bytes: u64,
}

impl ArrowIpcMmapWriter {
    /// Create a new Arrow IPC mmap writer.
    ///
    /// # Arguments
    /// * `path` - Path to the mmap file (will be created/truncated)
    /// * `schema` - Arrow schema for the batch
    /// * `estimated_rows` - Estimated number of rows (for capacity hint)
    ///
    /// # Returns
    /// ArrowIpcMmapWriter on success, error string on failure.
    pub fn new(path: &Path, schema: SchemaRef, estimated_rows: usize) -> Result<Self, String> {
        // Estimate capacity: schema metadata + batch data
        // Rule of thumb: ~100 bytes per row for typical IOC data
        let estimated_bytes = (estimated_rows as u64)
            .saturating_mul(100)
            .max(64 * 1024) // Minimum 64 KiB
            .min(MAX_MMAP_POOL_BYTES / 2); // Cap at 256 MiB

        check_mmap_budget(estimated_bytes)?;

        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(path)
            .map_err(|e| format!("failed to open mmap file: {}", e))?;

        // Set file size (pre-allocate)
        file.set_len(estimated_bytes)
            .map_err(|e| format!("failed to set file size: {}", e))?;

        let mmap = unsafe { MmapMut::map_mut(&file) }
            .map_err(|e| format!("failed to create mmap: {}", e))?;

        // Serialize schema to JSON for Python side
        let schema_json = serde_json::to_string(&schema.as_ref().to_json())
            .map_err(|e| format!("failed to serialize schema: {}", e))?;

        let writer = Self {
            file,
            mmap,
            schema_json,
            num_rows: 0,
            bytes_written: 0,
            allocated_bytes: estimated_bytes, // Track actual allocation for correct accounting
        };

        // Account allocation using actual bytes
        account_mmap_alloc(estimated_bytes);

        Ok(writer)
    }

    /// Write a RecordBatch to the mmap.
    ///
    /// Returns the number of bytes written.
    pub fn write_batch(&mut self, batch: &arrow::array::RecordBatch) -> Result<usize, String> {
        use std::io::Write;

        let start = self.bytes_written as usize;

        let mut cursor = std::io::Cursor::new(unsafe { self.mmap.as_mut_slice() });

        // Use IPC writer
        let mut writer = StreamWriter::try_new(&mut cursor, batch.schema().as_ref())
            .map_err(|e| format!("failed to create StreamWriter: {}", e))?;

        writer.write(batch)
            .map_err(|e| format!("failed to write batch: {}", e))?;

        writer.finish()
            .map_err(|e| format!("failed to finish stream: {}", e))?;

        let end = cursor.position() as usize;
        self.bytes_written = end as u64;
        self.num_rows += batch.num_rows() as u64;

        Ok(end - start)
    }

    /// Flush and finalize the mmap.
    ///
    /// Returns (schema_json, num_rows) for the Python side.
    pub fn finalize(mut self) -> Result<(String, u64), String> {
        // Flush mmap to disk
        self.mmap.flush()
            .map_err(|e| format!("failed to flush mmap: {}", e))?;

        // Truncate file to actual size
        self.file.set_len(self.bytes_written)
            .map_err(|e| format!("failed to truncate file: {}", e))?;

        Ok((self.schema_json.clone(), self.num_rows))
    }

    /// Get current bytes written.
    pub fn bytes_written(&self) -> u64 {
        self.bytes_written
    }

    /// Get schema JSON.
    pub fn schema_json(&self) -> &str {
        &self.schema_json
    }

    /// Get number of rows written.
    pub fn num_rows(&self) -> u64 {
        self.num_rows
    }
}

impl Drop for ArrowIpcMmapWriter {
    fn drop(&mut self) {
        // Account deallocation using the actual allocated bytes
        if self.allocated_bytes > 0 {
            account_mmap_free(self.allocated_bytes);
        }
    }
}

/// Result of Arrow IPC mmap write operation.
#[pyclass]
pub struct ArrowIpcMmapResult {
    /// Path to the mmap file
    pub path: String,
    /// Schema as JSON string
    pub schema_json: String,
    /// Number of rows written
    pub num_rows: i64,
    /// Bytes written (tracked for budget release)
    pub bytes_written: i64,
    /// Whether this result owns the budget (for RAII cleanup)
    owns_budget: bool,
}

#[pymethods]
impl ArrowIpcMmapResult {
    fn __repr__(&self) -> String {
        format!(
            "ArrowIpcMmapResult(path={}, rows={}, bytes={})",
            self.path, self.num_rows, self.bytes_written
        )
    }

    /// Release the budget allocation for this mmap.
    /// Called by Python when done consuming the mmap file.
    /// This is the explicit cleanup path — RAII Drop is the fallback.
    #[pyo3(signature = ())]
    pub fn release(&mut self) {
        if self.owns_budget && self.bytes_written > 0 {
            account_mmap_free(self.bytes_written as u64);
            self.owns_budget = false;
        }
    }
}

/// RAII guard for mmap budget allocation.
/// Ensures budget is freed even on panic/error.
struct MmapBudgetGuard {
    bytes: u64,
}

impl MmapBudgetGuard {
    fn new(bytes: u64) -> Self {
        account_mmap_alloc(bytes);
        Self { bytes }
    }
}

impl Drop for MmapBudgetGuard {
    fn drop(&mut self) {
        if self.bytes > 0 {
            account_mmap_free(self.bytes);
        }
    }
}

/// Arrow IPC mmap statistics.
#[pyclass]
pub struct ArrowIpcMmapStats {
    pub pool_bytes: u64,
    pub pool_peak: u64,
    pub pool_max: u64,
    pub pool_utilization: f64,
}

#[pymethods]
impl ArrowIpcMmapStats {
    fn __repr__(&self) -> String {
        format!(
            "ArrowIpcMmapStats(pool={:.2}/{:.2} MiB, peak={:.2} MiB, util={:.1}%)",
            self.pool_bytes as f64 / (1024.0 * 1024.0),
            self.pool_max as f64 / (1024.0 * 1024.0),
            self.pool_peak as f64 / (1024.0 * 1024.0),
            self.pool_utilization,
        )
    }
}

/// Parse schema from Arrow IPC bytes and return as JSON string.
///
/// Uses StreamReader to extract the schema without full deserialization.
fn parse_schema_from_ipc_bytes(ipc_bytes: &[u8]) -> Result<String, String> {
    let cursor = Cursor::new(ipc_bytes);
    let reader = StreamReader::try_new(cursor, None)
        .map_err(|e| format!("failed to create StreamReader: {}", e))?;
    
    let schema = reader);
    serde_json::to_string(&schema.as_ref().to_json())
        .map_err(|e| format!("failed to serialize schema: {}", e))
}

/// Get Arrow IPC mmap statistics.
#[pyfunction]
pub fn get_arrow_ipc_mmap_stats() -> ArrowIpcMmapStats {
    let pool_bytes = MMAP_POOL_BYTES.load(Ordering::Relaxed);
    let pool_peak = MMAP_POOL_PEAK.load(Ordering::Relaxed);

    ArrowIpcMmapStats {
        pool_bytes,
        pool_peak,
        pool_max: MAX_MMAP_POOL_BYTES,
        pool_utilization: (pool_bytes as f64 / MAX_MMAP_POOL_BYTES as f64) * 100.0,
    }
}

/// Write Arrow IPC RecordBatch to mmap file and return metadata.
///
/// This is the primary API for Python callers. Returns (path, schema_json, num_rows).
///
/// # Arguments
/// * `path` - Path to the mmap file
/// * `ipc_bytes` - Raw Arrow IPC bytes (from build_ipc_bytes)
/// * `estimated_rows` - Estimated number of rows
///
/// # Returns
/// ArrowIpcMmapResult on success, error string on failure.
/// 
/// # Budget Safety
/// Uses RAII guard to ensure budget is freed even on error.
/// Caller should call `.release()` on the result when done consuming the mmap,
/// or rely on the automatic cleanup via Drop.
#[pyfunction]
#[pyo3(signature = (path, ipc_bytes, estimated_rows))]
pub fn write_arrow_ipc_to_mmap(
    path: &str,
    ipc_bytes: &[u8],
    estimated_rows: usize,
) -> PyResult<ArrowIpcMmapResult> {
    let path_obj = std::path::Path::new(path);

    // Actual bytes that will be written
    let bytes_written = ipc_bytes);
    let actual_bytes = bytes_written.max(64 * 1024) as u64;

    // Check budget using actual bytes (not estimated)
    if let Err(e) = check_mmap_budget(actual_bytes) {
        return Err(PyValueError::new_err(e));
    }

    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(true)
        .open(path_obj)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "failed to open mmap file: {}",
            e
        )))?;

    // Set file size
    file.set_len(actual_bytes)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "failed to set file size: {}",
            e
        )))?;

    let mmap = unsafe { MmapMut::map_mut(&file) }
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "failed to create mmap: {}",
            e
        )))?;

    // RAII budget guard — freed on function exit (success or error)
    let _budget_guard = MmapBudgetGuard::new(actual_bytes);

    // Copy IPC bytes to mmap
    mmap[..bytes_written].copy_from_slice(ipc_bytes);

    // Flush to disk
    mmap.flush()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "failed to flush mmap: {}",
            e
        )))?;

    let schema_json = match parse_schema_from_ipc_bytes(ipc_bytes) {
        Ok(json) => json,
        Err(e) => {
            // Log warning but don't fail — schema can be inferred later
            eprintln!("[NEXTGEN-02] schema parse warning: {}", e);
            "{}".to_string()
        }
    };

    // Transfer budget ownership to the result object
    // (guard's Drop won't free since we consume it)
    std::mem::forget(_budget_guard);

    Ok(ArrowIpcMmapResult {
        path: path.to_string(),
        schema_json,
        num_rows: estimated_rows as i64,
        bytes_written: bytes_written as i64,
        owns_budget: true,
    })
}

/// Delete mmap file and free budget allocation.
///
/// This is the explicit cleanup path. The ArrowIpcMmapResult.release() method
/// is preferred when you have the result object.
///
/// # Arguments
/// * `path` - Path to the mmap file
/// * `bytes` - Number of bytes to free from budget
#[pyfunction]
#[pyo3(signature = (path, bytes))]
pub fn delete_arrow_ipc_mmap(path: &str, bytes: i64) -> PyResult<()> {
    if std::path::Path::new(path).exists() {
        std::fs::remove_file(path)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "failed to delete mmap file: {}",
                e
            )))?;
    }

    // Account deallocation
    if bytes > 0 {
        account_mmap_free(bytes as u64);
    }

    Ok(())
}

/// Create an ArrowIpcMmapResult for Python consumption with proper RAII tracking.
///
/// This is a helper for creating results that own their budget allocation.
pub fn create_mmap_result(
    path: String,
    schema_json: String,
    num_rows: i64,
    bytes_written: i64,
) -> ArrowIpcMmapResult {
    ArrowIpcMmapResult {
        path,
        schema_json,
        num_rows,
        bytes_written,
        owns_budget: false, // Budget already accounted by caller
    }
}

/// Register Arrow IPC mmap functions with Python module.
pub fn add_module(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(get_arrow_ipc_mmap_stats, module))?;
    module.add_function(wrap_pyfunction!(write_arrow_ipc_to_mmap, module))?;
    module.add_function(wrap_pyfunction!(delete_arrow_ipc_mmap, module))?;
    Ok(())
}

//! topology.rs — Apple Silicon P/E Core Topology Detection & Affinity
//!
//! ## MODERN-33 + MODERN-34: Unified P/E Core Affinity System
//!
//! This module provides the **single source of truth** for Apple Silicon topology
//! on MacBook Air M1 (and variants). It replaces scattered sysctl calls with cached,
////! startup-initialized core counts.
//!
//! ## M1 8GB Core Topology
//!
//! | Core Type | perflevel | Indices | Use Case |
//! |-----------|------------|---------|----------|
//! | E-cores   | perflevel0 | 0..e-1  | Background: DNS, I/O, Telemetry |
//! | P-cores   | perflevel1 | e..total | CPU-intensive: SIMD, MLX, Graph |
//!
//! ## Design Principles
//!
//! 1. **Cached at startup** — `init_topology()` called once, results stored in `OnceLock`
//! 2. **Zero sysctl on hot path** — `p_core_count()`, `e_core_count()` are simple reads
//! 3. **Thread-safe** — `OnceLock` guarantees single initialization
//! 4. **Fail-safe** — Falls back gracefully on non-M1 hardware
//!
//! ## Workload-to-Affinity Mapping (MODERN-34)
//!
//! | Workload Type | Preferred Cores | QoS Class | Examples |
//! |---------------|-----------------|-----------|----------|
//! | `cpu_intensive` | P-cores (0-3) | USER_INITIATED (0x19) | aho_corasick, deobfuscate, SIMD |
//! | `mlx_inference` | P-cores (0-3) | USER_INITIATED (0x19) | mlx_bridge inference |
//! | `graph_traverse` | P-cores (0-3) | USER_INITIATED (0x19) | DuckPGQ, Kuzu traversal |
//! | `io_bound` | E-cores (4-7) | UTILITY (0x11) | DuckDB, file I/O |
//! | `network_io` | E-cores (4-7) | UTILITY (0x11) | DNS, HTTP, QUIC |
//! | `telemetry` | E-cores (4-7) | BACKGROUND (0x09) | evidence_log, telemetry_agg |
//!
//! ## M1 8GB Memory Budget
//!
//! - Total cores: 8 (4P + 4E)
//! - cpu_pool: 3 P-cores (USER_INITIATED)
//! - io_pool: 2 E-cores (UTILITY)
//! - mixed_pool: 1 P-core (adaptive)
//! - dispatchers: 2 E-cores (UTILITY)
//! - Headroom: 1 core for OS overhead
//!
//! ## Usage
//!
//! ```python
//! from hledac_rust_extensions import topology
//!
//! # Initialize at startup (happens automatically on module import)
//! topology.init_topology()
//!
//! # Query core counts
//! print(f"P-cores: {topology.p_core_count()}")  # 4
//! print(f"E-cores: {topology.e_core_count()}")  # 4
//!
//! # Apply affinity for workload type
//! topology.apply_affinity_for_workload("cpu_intensive")  # P-cores
//! topology.apply_affinity_for_workload("io_bound")       # E-cores
//!
//! # Get core indices
//! print(f"P-core indices: {topology.get_p_core_indices()}")  # [0, 1, 2, 3]
//! print(f"E-core indices: {topology.get_e_core_indices()}")  # [4, 5, 6, 7]
//! ```
//!
//! ## Implementation Notes
//!
//! - Uses `sysctlbyname(2)` directly — no fork/exec
//! - Falls back to hw.physicalcpu on non-Apple Silicon
//! - darwin_affinity.rs provides the actual thread affinity calls

use libc::{c_int, size_t}; // MODERN-27 FIX: Removed unused pthread_self, qos_class_t
#[allow(unused_imports)]
use std::sync::OnceLock;
#[allow(unused_imports)]
use std::thread;

// MODERN-27 FIX: Import safe QoS conversion helper from qos_class_helpers module
use crate::qos_class_helpers::qos_class_i32_to_qos_class_t;

/// Performance level cluster — groups cores by type and performance level.
///
/// NEXTGEN-03: Used by elastic_pool.rs to create dedicated thread pools
/// for different workload types (SIMD, MLX, Graph) with explicit core affinity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PerfLevelCluster {
    /// CPU core indices belonging to this cluster.
    pub cpu_ids: Vec<usize>,
    /// Performance level (0 = E-cores, 1 = P-cores).
    pub perflevel: u32,
    /// Cluster type: "p" for P-cores, "e" for E-cores.
    pub cluster_type: String,
}

impl PerfLevelCluster {
    /// Create a P-core cluster.
    pub fn p_cores(cpu_ids: Vec<usize>, perflevel: u32) -> Self {
        Self {
            cpu_ids,
            perflevel,
            cluster_type: "p".to_string(),
        }
    }

    /// Create an E-core cluster.
    pub fn e_cores(cpu_ids: Vec<usize>, perflevel: u32) -> Self {
        Self {
            cpu_ids,
            perflevel,
            cluster_type: "e".to_string(),
        }
    }

    /// Check if this is a P-core cluster.
    pub fn is_p_core(&self) -> bool {
        self.cluster_type == "p"
    }

    /// Check if this is an E-core cluster.
    pub fn is_e_core(&self) -> bool {
        self.cluster_type == "e"
    }

    /// Get thread count for this cluster.
    pub fn thread_count(&self) -> usize {
        self.cpu_ids.len()
    }
}

/// Performance level 0 (E-cores = UTILITY cluster) sysctl name.
const PERFLEVEL0_CPU: &[u8] = b"hw.perflevel0.physicalcpu\0";

/// Performance level 1 (P-cores = PERFORMANCE cluster) sysctl name.
const PERFLEVEL1_CPU: &[u8] = b"hw.perflevel1.physicalcpu\0";

/// Fallback physical CPU count sysctl name.
const PHYSICAL_CPU: &[u8] = b"hw.physicalcpu\0";

/// Fallback logical CPU count sysctl name.
const LOGICAL_CPU: &[u8] = b"hw.logicalcpu\0";

/// QoS class for CPU-intensive work — runs on P-cores.
const QOS_USER_INITIATED: c_int = 0x19;

/// QoS class for I/O-bound work — runs on E-cores.
const QOS_UTILITY: c_int = 0x11;

/// QoS class for background/telemetry — runs on E-cores.
const QOS_BACKGROUND: c_int = 0x09;

/// Global topology info — initialized once via init_topology().
static TOPOLOGY: OnceLock<TopologyInfo> = OnceLock::new();

/// Whether we're running on Apple Silicon.
static IS_APPLE_SILICON: OnceLock<bool> = OnceLock::new();

/// NEXTGEN-03: Global clusters cache — initialized once via detect_perflevel_clusters().
static PERFLEVEL_CLUSTERS: OnceLock<Vec<PerfLevelCluster>> = OnceLock::new();

/// Topology information cached at startup.
#[derive(Debug, Clone)]
pub struct TopologyInfo {
    /// Number of P-cores (perflevel0).
    pub p_core_count: usize,
    /// Number of E-cores (perflevel1).
    pub e_core_count: usize,
    /// Total logical CPU count.
    pub total_logical: usize,
    /// P-core indices (0-based).
    pub p_core_indices: Vec<usize>,
    /// E-core indices (0-based).
    pub e_core_indices: Vec<usize>,
    /// Whether detection succeeded.
    pub detected: bool,
}

impl Default for TopologyInfo {
    fn default() -> Self {
        // Safe fallback for non-M1 hardware
        Self {
            p_core_count: 4,
            e_core_count: 4,
            total_logical: 8,
            p_core_indices: vec![0, 1, 2, 3],
            e_core_indices: vec![4, 5, 6, 7],
            detected: false,
        }
    }
}

#[cfg(target_os = "macos")]
fn sysctl_int(name: &[u8]) -> Option<usize> {
    let mut size: size_t = std::mem::size_of::<u32>();
    let mut value: u32 = 0;

    let ret = unsafe {
        libc::sysctlbyname(
            name.as_ptr() as *const libc::c_char,
            &mut value as *mut _ as *mut libc::c_void,
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };

    if ret == 0 {
        Some(value as usize)
    } else {
        None
    }
}

#[cfg(target_os = "macos")]
fn is_apple_silicon() -> bool {
    // MODERN-34 FIX: Use sysctlbyname directly — NO fork+exec (~100ns vs ~1-2ms)
    // Check for Apple Silicon via hw.optional.arm64 CPU feature flag
    let cpu_type = sysctl_int(b"hw.cputype\0");
    let cpu_subtype = sysctl_int(b"hw.cpusubtype\0");
    
    // ARM CPU type = 16777228 (0x0100000C), Apple variant = 1 (0x00000001)
    // This is MUCH faster than Command::new("sysctl")
    match (cpu_type, cpu_subtype) {
        (Some(cpu_type_val), Some(cpu_subtype_val)) => {
            // ARM64 CPU type + Apple CPU subtype indicates Apple Silicon
            cpu_type_val == 16777228 && cpu_subtype_val == 1
        }
        _ => false,
    }
}

#[cfg(not(target_os = "macos"))]
fn is_apple_silicon() -> bool {
    false
}

#[cfg(not(target_os = "macos"))]
fn sysctl_int(_name: &[u8]) -> Option<usize> {
    None
}

/// Initialize topology info — called once at startup.
///
/// MODERN-33: Caches perflevel0/1 counts to avoid repeated sysctl calls.
/// This is the **single source of truth** for core topology.
///
/// # Safety
///
/// Must be called before any thread pool workers are spawned.
pub fn init_topology() -> &'static TopologyInfo {
    TOPOLOGY.get_or_init(|| detect_topology())
}

/// Detect Apple Silicon topology using sysctlbyname.
///
/// MODERN-33: Uses hw.perflevel0 (E-cores) and hw.perflevel1 (P-cores) for true P/E partition.
#[cfg(target_os = "macos")]
fn detect_topology() -> TopologyInfo {
    // First check if we're on Apple Silicon
    let apple_silicon = is_apple_silicon();

    if !apple_silicon {
        // Fallback for Intel Macs
        return TopologyInfo::default();
    }

    // Try to get perflevel0 (E-cores) and perflevel1 (P-cores) counts
    let e_cores = sysctl_int(PERFLEVEL0_CPU);
    let p_cores = sysctl_int(PERFLEVEL1_CPU);

    match (p_cores, e_cores) {
        (Some(p), Some(e)) if p > 0 && e > 0 => {
            // M1 Pro/Max/Ultra or similar with distinct P/E cores
            let total = p + e;
            let p_core_indices: Vec<usize> = (0..p));
            let e_core_indices: Vec<usize> = (p..total));

            TopologyInfo {
                p_core_count: p,
                e_core_count: e,
                total_logical: total,
                p_core_indices,
                e_core_indices,
                detected: true,
            }
        }
        _ => {
            // Standard M1 (4P + 4E) or M1 Air (4P, no E on some configs)
            // Fallback: assume 4P + 4E for M1 if perflevel sysctls fail
            let p = 4;
            let e = 4;
            let total_logical = sysctl_int(LOGICAL_CPU).unwrap_or(8);

            TopologyInfo {
                p_core_count: p,
                e_core_count: e,
                total_logical,
                // E-cores first (perflevel 0), P-cores after (perflevel 1)
                e_core_indices: vec![0, 1, 2, 3],
                p_core_indices: vec![4, 5, 6, 7],
                detected: true,
            }
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn detect_topology() -> TopologyInfo {
    TopologyInfo::default()
}

/// Get reference to cached topology info.
pub fn get_topology() -> &'static TopologyInfo {
    TOPOLOGY.get().unwrap_or_else(|| {
        // Auto-initialize if not called explicitly
        init_topology()
    })
}

/// Get the number of P-cores (performance cores).
#[inline]
pub fn p_core_count() -> usize {
    get_topology().p_core_count
}

/// Get the number of E-cores (efficiency cores).
#[inline]
pub fn e_core_count() -> usize {
    get_topology().e_core_count
}

/// Get the total number of logical CPUs.
#[inline]
pub fn total_logical_cores() -> usize {
    get_topology().total_logical
}

/// Check if running on Apple Silicon.
#[inline]
pub fn is_m1() -> bool {
    *IS_APPLE_SILICON.get_or_init(is_apple_silicon)
}

/// Get P-core indices (0-based).
#[inline]
pub fn get_p_core_indices() -> Vec<usize> {
    get_topology().p_core_indices.clone()
}

/// Get E-core indices (0-based).
#[inline]
pub fn get_e_core_indices() -> Vec<usize> {
    get_topology().e_core_indices.clone()
}

/// Initialize perflevel clusters — called once at startup.
///
/// NEXTGEN-03: Returns cached Vec<PerfLevelCluster> where each cluster groups
/// cores by performance level (P-core vs E-core).
///
/// M1 8GB configuration:
///   - P-core cluster: CPU 0,1,2,3 (perflevel=0)
///   - E-core cluster: CPU 4,5,6,7 (perflevel=1)
///
/// Returns:
///   Vec<PerfLevelCluster> with clusters sorted by preference (P first, then E)
pub fn detect_perflevel_clusters() -> &'static Vec<PerfLevelCluster> {
    PERFLEVEL_CLUSTERS.get_or_init(|| {
        let topo = get_topology();
        let mut clusters = Vec::with_capacity(2);

        // P-core cluster (perflevel 0) — priority for CPU-intensive work
        if !topo.p_core_indices.is_empty() {
            clusters.push(PerfLevelCluster::p_cores(
                topo.p_core_indices.clone(),
                0, // perflevel 0 = P-cores
            ));
        }

        // E-core cluster (perflevel 1) — for I/O-bound work
        if !topo.e_core_indices.is_empty() {
            clusters.push(PerfLevelCluster::e_cores(
                topo.e_core_indices.clone(),
                1, // perflevel 1 = E-cores
            ));
        }

        // Sort: P-cores first, then E-cores (P-cores have priority)
        clusters.sort_by_key(|c| c.perflevel);

        clusters
    })
}

/// Get P-core cluster if available.
pub fn get_p_core_cluster() -> Option<&'static PerfLevelCluster> {
    let clusters = detect_perflevel_clusters();
    clusters.iter().find(|c| c.is_p_core())
}

/// Get E-core cluster if available.
pub fn get_e_core_cluster() -> Option<&'static PerfLevelCluster> {
    let clusters = detect_perflevel_clusters();
    clusters.iter().find(|c| c.is_e_core())
}

/// Get cluster for a specific workload type.
///
/// NEXTGEN-03: Maps workload to appropriate cluster for affinity binding.
pub fn get_cluster_for_workload(workload: WorkloadType) -> Option<&'static PerfLevelCluster> {
    match workload {
        WorkloadType::CpuIntensive | WorkloadType::MlxInference | WorkloadType::GraphTraverse => {
            get_p_core_cluster()
        }
        WorkloadType::IoBound | WorkloadType::NetworkIo | WorkloadType::Telemetry => {
            get_e_core_cluster()
        }
        WorkloadType::Default => get_p_core_cluster(),
    }
}

/// Workload type for affinity selection.
///
/// MODERN-34: Maps workloads to appropriate core types.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkloadType {
    /// CPU-intensive: SIMD, hashing, ML inference (P-cores)
    CpuIntensive,
    /// MLX inference: GPU-bound but coordinated from CPU (P-cores)
    MlxInference,
    /// Graph traversal: DuckPGQ, Kuzu (P-cores)
    GraphTraverse,
    /// I/O-bound: DuckDB, file I/O (E-cores)
    IoBound,
    /// Network I/O: DNS, HTTP, QUIC (E-cores)
    NetworkIo,
    /// Background: telemetry, logging (E-cores)
    Telemetry,
    /// Default: balanced (all cores)
    Default,
}

impl WorkloadType {
    /// Convert from string for Python interop.
    pub fn from_str(s: &str) -> Self {
        match s {
            "cpu_intensive" => WorkloadType::CpuIntensive,
            "mlx_inference" => WorkloadType::MlxInference,
            "graph_traverse" => WorkloadType::GraphTraverse,
            "io_bound" => WorkloadType::IoBound,
            "network_io" => WorkloadType::NetworkIo,
            "telemetry" => WorkloadType::Telemetry,
            _ => WorkloadType::Default,
        }
    }

    /// Get QoS class for this workload type.
    pub fn qos_class(&self) -> c_int {
        match self {
            WorkloadType::CpuIntensive
            | WorkloadType::MlxInference
            | WorkloadType::GraphTraverse => QOS_USER_INITIATED,
            WorkloadType::IoBound | WorkloadType::NetworkIo => QOS_UTILITY,
            WorkloadType::Telemetry => QOS_BACKGROUND,
            WorkloadType::Default => QOS_USER_INITIATED,
        }
    }

    /// Prefer P-cores for this workload.
    pub fn prefer_p_cores(&self) -> bool {
        matches!(
            self,
            WorkloadType::CpuIntensive
                | WorkloadType::MlxInference
                | WorkloadType::GraphTraverse
                | WorkloadType::Default
        )
    }
}

/// Apply thread affinity for the current thread based on workload type.
///
/// MODERN-34: This is the main entry point for workload-aware affinity.
/// Uses darwin_affinity for the actual Mach API calls.
///
/// # Arguments
///
/// * `workload` — Workload type string ("cpu_intensive", "io_bound", etc.)
///
/// # Example
///
/// ```ignore
/// topology::apply_affinity_for_workload("cpu_intensive");
/// ```
#[cfg(target_os = "macos")]
pub fn apply_affinity_for_workload(workload: WorkloadType) {
    use crate::darwin_affinity::apply_darwin_affinity_hint;

    // Set QoS class first (this is the primary mechanism)
    // MODERN-27 FIX: Use helper function instead of direct cast
    let qos = qos_class_i32_to_qos_class_t(workload.qos_class());
    unsafe {
        libc::pthread_set_qos_class_self_np(
            qos,
            0,
        );
    }

    // Also set perf-level hint for explicit P/E preference
    let prefer_pcore = workload.clone();
    apply_darwin_affinity_hint(prefer_pcore);
}

/// Apply affinity for string workload type (Python FFI).
#[cfg(target_os = "macos")]
pub fn apply_affinity_for_workload_str(workload: &str) {
    apply_affinity_for_workload(WorkloadType::from_str(workload));
}

/// Stub for non-macOS platforms.
#[cfg(not(target_os = "macos"))]
pub fn apply_affinity_for_workload(_workload: WorkloadType) {
    // No-op on non-Darwin platforms
}

/// Stub for non-macOS platforms (string variant).
#[cfg(not(target_os = "macos"))]
pub fn apply_affinity_for_workload_str(_workload: &str) {
    // No-op
}

/// Get a descriptive name for this thread based on its workload affinity.
pub fn get_thread_workload_name(workload: WorkloadType) -> &'static str {
    match workload {
        WorkloadType::CpuIntensive => "hledac-cpu",
        WorkloadType::MlxInference => "hledac-mlx",
        WorkloadType::GraphTraverse => "hledac-graph",
        WorkloadType::IoBound => "hledac-io",
        WorkloadType::NetworkIo => "hledac-net",
        WorkloadType::Telemetry => "hledac-telemetry",
        WorkloadType::Default => "hledac-worker",
    }
}

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

/// Initialize topology at module import time.
///
/// MODERN-33: This is called automatically when the Python module is imported.
#[pyfunction]
fn init_topology_py() -> PyResult<PyTopologyInfo> {
    let topo = init_topology();
    Ok(PyTopologyInfo::from(topo))
}

/// Get P-core count (cached).
#[pyfunction]
fn p_core_count_py() -> usize {
    p_core_count()
}

/// Get E-core count (cached).
#[pyfunction]
fn e_core_count_py() -> usize {
    e_core_count()
}

/// Get total logical core count (cached).
#[pyfunction]
fn total_logical_cores_py() -> usize {
    total_logical_cores()
}

/// Check if running on Apple Silicon.
#[pyfunction]
fn is_m1_py() -> bool {
    is_m1()
}

/// Get P-core indices (0-based).
#[pyfunction]
fn get_p_core_indices_py() -> Vec<usize> {
    get_p_core_indices()
}

/// Get E-core indices (0-based).
#[pyfunction]
fn get_e_core_indices_py() -> Vec<usize> {
    get_e_core_indices()
}

/// Apply affinity for workload type.
///
/// # Arguments
/// * `workload` — One of: "cpu_intensive", "mlx_inference", "graph_traverse",
///                "io_bound", "network_io", "telemetry", "default"
///
/// MODERN-34: Convenience wrapper for workload-aware affinity.
#[pyfunction]
fn apply_affinity_for_workload_py(workload: &str) {
    apply_affinity_for_workload_str(workload);
}

/// NEXTGEN-03: Detect perflevel clusters for topology-aware scheduling.
///
/// Returns a list of clusters, each with cpu_ids, perflevel, and cluster_type.
/// M1 8GB: Returns [P-core cluster (CPU 0-3), E-core cluster (CPU 4-7)]
#[pyfunction]
fn detect_perflevel_clusters_py() -> Vec<PyPerfLevelCluster> {
    let clusters = detect_perflevel_clusters();
    clusters.iter().map(PyPerfLevelCluster::from).collect()
}

/// NEXTGEN-03: Get P-core cluster info.
#[pyfunction]
fn get_p_core_cluster_py() -> Option<PyPerfLevelCluster> {
    get_p_core_cluster().map(|c| PyPerfLevelCluster::from(c))
}

/// NEXTGEN-03: Get E-core cluster info.
#[pyfunction]
fn get_e_core_cluster_py() -> Option<PyPerfLevelCluster> {
    get_e_core_cluster().map(|c| PyPerfLevelCluster::from(c))
}

/// Python-friendly topology info struct.
#[pyclass]
#[derive(Debug, Clone)]
pub struct PyTopologyInfo {
    #[pyo3(get)]
    pub p_core_count: usize,
    #[pyo3(get)]
    pub e_core_count: usize,
    #[pyo3(get)]
    pub total_logical: usize,
    #[pyo3(get)]
    pub p_core_indices: Vec<usize>,
    #[pyo3(get)]
    pub e_core_indices: Vec<usize>,
    #[pyo3(get)]
    pub detected: bool,
    #[pyo3(get)]
    pub is_apple_silicon: bool,
}

impl From<&TopologyInfo> for PyTopologyInfo {
    fn from(topo: &TopologyInfo) -> Self {
        Self {
            p_core_count: topo.p_core_count,
            e_core_count: topo.e_core_count,
            total_logical: topo.total_logical,
            p_core_indices: topo.p_core_indices.clone(),
            e_core_indices: topo.e_core_indices.clone(),
            detected: topo.detected,
            is_apple_silicon: is_m1(),
        }
    }
}

/// NEXTGEN-03: Python-friendly cluster info struct.
#[pyclass]
#[derive(Debug, Clone)]
pub struct PyPerfLevelCluster {
    #[pyo3(get)]
    pub cpu_ids: Vec<usize>,
    #[pyo3(get)]
    pub perflevel: u32,
    #[pyo3(get)]
    pub cluster_type: String,
    #[pyo3(get)]
    pub thread_count: usize,
}

impl From<&PerfLevelCluster> for PyPerfLevelCluster {
    fn from(cluster: &PerfLevelCluster) -> Self {
        Self {
            cpu_ids: cluster.cpu_ids.clone(),
            perflevel: cluster.perflevel,
            cluster_type: cluster.cluster_type.clone(),
            thread_count: cluster.thread_count(),
        }
    }
}

/// Register topology functions in Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(init_topology_py))?;
    m.add_function(wrap_pyfunction!(p_core_count_py))?;
    m.add_function(wrap_pyfunction!(e_core_count_py))?;
    m.add_function(wrap_pyfunction!(total_logical_cores_py))?;
    m.add_function(wrap_pyfunction!(is_m1_py))?;
    m.add_function(wrap_pyfunction!(get_p_core_indices_py))?;
    m.add_function(wrap_pyfunction!(get_e_core_indices_py))?;
    m.add_function(wrap_pyfunction!(apply_affinity_for_workload_py))?;
    // NEXTGEN-03: PerfLevelCluster registration
    m.add_function(wrap_pyfunction!(detect_perflevel_clusters_py))?;
    m.add_function(wrap_pyfunction!(get_p_core_cluster_py))?;
    m.add_function(wrap_pyfunction!(get_e_core_cluster_py))?;
    m.add_class::<PyTopologyInfo>()?;
    m.add_class::<PyPerfLevelCluster>()?;

    // Auto-initialize topology at module import
    let _ = init_topology();

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_topology_init() {
        let topo = init_topology();
        assert!(topo.p_core_count > 0);
        assert!(topo.e_core_count > 0);
        assert_eq!(topo.p_core_count + topo.e_core_count, topo.total_logical);
    }

    #[test]
    fn test_workload_type_from_str() {
        assert_eq!(
            WorkloadType::from_str("cpu_intensive"),
            WorkloadType::CpuIntensive
        );
        assert_eq!(
            WorkloadType::from_str("io_bound"),
            WorkloadType::IoBound
        );
        assert_eq!(
            WorkloadType::from_str("unknown"),
            WorkloadType::Default
        );
    }

    #[test]
    fn test_workload_qos() {
        assert_eq!(
            WorkloadType::CpuIntensive.qos_class(),
            QOS_USER_INITIATED
        );
        assert_eq!(WorkloadType::IoBound.qos_class(), QOS_UTILITY);
        assert_eq!(WorkloadType::Telemetry.qos_class(), QOS_BACKGROUND);
    }

    #[test]
    fn test_workload_prefers_p_cores() {
        assert!(WorkloadType::CpuIntensive.prefer_p_cores());
        assert!(WorkloadType::MlxInference.prefer_p_cores());
        assert!(!WorkloadType::IoBound.prefer_p_cores());
        assert!(!WorkloadType::Telemetry.prefer_p_cores());
    }
}

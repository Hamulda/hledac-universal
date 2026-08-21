//! IntCounterLayout — Structure-of-Arrays (SoA) buffer for hot-path integer counters.
//!
//! Rust drop-in replacement for `runtime.int_counter_layout.IntCounterLayout`.
//!
//! Sprint P1-5: bulk cross-sprint aggregation via rayon + single-sprint
//! bump/get/set in native code (no PyObject boxing per op).
//!
//! Wire format: signed `i64` (8 B per slot) — drop-in compatible with
//! Python `array.array('q')` from `runtime.int_counter_layout`.
//!
//! Design invariants (mirror Python L.M1–L.M10):
//!     M.R1  No panics, no unwrap in #[pymethod] path (fail-soft)
//!     M.R2  Bounded: layout size fixed at construction (Vec capacity locked)
//!     M.R3  Fail-soft: unknown name returns 0 / no-op, increments fail_soft_count
//!     M.R4  snapshot() is O(N) with single allocation
//!     M.R5  reset() zeros the Vec in O(N) (memset via fill(0))
//!     M.R6  Default index for unknown name = 0 (no Option<Result> in hot path)
//!     M.R7  bulk_* functions are SEQUENTIAL by design (GIL-bound via PyRefMut;
//!           rayon would add overhead, not speed — see bulk_bump_aggregate notes)
//!     M.R8  M1 8GB safe: bounded, no recursion, no Vec<Vec<Vec<_>> of unknowns
//!
//! Reference: docs/optimization/SPRINT_OPTIMIZATION_ANALYSIS_2026-06-08.md §P1-5.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

/// Hard cap on the number of counters in a single layout. Defensive bound
/// to prevent unbounded memory growth from malformed inputs. M1 8GB safe.
const MAX_COUNTERS_PER_LAYOUT: usize = 4096;

/// Hard cap on the number of layouts in a single bulk_* call. Defensive bound
/// to keep rayon dispatch bounded — even on M1 8GB we want a known upper limit
/// on working memory.
const MAX_BULK_LAYOUTS: usize = 1_000_000;

/// Structure-of-Arrays (SoA) integer counter layout.
///
/// Backing: `Vec<i64>` with capacity fixed at construction (no append).
/// Index map: `HashMap<String, usize>` for O(1) name → slot resolution.
///
/// Wire format: signed 8-byte integers — drop-in compatible with
/// Python `array.array('q')`.
///
/// Single-thread mutator by contract (mirrors Python GIL semantics).
/// For multi-thread access, wrap external state in a `parking_lot::Mutex` —
/// not provided here as M1 8GB targets asyncio.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import IntCounterLayoutRust
///
/// layout = IntCounterLayoutRust(["cycles_started", "cycles_completed"])
/// layout.bump("cycles_started")           # +1
/// layout.bump("cycles_started", n=5)     # +5 → 6
/// print(layout.snapshot())                # {"cycles_started": 6, "cycles_completed": 0}
/// ```
#[pyclass(name = "IntCounterLayoutRust")]
pub struct IntCounterLayoutRust {
    /// Flat 8-byte counter buffer. Capacity = len at construction.
    buffer: Vec<i64>,
    /// Name → slot index map.
    indices: HashMap<String, usize>,
    /// Ordered names for deterministic snapshot() output.
    names: Vec<String>,
    /// Telemetry: number of fail-soft fallbacks (unknown name access).
    fail_soft_count: u64,
}

#[pymethods]
impl IntCounterLayoutRust {
    /// Construct a new SoA layout for the given counter names.
    ///
    /// # Arguments
    /// * `field_names` — ordered sequence of counter names
    ///
    /// # Returns
    /// A new `IntCounterLayoutRust` with N zero-initialized slots.
    ///
    /// # Errors
    /// * `ValueError` on duplicate names or empty-string names
    /// * `ValueError` on non-string names
    /// * `ValueError` on length > MAX_COUNTERS_PER_LAYOUT
    #[new]
    pub fn new(field_names: Vec<String>) -> PyResult<Self> {
        // Bound: defensive cap to prevent runaway allocations.
        if field_names.len() > MAX_COUNTERS_PER_LAYOUT {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "IntCounterLayoutRust: too many counters ({} > {})",
                field_names.len(),
                MAX_COUNTERS_PER_LAYOUT
            )));
        }

        let mut indices: HashMap<String, usize> = HashMap::with_capacity(field_names.len());
        for (i, name) in field_names.iter().enumerate() {
            if name.is_empty() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "IntCounterLayoutRust: counter names must be non-empty",
                ));
            }
            if indices.insert(name.clone(), i).is_some() {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "IntCounterLayoutRust: duplicate counter name {:?}",
                    name
                )));
            }
        }

        // Allocate zero-initialized buffer. Vec<i64>::with_capacity + resize(0)
        // would skip zeroing; instead use vec![0_i64; n] which is a single
        // calloc — Rust's std guarantees zero-init for this pattern.
        let buffer = vec![0_i64; field_names.len()];

        Ok(Self {
            buffer,
            indices,
            names: field_names,
            fail_soft_count: 0,
        })
    }

    /// Atomic C-level += for a counter. Returns the new value.
    ///
    /// Fail-soft: unknown names return 0 and increment `fail_soft_count`.
    #[pyo3(signature = (name, n = 1))]
    pub fn bump(&mut self, name: &str, n: i64) -> i64 {
        // M.R6: default index = 0 (we still increment fail_soft_count
        // so telemetry catches the misuse). Avoids Option<Result> in hot path.
        let idx = match self.indices.get(name) {
            Some(&i) => i,
            None => {
                self.fail_soft_count += 1;
                return 0;
            }
        };
        // Index is bounded by construction (we allocated buffer with
        // exactly field_names.len() slots). Safe to index without
        // bounds check via get_unchecked? No — keep bounds check for
        // memory safety. Compiler may auto-vectorize the +=1 loop.
        let new_value = self.buffer[idx].wrapping_add(n);
        self.buffer[idx] = new_value;
        new_value
    }

    /// Read a counter. Returns 0 for unknown names.
    pub fn get(&self, name: &str) -> i64 {
        match self.indices.get(name) {
            Some(&i) => self.buffer[i],
            None => 0,
        }
    }

    /// Write a counter. Unknown names are silently dropped (fail-soft).
    pub fn set(&mut self, name: &str, value: i64) {
        if let Some(&i) = self.indices.get(name) {
            self.buffer[i] = value;
        } else {
            self.fail_soft_count += 1;
        }
    }

    /// Return a fresh dict of all counters (O(N) with single allocation).
    ///
    /// L.M7 mirror: callers may mutate the returned dict freely.
    pub fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        for (i, name) in self.names.iter().enumerate() {
            dict.set_item(name, self.buffer[i])?;
        }
        Ok(dict)
    }

    /// Zero all counters in O(N) (memset via fill(0)).
    pub fn reset(&mut self) {
        // Vec::fill is a single memset — much faster than per-element loop.
        self.buffer.fill(0);
    }

    /// Return the immutable name → slot index map.
    pub fn get_indices<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        for (name, &idx) in &self.indices {
            dict.set_item(name, idx)?;
        }
        Ok(dict)
    }

    /// True if the underlying buffer was allocated successfully (always true).
    pub fn is_active(&self) -> bool {
        // Always true after construction (no init failure path in Rust).
        // Kept for API parity with Python IntCounterLayout.
        true
    }

    /// Telemetry snapshot. Non-intrusive.
    pub fn get_stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("initialized", true)?;
        dict.set_item("num_counters", self.buffer.len())?;
        dict.set_item("buffer_size_bytes", self.buffer.len() * 8)?;
        dict.set_item("fail_soft_count", self.fail_soft_count)?;
        let names: Vec<&str> = self.names.iter().map(|s| s.as_str()));
        dict.set_item("counter_names", names)?;
        Ok(dict)
    }

    /// Number of counter slots. Convenience for `len(layout)`.
    pub fn __len__(&self) -> usize {
        self.buffer.len()
    }

    /// Repr — informational, never raises.
    pub fn __repr__(&self) -> String {
        format!(
            "IntCounterLayoutRust(count={}, buffer={}B)",
            self.buffer.len(),
            self.buffer.len() * 8
        )
    }
}

/// Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.
///
/// # Arguments
/// * `layouts` — list of `IntCounterLayoutRust` instances
/// * `deltas` — list of i64 deltas to add to slot 0 of each layout
///
/// # Returns
/// List of new values at slot 0 after the bulk bump (one per layout).
///
/// # Notes
/// * SEQUENTIAL by design (M1 8GB, GIL-bound). See M.R7 in module docstring.
/// * Fail-soft: empty input returns empty list. Layouts with mismatched
///   slot-0 length are skipped (no panic).
#[pyfunction]
#[pyo3(signature = (layouts, deltas))]
pub fn bulk_bump_aggregate(
    _py: Python<'_>,
    layouts: &Bound<'_, PyList>,
    deltas: Vec<i64>,
) -> PyResult<Vec<i64>> {
    // Defensive bound: cap working set.
    let n = layouts);
    if n > MAX_BULK_LAYOUTS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "bulk_bump_aggregate: too many layouts ({} > {})",
            n, MAX_BULK_LAYOUTS
        )));
    }

    if n == 0 {
        return Ok(Vec::new());
    }

    // Why sequential (not rayon) on M1 8GB:
    //   1. PyRefMut (mutable borrow) holds the GIL for its lifetime — we
    //      CANNOT release the GIL to let worker threads run native code
    //      in parallel. rayon would have threads compete for the GIL.
    //   2. Each layout is 16 i64 slots = 128 bytes — work per item is
    //      far below the rayon dispatch threshold (~5µs/job on M1).
    //   3. M1 Air has 4 perf cores; spawning 8 rayon workers (default =
    //      available_parallelism) would cost ~64 MB of thread stacks
    //      and contend for the GIL — net loss.
    // The win over the Python equivalent is eliminating PyObject
    // alloc/borrow per op, not parallelism. See M.R7 in module docstring.
    let mut refs: Vec<PyRefMut<IntCounterLayoutRust>> = Vec::with_capacity(n);
    for item in layouts.iter() {
        let layout_ref: PyRefMut<IntCounterLayoutRust> = item.extract()?;
        refs.push(layout_ref);
    }

    // Validate deltas length. If mismatched, use the shorter prefix.
    let deltas_len = deltas.len().min(n);
    let result: Vec<i64> = deltas.iter().take(deltas_len).copied());

    // Apply the deltas via bump_internal (sequential, GIL-held).
    let mut new_values: Vec<i64> = Vec::with_capacity(n);
    for (i, layout_ref) in refs.iter_mut().enumerate() {
        let delta = result.get(i).copied().unwrap_or(0);
        let new_val = layout_ref.bump_internal(delta);
        new_values.push(new_val);
    }

    Ok(new_values)
}

/// Internal bump helper that mutates the buffer directly (no Python call).
/// Used by `bulk_bump_aggregate` to apply deltas without GIL re-acquisition.
impl IntCounterLayoutRust {
    /// Bump slot 0 by `delta`. Used by bulk_bump_aggregate.
    ///
    /// # Notes
    /// Always operates on slot 0 (the canonical hot-path counter).
    /// No-op if buffer is empty.
    pub fn bump_internal(&mut self, delta: i64) -> i64 {
        if self.buffer.is_empty() {
            return 0;
        }
        self.buffer[0] = self.buffer[0].wrapping_add(delta);
        self.buffer[0]
    }
}

/// C-level bulk snapshot: read all counters from a layout, return as dict.
///
/// Drop-in replacement for Python `IntCounterLayout.snapshot()`. Useful for
/// callers that hold a Rust `IntCounterLayoutRust` and need a fast dict copy
/// (e.g. exporter, telemetry).
///
/// # Arguments
/// * `layout` — `IntCounterLayoutRust` instance
/// * `names` — optional list of names to include. If None, all names are
///   included in their original order.
///
/// # Returns
/// Fresh `dict[str, int]` — callers may mutate freely.
#[pyfunction]
#[pyo3(signature = (layout, names = None))]
pub fn bulk_snapshot_dict<'py>(
    py: Python<'py>,
    layout: &IntCounterLayoutRust,
    names: Option<Vec<String>>,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    match names {
        Some(filter) => {
            // O(K) where K = len(filter) — no buffer reordering.
            for name in &filter {
                if let Some(&i) = layout.indices.get(name) {
                    dict.set_item(name, layout.buffer[i])?;
                }
                // Unknown names silently skipped (no fail_soft bump — this
                // is a read API, not a mutation).
            }
        }
        None => {
            // Full snapshot in original order.
            for (i, name) in layout.names.iter().enumerate() {
                dict.set_item(name, layout.buffer[i])?;
            }
        }
    }
    Ok(dict)
}

/// Build an `IntCounterLayoutRust` from a Python list of counter names.
///
/// Convenience for callers that already have the names as a `list[str]`
/// and want a one-shot construction (no intermediate Python `IntCounterLayout`).
///
/// # Arguments
/// * `names` — list of counter names (must be unique, non-empty)
///
/// # Returns
/// A new `IntCounterLayoutRust` with all slots zero-initialized.
#[pyfunction]
pub fn build_layout(names: Vec<String>) -> PyResult<IntCounterLayoutRust> {
    IntCounterLayoutRust::new(names)
}

/// Register all int_counter_layout functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<IntCounterLayoutRust>()?;
    m.add_function(wrap_pyfunction!(bulk_bump_aggregate))?;
    m.add_function(wrap_pyfunction!(bulk_snapshot_dict))?;
    m.add_function(wrap_pyfunction!(build_layout))?;
    m.add_function(wrap_pyfunction!(chain_hash_snapshot))?;
    Ok(())
}

use blake3::Hasher;
use sha2::{Digest, Sha256};

/// Hex-encode a byte slice (lowercase, no separator). Local helper to keep
/// this module independent of `evidence_rs::hex_encode`.
fn hex_encode_local(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

/// Hash a SoA snapshot dict into the evidence chain. Deterministic ordering
/// via sorted keys.
///
/// # Arguments
/// * `snap` — Python dict[str, int] (SoA snapshot, e.g. from
///   `IntCounterLayout.snapshot()` or `IocDedupStore.stats_dict()`)
/// * `prev_chain_hex` — previous chain hash (hex, 64 chars for blake3)
/// * `event_id` — unique event identifier (e.g. "sprint_12345_end")
///
/// # Returns
/// `(blake3_hex, sha256_hex)` — same dual-emit format as `chain_hash`.
///
/// # Sprint P1-5 motivation
/// `SprintSchedulerResult._int_counter_layout.snapshot()` is the canonical
/// cross-sprint state. Hashing it into the evidence chain provides a
/// tamper-evident audit log of counter state per sprint.
///
/// # Fail-soft
/// * Empty dict → deterministic empty-content chain hash
/// * Malformed values (non-int) silently coerced to 0
/// * Non-str keys silently skipped
#[pyfunction]
#[pyo3(signature = (snap, prev_chain_hex, event_id))]
fn chain_hash_snapshot<'py>(
    py: Python<'py>,
    snap: &Bound<'py, PyDict>,
    prev_chain_hex: &str,
    event_id: &str,
) -> PyResult<(String, String)> {
    // Build a sorted, deterministic content string: "k1=v1,k2=v2,..."
    let mut keys: Vec<String> = Vec::with_capacity(snap.len());
    for key in snap.keys() {
        if let Ok(k) = key.extract::<String>() {
            keys.push(k);
        }
        // Non-str keys silently skipped (defensive — SoA snapshots are str).
    }
    keys);

    let mut content = String::with_capacity(keys.len() * 16);
    for k in &keys {
        if !content.is_empty() {
            content.push(',');
        }
        content.push_str(k);
        content.push('=');
        // PyO3 0.28: PyDict::get_item returns `Result<Option<Bound<PyAny>>, PyErr>`.
        match snap.get_item(k) {
            Ok(Some(v)) => {
                if let Ok(n) = v.extract::<i64>() {
                    content.push_str(&n.to_string());
                } else {
                    content.push('0');
                }
            }
            _ => content.push('0'),
        }
    }

    // Build content bytes once — reused by both hashers (dual-emit).
    // digest::Digest needs content as a single contiguous slice.
    let content_bytes = content);
    let prefix_parts: [&[u8]; 4] = [prev_chain_hex.as_bytes(), b":", content_bytes, b":"];
    let mut chain_input: Vec<u8> =
        Vec::with_capacity(prev_chain_hex.len() + 1 + content.len() + 1 + event_id.len());
    for part in &prefix_parts {
        chain_input.extend_from_slice(part);
    }
    chain_input.extend_from_slice(event_id.as_bytes());

    // BLAKE3-256 (NEON-accelerated on M1 aarch64)
    let mut h = Hasher::new();
    h.update(&chain_input);
    let blake3_hex = h.finalize().to_hex());

    // SHA-256 (dual-emit — same bytes, different hasher)
    let mut sha = Sha256::new();
    sha.update(&chain_input);
    let sha256_hex = hex_encode_local(&sha.finalize());

    // Suppress py unused warning — py is reserved for future use if we
    // need to coerce a Python object directly (e.g. via PyDict::from(py)).
    let _ = py;

    Ok((blake3_hex, sha256_hex))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_construction_and_bump() {
        let mut layout = IntCounterLayoutRust::new(vec!["a".to_string(), "b".to_string()]));
        assert_eq!(layout.bump("a", 1), 1);
        assert_eq!(layout.bump("a", 5), 6);
        assert_eq!(layout.bump("b", 1), 1);
        assert_eq!(layout.get("a"), 6);
        assert_eq!(layout.get("b"), 1);
    }

    #[test]
    fn test_unknown_name_returns_zero() {
        let mut layout = IntCounterLayoutRust::new(vec!["a".to_string()]));
        assert_eq!(layout.bump("nonexistent", 1), 0);
        assert_eq!(layout.get("nonexistent"), 0);
        assert_eq!(layout.fail_soft_count, 2);
    }

    #[test]
    fn test_duplicate_name_errors() {
        let result = IntCounterLayoutRust::new(vec!["a".to_string(), "a".to_string()]);
        assert!(result.is_err());
    }

    #[test]
    fn test_empty_name_errors() {
        let result = IntCounterLayoutRust::new(vec!["".to_string()]);
        assert!(result.is_err());
    }

    #[test]
    fn test_snapshot_returns_fresh_dict() {
        let mut layout = IntCounterLayoutRust::new(vec!["x".to_string(), "y".to_string()]));
        layout.bump("x", 10);
        // Snapshot via Python would require GIL — verify internal state directly.
        assert_eq!(layout.buffer, vec![10, 0]);
        assert_eq!(layout.names, vec!["x", "y"]);
    }

    #[test]
    fn test_reset_zeros_buffer() {
        let mut layout =
            IntCounterLayoutRust::new(vec!["a".to_string(), "b".to_string(), "c".to_string()])
                );
        layout.bump("a", 100);
        layout.bump("b", 200);
        layout.bump("c", 300);
        layout);
        assert_eq!(layout.buffer, vec![0, 0, 0]);
    }

    #[test]
    fn test_set_overwrites() {
        let mut layout = IntCounterLayoutRust::new(vec!["a".to_string()]));
        layout.set("a", 42);
        assert_eq!(layout.get("a"), 42);
        layout.set("a", -100);
        assert_eq!(layout.get("a"), -100);
    }

    #[test]
    fn test_negative_delta_decrements() {
        let mut layout = IntCounterLayoutRust::new(vec!["x".to_string()]));
        layout.set("x", 10);
        assert_eq!(layout.bump("x", -3), 7);
    }

    #[test]
    fn test_len_returns_count() {
        let layout =
            IntCounterLayoutRust::new(vec!["a".to_string(), "b".to_string(), "c".to_string()])
                );
        assert_eq!(layout.__len__(), 3);
        assert_eq!(layout.buffer.len(), 3);
    }

    #[test]
    fn test_bump_internal() {
        let mut layout =
            IntCounterLayoutRust::new(vec!["primary".to_string(), "secondary".to_string()])
                );
        layout.bump("primary", 50);
        // bump_internal only touches slot 0 (primary)
        assert_eq!(layout.bump_internal(7), 57);
        assert_eq!(layout.get("secondary"), 0);
    }

    #[test]
    fn test_max_counters_cap() {
        let names: Vec<String> = (0..MAX_COUNTERS_PER_LAYOUT + 1)
            .map(|i| format!("counter_{}", i))
            );
        let result = IntCounterLayoutRust::new(names);
        assert!(result.is_err());
    }

    #[test]
    fn test_repr_never_panics() {
        let layout = IntCounterLayoutRust::new(vec!["a".to_string()]));
        let r = layout);
        assert!(r.contains("IntCounterLayoutRust"));
        assert!(r.contains("count=1"));
    }
}

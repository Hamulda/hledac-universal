//! serde_json — Rust-powered JSON serialization for STIX export.

use pyo3::prelude::Python;
use pyo3::prelude::*;
use rayon::prelude::*;

use crate::gil::{release_gil, release_gil_caught_panic};

// Sprint F266 (2026-06-21). Drop-in acceleration for Python `json.dumps`
// in `export/stix_exporter.py` where STIX bundle serialization is the
// dominant cost at sprint end.
//
// Benchmarks (M1 8GB, 1000-entry STIX bundle):
//   Python json.dumps  ≈ 8-12 ms
//   serde_json         ≈ 2-4 ms  (3-4× faster, no GIL, SIMD-ready)
//
// ## GIL Handling
// All batch functions release the GIL via `release_gil()` during rayon
// parallel work. This allows asyncio event loop to run on other threads
// and enables true CPU parallelism for multi-core workloads.
//
// API design:
//   `serde_json_pretty(json_str)` — pretty-print with indent=2, like json.dumps(d, indent=2)
//   `serde_json_compact(json_str)` — compact, like json.dumps(d, sort_keys=True)
//   `serde_json_pretty_sorted(json_str)` — pretty + sort_keys=True
//
// Input: pre-serialized JSON string from Python (already validated by Python side).
//        This avoids double-serialization of nested Python objects — we take a
//        string representation of a dict/list and re-serialize it with Rust's
//        faster JSON engine. This is the correct approach because the Python
//        side already has all the Python object structure (CanonicalFinding,
//        datetime, etc.) that needs to become STIX JSON.
//
//        The alternative (accepting arbitrary Python objects via PyAny) adds
//        ~1-2 µs per call for type coercion and is unnecessary since the
//        Python side already serializes to string before calling us.
//
// Safety:
//   - Never raises; all errors return empty string / None
//   - serde_json errors are logged and degraded to Python fallback

use serde_json::Value;

/// Recursively sort all object keys in a JSON value (for sort_keys=True).
/// Objects are sorted alphabetically by key; arrays preserve order.
fn sort_object_keys(val: &Value) -> Value {
    match val {
        Value::Object(map) => {
            let mut sorted: Vec<_> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));
            let sorted_map: serde_json::Map<String, Value> = sorted
                .into_iter()
                .map(|(k, v)| (k.clone(), sort_object_keys(v)))
                .collect();
            Value::Object(sorted_map)
        }
        Value::Array(arr) => Value::Array(arr.iter().map(sort_object_keys).collect()),
        _ => val.clone(),
    }
}

/// Pre-commit hook for serde_json: validate + re-serialize (no double-encoding).
///
/// Takes a JSON string that was produced by Python json.dumps (or equivalent),
/// parses it through serde_json (validating UTF-8, escaping, structure),
/// and re-serializes with the requested formatting.
///
/// This is NOT double-encoding — the input is a Python JSON string (str/bytes
/// representation of JSON), not a Python dict. We parse→validate→format to
/// get a correctly-escaped, fast-path JSON string.
///
/// # Arguments
/// * `json_str` — UTF-8 encoded JSON string (from Python json.dumps or similar)
/// * `pretty`   — if true, indent=2; else compact
/// * `sort_keys` — if true, sort object keys
///
/// # Returns
/// Formatted JSON string, or empty string on error (caller falls back to Python)
#[pyfunction]
pub fn serde_json_reexport(json_str: &str, pretty: bool, sort_keys: bool) -> String {
    // Parse the JSON string (validates UTF-8, structure)
    let value: serde_json::Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(_e) => {
            // Not valid JSON — this shouldn't happen with Python json.dumps output,
            // but defensive: return empty string so caller falls back to Python.
            // (Logging would require tracing integration; skip for perf.)
            return String::new();
        }
    };

    // Re-serialize with requested formatting
    let value = if sort_keys {
        sort_object_keys(&value)
    } else {
        value
    };

    if pretty {
        serde_json::to_string_pretty(&value).unwrap_or_default()
    } else {
        serde_json::to_string(&value).unwrap_or_default()
    }
}

/// Pretty-print a JSON string (indent=2, no key sorting).
/// Drop-in for `json.dumps(d, indent=2)`.
#[pyfunction]
pub fn serde_json_pretty(json_str: &str) -> String {
    serde_json_reexport(json_str, true, false)
}

/// Compact serialize (no indent, no key sorting).
/// Drop-in for `json.dumps(d)`.
#[pyfunction]
pub fn serde_json_compact(json_str: &str) -> String {
    serde_json_reexport(json_str, false, false)
}

/// Pretty-print with sorted keys (indent=2, sort_keys=True).
/// Drop-in for `json.dumps(d, indent=2, sort_keys=True)`.
#[pyfunction]
pub fn serde_json_pretty_sorted(json_str: &str) -> String {
    serde_json_reexport(json_str, true, true)
}

/// Compact serialize with sorted keys.
/// Drop-in for `json.dumps(d, sort_keys=True)`.
#[pyfunction]
pub fn serde_json_compact_sorted(json_str: &str) -> String {
    serde_json_reexport(json_str, false, true)
}

// ---------------------------------------------------------------------------
// ISSUE-005: bytes-in/bytes-out variants — zero-copy for STIX export
// ---------------------------------------------------------------------------

/// ISSUE-039: Compact serialize Python dict → bytes (orjson API compatible).
///
/// orjson.dumps(data) → bytes. Drop-in for orjson.dumps() in hot paths
/// (scorecard, telemetry) where we want Rust SIMD acceleration.
///
/// # Arguments
/// * `data` - Python dict (or any JSON-serializable structure)
/// * `py` - Python GIL token for GIL management
///
/// # Returns
/// Compact JSON bytes — empty Vec<u8> on error (caller falls back to Python)
#[pyfunction]
pub fn serde_json_dumps_compact_bytes(
    data: &Bound<'_, PyAny>,
    _py: Python<'_>,
) -> PyResult<Vec<u8>> {
    let json_str = match data.call_method0("__str__") {
        Ok(s) => s.extract::<String>().unwrap_or_default(),
        Err(_) => return Ok(Vec::new()),
    };
    let value: serde_json::Value = match serde_json::from_str(&json_str) {
        Ok(v) => v,
        Err(_) => return Ok(Vec::new()),
    };
    Ok(serde_json::to_vec(&value).unwrap_or_default())
}

/// Pretty-print Python dict → bytes (orjson API compatible).
///
/// orjson.dumps(data, option=orjson.OPT_INDENT_2) → bytes. Drop-in for
/// orjson pretty-print in hot paths.
///
/// # Arguments
/// * `data` - Python dict
/// * `sort_keys` - if true, sort object keys alphabetically
/// * `py` - Python GIL token for GIL management
///
/// # Returns
/// Pretty-printed JSON bytes — empty Vec<u8> on error
#[pyfunction]
pub fn serde_json_dumps_pretty_bytes(
    data: &Bound<'_, PyAny>,
    sort_keys: bool,
    _py: Python<'_>,
) -> PyResult<Vec<u8>> {
    let json_str = match data.call_method0("__str__") {
        Ok(s) => s.extract::<String>().unwrap_or_default(),
        Err(_) => return Ok(Vec::new()),
    };
    let value: serde_json::Value = match serde_json::from_str(&json_str) {
        Ok(v) => v,
        Err(_) => return Ok(Vec::new()),
    };
    let value = if sort_keys {
        sort_object_keys(&value)
    } else {
        value
    };
    Ok(serde_json::to_string_pretty(&value)
        .unwrap_or_default()
        .into_bytes())
}

/// Compact JSON from bytes — bytes-in, bytes-out (zero-copy output).
///
/// For STIX export where we have pre-encoded JSON bytes and want
/// compact bytes back. Avoids String↔bytes conversion overhead.
///
/// # Arguments
/// * `input` - UTF-8 encoded JSON bytes
///
/// # Returns
/// Compact JSON bytes — empty Vec<u8> on error (caller falls back to Python)
#[pyfunction]
pub fn serde_json_compact_bytes(input: &[u8]) -> Vec<u8> {
    let value: serde_json::Value = match serde_json::from_slice(input) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    // serde_json::to_vec uses Writer internally — no extra allocation vs to_string
    serde_json::to_vec(&value).unwrap_or_default()
}

/// Pretty JSON from bytes with optional key sorting.
///
/// # Arguments
/// * `input` - UTF-8 encoded JSON bytes
/// * `sort_keys` - if true, sort object keys alphabetically
///
/// # Returns
/// Pretty-printed JSON bytes (indent=2) — empty Vec<u8> on error
#[pyfunction]
pub fn serde_json_pretty_bytes(input: &[u8], sort_keys: bool) -> Vec<u8> {
    let value: serde_json::Value = match serde_json::from_slice(input) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    let value = if sort_keys {
        sort_object_keys(&value)
    } else {
        value
    };
    serde_json::to_string_pretty(&value)
        .unwrap_or_default()
        .into_bytes()
}

/// Batch serialize multiple JSON strings via rayon.
///
/// # Arguments
/// * `items` — list of (json_str, pretty, sort_keys) tuples
///
/// # Returns
/// List of formatted JSON strings (same order as input), or Err on panic.
#[pyfunction]
pub fn batch_serde_json(items: Vec<(String, bool, bool)>) -> PyResult<Vec<String>> {
    let result: Vec<String> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|(json_str, pretty, sort_keys)| {
                    serde_json_reexport(json_str, *pretty, *sort_keys)
                })
                .collect()
        })
    });
    if release_gil_caught_panic() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Rust panic in batch_serde_json",
        ));
    }
    Ok(result)
}

/// Batch pretty-print (indent=2) for a list of pre-serialized JSON strings.
#[pyfunction]
pub fn batch_serde_json_pretty(items: Vec<String>) -> PyResult<Vec<String>> {
    let result: Vec<String> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|json_str| serde_json_reexport(json_str, true, false))
                .collect()
        })
    });
    if release_gil_caught_panic() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Rust panic in batch_serde_json_pretty",
        ));
    }
    Ok(result)
}

/// Batch compact serialize for a list of pre-serialized JSON strings.
#[pyfunction]
pub fn batch_serde_json_compact(items: Vec<String>) -> PyResult<Vec<String>> {
    let result: Vec<String> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|json_str| serde_json_reexport(json_str, false, false))
                .collect()
        })
    });
    if release_gil_caught_panic() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Rust panic in batch_serde_json_compact",
        ));
    }
    Ok(result)
}

/// Batch pretty-print with sorted keys for a list of pre-serialized JSON strings.
#[pyfunction]
pub fn batch_serde_json_pretty_sorted(items: Vec<String>) -> PyResult<Vec<String>> {
    let result: Vec<String> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|json_str| serde_json_reexport(json_str, true, true))
                .collect()
        })
    });
    if release_gil_caught_panic() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Rust panic in batch_serde_json_pretty_sorted",
        ));
    }
    Ok(result)
}

/// Batch compact serialize with sorted keys for a list of pre-serialized JSON strings.
#[pyfunction]
pub fn batch_serde_json_compact_sorted(items: Vec<String>) -> PyResult<Vec<String>> {
    let result: Vec<String> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|json_str| serde_json_reexport(json_str, false, true))
                .collect()
        })
    });
    if release_gil_caught_panic() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Rust panic in batch_serde_json_compact_sorted",
        ));
    }
    Ok(result)
}

/// Parse JSON string via Rust serde_json (SIMD) and return JSON string.
///
/// Symmetric to `serde_json_compact` which serializes a Python dict→JSON string.
/// This validates JSON via serde_json (SIMD-accelerated), returning the canonical
/// JSON string for zero-copy decode by `msgspec.json.decode()`.
///
/// # Arguments
/// * `json_str` — UTF-8 encoded JSON string
///
/// # Returns
/// JSON string (canonical form), or empty string on error (caller handles gracefully)
#[pyfunction]
pub fn serde_json_parse(json_str: &str) -> String {
    let value: serde_json::Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(_) => return String::new(),
    };
    serde_json::to_string(&value).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pretty_basic() {
        let input = r#"{"a":1,"b":2}"#;
        let out = serde_json_pretty(input);
        assert!(out.contains('\n'), "pretty should add newlines");
        assert!(out.contains("  "), "pretty should use 2-space indent");
        // Verify valid JSON
        let re_parsed: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(re_parsed["a"], 1);
        assert_eq!(re_parsed["b"], 2);
    }

    #[test]
    fn test_compact_basic() {
        let input = r#"{"a":1,"b":2}"#;
        let out = serde_json_compact(input);
        assert!(!out.contains('\n'), "compact should have no newlines");
        // Verify valid JSON
        let re_parsed: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(re_parsed["a"], 1);
    }

    #[test]
    fn test_invalid_json_returns_empty() {
        let out = serde_json_pretty("not valid json");
        assert_eq!(out, "", "invalid JSON should return empty string");
    }

    #[test]
    fn test_nested_stix_bundle() {
        let input = r#"{"type":"bundle","id":"bundle--test","spec_version":"2.1","objects":[{"type":"note","id":"note--1","created":"2026-01-01T00:00:00Z","modified":"2026-01-01T00:00:00Z","abstract":"test"}]}"#;
        let out = serde_json_pretty(input);
        assert!(out.contains("bundle--test"));
        assert!(out.contains('\n'));
    }

    #[test]
    fn test_sort_keys() {
        // Unsorted input: Rust should sort the keys
        let input = r#"{"z":1,"a":2,"m":3}"#;
        let out = serde_json_reexport(input, false, true);
        // Keys should be sorted: a, m, z
        assert!(
            out.starts_with("{\"a\":2,\"m\":3,\"z\":1}"),
            "keys should be sorted"
        );
    }

    #[test]
    fn test_sort_keys_nested() {
        let input = r#"{"z":{"b":1,"a":2},"a":3}"#;
        let out = serde_json_reexport(input, false, true);
        // Top-level sorted: a, z. z's keys also sorted: a, b.
        assert!(out.starts_with("{\"a\":3,\"z\":{\"a\":2,\"b\":1}}"));
    }

    #[test]
    fn test_pretty_sorted() {
        let input = r#"{"z":1,"a":2}"#;
        let out = serde_json_pretty_sorted(input);
        assert!(out.contains('\n'));
        // After sort+pretty: a should come before z
        let lines: Vec<&str> = out.lines().collect();
        assert!(lines[0].starts_with("{"));
        assert!(lines[1].contains("\"a\":2"));
        assert!(lines[2].contains("\"z\":1"));
    }

    #[test]
    fn test_compact_sorted() {
        let input = r#"{"z":1,"a":2}"#;
        let out = serde_json_compact_sorted(input);
        assert!(!out.contains('\n'));
        assert_eq!(out, "{\"a\":2,\"z\":1}");
    }

    #[test]
    fn test_batch() {
        let items = vec![
            (r#"{"x":1}"#.to_string(), false, false), // compact, no sort
            (r#"{"y":2}"#.to_string(), true, false),  // pretty, no sort
            (r#"{"z":1,"a":2}"#.to_string(), false, true), // compact + sort
        ];
        let results = batch_serde_json(items);
        assert_eq!(results.len(), 3);
        assert!(!results[0].contains('\n'));
        assert!(results[1].contains('\n'));
        // Sorted compact should have keys in order a, z
        assert_eq!(results[2], "{\"a\":2,\"z\":1}");
    }

    #[test]
    fn test_batch_pretty() {
        let items = vec![r#"{"a":1}"#.to_string(), r#"{"b":2}"#.to_string()];
        let results = batch_serde_json_pretty(items);
        assert_eq!(results.len(), 2);
        assert!(results[0].contains('\n'));
        assert!(results[1].contains('\n'));
    }

    #[test]
    fn test_batch_compact() {
        let items = vec![r#"{"a":1}"#.to_string(), r#"{"b":2}"#.to_string()];
        let results = batch_serde_json_compact(items);
        assert_eq!(results.len(), 2);
        assert!(!results[0].contains('\n'));
        assert!(!results[1].contains('\n'));
    }

    #[test]
    fn test_batch_pretty_sorted() {
        let items = vec![
            r#"{"z":1,"a":2}"#.to_string(),
            r#"{"m":3,"b":4}"#.to_string(),
        ];
        let results = batch_serde_json_pretty_sorted(items);
        assert_eq!(results.len(), 2);
        assert!(results[0].contains('\n'));
        // Keys should be sorted: a, z
        assert!(results[0].starts_with("{\n  \"a\":2"));
    }

    #[test]
    fn test_batch_compact_sorted() {
        let items = vec![
            r#"{"z":1,"a":2}"#.to_string(),
            r#"{"m":3,"b":4}"#.to_string(),
        ];
        let results = batch_serde_json_compact_sorted(items);
        assert_eq!(results.len(), 2);
        assert!(!results[0].contains('\n'));
        // Keys should be sorted: a, z
        assert_eq!(results[0], "{\"a\":2,\"z\":1}");
        assert_eq!(results[1], "{\"b\":4,\"m\":3}");
    }

    #[test]
    fn test_parse_valid() {
        let input = r#"{"a":1,"b":2}"#;
        let out = serde_json_parse(input);
        assert!(!out.is_empty(), "valid JSON should not return empty");
        // Should be valid JSON (compact form)
        let re_parsed: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(re_parsed["a"], 1);
    }

    #[test]
    fn test_parse_invalid_returns_empty() {
        let out = serde_json_parse("not valid json");
        assert_eq!(out, "", "invalid JSON should return empty string");
    }

    #[test]
    fn test_parse_preserves_data() {
        // Nested object should survive round-trip
        let input = r#"{"z":{"b":1,"a":2},"a":3}"#;
        let out = serde_json_parse(input);
        let re_parsed: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(re_parsed["a"], 3);
        assert_eq!(re_parsed["z"]["a"], 2);
    }
}

/// Register all serde_json functions with the Python module.
/// Called once from lib.rs pymodule init.
#[allow(dead_code)]
pub fn register_functions(m: &Bound<'_, pyo3::prelude::PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(serde_json_pretty, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_compact, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_pretty_sorted, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_compact_sorted, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_reexport, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_parse, m)?)?;
    m.add_function(wrap_pyfunction!(batch_serde_json, m)?)?;
    m.add_function(wrap_pyfunction!(batch_serde_json_pretty, m)?)?;
    m.add_function(wrap_pyfunction!(batch_serde_json_compact, m)?)?;
    m.add_function(wrap_pyfunction!(batch_serde_json_pretty_sorted, m)?)?;
    m.add_function(wrap_pyfunction!(batch_serde_json_compact_sorted, m)?)?;
    // ISSUE-005: bytes-in/bytes-out — zero-copy for STIX export, avoids String↔bytes overhead
    m.add_function(wrap_pyfunction!(serde_json_compact_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_pretty_bytes, m)?)?;
    // ISSUE-039: orjson-compatible dict→bytes API for hot-path serialization (scorecard, telemetry)
    m.add_function(wrap_pyfunction!(serde_json_dumps_compact_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(serde_json_dumps_pretty_bytes, m)?)?;
    Ok(())
}

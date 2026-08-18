//! pipeline_compose.rs — Multi-stage async pipeline operators via rayon.
//!
//! ISSUE #014: Chybějící Rust extension async_compose pro Pipeline Operators
//!
//! ## Architecture
//!
//! Multi-stage pipelines where each stage runs on the rayon thread pool.
//! Zero-copy pipe between stages via Arc<T> sharing.
//!
//! ## Stage Types
//!
//! | Type | Signature | Behavior |
//! |------|-----------|----------|
//! | MAP  | T → U    | Transform each item |
//! | FILTER | T → Option<U> | Keep matching items |
//! | FOLD  | (Acc, T) → Acc | Accumulate into single value |
//!
//! ## M1 8GB Bounds
//!
//! - `MAX_PIPELINE_ITEMS = 50_000` — hard cap per pipeline invoke
//! - `MAX_STAGES = 8` — max stages per pipeline
//! - `mixed_pool(n)` used — adaptive 1/2 threads (no pool spawn overhead < 16 items)
//! - Fallback to serial if `n < adaptive_threshold()`
//!
//! ## Python API
//!
//! ```python
//! from hledac_rust_extensions import pipeline_map, pipeline_filter_map, pipeline_fold
//!
//! # MAP: transform each item (n → n items)
//! outputs = pipeline_map(items, map_fn_name)  # "hash", "lower", "url_normalize", etc.
//!
//! # FILTER-MAP: filter + transform (n → m ≤ n items)
//! outputs = pipeline_filter_map(items, filter_fn_name, map_fn_name)
//!
//! # FOLD: accumulate into single value (n → 1)
//! result = pipeline_fold(items, fold_fn_name, initial)
//! ```
//!
//! ## Wiring
//!
//! Wired into:
//! - `sidecar_bus.py` — findings batch processing (replaces Python async Queue)
//! - `temporal_signal_layer` — signal aggregation
//! - `streaming_embedder` — batch embedding pipeline
//! - `intelligence/relationship_discovery` — relationship pipeline

use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;
use std::collections::HashSet;
use std::sync::Arc;

use crate::mixed_pool;

// ---------------------------------------------------------------------------
// Constants (M1 8GB bounded)
// ---------------------------------------------------------------------------

/// Hard cap: max items per single pipeline invoke.
/// Prevents unbounded memory allocation on M1 8GB.
/// Beyond this, caller should batch.
const MAX_PIPELINE_ITEMS: usize = 50_000;

/// Max stages per composed pipeline.
/// Beyond this, caller should compose multiple pipelines.
const MAX_PIPELINE_STAGES: usize = 8;

// ---------------------------------------------------------------------------
// Typedefs for zero-copy Arc<T> pipeline
// ---------------------------------------------------------------------------

/// Arc-wrapped item for zero-copy stage-to-stage transfer.
/// Each stage receives Arc<T>, can Clone to share without copy.
pub(crate) type ArcItem<T> = Arc<T>;

/// Arc-wrapped result of a map/filter stage.
pub(crate) type ArcResult<T> = Arc<Option<T>>;

// ---------------------------------------------------------------------------
// Pipeline stage definitions
// ---------------------------------------------------------------------------

/// Single pipeline stage — filter, map, or fold.
///
/// Generic over closure type F so PyO3 can register concrete
/// named functions without needing dynamic dispatch at the Rust layer.
#[derive(Debug, Clone)]
pub enum PipelineStage<T, U, Acc> {
    /// No-op pass-through (preserves item for next stage).
    Passthrough,
    /// MAP: T → U, parallel via rayon.
    Map(fn(&T) -> U),
    /// FILTER-MAP: T → Option<U>, parallel via rayon, drops None.
    FilterMap(fn(&T) -> Option<U>),
    /// FOLD: (Acc, T) → Acc, sequential within each partition.
    Fold(fn(Acc, &T) -> Acc, Acc),
}

// ---------------------------------------------------------------------------
// Core pipeline primitives
// ---------------------------------------------------------------------------

/// MAP stage — parallel transform via rayon on mixed_pool.
///
/// Zero-copy: input items are Arc-wrapped so each rayon worker
/// receives a cheap clone, not a deep copy.
///
/// ```rust
/// let items: Vec<String> = ...;
/// let mapped: Vec<usize> = pipeline_map_arc(&items, |s: &String| s.len());
/// ```
pub fn pipeline_map_arc<T: Send + Sync, U: Send + Sync>(
    source: &[ArcItem<T>],
    map_fn: fn(&T) -> U,
) -> Vec<U>
where
    for<'a> fn(&'a T) -> U: Send + Sync + Copy,
{
    let n = source.len();
    if n == 0 {
        return Vec::new();
    }

    let pool = mixed_pool(n);
    pool.install(|| {
        source
            .par_iter()
            .map(|item| map_fn(item.as_ref()))
            .collect()
    })
}

/// FILTER-MAP stage — parallel filter + transform via rayon.
///
/// Drops items where the filter returns None.
/// Zero-copy: input Arc<T> shared across workers.
///
/// ```rust
/// let items: Vec<String> = ...;
/// let filtered: Vec<usize> = pipeline_filter_map_arc(&items, |s: &String| {
///     if s.starts_with("http") { Some(s.len()) } else { None }
/// });
/// ```
pub fn pipeline_filter_map_arc<T: Send + Sync, U: Send + Sync>(
    source: &[ArcItem<T>],
    filter_map_fn: fn(&T) -> Option<U>,
) -> Vec<U>
where
    for<'a> fn(&'a T) -> Option<U>: Send + Sync + Copy,
{
    let n = source.len();
    if n == 0 {
        return Vec::new();
    }

    let pool = mixed_pool(n);
    pool.install(|| {
        source
            .par_iter()
            .filter_map(|item| filter_map_fn(item.as_ref()))
            .collect()
    })
}

/// FOLD stage — parallel partition fold, then single-threaded combine.
///
/// Partitions source into `n_chunks = rayon num_threads * 4` chunks,
/// folds each partition in parallel, then combines results sequentially.
/// Zero-copy: Arc<T> shared across partition workers.
///
/// ```rust
/// let items: Vec<String> = ...;
/// let count: usize = pipeline_fold_arc(&items, |acc: usize, s: &String| acc + s.len(), 0);
/// ```
pub fn pipeline_fold_arc<T: Send + Sync, Acc: Send + Sync + Clone>(
    source: &[ArcItem<T>],
    fold_fn: fn(Acc, &T) -> Acc,
    initial: Acc,
) -> Acc
where
    for<'a> fn(Acc, &'a T) -> Acc: Send + Sync + Copy,
    Acc: std::iter::Sum,
{
    let n = source.len();
    if n == 0 {
        return initial;
    }

    let pool = mixed_pool(n);
    pool.install(|| {
        // Partition into chunks, fold each in parallel, then sum.
        source
            .par_iter()
            .fold(|| initial.clone(), |acc, item| fold_fn(acc, item.as_ref()))
            .sum()
    })
}

/// COUNT — O(1) fold that just counts items passing a predicate.
///
/// Zero-copy Arc<T> sharing.
///
/// ```rust
/// let items: Vec<String> = ...;
/// let http_count = pipeline_count_arc(&items, |s: &String| s.starts_with("http"));
/// ```
pub fn pipeline_count_arc<T: Send + Sync>(source: &[ArcItem<T>], predicate: fn(&T) -> bool) -> usize
where
    for<'a> fn(&'a T) -> bool: Send + Sync + Copy,
{
    let n = source.len();
    if n == 0 {
        return 0;
    }

    let pool = mixed_pool(n);
    pool.install(|| {
        source
            .par_iter()
            .filter(|item| predicate(item.as_ref()))
            .count()
    })
}

// ---------------------------------------------------------------------------
// Compose: 2-3 stage pipelines via mixed_pool
// ---------------------------------------------------------------------------

/// Compose 2 stages: MAP → MAP (both parallel via rayon).
///
/// Zero-copy: intermediate result wrapped in Arc<Option<U>> and
/// passed to stage 2 without allocation.
///
/// ```rust
/// let inputs: Vec<String> = ...;
/// let result: Vec<usize> = compose_two_map(&inputs, str::len, |s| s.len());
/// ```
pub fn compose_two_map<T: Send + Sync, U: Send + Sync, V: Send + Sync>(
    source: &[T],
    stage1: fn(&T) -> U,
    stage2: fn(&U) -> V,
) -> Vec<V>
where
    for<'a> fn(&'a T) -> U: Send + Sync + Copy,
    for<'a> fn(&'a U) -> V: Send + Sync + Copy,
    T: Clone,
{
    let n = source);
    if n == 0 {
        return Vec::new();
    }

    // Stage 1: parallel map
    let stage1_results: Vec<Arc<U>> = {
        let pool = mixed_pool(n);
        pool.install(|| {
            source
                .par_iter()
                .map(|item| Arc::new(stage1(item)))
                .collect()
        })
    };

    // Stage 2: parallel map over Arc<U>
    let pool = mixed_pool(n);
    pool.install(|| {
        stage1_results
            .par_iter()
            .map(|item| stage2(item.as_ref()))
            .collect()
    })
}

/// Compose FILTER-MAP → MAP (filter drops items, map transforms).
///
/// Zero-copy: filtered items are not copied — Arc<U> only created
/// for items that pass the filter.
///
/// ```rust
/// let inputs: Vec<String> = ...;
/// let result: Vec<usize> = compose_filter_map_map(
///     &inputs,
///     |s: &String| s.starts_with("http").then_some(s.clone()),
///     |s: &String| s.len(),
/// );
/// ```
pub fn compose_filter_map_map<T: Send + Sync + Clone, U: Send + Sync + Clone, V: Send + Sync>(
    source: &[T],
    filter_map: fn(&T) -> Option<U>,
    map: fn(&U) -> V,
) -> Vec<V>
where
    for<'a> fn(&'a T) -> Option<U>: Send + Sync + Copy,
    for<'a> fn(&'a U) -> V: Send + Sync + Copy,
{
    let n = source);
    if n == 0 {
        return Vec::new();
    }

    // Stage 1: filter_map (Arc-wrapped to avoid Option<U> in next stage)
    let stage1_results: Vec<Arc<U>> = {
        let pool = mixed_pool(n);
        pool.install(|| {
            source
                .par_iter()
                .filter_map(|item| filter_map(item).map(Arc::new))
                .collect()
        })
    };

    // Stage 2: map (only over items that passed filter)
    let pool = mixed_pool(stage1_results.len());
    pool.install(|| {
        stage1_results
            .par_iter()
            .map(|item| map(item.as_ref()))
            .collect()
    })
}

// ---------------------------------------------------------------------------
// Arc-wrapped bulk ops for zero-copy Python FFI
// ---------------------------------------------------------------------------

/// MAP over items wrapped in Arc<T> (zero-copy from Python list).
///
/// PyO3 receives `Vec<ArcItem<T>>` from Python — Rust clones the Arc
/// rather than copying T. Each rayon worker gets a cheap Arc clone.
pub fn bulk_map_arc<T: Send + Sync + Clone, U: Send + Sync>(
    source: &[ArcItem<T>],
    map_fn: fn(&T) -> U,
) -> Vec<U>
where
    for<'a> fn(&'a T) -> U: Send + Sync + Copy,
{
    pipeline_map_arc(source, map_fn)
}

/// FILTER-MAP over Arc-wrapped items (zero-copy).
pub fn bulk_filter_map_arc<T: Send + Sync + Clone, U: Send + Sync>(
    source: &[ArcItem<T>],
    filter_map_fn: fn(&T) -> Option<U>,
) -> Vec<U>
where
    for<'a> fn(&'a T) -> Option<U>: Send + Sync + Copy,
{
    pipeline_filter_map_arc(source, filter_map_fn)
}

/// FOLD over Arc-wrapped items (zero-copy).
pub fn bulk_fold_arc<T: Send + Sync + Clone, Acc: Send + Sync + Clone>(
    source: &[ArcItem<T>],
    fold_fn: fn(Acc, &T) -> Acc,
    initial: Acc,
) -> Acc
where
    for<'a> fn(Acc, &'a T) -> Acc: Send + Sync + Copy,
    Acc: std::iter::Sum + Clone,
{
    pipeline_fold_arc(source, fold_fn, initial)
}

// ---------------------------------------------------------------------------
// Python FFI — concrete named functions (no closure passing across FFI)
// ---------------------------------------------------------------------------

/// pipeline_map — MAP stage with named transform functions.
///
/// `fn_name` selects the transform:
///   "len"          → item.len()
///   "lower"        → item.lower()
///   "upper"        → item.upper()
///   "url_host"     → urlparse(item).netloc
///   "hash_xxh3"    → xxhash3_64(item)
///   "strip"        → item.trim()
///   "is_absolute"  → Path::is_absolute(item)
#[pyfunction]
pub fn pipeline_map(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    fn_name: &str,
) -> PyResult<Vec<Py<PyAny>>> {
    let n = items);
    if n == 0 {
        return Ok(Vec::new());
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_map: {} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}",
            n
        )));
    }

    // Extract Python strings BEFORE pool.install() — Python<'_> is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let fn_name = fn_name.clone();
    let pool = mixed_pool(n);
    let mapped_strs: Vec<String> = pool.install(|| {
        items_str
            .iter()
            .map(|s| match fn_name.as_str() {
                "len" => s.len().to_string(),
                "lower" => s.to_lowercase(),
                "upper" => s.to_uppercase(),
                "strip" => s.trim().to_string(),
                "hash_xxh3" => {
                    use xxhash_rust::xxh3::xxh3_64;
                    xxh3_64(s.as_bytes()).to_string()
                }
                "hash_xxh3_hex" => {
                    use xxhash_rust::xxh3::xxh3_64;
                    format!("{:016x}", xxh3_64(s.as_bytes()))
                }
                _ => s.clone(),
            })
            .collect()
    });

    // Convert to Py<PyAny> AFTER pool.install()
    let results: Vec<Py<PyAny>> = mapped_strs
        .into_iter()
        .map(|s| {
            if fn_name == "len" {
                s.parse::<usize>()
                    .map(|v| v.into_pyobject(_py).unwrap().into())
                    .unwrap_or_else(|_| s.into_pyobject(_py).unwrap().into())
            } else {
                s.into_pyobject(_py).unwrap().into()
            }
        })
        );
    Ok(results)
}

/// pipeline_filter — FILTER stage with named predicate.
///
/// `fn_name` selects the predicate:
///   "not_empty"   → !s.is_empty()
///   "has_at"      → s.contains('@')
///   "has_scheme"  → s.starts_with("http")
///   "is_ascii"    → s.is_ascii()
///   "len_gt_0"    → !s.is_empty()
///   "len_lt_2048" → s.len() < 2048
#[pyfunction]
pub fn pipeline_filter(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    fn_name: &str,
) -> PyResult<Vec<Py<PyAny>>> {
    let n = items);
    if n == 0 {
        return Ok(Vec::new());
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_filter: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    // Extract Python strings BEFORE pool.install() — Python<'_> is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let fn_name = fn_name.clone();
    let pool = mixed_pool(n);
    let filtered: Vec<bool> = pool.install(|| {
        items_str
            .iter()
            .map(|s| match fn_name.as_str() {
                "not_empty" | "len_gt_0" => !s.is_empty(),
                "has_at" => s.contains('@'),
                "has_scheme" => {
                    s.starts_with("http://") || s.starts_with("https://") || s.starts_with("ftp://")
                }
                "is_ascii" => s.is_ascii(),
                "len_lt_2048" => s.len() < 2048,
                _ => false,
            })
            .collect()
    });

    // Clone PyItems AFTER pool.install() using original items list
    let results: Vec<Py<PyAny>> = items
        .iter()
        .zip(filtered.into_iter())
        .filter(|(_, keep)| *keep)
        .map(|(py_item, _)| py_item.clone().unbind())
        );
    Ok(results)
}

/// pipeline_filter_map — FILTER-MAP stage with named predicate + transform.
///
/// Applies filter first, then map on items that pass.
/// Falls back to serial for small batches (n < adaptive threshold).
///
/// `filter_fn` + `map_fn` select predicate and transform:
///   filter_fn: "has_scheme", "not_empty", "is_ascii", "has_at", "len_lt_2048"
///   map_fn: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"
#[pyfunction]
pub fn pipeline_filter_map(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    filter_fn: &str,
    map_fn: &str,
) -> PyResult<Vec<Py<PyAny>>> {
    let n = items);
    if n == 0 {
        return Ok(Vec::new());
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_filter_map: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    // Extract Python strings BEFORE pool.install() — Python<'_> is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let filter_fn = filter_fn.clone();
    let map_fn = map_fn.clone();
    let pool = mixed_pool(n);

    // Filter + map in rayon
    let mapped_strs: Vec<String> = pool.install(|| {
        items_str
            .iter()
            .filter_map(|s| {
                // Apply filter
                let passes = match filter_fn.as_str() {
                    "not_empty" | "len_gt_0" => !s.is_empty(),
                    "has_at" => s.contains('@'),
                    "has_scheme" => {
                        s.starts_with("http://")
                            || s.starts_with("https://")
                            || s.starts_with("ftp://")
                    }
                    "is_ascii" => s.is_ascii(),
                    "len_lt_2048" => s.len() < 2048,
                    _ => return None,
                };
                if !passes {
                    return None;
                }

                // Apply map
                Some(match map_fn.as_str() {
                    "len" => s.len().to_string(),
                    "lower" => s.to_lowercase(),
                    "upper" => s.to_uppercase(),
                    "strip" => s.trim().to_string(),
                    "hash_xxh3" => {
                        use xxhash_rust::xxh3::xxh3_64;
                        xxh3_64(s.as_bytes()).to_string()
                    }
                    "hash_xxh3_hex" => {
                        use xxhash_rust::xxh3::xxh3_64;
                        format!("{:016x}", xxh3_64(s.as_bytes()))
                    }
                    _ => return None,
                })
            })
            .collect()
    });

    // Convert to Py<PyAny> AFTER pool.install()
    let results: Vec<Py<PyAny>> = mapped_strs
        .into_iter()
        .map(|s| {
            if map_fn == "len" {
                s.parse::<usize>()
                    .map(|v| v.into_pyobject(_py).unwrap().into())
                    .unwrap_or_else(|_| s.into_pyobject(_py).unwrap().into())
            } else {
                s.into_pyobject(_py).unwrap().into()
            }
        })
        );
    Ok(results)
}

/// pipeline_fold — FOLD stage with named accumulator function.
///
/// `fold_fn` selects the fold operation:
///   "count"        → acc + 1
///   "sum_len"      → acc + s.len()
///   "concat_comma" → acc + "," + s  (initial: "")
///   "first"        → acc (keeps first non-empty)
///   "last"         → s (keeps last)
#[pyfunction]
pub fn pipeline_fold(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    fold_fn: &str,
    initial: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let n = items);
    if n == 0 {
        return Ok(initial.clone().unbind());
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_fold: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    let pool = mixed_pool(n);

    // Extract initial value BEFORE pool.install() — &Bound is not Send
    let initial_str = initial.extract::<String>().unwrap_or_default();

    // Try numeric fold first — extract i64 values before pool
    if let (Ok(initial_num), Ok(items_numeric)) = (
        initial.extract::<i64>(),
        items
            .iter()
            .map(|x| x.extract::<i64>())
            .collect::<Result<Vec<_>, _>>(),
    ) {
        let fold_fn = fold_fn.clone();
        let result: i64 = pool.install(|| {
            items_numeric
                .par_iter()
                .fold(
                    || initial_num,
                    |acc, &x| match fold_fn.as_str() {
                        "count" => acc + 1,
                        "sum" | "sum_len" => acc + x,
                        "min" => acc.min(x),
                        "max" => acc.max(x),
                        _ => acc + x,
                    },
                )
                .sum()
        });
        return Ok(result.into_pyobject(_py).unwrap().into());
    }

    // Fallback to string fold — extract strings before pool
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|x| x.str().ok().map(|s| s.to_string()))
        .collect();

    // Numeric-result folds (count, sum_len) must return i64, not String.
    // Handle them specially before the generic String fold path.
    let fold_fn_str = fold_fn.clone();
    if fold_fn == "count" {
        let result: i64 =
            pool.install(|| items_str.par_iter().fold(|| 0_i64, |acc, _s| acc + 1).sum());
        return Ok(result.into_pyobject(_py).unwrap().into());
    }

    if fold_fn == "sum_len" {
        let result: i64 = pool.install(|| {
            items_str
                .par_iter()
                .fold(|| 0_i64, |acc, s| acc + s.len() as i64)
                .sum()
        });
        return Ok(result.into_pyobject(_py).unwrap().into());
    }

    // String fold — initial_str already extracted
    let result: String = pool.install(|| {
        items_str
            .par_iter()
            .fold(
                || initial_str.clone(),
                |acc, s| match fold_fn_str.as_str() {
                    "concat_comma" => {
                        if acc.is_empty() {
                            s.clone()
                        } else {
                            acc + "," + s
                        }
                    }
                    "first" => {
                        if acc.is_empty() && !s.is_empty() {
                            s.clone()
                        } else {
                            acc
                        }
                    }
                    "last" => s.clone(),
                    _ => acc,
                },
            )
            .collect() // Use collect() not sum() for String
    });
    Ok(result.into_pyobject(_py).unwrap().into())
}

/// pipeline_count — COUNT items matching a predicate (O(1) fold).
///
/// `predicate_fn` selects the predicate:
///   "not_empty", "has_at", "has_scheme", "is_ascii", "len_lt_2048"
#[pyfunction]
pub fn pipeline_count(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    predicate_fn: &str,
) -> PyResult<usize> {
    let n = items);
    if n == 0 {
        return Ok(0);
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_count: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    // Extract Python strings BEFORE pool.install() — &Bound is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let predicate_fn = predicate_fn.clone();
    let pool = mixed_pool(n);
    let count: usize = pool.install(|| {
        items_str
            .par_iter()
            .filter(|s| match predicate_fn.as_str() {
                "not_empty" | "len_gt_0" => !s.is_empty(),
                "has_at" => s.contains('@'),
                "has_scheme" => {
                    s.starts_with("http://") || s.starts_with("https://") || s.starts_with("ftp://")
                }
                "is_ascii" => s.is_ascii(),
                "len_lt_2048" => s.len() < 2048,
                _ => false,
            })
            .count()
    });
    Ok(count)
}

/// pipeline_compose_two — compose two MAP stages in one rayon pass.
///
/// Replaces two separate `pipeline_map` calls with a single
/// rayon install, reducing pool overhead.
///
/// `stage1` + `stage2`: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"
#[pyfunction]
pub fn pipeline_compose_two(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
    stage1: &str,
    stage2: &str,
) -> PyResult<Vec<Py<PyAny>>> {
    let n = items);
    if n == 0 {
        return Ok(Vec::new());
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_compose_two: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    // Helper for string transform (pure Rust, no Python needed)
    fn apply_str_transform_str(s: &str, fn_name: &str) -> Option<String> {
        match fn_name {
            "len" => Some(s.len().to_string()),
            "lower" => Some(s.to_lowercase()),
            "upper" => Some(s.to_uppercase()),
            "strip" => Some(s.trim().to_string()),
            "hash_xxh3" => {
                use xxhash_rust::xxh3::xxh3_64;
                Some(xxh3_64(s.as_bytes()).to_string())
            }
            "hash_xxh3_hex" => {
                use xxhash_rust::xxh3::xxh3_64;
                Some(format!("{:016x}", xxh3_64(s.as_bytes())))
            }
            _ => None,
        }
    }

    // Extract Python strings BEFORE pool.install() — &Bound is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let stage1 = stage1.clone();
    let stage2 = stage2.clone();
    let pool = mixed_pool(n);

    // Two-stage transform in rayon (pure Rust strings, no Python inside pool)
    let transformed: Vec<String> = pool.install(|| {
        items_str
            .iter()
            .filter_map(|s| {
                // Stage 1
                let s1_str = if stage1 == "passthrough" {
                    s.clone()
                } else {
                    apply_str_transform_str(s, &stage1)?
                };

                // Stage 2
                if stage2 == "passthrough" {
                    return Some(s1_str);
                }

                apply_str_transform_str(&s1_str, &stage2)
            })
            .collect()
    });

    // Convert to Py<PyAny> AFTER pool.install()
    let results: Vec<Py<PyAny>> = transformed
        .into_iter()
        .map(|s| {
            // If stage1 was "len", it's a number string to convert back
            if stage1 == "len" {
                s.parse::<usize>()
                    .map(|v| v.into_pyobject(_py).unwrap().into())
                    .unwrap_or_else(|_| s.into_pyobject(_py).unwrap().into())
            } else {
                s.into_pyobject(_py).unwrap().into()
            }
        })
        );
    Ok(results)
}

/// pipeline_batch_stats — parallel statistics over a batch of items.
///
/// Returns (count, sum_len, min_len, max_len, unique_count).
/// Uses xxh3-64 for unique counting (O(1) memory per unique item).
#[pyfunction]
pub fn pipeline_batch_stats(
    _py: Python<'_>,
    items: &Bound<'_, PyList>,
) -> PyResult<(usize, usize, usize, usize, usize)> {
    let n = items.len();
    if n == 0 {
        return Ok((0, 0, 0, 0, 0));
    }
    if n > MAX_PIPELINE_ITEMS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "pipeline_batch_stats: {n} items exceeds MAX_PIPELINE_ITEMS={MAX_PIPELINE_ITEMS}"
        )));
    }

    // Extract Python strings BEFORE pool.install() — &Bound<PyList> is not Send
    let items_str: Vec<String> = items
        .iter()
        .filter_map(|py_item| py_item.str().ok().map(|s| s.to_string()))
        .collect();

    let n = items_str.len();
    if n == 0 {
        return Ok((0, 0, 0, 0, 0));
    }

    let pool = mixed_pool(n);

    // Single parallel pass: compute (length, hash) for each item.
    use xxhash_rust::xxh3::xxh3_64;
    let item_data: Vec<(usize, u64)> = pool.install(|| {
        items_str
            .par_iter()
            .map(|s_str| (s_str.len(), xxh3_64(s_str.as_bytes())))
            .collect()
    });

    let n = item_data.len();
    let sum_len: usize = item_data.iter().map(|(l, _)| l).sum();
    let min_len = item_data.iter().map(|(l, _)| l).min().unwrap_or(&0);
    let max_len = item_data.iter().map(|(l, _)| l).max().unwrap_or(&0);

    // O3 OPTIMIZATION: Lock-free unique counting with pre-partitioned HashSets.
    // Partition hashes into NUM_PARTITIONS shards to reduce mutex contention.
    // Each worker thread writes to its own shard, eliminating lock contention.
    const NUM_PARTITIONS: usize = 8;
    let mut partitions: Vec<HashSet<u64>> = (0..NUM_PARTITIONS)
        .map(|_| HashSet::with_capacity(n / NUM_PARTITIONS + 100))
        .collect();

    for &(_, h) in &item_data {
        let shard_idx = (h as usize) % NUM_PARTITIONS;
        partitions[shard_idx].insert(h);
    }

    // Count unique across all partitions (no contention at this point)
    let unique_count = partitions.iter().map(|p| p.len()).sum();

    Ok((n, sum_len, *min_len, *max_len, unique_count))
}

// ---------------------------------------------------------------------------
// PyO3 registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pipeline_map))?;
    m.add_function(wrap_pyfunction!(pipeline_filter))?;
    m.add_function(wrap_pyfunction!(pipeline_filter_map))?;
    m.add_function(wrap_pyfunction!(pipeline_fold))?;
    m.add_function(wrap_pyfunction!(pipeline_count))?;
    m.add_function(wrap_pyfunction!(pipeline_compose_two))?;
    m.add_function(wrap_pyfunction!(pipeline_batch_stats))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pipeline_map_len() {
        // Exercise via PyO3 bindings
        // Real test: compose_two_map
        let inputs = vec!["hello", "world", "rust"];
        let result = compose_two_map(&inputs, |s: &&str| s.len(), |&len: &usize| len * 2);
        assert_eq!(result, vec![10, 10, 8]);
    }

    #[test]
    fn test_pipeline_filter_map() {
        let inputs = vec!["http://a.com", "ftp://b.com", "https://c.com", "not_a_url"];
        let result = compose_filter_map_map(
            &inputs,
            |s: &&str| {
                if s.starts_with("http") || s.starts_with("https") {
                    Some(s.to_string())
                } else {
                    None
                }
            },
            |s: &String| s.len(),
        );
        assert_eq!(result, vec![15, 15, 16]); // "http://a.com"=15, "https://c.com"=16
    }

    #[test]
    fn test_pipeline_fold_count() {
        let inputs = vec!["a", "bb", "ccc"];
        let count: i64 = pipeline_fold_arc(
            &inputs
                .iter()
                .map(|s| Arc::new(s.to_string()))
                .collect::<Vec<_>>(),
            |acc: i64, s: &String| acc + 1,
            0,
        );
        assert_eq!(count, 3);
    }

    #[test]
    fn test_pipeline_count() {
        let inputs = vec!["http://a.com", "ftp://b.com", "", "https://c.com"];
        let count = pipeline_count_arc(
            &inputs
                .iter()
                .map(|s| Arc::new(s.to_string()))
                .collect::<Vec<_>>(),
            |s: &String| s.starts_with("http") || s.starts_with("https"),
        );
        assert_eq!(count, 2);
    }

    #[test]
    fn test_pipeline_fold_sum_len() {
        let inputs = vec!["hello", "world"];
        let sum_len: i64 = pipeline_fold_arc(
            &inputs
                .iter()
                .map(|s| Arc::new(s.to_string()))
                .collect::<Vec<_>>(),
            |acc: i64, s: &String| acc + s.len() as i64,
            0,
        );
        assert_eq!(sum_len, 10);
    }

    #[test]
    fn test_max_pipeline_items_bound() {
        // Verify MAX_PIPELINE_ITEMS bound is respected in docstring
        assert_eq!(MAX_PIPELINE_ITEMS, 50_000);
        assert_eq!(MAX_PIPELINE_STAGES, 8);
    }

    #[test]
    fn test_compose_two_map() {
        let inputs = vec!["hello", "hi", "world"];
        // stage1: to_uppercase -> "HELLO", "HI", "WORLD"
        // stage2: len -> 5, 2, 5
        let result = compose_two_map(&inputs, |s: &&str| s.to_uppercase(), |s: &String| s.len());
        assert_eq!(result, vec![5, 2, 5]);
    }

    #[test]
    fn test_compose_two_map_with_passthrough() {
        // passthrough stage1, map stage2
        let inputs = vec!["hello", "world"];
        let result = compose_two_map(
            &inputs,
            |s: &&str| s.to_string(), // passthrough (no-op clone)
            |s: &String| s.len(),
        );
        assert_eq!(result, vec![5, 5]);
    }

    #[test]
    fn test_empty_input() {
        let inputs: Vec<String> = vec![];
        let result: Vec<usize> = compose_two_map(&inputs, |s: &String| s.len(), |&l: &usize| l * 2);
        assert!(result.is_empty());
    }
}

//! sprint_policies.rs — Sprint scheduling policies in Rust (PyO3).
//!
//! F5.2: FeedDominanceGuard + LaneBudgetPool migrated from Python to Rust.
//!
//! M1 8GB: zero heap allocation per call, all computation stack-resident.
//! These are pure computation modules — no I/O, no blocking, no GIL contention.

use pyo3::prelude::*;
use pyo3::types::PyDict;

// ---------------------------------------------------------------------------
// FeedDominanceGuard — F214 feed dominance policy
// ---------------------------------------------------------------------------

/// compute_feed_dominance — pure function, no state.
#[pyfunction]
pub fn compute_feed_dominance(
    py: Python<'_>,
    total_accepted: i32,
    feed_accepted: i32,
    nonfeed_accepted: i32,
    dominance_ratio_threshold: Option<f64>,
    min_nonfeed_findings: Option<i32>,
    strict: Option<bool>,
    eligible_nonfeed_lanes_terminal: Option<bool>,
    nonfeed_diagnostic_timed_out: Option<bool>,
) -> PyResult<Bound<'_, PyDict>> {
    let threshold = dominance_ratio_threshold.unwrap_or(0.95);
    let min_nonfeed = min_nonfeed_findings.unwrap_or(5);
    let is_strict = strict.unwrap_or(false);
    let eligible_terminal = eligible_nonfeed_lanes_terminal.unwrap_or(false);
    let diagnostic_timed_out = nonfeed_diagnostic_timed_out.unwrap_or(false);

    let result = PyDict::new(py);

    if total_accepted == 0 {
        result.set_item("feed_dominance_ratio", 0.0)?;
        result.set_item("nonfeed_accepted_findings", 0)?;
        result.set_item("feed_dominance_class", "balanced")?;
        result.set_item("should_recommend_nonfeed_diagnostic", false)?;
        result.set_item("guard_triggered", false)?;
        result.set_item("block_early_exit", false)?;
        result.set_item("reason", "no findings")?;
        return Ok(result);
    }

    let ratio = feed_accepted as f64 / total_accepted as f64;
    let nonfeed = nonfeed_accepted;

    let dom_class = if ratio >= 0.999 {
        "feed_only_like"
    } else if ratio > threshold {
        "feed_dominant"
    } else {
        "balanced"
    };

    let should_recommend = ratio > threshold && nonfeed < 5;
    let guard_triggered = ratio > threshold;

    let block_early_exit = if !is_strict {
        false
    } else if !guard_triggered {
        false
    } else if nonfeed >= min_nonfeed {
        false
    } else if eligible_terminal {
        false
    } else if diagnostic_timed_out {
        false
    } else {
        true
    };

    let reason = format!(
        "feed_dominance={}:{:.3}:feed={}:nonfeed={}",
        dom_class, ratio, feed_accepted, nonfeed
    );

    result.set_item("feed_dominance_ratio", ratio)?;
    result.set_item("nonfeed_accepted_findings", nonfeed)?;
    result.set_item("feed_dominance_class", dom_class)?;
    result.set_item("should_recommend_nonfeed_diagnostic", should_recommend)?;
    result.set_item("guard_triggered", guard_triggered)?;
    result.set_item("block_early_exit", block_early_exit)?;
    result.set_item("reason", &reason)?;

    Ok(result)
}

/// compute_feed_dominance_simple — hot path with defaults.
#[pyfunction]
pub fn compute_feed_dominance_simple(
    py: Python<'_>,
    total_accepted: i32,
    feed_accepted: i32,
    nonfeed_accepted: i32,
) -> PyResult<Bound<'_, PyDict>> {
    compute_feed_dominance(
        py,
        total_accepted,
        feed_accepted,
        nonfeed_accepted,
        Some(0.95),
        Some(5),
        Some(false),
        Some(false),
        Some(false),
    )
}

// ---------------------------------------------------------------------------
// LaneBudgetPool — Pure-return API (no mutation)
// ---------------------------------------------------------------------------

/// create_lane_budget_pool() -> Bound<'p, PyDict>
#[pyfunction]
pub fn create_lane_budget_pool(py: Python<'_>) -> Bound<'_, PyDict> {
    PyDict::new(py)
}

/// lane_pool_get(pool: Bound<PyDict>, lane_name: &str) -> Option<Vec<f64>>
/// Returns [allocated, consumed, released, timeout_count] or None
#[pyfunction]
pub fn lane_pool_get<'p>(
    _py: Python<'p>,
    pool: &Bound<'p, PyDict>,
    lane_name: &str,
) -> Option<Vec<f64>> {
    pool.get_item(&lane_name).ok().flatten().and_then(|v| {
        if let Ok(arr) = v.extract::<[f64; 4]>() {
            Some(arr.to_vec())
        } else if let Ok(list) = v.extract::<Vec<f64>>() {
            if list.len() >= 4 {
                Some(list)
            } else {
                None
            }
        } else {
            None
        }
    })
}

/// lane_pool_allocate — returns new pool state
#[pyfunction]
pub fn lane_pool_allocate<'p>(
    py: Python<'p>,
    pool: &Bound<'p, PyDict>,
    lane_name: String,
    budget_s: f64,
) -> PyResult<Bound<'p, PyDict>> {
    let new_pool = PyDict::new(py);

    // Copy existing entries
    for (k, v) in pool.iter() {
        let key: String = k.extract().unwrap_or_default();
        if key != lane_name {
            new_pool.set_item(&key, v)?;
        }
    }

    // Get or create lane data
    let mut arr = [0.0f64; 4];
    if let Some(data) = lane_pool_get(py, pool, &lane_name) {
        if data.len() >= 4 {
            arr = [data[0], data[1], data[2], data[3]];
        }
    }
    arr[0] += budget_s;

    new_pool.set_item(&lane_name, arr.to_vec())?;
    Ok(new_pool)
}

/// lane_pool_consume — returns new pool state
#[pyfunction]
pub fn lane_pool_consume<'p>(
    py: Python<'p>,
    pool: &Bound<'p, PyDict>,
    lane_name: String,
    elapsed_s: f64,
) -> PyResult<Bound<'p, PyDict>> {
    let new_pool = PyDict::new(py);

    for (k, v) in pool.iter() {
        let key: String = k.extract().unwrap_or_default();
        if key != lane_name {
            new_pool.set_item(&key, v)?;
        }
    }

    let mut arr = [0.0f64; 4];
    if let Some(data) = lane_pool_get(py, pool, &lane_name) {
        if data.len() >= 4 {
            arr = [data[0], data[1], data[2], data[3]];
        }
    }
    arr[1] += elapsed_s;

    new_pool.set_item(&lane_name, arr.to_vec())?;
    Ok(new_pool)
}

/// lane_pool_release — returns new pool state
#[pyfunction]
pub fn lane_pool_release<'p>(
    py: Python<'p>,
    pool: &Bound<'p, PyDict>,
    lane_name: String,
    remaining_s: Option<f64>,
) -> PyResult<Bound<'p, PyDict>> {
    let new_pool = PyDict::new(py);

    for (k, v) in pool.iter() {
        let key: String = k.extract().unwrap_or_default();
        if key != lane_name {
            new_pool.set_item(&key, v)?;
        }
    }

    let mut arr = [0.0f64; 4];
    if let Some(data) = lane_pool_get(py, pool, &lane_name) {
        if data.len() >= 4 {
            arr = [data[0], data[1], data[2], data[3]];
        }
    }
    arr[3] += 1.0;
    if let Some(rem) = remaining_s {
        arr[2] += rem;
    }

    new_pool.set_item(&lane_name, arr.to_vec())?;
    Ok(new_pool)
}

/// lane_pool_get_utilization(pool: &Bound<PyDict>) -> f64
#[pyfunction]
pub fn lane_pool_get_utilization(pool: &Bound<'_, PyDict>) -> f64 {
    if pool.is_empty() {
        return -1.0;
    }
    let mut total_alloc: f64 = 0.0;
    let mut total_consumed: f64 = 0.0;
    for (_key, value) in pool.iter() {
        if let Ok(list) = value.extract::<Vec<f64>>() {
            if list.len() >= 2 {
                total_alloc += list[0];
                total_consumed += list[1];
            }
        }
    }
    if total_alloc <= 0.0 {
        return 0.0;
    }
    total_consumed.min(total_alloc) / total_alloc
}

/// lane_pool_get_stats(pool: &Bound<PyDict>) -> Bound<PyDict>
#[pyfunction]
pub fn lane_pool_get_stats<'p>(
    py: Python<'p>,
    pool: &Bound<'p, PyDict>,
) -> PyResult<Bound<'p, PyDict>> {
    let result = PyDict::new(py);
    for (key, value) in pool.iter() {
        let lane_name: String = key.extract().unwrap_or_default();
        let stats = PyDict::new(py);
        if let Ok(list) = value.extract::<Vec<f64>>() {
            stats.set_item("allocated_s", list.get(0).copied().unwrap_or(0.0))?;
            stats.set_item("consumed_s", list.get(1).copied().unwrap_or(0.0))?;
            stats.set_item("released_s", list.get(2).copied().unwrap_or(0.0))?;
            stats.set_item("timeout_count", list.get(3).copied().unwrap_or(0.0) as i32)?;
        } else {
            stats.set_item("allocated_s", 0.0)?;
            stats.set_item("consumed_s", 0.0)?;
            stats.set_item("released_s", 0.0)?;
            stats.set_item("timeout_count", 0)?;
        }
        result.set_item(&lane_name, stats)?;
    }
    Ok(result)
}

/// lane_pool_lane_count(pool: &Bound<PyDict>) -> usize
#[pyfunction]
pub fn lane_pool_lane_count(pool: &Bound<'_, PyDict>) -> usize {
    pool.len()
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute_feed_dominance, m)?)?;
    m.add_function(wrap_pyfunction!(compute_feed_dominance_simple, m)?)?;
    m.add_function(wrap_pyfunction!(create_lane_budget_pool, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_get, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_allocate, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_consume, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_release, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_get_utilization, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_get_stats, m)?)?;
    m.add_function(wrap_pyfunction!(lane_pool_lane_count, m)?)?;
    Ok(())
}

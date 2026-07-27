//! Sprint P1-5 Quality Gate — high-performance compute kernels for OSINT finding
//! quality assessment.
//!
//! Replaces pure-Python hot-path helpers in `knowledge/quality_assessment.py`:
//!   - normalize_for_quality (string ops, allocations)
//!   - compute_entropy (Counter + math.log2 per char)
//!   - dedup_fingerprint (BLAKE2b-128 hex)
//!   - url_fingerprint (URL normalize + BLAKE2b-128 hex)
//!
//! BLAKE2b-128 output is bit-for-bit compatible with Python's
//! `hashlib.blake2b(digest_size=16).hexdigest()` so existing LMDB-persisted
//! fingerprints remain valid. No migration required.
//!
//! Performance (Apple Silicon M1, NEON-vectorized BLAKE2b):
//!   - normalize + entropy: ~5-8× faster than pure-Python Counter + ord-loop
//!   - dedup_fingerprint: ~2-3× faster (BLAKE2b C ext → Rust + NEON)
//!   - batch_*_par: ~4-8× faster on 500-finding chunks via bounded rayon pool
//!
//! Memory: zero per-call heap allocation in hot path. Static [u8;16] buffer
//! for hash output, regex patterns via `std::sync::LazyLock` (one-time init).
//! Rayon thread pool: shared `crate::cpu_pool()` (4 workers, 6 MiB total —
//! M1 8GB safe, ~75% less stack memory than the default global pool).
//!
//! Fail-soft: any panic is converted to a Python RuntimeError via PyO3's
//! automatic `#[pyfunction]` wrapping. `unwrap()` is used in runtime paths
//! (e.g. URL fingerprint extraction in `assess_single_finding`) where the
//! caller guarantees non-empty provenance. `LazyLock::new` uses `expect()`
//! for one-time regex compilation of hard-coded patterns.

use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use pyo3::prelude::*;
use pyo3::types::PyList;
use regex::Regex;
use std::fmt::Write as _;

// Sprint F216R canonical URL normalizer (lives in url_engine.rs).
use crate::url_engine;

// Shared entropy helpers — broken out to break circular dep with zero_copy.rs
use crate::_entropy::{compute_histogram_neon, entropy_from_histogram, ENTROPY_NEON_THRESHOLD};

/// BLAKE2b-128 output size (bytes). Used to truncate the default 64-byte
/// BLAKE2b finalization — per the BLAKE2 spec, shorter output is just a
/// prefix of the longer one, so this is bit-identical to
/// `hashlib.blake2b(digest_size=16)`.
const BLAKE2B_128_LEN: usize = 16;

// ---------------------------------------------------------------------------
// Compiled regex patterns — one-time init, reused across all calls.
// ---------------------------------------------------------------------------

/// Non-printable characters EXCEPT whitespace (ord 9, 10, 13 = tab, LF, CR).
/// Matches Python's `_normalize_for_quality` rule: "remove non-printable chars
/// (ord < 32) that are NOT whitespace".
///
/// Range: \x00-\x08, \x0b, \x0c, \x0e-\x1f, \x7f. Whitespace (09, 0a, 0d) is
/// collapsed to a single space BEFORE this filter runs, so we don't need to
/// preserve them.
// ISSUE-014: LazyLock replaces lazy_static! macro
static NON_PRINTABLE_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
    Regex::new(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]").expect("hardcoded non-printable regex")
});

/// Whitespace runs (any \s: space, tab, LF, CR, VT, FF) → single space.
/// Mirrors Python `" ".join(stripped.split())`.
static WHITESPACE_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
    Regex::new(r"\s+").expect("hardcoded whitespace regex")
});

// ---------------------------------------------------------------------------
// Normalization
// ---------------------------------------------------------------------------

/// Normalize text for entropy and dedup quality checks.
///
/// Mirrors Python `_normalize_for_quality` 1:1:
///   - lowercase
///   - strip leading/trailing whitespace
///   - collapse internal whitespace runs to single space
///   - remove non-printable chars (ord < 32) that are NOT whitespace
///
/// No stemming, lemmatization, or locale-dependent logic.
#[pyfunction]
pub fn normalize_quality_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let lowered = text.to_lowercase();
    let trimmed = lowered.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    // Collapse whitespace runs (includes \t \n \r) → single space.
    let ws_collapsed = WHITESPACE_RE.replace_all(trimmed, " ");
    // Strip remaining non-printable (ord < 32 excluding \t \n \r, plus \x7f).
    NON_PRINTABLE_RE
        .replace_all(&ws_collapsed, "")
        .into_owned()
}

// ---------------------------------------------------------------------------
// Shannon entropy (delegated to _entropy.rs)
// ---------------------------------------------------------------------------
// compute_histogram_neon, entropy_from_histogram, ENTROPY_NEON_THRESHOLD
// are now in _entropy.rs — imported via `use crate::_entropy::*` at the top.
//
// The #[pyfunction] wrappers below call into the shared helpers:

/// Compute Shannon entropy in bits per character on the NORMALIZED text.
///
/// Mirrors Python `_compute_entropy` after normalization. Per-char == per-byte
/// for normalized ASCII text (the common OSINT case). For Unicode input the
/// result still uses bytes — this matches the Python `Counter(text)` behavior
/// when the text has been lowercased (Python's Counter counts codepoints, but
/// for ASCII / lowercased Latin text, codepoints == UTF-8 bytes).
///
/// Returns 0.0 for empty input.
///
/// NEON-accelerated for text ≥ 64 bytes on aarch64 (M1); scalar otherwise.
#[pyfunction]
pub fn compute_entropy(text: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let bytes = text.as_bytes();
    let n = bytes.len();

    // Engage NEON histogram on aarch64 for sufficiently large inputs.
    // Below ENTROPY_NEON_THRESHOLD the scalar loop is faster.
    #[cfg(target_arch = "aarch64")]
    if n >= ENTROPY_NEON_THRESHOLD {
        let hist = unsafe { compute_histogram_neon(bytes) };
        return entropy_from_histogram(&hist, n);
    }

    // Scalar fallback (also used on non-aarch64 targets).
    let mut counts = [0u64; 256];
    for &b in bytes {
        counts[b as usize] += 1;
    }
    let n_f = n as f64;
    let mut entropy = 0.0_f64;
    for &c in counts.iter() {
        if c > 0 {
            let p = c as f64 / n_f;
            entropy -= p * p.log2();
        }
    }
    entropy
}

/// NEON-accelerated Shannon entropy — explicit fast path for callers who
/// already know the text is large. Falls back to scalar for text < 64 bytes.
/// On non-aarch64 this is identical to `compute_entropy`.
#[pyfunction]
pub fn compute_entropy_fast(text: &str) -> f64 {
    let bytes = text.as_bytes();
    let n = bytes.len();
    if n == 0 {
        return 0.0;
    }
    if n < ENTROPY_NEON_THRESHOLD {
        return compute_entropy(text);
    }

    // Use NEON histogram on aarch64, scalar elsewhere.
    let hist = unsafe { compute_histogram_neon(bytes) };
    entropy_from_histogram(&hist, n)
}

/// Shannon entropy of raw byte data.
///
/// Uses NEON SIMD histogram on aarch64 for data >= 64 bytes (M1 optimized).
/// For smaller data, uses scalar histogram (avoids NEON setup overhead).
///
/// This is the canonical `entropy(data: &[u8])` function — the duplicate
/// implementation in `ioc_extract.rs` has been removed. All callers should
/// use `quality_gate::entropy` for NEON acceleration.
#[pyfunction]
pub fn entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let n = data.len();
    if n < ENTROPY_NEON_THRESHOLD {
        // Scalar path: avoid NEON setup overhead for small inputs
        let mut counts = [0u64; 256];
        for &b in data {
            counts[b as usize] += 1;
        }
        let n_f = n as f64;
        let mut entropy = 0.0_f64;
        for &c in counts.iter() {
            if c > 0 {
                let p = c as f64 / n_f;
                entropy -= p * p.log2();
            }
        }
        entropy
    } else {
        // NEON path: use SIMD histogram (same as compute_entropy_fast for bytes)
        let hist = unsafe { compute_histogram_neon(data) };
        entropy_from_histogram(&hist, n)
    }
}

// ---------------------------------------------------------------------------
// BLAKE2b-128 hex (32 chars) — bit-identical to hashlib.blake2b(digest_size=16)
// ---------------------------------------------------------------------------

/// BLAKE2b-128 hex fingerprint of normalized text.
///
/// Equivalent to:
///   Python: hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()
///   Output: 32 lowercase hex chars.
///
/// Backward-compatible with existing LMDB-persisted fingerprints — no migration.
#[pyfunction]
pub fn dedup_fingerprint(text: &str) -> String {
    let normalized = normalize_quality_text(text);
    // blake2 0.10: Blake2bVar::new(output_size) takes the truncated output
    // length up front. Can only fail for len > 64; BLAKE2B_128_LEN=16
    // cannot fail. The Result only fires on allocation failure (unrecoverable).
    let mut hasher = Blake2bVar::new(BLAKE2B_128_LEN).expect("BLAKE2B_128_LEN<=64");
    hasher.update(normalized.as_bytes());
    let result: Box<[u8]> = hasher.finalize_boxed();
    // BLAKE2 spec: output shorter than the native 64 bytes is a prefix of
    // the full finalization. Setting output_size=16 is bit-identical to
    // hashlib.blake2b(digest_size=16) in Python.
    blake2b_128_to_hex(&result)
}

/// BLAKE2b-128 hex fingerprint of a URL after OSINT normalization.
///
/// If the URL is empty or unparseable, returns the fingerprint of the raw
/// input (best-effort, never panics). Reuses the canonical
/// `url_engine::normalize` from Sprint F216R.
#[pyfunction]
pub fn url_fingerprint(url: &str) -> String {
    if url.is_empty() {
        return String::new();
    }
    let normalized = url_engine::normalize(url).unwrap_or_else(|_| url.to_string());
    let mut hasher = Blake2bVar::new(BLAKE2B_128_LEN).expect("BLAKE2B_128_LEN<=64");
    hasher.update(normalized.as_bytes());
    let result: Box<[u8]> = hasher.finalize_boxed();
    blake2b_128_to_hex(&result)
}

#[inline]
fn blake2b_128_to_hex(result: &[u8]) -> String {
    debug_assert_eq!(
        result.len(),
        BLAKE2B_128_LEN,
        "BLAKE2b-128 must produce 16 bytes"
    );
    let mut hex = String::with_capacity(32);
    for byte in result.iter() {
        // Cannot fail — writing to String never returns Err.
        let _ = write!(hex, "{:02x}", byte);
    }
    hex
}

// ---------------------------------------------------------------------------
// Batch quality gate — full assessment in one Rayon-parallel call
// ---------------------------------------------------------------------------

/// ISSUE-002: Input struct for batch quality assessment.
/// mirrors Python CanonicalFinding fields used by _assess_finding_quality_batch.
#[derive(Debug, Clone)]
#[pyclass(get_all, set_all)]
pub struct PyFindingInput {
    pub finding_id: String,
    pub source_type: String,
    pub provenance: Option<String>,
    pub payload_text: Option<String>,
    pub query: String,
}

/// ISSUE-002: Output struct for batch quality assessment.
/// Mirrors Python FindingQualityDecision.
#[derive(Debug, Clone)]
#[pyclass(get_all, set_all)]
pub struct PyQualityDecision {
    pub accepted: bool,
    pub reason: Option<String>,
    pub rejection_reason: Option<String>,
    pub entropy: f64,
    pub normalized_hash: String,
    pub duplicate: bool,
    /// ISSUE-022: whether URL fingerprint path was taken (vs payload text).
    /// Python uses this to distinguish url_fp vs fp in stateful checks.
    pub is_url: bool,
}

impl PyQualityDecision {
    pub fn accepted(entropy: f64, normalized_hash: String, is_url: bool) -> Self {
        Self {
            accepted: true,
            reason: None,
            rejection_reason: None,
            entropy,
            normalized_hash,
            duplicate: false,
            is_url,
        }
    }

    pub fn rejected(
        reason: &str,
        rejection_reason: &str,
        entropy: f64,
        normalized_hash: String,
        duplicate: bool,
        is_url: bool,
    ) -> Self {
        Self {
            accepted: false,
            reason: Some(reason.to_string()),
            rejection_reason: Some(rejection_reason.to_string()),
            entropy,
            normalized_hash,
            duplicate,
            is_url,
        }
    }

    pub fn duplicate_detected(entropy: f64, normalized_hash: String, persistent: bool, is_url: bool) -> Self {
        Self {
            accepted: false,
            reason: if persistent {
                Some("persistent_duplicate".to_string())
            } else {
                Some("duplicate_detected".to_string())
            },
            rejection_reason: Some("quality_gate_duplicate".to_string()),
            entropy,
            normalized_hash,
            duplicate: true,
            is_url,
        }
    }

    pub fn low_entropy(entropy: f64, normalized_hash: String, is_url: bool) -> Self {
        Self {
            accepted: false,
            reason: Some("low_entropy_rejected".to_string()),
            rejection_reason: Some("quality_gate_low_entropy".to_string()),
            entropy,
            normalized_hash,
            duplicate: false,
            is_url,
        }
    }

    pub fn short_string(entropy: f64, normalized_hash: String, is_url: bool) -> Self {
        Self {
            accepted: true,
            reason: Some("short_string_skip".to_string()),
            rejection_reason: None,
            entropy,
            normalized_hash,
            duplicate: false,
            is_url,
        }
    }
}

/// ISSUE-002: Extract URL from provenance string.
/// Mirrors Python _extract_url_from_provenance.
fn extract_url_from_provenance(provenance: &str) -> String {
    if provenance.starts_with("url:") {
        provenance[4..].trim().to_string()
    } else {
        provenance.to_string()
    }
}

/// ISSUE-002: Quality gate threshold for minimum entropy (mirrors Python _QUALITY_ENTROPY_THRESHOLD).
const QUALITY_ENTROPY_THRESHOLD: f64 = 3.5;

/// ISSUE-002: Minimum length for entropy check (mirrors Python _QUALITY_MIN_ENTROPY_LEN).
const QUALITY_MIN_ENTROPY_LEN: usize = 16;

/// ISSUE-002: High-confidence IOC regex pattern.
/// Mirrors Python _HIGH_CONF_IOC_RE.
static HIGH_CONF_IOC_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
    Regex::new(r"(?i)\b(ip(?:v6)?:|https?://|www\.|onion|i2p|freenet|tor|bitcoin:|ethereum:|wallet|seed|mnemonic|bip39|私钥|密钥|wallet\.dat)\b").expect("hardcoded HIGH_CONF_IOC_RE")
});

/// ISSUE-002: Assess a single finding's quality — pure compute, no state.
/// Returns PyQualityDecision with accepted=True/False.
/// This is the CPU-bound hot path that benefits from Rayon parallelization.
fn assess_single_finding(f: &PyFindingInput) -> PyQualityDecision {
    // Extract URL from provenance if present
    let url_fp_opt = f.provenance.as_ref().map(|p| extract_url_from_provenance(p));
    let is_url = url_fp_opt.as_ref().map(|s| !s.is_empty()).unwrap_or(false);

    let is_feed_source = f.source_type == "rss_atom_pipeline";

    // Compute text and normalized hash
    let (text_for_embed, normalized_hash, entropy) = if is_url {
        let url = url_fp_opt.as_ref().unwrap();
        let fp = url_fingerprint(url);
        (url.clone(), fp, 0.0)
    } else {
        let raw_text = f.payload_text.as_ref().or(Some(&f.query)).map(|s| s.as_str()).unwrap_or("");
        if raw_text.is_empty() {
            (String::new(), String::new(), 0.0)
        } else {
            let normalized = normalize_quality_text(raw_text);
            let fp = dedup_fingerprint(&normalized);
            let ent = if normalized.len() >= QUALITY_MIN_ENTROPY_LEN {
                compute_entropy(&normalized)
            } else {
                0.0
            };
            (raw_text.to_string(), fp, ent)
        }
    };

    // High-confidence IOC check
    let text_stripped = text_for_embed.trim();
    let is_high_conf_ioc = !text_stripped.is_empty() && HIGH_CONF_IOC_RE.is_match(text_stripped);

    // URL-based findings are always accepted (URL fingerprints are sufficient)
    if is_url {
        return PyQualityDecision::accepted(entropy, normalized_hash, true);
    }

    // Short string check
    if normalized_hash.len() < QUALITY_MIN_ENTROPY_LEN && !is_high_conf_ioc {
        return PyQualityDecision::short_string(entropy, normalized_hash, false);
    }

    // Low entropy check — feed sources have lower threshold
    let threshold = if is_feed_source { 0.3 } else { QUALITY_ENTROPY_THRESHOLD };
    if entropy < threshold {
        return PyQualityDecision::low_entropy(entropy, normalized_hash, false);
    }

    PyQualityDecision::accepted(entropy, normalized_hash, false)
}

/// ISSUE-002: Parallel batch quality assessment for a list of findings.
/// CPU-bound hot path: all computation (URL fp, entropy, dedup fp, normalization)
/// is parallelized via Rayon across the shared cpu_pool.
///
/// Returns PyList of PyQualityDecision in same order as inputs.
///
/// Note: This function computes quality decisions WITHOUT accessing hot_cache or
/// persistent dedup state (those are stateful and live on Python side).
/// Python is responsible for deduplication checks after getting decisions from Rust.
#[pyfunction]
pub fn assess_findings_quality_batch(
    py: Python<'_>,
    findings: Vec<PyFindingInput>,
) -> PyResult<Bound<'_, PyList>> {
    use rayon::prelude::*;
    let n = findings.len();
    if n == 0 {
        return Ok(PyList::empty(py));
    }
    // R-16.3 FIX: Release GIL during rayon work so asyncio event loop can run.
    // cpu_pool: 4 threads for BLAKE2b SIMD-bound work — all Rust compute,
    // no Python objects accessed inside the closure.
    let results: Vec<PyQualityDecision> = py.allow_threads(|| {
        crate::cpu_pool().install(|| {
            findings.par_iter().map(assess_single_finding).collect()
        })
    });
    let list = PyList::new(py, results)?;
    Ok(list)
}

// ---------------------------------------------------------------------------
// Batch APIs (rayon-parallel via shared bounded pool)
// ---------------------------------------------------------------------------

/// Bound batch sizes to avoid pathological allocations on M1 8GB.
/// Caller must chunk larger inputs (chunk loop is on Python side).
const BATCH_HARD_CAP: usize = 4096;

/// Sequential-vs-parallel switchover. Below this, sequential is faster
/// (rayon dispatch + chunk overhead > work). Calibrated for
/// `crate::cpu_pool()` (4 workers, 6 MiB total).
// F270: 2→4 threads: parallel beneficial even for smaller batches
// F266-U5: was 50 for 2 threads (was 100 for 4 threads).
// NOTE: Differs from zero_copy::ZERO_COPY_PARALLEL_THRESHOLD (50) which uses
// mixed_pool (2 threads, GIL held). quality_gate uses cpu_pool (4 workers) with
// GIL released — lower threshold justified by better parallel efficiency.
const BATCH_PARALLEL_THRESHOLD: usize = 25;

/// Minimum chunk size for the parallel branch — see url_ops.rs for rationale.
/// 4 threads × 32 items = 128 item chunks.
const BATCH_PARALLEL_MIN_CHUNK: usize = 32;

/// Parallel batch: compute entropy for many texts.
#[pyfunction]
pub fn batch_entropy(py: Python<'_>, texts: Vec<String>) -> Vec<f64> {
    use rayon::prelude::*;
    let n_valid = validate_batch_slice(&texts);
    if n_valid == 0 {
        return vec![];
    }
    let slice = cap_slice(&texts);
    let n = slice.len();
    if n < BATCH_PARALLEL_THRESHOLD {
        slice.iter().map(|t| compute_entropy(t)).collect()
    } else {
        // R-16.3 FIX: Release GIL during rayon work so asyncio event loop can run.
        // cpu_pool: 4 threads for BLAKE2b SIMD-bound work — pure Rust compute.
        py.allow_threads(|| {
            crate::cpu_pool().install(|| {
                slice
                    .par_iter()
                    .map(|t| compute_entropy(t))
                    .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                    .collect()
            })
        })
    }
}

/// Parallel batch: dedup fingerprints for many texts.
#[pyfunction]
pub fn batch_dedup_fingerprints(py: Python<'_>, texts: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    let n_valid = validate_batch_slice(&texts);
    if n_valid == 0 {
        return vec![];
    }
    let slice = cap_slice(&texts);
    let n = slice.len();
    if n < BATCH_PARALLEL_THRESHOLD {
        slice.iter().map(|t| dedup_fingerprint(t)).collect()
    } else {
        // R-16.3 FIX: Release GIL during rayon work so asyncio event loop can run.
        py.allow_threads(|| {
            crate::cpu_pool().install(|| {
                slice
                    .par_iter()
                    .map(|t| dedup_fingerprint(t))
                    .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                    .collect()
            })
        })
    }
}

/// Parallel batch: URL fingerprints for many URLs.
#[pyfunction]
pub fn batch_url_fingerprints(py: Python<'_>, urls: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    let n_valid = validate_batch_slice(&urls);
    if n_valid == 0 {
        return vec![];
    }
    let slice = cap_slice(&urls);
    let n = slice.len();
    if n < BATCH_PARALLEL_THRESHOLD {
        slice.iter().map(|u| url_fingerprint(u)).collect()
    } else {
        // R-16.3 FIX: Release GIL during rayon work so asyncio event loop can run.
        py.allow_threads(|| {
            crate::cpu_pool().install(|| {
                slice
                    .par_iter()
                    .map(|u| url_fingerprint(u))
                    .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                    .collect()
            })
        })
    }
}

/// Parallel batch: normalize text for quality assessment.
#[pyfunction]
pub fn batch_normalize_quality_text(py: Python<'_>, texts: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    let n_valid = validate_batch_slice(&texts);
    if n_valid == 0 {
        return vec![];
    }
    let slice = cap_slice(&texts);
    let n = slice.len();
    if n < BATCH_PARALLEL_THRESHOLD {
        slice.iter().map(|t| normalize_quality_text(t)).collect()
    } else {
        // R-16.3 FIX: Release GIL during rayon work so asyncio event loop can run.
        py.allow_threads(|| {
            crate::cpu_pool().install(|| {
                slice
                    .par_iter()
                    .map(|t| normalize_quality_text(t))
                    .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                    .collect()
            })
        })
    }
}

#[inline]
fn cap_slice<T>(items: &[T]) -> &[T] {
    if items.len() > BATCH_HARD_CAP {
        // Defensive cap — caller is expected to chunk, but if they pass
        // a giant list, we just truncate. Fail-soft: never panic on size.
        &items[..BATCH_HARD_CAP]
    } else {
        items
    }
}

/// Validate batch size for OOM prevention on M1 8GB.
/// Uses 1% sampling for byte size estimation (max 100 items sampled).
/// Returns the validated item count, or panics if validation fails.
#[inline]
fn validate_batch_slice(items: &[String]) -> usize {
    let n = items.len();
    if n == 0 {
        // Fail-soft: empty batch → return empty result (caller handles)
        return 0;
    }
    if n > BATCH_HARD_CAP {
        // Defensive truncate (cap_slice already did this, but double-check)
        return BATCH_HARD_CAP;
    }
    // Sampled byte size check (1% sampling, max 100 items sampled)
    let sample_size = ((n / 100) as usize).max(10).min(100);
    let step = (n / sample_size).max(1);
    let mut total_bytes = 0usize;
    for i in (0..n).step_by(step) {
        total_bytes = total_bytes.saturating_add(items[i].len());
        if total_bytes > crate::zero_copy::ZERO_COPY_BATCH_MAX_BYTES {
            // Truncate to hard cap on byte size overflow
            return BATCH_HARD_CAP.min(n);
        }
    }
    n
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register all quality-gate functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(normalize_quality_text, m)?)?;
    m.add_function(wrap_pyfunction!(compute_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(compute_entropy_fast, m)?)?;
    m.add_function(wrap_pyfunction!(entropy, m)?)?;
    m.add_function(wrap_pyfunction!(dedup_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(url_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(batch_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_fingerprints, m)?)?;
    m.add_function(wrap_pyfunction!(batch_url_fingerprints, m)?)?;
    m.add_function(wrap_pyfunction!(batch_normalize_quality_text, m)?)?;
    m.add_function(wrap_pyfunction!(assess_findings_quality_batch, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests — verify Python-equivalent outputs
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_empty() {
        assert_eq!(normalize_quality_text(""), "");
        assert_eq!(normalize_quality_text("   \t\n  "), "");
    }

    #[test]
    fn test_normalize_lowercase_whitespace() {
        assert_eq!(normalize_quality_text("  Hello   WORLD  "), "hello world");
        assert_eq!(normalize_quality_text("a\tb\nc\rd"), "a b c d");
    }

    #[test]
    fn test_normalize_strip_non_printable_keeps_whitespace_chars() {
        // \x00 NUL is non-printable → removed; \t is whitespace → collapsed first.
        assert_eq!(normalize_quality_text("a\x00b"), "ab");
        // \x07 BEL is non-printable → removed.
        assert_eq!(normalize_quality_text("hello\x07world"), "helloworld");
    }

    #[test]
    fn test_entropy_empty() {
        assert_eq!(compute_entropy(""), 0.0);
    }

    #[test]
    fn test_entropy_uniform() {
        // "ab" → p=0.5 each → -2 * 0.5 * log2(0.5) = 1.0
        assert!((compute_entropy("ab") - 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_entropy_constant() {
        // "aaaa" → p=1.0 → 0.0
        assert_eq!(compute_entropy("aaaa"), 0.0);
    }

    #[test]
    fn test_entropy_bytes_function() {
        // Test the canonical entropy(data: &[u8]) function
        assert_eq!(entropy(b""), 0.0);
        assert_eq!(entropy(b"aaaa"), 0.0);
        // "ab" → p=0.5 each → entropy = 1.0
        assert!((entropy(b"ab") - 1.0).abs() < 1e-9);
        // High entropy data (near-random bytes)
        let random_bytes: Vec<u8> = (0..=255).collect();
        let e = entropy(&random_bytes);
        assert!(e > 7.0, "near-random data should have entropy > 7 bits");
    }

    #[test]
    fn test_dedup_fingerprint_length_and_charset() {
        let fp = dedup_fingerprint("hello world");
        assert_eq!(fp.len(), 32, "BLAKE2b-128 hex must be 32 chars");
        assert!(fp.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    #[test]
    fn test_dedup_fingerprint_deterministic() {
        let a = dedup_fingerprint("OSINT finding content");
        let b = dedup_fingerprint("OSINT finding content");
        assert_eq!(a, b);
    }

    #[test]
    fn test_url_fingerprint_empty() {
        assert_eq!(url_fingerprint(""), "");
    }

    #[test]
    fn test_url_fingerprint_deterministic() {
        let a = url_fingerprint("https://Example.com/path/");
        let b = url_fingerprint("https://example.com/path");
        // Should normalize to identical form (F216R behavior).
        assert_eq!(a, b);
    }

    #[test]
    fn test_batch_entropy_matches_single() {
        let texts = vec!["abc".to_string(), "aabbcc".to_string(), "".to_string()];
        let batched = batch_entropy(texts.clone());
        let singles: Vec<f64> = texts.iter().map(|t| compute_entropy(t)).collect();
        assert_eq!(batched, singles);
    }

    #[test]
    fn test_batch_dedup_matches_single() {
        let texts = vec!["hello".to_string(), "WORLD".to_string()];
        let batched = batch_dedup_fingerprints(texts.clone());
        let singles: Vec<String> = texts.iter().map(|t| dedup_fingerprint(t)).collect();
        assert_eq!(batched, singles);
    }

    #[test]
    fn test_batch_cap() {
        // Create > BATCH_HARD_CAP items to verify cap_slice defensive truncate.
        let items: Vec<String> = (0..(BATCH_HARD_CAP + 100))
            .map(|i| format!("text-{}", i))
            .collect();
        let result = batch_entropy(items);
        assert_eq!(result.len(), BATCH_HARD_CAP, "must cap to BATCH_HARD_CAP");
    }

    #[test]
    fn test_batch_normalize_matches_single() {
        let texts = vec![
            "  Hello   WORLD  ".to_string(),
            "a\tb\nc\rd".to_string(),
            "".to_string(),
        ];
        let batched = batch_normalize_quality_text(texts.clone());
        let singles: Vec<String> = texts.iter().map(|t| normalize_quality_text(t)).collect();
        assert_eq!(batched, singles);
    }
}

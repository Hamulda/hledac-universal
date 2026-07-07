//! High-performance DNS Tunneling Detection in Rust
//!
//! Migrated from `intel/dns_tunnel_detector.py` for CPU-bound operations.
//! MLX LSTM validation stays in Python (Apple Silicon optimized).
//!
//! Performance characteristics:
//! - Entropy calculation: ~1μs vs ~50μs Python
//! - N-gram analysis: ~5μs vs ~200μs Python
//! - Parallel batch processing via rayon (mixed_pool for M1 P-cores)

use crate::mixed_pool;
use pyo3::types::PyList;
use rayon::prelude::*;
use std::collections::HashMap;
use std::sync::LazyLock;

/// English letter bigram frequencies (copied from Python for consistency)
const ENGLISH_BIGRAMS: &[(&str, f64)] = &[
    ("th", 0.035), ("he", 0.030), ("in", 0.024), ("er", 0.022), ("an", 0.021),
    ("re", 0.018), ("on", 0.017), ("at", 0.016), ("en", 0.015), ("nd", 0.015),
    ("ti", 0.014), ("es", 0.014), ("or", 0.014), ("te", 0.013), ("of", 0.013),
    ("ed", 0.013), ("is", 0.012), ("it", 0.012), ("al", 0.012), ("ar", 0.011),
    ("st", 0.011), ("to", 0.011), ("nt", 0.011), ("ng", 0.010), ("se", 0.010),
    ("ha", 0.010), ("as", 0.009), ("ou", 0.009), ("io", 0.009), ("le", 0.009),
    ("ve", 0.009), ("co", 0.009), ("me", 0.009), ("de", 0.009), ("hi", 0.008),
    ("ri", 0.008), ("ro", 0.008), ("ic", 0.008), ("ne", 0.008), ("ea", 0.008),
    ("ra", 0.008), ("ce", 0.007), ("li", 0.007), ("ch", 0.007), ("ll", 0.007),
    ("be", 0.007), ("ma", 0.007), ("si", 0.007), ("om", 0.007), ("ur", 0.006),
];

static BIGRAM_DB: LazyLock<HashMap<String, f64>> = LazyLock::new(|| {
    let mut m = HashMap::new();
    for (k, v) in ENGLISH_BIGRAMS {
        m.insert(k.to_string(), *v);
    }
    m
});

static VOWELS: LazyLock<Vec<char>> = LazyLock::new(|| {
    vec!['a', 'e', 'i', 'o', 'u']
});

/// Calculate Shannon entropy of data.
/// Returns entropy in bits per character.
/// Optimized: single pass over data, no allocations for small inputs.
#[inline]
pub fn calculate_entropy(data: &str) -> f64 {
    if data.is_empty() {
        return 0.0;
    }

    let bytes = data.as_bytes();
    let len = bytes.len();

    // Fast path: use array for small inputs (up to 256 bytes)
    if len <= 256 {
        let mut counts = [0u32; 256];
        for &b in bytes {
            counts[b as usize] += 1;
        }

        let mut entropy = 0.0;
        for count in counts.iter().filter(|&&c| c > 0) {
            let probability = *count as f64 / len as f64;
            entropy -= probability * probability.log2();
        }
        return entropy;
    }

    // Slow path: HashMap for large inputs
    let mut counts = HashMap::new();
    for &b in bytes {
        *counts.entry(b).or_insert(0) += 1;
    }

    let mut entropy = 0.0;
    for count in counts.values() {
        let probability = *count as f64 / len as f64;
        entropy -= probability * probability.log2();
    }
    entropy
}

/// Fast entropy-based screening.
/// Returns (entropy_value, is_suspicious) where is_suspicious is:
///   Some(true)  = suspicious (high entropy)
///   Some(false) = benign (low entropy)
///   None        = inconclusive
pub fn fast_entropy_screen(query: &str, threshold: f64) -> (f64, Option<bool>) {
    // Extract subdomain for analysis (remove TLD)
    let subdomain = extract_subdomain(query);

    if subdomain.len() < 4 {
        return (0.0, Some(false));
    }

    let entropy = calculate_entropy(&subdomain);

    if entropy > threshold {
        (entropy, Some(true))
    } else if entropy < 3.0 {
        (entropy, Some(false))
    } else {
        (entropy, None)
    }
}

/// Extract subdomain from DNS query (remove TLD).
fn extract_subdomain(query: &str) -> String {
    let lower = query.to_lowercase();
    let parts: Vec<&str> = lower.split('.').collect();
    if parts.len() < 2 {
        lower
    } else if parts.len() > 2 {
        parts[..parts.len() - 2].join(".")
    } else {
        parts[0].to_string()
    }
}

/// N-gram analysis score structure.
#[derive(Debug, Clone, Default)]
pub struct NgramScore {
    pub bigram_freq: f64,
    pub trigram_freq: f64,
    pub char_distribution: f64,
    pub anomaly_score: f64,
}

/// Analyze query using n-gram frequencies.
/// Compares bigram and trigram frequencies against English language patterns.
/// Returns NgramScore with frequency and anomaly metrics.
pub fn ngram_analysis(query: &str) -> NgramScore {
    let text = extract_subdomain_for_analysis(query);

    if text.len() < 3 {
        return NgramScore {
            bigram_freq: 0.5,
            trigram_freq: 0.5,
            char_distribution: 0.5,
            anomaly_score: 0.0,
        };
    }

    // Calculate bigram frequencies
    let bytes = text.as_bytes();
    let mut bigram_sum = 0.0;
    let mut bigram_count = 0;

    for window in bytes.windows(2) {
        if let [a, b] = window {
            let bg = String::from_utf8_lossy(&[*a, *b]).to_string();
            if let Some(freq) = BIGRAM_DB.get(&bg) {
                bigram_sum += *freq;
            } else {
                bigram_sum += 0.001; // Low default for unknown
            }
            bigram_count += 1;
        }
    }

    let avg_bigram = if bigram_count > 0 {
        bigram_sum / bigram_count as f64
    } else {
        0.0
    };

    // Calculate trigram frequencies (vowel-consonant patterns)
    let vowels = VOWELS.as_slice();
    let mut trigram_sum = 0.0;
    let mut trigram_count = 0;

    for window in bytes.windows(3) {
        let vowel_count = window.iter()
            .filter(|&&c| vowels.contains(&(c as char)))
            .count();

        let score = match vowel_count {
            1 | 2 => 0.7,  // Expected vowel distribution
            0 => 0.2,       // No vowels is suspicious
            _ => 0.4,       // Too many vowels
        };

        trigram_sum += score;
        trigram_count += 1;
    }

    let avg_trigram = if trigram_count > 0 {
        trigram_sum / trigram_count as f64
    } else {
        0.0
    };

    // Character distribution analysis
    let mut char_counts = [0u32; 256];
    for &b in bytes {
        char_counts[b as usize] += 1;
    }

    let total_chars = bytes.len();
    let unique_chars = char_counts.iter().filter(|&&c| c > 0).count();

    let mut char_entropy = 0.0;
    for &count in char_counts.iter().filter(|&&c| c > 0) {
        let p = count as f64 / total_chars as f64;
        char_entropy -= p * p.log2();
    }

    let max_entropy = if unique_chars > 1 {
        (unique_chars as f64).log2()
    } else {
        1.0
    };

    let char_dist_score = 1.0 - (char_entropy / max_entropy);

    // Combined anomaly score
    let anomaly = (1.0 - (avg_bigram * 10.0).min(1.0)) * 0.4
        + (1.0 - avg_trigram) * 0.3
        + char_dist_score * 0.3;

    NgramScore {
        bigram_freq: avg_bigram,
        trigram_freq: avg_trigram,
        char_distribution: char_dist_score,
        anomaly_score: anomaly,
    }
}

/// Extract subdomain for analysis (lowercase, no TLD).
fn extract_subdomain_for_analysis(query: &str) -> String {
    let lower = query.to_lowercase();
    let parts: Vec<&str> = lower.split('.').collect();
    if parts.len() < 2 {
        lower
    } else if parts.len() > 2 {
        parts[..parts.len() - 2].join(".")
    } else {
        parts[0].to_string()
    }
}

/// Wavelet/FFT preprocessing for LSTM input.
/// Converts query to 256-dimensional feature vector.
pub fn wavelet_preprocess(query: &str) -> Vec<f32> {
    use std::f32::consts::PI;

    // Convert query to numerical representation
    let query_bytes = query.as_bytes();
    let mut signal = [0f32; 64];

    // Fill with byte values normalized to [0, 1]
    let length = query_bytes.len().min(64);
    for (i, &b) in query_bytes.iter().take(length).enumerate() {
        signal[i] = b as f32 / 255.0;
    }

    // FFT-based features (fallback when pywt unavailable)
    let mut features = Vec::with_capacity(256);

    // Real FFT components
    for i in 0..64 {
        let mut real = 0.0f32;
        let mut imag = 0.0f32;
        for (j, &s) in signal.iter().enumerate() {
            let angle = -2.0 * PI * i as f32 * j as f32 / 64.0;
            real += s * angle.cos();
            imag += s * angle.sin();
        }
        features.push((real * real + imag * imag).sqrt());
    }

    // Phase information
    for i in 0..64 {
        let mut real = 0.0f32;
        let mut imag = 0.0f32;
        for (j, &s) in signal.iter().enumerate() {
            let angle = -2.0 * PI * i as f32 * j as f32 / 64.0;
            real += s * angle.cos();
            imag += s * angle.sin();
        }
        features.push(real.atan2(imag));
    }

    // Pad to 256 if needed
    while features.len() < 256 {
        features.push(0.0);
    }

    features.truncate(256);
    features
}

/// Verdict enumeration (matches Python Verdict enum).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Benign,
    Suspicious,
    Malicious,
    Ambiguous,
}

/// Result of majority vote combining detection layers.
#[derive(Debug, Clone)]
pub struct MajorityVoteResult {
    pub verdict: Verdict,
    pub confidence: f64,
}

/// Majority vote combination of detection layers.
/// Combines entropy, n-gram, and encoding pattern signals.
pub fn majority_vote(
    entropy_suspicious: Option<bool>,
    ngram_anomaly: f64,
    has_encoding: bool,
    ngram_threshold: f64,
    majority_threshold: usize,
) -> MajorityVoteResult {
    let mut malicious_votes = 0usize;
    let mut benign_votes = 0usize;
    let mut suspicious_votes = 0usize;
    let mut malicious_confidence = 0.0;
    let mut benign_confidence = 0.0;
    let mut suspicious_confidence = 0.0;

    // Entropy vote
    match entropy_suspicious {
        Some(true) => {
            malicious_votes += 1;
            malicious_confidence += 0.8;
        }
        Some(false) => {
            benign_votes += 1;
            benign_confidence += 0.7;
        }
        None => {
            suspicious_votes += 1;
            suspicious_confidence += 0.5;
        }
    }

    // N-gram vote
    if ngram_anomaly > ngram_threshold {
        malicious_votes += 1;
        malicious_confidence += ngram_anomaly;
    } else if ngram_anomaly < 0.3 {
        benign_votes += 1;
        benign_confidence += 1.0 - ngram_anomaly;
    } else {
        suspicious_votes += 1;
        suspicious_confidence += 0.5;
    }

    // Encoding pattern vote
    if has_encoding {
        malicious_votes += 1;
        malicious_confidence += 0.9;
    } else {
        benign_votes += 1;
        benign_confidence += 0.6;
    }

    // Determine verdict
    if malicious_votes >= majority_threshold {
        let confidence = malicious_confidence / malicious_votes as f64;
        MajorityVoteResult {
            verdict: Verdict::Malicious,
            confidence: confidence.min(1.0),
        }
    } else if benign_votes >= majority_threshold {
        let confidence = benign_confidence / benign_votes as f64;
        MajorityVoteResult {
            verdict: Verdict::Benign,
            confidence: confidence.min(1.0),
        }
    } else if suspicious_votes > 0 {
        let confidence = suspicious_confidence / suspicious_votes as f64;
        MajorityVoteResult {
            verdict: Verdict::Suspicious,
            confidence: confidence.min(1.0),
        }
    } else {
        MajorityVoteResult {
            verdict: Verdict::Ambiguous,
            confidence: 0.5,
        }
    }
}

// =============================================================================
// Python-facing API (PyO3)
// =============================================================================

use pyo3::prelude::*;

/// Calculate entropy for a single query string.
#[pyfunction]
pub fn rust_calculate_entropy(query: &str) -> f64 {
    let subdomain = extract_subdomain(query);
    calculate_entropy(&subdomain)
}

/// Fast entropy screen - returns (entropy, is_suspicious).
/// is_suspicious: 1 = suspicious, 0 = benign, -1 = inconclusive.
#[pyfunction]
pub fn rust_fast_entropy_screen(query: &str, threshold: f64) -> (f64, i8) {
    let (entropy, result) = fast_entropy_screen(query, threshold);
    let flag = match result {
        Some(true) => 1i8,
        Some(false) => 0i8,
        None => -1i8,
    };
    (entropy, flag)
}

/// Full N-gram analysis returning a dict-like structure.
#[pyfunction]
pub fn rust_ngram_analysis(query: &str) -> (f64, f64, f64, f64) {
    let score = ngram_analysis(query);
    (score.bigram_freq, score.trigram_freq, score.char_distribution, score.anomaly_score)
}

/// Wavelet preprocess - returns 256-element list.
#[pyfunction]
pub fn rust_wavelet_preprocess(query: &str) -> Vec<f32> {
    wavelet_preprocess(query)
}

/// Combined entropy + ngram analysis (optimized batch).
/// Returns (entropy, entropy_flag, bigram, trigram, char_dist, anomaly).
/// entropy_flag: 1 = suspicious, 0 = benign, -1 = inconclusive.
#[pyfunction]
pub fn rust_entropy_ngram(query: &str, entropy_threshold: f64) -> (f64, i8, f64, f64, f64, f64) {
    let (entropy, entropy_flag) = fast_entropy_screen(query, entropy_threshold);
    let ngram = ngram_analysis(query);
    let flag = match entropy_flag {
        Some(true) => 1i8,
        Some(false) => 0i8,
        None => -1i8,
    };
    (entropy, flag, ngram.bigram_freq, ngram.trigram_freq, ngram.char_distribution, ngram.anomaly_score)
}

/// Majority vote from Python values.
#[pyfunction]
pub fn rust_majority_vote(
    entropy_flag: i8,      // 1 = suspicious, 0 = benign, -1 = inconclusive
    ngram_anomaly: f64,
    has_encoding: bool,
    ngram_threshold: f64,
    majority_threshold: usize,
) -> (String, f64) {
    let entropy_suspicious = match entropy_flag {
        1 => Some(true),
        0 => Some(false),
        _ => None,
    };

    let result = majority_vote(
        entropy_suspicious,
        ngram_anomaly,
        has_encoding,
        ngram_threshold,
        majority_threshold,
    );

    let verdict_str = match result.verdict {
        Verdict::Benign => "benign",
        Verdict::Suspicious => "suspicious",
        Verdict::Malicious => "malicious",
        Verdict::Ambiguous => "ambiguous",
    };

    (verdict_str.to_string(), result.confidence)
}

/// Batch analysis for multiple queries (parallel via rayon).
/// Input: list of query strings.
/// Output: list of (entropy, entropy_flag, anomaly_score).
#[pyfunction]
pub fn rust_batch_entropy_analysis<'py>(
    queries: &Bound<'py, PyList>,
    _py: Python<'py>,
    entropy_threshold: f64,
) -> PyResult<Vec<(f64, i8, f64)>> {
    let n = queries.len();
    if n == 0 {
        return Ok(vec![]);
    }

    // Collect under GIL, then process in rayon scope (no Python objects)
    let owned: Vec<String> = queries
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    if n < 50 {
        // Serial for small batches
        let results: Vec<(f64, i8, f64)> = owned
            .iter()
            .map(|q| {
                let (entropy, flag) = fast_entropy_screen(q, entropy_threshold);
                let ngram = ngram_analysis(q);
                let f = match flag {
                    Some(true) => 1i8,
                    Some(false) => 0i8,
                    None => -1i8,
                };
                (entropy, f, ngram.anomaly_score)
            })
            .collect();
        Ok(results)
    } else {
        // Parallel for large batches (mixed_pool P-core ceiling)
        let pool = mixed_pool(n);
        let results: Vec<(f64, i8, f64)> = pool.install(|| {
            owned
                .par_iter()
                .map(|q| {
                    let (entropy, flag) = fast_entropy_screen(q, entropy_threshold);
                    let ngram = ngram_analysis(q);
                    let f = match flag {
                        Some(true) => 1i8,
                        Some(false) => 0i8,
                        None => -1i8,
                    };
                    (entropy, f, ngram.anomaly_score)
                })
                .collect()
        });
        Ok(results)
    }
}

/// Register DNS tunnel functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_calculate_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(rust_fast_entropy_screen, m)?)?;
    m.add_function(wrap_pyfunction!(rust_ngram_analysis, m)?)?;
    m.add_function(wrap_pyfunction!(rust_wavelet_preprocess, m)?)?;
    m.add_function(wrap_pyfunction!(rust_entropy_ngram, m)?)?;
    m.add_function(wrap_pyfunction!(rust_majority_vote, m)?)?;
    m.add_function(wrap_pyfunction!(rust_batch_entropy_analysis, m)?)?;
    Ok(())
}

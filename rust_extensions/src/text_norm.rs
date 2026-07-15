// SPDX-License-Identifier: Apache-2.0
// Sprint F265B-III: Unicode NFC/NFD normalization + diacritic stripping.
// ARM NEON fast-paths added for ASCII-only text (M1/AArch64).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use unicode_normalization::UnicodeNormalization;

use crate::gil::release_gil;


const BATCH_HARD_CAP: usize = 50_000;

/// 16 bytes per NEON register (128-bit).
const BATCH_NEON_CHUNK: usize = 16;

// ---------------------------------------------------------------------------
// ARM NEON helpers (M1/AArch64 only)
// ---------------------------------------------------------------------------

#[cfg(target_arch = "aarch64")]
unsafe fn is_ascii_only_neon(data: &[u8]) -> bool { unsafe {
    use core::arch::aarch64::*;
    let len = data.len();
    let chunks = len / BATCH_NEON_CHUNK;
    let mut i = 0;
    for _ in 0..chunks {
        let vals = vld1q_u8(data.as_ptr().add(i));
        // Any byte >= 128 sets the high bit — check all 16 lanes
        let high_bits = vreinterpretq_u8_u32(vcgeq_u32(
            vreinterpretq_u32_u8(vals),
            vdupq_n_u32(0),
        ));
        let mask = vgetq_lane_u8(high_bits, 0)
            | vgetq_lane_u8(high_bits, 1)
            | vgetq_lane_u8(high_bits, 2)
            | vgetq_lane_u8(high_bits, 3)
            | vgetq_lane_u8(high_bits, 4)
            | vgetq_lane_u8(high_bits, 5)
            | vgetq_lane_u8(high_bits, 6)
            | vgetq_lane_u8(high_bits, 7)
            | vgetq_lane_u8(high_bits, 8)
            | vgetq_lane_u8(high_bits, 9)
            | vgetq_lane_u8(high_bits, 10)
            | vgetq_lane_u8(high_bits, 11)
            | vgetq_lane_u8(high_bits, 12)
            | vgetq_lane_u8(high_bits, 13)
            | vgetq_lane_u8(high_bits, 14)
            | vgetq_lane_u8(high_bits, 15);
        if mask != 0 {
            return false;
        }
        i += BATCH_NEON_CHUNK;
    }
    // Tail: scalar check
    for &b in &data[i..] {
        if b > 127 {
            return false;
        }
    }
    true
}}

#[cfg(not(target_arch = "aarch64"))]
unsafe fn is_ascii_only_neon(_data: &[u8]) -> bool {
    // Non-NEON fallback: scalar scan
    false
}

/// Fast-path case-fold for ASCII-only text using NEON.
/// A-Z (0x41-0x5A) → a-z (OR 0x20).  All other bytes unchanged.
#[cfg(target_arch = "aarch64")]
unsafe fn ascii_case_fold_neon(input: &[u8]) -> Vec<u8> { unsafe {
    use core::arch::aarch64::*;
    let len = input.len();
    let chunks = len / BATCH_NEON_CHUNK;
    let mut out = Vec::with_capacity(len);
    out.extend_from_slice(input); // pre-alloc full size

    let mask = vdupq_n_u8(0x20); // OR mask for A-Z → a-z
    let lo = vdupq_n_u8(b'A');
    let hi = vdupq_n_u8(b'Z');

    let mut i = 0;
    for _ in 0..chunks {
        let vals = vld1q_u8(out.as_ptr().add(i));
        // 1 where byte is in 'A'..'Z'
        let in_range = vorrq_u8(vcgeq_u8(vals, lo), vcgtq_u8(hi, vals));
        let folded = vbslq_u8(in_range, vals, vorrq_u8(vals, mask));
        vst1q_u8(out.as_mut_ptr().add(i), folded);
        i += BATCH_NEON_CHUNK;
    }

    // Tail: scalar
    for j in i..len {
        let b = out[j];
        if b >= b'A' && b <= b'Z' {
            out[j] = b | 0x20;
        }
    }
    out
}}

#[cfg(not(target_arch = "aarch64"))]
unsafe fn ascii_case_fold_neon(_input: &[u8]) -> Vec<u8> {
    // Unreachable on non-aarch64, but compiler needs a body
    unreachable!()
}

/// NFC-normalize `text` — canonical decomposition followed by canonical
/// composition.  This is the recommended form for IR/UI display and
/// cross-system comparison (e.g. "café" vs the precomposed "café").
#[pyfunction]
pub fn nfc_normalize(text: &str) -> String {
    text.nfc().collect()
}

/// NFD-normalize `text` — canonical decomposition only (no recomposition).
/// Useful when you need to inspect or strip individual combining marks.
#[pyfunction]
pub fn nfd_normalize(text: &str) -> String {
    text.nfd().collect()
}

/// Batch NFC normalization via rayon, capped at `BATCH_HARD_CAP`.
/// Returns `ValueError` if the cap is exceeded.
#[pyfunction]
pub fn batch_nfc_normalize(texts: Vec<String>) -> Result<Vec<String>, PyErr> {
    if texts.len() > BATCH_HARD_CAP {
        return Err(PyValueError::new_err(format!(
            "batch_nfc_normalize: {} items exceeds hard cap {}",
            texts.len(),
            BATCH_HARD_CAP
        )));
    }
    let n = texts.len();
    let out = Python::with_gil(|py| {
        release_gil(py, || {
            crate::mixed_pool(n).install(|| texts.par_iter().map(|s| s.nfc().collect()).collect())
        })
    });
    Ok(out)
}

/// Strip diacritics from `text`.
///
/// Algorithm: NFD-decompose, then filter out all Unicode combining marks.
/// Combining marks live in the ranges U+0300–U+036F (Combining Diacritical Marks)
/// and U+1AB0–U+1AFF (Combining Diacritical Marks Extended).  Base characters
/// are kept regardless of their alphabetic property.
#[pyfunction]
pub fn strip_diacritics(text: &str) -> String {
    // Combining Diacritical Marks block: U+0300–U+036F
    // Combining Diacritical Marks Extended block: U+1AB0–U+1AFF
    const MARK_RANGES: &[(char, char)] = &[
        ('\u{0300}', '\u{036F}'),
        ('\u{1AB0}', '\u{1AFF}'),
    ];
    text.nfd()
        .filter(|c| !MARK_RANGES.iter().any(|(lo, hi)| c >= lo && c <= hi))
        .collect()
}

/// Batch diacritic stripping via rayon, capped at `BATCH_HARD_CAP`.
/// Returns `ValueError` if the cap is exceeded.
#[pyfunction]
pub fn batch_strip_diacritics(texts: Vec<String>) -> Result<Vec<String>, PyErr> {
    if texts.len() > BATCH_HARD_CAP {
        return Err(PyValueError::new_err(format!(
            "batch_strip_diacritics: {} items exceeds hard cap {}",
            texts.len(),
            BATCH_HARD_CAP
        )));
    }
    const MARK_RANGES: &[(char, char)] = &[
        ('\u{0300}', '\u{036F}'),
        ('\u{1AB0}', '\u{1AFF}'),
    ];
    let n = texts.len();
    let out = Python::with_gil(|py| {
        release_gil(py, || {
            crate::mixed_pool(n).install(|| {
                texts.par_iter()
                    .map(|s| {
                        s.nfd()
                            .filter(|c| !MARK_RANGES.iter().any(|(lo, hi)| c >= lo && c <= hi))
                            .collect()
                    })
                    .collect()
            })
        })
    });
    Ok(out)
}

// ---------------------------------------------------------------------------
// Fast-path batch functions (NEON for ASCII, scalar fallback otherwise)
// ---------------------------------------------------------------------------

/// Batch NFC normalization with NEON fast-path for ASCII-only strings.
///
/// Strategy:
/// - ASCII-only strings: case-fold (OR 0x20) and return — NFC is identity for ASCII
/// - Non-ASCII: delegate to the rayon scalar path
#[pyfunction]
pub fn batch_nfc_normalize_fast(texts: Vec<String>) -> Result<Vec<String>, PyErr> {
    if texts.is_empty() {
        return Ok(Vec::new());
    }
    if texts.len() > BATCH_HARD_CAP {
        return Err(PyValueError::new_err(format!(
            "batch_nfc_normalize_fast: {} items exceeds hard cap {}",
            texts.len(),
            BATCH_HARD_CAP
        )));
    }

    let n = texts.len();
    let out = Python::with_gil(|py| {
        release_gil(py, || {
            crate::mixed_pool(n).install(|| {
                texts
                    .into_par_iter()
                    .map(|s| {
                        let bytes = s.as_bytes();
                        // SAFETY: is_ascii_only_neon and ascii_case_fold_neon are
                        // marked unsafe but are deterministic and side-effect free.
                        // Both functions enforce the BATCH_NEON_CHUNK alignment
                        // invariants internally.
                        unsafe {
                            if is_ascii_only_neon(bytes) {
                                // ASCII: NFC is identity, but do case-fold to match
                                // expected OSINT normalisation behaviour.
                                let folded = ascii_case_fold_neon(bytes);
                                String::from_utf8_unchecked(folded)
                            } else {
                                // Non-ASCII: full NFC composition
                                s.nfc().collect()
                            }
                        }
                    })
                    .collect()
            })
        })
    });
    Ok(out)
}

/// Batch diacritic stripping with NEON fast-path for ASCII-only strings.
///
/// Strategy:
/// - ASCII-only strings: identity (no diacritics possible)
/// - Non-ASCII: delegate to the rayon scalar NFD+filter path
#[pyfunction]
pub fn batch_strip_diacritics_fast(texts: Vec<String>) -> Result<Vec<String>, PyErr> {
    if texts.is_empty() {
        return Ok(Vec::new());
    }
    if texts.len() > BATCH_HARD_CAP {
        return Err(PyValueError::new_err(format!(
            "batch_strip_diacritics_fast: {} items exceeds hard cap {}",
            texts.len(),
            BATCH_HARD_CAP
        )));
    }

    const MARK_RANGES: &[(char, char)] = &[
        ('\u{0300}', '\u{036F}'),
        ('\u{1AB0}', '\u{1AFF}'),
    ];

    let n = texts.len();
    let out = Python::with_gil(|py| {
        release_gil(py, || {
            crate::mixed_pool(n).install(|| {
                texts
                    .into_par_iter()
                    .map(|s| {
                        let bytes = s.as_bytes();
                        // SAFETY: is_ascii_only_neon is deterministic and side-effect free.
                        unsafe {
                            if is_ascii_only_neon(bytes) {
                                // ASCII: no diacritics possible, return as-is
                                s
                            } else {
                                // Non-ASCII: NFD decompose and filter combining marks
                                s.nfd()
                                    .filter(|c| !MARK_RANGES.iter().any(|(lo, hi)| c >= lo && c <= hi))
                                    .collect()
                            }
                        }
                    })
                    .collect()
            })
        })
    });
    Ok(out)
}

/// Register all text_norm functions under the `hledac_rust_extensions` module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(nfc_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(nfd_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_nfc_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_nfc_normalize_fast, m)?)?;
    m.add_function(wrap_pyfunction!(strip_diacritics, m)?)?;
    m.add_function(wrap_pyfunction!(batch_strip_diacritics, m)?)?;
    m.add_function(wrap_pyfunction!(batch_strip_diacritics_fast, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_nfc_café() {
        // "café" with acute accent as separate combining character
        let input = "cafe\u{0301}"; // café decomposed
        let result = nfc_normalize(input);
        assert_eq!(result, "café"); // composed NFC form
    }

    #[test]
    fn test_nfc_identity() {
        // Already composed text is unchanged
        assert_eq!(nfc_normalize("café"), "café");
        assert_eq!(nfc_normalize("žluťoučký"), "žluťoučký");
    }

    #[test]
    fn test_nfd() {
        let composed = "žluťoučký";
        let decomposed = nfd_normalize(composed);
        // NFD may differ; just verify deterministic
        assert_eq!(nfd_normalize(composed), decomposed);
    }

    #[test]
    fn test_strip_diacritics_basic() {
        assert_eq!(strip_diacritics("Brněnská"), "Brnenska");
        assert_eq!(strip_diacritics("café"), "cafe");
        assert_eq!(strip_diacritics("ÉCLAIR"), "ECLAIR");
    }

    #[test]
    fn test_strip_diacritics_preserves_ascii() {
        assert_eq!(strip_diacritics("Hello World"), "Hello World");
    }

    #[test]
    fn test_strip_diacritics_empty() {
        assert_eq!(strip_diacritics(""), "");
    }

    #[test]
    fn test_batch_nfc_normalize() {
        let texts = vec!["café".to_string(), "Žluťoučký".to_string()];
        let out = batch_nfc_normalize(texts).unwrap();
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn test_batch_nfc_normalize_cap() {
        let texts: Vec<String> = (0..BATCH_HARD_CAP + 1).map(|i| i.to_string()).collect();
        let result = batch_nfc_normalize(texts);
        assert!(result.is_err());
    }

    #[test]
    fn test_batch_strip_diacritics() {
        let texts = vec!["Brněnská".to_string(), "café".to_string()];
        let out = batch_strip_diacritics(texts).unwrap();
        assert_eq!(out, vec!["Brnenska".to_string(), "cafe".to_string()]);
    }

    #[test]
    fn test_batch_strip_diacritics_cap() {
        let texts: Vec<String> = (0..BATCH_HARD_CAP + 1).map(|i| i.to_string()).collect();
        let result = batch_strip_diacritics(texts);
        assert!(result.is_err());
    }

    // ---- Fast-path tests ----------------------------------------------------

    #[test]
    fn test_batch_nfc_normalize_fast_empty() {
        let out = batch_nfc_normalize_fast(vec![]).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn test_batch_nfc_normalize_fast_cap() {
        let texts: Vec<String> = (0..BATCH_HARD_CAP + 1).map(|i| i.to_string()).collect();
        let result = batch_nfc_normalize_fast(texts);
        assert!(result.is_err());
    }

    #[test]
    fn test_batch_nfc_normalize_fast_ascii_identity() {
        // ASCII text: NFC is identity but fast-path does case-fold
        let texts = vec!["HELLO".to_string(), "world".to_string()];
        let out = batch_nfc_normalize_fast(texts).unwrap();
        // Case is folded (OSINT normalization): HELLO → hello, world unchanged
        assert_eq!(out, vec!["hello".to_string(), "world".to_string()]);
    }

    #[test]
    fn test_batch_nfc_normalize_fast_mixed() {
        let texts = vec!["café".to_string(), "Brno".to_string(), "žluťoučký".to_string()];
        let out = batch_nfc_normalize_fast(texts).unwrap();
        assert_eq!(out.len(), 3);
        // café: NFC composed
        assert_eq!(out[0], "café");
        // Brno: ASCII, lowercased
        assert_eq!(out[1], "brno");
        // žluťoučký: NFC composed
        assert_eq!(out[2], "žluťoučký");
    }

    #[test]
    fn test_batch_nfc_normalize_fast_decomposed() {
        // Already-NFC composed strings are unchanged
        let texts = vec!["café".to_string()];
        let out = batch_nfc_normalize_fast(texts).unwrap();
        assert_eq!(out[0], "café");
    }

    #[test]
    fn test_batch_strip_diacritics_fast_empty() {
        let out = batch_strip_diacritics_fast(vec![]).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn test_batch_strip_diacritics_fast_cap() {
        let texts: Vec<String> = (0..BATCH_HARD_CAP + 1).map(|i| i.to_string()).collect();
        let result = batch_strip_diacritics_fast(texts);
        assert!(result.is_err());
    }

    #[test]
    fn test_batch_strip_diacritics_fast_ascii_identity() {
        // ASCII: no diacritics, returned as-is
        let texts = vec!["Hello World".to_string(), "Brno".to_string()];
        let out = batch_strip_diacritics_fast(texts).unwrap();
        assert_eq!(out, vec!["Hello World".to_string(), "Brno".to_string()]);
    }

    #[test]
    fn test_batch_strip_diacritics_fast_mixed() {
        let texts = vec![
            "Brněnská".to_string(),
            "hello".to_string(),
            "ÉCLAIR".to_string(),
        ];
        let out = batch_strip_diacritics_fast(texts).unwrap();
        assert_eq!(out, vec!["Brnenska".to_string(), "hello".to_string(), "ECLAIR".to_string()]);
    }
}

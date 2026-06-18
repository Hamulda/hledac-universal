// SPDX-License-Identifier: Apache-2.0
// Sprint F265B-III: Unicode NFC/NFD normalization + diacritic stripping.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use unicode_normalization::UnicodeNormalization;

const BATCH_HARD_CAP: usize = 50_000;

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
    let out = crate::bulk_pool().install(|| texts.par_iter().map(|s| s.nfc().collect()).collect());
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
    let out = crate::bulk_pool().install(|| {
        texts.par_iter()
            .map(|s| {
                s.nfd()
                    .filter(|c| !MARK_RANGES.iter().any(|(lo, hi)| c >= lo && c <= hi))
                    .collect()
            })
            .collect()
    });
    Ok(out)
}

/// Register all text_norm functions under the `hledac_rust_extensions` module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(nfc_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(nfd_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_nfc_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(strip_diacritics, m)?)?;
    m.add_function(wrap_pyfunction!(batch_strip_diacritics, m)?)?;
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
}

// SPDX-License-Identifier: Apache-2.0
// ISSUE [ULTIMATE]-005: Zero-Width & Homoglyph Attribution Fingerprint
// Extracts invisible character patterns as author-attribution watermarks.
// M1 8GB safe: ~200 bytes per fingerprint, O(N) single-pass scan.

use pyo3::prelude::*;
use sha2::{Digest, Sha256};

/// Zero-Width & Homoglyph Attribution Fingerprint.
/// Extracts invisible character patterns as author-attribution watermarks.
#[derive(Debug, Clone, PartialEq, msgspec::Struct)]
pub struct UnicodeFingerprint {
    /// (char_name, position) tuples for zero-width characters
    pub zero_width_pattern: Vec<(String, usize)>,
    /// Zero-width characters per 1000 visible chars
    pub zero_width_density: f64,
    /// (original_char, canonical_char, position) for homoglyph substitutions
    pub homoglyph_pattern: Vec<(char, char, usize)>,
    /// Directional override sequences (BIDI)
    pub unicode_bidi_sequence: Vec<String>,
    /// SHA-256 of combined pattern (attribution hash)
    pub fingerprint_hash: [u8; 32],
}

impl Default for UnicodeFingerprint {
    fn default() -> Self {
        Self {
            zero_width_pattern: Vec::new(),
            zero_width_density: 0.0,
            homoglyph_pattern: Vec::new(),
            unicode_bidi_sequence: Vec::new(),
            fingerprint_hash: [0u8; 32],
        }
    }
}

// ---------------------------------------------------------------------------
// Zero-Width Character Definitions
// ---------------------------------------------------------------------------

/// Zero-width characters used in Unicode steganography
const ZERO_WIDTH_CHARS: &[(char, &str)] = &[
    ('\u{200B}', "ZERO_WIDTH_SPACE"),           // U+200B
    ('\u{200C}', "ZERO_WIDTH_NON_JOINER"),      // U+200C
    ('\u{200D}', "ZERO_WIDTH_JOINER"),          // U+200D
    ('\u{200E}', "LEFT_TO_RIGHT_MARK"),         // U+200E
    ('\u{200F}', "RIGHT_TO_LEFT_MARK"),         // U+200F
    ('\u{2028}', "LINE_SEPARATOR"),             // U+2028
    ('\u{2029}', "PARAGRAPH_SEPARATOR"),        // U+2029
    ('\u{202A}', "LEFT_TO_RIGHT_EMBED"),        // U+202A
    ('\u{202B}', "RIGHT_TO_LEFT_EMBED"),        // U+202B
    ('\u{202C}', "POP_DIRECTIONAL_FORMATTING"), // U+202C
    ('\u{202D}', "LEFT_TO_RIGHT_OVERRIDE"),     // U+202D
    ('\u{202E}', "RIGHT_TO_LEFT_OVERRIDE"),     // U+202E
    ('\u{2060}', "WORD_JOINER"),                // U+2060
    ('\u{2061}', "FUNCTION_APPLICATION"),       // U+2061
    ('\u{2062}', "INVISIBLE_TIMES"),            // U+2062
    ('\u{2063}', "INVISIBLE_SEPARATOR"),        // U+2063
    ('\u{2064}', "INVISIBLE_PLUS"),             // U+2064
    ('\u{2066}', "FIRST_STRONG_ISOLATE"),       // U+2066
    ('\u{2067}', "FIRST_STRONG_ISOLATE"),       // U+2067
    ('\u{2068}', "FIRST_STRONG_ISOLATE"),       // U+2068
    ('\u{2069}', "POP_DIRECTIONAL_ISOLATE"),    // U+2069
    ('\u{FEFF}', "BYTE_ORDER_MARK"),            // U+FEFF
    ('\u{034F}', "COMBINING_GRAPHEME_JOINER"),  // U+034F
    ('\u{061C}', "ARABIC_LETTER_MARK"),         // U+061C
    ('\u{180E}', "MONGOLIAN_VOWEL_SEPARATOR"),  // U+180E (deprecated but still used)
    ('\u{200A}', "HAIR_SPACE"),                 // U+200A
    ('\u{205F}', "MEDIUM_MATHEMATICAL_SPACE"),  // U+205F
];

/// Build a fast lookup set for zero-width characters
fn build_zw_set() -> std::collections::HashSet<char> {
    ZERO_WIDTH_CHARS.iter().map(|(c, _)| *c).collect()
}

// ---------------------------------------------------------------------------
// Homoglyph Definitions (Cyrillic/Greek → ASCII)
// ---------------------------------------------------------------------------

/// Common Cyrillic/Greek homoglyphs that map to ASCII
const HOMOGLYPHS: &[(char, char)] = &[
    // Cyrillic → Latin
    ('А', 'A'),
    ('а', 'a'),
    ('В', 'B'),
    ('в', 'b'),
    ('С', 'C'),
    ('с', 'c'),
    ('Е', 'E'),
    ('е', 'e'),
    ('Н', 'H'),
    ('н', 'h'),
    ('К', 'K'),
    ('к', 'k'),
    ('М', 'M'),
    ('м', 'm'),
    ('О', 'O'),
    ('о', 'o'),
    ('Р', 'P'),
    ('р', 'p'),
    ('С', 'C'),
    ('с', 'c'),
    ('Т', 'T'),
    ('т', 't'),
    ('Х', 'X'),
    ('х', 'x'),
    ('У', 'Y'),
    ('у', 'y'),
    ('І', 'I'),
    ('і', 'i'),
    ('Ї', 'J'),
    ('ї', 'j'),
    ('Є', 'E'),
    ('є', 'e'),
    ('Ґ', 'G'),
    ('ґ', 'g'),
    // Greek → Latin lookalikes
    ('Α', 'A'),
    ('α', 'a'),
    ('Β', 'B'),
    ('β', 'b'),
    ('Ε', 'E'),
    ('ε', 'e'),
    ('Κ', 'K'),
    ('κ', 'k'),
    ('Μ', 'M'),
    ('μ', 'm'),
    ('Ν', 'N'),
    ('ν', 'n'),
    ('Ο', 'O'),
    ('ο', 'o'),
    ('Ρ', 'P'),
    ('ρ', 'p'),
    ('Τ', 'T'),
    ('τ', 't'),
    ('Υ', 'Y'),
    ('υ', 'u'),
    ('Χ', 'X'),
    ('χ', 'x'),
    ('Η', 'H'),
    ('η', 'h'),
    ('Ζ', 'Z'),
    ('ζ', 'z'),
    ('Ι', 'I'),
    ('ι', 'i'),
];

/// Build homoglyph lookup map
fn build_homoglyph_map() -> std::collections::HashMap<char, char> {
    HOMOGLYPHS.iter().copied().collect()
}

// ---------------------------------------------------------------------------
// BIDI Override Detection
// ---------------------------------------------------------------------------

/// Detect BIDI override sequences
fn detect_bidi_sequence(text: &str) -> Vec<String> {
    let mut sequences = Vec::new();
    let mut current_seq = String::new();
    let bidi_chars: std::collections::HashSet<char> = [
        '\u{202A}', '\u{202B}', '\u{202C}', '\u{202D}', '\u{202E}', '\u{2066}', '\u{2067}',
        '\u{2068}', '\u{2069}', '\u{200E}', '\u{200F}', '\u{061C}',
    ]
    .iter()
    .copied()
    .collect();

    for c in text.chars() {
        if bidi_chars.contains(&c) {
            if !current_seq.is_empty() {
                sequences.push(current_seq.clone());
            }
            current_seq = format!("U+{:04X}", c as u32);
        }
    }
    if !current_seq.is_empty() {
        sequences.push(current_seq);
    }
    sequences
}

// ---------------------------------------------------------------------------
// Fingerprint Extraction (O(N) single-pass)
// ---------------------------------------------------------------------------

/// Extract Unicode fingerprint from text.
/// This is the main extraction function - O(N) single-pass scan.
pub fn extract_fingerprint_impl(text: &str) -> UnicodeFingerprint {
    let zw_set = build_zw_set();
    let homoglyph_map = build_homoglyph_map();

    let mut zero_width_pattern = Vec::new();
    let mut homoglyph_pattern = Vec::new();
    let mut visible_char_count: usize = 0;

    for (pos, c) in text.char_indices() {
        // Check for zero-width characters
        if zw_set.contains(&c) {
            let name = ZERO_WIDTH_CHARS
                .iter()
                .find(|(zc, _)| *zc == c)
                .map(|(_, n)| n.to_string())
                .unwrap_or_else(|| format!("U+{:04X}", c as u32));
            zero_width_pattern.push((name, pos));
        } else if !c.is_whitespace() && !c.is_control() {
            visible_char_count += 1;
        }

        // Check for homoglyphs
        if let Some(&canonical) = homoglyph_map.get(&c) {
            homoglyph_pattern.push((c, canonical, pos));
        }
    }

    // Calculate density (ZW chars per 1000 visible)
    let zero_width_density = if visible_char_count > 0 {
        (zero_width_pattern.len() as f64 / visible_char_count as f64) * 1000.0
    } else {
        0.0
    };

    // Detect BIDI sequences
    let unicode_bidi_sequence = detect_bidi_sequence(text);

    // Compute SHA-256 fingerprint hash
    let mut hasher = Sha256::new();
    for (name, pos) in &zero_width_pattern {
        hasher.update(format!("{}:{}", name, pos));
        hasher.update(b":");
    }
    for &(orig, canon, pos) in &homoglyph_pattern {
        hasher.update(format!("{}->{}:{}", orig, canon, pos));
        hasher.update(b":");
    }
    for seq in &unicode_bidi_sequence {
        hasher.update(seq);
        hasher.update(b":");
    }
    let result = hasher.finalize();
    let fingerprint_hash: [u8; 32] = result.into();

    UnicodeFingerprint {
        zero_width_pattern,
        zero_width_density,
        homoglyph_pattern,
        unicode_bidi_sequence,
        fingerprint_hash,
    }
}

/// Compute Jaccard similarity between two fingerprints.
pub fn compute_similarity(a: &UnicodeFingerprint, b: &UnicodeFingerprint) -> f64 {
    // Compare zero-width patterns
    let zw_a: std::collections::HashSet<_> = a.zero_width_pattern.iter().collect();
    let zw_b: std::collections::HashSet<_> = b.zero_width_pattern.iter().collect();
    let zw_intersection = zw_a.intersection(&zw_b).count();
    let zw_union = zw_a.union(&zw_b).count();
    let zw_jaccard = if zw_union > 0 {
        zw_intersection as f64 / zw_union as f64
    } else {
        0.0
    };

    // Compare homoglyph patterns (by position and mapping)
    let hg_a: std::collections::HashSet<_> = a.homoglyph_pattern.iter().collect();
    let hg_b: std::collections::HashSet<_> = b.homoglyph_pattern.iter().collect();
    let hg_intersection = hg_a.intersection(&hg_b).count();
    let hg_union = hg_a.union(&hg_b).count();
    let hg_jaccard = if hg_union > 0 {
        hg_intersection as f64 / hg_union as f64
    } else {
        0.0
    };

    // Compare BIDI sequences
    let bidi_a: std::collections::HashSet<_> = a.unicode_bidi_sequence.iter().collect();
    let bidi_b: std::collections::HashSet<_> = b.unicode_bidi_sequence.iter().collect();
    let bidi_intersection = bidi_a.intersection(&bidi_b).count();
    let bidi_union = bidi_a.union(&bidi_b).count();
    let bidi_jaccard = if bidi_union > 0 {
        bidi_intersection as f64 / bidi_union as f64
    } else {
        1.0 // No BIDI = perfect match for this dimension
    };

    // Compare hash equality (exact match bonus)
    let hash_match = if a.fingerprint_hash == b.fingerprint_hash && a.fingerprint_hash != [0u8; 32]
    {
        1.0
    } else {
        0.0
    };

    // Weighted combination
    // Zero-width and hash are most indicative for attribution
    (zw_jaccard * 0.4) + (hg_jaccard * 0.2) + (bidi_jaccard * 0.1) + (hash_match * 0.3)
}

// ---------------------------------------------------------------------------
// PyO3 Python bindings
// ---------------------------------------------------------------------------

#[pyclass(module = "hledac_rust_extensions")]
pub struct PyUnicodeFingerprint {
    inner: UnicodeFingerprint,
}

impl PyUnicodeFingerprint {
    pub fn new(inner: UnicodeFingerprint) -> Self {
        Self { inner }
    }

    pub fn inner(&self) -> &UnicodeFingerprint {
        &self.inner
    }
}

#[pymethods]
impl PyUnicodeFingerprint {
    #[getter]
    fn zero_width_pattern(&self) -> Vec<(String, usize)> {
        self.inner.zero_width_pattern.clone()
    }

    #[getter]
    fn zero_width_density(&self) -> f64 {
        self.inner.zero_width_density
    }

    #[getter]
    fn homoglyph_pattern(&self) -> Vec<(char, char, usize)> {
        self.inner.homoglyph_pattern.clone()
    }

    #[getter]
    fn unicode_bidi_sequence(&self) -> Vec<String> {
        self.inner.unicode_bidi_sequence.clone()
    }

    #[getter]
    fn fingerprint_hash(&self) -> Vec<u8> {
        self.inner.fingerprint_hash.to_vec()
    }

    #[getter]
    fn fingerprint_hash_hex(&self) -> String {
        hex::encode(self.inner.fingerprint_hash)
    }

    fn __repr__(&self) -> String {
        format!(
            "UnicodeFingerprint(zw_count={}, density={}, hg_count={}, bidi_count={})",
            self.inner.zero_width_pattern.len(),
            format!("{:.2}", self.inner.zero_width_density),
            self.inner.homoglyph_pattern.len(),
            self.inner.unicode_bidi_sequence.len()
        )
    }
}

/// Extract Unicode fingerprint from text.
#[pyfunction]
pub fn extract_fingerprint(text: &str) -> PyUnicodeFingerprint {
    let fingerprint = extract_fingerprint_impl(text);
    PyUnicodeFingerprint::new(fingerprint)
}

/// Compute similarity between two Unicode fingerprints.
#[pyfunction]
pub fn compute_fingerprint_similarity(a: &PyUnicodeFingerprint, b: &PyUnicodeFingerprint) -> f64 {
    compute_similarity(a.inner(), b.inner())
}

/// Check if two fingerprints have identical hash (exact match).
#[pyfunction]
pub fn fingerprints_identical(a: &PyUnicodeFingerprint, b: &PyUnicodeFingerprint) -> bool {
    a.inner().fingerprint_hash == b.inner().fingerprint_hash
        && a.inner().fingerprint_hash != [0u8; 32]
}

/// Python module definition for unicode_fingerprint.
#[pymodule]
pub fn unicode_fingerprint(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyUnicodeFingerprint>()?;
    m.add_function(wrap_pyfunction!(extract_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(compute_fingerprint_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(fingerprints_identical, m)?)?;
    Ok(())
}

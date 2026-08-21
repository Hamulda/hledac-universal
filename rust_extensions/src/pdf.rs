//! PDF text extraction and IOC extraction using lopdf.
//!
//! # Architecture
//!   1. Pure Rust PDF parsing via lopdf (no external dependencies)
//!   2. Text extraction from all pages via document.extract_text()
//!   3. Reuses existing `extract_iocs_from_text()` for IOC extraction
//!   4. M1 8GB: bounded by max PDF size (100MB) and page count (10k pages)
//!
//! # Feature Gate
//!   - `pdf = ["dep:lopdf"]` — enables this module
//!   - Without feature: module not compiled, Python falls back to PyMuPDF
//!
//! # API
//!   - `pdf.extract_text(path) -> String` — extract plain text from PDF
//!   - `pdf.extract_iocs(path) -> Vec<(String, String)>` — extract IOCs from PDF
//!   - `pdf.extract_text_from_bytes(bytes) -> String` — extract from memory
//!   - `pdf.extract_text_and_iocs_from_bytes(bytes) -> (String, Vec)` — single-pass extract + IOC

use pyo3::prelude::*;
use std::path::Path;

/// Maximum PDF file size in bytes (100 MB) — prevents OOM on M1 8GB
const MAX_PDF_SIZE: usize = 100 * 1024 * 1024;

/// Maximum pages to extract (10k pages) — prevents runaway extraction
const MAX_PAGES: u32 = 10_000;

/// Extract plain text from a PDF file.
///
/// # Arguments
/// * `path` - Path to PDF file on disk
///
/// # Returns
/// * `String` - Extracted text from all pages, concatenated with newlines
///
/// # Errors
/// * `PyIOError` if file cannot be opened or parsed
/// * `PyValueError` if file exceeds size limits
#[pyfunction]
pub fn extract_text(path: &str) -> PyResult<String> {
    let path = Path::new(path);

    let metadata = std::fs::metadata(path).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read file metadata: {e}"))
    })?;

    if metadata.len() > MAX_PDF_SIZE as u64 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF file too large: {} bytes (max: {} bytes)",
            metadata.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load(path)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF: {e}")))?;

    extract_text_from_doc(&doc)
}

/// Extract text from PDF bytes in memory.
///
/// # Arguments
/// * `data` - PDF file content as bytes
///
/// # Returns
/// * `String` - Extracted text from all pages
///
/// # Errors
/// * `PyIOError` if bytes cannot be parsed as PDF
/// * `PyValueError` if data exceeds size limits
#[pyfunction]
pub fn extract_text_from_bytes(data: &[u8]) -> PyResult<String> {
    if data.len() > MAX_PDF_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF data too large: {} bytes (max: {} bytes)",
            data.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load_mem(data).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF bytes: {e}"))
    })?;

    extract_text_from_doc(&doc)
}

/// Internal: extract text from a loaded lopdf Document.
fn extract_text_from_doc(doc: &lopdf::Document) -> PyResult<String> {
    let page_count = doc.get_pages().len() as u32;

    if page_count > MAX_PAGES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF has too many pages: {} (max: {})",
            page_count, MAX_PAGES
        )));
    }

    let mut text = String::new();
    let page_nums: Vec<u32> = (1..=page_count));

    match doc.extract_text(&page_nums) {
        Ok(page_text) => {
            text.push_str(&page_text);
        }
        Err(e) => {
            // Partial failure: try page by page
            for page_num in page_nums {
                if let Ok(page_text) = doc.extract_text(&[page_num]) {
                    if !page_text.is_empty() {
                        text.push_str(&page_text);
                        text.push('\n');
                    }
                }
            }
            if text.is_empty() {
                return Err(pyo3::exceptions::PyIOError::new_err(format!(
                    "Failed to extract text from PDF: {e}"
                )));
            }
        }
    }

    Ok(text)
}

/// Extract text AND IOCs from PDF bytes in a single pass.
///
/// Avoids parsing the PDF twice (extract_text + extract_iocs would parse 2×).
///
/// # Arguments
/// * `data` - PDF file content as bytes
///
/// # Returns
/// * `(String, Vec<(String, String)>)` - (extracted_text, ioc_list)
#[pyfunction]
pub fn extract_text_and_iocs_from_bytes(data: &[u8]) -> PyResult<(String, Vec<(String, String)>)> {
    let text = extract_text_from_bytes(data)?;
    let iocs = crate::ioc_extract_fast::extract_iocs_from_text(&text);
    Ok((text, iocs))
}

/// Extract text AND IOCs from a PDF file in a single pass.
///
/// # Arguments
/// * `path` - Path to PDF file on disk
///
/// # Returns
/// * `(String, Vec<(String, String)>)` - (extracted_text, ioc_list)
#[pyfunction]
pub fn extract_text_and_iocs(path: &str) -> PyResult<(String, Vec<(String, String)>)> {
    let text = extract_text(path)?;
    let iocs = crate::ioc_extract_fast::extract_iocs_from_text(&text);
    Ok((text, iocs))
}

/// Extract IOCs from a PDF file.
///
/// # Arguments
/// * `path` - Path to PDF file on disk
///
/// # Returns
/// * `Vec<(String, String)>` - List of (ioc_value, ioc_type) tuples
#[pyfunction]
pub fn extract_iocs(path: &str) -> PyResult<Vec<(String, String)>> {
    let text = extract_text(path)?;
    Ok(crate::ioc_extract_fast::extract_iocs_from_text(&text))
}

/// Extract IOCs from PDF bytes in memory.
///
/// # Arguments
/// * `data` - PDF file content as bytes
///
/// # Returns
/// * `Vec<(String, String)>` - List of (ioc_value, ioc_type) tuples
#[pyfunction]
pub fn extract_iocs_from_bytes(data: &[u8]) -> PyResult<Vec<(String, String)>> {
    let text = extract_text_from_bytes(data)?;
    Ok(crate::ioc_extract_fast::extract_iocs_from_text(&text))
}

/// PDF metadata returned by extract_metadata functions.
#[pyclass]
#[derive(Debug, Default)]
pub struct PdfMetadata {
    #[pyo3(get, set)]
    pub title: Option<String>,
    #[pyo3(get, set)]
    pub author: Option<String>,
    #[pyo3(get, set)]
    pub subject: Option<String>,
    #[pyo3(get, set)]
    pub creator: Option<String>,
    #[pyo3(get, set)]
    pub producer: Option<String>,
    #[pyo3(get, set)]
    pub creation_date: Option<String>,
    #[pyo3(get, set)]
    pub modification_date: Option<String>,
    #[pyo3(get, set)]
    pub num_pages: u32,
    #[pyo3(get, set)]
    pub pdf_version: Option<String>,
    #[pyo3(get, set)]
    pub is_encrypted: bool,
}

/// Parse PDF date string (D:YYYYMMDDHHmmSSOH'm'm') to ISO-8601.
fn parse_pdf_date(date_str: &str) -> Option<String> {
    let s = date_str.strip_prefix("D:")?;
    if s.len() < 14 {
        return None;
    }
    let year = &s[0..4];
    let month = &s[4..6];
    let day = &s[6..8];
    let hour = &s[8..10];
    let minute = &s[10..12];
    let second = &s[12..14];
    Some(format!(
        "{}-{}-{}T{}:{}:{}",
        year, month, day, hour, minute, second
    ))
}

/// Internal: get a string value from a lopdf Dictionary by key.
fn get_string_from_dict(dict: &lopdf::Dictionary, key: &[u8]) -> Option<String> {
    let obj = dict.get(key).ok()?;
    match obj {
        lopdf::Object::String(bytes, _) => Some(String::from_utf8_lossy(bytes).to_string()),
        _ => None,
    }
}

/// Extract metadata from a loaded lopdf Document.
fn extract_metadata_from_doc(doc: &lopdf::Document) -> PdfMetadata {
    let mut meta = PdfMetadata::default();
    meta.num_pages = doc.get_pages().len() as u32;

    // Encryption
    meta.is_encrypted = doc);

    // Trailer /Info dict — lopdf 0.34: trailer is Dictionary (not Object enum)
    // get() returns Result<&Object>, where &Object is a reference to the value
    if let Ok(info_ref) = doc.trailer.get(b"Info") {
        // info_ref is &Object — follow Reference if needed
        let info_obj: &lopdf::Object = match info_ref {
            lopdf::Object::Reference(id) => match doc.get_object(*id) {
                Ok(obj) => obj,
                Err(_) => &lopdf::Object::Null,
            },
            other => other,
        };
        if let lopdf::Object::Dictionary(info_dict) = info_obj {
            meta.title = get_string_from_dict(info_dict, b"Title");
            meta.author = get_string_from_dict(info_dict, b"Author");
            meta.subject = get_string_from_dict(info_dict, b"Subject");
            meta.creator = get_string_from_dict(info_dict, b"Creator");
            meta.producer = get_string_from_dict(info_dict, b"Producer");
            if let Some(date_str) = get_string_from_dict(info_dict, b"CreationDate") {
                meta.creation_date = parse_pdf_date(&date_str);
            }
            if let Some(date_str) = get_string_from_dict(info_dict, b"ModDate") {
                meta.modification_date = parse_pdf_date(&date_str);
            }
        }
    }

    meta
}

/// Extract metadata from a PDF file.
///
/// # Arguments
/// * `path` - Path to PDF file on disk
///
/// # Returns
/// * `PdfMetadata` - PDF metadata (title, author, creator, dates, etc.)
///
/// # Errors
/// * `PyIOError` if file cannot be opened or parsed
/// * `PyValueError` if file exceeds size limits
#[pyfunction]
pub fn extract_metadata(path: &str) -> PyResult<PdfMetadata> {
    let path_obj = std::path::Path::new(path);

    let metadata = std::fs::metadata(path_obj).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read file metadata: {e}"))
    })?;

    if metadata.len() > MAX_PDF_SIZE as u64 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF file too large: {} bytes (max: {} bytes)",
            metadata.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load(path_obj)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF: {e}")))?;

    Ok(extract_metadata_from_doc(&doc))
}

/// Extract metadata from PDF bytes in memory.
///
/// # Arguments
/// * `data` - PDF file content as bytes
///
/// # Returns
/// * `PdfMetadata` - PDF metadata
///
/// # Errors
/// * `PyIOError` if bytes cannot be parsed as PDF
/// * `PyValueError` if data exceeds size limits
#[pyfunction]
pub fn extract_metadata_from_bytes(data: &[u8]) -> PyResult<PdfMetadata> {
    if data.len() > MAX_PDF_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF data too large: {} bytes (max: {} bytes)",
            data.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load_mem(data).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF bytes: {e}"))
    })?;

    Ok(extract_metadata_from_doc(&doc))
}

/// PDF forensics result including OCG layers and redaction analysis.
/// ISSUE-016: Advanced PDF forensics for hidden content detection.
#[pyclass]
#[derive(Debug, Default)]
pub struct PdfForensics {
    /// Optional Content Groups (layers) - each entry is (name, intent, is_visible)
    #[pyo3(get, set)]
    pub ocg_layers: Vec<(String, String, bool)>,
    /// Redaction failures - text found under redaction annotations
    #[pyo3(get, set)]
    pub redaction_failures: Vec<String>,
    /// Suppressed/hidden annotations - (page_num, annot_type, content)
    #[pyo3(get, set)]
    pub suppressed_annotations: Vec<(u32, String, String)>,
}

/// Maximum OCG layers to extract (M1 8GB safe)
const MAX_OCG_LAYERS: usize = 10;
/// Maximum pages to scan for redaction failures
const MAX_REDACTION_PAGES: u32 = 50;
/// Maximum redaction failures to report
const MAX_REDACTION_FAILURES: usize = 100;
/// Maximum suppressed annotations to report
const MAX_SUPPRESSED_ANNOTATIONS: usize = 200;

/// Extract OCG (Optional Content Groups) layers from a PDF document.
///
/// OCGs are PDF layers that can be toggled on/off. Hidden layers may contain
/// sensitive information like redacted text, watermarks, or alternate content.
///
/// # Arguments
/// * `doc` - Loaded lopdf Document
///
/// # Returns
/// * `Vec<(String, String, bool)>` - List of (name, intent, is_visible) tuples
fn extract_ocg_layers(doc: &lopdf::Document) -> Vec<(String, String, bool)> {
    let mut layers = Vec::new();

    let catalog = match doc.catalog() {
        Ok(c) => c,
        Err(_) => return layers,
    };

    let oc_props_ref = match catalog.get(b"OCProperties") {
        Ok(lopdf::Object::Reference(r)) => *r,
        _ => return layers,
    };

    let oc_props = match doc.get_object(oc_props_ref) {
        Ok(lopdf::Object::Dictionary(d)) => d,
        _ => return layers,
    };

    let ocgs_ref = match oc_props.get(b"OCGs") {
        Ok(lopdf::Object::Reference(r)) => *r,
        Ok(lopdf::Object::Array(arr)) => {
            // Direct array - process each OCG
            for obj in arr.iter().take(MAX_OCG_LAYERS) {
                if let lopdf::Object::Reference(r) = obj {
                    if let Some(layer) = extract_single_ocg(doc, *r) {
                        layers.push(layer);
                    }
                }
            }
            return layers;
        }
        _ => return layers,
    };

    // Dereference OCGs array
    let ocgs = match doc.get_object(ocgs_ref) {
        Ok(lopdf::Object::Array(arr)) => arr,
        _ => return layers,
    };

    // Process each OCG (limit to MAX_OCG_LAYERS)
    for obj in ocgs.iter().take(MAX_OCG_LAYERS) {
        if let lopdf::Object::Reference(r) = obj {
            if let Some(layer) = extract_single_ocg(doc, *r) {
                layers.push(layer);
            }
        }
    }

    layers
}

/// Extract a single OCG layer's information.
fn extract_single_ocg(
    doc: &lopdf::Document,
    ocg_ref: lopdf::ObjectId,
) -> Option<(String, String, bool)> {
    let ocg_dict = match doc.get_object(ocg_ref) {
        Ok(lopdf::Object::Dictionary(d)) => d,
        _ => return None,
    };

    let name = get_string_from_dict(ocg_dict, b"Name").unwrap_or_else(|| "Unnamed".to_string());

    // Get intent (default to "View")
    let intent = match ocg_dict.get(b"Intent") {
        Ok(lopdf::Object::Name(n)) => String::from_utf8_lossy(n).to_string(),
        Ok(lopdf::Object::Array(arr)) => {
            // Array of intents - take first
            arr.first()
                .and_then(|o| {
                    if let lopdf::Object::Name(n) = o {
                        Some(String::from_utf8_lossy(n).to_string())
                    } else {
                        None
                    }
                })
                .unwrap_or_else(|| "View".to_string())
        }
        _ => "View".to_string(),
    };

    // Check visibility from /Usage/View/ViewState or default to true
    let is_visible = match ocg_dict.get(b"Usage") {
        Ok(lopdf::Object::Reference(r)) => match doc.get_object(*r) {
            Ok(lopdf::Object::Dictionary(usage)) => match usage.get(b"View") {
                Ok(lopdf::Object::Reference(vr)) => match doc.get_object(*vr) {
                    Ok(lopdf::Object::Dictionary(view)) => match view.get(b"ViewState") {
                        Ok(lopdf::Object::Name(n)) => n.as_slice() != b"OFF",
                        _ => true,
                    },
                    _ => true,
                },
                _ => true,
            },
            _ => true,
        },
        _ => true,
    };

    Some((name, intent, is_visible))
}

/// Detect redaction failures - text visible under redaction annotations.
///
/// Redaction failures occur when:
/// 1. Black rectangles are drawn over text (visual redaction)
/// 2. But the underlying text is still selectable/searchable
///
/// This is a critical security issue - the redacted content is still accessible.
///
/// # Arguments
/// * `doc` - Loaded lopdf Document
///
/// # Returns
/// * `Vec<String>` - List of failure descriptions
fn detect_redaction_failures(doc: &lopdf::Document) -> Vec<String> {
    let mut failures = Vec::new();
    let pages = doc);

    // Limit pages to scan
    for (page_num, page_ref) in pages.iter().take(MAX_REDACTION_PAGES as usize) {
        if failures.len() >= MAX_REDACTION_FAILURES {
            break;
        }

        let page_dict = match doc.get_object(*page_ref) {
            Ok(lopdf::Object::Dictionary(d)) => d,
            _ => continue,
        };

        let annots = match page_dict.get(b"Annots") {
            Ok(lopdf::Object::Reference(r)) => match doc.get_object(*r) {
                Ok(lopdf::Object::Array(arr)) => arr.clone(),
                _ => continue,
            },
            Ok(lopdf::Object::Array(arr)) => arr.clone(),
            _ => continue,
        };

        for annot_obj in annots.iter() {
            if failures.len() >= MAX_REDACTION_FAILURES {
                break;
            }

            let annot_ref = match annot_obj {
                lopdf::Object::Reference(r) => *r,
                _ => continue,
            };

            let annot_dict = match doc.get_object(annot_ref) {
                Ok(lopdf::Object::Dictionary(d)) => d,
                _ => continue,
            };

            // Check if this is a Redact annotation
            let is_redact = match annot_dict.get(b"Subtype") {
                Ok(lopdf::Object::Name(n)) => n.as_slice() == b"Redact",
                _ => false,
            };

            if !is_redact {
                continue;
            }

            let rect = match annot_dict.get(b"Rect") {
                Ok(lopdf::Object::Array(arr)) if arr.len() == 4 => {
                    let coords: Vec<f64> = arr
                        .iter()
                        .filter_map(|o| match o {
                            lopdf::Object::Integer(i) => Some(*i as f64),
                            lopdf::Object::Real(r) => Some(*r),
                            _ => None,
                        })
                        );
                    if coords.len() == 4 {
                        Some((coords[0], coords[1], coords[2], coords[3]))
                    } else {
                        None
                    }
                }
                _ => None,
            };

            if let Some((x1, y1, x2, y2)) = rect {
                // Report the redaction failure with coordinates
                failures.push(format!(
                    "Page {}: Redaction failure at ({:.1},{:.1})-({:.1},{:.1}) - text may be recoverable",
                    page_num, x1, y1, x2, y2
                ));
            }
        }
    }

    failures
}

/// Extract suppressed/hidden annotations from PDF.
///
/// PDF annotations can have a /F (flags) field. Flag values indicate:
/// - Bit 1 (1): Invisible - annotation not displayed/printed
/// - Bit 2 (2): Hidden - annotation cannot be interacted with
/// - Bit 6 (32): Locked - annotation cannot be modified
///
/// These hidden annotations may contain IOCs, comments, or sensitive data
/// that was deliberately hidden from viewers.
///
/// # Arguments
/// * `doc` - Loaded lopdf Document
///
/// # Returns
/// * `Vec<(u32, String, String)>` - List of (page_num, annot_type, content) tuples
fn extract_suppressed_annotations(doc: &lopdf::Document) -> Vec<(u32, String, String)> {
    let mut suppressed = Vec::new();
    let pages = doc);

    // Flags that indicate suppressed/hidden annotations
    const INVISIBLE_FLAG: i64 = 1;
    const HIDDEN_FLAG: i64 = 2;

    for (page_num, page_ref) in pages.iter().take(MAX_REDACTION_PAGES as usize) {
        if suppressed.len() >= MAX_SUPPRESSED_ANNOTATIONS {
            break;
        }

        let page_dict = match doc.get_object(*page_ref) {
            Ok(lopdf::Object::Dictionary(d)) => d,
            _ => continue,
        };

        let annots = match page_dict.get(b"Annots") {
            Ok(lopdf::Object::Reference(r)) => match doc.get_object(*r) {
                Ok(lopdf::Object::Array(arr)) => arr.clone(),
                _ => continue,
            },
            Ok(lopdf::Object::Array(arr)) => arr.clone(),
            _ => continue,
        };

        for annot_obj in annots.iter() {
            if suppressed.len() >= MAX_SUPPRESSED_ANNOTATIONS {
                break;
            }

            let annot_ref = match annot_obj {
                lopdf::Object::Reference(r) => *r,
                _ => continue,
            };

            let annot_dict = match doc.get_object(annot_ref) {
                Ok(lopdf::Object::Dictionary(d)) => d,
                _ => continue,
            };

            let flags = match annot_dict.get(b"F") {
                Ok(lopdf::Object::Integer(f)) => *f,
                _ => 0,
            };

            // Check if annotation is invisible or hidden
            let is_suppressed = (flags & INVISIBLE_FLAG) != 0 || (flags & HIDDEN_FLAG) != 0;

            if !is_suppressed {
                continue;
            }

            let annot_type = match annot_dict.get(b"Subtype") {
                Ok(lopdf::Object::Name(n)) => String::from_utf8_lossy(n).to_string(),
                _ => "Unknown".to_string(),
            };

            let content = match annot_dict.get(b"Contents") {
                Ok(lopdf::Object::String(bytes, _)) => String::from_utf8_lossy(bytes).to_string(),
                _ => String::new(),
            };

            suppressed.push((*page_num, annot_type, content));
        }
    }

    suppressed
}

/// Extract comprehensive PDF forensics including OCG layers, redaction failures,
/// and suppressed annotations.
///
/// # Arguments
/// * `path` - Path to PDF file on disk
///
/// # Returns
/// * `PdfForensics` - Forensics result with all findings
///
/// # Errors
/// * `PyIOError` if file cannot be opened or parsed
/// * `PyValueError` if file exceeds size limits
#[pyfunction]
pub fn extract_pdf_forensics(path: &str) -> PyResult<PdfForensics> {
    let path = Path::new(path);

    let metadata = std::fs::metadata(path).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read file metadata: {e}"))
    })?;

    if metadata.len() > MAX_PDF_SIZE as u64 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF file too large: {} bytes (max: {} bytes)",
            metadata.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load(path)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF: {e}")))?;

    Ok(PdfForensics {
        ocg_layers: extract_ocg_layers(&doc),
        redaction_failures: detect_redaction_failures(&doc),
        suppressed_annotations: extract_suppressed_annotations(&doc),
    })
}

/// Extract PDF forensics from bytes in memory.
///
/// # Arguments
/// * `data` - PDF file content as bytes
///
/// # Returns
/// * `PdfForensics` - Forensics result with all findings
///
/// # Errors
/// * `PyIOError` if bytes cannot be parsed as PDF
/// * `PyValueError` if data exceeds size limits
#[pyfunction]
pub fn extract_pdf_forensics_from_bytes(data: &[u8]) -> PyResult<PdfForensics> {
    if data.len() > MAX_PDF_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "PDF data too large: {} bytes (max: {} bytes)",
            data.len(),
            MAX_PDF_SIZE
        )));
    }

    let doc = lopdf::Document::load_mem(data).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF bytes: {e}"))
    })?;

    Ok(PdfForensics {
        ocg_layers: extract_ocg_layers(&doc),
        redaction_failures: detect_redaction_failures(&doc),
        suppressed_annotations: extract_suppressed_annotations(&doc),
    })
}

mod tests {
    use super::*;

    #[test]
    fn test_pdf_size_limit() {
        assert_eq!(MAX_PDF_SIZE, 100 * 1024 * 1024);
        assert_eq!(MAX_PAGES, 10_000);
    }

    #[test]
    fn test_parse_pdf_date() {
        assert_eq!(
            parse_pdf_date("D:20240115123000+05'00'"),
            Some("2024-01-15T12:30:00".to_string())
        );
        assert_eq!(
            parse_pdf_date("D:20240301120000"),
            Some("2024-03-01T12:00:00".to_string())
        );
        assert_eq!(parse_pdf_date("invalid"), None);
        assert_eq!(parse_pdf_date(""), None);
    }

    #[test]
    fn test_pdf_metadata_default() {
        let meta = PdfMetadata::default();
        assert!(meta.title.is_none());
        assert!(meta.author.is_none());
        assert!(meta.num_pages == 0);
        assert!(!meta.is_encrypted);
    }
}

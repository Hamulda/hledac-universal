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

    // Check file size before loading
    let metadata = std::fs::metadata(path)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to read file metadata: {e}")))?;

    if metadata.len() > MAX_PDF_SIZE as u64 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("PDF file too large: {} bytes (max: {} bytes)", metadata.len(), MAX_PDF_SIZE)
        ));
    }

    // Load PDF document
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
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("PDF data too large: {} bytes (max: {} bytes)", data.len(), MAX_PDF_SIZE)
        ));
    }

    let doc = lopdf::Document::load_mem(data)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF bytes: {e}")))?;

    extract_text_from_doc(&doc)
}

/// Internal: extract text from a loaded lopdf Document.
fn extract_text_from_doc(doc: &lopdf::Document) -> PyResult<String> {
    let page_count = doc.get_pages().len() as u32;

    if page_count > MAX_PAGES {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("PDF has too many pages: {} (max: {})", page_count, MAX_PAGES)
        ));
    }

    let mut text = String::new();
    let page_nums: Vec<u32> = (1..=page_count).collect();

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
                return Err(pyo3::exceptions::PyIOError::new_err(
                    format!("Failed to extract text from PDF: {e}")
                ));
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
    Some(format!("{}-{}-{}T{}:{}:{}", year, month, day, hour, minute, second))
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
    meta.is_encrypted = doc.is_encrypted();

    // Trailer /Info dict — lopdf 0.34: trailer is Dictionary (not Object enum)
    // get() returns Result<&Object>, where &Object is a reference to the value
    if let Ok(info_ref) = doc.trailer.get(b"Info") {
        // info_ref is &Object — follow Reference if needed
        let info_obj: &lopdf::Object = match info_ref {
            lopdf::Object::Reference(id) => {
                match doc.get_object(*id) {
                    Ok(obj) => obj,
                    Err(_) => &lopdf::Object::Null,
                }
            }
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

    let metadata = std::fs::metadata(path_obj)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to read file metadata: {e}")))?;

    if metadata.len() > MAX_PDF_SIZE as u64 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("PDF file too large: {} bytes (max: {} bytes)", metadata.len(), MAX_PDF_SIZE)
        ));
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
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("PDF data too large: {} bytes (max: {} bytes)", data.len(), MAX_PDF_SIZE)
        ));
    }

    let doc = lopdf::Document::load_mem(data)
        .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("Failed to parse PDF bytes: {e}")))?;

    Ok(extract_metadata_from_doc(&doc))
}

#[cfg(test)]
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
        assert_eq!(parse_pdf_date("D:20240301120000"), Some("2024-03-01T12:00:00".to_string()));
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

//! WARC Parser — Issue 2.5
//!
//! Pure-Rust WARC/1.0 + gzip decompression via flate2.
//! M1 8GB: one WARC segment at a time, bounded by RAM.

use flate2::read::GzDecoder;
use pyo3::prelude::*;
use std::io::{BufRead, Cursor, Read as IoRead};

// ---------------------------------------------------------------------------
// Internal types (not exposed to Python)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum WarcRecordType {
    Response,
    Request,
    Metadata,
    Revisit,
    Conversion,
    Continuation,
    Unknown(String),
}

#[derive(Debug, Clone)]
struct WarcHeader {
    version: String,
    record_id: String,
    record_type: WarcRecordType,
    date: String,
    content_length: usize,
    warc_ip_address: Option<String>,
    warc_filename: Option<String>,
    warc_offset: Option<u64>,
}

#[derive(Debug, Clone)]
struct WarcRecord {
    header: WarcHeader,
    payload: Option<Vec<u8>>,
}

// ---------------------------------------------------------------------------
// Internal parsing helpers
// ---------------------------------------------------------------------------

fn parse_warc_type(line: &str) -> WarcRecordType {
    match line.trim() {
        "WARC-Type: response" => WarcRecordType::Response,
        "WARC-Type: request" => WarcRecordType::Request,
        "WARC-Type: metadata" => WarcRecordType::Metadata,
        "WARC-Type: revisit" => WarcRecordType::Revisit,
        "WARC-Type: conversion" => WarcRecordType::Conversion,
        "WARC-Type: continuation" => WarcRecordType::Continuation,
        other => WarcRecordType::Unknown(other.to_string()),
    }
}

fn parse_header_field(line: &str) -> Option<(String, String)> {
    let parts: Vec<&str> = line.splitn(2, ':').collect();
    if parts.len() == 2 {
        Some((parts[0].trim().to_string(), parts[1].trim().to_string()))
    } else {
        None
    }
}

fn record_type_name(rt: &WarcRecordType) -> String {
    match rt {
        WarcRecordType::Response => "response".to_string(),
        WarcRecordType::Request => "request".to_string(),
        WarcRecordType::Metadata => "metadata".to_string(),
        WarcRecordType::Revisit => "revisit".to_string(),
        WarcRecordType::Conversion => "conversion".to_string(),
        WarcRecordType::Continuation => "continuation".to_string(),
        WarcRecordType::Unknown(s) => s.clone(),
    }
}

fn parse_record<R: BufRead>(
    reader: &mut R,
    filename: Option<&str>,
    _start_offset: u64,
) -> Option<WarcRecord> {
    let mut header_lines: Vec<String> = Vec::new();

    // Read header lines until empty line (double CRLF = blank line)
    loop {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => return None,
            Ok(_) => {
                let trimmed = line.trim_end_matches('\r').trim_end_matches('\n');
                if trimmed.is_empty() {
                    break; // End of header section
                }
                header_lines.push(trimmed.to_string());
            }
            Err(_) => return None,
        }
    }

    // Parse header fields
    let mut version = String::new();
    let mut record_id = String::new();
    let mut record_type = WarcRecordType::Unknown(String::new());
    let mut date = String::new();
    let mut content_length: usize = 0;
    let mut warc_ip_address: Option<String> = None;
    let mut warc_target_uri: Option<String> = None;

    for line in &header_lines {
        match line.as_str() {
            s if s.starts_with("WARC/") => version = s.to_string(),
            s if s.starts_with("WARC-Record-ID:") => {
                if let Some((_, v)) = parse_header_field(s) { record_id = v; }
            }
            s if s.starts_with("WARC-Type:") => record_type = parse_warc_type(s),
            s if s.starts_with("WARC-Date:") => {
                if let Some((_, v)) = parse_header_field(s) { date = v; }
            }
            s if s.starts_with("Content-Length:") => {
                if let Some((_, v)) = parse_header_field(s) {
                    content_length = v.parse().unwrap_or(0);
                }
            }
            s if s.starts_with("WARC-IP-Address:") => {
                if let Some((_, v)) = parse_header_field(s) { warc_ip_address = Some(v); }
            }
            s if s.starts_with("WARC-Target-URI:") => {
                if let Some((_, v)) = parse_header_field(s) { warc_target_uri = Some(v); }
            }
            _ => {}
        }
    }

    let header = WarcHeader {
        version,
        record_id,
        record_type,
        date,
        content_length,
        warc_ip_address,
        warc_filename: filename.map(|s| s.to_string()),
        warc_offset: Some(0), // Offset tracking deferred for simplicity
    };

    // Read payload body
    let mut body = vec![0u8; content_length];
    let mut read_bytes = 0usize;
    while read_bytes < content_length {
        match reader.read(&mut body[read_bytes..]) {
            Ok(0) => break,
            Ok(n) => read_bytes += n,
            Err(_) => break,
        }
    }
    body.truncate(read_bytes);

    let payload = if body.len() == content_length { Some(body) } else { None };

    Some(WarcRecord { header, payload })
}

fn extract_url_from_response(payload: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(payload).ok()?;

    // Find Host header
    let mut host = None;
    for line in text.lines() {
        if line.starts_with("Host:") {
            host = Some(line.trim_start_matches("Host:").trim().to_string());
            break;
        }
    }

    // For WARC responses, the payload is the HTTP response
    // Return WARC-Target-URI if found, else Host
    let mut target_uri = None;
    for line in text.lines() {
        if line.starts_with("WARC-Target-URI:") {
            if let Some((_, v)) = parse_header_field(line) {
                target_uri = Some(v);
            }
            break;
        }
    }

    target_uri.or(host.map(|h| format!("http://{}", h)))
}

fn parse_gzip_warc_impl(gz_data: &[u8]) -> Vec<(String, Option<String>, String, Option<String>, Option<String>)> {
    let mut results = Vec::new();

    let mut decoder = GzDecoder::new(gz_data);
    let mut contents = Vec::new();
    if decoder.read_to_end(&mut contents).is_err() {
        return results;
    }

    let mut cursor = std::io::Cursor::new(contents);
    let mut offset: u64 = 0;

    while let Some(record) = parse_record(&mut cursor, None, offset) {
        let new_offset = cursor.position() as u64;
        if new_offset == offset {
            break;
        }
        offset = new_offset;

        if matches!(record.header.record_type, WarcRecordType::Response) {
            let record_type = record_type_name(&record.header.record_type);
            let url = record.payload.as_ref().and_then(|p| extract_url_from_response(p));
            let date = record.header.date.clone();
            let ip = record.header.warc_ip_address.clone();
            let filename = record.header.warc_filename.clone();

            results.push((record_type, url, date, ip, filename));
        }
    }

    results
}

// ---------------------------------------------------------------------------
// Python bindings
// ---------------------------------------------------------------------------

#[pyfunction]
pub fn parse_warc_gzip(segment_bytes: &[u8]) -> PyResult<Vec<PyWarcResult>> {
    let raw_results = parse_gzip_warc_impl(segment_bytes);
    let py_results: Vec<PyWarcResult> = raw_results
        .into_iter()
        .map(|(rt, url, date, ip, filename)| PyWarcResult {
            record_type: rt,
            url,
            date,
            ip_address: ip,
            warc_filename: filename,
        })
        .collect();
    Ok(py_results)
}

#[pyfunction]
pub fn parse_warc_gzip_batch(segments: Vec<Vec<u8>>) -> PyResult<Vec<Vec<PyWarcResult>>> {
    let results: Vec<Vec<PyWarcResult>> = segments
        .into_iter()
        .map(|seg| {
            let raw = parse_gzip_warc_impl(&seg);
            raw
                .into_iter()
                .map(|(rt, url, date, ip, filename)| PyWarcResult {
                    record_type: rt,
                    url,
                    date,
                    ip_address: ip,
                    warc_filename: filename,
                })
                .collect()
        })
        .collect();
    Ok(results)
}

#[pyfunction]
pub fn extract_url_from_warc_response(record_bytes: &[u8]) -> PyResult<Option<String>> {
    Ok(extract_url_from_response(record_bytes))
}

// ---------------------------------------------------------------------------
// PyO3 Python-exposed struct
// ---------------------------------------------------------------------------

#[pyclass(name = "PyWarcResult")]
#[derive(Debug, Clone)]
pub struct PyWarcResult {
    #[pyo3(get)]
    pub record_type: String,
    #[pyo3(get)]
    pub url: Option<String>,
    #[pyo3(get)]
    pub date: String,
    #[pyo3(get)]
    pub ip_address: Option<String>,
    #[pyo3(get)]
    pub warc_filename: Option<String>,
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_warc_gzip, m)?)?;
    m.add_function(wrap_pyfunction!(parse_warc_gzip_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_url_from_warc_response, m)?)?;
    m.add_class::<PyWarcResult>()?;
    Ok(())
}

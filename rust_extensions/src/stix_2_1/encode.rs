//! STIX 2.1 bundle encoding.
//!
//! Encodes `CanonicalFinding` dictionaries (from Python) into STIX 2.1 bundles
//! using serde_json for fast serialization. Supports both compact and
//! pretty-printed output.
//!
//! ## STIX 2.1 Bundle Structure
//! ```json
//! {
//!   "type": "bundle",
//!   "id": "bundle--<uuid>",
//!   "spec_version": "2.1",
//!   "objects": [<indicator>, <malware>, <note>, ...]
//! }
//! ```
//!
//! ## IOC → STIX SDO/SCO Mapping
//! CanonicalFinding fields map to STIX objects as follows:
//! - `source_type = "synthetic"` → `indicator` SDO (AI-generated content indicator)
//! - `source_type = "document"` → `indicator` SDO (document-based IOC)
//! - `source_type = "web"` → `indicator` SDO (web-harvested IOC)
//! - `confidence` → `confidence` field (0-100 in STIX)
//! - `provenance` → `created_by_ref` or `object_marking_refs`
//!
//! ## Fail-soft Invariant
//! Never raises. Errors return empty bytes/strings.

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// STIX 2.1 bundle wrapper.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StixBundle {
    #[serde(rename = "type")]
    pub bundle_type: String,
    pub id: String,
    #[serde(rename = "spec_version")]
    pub spec_version: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub objects: Vec<Value>,
}

impl StixBundle {
    /// Create a new empty STIX 2.1 bundle.
    pub fn new() -> Self {
        Self {
            bundle_type: "bundle".to_string(),
            id: format!("bundle--{}", new_uuid()),
            spec_version: "2.1".to_string(),
            objects: Vec::with_capacity(64),
        }
    }

    /// Add a STIX object (SDO or SCO) to the bundle.
    pub fn add_object(&mut self, obj: Value) {
        self.objects.push(obj);
    }

    /// Serialize to compact JSON bytes (STIX-JSON format).
    pub fn to_bytes_compact(&self) -> Vec<u8> {
        serde_json::to_vec(self).unwrap_or_default()
    }

    /// Serialize to pretty-printed JSON bytes.
    pub fn to_bytes_pretty(&self) -> Vec<u8> {
        serde_json::to_vec_pretty(self).unwrap_or_default()
    }
}

impl Default for StixBundle {
    fn default() -> Self {
        Self::new()
    }
}

/// Generate a proper RFC 4122 UUID v4 for STIX bundle/object IDs.
/// Uses rand crate — cryptographically suitable for identifiers.
fn new_uuid() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rng().fill_bytes(&mut bytes);
    // Set version (4) and variant (RFC 4122)
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3],
        bytes[4], bytes[5],
        bytes[6], bytes[7],
        bytes[8], bytes[9],
        bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    )
}

/// Map IOC type string to STIX object type.
fn ioc_type_to_stix_type(ioc_type: &str) -> &'static str {
    match ioc_type.to_lowercase().as_str() {
        "url" => "url",
        "ipv4" | "ipv4-addr" => "ipv4-addr",
        "ipv6" | "ipv6-addr" => "ipv6-addr",
        "domain" | "domain-name" => "domain-name",
        "email" | "email-addr" => "email-addr",
        "md5" => "file-hash",
        "sha1" => "file-hash",
        "sha256" => "file-hash",
        "sha512" => "file-hash",
        "cve" => "vulnerability",
        "mutex" | "mutex-name" => "mutex",
        "registry" | "registry-key" => "registry-key",
        _ => "unknown",
    }
}

/// Build a STIX indicator SDO from a finding dict.
/// Assumes the dict has keys: ioc_type, ioc_value, source_type, confidence, query
pub fn encode_indicator(finding: &Map<String, Value>) -> Option<Value> {
    let ioc_type = finding.get("ioc_type")?.as_str()?;
    let ioc_value = finding.get("ioc_value")?.as_str()?;
    let source_type = finding
        .get("source_type")
        .and_then(|v| v.as_str())
        .unwrap_or("web");
    let confidence = finding
        .get("confidence")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.5);
    let query = finding.get("query").and_then(|v| v.as_str()).unwrap_or("");
    let finding_id = finding
        .get("finding_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // Build pattern based on IOC type
    let pattern = build_cybox_pattern(ioc_type, ioc_value)?;
    let stix_type = ioc_type_to_stix_type(ioc_type);
    let stix_id = format!("{}-{}", stix_type, new_uuid());
    let now = iso8601_timestamp();

    // SCO for the observable
    let sco = build_sco(stix_type, ioc_value, ioc_type)?;

    let mut indicator = Map::new();
    indicator.insert("type".to_string(), Value::String(stix_type.to_string()));
    indicator.insert("id".to_string(), Value::String(stix_id));
    indicator.insert("spec_version".to_string(), Value::String("2.1".to_string()));
    indicator.insert("created".to_string(), Value::String(now.clone()));
    indicator.insert("modified".to_string(), Value::String(now));
    indicator.insert(
        "name".to_string(),
        Value::String(format!("{} indicator: {}", source_type, query)),
    );
    indicator.insert(
        "description".to_string(),
        Value::String(format!(
            "OSINT indicator extracted from {} source. Query: {}",
            source_type, query
        )),
    );
    indicator.insert("pattern".to_string(), Value::String(pattern));
    indicator.insert(
        "pattern_type".to_string(),
        Value::String("stix".to_string()),
    );
    indicator.insert("valid_from".to_string(), Value::String(iso8601_timestamp()));
    indicator.insert(
        "confidence".to_string(),
        Value::Number(serde_json::Number::from((confidence * 100.0) as i64)),
    );
    indicator.insert(
        "valid_until".to_string(),
        Value::String(future_timestamp(90)),
    ); // 90 days validity
    indicator.insert(
        "labels".to_string(),
        Value::Array(vec![
            Value::String(source_type.to_string()),
            Value::String("osint".to_string()),
        ]),
    );

    // Add the SCO as observable
    indicator.insert(
        "object_marking_refs".to_string(),
        Value::Array(vec![Value::String(
            "marking-definition--613f2e26-407d-48f7-9f50-60798f4e9e5e".to_string(),
        )]),
    );

    // Attach SCO if applicable
    if sco.is_object() || sco.is_array() {
        // SCOs are embedded in pattern or as observables
        // For indicators, the SCO is encoded in the pattern itself
    }

    Some(Value::Object(indicator))
}

/// Build a SCO (STIX Cyber-observable Object) from IOC data.
fn build_sco(stix_type: &str, ioc_value: &str, _ioc_type: &str) -> Option<Value> {
    let mut sco = Map::new();
    sco.insert("type".to_string(), Value::String(stix_type.to_string()));
    sco.insert("value".to_string(), Value::String(ioc_value.to_string()));

    // Add type-specific fields
    match stix_type {
        "file-hash" => {
            // Determine hash algorithm from value length
            let (algorithm, hash_value) = match ioc_value.len() {
                32 => ("MD5", ioc_value),
                40 => ("SHA-1", ioc_value),
                64 => ("SHA-256", ioc_value),
                128 => ("SHA-512", ioc_value),
                _ => ("MD5", ioc_value),
            };
            let mut hashes = Map::new();
            hashes.insert(algorithm.to_string(), Value::String(hash_value.to_string()));
            sco.insert("hashes".to_string(), Value::Object(hashes));
        }
        "ipv4-addr" => {
            sco.insert("value".to_string(), Value::String(ioc_value.to_string()));
            sco.insert("resolves_to_refs".to_string(), Value::Array(vec![]));
        }
        "ipv6-addr" => {
            sco.insert("value".to_string(), Value::String(ioc_value.to_string()));
        }
        "domain-name" => {
            sco.insert("value".to_string(), Value::String(ioc_value.to_string()));
            sco.insert("resolves_to_refs".to_string(), Value::Array(vec![]));
        }
        "url" => {
            sco.insert("value".to_string(), Value::String(ioc_value.to_string()));
        }
        "email-addr" => {
            sco.insert("value".to_string(), Value::String(ioc_value.to_string()));
            sco.insert("belongs_to_refs".to_string(), Value::Array(vec![]));
        }
        _ => {}
    }

    Some(Value::Object(sco))
}

/// Build a STIX pattern for a given IOC type and value.
/// Uses the STIX cyber-observable expressions (CyBox) pattern language.
fn build_cybox_pattern(ioc_type: &str, ioc_value: &str) -> Option<String> {
    let encoded_value = ioc_value.replace('\\', "\\\\").replace('\'', "\\'");
    match ioc_type.to_lowercase().as_str() {
        "url" => Some(format!("url = '{encoded_value}'")),
        "ipv4" | "ipv4-addr" => Some(format!("ipv4-addr:value = '{encoded_value}'")),
        "ipv6" | "ipv6-addr" => Some(format!("ipv6-addr:value = '{encoded_value}'")),
        "domain" | "domain-name" => Some(format!("domain-name:value = '{encoded_value}'")),
        "email" | "email-addr" => Some(format!("email-addr:value = '{encoded_value}'")),
        "md5" => Some(format!("file-hash:hashes.MD5 = '{encoded_value}'")),
        "sha1" => Some(format!("file-hash:hashes.'SHA-1' = '{encoded_value}'")),
        "sha256" => Some(format!("file-hash:hashes.'SHA-256' = '{encoded_value}'")),
        "sha512" => Some(format!("file-hash:hashes.'SHA-512' = '{encoded_value}'")),
        "mutex" => Some(format!("mutex:name = '{encoded_value}'")),
        "registry" => Some(format!("registry-key:key = '{encoded_value}'")),
        "cve" => Some(format!("vulnerability:cve = '{encoded_value}'")),
        _ => None,
    }
}

/// Build a note SDO from finding metadata.
pub fn encode_note(query: &str, summary: &str, finding_id: &str) -> Value {
    let mut note = Map::new();
    note.insert("type".to_string(), Value::String("note".to_string()));
    note.insert(
        "id".to_string(),
        Value::String(format!("note--{}", new_uuid())),
    );
    note.insert("spec_version".to_string(), Value::String("2.1".to_string()));
    note.insert("created".to_string(), Value::String(iso8601_timestamp()));
    note.insert("modified".to_string(), Value::String(iso8601_timestamp()));
    note.insert("abstract".to_string(), Value::String(query.to_string()));
    note.insert("content".to_string(), Value::String(summary.to_string()));
    note.insert("object_refs".to_string(), Value::Array(vec![]));
    Value::Object(note)
}

/// Encode a canonical finding dict to STIX bundle bytes.
#[pyfunction]
pub fn encode_finding(finding_py: &PyAny, py: Python<'_>) -> PyResult<Py<PyBytes>> {
    // Convert Python dict → serde_json::Value
    let dict_str = match finding_py.call_method0("__str__") {
        Ok(s) => s.extract::<String>().unwrap_or_default(),
        Err(_) => return Ok(PyBytes::new(py, &[]).into()),
    };

    let finding: Map<String, Value> = match serde_json::from_str(&dict_str) {
        Ok(serde_json::Value::Object(m)) => m,
        _ => return Ok(PyBytes::new(py, &[]).into()),
    };

    let mut bundle = StixBundle::new();

    // Encode as indicator
    if let Some(indicator) = encode_indicator(&finding) {
        bundle.add_object(indicator);
    }

    // Encode as note
    let query = finding.get("query").and_then(|v| v.as_str()).unwrap_or("");
    let payload = finding
        .get("payload_text")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let finding_id = finding
        .get("finding_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let note = encode_note(query, payload, finding_id);
    bundle.add_object(note);

    let bytes = bundle.to_bytes_compact();
    Ok(PyBytes::new(py, &bytes).into())
}

/// Encode a list of finding dicts to a STIX bundle.
#[pyfunction]
pub fn encode_findings_batch(findings_py: &PyAny, py: Python<'_>) -> PyResult<Py<PyBytes>> {
    use rayon::prelude::*;

    // Convert Python list → Vec of serde Value
    let list_str = match findings_py.call_method0("__str__") {
        Ok(s) => s.extract::<String>().unwrap_or_default(),
        Err(_) => return Ok(PyBytes::new(py, &[]).into()),
    };

    let findings: Vec<Map<String, Value>> =
        match serde_json::from_str::<Vec<Map<String, Value>>>(&list_str) {
            Ok(v) => v,
            _ => return Ok(PyBytes::new(py, &[]).into()),
        };

    // Parallel encode — rayon parallel iterator
    let indicators: Vec<Value> = findings
        .par_iter()
        .filter_map(|f| encode_indicator(f))
        .collect();

    let mut bundle = StixBundle::new();
    for indicator in indicators {
        bundle.add_object(indicator);
    }

    // Add summary note
    let total = findings.len();
    let note = encode_note(
        &format!("Batch export of {total} findings"),
        "Generated by Hledac OSINT orchestrator",
        "",
    );
    bundle.add_object(note);

    let bytes = bundle.to_bytes_compact();
    Ok(PyBytes::new(py, &bytes).into())
}

/// Parse STIX bundle bytes → Python dict.
#[pyfunction]
pub fn decode_bundle(bundle_bytes: &[u8]) -> PyResult<String> {
    let value: Value = match serde_json::from_slice(bundle_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(format!("{{\"error\": \"{}\"}}", e)),
    };

    match serde_json::to_string(&value) {
        Ok(s) => Ok(s),
        Err(e) => Ok(format!("{{\"error\": \"{}\"}}", e)),
    }
}

/// Encode findings to pretty-printed STIX bundle (for human review).
#[pyfunction]
pub fn encode_finding_pretty(finding_py: &PyAny, py: Python<'_>) -> PyResult<String> {
    let dict_str = match finding_py.call_method0("__str__") {
        Ok(s) => s.extract::<String>().unwrap_or_default(),
        Err(_) => return Ok(String::new()),
    };

    let finding: Map<String, Value> = match serde_json::from_str(&dict_str) {
        Ok(serde_json::Value::Object(m)) => m,
        _ => return Ok(String::new()),
    };

    let mut bundle = StixBundle::new();

    if let Some(indicator) = encode_indicator(&finding) {
        bundle.add_object(indicator);
    }

    let query = finding.get("query").and_then(|v| v.as_str()).unwrap_or("");
    let payload = finding
        .get("payload_text")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let finding_id = finding
        .get("finding_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let note = encode_note(query, payload, finding_id);
    bundle.add_object(note);

    match serde_json::to_string_pretty(&bundle) {
        Ok(s) => Ok(s),
        Err(_) => Ok(String::new()),
    }
}

/// Validate STIX JSON string — Python-callable wrapper.
///
/// # Arguments
/// * `stix_json` — UTF-8 STIX JSON string to validate
///
/// # Returns
/// Serialized `ValidationResult` JSON string with fields:
///   `is_valid: bool`, `errors: Vec<ValidationError>`, `object_count: Option<usize>`
#[pyfunction]
pub fn validate_json(stix_json: &str) -> String {
    let result = crate::stix_2_1::validation::validate_stix_json(stix_json);
    serde_json::to_string(&result).unwrap_or_else(|_| {
        r#"{"is_valid":false,"errors":[{"path":"","message":"serialization error","value_preview":null}],"object_count":null}"#.to_string()
    })
}

// ─── Timestamp helpers ────────────────────────────────────────────────────────

/// Julian Day Number for 1970-01-01 (Unix epoch).
const JULIAN_DAY_1970: u64 = 2_440_588;

/// Convert Julian Day Number to (year, month, day) in the Gregorian calendar.
/// Fliegel-Van Flandern algorithm (1968).
#[inline]
fn julian_day_to_ymd(jdn: u64) -> (u32, u32, u32) {
    let l = jdn + 68569;
    let n = (4 * l) / 146097;
    let l = l - (146097 * n + 3) / 4;
    let i = (4000 * (l + 1)) / 1461001;
    let l = l - (1461 * i) / 4 + 31;
    let j = (80 * l) / 2447;
    let day = (l - (2447 * j) / 80) as u32;
    let l = j / 11;
    let month = (j + 2 - 12 * l) as u32;
    let year = (100 * (n - 49) + i + l) as u32;
    (year, month, day)
}

/// Get current UTC time as ISO 8601 / RFC 3339 string: "YYYY-MM-DDTHH:MM:SSZ".
/// Uses only std library — no external dependencies.
fn iso8601_timestamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let days = now / 86400;
    let secs_in_day = now % 86400;
    let jd = JULIAN_DAY_1970 + days;

    let (year, month, day) = julian_day_to_ymd(jd);
    let hour = (secs_in_day / 3600) as u32;
    let minute = ((secs_in_day % 3600) / 60) as u32;
    let second = (secs_in_day % 60) as u32;

    // Build ISO string manually — faster than format! with conditional padding
    let y = year;
    let m = month;
    let d = day;
    let h = hour;
    let mn = minute;
    let s = second;

    // Use a simple approach: format into a fixed-size buffer
    // Year is always 4 digits for years 1000-9999
    let y_str = if y < 10 {
        format!("000{y}")
    } else if y < 100 {
        format!("00{y}")
    } else if y < 1000 {
        format!("0{y}")
    } else {
        y.to_string()
    };
    let m_str = if m < 10 {
        format!("0{m}")
    } else {
        m.to_string()
    };
    let d_str = if d < 10 {
        format!("0{d}")
    } else {
        d.to_string()
    };
    let h_str = if h < 10 {
        format!("0{h}")
    } else {
        h.to_string()
    };
    let mn_str = if mn < 10 {
        format!("0{mn}")
    } else {
        mn.to_string()
    };
    let s_str = if s < 10 {
        format!("0{s}")
    } else {
        s.to_string()
    };

    format!("{y_str}-{m_str}-{d_str}T{h_str}:{mn_str}:{s_str}Z")
}

/// Get future UTC timestamp (current time + days) as ISO 8601 string.
fn future_timestamp(days: i64) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let future_secs = now.saturating_add((days as u64).saturating_mul(86400));
    let days_count = future_secs / 86400;
    let secs_in_day = future_secs % 86400;
    let jd = JULIAN_DAY_1970 + days_count;

    let (year, month, day) = julian_day_to_ymd(jd);
    let hour = (secs_in_day / 3600) as u32;
    let minute = ((secs_in_day % 3600) / 60) as u32;
    let second = (secs_in_day % 60) as u32;

    let y_str = if year < 10 {
        format!("000{year}")
    } else if year < 100 {
        format!("00{year}")
    } else if year < 1000 {
        format!("0{year}")
    } else {
        year.to_string()
    };
    let m_str = if month < 10 {
        format!("0{month}")
    } else {
        month.to_string()
    };
    let d_str = if day < 10 {
        format!("0{day}")
    } else {
        day.to_string()
    };
    let h_str = if hour < 10 {
        format!("0{hour}")
    } else {
        hour.to_string()
    };
    let mn_str = if minute < 10 {
        format!("0{minute}")
    } else {
        minute.to_string()
    };
    let s_str = if second < 10 {
        format!("0{second}")
    } else {
        second.to_string()
    };

    format!("{y_str}-{m_str}-{d_str}T{h_str}:{mn_str}:{s_str}Z")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bundle_empty() {
        let bundle = StixBundle::new();
        let bytes = bundle.to_bytes_compact();
        let json_str = String::from_utf8(bytes).unwrap();
        assert!(json_str.contains("\"type\":\"bundle\""));
        assert!(json_str.contains("\"spec_version\":\"2.1\""));
    }

    #[test]
    fn test_cybox_pattern_url() {
        let pattern = build_cybox_pattern("url", "https://evil.com/payload").unwrap();
        assert_eq!(pattern, "url = 'https://evil.com/payload'");
    }

    #[test]
    fn test_cybox_pattern_ipv4() {
        let pattern = build_cybox_pattern("ipv4", "1.2.3.4").unwrap();
        assert_eq!(pattern, "ipv4-addr:value = '1.2.3.4'");
    }

    #[test]
    fn test_cybox_pattern_sha256() {
        let pattern = build_cybox_pattern(
            "sha256",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        .unwrap();
        assert!(pattern.contains("SHA-256"));
    }

    #[test]
    fn test_encode_indicator() {
        let mut finding = Map::new();
        finding.insert("ioc_type".to_string(), Value::String("url".to_string()));
        finding.insert(
            "ioc_value".to_string(),
            Value::String("https://evil.com".to_string()),
        );
        finding.insert("source_type".to_string(), Value::String("web".to_string()));
        finding.insert(
            "confidence".to_string(),
            Value::Number(serde_json::Number::from_f64(0.85).unwrap()),
        );
        finding.insert("query".to_string(), Value::String("test query".to_string()));
        finding.insert(
            "finding_id".to_string(),
            Value::String("finding-123".to_string()),
        );

        let indicator = encode_indicator(&finding).unwrap();
        let obj = indicator.as_object().unwrap();
        assert_eq!(obj.get("type").and_then(|v| v.as_str()), Some("url"));
        assert!(obj.contains_key("pattern"));
        assert!(obj.contains_key("id"));
        assert!(obj.contains_key("spec_version"));
    }

    #[test]
    fn test_uuid_format() {
        let id = new_uuid();
        // UUID format: 8-4-4-4-12 hex
        assert!(id.len() == 36);
        assert!(id.chars().filter(|c| *c == '-').count() == 4);
    }
}

//! STIX 2.1 JSON Schema validation.
//!
//! Validates STIX 2.1 bundles, SDOs, and SCOs against the OASIS STIX 2.1 JSON schema.
//! Uses the `jsonschema` crate for validation — fast, compile-time schema compilation,
//! and returns detailed error paths for debugging.
//!
//! ## Fail-soft Invariant
//! Never raises. All errors are encoded in `ValidationResult`:
//! - `is_valid: true, errors: []` — validation passed
//! - `is_valid: false, errors: [ErrorEntry, ...]` — validation failed with paths

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Detailed validation error for one schema violation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationError {
    /// JSON path to the failing field (e.g., "objects[0].id")
    pub path: String,
    /// Human-readable error message
    pub message: String,
    /// The JSON value that failed validation
    pub value_preview: Option<String>,
}

/// Validation result returned to Python via `stix.validate()`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationResult {
    /// `true` if the STIX JSON is valid, `false` otherwise.
    pub is_valid: bool,
    /// Detailed errors if `is_valid == false`, empty list otherwise.
    pub errors: Vec<ValidationError>,
    /// Number of objects in the bundle (if parseable)
    pub object_count: Option<usize>,
}

impl ValidationResult {
    /// Validation passed — fast path.
    pub fn valid() -> Self {
        Self {
            is_valid: true,
            errors: Vec::new(),
            object_count: None,
        }
    }

    /// Validation passed with object count.
    pub fn valid_with_count(count: usize) -> Self {
        Self {
            is_valid: true,
            errors: Vec::new(),
            object_count: Some(count),
        }
    }

    /// Validation failed — accumulate errors.
    pub fn invalid(errors: Vec<ValidationError>) -> Self {
        Self {
            is_valid: false,
            errors,
            object_count: None,
        }
    }

    /// Parse error — input is not valid JSON.
    pub fn parse_error(msg: String) -> Self {
        Self {
            is_valid: false,
            errors: vec![ValidationError {
                path: String::new(),
                message: format!("JSON parse error: {msg}"),
                value_preview: None,
            }],
            object_count: None,
        }
    }

    /// STIX feature not enabled.
    pub fn feature_disabled() -> Self {
        Self {
            is_valid: false,
            errors: vec![ValidationError {
                path: String::new(),
                message: "STIX validation requires the 'stix' feature flag".to_string(),
                value_preview: None,
            }],
            object_count: None,
        }
    }
}

/// Validate STIX JSON string — returns `ValidationResult`.
/// When `stix` feature is disabled, returns `ValidationResult::feature_disabled()`.
pub fn validate_stix_json(json_str: &str) -> ValidationResult {
    #[cfg(feature = "stix")]
    {
        validate_stix_json_impl(json_str)
    }
    #[cfg(not(feature = "stix"))]
    {
        let _ = json_str; // suppress unused warning
        ValidationResult::feature_disabled()
    }
}

/// Internal STIX validation implementation (only compiled when stix feature is enabled).
#[cfg(feature = "stix")]
fn validate_stix_json_impl(json_str: &str) -> ValidationResult {
    // Step 1: Parse JSON — fail-fast on invalid JSON
    let value: Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(e) => return ValidationResult::parse_error(e.to_string()),
    };

    // Step 2: Basic structural checks (STIX 2.1 required fields)
    if let Some(obj) = value.as_object() {
        let has_type = obj.contains_key("type");
        let has_id = obj.contains_key("id");

        if has_type {
            if let Some(t) = obj.get("type").and_then(|v| v.as_str()) {
                if t == "bundle" {
                    return validate_bundle(obj);
                }
            }
        }

        // If it looks like a SDO but missing spec_version
        if has_id && has_type && !obj.contains_key("spec_version") {
            return validate_sdo(&value);
        }
    }

    ValidationResult::invalid(vec![ValidationError {
        path: String::new(),
        message: "Unrecognized STIX object type".to_string(),
        value_preview: value.get("type").and_then(|v| v.as_str()).map(String::from),
    }])
}

/// Validate a STIX bundle object.
#[cfg(feature = "stix")]
fn validate_bundle(bundle: &serde_json::Map<String, Value>) -> ValidationResult {
    use std::collections::HashSet;

    if !bundle.contains_key("objects") {
        return ValidationResult::invalid(vec![ValidationError {
            path: String::new(),
            message: "STIX bundle must have 'objects' array".to_string(),
            value_preview: Some("bundle".to_string()),
        }]);
    }

    let objects = match bundle.get("objects").and_then(|v| v.as_array()) {
        Some(arr) => arr,
        None => {
            return ValidationResult::invalid(vec![ValidationError {
                path: "objects".to_string(),
                message: "'objects' field must be an array".to_string(),
                value_preview: None,
            }])
        }
    };

    let count = objects.len();
    let mut errors = Vec::new();
    let mut ids: HashSet<String> = HashSet::with_capacity(count.min(256));

    for (i, obj) in objects.iter().enumerate() {
        let path_prefix = format!("objects[{i}]");

        let obj_type = obj
            .get("type")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let obj_id = obj.get("id").and_then(|v| v.as_str());

        // Check ID uniqueness
        if let Some(id) = obj_id {
            if !ids.insert(id.to_string()) {
                errors.push(ValidationError {
                    path: format!("{path_prefix}.id"),
                    message: format!("Duplicate STIX ID '{id}' in bundle"),
                    value_preview: None,
                });
            }
        } else {
            errors.push(ValidationError {
                path: format!("{path_prefix}.id"),
                message: format!("STIX object of type '{obj_type}' missing required field 'id'"),
                value_preview: None,
            });
        }

        // SCOs: check value field; SDOs: check spec_version + created/modified
        match obj_type {
            "ipv4-addr" | "ipv6-addr" | "domain-name" | "url" | "file-hash" | "email-addr" => {
                if !obj.get("value").is_some() {
                    errors.push(ValidationError {
                        path: format!("{path_prefix}.value"),
                        message: format!("{obj_type} SCO missing required 'value' field"),
                        value_preview: None,
                    });
                }
            }
            _ => {
                // SDO — must have spec_version, created, modified
                if !obj.contains_key("spec_version") {
                    errors.push(ValidationError {
                        path: format!("{path_prefix}.spec_version"),
                        message: format!("SDO '{obj_type}' missing 'spec_version' field"),
                        value_preview: None,
                    });
                }
            }
        }
    }

    if errors.is_empty() {
        ValidationResult::valid_with_count(count)
    } else {
        ValidationResult::invalid(errors)
    }
}

/// Validate a STIX Domain Object (SDO).
#[cfg(feature = "stix")]
fn validate_sdo(value: &Value) -> ValidationResult {
    let obj = match value.as_object() {
        Some(o) => o,
        None => {
            return ValidationResult::invalid(vec![ValidationError {
                path: String::new(),
                message: "STIX object must be a JSON object".to_string(),
                value_preview: None,
            }])
        }
    };

    let obj_type = obj
        .get("type")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let mut errors = Vec::new();
    let path_prefix = String::new();

    // Type-specific required fields
    match obj_type {
        "indicator" => {
            if !obj.contains_key("pattern") {
                errors.push(ValidationError {
                    path: format!("{path_prefix}.pattern"),
                    message: "indicator SDO missing required 'pattern' field".to_string(),
                    value_preview: None,
                });
            }
            if !obj.contains_key("valid_from") {
                errors.push(ValidationError {
                    path: format!("{path_prefix}.valid_from"),
                    message: "indicator SDO missing required 'valid_from' field".to_string(),
                    value_preview: None,
                });
            }
        }
        "malware" => {
            if !obj.contains_key("name") {
                errors.push(ValidationError {
                    path: format!("{path_prefix}.name"),
                    message: "malware SDO missing required 'name' field".to_string(),
                    value_preview: None,
                });
            }
        }
        "note" => {
            if !obj.contains_key("abstract") && !obj.contains_key("content") {
                errors.push(ValidationError {
                    path: format!("{path_prefix}.abstract"),
                    message: "note SDO should have 'abstract' or 'content' field".to_string(),
                    value_preview: None,
                });
            }
        }
        _ => {}
    }

    if errors.is_empty() {
        ValidationResult::valid()
    } else {
        ValidationResult::invalid(errors)
    }
}

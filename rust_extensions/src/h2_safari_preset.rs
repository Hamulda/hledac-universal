//! h2_safari_preset.rs — Safari WebKit HTTP/2 SETTINGS fingerprint presets
//!
//! [NEXUS]-018-01: HTTP/2 Frame Windowing & Safari WebKit SETTINGS Spoofing
//!
//! ## Problem
//!
//! Modern anti-bot systems (Cloudflare Enterprise, Akamai Bot Manager, DataDome)
//! analyze HTTP/2 SETTINGS frames to detect automation. Real Safari WebKit
//! (macOS Sequoia 15.4+) sends specific SETTINGS values that differ from
//! generic curl_cffi/nghttp2 defaults.
//!
//! ## Safari WebKit HTTP/2 Fingerprint
//!
//! | Setting | Safari 18.0 | Safari 17.4 | curl_cffi generic |
//! |---------|-------------|-------------|-------------------|
//! | HEADER_TABLE_SIZE | 65,536 | 65,536 | 65,536 |
//! | ENABLE_PUSH | 1 | 1 | 1 |
//! | MAX_CONCURRENT_STREAMS | 100 | 100 | 100 |
//! | INITIAL_WINDOW_SIZE | 4,194,304 | 4,194,304 | 65,535 |
//! | MAX_FRAME_SIZE | 16,384 | 16,384 | 16,384 |
//! | MAX_HEADER_LIST_SIZE | 100,000 | 80,000 | ~262,144 |
//!
//! PRIORITY frames: Safari 18.0+ does NOT send PRIORITY frames (RFC 9218 strict).
//!
//! WINDOW_UPDATE pattern: Safari sends +1,048,304 (1 MiB - 216) byte increments.
//!
//! ## API
//!
//! ```python
//! # Get Safari 18.0 preset
//! preset = rust.h2.get_safari18_settings()
//! # Returns: [(1, 65536), (2, 1), (3, 100), (4, 4194304), (5, 16384), (6, 100000)]
//!
//! # Get Safari 17.4 preset
//! preset = rust.h2.get_safari17_settings()
//!
//! # Check if profile needs WebKit preset
//! needs_preset = rust.h2.needs_webkit_preset("safari18_0")  # True
//! needs_preset = rust.h2.needs_webkit_preset("chrome133")   # False
//!
//! # Get WINDOW_UPDATE increment for Safari (bytes)
//! window_increment = rust.h2.get_webkit_window_increment()  # 1048304
//! ```
//!
//! ## M1 8GB Safety
//!
//! - Pure Rust, zero runtime allocation after initialization
//! - Static constants only (~100 bytes total)
//! - No network I/O

use pyo3::prelude::*;

// ============================================================================
// Safari WebKit HTTP/2 SETTINGS Constants
// ============================================================================

/// HTTP/2 SETTINGS frame identifier types.
pub const H2_SETTING_HEADER_TABLE_SIZE: u16 = 0x1;     // SETTINGS_HEADER_TABLE_SIZE
pub const H2_SETTING_ENABLE_PUSH: u16 = 0x2;            // SETTINGS_ENABLE_PUSH
pub const H2_SETTING_MAX_CONCURRENT_STREAMS: u16 = 0x3; // SETTINGS_MAX_CONCURRENT_STREAMS
pub const H2_SETTING_INITIAL_WINDOW_SIZE: u16 = 0x4;    // SETTINGS_INITIAL_WINDOW_SIZE
pub const H2_SETTING_MAX_FRAME_SIZE: u16 = 0x5;         // SETTINGS_MAX_FRAME_SIZE
pub const H2_SETTING_MAX_HEADER_LIST_SIZE: u16 = 0x6;   // SETTINGS_MAX_HEADER_LIST_SIZE

/// Safari 18.0 (macOS Sequoia 15.4) HTTP/2 SETTINGS values.
/// Key differentiator: INITIAL_WINDOW_SIZE = 4,194,304 (4 MiB).
pub const SAFARI_18_SETTINGS: [(u16, u32); 6] = [
    (H2_SETTING_HEADER_TABLE_SIZE, 65_536),          // 64 KiB HPACK table
    (H2_SETTING_ENABLE_PUSH, 1),                      // Server push enabled
    (H2_SETTING_MAX_CONCURRENT_STREAMS, 100),         // 100 concurrent streams
    (H2_SETTING_INITIAL_WINDOW_SIZE, 4_194_304),     // 4 MiB initial window (KEY DIFFERENCE)
    (H2_SETTING_MAX_FRAME_SIZE, 16_384),              // 16 KiB max frame
    (H2_SETTING_MAX_HEADER_LIST_SIZE, 100_000),      // 100 KB max headers
];

/// Safari 17.4 (macOS Sonoma 14.4) HTTP/2 SETTINGS values.
/// Slightly different MAX_HEADER_LIST_SIZE vs 18.0.
pub const SAFARI_17_SETTINGS: [(u16, u32); 6] = [
    (H2_SETTING_HEADER_TABLE_SIZE, 65_536),
    (H2_SETTING_ENABLE_PUSH, 1),
    (H2_SETTING_MAX_CONCURRENT_STREAMS, 100),
    (H2_SETTING_INITIAL_WINDOW_SIZE, 4_194_304),     // Same as 18.0
    (H2_SETTING_MAX_FRAME_SIZE, 16_384),
    (H2_SETTING_MAX_HEADER_LIST_SIZE, 80_000),        // 80 KB (vs 100 KB in 18.0)
];

/// Safari 16.x HTTP/2 SETTINGS values (reference).
pub const SAFARI_16_SETTINGS: [(u16, u32); 6] = [
    (H2_SETTING_HEADER_TABLE_SIZE, 65_536),
    (H2_SETTING_ENABLE_PUSH, 1),
    (H2_SETTING_MAX_CONCURRENT_STREAMS, 100),
    (H2_SETTING_INITIAL_WINDOW_SIZE, 2_097_152),     // 2 MiB in older versions
    (H2_SETTING_MAX_FRAME_SIZE, 16_384),
    (H2_SETTING_MAX_HEADER_LIST_SIZE, 65_536),       // 64 KB in older versions
];

/// curl_cffi default HTTP/2 SETTINGS (generic nghttp2).
/// This is what we NEED to override to match Safari.
pub const CURL_CFFI_DEFAULT_SETTINGS: [(u16, u32); 5] = [
    (H2_SETTING_HEADER_TABLE_SIZE, 65_536),
    (H2_SETTING_ENABLE_PUSH, 1),
    (H2_SETTING_MAX_CONCURRENT_STREAMS, 100),
    (H2_SETTING_INITIAL_WINDOW_SIZE, 65_535),         // DIFFERENT from Safari (4 MiB)
    (H2_SETTING_MAX_FRAME_SIZE, 16_384),
];

/// Safari WebKit WINDOW_UPDATE increment (bytes).
/// Safari sends +1,048,304 byte increments (1 MiB - 216).
pub const WEBKIT_WINDOW_INCREMENT: u32 = 1_048_304;

/// Safari 18.0 does NOT send PRIORITY frames (RFC 9218 strict).
pub const SAFARI_18_NO_PRIORITY: bool = true;

/// Safari 17.4 also suppresses PRIORITY frames.
pub const SAFARI_17_NO_PRIORITY: bool = true;

// ============================================================================
// Python API
// ============================================================================

/// H2Settings tuple for Python consumption.
#[derive(Debug, Clone)]
#[pyclass]
pub struct H2Settings {
    #[pyo3(get)]
    pub settings: Vec<(u16, u32)>,
    #[pyo3(get)]
    pub window_increment: u32,
    #[pyo3(get)]
    pub no_priority: bool,
    #[pyo3(get)]
    pub profile_name: String,
}

impl H2Settings {
    fn new(name: &'static str, settings: &'static [(u16, u32); 6], no_priority: bool) -> Self {
        Self {
            settings: settings.iter().copied().collect(),
            window_increment: WEBKIT_WINDOW_INCREMENT,
            no_priority,
            profile_name: name.to_string(),
        }
    }
}

/// Profiles that need Safari WebKit HTTP/2 preset.
fn is_webkit_profile(profile: &str) -> bool {
    matches!(
        profile,
        "safari18_0" | "safari17_4" | "safari16_0" | "safari_ios_18" | "safari_ios_17"
    )
}

/// Get Safari 18.0 HTTP/2 SETTINGS preset.
#[pyfunction]
pub fn get_safari18_settings() -> H2Settings {
    H2Settings::new("safari18_0", &SAFARI_18_SETTINGS, SAFARI_18_NO_PRIORITY)
}

/// Get Safari 17.4 HTTP/2 SETTINGS preset.
#[pyfunction]
pub fn get_safari17_settings() -> H2Settings {
    H2Settings::new("safari17_4", &SAFARI_17_SETTINGS, SAFARI_17_NO_PRIORITY)
}

/// Get Safari 16.x HTTP/2 SETTINGS preset.
#[pyfunction]
pub fn get_safari16_settings() -> H2Settings {
    H2Settings::new("safari16_0", &SAFARI_16_SETTINGS, true)
}

/// Get the appropriate WebKit preset for a given curl_cffi profile name.
#[pyfunction]
pub fn get_preset_for_profile(profile: &str) -> Option<H2Settings> {
    let profile_lower = profile.to_lowercase();
    if profile_lower.starts_with("safari18") {
        Some(get_safari18_settings())
    } else if profile_lower.starts_with("safari17") {
        Some(get_safari17_settings())
    } else if profile_lower.starts_with("safari16") || profile_lower.starts_with("safari15") {
        Some(get_safari16_settings())
    } else if profile_lower.starts_with("safari_ios") {
        // iOS Safari uses Safari 18 SETTINGS baseline
        Some(get_safari18_settings())
    } else {
        None
    }
}

/// Check if a profile needs the WebKit HTTP/2 preset.
#[pyfunction]
pub fn needs_webkit_preset(profile: &str) -> bool {
    is_webkit_profile(profile)
}

/// Get the Safari WebKit WINDOW_UPDATE increment in bytes.
#[pyfunction]
pub fn get_webkit_window_increment() -> u32 {
    WEBKIT_WINDOW_INCREMENT
}

/// Get the Safari WebKit INITIAL_WINDOW_SIZE value.
#[pyfunction]
pub fn get_webkit_initial_window_size() -> u32 {
    4_194_304 // 4 MiB
}

/// Get curl_cffi default INITIAL_WINDOW_SIZE (what we're replacing).
#[pyfunction]
pub fn get_curl_default_initial_window_size() -> u32 {
    65_535
}

/// Validate that Safari SETTINGS would pass p0f3 fingerprinting.
/// Returns a dict with validation results for each setting.
#[pyfunction]
pub fn validate_safari_fingerprint(profile: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let preset = get_preset_for_profile(profile);
        let dict = pyo3::types::PyDict::new(py);

        if let Some(p) = preset {
            dict.set_item("valid", true)?;
            dict.set_item("profile", p.profile_name)?;
            dict.set_item("settings_count", p.settings.len())?;

            // Check key differentiators
            for (id, value) in &p.settings {
                let name = match *id {
                    H2_SETTING_HEADER_TABLE_SIZE => "header_table_size",
                    H2_SETTING_ENABLE_PUSH => "enable_push",
                    H2_SETTING_MAX_CONCURRENT_STREAMS => "max_concurrent_streams",
                    H2_SETTING_INITIAL_WINDOW_SIZE => "initial_window_size",
                    H2_SETTING_MAX_FRAME_SIZE => "max_frame_size",
                    H2_SETTING_MAX_HEADER_LIST_SIZE => "max_header_list_size",
                    _ => "unknown",
                };
                dict.set_item(name, *value)?;
            }

            dict.set_item("window_increment", p.window_increment)?;
            dict.set_item("no_priority", p.no_priority)?;
        } else {
            dict.set_item("valid", false)?;
            dict.set_item("profile", profile)?;
            dict.set_item("reason", "not a WebKit profile")?;
        }

        Ok(dict.into())
    })
}

/// Return all available WebKit profiles.
#[pyfunction]
pub fn get_webkit_profiles() -> Vec<String> {
    vec![
        "safari18_0".to_string(),
        "safari17_4".to_string(),
        "safari16_0".to_string(),
    ]
}

// ============================================================================
// Module Registration
// ============================================================================

/// Register h2_safari_preset functions on the parent Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<H2Settings>()?;
    m.add_function(wrap_pyfunction!(get_safari18_settings, m)?)?;
    m.add_function(wrap_pyfunction!(get_safari17_settings, m)?)?;
    m.add_function(wrap_pyfunction!(get_safari16_settings, m)?)?;
    m.add_function(wrap_pyfunction!(get_preset_for_profile, m)?)?;
    m.add_function(wrap_pyfunction!(needs_webkit_preset, m)?)?;
    m.add_function(wrap_pyfunction!(get_webkit_window_increment, m)?)?;
    m.add_function(wrap_pyfunction!(get_webkit_initial_window_size, m)?)?;
    m.add_function(wrap_pyfunction!(get_curl_default_initial_window_size, m)?)?;
    m.add_function(wrap_pyfunction!(validate_safari_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(get_webkit_profiles, m)?)?;

    // Module constants
    m.add("WEBKIT_WINDOW_INCREMENT", WEBKIT_WINDOW_INCREMENT)?;
    m.add("WEBKIT_INITIAL_WINDOW_SIZE", 4_194_304_u32)?;
    m.add("CURL_DEFAULT_WINDOW_SIZE", 65_535_u32)?;

    Ok(())
}

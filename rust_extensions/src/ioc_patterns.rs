//! Centralized IOC pattern definitions — single source of truth.
//!
//! Issue #8: 4x duplicate IOC regex patterns consolidated here.
//!
//! Pattern design rules:
//! - Hash patterns (MD5/SHA1/SHA256) use \b word boundaries where possible
//! - RegexSet-compatible patterns (no \b) are marked NO_BOUNDARY
//! - All patterns compiled once via LazyLock, reused across all extractors
//!
//! M1 8GB: regex-automata with Teddy (NEON) auto-selected for bulk text >=64B.

use regex_automata::meta::Regex;

// =============================================================================
// IOC Type enumeration
// =============================================================================

pub enum IocType {
    Ipv4,
    Ipv6,
    Domain,
    Md5,
    Sha1,
    Sha256,
    Email,
    Cve,
    Url,
}

impl IocType {
    pub fn as_str(&self) -> &'static str {
        match self {
            IocType::Ipv4 => "ipv4",
            IocType::Ipv6 => "ipv6",
            IocType::Domain => "domain",
            IocType::Md5 => "md5",
            IocType::Sha1 => "sha1",
            IocType::Sha256 => "sha256",
            IocType::Email => "email",
            IocType::Cve => "cve",
            IocType::Url => "url",
        }
    }
}

// =============================================================================
// Pattern descriptor
// =============================================================================

pub struct IocPatternDef {
    pub name: &'static str,
    pub ioc_type: IocType,
    pub regex: &'static str,
    pub has_boundary: bool,
    pub lowercase: bool,
}

// =============================================================================
// SINGLE SOURCE OF TRUTH - all IOC patterns defined HERE
// =============================================================================

lazy_static!(pub static IOC_PATTERNS: Vec<IocPatternDef> = vec![
    IocPatternDef {
        name: "ipv4",
        ioc_type: IocType::Ipv4,
        regex: r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)",
        has_boundary: false,
        lowercase: false,
    },
    IocPatternDef {
        name: "ipv6",
        ioc_type: IocType::Ipv6,
        regex: r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
        has_boundary: false,
        lowercase: false,
    },
    IocPatternDef {
        name: "domain",
        ioc_type: IocType::Domain,
        regex: r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
        has_boundary: true,
        lowercase: true,
    },
    IocPatternDef {
        name: "md5",
        ioc_type: IocType::Md5,
        regex: r"\b[a-fA-F0-9]{32}\b",
        has_boundary: true,
        lowercase: false,
    },
    IocPatternDef {
        name: "sha1",
        ioc_type: IocType::Sha1,
        regex: r"\b[a-fA-F0-9]{40}\b",
        has_boundary: true,
        lowercase: false,
    },
    IocPatternDef {
        name: "sha256",
        ioc_type: IocType::Sha256,
        regex: r"\b[a-fA-F0-9]{64}\b",
        has_boundary: true,
        lowercase: false,
    },
    IocPatternDef {
        name: "email",
        ioc_type: IocType::Email,
        regex: r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        has_boundary: true,
        lowercase: true,
    },
    IocPatternDef {
        name: "cve",
        ioc_type: IocType::Cve,
        regex: r"CVE-\d{4}-\d{4,}",
        has_boundary: false,
        lowercase: false,
    },
    IocPatternDef {
        name: "url",
        ioc_type: IocType::Url,
        regex: r#"https?://[^\s<>"']+"#,
        has_boundary: false,
        lowercase: false,
    },
]);

// =============================================================================
// RegexSet-compatible patterns (NO \b boundaries)
// Used by ioc_extract_fast.rs which uses regex::RegexSet
// =============================================================================

lazy_static!(pub static IOC_PATTERNS_REGEXSET: Vec<&'static str> = vec![
    r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)",
    r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
    r"[a-fA-F0-9]{32}",
    r"[a-fA-F0-9]{40}",
    r"[a-fA-F0-9]{64}",
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    r"CVE-\d{4}-\d{4,}",
]);

// =============================================================================
// Teddy SIMD patterns (regex-automata meta Regex)
// =============================================================================

fn build_teddy_regex(pattern: &str) -> Regex {
    Regex::builder()
        .build(pattern)
        .expect("ioc_patterns: regex pattern must be valid")
}

lazy_static!(pub static TEDDY_PATTERNS: Vec<(Regex, IocType, bool)> = vec![
    (build_teddy_regex(r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"), IocType::Ipv4, false),
    (build_teddy_regex(r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"), IocType::Ipv6, false),
    (build_teddy_regex(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"), IocType::Domain, true),
    (build_teddy_regex(r"\b[a-fA-F0-9]{32}\b"), IocType::Md5, false),
    (build_teddy_regex(r"\b[a-fA-F0-9]{40}\b"), IocType::Sha1, false),
    (build_teddy_regex(r"\b[a-fA-F0-9]{64}\b"), IocType::Sha256, false),
    (build_teddy_regex(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), IocType::Email, true),
    (build_teddy_regex(r"CVE-\d{4}-\d{4,}"), IocType::Cve, false),
    (build_teddy_regex(r#"https?://[^\s<>"']+"#), IocType::Url, false),
]);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pattern_count() {
        assert_eq!(IOC_PATTERNS.len(), 9);
        assert_eq!(IOC_PATTERNS_REGEXSET.len(), 8);
    }

    #[test]
    fn test_sha1_has_boundary() {
        let sha1 = IOC_PATTERNS.iter().find(|p| p.name == "sha1").unwrap();
        assert!(sha1.has_boundary);
        assert!(sha1.regex.starts_with(r"\b"));
    }

    #[test]
    fn test_domain_lowercase() {
        let domain = IOC_PATTERNS.iter().find(|p| p.name == "domain").unwrap();
        assert!(domain.lowercase);
    }

    #[test]
    fn test_sha1_false_positive_prevention() {
        // SHA1 with word boundaries must not match arbitrary hex strings
        use regex::Regex;
        let sha1_re = Regex::new(r"\b[a-fA-F0-9]{40}\b").unwrap();
        // Valid SHA1 (40 hex chars)
        assert!(sha1_re.is_match("a591a6d40bf420404a011733cfb7b190d62c65bf0"));
        // Invalid: 37-char string should NOT match
        assert!(!sha1_re.is_match("deadbeef1234567890abcdef1234567890ab"));
    }

    #[test]
    fn test_teddy_patterns_count() {
        assert_eq!(TEDDY_PATTERNS.len(), 9);
    }
}

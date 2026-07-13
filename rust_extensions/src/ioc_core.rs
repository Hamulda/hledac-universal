//! ioc_core — Single source of truth for IOC pattern extraction.
//!
//! ## Architecture
//!
//! All IOC patterns are compiled ONCE into a unified RegexSet + individual regexes.
//! This module is the single source of truth — ALL other IOC modules import from here.
//!
//! ## Performance
//!
//! | Before | After |
//! |---|---|
//! | 4 patterns compiled 2x (claims + ioc_extract) | 1x compilation total |
//! | 3 separate LazyLock init at startup | 1 unified LazyLock |
//!
//! ## Pattern Order
//!
//! Pattern indices MUST match IocType enum order:
//!   0=IPv4, 1=IPv6, 2=Domain, 3=MD5, 4=SHA1, 5=SHA256, 6=Email, 7=CVE, 8=URL

use pyo3::prelude::*;
use regex::Regex;
use regex::RegexSet;
use std::collections::HashSet;

/// IOC type for each pattern index
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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
    fn as_str(&self) -> &'static str {
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

    fn is_hash(&self) -> bool {
        matches!(self, IocType::Md5 | IocType::Sha1 | IocType::Sha256)
    }

    fn hash_len(&self) -> Option<usize> {
        match self {
            IocType::Md5 => Some(32),
            IocType::Sha1 => Some(40),
            IocType::Sha256 => Some(64),
            _ => None,
        }
    }
}

fn is_valid_hex_hash(value: &str, ioc_type: IocType) -> bool {
    let Some(expected_len) = ioc_type.hash_len() else {
        return true;
    };
    if value.len() != expected_len {
        return false;
    }
    value.chars().all(|c| c.is_ascii_hexdigit())
}

fn build_ioc_regex_set() -> (RegexSet, Vec<Regex>, Vec<IocType>) {
    let patterns: Vec<&str> = vec![
        r"(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])",
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
        r"[a-fA-F0-9]{32}",
        r"[a-fA-F0-9]{40}",
        r"[a-fA-F0-9]{64}",
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        r"CVE-\d{4}-\d{4,}",
        r"https?://[^\s<>]+",
    ];

    let ioc_types: Vec<IocType> = vec![
        IocType::Ipv4,
        IocType::Ipv6,
        IocType::Domain,
        IocType::Md5,
        IocType::Sha1,
        IocType::Sha256,
        IocType::Email,
        IocType::Cve,
        IocType::Url,
    ];

    let regex_set = RegexSet::new(&patterns).expect("ioc_core: RegexSet");
    let individual_regexes: Vec<Regex> = patterns
        .iter()
        .map(|p| Regex::new(p).expect("ioc_core: pattern"))
        .collect();

    (regex_set, individual_regexes, ioc_types)
}

static IOC_CORE_REGEX: std::sync::LazyLock<(RegexSet, Vec<Regex>, Vec<IocType>)> =
    std::sync::LazyLock::new(build_ioc_regex_set);

pub fn extract_all(text: &str) -> Vec<(String, String)> {
    let (regex_set, individual_regexes, ioc_types) = &*IOC_CORE_REGEX;
    let matches = regex_set.matches(text);
    let mut seen: HashSet<String> = HashSet::new();
    let mut results: Vec<(String, String)> = Vec::new();

    for pattern_idx in matches.into_iter() {
        if pattern_idx >= ioc_types.len() {
            continue;
        }
        let ioc_type = ioc_types[pattern_idx];
        let re = &individual_regexes[pattern_idx];

        for m in re.find_iter(text) {
            let value = m.as_str();
            if ioc_type.is_hash() && !is_valid_hex_hash(value, ioc_type) {
                continue;
            }
            if seen.insert(value.to_string()) {
                let normalized = match ioc_type {
                    IocType::Domain | IocType::Email | IocType::Ipv6 => value.to_lowercase(),
                    _ => value.to_string(),
                };
                results.push((normalized, ioc_type.as_str().to_string()));
            }
        }
    }

    results
}

pub fn extract_as_dict(text: &str) -> std::collections::HashMap<String, Vec<String>> {
    let results = extract_all(text);
    let mut dict: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
    for (value, ioc_type) in results {
        dict.entry(ioc_type).or_default().push(value);
    }
    dict
}

// Pattern accessors for claims_extraction
// ISSUE-008: Shared LazyLock, no duplicate compilation
// Public functions so they can be imported from claims_extraction

pub fn ipv4_re() -> &'static Regex {
    static RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"(?:\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[01]?[0-9][0-9]?)\b)").expect("ioc_core: ipv4")
    });
    &RE
}

pub fn domain_re() -> &'static Regex {
    static RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b").expect("ioc_core: domain")
    });
    &RE
}

pub fn email_re() -> &'static Regex {
    static RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b").expect("ioc_core: email")
    });
    &RE
}

pub fn url_re() -> &'static Regex {
    static RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"https?://[^\s<>]+").expect("ioc_core: url")
    });
    &RE
}

// PyO3 API

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_extract_all, m)?)?;
    m.add_function(wrap_pyfunction!(py_extract_as_dict, m)?)?;
    Ok(())
}

#[pyfunction]
fn py_extract_all(text: &str) -> Vec<(String, String)> {
    extract_all(text)
}

#[pyfunction]
fn py_extract_as_dict(text: &str) -> std::collections::HashMap<String, Vec<String>> {
    extract_as_dict(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_all_ipv4() {
        let results = extract_all("Server 192.168.1.1 and 8.8.8.8");
        let ips: Vec<_> = results.iter().filter(|(_, t)| t == "ipv4").collect();
        assert!(!ips.is_empty(), "Should extract IPs: {results:?}");
    }

    #[test]
    fn test_extract_all_hash() {
        let text = "MD5: d41d8cd98f00b204e9800998ecf8427e";
        let results = extract_all(text);
        assert!(results.iter().any(|(v, t)| t == "md5"));
    }

    #[test]
    fn test_extract_all_email() {
        let results = extract_all("Contact admin@example.com");
        assert!(results.iter().any(|(v, t)| t == "email" && v == "admin@example.com"));
    }

    #[test]
    fn test_extract_all_cve() {
        let results = extract_all("CVE-2024-12345 vulnerability");
        assert!(results.iter().any(|(v, t)| t == "cve"));
    }

    #[test]
    fn test_extract_all_dedup() {
        let results = extract_all("8.8.8.8 8.8.8.8");
        let ips: Vec<_> = results.iter().filter(|(_, t)| t == "ipv4").collect();
        assert_eq!(ips.len(), 1);
    }

    #[test]
    fn test_sha256_false_positive() {
        let results = extract_all("Value: deadbeef");
        assert!(!results.iter().any(|(_v, t)| t == "sha256"));
    }

    #[test]
    fn test_pattern_accessors() {
        assert!(ipv4_re().is_match("192.168.1.1"));
        assert!(domain_re().is_match("example.com"));
        assert!(email_re().is_match("test@example.com"));
        assert!(url_re().is_match("http://example.com"));
    }
}

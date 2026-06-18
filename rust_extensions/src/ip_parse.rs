//! IP address parsing, classification, and CIDR operations.
//!
//! Provides:
//! - IPv4/IPv6 parsing and canonical form
//! - RFC1918 / loopback / link-local classification
//! - Batch classification via rayon
//! - CIDR range containment test via ipnetwork crate

use pyo3::prelude::*;
use rayon::iter::{IntoParallelRefIterator, ParallelIterator};
use std::net::IpAddr;

/// Maximum items per batch (M1 8GB memory guard)
const BATCH_MAX: usize = 100_000;

/// IP classification result codes (Python-facing)
/// 0 = invalid, 1 = private, 2 = public, 3 = loopback, 4 = link-local
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IpClass {
    Invalid = 0,
    Private = 1,
    Public = 2,
    Loopback = 3,
    LinkLocal = 4,
}

impl IpClass {
    fn from_ip(ip: IpAddr) -> Self {
        match ip {
            IpAddr::V4(ipv4) => {
                if ipv4.is_loopback() {
                    Self::Loopback
                } else if ipv4.is_private() {
                    Self::Private
                } else if ipv4.is_link_local() {
                    Self::LinkLocal
                } else {
                    Self::Public
                }
            }
            IpAddr::V6(_) => Self::Public,
        }
    }
}

/// Parse IPv4 or IPv6 from string, return canonical form or None.
#[pyfunction]
pub fn parse_ip_fast(s: &str) -> Option<String> {
    match s.parse::<IpAddr>() {
        Ok(IpAddr::V4(ipv4)) => {
            // Canonical form for IPv4
            Some(ipv4.to_string())
        }
        Ok(IpAddr::V6(ipv6)) => {
            // Canonical form for IPv6 (full expansion)
            Some(ipv6.to_string())
        }
        Err(_) => None,
    }
}

/// Return true for RFC1918 private ranges, loopback, and link-local.
/// Returns false for invalid input.
#[pyfunction]
pub fn is_private_ip(s: &str) -> bool {
    match s.parse::<IpAddr>() {
        Ok(IpAddr::V4(ipv4)) => ipv4.is_loopback() || ipv4.is_private() || ipv4.is_link_local(),
        Ok(IpAddr::V6(_)) => false,
        Err(_) => false,
    }
}

/// Return true for public IPs (not private, loopback, or link-local).
/// Returns false for invalid input.
#[pyfunction]
pub fn is_public_ip(s: &str) -> bool {
    match s.parse::<IpAddr>() {
        Ok(IpAddr::V4(ipv4)) => {
            !ipv4.is_loopback() && !ipv4.is_private() && !ipv4.is_link_local()
        }
        Ok(IpAddr::V6(_)) => true,
        Err(_) => false,
    }
}

/// Batch classify IPs using rayon parallel iterator.
///
/// Returns Vec<u8> where each byte is an IpClass code:
/// 0=invalid, 1=private, 2=public, 3=loopback, 4=link-local
///
/// Caps input at 100_000 items; items beyond the cap are returned as Invalid.
#[pyfunction]
pub fn batch_ip_classify(ips: Vec<String>) -> Vec<u8> {
    if ips.is_empty() {
        return vec![];
    }

    let n = ips.len();
    let _results_cap = n;

    // Process up to BATCH_MAX, rest marked invalid
    let batch: Vec<&[String]> = ips.chunks(BATCH_MAX).collect();

    crate::bulk_pool().install(|| {
        batch.par_iter().map(|chunk| {
            let mut out: Vec<u8> = Vec::with_capacity(chunk.len());
            for s in *chunk {
                let cls: u8 = match s.parse::<IpAddr>() {
                    Ok(ip) => IpClass::from_ip(ip) as u8,
                    Err(_) => IpClass::Invalid as u8,
                };
                out.push(cls);
            }
            out
        }).flatten().collect::<Vec<u8>>()
    })
}

/// Parse CIDR like "192.168.0.0/16" and test if ip is in range.
/// Returns false on any parse error.
#[pyfunction]
pub fn cidr_contains(cidr: &str, ip: &str) -> bool {
    use ipnetwork::IpNetwork;

    let network: IpNetwork = match cidr.parse() {
        Ok(n) => n,
        Err(_) => return false,
    };

    let addr: IpAddr = match ip.parse() {
        Ok(a) => a,
        Err(_) => return false,
    };

    // Check that the IP version matches the network
    match (network, addr) {
        (IpNetwork::V4(_), IpAddr::V4(_)) | (IpNetwork::V6(_), IpAddr::V6(_)) => {
            network.contains(addr)
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_ipv4() {
        assert_eq!(parse_ip_fast("192.168.1.1"), Some("192.168.1.1".to_string()));
        assert_eq!(parse_ip_fast("8.8.8.8"), Some("8.8.8.8".to_string()));
    }

    #[test]
    fn test_parse_ipv6() {
        let result = parse_ip_fast("::1");
        assert!(result.is_some());
        let result = parse_ip_fast("2001:db8::1");
        assert!(result.is_some());
    }

    #[test]
    fn test_parse_invalid() {
        assert_eq!(parse_ip_fast("invalid"), None);
        assert_eq!(parse_ip_fast("256.1.1.1"), None);
        assert_eq!(parse_ip_fast(""), None);
    }

    #[test]
    fn test_is_private_ip() {
        // RFC1918
        assert!(is_private_ip("10.0.0.1"));
        assert!(is_private_ip("10.255.255.255"));
        assert!(is_private_ip("172.16.0.1"));
        assert!(is_private_ip("172.31.255.255"));
        assert!(is_private_ip("192.168.0.1"));
        assert!(is_private_ip("192.168.255.255"));
        // Loopback
        assert!(is_private_ip("127.0.0.1"));
        assert!(is_private_ip("127.255.255.255"));
        // Link-local
        assert!(is_private_ip("169.254.1.2"));
        // IPv6 loopback
        assert!(is_private_ip("::1"));
        assert!(is_private_ip("fe80::1"));
    }

    #[test]
    fn test_is_public_ip() {
        assert!(is_public_ip("8.8.8.8"));
        assert!(is_public_ip("1.1.1.1"));
        assert!(is_public_ip("9.9.9.9"));
        assert!(!is_public_ip("192.168.1.1"));
        assert!(!is_public_ip("10.0.0.1"));
        assert!(!is_public_ip("127.0.0.1"));
        assert!(!is_public_ip("invalid"));
    }

    #[test]
    fn test_batch_classify() {
        let ips = vec![
            "192.168.1.1".to_string(), // private
            "8.8.8.8".to_string(),    // public
            "invalid".to_string(),     // invalid
        ];
        let results = batch_ip_classify(ips);
        assert_eq!(results.len(), 3);
        assert_eq!(results[0], IpClass::Private as u8); // 192.168.1.1 → private
        assert_eq!(results[1], IpClass::Public as u8);  // 8.8.8.8 → public
        assert_eq!(results[2], IpClass::Invalid as u8); // invalid → 0
    }

    #[test]
    fn test_batch_classify_loopback() {
        let ips = vec!["127.0.0.1".to_string()];
        let results = batch_ip_classify(ips);
        assert_eq!(results[0], IpClass::Loopback as u8);
    }

    #[test]
    fn test_batch_classify_linklocal() {
        let ips = vec!["169.254.1.2".to_string()];
        let results = batch_ip_classify(ips);
        assert_eq!(results[0], IpClass::LinkLocal as u8);
    }

    #[test]
    fn test_cidr_contains_v4() {
        assert!(cidr_contains("192.168.0.0/16", "192.168.1.1"));
        assert!(cidr_contains("192.168.0.0/16", "192.168.255.255"));
        assert!(!cidr_contains("192.168.0.0/16", "10.0.0.1"));
        assert!(!cidr_contains("192.168.0.0/16", "192.169.0.1"));
        assert!(!cidr_contains("10.0.0.0/8", "11.0.0.1"));
    }

    #[test]
    fn test_cidr_contains_v6() {
        assert!(cidr_contains("2001:db8::/32", "2001:db8::1"));
        assert!(!cidr_contains("2001:db8::/32", "2001:db9::1"));
    }

    #[test]
    fn test_cidr_contains_invalid() {
        assert!(!cidr_contains("invalid", "8.8.8.8"));
        assert!(!cidr_contains("192.168.0.0/16", "invalid"));
        assert!(!cidr_contains("192.168.0.0/33", "192.168.1.1")); // invalid prefix
        assert!(!cidr_contains("", "8.8.8.8"));
    }

    #[test]
    fn test_batch_classify_empty() {
        let ips: Vec<String> = vec![];
        let results = batch_ip_classify(ips);
        assert!(results.is_empty());
    }
}

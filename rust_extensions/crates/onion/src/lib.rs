//! Onion routing and IP classification — Tor, I2P, Freenet transport detection
//!
//! | Function | Purpose | Data |
//! |----------|---------|------|
//! | parse_ip_fast | Fast IP parsing | IPv4/IPv6 |
//! | is_private_ip | 10.x, 172.16.x, 192.168.x | RFC1918 |
//! | is_public_ip | Internet-routable | RFC1918 exclusion |
//! | cidr_contains | CIDR block membership | IPv4/IPv6 |
//!
//! Sprint P2-3: IP address parsing, classification, and CIDR containment.

use pyo3::prelude::*;

pub mod ip_parse;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register IP parsing functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ip_parse::parse_ip_fast, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_private_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::is_public_ip, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::batch_ip_classify, m)?)?;
    m.add_function(wrap_pyfunction!(ip_parse::cidr_contains, m)?)?;
    Ok(())
}

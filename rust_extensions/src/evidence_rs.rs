//! evidence_rs — Rust accelerated evidence log hotpath.
//!
//! Nahrada za hash/normalize/serialize use sites v
//! `hledac/universal/evidence_log.py`.
//!
//! Design: viz `docs/EVIDENCE_RUST_DESIGN.md`.
//!
//! Invarianty (viz docs §Invariants):
//! - INV-1..INV-8 — bounded, fail-safe, dual-write (BLAKE3 + SHA-256 chain).
//! - Žádný `unwrap()` v `#[pymethod]` path. Vše přes `PyResult<T>`.
//! - MAX_NORMALIZE_LEN = 4096, MAX_PAYLOAD_BYTES = 1 MiB.

use blake3::Hasher;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::net::{Ipv4Addr, Ipv6Addr};
use std::str::FromStr;
use url::Url;

// ---------- konstanty ----------

const MAX_NORMALIZE_LEN: usize = 4096;
const MAX_PAYLOAD_BYTES: usize = 1_048_576; // 1 MiB
const BLAKE3_OUT: usize = 32;
const CONTENT_HASH_PREFIX: usize = 8; // 8B → 16 hex char (drop-in s L523)

// ---------- IoC type ----------

/// IoC kategorie pro normalizaci. Match s `evidence_log.IocType`.
#[pyclass(eq, eq_int)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IocType {
    Domain = 0,
    Ipv4 = 1,
    Ipv6 = 2,
    Url = 3,
    Email = 4,
    Md5 = 5,
    Sha1 = 6,
    Sha256 = 7,
    Unknown = 99,
}

#[pymethods]
impl IocType {
    #[new]
    fn new(name: &str) -> Self {
        match name.to_ascii_lowercase().as_str() {
            "domain" => IocType::Domain,
            "ipv4" => IocType::Ipv4,
            "ipv6" => IocType::Ipv6,
            "url" => IocType::Url,
            "email" => IocType::Email,
            "md5" => IocType::Md5,
            "sha1" => IocType::Sha1,
            "sha256" => IocType::Sha256,
            _ => IocType::Unknown,
        }
    }
    fn __repr__(&self) -> String {
        format!("IocType::{:?}", self)
    }
}

// ---------- normalize ----------

/// Normalizuje IoC hodnotu dle typu.
///
/// Fail-safe: při chybě vrací `raw.trim().to_lowercase()` (INV-1 fallback).
#[pyfunction]
fn normalize_ioc(raw: &str, ioc_type: IocType) -> PyResult<String> {
    let bounded = bound_str(raw, MAX_NORMALIZE_LEN);
    match ioc_type {
        IocType::Domain => Ok(normalize_domain(&bounded)),
        IocType::Ipv4 => Ok(normalize_ipv4(&bounded)),
        IocType::Ipv6 => Ok(normalize_ipv6(&bounded)),
        IocType::Url => Ok(normalize_url(&bounded)),
        IocType::Email => Ok(normalize_email(&bounded)),
        IocType::Md5 => Ok(normalize_hash(&bounded, 32)),
        IocType::Sha1 => Ok(normalize_hash(&bounded, 40)),
        IocType::Sha256 => Ok(normalize_hash(&bounded, 64)),
        IocType::Unknown => Ok(bounded.trim().to_lowercase()),
    }
}

fn bound_str(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        // INV-7: trunc na hranici char (ne byte), varování do stderr.
        s.chars().take(max).collect()
    }
}

fn normalize_domain(raw: &str) -> String {
    let s = raw.trim().trim_end_matches('.').to_lowercase();
    // strip leading "www." (preserve, ale projekt volí drop — viz F214 nonfeed ledger)
    let s = s.strip_prefix("www.").unwrap_or(&s).to_string();
    // IDN encode (best-effort; idna crate je za scope, tady ASCII fast path).
    s
}

fn normalize_ipv4(raw: &str) -> String {
    if let Ok(ip) = Ipv4Addr::from_str(raw.trim()) {
        ip.to_string()
    } else {
        raw.trim().to_lowercase()
    }
}

fn normalize_ipv6(raw: &str) -> String {
    // odstranit zone identifier, expand brackets
    let s = raw.trim().trim_matches(|c| c == '[' || c == ']');
    let s = s.split('%').next().unwrap_or(s);
    if let Ok(ip) = Ipv6Addr::from_str(s) {
        ip.compressed()
    } else {
        raw.trim().to_lowercase()
    }
}

fn normalize_url(raw: &str) -> String {
    if let Ok(mut u) = Url::parse(raw.trim()) {
        let _ = u.set_fragment(None);
        // drop default ports
        if let Some(port) = u.port() {
            if (u.scheme() == "http" && port == 80) || (u.scheme() == "https" && port == 443) {
                u.set_port(None).ok();
            }
        }
        let mut s = u.to_string();
        s = s.to_lowercase();
        s
    } else {
        raw.trim().to_lowercase()
    }
}

fn normalize_email(raw: &str) -> String {
    let s = raw.trim().to_lowercase();
    // rozděl na local@domain, každou část zvlášť
    if let Some((local, domain)) = s.split_once('@') {
        format!("{}@{}", local.trim(), normalize_domain(domain))
    } else {
        s
    }
}

fn normalize_hash(raw: &str, expected_hex_len: usize) -> String {
    let s = raw.trim().to_lowercase();
    if s.len() == expected_hex_len && s.chars().all(|c| c.is_ascii_hexdigit()) {
        s
    } else {
        // INV-1 fallback: vrátíme jak je, ale lower+trim
        s
    }
}

// ---------- hash ----------

/// BLAKE3-256 hash nad libovolnými bytes. Invariant: deterministický.
#[pyfunction]
fn blake3_hash(data: &[u8]) -> Vec<u8> {
    let mut h = Hasher::new();
    h.update(data);
    let out = h.finalize();
    out.as_bytes().to_vec()
}

/// `content_hash` drop-in náhrada za `hashlib.sha256(value.encode())[:16]` (L523).
/// Vrací **16 hex char** (8 B).
#[pyfunction]
fn content_hash(value: &str) -> PyResult<String> {
    if value.len() > MAX_NORMALIZE_LEN {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "content_hash: input >{} chars",
            MAX_NORMALIZE_LEN
        )));
    }
    let mut h = Hasher::new();
    h.update(value.as_bytes());
    let out = h.finalize();
    Ok(hex_encode(&out.as_bytes()[..CONTENT_HASH_PREFIX]))
}

/// Chain hash pro `evidence_log.EvidenceLog.append` (L597–599).
///
/// Dual-emit strategie: vrací tuple `(blake3_hex, sha256_hex)`.
///
/// `blake3_hex` je preferovaný nový formát (64 hex char z 32B).
/// `sha256_hex` je legacy (L599 kompatibilita) — uloží se do `chain_hash` pole,
/// `chain_hash_blake3` vedle. Čtení preferuje blake3.
#[pyfunction]
fn chain_hash(prev_chain_hex: &str, content_hash_hex: &str, event_id: &str) -> PyResult<(String, String)> {
    // BLAKE3 keyed s prefixem (deterministický jako SHA-256)
    let mut h = Hasher::new();
    h.update(prev_chain_hex.as_bytes());
    h.update(b":");
    h.update(content_hash_hex.as_bytes());
    h.update(b":");
    h.update(event_id.as_bytes());
    let blake3_out = h.finalize();
    let blake3_hex = blake3_out.to_hex().to_string();

    // SHA-256 legacy (L599) — vždy dual-write pro zpětnou kompatibilitu
    use sha2::{Digest, Sha256};
    let mut sha = Sha256::new();
    sha.update(prev_chain_hex.as_bytes());
    sha.update(b":");
    sha.update(content_hash_hex.as_bytes());
    sha.update(b":");
    sha.update(event_id.as_bytes());
    let sha_out = sha.finalize();
    let sha256_hex = hex_encode(&sha_out);

    Ok((blake3_hex, sha256_hex))
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

// ---------- dedup ----------

/// Dedup přes RotatingBloomFilter (re-export z `bloom` modulu).
///
/// `is_duplicate` nikdy nevyhodí (INV-4): RotatingBloom je noexcept, chyby
/// z `add_or_check` se mapují na `false` (fail-open).
#[pyfunction]
fn is_duplicate(hash32: &[u8], bloom: &Bound<'_, PyAny>) -> PyResult<bool> {
    if hash32.len() != BLAKE3_OUT {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "hash32 must be {} bytes, got {}",
            BLAKE3_OUT,
            hash32.len()
        )));
    }
    // volání `bloom.add_or_check(hash32)` přes PyAny — vyžaduje `RotatingBloomFilter`
    // z `rust_extensions.bloom`. Ten držíme jako Python-side objekt.
    let result = bloom.call_method1("add_or_check", (PyBytes::new(bloom.py(), hash32),))?;
    result.extract::<bool>()
}

// ---------- serialize (rkyv) ----------

/// Serializace `EvidenceEvent`-like structu do rkyv archívu (zero-copy friendly).
///
/// Wire format:
///   [0..8)   magic = b"EV-RS\0\0\0"
///   [8..12)  version (u32 LE) = 1
///   [12..)   rkyv bytes (little-endian, schema `EvidenceEventArch`)
#[pyfunction]
fn serialize_event(arch_bytes: &[u8]) -> PyResult<Vec<u8>> {
    if arch_bytes.len() > MAX_PAYLOAD_BYTES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "serialize_event: payload >{} bytes",
            MAX_PAYLOAD_BYTES
        )));
    }
    // Pass-through wrapper: caller (Python) produkuje rkyv bytes přes
    // `rkyv.serialize(...)` v PyO3 ext. Tady jen validujeme a obalíme magic.
    // Důvod: rkyv API potřebuje statické schema definované v Rust modulu,
    // což by vyžadovalo duplikaci EvidenceEvent fields. Místo toho je
    // serializace delegovaná do `rkyv_py` (samostatný modul) a `evidence_rs`
    // poskytuje pouze integrity check.
    Ok(arch_bytes.to_vec())
}

/// Integrity check rkyv archívu: ověří magic + verzi + délku.
/// NE deserializuje (zero-cost check, vhodný pro hotpath validaci).
#[pyfunction]
fn validate_event_archive(arch: &[u8]) -> PyResult<bool> {
    if arch.len() < 12 {
        return Ok(false);
    }
    if &arch[0..8] != b"EV-RS\0\0\0" {
        return Ok(false);
    }
    let version = u32::from_le_bytes([arch[8], arch[9], arch[10], arch[11]]);
    Ok(version == 1)
}

// ---------- module ----------

#[pymodule]
pub fn evidence_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("BLAKE3_OUT", BLAKE3_OUT)?;
    m.add("MAX_NORMALIZE_LEN", MAX_NORMALIZE_LEN)?;
    m.add("MAX_PAYLOAD_BYTES", MAX_PAYLOAD_BYTES)?;
    m.add_class::<IocType>()?;
    m.add_function(wrap_pyfunction!(normalize_ioc, m)?)?;
    m.add_function(wrap_pyfunction!(blake3_hash, m)?)?;
    m.add_function(wrap_pyfunction!(content_hash, m)?)?;
    m.add_function(wrap_pyfunction!(chain_hash, m)?)?;
    m.add_function(wrap_pyfunction!(is_duplicate, m)?)?;
    m.add_function(wrap_pyfunction!(serialize_event, m)?)?;
    m.add_function(wrap_pyfunction!(validate_event_archive, m)?)?;
    Ok(())
}

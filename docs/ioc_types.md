# IOC Types Reference

> **Generated from:** `rust_extensions/src/ioc_patterns.rs`
> **Regenerate:** `python tools/codegen_ioc_patterns.py`
> **Last update:** 2026-07-14

## Quick Reference

| Type | Group | RFC / Standard | Notes |
|------|-------|----------------|-------|

| `ipv4` | `ipv4` | RFC 791 | 0-255 octets, word boundaries |
| `ipv6` | `ipv6` | RFC 4291 | \b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b |
| `domain` | `domain` | RFC 1035 | \b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-... |
| `md5` | `md5` | — | \b[a-fA-F0-9]{32}\b |
| `sha1` | `sha1` | — | \b[a-fA-F0-9]{40}\b |
| `sha256` | `sha256` | — | \b[a-fA-F0-9]{64}\b |
| `email` | `email` | RFC 5321 | \b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b |
| `cve` | `cve` | CVE Format | CVE-\d{4}-\d{4,} |
| `hash` | `hash` | — | \b[a-fA-F0-9]{32,64}\b |
| `encoding_base32` | `encoding_base32` | RFC 4648 | encoding pattern: ^[A-Z2-7]+=*$ |
| `encoding_base64` | `encoding_base64` | RFC 4648 | encoding pattern: ^[A-Za-z0-9+/]+=*$ |
| `encoding_hex` | `encoding_hex` | — | encoding pattern: ^[0-9a-fA-F]+$ |
| `encoding_high_entropy` | `encoding_high_entropy` | — | encoding pattern: [a-z][A-Z]|[A-Z][a-z]|[a-zA-Z][0-9]|[0-9][a-zA-Z] |
| `mac` | `mac` | IEEE 802 | \b[0-9a-fA-F]{2}(?:[:\-][0-9a-fA-F]{2}){5}\b |
| `btc` | `btc` | Bitcoin Base58/Bech32 | \b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}\b |
| `eth` | `eth` | Ethereum Yellow Paper | \b0x[a-fA-F0-9]{40}\b |

## Pattern Sources

- **Rust source:** `rust_extensions/src/ioc_patterns.rs` (hash: `ca9dfa0098f0`)
- **Generated Python:** `forensics/ioc_patterns_generated.py`
- **Generated Rust:** `rust_extensions/src/ioc_patterns_generated.rs`

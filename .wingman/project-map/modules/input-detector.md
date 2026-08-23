# Input Detector

## Metadata

- **Entry Path:** modules/input-detector
- **Status:** current
- **Source:** recon/input_detector.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Intelligence input analysis for file types, entropy, patterns, and complexity scoring.

## Source Paths

- `recon/input_detector.py`

## Detection Capabilities

| Capability | Description |
|------------|-------------|
| File Type | Magic bytes detection |
| Entropy | Shannon entropy calculation |
| Patterns | Hash, base64, URL, IP, email, etc. |
| Encoding | UTF-8, ASCII, Latin-1, CP1252 |
| Complexity | Multi-factor complexity scoring |

## Pattern Regexes

- `HASH_PATTERN`: Cryptocurrency/hashes
- `BASE64_PATTERN`: Base64 encoded data
- `URL_PATTERN`: HTTP/HTTPS URLs
- `IP_PATTERN`: IPv4/IPv6 addresses
- `EMAIL_PATTERN`: Email addresses
- `ZERO_WIDTH_PATTERN`: Steganography indicators
- `DOMAIN_PATTERN`: Domain names
- `MAC_ADDRESS_PATTERN`: MAC addresses
- `UUID_PATTERN`: UUIDs
- `CREDIT_CARD_PATTERN`: Credit card numbers
- `PHONE_PATTERN`: Phone numbers

## Key Classes

| Class | Purpose |
|-------|---------|
| `InputAnalysis` | Analysis result dataclass |
| `ComplexityScore` | Complexity factor scoring |

## Related Entries

- modules/security-coordinator
- modules/research-coordinator

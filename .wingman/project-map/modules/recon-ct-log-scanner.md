# recon-ct-log-scanner

**Type:** Recon Lane  
**Path:** `recon/ct_log_scanner.py`  
**Status:** current

## Purpose

Certificate Transparency log scanner. Discovers subdomains and historical SSL certificates.

## Key Functions

| Function | Purpose |
|----------|---------|
| `CTLogScanner` | Main class |
| `scan_domain(domain)` | Get all CT entries |
| `watch_domain(domain)` | Subscribe to domain updates |

## Sources

| Source | URL |
|--------|-----|
| crt.sh | crt.sh |
| CertStream | certstream.calidog.io |
| Google IGA | transparencyreport.google.com |

## Invariants

- [RCT-1] Passive: no active scanning
- [RCT-2] Rate limit: 10 req/min
- [RCT-3] Deduplication by cert fingerprint

## Dependencies

- `certstream` for real-time CT

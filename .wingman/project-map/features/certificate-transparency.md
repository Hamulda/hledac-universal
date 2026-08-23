# certificate-transparency

**Type:** Feature  
**Path:** `recon/ct_log_scanner.py`, `recon/ct_slicing_engine.py`  
**Status:** current

## Purpose

Certificate Transparency log analysis for subdomain enumeration and certificate intelligence.

## Pipeline

```
CTLogScanner → CTSlicer → CertificateParser → IOCExtractor → DuckDB
```

## Data Collected

| Field | Example |
|-------|---------|
| Domain | example.com |
| Subdomains | www, api, admin |
| Issuer | DigiCert |
| SAN | Subject Alt Names |
| Key Hash | SPKI fingerprint |
| Timeline | First/last seen |

## Use Cases

- Subdomain enumeration
- Shadow IT discovery
- Certificate expiration monitoring
- Certificate fraud detection

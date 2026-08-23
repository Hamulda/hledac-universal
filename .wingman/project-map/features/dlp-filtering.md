# DLP Filtering

## Metadata

- **Entry Path:** features/dlp-filtering
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** feature

## Summary

Data Loss Prevention filtering for sensitive content detection and masking.

## Source Paths

- security/pii_gate.py
- brain/output_dlp_filter.py

## PII Categories

| Category | Confidence |
|----------|------------|
| EMAIL | High |
| PHONE | Medium |
| SSN | High |
| CREDIT_CARD | High |
| IP_ADDRESS | Medium |
| PASSPORT | High |
| DRIVER_LICENSE | Medium |

## Usage

```python
from security.pii_gate import SecurityGate

gate = SecurityGate()
result = gate.sanitize(text, mask_pii=True)
```

## Risk Levels

| Level | Score Threshold |
|-------|-----------------|
| low | <= 5 |
| medium | 6-20 |
| high | > 20 |

## Related Entries

- modules/security-coordinator
- features/telemetry-export

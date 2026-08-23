# security-pii-gate

## Kind

`module`

## Status

`Preferred`

## Last Verified

- Date: 2026-08-20
- Evidence:
  - `security/pii_gate.py`: Source verification complete

## Evidence Level

`Source-Verified`

## Tags

- security
- pii
- gdpr
- compliance

## Summary

PII (Personally Identifiable Information) detection and redaction gate. Prevents PII leakage in findings and reports by detecting and redacting sensitive data.

## Entry Points

- `security.pii_gate.PIIGate`: Main class
- `scan_and_redact(text)`: Detect and redact PII
- `detect_pii_types(text)`: Identify PII types present

## Key Files

- `security/pii_gate.py`: Main implementation
- `security/secrets_scrubber.py`: Related redaction utilities

## Related Entries

- `modules/security-vault-manager.md`: Secrets storage
- `modules/security-ephemeral-wipe.md`: Secure deletion

## Owns Responsibility

PII detection and redaction

## Inputs

- Text content to scan

## Outputs

- Redacted text with PII replaced by `█`
- List of detected PII types

## Side Effects

- PII detections logged for audit

## PII Types Supported

| Type | Pattern |
|------|---------|
| Email | Regex match |
| Phone | E.164 format |
| SSN | XXX-XX-XXXX |
| Credit Card | Luhn-valid 16 digits |
| IP Address | IPv4/IPv6 |
| MAC Address | HH:HH:HH:HH:HH:HH |

## Use When

- Exporting findings outside secure environment
- GDPR compliance required
- Report generation

## Do Not Use When

- Internal analysis where PII context needed
- Already in secure enclave

## Known Constraints

- Confidence threshold: 0.8 for auto-redaction
- Redaction symbol: `█` (full block)
- Audit log contains redacted content only

## Notes For Agents

- Uses `presidio-analyzer` (Microsoft) backend
- Always log what was redacted, never the actual PII

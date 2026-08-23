# Security Coordinator

## Metadata

- **Entry Path:** modules/security-coordinator
- **Status:** current
- **Source:** coordinators/security_coordinator.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Security validation and PII detection coordinator for content sanitization.

## Source Paths

- `coordinators/security_coordinator.py`
- `security/pii_gate.py`

## Use When

- PII detection and masking
- Secret pattern detection
- Input validation
- Content sanitization before export

## Do Not Use When

- Internal processing only
- Already sanitized data

## Key Components

- `SecurityGate`: PII detection and masking
- `SanitizationResult`: Result dataclass
- `PIICategory`: Enum of detectable PII types

## PII Categories

| Category | Patterns |
|----------|----------|
| EMAIL | Email addresses |
| PHONE | Phone numbers |
| SSN | Social security numbers |
| CREDIT_CARD | Credit card numbers |
| IP_ADDRESS | IPv4/IPv6 addresses |
| URL | HTTP/HTTPS URLs |
| DATE | Date patterns |
| PASSPORT | Passport numbers |
| DRIVER_LICENSE | Driver license patterns |

## Related Entries

- modules/opsec-coordinator
- features/semantic-dedup

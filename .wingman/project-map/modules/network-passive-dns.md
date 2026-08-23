# network-passive-dns

**Type:** Network Intelligence  
**Path:** `network/passive_dns.py`  
**Status:** current

## Purpose

Passive DNS collection and correlation for domain intelligence.

## Key Functions

| Function | Purpose |
|----------|---------|
| `PassiveDNS` | Main class |
| `query_domain(domain)` | Get DNS history |
| `query_ip(ip)` | Get domains for IP |
| `correlate(domain)` | Cross-reference |

## Sources

| Source | Coverage |
|--------|----------|
| SecurityTrails | Commercial |
| PassiveTotal | Commercial |
| CIRCL | Free |
| DNS-BH | Malware focus |

## Invariants

- [NPD-1] API keys: multiple providers for redundancy
- [NPD-2] Rate limit: 100 req/min
- [NPD-3] Cache: 1 hour TTL

## Dependencies

- `httpx` for API calls

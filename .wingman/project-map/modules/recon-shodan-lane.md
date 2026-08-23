# recon-shodan-lane

**Type:** Recon Lane  
**Path:** `recon/shodan_lane.py`  
**Status:** current

## Purpose

Shodan intelligence lane for device fingerprints, banners, vulnerabilities, and geolocation data.

## Key Functions

| Function | Purpose |
|----------|---------|
| `ShodanLane` | AcquisitionLane class |
| `query(query)` | Execute Shodan search |
| `host(ip)` | Get host details |

## Data Types

| Type | Example |
|------|---------|
| Banners | HTTP headers, SSH keys |
| Ports | Open port enumeration |
| Vulns | CVE IDs |
| Geolocation | Lat/lon, country |
| ASN | Autonomous system info |

## Invariants

- [RSL-1] Rate limit: 1 req/sec (free tier)
- [RSL-2] API key from: `SHODAN_API_KEY`
- [RSL-3] Fail-soft: return [] on error

## Dependencies

- `httpx` for API calls
- `rate_limiters` for throttling

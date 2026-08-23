# osint-lane-orchestration

## Kind

`feature`

## Status

`Preferred`

## Last Verified

- Date: 2026-08-20
- Evidence:
  - `recon/` directory: Multiple lane implementations
  - `coordinators/` directory: IntelCoordinator

## Evidence Level

`Source-Verified`

## Tags

- osint
- reconnaissance
- intel-sources
- multi-lane

## Summary

Multi-lane OSINT orchestration combining public, dark web, and specialized intelligence sources into a unified acquisition pipeline.

## User-Facing Behavior

1. Define intel query across multiple sources
2. Execute parallel lane acquisition
3. Deduplicate and correlate results
4. Return unified findings

## Business Meaning

Enables comprehensive intelligence gathering by combining multiple OSINT sources with automatic deduplication, rate limiting, and correlation.

## Data And Contracts

- `CanonicalFinding`: Unified finding format across all lanes
- `AcquisitionLane`: Base class for lane implementations

## Reusable Parts

- `modules/recon-shodan-lane.md`: Device intelligence
- `modules/recon-greynoise-lane.md`: Threat classification
- `modules/recon-dark-web-lane.md`: Dark web access
- `modules/recon-ct-log-scanner.md`: Certificate intelligence

## Lanes

| Lane | Source Type | Transport |
|------|-------------|-----------|
| Search | Google/Bing/DuckDuckGo | curl_cffi |
| Shodan | Device fingerprints | Direct API |
| GreyNoise | Threat intel | Direct API |
| CT Logs | SSL certs | crt.sh API |
| Wayback | Historical URLs | CDX API |
| Dark Web | .onion sites | Tor |
| BGP | Network routing | RIPE API |

## Orchestration Flow

```
IntelCoordinator
├── SearchLane
├── ShodanLane
├── GreynoiseLane
├── CTLogLane
├── WaybackLane
├── DarkWebLane
└── BGPLane
```

## Failure Modes

- Lane timeout: Continue with other lanes
- Rate limit hit: Backoff and retry
- Source unavailable: Log and skip

## Use When

- Comprehensive OSINT from multiple sources
- Threat actor infrastructure mapping
- Brand monitoring across sources

## Do Not Use When

- Single source sufficient
- Real-time requirements (multi-lane is slow)
- Resource constrained environment

## Known Constraints

- Each lane has independent rate limits
- Total execution time = slowest lane
- Deduplication at finding level, not IOC level

## Notes For Agents

- Fail-soft: one lane failure → continue others
- Rate limit: per-lane enforcement
- Correlation: entity stitching across lanes

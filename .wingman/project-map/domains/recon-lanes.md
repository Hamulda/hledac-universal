# Reconnaissance Lanes

## Metadata

| Field | Value |
| --- | --- |
| Kind | domain |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `domains/recon-lanes.md` |

## Summary

Specialized OSINT data collection lanes for different intelligence sources.

## Lane Catalog

| Lane | Source | Canonical Module |
|---|---|---|
| DOH | DNS-over-HTTPS | network/dns_tunnel_detector.py |
| CT | Certificate Transparency | recon/cert/ct_log_scanner.py |
| WAYBACK | Archive.org CDX | recon/wayback_cdx.py |
| PASSIVE_DNS | Passive DNS | recon/dns/passive_dns.py |
| BGP | BGP routing | recon/bgp_lane.py |
| PUBLIC | Public search | recon/search_lane_utils.py |
| SHODAN | Shodan | recon/shodan_lane.py |
| GREYNOISE | GreyNoise | recon/greynoise_lane.py |
| CENSYS | Censys | recon/censys_lane.py |
| GITHUB | GitHub secrets | recon/github_secret_scanner.py |
| DARK_WEB | Dark web | recon/dark_web_lane.py |
| CRYPTO | Blockchain | recon/blockchain_analyzer.py |

## Evidence

- recon/ directory contains all lane implementations
- PivotLanePlanner maps seed types to lanes
- workflow_orchestrator.py orchestrates multi-lane execution

## Use When

- Adding new intelligence sources
- Understanding which lanes cover which data types

## Do Not Use When

- Understanding pipeline stages (see sprint-pipeline)

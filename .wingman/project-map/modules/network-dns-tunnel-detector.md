# network-dns-tunnel-detector

**Type:** Network Intelligence  
**Path:** `network/dns_tunnel_detector.py`  
**Status:** current

## Purpose

DNS tunneling detection for exfiltration and C&C identification.

## Key Functions

| Function | Purpose |
|----------|---------|
| `DNSTunnelDetector` | Main class |
| `analyze_query(query)` | Analyze DNS query |
| `score_domain(domain)` | Tunnel likelihood |
| `detect_tunnel(流量)` | Flow-level detection |

## Indicators

| Indicator | Weight |
|-----------|--------|
| Long subdomain | High |
| High entropy | High |
| High query rate | Medium |
| Large response | Medium |

## Invariants

- [NDT-1] Threshold: score > 0.7 = likely tunnel
- [NDT-2] Training: supervised model on known tunnels
- [NDT-3] Alert: log + flag finding

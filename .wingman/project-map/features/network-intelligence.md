# network-intelligence

**Type:** Feature  
**Path:** `network/`, `recon/` (BGP)  
**Status:** current

## Purpose

Network-level intelligence gathering: DNS, BGP, passive DNS, and traffic analysis.

## Components

| Component | Path | Function |
|-----------|------|----------|
| PassiveDNS | `network/passive_dns.py` | Historical DNS |
| BGPMonitor | `network/bgp_monitor.py` | ASN/routing |
| CTLogScanner | `recon/ct_log_scanner.py` | SSL certs |
| DNSTunnelDetector | `network/dns_tunnel_detector.py` | Tunnel detection |
| BannerGrabber | `network/banner_grabber.py` | Service fingerprinting |

## Integration

```
NetworkCoordinator
├── PassiveDNSLane
├── BGPLane
├── CTLogLane
└── DNSTunnelLane
```

## Use Cases

- Infrastructure mapping
- Threat actor attribution
- Supply chain analysis
- Exfiltration detection

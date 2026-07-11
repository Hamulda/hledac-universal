---
title: External Service Integrations
summary: Tor SOCKS (127.0.0.1:9050), I2P SOCKS (127.0.0.1:7654), IPFS gateways, Shodan/Censys/GreyNoise APIs, BGP sidecar
tags: []
related: []
keywords: []
createdAt: '2026-07-11T19:03:39.547Z'
updatedAt: '2026-07-11T19:03:39.547Z'
---
## Reason
Document external service integrations from abstract context

## Raw Concept
**Task:**
Document external service integrations and transport layer

**Changes:**
- Added Tor SOCKS proxy
- Added I2P SOCKS proxy
- Added IPFS gateways
- Added threat intelligence APIs
- Added BGP enrichment

**Files:**
- transport/http3_lane.py

**Flow:**
Proxies -> http3_lane.py -> external services

**Timestamp:** 2026-07-11

## Narrative
### Structure
External services: Tor SOCKS (9050), I2P SOCKS (7654), IPFS (IPFSDSidecarAdapter), threat intel (Shodan/Censys/GreyNoise via feature flags), BGP enrichment via sidecar

### Dependencies
transport/http3_lane.py handles routing through proxies

### Highlights
Tor circuit renewal every 10 requests with 2.0x timeout scale

## Facts
- **tor_socks_default**: Tor SOCKS proxy default: 127.0.0.1:9050 [project]
- **tor_circuit_renewal**: Tor circuit renewal every 10 requests [project]
- **tor_timeout_scale**: Tor timeout scale 2.0x [project]
- **i2p_socks_default**: I2P SOCKS proxy default: 127.0.0.1:7654 [project]
- **ipfs_adapter**: IPFS gateways via IPFSDSidecarAdapter [project]
- **threat_intel_apis**: Shodan/Censys/GreyNoise APIs via feature flags [project]
- **bgp_enrichment**: BGP enrichment via BGP sidecar [project]
- **http3_lane_routing**: transport/http3_lane.py handles all proxy routing [project]

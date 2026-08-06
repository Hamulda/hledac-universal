---
children_hash: cd71750b4770351cc5189d1a68474144726617349cd02a387238df0ec0ac1bd0
compression_ratio: 0.28692579505300353
condensation_order: 1
covers: [context.md, http_3_bounded_resources.md, http_3_configuration_constants.md, http_3_lane_implementation.md, http_3_neqo_integration_plan.md, i2p_transport.md, issue_0_2_curl_cffi_caps_architecture_fix.md, tor_transport.md]
covers_token_total: 2830
summary_level: d1
token_count: 812
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries about transport_layers. I need to condense this information while preserving key facts, architectural decisions, and relationships.

Let me analyze the entries:

1. **context.md** - Overview of HTTP/3 lane implementation with two strategies
2. **http_3_bounded_resources.md** - H3 LRU cache 512 entries, concurrency cap 3, RSS memory block 5.5 GiB, cache TTL 24h
3. **http_3_configuration_constants.md** - M1_BOUNDS constants for HTTP/3
4. **http_3_lane_implementation.md** - Three HTTP/3 strategies, dark web rules
5. **http_3_neqo_integration_plan.md** - neqo integration plan
6. **i2p_transport.md** - I2P SAM v3 protocol, three transport modes
7. **issue_0_2_curl_cffi_caps_architecture_fix.md** - CAPS architecture fix
8. **tor_transport.md** - Tor socks5h proxy, circuit rotation, JARM fingerprinting

Key themes:
- HTTP/3 lane with multiple strategies (curl_cffi_opportunistic, neqo, aioquic)
- Bounded resources (LRU 512, concurrency 3, memory 5.5GiB)
- Dark web bypass (Tor, I2P transports)
- Alt-Svc probing
- JARM fingerprinting for C2 detection
- CAPS architecture for capability management

I need to create a concise structural summary that references the entry names for drill-down.
</think>

# transport_layers

## Overview
HTTP/3 lane implementation with layered transport strategies for anonymity networks. Two-tier architecture: HTTP/3 with bounded resources for clearnet, separate Tor/I2P transports for dark web.

## Architecture

### HTTP/3 Lane (http_3_lane_implementation.md, http_3_bounded_resources.md, http_3_configuration_constants.md)
Three strategies in priority order: curl_cffi_opportunistic (default) → NeqoRustlsTransportAdapter (M1 arm64 darwin) → AioquicTransportAdapter (fallback). **neqo integration plan** in http_3_neqo_integration_plan.md.

**Bounded Resources (M1_BOUNDS):**
- LRU cache: 512 entries max, 24h TTL
- Concurrency: 3 concurrent requests
- Memory guard: RSS block at 5.5 GiB
- Timeouts: 8.0s request, 2.0s semaphore wait, 4.0s probe
- Max probe tasks: 16

**Flow:** request → LRU cache check → memory guard → Alt-Svc detection → speculative probe → execute H3

**Dark Web Rule:** .onion, .i2p, .b32.i2p TLDs bypass HTTP/3 entirely.

### CAPS Integration (issue_0_2_curl_cffi_caps_architecture_fix.md)
FetchCoordinator uses CAPS.require(CURL_CFFI) for guaranteed JA3 spoofing. FAIL-FAST when curl_cffi unavailable—no silent httpx fallback.

## Anonymity Transports

### Tor Transport (tor_transport.md)
- Proxy: socks5h://127.0.0.1:9050
- Circuit rotation: every 10 requests
- Timeout multiplier: 2.0x
- Max concurrent: 3 per domain
- **JARM fingerprinting** for C2 detection (Cobalt Strike, Metasploit, AsyncRAT, Havoc, Covenant signatures documented)

### I2P Transport (i2p_transport.md)
Three modes: SAM v3 (port 7656, ~2MB RAM), SOCKS5H (port 4444), HTTP proxy (port 8888). Session ID format: hledac-samv3-<uuid>. NAMING LOOKUP resolves .i2p destinations.

## Key Relationships
- HTTP/3 never attempts dark web TLDs → delegates to Tor/I2P transports
- curl_cffi CAPS registration at capabilities.py:190
- neqo pending PyPI availability (rustls arena release on arm64+darwin)
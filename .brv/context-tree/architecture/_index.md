---
children_hash: 9cde009591055f33383169c1e3dd7fc1a9ce7bee3b084a43a282ef5d9626c1b8
compression_ratio: 0.18946749043836422
condensation_order: 2
covers: [context.md, core/_index.md, hledac_universal/_index.md, runtime/_index.md, transport_layers/_index.md]
covers_token_total: 3399
summary_level: d2
token_count: 644
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) for the transport_layers domain. Looking at the provided context, I need to compress the child entries into a concise overview while preserving key facts, architectural decisions, and relationships.

From the child entries provided:
1. HTTP/3 Lane with curl_cffi_opportunistic (default) and aioquic_stealth (fallback)
2. neqo integration planned
3. M1_BOUNDS configuration constants
4. Dark web TLDs excluded from HTTP/3
5. LRU caching (512 entries)
6. Memory guard at 5.5 GiB RSS
7. Concurrency capped at 3
8. CAPS system for capability gating
9. FAIL-FAST policy for curl_cffi

Let me create a clean structural summary.
</think>

# architecture/transport_layers — Domain Summary

## Overview

HTTP/3 lane implementation supporting multiple strategies with LRU caching, memory guards, and speculative Alt-Svc probing. Dark web TLDs (.onion, .i2p, .b32.i2p) are excluded from HTTP/3 due to Tor/I2P SOCKS incompatibility with QUIC/UDP.

## Strategies

| Strategy | Priority | Use Case |
|----------|----------|----------|
| `curl_cffi_opportunistic` | 1 | Default, JA3 spoofing via curl_cffi >= 0.7 |
| `neqo` | 2 | Mozilla Rust QUIC, arm64 darwin priority (planned) |
| `aioquic_stealth` | 3 | Standard fallback for stealth when neqo unavailable |

## Configuration (M1_BOUNDS)

- **LRU cache max**: 512 entries
- **Concurrency max**: 3
- **Timeout**: 8.0s
- **Semaphore wait**: 2.0s
- **Cache TTL**: 86400s (24h)
- **RSS memory block**: 5.5 GiB
- **Max probe tasks**: 16

## Architecture Decisions

- **FAIL-FAST policy**: No silent httpx fallback when curl_cffi unavailable — JA3 spoofing guaranteed
- **CAPS integration**: FetchCoordinator uses `CAPS.require(CURL_CFFI)` instead of availability check
- **Fallback chain**: neqo unavailable → aioquic → curl_cffi_opportunistic
- **LRU eviction**: O(1) via OrderedDict, bounded by `http3_lru_max`
- **Error handling**: Any error returns `None`, never propagates

## Key Files

- `transport/http3_lane.py` — HTTP/3 lane implementation
- `fetching/public_fetcher.py` — Public fetcher entry point
- `fetching/curl_cffi_fetch.py` — CAPS-aware curl_cffi wrapper
- `coordinators/fetch_coordinator.py` — Fetch orchestration with CAPS checks

## Drill-Down

- `http_3_lane_implementation.md` — Full strategy details, patterns, rules
- `http_3_configuration_constants.md` — Complete M1_BOUNDS constants
- `http_3_neqo_integration_plan.md` — neqo Rust integration roadmap
- `issue_0_2_curl_cffi_caps_architecture_fix.md` — CAPS integration fix details
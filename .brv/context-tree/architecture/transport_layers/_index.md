---
children_hash: bbe465913f557d2e9993ba392f82cb6836ac1df2129c0e36c2f2fb6a619f20b8
compression_ratio: 0.44798785117691725
condensation_order: 1
covers: [context.md, http_3_lane_implementation.md, issue_0_2_curl_cffi_caps_architecture_fix.md]
covers_token_total: 1317
summary_level: d1
token_count: 590
type: summary
---
<think>
The user wants me to create a structural summary of the transport_layers domain based on the provided child entries. I need to condense this into ~20-30% of the input while preserving key facts, relationships, and architectural decisions.

Key points to preserve:
1. Two HTTP/3 lane strategies: curl_cffi_opportunistic (default) and aioquic_stealth (opt-in)
2. LRU caching, concurrency limits, memory guards
3. Alt-Svc probing
4. Specific limits: LRU 512, concurrency 3, timeout 8.0s, memory guard 5.5 GiB
5. Dark web URLs skipped
6. CAPS integration for curl_cffi
7. FAIL-FAST policy when curl_cffi unavailable
8. JA3 spoofing enforcement
9. File paths and relationships

Let me create a concise structural summary.
</think>

# transport_layers

HTTP/3 lane implementation with dual strategy architecture and CAPS-based fetch transport enforcement.

## Strategies

- **curl_cffi_opportunistic** — default strategy, LRU-cached, no extra dependencies
- **aioquic_stealth** — opt-in QUIC strategy, adds ~50-80 MB resident memory

## Limits & Guards

| Parameter | Value |
|-----------|-------|
| LRU cache max | 512 entries |
| Concurrency cap | 3 |
| Per-request timeout | 8.0s |
| Semaphore wait timeout | 2.0s |
| Memory guard RSS ceiling | 5.5 GiB |
| extract_host LRU | 2048 |

## Dark Web Bypass

Skips HTTP/3 lane entirely for: `.onion`, `.i2p`, `.b32.i2p` TLDs.

## Alt-Svc Probing

- Probe session: `AsyncSession(max_clients=2)`
- Probe timeout: 4.0s
- Max concurrent probe tasks: 16
- Pattern: `^h3[= "']` (case-insensitive)

## CAPS Integration (ISSUE-0.2)

- FetchCoordinator now uses `CAPS.require(CURL_CFFI)` instead of `is_curl_cffi_available()`
- New module: `fetching/curl_cffi_fetch.py` provides CAPS-aware wrappers
- **FAIL-FAST policy**: no silent httpx fallback when curl_cffi unavailable
- JA3 spoofing enforced via CAPS check

## Key Files

- `transport/http3_lane.py` — dual strategy implementation
- `fetching/curl_cffi_fetch.py` — CAPS-aware curl_cffi wrapper
- `coordinators/fetch_coordinator.py` — uses CAPS for transport selection
- `capabilities.py:190` — CURL_CFFI capability registration

## Rules

1. Always `except Exception`, never bare `except`
2. aioquic missing → fallback to curl_cffi
3. Any error → return `None`, caller continues
4. Dark web URLs always skip HTTP/3 lane
5. Semaphore wait non-blocking with timeout
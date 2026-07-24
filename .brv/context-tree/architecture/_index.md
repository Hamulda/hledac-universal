---
children_hash: e46afc9472426fc1d63deedd8ba14ca36ee321d20c2d03d2d759b57929eec051
compression_ratio: 0.2148206543161214
condensation_order: 2
covers: [context.md, hledac_universal/_index.md, transport_layers/_index.md]
covers_token_total: 2537
summary_level: d2
token_count: 545
type: summary
---
<think>
The user wants me to create a structural summary at the d1 (domain) level. Let me analyze the provided child entries and create a coherent overview of the transport_layers domain.

Key information from the entries:
1. HTTP/3 lane implementation with dual strategy
2. curl_cffi and aioquic approaches
3. LRU caching, concurrency limits, memory guards
4. CAPS-based fetch transport enforcement (issue_0_2)
5. Alt-Svc probing
6. Dark web bypass
7. Specific technical parameters

Let me synthesize this into a concise domain-level summary.
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
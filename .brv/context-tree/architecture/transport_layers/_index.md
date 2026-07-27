---
children_hash: 0a169501548cadd7c6d34f39c4cfab923d0e51a5f0dd8e555c0f3dd65d659005
compression_ratio: 0.47812692544670365
condensation_order: 1
covers: [context.md, http_3_configuration_constants.md, http_3_lane_implementation.md, http_3_neqo_integration_plan.md, issue_0_2_curl_cffi_caps_architecture_fix.md]
covers_token_total: 1623
summary_level: d1
token_count: 776
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about transport_layers. I need to:

1. Condense the child entries into a structured overview
2. Preserve key facts, relationships, and patterns
3. Reference entry names for drill-down
4. Target ~20-30% compression
5. Output clean markdown without code blocks or XML tags

Let me analyze the entries:

1. **context.md** - Overview of HTTP/3 lane with curl_cffi_opportunistic and aioquic_stealth strategies
2. **http_3_configuration_constants.md** - M1_BOUNDS constants for HTTP/3 (cache, concurrency, timeouts, memory)
3. **http_3_lane_implementation.md** - Three strategies, LRU caching, memory guards, Alt-Svc probing
4. **http_3_neqo_integration_plan.md** - Future neqo integration plan
5. **issue_0_2_curl_cffi_caps_architecture_fix.md** - Bug fix for curl_cffi CAPS integration

Key patterns and relationships:
- HTTP/3 has multiple strategies (curl_cffi_opportunistic default, aioquic_stealth fallback, neqo planned)
- Configuration bounded by M1_BOUNDS constants
- Dark web TLDs (.onion, .i2p, .b32.i2p) excluded from HTTP/3
- LRU caching with 512 entry max
- Memory guard at 5.5 GiB RSS
- Concurrency limited to 3
- CAPS system manages capability registration

Let me create a concise structural summary.
</think>

# transport_layers

HTTP/3 lane implementation supporting multiple strategies with LRU caching, memory guards, and speculative Alt-Svc probing.

## Strategies

- **curl_cffi_opportunistic** (default) — JA3 spoofing via curl_cffi >= 0.7
- **neqo** (planned) — Mozilla Rust QUIC engine, arm64 darwin priority, pending PyPI
- **aioquic_stealth** (fallback) — Standard fallback when neqo unavailable

## Configuration (M1_BOUNDS)

| Constant | Value |
|----------|-------|
| LRU cache max | 512 entries |
| Concurrency max | 3 |
| Timeout | 8.0s |
| Semaphore wait | 2.0s |
| Cache TTL | 86400s (24h) |
| RSS memory block | 5.5 GiB |
| Max probe tasks | 16 |

## Architecture Decisions

- **Dark web exclusion**: HTTP/3 never attempted for `.onion`, `.i2p`, `.b32.i2p` TLDs
- **CAPS integration**: FetchCoordinator uses `CAPS.require(CURL_CFFI)` instead of availability check
- **FAIL-FAST policy**: No silent httpx fallback when curl_cffi unavailable — JA3 spoofing guaranteed
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
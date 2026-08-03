---
children_hash: bebcf399d55f2659385266517909d4aad6e8917311e2bb108d6aab576eeb5354
compression_ratio: 0.18036595991867557
condensation_order: 2
covers: [context.md, core/_index.md, hledac_universal/_index.md, runtime/_index.md, transport_layers/_index.md]
covers_token_total: 3443
summary_level: d2
token_count: 621
type: summary
---
<think>
The user wants me to create a concise structural summary at the domain level (d1) for transport_layers. I need to compress the child entries while preserving key facts, relationships, and patterns. Let me distill the essential information from these entries.

Key topics to cover:
1. HTTP/3 lane with dual strategy (curl_cffi + aioquic)
2. M1_BOUNDS configuration constants
3. Architecture decisions about dark web exclusion, CAPS integration, fail-fast policy
4. Key files and their roles
5. Reference entry names for drill-down

I need to output ONLY the summary text, no code blocks or XML.
</think>

# transport_layers

HTTP/3 lane implementation in `transport/http3_lane.py` with dual strategy for stealth and opportunistic modes. Canonical entry: `PublicFetcher.fetch()` delegates to `FetchCoordinator.coordinated_fetch()` which routes to HTTP/3 lane via CAPS-registered capability. curl_cffi_opportunistic is default; aioquic_stealth is fallback when neqo unavailable.

## Dual Strategy

**curl_cffi_opportunistic** (default, 0.7+): JA3 spoofing via curl_cffi. **aioquic_stealth** (fallback): Standard client hello when neqo not available. **neqo** (planned): Mozilla Rust QUIC engine for arm64 darwin, pending PyPI package. Dark web TLDs (.onion, .i2p, .b32.i2p) explicitly excluded from HTTP/3 attempts—H3 auto-skipped for Tor/I2P circuits per QUIC/UDP incompatibility.

## Configuration (M1_BOUNDS)

LRU cache bounded at 512 entries with 24h TTL. Concurrency limited to 3. Timeout 8.0s, semaphore wait 2.0s. Memory guard triggers RSS block at 5.5 GiB. Max probe tasks capped at 16.

## Architecture Decisions

CAPS system replaces availability checks—FetchCoordinator uses `CAPS.require(CURL_CFFI)` for gated capability. FAIL-FAST policy: no silent httpx fallback when curl_cffi unavailable, ensuring JA3 spoofing is always honored. LRU uses OrderedDict for O(1) eviction. Error propagation returns None rather than raising.

## Key Files

`transport/http3_lane.py` implements lane. `fetching/public_fetcher.py` entry point. `fetching/curl_cffi_fetch.py` wraps curl_cffi with CAPS checks. `coordinators/fetch_coordinator.py` orchestrates with capability gating.

## Drill-Down

`http_3_lane_implementation.md` covers strategy details, Alt-Svc probing, memory guards. `http_3_configuration_constants.md` lists M1_BOUNDS values. `http_3_neqo_integration_plan.md` outlines neqo Rust integration. `issue_0_2_curl_cffi_caps_architecture_fix.md` documents CAPS migration.
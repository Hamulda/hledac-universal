---
title: HTTP 3 Lane and Public Fetcher
summary: HTTP/3 dual strategy (curl_cffi opportunistic + aioquic stealth), Tor/I2P session management, URL classification, and body hash store for public fetcher
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:54:03.996Z'
updatedAt: '2026-07-11T15:06:50.899Z'
---
## Reason
Documenting HTTP/3 lane and public fetcher architecture

## Raw Concept
**Task:**
Document HTTP/3 lane and public fetcher architecture

**Changes:**
- Added HTTP/3 opportunistic upgrade with curl_cffi HttpVersion.v3
- Added aioquic stealth HTTP/3 with QUIC for advanced users
- Implemented LRU cache with sliding TTL per host
- Added memory guard at 5.5 GiB RSS threshold
- Implemented speculative Alt-Svc probe for first-time hosts
- Added Rust URL classification with xxh3_64 cache key
- Added BLAKE3-64 body hashing with xxHash3 fallback
- Replaced 11 module-level globals with _SessionManager singleton

**Files:**
- transport/http3_lane.py
- fetching/public_fetcher.py

**Flow:**
URL -> classify -> HTTP/3 lane (optional) -> HTTP/2 session -> Tor/I2P (if dark web) -> FetchResult

**Timestamp:** 2026-07-11

**Author:** HLEDAC Team

**Patterns:**
- `^socks5h?://[\w.-]+:\d+$` - Validates SOCKS proxy URLs

## Narrative
### Structure
HTTP/3 lane in transport/ provides dual strategy (curl_cffi opportunistic + aioquic stealth). Public fetcher in fetching/ handles Tor/I2P sessions, URL classification via Rust backend, body hashing, and HTTP/2 session management.

### Dependencies
Requires curl_cffi for opportunistic H3, aioquic for stealth H3. Tor/I2P require SOCKS proxies. Rust backend for URL classification.

### Highlights
Fail-soft invariants: errors return None never exceptions. QUIC/UDP incompatible with Tor/I2P - H3 auto-skipped for dark web. BLAKE3-64 with NEON acceleration for body hashing.

### Rules
Rule 1: No bare except: - always except Exception
Rule 2: Any error returns None - never propagate
Rule 3: aioquic missing -> fallback to curl_cffi -> HTTP/1.1

### Examples
Env gates: HLEDAC_ENABLE_HTTPX_H3=1 enables HTTP/3; HLEDAC_HTTP3=1 legacy alias also works
Dark web: is_dark_web_url("http://example.onion") returns True
FetchResult: frozen msgspec Struct with url, final_url, status_code, body (bytes), error fields

## Facts
- **http3_cache_config**: H3_CACHE_MAX uses M1_BOUNDS().http3_lru_max [project]
- **http3_cache_config**: H3_CONCURRENCY_MAX uses M1_BOUNDS().http3_concurrency_max [project]
- **http3_timeout**: HTTP/3 timeout is 8.0 seconds [project]
- **http3_timeout**: HTTP/3 wait timeout is 2.0 seconds [project]
- **probe_timeout**: Head probe timeout is 4.0 seconds [project]
- **probe_tasks**: Max probe tasks capped at 16 [project]
- **http3_env_gate**: HLEDAC_ENABLE_HTTPX_H3=1 enables HTTP/3 lane [environment]
- **http3_env_gate**: HLEDAC_HTTP3=1 is legacy alias for HTTP/3 (F260 compat) [environment]
- **dark_web_tlds**: Dark web TLDs: .onion, .i2p, .b32.i2p [project]
- **dark_web_h3_limitation**: QUIC/UDP cannot tunnel through Tor/I2P - H3 skipped for dark web [project]
- **tor_proxy**: TOR_SOCKS_PROXY default: socks5h://127.0.0.1:9050 [environment]
- **i2p_proxy**: I2P_SOCKS_PROXY default: socks5://127.0.0.1:7654 [environment]
- **tor_circuit_renewal**: Tor circuit renewal every 10 requests [project]
- **tor_timeout_scale**: Tor stealth timeout scale is 2.0x [project]
- **url_classification_kinds**: URL kinds: clearnet, onion, i2p, freenet, malformed [project]
- **size_limits**: Max bytes default: 2,000,000 [project]
- **size_limits**: Max bytes hard limit: 10,000,000 [project]
- **body_hash_store**: Max body hashes store: 10,000 [project]
- **httpx_session**: HTTP/2 max connections: 200 [project]
- **httpx_session**: HTTP/2 max keepalive: 100 [project]
- **httpx_timeout**: HTTP/2 connect timeout: 10.0s [project]
- **httpx_timeout**: HTTP/2 read timeout: 30.0s [project]
- **transport_counters**: TransportCounters bounded at 999,999 [project]
- **aioquic_memory**: aioquic stealth mode resident: ~50-80 MB [project]
- **aioquic_concurrency**: aioquic concurrency capped at 3 [project]
- **aioquic_memory_block**: aioquic memory block at 5.5 GiB [project]
- **fetch_result**: FetchResult uses msgspec.Struct, frozen=True [project]
- **body_optimization**: Body zero-copy preservation via body field [project]
- **hashing_algorithm**: BLAKE3-64 with NEON acceleration on M1 [project]
- **hashing_algorithm**: xxHash3 is fallback hashing [project]
- **browser_ua_pool**: Browser UA pool: Chrome 124, Firefox 133, Safari 17, Edge 124 [project]
- **accept_language_pool**: Accept-Language pool: en-US, en-GB, de-DE, fr-FR, ja-JP, zh-CN [project]
- **lru_cache_size**: LRU cache bounded at 512 entries [project]
- **lru_implementation**: LRU eviction in O(1) via OrderedDict [project]

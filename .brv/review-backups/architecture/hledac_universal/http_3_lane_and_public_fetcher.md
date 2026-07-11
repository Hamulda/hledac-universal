---
title: HTTP/3 Lane and Public Fetcher
summary: HTTP/3 lane with curl_cffi opportunistic and aioquic stealth modes, LRU cache with 5.5 GiB RSS guard, Alt-Svc parsing, dark web URL handling, public fetcher with Rust URL classification and BLAKE3-64 body hashing
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:54:03.996Z'
updatedAt: '2026-07-11T14:54:03.996Z'
---
## Reason
Document HTTP/3 opportunistic/stealth transport and public fetcher architecture

## Raw Concept
**Task:**
Document HTTP/3 lane transport and public fetcher architecture

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
request -> classify URL (Rust/httpx) -> select transport (curl_cffi/httpx/Tor/I2P) -> fetch with UA/Lang rotation -> hash body (BLAKE3/xxh3) -> return FetchResult

**Timestamp:** 2025-07-11

**Author:** HLEDAC Team

## Narrative
### Structure
HTTP/3 lane in transport/http3_lane.py provides two strategies: curl_cffi_opportunistic (default, no extra deps) and aioquic_stealth (opt-in, ~50-80 MB). Public fetcher in fetching/public_fetcher.py uses curl_cffi as primary, httpx for HTTP/2, and httpx-socks for Tor/I2P.

### Dependencies
curl_cffi>=0.7, aioquic (optional for stealth), msgspec, httpx, httpx-socks, psutil, rust extension _url_classify

### Highlights
URL classification: Rust AHashMap<u64, (kind, host)> with xxh3_64 keys. Body hashing: BLAKE3-64 with NEON acceleration. M1 8GB bounds: LRU max 512, concurrency max 3, RSS guard at 5.5 GiB.

### Rules
Rule 1: HTTP/3 never attempted for .onion/.i2p/.b32.i2p URLs (UDP cannot be tunneled through Tor TransPort)
Rule 2: No bare except: — always except Exception
Rule 3: All counters saturate at 999_999

### Examples
Env gates: HLEDAC_ENABLE_HTTPX_H3=1 enables HTTP/3; HLEDAC_HTTP3=1 legacy alias also works
Dark web: is_dark_web_url("http://example.onion") returns True
FetchResult: frozen msgspec Struct with url, final_url, status_code, body (bytes), error fields

## Facts
- **http3_strategies**: HTTP/3 lane uses two strategies: curl_cffi_opportunistic (default) and aioquic_stealth (opt-in) [project]
- **lru_cache_max**: LRU cache max entries bound from M1_BOUNDS() [project]
- **http3_concurrency_max**: HTTP/3 concurrency capped at 3 QUIC handshakes [project]
- **rss_block_threshold_gib**: Memory guard blocks HTTP/3 lane at 5.5 GiB RSS [project]
- **counter_max_saturation**: All transport counters saturate at 999,999 [project]
- **url_classify_cache_capacity**: URL classify batch cache capacity is 50,000 items [project]
- **body_hash_max**: Body hashes bounded at 10,000 entries [project]
- **tor_circuit_renewal_count**: Tor circuit renewal every 10 requests [project]
- **max_bytes_default**: Default max bytes for fetch is 2,000,000 [project]
- **max_bytes_hard**: Hard max bytes limit is 10,000,000 [project]
- **aioquic_resident_mb**: aioquic stealth mode resident size is ~50-80 MB [project]
- **tor_stealth_timeout_scale**: Tor stealth timeout scaled by 2.0x [project]
- **h3_wait_timeout_s**: HTTP/3 wait timeout for semaphore is 2.0 seconds [project]
- **h3_timeout_s**: HTTP/3 request timeout is 8.0 seconds [project]
- **url_cache_key**: URL classification uses xxh3_64 as cache key for 8-byte vs 80-200 byte full URL [project]

---
title: HTTP/3 Lane Implementation
summary: 'HTTP/3 with 3 strategies: curl_cffi_opportunistic, neqo (M1), aioquic fallback, with LRU caching and memory guards'
tags: []
related: [architecture/hledac_universal/http_3_lane_and_public_fetcher.md]
keywords: []
createdAt: '2026-07-11T19:03:39.552Z'
updatedAt: '2026-07-26T11:19:10.459Z'
---
## Reason
Documenting HTTP/3 lane architecture from codebase

## Raw Concept
**Task:**
Document HTTP/3 lane implementation in transport/http3_lane.py

**Changes:**
- Added curl_cffi_opportunistic strategy
- Added aioquic_stealth strategy
- Added Alt-Svc LRU cache
- Added memory guard
- Added speculative probe (F265B)

**Files:**
- transport/http3_lane.py
- fetching/public_fetcher.py

**Flow:**
Alt-Svc detection -> opportunistic HTTP/3 -> neqo/aioquic fallback -> LRU cache

**Timestamp:** 2026-07-26

**Patterns:**
- `\.onion|\.i2p|\.b32\.i2p` - Dark web TLDs - HTTP/3 never attempted

## Narrative
### Structure
Three HTTP/3 strategies: curl_cffi_opportunistic (default), NeqoRustlsTransportAdapter (M1 arm64 darwin), AioquicTransportAdapter (fallback). Cached per host with LRU bounded by M1_BOUNDS.http3_lru_max (512 default).

### Dependencies
curl_cffi >= 0.7, neqo (pending PyPI), aioquic, psutil for RSS memory guard

### Highlights
Memory guard at 5.5 GiB RSS, max 3 concurrent, 8s timeout, 2s semaphore wait. Speculative Alt-Svc probe fires detached background HEAD probe.

### Rules
Rule 1: Dark web TLDs (.onion, .i2p, .b32.i2p) never use HTTP/3
Rule 2: neqo unavailable -> aioquic fallback -> curl_cffi_opportunistic
Rule 3: No bare except: - always except Exception
Rule 4: Cache overflow -> LRU eviction O(1) via OrderedDict
Rule 5: Any error returns None, never propagates

### Examples
is_dark_web_url("http://example.onion") -> True, skips H3
http_version_for_curl_cffi("https://example.com") -> HttpVersion.v3 or None
fetch_http3_aioquic(url, headers, 8.0) -> bytes | None

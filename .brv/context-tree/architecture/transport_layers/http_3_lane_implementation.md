---
title: HTTP/3 Lane Implementation
summary: 'Two strategies: curl_cffi_opportunistic (default LRU-cached) + aioquic_stealth (opt-in QUIC). Concurrency cap 3, memory guard 5.5 GiB, Alt-Svc probing.'
tags: []
related: [architecture/hledac_universal/http_3_lane_and_public_fetcher.md]
keywords: []
createdAt: '2026-07-11T19:03:39.552Z'
updatedAt: '2026-07-11T19:03:39.552Z'
---
## Reason
Document HTTP/3 lane implementation from abstract context

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
request -> detect dark web -> check Alt-Svc cache -> curl_cffi or aioquic -> response

**Timestamp:** 2026-07-11

**Patterns:**
- `^h3[= "']` (flags: i) - Alt-Svc header parsing - accepts h3=, h3 ", h3=

## Narrative
### Structure
Two strategies: curl_cffi_opportunistic (default, LRU cached) and aioquic_stealth (opt-in QUIC). Fail-soft invariants: always except Exception, return None on errors.

### Dependencies
Requires curl_cffi >= 0.7. aioquic extra adds ~50-80 MB. psutil optional for memory guard.

### Highlights
Bounded LRU (512), concurrency cap (3), memory guard (5.5 GiB), Alt-Svc probing. Dark web (.onion/.i2p/.b32.i2p) always skipped.

### Rules
Rule 1: No bare except: - always except Exception
Rule 2: aioquic missing -> fall back to curl_cffi
Rule 3: Any error -> return None, caller continues
Rule 4: Dark web URLs always skip HTTP/3 lane
Rule 5: Semaphore wait non-blocking with timeout

### Examples
is_dark_web_url("http://example.onion") -> True, skips H3
http_version_for_curl_cffi("https://example.com") -> HttpVersion.v3 or None
fetch_http3_aioquic(url, headers, 8.0) -> bytes | None

## Facts
- **http3_default_strategy**: curl_cffi_opportunistic strategy is default (no extra deps) [project]
- **aioquic_extra**: aioquic_stealth requires [http3] extra [project]
- **http3_cache_max**: LRU cache bounded to 512 entries (M1_BOUNDS.http3_lru_max) [project]
- **http3_concurrency**: Concurrency capped at 3 (M1_BOUNDS.http3_concurrency_max) [project]
- **http3_timeout**: Per-request timeout 8.0 seconds [project]
- **http3_wait_timeout**: Semaphore wait timeout 2.0 seconds [project]
- **http3_rss_block**: Memory guard at 5.5 GiB (M1_BOUNDS.fetch_soft_ceiling_gb) [project]
- **http3_env_gate**: HLEDAC_ENABLE_HTTPX_H3=1 enables both strategies [project]
- **dark_web_tlds**: Dark web TLDS skipped for H3: .onion, .i2p, .b32.i2p [project]
- **aioquic_memory**: aioquic adds ~50-80 MB resident memory [project]
- **probe_session**: Alt-Svc probe uses AsyncSession(max_clients=2) [project]
- **probe_timeout**: Alt-Svc probe timeout 4.0 seconds [project]
- **max_probe_tasks**: Max 16 concurrent probe tasks (_MAX_PROBE_TASKS) [project]
- **extract_host_cache**: extract_host uses lru_cache(2048) [project]

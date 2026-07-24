---
title: ISSUE-0.2 curl_cffi CAPS Architecture Fix
summary: Fixed polar mismatch between declared and actual fetch transport architecture - curl_cffi capability registered in CAPS but not used by FetchCoordinator
tags: []
related: []
keywords: []
createdAt: '2026-07-24T17:40:12.140Z'
updatedAt: '2026-07-24T17:40:12.140Z'
---
## Reason
Documenting bug fix for fetch transport architecture mismatch

## Raw Concept
**Task:**
Fix curl_cffi CAPS integration in FetchCoordinator

**Changes:**
- Created fetching/curl_cffi_fetch.py - new CAPS-aware wrapper module
- Updated coordinators/fetch_coordinator.py to use CAPS-aware curl_cffi
- Updated fetching/public_fetcher.py imports
- Implemented FAIL-FAST when curl_cffi unavailable (no silent httpx fallback without JA3)

**Files:**
- fetching/curl_cffi_fetch.py
- coordinators/fetch_coordinator.py
- fetching/public_fetcher.py

**Flow:**
FetchCoordinator._fetch_url() -> Lightpanda (FAIL-FAST) -> curl_cffi with CAPS check -> JA3 spoofing -> If unavailable FAIL-FAST

**Timestamp:** 2026-07-24

**Author:** dev-team

## Narrative
### Structure
FetchCoordinator now uses CAPS.require(CURL_CFFI) instead of is_curl_cffi_available(). The new fetching/curl_cffi_fetch.py provides CAPS-aware wrapper functions: is_curl_cffi_capable(), require_curl_cffi(), fetch_via_curl_cffi_with_caps_check().

### Dependencies
Requires CURL_CFFI capability registration in capabilities.py:190

### Highlights
JA3 spoofing is now guaranteed when curl_cffi is used. No silent httpx fallback without JA3 - FAIL-FAST instead.

## Facts
- **curl_cffi_capability_location**: CURL_CFFI capability registered at capabilities.py:190 [project]
- **httpx_fallback_policy**: No silent httpx fallback when curl_cffi unavailable - FAIL-FAST policy [project]
- **ja3_spoofing_requirement**: JA3 spoofing required for fetch transport - enforced via CAPS [project]

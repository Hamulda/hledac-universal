---
title: issue_0_2_curl_cffi_caps_invariants
summary: ISSUE-0.2 establishes curl_cffi CAPS-based availability check with FAIL-FAST fallback invariants
tags: []
related: [facts/project/context.md, facts/project/caps-capability-registry-for-feature-gating.md]
keywords: []
createdAt: '2026-07-24T17:40:10.387Z'
updatedAt: '2026-07-24T17:40:10.387Z'
---
## Reason
Document curl_cffi availability check invariants from ISSUE-0.2

## Raw Concept
**Task:**
Establish curl_cffi CAPS-based availability check with FAIL-FAST fallback

**Changes:**
- Added CAPS.require(CURL_CFFI) as mandatory availability check
- Blocked httpx fallback WITHOUT JA3 (FAIL-FAST)
- All curl_cffi imports now route through fetching/curl_cffi_fetch.py

**Files:**
- fetching/curl_cffi_fetch.py

**Flow:**
Check CAPS.require(CURL_CFFI) -> if unavailable FAIL-FAST

**Timestamp:** 2026-07-24

## Narrative
### Structure
curl_cffi availability is checked via CAPS system, not is_curl_cffi_available()

### Highlights
httpx fallback without JA3 is explicitly blocked with FAIL-FAST behavior

### Rules
Rule 1: ALWAYS use CAPS.require(CURL_CFFI) for availability checks
Rule 2: httpx fallback MUST have JA3 support enabled
Rule 3: All curl_cffi imports route through fetching/curl_cffi_fetch.py

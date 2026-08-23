# curl_cffi Fetch

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/curl-cffi-fetch.md` |
| Source Path | `fetching/curl_cffi_fetch.py` |

## Summary

Primary transport wrapper using curl_cffi with JA3/TLS fingerprint rotation. Canonical fetch with 6 browser profiles, per-host session caching with LRU, and HTTP/3 Alt-Svc prewarm. NEVER silently falls back to httpx.

## Evidence

- 6 browser JA3 profiles for fingerprint rotation
- Per-host session caching with LRU eviction
- HTTP/3 Alt-Svc support via prewarm pool
- CAPS-based capability checking
- Fallback chain: curl_cffi → FAIL FAST (no silent httpx)
- Architecture: FetchCoordinator → curl_cffi_fetch → transport/curl_cffi_fetch

## Use When

- Making HTTP requests that need stealth
- Understanding the fetch fallback chain
- Debugging fetch transport issues

## Do Not Use When

- Writing a new fetch transport (implement in transport/ dir)

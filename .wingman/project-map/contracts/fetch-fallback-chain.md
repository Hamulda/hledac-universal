# Fetch Fallback Chain

## Metadata

- **Entry Path:** contracts/fetch-fallback-chain
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** contract

## Summary

Canonical fetch strategy with fallback chain for reliability.

## Chain

| Priority | Method | Use Case |
|----------|--------|----------|
| 1 | curl_cffi | JA3 stealth |
| 2 | httpx | HTTP/2 clearnet |
| 3 | httpx_socks | SOCKS5H proxy |
| 4 | StealthBrowser | JavaScript rendered |

## Contract

1. Try highest priority available
2. On failure, fall back to next
3. Log each attempt
4. Return first success

## Key Invariant

curl_cffi ONLY in FetchCoordinator - not global aiohttp replacement.

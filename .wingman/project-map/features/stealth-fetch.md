# Stealth Fetch

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/stealth-fetch.md` |
| Source Paths | `fetching/curl_cffi_fetch.py`, `layers/stealth.py`, `advanced_web/stealth_browser.py` |

## Summary

Stealth web fetching with JA3 fingerprint rotation, behavior simulation, and evasion scripts. curl_cffi primary transport with 6 browser profiles. StealthBrowser via Playwright for JS-rendered pages.

## Evidence

- curl_cffi_fetch.py: 6 JA3 profiles, per-host LRU session cache, HTTP/3 Alt-Svc
- Fallback chain: curl_cffi → FAIL FAST (no silent httpx)
- layers/stealth.py: StealthLayer, BehaviorSimulator, CaptchaSolver, FingerprintProfile
- evasion_pipeline.py: FingerprintProfile → EvasionScriptGenerator pipeline
- StealthBrowser: Playwright wrapper with anti-detection, __slots__ for M1 8GB
- advanced_web/stealth_browser.py: browser pool for JS rendering

## Use When

- Fetching stealth-sensitive targets
- CAPTCHA solving during fetch
- JS-rendered page fetching

## Do Not Use When

- Simple public fetch (see public_fetcher)
- Non-stealth fetch (curl_cffi is always stealthy anyway)

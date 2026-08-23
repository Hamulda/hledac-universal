# OPSEC Coordinator

## Metadata

- **Entry Path:** modules/opsec-coordinator
- **Status:** current
- **Source:** coordinators/opsec_coordinator.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Operational security coordinator managing fingerprinting, stealth, and anonymity requirements.

## Source Paths

- `coordinators/opsec_coordinator.py`
- `advanced_web/stealth_browser.py`
- `transport/session_pool.py`

## Use When

- Managing browser fingerprints
- JA3/TLS fingerprint spoofing
- SOCKS5/Tor integration
- Rate limiting and timing attacks prevention

## Do Not Use When

- Purely local operations
- Testing without stealth requirements

## Key Components

- `StealthBrowser`: Headless browser with fingerprint spoofing
- `SessionPool`: HTTP client pool with JA3 support
- `curl_cffi`: TLS fingerprint impersonation

## OPSEC Layers

| Layer | Technology | Purpose |
|-------|-----------|---------|
| HTTP | curl_cffi | JA3 fingerprint spoofing |
| Browser | nodriver | WebDriver fingerprint elimination |
| Proxy | SOCKS5H | Remote DNS resolution |
| Fingerprint | Custom profiles | Chrome/Firefox profile simulation |

## Related Entries

- features/stealth-fetch
- modules/fetch-coordinator

# Session Pool

## Metadata

- **Entry Path:** modules/session-pool
- **Status:** current
- **Source:** transport/session_pool.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Unified HTTP session pool with support for httpx, httpx-socks, and curl_cffi.

## Source Paths

- `transport/session_pool.py`
- `transport/curl_cffi_runtime.py`

## Session Types

| Type | Library | Use Case |
|------|---------|----------|
| httpx | httpx | HTTP/2 clearnet |
| httpx_socks | httpx-socks | SOCKS5H proxy (Tor/I2P) |
| curl_cffi | curl_cffi | JA3 stealth fingerprinting |

## ISSUES

- **ISSUE-007**: aiohttp removed — httpx + httpx-socks for all HTTP needs
- **ISSUE-013**: Rust async query path removed (async_query.rs deleted)

## Usage

```python
from transport.session_pool import SessionPool

pool = SessionPool()

# HTTP/2 clearnet
client = await pool.httpx()

# SOCKS5H (remote DNS)
client = await pool.httpx_socks("socks5h://127.0.0.1:9050")

# JA3 stealth
ok, session, profile = await pool.curl_cffi("chrome136")
```

## Related Entries

- modules/fetch-coordinator
- modules/opsec-coordinator

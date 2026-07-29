# Public Fetcher — Architecture Reference

> **Source:** `fetching/public_fetcher.py` module docstring (extracted 2026-07-29).

## Role

Public-passive text/HTML fetcher using `curl_cffi` (primary) + `httpx` (HTTP/2).
Always-on, bounded, fail-soft, typed via `msgspec.Struct`.

## HTTP Transport Modernization (F4XX)

| Transport | Purpose |
|-----------|---------|
| `curl_cffi` (primary) | Stealth, JA3 fingerprint rotation |
| `httpx` (HTTP/2) | Native HTTP/2, `httpx-socks` for SOCKS5 |
| `httpx-socks` | Tor/I2P via `transport/session_pool.py:httpx_socks_client()` |

## Tor + Stealth Layer (P4)

- `.onion` domains routed via Tor SOCKS5 proxy (port 9050)
- Optional stealth mode via `StealthManager`
- Circuit renewal every `TOR_CIRCUIT_RENEWAL_REQUEST_COUNT` requests
- Random jitter before each request when using Tor/stealth

## Global State Refactoring (F-GLOBAL, 2026-06-30)

| Before | After |
|--------|-------|
| `_body_hashes` + `_body_hashes_lock` | `_BodyHashStore` class (encapsulated, `__slots__`) |
| `_js_renderer_capability` + lock | `_JSRendererCapability` class |
| `_DRAIN_REGISTRY` + `_DRAIN_TOTAL_*` | `_DrainRegistry` class (singleton, `__slots__`) |
| `_session_source_telemetry` | `_SessionManager._session_source_telemetry` (instance dict, `__slots__`) |

## See Also

- `transport/session_pool.py` — `httpx_socks_client()`
- `transport/stealth_manager.py` — `StealthManager`
- `fetching/curl_cffi_runtime.py` — curl_cffi HTTP runtime

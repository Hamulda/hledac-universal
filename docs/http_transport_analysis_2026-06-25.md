# HTTP Transport Architecture Analysis — 2026-06-25

## Current State

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ACTUAL TRANSPORT LAYER                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    public_fetcher.py (4268L)                         │  │
│  │              SINGLE ENTRY POINT — canonical fetch API                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                       │
│                    ┌───────────────┼───────────────┐                       │
│                    ▼               ▼               ▼                       │
│           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              │
│           │ curl_cffi    │ │    httpx     │ │    aiohttp   │              │
│           │ (PRIMARY)    │ │  (HTTP/2)    │ │   (fallback) │              │
│           └──────────────┘ └──────────────┘ └──────────────┘              │
│                    │               │               │                       │
│                    ▼               │               │                       │
│           ┌──────────────┐         │               │                       │
│           │ prewarm_pool │         │               │                       │
│           │ (4-slot)     │         │               │                       │
│           └──────────────┘         │               │                       │
│                    │               │               │                       │
│                    ▼               │               │                       │
│           ┌──────────────┐         │               │                       │
│           │conditional_  │         │               │                       │
│           │cache (LMDB)  │         │               │                       │
│           └──────────────┘         │               │                       │
│                    │               │               │                       │
│                    ▼               ▼               ▼                       │
│           ┌─────────────────────────────────────────────────────┐          │
│           │              http3_lane.py (30KB)                  │          │
│           │   curl_cffi opportunistic (Alt-Svc H3)             │          │
│           │        + aioquic stealth (opt-in)                  │          │
│           └─────────────────────────────────────────────────────┘          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Problems Identified

### P1: Legacy "subprocess curl" Still Listed as 🔴 Legacy
**Reality:** subprocess curl is **already removed** from `public_fetcher.py`. Only comments reference it.

```
$ grep -c 'subprocess\|Popen' public_fetcher.py
→ 0 actual calls (only 2 comment references)
```

The table in the user's request is **outdated**.

### P2: session_runtime (aiohttp) Labeled "⚠️ Zastaralé" but Still Wired
**Findings:**
- `network/session_runtime.py` — 403L, **no env gates**, pure aiohttp
- Imported by `public_fetcher.py` as **fallback path**
- Used for `tor_session` and `i2p_session` in `public_fetcher.py`
- Documented as "PLAIN TCP WORLD" — separate from curl_cffi world

**Problem:** Label says "zastaralé" but code still has active fallback logic.

### P3: httpx_transport Labeled "🔶 Sekundární" but Has Own HTTP/3 Path
**Findings:**
- `transport/httpx_transport.py` — 548L, 2 class, `HLEDAC_ENABLE_HTTPX_H2` gate
- `http3_lane.py` — 30KB, has BOTH curl_cffi opportunistic AND aioquic stealth
- `httpx` also used in `transport/base.py` and `transport/policy.py`

**Problem:** httpx is not just "secondary" — it's part of a parallel HTTP/3 strategy.

### P4: 9 Env Gates for HTTP in public_fetcher — Confusing Priority
```
HLEDAC_ENABLE_TOR, HLEDAC_ENABLE_CONTENT_LAYER, HLEDAC_ENABLE_HTTPX_H3,
HLEDAC_ENABLE_HTTPX_H2, HLEDAC_ENABLE_STEALTH_LAYER, HLEDAC_CONDITIONAL_CACHE,
HLEDAC_ENABLE_CURL_CFFI, HLEDAC_HTTP3, HLEDAC_ENABLE_HEAVY_BROWSER
```

### P5: Duplicate HTTP Transport Documentation
- `transport/base.py` — canonical doc, TP-1 through TP-4 invariants
- `public_fetcher.py` — extensive inline docs (F260, F261, F265B, F271, etc.)
- `network/session_runtime.py` — separate PLAIN TCP WORLD docstring
- These are **not synchronized**

## Architecture Reality

### Actual Call Chain (from analysis)
```
public_fetcher.py
    ├── curl_cffi_fetch (primary, JA3, prewarm, conditional cache)
    │       └── curl_cffi_runtime (session cache, per-host)
    │               └── prewarm_pool (4-slot ring)
    │               └── http3_lane (Alt-Svc H3 + aioquic)
    │
    ├── httpx_transport (secondary, HTTP/2, API-like)
    │       └── hishel (httpx cache, but disabled)
    │
    ├── session_runtime (aiohttp fallback for Tor/I2P)
    │       └── NOT used for clearnet (only Tor SOCKS / I2P SOCKS)
    │
    └── Lightpanda (JS renderer, curl_cffi fallback)
```

### What Each Transport Actually Does

| Transport | File | Role | Status |
|-----------|------|------|--------|
| curl_cffi | `transport/curl_cffi_fetch.py` | Primary stealth fetch | ✅ Optimalizováno |
| curl_cffi runtime | `transport/curl_cffi_runtime.py` | Session management | ✅ Optimalizováno |
| HTTP/3 | `transport/http3_lane.py` | QUIC upgrade | ✅ Always-on |
| prewarm | `transport/prewarm_pool.py` | TLS handshake elimination | ✅ Optimalizováno |
| conditional cache | `transport/conditional_cache.py` | ETag/304 short-circuit | ✅ Optimalizováno |
| httpx | `transport/httpx_transport.py` | HTTP/2 API fetch | ⚠️ Sekundární |
| aiohttp | `network/session_runtime.py` | Tor/I2P SOCKS fallback | ⚠️ Zastaralé |
| Lightpanda | `coordinators/fetch_coordinator.py` | JS rendering | 🔶 Novější |
| subprocess curl | N/A | **Already removed** | ✅ Není potřeba |

## Root Causes

1. **Historical evolution:** F260 (JA3 unification) moved primary to curl_cffi, but left aiohttp as fallback
2. **Parallel HTTP/3 strategies:** curl_cffi opportunistic + aioquic stealth created two paths
3. **Documentation drift:** Tables updated manually, code evolved separately
4. **No unified transport interface:** `public_fetcher.py` directly knows about all transports

## Solution: Rationalize to 3 Transport Lanes

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    UNIFIED TRANSPORT SELECTOR                                │
│                         (in public_fetcher.py)                              │
│                                                                             │
│  route_transport(url, context) → TransportDecision                          │
│                                                                             │
│  Priority:                                                                  │
│  1. .onion → tor_socks (curl_cffi through Tor SOCKS5H)                      │
│  2. .i2p/.b32.i2p → i2p_socks (curl_cffi through I2P SOCKS5H)             │
│  3. use_js=True → Lightpanda (JS rendering)                                │
│  4. stealth_retry → curl_cffi (with prewarm + conditional cache)            │
│  5. api_like + HTTP/3 available → httpx_h2 (HTTP/2 multiplexing)           │
│  6. default → curl_cffi (always-on, always best-effort)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Recommended Changes

#### 1. Mark session_runtime as DEPRECATED (not "zastaralé")
- Add `HLEDAC_ENABLE_AIOHTTP_FALLBACK=0` env gate (default to 0)
- Route Tor/I2P through curl_cffi SOCKS paths instead
- Keep as emergency fallback for M1 8GB memory pressure

#### 2. Merge httpx into curl_cffi world
- HTTP/3 via Alt-Svc is already in `http3_lane.py`
- `httpx_transport.py` only for explicit HTTP/2 API calls
- Merge `should_use_httpx_h2` into `route_transport()`

#### 3. Remove subprocess curl references
- Already removed from code
- Update documentation to reflect reality

#### 4. Add unified transport constants
```python
# transport/constants.py (new)
TRANSPORT_PRIORITY = {
    "tor": 1,
    "i2p": 2, 
    "js": 3,
    "stealth": 4,
    "http2": 5,
    "default": 6,
}
```

#### 5. Consolidate env gates
```
Current (9 gates):
HLEDAC_ENABLE_TOR, HLEDAC_ENABLE_CONTENT_LAYER, HLEDAC_ENABLE_HTTPX_H3,
HLEDAC_ENABLE_HTTPX_H2, HLEDAC_ENABLE_STEALTH_LAYER, HLEDAC_CONDITIONAL_CACHE,
HLEDAC_ENABLE_CURL_CFFI, HLEDAC_HTTP3, HLEDAC_ENABLE_HEAVY_BROWSER

Proposed (4 gates):
HLEDAC_ENABLE_HTTP3=1          # Enable Alt-Svc H3 upgrade (default ON)
HLEDAC_ENABLE_AIOHTTP_FALLBACK=0 # Enable legacy aiohttp fallback (default OFF)
HLEDAC_ENABLE_LIGHTPANDA=0     # Enable JS rendering (default OFF, RAM intensive)
HLEDAC_ENABLE_HTTP2_API=1      # Enable httpx HTTP/2 for API URLs (default ON)
```

### M1 8GB Considerations

**Current optimized transport memory:**
- prewarm_pool: 4 sessions ≈ 60 MB
- conditional_cache LMDB: 16 MB
- http3_lane LRU: negligible
- Total: ~80 MB

**Risk of consolidation:** Adding more transports increases memory. Keep curl_cffi as single primary.

## Implementation Plan

### Phase 1: Documentation Fix (low risk)
- Update table to remove subprocess curl
- Mark httpx as "HTTP/2 API lane" not "secondary"
- Mark aiohttp as "Tor/I2P only" not "zastaralé"

### Phase 2: Deprecate session_runtime (medium risk)
- Add `HLEDAC_ENABLE_AIOHTTP_FALLBACK=0`
- Route Tor/I2P through curl_cffi
- Keep session_runtime for emergency fallback

### Phase 3: Merge httpx into transport_router (high risk)
- Move `should_use_httpx_h2` into `route_transport()`
- Make httpx lane selection automatic based on URL patterns
- Remove `HLEDAC_ENABLE_HTTPX_H2` (always on when available)

## Test Plan
```bash
# Run existing probe tests
pytest tests/probe_curl_cffi_stealth_lane/ -x -q
pytest tests/probe_p14_prewarm_conditional/ -x -q
pytest tests/probe_p12_http3_lane/ -x -q

# Verify no regression in fetch paths
pytest tests/ -k "fetch" -x -q
```

## Files to Modify

| File | Change | Risk |
|------|--------|------|
| `docs/http_transport_analysis_2026-06-25.md` | This document | None |
| `fetching/public_fetcher.py` | Consolidate env gates | Medium |
| `network/session_runtime.py` | Mark deprecated, add env gate | Medium |
| `transport/httpx_transport.py` | Integrate into route_transport | High |
| `transport/transport_router.py` | Merge httpx decisions | High |

---

*Analysis completed: 2026-06-25*
*Author: Claude Code (autonomous analysis)*

# F_TRANSPORT_ROUTER_REALITY_MAP

**Date:** 2026-05-10
**Purpose:** Internal reality map for transport authority unification — F206AR transport router work.

---

## Fetch Entry Points

### 1. FetchCoordinator._fetch_url() — PRIMARY CANONICAL
**File:** `coordinators/fetch_coordinator.py`

| Property | Value |
|---|---|
| Session lifecycle owner | `FetchCoordinator` class |
| Circuit breaker | `_domain_failures` dict (simple counter) + `transport/circuit_breaker.py` state machine |
| Timeout | Per-transport: Tor=30s, I2P=30s, Clearnet=15s |
| max_bytes | None at this layer |
| CancelledError | Not explicitly re-raised — natural propagation |
| Telemetry | `_session_source_telemetry` (tor/i2p source tracking) |
| selected_transport | NOT set at FC level (only in httpx_transport lane) |

**Key:** `_fetch_url()` dispatches to curl_cffi / tor / i2p / httpx_h2 based on URL + transport flags. Calls `get_transport_for_url()` for onion/i2p classification.

---

### 2. public_fetcher module
**File:** `fetching/public_fetcher.py`

| Property | Value |
|---|---|
| Session lifecycle owner | Module-level singleton + `FetchCoordinator` shared state |
| Circuit breaker | None — delegates to `FetchCoordinator` |
| Timeout | `DEFAULT_TIMEOUT_S=15.0`, `TOR_STEALTH_TIMEOUT_SCALE=2.0` |
| max_bytes | None |
| CancelledError | Not explicitly re-raised |
| Telemetry | `_session_source_telemetry` updated per call |

**Key:** Uses shared `_tor_session`/`_i2p_session` with FetchCoordinator (module-level singletons).

---

### 3. httpx_transport (HTTPX H2 lane)
**File:** `transport/httpx_transport.py`

| Property | Value |
|---|---|
| Session lifecycle owner | Module-level `async_get_httpx_client()` singleton |
| Circuit breaker | `H2CircuitBreaker` (max 3 failures, auto-disable) |
| Timeout | httpx internal: connect=10s, read=20s |
| max_bytes | None |
| CancelledError | **Explicitly re-raised** [H2-A5] |
| Telemetry | `transport_fallback_reason` set on fallback; `get_httpx_capability_reason()` returns availability string |

**Gate:** `HLEDAC_ENABLE_HTTPX_H2=1` env var. Uses `httpx_client.py` singleton.

---

### 4. httpx_client
**File:** `transport/httpx_client.py`

| Property | Value |
|---|---|
| Session lifecycle owner | Module-level `async_get_httpx_client()` singleton |
| Circuit breaker | None (delegated to httpx_transport) |
| Timeout | connect=10s, read=20s |
| max_bytes | None |
| CancelledError | Not explicitly re-raised |
| Telemetry | `get_httpx_capability_reason()` |

**Note:** Lazy initialization, thread-safe lock.

---

### 5. tor_transport — DORMANT
**File:** `transport/tor_transport.py`

| Property | Value |
|---|---|
| Status | **DORMANT in production** — policy candidate only |
| Session lifecycle owner | `TorTransport` class (start/stop) |
| Circuit breaker | None |
| Timeout | Not set |
| max_bytes | None |
| CancelledError | Not handled |
| Telemetry | `transport_mode` string only |

**Production authority:** `FetchCoordinator._fetch_url()` — Tor is handled directly via SOCKS session management there.

---

### 6. i2p_transport
**File:** `transport/i2p_transport.py`

| Property | Value |
|---|---|
| Status | Standalone — not wired to FetchCoordinator |
| Session lifecycle owner | `I2PTransport` class |
| Circuit breaker | None |
| Timeout | Not set |
| max_bytes | None |
| CancelledError | Not handled |
| Telemetry | `transport_mode` string |

**Note:** Fail-open design (`available=True`), tries SOCKS→SAM→HTTP modes.

---

### 7. transport_resolver — DORMANT
**File:** `transport/transport_resolver.py`

| Property | Value |
|---|---|
| Status | **DORMANT** — `resolve()` is not production authority |
| Role | Classification only: `.onion`→TOR, `.i2p`→I2P |
| Session lifecycle | None (classification only) |
| Circuit breaker | None |
| Telemetry | `TransportContext` dataclass |

**Note:** `get_transport_for_url()` IS used by FetchCoordinator for onion/i2p classification.

---

### 8. pastebin_monitor — BYPASS CANDIDATE
**File:** `intelligence/pastebin_monitor.py`

| Property | Value |
|---|---|
| Status | **STANDALONE BYPASS** — owns its own session + circuit breaker |
| Session lifecycle owner | Module-level `_circuit` (`_CircuitState`), creates own `aiohttp.ClientSession` |
| Circuit breaker | `_CircuitState` (limit=5, reset=60s) — self-contained |
| Timeout | `_CLIENT_TIMEOUT=30.0` |
| max_bytes | None |
| CancelledError | Not explicitly re-raised |
| Telemetry | None |

**Bypass issue:** Creates `aiohttp.ClientSession` directly, NOT going through `FetchCoordinator` or `public_fetcher`. Has its own circuit breaker independent of canonical system.

---

### 9. archive_discovery — BYPASS CANDIDATE
**File:** `intelligence/archive_discovery.py`

| Property | Value |
|---|---|
| Status | **STANDALONE BYPASS** — owns its own archive session |
| Session lifecycle owner | `ArchiveResurrector` class |
| Circuit breaker | `_WaybackCircuitBreaker` (local class, 429/503 opens) |
| Timeout | `_WAYBACK_TIMEOUT_S=30.0` |
| max_bytes | `_MAX_PAYLOAD_BYTES=1MB` ✅ |
| CancelledError | Not explicitly re-raised |
| Telemetry | `archived_url`, `request_id` on `ArchivedSnapshot` |

**Bypass issue:** Creates its own aiohttp session for wayback/archive fetches, NOT going through FetchCoordinator. Has local circuit breaker.

---

### 10. acquisition_strategy — NOT a fetch entrypoint
**File:** `runtime/acquisition_strategy.py`

Sprint/lane configuration file. Defines acquisition profiles and feed dominance budgets. Does not perform HTTP fetches.

---

## Unified Telemetry Gap

| Telemetry field | FetchCoordinator | httpx_transport | pastebin_monitor | archive_discovery |
|---|---|---|---|---|
| `selected_transport` | ❌ | ✅ | ❌ | ❌ |
| `fallback_reason` | ❌ | ✅ | ❌ | ❌ |
| `transport_fallback_reason` | ❌ | ✅ (additive) | ❌ | ❌ |
| `session_source` | ✅ | ❌ | ❌ | ❌ |

---

## Circuit Breaker Inventory

| Entrypoint | Circuit Breaker | Type |
|---|---|---|
| FetchCoordinator | `_domain_failures` + `circuit_breaker.py` | Per-domain state machine |
| httpx_transport | `H2CircuitBreaker` | Max-3, auto-disable |
| pastebin_monitor | `_CircuitState` | Per-source (pastebin/rentry/etc) |
| archive_discovery | `_WaybackCircuitBreaker` | Per-domain (wayback) |
| tor_transport | None | — |
| i2p_transport | None | — |

---

## Key Invariants

1. **[H2-A5]** httpx_transport: `CancelledError` MUST be re-raised by caller
2. **[H2-A4]** httpx_transport: `transport_fallback_reason` is additive, never overwrites
3. **DORMANT flag:** tor_transport and transport_resolver are policy candidates only — not the production authority
4. **Session sharing:** public_fetcher and FetchCoordinator share module-level `_tor_session`/`_i2p_session` singletons
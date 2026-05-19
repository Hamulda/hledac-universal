# Transport Reliability / Stealth Audit

**Date:** 2026-05-18
**Scope:** `hledac/universal/transport/` + `fetching/public_fetcher.py` + `fetching/fetch_coordinator.py`
**Goal:** Map transport layers without refactoring — identify safe next-fix candidates and do-not-unify warnings.

---

## Matrix: Transport Capabilities by Layer

| Transport | Body Cap | Timeout | Cancellation | Retry/Backoff | Circuit Breaker | Dependency Extra | M1 Memory Risk |
|-----------|----------|---------|---------------|---------------|-----------------|-----------------|----------------|
| **curl_cffi** (FetchCoordinator) | ✅ 2MB default / 10MB hard via inline loop | Per-request `timeout_s` param | ✅ `asyncio.CancelledError` re-raised at every call site | ✅ `_is_retryable_status()` + `_compute_backoff_seconds()` cap 8s | ✅ Domain-level `CircuitBreaker` from `circuit_breaker.py`, checked before fetch | `curl_cffi` | LOW — stateless per-request |
| **httpx H2** (env-gated) | ❌ **DEFERRED** — `_max_bytes` param exists but is **NOT enforced** in `fetch_via_httpx_h2()` | `timeout_s` passed to `client.get()` | ✅ `CancelledError` propagates | ❌ No retry path — `fetch_via_httpx_h2` is fire-and-forget | ✅ `H2CircuitBreaker` class (separate from domain CB), auto-disable after 3 failures | `httpx` + `h2` | LOW — pooled client |
| **aiohttp** (fallback) | ✅ Chunked loop with `max_bytes` cap inline in `public_fetcher.py:1493-1508` | Per-request via `ClientTimeout(total=30)` | ✅ `CancelledError` re-raised | ✅ 403/429 triggers curl_cffi retry before JS fallback | ✅ Shared domain `CircuitBreaker` via `circuit_breaker.py` | `aiohttp` + `aiohttp_socks` | MEDIUM — session per transport |
| **Tor** | ✅ Via aiohttp session | 2.0s socket, 30s aiohttp total | ✅ Via aiohttp | ❌ No per-request retry — circuit renewal via `NEWNYM` control port signal | ❌ No circuit breaker — uses `MaxCircuitDirtiness=600` (Tor daemon config) | `stem` (optional, SOCKS check fallback) | MEDIUM — subprocess |
| **I2P SAM** | ✅ Via aiohttp session | 2.0s socket, 3.0s SAM readline, 30s aiohttp total | ✅ Via aiohttp | ❌ No retry | ❌ No circuit breaker | `aiohttp` + `aiohttp_socks` | MEDIUM — SAM session |
| **I2P SOCKS5** | ✅ Via aiohttp session | 2.0s socket, 30s aiohttp total | ✅ Via aiohttp | ❌ No retry | ❌ No circuit breaker | `aiohttp_socks` | MEDIUM — pooled session |
| **Nym** | ✅ Queue-based, bounded `max_queue_size=100` | ❌ No per-request timeout — health check loop 30s | ✅ `CancelledError` handled in drain loops | ❌ No retry | ✅ **Inline circuit breaker** in `nym_transport.py:39-42` — not shared with domain CB | `websockets` | MEDIUM — process + websocket |
| **Gopher** | ❌ Not implemented | ❌ Not implemented | ❌ Not implemented | ❌ Not implemented | ❌ Not implemented | N/A | N/A |
| **Router** (`transport_router.py`) | ✅ Delegates to selected transport | ✅ Delegates | ✅ Delegates | ✅ Delegates | ✅ Delegates | N/A | LOW — stateless |

---

## Finding 1: HTTPX Inline Cap Invariants — BROKEN

**File:** `transport/httpx_transport.py:364`

```python
_max_bytes: int = 2 * 1024 * 1024,  # noqa: F841  # reserved; body cap deferred — see TRANSPORT_COMMON_POLICY_AUDIT.md
```

`fetch_via_httpx_h2()` declares `_max_bytes` but **never uses it**. The body is returned as `httpx.Response` and the caller (`public_fetcher`) reads it via a chunked loop with cap — but this only applies when aiohttp is used, not when httpx H2 succeeds directly.

**Impact:** If httpx H2 path returns a large body (>2MB), the caller may receive untruncated content. The hard cap exists in the signature but is a no-op.

**Safe next fix:** Wire `body_limiter.read_body_with_cap()` after `response.stream()` in `fetch_via_httpx_h2()`, using `_max_bytes`. Requires adding `from .body_limiter import read_body_with_cap` and calling it before returning the response.

---

## Finding 2: curl_cffi Helper Usage — CORRECT

**File:** `fetching/public_fetcher.py:1628`

`curl_cffi.fetch_via_curl_cffi()` correctly passes `max_bytes=max_bytes`. The body cap is enforced inside the curl_cffi path. No duplication detected.

**Status:** ✅ No action needed.

---

## Finding 3: Nym Inline Circuit Breaker Duplication — CONFIRMED

**File:** `transport/nym_transport.py:39-42, 134-145, 161-179`

Nym has its own circuit breaker implementation (states: open/failures/timeout), completely **separate** from the shared `circuit_breaker.py` domain breaker used by `public_fetcher`.

```python
self.circuit_breaker_open = False
self.circuit_breaker_failures = 0
self.circuit_breaker_threshold = 3
self.circuit_breaker_timeout = 60
self.circuit_breaker_last_failure = 0.0
```

**Problems:**
1. **No integration** with `circuit_breaker.py:get_breaker()` — Nym CB state is invisible to `domain_breaker_check()`
2. **Different threshold** (3) vs domain CB (3) — currently aligned by coincidence
3. **Different timeout semantics** — Nym CB uses fixed 60s recovery; domain CB uses adaptive exponential backoff (BASE=30s, doubles on consecutive timeouts, max 300s)

**Safe next fix:** Refactor `NymTransport` to use `domain_breaker_check()` for domain-level decisions while keeping its own connection-state CB for websocket-level health. Or wire Nym's CB state into the shared registry via a named domain (e.g., `nym:websocket`).

**Do-not-unify warning:** Do NOT merge Nym's CB into the domain CB registry directly — the Nym CB operates at connection layer (websocket health), not HTTP domain layer. Different concerns.

---

## Finding 4: Timeout Fragmentation — 6 DISTINCT VALUES

| Transport | Timeout Value | Location |
|-----------|---------------|----------|
| aiohttp (fallback) | `ClientTimeout(total=30)` | `public_fetcher.py` (aiohttp path) |
| curl_cffi | `timeout=20.0` default | `fetch_coordinator.py` |
| I2P SAM readline | `asyncio.wait_for(..., timeout=3.0/5.0)` | `i2p_transport.py:184,191` |
| I2P socket | `s.settimeout(2.0)` | `i2p_transport.py:146,227` |
| I2P HTTP aiohttp | `ClientTimeout(total=30)` | `i2p_transport.py:327` |
| Nym health check | 30s loop interval | `nym_transport.py:213` |
| Tor SOCKS check | `s.settimeout(2.0)` | `tor_transport.py:153` |
| JARM fingerprint | `timeout=4.0` | `tor_transport.py:259` |
| httpx H2 | `timeout_s=20.0` default | `httpx_transport.py:363` |

**Fragmentation:** No centralized timeout policy. 9 different timeout values across 5 transport files.

**Risk:** Unbounded resource consumption if a transport hangs — no unified timeout enforcement.

**Safe next fix:** Add `transport/_common_timeout.py` with `DEFAULT_TIMEOUT_S = 20.0`, `I2P_TIMEOUT_S = 30.0`, `CIRCUIT_CHECK_TIMEOUT_S = 2.0`. Import from each transport file.

**Do-not-unify warning:** Do NOT make all timeouts identical — I2P SAM protocol needs longer readline timeouts (3-5s) than HTTP. Normalization should be by transport class, not global constant.

---

## Finding 5: Cancellation Handling — CORRECT BUT SCATTERED

**Files:** `public_fetcher.py:510-513, 571-574`, `fetch_coordinator.py` (multiple), `tor_transport.py` (via aiohttp), `nym_transport.py` (in drain loops)

Every async fetch path correctly re-raises `asyncio.CancelledError` — never swallows it.

```python
# public_fetcher.py:511-513
if "CancelledError" in error_str:
    raise asyncio.CancelledError("fetch cancelled")

# public_fetcher.py:572-574 (second occurrence — duplicate pattern)
if "CancelledError" in error_str:
    raise asyncio.CancelledError("fetch cancelled")
```

**Duplicate:** The same `CancelledError` re-raise pattern appears twice in `public_fetcher.py` (lines ~510 and ~571). Possible dead code or copy-paste artifact.

**Do-not-unify warning:** Do NOT extract to a shared helper that catches and re-raises — the current inline pattern is explicit and audit-friendly. A helper would hide the re-raise intent.

---

## Finding 6: Tor Circuit Breaker — SEPARATE SYSTEM

**File:** `transport/tor_transport.py` + `fetching/public_fetcher.py:740-763`

Tor uses `MaxCircuitDirtiness=600` (Tor daemon config, not code). Circuit renewal via `NEWNYM` signal is handled in `public_fetcher._maybe_renew_tor_circuit()`.

**Gap:** Tor circuit state is **not visible** to the `circuit_breaker.py` domain registry. If Tor is failing, the domain CB won't know — it only tracks aiohttp/curl failures.

**Risk:** Tor failures can cascade without triggering domain circuit breaker protection for `.onion` domains.

**Do-not-unify warning:** Do NOT merge Tor circuit state into domain CB — Tor is a transport-layer circuit, not an application-layer domain circuit. Different failure modes and recovery semantics.

---

## Finding 7: Retry/Backoff — CORRECTLY BOUNDED

**File:** `public_fetcher.py:338-378`

- `MAX_RETRIES = 1` (exactly one retry, no infinite loop)
- `backoff` capped at 8s via `_compute_backoff_seconds()`
- `Retry-After` header respected with 60s cap

**Status:** ✅ Correctly bounded.

---

## Finding 8: I2P Three-Mode Transport — NO CB PROTECTION

**File:** `transport/i2p_transport.py`

I2P has three modes (SAM, SOCKS5, HTTP) but none are protected by `circuit_breaker.py`. The `get_i2p_session()` function at module level is used by `public_fetcher` but has no CB integration.

**Gap:** `.i2p` domains can fail repeatedly without triggering circuit breaker. No `_record_failure()` for I2P-specific errors.

**Do-not-unify warning:** Do NOT add CB to I2P transport class directly — I2P session management is at the router level, not per-domain. CB belongs at `public_fetcher` level (already has it for aiohttp).

---

## Finding 9: Body Limiter — CORRECTLY SHARED

**File:** `transport/body_limiter.py`

`read_body_with_cap()` is a pure async helper with no transport coupling. Used by both curl_cffi and httpx lanes via inline loops (not imported). The comment `TODO(F226-body-cap)` at `public_fetcher.py:1494` confirms the duplication is known.

**Status:** ✅ `body_limiter.read_body_with_cap()` exists and is correct. The inline duplication in `public_fetcher.py` is the known debt.

---

## Summary: Safe Next Fix Candidates

| Priority | Fix | Risk | Rationale |
|----------|-----|------|-----------|
| **HIGH** | Wire `_max_bytes` into `fetch_via_httpx_h2()` body reading | LOW | Adds missing cap to httpx H2 path; `read_body_with_cap` already exists |
| **MEDIUM** | Deduplicate `CancelledError` re-raise in `public_fetcher.py` (~line 571) | LOW | Second occurrence may be dead code; first occurrence at ~510 is live |
| **MEDIUM** | Add timeout constants to `transport/_common_timeout.py` | LOW | Centralization reduces fragmentation; no behavioral change |
| **LOW** | Wire Nym CB state into shared `circuit_breaker.py` registry | MEDIUM | Requires careful layering — Nym CB is connection-level, domain CB is HTTP-level |
| **LOW** | Add I2P CB protection to `get_i2p_session()` | LOW | Small addition, only affects I2P SAM path |

---

## Do-Not-Unify Warnings

| Pair | Why Not Unified |
|------|-----------------|
| Nym CB ↔ domain CB | Nym CB is connection/websocket health; domain CB is HTTP domain reputation. Different failure semantics. |
| Tor circuit ↔ domain CB | Tor circuit is transport-layer circuit management; domain CB is application-layer failure isolation. Different recovery paths. |
| I2P session ↔ domain CB | I2P session management is router-level; CB belongs at fetch-coordination level. |
| `CancelledError` re-raise ↔ shared helper | Explicit inline pattern is audit-friendly; helper would hide re-raise intent. |
| Timeout values ↔ single constant | I2P SAM needs 3-5s readline timeouts; HTTP needs 20-30s. Normalize by class, not global. |
| Body cap ↔ unified transport | `body_limiter.read_body_with_cap()` is already the canonical helper; inline loops in `public_fetcher.py` are the known debt (TODO F226), not a structural issue. |

---

## Audit Completeness

- [x] httpx body cap invariants
- [x] curl_cffi helper usage
- [x] Nym circuit breaker duplication
- [x] timeout fragmentation
- [x] Tor/I2P/Nym proxy transport
- [x] cancellation handling
- [x] retry/backoff bounds
- [x] circuit breaker distribution
- [x] dependency extras by transport
- [x] M1 memory risk by transport
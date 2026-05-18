# Transport Common Policy Duplication Audit

**Date:** 2026-05-18
**Sprint:** F226 — Transport Policy Audit
**Files in scope:** `transport/` (all), `tests/probe_*transport*`

---

## 1. Duplication Matrix

### 1.1 CancelledError Handling

| File | Line | Pattern | Re-raise? | fail-soft? |
|------|------|---------|-----------|------------|
| `base.py` | TP-5 docstring | "CancelledError is always re-raised by transport functions" | ✅ | ✅ |
| `curl_cffi_fetch.py` | 70 | `except asyncio.CancelledError: raise` | ✅ | ✅ |
| `curl_cffi_runtime.py` | 183 | `except asyncio.CancelledError:` + re-raise | ✅ | ✅ |
| `httpx_transport.py` | 133 | `if isinstance(exc_or_result, asyncio.CancelledError): raise exc_or_result` [H2-A5] | ✅ | ✅ |
| `transport_router.py` | TR-6 docstring | "CancelledError is NOT handled — caller must re-raise" | ✅ | ✅ |
| `nym_transport.py` | 105,127,174,201 | `except asyncio.CancelledError:` (4 sites) | ✅ | ✅ |
| `inmemory_transport.py` | 32,74 | `except asyncio.CancelledError:` | ✅ | ✅ |
| `i2p_transport.py` | — | No explicit CancelledError handling | N/A | N/A |

**Verdict:** ✅ CONSISTENT — all explicit handlers re-raise. Invariant is well-documented.

---

### 1.2 Body Cap / max_bytes

| File | max_bytes default | Enforcement method | Correct? |
|------|-------------------|-------------------|----------|
| `base.py` TransportConfig | 2,000,000 (2MB) | Not enforced at base level | N/A |
| `curl_cffi_fetch.py` | 10,485,760 (10MB) | `bytearray.extend()` + in-place `del content_bytes[max_bytes:]` | ✅ O(1) amortized |
| `httpx_transport.py` | 2,097,152 (2MB) `_max_bytes` param | **RESERVED, NOT USED** | ❌ GAP |
| `gopher_transport.py` | 1,048,576 (1MB) | `while total_size < max_bytes:` loop | ✅ |
| `transport_router.py` | 0 (passthrough) | Delegates to transport layer | N/A |

**Verdict:** ⚠️ PARTIALLY CLOSED — `public_fetcher.py:1493-1522` implements inline body cap for HTTPX. `curl_cffi_fetch.py:94-95` uses `read_body_with_cap` helper (✅). HTTPX path has same cap logic but with subtle semantic diffs:
  - Helper truncates in-place (`del content_bytes[max_bytes:]`); inline reads partial chunk at boundary (`_chunk[:_remaining]`) — both O(1) amortized.
  - Helper treats `max_bytes<=0` as unbounded (reads all); inline has edge-case: `max_bytes=0` → `text=None, fetched_bytes=0` (silent zero-read).
  - Helper returns `tuple[bytes, bool]`; inline returns full `FetchResult` with `error="size_cap_exceeded"` — different return type prevents drop-in replacement.
  - Helper has no `declared_length` tracking; inline hardcodes `declared_length=-1` (pre-existing — HTTPX doesn't surface Content-Length).
  **Future refactor:** Extend `read_body_with_cap` with `declared_length` param and error-envelope return variant. Then replace inline in public_fetcher.py. See §1.2 Future Work below.

### 1.2 Future Work: HTTPX Body Cap Full Closure

The inline HTTPX body cap in `public_fetcher.py:1493-1522` cannot be directly replaced by the current `read_body_with_cap` helper due to:

1. **Return type mismatch** — helper returns `tuple[bytes, bool]`; inline returns a full `FetchResult` with `error="size_cap_exceeded"`. A drop-in replacement requires either extending `read_body_with_cap` to also return an error signal (e.g., `tuple[bytes, bool, str | None]`), or having the caller wrap the helper output in the error envelope.

2. **`declared_length` tracking** — helper has no `declared_length` param/return; inline hardcodes `declared_length=-1`. HTTPX's `aiter_chunked()` does not surface Content-Length header, so `-1` is correct. Future helper could accept `declared_length: int = -1` and return it unchanged.

3. **`max_bytes=0` edge case** — helper treats 0 as "no cap" (reads all chunks); inline treats 0 as "cap at 0" (zero-read, returns `text=None`). Behavior should be documented explicitly before any change.

**Refactor plan:**
- Step 1: Add `declared_length: int = -1` param to `read_body_with_cap`, return it in a 3-tuple `(bytes, bool, int)`.
- Step 2: Change inline in `public_fetcher.py` to call helper and wrap in `FetchResult` error envelope.
- Step 3: Verify behavior preservation with existing probe tests.

---

### 1.3 Timeout

| File | timeout_s default | Mechanism | Invariant |
|------|------------------|-----------|-----------|
| `base.py` TransportConfig | 35.0 | Not enforced at base level | N/A |
| `curl_cffi_fetch.py` | 10.0 | `session.get(..., timeout=timeout_s)` | [TP-5] |
| `httpx_transport.py` | 20.0 | `client.get(..., timeout=timeout_s)` | [H2-A5] |
| `i2p_transport.py` | 2.0–30.0 (mixed) | `settimeout(2.0)`, `asyncio.wait_for(..., timeout=3.0/5.0)` | — |
| `nym_transport.py` | 1.0–10.0 (mixed) | `asyncio.wait_for(..., timeout=N)` | — |
| `tor_transport.py` | 4.0 | `asyncio.open_connection(..., timeout=4.0)` | — |
| `gopher_transport.py` | 30.0 | `asyncio.open_connection(..., timeout=timeout_s)` | — |

**Verdict:** ⚠️ FRAGMENTED — each transport has hardcoded defaults. No shared timeout policy helper. However, `httpx_client.py` manages the overall pool timeout (connect=10s, read=20s, write=10s, pool=10s). Not a pressing deduplication need since each transport has fundamentally different I/O models.

---

### 1.4 network_error_kind Taxonomy

| File | Function | Taxonomy |
|------|----------|----------|
| `curl_cffi_fetch.py` | inline error classification | `timeout`, `connection_refused`, `dns_failure`, `connection_reset`, `too_many_redirects`, `other` |
| `httpx_transport.py` | `classify_httpx_h2_error()` | `pool_timeout`, `read_timeout`, `connect_timeout`, `remote_protocol_error`, `too_many_connections`, `tls_error`, `http_403`, `http_429`, `http_5xx`, `protocol_error`, `unknown_httpx_error` |

**Verdict:** ❌ INCOMPATIBLE TAXONOMIES — these two sets are mutually exclusive. `curl_cffi_fetch` uses network-layer categories (DNS, connection, TCP), while `httpx_transport` uses application-layer categories (HTTP status, pool, TLS). Unifying would require a breaking change to `TransportResult.network_error_kind` contract. **Not a good extraction candidate** without a major version bump.

---

### 1.5 Connection Pooling

| File | Pool type | Limits |
|------|-----------|--------|
| `httpx_client.py` | `httpx.AsyncClient` singleton | `max_connections=25`, `max_keepalive_connections=10`, `keepalive_expiry=30.0` |
| `i2p_transport.py` | Per-connector `aiohttp.ClientSession` | Per-session, per-connector |
| `tor_transport.py` | `aiohttp.ClientSession` (direct + tor) | Per-session |

**Verdict:** ✅ APPROPRIATELY SEPARATE — each transport lib (httpx, aiohttp, curl_cffi) has its own pooling lifecycle. No shared extraction makes sense here.

---

### 1.6 Circuit Breaker

| File | CB implementation | State machine? |
|------|-------------------|----------------|
| `circuit_breaker.py` | Full `CircuitBreaker` class | ✅ CLOSED/OPEN/HALF_OPEN |
| `nym_transport.py` | Simple inline CB (lines 50–53, 235–236) | ❌ Simple flag + counter |

**Verdict:** ⚠️ DUPLICATION — `nym_transport.py` has its own simple CB with `circuit_breaker_open`, `circuit_breaker_failures`, `circuit_breaker_threshold=3`, `circuit_breaker_timeout=60`. This should use `circuit_breaker.py` instead. However, this is a production refactor — out of scope for this audit commit.

---

## 2. Invariant Verification

### [TP-5] CancelledError re-raised ✅
All 7 explicit CancelledError sites re-raise. No silent swallows.

### [H2-A5] httpx CancelledError re-raised ✅
`httpx_transport.py:133` explicitly re-raises before any classification.

### [TR-6] Router CancelledError passthrough ✅
Router does not catch — caller must handle.

### Body cap consistency ⚠️ PARTIAL
`public_fetcher.py:1493-1522` implements inline body cap for HTTPX (same logic as helper, O(1) amortized). `curl_cffi_fetch.py:94-95` uses `read_body_with_cap` helper (✅). HTTPX inline has `max_bytes=0` edge-case bug (silent zero-read) and hardcodes `declared_length=-1`. Full closure requires extending helper — see §1.2 Future Work below.

### timeout_s from TransportConfig ❌
`httpx_transport.py:420` passes `timeout=timeout_s` to `client.get()`, but `curl_cffi_fetch.py:87` also passes `timeout=timeout_s`. However, the defaults differ (10s vs 20s). No shared timeout policy enforces `TransportConfig.timeout_s` as a ceiling.

---

## 3. Extraction Candidates

### Priority 1: `transport/body_limiter.py` (PURE HELPER, no I/O)

**Why:** `curl_cffi_fetch.py:90-97` has clean, well-commented body capping code. httpx lane does NOT enforce body cap (real gap). A shared helper enables both lanes to use the same enforcement.

```python
# O(1) amortized — F206K: bytearray.extend() not bytes +=
async def read_body_with_cap(response, max_bytes: int) -> tuple[bytes, bool]:
    """Read response body with hard cap. Returns (body, was_truncated)."""
```

**Characterization tests needed:** Yes — pure helper with no I/O, testable in isolation.

### Priority 2: `transport/network_errors.py` (TAXONOMY ONLY, no I/O)

**Why:** Both `curl_cffi_fetch` and `httpx_transport` classify errors. The curl_cffi taxonomy (network-layer: DNS, TCP, connection) is appropriate for `TransportResult.network_error_kind`. httpx taxonomy (application-layer: pool, TLS, HTTP status) serves a different purpose and should remain in `httpx_transport.py` as `classify_httpx_h2_error`.

**However:** This would require unifying on one taxonomy. Given the mutual incompatibility, defer until `network_error_kind` contract is stabilized.

### Priority 3: NymTransport → `circuit_breaker.py` integration

**Why:** NymTransport has a simplified CB that should use the shared `CircuitBreaker`. However, this is a **production refactor** — out of scope for the audit commit.

---

## 4. Recommended First Extraction

**File:** `transport/body_limiter.py`
**Type:** Pure helper (no I/O, no network calls)
**Tests:** `tests/test_transport_body_limiter.py` (new characterization tests)

**Rationale:**
1. Solves a real gap (httpx doesn't cap body)
2. Pure function — no production behavior change risk
3. Testable in isolation without mocks
4. `bytearray.extend()` + in-place `del` is the correct F206K pattern already implemented in curl_cffi

**What to extract:**
```python
# From curl_cffi_fetch.py:90-97
async def read_body_with_cap(response, max_bytes: int, *, chunk_size=65536):
    """Read async iterable with hard cap at max_bytes. O(1) amortized."""
    content_bytes = bytearray()
    async for chunk in response.iter_content(chunk_size=chunk_size):
        content_bytes.extend(chunk)
        if len(content_bytes) > max_bytes:
            del content_bytes[max_bytes:]
            return bytes(content_bytes), True
    return bytes(content_bytes), False
```

**What NOT to extract yet:**
- ConnectionPool policy (each lib has different lifecycle)
- TimeoutPolicy (different I/O models)
- network_error_kind classifier (incompatible taxonomies)
- Nym CB → circuit_breaker refactor (production change)

---

## 5. Tests to Run

```bash
pytest tests/probe_transport_cap_2026 tests/probe_transport_policy_f206ar tests/probe_transport_fetch_cb_f206as tests/probe_transport_authority_f206az tests/probe_transport_authority_f206bc tests/probe_transport_bypass_f206ax tests/probe_transport_resolver_f206av tests/probe_f206ar_transport_router tests/probe_stealth_transport_plan_f206ay -v
```

---

## 6. Out of Scope

- ConnectionPool abstraction (httpx/aiohttp/curl_cffi have incompatible lifecycles)
- TimeoutPolicy (each transport's I/O model is too different)
- network_error_kind unification (incompatible taxonomies, would break `TransportResult` contract)
- NymTransport circuit breaker refactor (production change, separate PR)
- HTTPHandler base class (not needed — `TransportAdapter` ABC already exists in `base.py`)
- Changes to `transport_router.py` lane selection
- Changes to Tor/I2P/Nym/proxy behavior

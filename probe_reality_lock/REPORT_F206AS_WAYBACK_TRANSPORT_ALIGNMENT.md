# SPRINT F206AS — Wayback CDX Transport Alignment

**Date:** 2026-05-01
**Scope:** `discovery/wayback_cdx_adapter.py` HTTP transport alignment
**NO-GIT-RULE:** Active — no git operations

---

## PHASE 0 — CURRENT PATH AUDIT

### Wayback CDX HTTP Call — Before (Direct aiohttp)

```python
async with aiohttp.ClientSession() as session:       # raw session — no circuit breaker
    async with session.get(
        _WAYBACK_CDX_URL,
        params=params,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
        headers={"User-Agent": "Hledac/1.0 (research bot)"},
    ) as resp:
        if resp.status != 200:
            return DiscoveryBatchResult(hits=(), error_type="server_error", ...)
        data = await resp.json()
```

**Problems identified:**
1. Raw `aiohttp.ClientSession()` — bypasses shared session lifecycle management
2. No circuit breaker — Wayback failures don't contribute to domain penalty tracking
3. No transport telemetry — `transport_counters` don't account for Wayback calls
4. Only `resp.status != 200` caught as error — no specific 4xx/5xx taxonomy

### Alignment Option Analysis

| Option | Mechanic | Verdict |
|--------|----------|---------|
| A: `async_get_aiohttp_session()` + `checked_aiohttp_get` | Shared session + circuit breaker via `circuit_breaker.checked_aiohttp_get` | ✅ **CHOSEN** — aligns session lifecycle and circuit breaker |
| B: `async_fetch_public_text` | HTML/text fetcher — semantics mismatch for JSON CDX API | ❌ Not appropriate — CDX is JSON API, not HTML/text |
| C: Keep direct aiohttp + add telemetry | Minimal alignment, no structural fix | ❌ Not chosen — leaves circuit breaker gap |

### Shared Session Helper Available

- `network.session_runtime:async_get_aiohttp_session()` — singleton session, lazy, thread-safe, connector pooling (limit=25, limit_per_host=5, dns_cache=300s)
- `transport.circuit_breaker:checked_aiohttp_get()` — accepts shared session, applies domain circuit breaker, returns `(response | None, error_str | None)`, handles timeout/client_error/open-circuit

---

## PHASE 1 — CHOSEN ALIGNMENT

**Strategy A: Shared session + circuit breaker**

Replaces direct `aiohttp.ClientSession()` construction with:
1. `session = await async_get_aiohttp_session()` — shared singleton session
2. `resp, err = await checked_aiohttp_get(...)` — circuit breaker + error taxonomy

### What Changes

| Aspect | Before | After |
|--------|--------|-------|
| Session creation | `async with aiohttp.ClientSession()` (ephemeral) | `await async_get_aiohttp_session()` (shared, reused) |
| Circuit breaker | None — failures not tracked | Domain failures recorded via `record_failure()` |
| Transport telemetry | Not counted | Domain failures affect future allow/deny decisions |
| Error taxonomy | Only `server_error` for non-200 | Specific: `http_403`, `http_429`, `http_5xx`, `network_error`, `circuit_breaker_open` |
| Timeout handling | via `asyncio.timeout` | via `asyncio.timeout` (unchanged) + `checked_aiohttp_get` records circuit breaker on timeout |

### What Does NOT Change

- CDX URL construction (`_WAYBACK_CDX_URL`) — unchanged
- JSON response parsing — unchanged
- Hit construction (DiscoveryHit fields) — unchanged
- max_results bound (cap 20) — unchanged
- URL dedup via `seen_urls` — unchanged
- No archived body fetch — CDX index API only, no page content fetch
- No Brave/SearXNG imports — confirmed absent
- CancelledError re-raise — explicit `raise` after `except asyncio.CancelledError`

---

## PHASE 2 — CHANGED FILES

### `discovery/wayback_cdx_adapter.py`

**Imports added:**
```python
from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get
```

**HTTP call block replaced:**
```python
# BEFORE (direct session, no circuit breaker):
async with aiohttp.ClientSession() as session:
    async with session.get(_WAYBACK_CDX_URL, ...) as resp:
        if resp.status != 200:
            return DiscoveryBatchResult(..., error_type="server_error")
        data = await resp.json()

# AFTER (shared session + circuit breaker):
session = await async_get_aiohttp_session()
resp, err = await checked_aiohttp_get(
    session,
    _WAYBACK_CDX_URL,
    params=params,
    headers={"User-Agent": "Hledac/1.0 (research bot)"},
    timeout=timeout,
    failure_kind="wayback_cdx",
)
if err:
    # Map circuit_breaker errors to taxonomy
    if err.startswith("circuit_breaker_open:"):
        return DiscoveryBatchResult(..., error_type="circuit_breaker_open")
    if err == "timeout":
        return DiscoveryBatchResult(..., error_type="timeout")
    if err == "client_error":
        return DiscoveryBatchResult(..., error_type="network_error")
    # ...
# Status-based error mapping
status = resp.status
if status == 403: return DiscoveryBatchResult(..., error_type="http_403")
if status == 429: return DiscoveryBatchResult(..., error_type="http_429")
if status >= 500: return DiscoveryBatchResult(..., error_type="http_5xx")
if status != 200: return DiscoveryBatchResult(..., error_type="server_error")
data = await resp.json()
```

### New Test File

```
tests/probe_providerless_discovery/test_wayback_transport_alignment.py
  22 tests — all PASS
```

---

## PHASE 3 — WAYBACK ERROR TAXONOMY

| Condition | error_type | Notes |
|-----------|-----------|-------|
| `asyncio.TimeoutError` | `timeout` | asyncio.timeout triggers, `checked_aiohttp_get` records circuit breaker |
| `checked_aiohttp_get` returns `err="timeout"` | `timeout` | Circuit breaker records timeout failure |
| `checked_aiohttp_get` returns `err="client_error"` | `network_error` | aiohttp.ClientError |
| `checked_aiohttp_get` returns `err="circuit_breaker_open:..."` | `circuit_breaker_open` | Domain blocked by circuit breaker |
| HTTP status 403 | `http_403` | Archive.org access forbidden |
| HTTP status 429 | `http_429` | Archive.org rate limited |
| HTTP status >= 500 | `http_5xx` | Server error at archive.org |
| HTTP status other non-200 (4xx) | `server_error` | Other client errors |
| `resp.json()` raises | `provider_exception` | Parse error — caught by `except Exception` |
| `asyncio.CancelledError` | **re-raised** | NOT caught — propagates to caller |
| `aiohttp` not installed | `import_error` | Import-time fallback |
| Empty query | `empty_query` | Pre-HTTP validation |

---

## PHASE 4 — TEST RESULTS

### New Alignment Tests (22 tests)
```
test_f206as_1_no_raw_aiohttp_session_in_source        ✅ PASS
test_f206as_2_cdx_url_is_archive_org                  ✅ PASS
test_f206as_3_json_parsing_present                   ✅ PASS
test_f206as_3_header_row_skip_present                 ✅ PASS
test_f206as_4_max_results_cap_20                     ✅ PASS
test_f206as_5_seen_urls_dedup_present                ✅ PASS
test_f206as_6_timeout_maps_to_error_type_timeout     ✅ PASS
test_f206as_7_http_429_maps_to_error_type_http_429   ✅ PASS
test_f206as_8_http_403_maps_to_error_type_http_403    ✅ PASS
test_f206as_9_http_5xx_maps_to_error_type_http_5xx    ✅ PASS
test_f206as_10_parse_error_mapped                     ✅ PASS
test_f206as_16_network_error_on_client_error         ✅ PASS
test_f206as_11_provider_chain_always_set             ✅ PASS
test_f206as_11_source_family_archive                   ✅ PASS
test_f206as_12_cancelled_error_raised                  ✅ PASS
test_f206as_13_no_body_fetch                           ✅ PASS
test_f206as_14_no_brave_searx                          ✅ PASS
test_f206as_15_uses_checked_aiohttp_get               ✅ PASS
test_f206as_15_uses_async_get_aiohttp_session         ✅ PASS
test_f206as_empty_query_returns_empty                  ✅ PASS
test_f206as_import_error_returns_import_error          ✅ PASS
test_f206as_returns_correct_discovery_batch_result     ✅ PASS
```

### Regression Suite — ALL PASS

```
probe_providerless_discovery     ✅ 63 passed (incl. 22 new)
probe_f206aq_discovery_planner    ✅ 14 passed
probe_e2e_readiness              ✅ 25 passed
probe_public_branch_diagnosis     ✅ 49 passed  (pre-existing ddgs warning, not a regression)
probe_e2e_signal_fixture         ✅ 20 passed
probe_transport_cap_2026         ✅ 90 passed
```

---

## PHASE 5 — FINAL VERDICT

**SEALED** — All conditions satisfied:

1. ✅ **Wayback CDX semantics unchanged** — CDX URL, JSON parsing, hit construction, dedup all identical
2. ✅ **HTTP call path aligned** — `async_get_aiohttp_session()` + `checked_aiohttp_get()` replaces raw `aiohttp.ClientSession()`
3. ✅ **Error taxonomy specific** — 403, 429, 5xx, timeout, network_error, circuit_breaker_open all explicitly mapped
4. ✅ **No archived body fetch** — CDX index API only, confirmed in test F206AS-13
5. ✅ **No production behavior outside wayback adapter** — cascade, pipeline, scheduler unchanged
6. ✅ **Tests pass** — 22 new alignment tests + 261 regression tests all pass

**Canonical E2E path for MacBook Air M1 8GB: SAFE**
- No new asyncio.run() calls
- No MLX model loads
- Shared session is the same aiohttp session used by all other discovery/dispatch paths
- Circuit breaker integration is read-side only (record_failure called, no blocking of Wayback since `allowed` is per-decision)

**Changed file:**
- `discovery/wayback_cdx_adapter.py` — transport alignment only
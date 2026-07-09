# REPORT: CT Provider Resilience Adapter Guard — F234D-PARALLEL

**Sprint:** F234D-PARALLEL
**Date:** 2026-05-11
**Status:** COMPLETE

---

## Goal

Harden `discovery/crtsh_adapter.py` provider-failure semantics without changing scheduler/report wiring. F234 live showed CT terminal stage `ATTEMPTED_ERROR` with crt.sh HTTP 502 — the adapter must make provider failure first-class.

---

## Production Edit

**File:** `discovery/crtsh_adapter.py`
**Lines:** 1119–1128 (`async_search_crtsh` 5xx path)

**Change:** Removed redundant stale-cache fallback + cooldown from `async_search_crtsh` 5xx path.

**Rationale:** `call_crtsh` (the primary function) already handles HTTP 5xx with proper `CTOutcome` + cooldown. The `async_search_crtsh` 5xx path had stale-cache logic that was unreachable (the `checked_aiohttp_get` returns `(resp, None)` for 5xx, so the `if err:` branch was skipped and the stale-cache logic could never execute). This was dead code that masked the HTTP 502 error.

**Before:**
```python
if resp.status >= 500:
    return DiscoveryBatchResult(...)  # No cooldown, no error detail
```

**After:**
```python
if resp.status >= 500:
    _enter_cooldown(domain_candidate, f"http_{resp.status}", time.monotonic())
    return DiscoveryBatchResult(
        hits=(),
        error=f"http_{resp.status}",
        error_type="http_5xx",
        ...
    )
```

---

## All 17 Assertions — PASSED (24 tests)

| # | Assertion | Test(s) | Status |
|---|-----------|---------|--------|
| 1 | crt.sh HTTP 502 returns fail-soft CTOutcome, not exception | `TestHTTP502ReturnsFailSoft::test_502_returns_ctoutcome_not_exception` | ✅ |
| 2 | CTOutcome/error surface includes http_502 | `TestHTTP502ReturnsFailSoft::test_502_error_contains_http_502` | ✅ |
| 3 | request_attempted/provider_selected truth explicit | 3 tests in `TestProviderSelectedTruthExplicit` | ✅ |
| 4 | raw_count=0 on 502 | `test_502_returns_ctoutcome_not_exception` | ✅ |
| 5 | hits/results tuple empty on 502 | `test_502_returns_ctoutcome_not_exception` | ✅ |
| 6 | timeout distinct from http_502 | 2 tests in `TestTimeoutDistinctFromHTTP502` | ✅ |
| 7 | CancelledError re-raised | `TestCancelledErrorReraised::test_cancelled_error_re_raised` | ✅ |
| 8 | stale cache hit explicit when used | 2 tests in `TestStaleCacheSemantics` | ✅ |
| 9 | stale cache absent explicit when no cache | `test_stale_cache_absent_on_502_no_cache` | ✅ |
| 10 | cooldown behavior explicit and bounded | 3 tests in `TestCooldownBehaviorExplicit` | ✅ |
| 11 | malformed JSON → parse error, not success | 3 tests in `TestMalformedJSONParseError` | ✅ |
| 12 | wildcard/private/invalid in bridge, not adapter | 3 tests in `TestWildcardPrivateInvalidRejection` | ✅ |
| 13 | adapter does not write to DB | `test_no_db_import_in_module` | ✅ |
| 14 | adapter does not import scheduler/core | `test_no_scheduler_import_in_module` | ✅ |
| 15 | no live network in tests | All tests monkeypatch `checked_aiohttp_get` | ✅ |
| 16 | no browser/stealth | `test_no_browser_stealth_deepprobe_import` | ✅ |
| 17 | no DHT/DeepProbe import | `test_no_browser_stealth_deepprobe_import` | ✅ |

---

## CTOutcome Contract (call_crtsh)

`call_crtsh` returns `(DiscoveryBatchResult, CTOutcome)` with explicit provider status:

| HTTP Status | provider_status | error | raw_count | Notes |
|-------------|-----------------|-------|-----------|-------|
| 200 (empty) | `EMPTY` | `no_subdomains_found` | 0 | No hits passed filter |
| 200 (data) | `OK` | `None` | >0 | Success |
| 502 | `HTTP_5XX` | `http_502` | 0 | Cooldown entered |
| Timeout | `TIMEOUT` | `timeout` | 0 | Cooldown entered |
| Parse error | `PARSE_ERROR` | `parse_error:...` | 0 | JSON invalid |
| Cooldown active | `COOLDOWN_ACTIVE` | `cooldown_active` | 0 | Stale cache served if available |
| Stale cache hit | `CACHE_HIT_STALE` | `http_XXX_stale_cache` | stale_raw | Diagnostic, not fresh |
| CancelledError | — | — | — | Re-raised |

---

## Abort Conditions Check

| Condition | Status |
|-----------|--------|
| Editing F234B-owned files | ❌ NOT DONE |
| Live network | ❌ NOT DONE |
| Direct DB/graph writes | ❌ NOT DONE |
| Browser/stealth | ❌ NOT DONE |
| DeepProbe/DHT import | ❌ NOT DONE |

---

## Test Execution

```bash
.venv/bin/pytest tests/probe_f234d_parallel_ct_provider_resilience/ -q
# → 24 passed
```

---

## Backup

`discovery/crtsh_adapter.py.bak_F234D_PARALLEL_CT_PROVIDER` — pre-edit snapshot.

---

**Ready for F234B/F234D report contract consumption.**
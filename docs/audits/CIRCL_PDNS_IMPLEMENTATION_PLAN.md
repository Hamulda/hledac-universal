# Sprint F229 — CIRCL PDNS Passive DNS Implementation Plan

**Audit file:** `docs/audits/CIRCL_PDNS_IMPLEMENTATION_PLAN.md`
**Date:** 2026-05-18
**Commit:** `docs(discovery): plan CIRCL passive DNS adapter`
**Status:** PLANNED — do not implement yet

---

## 1. Background

The OSINT Next-Capability Prioritization audit (`OSINT_NEXT_CAPABILITY_PRIORITIZATION.md`) scored CIRCL PDNS as the top unimplemented OSINT capability (9/10).

**Actual current state (verified via source):**

| Component | Location | Status |
|---|---|---|
| `call_lookup_passive_dns` | `security/passive_dns.py:348` | ✅ **EXISTS** — already wired, already queries CIRCL |
| `_run_pdns_prelude_lane` | `sprint_scheduler.py:5547` | ✅ **EXISTS** — fully implemented, calls `call_lookup_passive_dns` |
| `passive_dns_results_to_findings` | `source_finding_bridge.py:1058` | ✅ **EXISTS** — already wired as converter |
| `compute_lane_eligibility` | `nonfeed_candidate_ledger.py:1025` | ✅ **EXISTS** — passive_dns eligibility already correct |
| `query_circl_pdns` | `ti_feed_adapter.py:550` | ⚠️ Simplified `list[dict]` version (not used by main pipeline) |

**The "gap" narrative in the first draft of this plan was wrong.** `call_lookup_passive_dns` already queries CIRCL PDNS at `https://www.circl.lu/pdns/query/{domain}` and returns `(list[str], PassiveDNSOutcome)`. The pipeline is already wired.

**What IS actually missing (real gap identified by review):**

1. **Transport seam misalignment** — `call_lookup_passive_dns` creates its own `aiohttp.ClientSession` directly (line 280), not using `async_get_aiohttp_session()` from `network.session_runtime` or `checked_aiohttp_get()` from `transport.circuit_breaker`. This breaks the uniform transport policy.

2. **No `circl_pdns_adapter.py`** — no `DiscoveryBatchResult`-returning adapter exists in `discovery/`. The `ti_feed_adapter.py::query_circl_pdns` is a simplified `list[dict]` version, not a proper adapter registered in `discovery/source_registry.py`.

3. **No cooldown/cache provider status** — `call_lookup_passive_dns` returns `PassiveDNSOutcome` but has no provider status tracking (`PDNSProviderStatus`) like crtsh_adapter has `CTProviderStatus`.

---

## 2. Real Implementation Goal

**Not** "create a new parallel path to CIRCL". Instead:

1. **Align `security/passive_dns.py::call_lookup_passive_dns`** to use `async_get_aiohttp_session()` + `checked_aiohttp_get()` like crtsh_adapter (transport seam)
2. **Create `discovery/circl_pdns_adapter.py`** as a proper discovery adapter returning `DiscoveryBatchResult` + `PDNSProviderStatus`
3. **Register in `source_registry.py`** as a named source for source-tier classification
4. **No changes needed to** `sprint_scheduler.py`, `acquisition_strategy.py`, `nonfeed_candidate_ledger.py`, or `source_finding_bridge.py` — those are already correct

---

## 3. Files to Create/Modify

---

## 3. Files to Create/Modify

### 3.1 `discovery/circl_pdns_adapter.py` (NEW — ~250 lines)

Pattern: mirrors `discovery/crtsh_adapter.py` exactly.

**Public API:**
```python
__all__ = ["async_search_circl_pdns", "call_circl_pdns", "PDNSOutcome", "PDNSProviderStatus"]
```

**`PDNSOutcome` dataclass** — already exists as `PassiveDNSOutcome` in `security/passive_dns.py`. Reuse or alias.

**`PDNSProviderStatus` dataclass** (mirrors `CTProviderStatus`):
```python
@dataclass
class PDNSProviderStatus:
    provider_name: str = "circl_pdns"
    cooldown_active: bool = False
    cooldown_reason: Optional[str] = None
    cooldown_remaining_s: float = 0.0
    cooldown_started_at_monotonic: float = 0.0
    provider_attempt_suppressed: bool = False
    built_count: int = 0
    accepted_count: int = 0
    duration_s: float = 0.0
    skip_reason: Optional[str] = None
```

**`async_search_circl_pdns(domain, timeout_s=5.0) -> DiscoveryBatchResult`**:
- Use `async_get_aiohttp_session()` from `network.session_runtime`
- Use `checked_aiohttp_get()` from `transport.circuit_breaker`
- CIRCL endpoint: `https://www.circl.lu/pdns/query/{domain}`
- CIRCL returns **plain text**, one JSON record per line (not JSON array)
- Parse each line: `json.loads(line)` → extract `rrname`, `rrtype`, `rdata`, `time_first`, `time_last`
- Map to `DiscoveryHit` with `source_type="circl_pdns"`
- CIRCL rate limit: 30 req/min → `await asyncio.sleep(2.0)` between calls (single call, no loop)
- Timeout: default 5s, configurable via `timeout_s`

**`call_circl_pdns(domain, timeout_s=5.0) -> Tuple[DiscoveryBatchResult, PDNSOutcome]`**:
- Thin wrapper returning both `DiscoveryBatchResult` and `PDNSOutcome`
- Cooldown: `PDNS_COOLDOWN_DEFAULT_S = 300`, `MAX_PDNS_COOLDOWN_KEYS = 64`, FIFO eviction
- Cache: optional stale cache fallback (same pattern as crtsh_adapter F217D)
- Rate limit sleep: `asyncio.sleep(2.0)` before call (single, not in loop)

**Key design decisions:**
| Decision | Rationale |
|---|---|
| Plain text, one JSON per line | CIRCL PDNS returns `\n`-delimited JSON, not JSON array |
| Rate limit sleep 2.0s | CIRCL ~30 req/min → conservative 1 req/2s (single call, no loop) |
| `DiscoveryBatchResult` return | Compatible with source_registry/source_tier classification |
| Cooldown keyed by domain | Prevents hammering same domain repeatedly |
| No API key | CIRCL PDNS community tier is keyless |

### 3.2 `security/passive_dns.py` — Transport Seam Alignment (MODIFY)

**Change**: Replace direct `aiohttp.ClientSession` creation in `call_lookup_passive_dns` (line 280) with `async_get_aiohttp_session()` + `checked_aiohttp_get()` pattern.

**Rationale**: All other discovery adapters (crtsh, wayback, duckduckgo) use the canonical session_runtime + circuit_breaker transport seam. `call_lookup_passive_dns` currently bypasses this.

**Change location**: `security/passive_dns.py:278-292` (the `else: async with aiohttp.ClientSession` branch)

**After**: use `async_get_aiohttp_session()` + `checked_aiohttp_get()` exactly like crtsh_adapter.py does.

**Keep everything else**: DoH resolver, circuit breaker preflight, `PassiveDNSOutcome`, rate limit sleep, `lookup_passive_dns` function — all unchanged.

### 3.3 `discovery/source_registry.py` — Adapter Registration (MODIFY)

Register `circl_pdns_adapter` as a named source in the source tier:
```python
"circl_pdns": SourceEntry(
    adapter=circl_pdns_adapter,
    tier=1,  # structured, deterministic
    acquisition_lane="passive_dns",
)
```

This enables source-tier classification, provenance tracking, and consistent observability.

---

## 4. No Changes Required To (Verified Correct)

The following are already correctly wired — no changes needed:

| File | Reason |
|---|---|
| `runtime/sprint_scheduler.py:5547` | `_run_pdns_prelude_lane` already calls `call_lookup_passive_dns` + `passive_dns_results_to_findings` |
| `runtime/acquisition_strategy.py` | No `_run_pdns_prelude_lane` exists here — plan's earlier claim was wrong |
| `runtime/nonfeed_candidate_ledger.py:1050` | `passive_dns` eligibility already `bool(has_domain or has_ip)` |
| `runtime/source_finding_bridge.py:1058` | `passive_dns_results_to_findings` already exists and is wired |
| `discovery/ti_feed_adapter.py:550` | Deprecated simplified version — do not wire, not used by main pipeline |

---

## 5. Lane Call Chain (Verified Working)

```
sprint_scheduler.py:_run_pdns_prelude_lane (line 5547)
  └── call_lookup_passive_dns(domain)          → security/passive_dns.py:348
        └── CIRCL PDNS API (https://www.circl.lu/pdns/query/{domain})
        └── returns (list[str], PassiveDNSOutcome)
  └── passive_dns_results_to_findings(ips, outcome, query, sprint_id)
        └── source_finding_bridge.py:1058
        └── returns (CanonicalFinding list, rejections, telemetry)
  └── duckdb_store.async_ingest_findings_batch(findings)
```

The pipeline is complete. The missing piece is the transport seam alignment in `security/passive_dns.py` + the proper `circl_pdns_adapter.py` for source tier registration.

---

## 6. Fixture Tests — `tests/probe_f229_circl_pdns_adapter/`

Structure mirrors `tests/probe_f217d_crtsh_provider_resilience/` and `tests/probe_f219e_crtsh_cooldown/`.

### 5.1 Test cases

| Test | Description | Fixture |
|---|---|---|
| `test_valid_response` | CIRCL returns structured JSON lines → `DiscoveryBatchResult` with hits | `valid.json` fixture |
| `test_empty_response` | CIRCL returns 404 / empty body → empty result, no error | `empty.json` fixture |
| `test_timeout` | HTTP timeout → empty result, `outcome.timeout=True` | `TimeoutError` mock |
| `test_http_500` | HTTP 503 → empty result, `outcome.error="http_503"` | Mock 503 response |
| `test_parse_error` | Malformed JSON line → skip bad line, return good hits | Mixed valid/malformed fixture |
| `test_domain_normalization` | Uppercase/lowercase domain → same result | Mixed case fixture |
| `test_rate_limit_sleep` | Verify 2.0s sleep between requests | Mock time, no actual sleep |
| `test_cooldown_active` | Domain in cooldown → skip, return empty, `skip_reason` set | Pre-loaded cooldown state |
| `test_private_ip_rejected` | RFC1918 IPs in response → filtered out, counted in telemetry | Private IP fixture |
| `test_empty_ip_rejected` | Empty/whitespace IP → filtered, counted in telemetry | Empty IP fixture |
| `test_duplicate_ip_rejected` | Duplicate IPs within batch → deduped | Duplicate IPs fixture |
| `test_converter_output_shape` | `passive_dns_results_to_findings` returns correct tuple shape | `CanonicalFinding` fields check |
| `test_converter_public_ip_accepted` | Public IPs → accepted as candidates, `pdns_public_accepted` telemetry | Public IP fixture |
| `test_outcome_timeout_flag` | `PDNSOutcome.timeout=True` when timeout | Timeout mock |
| `test_outcome_duration_s` | `PDNSOutcome.duration_s` populated | Timer check |
| `test_max_results_cap` | >50 hits → capped at 50 | Large fixture |

### 5.2 Fixture files

```
tests/probe_f229_circl_pdns_adapter/
├── __init__.py
├── conftest.py
├── test_adapter.py
└── fixtures/
    ├── valid.json          # CIRCL plain-text, one JSON per line
    ├── empty.json          # Empty body (404)
    ├── malformed.json       # Mixed valid + malformed lines
    ├── private_ips.json    # RFC1918 IPs
    ├── public_ips.json     # Public routable IPs
    └── duplicate_ips.json   # Duplicate IPs
```

**`valid.json` fixture format** (CIRCL PDNS plain-text, one JSON per line):
```
{"rrname":"example.com.","rrtype":"A","rdata":"93.184.216.34","count":5,"time_first":"2024-01-01T00:00:00Z","time_last":"2025-01-01T00:00:00Z"}
{"rrname":"example.com.","rrtype":"AAAA","rdata":"2606:2800:220:1::248a:1893","count":3,"time_first":"2024-01-15T00:00:00Z","time_last":"2025-02-01T00:00:00Z"}
```

---

## 6. Safety Requirements

| Requirement | Implementation |
|---|---|
| No API key | CIRCL PDNS community tier is keyless |
| Fail-soft empty result | Any error → return `([], outcome)` with telemetry |
| Timeout bounded | `aiohttp.ClientTimeout(total=5.0)` default, configurable |
| No live network in tests | All tests use `aiohttp.response` mocks + static fixtures |
| M1 memory negligible | No GPU, no MLX, single JSON parse, ~20MB |
| Rate limit protection | `asyncio.sleep(2.0)` between calls |
| Cooldown eviction | FIFO at `MAX_PDNS_COOLDOWN_KEYS=64` |
| Domain normalization | Strip whitespace, lowercase before API call |

---

## 7. Implementation Order (Corrected After Review)

1. **`discovery/circl_pdns_adapter.py`** — implement `async_search_circl_pdns`, `PDNSProviderStatus`, `call_circl_pdns` (cooldown, cache)
2. **`security/passive_dns.py`** — align `call_lookup_passive_dns` transport seam: replace direct `aiohttp.ClientSession` creation with `async_get_aiohttp_session()` + `checked_aiohttp_get()` (line ~280)
3. **`discovery/source_registry.py`** — register `circl_pdns` as a named source with tier=1
4. **`tests/probe_f229_circl_pdns_adapter/`** — all 17 fixture tests
5. **Run probe suite** — `pytest tests/probe_f229_circl_pdns_adapter/ -v`

---

## 8. GHOST_INVARIANTS Compliance

| Invariant | How satisfied |
|---|---|
| `gather_return_exceptions=True` | `asyncio.gather(result, return_exceptions=True)` on adapter calls |
| `mx.eval([]) before clear_cache` | N/A — no MLX in PDNS path |
| `time.monotonic for intervals` | All cooldown/duration tracking uses `time.monotonic()` |
| No bare `except` | All adapter methods wrapped in `try/except Exception as e: ...` |
| `fail-safe throughout` | Any error returns `([], outcome)` — never propagates |

---

## 9. Existing Code — What to Keep vs. Replace vs. Align

| File | Current State | Action |
|---|---|---|
| `security/passive_dns.py` | DoH resolver + CIRCL stub via direct `aiohttp.ClientSession` | **Align transport seam** - use `async_get_aiohttp_session()` + `checked_aiohttp_get()` |
| `security/passive_dns.py::call_lookup_passive_dns` | Returns `(list[str], PassiveDNSOutcome)` | Keep signature - is the production caller |
| `discovery/ti_feed_adapter.py::query_circl_pdns` | Simplified `list[dict]` version | Keep as-is (not used by main pipeline) |
| `runtime/source_finding_bridge.py::passive_dns_results_to_findings` | Already complete and wired | Keep as-is |
| `runtime/nonfeed_candidate_ledger.py` | Eligibility already correct | No changes |
| `runtime/sprint_scheduler.py:5547` | `_run_pdns_prelude_lane` fully implemented | No changes - already correct |

---

## 10. Diff Sketch (Corrected After Review)

### `discovery/circl_pdns_adapter.py` (new file, ~250 lines)
```
discovery/
+ circl_pdns_adapter.py   # NEW - mirrors crtsh_adapter.py pattern
```

### `security/passive_dns.py` (transport seam alignment only)
```python
# REPLACE direct session creation in call_lookup_passive_dns (line ~280):
- else:
-     async with aiohttp.ClientSession(timeout=timeout) as session:
-         async with session.get(url) as resp:
+ else:
+     session = await async_get_aiohttp_session()
+     resp, err = await checked_aiohttp_get(
+         session, url,
+         timeout=aiohttp.ClientTimeout(total=15),
+         failure_kind="circl_pdns",
+     )
+     if err or resp.status != 200:
+         ...
```

### `discovery/source_registry.py` (register adapter)
```python
+ from .circl_pdns_adapter import async_search_circl_pdns
+ "circl_pdns": SourceEntry(
+     adapter=async_search_circl_pdns,
+     tier=1,
+     acquisition_lane="passive_dns",
+ ),
```

### `tests/probe_f229_circl_pdns_adapter/` (new directory)
```
tests/
+ probe_f229_circl_pdns_adapter/
    __init__.py
    conftest.py
    test_adapter.py
    fixtures/
        valid.json
        empty.json
        ...
```

---

## 11. Exit Criteria

- [ ] `discovery/circl_pdns_adapter.py` exists with `async_search_circl_pdns(domain) -> DiscoveryBatchResult` and `PDNSProviderStatus`
- [ ] `security/passive_dns.py::call_lookup_passive_dns` uses `async_get_aiohttp_session()` + `checked_aiohttp_get()` (transport seam aligned)
- [ ] `discovery/source_registry.py` registers `circl_pdns` as a named source with tier=1
- [ ] 17 probe tests in `tests/probe_f229_circl_pdns_adapter/` pass
- [ ] No live network calls in tests (all `aiohttp.response` mocked)
- [ ] GHOST_INVARIANTS satisfied
- [ ] Zero new dependencies added to `pyproject.toml`
- [ ] No changes to `sprint_scheduler.py`, `acquisition_strategy.py`, `nonfeed_candidate_ledger.py`, or `source_finding_bridge.py` (already correct)

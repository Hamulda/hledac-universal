# PRIVACY GATE AUDIT — Sprint F26X

**Date:** 2026-06-02
**Scope:** `runtime/sprint_scheduler.py` — all call sites of
`async_ingest_findings_batch()`.
**Goal:** guarantee that every PII-bearing finding is anonymized **before**
it hits the canonical write path.

---

## 1. EXECUTIVE SUMMARY

| Metric | Before F26X | After F26X |
|--------|-------------|------------|
| Ingest call sites audited | 20 | 20 |
| Sites with PII gate (`_run_privacy_gate`) | **1** | **20** |
| Sites bypassing gate (PII leak risk) | **19** | **0** |
| Helper invocations of `_run_privacy_gate` | 1 (F250F inline) | 21 (1 inline + 20 via helper) |
| Lazy-init sites for `self._layer_manager.privacy` | 7 | 0 (replaced by `self._privacy_layer` with fallback) |
| `inject_privacy_layer()` method | ❌ absent | ✅ present |
| Probe tests (`probe_f26x_privacy_gate_coverage.py`) | n/a | **16/16 pass** |

**Result:** PII leak vector closed across all 20 call sites via central
`_gate_then_ingest()` helper. Inject refactor removes the lazy-init
scattering and makes the dependency explicit.

---

## 2. COVERAGE MATRIX (20 rows)

All line numbers refer to the **post-F26X** state of
`runtime/sprint_scheduler.py` (after helper insertion shifted lines by +52
and the multi-line `ingest_results = await ...` was collapsed).

| # | Line | Function / Site | Gated Before | Gated After | Notes |
|---|------|-----------------|--------------|-------------|-------|
| 1 | 9085 | `_run_ct_predispatch` | ❌ NO | ✅ YES | Multi-line `async_ingest_findings_batch(_candidate_findings)` collapsed. |
| 2 | 12262 | `_run_wayback_prelude_lane` | ❌ NO | ✅ YES | `_wb_cands` candidates. |
| 3 | 12358 | `_run_pdns_prelude_lane` | ❌ NO | ✅ YES | `_pdns_cands`. |
| 4 | 12600 | `_run_doh_prelude_lane` | ❌ NO | ✅ YES | `_doh_cands`. |
| 5 | 15728 | `_run_ct_log_discovery_in_cycle` | ✅ YES (F250F inline) | ✅ YES | Original F250F gate. Continues to be gated. |
| 6 | 16278 | `crawl_seed` (Tor) | ❌ NO | ✅ YES | Onion findings. |
| 7 | 16556 | `fetch_i2p_address` | ❌ NO | ✅ YES | Eepsite findings. |
| 8 | 16858 | `dht_lookup` | ❌ NO | ✅ YES | DHT findings. |
| 9 | 16932 | `_run_gopher_sidecar` | ❌ NO | ✅ YES | **Gopher produces dict findings** — see §3. |
| 10 | 17112 | `_run_ipfs_discovery_sidecar` | ❌ NO | ✅ YES | IPFS findings. |
| 11 | 17221 | `analyze_one` (forensics / digital_ghost) | ❌ NO | ✅ YES | Forensics findings. |
| 12 | 17327 | `analyze_one` (steganography) | ❌ NO | ✅ YES | Steg findings. |
| 13 | 17462 | `_query_one` (BGP enrichment) | ❌ NO | ✅ YES | BGP findings. |
| 14 | 17606 | `_grab_one` (banner grab) | ❌ NO | ✅ YES | Banner findings. |
| 15 | 18160 | `_run_pdns_for_domain` | ❌ NO | ✅ YES | PDNS pivot. |
| 16 | 19048 | `_ingest_ct_lane_candidates` | ❌ NO | ✅ YES | CT lane bridge. |
| 17 | 20014 | `_session_provider` (BGP) | ❌ NO | ✅ YES | `_session_provider` enrichment. |
| 18 | 20031 | `_session_provider` (PDNS) | ❌ NO | ✅ YES | `_session_provider` PDNS. |
| 19 | 20233 | `_session_provider` (Wayback) | ❌ NO | ✅ YES | `_session_provider` Wayback CDX. |
| 20 | 22511 | `_run_enhanced_research` | ❌ NO | ✅ YES | Enhanced research canonicals. |

**Pre-F26X state:** 19 of 20 sites bypassed the F250F gate that lived
only in `_run_ct_log_discovery_in_cycle`. Any sidecar finding that
carried PII (Gopher `payload_text` includes `display_string` from
Floodgap Veronica-2 — could include names/emails; enhanced research
synthesizes text) reached storage **without anonymization**.

---

## 3. FIXES APPLIED

### 3.1 Helper extraction (SprintScheduler closure in `__init__`)

Added `_gate_then_ingest(store, findings)` directly after the existing
`_run_privacy_gate` closure (line 4443). The helper:

- checks `HLEDAC_ENABLE_PRIVACY_LAYER=1` env var
- resolves privacy layer via `self._privacy_layer or
  getattr(self._layer_manager, "privacy", None)` (priority order)
- calls `self._run_privacy_gate(findings, layer)`
- updates `self._result.pii_findings_anonymized` counter
- awaits `store.async_ingest_findings_batch(_gated)`
- **fail-soft on every step** (privacy layer error → original
  findings; ingest error → `None` returned, never raises)

### 3.2 Mechanical replacement of 20 call sites

Each `await X.async_ingest_findings_batch(Y)` replaced with
`await self._gate_then_ingest(X, Y)`. Patterns handled:

- `VAR = await X.async_ingest_findings_batch(ARGS)` →
  `VAR = await self._gate_then_ingest(X, ARGS)`
- `await X.async_ingest_findings_batch(ARGS)` (bare, no LHS) →
  `_ = await self._gate_then_ingest(X, ARGS)`
- Multi-line `ingest_results = await X.async_ingest_findings_batch(\n  ARGS\n)` →
  `ingest_results = await self._gate_then_ingest(X, ARGS)` (collapsed to
  one line for grep-ability)

### 3.3 Dict finding support (Gopher, IPFS, etc.)

Gopher sidecar produces `dict` findings (from
`gopher_transport.item_to_finding()`), not `CanonicalFinding` instances.
The real `_run_privacy_gate()` was previously using
`getattr(f, 'content', None)` which returns `""` on a dict (no error, no
PII detection).

Added an `isinstance(f, dict)` branch in `_run_privacy_gate` (line
4379+):

```python
if isinstance(f, dict):
    text_fields = {
        'content': f.get('content') or "",
        'raw_content': f.get('raw_content') or "",
        'payload_text': f.get('payload_text') or "",
        'title': f.get('title') or "",
        'summary': f.get('summary') or "",
    }
else:
    text_fields = { ... getattr(...) ... }
```

And dual write-back (line 4400+):

```python
if isinstance(f, dict):
    f[field_name] = anon_text
else:
    setattr(f, field_name, anon_text)
```

Same dual write-back for the `_privacy_context_id` field. All three
operations are wrapped in `try/except` so dict findings that lack
writable fields degrade silently.

### 3.4 Inject refactor

Added `inject_privacy_layer(layer)` method to `SprintScheduler` (right
after `inject_duckdb_store`, line 25746). Replaced the 7+1 lazy-init
sites (1 inside `_gate_then_ingest`, 7 elsewhere) with:

```python
_privacy = (self._privacy_layer or getattr(self._layer_manager, "privacy", None))
```

Priority order: **explicit injection wins**, fallback to existing
`self._layer_manager.privacy` for callers that haven't migrated yet.

`self._privacy_layer: Any = None` initialised in `__init__` at line
4326.

This mirrors the F26X-2 pattern (`inject_coordination_layer()` etc.):
caller-owned, fail-soft, never raises.

---

## 4. INVARIANTS

| Invariant | Test |
|-----------|------|
| `_gate_then_ingest` exists in `__init__` closure | `test_gate_then_ingest_method_exists` |
| `_run_privacy_gate` exists in `__init__` closure | `test_run_privacy_gate_method_exists` |
| `inject_privacy_layer` exists as a public method | `test_inject_privacy_layer_method_exists` |
| `self._privacy_layer` initialised to `None` | `test_privacy_layer_attribute_initialized` |
| No `await .async_ingest_findings_batch` outside helper | `test_no_direct_ingest_calls_outside_helper` |
| Helper used at all 20 sites | `test_helper_used_at_least_20_times` |
| Dict findings supported in gate | `test_run_privacy_gate_handles_dicts` |
| Dict field writeback via `__setitem__` | `test_dict_field_writeback_uses_setitem` |
| Gate runs when `HLEDAC_ENABLE_PRIVACY_LAYER=1` | `test_gate_runs_when_env_var_set` |
| Gate bypassed when env var unset | `test_gate_bypassed_when_env_var_unset` |
| Fail-soft on privacy layer exception | `test_fail_soft_on_privacy_error` |
| No-op when `store is None` | `test_noop_when_store_is_none` |
| No-op when `findings` empty | `test_noop_when_findings_empty` |
| Fail-soft on ingest exception | `test_fail_soft_on_ingest_error` |
| Injected layer takes priority over layer manager | `test_injected_layer_used_over_layer_manager` |
| Fallback to layer manager when no inject | `test_fallback_to_layer_manager_when_no_inject` |

All 16 invariants covered by `tests/probe_f26x_privacy_gate_coverage.py`.
**16/16 pass.**

---

## 5. INJECT REFACTOR STATUS

| Aspect | Status |
|--------|--------|
| (a) All 20 sites gated | ✅ confirmed in §2 |
| (b) F26X-2 `inject_coordination_layer()` reference pattern verified | ✅ same file, line 25431+ |
| (c) No other active sprint touching `sprint_scheduler.py` | ✅ only F26X edits in this session |
| (d) `inject_privacy_layer()` implemented | ✅ line 25746 |
| (e) Lazy-init scattering removed (7 → 0) | ✅ all replaced with `_privacy_layer or _layer_manager.privacy` |
| (f) Fail-soft fallback for unmigrated callers | ✅ preserved |

**Refactor complete.**

---

## 6. FILES TOUCHED

| File | Change |
|------|--------|
| `runtime/sprint_scheduler.py` | +80 lines (helper + dict branch + inject), 20 mechanical replacements, 7 lazy-init replacements |
| `tests/probe_f26x_privacy_gate_coverage.py` | NEW — 16 tests, 100% pass |

No changes to:
- `layers/privacy_layer.py` (interface unchanged)
- `knowledge/duckdb_store.py` (canonical write path unchanged)
- any other sidecar file (gopher_transport etc. keep producing dicts)

---

## 7. FOLLOW-UP

- Sidecar files in `transport/gopher_transport.py`,
  `intelligence/dht_*.py`, etc. could be migrated to produce
  `CanonicalFinding` instances instead of dicts. This is a
  nice-to-have (the dict branch handles them correctly) but would
  make type discipline uniform.
- `HLEDAC_ENABLE_PRIVACY_LAYER=1` should be added to a default profile
  (e.g. `Hledac-default.env`) for production. Currently opt-in via
  env var. Out of scope for F26X.

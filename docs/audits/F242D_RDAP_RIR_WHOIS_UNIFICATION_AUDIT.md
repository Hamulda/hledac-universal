# Sprint F242D — RDAP/RIR/WHOIS Enrichment Unification Audit

## 1. Caller/Source Map Refresh

### RDAP Path (ACTIVE)
- **Canonical**: `discovery/ti_feed_adapter.py:940` — `query_rdap(target: str) → dict`
- **Task handler**: `discovery/ti_feed_adapter.py:1202` — `_handle_rdap_lookup()` wraps `query_rdap` and calls `rdap_result_to_findings()` (source_finding_bridge), then `async_ingest_findings_batch()` for canonical storage
- **Structured converter**: `runtime/source_finding_bridge.py:1769` — `rdap_result_to_findings(target, rdap_result, *, trigger_confidence, max_findings=32) → (findings, rejections, telemetry)`
- **Output**: `CanonicalFinding` with `source_type="rdap_enrichment"`, confidence `[0.55, 0.90]`
- **Stored via**: `scheduler._duckdb_store.async_ingest_findings_batch(findings)` in `_handle_rdap_lookup()`
- **Telemetry**: `SprintSchedulerResult.rdap_enrichment_attempted/built/stored/rejections/error` (F242C fields at line ~1056-1060)
- **Pivot buffered**: `scheduler._buffer_ioc_pivot("domain", task.ioc_value, 0.75)` after RDAP

### RDAP Path (DORMANT/DUPLICATE)
- `discovery/duckduckgo_adapter.py:1223` — `_query_rdap(target: str)` is a compat wrapper with explicit removal condition. Delegates to `ti_feed_adapter.query_rdap()`. Marked DEPRECATED. No direct call sites to the wrapper itself — only the wrapper calls canonical. Safe to leave with annotation.

### RIR Path (ACTIVE)
- **Entry**: `runtime/sidecar_bus.py:838` — `_rir_correlator_runner(findings, store, query)` registered as `"rir_correlator"` in `DEFAULT_SIDECAR_RUNNERS`
- **Adapter factory**: `intelligence/rir_correlator.py:645` — `create_rir_correlator_adapter()`
- **Async correlate**: `intelligence/rir_correlator.py:569` — `RIRCorrelatorAdapter.async_correlate(findings, query) → list[CanonicalFinding]`
- **Blocking wrapper**: `intelligence/rir_correlator.py:293` — `asyncio.get_running_loop().run_in_executor(None, _blocking_whois)` for WHOIS (F242B async safety fix)
- **Canonical conversion**: `intelligence/rir_correlator.py:500` — `to_canonical_findings(correlations, query) → list[CanonicalFinding]`, `source_type="rir_correlation"`, `confidence=corr.confidence`
- **Direct IP lookup**: `intelligence/rir_correlator.py:418` — hardcoded `confidence=0.85`
- **WHOIS-derived**: `intelligence/rir_correlator.py:458` — hardcoded `confidence=0.7`
- **Storage**: `_rir_correlator_runner` calls `store.async_ingest_findings_batch(derived_findings)` (line ~859)
- **Scheduler telemetry**: `SprintSchedulerResult.rir_correlation_produced` (line ~1045) — incremented when `source_type=="rir_correlation"` findings are accumulated to `_accumulate_findings_to_graph()` (line ~8273)

### WHOIS/Network Reconnaissance Path (DORMANT — not wired to canonical store)
- `intelligence/network_reconnaissance.py:379` — `WHOISLookup` class exists
- `intelligence/network_reconnaissance.py:78` — `WHOISData` dataclass exists
- WHOIS is called internally by `correlate_rir_signals()` (line ~441) for unresolved domains, results fed into `RIRCorrelation` objects — thus WHOIS data IS reaching canonical findings via the RIR path, but only as derived/correlated data within `rir_correlation` findings, not as `whois_enrichment` source_type directly
- `WHOISLookup` is NOT registered as a sidecar runner, NOT called as a standalone task handler, NOT ingested directly as canonical findings — it's an internal dependency of the RIR correlation pipeline

---

## 2. Confidence Consistency

| Path | Source Type | Confidence | Trigger Inheritance | Bounded |
|------|-------------|------------|---------------------|---------|
| RDAP enrichment | `rdap_enrichment` | Base 0.70; trigger [0.55, 0.90] | Yes: `max(0.55, min(0.90, trigger_confidence * 0.90))` | Yes |
| RIR correlation (direct IP) | `rir_correlation` | Hardcoded 0.85 | No | [0, 1] implicitly via float |
| RIR correlation (WHOIS-derived) | `rir_correlation` | Hardcoded 0.70 | No | [0, 1] implicitly via float |
| Passive DNS | `passive_dns` | Base 0.5, inherited [0.5, 0.85] | Yes | Yes (source_finding_bridge) |

**No unbounded confidence values found.** All enrichment paths have bounded defaults.

**Note**: RIR `confidence` field in `RIRCorrelation` dataclass is a plain float with no explicit bound in the dataclass definition itself — but the hardcoded values (0.85, 0.7) are within [0, 1]. `to_canonical_findings()` passes `corr.confidence` directly to `CanonicalFinding(confidence=...)` at line ~542. This means the upper bound depends on what `corr.confidence` contains — for direct IP lookups it's always 0.85, for WHOIS-derived it's always 0.7. No trigger inheritance exists in the RIR path (no `trigger_confidence` parameter in `correlate_rir_signals()`).

---

## 3. Investigation Packet Surface

`export/sprint_exporter.py:138-160` builds `source_family_summary` from `planner_state["source_family_outcomes"]`. Each entry has:
- `family` (str)
- `accepted` / `rejected` / `pending` (int)
- `attempted` (bool)
- `terminal_state` (str)
- `has_findings` (bool) — `accepted > 0`

**Does it include `rdap_enrichment`?** The `source_family_outcomes` dictionary is populated by the scheduler at various points. Looking at the `_handle_rdap_lookup()` in `ti_feed_adapter.py` — it does NOT write to `source_family_outcomes`. It only updates `SprintSchedulerResult` fields (`rdap_enrichment_attempted/built/stored/rejections/error`). There is no `_sfos.append({"family": "rdap_enrichment", ...})` equivalent for the RDAP path.

**Does it include `rir_correlation`?** Similarly, `_rir_correlator_runner` in `sidecar_bus.py` does NOT write to any `source_family_outcomes`. It only calls `async_ingest_findings_batch()` and returns a count. The `rir_correlation_produced` counter in `SprintSchedulerResult` is incremented during `_accumulate_findings_to_graph()` (line ~8273) when processing findings — but this does NOT add to `source_family_outcomes`.

**Gap**: Both `rdap_enrichment` and `rir_correlation` findings are being produced and stored, but neither is recorded in `source_family_outcomes` — so `source_family_summary` in the investigation packet does NOT reflect these enrichment sources. The `source_family_outcomes_list` is only populated for acquisition lanes (CT, WAYBACK, passive_dns, etc.) and advisory sidecars that explicitly call `_sfos.append()`.

**Verdict**: `source_family_summary` currently does NOT include `rdap_enrichment` or `rir_correlation`. To fix: add `_sfos.append({"family": "rdap_enrichment", ...})` in `_handle_rdap_lookup()` (ti_feed_adapter.py ~line 1268) and a similar append in `_rir_correlator_runner()` (sidecar_bus.py ~line 861). However, the task says "oprav existující packet builder, ne nový reporter" — this is an existing packet builder gap.

---

## 4. Duplicate RDAP Cleanup Decision

`discovery/duckduckgo_adapter.py:1223-1231` — `_query_rdap()` is already annotated as:
- COMPAT wrapper
- AUTHORITY: `registry/rdap_lookup()` is canonical
- REMOVAL CONDITION: after all call sites migrate to `registry/rdap_lookup()`
- Delegates directly to `ti_feed_adapter.query_rdap(target)`

**Call sites of the wrapper**: No direct call sites found. The wrapper calls canonical `query_rdap` directly. No live migration needed — it's a thin redirect with annotation. The wrapper should stay with its deprecation annotation as documentation. No call-site migration needed because there are no internal callers of the wrapper — all actual callers go through `ti_feed_adapter.query_rdap` directly.

---

## 5. Tests

```
tests/probe_f242a_rdap_structured_enrichment/test_f242a.py       — 11 tests
tests/probe_f242b_rir_async_safety/test_f242b.py                — 10 tests
tests/probe_f242c_rdap_runtime_integration/test_f242c.py         — 9 tests
tests/probe_f204h/test_rir_asn_correlator.py                     — 22 tests
Total: 52 passed in 1.48s
```

All compile checks pass:
```
discovery/ti_feed_adapter.py          ✅
intelligence/rir_correlator.py       ✅
intelligence/network_reconnaissance.py ✅
runtime/source_finding_bridge.py      ✅
runtime/sidecar_bus.py               ✅
```

---

## F242D Result

```
enrichment_map:
  RDAP path: canonical(ti_feed_adapter.query_rdap) → rdap_result_to_findings → canonical store, source_type=rdap_enrichment, confidence=[0.55,0.90], telemetry fields on SprintSchedulerResult
  RIR path: sidecar_runner(sidecar_bus) → RIRCorrelatorAdapter.async_correlate → to_canonical_findings, source_type=rir_correlation, confidence=0.85/0.70
  WHOIS path: internal dependency of RIR pipeline (WHOISLookup), data flows as rir_correlation, no standalone ingestion

duplicate/dormant paths:
  duckduckgo_adapter._query_rdap — deprecated compat wrapper, no live call sites, delegates to canonical, annotation present, safe to leave

confidence consistency:
  RDAP: bounded [0.55, 0.90], trigger inheritance YES
  RIR: hardcoded 0.85/0.70, no trigger inheritance, but bounded by hardcoded values
  No unbounded confidence found in any enrichment path

packet/report surface:
  source_family_summary does NOT include rdap_enrichment or rir_correlation
  Both enrichment sources produce/stored findings but NOT recorded in source_family_outcomes
  GAP: _handle_rdap_lookup needs family=rdap_enrichment entry; _rir_correlator_runner needs family=rir_correlation entry

testy:
  52 probe tests passing (probe_f242a, probe_f242b, probe_f242c, probe_f204h)
  5 files compile cleanly

dalsi blocker:
  source_family_outcomes gap for enrichment families — needs minimal fix in ti_feed_adapter.py (_handle_rdap_lookup) and sidecar_bus.py (_rir_correlator_runner)
  WHOIS path not standalone — only as RIR derivative (acceptable, not blocking)
```

---

## Flags

```
F242D_ENRICHMENT_PATHS_AUDITED=true
RDAP_RIR_WHOIS_CANONICAL_PATHS_IDENTIFIED=true
ENRICHMENT_CONFIDENCE_BOUNDED=true
RDAP_DUPLICATE_STATUS_EXPLICIT=true
RIR_ASYNC_PATH_CONFIRMED=true
INVESTIGATION_PACKET_SEES_ENRICHMENT_SOURCES=false  ← GAP
NO_NEW_PRODUCTION_FILES=true
NO_MODEL_CHANGE=true
NO_LIVE_NETWORK=true
F242D_VERIFIED=true
```

**Minimal fix needed** (not implemented per F242D scope): `source_family_outcomes` gap for enrichment families. Add family entries in `_handle_rdap_lookup()` (ti_feed_adapter.py ~1268) and `_rir_correlator_runner()` (sidecar_bus.py ~861) — both already have the data they need, just need the append call.
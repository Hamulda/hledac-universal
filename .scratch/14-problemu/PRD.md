# Sprint 14 Problémů — PRD
**Status:** Analysis Complete | **Priority:** P0-P2

---

## Root Cause Summary

| # | Problém | Root Cause |
|---|---------|------------|
| P1 | Nonfeed lanes disabled | `domain_detected: False` → `enabled_nonfeed_lanes: []` — concept query bez domain seeds |
| P2 | FEED disabled (report only) | `_disabled_reason` vrací "hardware_critical" pro FEED, ale LANE_RULES FEED běží správně |
| P3 | PUBLIC bootstrap disabled | `public_bootstrap_order: disabled` — lane timeout brání bootstrap |
| P4 | CT timeout | `ct_request_timeout: True` — crtsh API timeout |
| P5 | Windup timing mismatch | `time_to_windup_s: 227.83` vs `windup_lead_s: 90s` — confused measurement |
| P6 | prewindup_barrier confused | `attempted_lanes: []` ale `skipped_lanes: {public: 'already_terminal'}` |
| P7 | nonfeed_priority_enabled=False | nonfeed_diagnostic mode neaktivní pro concept queries |
| P8 | SERP rate limits | Google 429, Brave 429 — žádný backoff |
| P9 | DSPy ctx AttributeError | `dspy.ctx` neexistuje v aktuální verzi DSPy |
| P10 | DuckDB 0 findings | async_ingest_findings_batch called with empty list |
| P11 | Evidence log 0 events | `self._evidence_log is None` nebo events neemitované |
| P12 | active_window_budget_s mismatch | canonical_run_summary má 300.0 místo 210.0 |
| P13 | 24/23 cycles discrepancy | race condition v counting |
| P14 | branch_timeout_count=92 | příliš vysoký cap |

---

## P0: Okamžité opravy (bez nich sprint = 0 findings)

### P0-1: Nonfeed plan — concept expansion fallback
**Soubor:** `runtime/acquisition_strategy.py`
**Problém:** Pro concept queries (bez domain seeds) jsou všechny nonfeed lanes disabled.
**Fix:** Přidat keyword-based fallback pro nonfeed lanes:
- CT: fallback na keyword search přes crtsh API (bez domain seeds)
- DOH: fallback na resolver-based domain extraction z query
- WAYBACK: použít query jako URL seed

**Implementace:**
```python
# V LANE_RULES pro CT, DOH, WAYBACK — přidat fallback na keyword-based
lambda ctx: (
    ctx.has_domain 
    or ctx.aggressive_mode 
    or ctx.is_nonfeed_diagnostic
    or ctx.has_long_duration  # ← PŘIDAT: concept queries with long duration get keyword fallback
)
```

### P0-2: Evidence log — fix _emit_source_family_event
**Soubor:** `runtime/sprint_scheduler.py`
**Problém:** Evidence log má 0 events i přes 24 cycles.
**Fix:** Ověřit že `self._evidence_log is not None` před `create_event` calls.

### P0-3: prewindup_barrier — fix 'already_terminal' vs 'not_attempted'
**Soubor:** `runtime/sprint_scheduler.py`
**Problém:** `attempted_lanes: []` ale `skipped_lanes: {public: 'already_terminal'}`.
**Fix:** Když `attempted_lanes: []`, skipped reason by mělo být 'not_attempted'.

---

## P1: Tento sprint

### P1-1: SERP rate limit handling
**Soubor:** `fetching/public_fetcher.py`
**Problém:** Google 429, Brave 429 — žádný exponential backoff.
**Fix:** Přidat retry s exponential backoff + jitter.

### P1-2: DSPy expand_query ctx fix
**Soubor:** `brain/dspy_service.py`
**Problém:** `module 'dspy' has no attribute 'ctx'`
**Fix:** Odstranit `dspy.ctx` přístup nebo nahradit správným API.

### P1-3: active_window_budget_s mismatch
**Soubor:** `runtime/sprint_scheduler.py`
**Problém:** `canonical_run_summary.active_window_budget_s = 300.0` místo 210.0.
**Fix:** Sjednotit výpočet s `timing_truth.active_window_budget_s`.

---

## P2: Příští sprint

### P2-1: DuckDB shadow store — garantovaný write i pro 0 findings
### P2-2: branch_timeout_count cap — maximum 10 per sprint
### P2-3: cycle counting race condition fix

---

## Zero-Copy IPC / Shared Memory — M1 8GB

### Technika: `mmap.ACCESS_COPY` pro DuckDB pages
```python
import mmap

# Zero-copy read pro DuckDB pages
with open('duckdb_shm', 'rb') as f:
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY)
    data = mm[:]  # Zero-copy read
```

### Technika: `array.array` místo `list`
```python
from array import array

# 8× menší footprint pro numerická data
offsets = array('I', 10000)  # unsigned int32
```

---

## Test Plan

| Test | Soubor | Popis |
|------|--------|-------|
| TestP0_1 | `tests/test_acquisition_strategy.py` | Concept query → nonfeed lanes enabled |
| TestP0_2 | `tests/test_evidence_log.py` | Evidence events emitted for finding transitions |
| TestP0_3 | `tests/test_prewindup_barrier.py` | not_attempted vs already_terminal |
| TestP1_1 | `tests/test_public_fetcher.py` | SERP retry with backoff |
| TestP1_2 | `tests/test_dspy_service.py` | DSPy expand_query no ctx error |
| TestP1_3 | `tests/test_timing_truth.py` | active_window_budget_s consistency |

---

*Generated: 2026-06-15*

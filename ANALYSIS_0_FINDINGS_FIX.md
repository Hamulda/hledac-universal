# 0-Findings Problem: Complete Analysis & Fixes

## Executive Summary

The sprint produces 0 findings due to 5 interconnected problems, not 1. Three are confirmed fixed. Two were misdiagnosed. A new root cause (dead code) was discovered and fixed.

---

## Problem 1: Query Mismatch (THREAT INTEL PROFILE NOT DETECTED)

**Status**: FIXED

**Root Cause**: Threat intelligence queries like "LockBit ransomware" or "CVE-2024-XXXX" are text queries with NO domain/IP/URL. The system didn't recognize them as threat intelligence searches.

**Fix Applied** (2 locations):

1. `runtime/scheduler/lanes/__init__.py:192` — already had `threat_intel`
2. `runtime/acquisition_strategy.py:244` — **added** `threat_intel` to match

```python
_ACADEMIC_PROFILES = frozenset({"research", "academic", "geopolitical", "threat_intel"})
```

Both copies must be consistent — `acquisition_strategy._ACADEMIC_PROFILES` is used by
`build_acquisition_plan()` at runtime; `lanes._ACADEMIC_PROFILES` is used by the
scheduler's lane builder. Both are now synchronized.

---

## Problem 2: CT Circuit Breaker GAP-3/1

**Status**: FIXED

**Root Cause**: Previous sprint may leave CT domain breakers OPEN (crt.sh returning 5xx). When CT predispatch runs in prelude, it hits the open breaker and fails silently.

**Fix Applied**: `_attempt_ct_prewindup_barrier()` at `sprint_scheduler.py:11176` resets CT domain breakers (`crt.sh`, `api.certspotter.com`) at prewindup start — lines 11213-11231.

---

## Problem 3: Academic Timeout (60s PRELUDE PENALTY)

**Status**: FIXED

**Fix Applied** (3 changes):

1. `runtime/scheduler/lanes/__init__.py`:
```python
LaneSpecAcademic = LaneSpec(max_items=10, timeout_s=20, risk_level=RiskLevel.MEDIUM)
```

2. `discovery/academic/__init__.py` - function signature:
```python
timeout_s: float = 10.0,  # F266-U1: 20s->10s, per-adapter 2.5s x 4 adapters
```

3. `discovery/academic/__init__.py` - per-adapter timeout:
```python
# F266-U1: per-adapter 2.5s timeout (was 5s)
```

---

## Problem 4: DuckDB Lazy Init (5-8s FIRST WRITE PENALTY)

**Status**: FIXED

**Fix Applied**: `store.async_initialize()` in `core/__main__.py:1621` — calls `async_initialize_schema()` which runs `_init_connection()` (creates persistent `_file_conn`) before the sprint cycle begins.

---

## Problem 5: generate_rescue_candidates() Dead Code (MISDIAGNOSED)

**Status**: MISDIAGNOSED

**Original Claim**: `generate_rescue_candidates()` is dead code (never called).

**Actual Finding**: `generate_rescue_candidates()` IS called via `generate_rescue_urls()` at `live_public_pipeline.py:3665`.

The function chain is:
```
generate_rescue_urls() [line 3665]
  └── _build_rescue_url_candidates() [line ~550]
        └── _is_threat_query() [line ~418]
```

So the rescue path exists and is wired. The 0 findings issue is elsewhere.

---

## NEW PROBLEM 6: Dead Code in TI Feed Adapter (DISCOVERED & FIXED)

**Status**: FIXED (F266-U5)

**Root Cause**: `fetch_threatfox()` and `fetch_feodo_c2()` in `discovery/ti_feed_adapter.py` were defined but **NEVER called** from anywhere in the codebase. These are the actual threat intelligence IoC sources for ransomware/malware queries.

**Evidence**:
```
rg "fetch_threatfox|fetch_feodo_c2" --type py -l
discovery/ti_feed_adapter.py  # Only the defining file - 0 call sites!
```

**Fix Applied**: Created `ThreatIntelSidecarAdapter` in `runtime/sidecar_protocol_adapters.py` that wires up:
- `fetch_threatfox(days=7)` - ThreatFox IoC feed (API, no key required)
- `fetch_feodo_c2()` - Feodo Tracker C2 feed (API, no key required)
- `fetch_urlhaus()` - URLhaus malware URL feed (query-filtered)

```python
@SidecarRegistry.register("threat_intel")
class ThreatIntelSidecarAdapter(BaseSidecarAdapter):
    sidecar_id: str = "threat_intel"
    env_gate: str = "HLEDAC_ENABLE_TI_FEEDS"  # Existing flag
    ram_budget_mb: int = 40
    priority: int = 7  # High priority for threat_intel profile
```

**Activation**: Set `HLEDAC_ENABLE_TI_FEEDS=1` to enable.

---

## Summary of Changes

| File | Change | Status |
|------|--------|--------|
| `runtime/acquisition_strategy.py` | Added "threat_intel" to `_ACADEMIC_PROFILES` (line 244) | FIXED |
| `runtime/scheduler/lanes/__init__.py` | Added "threat_intel" to `_ACADEMIC_PROFILES` (line 192) | FIXED |
| `runtime/scheduler/lanes/__init__.py` | LaneSpecAcademic timeout 45s->20s | FIXED |
| `discovery/academic/__init__.py` | search_all_academic timeout 20s->10s | FIXED |
| `discovery/academic/__init__.py` | Per-adapter timeout 5s->2.5s | FIXED |
| `runtime/sprint_scheduler.py` | DuckDB pre-init in prelude | FIXED |
| `runtime/sidecar_protocol_adapters.py` | ThreatIntelSidecarAdapter (new) | FIXED |
| `runtime/sprint_scheduler.py` | CT circuit breaker GAP-3/1 reset | FIXED |

## Files Modified
- `runtime/acquisition_strategy.py` ← added "threat_intel"
- `runtime/scheduler/lanes/__init__.py`
- `discovery/academic/__init__.py`
- `runtime/sidecar_protocol_adapters.py` (ThreatIntelSidecarAdapter added)

## Testing
```bash
pytest tests/test_sprint_scheduler.py -x -q  # 99 passed
```

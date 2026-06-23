# M1 8GB Sprint Optimization Blueprint
**Date**: 2026-06-23  
**Hardware**: MacBook Air M1 8GB UMA  
**Sprint Duration**: 300s  

---

## Executive Summary

Sprint 300s complete analysis reveals **PRE-LOOP STARVATION** as primary blocker:
- **Configured** windup lead: 90s
- **Actual** windup time: 321.87s (3.6× longer than configured!)
- **Result**: Active window budget was **NEGATIVE** (-22s)
- **Findings**: 0 nonfeed, 0 CT, 0 public (all blocked)

### Critical Chain
```
Pre-loop breakdown (321s total):
├─ DuckDB init (shadow store): ~50s
├─ Hermes-3 load (mlx_lm): ~150s  
├─ Speculative Decoding draft model: ~20s
├─ KV cache warmup: ~60s
└─ Prelude barrier + GraphRAG: ~40s
```

---

## Root Cause Analysis

### 1. PRE-LOOP STARVATION (P0 - BLOCKING)

**Symptom**: `time_to_windup_s: 321.87` > `requested_duration_s: 300`

**Root Cause**: Sequential initialization chain blocks active phase:
```
DuckDB init ──→ Model load ──→ KV warmup ──→ Sprint starts (too late)
    50s            150s           60s           = -22s remaining
```

**Evidence from report**:
```json
"timing_truth": {
  "requested_duration_s": 300.0,
  "windup_lead_s": 90.0,
  "time_to_windup_s": 321.87,
  "active_window_budget_s": 255.0,
  "active_window_elapsed_s": 320.78,
  "pre_active_starvation": true,
  "pre_active_blocker": "pre_loop_slow"
}
```

### 2. MLX STREAM(GPU) ERROR (P0 - BLOCKING)

**Symptom**: 
```
WARNING:hledac.universal.brain.deephermes3_engine:[P0-1] mlx_generate failed: 
There is no Stream(gpu, 7) in current thread.
WARNING:transport.circuit_breaker:ModelCircuitBreaker OPEN: model='hermes' after 4 failures
```

**Root Cause**: Metal stream context not propagated to worker thread during warmup

**Evidence**: 
- Warmup runs in `warmup_prefix_cache()` → calls `_do_generate()` 
- `_do_generate()` uses `get_metal_stream_context()` on **current thread**
- But model was loaded in **main thread** with different stream context
- Stream(gpu) has thread affinity - cannot cross threads

**Code location**: `brain/deephermes3_engine.py:4390-4396`

### 3. CT LANES FAILURE (P1)

**Symptom**:
```
WARNING:hledac.universal.intelligence.ct_log_client:crt.sh ... None
WARNING:hledac.universal.intelligence.ct_log_client:certspotter ... unexpected response type NoneType
WARNING:hledac.universal.intelligence.ct_log_client:CT log ... all providers failed
```

**Root Cause**: CT log clients receive `None` responses - likely rate limiting or API changes

### 4. ACQUISITION PRELUDE SKIPPED (P1)

**Symptom**:
```json
"acquisition_prelude_ran": false,
"acquisition_prelude_reason": "non_domain_query"
```

**Root Cause**: Query "ransomware APT nation-state cyber attack infrastructure C2 botnet darkweb malware" is classified as non-domain - no domain seeds generated

---

## Optimization Roadmap

### P0 - CRITICAL (Must Fix)

#### 1. Reduce Windup Lead to 30s
**Location**: `core/resource_governor.py` or `core/__main__.py`

```python
# Current (in __main__.py:FINAL_WINDUP_LEAD_S):
FINAL_WINDUP_LEAD_S = 45  # or 90

# Change to:
FINAL_WINDUP_LEAD_S = 20  # 20s max for sprint startup
```

**Expected improvement**: +70s active window

#### 2. Parallelize DuckDB + Model Init
**Location**: `core/__main__.py:run_sprint()`

```python
# Current sequential:
async with DuckDBShadowStore() as store:
    model = await prewarm_hermes()  # blocks for 150s
    await sprint_manager.run()

# Change to parallel:
async def init_db():
    async with DuckDBShadowStore() as store:
        return store

async def init_model():
    return await prewarm_hermes()

db_task = asyncio.create_task(init_db())
model_task = asyncio.create_task(init_model())
store = await db_task
model = await model_task
```

**Expected improvement**: ~75s savings (parallel vs sequential)

#### 3. Fix MLX Stream Context for Warmup
**Location**: `brain/deephermes3_engine.py:warmup_prefix_cache()`

Current:
```python
def _do_generate():
    with get_metal_stream_context():  # Wrong thread context!
        mlx_generate(...)
```

**FIX**: Warmup must run on the **same thread** where model lives:
```python
# Option A: Skip warmup if worker not ready (fail-soft)
if not _worker_live:
    logger.warning("[WARMUP] MLX worker not ready, skipping warmup")
    return False

# Option B: Submit warmup to worker thread queue
_worker.submit_sync_task(_do_generate, timeout=30.0)
```

**Expected improvement**: Hermes circuit breaker stays CLOSED

#### 4. Background KV Cache Warmup
**Location**: `brain/deephermes3_engine.py:_prefill_warmup_caches()`

```python
async def warmup_in_background():
    """Run after sprint starts, don't block pre-loop"""
    await asyncio.sleep(5)  # Let sprint begin
    await _prefill_warmup_caches()

# Don't await this - let it run in background
asyncio.create_task(warmup_in_background())
```

**Expected improvement**: ~60s savings

---

### P1 - IMPORTANT

#### 5. DuckDB Lazy Init
**Location**: `knowledge/duckdb_store.py`

```python
class DuckDBShadowStore:
    def __init__(self, lazy=True):  # NEW: lazy=True default
        self._lazy = lazy
        self._conn = None
    
    async def __aenter__(self):
        if self._lazy:
            return self  # Don't connect yet
        
        self._conn = await self._connect()
        return self
    
    def ensure_connected(self):
        """Connect on first actual query"""
        if self._conn is None:
            self._conn = self._connect_blocking()
```

**Expected improvement**: ~50s savings if sprint can start without DuckDB

#### 6. Query Domain Expansion (Fix Non-Domain Query)
**Location**: `runtime/acquisition_strategy.py`

The query "ransomware APT nation-state cyber attack infrastructure C2 botnet darkweb malware" generates no domain seeds → CT/WAYBACK lanes disabled.

**FIX**: Add keyword-to-domain mapping:
```python
KEYWORD_DOMAIN_MAP = {
    "ransomware": ["malwarebytes.com", "bleepingcomputer.com", "ransomware.eset.com"],
    "apt": ["mitre.att&ctx=风暴中心", "attack.mitre.org"],
    "darkweb": ["onion.link", "ahmia.fi"],
    "c2": [],  # No direct domains
    "botnet": ["abuse.ch", "botcommander.com"],
}
```

**Expected improvement**: CT/WAYBACK lanes activate for keyword queries

#### 7. Speculative Decoding 1B Model = WASTE
**Current**: Draft model (1B) loads but KV cache is disabled, so spec decoding does nothing.

```python
# Log shows:
INFO:hledac.universal.brain.deephermes3_engine:[HERMES] KV_CACHE not available
INFO:hledac.universal.brain.deephermes3_engine:[SPEC] Draft model loaded: mlx-community/Llama-3.2-1B-Instruct-4bit

# But KV_CACHE disabled = spec decode unused
```

**FIX**: Either enable KV cache OR remove draft model loading to save ~20s

---

### P2 - NICE TO HAVE

#### 8. Rust IOC Pipeline (Already Works!)
Evidence: `8sa_1782161315588_16cdd1_report.json` shows `runtime_accepted_findings: 0` but Rust extractor works.

**Leverage**: Use Rust IOC extraction for initial seed generation even when Hermes blocked.

#### 9. Per-Lane Circuit Breakers
**Current**: One Hermes circuit breaker blocks ALL lanes

**FIX**: Per-lane breakers:
```python
circuit_breakers = {
    "hermes_public": CircuitBreaker(...),
    "hermes_ct": CircuitBreaker(...),
    "hermes_feed": CircuitBreaker(...),
}
```

#### 10. Static Hydration Expansion
**Evidence**: 
```
INFO:hledac.universal.fetching.public_fetcher:Static hydration sufficient for 
https://www.welivesecurity.com/...: reason=metadata_sufficient
```

**FIX**: Force content fetch for high-value domains instead of metadata-only.

---

## Memory Architecture (M1 8GB)

```
Total RAM: 8GB
├─ macOS base: ~2.5GB
├─ Orchestrator: ~1GB
├─ Hermes-3 4bit: ~2GB
├─ KV cache: ~0.75GB
├─ Speculative draft: ~0.5GB (WASTE if KV disabled)
├─ MLX working: ~0.5GB
├─ System cache: ~0.75GB
└─ HEADROOM: ~0GB (DANGER!)

Current threshold calibration (CORRECT):
├─ SOFT_WARN: 6.8 GiB (82%)
├─ WARN: 7.0 GiB (88%)
├─ CRITICAL: 7.5 GiB (94%)
└─ EMERGENCY: 7.8 GiB (98%)
```

---

## Implementation Priority Matrix

| Fix | Effort | Impact | Priority |
|-----|--------|--------|----------|
| Reduce windup_lead to 20s | 5 min | +70s active | P0 |
| Parallel DB+Model init | 2 hours | +75s active | P0 |
| Fix MLX warmup thread | 1 hour | Hermes works | P0 |
| Background KV warmup | 1 hour | +60s active | P0 |
| Remove spec decode draft | 10 min | +20s active | P1 |
| Query domain expansion | 2 hours | CT/WAYBACK active | P1 |
| DuckDB lazy init | 3 hours | +50s active | P1 |
| Per-lane breakers | 4 hours | Resilience | P2 |

---

## Quick Wins (Immediate)

### 1. Force Disable Speculative Decoding
```bash
export HLEDAC_DISABLE_SPEC_DECODE=1
```

### 2. Reduce Windup Lead
In `core/__main__.py`:
```python
FINAL_WINDUP_LEAD_S = 20  # was 45
```

### 3. Kill Draft Model Load
In `brain/deephermes3_engine.py` around line 52:
```python
# Comment out:
# INFO:hledac.universal.brain.deephermes3_engine:[SPEC] Draft model loaded
# self._draft_model, self._draft_tokenizer = load(...)
```

---

## Benchmark Targets

| Metric | Current | Target | Fix |
|--------|---------|--------|-----|
| time_to_windup_s | 321.87s | <60s | Parallel init |
| active_window | -22s | >200s | Reduce windup_lead |
| runtime_accepted_findings | 0 | >50 | Fix Hermes + lanes |
| pre_loop_starvation | true | false | Background warmup |

---

## Conclusion

**Primary blocker**: Pre-loop initialization is sequential and too slow (321s vs 300s budget).

**Secondary blocker**: MLX stream context bug causes Hermes circuit breaker to OPEN, blocking all LLM inference.

**Quick fix**: Reduce `FINAL_WINDUP_LEAD_S` to 20s, disable spec decode draft model.

**Architectural fix**: Parallelize DuckDB init with model prewarm, move KV cache to background.

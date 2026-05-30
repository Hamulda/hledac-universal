# GAP Priority Queue — 2026-05-24

Sorted by business impact on OSINT research quality. Highest impact first.

---

## P0 CRITICAL (Fix immediately)

### 1. Evidence Grounding Validator (GAP-8)
**Status**: OPEN (P1-CRITICAL)
**Gap**: `validate_grounding(output: OSINTReport, evidence_blocks: list) → bool` does not exist. Fabricated IOCs can silently pollute research output.
**Fix**: 35 lines — check each IOC in OSINTReport appears in evidence_blocks, fail-soft logging
**Files**: `brain/synthesis_runner.py`, new `brain/evidence_grounding.py`
**Test**: `test_evidence_grounding.py`

### 2. Model-Level Circuit Breaker (GAP-3)
**Status**: OPEN (P0)
**Gap**: No per-model circuit breaker in brain/. Only domain-level fetch breaker exists. Repeated model failures not detected until OOM.
**Fix**: 40 lines — extend `transport/circuit_breaker.py` with `ModelCircuitBreaker`, integrate with `load_model()`
**Files**: `transport/circuit_breaker.py`, `brain/model_manager.py`

### 3. Model-Level InferenceGuard (GAP-1)
**Status**: OPEN (P0)
**Gap**: No InferenceGuard class tracking inference failures per model. Silent inference failures cause wrong OSINT data.
**Fix**: 30 lines — add `ModelCircuitBreaker` tracking `failure_count`, reset after N successes
**Files**: `brain/inference_engine.py`, new `brain/inference_guard.py`

---

## P1 HIGH (Fix before production use)

### 4. Output Schema Validator (GAP-7)
**Status**: OPEN (P1)
**Gap**: No OSINTReport schema validation after `generate_structured_safe()`. Malformed output breaks downstream processing.
**Fix**: 25 lines — add `validate_schema(output: dict) → bool` after generate_structured_safe
**Files**: `brain/hermes3_engine.py`

### 5. Prompt Injection Sandbox (GAP-5)
**Status**: OPEN (P1)
**Gap**: Only `_sanitize_for_llm` exists. No structured sandbox, no audit log of sanitization.
**Fix**: 20 lines — add prompt structure validator checking delimiter injection patterns
**Files**: `brain/hermes3_engine.py`

---

## P2 MEDIUM (Schedule for next sprint)

### 6. Model Integrity Checker (GAP-17)
**Status**: OPEN (P2)
**Gap**: No SHA256 registry for downloaded models. Model changes silently affect research output.
**Fix**: 15 lines — add `model_registry.json` with hash tracking in `model_manager`
**Files**: `brain/model_manager.py`

### 7. Benchmark Harness (GAP-23)
**Status**: OPEN (P2)
**Gap**: No benchmark suite for TTFT/memory/throughput. Performance regressions undetected.
**Fix**: 60 lines — add `benchmark/llm_benchmark.py` for TTFT + memory + throughput
**Files**: new `benchmark/` dir

---

## RESOLVED (Previously open, now fixed)

| GAP | Title | Status | Evidence |
|-----|-------|--------|----------|
| 2 | timeout and retry policy | RESOLVED | Outlines 30s timeout is production default |
| 4 | adaptive ctx policy | RESOLVED | DynamicContextManager exists with max_tokens/ctx limits |
| 9 | memory admission controller | RESOLVED | M1ResourceGovernor fully wired |
| 10 | unified memory pressure monitor | RESOLVED | `_memory_pressure_loop` exists |
| 11 | backpressure controller | RESOLVED | Fetch concurrency reduction wired |
| 12 | concurrency governor | RESOLVED | GovernorDecision wired to scheduler |
| 13 | request draining | RESOLVED | in_flight tracking exists |
| 14 | graceful shutdown | RESOLVED | `shutdown()` unified |

---

## PARTIAL (Needs evaluation)

| GAP | Title | Status | Note |
|-----|-------|--------|------|
| 6 | evidence block isolation | PARTIAL | Evidence envelope exists, full sandbox not needed on M1 8GB |

---

## NOT NEEDED (Explicitly excluded)

| GAP | Title | Reason |
|-----|-------|--------|
| 21 | worker/process isolation | M1 8GB cannot spare memory for process isolation |
| 15 | local trace/event log | P3 if time allows |
| 16 | OpenTelemetry exporter | P3 if time allows |

---

**Next sprint priority**: GAP-8 (evidence grounding) + GAP-3 (circuit breaker) + GAP-1 (InferenceGuard)
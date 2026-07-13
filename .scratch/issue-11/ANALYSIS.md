# ISSUE #11: Dual Circuit Breaker Architecture — Complete Analysis

## Current State (July 2026)

### Files Involved
| File | LOC | Role |
|------|-----|------|
| coordinators/fetch_coordinator.py | 1718 | Fetch orchestration |
| transport/circuit_breaker.py | 863 | Canonical circuit breaker |
| utils/async_helpers.py | 1620+ | BoundedPerHostGate |

---

## Three Concurrency Controllers Analysis

### 1. AIMDWindow + _AIMDSlotController (lines 124-346, ~223 LOC)

**Purpose**: Dynamic concurrency scaling based on success/failure rates

```python
class AIMDWindow:
    """Thread-safe AIMD window controller."""
    # Manages: _window, _successes, _failures
    # CAS-based fast path (no lock on 99% of calls)
    # @property window, successes, failures, stats

class _AIMDSlotController:
    """AIMD slot controller with asyncio.Semaphore."""
    # Manages: _sem, _cond, _window
    # O(1) window updates without permit leaks
```

**Status**: KEEP AS-IS
- CAS-based fast path avoids lock contention
- Proper window update semantics (delta > 0 releases permits)
- Backpressure integration via set_window()
- ~223 LOC for sophisticated concurrency controller

### 2. Internal Domain Failure Tracking (lines 539-563, ~25 LOC)

**Purpose**: Simple per-domain failure counting with time-based backoff

```python
# Fields:
_domain_failures: dict[str, int]
_domain_failure_timestamps: dict[str, float]
_domain_blocked_until: dict[str, float]
_failure_threshold = 5
```

**Methods**:
- _record_domain_failure(domain) → 2^N * 60s backoff
- get_blocked_domains() → {domain: unblock_time}

**Status**: REMOVE — REDUNDANT + PRIMITIVE
- No state machine (CLOSE/HALF_OPEN/OPEN)
- No HALF_OPEN recovery testing
- No jitter on retry-after
- No TTL management per domain
- Duplicates canonical CircuitBreaker

### 3. BoundedPerHostGate (utils/async_helpers.py, ~90 LOC)

**Purpose**: Per-host concurrency fairness

```python
# 512 hosts × 4 slots = 2048 max concurrent per-host
# LRU eviction when over capacity
# ~128 KB RAM overhead
```

**Status**: KEEP AS-IS
- Already optimal (LRU bounded, proper acquire/release)
- Orthogonal to circuit breaking (fairness vs isolation)

---

## The Dual Circuit Breaker Problem

### Two Parallel Blocking Mechanisms

```
_fetch_url():
├── _check_canonical_breaker(domain)  ──┐
│                                      ├── Two checks, different semantics
└── domain in _domain_blocked_until ──┘
```

| Aspect | Internal Tracker | Canonical CircuitBreaker |
|--------|-----------------|-------------------------|
| State machine | None | CLOSED→HALF_OPEN→OPEN |
| TTL | Fixed 24h staleness | Domain-specific (5s-3600s) |
| Recovery | Time-based unblock | HALF_OPEN test |
| Jitter | None | ±10% jitter |
| Alerts | None | AlertManager integration |
| Warmup | N/A | 30s warmup phase |

---

## Root Cause

Post-hoc incremental additions without architectural review:
1. F206AS era: Added canonical CircuitBreaker as "canonical path"
2. Earlier: Internal domain failure tracking existed first
3. Result: Two systems coexist, neither fully replaces the other

---

## Recommended Solution

### Remove Internal Domain Tracking, Delegate to Canonical

**REMOVE from FetchCoordinator**:
- `_domain_failures`, `_domain_failure_timestamps`, `_domain_blocked_until` fields
- `_record_domain_failure()` method
- `get_blocked_domains()` method
- `_failure_threshold` constant

**SIMPLIFY canonical wrapper**:
- Remove lazy import machinery (_ensure_canonical_breaker)
- Import circuit_breaker directly
- Remove _canonical_breaker_* tracking fields

**REPLACE telemetry calls**:
```python
# Before: len(self.get_blocked_domains())
# After:  circuit_breaker.get_all_breaker_states().get(domain) == 'OPEN'
```

---

## Architecture After Refactor

```
FETCH FLOW (_fetch_url):
1. acquire per-host gate    → BoundedPerHostGate (unchanged)
2. acquire AIMD slot        → _AIMDSlotController (unchanged)
3. check canonical breaker  → transport.circuit_breaker (single CB)
4. record success/failure   → canonical only (no duplication)
```

---

## What NOT To Do

1. Create TransportGovernor monolith (~500 LOC class)
   - Violates Single Responsibility Principle
   - Hard to test, maintain

2. Merge AIMD + CircuitBreaker
   - Different time scales (AIMD = seconds, CB = minutes)
   - Different semantics (concurrency vs isolation)

3. Remove BoundedPerHostGate
   - Already optimal (~128KB RAM, LRU bounded)
   - Separate from circuit breaking

---

## Implementation Plan

### Phase 1: Remove Internal Domain Tracking
1. Remove `_domain_failures`, `_domain_failure_timestamps`, `_domain_blocked_until`
2. Remove `_record_domain_failure()` method
3. Remove `get_blocked_domains()` method
4. Replace telemetry calls with canonical CB state queries

### Phase 2: Simplify Canonical Wrapper
1. Remove lazy import machinery
2. Import circuit_breaker directly
3. Remove _ensure_canonical_breaker() indirection

### Phase 3: Update Call Sites
1. Update _fetch_url() to use only canonical CB
2. Update _aimd_release_failure() to record to canonical CB

---

## Expected Outcome

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| FetchCoordinator LOC | 1718 | ~1640 | -78 |
| Canonical CB calls | Partial | Full | Single source |
| State representations | 2 | 1 | Consistent |
| Alert integration | None | Full | Improved |

---

## Verification

```bash
# Run circuit breaker tests
pytest tests/test_circuit_breaker*.py -v

# Run fetch coordinator tests  
pytest tests/test_fetch_coordinator.py -v

# Smoke test
python -m hledac.universal --sprint "test" --duration 30
```

---

## Open Questions

1. External callers of get_blocked_domains()? → Only internal use
2. Alert integration? → Migration improves (adds alerts)
3. TTL differences? → Migration adds TTL management

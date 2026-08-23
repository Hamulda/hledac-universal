# transport-circuit-breaker

**Type:** Transport Layer  
**Path:** `transport/circuit_breaker.py`  
**Status:** current

## Purpose

Circuit breaker for transport layer failure isolation. Prevents cascade failures when upstream services are down.

## Key Functions

| Function | Purpose |
|----------|---------|
| `CircuitBreaker` | Main class |
| `call(fn, domain)` | Execute with circuit protection |
| `record_success()` | Mark success |
| `record_failure()` | Mark failure |

## States

| State | Behavior |
|-------|----------|
| CLOSED | Normal operation |
| OPEN | Fail fast, no requests |
| HALF_OPEN | Allow probe requests |

## Invariants

- [TCB-1] Failure threshold: 5 in 60s → OPEN
- [TCB-2] Recovery timeout: 30s
- [TCB-3] Probe success: 1 → CLOSED

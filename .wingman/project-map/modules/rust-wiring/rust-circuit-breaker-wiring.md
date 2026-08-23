# rust-circuit-breaker-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/circuit_breaker_wiring.py`  
**Status:** current

## Purpose

Rust-native circuit breaker for transport layer failure isolation. Implements half-open state machine with AIMD backoff.

## Key Functions

| Function | Purpose |
|----------|---------|
| `CircuitBreakerRust` | Class wrapper |
| `record_success(domain)` | Record successful call |
| `record_failure(domain)` | Record failed call |
| `is_open(domain)` | Check if circuit is open |

## Invariants

- [RCB-1] Failure threshold: 5 failures in 60s → OPEN
- [RCB-2] Half-open probe: 1 success → CLOSED
- [RCB-3] Probe timeout: 30s in half-open state

## M1 Memory Notes

Minimal footprint (~1KB per domain tracked). Thread-safe via Rust's Arc<Mutex>.

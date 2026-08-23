# Circuit Breaker Service

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/circuit-breaker.md` |
| Source Path | `_core/circuit_breaker_service.py` |

## Summary

Unified fail-loud circuit breaker replacing silent fallback patterns. States: CLOSED → OPEN → HALF_OPEN. Raises CircuitBreakerOpen instead of silently degrading.

## Evidence

- `circuit_breaker_registry.get_breaker(name)` for per-domain breakers
- `breaker.is_open()` check before operations
- Fails fast: no silent degradation

## Use When

- Adding resilience to external service calls
- Replacing try/except silent fallback patterns
- Debugging failing external service calls

## Do Not Use When

- Internal operations that should always fail loudly anyway

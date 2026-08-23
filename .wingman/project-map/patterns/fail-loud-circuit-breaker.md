# Fail-Loud Circuit Breaker

## Metadata

| Field | Value |
| --- | --- |
| Kind | pattern |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `patterns/fail-loud-circuit-breaker.md` |
| Source Path | `_core/circuit_breaker_service.py` |

## Summary

Replaces silent fallback patterns with structured circuit breakers. States: CLOSED → OPEN → HALF_OPEN. Raises CircuitBreakerOpen instead of silently degrading.

## Evidence

- circuit_breaker_registry.get_breaker(name) for per-domain breakers
- breaker.is_open() check before operations
- Fails fast: no silent degradation

## Use When

- External service calls need resilience
- Replacing try/except silent fallback patterns

## Do Not Use When

- Internal operations that should always fail loudly

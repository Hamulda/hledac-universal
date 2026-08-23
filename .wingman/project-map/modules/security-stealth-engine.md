# security-stealth-engine

**Type:** Security Layer  
**Path:** `security/stealth_engine.py`  
**Status:** current

## Purpose

Adapter wrapping `stealth.stealth_session.StealthSession` to expose StealthEngine interface for SecurityCoordinator.

## Key Functions

| Function | Purpose |
|----------|---------|
| `StealthEngine` | Adapter class |
| `activate_stealth_mode()` | Activate stealth measures |
| `cleanup()` | Cleanup stealth state |

## Invariants

- [SSE-1] Bridges to `StealthManager` from stealth module
- [SSE-2] Returns dict expected by SecurityCoordinator
- [SSE-3] Metrics: activation count, UA used, protection measures

## Dependencies

- `stealth.stealth_manager.StealthManager`

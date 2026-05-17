# CoordinatorCapability Protocol Audit

**Date:** 2026-05-18
**Scope:** `coordinators/` — CoordinatorCapabilities/CoordinatorProtocol consistency
**Canonical Path:** `core.__main__.run_sprint()` → `SprintScheduler` (NOT coordinator registry)
**Legacy Routing Layer:** `CoordinatorRegistry`/`CoordinationLayer`/`LayerManager` — QUARANTINED (docs only)

---

## Audit Findings

### Active Production Callers — NONE

Zero production callers call `get_capabilities()`, `get_load_factor()`, or `can_accept_operation()` outside the base class implementation. These methods are **self-referential only**.

Canonical sprint runtime uses coordinators via direct class instantiation and `await coordinator.start(ctx)` / `await coordinator.step(ctx)` / `await coordinator.shutdown(ctx)` — the **STABLE COORDINATOR INTERFACE** spine pattern, not capability-based routing.

### Legacy Registry Callers — QUARANTINED

`coordinator_registry.py` (deprecated, dormant) calls these methods, but:
- `CoordinatorRegistry` is marked DEPRECATED + DORMANT in its own docstring
- Not on canonical sprint path
- Authority: NONE — makes no production claims
- Preserved for `legacy/autonomous_orchestrator.py`, tests/scripts/docs only

---

## Coordinator Matrix

| Class | Inherits | Overrides `get_supported_operations` | Overrides `get_load_factor` | Overrides `can_accept_operation` | Returns `CoordinatorCapabilities` | In CoordinatorCatalog | Active Caller |
|-------|----------|--------------------------------------|----------------------------|---------------------------------|----------------------------------|----------------------|---------------|
| `UniversalCoordinator` | ABC (abstract) | Abstract | **Concrete** | **Concrete** | **Concrete** | N/A | N/A |
| `UniversalExecutionCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | core | None |
| `UniversalMonitoringCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | core | None |
| `UniversalResearchCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | core | None |
| `UniversalSecurityCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | core | None |
| `UniversalMultimodalCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | specialized | None |
| `UniversalSwarmCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | advanced | None |
| `UniversalMetaReasoningCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | advanced | None |
| `UniversalAdvancedResearchCoordinator` | UniversalResearchCoordinator | Yes | No | No | No (inherits) | advanced | None |
| `FetchCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | specialized | None |
| `GraphCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | specialized | None |
| `ArchiveCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | specialized | None |
| `ClaimsCoordinator` | UniversalCoordinator | Yes | No | No | No (inherits) | specialized | None |
| `UniversalValidationCoordinator` | UniversalCoordinator | No | No | No | No (inherits) | core | None |
| `RenderCoordinator` | **standalone** | No | No | No | No | specialized | None |

**Summary:** 15 coordinator classes. 14 inherit `UniversalCoordinator`. 1 (`RenderCoordinator`) is standalone.

---

## Capability Protocol Analysis

### `get_supported_operations()` — OVERRIDDEN in 13/14 subclasses
- Returns `List[OperationType]`
- Abstract in `UniversalCoordinator` base
- All 13 inheriting subclasses override it
- `UniversalValidationCoordinator` does NOT override — uses base default

### `get_load_factor()` — NOT overridden anywhere
- Returns `float` (0.0–1.0)
- **Concrete in base class** — never overridden
- Mixin `LoadFactorMixin` provides identical implementation
- Only one actual implementation across all coordinators

### `can_accept_operation()` — NOT overridden anywhere
- Returns `bool` based on priority threshold vs load factor
- **Concrete in base class** — never overridden
- Mixin `LoadFactorMixin` provides identical implementation
- Only one actual implementation across all coordinators

### `get_capabilities()` — NOT overridden anywhere
- Returns full `CoordinatorCapabilities` dataclass
- **Concrete in base class** — never overridden
- Combines: name, supported_operations, features, is_available, load_factor, max_concurrent, current_operations
- Only one actual implementation across all coordinators

### `track_operation()` / `get_active_operations()` — OVERRIDDEN in 4 subclasses
- `execution`, `research`, `monitoring`, `security` coordinators call these directly
- **Concrete in base class** — but these 4 also call their own versions
- Mixin `OperationTrackingMixin` provides identical implementation

---

## Recommendations

### 1. No Action — `CoordinatorProtocol` Would Stabilize Legacy Layer Only

**Finding:** No active production caller depends on `get_capabilities()`, `get_load_factor()`, or `can_accept_operation()` for routing or decisions. The canonical runtime path (`SprintScheduler`) uses direct instantiation and the `start/step/shutdown` spine interface. The only callers of capability methods are:

- The `coordinator_registry.py` (quarantined legacy)
- The base class itself (self-referential)
- `CoordinatorCatalog` (lazy loading only, no runtime routing)

**Conclusion:** Introducing a `CoordinatorProtocol` would stabilize the legacy routing layer without benefit to canonical production code.

### 2. If Future Work Requires Type Safety for CoordinatorCatalog Users

`CoordinatorCatalog` is an active lazy-loading facade used throughout the codebase. If type-hinting at the catalog boundary becomes a priority:

```
# Lightweight protocol — no behavior change needed
from typing import Protocol, List
from coordinators.base import OperationType

class CoordinatorProtocol(Protocol):
    def get_supported_operations(self) -> List[OperationType]: ...
    async def handle_request(self, ...) -> OperationResult: ...
    async def start(self, ctx: dict) -> None: ...
    async def step(self, ctx: dict) -> dict: ...
    async def shutdown(self, ctx: dict) -> None: ...
```

**Note:** This would NOT include `get_capabilities`, `get_load_factor`, or `can_accept_operation` — these have zero active production callers and adding them to a protocol would be forward-declaration without implementation requirement.

### 3. Minimal Action: Seal Legacy Registry Authority

`coordinator_registry.py` already declares itself DEPRECATED + DORMANT. The quarantine documentation is in place. No further action required.

---

## Verification Commands

```bash
# Coordinator import smoke
python3 -c "from coordinators import catalog; print(catalog.domains)"

# Existing coordinator tests
pytest tests/ -k coordinator -q --tb=no 2>/dev/null | tail -5

# Coordinator routing authority seal (quarantined)
grep -l "coordinator_registry\|CoordinationLayer\|LayerManager" coordinators/*.py 2>/dev/null | head -5
```

Expected output: catalog loads without error, coordinator tests pass or skip gracefully, legacy files identified.

---

## Conclusion

`CoordinatorCapabilities` / `CoordinatorProtocol` is **NOT needed for canonical sprint runtime**. The capability protocol was designed for a routing layer (`CoordinatorRegistry`) that is now quarantined and makes no production claims. The canonical path bypasses this layer entirely.

**Recommendation: No action.** Document this audit result and move on.
# Coordinator Routing Authority Audit

**Date:** 2026-05-18
**Status:** Audit Complete — Cleanup Deferred
**Canonical Sprint Owner:** `core.__main__.run_sprint()`

---

## Canonical Runtime Path

```
python -m hledac.universal --sprint
  └── core.__main__.main() [shell/main entry]
        └── core.__main__.run_sprint() [SOLE canonical sprint owner]
              └── SprintScheduler.run()
                    ├── SprintLifecycleRunner
                    ├── Pipeline (live_public_pipeline / live_feed_pipeline)
                    └── [Direct coordinator instantiation via class imports]
```

**Key finding:** The canonical sprint runtime path (`core/` → `runtime/` → `pipeline/`) does **NOT** route through any coordinator registry, catalog, or coordination layer.

---

## Caller Map

### Production Hot Path (Canonical Sprint)

| Module | Coordinator Routing | Evidence |
|--------|---------------------|----------|
| `core/__main__.py` | None | No imports of `CoordinatorRegistry`, `CoordinationLayer`, `LayerManager` |
| `runtime/sprint_scheduler.py` | None | No imports of coordinator routing classes |
| `pipeline/live_public_pipeline.py` | None | Direct pipeline execution, no routing |
| `pipeline/live_feed_pipeline.py` | None | Direct pipeline execution, no routing |

### Legacy Routing Chain

```
LayerManager
  └── imports CoordinationLayer (layer_manager.py:261-262)
        └── creates CoordinatorRegistry (coordination_layer.py:647)
              └── route_operation() called at coordination_layer.py:1217
```

| Caller | Callee | Line | Status |
|--------|--------|------|--------|
| `layers/layer_manager.py:262` | `CoordinationLayer()` | 262 | Dead path — LayerManager not imported by SprintScheduler |
| `layers/coordination_layer.py:647` | `CoordinatorRegistry()` | 647 | Dead path — CoordinationLayer only created via LayerManager |
| `layers/coordination_layer.py:1217` | `route_operation()` | 1217 | Unreachable — chain is dead |

### CoordinatorCatalog (Active)

| Caller | Usage |
|--------|-------|
| `coordinators/__init__.py:10` | `catalog.get('core')` — domain mapping |
| `coordinators/__init__.py:11` | `catalog.load('UniversalMemoryCoordinator')` — lazy loading |
| `coordinators/_catalog.py:20` | `catalog.load('UniversalMemoryCoordinator')` in `_load_all()` |

**CoordinatorCatalog is the active lazy-loading surface.** It is used by `coordinators/__init__.py` for import-time lazy loading, not by the canonical sprint runtime.

---

## Component Status

| Component | Path | Status |
|-----------|------|--------|
| `CoordinatorCatalog` | `coordinators/_catalog.py` | **Active** — lazy import surface for `coordinators` package |
| `CoordinatorRegistry` | `coordinators/coordinator_registry.py` | **Legacy/Bypassed** — only called by dead `CoordinationLayer` path |
| `CoordinationLayer` | `layers/coordination_layer.py` | **Legacy/Dead Path** — only instantiated by `LayerManager` |
| `LayerManager` | `layers/layer_manager.py` | **Legacy/Dead Path** — not imported by any canonical runtime module |

---

## Broken Artifact

### `_check_universal_coordinators` — Undefined Function

**Location:** `layers/coordination_layer.py`

**Called at:**
- Line 637: `if not _check_universal_coordinators():`
- Line 1150: `if self._coordinator_registry and _check_universal_coordinators():`
- Line 1451: `"universal_coordinators": _check_universal_coordinators(),`

**Status:** Function is called but never defined in `coordination_layer.py` or any imported module. This is a broken refactoring artifact — the function was likely removed or renamed during a previous refactor but call sites were left behind.

**Impact:** None on production — the entire chain is dead. However, if the chain were ever activated, these call sites would raise `NameError`.

**Action:** Documented only. No fix in this audit commit.

---

## Architecture Seal

See `tests/test_coordinator_routing_authority_seal.py` for the architecture seal test that enforces:

- Canonical runtime modules (`core/`, `runtime/`, `pipeline/`) must NOT import `CoordinatorRegistry`, `CoordinationLayer`, `LayerManager`, `register_all_coordinators`, or `get_registry`
- This seal is enforced as a blocking test

---

## Recommendations

1. **Cleanup in follow-up commit only** — Do not mix audit and cleanup
2. **Known broken artifact:** `_check_universal_coordinators` undefined — documented, not fixed
3. **Before cleanup:** Create legacy quarantine plan or cleanup plan with file removal list and broken import inventory
4. **After cleanup:** Remove `CoordinatorRegistry`, `CoordinationLayer`, `LayerManager` from codebase
5. **Preserve:** `CoordinatorCatalog` as the active lazy-loading surface

---

## Evidence

```bash
# Canonical runtime imports — NO coordinator routing
rg "CoordinatorRegistry|coordination_layer|LayerManager" \
  core/__main__.py runtime/sprint_scheduler.py pipeline/*.py

# Legacy chain — ONLY internal calls
rg "CoordinatorRegistry|coordination_layer|LayerManager" \
  layers/coordination_layer.py layers/layer_manager.py \
  coordinators/coordinator_registry.py

# CoordinatorCatalog — active lazy loading
rg "catalog\.(get|load)" coordinators/__init__.py coordinators/_catalog.py
```
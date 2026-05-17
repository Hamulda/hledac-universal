# Coordinator Routing Cleanup Plan

**Date:** 2026-05-18
**Status:** Planning — No Code Deletion in This Phase
**Commit:** `docs(runtime): plan legacy coordinator routing cleanup`

---

## Prior Work (This Plan Builds On)

| Sprint | Subject | Key Finding |
|--------|---------|-------------|
| F227 | `atomic_storage` shim sealed | Production imports via `__init__.py` redirected to real implementation |
| F226 | `acquisition_strategy` lane planning | Table-driven refactor replaces conditional routing |
| F224 | `IngestPipeline` removal | Canonical ingest path: `async_ingest_findings_batch → async_record_canonical_findings_batch → DuckDBShadowStore._canonical_findings_batch_to_activation_results` |
| Audit | `COORDINATOR_ROUTING_AUTHORITY_AUDIT.md` | Canonical sprint path is `core/__main__.run_sprint() → SprintScheduler` — no routing chain |

### Pre-Existing Failures (Out of Scope)
- `test_r9x_a27` — unrelated to coordinator routing
- `WebSearchArgs` fixture — unrelated to coordinator routing

---

## Current State Summary

### Canonical Runtime Path (ACTIVE)
```
core.__main__.run_sprint()          # sole production sprint owner
  └── SprintScheduler.run()
        └── [Direct coordinator class imports via CoordinatorCatalog]
```

**Canonical coordinator discovery:** Direct class imports + `CoordinatorCatalog` lazy-loading surface in `coordinators/_catalog.py`.

### Legacy Routing Chain (DEAD PATH)
```
LayerManager
  └── CoordinationLayer
        └── CoordinatorRegistry
              └── route_operation()     # unreachable
```

**All three are dead:** `LayerManager` not imported by SprintScheduler; `CoordinationLayer` only created via `LayerManager`; `CoordinatorRegistry` only created via `CoordinationLayer`.

### Broken Artifact
`_check_universal_coordinators()` called at `coordination_layer.py:637, 1150, 1451` but **never defined**. Impact: zero on production (chain is dead). No hot-path caller.

---

## Files in Scope

### Legacy/Bypassed (Proposed for Quarantine → Removal)
| File | Lines | Risk | Notes |
|------|-------|------|-------|
| `layers/coordination_layer.py` | ~1780 | Medium | `CoordinationLayer` + broken `_check_universal_coordinators` artifact |
| `layers/layer_manager.py` | ~920 | Medium | `LayerManager` class, dead path |
| `coordinators/coordinator_registry.py` | ~500 | Low | `CoordinatorRegistry`, dead path |

### Active/Preserve
| File | Risk | Notes |
|------|------|-------|
| `coordinators/_catalog.py` | Active | Lazy-loading surface, used by `coordinators/__init__.py` |
| `coordinators/__init__.py` | Active | Canonical package entry for lazy coordinator loading |
| `layers/__init__.py` | **Mixed** | Re-exports `CoordinationLayer`, `LayerManager` — canonical runtime should NOT use these |

### Seal Test (Active)
| File | Purpose |
|------|---------|
| `tests/test_coordinator_routing_authority_seal.py` | Blocks canonical runtime from importing legacy chain |

### Legacy Test Surface
| File | Risk | Notes |
|------|------|-------|
| `tests/test_autonomous_orchestrator.py:9216-9280` | Medium | `TestLayerManagerIntegration` — uses `LayerManager` in mock spec |
| `tests/test_atomic_storage_arch_seal.py:174` | Low | `TestLayerManagerMigrated` class name only |
| `tests/test_coordinator_routing_authority_seal.py` | Low | Self-referential seal test |

### Legacy Code Surface
| File | Risk | Notes |
|------|------|-------|
| `legacy/autonomous_orchestrator.py:1656, 11403-11942` | Medium | `LayerManager`/`CoordinationLayer` usage — already in `legacy/` |
| `layers/ghost_layer.py` | Low | Comments reference `LayerManager` for shared instance pattern |
| `layers/research_layer.py` | Low | Comments reference `LayerManager` for shared instance pattern |
| `layers/hive_coordination.py` | Low | Uses `CoordinationLayer` enum (different from `coordination_layer.py`) |
| `layers/smart_coordination.py` | Low | Comment redirection to new `CoordinationLayer` |

---

## Risk Matrix

| Risk Level | Category | Files |
|------------|----------|-------|
| **Low** | docs/test-only references | `docs/audits/COORDINATOR_ROUTING_AUTHORITY_AUDIT.md`, `tests/test_coordinator_routing_authority_seal.py`, `tests/test_atomic_storage_arch_seal.py` |
| **Medium** | scripts/live-run utilities, legacy test surface | `legacy/autonomous_orchestrator.py` (already in `legacy/`), `tests/test_autonomous_orchestrator.py:9216-9280` |
| **High** | production/runtime imports | None identified — canonical path already clean |
| **Unknown** | dynamic imports / catalog / `__init__` exports | `layers/__init__.py` re-exports `CoordinationLayer`/`LayerManager`; `coordinators/__init__.py` imports `CoordinatorRegistry` |

---

## Variant A — Legacy Quarantine Plan (Recommended First Phase)

**Principle:** Minimal blast radius. Label, don't delete. Prepare for future removal in a dedicated commit.

### Steps
1. **Annotate source files** with `DEPRECATED` docstrings (no code change, only comments)
2. **Preserve `layers/__init__.py`** re-exports — removing them would break `legacy/autonomous_orchestrator.py` imports
3. **Preserve `coordinators/__init__.py`** `CoordinatorRegistry` import — removing it would break `legacy/autonomous_orchestrator.py`
4. **Do NOT fix `_check_universal_coordinators`** — chain is dead, not a production hot path
5. **Keep existing seal test** — already prevents canonical runtime from using legacy chain

### Files Touched
- `layers/coordination_layer.py` — add DEPRECATED class docstring
- `layers/layer_manager.py` — add DEPRECATED class docstring
- `coordinators/coordinator_registry.py` — add DEPRECATED class docstring

### No Changes To
- Any `__init__.py`
- Any test file (except adding `DEPRECATED` docstring to quarantine comment)
- `legacy/autonomous_orchestrator.py`
- `_check_universal_coordinators` call sites

### Test Commands (Pre-Quarantine)
```bash
# Must pass before quarantine
pytest tests/test_coordinator_routing_authority_seal.py -v
pytest tests/test_autonomous_orchestrator.py -v -k "LayerManager"  # existing tests
pytest tests/test_atomic_storage_arch_seal.py -v  # unrelated but nearby

# Smoke: canonical runtime still works
pytest tests/test_sprint_scheduler.py -v 2>/dev/null || echo "no test_sprint_scheduler.py"
```

### Rollback Strategy
Trivially revert the docstring additions.

---

## Variant B — Full Cleanup Plan

**Principle:** Remove dead code, reduce maintenance surface.

### Steps
1. **Remove files:**
   - `layers/coordination_layer.py`
   - `layers/layer_manager.py`
   - `coordinators/coordinator_registry.py`

2. **Clean `layers/__init__.py`:**
   - Remove `CoordinationLayer` re-exports (lines ~19, ~63, ~107)
   - Remove `LayerManager` re-exports (lines ~74, ~153)
   - Keep `GhostWatchdog`, `DriverStatus` — these are defined in `coordination_layer.py` and would be deleted too. **Check before deleting.**

3. **Clean `coordinators/__init__.py`:**
   - Remove `CoordinatorRegistry` import (line ~154)
   - Remove from `__all__` (line ~281)
   - Keep `CoordinatorCatalog` usage (active surface)

4. **Fix `_check_universal_coordinators` references** (3 sites in `coordination_layer.py` — deleted, so no action needed)

5. **Update `legacy/autonomous_orchestrator.py`:**
   - `LayerManager` import (line ~11422)
   - `CoordinationLayer` import (line ~1656)
   - `LayerManager` instantiation and usage (lines ~11430-11942)
   - Either quarantine the test class or mock the imports

6. **Update `tests/test_autonomous_orchestrator.py`:**
   - `TestLayerManagerIntegration` (lines ~9216-9280) — needs update or removal

7. **Update `layers/ghost_layer.py` and `layers/research_layer.py`:**
   - Remove comments referencing `LayerManager` shared instance

8. **Update `ARCHITECTURE_MAP.py`:**
   - Remove `CoordinatorRegistry` entry

9. **Update `DUPLICATE_AUDIT.md`:**
   - Remove `CoordinatorRegistry` entry

### Files Removed or Modified
| Action | File | Risk |
|--------|------|------|
| DELETE | `layers/coordination_layer.py` | High — check `GhostWatchdog`/`DriverStatus` first |
| DELETE | `layers/layer_manager.py` | High |
| DELETE | `coordinators/coordinator_registry.py` | Medium |
| MODIFY | `layers/__init__.py` | Medium — remove re-exports |
| MODIFY | `coordinators/__init__.py` | Medium — remove `CoordinatorRegistry` |
| MODIFY | `legacy/autonomous_orchestrator.py` | Medium — update imports |
| MODIFY | `tests/test_autonomous_orchestrator.py` | Medium — fix test class |
| MODIFY | `layers/ghost_layer.py` | Low — update comments |
| MODIFY | `layers/research_layer.py` | Low — update comments |
| MODIFY | `ARCHITECTURE_MAP.py` | Low — update map |
| MODIFY | `DUPLICATE_AUDIT.md` | Low — update reference |

### Critical Checkpoints Before Deletion
```bash
# 1. Verify no canonical runtime imports legacy chain
pytest tests/test_coordinator_routing_authority_seal.py -v

# 2. Verify legacy/autonomous_orchestrator.py still imports work after __init__ cleanup
# (requires careful __init__ edit — circular import risk)

# 3. Check GhostWatchdog / DriverStatus — are they used elsewhere?
rg "GhostWatchdog|DriverStatus" --py | grep -v "coordination_layer|layers/__init__"

# 4. Check legacy autonomous_orchestrator LayerManager usage
rg "LayerManager|CoordinationLayer" legacy/autonomous_orchestrator.py

# 5. Smoke: full test suite
pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

### Rollback Strategy
Hardest of the two variants. Requires git revert of ~10 files. Only proceed after Variant A quarantine is committed and tested.

---

## Recommendation

### Phase 1 (Now): Variant A — Legacy Quarantine
**Rationale:**
- Zero production risk (docstring annotations only)
- Low blast radius (`legacy/autonomous_orchestrator.py` stays untouched)
- `_check_universal_coordinators` broken artifact is documented, not fixed (no hot-path impact)
- Creates clean separation between "labeled for future removal" and "actively used"
- Can be done in one commit with clear scope

### Phase 2 (Later Sprint): Variant B — Full Cleanup
**Rationale:**
- Only after quarantine is stable and tested
- Requires careful `__init__.py` edits to avoid breaking `legacy/autonomous_orchestrator.py`
- `GhostWatchdog`/`DriverStatus` need migration or deletion check
- Full test suite must pass before commit
- Dedicated commit with full smoke verification

### Why Not Rope Straight to Variant B
1. `layers/__init__.py` re-exports are consumed by `legacy/autonomous_orchestrator.py` — removing them requires updating that file
2. `GhostWatchdog` and `DriverStatus` are defined in `coordination_layer.py` and exported via `layers/__init__.py` — need to verify if they're used elsewhere
3. `tests/test_autonomous_orchestrator.py` has a `TestLayerManagerIntegration` class that would need updates

---

## Related Findings

- **`docs/audits/COORDINATOR_CAPABILITY_PROTOCOL_AUDIT.md`** — `CoordinatorCapabilities` / `CoordinatorProtocol` is **not needed for canonical sprint runtime** (quarantined routing layer bypass). Recommendation: **No action.** The canonical sprint path uses direct instantiation + `start/step/shutdown` spine; capability routing lives only in `CoordinatorRegistry` with zero active production callers.

---

## Proposed Commit Message
```
docs(runtime): plan legacy coordinator routing cleanup
```

This is a **docs-only commit** — no code deletion, no `__init__.py` changes.

### Commit Contents
- `docs/audits/COORDINATOR_ROUTING_CLEANUP_PLAN.md` (this file)

### Post-Commit Test Commands
```bash
# Seal test still passes (canonical runtime clean)
pytest tests/test_coordinator_routing_authority_seal.py -v

# No regressions
pytest tests/ -x -q --tb=short 2>&1 | tail -5
```
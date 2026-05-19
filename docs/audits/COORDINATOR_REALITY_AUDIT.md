# COORDINATOR REALITY AUDIT — Sprint F244A

**Audit Date:** 2026-05-19
**Scope:** `coordinators/` cross-referenced against `core/`, `runtime/`, `pipeline/`, `fetching/`, `rendering/`, `export/`, `tests/`

---

## 1. CANONICAL RUNTIME PATH (ground truth)

### Entry Point
```
python -m hledac.universal  →  core/__main__.main() --sprint
  → core/__main__.run_sprint()  [SOLE canonical sprint owner]
    → SprintScheduler.run()
```

### SprintScheduler Reality
**`runtime/sprint_scheduler.py`** — does NOT import any coordinator from `coordinators/`.

SprintScheduler owns these direct integrations:

| Concern | Canonical Owner | Coordinator Alternative |
|---------|----------------|------------------------|
| HTTP fetch | `fetching/public_fetcher` | FetchCoordinator (NOT used) |
| Graph storage | `knowledge/graph_service` | GraphCoordinator (NOT used) |
| Graph accumulation | `runtime/graph_accumulator.SprintGraphAccumulator` | GraphCoordinator (NOT used) |
| Multimodal | `multimodal.analyzer.MultimodalEnricher` | MultimodalCoordinator (NOT used) |
| Memory pressure | `core.resource_governor`, `utils.concurrency` | UniversalMemoryCoordinator (NOT used) |
| Rendering | `rendering/macos_webkit_renderer` | RenderCoordinator (NOT used) |
| Security | `security/pii_gate`, `security/passive_dns` | UniversalSecurityCoordinator (NOT used) |
| Validation | `export/formatters` (direct) | UniversalValidationCoordinator (NOT used) |
| Research/Planning | `runtime/investigation_planner.py` | UniversalResearchCoordinator (NOT used) |
| Execution | inline in SprintScheduler | UniversalExecutionCoordinator (NOT used) |

**Verdict:** Zero coordinators from `coordinators/` are on the canonical runtime path.

---

## 2. COORDINATOR INVENTORY

### 2.1 Canonical (used by canonical runtime)

**NONE.** The canonical path (`core.__main__.run_sprint()` → `SprintScheduler`) uses direct module imports, not coordinators.

---

### 2.2 Legacy Active (used by `legacy/autonomous_orchestrator.py`)

| Coordinator | File | Used by Legacy | Notes |
|-------------|------|----------------|-------|
| `FetchCoordinator` | `fetch_coordinator.py` | YES | Spine pattern, start/step/shutdown interface |
| `GraphCoordinator` | `graph_coordinator.py` | YES | Spine pattern, start/step/shutdown interface |
| `RenderCoordinator` | `render_coordinator.py` | YES | Legacy rendering decision tree |
| `ClaimsCoordinator` | `claims_coordinator.py` | YES | Legacy claims pipeline |
| `ArchiveCoordinator` | `archive_coordinator.py` | YES | Legacy archive handling |
| `AgentCoordinationEngine` | `agent_coordination_engine.py` | YES | Multi-agent coordination |

---

### 2.3 Dormant (imported by `layers/coordination_layer.py`, `layers/layer_manager.py`, `orchestrator_integration.py`)

These are wired into alternative orchestration layers that are NOT the canonical sprint owner:

| Coordinator | File | Status |
|-------------|------|--------|
| `UniversalResearchCoordinator` | `research_coordinator.py` | DORMANT — in layers/coordination_layer |
| `UniversalExecutionCoordinator` | `execution_coordinator.py` | DORMANT — in layers/coordination_layer |
| `UniversalSecurityCoordinator` | `security_coordinator.py` | DORMANT — in layers/coordination_layer + export/formatters.py |
| `UniversalMonitoringCoordinator` | `monitoring_coordinator.py` | DORMANT — in layers/layer_manager |
| `UniversalMemoryCoordinator` | `memory_coordinator.py` | DORMANT — in layers/layer_manager + memory_authority.py |
| `UniversalValidationCoordinator` | `validation_coordinator.py` | DORMANT — in orchestrator_integration.py |

---

### 2.4 Dormant / Never Wired

| Coordinator | File | Verdict |
|-------------|------|---------|
| `CoordinatorRegistry` | `coordinator_registry.py` | **LEGACY** — explicitly deprecated in its own docstring. No production callers in canonical path. |
| `MultimodalCoordinator` | `multimodal_coordinator.py` | **DORMANT** — MultimodalEnricher from `multimodal/analyzer.py` is the actual owner |
| `ArchiveCoordinator` | `archive_coordinator.py` | **DORMANT** — wayback handling is inline in sprint_scheduler |
| `ClaimsCoordinator` | `claims_coordinator.py` | **DORMANT** — claims handling is inline |
| `AgentBenchmarker` | `benchmark_coordinator.py` | **DORMANT** — benchmark tooling only |
| `AgentPerformanceOptimizer` | `performance_coordinator.py` | **DORMANT** — performance optimization only |
| `IntelligentResourceAllocator` | `resource_allocator.py` | **DORMANT** — resource allocation only |
| `UniversalSwarmCoordinator` | `swarm_coordinator.py` | **DORMANT** — swarm intelligence, never wired |
| `UniversalMetaReasoningCoordinator` | `meta_reasoning_coordinator.py` | **DORMANT** — meta reasoning, never wired |
| `PrivacyEnhancedResearch` | `privacy_enhanced_research.py` | **DORMANT** — privacy research, never wired |
| `ResearchOptimizer` | `research_optimizer.py` | **DORMANT** — research optimization, never wired |
| `UniversalAdvancedResearchCoordinator` | `advanced_research_coordinator.py` | **DEPRECATED** — wrapper around UniversalResearchCoordinator with warning |

---

## 3. DUPLICATE ANALYSIS (coordinator vs. direct module)

| Coordinator | Actual Runtime Owner | Relationship |
|-------------|---------------------|--------------|
| FetchCoordinator | `fetching/public_fetcher.py` | **DUPLICATE** — FetchCoordinator is a spine-pattern wrapper; `public_fetcher` is the actual fetch implementation used by SprintScheduler. FetchCoordinator is only in `legacy/autonomous_orchestrator.py`. |
| GraphCoordinator | `knowledge/graph_service.py` + `runtime/graph_accumulator.py` | **DUPLICATE** — SprintScheduler uses `graph_service` directly and `SprintGraphAccumulator`. GraphCoordinator is only in legacy. |
| RenderCoordinator | `rendering/macos_webkit_renderer.py` | **DUPLICATE** — `macos_webkit_renderer` is the actual renderer. RenderCoordinator is only in legacy and tests. |
| UniversalSecurityCoordinator | `security/pii_gate`, `security/passive_dns` | **DUPLICATE** — Security operations are direct. SecurityCoordinator appears in `export/formatters.py` but is instantiated per-call (not persistent). |
| UniversalResearchCoordinator | `runtime/investigation_planner.py` | **DUPLICATE** — Research planning is in investigation_planner. |
| UniversalMemoryCoordinator | `core.resource_governor`, `utils/concurrency` | **DUPLICATE** — Memory management is via resource_governor. |
| MultimodalCoordinator | `multimodal/analyzer.py` (MultimodalEnricher) | **DUPLICATE** — MultimodalEnricher is the actual owner, wired directly in sprint_scheduler. |

---

## 4. CROSS-REFERENCE SUMMARY

### `core/__main__.py` — canonical CLI entry
- **No coordinator imports.** Imports `SprintScheduler` directly.

### `runtime/sprint_scheduler.py` — canonical orchestrator
- **No coordinator imports.** Uses `public_fetcher`, `graph_service`, `graph_accumulator`, `multimodal_enricher`, `resource_governor` directly.

### `legacy/autonomous_orchestrator.py` — deprecated orchestrator
- Imports and uses: FetchCoordinator, GraphCoordinator, RenderCoordinator, ClaimsCoordinator, ArchiveCoordinator, AgentCoordinationEngine, privacy_enhanced_research, research_optimizer, memory_coordinator

### `layers/coordination_layer.py` — alternative layer
- Imports and uses: UniversalResearchCoordinator, UniversalExecutionCoordinator, UniversalSecurityCoordinator
- Uses `CoordinatorRegistry()` on line 654 (instantiates deprecated registry)

### `layers/layer_manager.py` — alternative layer
- Imports and uses: AgentCoordinationEngine, ResearchOptimizer, PrivacyEnhancedResearch, UniversalAdvancedResearchCoordinator, UniversalExecutionCoordinator, UniversalMemoryCoordinator, UniversalSecurityCoordinator, UniversalMonitoringCoordinator

### `export/formatters.py` — export
- Instantiates `UniversalSecurityCoordinator` per-call for sanitization

### `orchestrator_integration.py` — integration
- Conditionally imports: UniversalMetaReasoningCoordinator, UniversalMemoryCoordinator, UniversalSecurityCoordinator, UniversalMonitoringCoordinator, UniversalValidationCoordinator, UniversalSwarmCoordinator

---

## 5. RECOMMENDATIONS

### DO NOT USE (permanently dormant)
| Coordinator | Reason |
|-------------|--------|
| `CoordinatorRegistry` | Explicitly deprecated in its own docstring. Zero canonical call sites. |
| `UniversalAdvancedResearchCoordinator` | Deprecated wrapper, emits deprecation warning |
| `AgentBenchmarker` | Benchmark-only, not production runtime |
| `AgentPerformanceOptimizer` | Optimization-only, not production runtime |
| `IntelligentResourceAllocator` | Resource allocation only, not in production path |
| `UniversalSwarmCoordinator` | Never wired, no production caller |
| `UniversalMetaReasoningCoordinator` | Never wired, no production caller |
| `PrivacyEnhancedResearch` | Never wired, no production caller |
| `ResearchOptimizer` | Never wired, no production caller |

### DORMANT (legacy only — do not wire into canonical)
| Coordinator | Owner | Notes |
|-------------|-------|-------|
| `FetchCoordinator` | `fetching/public_fetcher.py` | Use `public_fetcher` directly |
| `GraphCoordinator` | `knowledge/graph_service.py` | Use `graph_service` directly |
| `RenderCoordinator` | `rendering/macos_webkit_renderer.py` | Use `macos_webkit_renderer` directly |
| `UniversalResearchCoordinator` | `runtime/investigation_planner.py` | Use `investigation_planner` directly |
| `UniversalSecurityCoordinator` | `security/pii_gate`, `security/passive_dns` | Use security modules directly |
| `UniversalMemoryCoordinator` | `core.resource_governor` | Use `resource_governor` directly |
| `UniversalExecutionCoordinator` | Inline in SprintScheduler | No separate coordinator needed |
| `UniversalMonitoringCoordinator` | `utils/concurrency` | Use concurrency utils directly |
| `UniversalValidationCoordinator` | `export/formatters` | Use formatters directly |
| `MultimodalCoordinator` | `multimodal.analyzer` | Use `MultimodalEnricher` directly |

### LEGACY (actively used in deprecated code)
| Coordinator | Used By | Notes |
|-------------|--------|-------|
| `FetchCoordinator` | `legacy/autonomous_orchestrator.py` | Legacy spine pattern |
| `GraphCoordinator` | `legacy/autonomous_orchestrator.py` | Legacy spine pattern |
| `RenderCoordinator` | `legacy/autonomous_orchestrator.py` | Legacy rendering |
| `ClaimsCoordinator` | `legacy/autonomous_orchestrator.py` | Legacy claims |
| `ArchiveCoordinator` | `legacy/autonomous_orchestrator.py` | Legacy archive |
| `AgentCoordinationEngine` | `legacy/autonomous_orchestrator.py` | Legacy agent coordination |

---

## 6. ARCHITECTURAL OBSERVATION

### The Coordinator Pattern is a Parallel Framework
The `coordinators/` directory implements an independent orchestration framework with:
- Base class `UniversalCoordinator` with lifecycle management
- `CoordinatorRegistry` for routing
- `start/step/shutdown` spine pattern interface
- Domain-organized catalog with lazy loading

**This framework is NOT wired into the canonical sprint runtime.** SprintScheduler owns all orchestration directly through direct module imports.

### Two Orchestration Systems Exist
1. **Canonical:** `core.__main__.run_sprint()` → `SprintScheduler` — direct module imports, no coordinators
2. **Legacy/Alternative:** `legacy/autonomous_orchestrator.py`, `layers/coordination_layer.py` — coordinator pattern

### Risk
Future developers may incorrectly assume coordinators are part of the production path. The `_catalog.py` and `__init__.py` make the coordinators appear first-class and active. The explicit `deprecated` markers on `CoordinatorRegistry` are good, but other dormant coordinators lack such markers.

---

## 7. AUDIT RESULTS

```
LOCAL_CHANGES_DONE=false
F244A_COORDINATOR_REALITY_AUDIT_COMPLETE=true
CANONICAL_COORDINATORS=none
DORMANT_COORDINATORS=[
  CoordinatorRegistry,
  UniversalResearchCoordinator,
  UniversalExecutionCoordinator,
  UniversalSecurityCoordinator,
  UniversalMonitoringCoordinator,
  UniversalMemoryCoordinator,
  UniversalValidationCoordinator,
  MultimodalCoordinator,
  ArchiveCoordinator,
  ClaimsCoordinator,
  AgentBenchmarker,
  AgentPerformanceOptimizer,
  IntelligentResourceAllocator,
  UniversalSwarmCoordinator,
  UniversalMetaReasoningCoordinator,
  PrivacyEnhancedResearch,
  ResearchOptimizer,
  UniversalAdvancedResearchCoordinator,
]
LEGACY_ACTIVE_COORDINATORS=[
  FetchCoordinator,
  GraphCoordinator,
  RenderCoordinator,
  ClaimsCoordinator,
  ArchiveCoordinator,
  AgentCoordinationEngine,
]
DUPLICATE_COORDINATORS=[
  FetchCoordinator (duplicates fetching/public_fetcher.py),
  GraphCoordinator (duplicates knowledge/graph_service.py),
  RenderCoordinator (duplicates rendering/macos_webkit_renderer.py),
  UniversalSecurityCoordinator (duplicates security/pii_gate, security/passive_dns),
  UniversalResearchCoordinator (duplicates runtime/investigation_planner.py),
  UniversalMemoryCoordinator (duplicates core/resource_governor),
  MultimodalCoordinator (duplicates multimodal/analyzer.py),
]
DO_NOT_USE_COORDINATORS=[
  CoordinatorRegistry,
  UniversalAdvancedResearchCoordinator,
  AgentBenchmarker,
  AgentPerformanceOptimizer,
  IntelligentResourceAllocator,
  UniversalSwarmCoordinator,
  UniversalMetaReasoningCoordinator,
  PrivacyEnhancedResearch,
  ResearchOptimizer,
]
EXTRACTABLE_HELPERS=none (no canonical coordinator owns a unique capability not already in a direct module)
AUDIT_PATH=docs/audits/COORDINATOR_REALITY_AUDIT.md
BLOCKERS=none
```

---

## 8. FILES CREATED

- `docs/audits/COORDINATOR_REALITY_AUDIT.md` — this document
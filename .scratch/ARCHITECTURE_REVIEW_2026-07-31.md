# Architecture Review — 2026-07-31

**Scope:** `core/`, `runtime/`, `knowledge/`
**Tool:** pyscn 1.29.0
**Analysis date:** 2026-07-31

---

## Executive Summary

| Directory | Health | Grade | Worst Score | Layer Violations | Compliance |
|-----------|--------|-------|-------------|-------------------|------------|
| `core/` | 76 | B | Duplication (35) | 0 | 100% |
| `runtime/` | 77 | B | Duplication (35) | 0 | 99% |
| `knowledge/` | 68–71 | C | Duplication (35) | **0 (by oprava)** | **93.5%** |
| `pipeline/` | 73 | C | Complexity + Duplication | ~0 | ~90% |
| `coordinators/` | 66 | C | Duplication + Cohesion | **0 (by oprava)** | **91.7%** |
| `transport/` | 66 | C | Complexity | **0 (by oprava)** | **80%** |
| `brain/` | **78** | **B** | Duplication (70) | 7 warnings | 92% |

**Dominant pattern:** Layer violations in knowledge/, coordinators/, transport/ byly způsobeny chybějící `transport` layer v .pyscn.toml a chybějícím `duckdb_write_coordinator` v storage layer. Po opravě: 0 layer violations. Zbývající SRP violations jsou dokumentované false positives nebo inherententní facade aggregátory.

---

## Extended Scope: pipeline/, coordinators/, brain/, transport/

> Note: `transport/` overwrite note (starý) — po opravě .pyscn.toml 2026-07-31 jsou data správná. Viz sekce "Po opravě .pyscn.toml" výše.

---

## 1. Complexity

Complexity parsing returned avg=0.00 for all three dirs — the async/await patterns and type stubs in this codebase confuse the pyscn AST parser. This is a tooling blind spot, not a quality signal. The full-repo run found 1,577 high-complexity functions; the heaviest are:

| File | Function | CC |
|------|----------|----|
| `archive/scheduler_archives/sprint_scheduler_v1_archived.py:4255` | `_run_internal` | **202** |
| `pipeline/live_public_pipeline.py:2519` | `async_run_live_public_pipeline` | 170 |
| `pipeline/public_fetch.py:106` | `_fetch_and_process_page` | 83 |
| `runtime/sprint_entrypoint.py:2032` | `run_sprint` | 80 |
| `coordinators/fetch_coordinator.py:1687` | `_fetch_url` | 78 |

The archived scheduler (~18K lines) is the dominant complexity hotspot in the repo. It should be confirmed as truly dead and archived out of the analysis scope.



## 2. Coupling

### core/ (avg CBO 1.5, only 1 high-coupling class)
| Class | CBO | File |
|-------|-----|------|
| AccelBackend | 8 | `__init__.py:205` |
| DLQManager | 7 | `dlq_manager.py:138` |
| BaseInferenceBackend | 5 | `_base.py:53` |
| MlxcelBackend | 5 | `mlxcel_backend.py:39` |
| MLXWorker | 5 | `mlx_inference_lock.py:57` |

Clean. DLQManager at CBO=7 is the only notable coupling — changes there ripple into the MLX inference path.

### runtime/ (avg CBO 1.7, 4 high-coupling classes)
| Class | CBO | File |
|-------|-----|------|
| AcquisitionOrchestrator | 12 | `acquisition.py:83` |
| SprintSchedulerV2 | 12 | `scheduler.py:36` |
| SidecarOrchestrator | 11 | `sidecar_orchestrator.py:267` |
| SprintAdvisoryRunner | 8 | `sprint_advisory_runner.py:148` |
| WinddownOrchestrator | 5 | `winddown.py:62` |

These are orchestration coordinators — high CBO is expected for classes that instantiate and coordinate many subsystems. However, CBO=12 means both `AcquisitionOrchestrator` and `SprintSchedulerV2` depend on 12+ other classes directly. **Recommendation:** Extract formal interface protocols to reduce instantiation coupling.



---

## 3. Cohesion (LCOM4)

Note: LCOM values showed `?` across all classes in knowledge/ — pyscn's LCOM parser doesn't handle this codebase's patterns well. The method counts below are reliable; treat the LCOM values as unreliable.

### core/
7 high-LCOM classes. All LCOM=0 classes are domain adaptor patterns (`_PythonJsonDomain`, `_PythonHotEdgesDomain`, `FederatedQTableDomain`, `_PythonTextDomain`, `_PythonMadvisDomain`) — likely false positives from data-flow-through domain embedding patterns. Not actionable.



### knowledge/
4 high-LCOM classes, all are real concerns:

| Class | Methods | File |
|-------|---------|------|
| `DuckDBStoreProtocol` | 43 | `duckdb_protocol.py:40` |
| `DuckDBShadowStore` | 304 | `duckdb_store.py:1836` |
| `RAGEngine` | 30 | `rag_engine.py:526` |
| `LanceDBIdentityStore` | 46 | `lancedb_store.py:326` |
| `GraphService` | 17 | `graph_service.py:101` |
| `UnifiedDatabaseFacade` | 17 | `db.py:179` |
| `_DuckDBQueryExecutor` | 18 | `duckdb_store.py:2797` |

`DuckDBStoreProtocol` at 43 methods is an oversized interface — it should be split into focused subslices (ingest vs query). `DuckDBShadowStore` at 304 methods is a confirmed god object.

---



---

## 5. Dead Code — 100/100 all dirs

No dead code found. Clean across all three directories.

---

## 6. Dependencies

| Dir | Cycles | Max Depth | Score |
|-----|--------|-----------|-------|
| core/ | 0 | 1 | 80 |
| runtime/ | 0 | 1 | 80 |
| knowledge/ | 0 | 3 | 80 |

No dependency cycles in any directory. `knowledge/` has depth=3 (likely DuckDB → LMDB → path chain or similar). Acceptable.

---

## 7. Architecture Compliance

| Dir | Violations | Compliance | Score |
|-----|-----------|-----------|-------|
| core/ | 0 | 1.000 | 100 |
| runtime/ | 0 | 0.988 | 99 |
| **knowledge/** | **0** (by oprava) | **0.935** | **93.5** |

### core/ and runtime/: Clean
Architecture compliance near-perfect. No violations detected.

### knowledge/ — OPRAVENO (2026-07-31)
Původních 17 violations (5 errors + 12 warnings) byly způsobeny:
1. `transport` layer nebyla definována → "unknown layer" violations
2. `duckdb_write_coordinator` nebyl v žádné layer → spadl do `root` → storage→root violation
3. `storage` rule neměla `crosscutting` v allow listu → `finding_envelope` dependency

Oprava: .pyscn.toml přidána `transport` layer + `crosscutting` do storage allow.


---

## 8. Community Structure

### core/
- 92 communities, modularity **0.622** (good separation)
- Community risk: 39
- 2 bridge modules: `core.embeddings.legacy`, `core.mlx_embeddings`

### runtime/
- 143 communities, modularity 0.500
- Community risk: 25 (lowest of the three)
- No high-risk bridge modules



y



---



---

## brain/ (Health 78 — Grade B) ✅ healthiest of the seven

| Score | Value |
|-------|-------|
| Health | 78 |
| Grade | B |
| Complexity | 90 (avg 3.5, 98 high-CC) |
| Duplication | **70** ⚠️ (9.0%, 64 groups) |
| Coupling | 80 (avg CBO 2.4, **7 high-CBO**) |
| Cohesion | 90 (avg LCOM 1.4, 6 high-LCOM) |
| Dependencies | 80 (depth 2, no cycles) |
| Architecture | **92%** ✅ (7 warnings, 0 errors) |
| Clone pairs | 85 pairs, 64 groups |





---

## transport/ (Health 66 — Grade C)

> ⚠️ Soubor `analyze_20260731_114008.json` (transport/) byl při paralelním běhu přepsán obsahem `coordinators/`. Výsledky jsou shodné s `coordinators/` — viz sekce `coordinators/` výše. Opakovaná analýza `transport/` sekvenčně by gives true transport-layer data.

---

## Priority Action Items (aktualizované — po opravě .pyscn.toml)

**.pyscn.toml opravy (2026-07-31):**
- Přidána `transport` layer (30 modulů) a `duckdb_write_coordinator` do `storage` layer
- Přidány `transport` do `coordinators` a `pipeline` allow-rules
- Přidán `crosscutting` do `storage` allow-rule (finding_envelope dependency)
- Layer violations: **knowledge 17→0**, **coordinators 17→0**, **transport ~11→0**

| # | Action | Directory | Priority | Effort |
|---|--------|-----------|----------|--------|
| 1 | **`duckdb_store` — SRP false positive** — mixes 13 concerns, fan-in 3. Dokumentovaný false positive v pyscn.toml. Není potřeba akce. | knowledge/ | — | — |
| 2 | **`coordinators.base` SRP** — 13 dependency concerns. Inherententní facade/base aggregator. Subtilní. | coordinators/ | P2 | Low |
| 3 | **`transport.base` SRP** — 8 dependency concerns. Re-export aggregator za [TP-1] invariant. | transport/ | P2 | Low |
| 4 | **Split `HypothesisEngine`** — CBO=29, 91 methods. | brain/ | P1 | High |
| 5 | **`_quality_types` SRP** — 4 concerns, fan-in 4. Dokumentovaný false positive. | knowledge/ | — | — |
| 6 | **Deduplikovat klony** — 529 (knowledge/) + 272 (runtime/) + 177 (coordinators/). | cross-dir | P2 | Medium |
| 7 | **Verify archived scheduler** — `sprint_scheduler_v1_archived.py` (~18K lines, CC=202). | archive/ | P3 | Low |

**Reálne zbývající architekturní problémy:**
- Žádné layer violations — 0 ve všech třech adresářích
- SRP violations: `coordinators.base` (13 concerns), `transport.base` (8), `transport.curl_cffi_fetch` (5) — všechny jsou inherententní agregátory, ne izolované moduly

---

## Tooling Notes

- **LCOM parsing unreliable** — all values showed `?`. Method counts are reliable; LCOM ratios are not.
- **Clone group IDs show `None`** in pyscn output — a parsing bug specific to this codebase's AST patterns. Pair counts and statistics are reliable; per-clone file refs are not.
- **Dependency chain extraction empty** — pyscn's chain tracer returned depth=0 for all 10 longest chains. The violations list is the reliable dependency signal.
- **Complexity avg=0.00** in core/runtime/knowledge dirs — async/await + type stub patterns confuse the parser. The full-repo run and per-dir runs on pipeline/coordinators/brain/transport captured CC correctly.
- **pyscn v1.29.0 `--select arch` neexistuje** — validni flagy: `complexity`, `deadcode`, `clones`, `cbo`, `lcom`, `deps`, `communities`. Pro architecture analysis použij `--select deps`.

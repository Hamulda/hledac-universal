# Audit Report — Placeholder/TODO Analysis — `hledac/universal`
Datum: 2026-05-09
Metodologie: Přímá kontrola zdrojového kódu (ne z dokumentace)

---

## P1 — CRITICAL (Aktivní produkční kód s NotImplementedError)

| Soubor | Řádek | Popis | Kontext | Ověřeno |
|--------|-------|--------|---------|---------|
| `brain/ane_embedder.py` | 154 | `raise NotImplementedError("ANE embedder not loaded, use fallback")` | `embed()` hodil, pokud není ANE loaded. Catch na řádku 180 — padá do MLX fallback. Warmup na 183 catchuje a skipuje gracefully. **ANE reranker nikdy neproběhne v produkci.** | Ověřeno: 2026-05-09 ✅ VALID — `embed()` stále raises NotImplementedError, fallback v `sprint_diff_engine.py:230` checkuje `is_loaded` PŘED voláním, ANE path nikdy neproběhne |
| `deep_probe.py` | 386 | `raise NotImplementedError("PathPattern.generate_predictions must be implemented by subclass")` | Abstraktní base `PathPattern.generate_predictions()` bez default impl. **Subclasses existují** — `DatePathPattern`, `FilePathPattern` overridují. Není blocking pro známé subclass, ale base je prázdná. | Ověřeno: 2026-05-09 ✅ VALID — base stále prázdná, subclasses (`DatePathPattern`, `FilePathPattern`, `ExtensionPathPattern`) jsou funkční. Volající kód má fallback na řádku 242. |
| `project_types.py` | 755 | `raise NotImplementedError("Subclasses must implement research()")` | Abstraktní base `BaseProject.research()` — enforced NotImplementedError. Všechny concrete project types inherit a implementují. | Ověřeno: 2026-05-09 ✅ VALID — intentional abstract base pattern, všechny concrete typy implementují `research()`. |
| `tests/test_sprint55.py` | 46 | `# Without loading, should raise NotImplementedError` | Test comment dokumentující expected behavior — intentional test assertion. | Ověřeno: 2026-05-09 ✅ VALID — test comment, není produkční kód. |

---

## P2 — HIGH (FUTURE markery = plánované, ale neimplementované)

| Soubor | Řádek | Popis |
|--------|-------|--------|
| `network/session_runtime.py` | 23 | `# FUTURE(8AC):` — `DomainConcurrencyBandit` integration odložena |
| `network/session_runtime.py` | 24 | `# FUTURE(8AD):` — per-transport sessions odloženy, `SourceTransportMap` dostupný ale nepoužitý |
| `network/session_runtime.py` | 25 | `# FUTURE(8AE):` — `SourceTransportMap` částečně integrován v `FetchCoordinator`, rozšíření odloženo |
| `project_types.py` | 1562 | `# LifecycleSnapshot: FUTURE canonical owner = runtime/sprint_lifecycle.py` |
| `project_types.py` | 1566 | `# ProviderRecommendation: FUTURE canonical owner = capabilities.py` |
| `project_types.py` | 1729 | `# [1] This is a FUTURE canonical target, not immediate migration` |
| `project_types.py` | 1739 | `This is the FUTURE canonical target for the local seam in enhanced_research.py` |
| `project_types.py` | 1816 | `# FUTURE: WINDUP HANDOFF & WARMUP HANDOFF (Phase 2+)` |
| `knowledge/duckdb_store.py` | 884 | `FUTURE OWNER / REMOVAL CONDITION` — graph truth owner annotation |
| `knowledge/duckdb_store.py` | 1230 | `FUTURE OWNER / REMOVAL CONDITION` — graph truth owner annotation |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis |
|--------|-------|--------|
| `utils/worker_pool.py` | 1 | `# DEPRECATED/UNUSED — zero callers as of F214CLEAN (2026-05-06)` — potvrzeno zero callers, mrtvý kód kandidát na smazání |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis |
|--------|-------|--------|
| `utils/worker_pool.py` | 1 | `# DEPRECATED/UNUSED — zero callers as of F214CLEAN (2026-05-06)` — potvrzeno zero callers, mrtvý kód kandidát na smazání |

---

## P3 — MEDIUM (Reálné TODO vyžadující implementaci)

| Soubor | Řádek | Popis | Kontext |
|--------|-------|--------|---------|
| `utils/shared_tensor.py` | 3, 21 | `to je zatím TODO` / `TODO pro budoucí implementaci` | **Zero-copy Metal buffer neimplementován** — `SharedTensor` používá MLX array wrapper, ale true zero-copy vyžaduje Metal shared memory. Aktuální impl je funkční ale ne optimální. |
| `planning/htn_planner.py` | 660 | `# TODO 8S/8T: further refine per-task instrumentation if Hermes` | Per-task instrumentation potřebuje Hermes-native timing pro error cases. Currently uses observed elapsed s fallback. |
| `planning/htn_planner.py` | 724 | `# TODO §7.4/§5.15: nahradit quality/corroboration score` | `confidence = 0.8` hardcoded — potřebuje real quality/corroboration score from Hermes. |
| `knowledge/duckdb_store.py` | 168 | `TODO 8Q/8R: zvážit přesun CanonicalFinding do sdíleného DTO modulu` | CanonicalFinding používaný přes storage boundary — could move to shared DTO if used outside storage layer. |
| `legacy/atomic_storage.py` | 1178 | `# TODO: Use Hermes for extraction (requires integration)` | Archive extraction by měla používat Hermes, ale integrace není hotová. |
| `legacy/autonomous_orchestrator.py` | 26713 | `# TODO: actual archive fetch (future)` | Archive fetch je future item v legacy AO. |
| `discovery/discovery_planner.py` | 146 | `# TODO: commoncrawl-specific endpoint when adapter supports it` | CommonCrawl adapter ještě není podporován — currently falls back to wayback_cdx. Soft TODO. |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis | Kontext |
|--------|-------|--------|---------|
| `planning/htn_planner.py` | 724 | `confidence = 0.8  # TODO §7.4/§5.15: nahradit quality/corroboration score` | Hardcoded confidence v `_runtime_result_to_canonical_finding()`. Metoda `_cost_model_confidence()` existuje ale není v tomto call path použita. |
| `deep_probe.py` | 1019 | `confidence=0.9 if result.get("objects") else 0.5` | Hardcoded conditional confidence v S3 bucket finding. |
| `deep_probe.py` | 1264 | `confidence=0.7` | Hardcoded confidence v IPFS finding. |
| `layers/coordination_layer.py` | 1172, 1274, 1284, 1378, 1825 | `confidence=0.0`, `0.9`, `0.5`, `0.5`, `0.6` | Více hardcoded confidence hodnot v coordination layer. |
| `layers/stealth_layer.py` | 223, 241, 274, 298, 355 | `confidence=0.0` (4×), `0.75` | Více hardcoded confidence hodnot ve stealth layer. |
| `cache/budget_manager.py` | 620, 632 | `min_confidence=0.6`, `0.8` | Hardcoded min_confidence thresholdy v budget manageru. |
| `intelligence/social_identity_miner.py` | 31 | `if False: from ..knowledge.duckdb_store import DuckDBShadowStore` | **Mrtvý kód** — `if False:` block, import nikdy neproběhne. DuckDBShadowStore v tomto souboru není používán. |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis | Kontext |
|--------|-------|--------|---------|
| `planning/htn_planner.py` | 724 | `confidence = 0.8  # TODO §7.4/§5.15: nahradit quality/corroboration score` | Hardcoded confidence v `_runtime_result_to_canonical_finding()`. Metoda `_cost_model_confidence()` existuje ale není v tomto call path použita. |
| `deep_probe.py` | 1019 | `confidence=0.9 if result.get("objects") else 0.5` | Hardcoded conditional confidence v S3 bucket finding. |
| `deep_probe.py` | 1264 | `confidence=0.7` | Hardcoded confidence v IPFS finding. |
| `layers/coordination_layer.py` | 1172, 1274, 1284, 1378, 1825 | `confidence=0.0`, `0.9`, `0.5`, `0.5`, `0.6` | Více hardcoded confidence hodnot v coordination layer. |
| `layers/stealth_layer.py` | 223, 241, 274, 298, 355 | `confidence=0.0`, `0.0`, `0.0`, `0.0`, `0.75` | Více hardcoded confidence hodnot ve stealth layer. |
| `cache/budget_manager.py` | 620, 632 | `min_confidence=0.6`, `0.8` | Hardcoded min_confidence thresholdy. |
| `intelligence/social_identity_miner.py` | 31 | `if False: from ..knowledge.duckdb_store import DuckDBShadowStore` | **Mrtvý kód** — `if False:` block, import nikdy neproběhne. DuckDBShadowStore není v tomto souboru používán. |

---

## P4 — LOW (Placeholdery v aktivním kódu)

| Soubor | Řádek | Popis | Typ |
|--------|-------|--------|-----|
| `brain/model_lifecycle.py` | 634 | `Toto je placeholder pro budoucí implementaci prediktivního preloadu. Momentálně jen loguje hint.` | **Placeholder** — aktivní comment o neimplementované funkci |
| `coordinators/advanced_research_coordinator.py` | 84 | `# Return empty list as placeholder - DeepProbeScanner moved to deep_research module` | **Placeholder** — vrací `[]`, DeepProbeScanner přesunut |
| `coordinators/claims_coordinator.py` | 269 | `For now, returns empty list as placeholder.` | **Placeholder** — claim extraction vrací prázdný seznam |
| `execution/ghost_executor.py` | 133 | `# ToolRegistry handlery jsou placeholder/univerzální.` | **Komentář** — vysvětluje že TR handlery jsou placeholder |
| `execution/ghost_executor.py` | 148 | `# Ghost: placeholder (vrací prázdné)` | **Komentář** — RESEARCH_PAPER → academic_search placeholder |
| `execution/ghost_executor.py` | 931-937 | `STUB_METADATA`, `"contract": "noop_placeholder"`, `"implemented": False` | **Aktivní stub metadata** — stub akce definované pro truthful distinguishability |
| `coordinators/multimodal_coordinator.py` | 446, 448, 450, 452 | `# Image processor (placeholder)` / `# Audio processor (placeholder)` etc. | **Placeholder** — modality processors označeny jako placeholder |
| `coordinators/multimodal_coordinator.py` | 785 | `"""Process document content (placeholder)."""` | **Placeholder** — `_process_document` returns `ModalityOutput` |
| `discovery/discovery_planner.py` | 152 | `# Feed pivots and CT pivots require pipeline context — stub for now` | **Stub** — feed/CT pivots vyžadují pipeline context, currently gated |
| `orchestrator_integration.py` | 10 | `Role: Legacy backward-compatibility stub only` | **Stub** — dormant modul bez produkční authority |
| `intelligence/social_identity_miner.py` | 31 | `if False: from ..knowledge.duckdb_store import DuckDBShadowStore` | **Mrtvý kód** — conditional import bez efektu (if False block) |
| `runtime/sidecar_bus.py` | 36 | `if False: from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore` | **Mrtvý kód** — conditional import bez efektu (if False block) |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis | Typ |
|--------|-------|--------|-----|
| `intelligence/decision_engine.py` | 1 | `# DEPRECATED — use brain.decision_engine` | Deprecated class, backward compat only, zero active callers. |
| `model_lifecycle.py` | 1 | `# DEPRECATED — use brain.model_lifecycle` | Deprecated facade. |
| `pipeline/live_feed_pipeline.py` | 1116 | `# DEPRECATED: pipeline now uses pattern-backed approach` | Deprecated pipeline, replaced by pattern approach. |
| `enhanced_research.py` | 3011, 3030, 3057 | `# DEPRECATED F187A:` — backward-compat helpers only | Multiple deprecated helpers. |
| `runtime_authority_manifest.py` | 66 | `# DEPRECATED FACADE files — re-export facades` | Deprecated facade. |
| `legacy/autonomous_orchestrator.py` | 3729-3733 | `# DEPRECATED in Sprint 8AI — replaced by _MONOPOLY_GUARD_WINDOW_SEC` | Multiple deprecated monopoly guard fields. |

### Průchod 2026-05-09 — nové nálezy

| Soubor | Řádek | Popis | Typ |
|--------|-------|--------|-----|
| `intelligence/decision_engine.py` | 1 | `# DEPRECATED — use brain.decision_engine` | Deprecated class, backward compat only, zero active callers. |
| `model_lifecycle.py` | 1 | `# DEPRECATED — use brain.model_lifecycle` | Deprecated facade. |
| `pipeline/live_feed_pipeline.py` | 1116 | `# DEPRECATED: pipeline now uses pattern-backed approach via _entry_to_pattern_findings` | Deprecated pipeline, replaced by pattern approach. |
| `enhanced_research.py` | 3011, 3030, 3057 | `# DEPRECATED F187A:` markers | Multiple deprecated backward-compat helpers. |
| `runtime_authority_manifest.py` | 66 | `# DEPRECATED FACADE files — re-export facades that look like orchestrators` | Deprecated facade. |
| `legacy/autonomous_orchestrator.py` | 3729-3733 | `# DEPRECATED in Sprint 8AI — replaced by _MONOPOLY_GUARD_WINDOW_SEC` | Multiple deprecated fields (monopoly guard) in legacy module. |

---

## P5 — Informativní komentáře (ne placeholders)

| Soubor | Řádek | Popis | Verdikt |
|--------|-------|--------|---------|
| `security/pii_gate.py` | 414, 426 | `# Format: +XX XXX XXX XXXX...` | **Inline format dokumentace** pro regex patterns — NOT TODO |
| `tools/prelive_decision_gate.py` | 149 | `probe_XXX` in JSON schema | Placeholder **v test dokumentaci**, ne v kódu |
| `tests/test_autonomous_orchestrator.py` | 9475, 9589 | `# Rodné číslo format...` / `# Use phone format...` | **Inline test comments** dokumentující expected input format |
| `intelligence/data_leak_hunter.py` | 67 | `HACKER_FORUM = "hacker_forum"` | **Label constant**, ne HACK comment — string enum value |
| `enhanced_research.py` | 792 | `# TASK IMPLEMENTATIONS (replacing TODOs)` | **Meta-komentář** dokumentující že real impl nahradily TODOs |
| `smoke_runner.py` | 259, 261 | `# NOTE: ghost modules deleted` | **Historical audit trail** — dokumentuje smazané ghost moduly |
| `core/__main__.py` | 787-792 | `# F214Q: Remote debug OPSEC guard` | **Active production code** — guards Python 3.14 safe-external-debugger |
| `core/__main__.py` | 1258 | `# NOTE (F189A): meaningful_empty_run moved...` | **Active architectural note** — dokumentuje execution order |
| `coordinators/monitoring_coordinator.py` | 797, 804 | `TODO/FIXME comments` | **Feature description** pro `strict_mode` — popisuje co mode kontroluje |
| `brain/hermes3_engine.py` | 117 | `MAX_PENDING_FUTURES = 256` | Named constant, ne FUTURE comment — bound constant |
| `discovery/rss_atom_adapter.py` | 316, 343 | `_FUTURE_GAP_MAX` | **Named constant** pro future-gap tolerance — ne TODO comment |
| `ARCHITECTURE_MAP.py` | 54, 379, 489, 651, 674, 854 | `DEPRECATED` / `BEST SEAM FOR FUTURE` | **Architecture map dokumentace** — ne aktivní kód |
| `utils/semantic.py` | 635 | `# DEPRECATED CLASSES - Kept for backward compatibility` | **Real deprecated class block** s backward compat comment |
| `execution/ghost_executor.py` | 936 | `"contract": "noop_placeholder"` | Součást `STUB_METADATA` dict — aktivní stub contract definition |

---

## Deprecated Modules (do not delete — backward compatibility)

| Soubor | Řádek | Popis | Status |
|--------|-------|--------|--------|
| `intelligence/decision_engine.py` | 1 | `# DEPRECATED — use brain.decision_engine` | Deprecated class, backward compat only |
| `model_lifecycle.py` | 1 | `# DEPRECATED — use brain.model_lifecycle` | Deprecated facade |
| `pipeline/live_feed_pipeline.py` | 1116 | `# DEPRECATED: pipeline now uses pattern-backed approach` | Deprecated pipeline |
| `enhanced_research.py` | 3011, 3030, 3057 | `# DEPRECATED F187A:` — backward-compat helpers only | Multiple deprecated helpers |
| `runtime_authority_manifest.py` | 66 | `# DEPRECATED FACADE files` | Deprecated facade |
| `legacy/autonomous_orchestrator.py` | 3729-3733 | `# DEPRECATED in Sprint 8AI` — monopoly guard fields | Multiple deprecated fields |
| `utils/semantic.py` | 635 | `# DEPRECATED CLASSES - Kept for backwards compatibility` | Real deprecated class block |
| `legacy/autonomous_orchestrator.py` | 26713 | `# TODO: actual archive fetch (future)` | Future item in deprecated module |

---

## Deprecated Modules (do not delete — backward compatibility)

| Soubor | Řádek | Popis | Status |
|--------|-------|--------|--------|
| `intelligence/decision_engine.py` | 1 | `# DEPRECATED — use brain.decision_engine` | Deprecated class, backward compat only |
| `model_lifecycle.py` | 1 | `# DEPRECATED — use brain.model_lifecycle` | Deprecated facade |
| `pipeline/live_feed_pipeline.py` | 1116 | `# DEPRECATED: pipeline now uses pattern-backed approach` | Deprecated pipeline |
| `enhanced_research.py` | 3011, 3030, 3057 | `# DEPRECATED F187A:` — backward-compat helpers only | Multiple deprecated helpers |
| `runtime_authority_manifest.py` | 66 | `# DEPRECATED FACADE files` | Deprecated facade |
| `legacy/autonomous_orchestrator.py` | 3729-3733 | `# DEPRECATED in Sprint 8AI` — monopoly guard fields | Multiple deprecated fields |
| `utils/semantic.py` | 635 | `# DEPRECATED CLASSES - Kept for backwards compatibility` | Real deprecated class block |

---

## Prázdné testovací probe soubory (bez implementace)

| Soubor | Status |
|--------|--------|
| `tests/probe_f130f/__init__.py` | 1řádkový comment placeholder — žádné testy, žádná implementace |
| `tests/probe_8ad/__init__.py` | Prázdný (0 lines) |
| `tests/probe_8bg/__init__.py` | 1řádkový comment placeholder |
| `tests/probe_8se/__init__.py` | Prázdný (0 lines) |

---

## Dřívější AUDIT_REPORT.md — STALE CLAIMS (overruled)

AUDIT_REPORT.md z 2026-05-08 tvrdil že tyto soubory mají "TODO: rekonstruovat z bytecode" + prázdné stuby — **přímá kontrola zdrojového kódu prokázala že jsou PLNĚ IMPLEMENTOVÁNY:**

| Soubor | AUDIT_REPORT.md claim | Skutečnost |
|--------|----------------------|------------|
| `knowledge/search_index.py` | 5 prázdných stub tříd | **Full BM25 implementation** — `SearchDocument`, `SearchResult`, `BM25Index` (227 lines, plná implementace včetně `add()`, `search()`, `_score_bm25()`, `_tokenize()`) |
| `tests/probe_bench_g/run_m1_inference_mlx_baseline.py` | 9 NotImplementedError stub funkcí | **9 working probe functions** — všechny mají reálné implementace (mlx import check, metal memory, model latency měření atd.) |
| `tests/probe_f12g/__init__.py` | Prázdné stub třídy | **5 real test classes, 19 real test methods** |
| `tests/probe_f191b/__init__.py` | Prázdné stub třídy | **Real test classes s reálnými test methods** |
| `tests/probe_f300a/__init__.py` | Prázdné stub třídy | **Real test classes s reálnými test methods** |

---

## Summary by Severity

| Severity | Count | Items |
|---|---|---|
| **P1 CRITICAL** | 4 | `brain/ane_embedder.py:154` (ANE embed production path always raises), `deep_probe.py:386` (abstract base without impl), `project_types.py:755` (abstract base), `tests/test_sprint55.py:46` (test comment) |
| **P2 HIGH** | 10 | 3 FUTURE(8AC/8AD/8AE) in `session_runtime.py`, 5 in `project_types.py`, 2 in `duckdb_store.py` |
| **P3 MEDIUM** | 7 | 2 in `shared_tensor.py`, 2 in `htn_planner.py`, 1 in `duckdb_store.py`, 1 in `atomic_storage.py`, 1 in `legacy/ao`, 1 in `discovery_planner.py` |
| **P4 LOW (placeholder)** | 12 | `model_lifecycle.py:634`, `advanced_research_coordinator.py:84`, `claims_coordinator.py:269`, `ghost_executor.py:133,148,931-937`, `multimodal_coordinator.py:446-452,785`, `discovery_planner.py:152`, `orchestrator_integration.py:10`, `social_identity_miner.py:31`, `sidecar_bus.py:36` |
| **P5 informational** | 14+ | Format docs, feature descriptions, architecture notes |
| **Empty test probes** | 4 | `probe_f130f`, `probe_8ad`, `probe_8bg`, `probe_8se` |
| **Stale claims in AUDIT_REPORT.md** | 5 | search_index.py, run_m1_inference_mlx_baseline.py, probe_f12g/f191b/f300a — all fully implemented |

---

## Targeted Scan — Functional Gaps (2026-05-09)

### OBLAST 1 — Disconnected Components

#### knowledge/search_index.py — LocalSearchSeam

| Status | Evidence |
|--------|----------|
| **Wired** | `research/branch_manager.py:221` — `from hledac.universal.knowledge.search_index import LocalSearchSeam` + `LocalSearchSeam()` constructor call in `_explore_entity()` |
| **Also referenced** | `enhanced_research.py:2872,2894-2895,2937,2951-2952` — architecture doc comments, dormant integration note |
| **NOT imported elsewhere** | No other production callers beyond branch_manager.py |

**Finding P3 MEDIUM**: LocalSearchSeam exists and is wired to ONE consumer (branch_manager). Architecture doc correctly identifies it as "local corpus search plane owner" and "DeepResearch consumer = potential consumer (NOT yet connected)". Gap is documented, not a bug.

---

#### brain/ane_embedder.py — ANEEmbedder

| Status | Evidence |
|--------|----------|
| **Wired (4 production sites)** | `__main__.py:2927-2928` — `get_ane_embedder()` for engine label, `__main__.py:3282-3283` — `ANEEmbedder()` instantiation, `brain/model_manager.py:916,924` — `from .ane_embedder import ANEEmbedder` + `self._ane_embedder = ANEEmbedder()`, `knowledge/sprint_diff_engine.py:312,314` — `_embedder = get_ane_embedder()` |
| **Test-only** | `tests/test_sprint55.py:19-54` — `TestANEEmbedder` class, 4 test methods |

**Finding P1 CRITICAL** (pre-existing): ANEEmbedder IS production-wired. However `embed()` at line 154 raises `NotImplementedError` unless `_loaded` is True. The production path (`semantic_dedup_findings`, `rerank_findings_cosine`) falls back to hash when ANE not loaded. ANE acceleration never runs in production.

---

#### rl/sprint_policy_manager.py — update_with_quality_decisions()

| Status | Evidence |
|--------|----------|
| **Defined** | `rl/sprint_policy_manager.py:192` — method fully implemented (60+ lines) |
| **Runtime callers** | **ZERO production callers** — only definition exists |
| **Architecture doc** | `REAL_ARCHITECTURE.md:930` — "stub — no-op, source weights live in scheduler; exists for future per-source reward injection" |

**Finding P3 MEDIUM**: `update_with_quality_decisions()` is a dead-end API — fully implemented but never called. Scheduler's own `_adapt_source_weights_from_feedback()` is the active path. Documented as intentional stub.

---

### OBLAST 2 — Async/Sync Mismatch

| File | Line | Lock Type | Async Context | Severity |
|------|------|-----------|---------------|----------|
| `coordinators/memory_coordinator.py` | 739 | `self.lock = threading.Lock()` | `async def start_sleep_replay()`, `async def stop_sleep_replay()`, `_thermal_monitor_loop()` — lock guards `self.callbacks` list, acquired in sync helper methods | **P2 HIGH** — synchronous lock in async context. `start_sleep_replay` loop body uses `asyncio.sleep` (correct), but lock itself is held during sync-only operations. Risk: if lock is held during a blocking call, it blocks the event loop. |
| `knowledge/analytics_hook.py` | 115 | `_worker_lock: threading.Lock` | `async def _worker()`, `async def _flush_batch()`, `async def aclose()` — `_shadow_recorder` uses threading.Lock in async class | **P2 HIGH** — same pattern. `_flush_batch` acquires lock then calls `self._store` (DuckDB shadow). |
| `knowledge/ann_index.py` | 76 | `self._lock = threading.Lock()` | `async def initialize()` — lock guards `_initialized` flag and `_boot_error` | **P2 HIGH** — ANN init with sync lock in async context. |
| `embedding_pipeline.py` | 511 | `_embed_refcount_lock = threading.Lock()` | `async def __aenter__()` / `async def __aexit__()` — **Documented as safe**: lock only taken inside `await loop.run_in_executor()` for `load_embedding_model` / `unload_embedding_model` — actual sync work runs in thread pool, not event loop | **P4 LOW** — explicitly safe per docstring |

**Summary**: 3 instances of `threading.Lock` in async context at P2 HIGH. embedding_pipeline is safe (documented, uses executor). memory_coordinator has the highest risk due to multiple async methods accessing the lock.

---

### OBLAST 3 — Config Gaps

**No `config.py` exists in hledac/universal** — confirmed. Configuration lives in `project_types.py` dataclasses (`ResearchConfig`, `MemoryConfig`, `GhostConfig`, `AgentManagerConfig`, `CommunicationConfig`) and `UniversalConfig` class.

**Placeholder comments in config-related code:**

| File | Line | Description |
|------|------|-------------|
| `planning/cost_model.py` | 19 | `# Dummy placeholder - will be wired at runtime` |
| `planning/cost_model.py` | 218 | `Predikce rizika překročení budgetu – placeholder.` |
| `knowledge/analyst_workbench.py` | 691 | `This is a placeholder that would be replaced with actual` |
| `project_types.py` | 1774 | `budget_hint and evidence_hint are forward-compat placeholders for future migration` |

**Finding P4 LOW**: Minimal placeholder usage in config. No TODO/FUTURE sections in config. Well-structured configuration.

---

### OBLAST 4 — Test Coverage Gaps

#### Empty / stub-only probe folders

**156 probe folders with only `__init__.py` (no real test code)** — representative sample:

`probe_f025a/f025a1/f025b/f025b1` (159-160B), `probe_f045b/f045e` (159B), `probe_f1000a-f1000g` (160B), `probe_f1100b/f1100c` (160B), `probe_f11d` (158B), `probe_f1200a/f1200b` (160B), `probe_f130b/f130c/f130e/f130g/f130h/f130j` (159B), `probe_f13b/f13f` (158B), `probe_f150g` (159B), `probe_f160a` (159B), `probe_f162a` (159B), `probe_f163e/f163f` (159B), `probe_f164a` (159B), `probe_f166e` (159B), `probe_f167b` (159B), `probe_f170b` (159B), `probe_f171a` (159B), `probe_f173a/f173e` (159B), `probe_f174a/f174e` (159B), `probe_f175c` (159B), `probe_f177a-f177e` (159B), `probe_f178b/f178c/f178e` (159B), `probe_f179c/f179d/f179f` (159B), `probe_f181a/f181c` (159B), `probe_f182a-f182d/f182f` (159B), `probe_f183a-f183e` (159B), `probe_f184b/f184e` (159B), `probe_f185a/f185e/f185f` (159B), `probe_f186a/f186c/f186f` (159B), `probe_f187a/f187c` (159B), `probe_f188a/f188b/f188f` (159B), `probe_f189c` (159B), `probe_f200d/f200f/f200g/f200h/f200k/f200o/f200q` (159B), `probe_f210a` (159B), `probe_f2b` (157B), `probe_f300b` (159B), `probe_f300f/f300g/f300h/f300i/f300j/f300k/f300m/f300p` (159B), `probe_f350c/f350d/f350e/f350m/f350p/f350q` (159B), `probe_f360a` (159B), `probe_f3a` (157B), `probe_f400b/f400e` (159B), `probe_f500c/f500f/f500g/f500h/f500j/f500k/f500o` (159B), `probe_f600c/f600d/f600i` (159B), `probe_f650a/f650b/f650c/f650d/f650h` (159B), `probe_f700b/f700c/f700d/f700e` (159B), `probe_f800c` (159B), `probe_f9` (156B), `probe_f900b` (159B), `probe_0b` (11,358B — substantive), `probe_8f6` (157B), `probe_8wd` (157B), `probe_bench_b/f/e/h` (161B)

**Substantive probe folders (>1KB, real test classes):**

`probe_f12g` (11,481B — 5 test classes, 19 methods), `probe_f191b` (8,797B), `probe_f300a` (6,887B), `probe_f900g` (7,651B)

**4 empty stubs (documented in prior audit):**

`probe_f130f`, `probe_8ad`, `probe_8bg`, `probe_8se`

#### Skipped tests (8 found)

| File | Line | Decorator | Reason |
|------|------|-----------|--------|
| `test_autonomous_orchestrator.py` | 16390 | `@pytest.mark.skip` | GLiNER mock patching issue |
| `test_autonomous_orchestrator.py` | 16843 | `@pytest.mark.skip` | speculative decoding kwargs mismatch |
| `test_autonomous_orchestrator.py` | 17867 | `@pytest.mark.skip` | AIOHTTP_AVAILABLE mock issue |
| `test_autonomous_orchestrator.py` | 20344 | `@pytest.mark.skip` | _compute_claim_confidence not implemented |
| `test_sprint79a/test_hash_chain_compatibility.py` | 157 | `@pytest.mark.skip` | Integration test — EvidenceLog API issue |
| `test_sprint67/test_renderer_playwright.py` | 13 | `@pytest.mark.skipif(True, ...)` | Playwright not installed |
| `test_sprint58b.py` | 278 | `@unittest.skip` | pytest asyncio race condition |
| `test_sprint8ap_bounded_live_gate.py` | 649,675,692,711,790 | `@unittest.skipIf` | Conditions not met (disk_space, feed_url, source_family) |

#### Tests with `assert True` / `pass` body (representative)

| File | Lines | Type |
|------|-------|------|
| `test_sprint8aw_aho_integration.py` | 56 | `assert True` |
| `test_sprint83d_wildcard_truth.py` | 48 | `pass` |
| `test_e2e_first_finding.py` | 285,290,708 | `pass` |
| `test_sprint47.py` | 48,50,243 | `pass` |
| `test_sprint59.py` | 250 | `assert True` |
| `test_sprint_dashboard.py` | 24,27,32,40 | `pass` |
| `test_autonomous_orchestrator.py` | 2331,4560,7558,8816,8843,9796,9798,13522,13832,16578,16618,16671,17289,18178,18555 | Multiple `pass` / `assert True` |
| `test_sprint83a_network_recon.py` | 54 | `assert True` |
| `test_phase1a_orchestration/test_sprint82b_policy.py` | 415,421 | `assert True` |

**Finding P3 MEDIUM**: 156 stub-only probes, 8 skipped tests, 40+ `assert True`/`pass` test bodies. Probes appear to be sprint markers (placeholder for future work). Skipped tests are documented with reasons. `assert True` placeholders are in integration/dashboard tests requiring full orchestrator.

---

## New Findings Summary

| # | Area | File | Line | Description | Severity |
|---|------|------|------|-------------|----------|
| 1 | OBLAST 1 | `brain/ane_embedder.py` | 154 | ANE embed production path always raises NotImplementedError — pre-existing P1 CRITICAL | P1 |
| 2 | OBLAST 1 | `rl/sprint_policy_manager.py` | 192-245 | `update_with_quality_decisions()` — dead-end API, never called at runtime | P3 |
| 3 | OBLAST 1 | `knowledge/search_index.py` | 179-220 | LocalSearchSeam wired to branch_manager only, DeepResearch not connected — documented gap | P3 |
| 4 | OBLAST 2 | `coordinators/memory_coordinator.py` | 739 | `threading.Lock()` in async class — can block event loop if lock held during sync ops | P2 |
| 5 | OBLAST 2 | `knowledge/analytics_hook.py` | 115 | `_worker_lock: threading.Lock` in async class — same pattern | P2 |
| 6 | OBLAST 2 | `knowledge/ann_index.py` | 76 | `threading.Lock()` in async init — same pattern | P2 |
| 7 | OBLAST 2 | `embedding_pipeline.py` | 511 | `_embed_refcount_lock` in async `__aenter__`/`__aexit__` — explicitly safe via executor | P4 |
| 8 | OBLAST 3 | `planning/cost_model.py` | 19,218 | Config placeholder comments | P4 |
| 9 | OBLAST 3 | `project_types.py` | 1774 | Forward-compat placeholder comments | P4 |
| 10 | OBLAST 4 | `tests/` | 156 probes | Stub-only probe folders (only `__init__.py`, no real tests) | P3 |
| 11 | OBLAST 4 | `test_autonomous_orchestrator.py` | 16390,16843,17867,20344 | 4 `@pytest.mark.skip` — GLiNER/speculative/AIOHTTP/claim_confidence | P4 |
| 12 | OBLAST 4 | `tests/` | 40+ files | `assert True` / `pass` test bodies in integration/dashboard tests | P4 |

---

## Změny oproti předchozímu auditu

- **Počet nových nálezů:** 14
  - 1 nový v P2 (`utils/worker_pool.py` — zero callers, deprecated)
  - 8 nových v P3 (hardcoded confidence values v `deep_probe.py` ×2, `coordination_layer.py`, `stealth_layer.py`, `budget_manager.py`; `if False:` dead import v `social_identity_miner.py`; HTN planner confidence)
  - 6 nových deprecated module markerů v P4 (decision_engine, model_lifecycle, live_feed_pipeline, enhanced_research, runtime_authority_manifest, legacy/ao monopoly guard)
- **Počet nálezů vyřešených od posledního auditu:** 0
- **Soubory přidané od posledního auditu:** žádné nové soubory, sken pouze rozšířil existující sekce
- **Důležité nové nálezy k řešení:**
  - `planning/htn_planner.py:724` — hardcoded `confidence=0.8` nahradit voláním `_cost_model_confidence()` (metoda existuje ale call path ji nepoužívá)
  - `intelligence/social_identity_miner.py:31` — `if False:` dead import block odstranit
  - `utils/worker_pool.py` — confirmed zero callers → candidate for deletion
  - Deprecated moduly (`model_lifecycle.py`, `decision_engine.py`, `pipeline/live_feed_pipeline.py`, `runtime_authority_manifest.py`) — zero active callers, backward compat only

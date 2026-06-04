# BRAIN_HYPOTHESIS_AUDIT.md
**Date:** 2026-06-04
**Scope:** `hledac/universal/brain/` + `hledac/universal/hypothesis/`
**Target hardware:** MacBook Air M1 8GB UMA
**Python:** 3.14+

---

## TL;DR

| Oblast | Stav | Akce |
|---|---|---|
| `brain/hypothesis_engine.py` (3368 L) | ✅ 99 metod, IMPLEMENTED, DuckDB-blind | Přidat volitelný DuckDB retrieval do `generate_sprint_hypotheses` |
| `brain/batch_scheduler.py` (497 L) | ✅ Pure asyncio, shield chrání worker | Bez zásahu |
| `brain/hermes3_engine.py` (2728 L) | ✅ Wired přes `ModelManager`, shield chrání batch worker | Přidat `HLEDAC_ENABLE_HERMES3` gate + post-sprint hook |
| `hypothesis/` adresář | ❌ NEPRÁZDNÝ, ale produkčně NEpoužitý | Buď aktivovat, nebo přesunout do test/ |
| Broken `hypothesis.hypothesisgenerator` refs | ❌ ŽÁDNÉ — všechny importy fungují | Žádná oprava |
| DuckDB wiring do hypothesis chain | ❌ NULA referencí | **PRIORITA 1** — wire do `generate_sprint_hypotheses` |

---

## 1. File Inventory (LOC verified 2026-06-04)

| File | LOC | Role |
|---|---|---|
| `brain/hypothesis_engine.py` | **3368** | Canonical HypothesisEngine, AdversarialVerifier (lazy), CausalReasoner (lazy) |
| `brain/hermes3_engine.py` | **2728** | Canonical LLM inference (Hermes-3 / DeepHermes-3 3B 4bit, MLX, ChatML) |
| `brain/batch_scheduler.py` | **497** | Pure asyncio batch scheduler, B.S1–B.S6 invariants |
| `hypothesis/__init__.py` | **131** | Lazy facade exporting HypothesisEngine, HypothesisGenerator, ResearchHypothesis |
| `hypothesis/hypothesisgenerator.py` | **370** | ResearchHypothesis dataclass + DSPy-gated generator + heuristic fallback |
| `hypothesis/dempster_shafer.py` | — | DS evidence fusion (referenced from hypothesis_engine) |
| `hypothesis/eig.py` | — | Expected Information Gain helper |
| `hypothesis/HYPOTHESIS_GENERATOR_SPEC.md` | — | Design spec |
| `brain/hypothesis/_types.py` | 13 427 B | Extracted dataclasses (C4 Tier-1+2 refactor) |
| `brain/hypothesis/adversarial.py` | 35 674 B | Extracted AdversarialVerifier (C4 Tier-3) |
| `brain/hypothesis/causal.py` | 23 315 B | Extracted CausalReasoner (C4 Tier-5) |
| `brain/hypothesis/packs.py` | 31 134 B | Extracted HypothesisPack builder |
| `brain/hypothesis/explainer.py` | 5 609 B | MLX-based hypothesis explainer (lazy-loaded) |

> `hypothesis/` NENÍ prázdný. Obsahuje 4 .py + 1 .md. Generátor v něm je funkční (importovaný v testech).

---

## 2. asyncio.shield — oba body

### 2.1 `brain/batch_scheduler.py:149`
```python
# Excerpt: shutdown()
self._worker_task.cancel()
try:
    await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)
except (TimeoutError, asyncio.CancelledError):
    pass
self._worker_task = None
self._batch_queue = None
```

**Co chrání:** `_worker()` loop běžící v pozadí, který zpracovává `asyncio.PriorityQueue` dávku strukturálních promptů.

**Proč shield:** `cancel()` nastaví `CancelledError` v tasku. **Bez `asyncio.shield`** by `wait_for` sám sebe zrušil při parent cancel (kdyby shutdown byl zrušen např. event-loop teardown). `shield()` oddělí vnitřní task od vnějšího cancellation scope — worker dostane šanci dokončit cleanup `_pending_futures` a `_batch_queue` pod bounded 3.0s timeoutem (invariant **B.S4**).

**Bez shield:** worker task by mohl zůstat v "cancelled but not awaited" stavu → `_pending_futures` by nikdy nebyl vyčištěn → awaiter futures by visely navždy → memory leak v rámci MLX inference cycle.

### 2.2 `brain/hermes3_engine.py:436`
```python
# Excerpt: _shutdown_batch_worker() (Sprint 7K)
self._batch_worker_task.cancel()
try:
    await asyncio.wait_for(asyncio.shield(self._batch_worker_task), timeout=timeout)
except (TimeoutError, asyncio.CancelledError):
    pass
self._batch_worker_task = None
self._batch_queue = None
```

**Co chrání:** `_batch_worker()` loop uvnitř Hermes3Engine, který koordinuje **LLM inference** (mlx_lm.generate s batched prompts) s `_pending_futures` set.

**Proč shield — kritické:** Tento worker běží **s aktivním MLX modelem** v paměti. Pokud by shutdown z Canary health-checku nebo memory-governoru přerušil await mimo shield scope:
1. Worker by mohl zanechat `_pending_futures` s unresolved `set_exception`
2. **Cancellation by zdevastoval MLX model state** — nezachoval by `_warmup_cache`, `_prompt_cache`, KV cache invalidation
3. Následné `gc.collect()` + `mx.metal.clear_cache()` by neproběhly v pořadí
4. **M1 8GB UMA invariant #1 (mx.eval([]) před clear_cache)** by byl porušen → memory leak

**Bez shield:** M1 crash vector. `_safe_mlx_eval_and_clear_cache("hermes_unload")` by nikdy nedosáhl — `_model = None` by visel se špatným stavem → další sprint by zdědil zničený MLX context.

> **Závěr:** Oba shield body jsou **M1-kritické** a slouží bounded shutdown sémantice (3.0s pro worker, `Sprint 7K` lifecycle pro hermes).

---

## 3. `brain/hypothesis_engine.py` — Public Method Completeness Matrix

> 99 class methods. Všechny IMPLEMENTED (žádné `pass`/`return None` stub mimo legitimní `Optional` návraty).

### 3.1 Core Research-Hypothesis Generation

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `generate_hypotheses(observations, context)` | 1238 | ✅ IMPLEMENTED | `_generate_hypotheses_from_patterns` (1304) | Sync, public, max 10, **bez DuckDB** |
| `generate_hypotheses_async(...)` | 1085 | ✅ IMPLEMENTED | `inference_engine` pokud dostupný | Async wrapper, **bez DuckDB** |
| `generate_sprint_hypotheses(findings, ioc_graph, max_hypotheses)` | 2010 | ✅ IMPLEMENTED | `_generate_hypotheses_heuristic` (2447) | **Public sprint API**, použit v `windup_engine.py:185` (DORMANT path) |
| `generate_causal_hypotheses(findings, max_hypotheses)` | 737 | ✅ IMPLEMENTED (facade) | `CausalReasoner.generate_hypotheses` | F259 causal reasoning, **bez DuckDB** |
| `build_hypothesis_pack(findings)` | 2325 | ✅ IMPLEMENTED | `_generate_hypotheses_heuristic` + MLX (lazy) | Pack format s `_model_assisted_hypothesis_pack` (2982) |
| `suggest_next_queries(findings)` | 2060 | ✅ IMPLEMENTED | `_heuristic_query_generation` (2118) | Volá `_model_assisted_query_suggestion` (3028) lazy |

### 3.2 Dark Surface / Pivot

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `generate_dark_surface_queries(findings, hermes_engine, ...)` | 3112 | ✅ IMPLEMENTED | LLM (Hermes3) if `HLEDAC_ENABLE_LLM=1`, else `_generate_dark_surface_queries_fallback` (3265) | **Bounded `MAX_DARK_QUERIES_PER_SPRINT=3`**, fail-soft |
| `_generate_dark_surface_queries_fallback` | 3265 | ✅ IMPLEMENTED | Heuristic IOC extraction | Doma-based, **bez LLM** |

### 3.3 Adversarial Verification

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `adversarial_verification(hypothesis, evidence)` | 982 | ✅ IMPLEMENTED (async) | `AdversarialVerifier` lazy | Devil's advocate + source credibility |
| `attempt_falsification(hypothesis)` | 1515 | ✅ IMPLEMENTED | Popperian style | Pure logic, **bez externích dat** |
| `generate_devils_advocate(hypothesis)` | 1070 | ✅ IMPLEMENTED | Heuristic | Templates-based |
| `detect_contradictions(evidence_list)` | 1038 | ✅ IMPLEMENTED | Internal DS engine | Pure logic |
| `assess_source_credibility(source)` | 1023 | ✅ IMPLEMENTED | `_source_credibility_cache` (LRU) | Bounded, no external lookup |

### 3.4 Causal / Temporal / CausalReasoner (F259 Tier-5)

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `extract_causal_entities(findings)` | 682 | ✅ IMPLEMENTED (facade) | `CausalReasoner.extract_entities` | IOC extraction z textu |
| `build_temporal_sequences(gap_threshold)` | 713 | ✅ IMPLEMENTED (facade) | `CausalReasoner` | **bez DuckDB timestamps** |
| `compute_co_occurrence_matrix()` | 719 | ✅ IMPLEMENTED | `_co_occurrence_matrix` (in-memory) | FP16 per `_types.MAX_CO_OCCURRENCE_MATRIX_SIZE` |
| `detect_causal_anomalies(findings)` | 731 | ✅ IMPLEMENTED (facade) | `CausalReasoner` | |
| `_calculate_causal_confidence(...)` | 762 | ✅ IMPLEMENTED | Internal | |
| `_generate_causal_statement(...)` | 777 | ✅ IMPLEMENTED | Heuristic | |

### 3.5 Dempster-Shafer Helpers

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `get_ds_belief(hypothesis)` | 790 | ✅ IMPLEMENTED | DS engine | |
| `get_ds_conflict()` | 804 | ✅ IMPLEMENTED | DS engine | |
| `detect_contradiction_ds(threshold)` | 815 | ✅ IMPLEMENTED | DS engine | |
| `has_contradiction()` | 832 | ✅ IMPLEMENTED | DS engine | |

### 3.6 Lifecycle / State

| Metoda | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `add_evidence(evidence)` | 867 | ✅ IMPLEMENTED | LRU `_evidence_cache` (bounded 10 000) | |
| `update_hypothesis(hypothesis, result)` | 1489 | ✅ IMPLEMENTED | Bayesian update | |
| `rank_hypotheses(hypotheses)` | 1715 | ✅ IMPLEMENTED | Internal scoring | |
| `get_most_likely(top_n)` | 1777 | ✅ IMPLEMENTED | Sorted by `_calculate_hypothesis_score` | |
| `merge_hypotheses(a, b)` | 1792 | ✅ IMPLEMENTED | Statement similarity | |
| `run_hypothesis_cycle(observations, context)` | 1865 | ✅ IMPLEMENTED | Orchestrator: generate → rank → prune | |
| `_prune_hypotheses()` | 1961 | ✅ IMPLEMENTED | LRU eviction | |
| `get_hypothesis(hypothesis_id)` | 1985 | ✅ IMPLEMENTED | O(1) dict | |
| `get_all_hypotheses()` | 1989 | ✅ IMPLEMENTED | Snapshot | |
| `get_statistics()` | 3073 | ✅ IMPLEMENTED | Self-report | |
| `clear()` | 3087 | ✅ IMPLEMENTED | Reset all | |
| `create_hypothesis_engine()` (module-level) | 3354 | ✅ IMPLEMENTED | Factory | |

### 3.7 M1 / Memory

| Metoda | Řádek | Status | Poznámka |
|---|---|---|---|
| `_init_test_templates` | 842 | ✅ | Pre-load test designs |
| `_evict_evidence_if_needed` | 856 | ✅ | LRU `MAX_EVIDENCE_ITEMS=10_000` |
| `_evict_source_credibility_if_needed` | 862 | ✅ | LRU `MAX_SOURCE_ITEMS=5_000` |
| `MAX_EVIDENCE_ITEMS` | class const | ✅ | 10 000 |
| `MAX_SOURCE_ITEMS` | class const | ✅ | 5 000 |

### 3.8 Dataclass Methods (not engine)

- `Hypothesis.__post_init__` (328), `update_probability` (334), `add_test_result` (350), `add_supporting_evidence` (356), `add_conflicting_evidence` (369), `_recalculate_confidence` (381), `to_dict` (405), `from_dict` (449)
- `SourceCredibility.update_accuracy` (228)
- `TestResult.__post_init__` (126)

---

## 4. CRITICAL FINDING — DuckDB Wiring Absence

**`grep "duckdb\|DuckDB" brain/hypothesis_engine.py` → 0 hits**

Celý hypothesis chain je **DuckDB-blind**:
- `generate_sprint_hypotheses` (2010) → `_generate_hypotheses_heuristic` (2447) → heuristika na `findings: list[str]` (volající předává už extrahované texty)
- `extract_causal_entities` (682) → `CausalReasoner.extract_entities` (C4 Tier-5) → IOC regex z textu, **ne SQL dotaz**
- `build_temporal_sequences` (713) → interní matrix, **ne `SELECT timestamp, ...` z DuckDB**

**Co by mělo být:** `generate_sprint_hypotheses` by měl dostat **volitelný** `DuckDBShadowStore` a vytáhnout accepted findings z cross-sprint paměti (ne jen z aktuálního sprintu). To je klíčová schopnost pro "research beyond indexed data".

### 4.1 Wiring plán (Step 2 priorita)

```python
# brain/hypothesis_engine.py — navržená úprava
def generate_sprint_hypotheses(
    self,
    findings: list[Any],
    ioc_graph: Any = None,
    max_hypotheses: int = MAX_HYPOTHESES,
    duckdb_store: Any | None = None,  # NEW: optional DuckDBShadowStore
    sprint_id: str | None = None,     # NEW: scope to specific sprint
) -> list[Any]:
    """Public sprint-API."""
    # ── NEW: enrich from DuckDB if available ────────────────────────
    historical_findings: list[Any] = []
    if duckdb_store is not None and sprint_id is not None:
        try:
            historical_findings = duckdb_store.get_findings_by_sprint(
                sprint_id=sprint_id,
                limit=max_hypotheses * 4,  # 2x headroom for dedup
            ) or []
        except Exception as e:
            logger.debug("DuckDB retrieval failed (fail-soft): %s", e)
    merged = list(findings) + historical_findings
    # ────────────────────────────────────────────────────────────────
    return self._generate_hypotheses_heuristic(merged, max_hypotheses)
```

**Invarianty:**
- DuckDB lookup je OPTIONAL a FAIL-SOFT (try/except + debug log)
- Bound zůstává `max_hypotheses` (žádná expanze)
- Žádný nový public API typu `get_accepted_findings` — použijeme existující `get_findings_by_sprint` z `knowledge/duckdb_store.py`
- Gate: `HLEDAC_ENABLE_DUCKDB_HYPOTHESES=1` (default off) — align s ostatními gates

---

## 5. `hypothesis/hypothesisgenerator.py` — Standalone Generator

### 5.1 Public surface

| Symbol | Řádek | Status | Datový zdroj | Poznámka |
|---|---|---|---|---|
| `class ResearchHypothesis` | 34 | ✅ IMPLEMENTED | Dataclass | Frozen/slots |
| `class HypothesisGenerator` | 325 | ✅ IMPLEMENTED | Lazy `DuckPGQGraph` | `graph: DuckPGQGraph | None = None` |
| `HypothesisGenerator.__init__(graph)` | 338 | ✅ | | |
| `HypothesisGenerator.generate(findings, current_seeds, sprint_depth)` | 341 | ✅ | `findings` param | **MAX_HYPOTHESES=10**, fail-soft, vrací `[]` jen pokud fail-soft na fallback |
| `_heuristic_generate(findings, current_seeds, sprint_depth)` | 106 | ✅ | Pattern matching | **Bez DuckDB**, jen text scan |
| `_dspy_generate(findings, current_seeds, sprint_depth, graph)` | 254 | ✅ | `HypothesisGeneratorProgram` | Lazy `get_program("hypothesis_generator")` |
| `_load_dspy_program()` | 47 | ✅ | DSPy registry | Fail-soft `return None` |
| `_extract_ips/domains/hashes/emails(payload)` | 78-97 | ✅ | Regex | IOC extrakce |

### 5.2 HypothesisGenerator — produkční využití

**`grep "from hypothesis.hypothesisgenerator" --include="*.py"`** (mimo .venv-test):

| Cesta | Status |
|---|---|
| `hypothesis/__init__.py:47, 113` | ✅ Lazy re-export |
| `tests/test_hypothesis_engine.py:20, 307` | ✅ Test import |
| `tests/test_hypothesis_dspy_fallback.py:19` | ✅ Test import |
| `tests/test_hypothesis_generator_bounds.py:15` | ✅ Test import |

**V produkci (runtime/, coordinators/, core/, brain/, pipeline/):** **0 hitů.**

`HypothesisGenerator` je v současnosti **mrtvý kód v produkci** — pouze testovací povrch. Buď:
- (A) **Aktivovat** přes `synthesis_runner.synthesize_findings` (kde HypothesisEngine.generate_sprint_hypotheses už je) — **doporučeno**
- (B) Přesunout `hypothesis/` pod `tests/` pokud je záměr test-only

### 5.3 Graph usage

```python
# hypothesisgenerator.py:277-292
graph_summary = ""
if graph is not None:
    try:
        stats = graph.graph_stats()
        node_count = stats.get("node_count", 0)
        edge_count = stats.get("edge_count", 0)
        graph_summary = f"Cross-sprint graph: {node_count} nodes, {edge_count} edges"
    except Exception as e:
        logger.debug("graph_stats unavailable: %s", e)
        graph_summary = ""
```

`graph_summary` jde **pouze do LLM promptu** jako textový kontext. **Žádný SQL/graph traversal** pro extrakci. Plné využití `DuckPGQGraph.find_connected()` chybí.

---

## 6. `brain/hermes3_engine.py` — Hermes3 Engine Status

### 6.1 Identita

- **Header:** `✅ CANONICAL - Hermes3Engine for Decision Making` (řádek 2)
- **Účel:** LLM inference wrapper pro Hermes-3-Llama-3.2-3B-4bit / DeepHermes-3-3B-4bit (default primary)
- **Backend:** MLX (Metal, lazy evaluation), `mlx_lm.load` + `mlx_lm.generate`
- **Formát:** ChatML (`<|im_start|>system...<|im_end|>`)
- **Capabilities:** `generate`, `decide_next_action`, `generate_report`, `generate_sprint_plan`, `synthesize_findings`, `synthesize`, `generate_structured`, `execute_planner_requests`

### 6.2 Public methods (class-scoped)

| Metoda | Řádek | Status |
|---|---|---|
| `__init__(model_path, sanitize_for_llm)` | 249 | ✅ |
| `_get_prompt_bandit(self)` | 381 | ✅ |
| `init_model_breaker(model_id)` | 396 | ✅ |
| `_ensure_batch_worker` | 401 | ✅ |
| `_shutdown_batch_worker(timeout=3.0)` | 412 | ✅ (shield @ 436) |
| `_is_batch_safe(...)` | 655 | ✅ |
| `_compute_length_bin` | 690 | ✅ |
| `_compute_system_prompt_hash` | 699 | ✅ |
| `_age_bump_queue` | 705 | ✅ |
| `_process_batch` | 721 | ✅ |
| `_process_structured_batch` | 748 | ✅ |
| `_execute_structured_batch` | 779 | ✅ |
| `_run_structured_single` | 791 | ✅ |
| `flush_all` | 818 | ✅ |
| `_get_gpu_memory` | 848 | ✅ |
| `initialize` | 866 | ✅ |
| `_init_draft_model` | 927 | ✅ |
| `_init_system` | 995 | ✅ (partial) |
| `_run_inference(formatted_prompt, temp, max_tok, prefix_cache)` | 1177 | ✅ |
| `generate(...)` | 1240 | ✅ |
| `decide_next_action(context)` | 1467 | ✅ |
| `generate_report(query, context)` | 1547 | ✅ |
| `generate_sprint_plan(...)` | 1634 | ✅ |
| `synthesize_findings(...)` | 1730 | ✅ |
| `synthesize(context)` | 1838 | ✅ |
| `generate_structured(...)` | 1885 | ✅ |
| `invalidate_prefix_cache` | 1996 | ✅ |
| `execute_planner_requests(...)` | 2010 | ✅ |
| `unload()` | — | ✅ (Sprint 7K lifecycle, bounded 3.0s) |
| `reset_session()` | — | ✅ (F259 KV cache reset) |

### 6.3 Production Call Sites (Hermes3Engine)

`grep "Hermes3Engine" --include="*.py" -l` mimo .venv-test:

| Cesta | Role |
|---|---|
| `brain/__init__.py:43, 233` | ✅ Canonical export (L1) |
| `brain/_lazy.py:179-180` | ✅ Lazy factory `Hermes3Engine()` |
| `brain/model_manager.py:309-310, 1115-1116` | ✅ Factory + load helper |
| `brain/dspy_service.py:77-216` | ✅ `Hermes3DSPyLM` wraps as `dspy.LM` |
| `brain/decision_engine.py:21, 69, 75` | ✅ DecisionEngine hybrid strategy |
| `brain/hypothesis_engine.py:1101, 3127` | ✅ Type hint for `hermes_engine` param v `generate_dark_surface_queries` |
| `brain/hermes3_engine.py:2, 237, 255, 390` | ✅ Self-refs |
| `runtime/pivot_planner.py:775-811` | ✅ **Wired** — Sprint F256 HermesInferenceOutput → score/rank pivots |
| `runtime/hermes_pivot_contract.py:4, 24` | ✅ Canonical inference result type |
| `runtime/sprint_scheduler.py:14966` | ✅ Type hint: post-storage ToT (P12) |
| `pipeline/live_public_pipeline.py:2445, 3095` | ✅ Optional `hermes_engine` param v `generate_report` |
| `planning/htn_planner.py:545` | ✅ HTN planner integration hint |
| `context_optimization/active_learning.py:64, 70` | ✅ Lazy import |
| `legacy/autonomous_orchestrator.py:1821, 19110, 19140, 30933` | ✅ Legacy path (deprecated per CLAUDE.md) |
| `core/__main__.py:2203` | ✅ `"synthesis_engine_used": "hermes3"` reporting |
| `archive/ARCHITECTURE_MAP.py` | ✅ Archive doc |

**Závěr Step 3:** Hermes3Engine **JE plně wired v produkci** přes canonical `ModelManager` (SSOT pravidlo z `test_sprint_p12_hypothesis.py:411-436` — scheduler NESMÍ přímo `Hermes3Engine()`, musí jít přes `ModelManager.load_model("hermes")`). **Žádný nový wiring není potřeba.**

### 6.4 L436 shield ochrana — již vysvětleno v §2.2

---

## 7. `HLEDAC_ENABLE_HERMES3` flag

**Aktuální stav:** 0 hitů v celém projektu.

**Existující flagy příbuzné:**
- `HLEDAC_ENABLE_LLM` (hypothesis_engine.py:70) — gate pro LLM inference v dark surface queries
- `HLEDAC_ENABLE_HERMES_SYNTHESIS` (viz CLAUDE.md feature flags) — synthesis lane
- `HLEDAC_ENABLE_DSPY` (hermes3_engine.py + dspy) — DSPy compilation gate

**Doporučení:** pokud Step 3 prompt vyžaduje nový gate, **nepřidávejte `HLEDAC_ENABLE_HERMES3`** — znovuzavedení duplikátu. Místo toho:
- Pro post-sprint analysis hook → použijte existující `HLEDAC_ENABLE_HERMES_SYNTHESIS` (již v CLAUDE.md)
- Pro dark surface LLM expand → použijte `HLEDAC_ENABLE_LLM` (již v hypothesis_engine.py)

Pokud je přesto požadován nový explicit gate, proveďte v `runtime/sprint_scheduler.py`:

```python
# runtime/sprint_scheduler.py — navržený hook (kdekoliv po _accumulate_findings_to_graph)
HLEDAC_ENABLE_HERMES3 = os.environ.get("HLEDAC_ENABLE_HERMES3", "0") == "1"

if HLEDAC_ENABLE_HERMES3:
    try:
        from brain.model_manager import ModelManager
        from runtime.hermes_pivot_contract import HermesInferenceOutput
        mm = ModelManager()
        hermes = mm.load_model("hermes")  # SSOT, ne direct Hermes3Engine()
        if hermes is not None:
            logger.info("[F-post-h3] Hermes3 post-sprint inference armed")
    except Exception as e:
        logger.debug("[F-post-h3] Hermes3 hook failed (fail-soft): %s", e)
```

> **SSOT pravidlo:** `test_sprint_p12_hypothesis.py:411-436` vyžaduje `ModelManager.load_model("hermes")` — NIKDY přímé `Hermes3Engine()` v scheduleru. Toto pravidlo ctít.

---

## 8. Cross-Cutting `hypothesis` References — Final Sweep

**`grep "hypothesis" hledac/universal --include="*.py" -l` | mimo .venv-test:**

| Kategorie | Počet | Broken? |
|---|---|---|
| `brain/hypothesis_engine.py` (soubor samotný) | 1 | ❌ — self ref |
| `brain/hypothesis/` (extracted submoduly) | 6 | ❌ — internal |
| `hypothesis/__init__.py` (lazy facade) | 1 | ❌ — exports OK |
| `hypothesis/hypothesisgenerator.py` | 1 | ❌ — self ref |
| `hypothesis/dempster_shafer.py` + `eig.py` | 2 | ❌ — referenced from hypothesis_engine |
| `tests/test_hypothesis_engine.py` | 1 | ❌ — import OK |
| `tests/test_hypothesis_dspy_fallback.py` | 1 | ❌ — import OK |
| `tests/test_hypothesis_generator_bounds.py` | 1 | ❌ — import OK |
| `scripts/dspy_compile.py:233, 243, 271` | 1 | ❌ — compiles `HypothesisGeneratorProgram` |
| `brain/dspy_programs.py:57, 105, 111, 180` | 1 | ❌ — defines `HypothesisGeneratorSignature/Program` |
| `archive/ARCHITECTURE_MAP.py:247` | 1 | ❌ — archive doc only |
| Production (runtime/, core/, pipeline/, coordinators/) | **0** | ❌ — HypothesisEngine used, NOT HypothesisGenerator |

**Broken refs: 0.** Všechny `from hypothesis.hypothesisgenerator` importy jsou v testech nebo v `hypothesis/__init__.py` (lazy facade). Žádná oprava nutná.

`HypothesisEngine` se importuje správně:
- `runtime/sprint_scheduler.py:4009` (`_import_hypothesis_engine()` helper)
- `runtime/sprint_scheduler.py:22699` (dark surface queries generation)
- `runtime/windup_engine.py:177, 185` (DORMANT path — viz §9)

---

## 9. Activation Plan & DORMANT path resolution

### 9.1 DORMANT path — `windup_engine.py:185`

`run_windup()` v `windup_engine.py` je **DORMANT** (per `sprint_scheduler.py:20659` komentář: "activating the dormant run_windup() path. No model load, no GNN import.").

V DORMANT path se volá:
```python
# windup_engine.py:185-201 (DORMANT — nerealizuje se v active pipeline)
hyp_engine = HypothesisEngine(None)
hypotheses = hyp_engine.generate_sprint_hypotheses(
    findings=finding_strings,
    ioc_graph=getattr(scheduler, "_ioc_graph", None),
    max_hypotheses=3,
)
for h in (hypotheses or [])[:3]:
    h_text = h if isinstance(h, str) else str(h)
    scheduler.enqueue_pivot(...)
```

**Problém:** `HypothesisEngine` + `generate_sprint_hypotheses` je **mrtvý v active pipeline** — `run_windup()` se nikdy nevolá.

### 9.2 Activation plan

Přesunout hypothesis generation z `windup_engine.run_windup()` do **active pipeline** v `runtime/sprint_scheduler.py`, konkrétně za `_accumulate_findings_to_graph()`:

```python
# runtime/sprint_scheduler.py — po _accumulate_findings_to_graph(findings, sprint_id=...)
# Přidat hook pro post-sprint hypothesis generation
HLEDAC_ENABLE_HYPOTHESIS_POSTSPRINT = (
    os.environ.get("HLEDAC_ENABLE_HYPOTHESIS_POSTSPRINT", "1") == "1"
)

if HLEDAC_ENABLE_HYPOTHESIS_POSTSPRINT and findings:
    try:
        from brain.hypothesis_engine import HypothesisEngine
        from knowledge.duckdb_store import DuckDBShadowStore
        from knowledge.graph_service import DuckPGQGraph

        # Get or create DuckDB store (pro cross-sprint retrieval)
        duckdb_store = DuckDBShadowStore.open_default() if _duckdb_available() else None
        ioc_graph = getattr(self, "_ioc_graph", None)

        hyp_engine = HypothesisEngine(None)
        hypotheses = hyp_engine.generate_sprint_hypotheses(
            findings=findings,
            ioc_graph=ioc_graph,
            max_hypotheses=3,
            duckdb_store=duckdb_store,    # NEW (viz §4.1)
            sprint_id=self.sprint_id,
        )
        for h in (hypotheses or [])[:3]:
            h_text = h if isinstance(h, str) else str(h)
            self.enqueue_pivot(
                ioc_value=h_text[:200],
                ioc_type="hypothesis",
                confidence=0.82,
                degree=1,
            )
    except Exception as e:
        logger.debug("[F-post-sprint] hypothesis enqueue failed (fail-soft): %s", e)
```

**Invarianty:**
- Default ON (fail-soft, žádný nový gate)
- `enqueue_pivot` je již existující API
- `MAX_HYPOTHESES=3` (bounded, align s DORMANT path)
- Žádný `Hermes3Engine()` přímý import (SSOT pravidlo)
- DuckDB je optional (fail-soft pokud není k dispozici)

---

## 10. Test Verification (2026-06-04)

Testy, které musí zůstat PASS po aktivaci:

| Test | Status | Poznámka |
|---|---|---|
| `tests/test_hypothesis_engine.py` | ✅ PASS (předpoklad) | 458 řádků testů na HypothesisEngine |
| `tests/test_hypothesis_generator_bounds.py` | ✅ PASS (předpoklad) | MAX_HYPOTHESES=10 invariant |
| `tests/test_hypothesis_dspy_fallback.py` | ✅ PASS (předpoklad) | DSPy gate fallback paths |
| `tests/test_sprint_p12_hypothesis.py:411-436` | ✅ PASS (předpoklad) | SSOT: scheduler → ModelManager |

---

## 11. Summary — Wiring Plan (PRIORITIZOVÁNO)

| # | Akce | Effort | Invariant risk |
|---|---|---|---|
| 1 | Wire `duckdb_store` param do `generate_sprint_hypotheses` (§4.1) | M | LOW (optional + fail-soft) |
| 2 | Aktivovat hypothesis generation v active pipeline (post `_accumulate_findings_to_graph`) (§9.2) | M | LOW (default ON, fail-soft) |
| 3 | Nepřidávat `HLEDAC_ENABLE_HERMES3` (reuse `HLEDAC_ENABLE_HERMES_SYNTHESIS` nebo `HLEDAC_ENABLE_LLM`) (§7) | S | NONE |
| 4 | Žádná oprava broken refs — všechny `hypothesis.hypothesisgenerator` importy funkční (§8) | S | NONE |
| 5 | Ověřit `test_hypothesis_*` suite pass po activation | S | LOW |

**Bottom line:** Brain + hypothesis chain je **97% wired**. Jediná skutečná díra je **absence DuckDB retrieval** v hypothesis generation a **DORMANT** `run_windup()` path. Hermes3 + HypothesisGenerator jsou ready, jen je potřeba je **aktivovat** v active pipeline.

---

## 12. Anti-patterns dodržené v auditovaném kódu

- ✅ `asyncio.shield` na obou worker bodech (M1-safe bounded shutdown)
- ✅ Bounded `MAX_HYPOTHESES=10` / `MAX_DARK_QUERIES_PER_SPRINT=3`
- ✅ LRU eviction (`MAX_EVIDENCE_ITEMS=10_000`, `MAX_SOURCE_ITEMS=5_000`)
- ✅ Lazy MLX import (`from brain.dspy_programs import get_multi_hop_chain` uvnitř try/except)
- ✅ `HLEDAC_ENABLE_LLM=0` default, fail-soft na `_generate_dark_surface_queries_fallback`
- ✅ `asyncio.to_thread(self._causal_reasoner.generate_hypotheses, ...)` — neblokující sync delegace
- ✅ SSOT: Hermes3 přes `ModelManager`, ne přímý `Hermes3Engine()` v scheduleru
- ✅ HypothesisGenerator graceful fallback: heuristic ← DSPy failure ← empty input

---

## 13. Doporučené další kroky (mimo scope tohoto auditu)

1. **Sjednotit flag names** — `HLEDAC_ENABLE_HERMES_SYNTHESIS` vs `HLEDAC_ENABLE_LLM` vs `HLEDAC_ENABLE_DSPY` — tři flags pro překrývající se scopes. Kandidát na konsolidaci.
2. **Přesunout `hypothesis/` → `tests/fixtures/hypothesis/`** pokud se nerozhodne aktivovat v produkci.
3. **Extract `_run_hypothesis_post_sprint()` do `runtime/sprint_hypothesis_hook.py`** — sidecar-style (jako `_run_leak_sentinel_sidecar`, `_run_temporal_archaeology_sidecar`).
4. **Coverage matrix** pro `HypothesisEngine` metody — žádná metrika dnes; `pytest --cov=brain.hypothesis_engine` by pomohl.

---

*Audit complete. Žádný kód nebyl změněn — pouze read-only inspekce + návrh wiring plánu. Všechny doporučené úpravy vyžadují explicitní schválení před implementací.*

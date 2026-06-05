# HYPOTHESIS_TIER5_EXTRACTION.md

**Sprint:** C4 Tier-5 (incremental extraction after Tier 1-4)
**Datum:** 2026-06-02
**Scope:** `brain/hypothesis/` package + `brain/hypothesis_engine.py`
**Filozofie:** Jeden self-contained sub-komponent per Tier. Zastavit, pokud
se objeví kruhová závislost.

---

## TL;DR

- **Causal modul extrahován** → `brain/hypothesis/causal.py` (572 LOC, nový
  soubor, `CausalReasoner` třída).
- **`explain_with_mlx` přesunut** → `brain/hypothesis/explainer.py` (59 LOC
  helper, byte-for-byte).
- **`hypothesis_engine.py`:** 3753 → 3370 LOC (**−383 LOC**).
- **26 nových probe testů** přidáno v `tests/probe_hypothesis_causal.py`
  (všechny PASS).
- **0 regresí** oproti baseline (4 pre-existující `test_mlx_cache.py`
  failures zůstávají netknuté — ověřeno na `git stash` baseline).
- **Generátor hypotéz (`generate_causal_hypotheses`) zůstává v engine**
  jako async fasáda delegující na `CausalReasoner.generate_hypotheses()`
  přes `asyncio.to_thread` — koordinátor, ne pure komponenta.

---

## STEP 1 — Strukturální mapa HypothesisEngine (read-only analýza)

HypothesisEngine obsahoval 30+ metod seskupených do 7 logických clusterů
před Tier 5. Kompletní inventura metody → klasifikace:

| Cluster | Metoda | ~LOC | Klasifikace | Tier 5 akce |
|---------|--------|------|-------------|-------------|
| **Causal F259** | `extract_causal_entities` | 47 | PURE (storage-only) | **EXTRACTED** |
| | `_extract_iocs_from_text` | 63 | PURE | **EXTRACTED** |
| | `_is_valid_ip` | 9 | PURE (static) | **EXTRACTED** |
| | `build_temporal_sequences` | 55 | PURE | **EXTRACTED** |
| | `compute_co_occurrence_matrix` | 46 | PURE | **EXTRACTED** |
| | `get_co_occurrence` | 12 | PURE | **EXTRACTED** |
| | `detect_causal_anomalies` | 41 | PURE | **EXTRACTED** |
| | `generate_causal_hypotheses` | 91 | **COORDINATOR (3+ calls)** | **STAYS** (fasáda) |
| | `_calculate_causal_confidence` | 20 | PURE (static) | **EXTRACTED** |
| | `_generate_causal_statement` | 16 | PURE (static) | **EXTRACTED** |
| **Module-level** | `explain_with_mlx` | 59 | PURE (helper) | **EXTRACTED** (moved to explainer.py) |
| DS | `get_ds_belief`, `get_ds_conflict`, `detect_contradiction_ds`, `has_contradiction` | 51 | COORDINATOR | STAYS |
| Evidence/Storage | `_evict_*`, `add_evidence`, `_update_source_credibility` | 45 | COORDINATOR | STAYS |
| Test design | `_init_test_templates`, `_design_*` (×5) | 119 | PURE | **DEFERRED Tier-6** |
| Adversarial | `adversarial_verifier`, `adversarial_verification`, `assess_*`, `detect_*`, `check_temporal_*`, `generate_devils_advocate` | 118 | COORDINATOR | STAYS |
| Hermes LLM | `generate_hypotheses_async` | 152 | COORDINATOR | STAYS |
| Generate/test | `generate_hypotheses`, `_create_*`, `_generate_hypotheses_from_patterns`, `_check_co_occurrence` | 150 | COORDINATOR (4+ self calls) | STAYS |
| Test exec | `design_test`, `execute_test`, `update_hypothesis` | 125 | COORDINATOR | STAYS |
| Falsify | `attempt_falsification`, `_attempt_adversarial_*`, `_check_logical_inconsistency`, `_statements_contradict` | 199 | COORDINATOR | STAYS |
| Rank/merge | `rank_hypotheses`, `_calculate_hypothesis_score`, `get_most_likely`, `merge_hypotheses`, `_statement_similarity` | 149 | COORDINATOR | STAYS |
| Cycle | `run_hypothesis_cycle` | 95 | **BIG COORDINATOR (7+ calls)** | STAYS |
| Stats/clear | `_prune_hypotheses`, `get_hypothesis`, `get_all_hypotheses`, `get_statistics`, `clear` | 146 | COORDINATOR | STAYS |
| Sprint 8TD | `generate_sprint_hypotheses` | 45 | PURE | **DEFERRED Tier-6** |
| F150H heuristic | `suggest_next_queries`, `_heuristic_query_generation`, `_extract_*_heuristic` (×2), `_find_*` (×2) | 583 | COORDINATOR | STAYS (cluster `_heuristic_*` would be Tier-7+) |
| F150+ packs | `build_hypothesis_pack`, `_generate_hypotheses_heuristic`, `_generate_ranked_queries`, `_generate_ioc_follow_ups`, `_deduplicate_and_rank_queries`, `_extract_relationships_heuristic`, `_extract_source_hints_heuristic`, `_extract_temporal_anchors_heuristic`, `_extract_org_anchors`, `_ner_capability_probe`, `_model_assisted_*` (×2) | 753 | COORDINATOR (uses many `self._heuristic_*`) | STAYS (already partially in `packs.py`) |
| Dark surface | `generate_dark_surface_queries`, `_generate_dark_surface_queries_fallback`, `_looks_like_*` (×3) | 245 | COORDINATOR (Hermes LLM + ResearchLayer) | **DEFERRED Tier-6** |

**Čistá kandidátní extrakce:** 10 PURE causal metod + 1 module-level helper.

---

## STEP 2 — Extrakce CausalReasoner

**Nový soubor:** `brain/hypothesis/causal.py` (572 LOC)

### Architektonické rozhodnutí

1. **Třída `CausalReasoner`, ne volné funkce** — sdílí 7 storage fields
   (`_causal_entities`, `_co_occurrence_matrix`, `_entity_id_to_idx`,
   `_idx_to_entity_id`, `_temporal_sequences`, `_anomaly_signals`,
   `_source_types`). Volné funkce by vyžadovaly předávání těchto
   fields skrze každý call — vysoký kouming overhead.

2. **Vlastní storage** — `CausalReasoner` vlastní svůj storage; engine si
   drží `self._causal_reasoner` a fasáduje. Tím se **vylučuje kruhová
   závislost** na `HypothesisEngine._hypotheses`, `_evidence` atd.

3. **`_extract_iocs_from_text` přijímá `source_type` parametr i když ho
   nepoužívá** — originální engine signatura ho měl; testuje se, že
   `_source_types` set se plní v `extract_entities` nad rámec textu.
   Zachováno kvůli byte-for-byte API shodě.

4. **Lazy `import time` uvnitř `extract_entities`** — originální engine
   měl `import time` na úrovni modulu, ale po přesunu do
   `CausalReasoner` by to byl křížový import. Přesunuto do metody
   (lokální import, fail-soft fallback na `time.time()`).

5. **`generate_hypotheses` (sync) na `CausalReasoner`** — engine fasáda
   je `async` a běží v `asyncio.to_thread` aby se zabránilo blokování
   event loopu na velkých finding setech.

### Extrahované metody (10)

| Engine originál | CausalReasoner metoda | Rozdíl |
|----------------|----------------------|--------|
| `extract_causal_entities(findings)` | `extract_entities(findings)` | Přejmenováno (originál `_entities` suffix byl kryptický) |
| `_extract_iocs_from_text(text, source_type, finding_id, ts)` | `_extract_iocs_from_text(text, source_type, finding_id, ts)` | Identická |
| `_is_valid_ip(ip)` | `_is_valid_ip(ip)` (staticmethod) | Identická |
| `build_temporal_sequences(gap_threshold)` | `build_temporal_sequences(gap_threshold)` | Identická |
| `compute_co_occurrence_matrix()` | `compute_co_occurrence_matrix()` | Identická |
| `get_co_occurrence(entity_a, entity_b)` | `get_co_occurrence(entity_a, entity_b)` | Identická |
| `detect_causal_anomalies(findings)` | `detect_anomalies(findings)` | Přejmenováno (konzistentnější) |
| `generate_causal_hypotheses(findings, max_hypotheses)` | `generate_hypotheses(findings, max_hypotheses)` | Přejmenováno |
| `_calculate_causal_confidence(...)` | `_calculate_confidence(...)` (staticmethod) | Zkráceno, PURE |
| `_generate_causal_statement(e1, e2, conf)` | `_generate_statement(e1, e2, conf)` (staticmethod) | Zkráceno, PURE |

### Přidané read-side properties (nové)

| Property | Účel |
|----------|------|
| `source_types` | Read-only `set[str]` pro testy a agregaci v engine |
| `entity_count` | `int` pro monitoring |
| `sequence_count` | `int` pro monitoring |

---

## STEP 3 — Backward compat shim

Engine fasáda na `HypothesisEngine` (řádky ~770-870 v `hypothesis_engine.py`):

```python
self._causal_reasoner: CausalReasoner = CausalReasoner()
# Legacy attribute aliases — kept for backward compat
self._causal_entities = self._causal_reasoner._causal_entities  # type: ignore[assignment]
self._co_occurrence_matrix = self._causal_reasoner._co_occurrence_matrix
self._entity_id_to_idx = self._causal_reasoner._entity_id_to_idx
self._idx_to_entity_id = self._causal_reasoner._idx_to_entity_id
self._temporal_sequences = self._causal_reasoner._temporal_sequences  # type: ignore[assignment]
self._anomaly_signals = self._causal_reasoner._anomaly_signals  # type: ignore[assignment]
self._source_types = self._causal_reasoner._source_types
```

Každá engine metoda (9 fasád + 2 static helpery) deleguje na
`CausalReasoner` a obnovuje aliasy. `generate_causal_hypotheses` je
zabalena do `async` wrapperu s `asyncio.to_thread`.

### Testy (26 PASS v `tests/probe_hypothesis_causal.py`)

6 tříd, 26 testů:

| Třída | Testy | Účel |
|-------|-------|------|
| `TestCausalReasonerImports` | 4 | Import paths: canonical, package facade, engine, no-circular |
| `TestCausalReasonerStandalone` | 5 | CausalReasoner funguje bez HypothesisEngine |
| `TestHypothesisEngineCausalFacade` | 7 | Engine fasády delegují + aliasy obnoveny |
| `TestCausalM1Bounds` | 6 | Bounds constants + bounded extraction cap |
| `TestCausalIsolation` | 2 | Dvě instance mají nezávislý storage |
| `TestExplainWithMLXExtraction` | 2 | explain_with_mlx přesunut do explainer.py |

---

## STEP 4 — Přesun `explain_with_mlx` (59 LOC)

**Z:** `brain/hypothesis_engine.py` (top-level async, ~řádky 525-575)
**Do:** `brain/hypothesis/explainer.py` (přidáno jako module-level async)

### Změny
- Importy `asyncio`, `hashlib` přesunuty na úroveň modulu (lazy importy
  v těle nebyly potřeba — `asyncio` se používá všude v `explainer.py`,
  `hashlib` jen pro jeden `sha256().hexdigest()[:8]`).
- Byte-for-byte tělo funkce zachováno.
- Engine re-exportuje přes `from brain.hypothesis.explainer import explain_with_mlx`.
- `AdversarialVerifier` (v `brain/hypothesis/adversarial.py:216`) importuje
  z původního `brain.hypothesis_engine` → stále funguje (je to tentýž
  symbol).

### Testy
- 2 nové testy v `TestExplainWithMLXExtraction`.
- Stávající 2 testy v `tests/test_sprint67/test_hypothesis_explainer.py`
  (`TestExplainWithMLX::test_explain_with_mlx_no_model`,
  `test_explain_with_mlx_generates_hash`) — PASS nezměněně.

---

## STEP 5 — Aktuální stav (po Tier 5)

### Package layout

```
brain/hypothesis/
├── __init__.py           113 LOC   (facade re-export)
├── _types.py             387 LOC   (enums + dataclasses + Protocol)
├── adversarial.py        895 LOC   (AdversarialVerifier)
├── explainer.py          173 LOC   (SimpleNodeAblationExplainer + explain_with_mlx)  ↑
├── packs.py              744 LOC   (SourceHint + HypothesisPack)
└── causal.py             572 LOC   (CausalReasoner)                                    ← NEW
```

### Engine zmenšení

| Metrika | Před Tier 5 | Po Tier 5 | Delta |
|---------|-------------|-----------|-------|
| `hypothesis_engine.py` LOC | 3753 | 3370 | **−383** |
| `hypothesis/` package LOC | 2248 | 2884 | +636 |
| Engine / package ratio | 1.67× | 1.17× | -30% |

> Pozn.: Causal DTOs (`CausalEntity`, `TemporalSequence`, `AnomalySignal`,
> `CausalHypothesis`) a bounds constants byly v Tier 1-2 extrahovány do
> `_types.py` — engine je nyní importuje jako re-export, čímž odpadla
> lokální duplicita (~50 LOC) a zmírnila se Pyright variance chyba.

### Test pokrytí

| Test file | PASS | FAIL | Poznámka |
|-----------|------|------|----------|
| `tests/probe_hypothesis_causal.py` | **26** | 0 | **Nový Tier 5 probe** |
| `tests/test_sprint67/test_hypothesis_explainer.py` | 4 | 0 | Back-compat ověřen |
| `tests/test_ds_integration.py` | ~14 | 0 | Engine import + DS bridge |
| `tests/probe_packs_extraction.py` | 13 | 0 | Tier-4 sanity |
| `tests/probe_adversarial_extraction.py` | 13 | 0 | Tier-3 sanity |
| `tests/probe_hypothesis_types_extraction.py` | ~7 | 0 | Tier-1+2 sanity |
| `tests/probe_explainer_extraction.py` | 4 | 0 | Tier-3 sanity |
| `tests/test_sprint67/test_mlx_cache.py` | 5 | **4** | **PRE-EXISTING** (baseline: 5 pass, 4 fail na `git stash`) |
| `tests/test_hypothesis_builder.py` | n/a | n/a | **PRE-EXISTING** broken: import `brain.causal_engine` (neexistující modul) |

**Součet Tier 5 přínos:** +26 PASS, 0 nových FAIL.

---

## Rozhodnutí o jednotlivých metodách (kompletní mapa)

### EXTRACTED (11 metod, Tier 5)

| Metoda | Tier | Nový domov |
|--------|------|-----------|
| `extract_causal_entities` | 5 | `CausalReasoner.extract_entities` |
| `_extract_iocs_from_text` | 5 | `CausalReasoner._extract_iocs_from_text` |
| `_is_valid_ip` | 5 | `CausalReasoner._is_valid_ip` (static) |
| `build_temporal_sequences` | 5 | `CausalReasoner.build_temporal_sequences` |
| `compute_co_occurrence_matrix` | 5 | `CausalReasoner.compute_co_occurrence_matrix` |
| `get_co_occurrence` | 5 | `CausalReasoner.get_co_occurrence` |
| `detect_causal_anomalies` | 5 | `CausalReasoner.detect_anomalies` |
| `_calculate_causal_confidence` | 5 | `CausalReasoner._calculate_confidence` (static) |
| `_generate_causal_statement` | 5 | `CausalReasoner._generate_statement` (static) |
| `generate_causal_hypotheses` (koordinátor) | 5 | Engine fasáda (async) + `CausalReasoner.generate_hypotheses` (sync core) |
| `explain_with_mlx` | 5 | `brain.hypothesis.explainer.explain_with_mlx` |

### COORDINATOR_STAYS (16+ metod, doporučeno pro Tier 6+)

Tyto metody volají 3+ dalších `self.X` metod nebo sdílejí storage
s jinými engine subsystémy (hypothesis, evidence, ds_engine,
adversarial_verifier). Extrakce by vytvořila kruhové závislosti.

- `generate_hypotheses`, `run_hypothesis_cycle` (top-level cycle)
- `attempt_falsification` (uses adversarial + ds)
- `rank_hypotheses`, `merge_hypotheses`
- `execute_test`, `design_test`, `update_hypothesis`
- `generate_devils_advocate`, `check_temporal_consistency`
- `get_ds_belief`, `get_ds_conflict`, `detect_contradiction_ds`
- `generate_hypotheses_async` (Hermes LLM)
- `build_hypothesis_pack` (uses many heuristic helpers)
- `generate_dark_surface_queries` (Hermes + ResearchLayer)

### DEFERRED (PURE, ale větší clustery)

| Metoda/Cluster | Tier 6+ cíl | Důvod odkladu |
|----------------|-------------|---------------|
| `_init_test_templates` + `_design_*` (×5) | Tier 6 → `brain/hypothesis/test_designer.py` | Self-contained, ale 119 LOC celkem |
| `generate_sprint_hypotheses` | Tier 6 → `brain/hypothesis/sprint_hypotheses.py` | PURE, 45 LOC |
| `generate_dark_surface_queries` + helpers | Tier 7 → `brain/hypothesis/dark_surface.py` | Volá Hermes LLM async; potřebuje coordinator pattern |
| `_heuristic_query_generation` + cluster | Tier 8+ → `brain/hypothesis/heuristic_packs.py` | 583 LOC; mnoho `self._extract_*_heuristic` cross-calls |
| `_extract_*_heuristic` (×8) | Tier 8+ | Závisí na heuristickém clusteru |

### BLOCKED (TBD — kruhové závislosti)

Žádné metody nebyly BLOCKED v Tier 5. Všechny PURE causal metody
byly extrahovatelné díky izolované storage množině.

---

## Doporučený Tier 6 split plán

**Tier 6 — Test Designer + Sprint Hypotheses (nízké riziko):**

1. `brain/hypothesis/test_designer.py` (~140 LOC)
   - `TestDesigner` třída
   - Metody: `_init_test_templates`, `_design_existence_test`,
     `_design_relationship_test`, `_design_causal_test`,
     `_design_identity_test`, `_design_temporal_test`
   - Backward compat: `HypothesisEngine.design_test` deleguje

2. `brain/hypothesis/sprint_hypotheses.py` (~50 LOC)
   - `SprintHypothesisGenerator` třída
   - Metoda: `generate_sprint_hypotheses` (PURE, 45 LOC)
   - Žádná závislost na engine — triviální extrakce

**Očekávaná redukce engine:** ~190 LOC (3370 → ~3180).

**Tier 7 — Dark Surface + Hermes LLM (střední riziko):**

1. `brain/hypothesis/dark_surface.py` (~250 LOC)
   - `DarkSurfaceQueryGenerator` třída
   - Metody: `generate_dark_surface_queries`,
     `_generate_dark_surface_queries_fallback`,
     `_looks_like_domain_or_ip`, `_looks_like_ipfs_cid`, `_looks_like_hash`
   - Vyžaduje `hermes_engine` (injection) — async coordinator
   - **Riziko:** Hermes LLM glue — fallback path musí být fail-soft

2. `brain/hypothesis/llm_hypotheses.py` (~155 LOC)
   - `LLMHypothesisGenerator` třída
   - Metoda: `generate_hypotheses_async` (Hermes + MultiHop)
   - Vyžaduje hermes_engine, MultiHop chain, graph_rag

**Tier 8+ — Heuristic packs cluster (vysoké riziko):**

`build_hypothesis_pack` cluster (753 LOC) je největší souvislý blok v
engine. Extrakce vyžaduje:
- Přesun `_heuristic_query_generation` + 8× `_extract_*_heuristic` do
  `brain/hypothesis/heuristic_packs.py` (~600 LOC)
- Přesun `_generate_ranked_queries`, `_deduplicate_and_rank_queries`,
  `_generate_ioc_follow_ups` (~200 LOC)
- `_ner_capability_probe` (~80 LOC, thread-based fail-soft)
- `_model_assisted_hypothesis_pack` + `_model_assisted_query_suggestion`
  (~80 LOC, lazy MLX glue)

Doporučení: Tier 8 rozdělit na 2-3 sub-tiers (heuristic-only,
model-assisted, IOC pivots) kvůli kruhovým `_extract_*_heuristic`
závislostem.

---

## Kritické GHOST_INVARIANT ověření

✅ **Async safety:** `generate_causal_hypotheses` engine fasáda běží v
`asyncio.to_thread` — sync `CausalReasoner.generate_hypotheses` neblokuje
event loop.

✅ **Storage izolace:** `CausalReasoner._causal_entities` ≠
`HypothesisEngine._hypotheses` — žádný cross-write.

✅ **Back-compat:** Všech 7 signatur engine metod + `_calculate_*`,
`_generate_*` static helpers zachováno byte-for-byte.

✅ **M1 8GB bounds:** `MAX_CAUSAL_ENTITIES`, `MAX_CAUSAL_FINDINGS`,
`MAX_CAUSAL_HYPOTHESES`, `MAX_CO_OCCURRENCE_MATRIX_SIZE`,
`CO_OCCURRENCE_FP16` importovány z `_types.py` (single source of truth).

✅ **Fail-soft:** `compute_co_occurrence_matrix` vrací `None` na
numpy ImportError; `detect_anomalies` nevyhazuje.

✅ **Numpy lazy import:** v `compute_co_occurrence_matrix` (volá se
pouze pokud je numpy k dispozici).

✅ **Lazy time import:** v `extract_entities` (lokální fallback na
`time.time()`).

---

## Celkový Tier progress

| Tier | Scope | Engine Δ | Net gain |
|------|-------|---------|----------|
| Tier 1+2 | Types extraction | 5373 → 3753 | **−1620 LOC** |
| Tier 3 partial | Adversarial + Explainer | (v rámci Tier 1+2) | 837+78 LOC extracted |
| Tier 4 | Packs extraction | 3753 → 3753 | 711 LOC extracted (within same engine LOC) |
| **Tier 5** | **Causal + explain_with_mlx** | **3753 → 3370** | **−383 LOC engine, 631 LOC in dedicated modules** |

**Celkově od originálu 5373 LOC engine:** `−2003 LOC` (37% redukce),
s 5 dedikovanými moduly v `brain/hypothesis/` (2884 LOC).

---

*Tier 5 hotovo. Tier 6 připraven k plánování.*

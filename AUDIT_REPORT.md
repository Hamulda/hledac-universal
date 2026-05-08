# Audit Report — hledac/universal
Datum: 2026-05-08
Celkem nalezeno: 15 problémů (9 bytecode stub souborů, 4 důležité, 2 drobnosti)

## Přehled podle kategorie
| Kategorie | Počet |
|-----------|-------|
| Bytecode stubs (zdrojový kód zničen, v bytecode) | 9 souborů |
| Důležité nedokončené implementace | 4 |
| Drobnosti (kosmetika) | 2 |

---

## 🔴 KRITICKÉ (blokující funkčnost) — 9 bytecode stub souborů

### 1. `knowledge/search_index.py`
**Typ**: 5 prázdných tříd — všechny s `"""TODO: rekonstruovat z bytecode"""` a `pass`
```
class SearchDocument:     """TODO: rekonstruovat z bytecode"""    pass
class SearchResult:      """TODO: rekonstruovat z bytecode"""    pass
class BM25Index:         """TODO: rekonstruovat z bytecode"""    pass
class MetadataStore:      """TODO: rekonstruovat z bytecode"""    pass
class LocalSearchSeam:    """TODO: rekonstruovat z bytecode"""    pass
```
**Kontext**: BM25 fulltext vyhledávání, metadata storage, local search seam. Zdrojový kód zničen, data v bytecode.

***

### 2. `tests/probe_f12g/__init__.py`
**Typ**: 5 prázdných test tříd
**Kontext**: F12G probe testuje "Truthful fallback attrs, dead world cleanup, runtime consistency, PEP562 compliance, no new framework creep".

***

### 3. `tests/probe_f130f/__init__.py`
**Typ**: Prázdné test třídy s `"""TODO: rekonstruovat z bytecode"""` a `pass`
**Třídy**: TestRootMainShellOnly, TestEntrypointAuthorityTruth, TestMainDelegationMap

***

### 4. `tests/probe_f191b/__init__.py`
**Typ**: Prázdné test třídy s `"""TODO: rekonstruovat z bytecode"""` a `pass`
**Třídy**: TestGhostExecutorDonorStubTruth, TestGhostExecutorDonorRole, TestGhostBridgeClassification, TestStaleStealthImport, TestNoFrameworkCreep

***

### 5. `tests/probe_f300a/__init__.py`
**Typ**: Prázdné test třídy s `"""TODO: rekonstruovat z bytecode"""` a `pass`
**Třídy**: TestPlaceholderHandlerDistinguishability, TestGhostBridgeReadSideOnly, TestCanonicalReadySliceEmpty, TestNoExecuteWithLimits, TestDonorCompatRoleExplicit, TestStealthHarvestTruthfulDegraded, TestCleanupBoundedIdempotent

***

### 6. `tests/probe_f500m/__init__.py`
**Typ**: 7 prázdných test tříd
**Třídy**: TestSanitizeBoundaryTruth, TestSecondParseFailureTruth, TestExportHandoffPrimary, TestDictPathCompatOnly, TestTopNodesFallback, TestPathAuthorityDelegated, TestNoFrameworkCreep

***

### 7. `tests/probe_f900g/__init__.py`
**Typ**: Prázdné test třídy s `"""TODO: rekonstruovat z bytecode"""` a `pass`

***

### 8. `tests/probe_f190f/probe_f190f_web_academic_hygiene.py`
**Typ**: 26 test funkcí — všechny raising `NotImplementedError("Stub - rekonstruovat z bytecode")`
**Kontext**: Testuje hygiene pravidla pro web_intelligence a academic_search moduly.

***

### 9. `tests/probe_bench_g/run_m1_inference_mlx_baseline.py`
**Typ**: 9 probe funkcí — všechny raising `NotImplementedError("Stub - rekonstruovat z bytecode")`
**Kontext**: M1 inference benchmark runner (probe_mlx_import, probe_mlx_lm_import, probe_metal_memory_surface, probe_tiny_array_ops, probe_cached_model_path, probe_model_load_latency, probe_first_token_latency, probe_cache_clear_latency, run_baseline).

***

## 🟡 DŮLEŽITÉ (neúplné funkce)

### 10. `network/session_runtime.py`, řádky 23-25
**Typ**: Architektonické TODO komentáře
```python
TODO(budget/8AC): napojit concurrency matrix na connector limits
TODO(transport/8AD): per-transport sessions pokud bude potřeba
TODO(integration/8AE): SourceTransportMap integration
```
**Kontext**: TCPConnector limit=25, limit_per_host=5, ttl_dns_cache=300 je implementováno. TODO označují FUTURE integrace.

***

### 11. `research/task_prioritizer.py`, řádek ~105
**Typ**: Placeholder implementace `extract_features()`
```python
def extract_features(self, task_metadata: Dict):
    """TODO: implementovat podle skutečných metadat."""
    # Základní feature vector - placeholder implementace
    features = [
        task_metadata.get('priority', 0.5),
        task_metadata.get('estimated_duration', 1.0),
        # ... 10 features celkem
    ]
    return mx.array(features, dtype=mx.float32)
```
**Kontext**: Funkce vrací mx.array, ale hodnoty jsou hrubé aproximace.

***

### 12. `research/branch_manager.py`, řádek ~195-204
**Typ**: Prázdná implementace `_explore_entity()`
```python
async def _explore_entity(self, entity: str):
    """Placeholder pro exploraci entity. TODO: Implementovat skutečnou exploraci."""
    logger.debug(f"Exploring entity: {entity}")
    pass
```
**Kontext**: Voláno z _create_branch() když branch prob > 0.7. V současnosti pouze loguje.

***

### 13. `rl/sprint_policy_manager.py`, řádek ~210
**Typ**: Stub s redirectem na jinou třídu
```python
def update_with_quality_decisions(self, decisions: list, feed_url: str = "unknown") -> None:
    pass  # Stub: source weight adaptation lives in SprintScheduler._adapt_source_weights_from_feedback
```
**Kontext**: Autorita je v `SprintScheduler._adapt_source_weights_from_feedback()`. Funkce je tu pro future per-source reward injection.

***

## 🟢 DROBNOSTI (kosmetika)

### 14. `config.py`, řádek 188
**Typ**: Hardcoded TOR proxy `socks5://127.0.0.1:9050`
**Kontext**: Standardní TOR port. Lze překonfigurovat přes ENV variable.

***

### 15. `coordinators/fetch_coordinator.py`, řádek 269
**Typ**: Hardcoded WebSocket endpoint `"ws://127.0.0.1:9222"`
**Kontext**: Chrome DevTools debug endpoint — jen pro vývoj/debug.

***

## Odstraněno z původního reportu (false positives)

1. **brain/ane_embedder.py embed() raise NotImplementedError** — záměrný fail-soft design (caller catchuje a použije MLX fallback)
2. **deep_probe.py PathPattern.generate_predictions()** — správný ABC pattern, všechny subclassy implementují
3. **project_types.py ResearchOrchestratorBase.research()** — správný ABC pattern

---

## Soupis oprav

| # | Soubor | Závažnost | Akce |
|---|--------|----------|------|
| 1 | search_index.py | 🔴 KRITICKÉ | Přepsat od nuly / obnovit z bytecode |
| 2 | probe_f12g | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 3 | probe_f130f | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 4 | probe_f191b | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 5 | probe_f300a | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 6 | probe_f500m | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 7 | probe_f900g | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 8 | probe_f190f | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 9 | probe_bench_g | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 10 | session_runtime.py | 🟡 DŮLEŽITÉ | Implementovat TODO nebo přesunout do PM |
| 11 | task_prioritizer.py | 🟡 DŮLEŽITÉ | Napojit feature extraction na skutečná metadata |
| 12 | branch_manager.py | 🟡 DŮLEŽITÉ | Implementovat _explore_entity() |
| 13 | policy_manager.py | 🟡 DŮLEŽITÉ | Implementovat / zdokumentovat stub |
| 14 | config.py | 🟢 DROBNOST | Není potřeba akce |
| 15 | fetch_coordinator.py | 🟢 DROBNOST | Není potřeba akce |

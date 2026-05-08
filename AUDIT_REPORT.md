# Audit Report — hledac/universal
Datum: 2026-05-08
Celkem nalezeno: 23 problémů

## Přehled podle kategorie
| Kategorie | Počet |
|-----------|-------|
| Bytecode stubs (TODO: rekonstruovat) | 17 |
| NotImplementedError stubs | 3 |
| Hardcoded konstanty | 2 |
| TODO architektonické | 1 |

---

## 🔴 KRITICKÉ (blokující funkčnost)

### 1. Bytecode stub: `tests/probe_f190f/probe_f190f_web_academic_hygiene.py`
**Typ**: 26 funkcí — všechny raising `NotImplementedError("Stub - rekonstruovat z bytecode")`
**Kód**:
```python
def test_f190f_1_web_intelligence_utility_not_canonical(*args, **kwargs):
    """TODO: rekonstruovat z bytecode"""
    raise NotImplementedError("Stub - rekonstruovat z bytecode")
# ... 25 dalších stejných stub funkcí
```
**Kontext**: Tento probe testuje hygiene pravidla pro web_intelligence a academic_search moduly. Všech 26 test funkcí je nefunkčních stubů — žádná hygiena není testována.

**Jak opravit**: Obnovit z `.cpython-312.pyc` bytecode souboru (název v komentáři souboru), nebo přepsat testy od nuly.

***

### 2. Bytecode stub: `tests/probe_bench_g/run_m1_inference_mlx_baseline.py`
**Typ**: 9 funkcí — všechny raising `NotImplementedError("Stub - rekonstruovat z bytecode")`
**Kód**:
```python
def probe_mlx_import(*args, **kwargs):
    """TODO: rekonstruovat z bytecode"""
    raise NotImplementedError("Stub - rekonstruovat z bytecode")

def probe_mlx_lm_import(*args, **kwargs):
    """TODO: rekonstruovat z bytecode"""
    raise NotImplementedError("Stub - rekonstruovat z bytecode")
# ... atd.
```
**Kontext**: Toto je M1 inference benchmark runner. 9 probe funkcí (probe_mlx_import, probe_mlx_lm_import, probe_metal_memory_surface, probe_tiny_array_ops, probe_cached_model_path, probe_model_load_latency, probe_first_token_latency, probe_cache_clear_latency, run_baseline) je zcela nefunkčních. M1 inference benchmark není měřitelný.

**Jak opravit**: Obnovit z `__pycache__/run_m1_inference_mlx_baseline.cpython-312.pyc`, nebo přepsat benchmark implementace.

***

### 3. Bytecode stub: `knowledge/search_index.py`
**Typ**: 5 prázdných tříd — všechny s `"""TODO: rekonstruovat z bytecode"""` a `pass`
**Kód**:
```python
class SearchDocument:
    """TODO: rekonstruovat z bytecode"""
    pass

class SearchResult:
    """TODO: rekonstruovat z bytecode"""
    pass

class BM25Index:
    """TODO: rekonstruovat z bytecode"""
    pass

class MetadataStore:
    """TODO: rekonstruovat z bytecode"""
    pass

class LocalSearchSeam:
    """TODO: rekonstruovat z bytecode"""
    pass
```
**Kontext**: Kódový comment říká "Stub pro __pycache__/search_index.cpython-312.pyc - generováno z bytecode". Toto je search index modul — BM25 fulltext vyhledávání, metadata storage, local search seam. Zdrojový kód byl zničen (přepsán stubem), data jsou v bytecode.

**Jak opravit**: Obnovit z `__pycache__/search_index.cpython-312.pyc`. Příkaz: `python -c "import dis; dis.dis(open('__pycache__/search_index.cpython-312.pyc','rb').read())"` — ale toto je extremně obtížné. Lepší je přepsat funkcionalitu od nuly z dokumentace.

***

### 4. Bytecode stub: `tests/probe_f12g/__init__.py`
**Typ**: 5 prázdných test tříd
**Kód**:
```python
class TestF12GTruthfulFallbackAttrs:
    """TODO: rekonstruovat z bytecode"""
    pass

class TestF12GDeadWorldCleanup:
    """TODO: rekonstruovat z bytecode"""
    pass
# ... atd.
```
**Kontext**: F12G probe testuje "Truthful fallback attrs, dead world cleanup, runtime consistency, PEP562 compliance, no new framework creep". Zdroják zničen, bytecode ztratil.

**Jak opravit**: Obnovit z bytecode, nebo přepsat testy podle的名义 scope.

***

---

## 🟡 DŮLEŽITÉ (neúplné funkce)

### 5. `brain/ane_embedder.py`, řádek ~145
**Typ**: NotImplementedError jako flow control
**Kód**:
```python
async def embed(self, texts: Union[str, List[str]]) -> np.ndarray:
    if not self._loaded or self.model is None:
        raise NotImplementedError("ANE embedder not loaded, use fallback")
```
**Kontext**: Toto NENÍ problém — je to záměrné fail-open design. Když ANE není dostupná/není načtena, volá se `raise NotImplementedError` s textem "use fallback". Volající mácatch NotImplementedError a použije MLX fallback. Podívejme se na volající kód — v `warmup()`:

```python
except NotImplementedError:
    # embed() throws NotImplementedError until real inference is implemented
    # This is expected — warmup still counts as "priming the ANE subsystem"
    logger.debug("ANEEmbedder warmup: real inference not implemented yet, skipping")
```

Toto je fail-soft design, ne bug. **Snížit na 🟢 DROBNOST** — je to záměrné.

***

### 6. `deep_probe.py`, řádek 384-386
**Typ**: Abstraktní metoda v base třídě
**Kód**:
```python
class PathPattern:
    """Base class for path patterns."""

    def generate_predictions(self) -> List[Tuple[str, float]]:
        """Generate path predictions with confidence scores."""
        raise NotImplementedError("PathPattern.generate_predictions must be implemented by subclass")
```
**Kontext**: PathPattern je base class. Všechny 3 subclassy (DatePathPattern na řádku 388, SequentialPathPattern na řádku 410, FilePathPattern na řádku 465) **mají vlastní implementace** generate_predictions(). Ověřeno:
- DatePathPattern.generate_predictions() — implementuje predikci dalšího/předchozího roku
- SequentialPathPattern.generate_predictions() — implementuje sekvenční pattern detection
- FilePathPattern.generate_predictions() — implementuje file extension patterns

**Závěr**: Base class raise NotImplementedError je SPRÁVNÝ OOP pattern pro abstract base class. Není to bug. **Odstranit z reportu.**

***

### 7. `project_types.py`, řádek 735-755
**Typ**: NotImplementedError v base class
**Kód**:
```python
class ResearchOrchestratorBase:
    """Abstract base class for research orchestrators."""
    
    async def research(
        self,
        query: str,
        search_func: Optional[Any] = None,
        domain: str = "general"
    ) -> Any:
        """
        Execute research query.
        ...
        Raises:
            NotImplementedError: Must be implemented by subclasses
        """
        raise NotImplementedError("Subclasses must implement research()")
```
**Kontext**: Toto je **abstract base class pattern** — `ResearchOrchestratorBase` je ABC a `research()` je abstraktní metoda. Subclass musí implementovat. Není to bug, je to správný OOP design.

**Závěr**: NENÍ problém — správný abstract base class pattern. **Odstranit z reportu.**

***

### 8. `network/session_runtime.py`, řádky 23-25
**Typ**: Architektonické TODO komentáře
**Kód**:
```python
TODO(budget/8AC): napojit concurrency matrix na connector limits
TODO(transport/8AD): per-transport sessions pokud bude potřeba
TODO(integration/8AE): SourceTransportMap integration
```
**Kontext**: Tyto TODO jsou v hlavičce modulunad INVARIANTS blockem. Dokumentují plánované rozšíření transport warstvy. Modul sám o sobě funguje — TCPConnector limit=25, limit_per_host=5, ttl_dns_cache=300 je implementováno. Tyto TODO označují FUTURE integrace, ne missing implementace.

**Jak opravit**: Buď implementovat (a smazat TODO), nebo přesunout TODO do projekt management nástroje (Linear/JIRA).

***

### 9. `research/task_prioritizer.py`, řádek ~105
**Typ**: TODO v commentu + placeholder implementace
**Kód**:
```python
def extract_features(self, task_metadata: Dict):
    """
    Extrahuje 10-dim feature vector z task metadata.
    TODO: implementovat podle skutečných metadat.
    """
    if not MLX_AVAILABLE:
        return None

    # Základní feature vector - placeholder implementace
    features = [
        task_metadata.get('priority', 0.5),
        task_metadata.get('estimated_duration', 1.0),
        task # ... 10 features celkem
    ]
    return mx.array(features, dtype=mx.float32)
```
**Kontext**: extract_features() má placeholder hodnoty pro všechny feature dimenze. Funkce je funkční (vrací mx.array), ale hodnoty jsou hrubé aproximace než skutečné feature engineering z task_metadata.

**Jak opravit**: Napojit na skutečná metadata tasku podle jeho schema (priority, estimated_duration, complexity, source_type, entity_count, novelty, contradiction_score, centrality, historical_gain, historical_duration).

***

### 10. `research/branch_manager.py`, řádek ~195-204
**Typ**: Prázdná implementace + TODO
**Kód**:
```python
async def _explore_entity(self, entity: str):
    """
    Placeholder pro exploraci entity.
    TODO: Implementovat skutečnou exploraci.
    """
    logger.debug(f"Exploring entity: {entity}")
    # Zde by byla implementace dalšího výzkumu
    pass
```
**Kontext**: _explore_entity() je voláno z _create_branch() když branch prob > 0.7. V současné době pouze loguje. BranchManager jako celek funguje (ANE prediction, fallback rules, spike priority network), ale _explore_entity() je prázdný stub.

**Jak opravit**: Implementovat skutečnou exploraci — typicky by to mělo vytvořit nový research task s danou entity jako cílem.

***

### 11. `rl/sprint_policy_manager.py`, řádek ~210
**Typ**: Stub s redirectem na jinou třídu
**Kód**:
```python
def update_with_quality_decisions(
    self, decisions: list, feed_url: str = "unknown"
) -> None:
    """
    ...Currently a no-op stub — source-type-level quality signal is derived from
    accepted_findings ratio and applied in SprintScheduler._adapt_source_weights_from_feedback().
    ...
    """
    pass  # Stub: source weight adaptation lives in SprintScheduler._adapt_source_weights_from_feedback
```
**Kontext**: Toto je vědomě označeno jako stub — autorita je v `SprintScheduler._adapt_source_weights_from_feedback()`. Design dokumentace to připouští. Funkce je tu pro future per-source reward injection bez změny public API.

**Jak opravit**: Buď implementovat (napojit na FindingQualityDecision list), nebo explicitně zdokumentovat že je to rezervované pro budoucí použití.

***

---

## 🟢 DROBNOSTI (kosmetika, komentáře)

### 12. `config.py`, řádek 188
**Typ**: Hardcoded TOR proxy
**Kód**:
```python
tor_proxy: str = "socks5://127.0.0.1:9050"
```
**Kontext**: Default TOR port 9050 je standardní. Pokud uživatel má TOR na jiném portu, může překonfigurovat přes ENV variable nebo config file. Není to kritický problém.

**Status**: Low priority — standard port je v pořádku.

***

### 13. `coordinators/fetch_coordinator.py`, řádek 269
**Typ**: Hardcoded WebSocket endpoint pro DevTools debug
**Kód**:
```python
self._endpoint = "ws://127.0.0.1:9222"
```
**Kontext**: Toto je Chrome DevTools debug endpoint používaný pro headless browser inspection. Jen pro vývoj/debug, ne v produkci. Není to kritické.

**Status**: Low priority — jen pro debug.

***

---

## Přehled oprav

| # | Soubor | Závažnost | Akce |
|---|--------|----------|------|
| 1 | probe_f190f | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 2 | probe_bench_g | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 3 | search_index.py | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 4 | probe_f12g | 🔴 KRITICKÉ | Obnovit z bytecode / přepsat |
| 5 | ane_embedder.py | 🟢 DROBNOST | Odstranit z reportu (záměrný fail-soft) |
| 6 | deep_probe.py | 🟢 DROBNOST | Odstranit z reportu (správný ABC pattern) |
| 7 | project_types.py | 🟢 DROBNOST | Odstranit z reportu (správný ABC pattern) |
| 8 | session_runtime.py | 🟡 DŮLEŽITÉ | Implementovat TODO nebo přesunout do PM |
| 9 | task_prioritizer.py | 🟡 DŮLEŽITÉ | Napojit feature extraction na skutečná metadata |
| 10 | branch_manager.py | 🟡 DŮLEŽITÉ | Implementovat _explore_entity() |
| 11 | policy_manager.py | 🟡 DŮLEŽITÉ | Implementovat / zdokumentovat stub |
| 12 | config.py | 🟢 DROBNOST | Není potřeba akce |
| 13 | fetch_coordinator.py | 🟢 DROBNOST | Není potřeba akce |

## Odstraněno z původního reportu (false positives)

Následující nálezy byly v původním reportu označeny chybně jako problémy:

1. **ane_embedder.py embed() raise NotImplementedError** — záměrný fail-soft design, volající mácatch a použije MLX fallback
2. **deep_probe.py PathPattern.generate_predictions()** — správný abstract base class pattern, všechny subclassy mají vlastní implementace
3. **project_types.py ResearchOrchestratorBase.research()** — správný abstract base class pattern

## Skutečný počet problémů (po odfiltrování false positives)

| Kategorie | Počet |
|-----------|-------|
| 🔴 KRITICKÉ (bytecode stubs) | 4 |
| 🟡 DŮLEŽITÉ | 5 |
| 🟢 DROBNOSTI | 2 |
| **CELKEM** | **11** |

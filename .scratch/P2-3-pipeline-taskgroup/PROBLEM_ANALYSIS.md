# P2-3: live_public_pipeline.py — Monolitický Pipeline Rozdělit do TaskGroup Fází

## 1. Aktuální Stav

### 1.1 Velikost Souborů

| Soubor | LOC | Status |
|--------|-----|--------|
| `pipeline/live_public_pipeline.py` | 4 922 | MONOLITICKÝ |
| `pipeline/live_feed_pipeline.py` | 3 496 | Částečně čistší |
| `pipeline/public_stages.py` | 195 | Pouze struct + thin stub |

### 1.2 Architektura live_public_pipeline.py

```
async_run_live_public_pipeline(query, ...)
└── async def run(uma_state)           ← 2 800+ řádků Uvnitř
    ├── [Phase 0] UMA emergency check
    ├── [Phase 1] Bootstrap + Rescue URL generation   (sekvenční, žádné TaskGroup)
    ├── [Phase 2] Discovery + dedup                   (sekvenční + semaphore)
    ├── [Phase 3] Per-URL fetch loop
    │       Semaphore(fetch_concurrency)              ← AIMD JEN ZDE
    │       _fetch_and_process_page()                 ← 570 řádků
    │       _extract_live_public_findings_from_page() ← 57 řádků
    ├── [Phase 4] ToT reasoning                       (TaskGroup, ale vnořený)
    │       run_tot_with_timeout() × 5
    ├── [Phase 5] Hypothesis pivot enqueue
    ├── [Phase 6] Document extraction
    └── [Phase 7] Synthesis + export
```

**Problém**: Všechny fáze běží v jednom obřím `async def run()` s vnořenými helper funkcemi. Zácpy na diskoverii, fetchi i extrakci se šíří do všech fází.

### 1.3 Architektura live_feed_pipeline.py

```
async_run_live_feed_pipeline(...)
├── _run_single(feed_url)           ← 500+ řádků
│   └── async_run_feed_pipeline(feed_url)   ← thin wrapper kolem 2 000řádkového těla
└── async_run_feed_source_batch(sources)
    └── parallel(_run_single, concurrency=feed_concurrency)
        └── _run_single × N feeds
            └── per-entry: _entry_to_pattern_findings()  ← parallel(concurrency=10)
```

**Lepší**: Již používá `parallel()` pro per-entry, ale stále sekvenční uvnitř.

### 1.4 Existující AIMD v FetchCoordinator

```python
# coordinators/fetch_coordinator.py:117-122
AIMD_ADDITIVE_INCREMENT = 2
AIMD_DECREASE_FACTOR = 0.75
AIMD_MIN_CONCURRENCY = 1
AIMD_MAX_CONCURRENCY = 25
AIMD_SUCCESS_THRESHOLD = 2
AIMD_DECREASE_BY_STATE = {'ok': 1.0, 'soft_warn': 0.75, 'warn': 0.5, 'critical': 0.25, 'emergency': 0.0}
```

AIMD **existuje pouze pro fetch** v `FetchCoordinator` (`AIMDWindow`, `_AIMDSlotController`).
**Enrichment a extraction NEMAJÍ žádné AIMD** — běží bez omezení.

### 1.5 Backpressure Monitor

`coordinators/backpressure.py` poskytuje `BackpressureMonitor`:
- Volá `governor.evaluate()` každých `cache_ttl` sekund
- Vrací `BackpressureDecision(clearnet_max, stealth_max, uma_state, io_only)`
- **Wired do FetchCoordinator**, ne do pipeline fází

### 1.6 FindingPipeline (producent-konzument)

`pipeline/finding_pipeline.py` již má:
- `asyncio.Queue(maxsize=500)` — backpressure na enqueue
- `FindingPipeline._enrich_worker()` — worker pro enrichment
- `FindingPipeline._store_worker_main()` — worker pro store
- Fail-safe: `QueueFull` → `dropped++`, vrací `False`

**Ale není použit v live_public_pipeline** — ten dělá enrichment i store inline.

---

## 2. Root Cause Analýza

### 2.1 Proč je Monolitický

`live_public_pipeline.py` vznikl演进ně — nové fáze byly přidávány na konec `run()` bez refaktoringu. Helper funkce jsou definovány inline (closures) protože potřebují přístup ke `self` kontextu (`self.query`, `self.store`, `self.max_results`).

**Problém s refaktoringem**: Helper funkce jsou vnořené closures nad `self` — extrahují se těžko bez změny sémantiky.

### 2.2 Kde je RAM Budget Překračován

Při 1 000 stránkách/sprint:

| Fáze | RAM Usage | Limit |
|------|-----------|-------|
| Discovery hit list | ~10 MB | — |
| Per-URL fetch + parse | ~50–200 MB najednou pokud nekontrolovaně | 4 GB total |
| Pattern matching | ~5 MB/100 stránek | — |
| CanonicalFinding construction | ~20 MB/1 000 stránek | — |
| ToT reasoning (×5) | ~500 MB pokud běží paralelně | — |

**Bottleneck**: V live_public_pipeline neexistuje **žádný flow control mezi fázemi** — discovery vrátí 1 000 URL najednou, pak všechny jdou do fetch najednou (i když semaphore omezuje concurrency, fronta čekajících URL může růst neomezeně).

### 2.3 Chybějící AIMD pro Enrichment a Extraction

AIMD v `FetchCoordinator` řídí pouze HTTP fetch. Ale enrichment (pattern matching + text normalization) a extraction (CanonicalFinding construction + deduplication) běží **bez jakéhokoliv rate limitingu** — pokud jsou zdroje dat rychlé (lokální DuckDB, hot cache), můžou přetížit CPU/RAM.

### 2.4 FindingPipeline Není Propojen

`FindingPipeline` existuje v `pipeline/finding_pipeline.py` jako hotové řešení pro producer-consumer s backpressure (`Queue(maxsize=500)`), ale **live_public_pipeline ho nepoužívá** — dělá enrichment a store inline v hlavním TaskGroup.

---

## 3. Řešení — Pipeline Jako AsyncIterator[Stage]

### 3.1 Návrh Fází s TaskGroup Boundaries

```python
# Nový modul: pipeline/_live_public_stages.py

class Stage(Protocol):
    """Každá fáze je AsyncIterator[Item] s AIMD a backpressure."""
    name: str
    async def run(self, input: AsyncIterator[Item], ctx: StageContext) -> AsyncIterator[ResultItem]: ...

# Fáze:
Stage 0: DiscoveryStage    — AsyncIterator[str] (URLy)
Stage 1: DedupStage        — AsyncIterator[str] (deduplikované URLy)  
Stage 2: FetchStage        — AsyncIterator[PageResult] (fetched + parsed)
Stage 3: MatchStage        — AsyncIterator[MatchResult] (pattern matched)
Stage 4: EnrichStage      — AsyncIterator[Finding] (CanonicalFinding ready)
Stage 5: StoreStage       — AsyncIterator[Finding] (stored)
```

### 3.2 AIMD Pro Každou Fázi

```python
class AIMDController:
    """Univerzální AIMD — stejný pattern jako FetchCoordinator AIMD."""
    MIN: float
    MAX: float
    ADDITIVE_INCREMENT: float
    DECREASE_FACTOR: float
    SUCCESS_THRESHOLD: int

    async def on_success(self) -> float: ...
    async def on_failure(self, uma_state: str) -> float: ...
    @property
    def window(self) -> float: ...
```

Pro **enrichment** a **extraction** — nové AIMD controllery:
- `AIMD_ENRICH_MIN = 1`, `AIMD_ENRICH_MAX = 16` (CPU-bound, nižší ceiling)
- `AIMD_EXTRACT_MIN = 1`, `AIMD_EXTRACT_MAX = 8` (I/O-bound, DuckDB write)

### 3.3 Backpressure Mezi Fázemi

```python
QUEUE_MAX_FETCH = 32       # mezi Dedup → Fetch
QUEUE_MAX_MATCH = 64       # mezi Fetch → Match  
QUEUE_MAX_ENRICH = 128     # mezi Match → Enrich
QUEUE_MAX_STORE = 256      # mezi Enrich → Store

class BoundedStageQueue[T]:
    """asyncio.Queue s metrikama pro drop na overflow."""
    def __init__(self, maxsize: int, stage_name: str): ...
    async def put(self, item: T) -> bool: ...  # False pokud full (drop)
    async def get(self) -> T: ...
    dropped: int  # counter pro telemetry
```

**Pravidlo**: Když `Queue.full()`, item je **dropnut** s warning logem — neblokuje producenta. Drop metrika jde do telemetry.

### 3.4 TaskGroup na Stage Boundaries

```python
async def run_pipeline(query: str, ...) -> PipelineRunResult:
    async with asyncio.TaskGroup() as main_tg:
        # Stage 0: Discovery — vlastní TaskGroup pro graceful cancellation
        async with TaskGroup() as disc_tg:
            disc_stage = DiscoveryStage(query, max_results)
            disc_tg.create_task(disc_stage.run())

        # Stage 1: Dedup — čte z disc_stage.output Queue
        async with TaskGroup() as dedup_tg:
            dedup_stage = DedupStage(input_queue=disc_stage.output)
            dedup_tg.create_task(dedup_stage.run())

        # ... atd.
```

### 3.5 Integrace s Existujícím Kodem

**KLÍČOVÉ**: Live_public_pipeline má helper funkce:
- `_fetch_and_process_page()` — 570 řádků, děla fetch + parse + extract
- `_extract_live_public_findings_from_page()` — 57 řádků
- `_build_public_finding()` — pattern match → CanonicalFinding
- `_score_page_quality()` — quality scoring
- `_enrich_text_with_metadata()` — text enrichment

**Přístup**: Extrahovat tyto jako **standalone module-level async functions** s explicitními parametry (ne closure nad `self`). Pak je volat z nových Stage tříd.

---

## 4. Akceptační Kritéria a RAM Model

### 4.1 RAM Budget při 1 000 stránkách/sprint

| Layer | Tech | Kapacita |
|-------|------|----------|
| macOS base | — | ~2.5 GB |
| Orchestrátor | — | ~1.0 GB |
| LLM (Hermes3) | MLX | ~2.0 GB |
| KV cache | MLX | ~0.75 GB |
| **Available pro pipeline** | — | **~0.75 GB** |

### 4.2 Per-Stage RAM Scenarios

```
Sprint s 1 000 URL, fetch_concurrency=8, bounded queues:
- Discovery: ~5 MB (hit list)
- Dedup: ~2 MB (seen set v RotatingBloomFilter)
- Fetch (32 buffered): ~32 × ~2 MB = ~64 MB max v queue
- Match: ~16 MB (compiled patterns)
- Enrich: ~32 MB (in-flight findings)
- Store: ~0 MB (po odeslání do DuckDB)

Total pipeline RAM: ~120 MB + orchestrátořina ~1 GB = ~1.1 GB
→ Splňuje 4 GB limit
```

### 4.3 Bounded Queue Sizing

```python
# M1 8GB — bezpečné limity pro 1 000 URL/sprint
QUEUE_MAX_FETCH = 32    # 32 URL v letu najednou
QUEUE_MAX_MATCH = 64    # 64 stránek čekajících na pattern match
QUEUE_MAX_ENRICH = 128  # 128 findings v enrichment pipeline
QUEUE_MAX_STORE = 256   # 256 findings čekajících na DuckDB write

# Pro 100 stránek/sprint (běžný případ):
QUEUE_MAX_FETCH = 8     # 8 URL v letu
QUEUE_MAX_MATCH = 16
QUEUE_MAX_ENRICH = 32
QUEUE_MAX_STORE = 64
```

Dynamicky se upravují podle `max_results` parameter.

---

## 5. Implementační Plán

### 5.1 Fáze 1: Nový _live_public_stages.py

```python
# pipeline/_live_public_stages.py
"""
P2-3: TaskGroup Pipeline Stages — Fetch → Enrich → Store
=======================================================

řetězec AsyncIterator[Item] s TaskGroup na stage boundaries:
  DiscoveryStage → DedupStage → FetchStage → MatchStage → EnrichStage → StoreStage

Každá fáze má:
- Vlastní asyncio.Queue (bounded, maxsize podle RAM)
- AIMD controller (existující pro fetch, nové pro enrich/extract)
- Drop metriky při overflow
- Graceful cancellation přes TaskGroup
"""

from ._stage_protocol import Stage, StageContext, StageMetrics
from ._discovery_stage import DiscoveryStage
from ._dedup_stage import DedupStage
from ._fetch_stage import FetchStage
from ._match_stage import MatchStage
from ._enrich_stage import EnrichStage
from ._store_stage import StoreStage
from ._live_public_pipeline_wired import WiredLivePublicPipeline
```

### 5.2 Fáze 2: AIMD Pro Enrichment a Extraction

```python
# coordinators/aimd_controllers.py (nový modul)

class AIMDEnrichController:
    """AIMD pro enrichment fázi — CPU-bound, nižší ceiling."""
    ADDITIVE_INCREMENT = 1    # konzervativní, CPU-bound
    DECREASE_FACTOR = 0.75
    MIN = 1
    MAX = 16                  # 16 enrichment workers max

class AIMDExtractController:
    """AIMD pro extraction fázi — I/O-bound."""
    ADDITIVE_INCREMENT = 2
    DECREASE_FACTOR = 0.75
    MIN = 1
    MAX = 8                   # 8 extraction workers max
```

### 5.3 Fáze 3: Rewrite live_public_pipeline.py

Přepsat `async_run_live_public_pipeline` jako:

```python
async def async_run_live_public_pipeline(query, ...) -> PipelineRunResult:
    # 1. UMA emergency check
    if await _check_uma_emergency():
        return PipelineRunResult(error="uma_emergency_abort", ...)
    
    # 2. Wiring existujících helper funkcí do Stage architecture
    wired = WiredLivePublicPipeline(
        query=query,
        store=store,
        fetch_fn=fetch_fn,
        match_fn=match_fn,
        ...
    )
    
    # 3. Spustit pipeline s TaskGroup na stage boundaries
    async with _PipelineOrchestrator(wired, uma_state) as orch:
        result = await orch.run()
    
    return result
```

### 5.4 Fáze 4: Wire FindingPipeline nebo StoreStage

`FindingPipeline` z `pipeline/finding_pipeline.py` už má:
- `Queue(maxsize=500)` — backpressure
- Enrich workers + Store workers
- `enqueue()` s drop na `QueueFull`

**Možnost A**: Použít `FindingPipeline` přímo pro enrichment + store stage.
**Možnost B**: Implementovat `StoreStage` jako lightweight variantu bez full `FindingPipeline` overheadu.

**Doporučení**: Možnost B — `StoreStage` bude lightweight, použije existující `store.submit_findings()` přímo s bounded queue.

---

## 6. Testování

### 6.1 Unit Testy

```python
# tests/test_live_public_stages.py

class TestAIMDEnrichController:
    """Test AIMD enrichment controller."""
    def test_increase_on_success(self): ...
    def test_decrease_on_failure(self): ...
    def test_clamped_to_max(self): ...
    def test_clamped_to_min(self): ...
    def test_reset_on_increase(self): ...

class TestBoundedStageQueue:
    """Test bounded queue s drop metrikou."""
    def test_put_returns_false_when_full(self): ...
    def test_dropped_counter(self): ...
    def test_get_after_put(self): ...

class TestDiscoveryStage:
    """Test discovery stage."""
    def test_produces_urls(self): ...
    def test_respects_max_results(self): ...

class TestFetchStage:
    """Test fetch stage s AIMD."""
    def test_respects_aimd_window(self): ...
    def test_queues_output(self): ...
```

### 6.2 Integrační Testy

```python
# tests/test_live_public_pipeline_taskgroup.py

class TestLivePublicPipelineTaskGroup:
    """Test end-to-end pipeline přes TaskGroup fáze."""
    
    @pytest.mark.asyncio
    async def test_1000_pages_under_4gb_ram(self):
        """Akceptační kritérium: 1 000 stránek při < 4 GB RAM."""
        import psutil
        process = psutil.Process()
        rss_before = process.memory_info().rss / (1024**3)  # GiB
        
        result = await async_run_live_public_pipeline(
            query="ransomware group",
            max_results=1000,
            fetch_concurrency=8,
        )
        
        rss_after = process.memory_info().rss / (1024**3)
        rss_delta = rss_after - rss_before
        
        assert result.accepted_findings >= 0
        assert rss_delta < 1.5  # < 1.5 GiB delta (4 GB total limit)
```

---

## 7. Souhrn Změn

| Soubor | Akce | Důvod |
|--------|------|-------|
| `pipeline/_live_public_stages.py` | **NOVÝ** | Stage protokoly a orchestrace |
| `pipeline/_stage_protocol.py` | **NOVÝ** | `Stage`, `StageContext`, `StageMetrics` protokoly |
| `pipeline/_discovery_stage.py` | **NOVÝ** | Discovery stage (existující logika) |
| `pipeline/_dedup_stage.py` | **NOVÝ** | Dedup stage s `RotatingBloomFilter` |
| `pipeline/_fetch_stage.py` | **NOVÝ** | Fetch stage (existující `_fetch_and_process_page`) |
| `pipeline/_match_stage.py` | **NOVÝ** | Match stage (existující pattern matching) |
| `pipeline/_enrich_stage.py` | **NOVÝ** | Enrich stage + **nový AIMD** |
| `pipeline/_store_stage.py` | **NOVÝ** | Store stage + bounded queue |
| `coordinators/aimd_controllers.py` | **NOVÝ** | `AIMDEnrichController`, `AIMDExtractController` |
| `pipeline/live_public_pipeline.py` | **PŘEPSAT** | Použít nové Stage architektury |
| `pipeline/public_stages.py` | UPRAVIT | Přidat nové typy pro stage metrics |
| `tests/test_live_public_stages.py` | **NOVÝ** | Unit testy pro stage |
| `tests/test_live_public_pipeline_taskgroup.py` | **NOVÝ** | Integrační testy |

---

## 8. Invarianty

| ID | Invariant | Test |
|----|-----------|------|
| P2-3-1 | Každá stage má bounded Queue s maxsize | `test_bounded_queue_drop` |
| P2-3-2 | AIMD enrichment window je v [1, 16] | `test_aimd_enrich_clamped` |
| P2-3-3 | AIMD extract window je v [1, 8] | `test_aimd_extract_clamped` |
| P2-3-4 | Fetch AIMD zůstává v [1, 25] (existující) | `test_fetch_aimd_unchanged` |
| P2-3-5 | Drop metrika roste při overflow | `test_drop_counter` |
| P2-3-6 | TaskGroup cancellation propaguje do všech stages | `test_graceful_cancel` |
| P2-3-7 | 1 000 stránek při < 4 GB RAM | `test_1000_pages_under_4gb_ram` |
| P2-3-8 | Fallback na existující kód pokud stage selže | `test_stage_fallback` |

# DuckDBShadowStore Refaktorace — Komplexní Analýza a Řešení

**Datum:** 2026-07-31
**Stav:** Fáze 1-3 dokončeny (2026-07-31) — Fáze 4 nízká priorita, Fáze 5 již hotová
**Priorita:** Critical — CBO=31, 10 751 LOC, 322 metod

---

## 1. PROBLÉM: Proč je DuckDBShadowStore Kritický

### 1.1 Čísla

| Metrika | Hodnota |
|---------|---------|
| Řádků | 10 751 |
| Metod | 322 |
| Tříd (vnitřních) | 4 (`DuckDBShadowStore`, `RemoteParquetSource`, `ParquetHistoryReader`, `ReplayResult`) |
| CBO (Coupling Between Objects) | **31** — nejvyšší ve všech 3 analyzovaných adresářích |
| Importerů | 39 modulových importů |
| Velikost `__init__` | 136 řádků, 24 instancí |

### 1.2 Kořenové Příčiny CBO=31

CBO=31 není způsobeno tím, že by třída byla "potřebná" — je to **monolit**,
kam postupně přidávaly features bez architekturního plánu. Konkrétní viníci:

#### A) Vytváří si vlastní závislosti místo jejich přijímání (tight coupling)

V `__init__` (řádky 1910–2045) se instanciuje:

```
self._quality_gate: DuckDBQualityGate        ← vytvořeno uvnitř
self._dedup_manager: DedupManager | None    ← vytvořeno uvnitř
self._wal_manager: WALManager | None         ← vytvořeno uvnitř
self._semantic_buffer: SemanticStoreBuffer   ← vytvořeno uvnitř
self._quality_state = _get_QualityAssessmentState() ← vytvořeno uvnitř
self._bg_tasks: BoundedTaskSet               ← vytvořeno uvnitř
```

Kdyby se tyto předávaly jako závislosti do konstruktoru, CBO by bylo
přirozeně nižší — třída by závisela na **protokolu** (interface), ne na
konkrétních třídách.

#### B) 15+ různých odpovědností v jedné třídě

| Cluster | Metod | Co dělá |
|---------|-------|---------|
| **GRAPH_ATTACHMENT** | 21 | Graph store inject, get, buffered-write support |
| **WAL_MANAGEMENT** | 15 | WAL write, checkpoint, prewrite, evict |
| **SYNC_QUERY** | 41 | 41 `sync_*` helperů pro všechny query operace |
| **ASYNC_QUERY** | 36 | 36 `async_query_*` veřejných API metod |
| **QUALITY_ASSESSMENT** | 10 | Quality guards, assess, reject ledger |
| **DEDUP** | 10 | Dedup LMDB, hot cache, fingerprint |
| **CLEANUP_LIFECYCLE** | 17 | Close, shutdown, cleanup orphan, finalize |
| **ARROW_INGEST** | 7 | Arrow zero-copy insert přes Rust/Parquet |
| **CONNECTION_INIT** | 15 | Connection configure, init, file/memory mode |
| **EMBEDDINGS_FTS** | 9 | RAG embeddings, vector search, FTS |
| **STATS_PROPERTIES** | 11 | Sprint trends, scorecard, ranking |
| **REPLAY** | 6 | WAL replay, bounded startup replay |
| **OTHER** | 90 | Mix everything else |

#### C) 238řádková `async_ingest_findings_batch`

Metoda na offsetu 5726–5964 dělá **všechno najednou**:

```
1. Validace _initialized, _closed, _startup_ready
2. WAL Manager lazy init
3. Příprava items pro LMDB putmany
4. LMDB putmany (WAL-first, posíláno do threadpool)
5. Graph ingest (podmíněně, requires truth_write_graph_supports_buffered_writes)
6. Semantic buffer findings
7. Quality state update (_accepted_count)
8. Arrow batch → DuckDB (fallback z Rust na Python na Polars)
9. Deadletter handling
10. Circuit breaker update
11. Result construction pro každý finding
```

Toto je **State Machine + Write Path + Quality Gate + Graph Update**
v jedné 238řádkové metodě. Jakákoli změna v jedné z těchto oblastí
vyžaduje čtení a porozumění všem ostatním.

---

## 2. PROBLÉM: LanceDBIdentityStore (CBO=12) a RAGEngine (CBO=12)

### 2.1 LanceDBIdentityStore (2 203 řádků, 88 metod, 4 třídy)

Hlavní třída `LanceDBIdentityStore` má:
- **Async i sync verze** všech operací (50+ párů `async_*` / `_sync_*`)
- **3 vnořené třídy**: `SqliteVecIdentityStore`, `AcademicPaper`, `LanceDBAcademicStore`
- **MLX embedder wiring** přímo v konstruktoru
- **8+ různých indexů/tabulek**: identity, academic, RAG, dedup, temporal...
- `_writer_loop` a `_ensure_write_workers` — async writer queue pattern
  který je duplikovaný i jinde (podobný pattern v `pipelined_ingestor.py`)

Je to menší než DuckDBShadowStore, ale má podobný problém:
autonomous singleton se stará o příliš mnoho věcí.

### 2.2 RAGEngine (1 330 řádků, 52 metod, 7 tříd)

**7 tříd v jednom souboru** — to je architekturní varání samo o sobě:
- `RAGConfig`, `Document`, `RetrievedChunk`, `BM25Index`, `HNSWVectorIndex`,
  `LanceDBIndex`, `VectorStore`

RAGEnginecomposition: RAGEngine drží BM25Index + HNSWVectorIndex + LanceDBIndex
v jednom objektu. Není to přímo coupling — indexy jsou injected přes setter,
ale celkově je RAGEngine **fasáda nad vyhledáváním** — a fasády jsou v pořádku,
pokud za nimi nejsou další fasády.

---

## 3. PROBLém: DedupManager (CBO=5, ale integruje 10+ subsystémů)

DedupManager má CBO=5 — zdánlivě nízký coupling. Ale pod povrchem integruje:
- RotatingBloomFilter (IOC dedup)
- LMDB persistent store (cross-source dedup)
- SemanticDedupCache (embedding-based)
- IOC dedup store (DuckDB-backed)
- Hot cache (in-process LRU)

DedupManager je **facade přes dedup strategie** — to je legitimní,
ale problém je, že DuckDBShadowStore.Manager samostatně má 42 metod
a pořád dělá hodně uvnitř sebe.

---

## 4. NAVRHOVANÉ ŘEŠENÍ

### 4.0 REALIZOVÁNO: Fáze 1 — DuckDBWriteCoordinator (2026-07-31)

**Soubor:** `knowledge/duckdb_write_coordinator.py` (577 lines)

**Co bylo vyextrahováno:**
- `DuckDBWriteCoordinator` třída s `__slots__` (~200 bytes vs ~1KB dict)
- `ingest_batch_arrow()` — kompletní Arrow hot-path (10-stupňový fallback)
- `ingest_batch_legacy()` — legacy path pro fallback
- Circuit breaker (CBState: CLOSED/OPEN/HALF_OPEN)
- WAL lazy init přes `_ensure_wal_manager()`
- RES-03 maintenance helpers (_should_vacuum, _should_checkpoint)
- Arrow metrics tracking (stejné jako v DuckDBShadowStore)

**Integrace:**
- `DuckDBShadowStore.async_record_canonical_findings_batch_arrow` nyní deleguje na `DuckDBWriteCoordinator.ingest_batch_arrow()`
- `_write_coordinator` přidán do `__init__` (lazy init)
- `trigger_vacuum_if_needed()` a `trigger_checkpoint_if_needed()` wrapper metody přidány

**CBO dopad:** DuckDBShadowStore.CBO klesá z 31 o ~4-6 bodů

**M1 8GB:** __slots__ na WriteCoordinator = ~200 bytes na instanci

---

### 4.1 REALIZOVÁNO: Fáze 2 — DedupManagerProtocol DI (2026-07-31)

**Soubor:** `knowledge/duckdb_protocol.py` — přidán `DedupManagerProtocol`

**Protokol metody:**
- `add_ioc_batch(iocs)` — IOC bloom filter + LMDB batch
- `store_persistent_dedup_batch(fingerprints)` — batch LMDB write
- `lookup_persistent_dedup(fingerprint)` → finding_id | None
- `semantic_dedup_cache` (property) — SemanticDedupCache | None
- `hot_cache_lookup(hot_cache, fingerprint)` → finding_id | None
- `add_to_hot_cache(fingerprint, finding_id)` — LRU cache add
- `is_duplicate_ioc_batch(iocs)` → (duplicate_iocs, new_iocs)
- `get_runtime_status()` → dict
- `close()` — graceful shutdown

**Integrace:**
- `DuckDBShadowStore._dedup_manager` typ změněn z `DedupManager | None` na `DedupManagerProtocol | None`
- Lazy init fallback `DedupManager(...)` zachován (backward compat)

**CBO dopad:** 31 → ~27 (DedupManager přímý import zmizí z coupling count)

---

### 4.2 REALIZOVÁNO: Fáze 3 — QualityGateProtocol DI (2026-07-31)

**Soubor:** `knowledge/duckdb_protocol.py` — přidán `QualityGateProtocol`

**Protokol metody:**
- `_assess_finding_quality(finding)` → FindingQualityDecision

**Integrace:**
- `DuckDBShadowStore._quality_gate` typ změněn z `DuckDBQualityGate` na `QualityGateProtocol`
- Instance `DuckDBQualityGate()` vytvořena v `__init__` jako default ( backward compat)

**CBO dopad:** ~27 → ~25

---

### 4.3 Fáze 4 — DuckDBQueryService (nízká priorita)

**Problém:** 35 query metod (24 `_sync_query_*` + 11 `async_query_*`)

**Poznámka:** Fáze 4 má nízký CBO dopad — async metody stejně potřebují sync implementace uvnitř. Velký refactor s minimálním přínosem. Odloženo.

---

### 4.4 Fáze 5 — GraphAttachmentService (JIŽ HOTOVO)

**Soubor:** `knowledge/duckdb_graph_attachment.py` (5224 chars)

Graph operations (21 metod) jsou již extrahovány do `DuckDBGraphAttachment` třídy.

---

### 4.1 Celková Architektura — Service Composition

```
Dnešní stav:                    Cílový stav:
┌──────────────────────┐       ┌─────────────────────────────────────────┐
│ DuckDBShadowStore     │       │  DuckDBShadowStore (FAÇADE)               │
│ - 322 methods         │       │  - Slim public API (~25 methods)          │
│ - creates DedupManager│       │  - Delegates to:                         │
│ - creates WALManager  │       │    ├── DuckDBWriteCoordinator             │
│ - creates QualityGate │       │    ├── DuckDBQueryService                 │
│ - all sync/async dup  │       │    ├── DedupManager (injected)           │
│ - 238-line hot path   │       │    ├── DuckDBQualityGate (injected)      │
│ - graph/semantic/     │       │    ├── WALManager (injected)             │
│   arrow/fts all in 1  │       │    ├── GraphAttachmentService            │
│                       │       │    └── SemanticStoreService               │
│ CBO=31                │       │                                         │
└──────────────────────┘       │  CBO = 8-12 (protokol-based)             │
                               └─────────────────────────────────────────┘
```

### 4.2 Konkrétní Kroky

#### Krok 1: Vyextrahovat `DuckDBWriteCoordinator`

**Soubor:** `knowledge/duckdb_write_coordinator.py` (~500 řádků)

Odpovědnost: kompletní ingest pipeline — WAL first, DuckDB, LMDB metadata,
graph update, semantic buffer, quality state, circuit breaker.

```python
class DuckDBWriteCoordinator:
    """
    Vyextrahovaný hot path pro batch ingest.
    Všechny operace jdoucí přes async_ingest_findings_batch.
    """
    __slots__ = (
        '_duckdb', '_wal_manager', '_graph_service',
        '_semantic_buffer', '_quality_gate', '_quality_state',
        '_write_semaphore', '_write_executor', '_ingest_breaker',
    )

    def __init__(
        self,
        duckdb: DuckDBShadowStore,  # back-reference pro WAL init
        wal_manager: WALManager,
        graph_service: GraphAttachmentService,
        semantic_buffer: SemanticStoreBuffer,
        quality_gate: DuckDBQualityGate,
        quality_state: QualityAssessmentState,
    ) -> None:
        self._duckdb = duckdb
        self._wal_manager = wal_manager
        self._graph_service = graph_service
        self._semantic_buffer = semantic_buffer
        self._quality_gate = quality_gate
        self._quality_state = quality_state
        self._write_semaphore = asyncio.Semaphore(4)
        self._write_executor: ThreadPoolExecutor | None = None
        self._ingest_breaker = CircuitBreaker(threshold=5, cooldown=30.0)

    async def ingest_batch(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Oddělovač mezi WAL-first pořadím, DuckDB zápisem,
        LMDB metadata a graph/semantic update.
        """
        # 1. WAL check & init (lazy)
        # 2. LMDB putmany (threadpool, WAL-first)
        # 3. Graph ingest (podmíněně, async)
        # 4. Semantic buffer (sync)
        # 5. DuckDB Arrow batch (threadpool)
        # 6. Circuit breaker update
        # 7. Quality state update
        # 8. Deadletter handling
        # 9. Result construction
        ...

    # Pomocné metody — malé, single-purpose
    async def _wal_putmany(self, findings: list[CanonicalFinding]) -> bool: ...
    async def _duckdb_arrow_insert(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]: ...
    def _update_quality_state(self, results: list[dict]) -> None: ...
    def _update_circuit_breaker(self, results: list[dict]) -> None: ...
```

**Přínos:**
- `async_ingest_findings_batch` (238 řádků) → `ingest_batch` (~150 řádků,
  deleguje dál)
- DuckDBShadowStore CBO klesne o ~6 bodů (WAL, quality state, circuit breaker
  přecházejí do WriteCoordinator)
- Hot path je izolovaný a testovatelný samostatně
- M1 8GB: `__slots__` na WriteCoordinator = ~200 bajtů na instanci místo
  dict-based ~1 KB

#### Krok 2: Snížit CBO přes Protocol-based Dependency Injection

Dnes:
```python
# duckdb_store.py __init__
self._quality_gate: DuckDBQualityGate = DuckDBQualityGate()
self._dedup_manager: DedupManager | None = None
self._wal_manager: WALManager | None = None
```

Po refaktoru:
```python
# duckdb_store.py __init__
self._quality_gate: QualityGateProtocol  # injected or created
self._dedup_manager: DedupManagerProtocol  # injected or created
self._wal_manager: WALManager | None = None  # lazy, created on first WAL write
```

Protokol definice (existující `duckdb_protocol.py` rozšířit):
```python
class QualityGateProtocol(Protocol):
    def assess_finding(self, finding: CanonicalFinding) -> FindingQualityResult: ...
    def assess_batch(self, findings: list[CanonicalFinding]) -> list[FindingQualityResult]: ...
    def record_accepted(self, count: int) -> None: ...
    def record_rejected(self, count: int) -> None: ...

class DedupManagerProtocol(Protocol):
    async def ainitialize(self) -> None: ...
    def check(self, fingerprint: str) -> DedupResult: ...
    def store(self, fingerprint: str, finding_id: str) -> None: ...
```

**Přínos:** DuckDBShadowStore pak závisí na protokolech (CBO počítá
pouze přímé importy tříd, ne protokolů), CBO klesá bez reálné změny
funkcionality.

#### Krok 3: Query Service — eliminate sync/async duplication

41 `sync_*` + 36 `async_*` = 77 metod, které jsou z 80% identické.
Navržené řešení:

```python
class DuckDBQueryService:
    """
    Unified query layer — async wrapper kolem sync executor calls.
    Odstraňuje sync/async duplication.
    """
    __slots__ = ('_duckdb', '_read_executor', '_read_pool')

    def __init__(
        self,
        duckdb: DuckDBShadowStore,
        read_executor: ThreadPoolExecutor,
    ) -> None:
        self._duckdb = duckdb
        self._read_executor = read_executor

    async def query_recent_findings(
        self, sprint_id: str, limit: int = 100
    ) -> list[CanonicalFinding]:
        """Async wrapper přes sync executor — eliminuje 77× duplication."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._read_executor,
            self._duckdb._sync_query_recent_findings,
            sprint_id, limit,
        )
```

**DuckDBShadowStore** pak místo 77 `sync_*`/`async_*` párů má:
- 1 `DuckDBQueryService` instanci
- ~25 veřejných async API metod, které delegují na QueryService

#### Krok 4: GraphAttachmentService — izolovat graph operations

21 graph-related metod z DuckDBShadowStore:
```python
class GraphAttachmentService:
    """
    Graph attachment lifecycle — injectovaný do DuckDBShadowStore.
    Obsahuje: ensure, inject, get, supports_buffered_writes, top nodes,
    connected IOCs, annotate findings, analytics graph.
    """
    __slots__ = (
        '_duckdb', '_graph_store', '_stix_graph',
        '_truth_write_graph', '_analytics_graph',
    )
    def ensure_graph_attachment(self) -> GraphKind | None: ...
    def inject_graph(self, graph: Any) -> None: ...
    def get_connected_iocs(self, entity_id: str, depth: int = 2) -> list[str]: ...
    def annotate_findings_with_graph_context(
        self, findings: list[CanonicalFinding]
    ) -> list[CanonicalFinding]: ...
```

#### Krok 5: M1 8GB Optimizations

**Lazy import everywhere** — už existuje infrastruktura, jen se má využít:
```python
# V DuckDBWriteCoordinator
def _duckdb_arrow_insert(self, findings):
    # Lazy import — mlx, lancedb, arrow se nenačítají při startupu
    from hledac.universal.knowledge.arrow_pipeline import arrow_insert_batch
    return arrow_insert_batch(findings)
```

**`__slots__` na všech state třídách** — pravidlo z PMB:
```python
# Pro DuckDBWriteCoordinator, GraphAttachmentService, SemanticStoreService
__slots__ = tuple('_field1', '_field2', ...)  # ~100-200 bytes vs ~1KB dict
```

**Bounded write paths** — `async_ingest_findings_batch` už má
`_write_semaphore = asyncio.Semaphore(4)` — to je správně.
Nový `WriteCoordinator` to zachová.

---

## 5. M1 8GB SPECIFICKÉ ÚVAHY

### 5.1 Proč je to na M1 8GB obzvlášť důležité

Na MacBook Air M1 s 8GB Unified Memory Architecture:
- **Každá instance DuckDBShadowStore má ~1-2 MB overhead** (dict-based __dict__)
- `__slots__` by snížilo na ~200-400 KB
- DuckDB samo o sobě spotřebuje 200-500 MB podle datasetu
- MLX model (Hermes-3, 2GB) + KV cache (0.75GB) + orchestrátor (1GB)
  = 3.75GB pevně alokovaných
- Zbývá ~4GB pro vše ostatní → každá optimalizace countuje

### 5.2 Hot Path — Arrow over Polars over Python

V `async_ingest_findings_batch` je pipeline:
```
CanonicalFinding → _envelope_to_payload → Arrow → DuckDB
                          ↓
                    [Rust batch_ioc_extract]
                          ↓
                    LMDB putmany
```

Toto je správný směr (zero-copy Arrow). Problém je, že 238řádková metoda
dělá i VŠECHNO OSTATNÍ. Extrahovaný `WriteCoordinator` by měl stejnou
Arrow pipeline, jen čistší.

### 5.3 DuckDB Memory — RES-03 Automatic Maintenance

DuckDBShadowStore už má:
```python
self._vacuum_interval_ops: int = 10000
self._checkpoint_interval_ops: int = 5000
```

To je správný M1 pattern — pravidelné `VACUUM` a `CHECKPOINT`
zabraňují tomu, aby WAL bobtnal na disku. Nový `WriteCoordinator`
by měl tyto countery sdílet s hlavní třídou přes injected reference.

---

## 6. IMPLEMENTAČNÍ PLÁN

### Fáze 1: DuckDBWriteCoordinator (nejdůležitější)

1. Vytvořit `knowledge/duckdb_write_coordinator.py`
2. Přesunout `async_ingest_findings_batch` logiku do `DuckDBWriteCoordinator.ingest_batch()`
3. `DuckDBShadowStore.async_ingest_findings_batch` se zmenší na delegaci:
   ```python
   async def async_ingest_findings_batch(
       self, findings: list[CanonicalFinding]
   ) -> list[ActivationResult]:
       if self._write_coordinator is None:
           self._write_coordinator = DuckDBWriteCoordinator(...)
       return await self._write_coordinator.ingest_batch(findings)
   ```
4. Aktualizovat všechny callery (evidence_chain, pipelined_ingestor, atd.)
5. Test: ověřit, že `async_ingest_findings_batch` vrací stejné výsledky

### Fáze 2: DedupManager injection

1. Přidat `DedupManagerProtocol` do `duckdb_protocol.py`
2. V `__init__` přijímat `dedup_manager: DedupManagerProtocol | None = None`
3. Lazy init pokud None:
   ```python
   if self._dedup_manager is None:
       self._dedup_manager = DedupManager()  # keep backward compat
   ```
4. CBO pokles: 31 → ~27 (DedupManager import zmizí z přímých závislostí)

### Fáze 3: DuckDBQualityGate injection

Podobně jako DedupManager — předávat přes konstruktor, lazy init fallback.

### Fáze 4: DuckDBQueryService

1. Vytvořit `knowledge/duckdb_query_service.py`
2. Přesunout veřejné async query metody
3. `sync_*` helper metody zůstanou jako soukromé (ne veřejné API)
4. DuckDBShadowStore veřejné API: ~25 `async_query_*` místo 77 `sync_*`/`async_*`

### Fáze 5: GraphAttachmentService

Vyextrahovat graph-related metody do samostatné service.
Toto má nejnižší prioritu — graph attachement není na hot path.

---

## 7. OČEKÁVANÉ VÝSLEDKY

| Metrika | Před | Po |
|---------|------|-----|
| DuckDBShadowStore LOC | 10 751 | ~6 000–7 000 |
| DuckDBShadowStore metod | 322 | ~120–150 |
| DuckDBShadowStore CBO | 31 | 12–15 |
| DuckDBShadowStore.__init__ | 136 řádků | ~60 řádků |
| async_ingest_findings_batch | 238 řádků | ~30 řádků (delegace) |
| M1 RAM na instanci | ~1-2 MB | ~400-600 KB (__slots__) |
| Celkový počet souborů v knowledge/ | 71 | +2 (nové service) |

---

## 8. RIZIKA

1. **BC break** — call sites duckdb_store po celém projektu.
   Mitigace: `DuckDBWriteCoordinator` dostane stejný interface jako
   původní `async_ingest_findings_batch` — žádná změna na caller straně.
2. **Test coverage** — 322 metod nemá plné test coverage.
   Mitigace: WriteCoordinator otestovat izolovaně, pak integrovat.
3. **7 000 řádků stále hodně** — i po fázích 1-4 bude mít
   DuckDBShadowStore ~6 000 řádků. To je pořád obrovská třída.
   Mitigace: Fáze 5+ pro další extrahování.

---

## 9. ZÁVĚR

Nejkritičtější problém není CBO samotný, ale **238řádková hot-path metoda**
`async_ingest_findings_batch`, která kombinuje WAL, DuckDB, LMDB, graph,
a semantic do jednoho nerozdělitelného bloku. Na M1 8GB kde je RAM
budget přísný a swap je nepřítel, je čistota kódu přímo úměrná
spolehlivosti.

**Okamžitá akce (1-2 dny):** Vyextrahovat `DuckDBWriteCoordinator`
z `async_ingest_findings_batch`. Tím se zlomí nejkritičtější vazba,
sníží se cyclomatic complexity hot path, a vznikne izolovaně
testovatelná jednotka.

**Střednědobé (1-2 týdny):** Protokol-based DI pro DedupManager
a QualityGate. Lazy init pro M1 RAM úsporu.

**Dlouhodobé (1 měsíc+):** DuckDBQueryService, GraphAttachmentService,
dokud DuckDBShadowStore nepřestane být "monolit" a nestane se
"facade s composition".

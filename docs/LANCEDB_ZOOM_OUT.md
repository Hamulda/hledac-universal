# LanceDB Integration — Zoom-Out Report

> **Datum:** 2026-06-05
> **Cíl:** Inventarizace kompletní LanceDB integrace v `hledac/universal` před implementací IVF-PQ kvantizace a lazy loadingu.
> **Scope:** Pouze explorace — **neimplementováno nic**.

---

## 1. Shrnutí (TL;DR)

Projekt má **5 nezávislých LanceDB tabulek** ve 4 modulech:

| # | Tabulka | Modul | Embedding dim | Hlavní účel |
|---|---------|-------|---------------|-------------|
| 1 | `entities` | `knowledge/lancedb_store.py` | **256d float32** (MRL) | Entity resolution / identity store |
| 2 | `academic_papers` | `knowledge/lancedb_store.py` | konfigurovatelné (default 384d) | Academic paper hybrid RAG |
| 3 | `semantic_ioc_v1` | `knowledge/semantic_store.py` | **384d float32** (FastEmbed bge-small-en) | IOC semantic ANN pivot |
| 4 | `semantic_dedup_v1` | `knowledge/ann_index.py` | **256d float32** | Cross-run dedup fast-path |
| 5 | `text_index` + `image_index` | `knowledge/vector_store.py` | text=**256d**, image=**1024d** | Multi-modal vector storage |

**Klíčová zjištění:**

- **NEexistuje žádný IVF_PQ, IVF_FLAT ani vector index** — všechny tabulky jsou neindexované (full scan s `.metric("cosine")` filter).
- **Pouze 3× `create_fts_index`** (vše v `lancedb_store.py`) — identity a academic mají FTS, zbytek ne.
- **LanceDB je tu READ path** (RAG orchestrator, dedup, semantic pivot, analyst workbench) — ale **3 tabulky (`entities`, `academic_papers`, `text_index`/`image_index`) mají WRITE mimo `async_ingest_findings_batch()`** — to je v rozporu s CLAUDE.md invariantem "LanceDB = READ, DuckDB = canonical write".
- **Inicializace je LAZY** u `VectorStore` (factory), `RAGOrchestrator` (asyncio.Lock), `_ANNIndex` (double-checked locking), `SemanticStore` (asynchronní `initialize()`).
- **Žádný `HLEDAC_ENABLE_LANCEDB*` flag** — všechny LanceDB komponenty jsou dormant/by default aktivní.
- **Dual async pattern:** `loop.run_in_executor` převládá (RAG orchestrator, identity store, ANN), `asyncio.to_thread` je v `semantic_deduplicator` a embedding path — invariant: **NE `asyncio.to_thread` pro I/O**, `run_in_executor` pro sync lance API.

---

## 2. Všechny LanceDB tabulky — schémata

### 2.1 `entities` — `knowledge/lancedb_store.py:1083-1135`

```python
# LanceDBIdentityStore._initialize() — lazy, called by add_entity()
self._table = self.db.create_table(
    "entities",
    schema=pa.schema([
        pa.field("id", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), list_size=256)),  # F259: 768→256
        pa.field("text", pa.string()),
        pa.field("aliases", pa.string()),   # newline-separated, FTS-indexed
        pa.field("source_type", pa.string()),
        pa.field("confidence", pa.float32()),
        pa.field("metadata_json", pa.string()),
        pa.field("sprint_id", pa.string()),
        pa.field("first_seen", pa.timestamp('s')),
        pa.field("last_seen", pa.timestamp('s')),
    ]),
    exist_ok=True
)
# FTS index on aliases column (lancedb_store.py:1111)
self._table.create_fts_index("aliases", replace=False, with_position=True, tokenizer_name="en_stem")
```

- **URI:** `~/.hledac/lancedb/identity/` (default `_DEFAULT_URI`)
- **Bound:** `_MAX_CACHE_SIZE = _resolve_lancedb_cache_size()` (env-configurable)
- **Embedding model:** MRL 256d (sprint F259 — dříve 768)
- **Write paths:** `add_entity()` lancedb_store.py:1132; `add_entity()` called from `rag_orchestrator` (canonical write)

### 2.2 `academic_papers` — `knowledge/lancedb_store.py:1773-1810`

```python
# LanceDBAcademicStore.__init__/_initialize (lazy)
self._table = self._db.create_table(
    AcademicPaper.TABLE_NAME,  # "academic_papers"
    schema=pa.schema([
        pa.field("paper_id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("abstract", pa.string()),
        pa.field("authors", pa.list_(pa.string())),
        pa.field("year", pa.int32()),
        pa.field("source", pa.string()),
        pa.field("doi", pa.string()),
        pa.field("url", pa.string()),
        pa.field("citation_count", pa.int32()),
        pa.field("embedding", pa.list_(pa.float32(), list_size=self._dim)),  # default 384d
    ]),
    exist_ok=True
)
# 2× FTS indexes (lancedb_store.py:1800 + 1807)
self._table.create_fts_index("title", replace=False, with_position=True, tokenizer_name="en_stem")
self._table.create_fts_index("abstract", replace=False, with_position=True, tokenizer_name="en_stem")
```

- **Embedding model:** FastEmbed BAAI/bge-small-en-v1.5 (384d, 33MB)
- **FTS:** title + abstract (native single-column)
- **Singleton:** `get_academic_store()` (lancedb_store.py:2103)

### 2.3 `semantic_ioc_v1` — `knowledge/semantic_store.py:40, 158-165`

```python
_TABLE_NAME = "semantic_ioc_v1"
# open_table only (no explicit schema in create — append mode, B.6 invariant)
self._table = self._db.open_table(_TABLE_NAME)
# First flush() creates implicitly via .add(records)
```

**Implicitní schéma** (z `flush()` records na ř. 276-286):

| Field | Type | Zdroj |
|-------|------|-------|
| `vector` | list<float32> 384d | FastEmbed/CoreML embed |
| `text` | string (≤4096) | finding text |
| `source_type` | string | `getattr(f, "source_type", "unknown")` |
| `finding_id` | string | finding ID |
| `ts` | float | event loop time |
| `ioc_types` | string (comma-joined) | pattern_matches |

- **Async lifecycle:** `await store.initialize()` (ř. 112) — BOOT
- **Buffer bounds:** `_MAX_PENDING = 2000`, `_MAX_TEXT_LEN = 4096`
- **Backend priority:** CoreML/ANE → FastEmbed CPU → hash fallback
- **No FTS, no vector index** — pure cosinová `.search(q_vec).metric("cosine").limit(k).to_list()`

### 2.4 `semantic_dedup_v1` — `knowledge/ann_index.py:39, 119-132`

```python
_TABLE_NAME = "semantic_dedup_v1"
_EMBEDDING_DIM = 256  # musí matchnout embedding_pipeline._EMBEDDING_DIM
_MAX_ENTRIES = 50_000  # bounded
_MIN_SCORE = 0.90
# Open nebo create:
schema = pa.schema([
    pa.field("finding_key", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), _EMBEDDING_DIM)),
    pa.field("text_hash", pa.string()),
    pa.field("added_at", pa.float64()),
])
self._table = self._db.create_table(_TABLE_NAME, schema=schema)
```

- **Path:** `~/.hledac/ann_index/`
- **Threading:** `self._lock = threading.Lock()` — SYNC API, není async
- **Eviction:** `_maybe_evict()` při `count_rows() > _MAX_ENTRIES` (sort by `added_at ASC`, delete oldest)
- **Compact:** `self._table.optimize()` nebo `compact_files()` (ř. 272-274)
- **Memory guard:** `_check_memory_guard()` skipne init pokud RSS > 6GB
- **Public API:** `get_ann_index()`, `check_ann_duplicate()`, `reset_ann_index()`

### 2.5 `text_index` + `image_index` — `knowledge/vector_store.py:30-108`

```python
_LANCEDB_ROOT = Path.home() / ".hledac" / "lancedb"
_TEXT_DIM = 256   # MRL
_IMAGE_DIM = 1024
text_schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), _TEXT_DIM)),
])
image_schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), _IMAGE_DIM)),
])
self._text_table = self._db.create_table("text_index", schema=text_schema, exist_ok=True)
self._image_table = self._db.create_table("image_index", schema=image_schema, exist_ok=True)
```

- **Singleton:** `get_vector_store()` (vector_store.py:297) — modul-level lazy
- **No FTS, no vector index** — single search path `.to_polars()` (lazy import polars)
- **Consumer:** `analyst_workbench.py:1858` — `query_vectors()` over text index

---

## 3. Inicializace — kdo volá `lancedb.connect()` / `create_table()` / `open_table()`

| File:Line | Volání | Pattern |
|-----------|--------|---------|
| `knowledge/ann_index.py:115` | `self._db = lancedb.connect(str(self._db_path))` | sync, double-checked |
| `knowledge/ann_index.py:119` | `self._db.open_table(_TABLE_NAME)` | try/except |
| `knowledge/ann_index.py:132` | `self._db.create_table(_TABLE_NAME, schema=schema)` | append-mode fallback |
| `knowledge/lancedb_store.py:1093` | `self.db = lancedb.connect(self.uri)` + `self.db.create_table("entities", ...)` | sync, lazy |
| `knowledge/lancedb_store.py:1776` | `self._db.create_table(AcademicPaper.TABLE_NAME, ...)` | sync, lazy |
| `knowledge/semantic_store.py:152` | `self._db = lancedb.connect(db_path_str)` | **async** initialize() |
| `knowledge/semantic_store.py:159` | `self._db.open_table(_TABLE_NAME)` | append mode, NO create |
| `knowledge/vector_store.py:72` | `self._db = lancedb.connect(str(_LANCEDB_ROOT))` | sync, lazy `_init_db()` |
| `knowledge/vector_store.py:88, 91, 100, 103` | `open_table` + `create_table` (text + image) | try/except pattern |

**Root paths:**

- `~/.hledac/lancedb/identity/` — entities
- `~/.hledac/lancedb/ann_index/` — semantic_dedup_v1
- `~/.hledac/lancedb/` — text_index + image_index (VectorStore)
- (academic) — vlastní URI z `LanceDBAcademicStore`

---

## 4. Write pathy — kde se zapisuje do LanceDB

| Table | Write API | File:Line | Voláno z | Mimo canonical seam? |
|-------|-----------|-----------|----------|----------------------|
| `entities` | `LanceDBIdentityStore.add_entity()` | lancedb_store.py:1132 | `rag_orchestrator` (search-similar indirect); dedup adapter | ⚠️ **YES** (RAG write path) |
| `entities` | `upsert_ioc` (graph service) | knowledge/graph_service.py (DuckPGQ) | sprint_scheduler | DuckPGQ ≠ LanceDB, ale obojí je mimo `async_ingest_findings_batch` |
| `academic_papers` | `LanceDBAcademicStore.add_paper()` | lancedb_store.py:~1900 | research lanes | ⚠️ **YES** (RAG write path) |
| `semantic_ioc_v1` | `SemanticStore.flush()` → `self._table.add(records)` | semantic_store.py:289 | `SemanticStoreBuffer.buffer_findings()` ← `DuckDBShadowStore.async_ingest_findings_batch()` | ✅ **OK** (buffered přes canonical seam) |
| `semantic_dedup_v1` | `_ANNIndex.upsert()` → `self._table.add([row])` | ann_index.py:216 | `check_ann_duplicate()` (sync) | ⚠️ **YES** (cross-run dedup, dedikovaný path) |
| `text_index` + `image_index` | `VectorStore.add_vectors()` → `table.add(data)` | vector_store.py:169 | `embedding_pipeline`; `analyst_workbench` | ⚠️ **YES** (multi-modal embeddings) |

**Invariant analýza (CLAUDE.md):**

> "LanceDB je READ path pro RAG, WRITE path jde přes `async_ingest_findings_batch()`"

**Realita:**

- **3 z 5 tabulek** (`entities`, `academic_papers`, `text_index`/`image_index`) zapisují **přímo, mimo canonical seam** — to je v souladu s **dual-writer patternem** (RAG enrichment je vedlejší pipeline, ne canonical).
- `semantic_ioc_v1` je **filtrovaný přes `SemanticStoreBuffer`** z `async_ingest_findings_batch` (ř. 931-933 duckdb_store.py) — správně.
- `semantic_dedup_v1` je **cross-run dedup** — write probíhá jen při detekci nového klíče, není to enrichment.

**Doporučení:** Přidat do CLAUDE.md poznámku "LanceDB write seams: 3 kanonické (entities, academic_papers, dedup) jsou out-of-band; semantic_ioc je buffered přes canonical." (NEBUDEME MĚNIT, jen dokumentovat.)

---

## 5. Read pathy — kdo čte z LanceDB

### 5.1 Search metody (všude `.search(...).to_*()`)

| File:Line | Query | Output | Caller |
|-----------|-------|--------|--------|
| `knowledge/lancedb_store.py:1296-1316` | `hybrid`/`fts`/vector přes `_table.search(...)` | `.to_polars()` | `LanceDBIdentityStore.search_similar()` (canonical) |
| `knowledge/lancedb_store.py:1430` | (full scan) | `self._table.to_pandas()` | identity export |
| `knowledge/lancedb_store.py:1986, 2000, 2006, 2009, 2057, 2066` | `fts` + vector hybrid | `.to_list()` | `LanceDBAcademicStore` search/citation |
| `knowledge/semantic_store.py:327-332` | vector cosine | `.to_list()` | `semantic_pivot()` |
| `knowledge/ann_index.py:168` | vector | (sync, returns list[dict]) | `ann_search()` |
| `knowledge/vector_store.py:260` | vector | `.to_polars()` | `query_vectors()` (analyst_workbench) |
| `tests/probe_hybrid_search_lancedb.py:191` | hybrid RRF | (test only) | probe test |

### 5.2 Konkrétní read consumers

| Consumer | Method | LanceDB source | Purpose |
|----------|--------|----------------|---------|
| `advanced_rag/rag_orchestrator.py:150` | `await self._store.search_similar_adaptive(...)` | `entities` (identity) | RAG research_and_answer |
| `utils/semantic_deduplicator.py` | `check_ann_duplicate(emb, ...)` | `semantic_dedup_v1` | Cross-run dedup pre-filter |
| `knowledge/analyst_workbench.py:476, 1858` | `query_vectors()` | `text_index` | Analyst text ANN |
| `brain/hermes3_engine.py` (calls) | `LanceDBAcademicStore.search_similar` | `academic_papers` | Synthesis context |
| `coordinators/memory_coordinator.py:2677` | `self.semantic_index.search(...)` (HNSW) | (HNSW, not Lance) | RAG fallback |

**Read je vždy `asyncio.to_thread` / `loop.run_in_executor` kvůli sync Lance API.**

---

## 6. Existující indexy — úplný katalog

**LanceDB indexy v kódu (search `create_fts_index` / `create_index` / `create_scalar_index`):**

| File:Line | Table | Index type | Column | Poznámka |
|-----------|-------|-----------|--------|----------|
| `knowledge/lancedb_store.py:1111` | `entities` | **FTS** (text) | `aliases` | `with_position=True`, `tokenizer_name="en_stem"` |
| `knowledge/lancedb_store.py:1800` | `academic_papers` | **FTS** | `title` | en_stem |
| `knowledge/lancedb_store.py:1807` | `academic_papers` | **FTS** | `abstract` | en_stem |

- **❌ Žádné `create_index(num_partitions=..., num_sub_vectors=...)` volání neexistuje.**
- **❌ Žádný IVF_PQ / IVF_FLAT / HNSW index na vector sloupcích.**
- **❌ Žádný scalar/BTree index.**

**Důsledek:** Všechny `.search(q_vec).limit(k)` query jsou **brute-force cosine** přes všechny řádky tabulky. U `semantic_dedup_v1` s 50k entries a 256d to znamená 50k × 256 = 12.8M float32 ops na každý ANN search — to je **hlavní bottleneck** pro IVF-PQ implementaci.

---

## 7. Lazy loading — všechny factory/singleton patterny

### 7.1 Lazy patterns v kódu

| Component | Pattern | Thread-safety | Async-safety | Memory gate |
|-----------|---------|---------------|--------------|-------------|
| `LanceDBIdentityStore` (`get_identity_store()` lancedb_store.py:1660) | **module singleton**, `if _identity_store is None` | ❌ (no lock) | ❌ | `_MAX_CACHE_SIZE` env-driven |
| `LanceDBAcademicStore` (`get_academic_store()` lancedb_store.py:2103) | **module singleton**, `if _academic_store is None` | ❌ | ❌ | none |
| `_ANNIndex` (`get_ann_index()` ann_index.py:352) | **module singleton + double-checked locking** | ✅ `_ann_index_lock = threading.Lock()` | ⚠️ sync only | `_check_memory_guard()`: RSS < 6GB |
| `VectorStore` (`get_vector_store()` vector_store.py:297) | **module singleton + `_init_db()` lazy** | ❌ | ❌ | none |
| `SemanticStore` (`SemanticStore.initialize()` semantic_store.py:112) | **explicit async init**, idempotent (`if self._initialized: return`) | ❌ | ✅ idempotent | none |
| `RAGOrchestrator` (`initialize()` rag_orchestrator.py:71) | **asyncio.Lock + double-checked**, `if self._initialized: return` | ❌ | ✅ `async with self._init_lock` | none |

### 7.2 Async pattern pro sync Lance API

**Převládá `loop.run_in_executor`:**

- `advanced_rag/rag_orchestrator.py:1318` — `df = await loop.run_in_executor(None, _search)`
- `advanced_rag/rag_orchestrator.py:1167` — `await loop.run_in_executor(None, ...)`
- `knowledge/lancedb_store.py:2016, 1318, 1202` — všechny sync I/O přes executor
- `knowledge/semantic_store.py:237, 245, 252, 320, 369` — embed + search přes executor
- `knowledge/ann_index.py` — **čistě sync, není async**, lock-based

**`asyncio.to_thread` (převážně v dedup a embedding, ne I/O):**

- `knowledge/lancedb_store.py:303, 360, 364, 368, 414, 420, 426, 665, 670, 697, 742, 1876` — pro embed, LMDB put, hash
- `utils/semantic_deduplicator.py` (bulk)

**CLAUDE.md invariant:** "NE `asyncio.to_thread` pro I/O" — to je zdůvodnění pro `run_in_executor` pattern v `rag_orchestrator.py:209-210`.

---

## 8. Feature flag env-var patterny

### 8.1 Jak se čtou flags (3 vzory)

**Pattern A — `os.environ.get` s defaultem:**

```python
# fetching/public_fetcher.py:2594
_env_curl = os.environ.get("HLEDAC_ENABLE_CURL_CFFI", "")
if _env_curl and _env_curl != "0":
    ...
```

- Výchozí `""` → falsy → off
- `os.environ.get("HLEDAC_ENABLE_TOR", "0") == "1"` — explicit "1" check

**Pattern B — `os.getenv` boolean:**

```python
# intelligence/dark_web_intelligence.py:511
if not os.getenv("HLEDAC_ENABLE_IMAGE_OSINT"):
    return  # skip if not set
```

**Pattern C — s guard logikou:**

```python
# transport/curl_cffi_transport.py:63
env_value = os.environ.get("HLEDAC_ENABLE_CURL_CFFI", "")
if env_value and env_value != "0":
    ...
```

### 8.2 Kde se flags dokumentují

| Místo | Účel | Příklad |
|-------|------|---------|
| `CLAUDE.md:70-117` | **Master tabulka** — 43 flagů | `HLEDAC_ENABLE_TOR \| 0 \| Tor transport` |
| `.env.example:5-87` | Default values + komentáře | `HLEDAC_OFFLINE=false` |
| `module docstring` | Module-level gate (př. security/captcha_detector.py:3) | "Gated by HLEDAC_ENABLE_CAPTCHA_DETECTION=1" |
| `os.environ.get` line | Inline check | runtime |

### 8.3 LanceDB-specific flags

- **❌ NEEXISTUJE žádný `HLEDAC_ENABLE_LANCEDB*` flag.**

`HLEDAC_ENABLE_RAG` — neexistuje.
`HLEDAC_ENABLE_FASTEMBED` — neexistuje.
`HLEDAC_ENABLE_VECTOR` — neexistuje.
`HLEDAC_ENABLE_SEMANTIC` — neexistuje.

Nejbližší existující: `HLEDAC_ENABLE_GRAPH_RAG=0` (CLAUDE.md:91) — ale ten se týká DuckPGQ, ne LanceDB.

**Doporučení pro IVF-PQ lazy:** Přidat:

- `HLEDAC_ENABLE_LANCEDB_IVFPQ` — default 0 (opt-in)
- `HLEDAC_LANCEDB_TRAIN_THRESHOLD` — default 1000 (rows before train)
- Do `CLAUDE.md` feature flags tabulky + do `.env.example`

---

## 9. Memory footprint — jak se měří

**LanceDB-specifické metriky (žádné `.nbytes` přímo na tabulce):**

| Source | Metrika | Výskyt |
|--------|---------|--------|
| `knowledge/semantic_store.py:162` | `self._table.count_rows()` | log info po open |
| `knowledge/ann_index.py:120` | `row_count = self._table.count_rows()` | log info po open |
| `knowledge/ann_index.py:234` | `count = self._table.count_rows()` (eviction check) | runtime |
| `knowledge/ann_index.py:241` | `self._table.to_arrow().sort_by(...).slice(0, to_delete)` | eviction payload |
| `knowledge/ann_index.py:289` | `self._table.to_arrow().sort_by(...).slice(0, 1)` | oldest timestamp |
| `knowledge/ann_index.py:1202` | `_maybe_compact_blocking` — `.optimize()` / `.compact_files()` | maintenance |

**Embedded vektor size tracking** (mimo Lance):

- `utils/deduplication.py:289, 292, 325, 328, 465` — `embedding.nbytes` pro LRU cache
- `brain/paged_attention_cache.py:185` — `keys.nbytes + values.nbytes` (LLM KV)
- `knowledge/rag_engine.py:608` — `sample_vec.nbytes` (estimate bytes_per_vector)

- **❌ Žádná observability pro LanceDB table-level size** — není `lancedb.observability` log hook, není Prometheus export.

---

## 10. Diagram datového toku

```
┌──────────────────────────────────────────────────────────────────────┐
│                    SPRINT LIFECYCLE (sprint_scheduler)                │
└──────────────────────────────────────────────────────────────────────┘
                │                                    │
                │ async_ingest_findings_batch()      │ semantic_pivot/advisory
                ▼ (canonical write seam)            ▼
┌─────────────────────────────────┐   ┌──────────────────────────────┐
│   DuckDBShadowStore (canonical) │   │  RAGOrchestrator             │
│   + SemanticStoreBuffer        │   │  - lazy initialize()         │
│   - buffer_findings()          │   │  - search_similar_adaptive() │
└────────────┬────────────────────┘   └─────────────┬────────────────┘
             │                                       │
             │ writes to                             │ reads from
             ▼                                       ▼
┌─────────────────────────────────┐   ┌──────────────────────────────┐
│ SemanticStore                   │   │ LanceDBIdentityStore         │
│ - add_text() (in-mem deque)    │   │ - entities (256d MRL)        │
│ - flush() → self._table.add()   │   │ - FTS on aliases             │
│ Path: ~/.hledac/lancedb/        │   │ - hybrid: vector + FTS + RRF │
│ Table: semantic_ioc_v1 (384d)   │   │ Path: ~/.hledac/lancedb/identity/ │
└─────────────────────────────────┘   └──────────────────────────────┘
             │                                       ▲
             │ also writes                           │ writes (out-of-band)
             ▼                                       │
┌─────────────────────────────────┐   ┌──────────────────────────────┐
│ ANN Index (sync, dedup)         │   │ RAG embedding pipeline       │
│ - check_ann_duplicate()         │   │ - add_vectors()              │
│ - _ANNIndex.upsert()            │   │ - 256d text + 1024d image    │
│ Path: ~/.hledac/ann_index/      │   │ Path: ~/.hledac/lancedb/      │
│ Table: semantic_dedup_v1 (256d) │   │ Tables: text_index + image_index│
│ - lock-guarded, no async        │   │ Lazy: _init_db()              │
│ - RSS < 6GB guard               │   └──────────────────────────────┘
│ - max 50k entries, evict by ts  │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ LanceDBAcademicStore            │
│ - add_paper() (out-of-band)     │
│ Path: ~/.hledac/lancedb/academic│
│ Table: academic_papers (384d)   │
│ - FTS on title + abstract       │
│ - search_similar/citation count │
└─────────────────────────────────┘
```

---

## 11. Před-implementační poznámky pro IVF-PQ + lazy loading

### 11.1 Horká místa pro IVF-PQ

| Table | Rows (typical) | Dim | Doporučení |
|-------|----------------|-----|------------|
| `entities` | bounded _MAX_CACHE_SIZE (~50k-100k) | 256 | ✅ IVF_PQ win ≥ 10× rychlost, trénovat po 1k řádcích |
| `semantic_ioc_v1` | unbounded (append mode) | 384 | ⚠️ POTENCIÁLNĚ VELKÉ — IVF_PQ kritický, ale trénink vyžaduje warmup |
| `semantic_dedup_v1` | bounded 50k | 256 | ✅ IVF_PQ, num_partitions=64, num_sub_vectors=32 |
| `academic_papers` | bounded (research scope) | 384 | ❌ malé (<1000 typicky) — IVF_PQ overhead > benefit |
| `text_index` / `image_index` | unbounded | 256/1024 | ⚠️ image dim vysoká, IVF_PQ velký win |

### 11.2 Lazy loading patterny k reuse

**Nejlepší vzor (RAGOrchestrator):**

```python
async def initialize(self) -> None:
    if self._initialized: return
    async with self._init_lock:                  # asyncio.Lock
        if self._initialized: return             # double-checked
        try:
            self._store = get_identity_store()    # factory
            self._initialized = True
        except Exception as e:
            self._init_error = f"{type(e).__name__}: {e}"
            # FAIL-SOFT — nikdy nevyhazuj
```

**Nejlepší vzor (ANNIndex — sync):**

```python
def get_ann_index() -> _ANNIndex:
    global _ann_index
    if _ann_index is None:                       # fast path
        with _ann_index_lock:                    # threading.Lock
            if _ann_index is None:               # double-checked
                _ann_index = _ANNIndex(db_path)
                _ann_index.init()                # could fail
    return _ann_index
```

### 11.3 Bezpečnostní invarianty pro IVF-PQ implementaci

1. **NE trainuj IVF_PQ na < 256 řádcích** — degradovaná kvalita, lepší brute-force.
2. **Train async, off event loop** — `loop.run_in_executor` (jako ostatní Lance calls).
3. **Bound num_partitions** — `min(64, max(8, row_count // 1000))` — M1 8GB safe.
4. **Bound num_sub_vectors** — `dim // 8` (32 pro 256d, 48 pro 384d).
5. **Auto-rebuild check** — `list_indices()` po každém `.add()` batchi (pokud překročen threshold, retrain async).
6. **Fallback** — `IVF_PQ` index creation failure → log warning + fallback na brute-force (invariant: nikdy nevyhazuj výjimku).
7. **Memory guard** — RSS < 6GB check (reuse `_check_memory_guard` z `ann_index.py:88`).

### 11.4 Nové env flags k přidání

```bash
# .env.example additions
HLEDAC_ENABLE_LANCEDB_IVFPQ=0      # default off, opt-in pro M1 8GB safety
HLEDAC_LANCEDB_IVFPQ_TRAIN_ROWS=1000  # train po tolika řádcích
HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS=64
HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS=32  # 256d/8; pro 384d → 48
```

A do `CLAUDE.md` feature flags tabulky (ř. 70-117) přidat 3 nové řádky.

---

## 12. Appendix — souborový index

| File | Řádků | Role |
|------|-------|------|
| `knowledge/lancedb_store.py` | 2108 | identity (entities) + academic (papers) — dual stores |
| `knowledge/semantic_store.py` | 402 | SemanticStore — buffered FastEmbed/CoreML + LanceDB |
| `knowledge/semantic_store_buffer.py` | 81 | Bridge: DuckDB findings → SemanticStore buffering |
| `knowledge/ann_index.py` | 425 | _ANNIndex — sync dedup ANN with thread lock |
| `knowledge/vector_store.py` | 307 | VectorStore — text (256d) + image (1024d) tables |
| `advanced_rag/rag_orchestrator.py` | ~250 | RAGOrchestrator — lazy-init pattern reference |
| `utils/semantic_deduplicator.py` | (uses `check_ann_duplicate`) | dedup consumer |
| `knowledge/analyst_workbench.py` | 1858 | VectorStore consumer (text ANN) |
| `tests/probe_hybrid_search_lancedb.py` | ~540 | Hybrid RRF + IVF_PQ regression test |

**Testy s LanceDB:**

- `tests/probe_hybrid_search_lancedb.py` — AREA H+ hybrid RRF regression (530 řádků)
- `tests/test_semantic_store_buffer.py` — buffer injection
- `tests/probe_advanced_modules_wiring.py:680-692` — assert `lancedb.connect` NOT called in `RAGOrchestrator` (uses factory)
- `tests/probe_hybrid_search_lancedb.py:513-529` — stub `create_table` to test re-init failure

---

*Konec reportu. Připraveno k implementaci IVF-PQ lazy loadingu.*

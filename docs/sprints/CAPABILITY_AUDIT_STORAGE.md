# Capability Audit: Knowledge Storage & Retrieval

**Datum:** 2026-05-30
**Auditor:** Vojtech Hamada

---

## Sekce 1: Storage Backends — Co máme a v jakém stavu

### 1.1 DuckDB Store (`knowledge/duckdb_store.py`)
**Stav:** AKTIVNÍ, 268KB, hot-path pro všechny sprint výsledky

**Tabulky:**
| Tabulka | Primární klíč | Účel |
|---------|--------------|------|
| `shadow_findings` | (query, source_type) UNIQUE | Vědecká měření, shadow run výsledky |
| `shadow_runs` | run_id | Benchmark metadata |
| `sprint_delta` | sprint_id | Sprint-level agregace (new_findings, dedup_hits, ioc_nodes) |
| `source_hit_log` | (sprint_id, ts) | Zdrojová hit rate tracking |
| `sprint_scorecard` | sprint_id | Kvalitativní sprint score |
| `research_episodes` | episode_id | Historické sprint epizody |
| `target_profiles` | target_id | Target-level agregace |
| `hypothesis_feedback` | id | RL feedback loop |
| `hypothesis_tracking` | hypothesis_id | Sprint hypothesis lifecycle |
| `target_memory` | target_id | Cross-sprint entity persistence |
| `global_entities` | entity_value | Canonical entity tracking |

### 1.2 LanceDB Store (`knowledge/lancedb_store.py`)
**Stav:** AKTIVNÍ, 57KB, pouze `LanceDBIdentityStore`

**Kolekce:**
- `_embedding_dim = 768` (fallback), aktuálně `self._current_mrl_dim = 768` (not 256!)
- PyArrow schema pro `(id, text, embedding, metadata)`
- MLX-compiled cosine similarity reranking
- EVICTION_THRESHOLD_RATIO = 0.85

### 1.3 LMDB Stores
| LMDB | Účel | Bound |
|------|------|-------|
| `semantic_dedup.lmdb` | Sémantický dedup cache | Memory guard skip >6GB RSS |
| `sprint_seeds.lmdb` | Sprint seed persistence (F214Q) | Aktivní |
| `dedup.lmdb` | URL dedup persistent | Aktivní |

### 1.4 DHT (`dht/`)
**Stav:** ČÁSTEČNĚ IMPLEMENTOVANÝ, 0 production call sites

- `kademlia_node.py`: UDP Kademlia Node (BEP-5)
- `local_graph.py`: Local DHT graph
- `sketch_exchange.py`: Sketch exchange protocol
- **BEP-9 metadata extension (ut_metadata) NENÍ IMPLEMENTOVÁNA** — pouze comments
- ** Žádné production call sites** (grep: 0 volání `crawl_dht_for_keyword`/`lookup_info_hash_metadata`)

---

## Sekce 2: Embedding Capabilities

### 2.1 ANE (Apple Neural Engine) Path
**Stav:** IMPLEMENTOVÁNO, `brain/ane_embedder.py`

```
Priority routing: ANE (CoreML) → MLX ModernBERT → CPU sentence-transformers
M1 8GB UMA constraint: ANE and MLX ModernBERT are never loaded simultaneously
ANE uses ~300MB CoreML model; MLX ModernBERT uses ~500MB
```

**ANE Embedder (`brain/ane_embedder.py`):**
- `ANE_AVAILABLE` flag (CoreML/pyobjc detection)
- `ANEEmbedder` class: CoreML first → MLX fallback → hash fallback
- `ANE_MLX_Mutex` singleton: prevents OOM on M1 8GB
- `get_ane_embedder()`: lazy init MiniLM-L6-v2 (384d)
- `semantic_dedup_findings()`: ANE batch inference → cosine similarity
- `rerank_findings_cosine()`: reranker using ANE embeddings
- `get_ane_status()`: telemetry (embed_attempted, embed_fallback_used, warmup)

### 2.2 ModernBERT (MLX)
**Stav:** AKTIVNÍ, `embeddings/modernbert_embedder.py`

- Model: `mlx-community/embeddings-bge-m3`
- MRL (Matryoshka Representation Learning): 256d truncation
- Lazy load, MLX Metal backend
- `embed_batch()` sync API

### 2.3 FastEmbed (Semantic Store)
**Stav:** AKTIVNÍ, `knowledge/semantic_store.py`

- Model: `BAAI/bge-small-en-v1.5` (ONNX, dim=384, ~33MB, CoreML-friendly)
- Used in `LanceDBIdentityStore` with MLX fallback

### 2.4 Embedding Dimensions
| Pipeline | Model | Dimenze |
|----------|-------|---------|
| `embedding_pipeline.py` | ModernBERT MRL | **256d** (MRL truncated) |
| `lancedb_store.py` | LanceDBIdentityStore | **768d** (!) |
| `semantic_store.py` | FastEmbed BAAI/bge-small-en-v1.5 | **384d** |
| `core/mlx_embeddings.py` | MLXEmbeddingManager | **768d** |

---

## Sekce 3: Knowledge Schemas — Všechny DuckDB Tabulky

```sql
-- Shadow findings (scientific measurements)
shadow_findings (
    id VARCHAR PRIMARY KEY,
    query VARCHAR,
    source_type VARCHAR,
    confidence DOUBLE,
    ts DOUBLE,
    provenance_json TEXT,
    UNIQUE (query, source_type)
)

-- Shadow runs (benchmark metadata)
shadow_runs (
    run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    total_fds INTEGER,
    rss_mb INTEGER
)

-- Sprint delta (sprint-level aggregation)
sprint_delta (
    sprint_id TEXT PRIMARY KEY,
    ts DOUBLE NOT NULL,
    query TEXT,
    duration_s REAL DEFAULT 0,
    new_findings INT DEFAULT 0,
    dedup_hits INT DEFAULT 0,
    ioc_nodes INT DEFAULT 0,
    ioc_new_this_sprint INT DEFAULT 0,
    uma_peak_gib REAL DEFAULT 0,
    synthesis_success BOOL DEFAULT false,
    ... (fi truncated)
)

-- Source hit log
source_hit_log (
    sprint_id TEXT,
    ts DOUBLE,
    source_type TEXT,
    findings_count INT,
    ioc_count INT,
    hit_rate REAL
)

-- Sprint scorecard
sprint_scorecard (
    sprint_id TEXT PRIMARY KEY,
    ts DOUBLE NOT NULL,
    findings_per_minute REAL,
    ioc_density REAL,
    semantic_novelty REAL,
    source_yield_json TEXT,
    phase_timings_json TEXT,
    outlines_used BOOL,
    accepted_findings INT,
    ioc_nodes INT
)

-- Research episodes
research_episodes (
    episode_id TEXT PRIMARY KEY,
    sprint_id TEXT NOT NULL,
    query TEXT NOT NULL,
    summary TEXT,
    top_findings JSON,
    ioc_clusters JSON,
    source_yield JSON,
    synthesis_engine TEXT,
    duration_s REAL,
    ts DOUBLE NOT NULL
)

-- Target profiles
target_profiles (
    target_id TEXT PRIMARY KEY,
    first_seen DOUBLE,
    last_seen DOUBLE,
    cumulative_finding_count INTEGER,
    entity_summary_json TEXT
)

-- Hypothesis feedback (RL loop)
hypothesis_feedback (
    id TEXT PRIMARY KEY,
    target_id TEXT,
    pivot_type TEXT,
    ioc_type TEXT,
    produced_count INTEGER,
    accepted_count INTEGER,
    signal_value DOUBLE,
    ts DOUBLE
)

-- Hypothesis tracking
hypothesis_tracking (
    hypothesis_id TEXT PRIMARY KEY,
    sprint_id TEXT,
    hypothesis_text TEXT,
    status TEXT,
    confidence REAL,
    falsification_result TEXT,
    disproved_by_sprint_id TEXT,
    ts DOUBLE
)

-- Target memory (cross-sprint entity persistence)
target_memory (
    target_id TEXT PRIMARY KEY,
    first_seen_ts DOUBLE NOT NULL,
    last_seen_ts DOUBLE NOT NULL,
    sprint_count INTEGER NOT NULL,
    cumulative_finding_count INTEGER NOT NULL,
    entity_facets_json TEXT NOT NULL,
    exposure_facets_json TEXT NOT NULL,
    pivot_facets_json TEXT NOT NULL,
    confidence_drift_json TEXT,
    updated_by_sprint_id TEXT,
    updated_ts ...
)

-- Global entities (canonical entity tracking)
global_entities (
    entity_value TEXT PRIMARY KEY,
    entity_type TEXT,
    sprint_count INT DEFAULT 0,
    last_seen DOUBLE,
    confidence_cumulative REAL DEFAULT 0
)
```

---

## Sekce 4: Dedup Pipeline

### 4.1 URL Dedup (RotatingBloomFilter)
**Stav:** NENÍ IMPLEMENTOVÁNO

```
# Sprint F222F: RotatingBloomFilter for cross-run URL dedup pre-check
```
- Comment exists in `knowledge/dedup.py` ale **třída není implementována**
- Žádná `pybloom_live` dependency v `pyproject.toml`

### 4.2 LRU Hot Cache
**Stav:** AKTIVNÍ

```python
# knowledge/dedup.py
def _load_dedup_hot_cache_max() -> int:
    # Default: 10,000 entries
    return int(os.getenv("DEDUP_HOT_CACHE_MAX", "10000"))
```

| Parametr | Hodnota |
|----------|---------|
| `DEDUP_HOT_CACHE_MAX` | 10,000 (default env var) |
| Implementace | `OrderedDict` pro LRU eviction |

### 4.3 Semantic Dedup
**Stav:** AKTIVNÍ, `semantic_deduplicator.py`

| Parametr | Hodnota |
|----------|---------|
| Threshold | **0.90** (cosine similarity) |
| LMDB cache | `semantic_dedup.lmdb` |
| Memory guard | Skip init if RSS > 6GB |
| Batch API | `check_batch(texts, threshold=0.90)` |

```python
# semantic_deduplicator.py
def check_and_cache(self, text: str, threshold: float = 0.90) -> bool:
    sim = _cosine_similarity(query_emb, cached_emb.reshape(1, -1))[0, 0]
    if sim >= threshold:  # Cache hit
```

---

## Sekce 5: Chybějící Storage

### 5.1 DuckDB: `dht_metadata` Tabulka
**Status:** 🚨 MISSING

Žádná `dht_metadata` tabulka v `duckdb_store.py`. DHT modul je částečně implementován ale nemá persistence layer.

**Doporučené schema:**
```sql
CREATE TABLE IF NOT EXISTS dht_metadata (
    infohash TEXT PRIMARY KEY,          -- 20-byte torrent hash
    name TEXT,                          -- torrent name
    files_json TEXT,                    -- JSON array of {path, length}
    size_bytes BIGINT,                  -- total size
    first_seen DOUBLE,                  -- timestamp
    last_seen DOUBLE,                   -- timestamp
    peer_count INT,                     -- observed peers
    sources_json TEXT                   -- JSON array of source nodes
)
```

### 5.2 LanceDB: Akademická Data Kolekce
**Status:** 🚨 MISSING

Žádná `academic_store` nebo podobná kolekce pro:
- Research papers (PDF metadata)
- Academic abstracts
- Citation graphs
- arXiv/semantic scholar records

**Doporučené kolekce:**
| Kolekce | Dimenze | Schema |
|---------|---------|--------|
| `academic_papers` | 384d (FastEmbed) | (paper_id, title, abstract, authors, year, citations, embedding) |
| `academic_abstracts` | 256d (MRL) | (abstract_id, text, field, embedding) |

### 5.3 LanceDB: Archívní Data Kolekce
**Status:** 🚨 MISSING

Žádná `archival_store` pro:
- CommonCrawl WARC records
- Wayback Machine snapshots
- Historical web content

**Doporučené kolekce:**
| Kolekce | Dimenze | Schema |
|---------|---------|--------|
| `common_crawl` | 256d (MRL) | (url, timestamp, content_hash, embedding) |
| `wayback_snapshots` | 256d (MRL) | (original_url, capture_ts, archived_url, embedding) |

### 5.4 CoreML/ANE Embedding Path
**Status:** ✅ IMPLEMENTOVÁNO

`brain/ane_embedder.py` je kompletní:
- `ANEEmbedder` class s CoreML → MLX → hash fallback chain
- `ANE_MLX_Mutex` pro M1 8GB mutual exclusion
- `convert_to_ane()` pro offline CoreML konverzi
- `warmup()` pro cache priming
- Telemetry tracking: `ane_embed_attempted`, `ane_embed_fallback_used`, `ane_warmup_executed`

**Konverze modelu:**
```bash
# Offline konverze MLX → CoreML pro ANE
embedder.convert_to_ane()  # async, compileModelAtURL
```

### 5.5 MRL Embedding (256d)
**Status:** ⚠️ ČÁSTEČNĚ IMPLEMENTOVÁNO

`embedding_pipeline.py` má MRL 256d truncation:
```python
_EMBEDDING_DIM = 256  # MRL dimension
truncate_dim=_EMBEDDING_DIM
```

**ALE `lancedb_store.py` používá 768d fallback!**
```python
self._current_mrl_dim = 768  # NOT 256!
```

---

## Shrnutí Akčních Bodů

| Priorita | Action | Soubor |
|---------|--------|--------|
| HIGH | Implementovat `dht_metadata` tabulku | `knowledge/duckdb_store.py` |
| HIGH | Opravit `LanceDBIdentityStore._current_mrl_dim` na 256 | `knowledge/lancedb_store.py:195` |
| MEDIUM | Implementovat RotatingBloomFilter (F222F comment-only) | `knowledge/dedup.py` |
| MEDIUM | Vytvořit academic_store kolekci | `knowledge/lancedb_store.py` |
| MEDIUM | Vytvořit archival_store kolekci | `knowledge/lancedb_store.py` |
| LOW | Wire DHT kademlia_node do production (0 call sites) | `dht/kademlia_node.py` |

---

## Sekce 6: Celková Architektura Storage

### 6.1 Databázový Stack

```
┌─────────────────────────────────────────────────────────────┐
│                    CANONICAL WRITE                          │
│                 knowledge/duckdb_store.py                   │
│                      (DuckDB)                               │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌──────────┐       ┌──────────┐       ┌──────────┐
    │ LMDB     │       │ LanceDB  │       │ Graph    │
    │ (KV)     │       │ (Vector) │       │ DuckPGQ  │
    │ dedup    │       │ identity │       │ ioc_edges│
    │ semantic │       │          │       │          │
    └──────────┘       └──────────┘       └──────────┘
```

| Backend | Velikost | Účel | Bound |
|---------|----------|------|-------|
| DuckDB | 268KB | Canonical write, 13 tabulek | ✅ aktivní |
| LMDB | ~50KB | KV store, dedup, semantic cache | ✅ aktivní |
| LanceDB | 57KB | Vector ANN search | ⚠️ dimenze 768d |
| DuckPGQ | 52KB | Graph analytics | ✅ aktivní |
| FalkorDB | — | NOT USED | ❌ |
| Kuzu | — | IOCGraph reference only | ⚠️ |

### 6.2 Storage Moduly mimo knowledge/

| Modul | Soubor | Účel |
|-------|--------|------|
| LMDB Boot Guard | `knowledge/lmdb_boot_guard.py` | Stale lock cleanup |
| LMDB KV Store | `tools/lmdb_kv.py` | Generic KV interface |
| HNSW Builder | `tools/hnsw_builder.py` | Legacy HNSW index (IncrementalHNSW) |
| Prefetch Cache | `prefetch/prefetch_cache.py` | SPRINT_LMDB_ROOT |
| Task Cache | `planning/task_cache.py` | SPRINT_LMDB_ROOT |

### 6.3 Graph Storage (`graph/`)

**DuckPGQGraph** (`graph/quantum_pathfinder.py:1105`):
- SQL/PGQ MATCH queries přes DuckDB
- `ioc_nodes` + `ioc_edges` tabulky
- `find_connected()` s recursive CTE fallback
- `stats()`, `get_top_nodes_by_degree()`, `export_edge_list()`
- **NEBO** IOCGraph (Kuzu) pro full STIX capability

**GraphAttachmentStore** (`knowledge/graph_attachment.py`):
- Facade pattern: `_ioc_graph` (analytics/donor) vs `_stix_graph` (STIX-only)
- DuckPGQGraph se NIKDY neinjektuje do `_stix_graph` (chybí export_stix_bundle)

---

## Sekce 7: Network/Protocol Storage Clients

### 7.1 Alternative Protocol Clients

| Klient | Modul | Status | Backend |
|--------|-------|--------|---------|
| IPFSClient | `intelligence/archive_discovery.py:443` | ✅ AKTIVNÍ | HTTP gateway |
| WaybackMachineClient | `intelligence/archive_discovery.py:255` | ✅ AKTIVNÍ | archive.org |
| ArchiveTodayClient | `intelligence/archive_discovery.py:382` | ✅ AKTIVNÍ | archive.today |
| GopherCrawler | `discovery/gopher_crawler.py:83` | ⚠️ LEGACY | Gopher protocol |
| TorManager | `network/tor_manager.py:21` | ✅ AKTIVNÍ | SOCKS proxy |
| I2PTransport | `transport/i2p_transport.py:55` | ✅ AKTIVNÍ | I2P SAM |

### 7.2 IPFS Gateway Storage

**IPFSClient** (`intelligence/archive_discovery.py:443`):
```python
class IPFSClient:
    GATEWAYS = [
        "https://gateway.ipfs.io/ipfs/",
        "https://cloudflare-ipfs.com/ipfs/",
        "https://ipfs.io/ipfs/",
    ]
    def fetch_content(self, cid: str) -> bytes | None
```

### 7.3 DHT Kademlia (`dht/`)

**KademliaNode** (`dht/kademlia_node.py:305`):
- UDP DHT node (BEP-5 get_peers)
- **BEP-9 ut_metadata NENÍ implementována**
- 0 production call sites
- **Findings from DHT are returned but NOT stored to DuckDBShadowStore**

---

## Sekce 8: Export Formats

### 8.1 Export Modules (`export/`)

| Modul | Velikost | Formát |
|-------|----------|--------|
| `stix_exporter.py` | 71KB | STIX 2.1 (CTI) |
| `jsonld_exporter.py` | 23KB | JSON-LD |
| `sprint_exporter.py` | 165KB | JSON (canonical) |
| `sprint_markdown_reporter.py` | 47KB | Markdown |
| `markdown_reporter.py` | 19KB | Diagnostic markdown |
| `formatters.py` | 26KB | JSON + boundary content |

### 8.2 Export Capabilities

| Export | Metoda |
|--------|--------|
| STIX bundle | `render_stix_bundle()`, `render_cti_stix_bundle_to_path()` |
| JSON-LD | `render_jsonld()`, `render_jsonld_to_path()` |
| Sprint JSON | `export/sprint_exporter.py` (canonical) |
| Markdown | `render_diagnostic_markdown()`, `render_investigation_packet_markdown()` |
| Edge list | `graph/quantum_pathfinder.py:1165` → `export_edge_list()` |

---

## Sekce 9: Intelligence/Exposure Clients

### 9.1 Exposure Intelligence

| Klient | Modul | Data |
|--------|-------|------|
| ShodanClient | `intelligence/exposure_clients.py:178` | IP/scan data |
| CensysClient | `intelligence/exposure_clients.py:273` | SSL certs |
| GreyNoiseClient | `intelligence/exposure_clients.py:623` | Threat intel |
| CVIntelligenceClient | `intelligence/exposure_clients.py:697` | CVE data |
| PassiveDNSClient | `intelligence/network_reconnaissance.py:927` | DNS history |

### 9.2 Academic Search

| Klient | Modul | API |
|--------|-------|-----|
| ArxivAdapter | `intelligence/academic_search.py:290` | export.arxiv.org/api/query |
| CrossrefAdapter | `intelligence/academic_search.py:460` | api.crossref.org |
| SemanticScholarAdapter | `intelligence/academic_search.py:610` | api.semanticscholar.org |

**Poznámka:** Akademická data jsou hledána přes tyto klienty, ale **neukládají se** do specializované DuckDB tabulky nebo LanceDB kolekce.

### 9.3 Archive Discovery

| Klient | Modul | Zdroj |
|--------|-------|-------|
| WaybackMachineClient | `intelligence/archive_discovery.py:255` | archive.org |
| ArchiveTodayClient | `intelligence/archive_discovery.py:382` | archive.today |
| GitHubHistoricalClient | `intelligence/archive_discovery.py:500` | github.com |
| PastebinMonitorClient | `intelligence/archive_discovery.py:1763` | pastebin sites |
| WaybackCDXClient | `deep_probe.py:490` | Wayback CDX |

---

## Sekce 10: Kompletní Seznam Storage Module

### 10.1 Storage Class Hierarchy

```
DuckDBShadowStore (knowledge/duckdb_store.py:557)
├── 13 tabulek (shadow_findings, sprint_delta, target_memory, ...)
├── async/sync query methods
└── Arrow batch support

LanceDBIdentityStore (knowledge/lancedb_store.py:114)
├── PyArrow schema (id, text, embedding, metadata)
├── MLX-compiled cosine similarity
└── ANN search

DuckPGQGraph (graph/quantum_pathfinder.py:1105)
├── SQL/PGQ MATCH queries
├── ioc_nodes + ioc_edges
└── Graph analytics

SemanticDedupCache (semantic_deduplicator.py)
├── cosine similarity threshold 0.90
├── LMDB persistence
└── batch API

IPFSClient (intelligence/archive_discovery.py:443)
└── Multi-gateway fallback

WaybackMachineClient (intelligence/archive_discovery.py:255)
└── archive.org API

AcademicSearchClient (intelligence/academic_search.py:130)
├── ArxivAdapter
├── CrossrefAdapter
└── SemanticScholarAdapter
```

### 10.2 Storage Boundary Invariants

| Rule | Source |
|------|--------|
| DuckDB = canonical write | duckdb_store.py |
| LMDB = KV metadata, dedup | paths.py |
| LanceDB = RAG embeddings | lancedb_store.py |
| DuckPGQ = graph analytics | graph/quantum_pathfinder.py |
| ANE/MLX mutual exclusion | brain/ane_embedder.py:30 |

---

## Sekce 11: Gap Analysis — Storage vs. Capability

### 11.1 Implementované ale nepoužívané

| Modul | Status | Problém |
|-------|--------|---------|
| DHT Kademlia | Částečně impl | 0 production call sites |
| GopherCrawler | Legacy | Deprecated |
| FalkorDB | NOT USED | Není v codebase |

### 11.2 Chybějící Integration

| Capability | Chybí |
|-----------|-------|
| DHT metadata → DuckDB | Žádná `dht_metadata` tabulka |
| Academic papers → LanceDB | Žádná `academic_papers` kolekce |
| Wayback snapshots → LanceDB | Žádná `wayback_snapshots` kolekce |
| CommonCrawl → LanceDB | Žádná `common_crawl` kolekce |

### 11.3 Dimension Mismatch

| Komponenta | Aktuální | Správný |
|------------|----------|---------|
| `LanceDBIdentityStore._current_mrl_dim` | 768 | 256 |
| `core/mlx_embeddings.py` | 768 | 256 (pro MRL) |
| `semantic_store.py` | 384 | OK (FastEmbed) |

---

## Sekce 12: Doporučení pro Další Sprint

### 12.1 F224A: DuckDB dht_metadata Table
**Implementace:**
```python
# knowledge/duckdb_store.py
CREATE TABLE IF NOT EXISTS dht_metadata (
    infohash TEXT PRIMARY KEY,
    name TEXT,
    files_json TEXT,
    size_bytes BIGINT,
    first_seen DOUBLE,
    last_seen DOUBLE,
    peer_count INT,
    sources_json TEXT
)
```

### 12.2 F224B: LanceDB Academic Store
**Implementace:**
```python
# knowledge/lancedb_store.py
class LanceDBAcademicStore:
    def __init__(self, dim: int = 384):
        self._table = self.db.create_table("academic_papers", ...)
        # schema: (paper_id, title, abstract, authors, year, embedding)
```

### 12.3 F224C: LanceDB Archival Store
**Implementace:**
```python
# knowledge/lancedb_store.py  
class LanceDBArchivalStore:
    def __init__(self, dim: int = 256):
        self._table = self.db.create_table("wayback_snapshots", ...)
        # schema: (original_url, capture_ts, archived_url, content_hash, embedding)
```

### 12.4 F224D: Fix MRL Dimension
**Oprava:**
```python
# knowledge/lancedb_store.py:195
self._current_mrl_dim = 256  # NOT 768
```

### 12.5 F224E: RotatingBloomFilter
**Implementace:**
```python
# knowledge/dedup.py
class RotatingBloomFilter:
    """Sprint F222F: Cross-run URL dedup pre-check"""
    def __init__(self, capacity: int = 100000, fp_rate: float = 0.001):
        # Use pybloom_live or custom implementation
```
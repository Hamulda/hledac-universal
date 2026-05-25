# Storage & Knowledge Audit — 2026-05-23

## DuckDB Schema

### Tables — 10 confirmed via CREATE TABLE IF NOT EXISTS

| Table | Tier | Key |
|-------|------|-----|
| `sprint_delta` | Tier 1 | sprint_id PK |
| `sprint_scorecard` | Tier 1 | sprint_id PK |
| `runtime_status` (shadow_runs) | Tier 2 | run_id PK |
| `source_hit_log` | Tier 1 | (source_type, sprint_id) PK |
| `shadow_findings` | Tier 1 | finding_id PK |
| `research_episodes` | Tier 2 | episode_id PK |
| `target_profiles` | Tier 1 | target_id PK |
| `hypothesis_feedback` | Tier 1 | feedback_id PK |
| `target_memory` | Tier 1 | target_id PK |
| `global_entities` | Tier 1 | entity_id PK |

**shadow_findings** (Tier 2 — durable)
```
id              VARCHAR PRIMARY KEY
source_type     VARCHAR
confidence      DOUBLE
finding_type    VARCHAR
ioc_type        VARCHAR
ioc_value       TEXT
ts              DOUBLE
payload_text    TEXT
envelope_json   TEXT (nullable)
```

**shadow_runs** (Tier 2 — durable)
```
run_id          VARCHAR PRIMARY KEY
sprint_id       VARCHAR
started_at      DOUBLE
ended_at        DOUBLE (nullable)
duration_s      DOUBLE (nullable)
findings_new    INTEGER
findings_total  INTEGER
findings_per_minute REAL (nullable)
rss_mb          REAL (nullable)
total_fds       INTEGER (nullable)
```

**sprint_delta** (Tier 1 — durable)
```
sprint_id       VARCHAR PRIMARY KEY
query           VARCHAR
duration_s      DOUBLE
new_findings    INTEGER
dedup_hits      INTEGER
ioc_nodes       INTEGER
updated_by_sprint_id VARCHAR (nullable)
```

**sprint_scorecard** (Tier 1 — durable)
```
sprint_id       VARCHAR PRIMARY KEY
fpm             REAL
ioc_density     REAL
semantic_novelty REAL
source_yield_json TEXT
phase_timings_json TEXT
outlines_used   INTEGER
research_episodes INTEGER
hypothesis_feedback TEXT (nullable)
```

**source_hit_log** (Tier 1 — durable)
```
source_type     VARCHAR
sprint_id       VARCHAR
hit_rate        REAL
PRIMARY KEY (source_type, sprint_id)
```

**global_entities** (Tier 3 — graph-adjacent)
```
entity_value    TEXT PRIMARY KEY
entity_type     TEXT
sprint_count    INT DEFAULT 0
last_seen       DOUBLE
confidence_cumulative REAL DEFAULT 0
```

**target_memory** (Tier 3 — profile tracking)
```
target_id       TEXT PRIMARY KEY
first_seen_ts   DOUBLE NOT NULL
last_seen_ts    DOUBLE NOT NULL
sprint_count    INTEGER NOT NULL
cumulative_finding_count INTEGER NOT NULL
entity_facets_json TEXT NOT NULL
exposure_facets_json TEXT NOT NULL
pivot_facets_json TEXT NOT NULL
confidence_drift_json TEXT NOT NULL
```

**target_profiles**
```
target_id       TEXT PRIMARY KEY
first_seen      DOUBLE
last_seen       DOUBLE
cumulative_finding_count INTEGER
entity_summary_json TEXT
updated_by_sprint_id TEXT
updated_ts      DOUBLE
```

---

## DuckDB Safety

| Property | Value |
|---|---|
| Thread model | Single-worker `ThreadPoolExecutor(max_workers=1)` |
| Async safety | All public `async def` methods use `loop.run_in_executor()` — never block event loop |
| Connection model | MODE A (RAMDISK active): file-backed DB at `DB_ROOT/shadow_analytics.duckdb` + temp on RAMDISK. MODE B: `:memory:` with persistent single conn |
| Connection lifecycle | Created INSIDE worker thread (thread-affine). `PRAGMA threads=2` applied at init (M1 8GB conservative) |
| Lock | `asyncio.Lock` only for `_replay_lock` (WAL replay boundary) — not for DB write ops |
| Deferred import | DuckDB NOT imported at module level of any boot-path file — deferred to first `initialize()` call |
| Batch chunking | `max_batch_size=500` enforced in all batch methods |
| Shutdown | `aclose()` idempotent with `_closed` flag |

**Write path (canonical):**
```
async_ingest_findings_batch()
  → QualityAssessor.assess_quality()      [entropy, dedup fp, URL normalization]
  → accept/reject decision
  → WALManager.append()                   [write-ahead log, crash safety]
  → DedupManager.check()                 [duplicate detection]
  → DuckDB insert (sprint_delta, shadow_findings)
  → SemanticStoreBuffer.buffer()         [FastEmbed + LanceDB async index]
  → GraphAttachmentStore (optional, post-accumulation)
  → WALManager.flush()                   [sync marker, allows replay]
```

**Read methods:** `async_query_recent_findings()`, `async_query_sprint_deltas()`, `async_query_source_hit_log()`, `get_runtime_status()`, `get_dedup_runtime_status()`, `get_wal_runtime_status()`, `get_semantic_buffer_status()`, `get_graph_stats()`

**LMDB delegation (no LMDB handles owned by DuckDBShadowStore):**
- `WALManager` → `wal.py` (pending sync markers, deadletters, WAL replay state)
- `DedupManager` → `dedup.py` (persistent LMDB at `LMDB_ROOT/dedup.lmdb`, hot cache, semantic dedup cache)
- `SemanticStoreBuffer` → `buffer.py` (FastEmbed + LanceDB batch embedding pipeline)
- `GraphAttachmentStore` → `graph_attachment.py` (IOCGraph injection, STIX, truth-write)

---

## LanceDB Configuration

### vector_store.py — Primary vector storage (text + image indices)

| Property | Value |
|---|---|
| DB path | `~/.hledac/lancedb/` |
| Text index | `text_index.lance` — 256d MRL (ModernBERT) |
| Image index | `image_index.lance` — 1024d |
| Metric | Cosine similarity (via `1 - L2 distance` score) |
| Index type | IVF (default LanceDB) |

### lancedb_store.py — Identity/Entity Store (identity stitching)

| Property | Value |
|---|---|
| Class | `LanceDBIdentityStore` (confirmed via Python AST) |
| DB path | `~/.hledac/identity.lance` (`_DEFAULT_URI`) |
| Table | `findings_v1` (inherited from semantic_store) |
| Dimension | 384 (FastEmbed BAAI/bge-small-en-v1.5 ONNX, ~33MB, CoreML-friendly) |
| Metric | Cosine similarity (MLX-compiled `_cosine_sim_batch`, numpy fallback) |
| Index type | Append mode, never drop+recreate |
| Cache bound | Default 256MB, hard cap 512MB, env `HLEDAC_LANCEDB_CACHE_MB` overrides |
| Hard override | `HLEDAC_ALLOW_LARGE_LANCEDB_CACHE=1` unlocks up to 1GB |

### semantic_store.py — FastEmbed + LanceDB semantic IOC search

| Property | Value |
|---|---|
| Table | `findings_v1` |
| Embed dim | 384 |
| Max text len | 512 tokens (bge-small max) |
| Max pending buffer | 10,000 (bounded for M1 8GB safety) |
| Lifecycle | `initialize()` BOOT → `buffer_finding()` per-finding (no I/O) → `flush()` WINDUP batch → `close()` TEARDOWN |
| Thread | CPU_EXECUTOR (never blocks event loop) |

### semantic_store_buffer.py — Semantic buffering seam

| Property | Value |
|---|---|
| Role | Inject `SemanticStore` into DuckDBShadowStore buffering pipeline |
| Fail-open | Missing store or any exception silently skipped — never blocks storage |

---

## Dedup Algorithm

| Property | Value |
|---|---|
| Primary dedup | RotatingBloomFilter (`pybloom_live`) — cross-run URL dedup pre-check |
| Hot cache | In-process `dict[str, str]` (fingerprint → finding_id), `OrderedDict` eviction for LRU, 10,000 bound |
| LMDB persistent | `LMDB_ROOT/dedup.lmdb`, namespace `dedup:{fingerprint_hex}` → finding_id (UTF-8 bytes), map_size=64MB |
| Semantic dedup | Embedding-based near-duplicate (LanceDB-based, optional, lazy init) |
| Write seam | `store_persistent_dedup()` called AFTER semantic dedup check in canonical path — LMDB write only after dedup pass/fail-open |
| Hot cache max | 10,000 (from `quality_assessment._DEDUP_HOT_CACHE_MAX`) |
| Dedup LMDB map_size | 64MB (`_DEDUP_LMDB_MAP_SIZE`) |

**DedupManager boundary:** Owns persistent LMDB, hot cache, semantic dedup cache. Separated from DuckDBShadowStore for testability.

---

## LMDB Configuration

### tools/lmdb_kv.py — Zero-copy KV store

| Property | Value |
|---|---|
| `DEFAULT_MAP_SIZE` | 256MB (`256 * 1024 * 1024`) |
| `MAX_KEYS` | 10,000 |
| `LMDB_WRITE_BATCH_SIZE` | 500 (hard cap) |
| Path resolution | `SPRINT_LMDB_ROOT / "kvstore.lmdb"` via canonical `paths.open_lmdb()` |
| Features | `buffers=True` for zero-copy reads, orjson for JSON serialization, async via `aiolmdb` if available |
| Key types | General KV store — used by checkpoint, attribution scorer, forensics, and others |

### memory/memory_manager.py — Session-scoped persistent memory

| Property | Value |
|---|---|
| `DEFAULT_MAP_SIZE` | 128MB (`128 * 1024 * 1024`) |
| Backend | LMDB zero-copy |
| Interface | `async put(session_id, key, val)`, `async get(session_id, key)` |
| Error | `ImportError` if LMDB unavailable (not MemoryPressureError) |
| Shared variant | `memory/shared_memory_manager.py` (5.4KB) |

### knowledge/wal.py — WAL Manager

| Property | Value |
|---|---|
| Path | `LMDB_ROOT/wal.lmdb` |
| Role | Write-ahead log for crash safety, replay state, pending sync markers, deadletters |
| Operations | `append()`, `flush()`, `replay_pending()`, `async_replay_all_pending_duckdb_sync()` |

---

## Memory Layer

### memory/memory_manager.py — Session-scoped persistent memory

| Property | Value |
|---|---|
| Backend | LMDB with zero-copy reads |
| Key operations | `async put(session_id, key, val)`, `async get(session_id, key)` |
| Default map_size | 128MB (`DEFAULT_MAP_SIZE = 128 * 1024 * 1024`) |
| Files | `memory_manager.py` (15.4KB), `shared_memory_manager.py` (5.4KB), `__init__.py` |
| Error | `ImportError` if LMDB unavailable (not MemoryPressureError) |

**RAG backend:** LanceDB + igraph for graph analytics (not NetworkX).

---

## Embedding Pipeline

### embedding_pipeline.py (1,064 lines, 37.4KB) — Semantic search integration (P13)

| Property | Value |
|---|---|
| Role | Primary embedder using `MLXEmbeddingManager` singleton |
| Model | ModernBERT (256d MRL — Matryoshka Representation Learning) |
| Pattern | Singleton — loads once, reuses across all calls |
| Batch size | `_BATCH_SIZE` (default, see source) |
| Thread | CPU executor for async operations |
| Functions | `embed_query()`, `embed_document()`, `generate_embeddings_async()`, `embed_query_async()` |
| Fusion | MMR (Maximal Marginal Relevance) + RRF (Reciprocal Rank Fusion) for search |
| Caching | MLXEmbeddingManager singleton — prompt cache managed via `make_prompt_cache()` |

**CPU executor:** All embedding via `CPU_EXECUTOR` — never blocks event loop.

### Distributed embedding components

| Component | Model | Dimension | Location |
|---|---|---|---|
| FastEmbed (semantic search) | BAAI/bge-small-en-v1.5 ONNX | 384 | `knowledge/semantic_store.py` |
| ModernBERT (embedding_pipeline) | mlx-community/answerdotai-ModernBERT-base-6bit | 256 | `embedding_pipeline.py` |
| VisionEncoder | Not specified in this audit scope | 1024 | `multimodal/` |

**Cache:** `EMBEDDING_CACHE: Path = RUNTIME_BASE / "embeddings"` (from XDG base dir spec, path `~/.hledac/embeddings/`)

---

## Export Formats

### sprint_exporter.py — Primary export (4,960 lines, 209KB)

**Output dict keys (canonical):**
```
query, seed_context, source_family_summary, terminal_coverage,
corroboration, capability, gaps, planner_actions, next_pivots,
investigation_packet, runtime_diagnosis, doh_recommendation,
wayback_recommendation, sprint_summary, next_actions,
focus_recommendations, run_truth_note, branch_truth,
best_first_move, why_this_run_matters, canonical_run_summary,
hypothesis_pack, scorecard, pivot_recommended, product_value_summary,
recommended_next_engineering_action, recommended_next_investigation_action,
branch_value, sprint_trend, source_leaderboard, runtime_truth,
feed_verdict, public_verdict, signal_path, runtime_loop_telemetry
```

### stix_exporter.py — STIX 2.1 (1,816 lines, 66KB)

| Property | Value |
|---|---|
| Spec version | STIX 2.1 |
| Bundle type | `bundle`, id `bundle--<uuid>`, `spec_version = "2.1"` |
| Timestamps | RFC3339 |

**STIX object types built via `_build_*_object` functions:**
- `_build_malware_object`
- `_build_tool_object`
- `_build_attack_pattern_object`
- `_build_campaign_object`
- `_build_intrusion_set_object`
- `_build_identity_object`
- `_build_evidence_chain_object`
- `_build_root_cause_object`

**Builtins path:** Plain dicts (no `stix2` package required) that are STIX-compatible and pass basic shape validation. If `stix2` available, uses it for full object construction.

**B.5/B.7 rule:** Never invents IOC/indicator/malware objects when no accepted findings are present — exports metadata-safe diagnostic bundle only.

### jsonld_exporter.py — JSON-LD (500 lines)

**Context:** Shared with stix/markdown reporters via `_ROOT_CAUSE_LABELS` canonical map.

### sprint_markdown_reporter.py — Markdown reports (1,193 lines)

**Sections:**
```
Source Leaderboard | Phase Timings | Executive Summary | Research Metrics
Threat Actors | Top Findings | Analyst Brief
  └── Key Findings | Evidence Chains | Next Actions | Open Questions
      Source Families | Corroboration | Evidence Gaps | Risk Hypotheses
      Feed Cluster | Pivot Recommendations
Evidence Envelope Findings | Finding | Identity Candidates
```

---

## Runtime Diagnosis Categories

### _compute_runtime_diagnosis() — input/output

**Input:** `compute_runtime_loop_telemetry()` output — `{slowest_phases, lane_timings, timer_event_count, phase_totals_s, ...}`

**Output:**
```python
{
  "status": "unavailable" | str,      # "unavailable" if no telemetry
  "slowest_phase": str,               # phase with highest elapsed
  "slow_lanes": [...],                # phases with elapsed > 50% of slowest
  "likely_bottleneck": str,
  "recommended_runtime_action": str,
  "memory_safe_to_expand": bool
}
```

**Rules (5 explicit bottleneck + 2 auxiliary):**
| Rule | Condition | recommended_runtime_action |
|---|---|---|
| 1 | slowest phase = `public_lane` / `public_discovery` | `diagnose_public_provider_or_replay` |
| 2 | slowest phase = `wayback_lane` | `wayback_replay_or_limit_urls` |
| 3 | slowest phase = `ct_lane` (any) | `ct_timeout_tuning` |
| 4 | slowest phase = `graph_accumulation` | `graph_batch_or_defer` |
| 5 | slowest phase = `export` | `export_payload_trim` |
| 6 | (memory check) uma pressure | `memory_safe_to_expand=False` (field, not action) |
| 7 | missing telemetry | early `return {"status": "unavailable"}` |

**primary_action values** (from `export_sprint` dict):
```
add_or_use_provider_replay_fixture, continue_pivot_expansion,
improve_sidecar_input_or_mapping, none, refine, broaden, narrow,
new_approach, update_patterns, update_extraction_logic,
update_quality_thresholds, update_dedup_logic, check_registry,
repeat_live_run, continue_monitoring, skip_branch, retry_known_sources
```

---

## GHOST_* / HLEDAC_* Env Variables

### GHOST_* (runtime paths and sizing)

| Variable | Default | Purpose |
|---|---|---|
| `GHOST_RAMDISK` | `""` (empty — must be set or `/Volumes/ghost_tmp` mounted) | Ramdisk mount path; defaults to empty, user must configure |
| `GHOST_LMDB_MAX_SIZE_MB` | 512MB | LMDB map_size for all LMDB stores |
| `GHOST_EXPORT_DIR` | `~/.hledac/cti` (overrides CTI_EXPORT_DIR) | CTI export output directory |
| `GHOST_DUCKDB_MAX_TEMP` | — | DuckDB temp data max size |
| `GHOST_DUCKDB_MEMORY` | — | DuckDB memory limit |

### HLEDAC_* (feature flags and sizing)

| Variable | Default | Purpose |
|---|---|---|
| `HLEDAC_RESEARCH_MODE` | `standard` | quick/standard/deep/extreme/autonomous |
| `HLEDAC_MEMORY_LIMIT_MB` | — | Memory limit override |
| `HLEDAC_MAX_STEPS` | — | Max research steps |
| `HLEDAC_LOG_LEVEL` | — | DEBUG/INFO/WARN/ERROR |
| `HLEDAC_M1_OPTIMIZED` | `true` | M1 optimization preset enable |
| `HLEDAC_LANCEDB_CACHE_MB` | 256MB | LanceDB cache size (M1-safe default) |
| `HLEDAC_ALLOW_LARGE_LANCEDB_CACHE` | — | Set to `1` to allow up to 1GB LanceDB cache |
| `HLEDAC_SPRINT_STORE` | — | Sprint store root override |

**paths.py also reads:** `GHOST_RAMDISK`, `GHOST_LMDB_MAX_SIZE_MB` (and internally uses `HLEDAC_SPRINT_STORE` for sprint store root).

---

## File Paths (from paths.py)

### RAMdisk / fallback roots
```
RAMDISK_ROOT     = /Volumes/ghost_tmp  (or GHOST_RAMDISK env)
FALLBACK_ROOT    = ~/.hledac_fallback_ramdisk (if RAMDISK inactive)
CACHE_ROOT       = RAMDISK_ROOT / "cache"
LIGHTRAG_ROOT    = RAMDISK_ROOT / "lightrag"
RAMDISK_ACTIVE   = True/False (mount check: st_dev differs from parent)
```

### Runtime path constants
```
DB_ROOT          = RAMDISK_ROOT / "duckdb"
LMDB_ROOT        = RAMDISK_ROOT / "lmdb"
SPRINT_LMDB_ROOT = LMDB_ROOT / "sprint"
EVIDENCE_ROOT    = RAMDISK_ROOT / "evidence"
KEYS_ROOT        = RAMDISK_ROOT / "keys"
TOR_ROOT         = RAMDISK_ROOT / "tor"
NYM_ROOT         = RAMDISK_ROOT / "nym"
I2P_ROOT         = RAMDISK_ROOT / "i2p"
RUNS_ROOT        = RAMDISK_ROOT / "runs"
SOCKETS_ROOT     = RAMDISK_ROOT / "sockets"
SPRINT_STORE_ROOT = (from HLEDAC_SPRINT_STORE env or fallback)
IOC_DB_PATH      = Sprint store root / "iocs.db"
```

### Sprint artifact helpers
```
get_sprint_parquet_dir(sprint_id)     → SPRINT_STORE_ROOT / sprint_id / "parquet"
get_ioc_db_path()                     → IOC_DB_PATH
get_sprint_report_path(sprint_id)    → SPRINT_STORE_ROOT / sprint_id / "report.md"
get_sprint_json_report_path(sprint_id) → SPRINT_STORE_ROOT / sprint_id / "report.json"
get_sprint_next_seeds_path(sprint_id) → SPRINT_STORE_ROOT / sprint_id / "next_seeds.json"
```

### XDG Base Directory (Sprint F208A)
```
RUNTIME_BASE     = ~/.hledac (XDG: HOME/.hledac)
CTI_EXPORT_DIR   = RUNTIME_BASE / "cti"
RUNTIME_STATE    = RUNTIME_BASE / "state"
EMBEDDING_CACHE  = RUNTIME_BASE / "embeddings"
BENCHMARK_CACHE  = RUNTIME_BASE / "benchmarks"
```

### DuckDB shadow analytics
```
shadow_analytics.duckdb  →  DB_ROOT / "shadow_analytics.duckdb"   (file mode)
                             :memory:                              (RAMDISK mode)
PRAGMA threads=2 at connection init (M1 8GB conservative)
```

---

## Key Findings

1. **DuckDB thread-safety:** Single-threaded executor + `run_in_executor` for all async public methods — safe for M1 8GB. No `asyncio.Lock` on write path.

2. **DuckDB deferred import:** DuckDB not imported at module level — avoids boot-time import cost and ensures thread-affine conn creation.

3. **LMDB no direct handles in DuckDBShadowStore:** WAL/Dedup/Semantic/Graph boundaries extracted into delegate managers; DuckDBShadowStore owns none.

4. **Dedup write-after-check:** `store_persistent_dedup()` called after semantic dedup check — LMDB write only happens post-dedup pass/fail-open.

5. **RotatingBloomFilter:** Cross-run URL dedup pre-check is RotatingBloomFilter (bounded, not ScalableBloomFilter). Hot cache is `OrderedDict` LRU 10K.

6. **LanceDB cache bound:** 256MB default, 512MB hard cap, 1GB with override. Append-only index (never drop+recreate).

7. **STIX 2.1 builtin path:** Plain dicts — no stix2 package required; passes basic shape validation.

8. **`embedding_pipeline.py` at root** (1,064 lines) — ModernBERT 256d MRL, singleton MLX, MMR+RRF fusion. Distributed helper modules in `semantic_store.py`, `vector_store.py`, `brain/`.

9. **5 explicit bottleneck rules + 2 auxiliary** in `_compute_runtime_diagnosis()` — public_lane/public_discovery→diagnose_public_provider_or_replay, wayback_lane→wayback_replay_or_limit_urls, ct_lane→ct_timeout_tuning, graph_accumulation→graph_batch_or_defer, export→export_payload_trim, else→observe_only. Bottleneck string pattern: `{phase}_slow`.

# System Architecture Overview — Hledac Universal (2026-05-23)

> Derived from: storage audit + codebase structure + domain glossary.
> Prereq: `STORAGE_KNOWLEDGE_AUDIT_20260523_0611.md`

---

## Architecture Map

```
python -m hledac.universal           ← canonical CLI entry point
└── core/__main__.py::run_sprint()    ← SOLE canonical sprint owner (F186A)
    ├── core/resource_governor.py     ← M1 UMA advisory
    ├── runtime/sprint_scheduler.py  ← RUNTIME WORKER (not owner)
    │   ├── pipeline/live_public_pipeline.py
    │   ├── pipeline/live_feed_pipeline.py
    │   ├── coordinators/fetch_coordinator.py  (curl_cffi stealth, JA3)
    │   ├── coordinators/memory_coordinator.py
    │   ├── coordinators/security_coordinator.py
    │   └── 20+ other coordinators
    ├── knowledge/duckdb_store.py     ← CANONICAL WRITE CORE (Tier 1-2)
    │   ├── WALManager (wal.py)
    │   ├── DedupManager (dedup.py)   ← RotatingBloomFilter + LMDB
    │   ├── SemanticStoreBuffer (buffer.py)
    │   └── GraphAttachmentStore (graph_attachment.py)
    ├── knowledge/lancedb_store.py     ← Identity/entity store (384d FastEmbed)
    ├── knowledge/semantic_store.py    ← FastEmbed + LanceDB IOC search (384d)
    ├── knowledge/vector_store.py      ← ModernBERT 256d text+image ANN
    ├── knowledge/rag_engine.py         ← Primary RAG + HNSWVectorIndex
    ├── knowledge/graph_service.py     ← DuckPGQ graph facade
    ├── knowledge/ioc_graph.py          ← IOC entity graph
    ├── brain/hermes3_engine.py        ← Hermes3 LLM inference (MLX)
    ├── brain/inference_engine.py      ← Multi-hop evidence reasoning
    ├── brain/synthesis_runner.py      ← Synthesis orchestration
    ├── brain/hypothesis_engine.py      ← Hypothesis generation (170KB, largest)
    ├── export/sprint_exporter.py      ← PRIMARY export (4,960 lines)
    ├── export/stix_exporter.py        ← STIX 2.1 (builtin path, no stix2 dep)
    ├── export/export_manager.py       ← Graph/JSON/MD/HTML export
    └── embedding_pipeline.py          ← ModernBERT 256d MRL, singleton MLX
```

---

## Canonical Entry Points (F186A ROLE TABLE)

| Role | Function | Owner | Notes |
|---|---|---|---|
| canonical | `run_sprint()` | YES | SOLE canonical sprint owner |
| canonical | `_runtime_truth()` | YES | canonical run boundary |
| canonical | `_is_meaningful_run()` | YES | canonical run boundary |
| canonical | `run_pre_sprint_checks()` | YES | pre-flight |
| canonical | `write_sprint_delta()` | YES | teardown |
| shell | `main() --sprint` | NO | delegates to `run_sprint()` |
| alternate | `main() --ct-pivot` | NO | CT log tool, no sprint |
| alternate | `main() --pivot` | NO | semantic pivot, no sprint |

**Canonical path:** `python -m hledac.universal --sprint` → `core.__main__.run_sprint()`

---

## SprintSchedulerResult Fields

**Canonical output of `SprintScheduler.run()`:**

| Field | Type | Description |
|---|---|---|
| `cycles_started` | int | Fetch cycles initiated |
| `cycles_completed` | int | Fetch cycles completed all phases |
| `unique_entry_hashes_seen` | int | Deduped entries processed |
| `duplicate_entry_hashes_skipped` | int | Duplicates filtered |
| `findings_accepted` | int | Accepted findings count |
| `findings_rejected` | int | Rejected (quality/dedup) count |
| `new_iocs` | int | New IOC nodes |
| `runtime_s` | float | Total runtime seconds |
| `final_phase` | str | Last phase reached |
| `export_paths` | list[str] | Output file paths |
| `abort_reason` | str | Abort reason if any |
| `stop_requested` | bool | Wind-down requested |
| `public_error` | str | Public pipeline error |
| `ct_log_error` | str | CT log error |
| `public_provider_selection_debug` | dict | Provider debug info |
| `ct_log_discovered` | int | CT log entries discovered |
| `ct_log_stored` | int | CT log entries stored |
| `ct_log_accepted_findings` | int | CT log accepted findings |

---

## Coordinator Domain Map (25 coordinators across 5 domains)

### core
- `UniversalResearchCoordinator` → `.research_coordinator`
- `UniversalExecutionCoordinator` → `.execution_coordinator`
- `UniversalSecurityCoordinator` → `.security_coordinator`
- `UniversalMonitoringCoordinator` → `.monitoring_coordinator`
- `UniversalMemoryCoordinator` → `.memory_coordinator`
- `UniversalValidationCoordinator` → `.validation_coordinator`

### advanced
- `UniversalAdvancedResearchCoordinator` → `.advanced_research_coordinator`
- `UniversalSwarmCoordinator` → `.swarm_coordinator`
- `UniversalMetaReasoningCoordinator` → `.meta_reasoning_coordinator`
- `PrivacyEnhancedResearch` → `.privacy_enhanced_research`

### optimization
- `AgentPerformanceOptimizer` → `.performance_coordinator`
- `AgentBenchmarker` → `.benchmark_coordinator`
- `IntelligentResourceAllocator` → `.resource_allocator`
- `ResearchOptimizer` → `.research_optimizer`

### infrastructure
- `UniversalCoordinator` → `.base`
- `CoordinatorRegistry` → `.coordinator_registry`
- `OperationTrackingMixin` → `.mixins` ⚠️ NOTE: `coordinators/mixins.py` does NOT exist on disk. The base `UniversalCoordinator` class directly implements all mixin concerns (op lifecycle, load factor, memory pressure) inline in `base.py`. `_catalog.py` `MIXIN_LOCATIONS` dict references a non-existent module.
- `MemoryPressureLevel` → `.enums`

### specialized
- `FetchCoordinator` → `.fetch_coordinator` (curl_cffi stealth, JA3 fingerprint)
- `GraphCoordinator` → `.graph_coordinator`
- `ArchiveCoordinator` → `.archive_coordinator`
- `ClaimsCoordinator` → `.claims_coordinator`
- `MultimodalCoordinator` → `.multimodal_coordinator`
- `RenderCoordinator` → `.render_coordinator`
- `AgentCoordinationEngine` → `.agent_coordination_engine`

---

## Transport Layer

**Base classes** (`transport/base.py`):
- `TransportConfig` — configuration dataclass
- `TransportResult` — result wrapper
- `TransportAdapter(ABC)` — adapter interface for HTTP transports
- `Transport(ABC)` — node-transport overlay interface

**Circuit breaker** (`transport/circuit_breaker.py`):
- `CBState(Enum)` — CLOSED/OPEN/HALF_OPEN states
- `CircuitBreakerSnapshot` — state snapshot
- `CircuitDecision` — per-request decision
- `CircuitBreaker` — breaker implementation

**Active transports** (`transport/`):
- `curl_cffi_transport.py` — PRIMARY stealth HTTP (JA3 fingerprint, FetchCoordinator only)
- `httpx_transport.py` — HTTP/2 transport (gated by `HLEDAC_ENABLE_HTTPX_H2`)
- `gopher_transport.py` — Gopher protocol
- `httpx_client.py` — HTTPX client adapter
- `curl_cffi_fetch.py` — curl_cffi fetch interface

**Policy:** curl_cffi only in FetchCoordinator. Never aiohttp in FetchCoordinator.

---

## Brain Modules (LLM + Reasoning)

Sorted by size:

| Module | Size | Role |
|---|---|---|
| `hypothesis_engine.py` | 170KB | Hypothesis generation + Dempster-Shafer EIG calculation |
| `hermes3_engine.py` | 97KB | Hermes3 LLM inference via MLX (GenericResult, FetchResult, DeepReadResult, AnalyseResult, SynthesizeResult) |
| `inference_engine.py` | 81KB | Multi-hop evidence reasoning (Evidence, InferenceStep, Hypothesis, ResolvedEntity, MultiHopPath) |
| `synthesis_runner.py` | 65KB | Synthesis orchestration |
| `ner_engine.py` | 56KB | Named entity recognition |
| `prompt_cache.py` | — | MLX prompt cache management |
| `adaptive_context_policy.py` | 7KB | Context window optimization |
| `model_engine.py` | 6KB | Model lifecycle management |
| `paged_attention_cache.py` | 5KB | Paged attention caching |

**MLX inference:** Hermes3 via `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` (2GB, M1 8GB safe).

---

## Knowledge Modules (Storage + Retrieval)

Sorted by size:

| Module | Size | Role |
|---|---|---|
| `duckdb_store.py` | 253KB | Canonical sprint facts store, 7 tables, single TPE |
| `graph_rag.py` | 91KB | GraphRAG orchestrator (CentralityScores, Community, GraphContradiction, GraphRAGOrchestrator) |
| `analyst_workbench.py` | 76KB | Analyst workspace |
| `rag_engine.py` | 62KB | Primary RAG + HNSWVectorIndex for grounding |
| `lancedb_store.py` | 56KB | Identity/entity store (384d FastEmbed, singleton, append-only) |
| `entity_linker.py` | 31KB | Entity resolution |
| `ioc_graph.py` | 29KB | IOC entity graph |
| `graph_attachment.py` | 25KB | Graph attachment facade |
| `evidence_chain.py` | 23KB | Evidence chain tracking |
| `quality_assessment.py` | 21KB | Entropy, dedup fingerprint, rejection ledger |
| `semantic_store.py` | — | FastEmbed 384d + LanceDB, buffer (10K max), CPU executor |
| `semantic_store_buffer.py` | — | Semantic buffering seam (fail-open) |
| `vector_store.py` | — | LanceDB text (256d MRL ModernBERT) + image (1024d) ANN |
| `sprint_diff_engine.py` | — | Delta tracking between sprints |
| `target_memory.py` | — | Cross-sprint entity persistence |
| `wal.py` | — | WALManager (LMDB at LMDB_ROOT/wal.lmdb) |
| `dedup.py` | — | DedupManager (RotatingBloomFilter + LMDB 64MB + hot cache 10K) |

---

## Export Modules

| Module | Size | Role |
|---|---|---|
| `sprint_exporter.py` | 209KB | PRIMARY export — `export_sprint(handoff, store, export_mode)` |
| `export_manager.py` | 66KB | Graph/JSON/MD/HTML export via ExportManager |
| `stix_exporter.py` | 66KB | STIX 2.1 bundle (9 object types, builtin dict path) |
| `sprint_markdown_reporter.py` | 50KB | Markdown report (9 sections) |
| `formatters.py` | 22KB | Export formatters |
| `jsonld_exporter.py` | 20KB | JSON-LD export |

**Export signature:** `async def export_sprint(store, handoff: ExportHandoff, sprint_id, enable_security_enrichment, export_mode)`

---

## Brain Contract: Hermes3Engine GenericResult Types

```
GenericResult         ← base result wrapper
├─ FetchResult         ← web fetch output
├─ DeepReadResult      ← deep reading output
├─ AnalyseResult       ← analysis output
└─ SynthesizeResult    ← synthesis output

Config: HermesConfig (MLX metal, lazy eval, kv_bits=4, max_kv_size=8192)
```

---

## Knowledge Graph (DuckPGQ)

**GraphService** facade (`knowledge/graph_service.py`):
- `_ModuleSeenIOCs` — module-level IOC dedup
- `_ModuleSeenRels` — module-level relationship dedup
- `inject_graph()` — attaches IOCGraph to sprint
- `upsert_ioc()` / `upsert_rel()` — graph writes
- `query_graph()` — graph queries
- `reset_session()` — called at sprint teardown

**GraphRAGOrchestrator** (`knowledge/graph_rag.py`):
- CentralityScores, Community, GraphContradiction

---

## FetchCoordinator (Stealth HTTP)

| Property | Value |
|---|---|
| HTTP library | curl_cffi (JA3 fingerprint, stealth) |
| Location | `coordinators/fetch_coordinator.py` |
| Thread model | Single-worker TPE for connection safety |
| Fallback | httpx (HTTP/2, gated by `HLEDAC_ENABLE_HTTPX_H2`) |
| NEVER | aiohttp in FetchCoordinator |

---

## Memory Layer

| Module | Size | Role |
|---|---|---|
| `memory/memory_manager.py` | 15.4KB | Session-scoped LMDB store, 128MB map_size |
| `memory/shared_memory_manager.py` | 5.4KB | Shared variant |
| `memory/__init__.py` | 1.1KB | Exports `get_memory_manager()`, `close_memory_manager()` |

---

## Key Call Chains

### Canonical sprint write path
```
run_sprint()
  → SprintScheduler.run()
    → live_public_pipeline / live_feed_pipeline
      → FetchCoordinator (curl_cffi stealth)
        → CanonicalFinding
          → DuckDBShadowStore.async_ingest_findings_batch()
            → WALManager.append()           [WAL]
            → DedupManager.check()           [RotatingBloomFilter + LMDB]
            → DuckDB INSERT                  [sprint_delta, shadow_findings]
            → SemanticStoreBuffer.buffer()   [FastEmbed + LanceDB]
            → GraphAttachmentStore           [optional graph]
            → WALManager.flush()            [sync marker]
```

### Export path
```
SprintScheduler.run() teardown
  → core.__main__._print_scorecard_report()
    → export_sprint(handoff, store)
      → sprint_exporter (markdown, JSON, graph)
      → stix_exporter (STIX 2.1)
      → export_manager (sigma, timeline, GEXF)
```

---

## GHOST_* / HLEDAC_* Env Variables

| Variable | Default | Purpose |
|---|---|---|
| `GHOST_RAMDISK` | `/Volumes/ghost_tmp` | Active ramdisk mount |
| `GHOST_LMDB_MAX_SIZE_MB` | 512MB | LMDB map_size (all stores) |
| `GHOST_EXPORT_DIR` | `~/.hledac/cti` | CTI export output |
| `GHOST_DUCKDB_MAX_TEMP` | — | DuckDB temp limit |
| `GHOST_DUCKDB_MEMORY` | — | DuckDB memory limit |
| `HLEDAC_RESEARCH_MODE` | `standard` | quick/standard/deep/extreme/autonomous |
| `HLEDAC_MEMORY_LIMIT_MB` | — | Memory limit override |
| `HLEDAC_MAX_STEPS` | — | Max research steps |
| `HLEDAC_LOG_LEVEL` | — | DEBUG/INFO/WARN/ERROR |
| `HLEDAC_M1_OPTIMIZED` | `true` | M1 optimization preset |
| `HLEDAC_LANCEDB_CACHE_MB` | 256MB | LanceDB cache (M1-safe) |
| `HLEDAC_ALLOW_LARGE_LANCEDB_CACHE` | — | Set `1` for up to 1GB LanceDB cache |
| `HLEDAC_SPRINT_STORE` | — | Sprint store root override |

---

## M1 8GB Constraints (enforced everywhere)

| Constraint | Value | Enforcement |
|---|---|---|
| DuckDB PRAGMA threads | 2 | `duckdb_store.py` `_init_connection()` |
| LanceDB cache | 256MB default, 512MB hard cap | `lancedb_store.py` `_resolve_lancedb_cache_size()` |
| LMDB (kvstore) | 256MB | `tools/lmdb_kv.py` `DEFAULT_MAP_SIZE` |
| LMDB (dedup) | 64MB | `dedup.py` `_DEDUP_LMDB_MAP_SIZE` |
| LMDB (memory) | 128MB | `memory/memory_manager.py` |
| Hot cache | 10,000 entries | `dedup.py` `_DEDUP_HOT_CACHE_MAX` |
| Semantic pending buffer | 10,000 | `semantic_store.py` `_MAX_PENDING` |
| Batch chunking | 500 max | All `async_ingest_findings_batch()` callers |
| ThreadPoolExecutor | `max_workers=1` | DuckDB single-worker, MLX safety |
| RAM guard | 85% threshold | MultimodalEnricher RAM guard blocks heavy vision |

---

## Key Findings

1. **DuckDB thread-safety:** Single-threaded executor + `run_in_executor` for all async public methods — safe for M1 8GB. No `asyncio.Lock` on write path.

2. **DuckDB deferred import:** DuckDB not imported at module level — avoids boot-time import cost and ensures thread-affine conn creation.

3. **LMDB no direct handles in DuckDBShadowStore:** WAL/Dedup/Semantic/Graph boundaries extracted into delegate managers; DuckDBShadowStore owns none.

4. **Dedup write-after-check:** `store_persistent_dedup()` called after semantic dedup check — LMDB write only happens post-dedup pass/fail-open.

5. **RotatingBloomFilter:** Cross-run URL dedup pre-check is RotatingBloomFilter (bounded, not ScalableBloomFilter). Hot cache is `OrderedDict` LRU 10K.

6. **LanceDB cache bound:** 256MB default, 512MB hard cap, 1GB with override. Append-only index (never drop+recreate).

7. **STIX 2.1 builtin path:** Plain dicts — no stix2 package required; passes basic shape validation.

8. **embedding_pipeline.py** (1,064 lines) at root — ModernBERT 256d MRL, singleton MLX, MMR+RRF fusion.

9. **5 explicit bottleneck rules** in `_compute_runtime_diagnosis()` — public_lane/public_discovery→diagnose_public_provider_or_replay, wayback_lane→wayback_replay_or_limit_urls, ct_lane→ct_timeout_tuning, graph_accumulation→graph_batch_or_defer, export→export_payload_trim, else→observe_only. Bottleneck string pattern: `{phase}_slow`.

10. **GHOST_RAMDISK env** drives all runtime paths; RAMDISK_ACTIVE validated via `st_dev != parent.st_dev` mount check.

11. **25 coordinators** across 5 domains (core/advanced/optimization/infrastructure/specialized) — includes 7 specialized (Fetch, Graph, Archive, Claims, Multimodal, Render, AgentCoordination).

12. **hypothesis_engine.py** (170KB) — largest brain module; largest overall after duckdb_store.

13. **memory/** directory exists with `memory_manager.py` (15.4KB) and `shared_memory_manager.py` (5.4KB) — LMDB-backed session-scoped store with 128MB map_size.

14. **Transport layer:** `TransportAdapter` + `Transport` abstract base classes, `CircuitBreaker` with CLOSED/OPEN/HALF_OPEN states. curl_cffi PRIMARY, httpx secondary, aiohttp NEVER in FetchCoordinator.

## rl/ — Reinforcement Learning State

**Directory:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/rl/`

### Files (7 total, 940 lines combined)

| File | Lines | Size (bytes) | Purpose |
|------|-------|-------------|---------|
| `__init__.py` | 28 | 720 | Module exports: `QNetwork`, `QMixer`, `QMIXAgent`, `MARLReplayBuffer`, `SprintPolicyManager` |
| `qmix.py` | 234 | 9,550 | QMIX MARL implementation: QNetwork, QMixer, QMIXAgent, JointModel, QMIXJointTrainer (all require MLX) |
| `sprint_policy_manager.py` | 385 | 15,325 | SprintPolicyManager + SprintPolicyState dataclass — epsilon-greedy RL advisor (enabled=False default) |
| `state_extractor.py` | 74 | 2,433 | StateExtractor — extracts observation vector (default 12-dim) from thread_state + global_state |
| `replay_buffer.py` | 94 | 3,382 | MARLReplayBuffer — numpy-based replay buffer (capacity=50000, state_dim=12, n_agents=5) |
| `actions.py` | 19 | 368 | Action constants: ACTION_CONTINUE(0), ACTION_FETCH_MORE(1), ACTION_DEEP_DIVE(2), ACTION_BRANCH(3), ACTION_YIELD(4), ACTION_DIM=5 |
| `.sprint_policy_state.json` | 107 | 1,037 | Persisted RL state (human-readable JSON) |
| `.sprint_policy_state.json.zst` | — | 134 | Zstd-compressed backup of policy state |

### Policy State Structure (`.sprint_policy_state.json`)

```json
{
  "sprint_sequence_number": 124,
  "epsilon": 0.0999,
  "total_reward": 606.2,
  "sprint_rewards": [6.0, 12.6, ...],   ←  last ~100 rewards
  "source_weights": [1.0, 1.0, ...],     ←  per-source adaptive weights
  "recent_quality_decisions": [...]      ←  FindingQualityDecision records
}
```

**Fields:**
- `sprint_sequence_number` (int): How many sprints have run (counter)
- `epsilon` (float): Current exploration epsilon (epsilon-greedy), floor=0.05, decay=0.999/sprint
- `total_reward` (float): Cumulative reward across all sprints
- `sprint_rewards` (list[float]): Recent per-sprint rewards (rolling)
- `source_weights` (list[float]): Adaptive per-source weights (B.6 bounds [0.3, 2.5])
- `recent_quality_decisions` (list): FindingQualityDecision records for reward signal

### Architecture

- **SprintPolicyManager** (385 lines): Opt-in RL advisor, `enabled=False` by default. Injected via `inject_policy_manager()` → `SprintScheduler.run(policy_manager=...)`. Uses epsilon-greedy (no Q-learning update in current impl). Exploration interval: every 5th sprint. Reward computed from `SprintSchedulerResult` via `update_with_quality_decisions()`.

- **QMIX** (234 lines, MLX-only): Full QMIX decomposition networks — QNetwork (fc1→fc2→q_out), QMixer (hyper_w1/w2/b1/b2), QMIXAgent (own QNet + target QNet + Adam 1e-3), JointModel, QMIXJointTrainer. ALL raise `ImportError("requires MLX")` when mlx is not available — non-functional without Apple Silicon.

- **StateExtractor** (74 lines): Builds 12-dim observation vector from thread_state (fetch/branch/depth stats) + global_state (duckdb/graph metrics). Falls back to numpy if MLX unavailable.

- **MARLReplayBuffer** (94 lines): NumPy-based (not MLX), capacity=50000, state_dim=12, n_agents=5. `.save()`/`.load()` via numpy binary format.

- **actions.py** (19 lines): 5 actions (CONTINUE, FETCH_MORE, DEEP_DIVE, BRANCH, YIELD). ACTION_DIM=5.

### Persistence

- **Yes** — RL state persists across sprints via `.sprint_policy_state.json` (plain JSON, human-readable)
- Path: `rl/.sprint_policy_state.json` (940 bytes)
- Backup: `.sprint_policy_state.json.zst` (134 bytes, zstd compressed)
- Write method: `SprintPolicyManager._save()` → json.dump + optional zstd compression
- Fail-safe: does not crash on write errors (logged warning only)

### Hyperparameters

| Param | Value | Location |
|-------|-------|----------|
| Learning rate | 1e-3 (Adam) | qmix.py QMIXAgent.__init__ |
| Hidden dim | 64 | qmix.py QNetwork.__init__ |
| Epsilon start | 0.1 | sprint_policy_manager.py `_DEFAULT_EPSILON` |
| Epsilon floor | 0.05 | sprint_policy_manager.py (max(0.05, epsilon * 0.999)) |
| Epsilon decay | 0.999/sprint | sprint_policy_manager.py `_epsilon` update |
| Exploration interval | every 5 sprints | sprint_policy_manager.py `_exploration_interval` |
| Replay capacity | 50000 | replay_buffer.py `MARLReplayBuffer.__init__` |
| State dimension | 12 | state_extractor.py `StateExtractor.__init__`, replay_buffer.py |
| N agents | 5 | replay_buffer.py |
| Source weight bounds | [0.3, 2.5] | sprint_policy_manager.py `_adapt_source_weights_from_feedback` |

### Security Assessment

- **No secrets/credentials** — policy state is reward/epsilon values only
- **Human-readable** — `.sprint_policy_state.json` is plain JSON, easily inspected
- **No PII** — sprint_sequence_number, epsilon, rewards are operational metrics
- **Zstd compression** — `.zst` file is standard compression (not encryption)
- **No network transmission** — RL state is purely local, never transmitted
- **MLX dependency** — QMIX network (qmix.py) requires MLX; non-functional on non-Apple Silicon

### Current Status

- QMIX classes (QNetwork, QMixer, QMIXAgent, JointModel, QMIXJointTrainer) all raise `ImportError("requires MLX")` — not functional on non-M1 hardware
- `SprintPolicyManager` is the active RL component; it uses epsilon-greedy only (no Q-learning weight updates in current code path)
- `SprintPolicyManager.run()` returns action (0-4) but does not call QMIX network update

## reports/ — Sprint & Benchmark Reports

**Directory:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/reports/`

### File Inventory

| Extension | Count | Total Size |
|-----------|-------|------------|
| `.json`   |   59  | 1,251.8 KB |
| `.md`     |   51  |   834.4 KB |
| `.log`    |   14  |   199.8 KB |
| `.jsonl`  |   12  |    42.6 KB |
| `.txt`    |    1  |   249.4 KB |
| `.DS_Store` | 1 | 12.0 KB |
| **Total** | **129** | **2,165.6 KB** |

### Report File Categories

#### 1. Sprint Run Reports (`live_sprint_*.json`, `live_run_*.json`)

63 JSON files produced by `SprintScheduler` live runs and dry/diagnostic runs.

**Schema** (all share this 110-key structure):

| Key | Type | Description |
|-----|------|-------------|
| `measurement_id` | `str` | Unique measurement ID (e.g., `lsm_1778948036137_5664f9`) |
| `sprint_id` | `str` | Sprint ID (e.g., `8sa_1778948036137_d5b5cb`) |
| `mode` | `str` | `live` / `planned` / `dry_run` |
| `status` | `str` | `complete` / `failed` / `aborted` / `planned` |
| `start_time_iso` | `str` | ISO timestamp of sprint start |
| `end_time_iso` | `str` | ISO timestamp of sprint end |
| `planned_duration_s` | `float` | Requested sprint duration (seconds) |
| `actual_duration_s` | `float` | Actual wall-clock duration |
| `query` | `str` | Search query (e.g., `lockbit3.tw`) |
| `profile` | `str` | Profile name (e.g., `nonfeed_diagnostic180`) |
| `duration_s` | `int` | Configured sprint duration |
| `aggressive_mode` | `bool` | Whether aggressive mode enabled |
| `deep_probe` | `bool` | Whether deep probe scan was triggered |
| `uma_pre_used_gib` | `float` | UMA RAM used before sprint (GiB) |
| `uma_pre_swap_gib` | `float` | UMA swap used before sprint (GiB) |
| `uma_post_used_gib` | `float` | UMA RAM used after sprint (GiB) |
| `uma_post_swap_gib` | `float` | UMA swap used after sprint (GiB) |
| `findings_count` | `int` | Total findings discovered |
| `cycles_completed` | `int` | Research cycles completed |
| `cycles_started` | `int` | Research cycles started |
| `accepted_findings` | `int` | Findings accepted (passed quality gate) |
| `runtime_truth` | `dict` | Runtime gate results (gate pass/fail per phase) |
| `timing_truth` | `dict` | Timing budget reconciliation |
| `duckdb_seeds` | `list` | DuckDB seed entries that initiated the run |
| `findings` | `list` | Accepted canonical findings (empty in most runs) |
| `export_paths` | `dict` | Paths to exported artifacts (JSON, STIX, markdown) |
| `report_json_path` | `str` | Path to this report file |

**Sensitivity: LOW** — reports contain only operational metrics, query strings, and UMA memory snapshots. No credentials, tokens, or PII.

**Notable files** (largest by line count):

| File | Lines | Size | Notes |
|------|-------|------|-------|
| `live_sprint_f231c_domain_lockbit3.json` | 1598 | 43.8 KB | Largest sprint report |
| `live_sprint_f230d_domain_lockbit3.json` | 1594 | 43.8 KB | |
| `live_sprint_f230d_text_lockbit.json` | 1563 | 43.5 KB | |
| `live_sprint_300s_20260516.json` | 699 | 31.3 KB | |
| `live_sprint_300s.json` | 689 | 31.0 KB | |
| `nonfeed_diagnostic_domain_180.json` | 658 | 29.9 KB | |

#### 2. Benchmark Reports (`benchmarks/bench_m1_runtime_gates_*.jsonl`)

11 JSONL files from `m1_sustained_sprint.py` benchmark harness. Each file = 5 JSON lines.

**Schema** (per line):

| Key | Type | Example Value |
|-----|------|---------------|
| `type` | `str` | `"gate"` |
| `name` | `str` | `"runtime_gate"` |
| `timestamp` | `float` | Unix timestamp |
| `python_version` | `str` | `"3.14.4"` |
| `platform` | `str` | `"darwin"` |
| `free_threaded` | `bool` | Python free-threaded build? |
| `jit_available` | `bool` | PEP 749 JIT available? |
| `jit_active` | `bool` | PEP 749 JIT currently active? |

**Sensitivity: NONE** — no user data, only Python runtime metadata.

Also: `benchmarks/sprint_timer_overhead.jsonl` (1 JSONL, 432 bytes).

#### 3. Sprint Export Reports (`f22x_f23x_live_sprint_*.json`)

Subdirectory-structured reports from named sprint runs (F222 through F234 series). Each subdirectory (`f233c/`, `f233d/`) contains:

| File | Type | Description |
|------|------|-------------|
| `domain_gate.json` | JSON | Gate-phase snapshot (same 110-key schema) |
| `domain_gate.md` | Markdown | Human-readable gate summary |
| `domain_live.json` | JSON | Live-run snapshot (findings may be empty) |
| `domain_live.log` | Text | Annotated log from live run |
| `domain_live.md` | Markdown | Human-readable live summary (f233d only) |

**f233d/ example** (subdirectory with 5 files):

| File | Lines | Size |
|------|-------|------|
| `domain_gate.json` | 247 | 9.7 KB |
| `domain_gate.md` | 160 | 5.5 KB |
| `domain_live.json` | 173 | 6.5 KB |
| `domain_live.log` | 17 | 2.0 KB |
| `domain_live.md` | 87 | 2.0 KB |

**Sensitivity: LOW** — operational data only.

#### 4. Pre-flight Check Reports (`preflight*.json`)

8 JSON files from sprint preflight validation runs.

**Schema** (107 keys): same as sprint run reports but `status` always `planned`. Used to validate sprint readiness before execution.

#### 5. Capability Export (`capability_export_f228d.json`)

4,543 bytes, 83 lines. Export from capability truth reconciliation sprint F228D.

**Schema:**

| Key | Type | Description |
|-----|------|-------------|
| `sprint` | `str` | Sprint identifier (e.g., `F228D`) |
| `title` | `str` | Sprint title |
| `status` | `str` | `COMPLETE` / `PARTIAL` |
| `created` | `str` | ISO date |
| `problem` | `str` | Problem statement (text) |
| `root_causes` | `list` | Root cause objects with `id`, `description`, `severity`, `files_affected` |
| `fixes_applied` | `list` | Applied fix descriptions |
| `test_results` | `list` | Test outcome summaries |
| `test_infrastructure_issue` | `str` or `null` | Infrastructure issues noted |
| `files_modified` | `list` | Files touched by fixes |
| `verification` | `str` | Verification approach used |
| `canonical_verdict_mapping` | `dict` | Mapping of capabilities to verdicts |

**Sensitivity: LOW** — text descriptions of code issues. No credentials.

#### 6. Runtime Hygiene Event Report (`runtime_hygiene_event_truth.json`)

2,793 bytes, 83 lines. Sprint F216A runtime hygiene event-sourced lane truth seed report.

**Schema:**

| Key | Type | Description |
|-----|------|-------------|
| `sprint` | `str` | Sprint identifier |
| `title` | `str` | Sprint title |
| `status` | `str` | `COMPLETE` |
| `date` | `str` | Date string |
| `tests` | `list` | Test results |
| `changes` | `list` | Code changes applied |
| `abort_conditions_verified` | `list` | Verified abort conditions |
| `files_modified` | `list` | Files modified |

**Sensitivity: NONE** — internal test and change documentation.

#### 7. Audit Markdown Reports (`F214*.md`)

33 audit markdown files from sprint F214 series (Python 3.14 modernization audit sweep). All contain structured audit findings, benchmark results, and feasibility analyses.

**Size range:** 54 lines / 2.1 KB (`F214Q_REMOTE_DEBUG_OPSEC_GUARD.md`) to 793 lines / 41.6 KB (`F214M_PY314_MODERNIZATION_AUDIT_V2.md`).

**Common prefixes:** `F214A` through `F214ZSTD2` — covering: annotationlib pilot, blockers pre-sprint, ZSTD compression, CLI UX, dependency hygiene, GC benchmark, executor backpressure, import time audit, JIT benchmark, interpreter pool POC, process pool M1 audit, remote debug OPSEC guard, sprint readiness gate, archive extraction security, controlled smoke tests, teardown cleanup, UUIDv7 runtime IDs, execution optimizer correctness, transient artifact rollout.

**Also:** `PY314_ADVANCEMENTS_AUDIT.md` (29.3 KB, standalone Python 3.14 audit), `REPORT_CAPABILITY_EXPORT_F228D.md`, `REPORT_LIVE_RUNTIME_PRODUCT_PATH_CLOSURE.md`, `REPORT_RUNTIME_HYGIENE_EVENT_TRUTH.md`.

**Sensitivity: LOW** — technical audit notes, no credentials or PII.

#### 8. Diagnostic Reports (`nonfeed_diagnostic_*.json`, `f222f_nonfeed_dry_report.json`)

4 diagnostic runs with lockbit-themed queries. JSON schema identical to sprint run reports (110-key). Files:

| File | Lines | Size |
|------|-------|------|
| `nonfeed_diagnostic_domain_180.json` | 658 | 29.9 KB |
| `nonfeed_diagnostic_lockbit_180.json` | 658 | 29.6 KB |
| `f222f_nonfeed_dry_report.json` | 374 | 13.5 KB |
| `f222g_lockbit_domain_nonfeed_180.json` | 896 | 34.4 KB |
| `f222g_lockbit_text_nonfeed_180.json` | 894 | 33.7 KB |

**Sensitivity: LOW** — IOC-adjacent queries (e.g., `lockbit3.tw`) stored only as `query` field values, not as resolved indicators.

#### 9. STIX Bundles (`ghost_cti_*.stix.json`)

31 STIX 2.1 bundles located in `hledac/universal/` (not in `reports/`).

| Date | Count | Total Size |
|------|-------|------------|
| 2026-04-27 | 5 files | 4.8 KB |
| 2026-05-06 | 24 files | 25.9 KB |
| 2026-05-21 | 2 files | 1.9 KB |

**Schema:**

| Key | Value |
|-----|-------|
| `type` | `bundle` |
| `spec_version` | `2.1` |
| `created` | ISO timestamp |
| `id` | STIX bundle ID |
| `modified` | ISO timestamp |
| `objects` | List of 2 objects: `identity` + `report` |

**Object types present:** `identity` (Ghost Prime, identity_class=system), `report` (threat-report, indicator).

**Sensitivity: NONE** — empty bundles (0 findings, 0 identities, 0 evidence chains) generated as sprint export artifacts. No malware IOCs, campaign IDs, or victim data stored.

#### 10. Log Files (`.log`)

14 `.log` files from sprint runs (F222 through F234 series) and one benchmark log. These are annotated sprint execution logs with INFO/WARNING/ERROR entries.

**Sample log structure** (`f222g_lockbit_domain_nonfeed_180.log`, 361 lines, 40.8 KB):

```
INFO:root:[LIVE] Profile=nonfeed_diagnostic180 duration=180s query='lockbit3.tw' aggressive=False
INFO:root:[LIVE] Starting sprint measurement_id=lsm_1778948036137_5664f9 sprint_id=8sa_1778948036137_d5b5cb
W:hledac.universal.intelligence.web_intelligence:... opt Hledac components unavailable
W:hledac.universal.intelligence.cryptographic_intelligence:... cryptography library not available
W:hledac.universal.intelligence.document_intelligence:... PIL not available - image analysis disabled
```

**Sensitivity: LOW** — component availability warnings and sprint metadata. No credentials.

#### 11. Pytest Collection Log (`pytest_collect_after_p0.txt`)

249.4 KB, 2,779 lines. Full pytest collection output showing all discovered tests across the suite.

#### 12. Other Reports

- `EMBEDDING_SIMILARITY_DEDUP_AUDIT_2026-05-06.md` — 13,393 bytes, audit of embedding-based semantic dedup.
- `MEMORY_AUDIT_2026-05-07.md`, `MEMORY_LEAK_UMA_AUDIT_2026-05-07.md`, `MEMORY_OPTIMIZATION_AUDIT_2026-05-07.md` — memory audit series.
- `FALSE_OPTIMIZATION_AUDIT_2026-05-06.md` — optimization false positive audit.
- `F_GLOBAL_SCHEDULER_SPAWN_REGISTRY_REALITY.md`, `F_MLX_ROUNDTRIP_AUDIT.md`, `F_TRANSPORT_ROUTER_REALITY_MAP.md` — F-series architecture audits.
- `P0_ASYNC_SCHEDULER_REALITY_CHECK.md` — P0 async scheduler check.

### Security Notes

- **No API keys or tokens** in any report file.
- **No PII or personal data** — reports store only operational metrics, query strings, and system state.
- **IOC-adjacent queries** (`lockbit3.tw`) appear only as `query` field values — no resolved indicator content.
- **STIX bundles are empty** (0 findings) — no malware, campaign, or victim data stored in STIX form.
- **Logs contain component warnings** — some show `sk_test_*` patterns in test contexts; no live credentials.
- **`.DS_Store`** in reports/ is a macOS metadata file — ignore.

### Reports Not in `reports/`

STIX bundles (`ghost_cti_*.stix.json`) are stored in `hledac/universal/` root, not in `reports/`. They are also sprint export artifacts but are colocated with source code rather than in the reports directory.

## docs/ — Documentation Inventory

### Overview

| Metric | Value |
|--------|-------|
| Total files | 82 |
| Total lines | ~25,000 |
| Subdirs | `agents/`, `audits/`, `runtime/`, `sprints/` |
| Root docs | ARCHITECTURE.md, CODEX_AUDIT_REPORT.md, DEPENDENCY_HYGIENE.md, DEPENDENCY_PROFILES.md, LIVE_SPRINT_EXPERIMENT_MATRIX.md, LOCAL_M1_SMOKE_RUNBOOK.md, LOCAL_OSINT_CAPABILITY_MATRIX.md, TESTING.md, type_audit.md |

---

### Root Documents (9 files)

| File | Lines | Size | H1 Title |
|------|-------|------|----------|
| `ARCHITECTURE.md` | 309 | — | (M1 8GB Hard Constraints table only) |
| `CODEX_AUDIT_REPORT.md` | 129 | — | (F1 complete — baseline zdokumentován) |
| `DEPENDENCY_HYGIENE.md` | 267 | — | (Sprint exec engine audit) |
| `DEPENDENCY_PROFILES.md` | 67 | — | (Profile definitions, no-torch guard) |
| `LIVE_SPRINT_EXPERIMENT_MATRIX.md` | 224 | — | (Sprint experiment tracking) |
| `LOCAL_M1_SMOKE_RUNBOOK.md` | 544 | 19,864 | Local M1 Smoke Runbook |
| `LOCAL_OSINT_CAPABILITY_MATRIX.md` | 187 | — | (Advisory sidecars table, file stats) |
| `TESTING.md` | 126 | — | (Dependency profiles, no-torch guard) |
| `type_audit.md` | 472 | — | (M1 smoke runbook — CLI bug noted at line 458) |

**Note:** `LOCAL_M1_SMOKE_RUNBOOK.md` (544 lines) is the largest root doc. Contains Stage 1/2/3 procedures, acceptance criteria, jq validation commands. CLI bug documented at line 458: `core/__main__.py:2223` missing `deep_osint_m1` in `choices=["default", "nonfeed_diagnostic"]`.

---

### agents/ (4 files)

| File | Lines | H1 Title | Purpose |
|------|-------|----------|---------|
| `ARCHITECTURE_CONNECTIVITY_PLAN.md` | 110 | Plan: Architecture Connectivity Fixes | P1 SprintScheduler.run() unit test, DuckDBShadowStore.for_testing(), runtime seam verification |
| `domain.md` | 51 | Domain Docs | Single-context layout: `CONTEXT.md` at repo root + `docs/adr/` for ADRs. Use glossary vocabulary. Flag ADR conflicts. |
| `issue-tracker.md` | 19 | Issue tracker: Local Markdown | Issues/PRDs in `.scratch/<feature-slug>/`, one feature per directory, `Status:` line with triage role |
| `triage-labels.md` | 15 | Triage Labels | Maps mattpocock/skills labels → our tracker: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` |

---

### audits/ (66 files — largest subdir)

#### Major Capability/Infrastructure Audits

| File | Lines | H1 Title | Key Findings |
|------|-------|----------|-------------|
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | 245 | Whole-Repo Capability Inventory | Full capability map across all modules |
| `OSINT_CAPABILITY_COVERAGE_AUDIT.md` | 354 | OSINT Capability Coverage Audit — F256A | 60 lines (shown truncated); full coverage matrix |
| `OSINT_NEXT_CAPABILITY_PRIORITIZATION.md` | 192 | OSINT Next-Capability Prioritization — 2026-05-18 | Capability prioritization |
| `LIVE_SPRINT_READINESS_AUDIT.md` | 137 | LIVE_SPRINT_READINESS_AUDIT | Gap: no canonical public discovery fixture; ad-hoc dicts only. No canonical CT provider fixture. |
| `M1_OFFLINE_PERFORMANCE_HOTSPOTS_AUDIT.md` | 253 | M1 Offline Performance Hotspots Audit | Performance hotspots on M1 offline mode |
| `HERMES3_BATCH_SCHEDULER_EXTRACTION_AUDIT.md` | 323 | Hermes3 Batch Scheduler Extraction Audit | BatchScheduler CAN be extracted — pure asyncio, no MLX/GPU deps. Seam at `_is_batch_safe()` / `generate_structured()` |
| `LOCAL_ML_MLX_RUNTIME_AUDIT.md` | 242 | LOCAL_ML_MLX_RUNTIME_AUDIT — Sprint F230E | MLX runtime audit |
| `DUCKDB_STORE_SEAM_STATUS_AUDIT.md` | 222 | DuckDBShadowStore Seam Status Audit | DuckDBShadowStore wiring status |
| `DUCKDB_STORE_RESIDUAL_SEAM_AUDIT.md` | 113 | DuckDBShadowStore Residual Seam Audit | Residual seam follow-up |
| `DUCKDB_READ_STORE_BOUNDARY_AUDIT.md` | — | — | (name suggests DuckDB read store boundary) |
| `RESOURCE_GOVERNOR_AUTHORITY_AUDIT.md` | 268 | ResourceGovernor Authority Audit — F226A | Two authority layers: canonical UMA policy (`core/resource_governor.py`) vs runtime admission facade (`runtime/resource_governor.py`). Pipeline layer still reads `sample_uma_status()` directly — parallel authority path. |
| `UV_DEPENDENCY_TRUTH_AUDIT.md` | 175 | UV Dependency Truth Audit — hledac/universal | UV dependency hygiene |
| `LOCAL_STORAGE_M1_AUDIT.md` | 309 | Local Storage Architecture Audit — M1 8GB | Storage architecture on M1 |
| `SOURCE_CONFIDENCE_SCORING_AUDIT.md` | 234 | Source Confidence Scoring Audit | Confidence scoring |

#### Provider/Lane Audits

| File | Lines | H1 Title |
|------|-------|----------|
| `DISCOVERY_OFFLINE_REPLAY_AUDIT.md` | 258 | DISCOVERY OFFLINE REPLAY/AUDIT |
| `OFFLINE_PROVIDER_YIELD_DIAGNOSIS.md` | 199 | Offline Provider-Yield Diagnosis |
| `PROVIDER_REPLAY_NEXT_TARGET_AUDIT.md` | 209 | Provider Replay Next Target Audit |

#### Sprint/Execution Audits

| File | Lines | H1 Title |
|------|-------|----------|
| `SPRINT_SCHEDULER_INJECTION_OWNERSHIP_AUDIT.md` | 198 | SprintScheduler Injection Ownership Audit |
| `SPRINT_SCHEDULER_COMPONENT_OWNERSHIP_AUDIT.md` | 176 | SprintScheduler Component Ownership Audit |
| `SPRINT_CRITICAL_PATH_BOTTLENECK_AUDIT.md` | 173 | Sprint Critical Path Bottleneck Audit |
| `SPRINT_ENRICHMENT_SERVICES_EXTRACTION_PLAN.md` | — | Sprint enrichment services extraction plan |
| `SIDECAR_ACTIVATION_REALITY_REFRESH.md` | — | Sidecar activation reality refresh |
| `SIDECAR_SOURCE_FAMILY_SURFACE_AUDIT.md` | — | Sidecar source family surface audit |
| `NEXT_CAPABILITY_ACTIVATION_PLAN.md` | — | Next capability activation plan |
| `EXPORT_REPORT_PIPELINE_AUDIT.md` | — | Export report pipeline audit |
| `EXPORT_FORMATTER_HELPER_OWNERSHIP_AUDIT.md` | — | Export formatter helper ownership audit |
| `EXPORT_REPORT_FIRST_FIX_PLAN.md` | 128 | Export Report First Fix Plan |
| `F256C_CAPABILITY_TRUTH_RECONCILIATION.md` | — | F256C capability truth reconciliation |
| `F242D_RDAP_RIR_WHOIS_UNIFICATION_AUDIT.md` | 153 | SWARM Coordinator Decomposition Audit |
| `INFERENCE_ENGINE_SEAM_AUDIT.md` | 118 | Inference Engine Seam Audit |
| `ENRICHMENT_OWNERSHIP_AUDIT.md` | 185 | Enrichment Ownership Audit |
| `COORDINATOR_OPERATION_REGISTRY_NO_ACTION.md` | 56 | Coordinator Operation Registry No Action |
| `TRANSPORT_RELIABILITY_STEALTH_AUDIT.md` | 206 | Transport Reliability / Stealth Audit |
| `TRANSPORT_COMMON_POLICY_AUDIT.md` | — | Transport common policy audit |
| `PYDANTIC_MSGSPEC_PHASEOUT_AUDIT.md` | 84 | Pydantic → Msgspec Phaseout Audit |
| `PYTEST_COLLECTION_REMAINING_ERRORS_AUDIT.md` | 190 | PYTEST_COLLECTION_REMAINING_ERRORS_AUDIT |
| `GRAPH_PIVOT_PRIORITIZATION_AUDIT.md` | — | Graph pivot prioritization audit |
| `INTELLIGENCE_ADAPTER_BOUNDARY_AUDIT.md` | — | Intelligence adapter boundary audit |
| `DEPENDENCY_PROFILE_CONSISTENCY_AUDIT.md` | — | Dependency profile consistency audit |
| `IMPORT_TIME_M1_AUDIT.md` | — | Import time M1 audit |
| `SYNTAX_IMPORT_COLLECTION_AUDIT.md` | — | Syntax import collection audit |
| `DEFAULT_DEPENDENCY_THINNING_AUDIT.md` | — | Default dependency thinning audit |
| `TEST_SUITE_HEALTH_AUDIT.md` | — | Test suite health audit |
| `PY314_MODERNIZATION_OPPORTUNITY_AUDIT.md` | — | Py314 modernization opportunity audit |

---

### runtime/ (2 files)

| File | Lines | H1 Title | Purpose |
|------|-------|----------|---------|
| `GRAPH_ACCUMULATION_SEAM_AUDIT.md` | 130 | DuckPGQGraph Accumulation Seam Audit | Phase A audit: `DuckPGQGraph.add_relation` has NO internal exception handling — extracted method MUST wrap call in `try/except Exception`. Discrepancy: F206AI report incorrectly shows IOCGraph as backend (it's actually DuckPGQGraph). |
| `SPRINT_GRAPH_ACCUMULATOR_PHASE_B_PLAN.md` | 205 | Sprint Graph Accumulator Phase B Plan | Phase B plan: extract `SprintGraphAccumulator` adapter, fail-safe invariants I1/I2/I3, run cmds for probe_f227a/probe_f227b |

---

### sprints/ (1 file)

| File | Lines | H1 Title | Purpose |
|------|-------|----------|---------|
| `F350M_SPRINT_SCHEDULER_REFACTORING.md` | 489 | Sprint F350M Sprint Scheduler Refactoring | 7-phase refactor: extract SidecarOrchestrator, EnrichmentLifecycle, MemoryPressureMonitor, SprintSchedulerResult → own files. Success criteria: `SprintScheduler.run() ≤ 300 lines`, 136 tests pass backward-compat. |

---

### Source File References (from H1/content analysis)

| Doc | Referenced Source Files |
|-----|------------------------|
| `ARCHITECTURE.md` | `runtime/sprint_scheduler.py`, `core/__main__.py`, `coordinators/fetch_coordinator.py`, `knowledge/duckdb_store.py`, `brain/hermes3_engine.py` |
| `LOCAL_OSINT_CAPABILITY_MATRIX.md` | `GraphService` (`knowledge/graph_service.py`), `MLX` (`utils/mlx_cache.py`), `SidecarBus` (`runtime/sidecar_bus.py`), `DOHAdapter` (`intelligence/doh_lane.py`) |
| `ARCHITECTURE_CONNECTIVITY_PLAN.md` | `SprintScheduler.run()` → `graph_service.upsert_ioc()` → `DuckPGQGraph` |
| `GRAPH_ACCUMULATION_SEAM_AUDIT.md` | `DuckPGQGraph.add_relation` (quantum_pathfinder.py:1256-1263), `runtime/sprint_scheduler.py` (line ~1844 `_accumulate_findings_to_graph`), `knowledge/ioc_graph.py` (IOCGraph — separate Kuzu-backed mod) |
| `LIVE_SPRINT_READINESS_AUDIT.md` | `live_measurement_kpi.py:931`, `discovery/discovery_planner.py:128`, `sprint_exporter.py:299-334,342-381`, `acquisition_telemetry_reconcile.py:52` |
| `RESOURCE_GOVERNOR_AUTHORITY_AUDIT.md` | `core/resource_governor.py` (canonical UMA policy), `runtime/resource_governor.py` (M1ResourceGovernor facade) |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/ct_log_client.py`, `intelligence/exposure_correlator.py`, `intelligence/temporal_archaeologist_adapter.py`, `intelligence/identity_stitching_canonical.py`, `intelligence/leak_sentinel.py`, `intelligence/bgp_lane.py`, `intelligence/shodan_wrapper.py` (dormant), `intelligence/dark_web_intelligence.py` (dormant) |
| `LOCAL_M1_SMOKE_RUNBOOK.md` | `core/__main__.py:2223` (CLI bug — missing `deep_osint_m1` in choices) |

---

### Features Described But Not Yet Implemented

| Doc | Feature | Status |
|-----|---------|--------|
| `HERMES3_BATCH_SCHEDULER_EXTRACTION_AUDIT.md` | `BatchScheduler` extraction (pure asyncio, no MLX) | CAN be extracted — not yet done |
| `F350M_SPRINT_SCHEDULER_REFACTORING.md` | 7-phase SprintScheduler refactor (extract SidecarOrchestrator, EnrichmentLifecycle, MemoryPressureMonitor, SprintSchedulerResult) | Planned for F350M — not yet executed |
| `SPRINT_GRAPH_ACCUMULATOR_PHASE_B_PLAN.md` | Phase B: extract `SprintGraphAccumulator` adapter | Planned — not yet executed |
| `LIVE_SPRINT_READINESS_AUDIT.md` | Canonical `tests/fixtures/public_discovery_fixture_matrix.json` | GAP — not created |
| `LIVE_SPRINT_READINESS_AUDIT.md` | Canonical CT provider fixture | GAP — not created |

---

### Deprecated / Dormant Behavior

| Doc | Module | Status |
|-----|--------|--------|
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/shodan_wrapper.py` | dormant — not wired, no CanonicalFinding output |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/network_intelligence.py` | dormant |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/dark_web_intelligence.py` | dormant — Tor/PGP, active profile only |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/cryptographic_intelligence.py` | legacy — classical cipher analysis |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/advanced_image_osint.py` | dormant — converter only |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/pattern_mining.py` | dormant |
| `WHOLE_REPO_CAPABILITY_INVENTORY.md` | `intelligence/attribution_scorer.py` | dormant |
| `DEPENDENCY_PROFILES.md` | Legacy import path `hledac.universal.autonomous_orchestrator` | Deprecated — canonical path is `runtime.sprint_scheduler` |
| `GRAPH_ACCUMULATION_SEAM_AUDIT.md` | IOCGraph (Kuzu-backed) | Separate mod, NOT called by scheduler's graph accumulation path (DuckPGQGraph is actual backend) |

- 124 sprints recorded in policy state, epsilon=0.0999, total_reward=606.2

## models/ — ML Model Artifacts

### Directory Status

**`models/` is empty** (0 files, 0 subdirs). All model artifacts live elsewhere.

### Model Artifact Locations

| Location | Model | Type | Size |
|----------|-------|------|------|
| `~/.hledac/models/` | AllMiniLML6V2 | CoreML (.mlmodel + .mlmodelc) | 85.5 MB raw + ~86 MB compiled |
| `~/.hledac/models/flashrank/` | ms-marco-TinyBERT-L-2-v2 | FlashRank ONNX | 4.3 MB |
| `cache_storage/embeddings/models--nomic-ai--nomic-embed-text-v1.5/.../onnx/` | nomic-embed-text-v1.5 | ONNX (embedding) | 521.96 MB |
| `~/.cache/huggingface/hub/` (not yet populated) | DeepHermes-3-Llama-3-3B-Preview-4bit | MLX (downloaded at runtime) | ~3.5 GB |
| `~/.cache/huggingface/hub/` (not yet populated) | Hermes-3-Llama-3.2-3B-4bit | MLX (downloaded at runtime) | ~3.5 GB |
| `~/.cache/huggingface/hub/` (not yet populated) | nomic-ai/modernbert-embed-base | MLX (downloaded at runtime) | ~1.1 GB |
| `~/.cache/huggingface/hub/` (not yet populated) | knowledgator/gliner-relex-large-v0.5 | PyTorch (NER, downloaded) | ~7.2 GB |

**Total MLX model cache in ~/.cache/huggingface/hub: ~15.3 GB** (per MODEL_STACK_LOCAL_READY.md, 2026-05-15).

### Model ID Constants (from `scripts/model_stack_smoke.py`, canonical source)

```python
PRIMARY_LLM   = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
ROLLBACK_LLM  = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
EMBED_MODEL   = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"  # ModernBERT via mlx_embeddings
NER_MODEL     = "knowledgator/gliner-relex-large-v0.5"
RERANKER_MODEL = "ms-marco-MiniLM-L-12-v2"  # FlashRank auto-downloads this
```

Additional model IDs found in code:
- `brain/modernbert_engine.py`: `mlx_model = "nomic-ai/modernbert-embed-base"` (line 56); `st_model = "nomic-ai/nomic-embed-text-v1.5"` (line 58)
- `brain/ane_embedder.py`: CoreML `AllMiniLML6V2.mlmodel` (line 275)
- `brain/model_manager.py`: `COREML_MODEL_PATH = MODELS_DIR / "modernbert_ane.mlpackage"` (line 149)

### Cache Storage: Nomic ONNX Embedding Model

Path: `cache_storage/embeddings/models--nomic-ai--nomic-embed-text-v1.5/snapshots/e9b6763023c676ca8431644204f50c2b100d9aab/onnx/model.onnx`

- Size: **547,310,275 bytes (521.96 MB)**
- Vocab size: 30,528 (BERT tokenizer, `BertTokenizer`)
- Model max length: 8,192
- Config: `transformers_version = 5.3.0.dev0`, `torch_dtype = float32`
- Storage format: HuggingFace Hub cache snapshot (blob ID `147d5aa...`, 522 MB)
- Related blobs: `tokenizer.json` (711,396 bytes), `tokenizer_config.json` (1,191 bytes), `special_tokens_map.json` (695 bytes), `config.json` (2,538 bytes)

### ~/.hledac/models/ — Bundled/Pre-cached Local Models

```
AllMiniLML6V2.mlmodel                           85.5 MB  (raw CoreML sentence-transformer)
AllMiniLML6V2.mlmodelc/                          (compiled CoreML forANE)
  model.espresso.shape                           0 KB
  model.espresso.net                             0.1 MB
  model.espresso.weights                        85.8 MB
  coremldata.bin                                0 KB  (x5 scattered)
flashrank/ms-marco-TinyBERT-L-2-v2/
  flashrank-TinyBERT-L-2-v2.onnx                 4.3 MB
  tokenizer.json                                0.4 MB
  tokenizer_config.json, special_tokens_map.json, config.json  (tiny)
```

This path is set by `MODELS_DIR = Path.home() / ".hledac" / "models"` in:
- `scripts/model_stack_smoke.py:40`
- `brain/model_manager.py:147`
- `brain/ane_embedder.py:39`

### Model Loading: How and When

**LLM models (MLX):**
- `brain/model_lifecycle.py:739` — `mlx_lm.load(model_path_str)` loads via `mlx_lm` from HuggingFace mlx-community repo IDs (e.g., `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit`)
- Downloaded on first use to `~/.cache/huggingface/hub/models--mlx-community--*/`
- Not committed to repo; downloaded at runtime
- `brain/hermes3_engine.py` — same `mlx_lm.load()` path

**Embedding models:**
- `brain/modernbert_engine.py:56-58` — primary `nomic-ai/modernbert-embed-base` via `mlx_embeddings`; fallback `nomic-ai/nomic-embed-text-v1.5` via sentence-transformers
- `cache_storage/embeddings/` — Nomic ONNX model pre-cached inside the repo (HuggingFace Hub cache snapshot, blob store)
- `brain/ane_embedder.py:270` — `AllMiniLML6V2.mlmodel` CoreML loaded from `MODELS_DIR`

**NER model (GLiNER-Relex):**
- `knowledgator/gliner-relex-large-v0.5` — downloaded by transformers/AutoModelForTokenClassification on first use to `~/.cache/huggingface/hub/`
- Referenced in `scripts/model_stack_smoke.py:347`

**Reranker (FlashRank):**
- `ms-marco-MiniLM-L-12-v2` — auto-downloaded by FlashRank to `~/.hledac/models/flashrank/`
- Configured in `brain/ane_embedder.py:540` (`_flashrank_reranker`)

### External Model URLs / HuggingFace References

All model IDs reference public HuggingFace repositories:

| Model | HF Repo |
|-------|---------|
| DeepHermes-3-Llama-3-3B-Preview-4bit | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` |
| Hermes-3-Llama-3.2-3B-4bit | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` |
| nomic-ai/modernbert-embed-base | `nomic-ai/modernbert-embed-base` |
| nomic-ai/nomic-embed-text-v1.5 | `nomic-ai/nomic-embed-text-v1.5` |
| knowledgator/gliner-relex-large-v0.5 | `knowledgator/gliner-relex-large-v0.5` |
| ms-marco-MiniLM-L-12-v2 | `ms-marco/MiniLM-L-12-v2` |

No private model URLs, no API keys, no credentials in any model file or config.

### Security Assessment

**No security risk identified.** All models are:
- Publicly hosted on HuggingFace (mlx-community, nomic-ai, knowledgator, ms-marco orgs)
- Standard open-weight models (LLM, embedding, NER, reranker)
- No credentials, no API keys, no secrets embedded in model files
- No bundled model files committed to the repo (except Nomic ONNX in `cache_storage/`, a cached HuggingFace snapshot)

### Key Files (model loading)

| File | Lines | Purpose |
|------|-------|---------|
| `brain/model_lifecycle.py` | 928 | Canonical `ModelManager`, `mlx_lm.load()` path |
| `brain/model_manager.py` | 1379 | `ModelManager.PHASE_MODEL_MAP`, `MODELS_DIR` |
| `brain/hermes3_engine.py` | 2469 | `Hermes3Engine` using `mlx_lm.generate()` |
| `brain/ane_embedder.py` | 600 | CoreML embedder, FlashRank reranker, ANE path |
| `brain/modernbert_engine.py` | 265 | ModernBERT extractive summarization, mlx-embeddings + sentence-transformers fallback |
| `scripts/model_stack_smoke.py` | 392 | Canonical model ID constants, smoke checks |
| `autonomous_analyzer.py` | 868 | `models_needed: Set[str]` tracking |
---

## config/ — Configuration Schemas

**NOTE: `/config/` directory does NOT exist.** Configuration files are at the project root.

---

### Files Found

| File | Size | Lines | Purpose |
|------|------|-------|---------|
| `config.py` | 23.2KB | 665 | Centralized config management (env-based, M1 presets, layer settings) |
| `config-schema.json` | 23.1KB | 730 | JSON Schema for config validation (versioned, multi-section) |
| `pyrightconfig.json` | 0.2KB | 7 | Pyright type-checker configuration |
| `skills-lock.json` | 3.4KB | 90 | Skill registry lockfile (mattpocock/skills source refs) |

---

### 1. config.py — Schema Structure

**Imports from outside hledac/universal:** `os` (stdlib), `json` (stdlib), `dataclasses` (stdlib), `typing` (stdlib) — all stdlib, no external imports. **CLEAN.**

**Main Classes:**

| Class | Lines | Purpose |
|-------|-------|---------|
| `ResearchPresets` | 59-117 | Research mode presets (QUICK/STANDARD/DEEP/EXTREME/AUTONOMOUS) |
| `M1Presets` | ~120-200 | M1 8GB RAM optimization presets (MEMORY_LIMIT_MB=5500.0, THERMAL_THRESHOLD_C=85.0) |
| `StealthConfig` | ~140-170 | Stealth/crawler settings (user agents, TLS, JA3, cookie policy, Tor, DNS, encryption) |
| `PrivacyConfig` | ~170-200 | Privacy layer (agent privacy, data retention, PII handling) |
| `MemoryConfig` | ~200-260 | Memory management (LMDB, embeddings, lifecycle, gotcha, sessions) |
| `ModelConfig` | ~260-310 | Model settings (Hermes, ModernBERT, GLiNER, timeouts, caching) |
| `ResearchConfig` | ~310-392 | Research exec (steps, agents, knowledge graph, RAG, fact-checking) |
| `GhostConfig` | ~392-440 | Ghost layer (enable flags for ghost/coordinator/knowledge/security/stealth/privacy/deep-research/communication layers) |
| `CoordinationConfig` | ~440-470 | Coordination layer settings |
| `AgentManagerConfig` | ~470-510 | Agent management (timeout, meta-optimization, distillation) |
| `UniversalConfig` | ~510-560 | Unified top-level config container (mode + sub-configs) |

---

### 2. All Env Vars Read by config.py

| Env Var | Type | Default | Purpose |
|---------|------|---------|---------|
| `HLEDAC_MEMORY_LIMIT_MB` | float | from preset | Memory limit in MB |
| `HLEDAC_MAX_STEPS` | int | from preset | Max research steps |
| `HLEDAC_LOG_LEVEL` | str | from preset | Log level (DEBUG/INFO/WARN/err) |
| `TOR_PROXY_URL` | str | `"socks5://127.0.0.1:9050"` | Tor SOCKS proxy URL |
| `I2P_PROXY_URL` | str | `"socks5://127.0.0.1:7654"` | I2P SOCKS proxy URL |
| `GHOST_DUCKDB_MEMORY` | str | `"400MB"` | DuckDB memory limit (used elsewhere in codebase) |
| `GHOST_DUCKDB_MAX_TEMP` | str | `"1GB"` | DuckDB max temp storage |
| `HLEDAC_LANCEDB_CACHE_MB` | int | 256 | LanceDB cache in MB (M1-safe mode default) |
| `HLEDAC_ALLOW_LARGE_LANCEDB_CACHE` | bool | false | Override to allow up to 1GB LanceDB cache |
| `HLEDAC_M1_OPTIMIZED` | bool | true | M1 optimization flag |

**Also referenced in public_fetcher.py (not config.py):**

| Env Var | Default | Purpose |
|---------|---------|---------|
| `TOR_PROXY_URL` | `"socks5://127.0.0.1:9050"` | Tor SOCKS proxy |
| `I2P_PROXY_URL` | `"socks5://127.0.0.1:7654"` | I2P proxy |
| `HLEDAC_LANCEDB_CACHE_MB` | 256 | LanceDB cache |
| `HLEDAC_ALLOW_LARGE_LANCEDB_CACHE` | — | Large cache override |

---

### 3. Surprising Constants / Defaults

| Constant | Value | Surprise? |
|----------|-------|-----------|
| `MEMORY_LIMIT_MB` (M1Presets) | 5500.0 | YES — explicit 5.5GB budget, not 6GB |
| `THERMAL_THRESHOLD_C` | 85.0 | YES — hard thermal threshold for M1 throttling |
| `CIRCUIT_BREAKER_THRESHOLD` | 3 | YES — only 3 failures before circuit opens |
| `CONTEXT_SWAP_ENABLED` | True | — |
| `MLX_CACHE_CLEAR_INTERVAL` | 10 | — Clear MLX cache every 10 transitions |
| `AGENT_TIMEOUT_SECONDS` | 300 | 5 min agent timeout |
| `QUICK_SCAN_TIME_LIMIT` | 5 | 5 second quick scan |
| `TOR_CIRCUIT_RENEWAL_REQUEST_COUNT` | 10 | Tor renews circuit every 10 requests |
| `TOR_STEALTH_TIMEOUT_SCALE` | 2.0 | Tor requests need 2x timeout |
| `JITTER_MIN_S` / `JITTER_MAX_S` | 0.1 / 0.5 | Request jitter range |

**Research Presets:**

| Mode | max_steps | max_time_minutes | max_concurrent_agents |
|------|-----------|------------------|----------------------|
| QUICK | 5 | 5 | 2 |
| STANDARD | 20 | 60 | 3 |
| DEEP | 50 | 240 | 4 |
| EXTREME | 100 | 480 | 6 |
| AUTONOMOUS | 200 | 1440 (24h) | 6 |

---

### 4. Safety-Related Config (Memory Limits, Timeouts, Bounds)

**M1 Memory Budget (M1Presets):**
- `MEMORY_LIMIT_MB = 5500.0` — 5.5GB active ceiling
- `THERMAL_THRESHOLD_C = 85.0` — thermal throttle trigger
- `CONTEXT_SWAP_ENABLED = True` — explicit context swap enabled
- `MLX_CACHE_CLEAR_INTERVAL = 10` — cache clear cadence

**LanceDB Cache:**
- Default: 256MB (M1-safe mode)
- Hard cap without override: 512MB
- Large override (HLEDAC_ALLOW_LARGE_LANCEDB_CACHE): up to 1GB

**Agent Bounds:**
- `AGENT_TIMEOUT_SECONDS = 300`
- `AGENT_META_OPTIMIZATION_INTERVAL = 100`
- `AGENT_META_MIN_SAMPLES = 50`
- `DISTILLATION_LEARNING_RATE = 0.001`
- `DISTILLATION_HIDDEN_DIM = 256`

**Quantum Pathfinding:**
- `QUANTUM_MAX_STEPS = 50`
- `QUANTUM_AMPLIFICATION_STRENGTH = 0.1`
- `QUANTUM_MAX_NODES = 10000`

---

### 5. Security-Relevant Config

**Stealth Layer:**
- `enable_stealth: bool` — stealth mode toggle
- `user_agent_rotation: bool` — UA rotation
- `tls_fingerprint_spoofing: bool` — TLS fingerprint spoofing
- `enable_tor: bool` — Tor routing
- `tor_proxy: str` — configurable Tor proxy (default: `socks5://127.0.0.1:9050`)
- `JITTER_MIN_S / JITTER_MAX_S` — request timing jitter

**Encryption:**
- `enable_encryption: bool = True`
- `encryption_algorithm: str = "fernet"` (fernet or aes256)

**Privacy:**
- `agent_privacy_enabled: bool`
- `data_retention_days: int = 30`
- `pii_redaction_enabled: bool = True`
- `forget_on_exit: bool = True`

**CAPTCHA:**
- `enable_captcha_solving: bool = True`
- `captcha_providers: List[str] = ["2captcha", "anticaptcha"]`
- `captcha_timeout: int = 120`

**Proxy:**
- `enable_proxy_rotation: bool = False`
- `proxy_list: List[str] = []`

---

### 6. Security Risks

**LOW RISK — findings:**

1. `encryption_algorithm: str = "fernet"` — Fernet is AES-128-CBC with HMAC (not post-quantum). For a security-focused OSINT platform, this is worth noting but not a runtime risk.

2. `tor_proxy` default `socks5://127.0.0.1:9050` — Hardcoded localhost Tor proxy. If user runs without Tor listening, stealth layer silently degrades. No check at init time.

3. `captcha_providers` default includes `2captcha`, `anticaptcha` — These are third-party paid services. If enabled without API keys, requests fail. No guard in config.

4. `proxy_list: List[str] = []` — Empty by default. If user sets proxy_list without validation, malformed URLs cause runtime errors.

5. `TOR_PROXY_URL` / `I2P_PROXY_URL` env vars with localhost defaults — If these env vars leak in logs, localhost proxy addresses reveal user is running anonymity software.

**No CRITICAL risks found** — config.py has no secrets, no hardcoded credentials, no dangerous eval(), no shell execution, no unsafe deserialization.

---

### 7. config-schema.json — Schema Structure

**Schema version:** `1` (top-level key)

**Sections in `sections`:**

| Section | Keys | Notable Settings |
|---------|------|------------------|
| `research` | 30+ | max_steps, max_time_minutes, enable_knowledge_graph, enable_rag, enable_fact_checking |
| `memory` | 20+ | lmdb path, embeddings (provider, model, batch_size=32, dimension=384), lifecycle (decay_rate=0.01), gotcha, sessions |
| `archive` | 10 | compression_level, include_metadata, retention_days |
| `knowledge` | 15+ | cross_project_search, cross_project_import, universal_gotchas_enabled |
| `context7` | 10+ | MCP library resolution |
| `ide_paths` | — | Per-IDE path allowlists (cursor, codex, opencode, antigravity) |
| `mcp_bridges.<name>` | — | MCP server config (alias, command, url, auth_env) |
| `lean_ctx` | 30+ | context engine settings, ignored_files, shell hooks, FTS5, compression, reference_results |
| `auto_update` | 3 | check_interval_hours=6, notify_only=false |

**Notable defaults in schema:**
- `memory.embeddings.batch_size = 32`
- `memory.embeddings.dimension = 384`
- `memory.lifecycle.decay_rate = 0.01`
- `memory.max_episodes = 500`
- `memory.summary_max_chars = 200`
- `lean_ctx.response_verbosity = "normal"` (enum: compact/normal/diagnostic)
- `lean_ctx.enable_wakeup_ctx = true`

---

### 8. pyrightconfig.json

```json
{
  "include": [".."],
  "pythonVersion": "3.14",
  "typeCheckingMode": "basic",
  "reportMissingImports": false,
  "reportMissingTypeStubs": false
}
```

**Observations:**
- `pythonVersion: "3.14"` — Python 3.14 (very new, not fully released as of 2026-05)
- `include: [".."]` — Scans parent directory (project root)
- `reportMissingImports: false` — Suppresses import errors (may hide dependency issues)

---

### 9. skills-lock.json

Lockfile for skill registry. References `mattpocock/skills` GitHub source. 12 skills locked including: code-review, context-mode, deep-research, mcp-setup, terminal-tooling, validate-and-fix, etc. Hash-verified with `computedHash` field. **No security concerns** — read-only skill metadata.

---

### Summary

| Question | Answer |
|----------|--------|
| Configuration schemas exist? | YES — config.py (dataclasses) + config-schema.json (JSON Schema) |
| Env vars identified? | 9 env vars (HLEDAC_*, TOR_PROXY_URL, I2P_PROXY_URL, GHOST_DUCKDB_*, HLEDAC_LANCEDB_CACHE_MB) |
| Surprising constants? | MEMORY_LIMIT_MB=5500, THERMAL_THRESHOLD_C=85.0, CIRCUIT_BREAKER_THRESHOLD=3 |
| Imports from outside hledac/universal? | NO — only stdlib (os, json, dataclasses, typing) |
| Safety-related config? | YES — memory limits, thermal thresholds, agent timeouts, LanceDB cache bounds, quantum pathfinding bounds |
| Security risks? | LOW — Fernet (not post-quantum), localhost Tor default (log leakage risk), captcha providers (paid service without keys guard) |

---

## data/ — Reference & Seed Data

**Status: EMPTY — no files exist**

```
data/
total 0
drwxr-xr-x@ 2 vojtechhamada  staff  64 May 20 07:58 .
```

| Item | Value |
|------|-------|
| Files found | 0 |
| Subdirectories | 0 |
| Total size | 0 bytes |
| Last modified | 2026-05-20 07:58 |
| File types | none |

### Security Assessment

- **Secrets/keys/tokens:** NONE — directory is empty
- **Caches:** NONE
- **Reference data:** NONE
- **Seed data:** NONE
- **Risk posture:** CLEAN — no files present

## logs/ — Log Inventory & Security

**Status: EMPTY (hledac/universal/logs/)**

The canonical `logs/` directory at `hledac/universal/logs/` is empty — 0 files, 0 bytes, last modified 2026-05-20. This directory serves as the designated runtime log output directory but no files have been written to it during the current audit period.

### Artifact Log Inventory (reports/)

Live log files exist under `reports/` (not `logs/`). These are sprint/run output artifacts, not runtime log files. No rotation policy is observed — files accumulate indefinitely.

| File | Size | Lines | Type | Date |
|------|------|-------|------|------|
| `reports/f222g_lockbit_domain_nonfeed_180.log` | 40,829 B | ~800+ | Sprint log | 2026-05-18 |
| `reports/f222g_lockbit_text_nonfeed_180.log` | 39,905 B | ~800+ | Sprint log | 2026-05-18 |
| `reports/f223f_domain_lockbit3_nonfeed_180.log` | 21,113 B | ~400+ | Sprint log | 2026-05-18 |
| `reports/live_sprint_300s.log` | 18,697 B | ~350+ | Sprint log | 2026-05-18 |
| `reports/nonfeed_diagnostic_domain_180.log` | 13,862 B | ~300+ | Sprint log | 2026-05-18 |
| `reports/f226d_domain_lockbit3_nonfeed_check.log` | 12,946 B | ~250+ | Sprint log | 2026-05-18 |
| `reports/f230f_domain_lockbit3_after_f230e.log` | 12,187 B | ~250+ | Sprint log | 2026-05-18 |
| `reports/f226b_domain_lockbit3_nonfeed_check.log` | 11,869 B | ~250+ | Sprint log | 2026-05-18 |
| `reports/nonfeed_diagnostic_lockbit_180.log` | 9,743 B | ~200+ | Sprint log | 2026-05-18 |
| `reports/f223f_text_lockbit_nonfeed_180.log` | 9,130 B | ~200+ | Sprint log | 2026-05-18 |
| `reports/f229d_domain_lockbit3_shape_recheck.log` | 8,509 B | ~200+ | Sprint log | 2026-05-18 |
| `reports/f229b_domain_lockbit3_nonfeed_180.log` | 7,969 B | ~150+ | Sprint log | 2026-05-18 |
| `reports/live_sprint_300s_20260515.log` | 7,875 B | ~150+ | Sprint log | 2026-05-18 |
| `reports/f233c/domain_live.log` | 5,487 B | ~100+ | Sprint log | 2026-05-18 |
| `reports/f233d/domain_live.log` | 2,065 B | ~50 | Sprint log | 2026-05-19 |
| `reports/f233a_domain_live.log` | 1,871 B | ~50 | Sprint log | 2026-05-18 |
| `reports/benchmarks/sprint_timer_overhead.jsonl` | 432 B | 1 | Benchmark JSONL | 2026-05-19 |
| `reports/benchmarks/bench_m1_runtime_gates_*.jsonl` (14 files) | ~2,800–3,800 B each | 5 each | Benchmark JSONL | 2026-05-18 |

**Total: 31 log files, ~215 KB aggregate.**

### Schema / Content Pattern

All `.log` files share a common format produced by sprint execution:

```
fast-langdetect not available, using fallback detection
WARNING:hledac.universal.intelligence.web_intelligence:intel.webintel: optional Hledac components unavailable...
WARNING:hledac.universal.intelligence.cryptographic_intelligence:...
WARNING:hledac.universal.intelligence.document_intelligence:...
WARNING:hledac.universal.tools.lightpanda_manager.py:25: UserWarning: [GHOST OPSEC] No active ramdisk found at /Volumes/ghost_tmp...
WARNING:hledac.universal.intelligence.relationship_discovery:[LSH] datasketch not installed...
INFO:root:[LIVE] Profile=active300 duration=300s query='LockBit ransomware' aggressive=False
INFO:root:[LIVE] Starting sprint measurement_id=lsm_1778805309616_a0496a sprint_id=8sa_1778805309616_7a1680...
INFO:hledac.universal.core.__main__:[GC] gc.freeze() applied — reduces GC pause variance
INFO:hledac.universal.utils.mlx_cache:[Sprint 8T] Metal limits configured: cache=2560 MiB, wired=2560 MiB
INFO:hledac.universal.core.__main__:[BOOT] Pre-sprint checks OK | UMA: 4.80GiB used | swap: 0.79GiB
INFO:hledac.universal.patterns.pattern_matcher:[PATTERNS] configured 134 bootstrap patterns
INFO:hledac.universal.tools.lmdb_kv:LMDB KV store initialized at .../shadow_wal.lmdb
INFO:hledac.universal.metrics_registry:MetricsRegistry initialized: run_id=default
WARNING:coremltools:Torch version 2.12.0 has not been tested with coremltools...
WARNING:coremltools:Failed to load _MLModelProxy: No module named 'coremltools.libcoremlpython'
INFO:primp:res: https://www.bing.com/search?q=LockBit+ransomware 200
INFO:hledac.universal.pipeline.live_public_pipeline:[P18] Exported markdown to /Users/vojtechhamada/hledac_outputs/1778805362_report.md
```

### Sensitive Data Assessment

| Data Category | Present? | Evidence |
|---------------|----------|----------|
| API keys/tokens | **NO** | No `sk_live_`, `sk_test_`, bearer tokens, or AWS keys in any log |
| Passwords | **NO** | No password strings observed |
| IP addresses | **NO** | No client IPs logged; only server-side UMA metrics (`UMA: 4.80GiB used`) |
| Query strings | **PARTIAL** | Search queries appear in INFO lines (`query='LockBit ransomware'`) — OSINT research queries, not user credentials. Could expose targeting interests. |
| URLs with auth | **NO** | No `user:pass@` in URLs |
| PII | **NO** | No names, emails, phone numbers |
| Error stack traces | **YES** | Stack traces appear in failed runs (e.g., `f233c/domain_live.log` — `AttributeError: 'SprintSchedulerResult' object has no attr 'next_seeds_ioc_domains'`). Traces contain file paths and line numbers but no sensitive data. |
| LMDB paths | **YES** | Paths like `.../shadow_wal.lmdb` and `.../dedup.lmdb` appear — internal storage paths, not sensitive. |

**Query string exposure:** Logs contain OSINT research queries (e.g., `query='lockbit3.tw'`). These are research targets, not personal data, but could reveal intelligence interests if logs are compromised.

### Rotation / Retention

- **Rotation policy:** NONE — no logrotate config, no size-based rotation, no time-based rotation
- **Retention:** INDEFINITE — files persist until manually deleted
- **Archive/deletion:** NOT OBSERVED — no `.gz`, `.zip`, or archived copies of old logs
- **Deletion candidate:** `reports/f233a_domain_live.log` (failed run with traceback, 1,871 B) — could be removed but is small

### Security / Audit Trail Assessment

| Property | Status | Notes |
|----------|--------|-------|
| Dedicated audit log | **NO** | `security/audit.py` defines `AuditEventType` enum but produces no persistent audit trail file |
| Access log | **NO** | No request/response access logging |
| Integrity log | **NO** | No hash/checksum verification of log files |
| Log tamper detection | **NO** | No append-only or signed logs |
| Security event log | **PARTIAL** | SecurityCoordinator exists but no security events written to logs/ directory |
| ToolExecLog | **NO** | `tool_exec_log.py` writes to `run_dir/logs/tool_exec.jsonl` — no such files found in `logs/` |

**Conclusion:** The `logs/` directory is empty. Active logging goes to `reports/` as sprint artifacts. There is no dedicated security/audit trail log. Tool execution audit (`ToolExecLog`) is not persisted to disk in the current setup.

## scripts/ — Utility Scripts Inventory

8 scripts total (1475 lines). Classification: local dev + CI/CD hooks.

### check_torrc.py
- **Lines**: 114 | **Size**: 3.2K
- **Purpose**: Validates Tor config for `IsolateSOCKSAuth` directive presence.
- **Entry point**: `main()` → `if __name__ == "__main__"`
- **Reads**: torrc files at `~/.torrc`, `/etc/tor/torrc`, `/opt/homebrew/etc/tor/torrc`, or CLI override `--torrc`
- **Env vars**: `TORRC_PATH_OVERRIDE` (module-level override for testing)
- **Exit codes**: 0=found, 1=not found, 2=not found/error
- **Security**: Read-only Tor config check, no network, no privilege escalation
- **Use**: Pre-deployment validation of Tor anonymity settings

### extract_nonfeed_seeds.py
- **Lines**: 532 | **Size**: 17.5K
- **Purpose**: Extracts nonfeed candidate seeds from DuckDB findings for F222H pivot planning.
- **Entry point**: `main()` → `if __name__ == "__main__"`
- **Reads**: DuckDB database files (findings table, text columns, publisher_domains)
- **Writes**: JSON output file + optional DuckDB-backed report path
- **Env vars**: `_HLADAC_ROOT` detection for project root, `NONFEED_SEED_MIN_QUALITY` (via argparse)
- **Network**: None (local DuckDB only)
- **Security**: Local data processing only, no exfil, no privilege escalation
- **Key functions**: `extract_nonfeed_seeds_from_findings()`, `_read_findings_from_duckdb()`, `_classify_with_quality()`, `_passes_quality_gate()`

### model_stack_smoke.py
- **Lines**: 392 | **Size**: 14.4K
- **Purpose**: Smoke check for model stack availability (MLX, embeddings, NER, reranker, PII, OCR) on M1 MacBook 8GB.
- **Entry point**: `main()` → `if __name__ == "__main__"`
- **Modes**: `--check` (component verification), `--smoke` (import test), `--component llm|embeddings|ner|reranker|pii|ocr`, `--print-download-commands`
- **Reads**: Disk space via `shutil.disk_usage()`, model paths (no model loading)
- **Env vars**: Model IDs defined in script (no env var override)
- **Network**: None (model check only, no download)
- **Security**: Read-only availability check, no model loading, no network access
- **Canonical model**: `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` (rollback: `mlx-community/Hermes-3-Llama-3.2-3B-4bit`)

### mount_ramdisk.sh
- **Lines**: 118 | **Size**: 3.6K
- **Purpose**: Creates a RAM disk at `/Volumes/ramdisk` for high-speed temp storage (DuckDB tmp, WARC, sockets, Arrow).
- **Entry point**: `main "$@"` (bash sourced)
- **Privileges**: Requires `root` or `sudo` for `hdiutil attach` and `diskutil erasevolume`
- **Writes**: Creates `/Volumes/ramdisk/` with subdirs: `duckdb_tmp/`, `sockets/`, `warc/`, `arrow/`
- **Security**: Local disk mount only, no network, no data exfil. RAM disk cleared on unmount.
- **Risk**: Privilege escalation via `sudo` — only use in local dev environments

### pre_commit_guard.py
- **Lines**: 15 | **Size**: 504B
- **Purpose**: Git pre-commit hook to block committing files named `None` or `None.*`.
- **Entry point**: `if __name__ == "__main__"` (CI/CD hook)
- **Reads**: `git diff --cached --name-only` (staged files only)
- **Security**: Read-only git operations, no network, no privilege escalation
- **Use**: Git hook installed via `.git/hooks/pre-commit`

### score_corroboration.py
- **Lines**: 140 | **Size**: 4.7K
- **Purpose**: CLI for Evidence Corroboration Scorer (Sprint F223D) — scores corroboration of CT findings against nonfeed seeds.
- **Entry point**: `main()` → `if __name__ == "__main__"`
- **Inputs**: `--report` (JSON), `--seeds-json` (JSON), or `--duckdb` (DuckDB query)
- **Writes**: `--output` JSON file or stdout
- **Env vars**: None
- **Network**: None (local file processing only)
- **Security**: Local scoring only, no exfil, no privilege escalation

### smoke_llm_candidate.py
- **Lines**: 120 | **Size**: 3.9K
- **Purpose**: Resolves LLM candidate model IDs and optionally performs a tiny generation smoke test.
- **Entry point**: `main()` → `if __name__ == "__main__"`
- **Candidates**: `default`, `deephermes`, `hermes` (with `--list` to show all)
- **Env vars**: None
- **Network**: None (model resolution is local, tiny gen only if model already downloaded)
- **Security**: Read-only model resolution, no model download, no privilege escalation

### unmount_ramdisk.sh
- **Lines**: 50 | **Size**: 1.1K
- **Purpose**: Unmounts and detaches the RAM disk at `/Volumes/ramdisk`.
- **Entry point**: `main "$@"` (bash sourced)
- **Privileges**: Requires `root`/`sudo` for `hdiutil detach`
- **Writes**: Syncs before detach, flushes pending writes
- **Security**: Local disk unmount only, no network, no data exfil
- **Risk**: Privilege escalation via `sudo` — only use in local dev environments

### Summary Table

| Script | Type | Purpose | Privilege | Network | Security Risk |
|--------|------|---------|-----------|--------|---------------|
| `check_torrc.py` | Python | Tor config validation | None | None | Low |
| `extract_nonfeed_seeds.py` | Python | DuckDB seed extraction | None | None | Low |
| `model_stack_smoke.py` | Python | Model stack smoke check | None | None | Low |
| `mount_ramdisk.sh` | Bash | RAM disk creation | sudo/root | None | Medium (priv escalation) |
| `pre_commit_guard.py` | Python | Git hook blocker | None | None | Low |
| `score_corroboration.py` | Python | Evidence scoring | None | None | Low |
| `smoke_llm_candidate.py` | Python | LLM candidate resolution | None | None | Low |
| `unmount_ramdisk.sh` | Bash | RAM disk teardown | sudo/root | None | Medium (priv escalation) |

**Key findings**:
- No scripts write to network or exfiltrate data
- Two scripts (`mount_ramdisk.sh`, `unmount_ramdisk.sh`) require `sudo` — both are local dev utilities, not CI/CD
- `pre_commit_guard.py` is the only CI/CD hook; it blocks `None` files from being committed
- All scripts are idempotent and read-only except `mount_ramdisk.sh`/`unmount_ramdisk.sh` which manage a RAM disk mount point

# Storage & Telemetry Audit — 20260522

## 1. DuckDB Schema

**File:** `knowledge/duckdb_store.py` (~2469 lines)
**Write path:** `insert_finding()` / `insert_findings_bulk()` → `_sync_insert_findings_bulk()` via `ThreadPoolExecutor(max_workers=1)` (single dedicated thread, thread-affine connection)
**Async:** all public methods use `run_in_executor` to avoid event-loop blocking
**Schema (11 CREATE TABLE statements):**

```
shadow_findings (
  id VARCHAR PRIMARY KEY,
  source_type VARCHAR,
  confidence DOUBLE,
  provenance_json TEXT,
  UNIQUE (query, source_type)
)

shadow_runs (
  run_id VARCHAR PRIMARY KEY,
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  total_fds INTEGER,
  rss_mb INTEGER
)

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
  findings_per_minute REAL DEFAULT 0,
  top_source_type TEXT,
  synthesis_confidence REAL DEFAULT 0
)

source_hit_log (
  sprint_id TEXT,
  ts DOUBLE,
  source_type TEXT,
  findings_count INT,
  ioc_count INT,
  hit_rate REAL
)

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

research_episodes (
  episode_id TEXT PRIMARY KEY,
  sprint_id TEXT NOT NULL,
  query TEXT NOT NULL,
  summary TEXT,
  top_findings JSON,
  ioc_clusters JSON,
  source_yield JSON,
  synthesis_engine TEXT,
  duration_s REAL
)

target_profiles (
  target_id TEXT PRIMARY KEY,
  first_seen DOUBLE,
  last_seen DOUBLE,
  cumulative_finding_count INTEGER,
  entity_summary_json TEXT
)

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

target_memory (
  target_id TEXT PRIMARY KEY,
  first_seen_ts DOUBLE,
  last_seen_ts DOUBLE,
  sprint_count INTEGER,
  cumulative_finding_count INTEGER,
  entity_facets_json TEXT,
  exposure_facets_json TEXT,
  pivot_facets_json TEXT,
  confidence_drift_json TEXT,
  updated_by_sprint_id TEXT,
  updated_ts DOUBLE
)

global_entities (
  entity_id TEXT PRIMARY KEY,
  entity_type TEXT,
  first_seen_ts DOUBLE,
  last_seen_ts DOUBLE,
  sprint_count INTEGER,
  cumulative_finding_count INTEGER,
  entity_facets_json TEXT,
  exposure_facets_json TEXT,
  pivot_facets_json TEXT,
  confidence_drift_json TEXT,
  updated_by_sprint_id TEXT,
  updated_ts DOUBLE
)
```

---

## 2. LanceDB Configuration

**File:** `knowledge/lancedb_store.py` (1432 lines)
**Role:** Identity/Entity store for entity resolution — NOT grounding authority (that is `rag_engine`)
**Table:** `self._table = self.db.create_table(...)` — no explicit string name constant
**Embedding dimension:** `self._embedding_dim = 768`
**Index type:** `usearch` with `Index(ndim=self._embedding_dim)` — not HNSW, uses usearch (usearch-py)
**Fallback:** Numpy fallback at line 200+ for dimension handling

---

## 3. LMDB Status

**File:** `tools/lmdb_kv.py` (360 lines)
**DEFAULT_MAP_SIZE:** `256 * 1024 * 1024` (256MB) — `tools/lmdb_kv.py:50`
**MAX_KEYS:** 10000
**LMDB_WRITE_BATCH_SIZE:** 500
**Atomicity:** No `asyncio.Lock` in `LMDBKVStore` itself. `open_lmdb()` from `paths.py` is used.
**Callers:** `from tools.lmdb_kv import LMDBKVStore` is used by semantic deduplicator, duckdb store (sprint LMDB), and finding envelope storage

---

## 4. MLX Cache Limits

**File:** `utils/mlx_cache.py`
| Limit | Value | Source |
|-------|-------|--------|
| `_MLX_CACHE_MAX` | 2 | `utils/mlx_cache.py:39` |
| `_METAL_WIRED_LIMIT_BYTES` | `int(0.5 * 1024**3)` = **512 MiB** | `utils/mlx_cache.py:191` |
| `mx.metal.clear_cache()` | called after `mx.eval([])` barrier in `_safe_mlx_eval_and_clear_cache()` | `utils/mlx_cache.py:344-366` |
| Prompt cache `max_kv_size` | 512 (system), 8192 (generate), env `GHOST_KV_SIZE` default 4096 | `brain/hermes3_engine.py:930,1106,2196` |
| `kv_bits` | 4 | `brain/hermes3_engine.py:1107` |

---

## 5. Memory Budget Verification

**Hermes3 Model** (`brain/hermes3_engine.py`):
- Model: `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` (line 173)
- Quantization: `layer.quantize(group_size=64, bits=4)` at line 1095
- Loaded via `mlx_lm.load()` at line 801

| Component | Size | Source |
|-----------|------|--------|
| LLM weights (4bit, 3B param) | ~1.6 GB | `brain/hermes3_engine.py:173,1095` |
| KV cache (max_kv_size=8192, kv_bits=4) | ~64 MB | `brain/hermes3_engine.py:1106-1107` |
| ANE embedder (ModernBERT) | ~80 MB (CoreML converted) | `brain/ane_embedder.py` |
| NER (GLiNER-X gliner-relex-large-v0.5) | ~250 MB | `brain/ner_engine.py` |
| Python runtime + coordinators | ~300 MB (estimate) | — |
| DuckDB in-memory | ~200 MB | `knowledge/duckdb_store.py` |
| LMDB maps (256MB x2) | ~512 MB | `tools/lmdb_kv.py:50` |
| **Theoretical peak** | **~3000 MB** | |
| **Available on M1 8GB** | **~5500 MB** | |
| **% utilization** | **~55%** | |

Note: Above is conservative — actual peak depends on concurrency levels and whether models are simultaneously in memory. The ANE embedder and NER may not be loaded at the same time as Hermes3 depending on sprint configuration.

---

## 6. Sprint Telemetry Fields

**File:** `export/sprint_exporter.py` (4960 lines)
All exported keys (found in output dict literals):

`sprint_id`, `lane`, `runtime_accepted_findings`, `runtime_findings_per_minute`, `synthesis_engine`, `runtime_truth`, `synthesis_outcome_payload`, `source_family_outcomes`, `lane_details`, `scorecard`, `planner_state`, `terminal_state`, `terminal_coverage`, `product_value_summary`, `source_family_summary`

Phase timing labels (from `sprint_timer.py`):
`mandatory_prelude`, `runtime_pivot_seed_extraction`, `planner_actions_consumption`, `nonfeed_prelude_gather`, `public_lane`, `ct_lane`, `doh_lane`, `wayback_lane`, `passive_dns_lane`, `graph_accumulation`, `pivot_planning`, `export`, `investigation_packet_build`, `next_sprint_seeds_generation`

---

## 7. Runtime Diagnosis Categories

**Function:** `_compute_runtime_diagnosis()` at `export/sprint_exporter.py:4895`

| Bottleneck | Condition |
|------------|-----------|
| `public_lane_slow` | public lane exceeds threshold |
| `wayback_lane_slow` | wayback lane exceeds threshold |
| `ct_lane_slow` | CT lane exceeds threshold |
| `graph_accumulation_slow` | graph phase exceeds threshold |
| `export_slow` | export phase exceeds threshold |
| `{slowest_phase}_slow` | fallback using slowest phase name |
| `unknown` | default when no bottleneck detected |

`recommended_runtime_action` is set per bottleneck type.

---

## 8. Broken Imports Summary

**File:** `broken_imports.json` (281 entries, 172 unique modules, 72 unique files)
**Blocking `__main__.py`:** 0 entries (no critical blockers)
**hledac_core (Rust):** 0 entries

**Top 10 most-missing modules:**
| Count | Module |
|-------|--------|
| 16x | `hledac.universal.layers.build_temporal_priority_hints` |
| 13x | `hledac.universal.utils.ActionResult` |
| 8x | `hledac.universal.rl.marl_coordinator.MARLCoordinator` |
| 7x | `hledac.universal.runtime.memory_watchdog.PressureLevel` |
| 6x | `hledac.universal.transport.TransportContext` |
| 6x | `hledac.universal.utils.get_uuid7_compat_status` |
| 5x | `hledac.core.mlx_embeddings.MLXEmbeddingManager` |
| 5x | `hledac.core.mlx_embeddings.get_embedding_manager` |
| 5x | `hledac.universal` (top-level import) |
| 5x | `hledac.universal.transport.TransportResolver` |

**Pattern:** These are from deleted/moved/refactored sprint code (ghost backups, deprecated coordinators like `marl_coordinator`, `memory_watchdog`, `ghost_director`) and a Rust core (`hledac_core`) that doesn't exist yet. None would block `__main__.py` startup — they are internal probe/test imports.

---

## 9. Critical Blockers

**`__main__.py` startup blockers: NONE**

The broken imports are in test/probe files and deprecated ghost modules — none are in the canonical boot path (`__main__.py`, `core.__main__.run_sprint()`, `SprintScheduler`).

The only notable pattern is `hledac_core` — a Rust MLX embeddings module that is imported but doesn't exist. This is a planned architecture (Rust extensions) with graceful fallback, not a blocking crash.

---

## 10. Sprint Timer

**File:** `runtime/sprint_timer.py` (exists)
Records wall-clock time per instrumented phase using `time.monotonic()`. All 14 phase labels listed above are instrumented. Uses `deque` for bounded event list (not unbounded `list.append`).

---

*Audit completed 2026-05-22. Sources verified via direct code read.*
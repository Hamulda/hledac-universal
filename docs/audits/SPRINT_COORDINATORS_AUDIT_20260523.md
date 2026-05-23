# SPRINT_COORDINATORS_AUDIT_20260523.md

## Zoom-Out Deep Analysis

> **Status**: REVISED after multi-agent verification — false positives removed, method names corrected, paths verified

---

### 1. Entry Points

| Entry Point | File | Line | Parameters | Notes |
|------------|------|------|------------|-------|
| `run_sprint()` | `core/__main__.py` | 1099 | `query: str, config: SprintConfig` | Canonical sprint owner |
| `run_ct_pivot(domain)` | `core/__main__.py` | 2072 | `domain: str` | CT pivot CLI entry |
| `run_semantic_pivot(query, top_k)` | `core/__main__.py` | 2101 | `query: str, top_k: int = 10` | Semantic pivot CLI entry |
| `async_run_live_public_pipeline()` | `pipeline/live_public_pipeline.py` | ~3052 | `source_types, canonical_findings_factory, ...` | Standalone public pipeline |
| `async_run_live_feed_pipeline()` | `pipeline/live_feed_pipeline.py` | ~1447 | `canonical_findings_factory, ...` | Feed pipeline runner |
| `python -m hledac.universal.core` | `core/__main__.py` | CLI | argparse | Pre-sprint checks, UMA wiring |

**Canonical sprint owner**: `core.__main__.run_sprint()` → `SprintScheduler.run()`

---

### 2. Domain Map (Module Grid)

| Domain | Module Path | Owns | Depends On | Key Public APIs |
|--------|-----------|------|------------|----------------|
| **brain** | `brain/` | Hermes3 model lifecycle, inference, synthesis, KV cache, MoE/VLM routing | transport, knowledge | `prewarm()`, `infer()`, `synthesize()`, `batch_schedule()` |
| **coordinators** | `coordinators/` | Task coordination; CoordinatorCatalog lazy-loading | NONE in sprint path | `UniversalCoordinator`, `CoordinatorRegistry` (dead design pattern) |
| **knowledge** | `knowledge/` | DuckDBStore (canonical findings), LanceDB (vector ANN via ann_index.py) | pipeline, brain | `async_ingest_findings_batch()`, `vector_search()`, `target_memory_store()` |
| **fetching** | `fetching/` | HTTP transport abstraction, public_fetcher | transport | `fetch()`, `fetch_with_retry()` |
| **pipeline** | `pipeline/` | Finding flow: source → parse → quality gate → scoring | knowledge | `async_run_live_public_pipeline()`, `async_run_live_feed_pipeline()` |
| **runtime** | `runtime/` | SprintScheduler, lifecycle, pivot planning | pipeline, brain | `run()`, `acquire_sources()`, `run_advisories()` |
| **security** | `security/` (dir) | Security validation, leak sentinel, identity stitching | knowledge, intelligence | `validate_finding()`, `check_leak()` |
| **forensics** | `forensics/` (dir) | Forensics LMDB, identity stitcher | knowledge, intelligence | `ForensicsEnricher`, `IdentityStitcherAdapter` |
| **multimodal** | `brain/` | Vision/OCR via VLM routing | brain | `route_to_vlm()`, `classify_image()` |
| **text** | `intelligence/` | Text normalization, encoding detection | — | `normalize()`, `deduplicate()` |
| **hypothesis** | `pipeline/` | Pivot candidate generation, hypothesis | runtime | `generate_pivot_candidates_from_query()` |
| **export** | `export/` | Finding export (CSV, JSON, JSONL, HTML, STIX) | knowledge | `export_findings()`, `format_evidence()` |
| **transport** | `transport/` | CircuitBreaker, curl_cffi, httpx, tor, nym, i2p | — | `CircuitBreaker`, `TransportResolver`, `get_transport()` |
| **utils** | `utils/` | Async helpers, time, math, config, MLX cache | — | `gather()`, `time_monotonic()`, `bounded()` |
| **intelligence** | `intelligence/` | Temporal archaeology, leak sentinel, passive DNS, DOH, wayback | fetching | `TemporalArchaeologistAdapter`, `LeakSentinelAdapter`, `WaybackAdapter`, `DOHAdapter` |

**Note**: `brain/vlm_routing.py` does NOT exist — VLM routing is in `brain/moe_router.py`.  
**Note**: `forensics/enrichment_service.py` exists (not `runtime/enrichment_services.py` — that is a DIFFERENT file).  
**Note**: `transport/curl_transport.py` does NOT exist — actual file is `transport/curl_cffi_transport.py`.
**Note**: `hledac/core/` is a **sibling package** (NOT part of hledac/universal/) — contains `sprint_lifecycle.py`, `resource_governor.py`, `unified_ai_orchestrator.py`. Coordinators import from `hledac.core.*`, not `hledac.universal.core.*`.

---

### 2b. Related Packages & External Modules

| Package | Relationship | Key Contents |
|---------|-------------|-------------|
| `hledac/core/` (sibling) | Separate package — coordinators import from here | `sprint_lifecycle.py`, `resource_governor.py`, `unified_ai_orchestrator.py`, `watchdog.py` |
| `hledac-core/` | Separate package | MLX embeddings, AI orchestration |
| `layers/` | Separate orchestration layer — NOT part of sprint prod path | `coordination_layer.py`, `memory_layer.py`, `layer_manager.py` — has lazy-import references to coordinators as a standalone system |
| `tools/` | Utility modules | `url_dedup.py`, `lmdb_kv.py`, `checkpoint.py`, `wayback_adapter.py` |
| `network/` | Runtime networking | `session_runtime.py`, `banner_grabber.py` |
| `transport/` | HTTP transport abstraction | See Transport Seam Table |

**`layers/coordination_layer.py` note**: Has lazy-import pattern for `UniversalSecurityCoordinator` and `UniversalMemoryCoordinator` — but this is a **separate self-contained system**, NOT invoked by `SprintScheduler.run()`. It is a standalone memory/security layer, not part of the sprint execution path. The document's "all coordinators dead" claim refers specifically to the sprint prod path.

---

### 3b. Root-Level Entry Points (not in sprint path)

These exist in `hledac/universal/` root but are NOT part of the `run_sprint()` canonical path:

| File | Purpose | Entry Type |
|------|---------|-----------|
| `autonomous_orchestrator.py` | Legacy autonomous orchestrator facade | `main()` argparse |
| `enhanced_research.py` | Research enhancement layer | CLI module |
| `evidence_log.py` | Evidence logging | Utility |
| `tot_integration.py` | Tree-of-Thought integration | Integration |
| `orchestrator_integration.py` | Orchestrator integration | Integration |
| `capabilities.py` | Capabilities enumeration | Discovery |

**Canonical sprint owner is `core.__main__.run_sprint()`** — these root-level files are alternative/legacy entry paths.

---

### 3. Call Chain Analysis

**Canonical Path**: `core.__main__.run_sprint()` → `SprintScheduler.run()`

#### Depth 1 (Direct calls from SprintScheduler.run())
```
SprintScheduler.run()
├── _init_metrics_registry()                  [async, internal — MetricsRegistry fail-soft]
├── _load_dedup()                            [async, internal — SemanticDedupLMDB created here]
├── _prewarm_hermes_for_sprint()             [async, calls get_model_manager().load_model("hermes")]
├── _runner.sleep_or_abort()                 [async, injected SprintRunner]
├── _run_one_cycle()                         [async — CORRECTED: was mislabeled as _run_cycle()]
│   ├── _run_public_branch()                 [async — CORRECTED: was _run_public_live_lane()]
│   │   └── _run_public_discovery_in_cycle() [async]
│   │       └── async_run_live_public_pipeline() [pipeline] ← calls async_fetch_public_text() NOT PublicFetcher.fetch()
│   ├── _run_feed_branch()                   [async — CORRECTED: was _run_feed_lane()]
│   │   └── _run_feed_discovery_in_cycle()    [async]
│   │       └── async_run_live_feed_pipeline() [pipeline]
│   ├── _run_nonfeed_branch()               [async — CORRECTED: was _run_nonfeed_lane()]
│   │   └── _run_nonfeed_diagnostic_cycle()  [async]
│   └── _run_archive_branch()               [async — CORRECTED: was _run_archive_lane()]
│       └── _run_wayback_cdx_deep_sidecar()  [async]
├── _run_advisory_runner()                  [async — CORRECTED: was _run_advisories()]
│   ├── pivot_planner.generate_pivot_candidates_from_query() [pipeline/pivot_lane_planner.py]
│   ├── leak_sentinel_adapter.fetch_*()      [intelligence/leak_sentinel.py]
│   ├── temporal_archaeologist_adapter.mine() [intelligence/temporal_archaeologist_adapter.py]
│   └── forensics_enricher.enrich_one()       [forensics/enrichment_service.py]
├── _enrichment_services._flush_forensics()  [async]
├── _finalize_result_truth()               [sync]
└── _run_export() / _run_cti_export()       [async]
```

**Key Corrections vs Previous Audit**:
- `_run_cycle()` → `_run_one_cycle()` (line 6340)
- `_run_public_live_lane()` → `_run_public_branch()` (line 6798)
- `_execute_work_lanes()` → DOES NOT EXIST as a method
- `_run_advisories()` → `_run_advisory_runner()` (no direct equivalent — advisory runner is different)
- `_quality_gate_pass()` → DOES NOT EXIST as a method — quality gate is INSIDE `async_run_live_public_pipeline()`
- `PublicFetcher.fetch()` → `async_fetch_public_text()` (different function in pipeline)
- Hermes3 is NOT injected via `inject_brain()` — NO SUCH METHOD. Brain is loaded internally via `get_model_manager().load_model("hermes")` at line 9877
- DuckDBStore is NOT called directly by SprintScheduler — passed to pipeline as param, pipeline calls `async_ingest_findings_batch()` internally

#### DI vs Internal Creation (verified)
| Component | How | Verified |
|-----------|-----|----------|
| DuckDBStore | Injected via `inject_duckdb_store()` (line ~11068) | ✓ |
| SprintLifecycleManager | Received as `lifecycle` param in `__init__` | ✓ |
| EnrichmentServices | Via `inject_forensics_enricher()` + `inject_multimodal_enricher()` (separate methods) | ✓ |
| **Hermes3/brain** | **INTERNAL** via `_load_hermes_for_sprint()` → `get_model_manager().load_model()` | **CORRECTED** |
| SemanticDedupLMDB | Internal via `_load_dedup()` | ✓ |
| SprintRunner | Via `_runner` param | ✓ |
| MetricsRegistry | Fail-soft import from `metrics_registry.py` | ✓ |

---

### 4. Data Flow Diagram

```
SPRINT CYCLE (SprintScheduler.run())
│
├── PRELUDE + FIRST CYCLE concurrently
│   └── _run_mandatory_acquisition_prelude() + _run_one_cycle() in asyncio.gather
│
├── OODA LOOP (60s cycle via _bg_tasks)
│   └── _run_ooda_cycle() → pivot/OODA analysis
│
├── ADVISORY RUNNER (_run_advisory_runner)
│   ├── PivotPlanner → generate_pivot_candidates_from_query()
│   ├── LeakSentinelAdapter → paste/github/breach signals
│   ├── TemporalArchaeologistAdapter → CT timestamp composition
│   └── ForensicsEnricher → LMDB forensics flush
│
├── BRANCH EXECUTION (parallel via asyncio.gather)
│   ├── PUBLIC BRANCH: _run_public_branch()
│   │   └── async_run_live_public_pipeline()
│   │       └── async_fetch_public_text()  ← uses FetchCoordinator transport internally
│   │           ├── Discovery (URL list)
│   │           ├── Fetch (curl_cffi stealth HTTP)
│   │           ├── Parse (BeautifulSoup/regex)
│   │           ├── Quality Gate (inline in pipeline) ← NOT a separate module
│   │           └── duckdb_store.async_ingest_findings_batch() ← INSIDE pipeline, not SprintScheduler
│   │
│   ├── FEED BRANCH: _run_feed_branch()
│   │   └── async_run_live_feed_pipeline() → same pattern
│   │
│   ├── NONFEED BRANCH: _run_nonfeed_branch()
│   │   ├── leak_sentinel → CanonicalFinding
│   │   ├── passive_dns → exposure correlation
│   │   └── pivot_planner → next seeds
│   │
│   └── ARCHIVE BRANCH: _run_archive_branch()
│       └── _run_wayback_cdx_deep_sidecar() → WaybackAdapter
│
├── ENRICHMENT (sequential at cycle end)
│   ├── Forensics LMDB flush (_flush_forensics)
│   └── Multimodal enricher (if RAM allows)
│
├── EXPORT (at TEARDOWN)
│   └── _run_export() → CSV/JSON/JSONL → _run_cti_export() → STIX
```

**Data Shapes (TypedDict/dataclass)**:
| Type | Location | Fields |
|------|----------|--------|
| `CanonicalFinding` | `knowledge/duckdb_store.py` | msgspec.Struct, frozen=True |
| `PipelineRunResult` | `pipeline/live_public_pipeline.py` | raw_count, built_count, accepted_count, attempted, skipped, err |
| `PipelinePageResult` | `pipeline/live_public_pipeline.py` | Page-level parsing result |
| `PivotTask` | `runtime/sprint_scheduler.py` | Pivot task descriptor |
| `AcquisitionProfile` | `runtime/acquisition_strategy.py` | Source acquisition config |
| `SourceTier` | `runtime/sprint_scheduler.py` | Enum: PUBLIC, FEED, NONFEED, ARCHIVE |

---

### 5. Transport Seam Table

| Transport | File | JA3 Fingerprint | Circuit Breaker | Use Case |
|-----------|------|---------------|-----------------|----------|
| `curl_cffi` | `transport/curl_cffi_transport.py` | Yes (stealth mode) | Via `CircuitBreaker` | Stealth/403/429 retry |
| `httpx` | `transport/httpx_transport.py` | No (std browser headers) | `H2CircuitBreaker` | HTTP/2 structured endpoints |
| `tor` | `transport/tor_transport.py` | JARM fingerprint | Via `CircuitBreaker` | Anonymous routing |
| `nym` | `transport/nym_transport.py` | N/A | Via `CircuitBreaker` | Nym mixnet |
| `i2p` | `transport/i2p_transport.py` | N/A | Via `CircuitBreaker` | I2P anonymous |

**Does NOT exist**: `transport/aiohttp_transport.py`, `transport/curl_transport.py`  
**JA3 app**: `transport/transport_router.py:153` — curl_cffi handles JA3 spoofing in stealth mode  
**Circuit Breaker**: `transport/circuit_breaker.py` — `CircuitBreaker` class applied in `transport/httpx_transport.py` as `H2CircuitBreaker`

**Note**: FetchCoordinator class exists (`coordinators/fetch_coordinator.py`) but is NOT wired to sprint_scheduler. Actual HTTP fetch in pipeline uses `async_fetch_public_text()` which calls curl_cffi directly.

---

### 6. Storage Architecture

| Store | File | What It Stores | Canonical Write Path | ANN/RAG |
|-------|------|--------------|---------------------|---------|
| **DuckDB** | `knowledge/duckdb_store.py` | CanonicalFinding (structured findings) | SprintScheduler passes `duckdb_store` param to pipeline lanes → pipeline calls `store.async_ingest_findings_batch()` at `live_public_pipeline.py:2003,2136,2614,2684,3044` | No (relational) |
| **LanceDB** | `knowledge/lancedb_store.py` + `knowledge/ann_index.py` | Vector embeddings for RAG cross-run dedup | `lancedb_store.add()` — called from `check_ann_duplicate()` in dedup path | Yes (vector ANN) |
| **LMDB (semantic dedup)** | `tools/lmdb_kv.py` | Semantic dedup bloom filter | `SemanticDedupLMDB` created internally in `_load_dedup()` | No (KV) |
| **LMDB (forensics)** | `forensics/enrichment_service.py` | Forensics enrichment data keyed by finding_id | `ForensicsEnricher.flush()` at WINDUP entry | No (KV) |

**Canonical write path**: Pipeline lane → `duckdb_store.async_ingest_findings_batch()` → DuckDB (canonical)  
**Read path**: LanceDB for vector similarity; DuckDB for structured queries  
**Both LMDB stores use `paths.open_lmdb()`** (fail-safe singleton pattern)

---

### 7. MLX/Brain

| Component | File | Owner | Public APIs |
|-----------|------|-------|-------------|
| **Hermes3 Model** | `brain/hermes3_model.py` | Brain mod | `prewarm()`, `infer()`, `synthesize()` |
| **KV Cache** | `brain/hermes3_kv_cache.py` | Brain mod | LRU cache management |
| **MoE Router** | `brain/moe_router.py` | Brain mod | Expert routing + VLM classification |
| **Batch Scheduler** | `brain/batch_scheduler.py` | Brain mod | Batch inference scheduling |
| **Prompt Injection Validator** | `brain/prompt_injection_validator.py` | Brain mod | Security validation |
| **LMDB Cache** | `utils/mlx_cache.py` | Brain mod | `aggressive_cleanup()` with `mx.eval([])` barrier |

**Lifecycle**: `_prewarm_hermes_for_sprint()` → `get_model_manager().load_model("hermes")` → KV cache priming → ready  
**Does NOT exist**: `brain/kv_cache.py`, `brain/vlm_routing.py` — VLM routing is in `brain/moe_router.py`  
**mx.eval barrier** (GHOST_INVARIANT): verified at `utils/mlx_cache.py:437-440` — `mx.eval([])` before `clear_cache()`:
```python
if hasattr(mx, 'clear_cache'):
    mx.clear_cache()  # line 438
elif hasattr(mx.metal, 'clear_cache'):
    mx.metal.clear_cache()  # line 440
```
**Inference Callers**: SprintScheduler advisory runner (hypothesis synthesis), enrichment services (multimodal)

---

### 8. Sidecar Wiring Table

| Sidecar | File | Class | Wired | Status | Trigger |
|--------|------|-------|-------|--------|---------|
| **PivotPlanner** | `pipeline/pivot_lane_planner.py` | `PivotPlanner` | Yes | Active | `generate_pivot_candidates_from_query()` called via `_run_advisory_runner()` |
| **LeakSentinel** | `intelligence/leak_sentinel.py` | `LeakSentinelAdapter` | Yes | Active | Called in nonfeed advisory path |
| **TemporalArchaeology** | `intelligence/temporal_archaeologist_adapter.py` | `TemporalArchaeologistAdapter` | Yes | Active | Called in nonfeed advisory path |
| **ForensicsEnrichment** | `forensics/enrichment_service.py` | `ForensicsEnricher` | Yes | Active | `inject_forensics_enricher()`; `_flush_forensics()` at WINDUP |
| **BGP Advisory** | `runtime/sprint_scheduler.py:8594` | `_run_bgp_advisory_sidecar()` | Yes | Active | Runs as bg_task in nonfeed path |
| **Identity Stitching** | `forensics/` or `intelligence/` | `IdentityStitchingAdapter` | Yes | Active | Referenced in forensics path |
| **WaybackCDX** | `tools/wayback_adapter.py` | `WaybackAdapter` | Yes | Active | `_run_wayback_cdx_deep_sidecar()` at line 8700 |
| **DOH Adapter** | `intelligence/doh_lane.py` | `DOHAdapter` | Yes | Active | DNS over HTTPS lane in nonfeed |

**Inject methods** (verified in sprint_scheduler.py):
- `inject_duckdb_store()` → passes DuckDBStore to pipeline
- `inject_forensics_enricher()` → ForensicsEnricher
- `inject_multimodal_enricher()` → MultimodalEnricher
- `inject_pivot_planner()` → PivotPlanner
- `inject_policy_manager()` → RL policy manager

**Note**: `inject_enrichment_services()` does NOT exist as a single method — forensics and multimodal are separate injectors.

---

### 9. Coordinator Orphan Verification

**VERIFIED: Coordinators exist but are NOT instantiated by SprintScheduler or sprint execution path**

| Coordinator | Instantiated in prod? | Where referenced |
|-------------|----------------------|------------------|
| `FetchCoordinator` | **NO** (prod path uses curl_cffi directly) | `autonomous_orchestrator.py:225` (comment), test files |
| `SecurityCoordinator` | **NO** (dead code) | `layers/coordination_layer.py:84` (lazy import pattern, not called) |
| `MemoryCoordinator` | **NO** (dead code) | `layers/coordination_layer.py:86` (lazy import pattern, not called) |
| `ValidationCoordinator` | **NO** | — |
| `PerformanceCoordinator` | **NO** | — |
| `ResourceAllocator` | **NO** | — |
| `GraphCoordinator` | **NO** | — |
| `MultimodalCoordinator` | **NO** | — |
| `ResearchCoordinator` | **NO** | — |
| `ClaimsCoordinator` | **NO** | — |
| `SwarmCoordinator` | **NO** | — |
| `MonitoringCoordinator` | **NO** | — |
| `MetaReasoningCoordinator` | **NO** | — |
| `ExecutionCoordinator` | **NO** | — |

**prod path**: `SprintScheduler.run()` → `_run_one_cycle()` → branch functions → `async_run_live_public_pipeline()` → pipeline calls `async_fetch_public_text()` using curl_cffi directly. No coordinator is instantiated.

**layers/coordination_layer.py** has lazy-import references to `UniversalSecurityCoordinator` and `UniversalMemoryCoordinator`, but these are **NOT called in the sprint path** — they are in the layers/ module which is a separate orchestration layer.

**Conclusion**: Coordinator pattern is a design placeholder — not active in sprint execution.

---

### 10. GHOST_INVARIANTS Compliance

| Invariant | Status | Verified Location |
|-----------|--------|------------------|
| `gather(return_exceptions=True)` | **COMPLIANT** | SprintScheduler has multiple actual exec calls (not just comments): line ~7653, ~8440 in docstrings but ALSO actual calls: `_results = await asyncio.gather(prelude_task, first_cycle_task, return_exceptions=True)` and `await asyncio.gather(*self._bg_tasks, return_exceptions=True)` |
| `mx.eval([])` before `clear_cache` | **COMPLIANT** | `utils/mlx_cache.py:437-440` — `mx.eval([])` before `mx.clear_cache()` / `mx.metal.clear_cache()` |
| `time.monotonic` for intervals | **COMPLIANT** | SprintScheduler uses `utils.time.monotonic()` (adapter wrapper) throughout |
| No bare `except` | **COMPLIANT** | Uses `except Exception:` or specific exception types |
| Bounded collections | **COMPLIANT** | `MAX_LANE_REJECTIONS=1000` (line 96), `MAX_GC_STATS=1000` (line 100), `MAX_TRACKED_DOMAINS` bounded |

**MetricsRegistry**: Imported fail-soft from `metrics_registry.py` at `runtime/sprint_scheduler.py:9721` — not a coordinator registry.

---

### Summary Table

| Component | Status | Notes |
|-----------|--------|-------|
| Entry Points | 6 + 6 root-level | Canonical: `core.__main__.run_sprint()` |
| Domain Modules | 15 domains | Full coverage |
| Call Chain | **CORRECTED** | `_run_one_cycle()` not `_run_cycle()`; `_run_public_branch()` not `_run_public_live_lane()`; `_run_advisory_runner()` not `_run_advisories()` |
| Data Flow | 5 stages | src → Fetch → Parse → Quality (inline) → Storage → Export |
| Transport Layers | 5 transports | curl_cffi, httpx, tor, nym, i2p; NO aiohttp_transport |
| Storage Layers | 3 stores | DuckDB (canonical write via pipeline at lines 2003/2136/2614/2684/3044), LanceDB (ANN), LMDB (KV) |
| Brain/MLX | 6 components | Hermes3 loaded internally via `get_model_manager()`, NOT injected |
| Sidecars | 8 wired | PivotPlanner, LeakSentinel, TemporalArchaeology, Forensics, BGP, IdentityStitching, Wayback, DOH |
| Coordinators | Package dead in sprint path; `layers/` is separate live system | 14 coordinators not instantiated in sprint prod path |
| Related Packages | 4 added | `hledac/core/` (sibling), `hledac-core/`, `layers/` (separate system), `tools/` |
| GHOST_INVARIANTS | COMPLIANT | All invariants verified in source |

### 17. rl/ Directory

**Purpose**: Reinforcement Learning module for sprint policy optimization. Contains the QMIX MARL algorithm implementation (MLX-based), the `SprintPolicyManager` policy advisor, state extraction for agents, replay buffer, and action definitions. All state is persisted to disk as JSON (optionally ZSTD-compressed).

#### File Inventory

| File | Format | Size | Purpose | RL Component |
|------|--------|------|---------|-------------|
| `sprint_policy_manager.py` | Python | 15.3 KB | Opt-in RL policy advisor for sprint execution; provides action hints (`should_explore`, `action_deep_dive`, `action_continue`, `action_fetch_more`) | SprintPolicyManager |
| `qmix.py` | Python | 9.6 KB | QMIX: Value Decomposition Networks for Multi-Agent RL. Includes `QNetwork`, `QMixer`, `QMIXAgent`, `QMIXJointTrainer`, `JointModel`. Requires MLX (Apple Silicon). Falls back to ImportError stubs on non-MLX hardware. | QMIX Agent |
| `state_extractor.py` | Python | 2.4 KB | Extracts state features for MARL agents from graph, scheduler, and thread-local context. Builds global + local feature vectors. | State Extractor |
| `replay_buffer.py` | Python | 3.4 KB | `MARLReplayBuffer` — experience replay buffer for QMIX. Stores `(states, actions, rewards, next_states, dones)` tuples. Uses NumPy internally. | Replay Buffer |
| `actions.py` | Python | 368 B | Action space constants: `ACTION_FETCH_MORE`, `ACTION_CONTINUE`, `ACTION_DEEP_DIVE`, `ACTION_STOP`. | Action Space |
| `__init__.py` | Python | 720 B | Module exports: `QMixer`, `QNetwork`, `MARLReplayBuffer`, `StateExtractor`, `SprintPolicyManager` | Module API |
| `.sprint_policy_state.json` | JSON | 1.0 KB | Persisted `SprintPolicyState` — epsilon, sprint_sequence_number, total_reward, sprint_rewards, source_quality_feedback | Policy State |
| `.sprint_policy_state.json.zst` | ZSTD | 134 B | Compressed snapshot of policy state (ZSTD-compressed JSON); loaded preferentially if zstd available | Policy State (compressed) |

#### Policy Persistence Mechanism

**Format**: JSON (primary) or ZSTD-compressed JSON (preferred if `zstandard` package available).

**Path**: `rl/.sprint_policy_state.json` (default); configurable via `policy_path` constructor argument.

**Load precedence**:
1. `.json.zst` (compressed) — if `zstandard` available and file exists
2. `.json` (uncompressed) — fallback

**State dataclass** (`SprintPolicyState`):
```python
@dataclass
class SprintPolicyState:
    sprint_sequence_number: int = 0      # Monotonic sprint counter
    epsilon: float = 0.1                  # Exploration probability
    total_reward: float = 0.0             # Cumulative reward
    sprint_rewards: list = field(default_factory=list)  # Per-sprint reward history
    source_quality_feedback: list = field(default_factory=list)  # Quality decisions
    enabled: bool = True                  # Policy enabled flag
```

**Save triggers**: `SprintPolicyManager.update()` called post-sprint by `SprintScheduler` (via `run()` teardown). Fail-safe — errors caught and logged, never crashes.

**Load triggers**: `SprintPolicyManager.__init__()` automatically loads on construction.

#### Integration with SprintScheduler

**Injection method**: `SprintScheduler.__init__()` accepts `inject_policy_manager: SprintPolicyManager | None = None`. Stored as `self._policy_manager`.

**Usage in `SprintScheduler.run()`**:
- `update(result)` — called post-sprint (line ~3357 in `sprint_scheduler.py`) to persist state
- `should_explore()` — called pre-sprint to decide exploration vs exploitation
- `action_deep_dive()`, `action_continue()`, `action_fetch_more()` — action hints returned to scheduler

**Feedback loop**: `SprintPolicyManager.update_with_quality_decisions()` → stores `FindingQualityDecision` list for `_adapt_source_weights_from_feedback` processing by the scheduler at teardown.

#### Exploration / Exploitation Parameters

| Parameter | Default | Storage | Mechanism |
|-----------|---------|---------|------------|
| `epsilon` | 0.1 | `SprintPolicyState.epsilon` (persisted) | Epsilon-greedy: `random.random() < epsilon` triggers exploration |
| `exploration_interval` | 5 | In-memory (`_exploration_interval`) | Deterministic interval-based: fires every N sprints `(sequence_number + 1) % interval == 0` |

**Two-layer exploration**:
1. **Stochastic** (`epsilon`): Random exploration with probability `epsilon`
2. **Deterministic interval**: Forced exploration every `_exploration_interval` sprints (sprint #5, #10, #15, ...)

#### QMIX Multi-Agent RL (MLX-only)

**Architecture**: Centralized training, decentralized execution.
- **Agents**: 5 per sprint (`n_agents=5`)
- **State dim**: 12 (`state_dim=12`)
- **Replay capacity**: 50,000 (`capacity=50000`)
- **Optimizer**: MLX `value_and_grad` with Adam
- **Gamma**: 0.99 (discount), **Tau**: 0.005 (Polyak update rate)
- **Mixer**: Hypernetwork mixing Qtot = f(Qtot, state) for value decomposition

**Current state**: QMIX classes raise `ImportError("...requires MLX (not available)")` on non-Apple-Silicon hardware. All MLX imports are lazy (deferred to first use) to allow import-time savings on M1 8GB.

---

### 18. scripts/ Directory

**Purpose**: Contains utility scripts supporting sprint operations, model evaluation, RAM disk management, and pre-commit validation. All scripts are version-controlled; none are auto-generated.

#### File Inventory

| Script | Type | Shebang / Purpose | State-Modifying? |
|--------|------|-------------------|-------------------|
| `check_torrc.py` | Python | `#!/usr/bin/env python3` — Bootstrap helper verifying Tor configuration sanity: torrc path resolution, SOCKS auth isolation, circuit hygiene checks | No |
| `extract_nonfeed_seeds.py` | Python | No shebang — CLI utility to extract nonfeed IOC seeds from either a live sprint JSON report or a DuckDB file | No |
| `model_stack_smoke.py` | Python | `#!/usr/bin/env python3` — Sprint F221A smoke check for selected model stack. Verifies: imports, availability flags, disk-free space, model download commands. Does NOT load models or modify any state | No |
| `mount_ramdisk.sh` | Shell | `#!/bin/zsh` — Mount Hledac RAM disk for performance-sensitive operations. Idempotent; requires macOS (hdiutil) | Yes (mounts filesystem) |
| `pre_commit_guard.py` | Python | `#!/usr/bin/env python3` — Pre-commit hook guard that runs LLM candidate smoke test before commit is accepted | No |
| `score_corroboration.py` | Python | No shebang — CLI utility that scores corroboration for pivot seeds (read-only scoring operation) | No |
| `smoke_llm_candidate.py` | Python | `#!/usr/bin/env python3` — Smoke test for LLM candidate evaluation. Runs model and validates output quality for sprint candidate acceptance | No |
| `unmount_ramdisk.sh` | Shell | `#!/bin/zzsh` — Safely unmount the Hledac RAM disk. Idempotent: if RAM disk is not mounted, exits 0. Requires macOS (hdiutil) | Yes (unmounts filesystem) |

#### CI/CD Integration

- **pre_commit_guard.py** is wired as a pre-commit hook (via `.pre-commit-config.yaml`)
- **smoke_llm_candidate.py** is called by `pre_commit_guard.py` during pre-commit evaluation
- **model_stack_smoke.py** is intended for local developer verification, not CI automation
- No GitHub Actions workflows reference scripts in this directory

#### Security-Sensitive Operations

- **check_torrc.py**: Reads torrc configuration files. Tor proxy configuration may contain circuit isolation directives and SOCKS auth settings. Not a credential generation or token rotation operation — purely configuration validation.
- No scripts perform API key generation, token rotation, secrets provisioning, or credential handling.

#### Standalone vs Integrated Assessment

| Script | Classification | Called By |
|--------|---------------|-----------|
| `check_torrc.py` | Standalone | Manual invocation; not called by other scripts |
| `extract_nonfeed_seeds.py` | Standalone | Manual sprint operations; not called by other scripts |
| `model_stack_smoke.py` | Standalone | Manual developer verification; not called by other scripts |
| `mount_ramdisk.sh` | Utility | Manual or wrapper scripts; not called by other scripts |
| `pre_commit_guard.py` | Integrated | Git pre-commit hook system |
| `score_corroboration.py` | Standalone | Manual sprint operations; not called by other scripts |
| `smoke_llm_candidate.py` | Integrated | `pre_commit_guard.py` (pre-commit hook) |
| `unmount_ramdisk.sh` | Utility | Manual or wrapper scripts; not called by other scripts |

---

### 16. reports/ Directory

**Purpose**: Machine-generated sprint run output directory containing live measurement results, preflight gate checks, benchmark measurements, and domain-specific diagnostic reports. Serves as the operational output log for sprint coordinator runs — both live sprints and preflight gate validations. Not curated by hand; auto-generated by the sprint runner and related tooling.

**Machine-generated vs Human-curated**: Primarily **machine-generated** (98%+). Files named with sprint IDs (e.g., `live_sprint_f234d_r2.json`) are direct outputs of the `live_sprint` measurement system. Preflight JSON files are structured gate-check outputs from the preflight system. Benchmark JSONL files are structured telemetry records. The only human-curated content is the small number of `.md` audit files (e.g., `PY314_ADVANCEMENTS_AUDIT.md`) which are written manually to document findings, and the `.log` files which capture runtime stderr/stdout.

#### Subdirectory Structure

| Subdir | Count | Purpose |
|--------|-------|---------|
| `benchmarks/` | 12 files | Runtime micro-benchmark JSONL records (throughput, parser speed, serialization latency). Named `bench_m1_runtime_gates_YYYYMMDD_HHMMSS.jsonl`. |
| `f233c/` | 4 files | Domain-lock diagnostic output for sprint f233c — paired `.json`, `.md`, `.log` for gate and live measurements. |
| `f233d/` | 5 files | Domain-lock diagnostic output for sprint f233d — paired `.json`, `.md`, `.log`. |

#### File Inventory (126 root + 21 subdir = 147 total)

##### Preflight Gate JSON (8 files)

| File | Format | Size | Date | Covers Sprint |
|------|--------|------|------|---------------|
| `preflight.json` | JSON | 6KB | 2026-05-18 | (generic preflight) |
| `preflight_f226_live.json` | JSON | 6KB | 2026-05-18 | f226 |
| `preflight_f226_live_after_blocker_fix.json` | JSON | 6KB | 2026-05-18 | f226 (post-fix) |
| `preflight_f227a_live.json` | JSON | 6KB | 2026-05-18 | f227a |
| `preflight_f230_live.json` | JSON | 6KB | 2026-05-18 | f230 |
| `preflight_f230d_live.json` | JSON | 6KB | 2026-05-18 | f230d |
| `preflight_f231c_live.json` | JSON | 6KB | 2026-05-18 | f231c |
| `preflight_f234d_deep_osint_m1.json` | JSON | 6KB | 2026-05-18 | f234d |
| `preflight_f234d_r2.json` | JSON | 6KB | 2026-05-18 | f234d r2 |

##### Live Sprint JSON (30+ files)

All follow the same schema (see below). Named `live_sprint_*.json` and `live_run_*.json`. Dates span 2026-05-12 to 2026-05-18. Notable files:

| File | Format | Size | Date | Covers Sprint / Query |
|------|--------|------|------|----------------------|
| `live_sprint_300s_20260515.log` | LOG | 8KB | 2026-05-18 | sprint 300s (LockBit) |
| `live_sprint_300s_20260516f.json` | JSON | 24KB | 2026-05-16 | sprint 300s (LockBit) |
| `live_sprint_f226_domain_lockbit3_after_blocker_fix.json` | JSON | 40KB | 2026-05-18 | f226 (LockBit3) |
| `live_sprint_f226_text_lockbit.json` | JSON | 44KB | 2026-05-18 | f226 (text LockBit) |
| `live_sprint_f230d_domain_lockbit3.json` | JSON | 44KB | 2026-05-18 | f230d (LockBit3) |
| `live_sprint_f230d_text_lockbit.json` | JSON | 44KB | 2026-05-18 | f230d (text LockBit) |
| `live_sprint_f231c_domain_lockbit3.json` | JSON | 44KB | 2026-05-18 | f231c (LockBit3) |
| `live_sprint_f234d_deep_osint_m1.json` | JSON | 40KB | 2026-05-18 | f234d (deep OSINT m1) |
| `live_run_20260512_145403.json` | JSON | 13KB | 2026-05-12 | early dry run |
| `live_run_20260512_145412.json` | JSON | 24KB | 2026-05-12 | early dry run |
| `live_run_20260512_150747.json` | JSON | 24KB | 2026-05-12 | early dry run |
| `live_run_20260512_151045.json` | JSON | 24KB | 2026-05-12 | early dry run |
| `live_run_20260512_160942.json` | JSON | 25KB | 2026-05-12 | early dry run |

##### Domain Lock Diagnostic JSON + Log (f222–f233 series)

Each has paired `.json` (structured findings) and `.log` (stderr/stdout capture). Coverage spans f222 through f233.

| File | Format | Size | Date |
|------|--------|------|------|
| `f222f_nonfeed_dry_report.json` | JSON | 13KB | 2026-05-18 |
| `f222g_lockbit_domain_nonfeed_180.json` | JSON | 34KB | 2026-05-18 |
| `f222g_lockbit_domain_nonfeed_180.log` | LOG | 40KB | 2026-05-18 |
| `f222g_lockbit_text_nonfeed_180.json` | JSON | 33KB | 2026-05-18 |
| `f222g_lockbit_text_nonfeed_180.log` | LOG | 39KB | 2026-05-18 |
| `f222h_duckdb_nonfeed_seeds.json` | JSON | 2KB | 2026-05-18 |
| `f222l_domain_dry.json` | JSON | 13KB | 2026-05-18 |
| `f223b_duckdb_nonfeed_seeds_all.json` | JSON | 3KB | 2026-05-18 |
| `f223b_duckdb_nonfeed_seeds_filtered.json` | JSON | 2KB | 2026-05-18 |
| `f223b_duckdb_nonfeed_seeds_incl_weak.json` | JSON | 3KB | 2026-05-18 |
| `f223f_duckdb_lockbit_seeds_quality.json` | JSON | 2KB | 2026-05-18 |
| `f223f_domain_lockbit3_nonfeed_180.json` | JSON | 14KB | 2026-05-18 |
| `f223f_domain_lockbit3_nonfeed_180.log` | LOG | 21KB | 2026-05-18 |
| `f223f_text_lockbit_nonfeed_180.json` | JSON | 14KB | 2026-05-18 |
| `f223f_text_lockbit_nonfeed_180.log` | LOG | 9KB | 2026-05-18 |
| `f223f2_payload_dry.json` | JSON | 13KB | 2026-05-18 |
| `f226b_domain_lockbit3_nonfeed_check.json` | JSON | 34KB | 2026-05-18 |
| `f226b_domain_lockbit3_nonfeed_check.log` | LOG | 12KB | 2026-05-18 |
| `f226d_domain_lockbit3_nonfeed_check.json` | JSON | 40KB | 2026-05-18 |
| `f226d_domain_lockbit3_nonfeed_check.log` | LOG | 13KB | 2026-05-18 |
| `f229b_domain_lockbit3_nonfeed_180.json` | JSON | 14KB | 2026-05-18 |
| `f229b_domain_lockbit3_nonfeed_180.log` | LOG | 8KB | 2026-05-18 |
| `f229d_domain_lockbit3_shape_recheck.json` | JSON | 44KB | 2026-05-18 |
| `f229d_domain_lockbit3_shape_recheck.log` | LOG | 8KB | 2026-05-18 |
| `f230f_domain_lockbit3_after_f230e.json` | JSON | 41KB | 2026-05-18 |
| `f230f_domain_lockbit3_after_f230e.log` | LOG | 12KB | 2026-05-18 |
| `f233a_domain_gate.json` | JSON | 10KB | 2026-05-18 |
| `f233a_domain_gate.md` | MD | 5KB | 2026-05-18 |
| `f233a_domain_live.log` | LOG | 2KB | 2026-05-18 |

##### Benchmark JSONL (12 files in `benchmarks/`)

| File | Format | Size | Date |
|------|--------|------|------|
| `bench_m1_runtime_gates_20260518_150512.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_150741.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_150827.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_150854.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_153106.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_153313.jsonl` | JSONL | 3KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_153339.jsonl` | JSONL | 4KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_153531.jsonl` | JSONL | 4KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_153704.jsonl` | JSONL | 4KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_172038.jsonl` | JSONL | 4KB | 2026-05-18 |
| `bench_m1_runtime_gates_20260518_172138.jsonl` | JSONL | 4KB | 2026-05-18 |
| `sprint_timer_overhead.jsonl` | JSONL | 0.4KB | 2026-05-19 |

##### Human-Curated Markdown Audits (35 files, all `.md`)

All authored manually to document research/audit findings. Dates cluster around 2026-05-05 to 2026-05-07.

| File | Format | Size | Date |
|------|--------|------|------|
| `EMBEDDING_SIMILARITY_DEDUP_AUDIT_2026-05-06.md` | MD | 13KB | 2026-05-06 |
| `F214C_ZSTD_COMPRESSION_AUDIT.md` | MD | 9KB | 2026-05-05 |
| `F214H_EXECUTOR_BACKPRESSURE_AUDIT.md` | MD | 14KB | 2026-05-05 |
| `F214I_IMPORTTIME_314_AUDIT.md` | MD | 12KB | 2026-05-05 |
| `F214M_PY314_MODERNIZATION_AUDIT_V2.md` | MD | 41KB | 2026-05-05 |
| `F214S_ARCHIVE_EXTRACTION_SECURITY_AUDIT.md` | MD | 13KB | 2026-05-05 |
| `F214SMOKE3_TARGETED_SPRINT_PATH_SMOKE.md` | MD | 13KB | 2026-05-06 |
| `F214T_TSTRING_SAFE_RENDERER_POC.md` | MD | 16KB | 2026-05-06 |
| `F214TRANSPORT_BROWSER_DEEP_AUDIT_2026-05-06.md` | MD | 18KB | 2026-05-06 |
| `F214WINDUP_NONFEED_ACCEPTANCE_AND_BARRIER_TRUTH.md` | MD | 18KB | 2026-05-06 |
| `FALSE_OPTIMIZATION_AUDIT_2026-05-06.md` | MD | 11KB | 2026-05-06 |
| `MEMORY_AUDIT_2026-05-07.md` | MD | 14KB | 2026-05-07 |
| `MEMORY_LEAK_UMA_AUDIT_2026-05-07.md` | MD | 12KB | 2026-05-07 |
| `MEMORY_OPTIMIZATION_AUDIT_2026-05-07.md` | MD | 12KB | 2026-05-07 |
| `PY314_ADVANCEMENTS_AUDIT.md` | MD | 29KB | 2026-05-05 |
| (and ~20 more F214*-prefixed audit docs) | | | |

##### Miscellaneous

| File | Format | Size | Date | Purpose |
|------|--------|------|------|---------|
| `capability_export_f228d.json` | JSON | 4KB | 2026-05-09 | Capability export manifest |
| `pytest_collect_after_p0.txt` | TXT | 249KB | 2026-05-18 | pytest collection output |
| `runtime_hygiene_event_truth.json` | JSON | 3KB | 2026-05-07 | Runtime hygiene measurement |
| `test_f226_retry.json` | JSON | 14KB | 2026-05-18 | Test retry diagnostic |
| `nonfeed_diagnostic_domain_180.json` | JSON | 30KB | 2026-05-18 | Nonfeed diagnostic |
| `nonfeed_diagnostic_lockbit_180.json` | JSON | 30KB | 2026-05-18 | Nonfeed diagnostic |
| `f233c/domain_gate.md` | MD | 5KB | 2026-05-18 | Gate report for f233c |
| `f233c/domain_live.json` | JSON | 14KB | 2026-05-18 | Live results for f233c |
| `f233c/domain_live.log` | LOG | 5KB | 2026-05-18 | Runtime log for f233c |
| `f233d/domain_live.md` | MD | 2KB | 2026-05-18 | Live report for f233d |
| `f233d/domain_gate.md` | MD | 6KB | 2026-05-18 | Gate report for f233d |

#### Report Schemas

**Live Sprint JSON Schema** (`live_sprint_*.json`, `live_run_*.json`):

Top-level fields include:
- `measurement_id` (UUID): unique measurement identifier, e.g., `lsm_1779126586593_af0355`
- `sprint_id`: sprint identifier, e.g., `8sa_1778885336861_336b8e`
- `mode`: `live` | `preflight`
- `status`: `planned` | `completed` | `aborted`
- `start_time_iso` / `end_time_iso`: ISO timestamps
- `planned_duration_s` / `actual_duration_s`
- `query`: the threat-intel query string (e.g., `LockBit ransomware`)
- `profile`: profile name (e.g., `active300`, `nonfeed_diagnostic180`)
- `findings_count`, `accepted_findings`, `cycles_completed`, `cycles_started`
- `uma_pre_used_gib`, `uma_pre_swap_gib`, `uma_pre_state`: pre-run memory snapshot
- `uma_post_*`: post-run memory snapshot
- `runtime_truth`, `timing_truth`: structured runtime assessments
- `hardware_constrained`, `swap_warning`, `swap_gate_triggered`: hardware state flags
- `nonfeed_mission_active`, `nonfeed_family_status`: nonfeed mission fields
- `acquisition_terminality_*`: acquisition terminality check fields
- `research_quality_grade`, `research_quality_score`: quality signals
- `measurement_metadata`, `derived_checks`: auxiliary metadata

**Preflight JSON Schema**:

Fields parallel live sprint but with `mode: preflight`. Key additions:
- `measurement_id`: structured as `lsm_<timestamp>_<hex_id>`
- `profile`: always `preflight`
- `status`: `planned` (preflight always planned, not run)
- `aggressive_mode`, `deep_probe`: configuration flags
- `uma_pre_*`: pre-flight memory snapshot
- `swap_policy_tier`, `swap_gate_reason`: swap gate assessment
- `recommended_next_profile`, `recommended_operator_action`: guidance fields

**Domain Gate JSON Schema** (`f233a_domain_gate.json`):

- `verdict`: `READY_FOR_FEED_BASELINE_ONLY` | `DO_NOT_RUN_UNKNOWN` etc.
- `live_allowed`: boolean
- `reasons[]`, `warnings[]`: human-readable assessments
- `uma`: memory snapshot
- `f221_artifacts`, `f223_artifacts`: prerequisite artifact presence flags
- `swap_policy_tier`, `swap_gate_reason`: swap gate
- `triage_verdict`, `capability_live_allowed`, `feed_baseline_allowed`: capability verdicts
- `canonical_fallback_detected`, `f232g_research_quality_present`: quality signals

**Benchmark JSONL Schema** (newline-delimited JSON records):

Each line is a JSON object with:
- `type`: always `benchmark_record`
- `name`: benchmark name (e.g., `body_limiter_throughput`, `html_parser_characterization`)
- `timestamp`: ISO timestamp
- `python_version`, `platform`, `free_threaded`, `jit_available`, `jit_active`
- `rss_start_kb`, `rss_psutil_start_mib`: memory at startup
- `has_psutil`, `has_selectolax`, `has_bs4`: feature flags
- `quick`: boolean
- `result`: { `status`, `wall_s`, `samples_ms[]`, `summary{ min_ms, median_ms, mean_ms, p95_ms, max_ms, runs }`, ... }
- `throughput_mb_s`: for throughput benchmarks

#### Aging and Retention Notes

- All live sprint and preflight files are dated 2026-05-18 (yesterday from audit date).
- Benchmark files are dated 2026-05-18 and 2026-05-19.
- The ~35 human-curated `.md` audit files are from 2026-05-05 to 2026-05-07 (pre-sprint F214 era).
- The `live_run_20260512_*` files (May 12) are the oldest — early dry-run experiments.
- No automatic retention/culling policy is apparent; files accumulate unchecked.

---

### 15. models/ Directory

**Purpose**: Local model artifact cache and storage directory. The `models/` directory at the project root is intentionally **empty** — no model files are bundled in the repository. All ML models are downloaded lazily at runtime from HuggingFace Hub via mlx-community repositories or cached in the user's home directory at `~/.hledac/models/`.

#### Models Not Bundled (Downloaded at Runtime)

| Model File | Format | Size | Framework | Purpose | Quantization |
|------------|--------|------|-----------|---------|---------------|
| `AllMiniLML6V2.mlmodel` / `.mlmodelc` | CoreML | ~50MB | CoreML (Apple ANE) | FastEmbed embeddings (text) | N/A (pre-compiled) |
| `ner.mlmodel` | CoreML | ~50MB | CoreML (Apple ANE) | NER/PII detection | N/A (pre-compiled) |
| `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | MLX (safetensors) | ~2GB | MLX | Primary LLM reasoner (Hermes3) | **4bit** |
| `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | MLX (safetensors) | ~2GB | MLX | Fallback LLM reasoner | **4bit** |
| `mlx-community/Qwen3-0.6B-4bit` | MLX (safetensors) | ~600MB | MLX | Structured JSON candidate (deferred) | **4bit** |
| `ms-marco-MiniLM-L-12-v2` (FlashRank) | PyTorch | ~4MB | Transformers/Torch | Re-ranker for search | N/A |
| SmolVLM2-500M-4bit (future) | MLX | ~500MB | MLX | Vision encoder (future candidate) | **4bit** |

#### Model Loading Paths

| Component | Loading Mechanism | Default Path |
|-----------|-------------------|---------------|
| Hermes3 LLM | `mlx.load()` via `brain/hermes3_engine.py` | HuggingFace Hub: `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` |
| FastEmbed Embeddings | `mlx_embedders` lazy init | `~/.cache/huggingface/hub/` (auto-download) |
| Vision Captcha Solver | CoreML `_load_model()` | `~/.hledac/models/AllMiniLML6V2.mlmodel` |
| ANE NER | CoreML `_load_coreml_model()` | `~/.hledac/models/ner.mlmodel` |
| Task Prioritizer | `mx.load()` in `research/task_prioritizer.py` | Local `Path` stored model |
| FlashRank Re-ranker | `flashrank.Reranker()` | HuggingFace Hub auto-download |
| Memory Coordinator | No direct model load | Delegates to `mlx_memory.py` for cache clearing |

#### Storage Locations

| Location | Type | Purpose |
|----------|------|---------|
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/models/` | **Empty** | Project-local cache (not used) |
| `~/.hledac/models/` | User home | CoreML models (AllMiniLML6V2, ner.mlmodel) |
| `~/.cache/huggingface/hub/` | System cache | MLX/Transformers model weights |

#### M1 RAM Constraints

- **Primary LLM** (`DeepHermes-3-Llama-3-3B-Preview-4bit`): ~2GB + KV cache overhead
- **FlashRank reranker**: ~4MB (minimal)
- **FastEmbed embeddings**: ~50MB
- **Total loaded simultaneously**: Estimated ~2.5–3GB for full MLX stack
- **Recommendation**: RAM disk for temp model staging (see `scripts/mount_ramdisk.sh`)
- **Lazy loading**: All models use fail-soft lazy initialization — no eager load on import

#### Notes

- No model files are committed to the repository
- All HuggingFace Hub models are fetched via `mlx-community/` prefix (Apple Silicon optimized)
- The `memory_coordinator.py` manages MLX GPU memory via `utils/mlx_memory.py`
- CoreML models at `~/.hledac/models/` are **optional** — app runs in dummy mode if absent
- Bundle size impact on repo: **0 MB** (models excluded via `.gitignore`)

---

*Generated: 2026-05-23*
*Revised after multi-agent verification*
*Audit Scope: `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`*

---
### 11. config/ Directory

#### Directory Purpose
The `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/config/` directory is **empty** — no config files reside here. Configuration for the universal orchestrator is instead managed in the project root via two files: `config.py` (Python source, the primary config engine) and `config-schema.json` (JSON Schema for validation). `utils/config.py` re-exports from `hledac.config` and provides a `create_config()` factory.


#### File Table

| File | Format | Loaded By | Purpose | Critical? |
|------|--------|-----------|---------|-----------|
| `config/` (dir) | — | — | **Empty directory** — not used | No |
| `config.py` | Python source | `utils/config.py`, `hledac.universal.*` modules | Primary `UniversalConfig` class: runtime presets, layer toggles, model choices, memory/coordination/distillation/privacy/security settings | **Yes — required at runtime** |
| `config-schema.json` | JSON Schema | Validated by `UniversalConfig.from_env()` validation path | JSON Schema formal description of all config sections/fields for runtime validation | Optional (used only when validating externally-loaded config) |
| `utils/config.py` | Python source | Imported by coordinators, pipeline, knowledge modules | Factory `create_config(mode, m1_optimized)` and re-exports `UniversalConfig` from `hledac.config` | **Yes — primary config entry point for submodules** |
| `.repowise/config.yaml` | YAML | repowise documentation engine | repowise tool configuration (not part of sprint runtime) | No |
| `.claudekit/config.json` | JSON | Claude Kit harness | Claude Code harness settings (not part of sprint runtime) | No |
| `pyrightconfig.json` | JSON | pyright type checker | Type-checking configuration (build/dev tool, not runtime) | No |

#### Config Loading Chain

```
utils/config.py
  └── from hledac.config import *   # re-exports UniversalConfig
       └── config.py (root)
            ├── class UniversalConfig       # main config container
            ├── class MemoryConfig          # memory limits, RAM percent
            ├── class ModelConfig           # model names, dims, batch sizes
            ├── class AgentManagerConfig    # max_concurrent_agents, timeout
            ├── class CoordinationConfig    # max_context_length, temperature
            ├── class SecurityConfig       # obfuscation, decoys, wipe standard
            ├── class PrivacyConfig        # VPN, Tor, DNS, encryption
            ├── class ResearchConfig       # max_steps, max_time_minutes, mode
            ├── class QuantumPathfindingConfig
            ├── class DistillationConfig
            ├── class M1Presets            # memory-constrained presets
            └── def from_env()             # applies env var overrides
```

Modules that import config: `coordinators/`, `knowledge/`, `pipeline/`, `brain/` submodules, `intelligence/`, and `utils/` utilities.

#### Research Mode Presets (from `UniversalConfig.for_mode()`)

| Mode | Memory Limit MB | Max Concurrent Agents | Max Context Length | Notes |
|------|----------------|----------------------|-------------------|-------|
| `STANDARD` | 8000 | 5 | 4096 | Default preset |
| `DEEP` | 8000 | 3 | 8192 | Deeper analysis, fewer agents |
| `EXTREME` | 8000 | 2 | 16384 | Maximum context, single agent |
| `AUTONOMOUS` | 8000 | 1 | 16384 | Fully autonomous mode |

M1 8GB-specific caps applied post-construction via `M1Presets` limits: `memory_limit_mb = 5500`, `max_concurrent_agents = min(self.agent_manager.max_concurrent_agents, 3)`, `quantum_max_nodes = min(5000)`, `distillation_hidden_dim = min(128)`.

#### Bounds / Limits Defined in Config

From `config.py` / `config-schema.json`:


| Field | Section | Default | Bound/Limit |
|-------|---------|---------|---------------|
| `memory_limit_mb` | MemoryConfig | 8000 | soft cap via M1Presets to 5500 |
| `max_ram_percent` | MemoryConfig | 80 | u8 (0–100) |
| `max_concurrent_agents` | AgentManagerConfig | 5 | M1 cap: min(original, 3) |
| `agent_timeout_seconds` | AgentManagerConfig | 300 | — |
| `circuit_breaker_threshold` | AgentManagerConfig | 10 | — |
| `max_context_length` | CoordinationConfig | 4096 | M1 cap: 1024 |
| `temperature` | CoordinationConfig | 0.0 | — |
| `max_steps` | ResearchConfig | 100 | must be >= 1 |
| `max_time_minutes` | ResearchConfig | 30 | must be >= 1 |
| `quantum_max_steps` | QuantumPathfindingConfig | 50 | — |
| `quantum_max_nodes` | QuantumPathfindingConfig | 10000 | M1 cap: min(5000) |
| `quantum_amplification_strength` | QuantumPathfindingConfig | 1.5 | — |
| `distillation_hidden_dim` | DistillationConfig | 256 | M1 cap: min(128) |
| `distillation_learning_rate` | DistillationConfig | 0.001 | — |
| `thermal_threshold_c` | ModelConfig | 85 | — |
| `federated_batch_size` | FederatedConfig | 16 | M1 8GB limit |
| `stego_chi_square_threshold` | SteganographyConfig | 0.05 | — |
| `intelligence_cache_ttl` | IntelligenceConfig | 3600 | seconds |
| `quick_scan_time_limit` | IntelligenceConfig | 5 | seconds |
| `chaff_ratio` | SecurityConfig | 0.3 | — |
| `decoy_count` | SecurityConfig | 20 | — |

#### Security-Sensitive Values

| Field | Section | Default | Sensitivity |
|-------|---------|---------|-------------|
| `obfuscation_level` | SecurityConfig | `medium` | Controls decoy/hiding strength — "none" to "max" |
| `generate_decoys` | SecurityConfig | `True` | Creates decoy artifacts |
| `wipe_standard` | SecurityConfig | `nist_800_88` | Disk wiping algorithm (also: `dod_5220_22m`, `gutmann`) |
| `enable_query_masking` | SecurityConfig | `True` | Obfuscates research queries |
| `enable_chaff_traffic` | SecurityConfig | `True` | Generates decoy network traffic |
| `enable_encryption` | PrivacyConfig | `True` | toggles file-level encryption |
| `tor_proxy` | PrivacyConfig | `socks5://127.0.0.1:9050` | **Loaded from `TOR_PROXY_URL` env var** — default is localhost, should be overridden in production |
| `vpn_config_path` | PrivacyConfig | `None` | File path to VPN configuration |
| `use_doh` | PrivacyConfig | `False` | DNS-over-HTTPS resolution |
| `enable_vpn` / `enable_tor` | PrivacyConfig | `False` | Privacy layer toggles |

#### Environment Variable Overrides

`UniversalConfig.from_env()` applies the following env var overrides at load time:


| Env Var | Config Field | Accepted Values |
|---------|-------------|-----------------|
| `HLEDAC_LOG_LEVEL` | `log_level` | `DEBUG`, `INFO`, `W`, `err` |
| `TOR_PROXY_URL` | `tor_proxy` (PrivacyConfig) | Any valid SOCKS proxy URL |
| All other fields | Passed via `config.update(**kwargs)` | Validated against schema |


#### Config Schemas / Validation

- `config-schema.json` is a JSON Schema (draft-07) describing all config sections, field types, defaults, and descriptions. It is not auto-loaded by the runtime — it exists for external validation tooling and documentation.
- `UniversalConfig.__post_init__()` performs runtime validation: checks `max_steps >= 1`, `max_time_minutes >= 1`, memory warnings when `memory_limit_mb > 8000`, M1-specific warnings for `max_concurrent_agents > 10`.
- `from_env()` returns a list of validation error messages (empty if valid).
- `ResearchMode` enum: `STANDARD`, `QUICK`, `DEEP`, `EXTREME`, `AUTONOMOUS` — selected at config creation time, not overridable via env vars.


#### Findings Summary

- The `config/` directory is **empty** — no config files are stored there. All config is in the project root as Python source (`config.py`) and JSON Schema (`config-schema.json`).
- `config.py` is the **single source of truth** for runtime configuration. It is not a data file (JSON/YAML) but Python classes — meaning config is statically typed and validated at import time.
- No API keys or secrets are hardcoded in config — sensitive values are toggles and paths (VPN/Tor config), with the Tor proxy defaulting to a localhost placeholder.
- The M1 8GB preset system (`M1Presets`) provides automatic memory-constrained bounds, capping `max_concurrent_agents`, `quantum_max_nodes`, `distillation_hidden_dim`, and `max_context_length` when `m1_optimized=True`.
- `config-schema.json` is optional for runtime but valuable for external config tooling and as living documentation of all configurable fields.

---

### 14. logs/ Directory

#### Purpose
The `logs/` directory is intended as a central logging location for runtime artifacts. However, at time of audit (2026-05-23), the directory is **empty** — no log files are stored there. All actual log output is written to two other locations: the project root (for live run logs) and `reports/` (for sprint/run reports).

#### Actual Log File Locations

| Location | Pattern | Description |
|---|---|---|
| Project root | `live_run_YYYY-MM-DDTHH-MM-SS_*.log` | Per-run console output captures (stderr/stdout), created by `benchmarks/run_sprint82j_benchmark.py` and similar benchmark scripts |
| `reports/` | `f<hash>_*.log`, `live_sprint*.log`, `nonfeed_diagnostic*.log` | Sprint run report logs from coordinator/runner executions |

#### File Table

| Log File | Logger Name | Format | Rotation? |
|---|---|---|---|
| `live_run_2026-05-12T14-58-53_150s.log` (590 B) | `run_sprint82j_benchmark.py` (via `logging.basicConfig`) | Plain text, line-based with prefix `EXIT:`, `STDERR:`, `STDOUT:` | None — single-run artifact |
| `live_run_2026-05-12T15-01-35_150s.log` (3.8 KB) | Same as above | Same | None |
| `live_run_2026-05-12T15-05-35_150s.log` (3.6 KB) | Same as above | Same | None |
| `live_run_2026-05-12T15-08-41_150s.log` (8.5 KB) | Same as above | Same | None |
| `reports/f222g_lockbit_domain_nonfeed_180.log` (40 KB) | `__main__.py` / `sprint_scheduler.py` (via `logging.basicConfig`) | Plain text, standard Python logging format: `LEVEL:module:message` | None — kept in `reports/` |
| `reports/f223f_domain_lockbit3_nonfeed_180.log` (21 KB) | Same as above | Same | None |
| `reports/f226b_domain_lockbit3_nonfeed_check.log` (12 KB) | Same as above | Same | None |
| `reports/nonfeed_diagnostic_lockbit_180.log` (9.7 KB) | Same as above | Same | None |
| `reports/live_sprint_300s.log` (19 KB) | Same as above | Same | None |
| `reports/live_sprint_300s_20260515.log` (7.9 KB) | Same as above | Same | None |
| `reports/f233a_domain_live.log` (1.9 KB) | Same as above | Same | None |

#### Logger Identification

- **No dedicated application log handler** writes to `logs/` — the directory exists but is unused.
- `security/automation/threat-intelligence-automation.py:398` references `logs/access.log` (relative path) for access log analysis, but this file is also not present in the directory.
- `runtime/sprint_scheduler.py:9725` writes metrics to `run_dir/logs/metrics.jsonl` (a run-specific subdirectory), not to the central `logs/` folder.
- All primary logging uses `logging.basicConfig(...)` without file output — logs go to stderr/stdout, captured by benchmark runners and redirected to `.log` files.

#### Format Assessment

- **Plain text** — no structured logging (no JSON, no structlog).
- Standard Python logging format: `LEVEL:fully.qualified.module:message` (e.g., `INFO:hledac.universal.runtime.sprint_scheduler:Dedup LMDB loaded: 0 existing hashes`).
- Some logs contain URLs (e.g., `https://www.bing.com/search?q=site%3Alockbit3.tw 200`) — these are query URLs with possible IP addresses in referer headers depending on fetcher implementation.
- No evidence of structured (JSON) logging in any log file.

#### Sensitive Data Exposure

| Risk | Evidence |
|---|---|
| **File paths** | Logs contain absolute user paths (e.g., `/Users/vojtechhamada/PycharmProjects/...`) |
| **URLs with query parameters** | Bing/DuckDuckGo search URLs with domain targets (e.g., `lockbit3.tw`) — not sensitive but revealing OSINT targets |
| **OPSEC warnings** | Logs contain `[OPSEC]` and `[GHOST OPSEC]` warnings about ramdisk status, remote debug interfaces — these are intentional operational flags |
| **No credentials observed** | No API tokens, passwords, or API keys appear in sampled logs |
| **Path disclosure** | Local filesystem paths exposed in stack traces |

#### Retention / Policy Notes

- **No log rotation** — files grow indefinitely.
- **No retention policy** — logs persist until manually deleted.
- **No central cleanup** — `reports/` accumulated ~15 log files from May 12-18, 2026 with sizes up to 40 KB.
- The `logs/` directory itself is empty and serves no current function — it may be a planned location that was never connected.

#### Recommendations

1. Connect `logs/` to actual logging via a proper `logging.FileHandler` or `RotatingFileHandler` if centralized logging is desired.
2. Add log rotation (e.g., `TimedRotatingFileHandler` with daily rotation and 7-day retention) to prevent unbounded growth.
3. Consider structured logging (JSON) for programmatic log analysis.
4. Filter or redact absolute filesystem paths from logs for OPSEC.
5. Add `logs/` cleanup or archival to CI/CD if these logs have retention requirements.

---

### 12. data/ Directory

**Purpose**: The `data/` directory at `hledac/universal/data/` is a **placeholder/reserved directory** intended for sprint-generated runtime data. It is currently **empty** (May 20 07:58 creation, 0 files).

**Current Status**: No files present. The directory is not actively used for persistence in the current sprint architecture.


**Runtime Data Persistence** (actual location: `runtime/cti/db/`):

The sprint system stores runtime data under `runtime/cti/db/` (not `data/`). All runtime artifacts are gitignored and not committed.

| File | Format | Size | Purpose | Runtime I/O |
|------|--------|------|---------|-------------|
| `shadow_wal.lmdb` | LMDB | — | Shadow WAL for forensics replay | Output |
| `analytics.duckdb` | DuckDB | — | Analytics for CTI findings | Output |
| `lmdb/semantic_dedup.lmdb` | LMDB | — | Semantic deduplication map | Output |
| `lmdb/sprint_dedup.lmdb` | LMDB | — | Cross-sprint deduplication (Sprint 8RA) | Output |
| `lmdb/forensics_enrichment.lmdb` | LMDB | — | Forensics enrichment data (Sprint 8RA) | Output |
| `lmdb/multimodal_enrichment.lmdb` | LMDB | — | Multimodal enrichment data (Sprint 8RA) | Output |
| `lmdb/bandit.lmdb` | LMDB | — | Bandit model state | Output |
| `lmdb/dedup.lmdb` | LMDB | — | Within-sprint deduplication | Output |

**Schema**: LMDB key-value maps (no enforced schema). DuckDB uses SQL tables for analytics.

**Persistence Mechanism**: LMDB (memory-mapped DB) for high-throughput dedup; DuckDB for analytical SQL queries.


**Bounds/Limits**: No explicit bounds encoded in data files. LMDB map sizes governed by `lmdb_map_size()` in `paths.py`.


**Note**: `data/` is reserved but unused. Actual sprint output goes to `runtime/cti/db/` which is gitignored.
---

### 13. docs/ Directory

**Directory purpose**: Project documentation covering architecture decisions, operational runbooks, audit reports, capability matrices, and sprint planning. Acts as the primary knowledge base for both human developers and agentic workflows.

---

#### 13.1 File Inventory

**Root-level docs** (project scope, not audit-specific):

| File | Type | Covers | Last Modified | Maintained? |
|------|------|--------|-------------|-------------|
| `ARCHITECTURE.md` | Architecture | System architecture: entry points (`run_sprint()`, `SprintScheduler`), lane pipeline (CT/WAYBACK/PASSIVE_DNS/DOH/PIVOT_EXECUTOR), DuckDB ingest, lifecycle phases | 2026-05-12 | Stale — not updated after sprint refactor |
| `CODEX_AUDIT_REPORT.md` | Audit | Codex AI tooling audit, agent skill inventory, workflow patterns | 2026-05-12 | Stale |
| `DEPENDENCY_HYGIENE.md` | Guide | Dependency management rules, profile setup, M1 MacBook recommendations | 2026-05-18 | Active |
| `DEPENDENCY_PROFILES.md` | Reference | UV dep profiles (default, m1-local, no-torch-default, no-browser-default) with smoke check scripts | 2026-05-18 | Active |
| `LIVE_SPRINT_EXPERIMENT_MATRIX.md` | Runbook | Structured live sprint execution protocol: preflight, profile checks, memory thresholds, consistency invariants | 2026-05-20 | Active |
| `LOCAL_M1_SMOKE_RUNBOOK.md` | Runbook | M1-specific smoke test commands for minimal sprint runs (no browser/model/OCR) | 2026-05-20 | Active |
| `LOCAL_OSINT_CAPABILITY_MATRIX.md` | Reference | OSINT capability coverage by provider (CIRCL PDNS, Shodan, Hunter, Snova, Quadzy, Fullhunt, HunterHow, IPInfo) | 2026-05-18 | Active |
| `TESTING.md` | Guide | Testing strategy and conventions | 2026-05-18 | Active |
| `type_audit.md` | Audit | Type annotation audit of `runtime/sprint_scheduler.py`: 77 annotations, category A (manager instances) vs category B (other) | 2026-05-12 | Stale |

**Subdirectory: `agents/`** — Agent workflow documentation:

| File | Type | Covers | Last Modified | Maintained? |
|------|------|--------|-------------|-------------|
| `agents/ARCHITECTURE_CONNECTIVITY_PLAN.md` | Plan | 5-problem analysis: SprintScheduler.run() unit test gap (P1 CRITICAL), DuckDBShadowStore test coupling (P5 MEDIUM); P2/P3/P4 dismissed as false positives | 2026-05-21 | Active |
| `agents/domain.md` | Guide | Agent domain conventions: `.scratch/<feature-slug>/` layout, PRD files, issue numbering | 2026-05-14 | Stale |
| `agents/issue-tracker.md` | Guide | Local markdown issue tracker conventions | 2026-05-14 | Stale |
| `agents/triage-labels.md` | Reference | Triage role strings and label conventions | 2026-05-14 | Stale |

**Subdirectory: `audits/`** — 66 audit documents covering capability audits, coordinator reviews, dependency hygiene, capability truth reconciliation, format pipeline audits, etc. Actively maintained (recent entries from May 2026).

**Subdirectory: `runtime/`** — Sprint runtime planning:

| File | Type | Covers | Last Modified | Maintained? |
|------|------|--------|-------------|-------------|
| `runtime/GRAPH_ACCUMULATION_SEAM_AUDIT.md` | Audit | Graph accumulation seam analysis between components | 2026-05-17 | Stale |
| `runtime/SPRINT_GRAPH_ACCUMULATOR_PHASE_B_PLAN.md` | Plan | Sprint Graph Accumulator Phase B execution plan | 2026-05-17 | Stale |

**Subdirectory: `sprints/`**:

| File | Type | Covers | Last Modified | Maintained? |
|------|------|--------|-------------|-------------|
| `sprints/F350M_SPRINT_SCHEDULER_REFACTORING.md` | Plan | SprintScheduler refactoring plan (20KB, most substantial sprint doc) | 2026-05-14 | Stale |

---

#### 13.2 ADR Coverage

**No `docs/adr/` directory exists.** Architecture Decision Records are not maintained as a formal ADR system. Decisions are captured inline in audit documents and planning docs (e.g., `ARCHITECTURE_CONNECTIVITY_PLAN.md` records the P1/P5 decision to add unit tests, but not in ADR format). This is a documentation gap.

---

#### 13.3 Architecture Documentation Assessment

**Single source of truth**: `ARCHITECTURE.md` at project root is the intended architecture source of truth, covering:
- Entry points table (`run_sprint()`, `main()`, `run_ct_pivot()`, `run_semantic_pivot()`, `SprintScheduler`)
- SprintScheduler lifecycle (run loop, feed/public/CT branch routing)
- Lane pipeline (Acquisition Lanes: CT, WAYBACK, PASSIVE_DNS, DOH, PIVOT_EXECUTOR)
- DuckDB ingest path (`async_ingest_findings_batch()`)

**Scattered documentation problem**: Architecture knowledge is fragmented across:
- `ARCHITECTURE.md` — high-level system structure
- `agents/ARCHITECTURE_CONNECTIVITY_PLAN.md` — agent-specific architectural issues (SprintScheduler.run() gap)
- `runtime/GRAPH_ACCUMULATION_SEAM_AUDIT.md` — graph seam concerns
- `sprints/F350M_SPRINT_SCHEDULER_REFACTORING.md` — scheduler refactoring decisions
- 66 audits in `audits/` — capability and coordinator deep-dives

No single index or map document points to these interrelated docs. An architecture overview doc that cross-references these would improve discoverability.

**Key maintained docs** (recent activity):
- `LIVE_SPRINT_EXPERIMENT_MATRIX.md` (May 20) — active sprint execution guide
- `LOCAL_M1_SMOKE_RUNBOOK.md` (May 20) — M1 operational runbook
- `agents/ARCHITECTURE_CONNECTIVITY_PLAN.md` (May 21) — most recent architectural planning doc
- `audits/` directory — actively maintained with 66 documents, latest activity May 2026

**Stale docs** (no updates since mid-May, prior to recent sprint work):
- `ARCHITECTURE.md` (May 12) — predates SprintScheduler refactoring, likely outdated
- `CODEX_AUDIT_REPORT.md` (May 12) — AI tooling audit may not reflect current setup
- `agents/domain.md`, `issue-tracker.md`, `triage-labels.md` (May 14) — agent workflow docs not updated
- `sprints/F350M_SPRINT_SCHEDULER_REFACTORING.md` (May 14) — may be superseded by recent sprint changes

---

*Section generated: 2026-05-23*
*Audit Scope: docs/ directory*

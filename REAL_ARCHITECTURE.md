# Hledač — Real Architecture (aktualizováno 2026-04-24, F201B)

## F200C Async Batch Public Pipeline (2026-04-23)

**Zdokumentováno** — bounded async batch processing v live_public_pipeline je plně compliant s GHOST_INVARIANTS:

**Async batch execution v `async_run_live_public_pipeline()`** (řádky 2173-2218):
```python
# Vytvoření tasků pro každý discovery hit
tasks: list[asyncio.Task] = []
for hit in hits:
    task = asyncio.create_task(_fetch_and_process_page(...))
    tasks.append(task)

# gather s return_exceptions=True (GHOST_INVARIANTS: asyncio.gather vždy return_exceptions=True)
raw_results = await asyncio.gather(*tasks, return_exceptions=True)

# _check_gathered volán po každém gather (GHOST_INVARIANTS: _check_gathered po každém gather)
from hledac.universal.network.session_runtime import _check_gathered
ok_results, error_results = _check_gathered(raw_results)
```

**Async hygiene invariants (F200C-1 až F200C-5)**:
- `asyncio.gather` vždy používá `return_exceptions=True` — jeden selhání nezruší siblings
- `_check_gathered()` volána po každém `gather` — filtruje exceptiony a loguje je
- `asyncio.CancelledError` propaguje (re-raised, ne swallowed)
- `BaseException` (ne `Exception`) propaguje — `SystemExit`, `KeyboardInterrupt` neztraceny
- Regular `Exception` jdou do `error_results`, ok results do `ok_results`

**Concurrency invariants (F200C-6 až F200C-7)**:
- Pages processed concurrently via `asyncio.create_task` + `gather`, ne sequentially
- `asyncio.Semaphore` limituje concurrency na `fetch_concurrency` parametr
- Memory guard: při UMA_STATE_CRITICAL/EMERGENCY → `effective_concurrency = 1`

**Model/renderer mutual exclusion (F200C-8)**:
- `is_embedding_context_active()` vrací True když embedding model je loaded
- `_fetch_with_camoufox` vrací early prázdný string když embedding context active
- Zabraňuje M1 RAM conflict: model + JS renderer současně

**Fail-soft invariants (F200C-9)**:
- Pipeline pokračuje i když individual page fetch selže
- Výjimky jdou do `error_results`, pipeline vrací `PipelineRunResult` s partial results
- Storage errors: fail-soft, `accepted_count`/`stored_count` zůstávají 0

**Bounded invariants**:
- Semaphore max = `fetch_concurrency` (default 5)
- `_check_gathered` partitionuje results bez memory leak
- žádný nový public API — pouze existující `async_run_live_public_pipeline()`

**Canonical write path maintained**: DuckDB writes still flow through `async_ingest_findings_batch()`.

**Tests**: 16 probe tests v `tests/probe_f200c/test_async_batch_public_pipeline.py`:
- `TestF200CGatherHygiene`: invariant_1-5 (gather pattern, _check_gathered)
- `TestF200CConcurrency`: invariant_6 (overlapping concurrent execution)
- `TestF200CSemaphoreLimiting`: invariant_7 (max 2 concurrent when semaphore=2)
- `TestF200CMemoryGuard`: invariant_8 (UMA CRITICAL → concurrency=1)
- `TestF200CModelRendererExclusion`: invariant_9 (embedding context blocks Camoufox)
- `TestF200CFailSoft`: invariant_10 (exceptions collected, pipeline continues)
- `TestF200CFullGatherPattern`: AST verification of gather pattern

## F200B LanceDB ANN Fast Path (2026-04-23)

**Přidáno** — optional fast-path ANN index for semantic dedup cross-run persistence:
- `knowledge/ann_index.py`: `_ANNIndex` class — LanceDB-backed ANN with cosine similarity. Singleton via `get_ann_index()`. Fail-soft: `_boot_error` set on init failure, all methods return empty/false when unavailable.
- `knowledge/ann_index.py`: `check_ann_duplicate(embedding, text_hash, finding_key)` — public facade. Returns True if ANN search score >= 0.90 and text_hash matches. Upserts on miss.
- `knowledge/ann_index.py`: `reset_ann_index()` — closes and nullifies singleton (sprint teardown).
- `semantic_deduplicator.py`: step 5 now calls `check_ann_duplicate()` before LMDB write — ANN fast path for cross-run duplicate detection.

**ANN fast-path integration in `check_and_cache()`**:
```python
# After LRU check (step 4) → ANN search (step 5) → LMDB store (step 6)
try:
    from hledac.universal.knowledge.ann_index import check_ann_duplicate
    if check_ann_duplicate(emb, text_hash, key):
        self._duplicate_count += 1
        return True
except Exception:
    pass  # Fail-open: ANN errors don't block findings
```

**Data contracts**:
- Dimension: 256d float32 (matches `embedding_pipeline._EMBEDDING_DIM`)
- Metric: cosine similarity (LanceDB `.metric("cosine")`)
- Threshold: 0.90 (same as semantic dedup default)
- Bounded: MAX_ENTRIES=50,000 with 10% LRU eviction on overflow
- Memory guard: init skipped above 6GB RSS

**Fail-open invariants**:
- `_ANNIndex.init()` failure → `_boot_error` set, `ann_search()` returns []
- `check_ann_duplicate()` returns False when ANN unavailable
- Exception in ANN path → caught and returns False (finding accepted)

**Canonical write path maintained**: `duckdb_store.py` still owns all persist writes. ANN is advisory overlay, not a replacement.

**Smoke failures (pre-existing, not introduced by F200B)**:
- `AdaptiveSemaphore.__init__()` signature mismatch (`initial_value` kwarg)
- `FETCH_SEMAPHORE` is `_FetchSemaphoreProxy` not `AdaptiveSemaphore`
- `'Semaphore' object has no attribute 'current_limit'`

## F200A Bounded Prefetch Oracle (2026-04-23)

**Přidáno** — lightweight bounded oracle advisory for scheduler work-item ordering:
- `prefetch/prefetch_oracle_integration.py`: `PrefetchOracleIntegration` class — advisory only, scheduler retains authority. Score composition: historical yield (hot/warm/lukewarm/marginal/cold) + recency bonus + novelty bonus. All methods fail-soft. Bounded: MAX_SOURCE_HISTORY=200 (LRU eviction), MAX_URL_SEEN=50k, MAX_CANDIDATES=100.
- `runtime/sprint_scheduler.py`: `_prefetch_oracle` field (None by default), `inject_prefetch_oracle(oracle)` method, `_sort_work_items_by_economics` incorporates oracle advisory scores, `_process_result` calls `oracle.record_outcome()`, `_reset_result` calls `oracle.reset()`.

**Oracle advisory invariants**:
- Advisory only: oracle SUGGESTS, scheduler DECIDES — oracle score multiplies economics sort key
- Fail-soft: all oracle calls wrapped in `try/except` — oracle failure → scheduler uses default ordering
- Bounded: MAX_SOURCE_HISTORY=200 sources tracked (LRU eviction), MAX_URL_SEEN=50k (LRU eviction), MAX_CANDIDATES=100 per sort
- Score range clamped to [0.1, 3.0]
- Score cache invalidated when `current_cycle` changes
- No network I/O, no MLX/Metal — pure Python, M1-safe

**Integration seam**:
```python
oracle = PrefetchOracleIntegration()
scheduler.inject_prefetch_oracle(oracle)
# During _sort_work_items_by_economics:
#   oracle_scores = oracle.suggest_scores(work_items, current_cycle)
#   oracle_score shifts effective priority within tier/posture band
# After _process_result:
#   oracle.record_outcome(feed_url, fetched, accepted, cycle, seen_new_urls)
# At sprint teardown:
#   oracle.reset()  # clears all state for next sprint
```

**Invariants maintained**:

**Přidáno** — rich terminal dashboard sidecar for live sprint metrics:
- `monitoring/sprint_dashboard.py`: `SprintDashboard` class — rich-based live terminal dashboard. Reads `SprintSchedulerResult` fields directly (data contract: dashboard is NOT a second source of truth, it mirrors scheduler result). Exposes `start()`, `update(result, phase, elapsed_s)`, `finish(result, elapsed_s)`. All render failures are fail-soft (`try/except` throughout, `Live = None` when rich unavailable).
- `runtime/sprint_scheduler.py`: `progress_callback` parameter in `run()` — called after each cycle with `(result, phase_str, elapsed_s)`. Callback is wrapped in `try/except` — exceptions never affect sprint completion.
- `core/__main__.py`: `run_sprint(ui_mode=False)` — when `ui_mode=True`, creates `SprintDashboard` and wires `_on_cycle` progress callback. Dashboard creation is fail-safe — sprint proceeds if dashboard fails.

**Dashboard non-blocking invariants**:
1. `progress_callback` exception is caught by scheduler (`except Exception: pass`)
2. `dashboard.update()` exception is caught by `_on_cycle` wrapper in `__main__`
3. Dashboard `Live` is `None` when rich is not installed — all methods no-op
4. `ui_mode=False` skips all dashboard code path entirely

**Dashboard non-blocking invariants**:
- Dashboard does NOT own lifecycle authority
- Dashboard does NOT create duplicate state — reads from `SprintSchedulerResult`
- Sprint completes regardless of dashboard render failure

## F199A Reward-Driven Source Weight Adaptation (2026-04-23)

**Přidáno** — granular reward signal from `FindingQualityDecision` → source weights:
- `runtime/sprint_scheduler.py`: `_source_quality_feedback` dict (`feed_url → {fetched, accepted}`) — accumulated per `_process_result()` call, bounded at 200 feed_urls, cleared per sprint via `_reset_result()`
- `runtime/sprint_scheduler.py`: `_adapt_source_weights_from_feedback()` — called at sprint teardown; adapts `_source_weights[source_type]` by accepted/total ratio:
  - ratio ≥ 0.7 → +10% (delta=1.10)
  - 0.4 ≤ ratio < 0.7 → +5% (delta=1.05)
  - 0.15 ≤ ratio < 0.4 → neutral (delta=1.00)
  - ratio < 0.15 → -5% (delta=0.95)
  - B.6 bounds enforced: clamped to [0.3, 2.5]
- `runtime/sprint_scheduler.py`: `run()` teardown calls `_adapt_source_weights_from_feedback()` fail-soft (try/except), independent of `_policy_manager`
- `rl/sprint_policy_manager.py`: `update_with_quality_decisions(decisions, feed_url)` stub — no-op, source weights live in scheduler; exists for future per-source reward injection

**Invariants maintained**:
- B.6 bounds: ±20% per sprint → clamp [0.3, 2.5]
- No new fake RL loop — existing `SprintPolicyManager.update()` handles global RL reward
- Scheduler works even when RL helper fails (fail-soft throughout)
- `_source_quality_feedback` reset per sprint via `_reset_result()`

**RL evolution over marl_coordinator.py**:
- `marl_coordinator.py` was ghost code (zero production call-sites, deleted F196A)
- F199A uses the existing `SprintPolicyManager` (opt-in, enabled=False by default)
- Source weight adaptation is scheduler-internal, bounded, explainable
- No resurrection of dead RL coordinator

## F198A Cross-Sprint Graph Accumulation (2026-04-23)

**Přidáno** — cross-sprint graph accumulation:
- `runtime/sprint_scheduler.py`: `_accumulate_findings_to_graph()` — upserts accepted findings (CT log path) to `graph_service` via `upsert_ioc(finding_id, source_type, confidence, sprint_id)`. Fail-soft throughout.
- `runtime/sprint_scheduler.py`: `_get_graph_signal()` — reads `graph_stats()` at teardown and includes in diagnostic report export. Non-blocking.
- `knowledge/graph_service.py`: already had `upsert_ioc`, `upsert_relation`, `find_entity_history`, `graph_stats`, `reset_session` — F198A wires it into canonical sprint path.

**Role distinction maintained**:
- `graph/quantum_pathfinder.py` → **read-side ML overlay only**; `DuckPGQGraph` is the analytics donor backend; NOT a truth store
- `knowledge/graph_service.py` → **sprint memory seam** for cross-sprint entity accumulation; backed by `DuckPGQGraph`; fail-safe throughout

## F196A Ghost Verdict (2026-04-23)

**Deleted** — zero production call-sites, no canonical wiring:
- `runtime/intelligence_dispatcher.py` — ghost; `attach_dispatcher()` never called from sprint path
- `runtime/memory_watchdog.py` — ghost; `attach_to_dispatcher()` never called from sprint path
- `runtime/session_authority.py` — ghost; zero call-sites confirmed
- `rl/marl_coordinator.py` — stub experiment; zero production call-sites; deleted

**Kept** — active with real imports:
- `runtime/telemetry.py` — ACTIVE; real imports from `__main__.py`, `core/__main__.py`, `metrics_registry.py`

Canonical sprint path is now clean of ghost authority surfaces.

Tento dokument je založený na reálném stavu kódu v repu, ne na `ARCHITECTURE_MAP.py`.

## Canonical Sprint Pipeline

### Hlavní zjištění
- Canonical sprint owner je `run_sprint()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:320`.
- Root CLI v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py:3028` je jen shell/dispatcher a pro sprint deleguje do `core.__main__.run_sprint()`.
- Root CLI dnes **nemá** `--mode aggressive` ani `SprintMode` enum. Canonical CLI přepínač je `--aggressive` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:1193`.
- `SprintMode` enum v aktuálním `runtime/sprint_scheduler.py` **neexistuje**.
- Jediný canonical write path pro findings je `DuckDBShadowStore.async_ingest_findings_batch()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py:4102`.
- Sprint F197B: `DuckDBShadowStore._assess_finding_quality()` quality gate hook ordering:
  1. Hot cache dedup (in-memory fingerprint)
  2. Persistent LMDB dedup (cross-run fingerprint)
  3. URL-first short-circuit (URL = identity, no entropy check)
  4. Short-string skip (< 8 chars) + semantic dedup check → store
  5. Entropy threshold check (≥ 0.5 bits/char)
  6. **Sprint F197B: Semantic dedup BEFORE LMDB write** → reject on duplicate OR accept on fail-open
  7. Entropy pass → **store to LMDB + hot cache AFTER semantic dedup pass**
- Semantic dedup is fail-open: embedder/LMDB/memory failure → finding accepted (never rejected).

### Reálný datový tok

```text
python -m hledac.universal --sprint <query> [duration]
        |
        v
root __main__.py main()
        |
        | delegates
        v
core/__main__.py run_sprint(query, duration_s, export_dir, aggressive_mode)
        |
        |-- init DuckDBShadowStore
        |-- build SprintSchedulerConfig(aggressive_mode=bool, branch_timeout_budget_s=8.0 if aggressive)
        |-- build SprintLifecycleManager
        |-- build SprintScheduler
        |-- build CTLogClient
        v
SprintScheduler.run(...)
        |
        |-- prewarm Hermes via ModelManager (mode-aware)
        |-- stable mode: _run_one_cycle_stable()
        |     |-- feed branch first
        |     `-- public branch after feed
        |
        `-- aggressive mode: _run_one_cycle_aggressive()
              |-- feed branch task
              |-- public branch task
              `-- CT branch task
        |
        |-- feed -> pipeline/live_feed_pipeline.py async_run_live_feed_pipeline()
        |-- public -> pipeline/live_public_pipeline.py async_run_live_public_pipeline()
        |-- CT -> _run_ct_log_discovery_in_cycle()
        |
        `-- every branch emits CanonicalFinding -> async_ingest_findings_batch()
                    |
                    v
           knowledge/duckdb_store.py quality gate + persistent ingest
                    |
                    v
core/__main__.py runtime truth + report_dict + ExportHandoff
                    |
                    |-- export/sprint_exporter.py export_partial_sprint() during aggressive mode
                    `-- export/sprint_exporter.py export_sprint() at final export
```

### Canonical pipeline modules

| Modul | Public API volaná zvenčí | Vstup | Výstup | Kde se napojuje |
|---|---|---|---|---|
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py` | `main()` | `sys.argv` | process exit / delegation | root shell only |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py` | `run_sprint()`, `main()` | query, duration, export_dir, aggressive bool | report/export side effects | canonical sprint owner |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py` | `SprintSchedulerConfig`, `SprintSchedulerResult`, `SprintScheduler.run()`, `compute_sprint_intelligence()` | lifecycle, sources, query, store, CT client | scheduler result + intelligence dict | runtime executor |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_feed_pipeline.py` | `async_run_live_feed_pipeline()` | `feed_url`, optional store, bounds | `FeedPipelineRunResult` | feed branch |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py` | `async_run_live_public_pipeline()` | query, store, fetch budgets, Hermes, memory manager, optional feedback hook | `PipelineRunResult` | **public branch + F197C: per-finding embeddings via embedding_pipeline after DuckDB quality gate (fail-soft)** |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py` | `DuckDBShadowStore`, `async_ingest_findings_batch()`, `_assess_finding_quality()` | `list[CanonicalFinding]` | `list[FindingQualityDecision | ActivationResult]` | **canonical write seam: hash dedup → semantic dedup (F197B) → LMDB store** |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/export/sprint_exporter.py` | `export_partial_sprint()`, `export_sprint()` | store + `ExportHandoff`/compat handoff | JSON artifact paths | final export plane |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tot_integration.py` | `TotIntegrationLayer`, `solve_with_tot()` | prompt, timeout | solution string / empty string | P12 post-storage hypothesis evaluation |

### Important canonical details
- `async_run_live_public_pipeline()` still emits `_SOURCE_TYPE = "live_public_pipeline"` in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:42`, not `live_public`.
- P12 hypothesis/ToT block is reachable and runs **before** return in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:2667`.
- P12 now does bounded concurrent ToT burst through `asyncio.as_completed(tasks)` in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:2718`.
- Aggressive branch fan-out exists in `_run_one_cycle_aggressive()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py:1309`.
- Partial export is already wired in aggressive mode through `_maybe_export_partial()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py:1872` and `export_partial_sprint()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/export/sprint_exporter.py:79`.

## Aktivní moduly (wired do pipeline)

| Modul | Účel | Vstup | Výstup | Testy |
|---|---|---|---|---|
| `core/__main__.py` | canonical sprint bootstrap, truth closure, export handoff | CLI/query/duration/aggressive flag | scheduler run + export artifact | silné pokrytí napříč `tests/test_e2e_*`, `tests/probe_*` |
| `runtime/sprint_scheduler.py` | lifecycle executor, aggressive/stable cycle runner, CT/public/feed orchestrace, Hermes lifecycle | lifecycle, source list, store, query | `SprintSchedulerResult`, intelligence dict | ~54 test files odkazuje přímo |
| `pipeline/live_feed_pipeline.py` | RSS/Atom discovery -> pattern hits -> CanonicalFinding | feed URL + optional store | `FeedPipelineRunResult` | ~16 přímých test souborů |
| `pipeline/live_public_pipeline.py` | public discovery, fetch policy, CommonCrawl injection, onion/academic/paste/GitHub secret side lanes, P12 | query + store + Hermes + fetch budgets | `PipelineRunResult` | ~16 přímých test souborů |
| `knowledge/duckdb_store.py` | quality gate + WAL/DuckDB canonical persistence + graph/seeds read seams | `CanonicalFinding` batch | quality/storage results | ~62 přímých test souborů |
| `export/sprint_exporter.py` | partial/final export, product value summary, research depth score | `ExportHandoff` | JSON report + seed artifact | 9 přímých test souborů |
| `intelligence/ct_log_client.py` | CRT.SH CT pivot | domain-like query | CT findings | `tests/test_ct_log_pipeline.py`, benchmark doubles |
| `discovery/rss_atom_adapter.py` | feed fetch/parse | feed URL | feed entries | multiple feed probes |
| `discovery/duckduckgo_adapter.py` | public discovery backend | query | discovery hits | public pipeline probes |
| `fetching/public_fetcher.py` | bounded HTTP/JS/Tor capable fetch | URL + policy | `FetchResult` | indirectly tested via public pipeline |
| `patterns/pattern_matcher.py` | pattern matching SSOT | text | pattern hits | dedicated pattern probes |
| `brain/model_manager.py` | Hermes model lifecycle and memory admission | model name | loaded model / release | hypothesis + memory tests |
| `memory/memory_manager.py` | session memory export/history | session id, events | persisted memory/session export | referenced by public pipeline and tests |
| `network/bgp_monitor.py`, `network/ipfs_client.py`, `intelligence/shodan_wrapper.py`, `discovery/ti_feed_adapter.py` | TI/pDNS/Shodan/BGP/IPFS canonical finding ingress | pivot/query/materialized inputs | CanonicalFinding batches | dedicated probe families exist |
| `tools/commoncrawl_adapter.py` | archival URL discovery augmentation | query + session | injected archive hits | `tests/probe_f193a/test_wayback_adapter.py` plus public tests |
| `runtime/telemetry.py` | sprint event logging, phase transitions, EMA metrics | sprint lifecycle events | event ring buffer, JSON log output | `__main__.py`, `core/__main__.py`, `metrics_registry.py` imports |

## Dormant moduly (existují, ale nejsou wired nebo jen okrajově)

### Audited directories

| Modul/adresář | Co reálně dělá | Napojení na canonical sprint path | Testy | Potenciál / priorita |
|---|---|---|---|---|
| `forensics/` | metadata extraction, steganography, digital ghost for file artifacts | **ano** — `forensics/enrichment_service.py` je wired do `duckdb_store._enrich_ct_findings_forensics()` (F195C); F198B rozšířeno o ForensicsResult na URL payload | `tests/probe_f196b/`, `tests/probe_f198b/` | **active — enrichment layer for CT findings** |
| `knowledge/` | mix canonical store + analytics + entity/context modules | **ano**, hlavně `duckdb_store`, `semantic_store`, **graph_service** (F198A cross-sprint accumulation seam) | ano | aktivní jádro + zbytek částečně dormant |
| `graph/` | `quantum_pathfinder.py` (ML overlay/read analytics), `duck_pgq_graph.py` (DuckDB SQL/PGQ donor backend) | **ano**, `graph_service` upsert v sprint_scheduler (F198A); quantum_pathfinder is read-only analytics | ano | read-side analytics overlay; DuckPGQGraph is donor backend, not truth store |
| `loops/` | RL research loop (`ResearchLoop`) | **ano**, public pipeline jej importuje v P16/P17 bloku | vlastní coverage minimální | střední, ale experimentální |
| `federated/` | federated learning, DP, peer/model store; část souborů archivní | nenašel jsem canonical import | 3 test hits | nízká priorita pro OSINT sprint |
| `memory/` | `MemoryManager`, shared memory | **ano**, scheduler/public pipeline | mnoho test hitů | aktivní sidecar |
| `layers/` | univerzální orchestration layers, content/communication/ghost wrappers | nenašel jsem canonical sprint import | bez přímých testů v canonical flow | spíš legacy/integration stack |
| `multimodal/` | fusion/vision encoder, DocumentExtractor, MultimodalEnricher | **ano** — `multimodal/analyzer.py` je wired do sprint_scheduler (F195C); F198C přidává DocumentExtractor pro PDF/image → CanonicalFinding | `tests/probe_f196b/`, `tests/probe_f198c/` | **active — vision + document enrichment layer** |
| `context_optimization/` | cache/compression/MMR/active learning | **ano**, public pipeline používá MMR | bez cílených canonical testů | střední potenciál |
| `coordinators/` | široká vrstva coordinatorů, archive/claims/security atd. | pouze `security_coordinator` je aktivně volán v exportu | bez přímé canonical suite mapy | velká část dormant/secondary |
| `execution/` | `GhostExecutor` | nenašel jsem canonical import | 1 test hit | dormant |
| `network/` | BGP/IPFS/CT scanner/favicon/JARM/JS extraction | **ano**, některé moduly jsou aktivní přes pipeline/adaptery | ano | částečně aktivní, částečně dormancy |
| `planning/` | HTN planner, cost/search, task cache | nenašel jsem canonical sprint import | bez přímých testů v sprintu | dormant, možný future planner |
| `policy/` | Nym/Tor transport policy | nenašel jsem přímé zapojení do canonical sprintu | pár test hitů | dormant |
| `prefetch/` | prefetch cache/oracle/budget/reranker | **částečně** — `prefetch_oracle_integration.py` je aktivní v sprint_scheduler via `inject_prefetch_oracle()` (F200A); `prefetch_oracle.py` (predikční model) zůstává dormant; `prefetch_cache.py` je podpůrná cache, není wired | bez testů | **částečně aktivní — oracle integration active, cache/predictor dormant** |
| `research/` | branch manager, scheduler, prioritizer | nenašel jsem canonical import | 10 test hitů | dormant/alternative scheduler stack |
| `stealth/` | `StealthManager`, `StealthSession`, host telemetry/token bucket | **ano** via `stealth/stealth_session.py` → `public_fetcher.py` (F195C canonical StealthSession wired); `StealthManager` is secondary owner | 3 test hits | **ACTIVE — canonical stealth surface in fetch path** |
| `security/` | audit, destruction, deep research security, obfuscation, encryption | canonical export používá `security_coordinator` z coordinators, ne tyto moduly přímo | několik testů | většinou dormant pro sprint path |
| `text/` | encoding/hash/unicode analyzers | nenašel jsem přímé canonical napojení | hodně test hitů | usable analyzers, dnes mimo sprint |
| `transport/` | Tor/I2P/Nym/circuit breaker/resolver | `core.__main__` importuje `TorTransport`; resolver/testy existují | ano | částečně aktivní |
| `tools/` | velký toolbox včetně CommonCrawl, content extraction, dark web helpers | CommonCrawl je aktivní, zbytek heterogenní | hodně testů | mix active + dormant |
| `rl/` | QMIX/replay buffer/state extraction/action constants | **ano** via `sprint_policy_manager.py` | starší testy (test_sprint58a) | sprint_policy_manager is real (reward contract); marl_coordinator deleted F196A |
| `pipeline/` | canonical feed/public pipelines | **ano** | ano | aktivní |
| `patterns/` | pattern matcher SSOT | **ano** | ano | aktivní |
| `orchestrator/` | secondary thin facade + phase/memory pressure/request routing | canonical sprint owner jej nepoužívá | několik testů | secondary/legacy orchestrator stack |
| `intelligence/` | capability forest: CT, academic, archive, image, blockchain, workflow helpers | část aktivní (`ct_log_client`, `academic_discovery`, paste/github scanners), část dormant | ano | mixed |
| `infrastructure/` | plugin manager, system monitor | nenašel jsem canonical import | bez testů | dormant |
| `discovery/` | adapters for public/feed/TI ingress | **ano** | ano | aktivní |
| `fetching/` | public fetch runtime | **ano** | nepřímo | aktivní |
| `deep_research/` | path discovery + deep utilities | **ano**, volán z `core/__main__.py` při `--deep-probe` flag | `tests/probe_f197a/` | aktivní post-sprint canonical deep research |
| `dht/` | Kademlia crawl/lookups + local graph | `kademlia_node.py` existuje, ale DHT write path je no-op | ano | částečně dormant, not canonical producer today |
| `brain/` | model/embedding/hypothesis/distillation/router stack | část aktivní (`model_manager`, `hypothesis_engine`, `ane_embedder`) | má test coverage | mixed |
| `graph/` | DuckPGQ/graph manager/pathfinder | read-side seam aktivní přes store; full graph orchestration není canonical owner | hodně testů | střední až vysoký potenciál |
| `hypothesis/` | beta-binomial, Dempster-Shafer, EIG, generator | canonical path používá spíš `brain.hypothesis_engine`, ne tento subtree přímo | test hits existují | dormant support math layer |
| `embedding_cache/` | data/cache directory, bez `.py` souborů | žádný canonical import | jen test references | podpůrný asset, ne modul |

### Audited root files

| Soubor | Co reálně obsahuje | Canonical wiring | Testy | Verdikt |
|---|---|---|---|---|
| `enhanced_research.py` | velký dormant monolith (`UnifiedResearchEngine`, `EnhancedResearchOrchestrator`, `DeepResearchRequest`) | nenašel jsem canonical import | 1 hit | legacy candidate / reference only |
| `deep_probe.py` | deep crawler, dorking, path prediction, IPFS/S3 scanners | **ano** via `deep_research/probe_runner.py` -> `async_ingest_findings_batch()` | `tests/probe_f197a/` | aktivní post-sprint deep research lane |
| `tool_registry.py` | typed tool schemas/cost model/registry | canonical sprint owner jej nepoužívá | 7 hitů | useful support layer, dnes ne-wired |
| `evidence_log.py` | canonical evidence ledger | není v canonical sprint path, spíš parallel/legacy ledger plane | 22 hitů | aktivní mimo sprint pipeline |
| `capabilities.py` | capability truth/router/model lifecycle facade | nepůsobí jako canonical sprint dependency | bez přímé sprint integrace | support/diagnostic layer |
| `embedding_pipeline.py` | lazy embedding load/generate/embed query/doc + memory guard | **F197C: wired to live_public_pipeline per-finding storage block** | `tests/probe_f197c/` | **active — per-finding embedding sidecar** |
| `captcha_solver.py` | Apple Vision/CoreML CAPTCHA solver | ne-wired | 3 hitů | niche capability |
| `behavior_simulator.py` | 54-line ghost feature placeholder | ne-wired | 0 | dead/dormant |
| `resource_allocator.py` | budget/resource allocator, adaptive concurrency helpers | není canonical owner dependency | 0 | could inform scheduler later |
| `research_context.py` | canonical context carrier types | typová vrstva, ne active runtime in canonical sprint | 0 | useful shared DTO layer |
| `autonomous_analyzer.py` | autonomous analyzer profiles/orchestrator | canonical sprint jej nevolá | 0 | legacy/alternative analysis lane |
| `metrics_registry.py` | lightweight metrics registry | ne-wired | 0 | dormant observability helper |
| `project_types.py` | consolidated type definitions | **ano**, broad shared DTO/types layer | implicit through many modules | active shared type base |

## Dead code / Legacy

### Jasné legacy nebo secondary surfaces
- Root `__main__.py` obsahuje velký alternativní/deprecated runtime (`_run_sprint_mode`, `_run_public_passive_once`, warmup scaffolding), ale canonical sprint owner je jen `core.__main__.run_sprint()`.
- `enhanced_research.py` je 3058-line dormant orchestrator-like monolith bez canonical wiring.
- `orchestrator/` subtree je secondary facade/orchestration stack, ne canonical sprint path.
- `federated/federated_coordinator_v2.py` a `federated/model_store_v2.py` se samy označují jako archived.
- `behavior_simulator.py` je v podstatě ghost placeholder.
- `knowledge/atomic_storage.py` a `knowledge/corpus_ingester.py` nesou stub/archival signály.

### Duplikované nebo zastaralé authority surfaces
- Root `__main__.py` vs `core/__main__.py`: dvě entrypoint plochy, ale jen jedna canonical sprint authority.
- `research/`, `planning/`, `orchestrator/`, `enhanced_research.py` představují paralelní architektonické směry mimo dnešní sprint pipeline.
- `live_public_pipeline.py` používá `source_type="live_public_pipeline"`, zatímco některé export/plan představy pracují s `live_public`.
- F196A: `intelligence_dispatcher.py`, `memory_watchdog.py`, `session_authority.py`, `marl_coordinator.py` smazány jako ghost moduly s nulovými canonical call-sites.

## Known test failures

### Collect / inventory
- `.venv/bin/pytest tests/ --co -q`:
  - `6244 tests collected`
  - `4 skipped`
  - warnings on unknown marks `slow`, `stress`, `timeout`

### Aktuální baseline běh
- `.venv/bin/pytest tests/ -q --maxfail=20 --tb=short`
  - `20 failed, 325 passed, 4 skipped` před stopem na `--maxfail=20`

### Kategorizace prvních reálných failure clusters

| Kategorie | Příklady | Reálný problém |
|---|---|---|
| stale expectations vůči `autonomous_orchestrator` | `tests/probe_2a/test_sprint_2a.py`, `tests/probe_4a/test_lifecycle_4a.py` | testy očekávají staré symboly nebo modul-level attributes (`ActionResult`, `logger`, `time`) |
| drift v UMA snapshot contractu | `tests/probe_1b/test_uma_budget.py`, `tests/probe_6b/test_uma_budget_thresholds.py` | snapshot shape postrádá `is_warn` a `is_emergency` |
| drift ve fetch coordinator API | `tests/probe_4b/test_fetch_4b.py` | test čeká sync `_resolve_host_ips` a `asyncio.to_thread` v jiné podobě |
| chybějící repo artifact | `tests/probe_6a/test_async_hygiene.py`, `tests/probe_7a/test_sprint_7a.py` | `GHOST_INVARIANTS.md` v rootu chybí |
| drift v `utils.mlx_cache` contractu | `tests/probe_6b/test_mlx_cache_limits.py`, `tests/probe_7b/test_mlx_init.py` | chybí `_MLX_CACHE_LIMIT` a related symbol export |

### Known skips / optional deps
- MLX-gated tests: `tests/test_sprint59.py`, `tests/test_sprint60.py`, `tests/test_embedding_prefix_discipline/test_embedding_task.py`
- optional deps/importorskip: `ahocorasick`, `aiohttp_socks`, `stix2`, `polars`, `gliner`, `psutil`
- live/network gates: `tests/test_e2e_pipeline_smoke.py`, `tests/probe_8rb/test_tor_live_clearnet.py`

## Coverage gaps worth calling out


- `embedding_pipeline.py` has direct tests via `tests/probe_f197c/` (F197C wiring).
- `enhanced_research.py`, `resource_allocator.py`, `metrics_registry.py`, `autonomous_analyzer.py` have little or no direct coverage relative to size/importance.

## F195C Probe Coverage (Sprint F196B)

Sprint F195C forensics and multimodal enrichment layers now have dedicated probe coverage:
- `forensics/enrichment_service.py`: 29 probe tests in `tests/probe_f196b/test_forensics_probe_lane.py`
  - Fail-soft: `enrich()` / `enrich_batch()` never raise
  - Lifecycle: `initialize()` / `close()` idempotent
  - Supported-file gating: extension allowlist enforced
  - Return contract: dict keys `metadata`, `steganography`, `ghosts`, `enrichment_available`
  - **Sprint F198B addition**: `"forensics"` key with typed `ForensicsResult` + WHOIS/SSL/DNS/rDNS on URL payloads
  - Counter contract: `SprintSchedulerResult.forensics_enriched_ct_findings`
- `multimodal/analyzer.py`: 28 probe tests in `tests/probe_f196b/test_multimodal_probe_lane.py`
  - Fail-soft: `enrich()` / `enrich_batch()` never raise
  - RAM guard: `_can_run_heavy_vision()` fails-open
  - Lifecycle: `initialize()` / `close()` idempotent
  - Supported-file gating: extension allowlist enforced
  - Return contract: dict keys `vision_embedding`, `fused_embedding`, `clip_score`, `enrichment_available`
  - Counter contract: `SprintSchedulerResult.multimodal_enriched_findings`

## F198B Forensics Metadata on Canonical Findings

Sprint F198B extends forensics enrichment to inject typed ForensicsResult into finding.metadata["forensics"]:
- `forensics/enrichment_service.py`: 20 probe tests in `tests/probe_f198b/test_forensics_metadata_enrichment.py`
  - `ForensicsResult` typed dataclass with fields: `finding_id`, `file_path`, `whois`, `ssl`, `dns`, `rdns`, `enrichment_available`
  - `enrich()` returns dict with `"forensics"` key containing `ForensicsResult.to_dict()`
  - `enrich()` injects into `finding.metadata["forensics"]` when finding has mutable `metadata` dict
  - Domain extraction from URL payload_text via `_extract_domain_from_url()`
  - External lookups: `_whois_lookup()`, `_ssl_lookup()`, `_dns_lookup()`, `_rdns_lookup()` — all with 5s timeout + graceful fallback (None on failure)
  - Fail-soft: all methods return None on failure, never raise
  - URL-based enrichment: findings with URL payload_text are processed via WHOIS/SSL/DNS/rDNS
- 20 probe tests passing: F198B-1 through F198B-7 cover all invariants

## F198C Multimodal Document Findings

Sprint F198C adds document extraction for PDF/image inputs producing CanonicalFinding(source_type="document"):
- `multimodal/analyzer.py`: `DocumentExtractor` and `DocumentResult` added
  - `DocumentResult` typed dataclass: `finding_id`, `file_path`, `file_type`, `text_content`, `page_count`, `metadata`, `extraction_ok`, `to_dict()`
  - `DocumentExtractor.extract()` returns `CanonicalFinding(source_type="document")` or None
  - `DocumentExtractor.extract_batch()` for concurrent extraction (Semaphore-4 bounded, M1 8GB safe)
  - PDF extraction via PyPDF2 (lazy-loaded, `_PYPDF2_AVAILABLE` guard)
  - Image placeholder via PIL (metadata extraction, no OCR in scope)
  - MAX_FILE_SIZE_BYTES=50MB guard, MAX_PDF_PAGES=500 cap, MAX_TEXT_CHARS=200000 cap
  - RAM guard: `_check_ram_guard()` denies at is_critical/is_emergency
  - Fail-soft: all methods return None/empty on failure, never raise
- `pipeline/live_public_pipeline.py`: F198C block added (lazy import of DocumentExtractor, fail-soft)
- `multimodal/__init__.py`: exports `DocumentExtractor` and `DocumentResult`
- 17 probe tests in `tests/probe_f198c/test_multimodal_document_findings.py`
  - Invariants: source_type="document" (exact), fail-soft, RAM guard, size limits, batch concurrency
  - F198C-1 through F198C-10 cover all invariants
- 99 probe tests across F196A-F198C passing

## F197A DeepProbe Canonical Ingest

DeepProbe findings now flow through the canonical persist path:
- `deep_probe.py`: `DeepProbeScanner._make_bucket_finding()` returns `CanonicalFinding` instead of direct persist
- `deep_probe.py`: `scan_ipfs()` returns `List[CanonicalFinding]` instead of direct persist
- `deep_probe.py`: `scan_s3_buckets()` returns `Tuple[List[dict], List[CanonicalFinding]]`
- `deep_research/probe_runner.py`: `run_deep_probe()` collects findings and calls `store.async_ingest_findings_batch()`
- `deep_research/probe_runner.py`: `_make_discovery_findings()` converts DHT URLs to `CanonicalFinding`
- 19 probe tests in `tests/probe_f197a/test_deep_probe_canonical_ingest.py`
  - Invariants: source_type="deep_probe", bounded timeout/depth, fail-safe everywhere
  - `async_ingest_findings_batch()` is the only write path
  - Counter contract: `result["findings_ingested"]` tracks accepted count

## F201A Smoke Concurrency Contract Repair (2026-04-24)

**Přidáno** — repaired smoke_runner.py --smoke concurrency contract:
- `utils/concurrency.py`: `_FetchSemaphoreProxy.limit()` method added — returns current semaphore limit via `get_fetch_semaphore()._value`. Fail-safe, delegates to underlying lazy singleton.
- `smoke_runner.py`: smoke test updated to match actual contract:
  - `AdaptiveSemaphore()` initialized without `initial_value` kwarg — asserts `current_limit == 3` (M1 hard ceiling)
  - `FETCH_SEMAPHORE` checked via `hasattr(limit)` and `FETCH_SEMAPHORE.limit()` instead of `isinstance(AdaptiveSemaphore)`
  - `adjust_fetch_workers` assertions use `FETCH_SEMAPHORE.limit()` instead of `._value`

**Concurrency contract invariants (F201A-1 až F201A-6)**:
- `AdaptiveSemaphore()` initializes with `current_limit=3` (M1 hard ceiling, `_CONCURRENCY_CEILING=3`)
- `FETCH_SEMAPHORE.limit()` returns current underlying semaphore limit via proxy delegation
- `adjust_fetch_workers(3)` sets FETCH_SEMAPHORE to limit 3 (model loaded path)
- `adjust_fetch_workers(25)` restores FETCH_SEMAPHORE to limit 25 (model released path)
- M1 invariant: LLM loaded → fetch limit 3, LLM released → fetch limit 25
- No new model lifecycle owner introduced — `brain/model_manager.py` remains the single owner

**Updated sections**:
- `utils/concurrency.py`: `limit()` method on `_FetchSemaphoreProxy`
- `smoke_runner.py`: all 6 smoke checks updated to match actual lazy proxy contract
- `REAL_ARCHITECTURE.md`: F200A and F199B smoke failure bullets removed (resolved by F201A)

**Tests**: 13 probe tests in `tests/probe_f201a/test_smoke_concurrency_contract.py`:
- `TestF201AAdaptiveSemaphoreContract`: F201A-1 (current_limit=3 default)
- `TestF201AFetchSemaphoreProxy`: F201A-2 (limit() returns int)
- `TestF201AAdjustFetchWorkers`: F201A-3/4 (3→25→3 roundtrip)
- `TestF201ALLMPathConcurrency`: F201A-5/6 (load/release lifecycle)
- `TestF201AImportContract`: root import surface verification
- 320 probe tests across F196A-F201A passing

## F201C Repository Artifact Hygiene (2026-04-24)

**Přidáno** — hygiene guards preventing bytecode and ghost backup artifacts from entering tracked tree:

**Ghost artifact cleanup (F201C-1)**:
- `runtime/telemetry.py.bak_F180F` — removed from git index (was tracked ghost backup)
- `tests/probe_8bh/runtime/.venv_ddgs/` — removed from git index (test venv artifact)
- `.srclight_bak/index.db*` — removed from git index (srclight backup artifact)

**`.gitignore` artifact patterns**:
- Bytecode: `__pycache__/`, `*.pyc`, `*.pyo`, `*.so`
- Ghost backups: `*.bak`, `*.bak_*`, `*_bak_*`, `.bak_*`
- Srclight: `.srclight/`, `.srclight_bak/`
- Probe venv: `tests/probe_*/runtime/.venv*/`

**Probe tests**: `tests/probe_f201c/test_repo_artifact_hygiene.py` — 6 invariants:
- `test_no_tracked_pycache`: `__pycache__/` not tracked
- `test_no_tracked_pyc`: `*.pyc`/`*.pyo` not tracked
- `test_no_tracked_dsvc`: `.DS_Store` not tracked
- `test_no_tracked_bak_files`: ghost backup source not tracked
- `test_no_tracked_srclight_bak`: `.srclight_bak/` not tracked
- `test_no_tracked_probe_venv`: probe venv not tracked

**Ghost audit methodology**: counts source `.py` call-sites, not bytecode. Untracked user directories (`.backup/`, `.codebase-memory/`, `.full-review/`, `rl/.sprint_policy_state.json`) are excluded from hygiene scope.

## F202E: Temporal Archaeology and Drift Timelines (2026-04-24)

**Přidáno** — timeline synthesizer that composes CT timestamps, archive observations, document metadata timestamps, and finding timestamps into bounded explainable timeline:

**Timeline Synthesizer** (`intelligence/timeline_synthesizer.py`):
- `TimelineSynthesizer` — event aggregation from multiple sources
- `TimelineEvent` — single timestamped event (ts, event_type, source, description, entity_id, confidence, evidence)
- `TimelineMetadata` — aggregate stats (total_events, oldest/newest ts, event_types, sources)
- `SynthesizedTimeline` — complete timeline with events and metadata

**Event Sources**:
- CT timestamps: `add_ct_events()` — extracts ts from `source_type="ct_log"` findings
- Archive observations: `add_archive_events()` — from ArchiveResult/ArchivedVersion objects
- Document timestamps: `add_document_timestamps()` — from DocumentMetadata created/modified fields
- Finding timestamps: `add_finding_events()` — generic finding ts extraction

**Bounds** (F202E-3):
- `MAX_TIMELINE_EVENTS = 200` — hard cap on output events
- `MAX_EVENT_AGE_DAYS = 1825` — 5 years, events older excluded
- Invalid timestamps (NaN, negative, far future) skipped fail-soft (F202E-4)

**Temporal Archaeologist Adapter** (`intelligence/temporal_archaeologist_adapter.py`):
- `TemporalArchaeologistAdapter` — wraps TimelineSynthesizer for sprint pipeline
- `synthesize_timeline()` — multi-source aggregation → `SynthesizedTimeline`
- `to_derived_findings()` — converts timeline to CanonicalFinding with source_type="temporal_archaeology"

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_run_temporal_archaeology_sidecar()` — async sidecar after CT findings accepted
- Filters CT findings (`source_type="ct_log"`) and synthesizes timeline
- Derived findings ingested via `async_ingest_findings_batch()` — canonical write path
- `timeline_findings_produced` counter in `SprintSchedulerResult`
- Non-blocking, fail-soft (F202E-8)

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_timeline_section()` — renders timeline with entity_id, event count, time span, event type/source breakdown, bounded event list
- Bounded at 5 timelines displayed, 50 events per timeline

**No Model Load** (F202E-12):
- Pure Python timestamp processing — no MLX, no transformers
- O(n log n) sort for timeline ordering (n ≤ 200)

**Tests**: `tests/probe_f202e/test_temporal_archaeology_timeline.py` — 35 tests:
- F202E-1: source_type="temporal_archaeology" for derived findings
- F202E-2: payload_text contains serialized timeline JSON
- F202E-3: MAX_TIMELINE_EVENTS=200 cap applied
- F202E-4: Invalid timestamps skipped fail-soft
- F202E-5: async_ingest_findings_batch integration
- F202E-6: Sidecar wiring in SprintScheduler
- F202E-7: timeline_findings_produced in result
- F202E-8: Fail-soft error handling
- F202E-9: Event types (ct_observed, archive_snapshot, document_dated, finding_accepted)
- F202E-10: Timeline sorted ascending
- F202E-11: Markdown rendering
- F202E-12: No model load

---

## F202I: Multimodal Evidence Triage (2026-04-24)

**Přidáno** — bounded triage extraction from PDF/image artifacts discovered in sprint runs. Extracts: title/author, EXIF/GPS, OCR snippets, file hashes, embedded URL/domain hits.

**EvidenceTriageCoordinator** (`multimodal/evidence_triage.py`):
- `TriageFacets` dataclass: title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains, triage_complete
- `EvidenceTriageCoordinator.extract_triage_facets(file_path, source_type)` — orchestrator method
- Metadata extraction via `UniversalMetadataExtractor` (forensic metadata: title, author, EXIF, GPS, hashes)
- OCR text extraction via `VisionOCR` (macOS Vision framework — no VLM)
- URL/domain hit detection in OCR text via regex
- All operations are **fail-safe**: partial facets returned on any error

**Bounds** (F202I-3, F202I-4, F202I-17):
- `MAX_URL_HITS = 20` — max embedded URLs/domains per file
- `MAX_OCR_SNIPPETS = 10` — max OCR text snippets stored
- `MAX_OCR_CHARS = 5000` — max OCR characters per file
- `MAX_FILE_SIZE_FOR_TRIAGE = 100MB` — files larger are skipped
- `METADATA_TIMEOUT_S = 30.0` — metadata extraction timeout
- `OCR_TIMEOUT_S = 30.0` — OCR timeout

**No VLM** (F202I-15):
- Uses `VisionOCR` (macOS Vision OCR) only — no VisionEncoder, no MambaFusion, no MLX models
- Model load/unload only via `brain/model_lifecycle.py`

**Evidence Envelope** (`multimodal/analyzer.py` `_build_document_envelope()`):
- Combines F202A envelope pattern (audit_reason, evidence_pointers, signal_facets, suggested_pivots) with F202I triage facets
- F202I triage section: title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains
- `content_preview`: first 1000 chars of extracted text
- Bounded at `_MAX_ENVELOPE_SIZE = 4098` bytes

**DocumentExtractor Integration** (`multimodal/analyzer.py`):
- `DocumentExtractor.extract()` calls `EvidenceTriageCoordinator.extract_triage_facets()` before building `CanonicalFinding`
- Resulting `CanonicalFinding(source_type="document")` has `payload_text` containing triage envelope JSON
- RAM guard via `_check_ram_guard()` — blocks when UMA critical/emergency

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_evidence_triage_adapter` field (lazy, None until first use)
- `_evidence_triage_findings_count` counter in `SprintSchedulerResult`
- `_run_evidence_triage_sidecar(findings, store, query)` — counts document findings with triage envelopes
- Called after `_run_temporal_archaeology_sidecar()` — non-blocking sidecar
- **No change to live_feed tuple contract** — sidecar is purely observational

**Tests**: `tests/probe_f202i/test_multimodal_evidence_triage.py` — 36 tests:
- F202I-1: Lazy initialization of metadata extractor
- F202I-2: extract_triage_facets returns TriageFacets
- F202I-3: URL/domain extraction bounded at MAX_URL_HITS=20
- F202I-4: OCR snippets bounded at MAX_OCR_SNIPPETS=10
- F202I-5: File hashes extracted from GenericMetadata
- F202I-6: TriageFacets.to_dict() includes all required fields
- F202I-7: DocumentExtractor.extract() calls triage and builds envelope
- F202I-8: _build_document_envelope produces JSON with triage facets
- F202I-9: Envelope bounded at _MAX_ENVELOPE_SIZE=4098
- F202I-10: _run_evidence_triage_sidecar counts document findings with triage
- F202I-11: SprintSchedulerResult.evidence_triage_findings_count field exists
- F202I-12: _evidence_triage_adapter field exists in SprintScheduler
- F202I-13: Sidecar called after F202E temporal archaeology sidecar
- F202I-14: Fail-soft throughout
- F202I-15: No VLM (VisionOCR only)
- F202I-16: RAM guard blocks when UMA tight
- F202I-17: Size guard skips files > 100MB
- F202I-18: OCR timeout after 30s
- F202I-19: Metadata timeout after 30s
- F202I-20: No live_feed tuple contract change

---

## F202J: M1 Sustained Sprint Governor (2026-04-24)

**Přidáno** — advisory safety layer for M1 8GB branch concurrency, model lease, and renderer lease.

**M1ResourceGovernor** (`runtime/resource_governor.py`):
- Advisory safety layer — NOT a sprint owner. Reads from canonical sources only.
- Governs branch concurrency, model lease, renderer lease via fail-soft decisions
- `GovernorDecision` dataclass: `fetch_limit`, `allow_renderer`, `allow_model_load`, `branch_concurrency`, `reason`, `uma_state`, `model_loaded`
- `GovernorSnapshot` dataclass: current state for dashboard rendering
- `evaluate()` async: evaluates governor decisions per cycle
- `apply_decision()` async: applies decisions to runtime surfaces via `adjust_fetch_workers()`
- Singleton via `get_governor()`

**Read-only surfaces**:
- `brain/model_lifecycle.get_model_lifecycle_status()` — model lease state
- `core/resource_governor.sample_uma_status()` — UMA memory state
- `utils/concurrency.adjust_fetch_workers()` — fetch concurrency control

**Decision logic**:
- CRITICAL/EMERGENCY UMA → `fetch_limit=3`, `allow_renderer=False`, `branch_concurrency=1`
- Model loaded → `fetch_limit=3`, `allow_renderer=False`, `branch_concurrency=2`
- WARN UMA → `fetch_limit=12` (half of 25), `branch_concurrency=3`
- Normal → `fetch_limit=25`, `branch_concurrency=4`

**Sprint Scheduler integration** (`runtime/sprint_scheduler.py`):
- `_governor` field: lazy singleton, initialized in `run()` via `get_governor()`
- `_run_resource_governor_advisory()` at TEARDOWN: evaluates and applies governor decision
- Aggressive branch concurrency uses `decision.branch_concurrency` via semaphore limit
- `_reset_result()` resets governor reference (singleton stays across sprints)
- Governor is advisory only — scheduler retains all authority

**Dashboard** (`monitoring/sprint_dashboard.py`):
- Governor state row: uma state, fetch limit, branches, model status, denied counts
- Fail-safe: errors silently ignored (optional dashboard info)

**Hermetic benchmark** (`benchmarks/m1_sustained_sprint.py`):
- `--hermetic` flag (default True) — canned data, no network, no MLX
- Measures: findings/min, acceptance ratio, RSS peak, UMA state summary, denied counts
- M1 8GB ceiling: `M1_8GB_CEILING_MB = 6.5 * 1024`
- Writes bounded summary to stdout and optional JSON

**Key invariants**:
- Model lifecycle authority remains `brain/model_lifecycle.py` (read-only client)
- No model + JS renderer concurrently — enforced via `allow_renderer=False` when model loaded
- FETCH_SEMAPHORE limit=3 while model loaded — via `adjust_fetch_workers(3)`
- Governor fails soft — safe defaults on any error

**Tests**: `tests/probe_f202j/test_m1_resource_governor.py` — 10 probe tests (F202J-1 through F202J-10)

---

## F202F: Local Graph/RAG Analyst Workbench (2026-04-24)

**Přidáno** — analyst read-side facade for local questions over findings, graph, and vectors. Works without LLM (extractive fallback) and without external network calls.

**AnalystWorkbench** (`knowledge/analyst_workbench.py`):
- `AnalystWorkbench` — read-side facade aggregating DuckDBShadowStore, DuckPGQGraph, LanceDB VectorStore
- `AnalystAnswer` — result DTO with extractive_answer, llm_answer, evidence_pointers, related_entities, context_bytes, model_used, sources_used, timing_ms
- `EvidencePointer` — finding_id, source_type, query, confidence, ts, provenance, envelope_available, snippet
- `RelatedEntity` — entity_value, entity_type, confidence, hops, relation_types

**Bounds** (F202F-1 through F202F-5):
- `MAX_CONTEXT_BYTES = 8192` — 8KB max context per answer
- `MAX_TOP_K = 20` — max results from any single source
- `MAX_GRAPH_HOPS = 2` — entity history max hops
- `MAX_EVIDENCE_PTRS = 5` — max evidence pointers per answer
- `MAX_RELATED_ENTITIES = 10` — max related entities per answer

**Core pipeline** (`ask()`):
1. `query_findings()` — keyword search over recent DuckDBShadowStore findings
2. `query_graph()` — multi-hop entity traversal via DuckPGQGraph
3. `_extract_answer()` — deterministic extractive text (no model required)
4. `get_evidence_pointers()` — EvidencePointer list from findings
5. `get_related_entities()` — RelatedEntity list from graph traversal
6. (Optional) `_generate_llm_answer()` via `brain/model_lifecycle.py`

**No-model invariants** (F202F-12, F202F-16):
- `_extract_answer()` returns keyword-matched paragraph — pure Python, no MLX
- No external network calls — all data sources are local (DuckDB, LanceDB, DuckPGQGraph)
- Model lifecycle via `brain.model_lifecycle.load_model()` / `unload_model()` only
- `ask()` always returns `extractive_answer` even when `model_used=False`

**Export layer** (`export/jsonld_exporter.py`):
- `render_analyst_evidence_jsonld()` — exports AnalystAnswer as JSON-LD with ghost: namespace
- `render_analyst_evidence_jsonld_str()` — deterministic JSON string with sorted keys
- Evidence pointers include: finding_id, source_type, query, confidence, ts, provenance, snippet

**Store wiring** (pass-through to existing modules):
- DuckDBShadowStore: `async_query_recent_findings(limit)` → finding dicts
- DuckPGQGraph: `find_entity_history(entity, max_hops)` → entity history
- LanceDB VectorStore: `query(vector, k, index_type="text")` → (id, score) tuples
- SemanticStore: `semantic_pivot(query, top_k)` → finding_ids (if available)

**Factory**: `create_analyst_workbench()` — lazily resolves VectorStore and DuckPGQGraph singletons; DuckDBShadowStore and SemanticStore passed explicitly by caller.

**Tests**: `tests/probe_f202f/test_local_graph_rag_workbench.py` — 50 tests:
- F202F-1 through F202F-5: bounds constants
- F202F-6: `ask()` always returns extractive_answer
- F202F-7: `query_findings()` keyword search
- F202F-8: `query_graph()` bounded RelatedEntity list
- F202F-9: `query_vectors()` bounded by MAX_TOP_K
- F202F-10: evidence_pointers built from findings
- F202F-11: related_entities from graph traversal
- F202F-12: `_extract_answer()` no-model fallback
- F202F-13: `_truncate_to_bytes()` respects MAX_CONTEXT_BYTES
- F202F-14: `_build_evidence_pointers()` caps at MAX_EVIDENCE_PTRS
- F202F-15: `create_analyst_workbench()` factory
- F202F-16: no external network calls
- F202F-17: JSON-LD evidence export
- F202F-18: `ask_sync()` produces AnalystAnswer
- F202F-19: graph_rag `multi_hop_search` has max_nodes bounded
- F202F-20: rag_engine has no-model BM25/hybrid fallback

---

## F202G: Hypothesis-Driven Pivot Planner (2026-04-24)

**Přidáno** — bounded advisory layer that generates next pivots from accepted findings and envelope facets. Scheduler uses pivots as advisory ordering input, NOT as new sprint owner.

**PivotPlanner** (`runtime/pivot_planner.py`):
- `Pivot` — dataclass with priority, pivot_type, ioc_value, ioc_type, reason, expected_value, source_hint, evidence_pointers
- `PivotType` — constants: DOMAIN, IDENTITY, LEAK, ARCHIVE, GRAPH
- `PivotPlanner.plan_pivots(findings, graph_stats, max_pivots)` → `list[Pivot]`

**Pivot types** (5 total):
- `domain` — DNS, WHOIS, passive DNS pivots from domain IOCs
- `identity` — entity resolution, profile correlation from email/username IOCs
- `leak` — paste/GitHub/breach signal pivots from email IOCs
- `archive` — wayback, archive.org historical pivots from domain/URL IOCs
- `graph` — IOC graph traversal pivots from IP/hash IOCs

**Bounds** (F202G-1 through F202G-4):
- `MAX_PIVOTS = 20` — max pivots per sprint
- `MAX_ENVELOPE_SIZE` — envelope deserialization bounded
- Graph stats optional — no graph means no novelty bonus
- Deduplication key: `(pivot_type, ioc_type, ioc_value)` — same IOC can have multiple pivot types

**Scoring**:
- `_cheap_score_finding()` — heuristic scoring without model inference
- Source type quality boost for ct_log, certificate, cisa_kev, threatfox_ioc, public, deep_probe, forensics, multimodal
- Signal facets boost from envelope when available
- Per-type scoring: domain (novelty bonus), identity (email/URL boost), leak (email breach bonus), archive (supplementary), graph (novelty + degree)

**Evidence envelope integration**:
- `_deserialize_envelope()` — extracts envelope from finding.payload_text if audit_reason present
- Envelope signal_facets influence scoring
- `source_hint` tracks which finding triggered each pivot

**Discovery wiring** (`discovery/source_registry.py`):
- `PIVOT_TYPE_MAP` — maps IOC types to default pivot types
- `get_pivot_type(ioc_type)` → pivot_type string
- `get_pivot_task_types(pivot_type)` → list of task types for each pivot

**Scheduler advisory hook** (`runtime/sprint_scheduler.py`):
- `_run_pivot_planner_advisory()` — called at sprint teardown
- `inject_pivot_planner(planner)` — DI interface for PivotPlanner
- Advisory only — scheduler retains authority, pivots influence ordering not execution
- Fail-soft — planner failure never blocks export or sprint

**Tests**: `tests/probe_f202g/test_hypothesis_pivot_planner.py` — 20 tests:
- F202G-1: MAX_PIVOTS=20 bound enforced
- F202G-2: Empty findings returns empty list (fail-soft)
- F202G-3: Domain IOC → domain pivot + archive pivot
- F202G-4: IP IOC → domain pivot (reverse DNS) + graph pivot
- F202G-5: Hash IOC → graph pivot
- F202G-6: Email IOC → leak pivot + identity pivot
- F202G-7: URL IOC → domain pivot + archive pivot
- F202G-8: Envelope deserialization from payload_text
- F202G-9: Pivot has required fields (reason, expected_value, source_hint, evidence_pointers)
- F202G-10: Deduplication by (pivot_type, ioc_type, ioc_value)
- F202G-11: Sorting by expected_value descending
- F202G-12: Planner fail-soft on exception
- F202G-13: PIVOT_TYPE_MAP in source_registry
- F202G-14: get_pivot_task_types returns task list

**Constraints honored**:
- Planner failure never blocks export or sprint (fail-soft)
- Model load/unload only via brain.model_lifecycle (future: _score_with_model async stub exists)
- No model + JS renderer concurrently (advisory only, no rendering)

---

## F202H: OPSEC Transport Policy Engine (2026-04-24)

**Přidáno** — single read-side policy engine for transport posture. Prevents M1 model+renderer conflicts and provides concurrency/timeout hints.

**OPSEC Policy Engine** (`runtime/opsec_policy.py`):
- `OPSECContext` — dataclass with: `has_model_context`, `has_stealth`, `transport_hint`, `risk_level`
- `RendererPolicy` — frozen dataclass: `allowed`, `max_concurrent`, `timeout_hint`, `blocked_reason`
- `ConcurrencyHint` — frozen dataclass: `max_workers`, `timeout_s`
- `TransportPolicy` — composite: `renderer` + `concurrency` + `transport` string

**Core functions**:
- `get_renderer_policy(ctx)` — M1 model+renderer conflict guard. Returns `allowed=False` when `has_model_context=True` (blocked_reason="M1_model_context_active"). Also blocks when renderer concurrency exhausted.
- `get_concurrency_hint(transport_hint)` — per-transport concurrency hints: clearnet=3, tor=2, i2p=1, stealth=2
- `get_transport_policy(ctx)` — composite policy combining renderer + concurrency. Lowers concurrency when renderer blocked.
- `get_stealth_capability_flags(has_model_context)` — advisory flags for StealthSession. Disables TLS fingerprint under model load to reduce RAM pressure.

**Renderer lifecycle tracking**:
- `acquire_renderer_slot()` / `release_renderer_slot()` — thread-safe slot management
- `get_renderer_active_count()` — current active renderer count
- MAX_CONCURRENT_RENDERERS=1 (M1 single-JS-renderer constraint)

**Transport resolver integration** (`transport/transport_resolver.py`):
- `get_transport_hint_string(url)` — maps Transport enum to string ("clearnet", "tor", "i2p") for opsec_policy
- Aligns with existing `get_transport_for_url()` — same classification logic

**Public fetcher integration** (`fetching/public_fetcher.py`):
- `_fetch_with_camoufox()` now uses `get_renderer_policy(OPSECContext(has_model_context=is_embedding_context_active()))` instead of inline check
- Policy consulted before every Camoufox launch — centralized M1 conflict guard

**Key invariants**:
- [F202H-I1] `get_renderer_policy(OPSECContext(has_model_context=True)).allowed` is False
- [F202H-I2] `get_concurrency_hint("clearnet").max_workers` == 3
- [F202H-I3] `acquire/release` slot maintains count within [0, MAX_CONCURRENT_RENDERERS]
- [F202H-I4] `get_stealth_capability_flags(has_model_context=True)["tls_fingerprint"]` is False
- [F202H-I5] `get_transport_policy` returns `TransportPolicy` with correct `renderer.allowed`
- [F202H-I6] `asyncio.gather(return_exceptions=True)` + `_check_gathered` pattern present
- [F202H-I7] smoke: renderer disabled while model context active, fail-open to clearnet

**Fail-safe**: All methods return safe defaults — renderer allowed=False with reason when blocked, concurrency hints are conservative. No crashes on import failure (fail-open).

**Tests**: `tests/probe_f202h/test_opsec_transport_policy.py` — 28 tests all passing.

---

## Architectural verdict

### What is real today
- The project already has a real, typed canonical sprint path with feed + public + CT + aggressive branch fan-out + partial export + Hermes prewarm.
- Public pipeline is the densest integration surface: CommonCrawl, onion discovery, academic discovery, paste monitoring, GitHub secret scanning, bounded P12 hypothesis/ToT burst.
- The store seam is strong and centralized.

### What is not real today
- No `SprintMode` enum.
- No root `--mode aggressive` CLI.
- `deep_research/` is now canonical (F197A): `run_deep_probe_if_enabled()` -> findings normalized to `CanonicalFinding` -> `async_ingest_findings_batch()`
- No evidence that many ambitious subtrees (`forensics`, `multimodal`, `planning`, `research`) are part of the canonical sprint path.
- `rl/marl_coordinator.py` was deleted in F196A (stub with zero production call-sites); `rl/sprint_policy_manager.py` is the surviving RL plane.
- Ghost modules (`intelligence_dispatcher`, `memory_watchdog`, `session_authority`) were removed in F196A; sprint_scheduler.py is clean.
- Test baseline is not green; there is still real debt before major new subsystem activation.

## F202A Evidence Envelope and Signal Schema (2026-04-24)

**Přidáno** — bounded evidence envelope layer for CanonicalFinding audit trail:

**Schema** (`knowledge/finding_envelope.py`):
- `FindingEnvelope` plain Python class with fields: `audit_reason: str`, `evidence_pointers: list[str]`, `signal_facets: dict[str, float]`, `suggested_pivots: list[dict]`
- `MAX_ENVELOPE_SIZE = 4098` bytes — deterministic upper bound
- `envelope_size_guard()` — returns True if serialized JSON fits within bound
- `serialize_envelope()` / `deserialize_envelope()` — roundtrip to/from JSON string

**Storage seam** (`knowledge/duckdb_store.py`):
- Envelope serialized into `CanonicalFinding.payload_text` JSON field — no new write path
- `async_ingest_findings_with_envelope()` — ingest with envelope; size guard degrades oversized to plain
- `async_get_findings_with_envelope()` — read seam with deserialized envelope attached

**Export** (`export/sprint_exporter.py`):
- `envelope_findings` key in export result dict — list of findings with deserialized envelopes

**Markdown rendering** (`export/sprint_markdown_reporter.py`):
- `_render_envelope_findings()` helper — renders audit_reason, evidence_pointers, signal_facets, suggested_pivots; bounded at 10 findings; fail-soft skips invalid envelopes

**Probe tests**: `tests/probe_f202a/test_evidence_envelope_schema.py` — 19 tests, all passing.

## F202B Identity Stitching Sidecar (2026-04-24)

**Přidáno** — deterministic entity extraction and identity stitching as a bounded sidecar on accepted findings:

**Entity Signal Extractor** (`intelligence/entity_signal_extractor.py`):
- `extract_entities_from_finding()` — extracts emails, usernames, domain handles from `CanonicalFinding.payload_text` via regex
- `extract_entities_from_findings()` — groups entities into `EntitySignalProfile` list; bounded at MAX_PROFILES=500
- `ExtractedEntity` dataclass: entity_type, value, raw_value, platform, finding_id, confidence
- `EntitySignalProfile` dataclass: id, primary_name, emails, usernames, domain_handles, platforms, finding_ids, confidence
- No ML models — pure regex/string heuristics
- M1 8GB safe: bounded profile count, no large in-memory structures

**Identity Stitching Canonical Adapter** (`intelligence/identity_stitching_canonical.py`):
- `IdentityStitchingAdapter` wraps `IdentityStitchingEngine` with M1-safe bounds
- `extract_and_stitch()` — converts EntitySignalProfile → IdentityProfile → IdentityStitchingEngine → IdentityCandidate list
- `to_derived_findings()` — converts IdentityCandidate list → CanonicalFinding(source_type="identity_stitching") list
- `upsert_identity_edges()` — writes same_identity edges to graph_service
- Bounded: MAX_COMPARISONS=2000 cap enforced, `optimize_memory()` called after each batch
- All methods fail-soft: sprint continues on any error
- `IdentityCandidate` dataclass: candidate_id, profile_ids, primary_name, emails, usernames, platforms, confidence, signals, evidence, finding_ids

**Graph Service** (`knowledge/graph_service.py`):
- `upsert_identity_edge(src, dst, confidence, evidence)` — convenience wrapper around `upsert_relation` with `rel_type="same_identity"`. Advisory only.

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_identity_adapter` field (lazy, None until first use)
- `_run_identity_stitching_sidecar(findings, store, query)` — async sidecar after CT findings are accepted and stored
  - Extracts entity profiles (bounded MAX_PROFILES=500)
  - Runs stitching (bounded MAX_COMPARISONS=2000)
  - Upserts graph edges (advisory, fail-soft)
  - Converts to derived CanonicalFinding list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_ct_log_discovery_in_cycle()` stores findings — non-blocking sidecar
- `identity_candidates_found` and `identity_findings_produced` counters in `SprintSchedulerResult`
- `core/__main__.py`: scorecard includes `identity_candidates_found` and `identity_findings_produced`

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_identity_candidates()` — renders candidate_id, primary_name, confidence (high/medium/low), platforms, emails, usernames, signals, evidence, finding_ids
- Bounded at 10 candidates displayed
- Graceful degradation: skips non-dict items, empty candidates

**Data contracts**:
- Derived findings have `source_type="identity_stitching"` — searchable in DuckDB
- Identity candidates are explainable: each has confidence + individual signals + evidence pointers
- Graph edges are advisory (same_identity rel_type) — do not affect sprint completion
- No new public APIs beyond existing `async_ingest_findings_batch()`

**M1 8GB bounds**:
- MAX_PROFILES=500 entity profiles per sprint (from entity_signal_extractor)
- MAX_COMPARISONS=2000 stitching comparisons per sprint (hard cap)
- `optimize_memory()` called after each stitching batch

**Tests**: `tests/probe_f202b/test_identity_stitching_canonical.py` — 9 test classes, all MagicMock/AsyncMock, no real DB deps:
- F202B-1: Entity extraction (emails, usernames, domain handles)
- F202B-2: Profile grouping and MAX_PROFILES bound
- F202B-3: Adapter factory and stats
- F202B-4: extract_and_stitch produces candidates
- F202B-5: to_derived_findings produces CanonicalFinding
- F202B-6: upsert_identity_edge delegates to upsert_relation
- F202B-7: SprintSchedulerResult has identity counters
- F202B-8: Markdown rendering with confidence/signals
- F202B-9: SprintScheduler has _identity_adapter field

## F202C Asset Exposure Correlator (2026-04-24)

**Přidáno** — correlates asset exposure signals into explainable exposure findings:

**Signal Sources Consumed** (`intelligence/exposure_correlator.py`):
- `ct_log` findings: cert→SAN mappings, issuers, timestamps → `SIGNAL_TYPE_CT_CERT`
- `open_storage` findings: exposed S3/Firebase/Elasticsearch/MongoDB buckets → `SIGNAL_TYPE_OPEN_BUCKET`
- `jarm` findings: TLS fingerprint hashes → `SIGNAL_TYPE_JARM`
- `passive_dns` findings: domain→IP mappings → `SIGNAL_TYPE_PASSIVE_DNS`

**Correlation Types Produced**:
- `exposed_host`: host with open bucket + cert-domain or DNS correlation
- `cert_domain_relation`: CT cert SAN with issuer and timestamp
- `open_bucket`: confirmed exposed cloud storage bucket (S3/Firebase/Elasticsearch/MongoDB)
- `suspicious_service_fingerprint`: JARM hash with GREASE/000 prefix (known-suspicious)
- `infra_cluster`: 2+ hosts sharing same JARM hash (co-located infrastructure)

**ExposureCorrelatorAdapter** (`intelligence/exposure_correlator.py`):
- `correlate(findings, query)` — entry point, returns `CanonicalFinding(source_type="exposure_correlation")` list
- `get_stats()` / `reset()` — stats tracking for probe verification
- `create_exposure_correlator_adapter()` — factory function

**Evidence Envelope Fields** (in `payload_text` JSON):
- `evidence_pointers`: list of source `finding_id`s
- `signal_facets`: `{signal_type: confidence}` per-signal confidence contribution
- `suggested_pivots`: recommended follow-up queries as `[{type, query}, ...]`
- `correlation_payload`: full correlation data (jarm_hash, host_count, bucket_type, etc.)

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_exposure_adapter` field (lazy, None until first use)
- `_run_exposure_correlator_sidecar(findings, store, query)` — async sidecar after CT findings are accepted
  - Correlates signals into `ExposureFinding` objects
  - Converts to `CanonicalFinding` list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_identity_stitching_sidecar()` — non-blocking sidecar
- `exposure_findings_produced` and `correlated_assets_count` counters in `SprintSchedulerResult`

**Bounds**:
- `MAX_ASSETS = 1000` — max unique assets per sprint (hard cap)
- `MAX_SIGNALS_PER_ASSET = 3` — max signals per asset (hard cap)
- `MAX_FINDINGS = 500` — max exposure findings produced (hard cap)
- All methods fail-soft: sprint continues on any error

**Data Contracts**:
- Derived findings have `source_type="exposure_correlation"` — searchable in DuckDB
- Correlation findings are explainable: each has confidence rationale + evidence pointers
- `asyncio.gather(return_exceptions=True)` + `_check_gathered()` for all async operations
- Persists only via `async_ingest_findings_batch()` (canonical write path)
- External calls (JARM, open_storage scan) have timeouts + graceful fallback

**M1 8GB Safety**:
- Bounded asset map (MAX_ASSETS=1000) prevents OOM
- Signal-per-asset cap (MAX_SIGNALS_PER_ASSET=3) limits correlation explosion
- No ML models — pure regex/string heuristics

---

### F202D: Leak and Secret Sentinel

**Module**: `intelligence/leak_sentinel.py`

**Role**: Bounded optional branch that converts paste/GitHub/breach signals into redacted CanonicalFinding objects. No raw secrets in findings — all masked via redaction patterns.

**Sources**:
- `data_leak_hunter`: breach API results (HaveIBeenPwned, DeHashed, etc.)
- `pastebin_monitor`: paste site scraping (pastebin, paste.gg, rentry)
- `github_secret_scanner`: GitHub code search for leaked secrets

**Signal types produced**:
- `paste_leak`: paste site findings with redacted secrets
- `github_secret`: GitHub secret findings with masked context
- `leak_sentinel`: breach database findings with redacted PII

**Redaction** (F202D-3):
- Secret patterns applied BEFORE `fallback_sanitize` to prevent partial masking
- AWS keys (`AKIA...`), Stripe keys (`sk_live_...`), Bearer tokens, private key headers
- Generic credentials via `api_key=`, `password=`, `secret=`, `token=` patterns
- Google API keys (`AIza...`)

**Evidence envelope** (F202D-2):
- Stored in `payload_text` alongside finding data
- Contains: `audit_reason`, `evidence_pointers`, `signal_facets`, `suggested_pivots`
- All secrets replaced with `[REDACTED]` tokens before envelope construction

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_leak_sentinel_adapter` field (lazy, None until first use)
- `_run_leak_sentinel_sidecar(findings, store, query)` — async sidecar after CT findings accepted
  - Runs bounded leak scans via `LeakSentinelAdapter.scan(query)`
  - Converts findings to `CanonicalFinding` list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_exposure_correlator_sidecar()` — non-blocking sidecar
- `leak_findings_produced` counter in `SprintSchedulerResult`

**Bounds** (F202D-4):
- `MAX_LEAK_SOURCES = 3` — max concurrent source fetches
- `MAX_FINDINGS_PER_SOURCE = 50` — max findings per source
- `MAX_TOTAL_FINDINGS = 100` — max total findings (hard cap)
- `TIMEOUT_PER_SOURCE = 30.0` — seconds per source fetch

**Constraints**:
- No raw secrets in report/export — all redacted before persistence
- External calls timeout + fail-soft
- No background monitoring loop — single-shot bounded execution
- Persist only via `async_ingest_findings_batch()` (canonical write path)

**Tests**: `tests/probe_f202c/test_asset_exposure_correlator.py` — 12 test classes, all MagicMock, no real DB deps:
- F202C-1: Signal extraction (ct_log, open_storage, jarm, passive_dns)
- F202C-2: Correlation — open_bucket
- F202C-3: Correlation — exposed_host (bucket + cert/dns)
- F202C-4: Correlation — cert_domain_relation
- F202C-5: Correlation — infra_cluster (JARM clustering)
- F202C-6: CanonicalFinding conversion with evidence envelope
- F202C-7: Bounds degradation (MAX_ASSETS, MAX_SIGNALS_PER_ASSET, MAX_FINDINGS)
- F202C-8: Public API — correlate_exposure_signals
- F202C-9: ExposureCorrelatorAdapter stats and reset
- F202C-10: Suspicious JARM fingerprints (GREASE/000 prefix)
- F202C-11: Stats tracking
- F202C-12: Evidence envelope fields completeness

## Architectural verdict

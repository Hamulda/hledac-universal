# Hledač — Real Architecture (aktualizováno 2026-04-23, F200C)

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
| `forensics/` | metadata extraction, steganography, digital ghost for file artifacts | nenašel jsem přímý import z canonical sprint path | jen nepřímý odkaz ve `tests/test_sprint48_49.py` | vysoký potenciál jako enrichment layer |
| `knowledge/` | mix canonical store + analytics + entity/context modules | **ano**, hlavně `duckdb_store`, `semantic_store`, **graph_service** (F198A cross-sprint accumulation seam) | ano | aktivní jádro + zbytek částečně dormant |
| `graph/` | `quantum_pathfinder.py` (ML overlay/read analytics), `duck_pgq_graph.py` (DuckDB SQL/PGQ donor backend) | **ano**, `graph_service` upsert v sprint_scheduler (F198A); quantum_pathfinder is read-only analytics | ano | read-side analytics overlay; DuckPGQGraph is donor backend, not truth store |
| `loops/` | RL research loop (`ResearchLoop`) | **ano**, public pipeline jej importuje v P16/P17 bloku | vlastní coverage minimální | střední, ale experimentální |
| `federated/` | federated learning, DP, peer/model store; část souborů archivní | nenašel jsem canonical import | 3 test hits | nízká priorita pro OSINT sprint |
| `memory/` | `MemoryManager`, shared memory | **ano**, scheduler/public pipeline | mnoho test hitů | aktivní sidecar |
| `layers/` | univerzální orchestration layers, content/communication/ghost wrappers | nenašel jsem canonical sprint import | bez přímých testů v canonical flow | spíš legacy/integration stack |
| `multimodal/` | fusion/vision encoder primitives | nenašel jsem canonical import | bez testů | střední potenciál, dnes dormant |
| `context_optimization/` | cache/compression/MMR/active learning | **ano**, public pipeline používá MMR | bez cílených canonical testů | střední potenciál |
| `coordinators/` | široká vrstva coordinatorů, archive/claims/security atd. | pouze `security_coordinator` je aktivně volán v exportu | bez přímé canonical suite mapy | velká část dormant/secondary |
| `execution/` | `GhostExecutor` | nenašel jsem canonical import | 1 test hit | dormant |
| `network/` | BGP/IPFS/CT scanner/favicon/JARM/JS extraction | **ano**, některé moduly jsou aktivní přes pipeline/adaptery | ano | částečně aktivní, částečně dormancy |
| `planning/` | HTN planner, cost/search, task cache | nenašel jsem canonical sprint import | bez přímých testů v sprintu | dormant, možný future planner |
| `policy/` | Nym/Tor transport policy | nenašel jsem přímé zapojení do canonical sprintu | pár test hitů | dormant |
| `prefetch/` | prefetch cache/oracle/budget/reranker | nenašel jsem canonical import | bez testů | dormant |
| `research/` | branch manager, scheduler, prioritizer | nenašel jsem canonical import | 10 test hitů | dormant/alternative scheduler stack |
| `stealth/` | `StealthManager`, `StealthSession`, host telemetry/token bucket | nenašel jsem přímý canonical import; public fetcher má vlastní stealth logic | 3 test hits | dobrý kandidát na sjednocení stealth vrstvy |
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

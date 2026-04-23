# Hledač — Real Architecture (aktualizováno 2026-04-22)

Tento dokument je založený na reálném stavu kódu v repu, ne na `ARCHITECTURE_MAP.py`.

## Canonical Sprint Pipeline

### Hlavní zjištění
- Canonical sprint owner je `run_sprint()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:320`.
- Root CLI v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py:3028` je jen shell/dispatcher a pro sprint deleguje do `core.__main__.run_sprint()`.
- Root CLI dnes **nemá** `--mode aggressive` ani `SprintMode` enum. Canonical CLI přepínač je `--aggressive` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:1193`.
- `SprintMode` enum v aktuálním `runtime/sprint_scheduler.py` **neexistuje**.
- Jediný canonical write path pro findings je `DuckDBShadowStore.async_ingest_findings_batch()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py:4102`.

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
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py` | `async_run_live_public_pipeline()` | query, store, fetch budgets, Hermes, memory manager, optional feedback hook | `PipelineRunResult` | public branch |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py` | `DuckDBShadowStore`, `async_ingest_findings_batch()` | `list[CanonicalFinding]` | `list[FindingQualityDecision | ActivationResult]` | only canonical finding write seam |
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

## Dormant moduly (existují, ale nejsou wired nebo jen okrajově)

### Audited directories

| Modul/adresář | Co reálně dělá | Napojení na canonical sprint path | Testy | Potenciál / priorita |
|---|---|---|---|---|
| `forensics/` | metadata extraction, steganography, digital ghost for file artifacts | nenašel jsem přímý import z canonical sprint path | jen nepřímý odkaz ve `tests/test_sprint48_49.py` | vysoký potenciál jako enrichment layer |
| `knowledge/` | mix canonical store + analytics + entity/context modules | **ano**, hlavně `duckdb_store`, `semantic_store`, analytics seams | ano | aktivní jádro + zbytek částečně dormant |
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
| `rl/` | MARL/QMIX/state extraction/action constants | nenašel jsem canonical sprint wiring | hodně starších testů | vysoké riziko experimentality, nízká krátkodobá priorita |
| `pipeline/` | canonical feed/public pipelines | **ano** | ano | aktivní |
| `patterns/` | pattern matcher SSOT | **ano** | ano | aktivní |
| `orchestrator/` | secondary thin facade + phase/memory pressure/request routing | canonical sprint owner jej nepoužívá | několik testů | secondary/legacy orchestrator stack |
| `intelligence/` | capability forest: CT, academic, archive, image, blockchain, workflow helpers | část aktivní (`ct_log_client`, `academic_discovery`, paste/github scanners), část dormant | ano | mixed |
| `infrastructure/` | plugin manager, system monitor | nenašel jsem canonical import | bez testů | dormant |
| `discovery/` | adapters for public/feed/TI ingress | **ano** | ano | aktivní |
| `fetching/` | public fetch runtime | **ano** | nepřímo | aktivní |
| `deep_research/` | path discovery + deep utilities | nenašel jsem canonical import | bez testů | vysoký potenciál, dnes dormant |
| `dht/` | Kademlia crawl/lookups + local graph | `kademlia_node.py` existuje, ale DHT write path je no-op | ano | částečně dormant, not canonical producer today |
| `brain/` | model/embedding/hypothesis/distillation/router stack | část aktivní (`model_manager`, `hypothesis_engine`, `ane_embedder`) | má test coverage | mixed |
| `graph/` | DuckPGQ/graph manager/pathfinder | read-side seam aktivní přes store; full graph orchestration není canonical owner | hodně testů | střední až vysoký potenciál |
| `hypothesis/` | beta-binomial, Dempster-Shafer, EIG, generator | canonical path používá spíš `brain.hypothesis_engine`, ne tento subtree přímo | test hits existují | dormant support math layer |
| `embedding_cache/` | data/cache directory, bez `.py` souborů | žádný canonical import | jen test references | podpůrný asset, ne modul |

### Audited root files

| Soubor | Co reálně obsahuje | Canonical wiring | Testy | Verdikt |
|---|---|---|---|---|
| `enhanced_research.py` | velký dormant monolith (`UnifiedResearchEngine`, `EnhancedResearchOrchestrator`, `DeepResearchRequest`) | nenašel jsem canonical import | 1 hit | legacy candidate / reference only |
| `deep_probe.py` | deep crawler, dorking, path prediction, IPFS/S3 scanners | canonical sprint jej dnes nevolá | 1 hit | silný budoucí deep-research kandidát |
| `tool_registry.py` | typed tool schemas/cost model/registry | canonical sprint owner jej nepoužívá | 7 hitů | useful support layer, dnes ne-wired |
| `evidence_log.py` | canonical evidence ledger | není v canonical sprint path, spíš parallel/legacy ledger plane | 22 hitů | aktivní mimo sprint pipeline |
| `capabilities.py` | capability truth/router/model lifecycle facade | nepůsobí jako canonical sprint dependency | bez přímé sprint integrace | support/diagnostic layer |
| `embedding_pipeline.py` | lazy embedding load/generate/embed query/doc + memory guard | canonical sprint jej nevolá | 0 přímých test hitů | dormant but promising |
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
- `research/`, `planning/`, `orchestrator/`, `rl/`, `enhanced_research.py` představují paralelní architektonické směry mimo dnešní sprint pipeline.
- `live_public_pipeline.py` používá `source_type="live_public_pipeline"`, zatímco některé export/plan představy pracují s `live_public`.

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

- `forensics/` exists but has effectively no canonical integration tests.
- `multimodal/` has no visible direct test lane.
- `deep_research/` has no direct test lane in canonical sprint context.
- `embedding_pipeline.py` has no direct tests despite being a strong future integration point.
- `enhanced_research.py`, `resource_allocator.py`, `metrics_registry.py`, `autonomous_analyzer.py` have little or no direct coverage relative to size/importance.

## Architectural verdict

### What is real today
- The project already has a real, typed canonical sprint path with feed + public + CT + aggressive branch fan-out + partial export + Hermes prewarm.
- Public pipeline is the densest integration surface: CommonCrawl, onion discovery, academic discovery, paste monitoring, GitHub secret scanning, bounded P12 hypothesis/ToT burst.
- The store seam is strong and centralized.

### What is not real today
- No `SprintMode` enum.
- No root `--mode aggressive` CLI.
- No evidence that many ambitious subtrees (`forensics`, `multimodal`, `planning`, `research`, `deep_research`, `rl`) are part of the canonical sprint path.
- Test baseline is not green; there is still real debt before major new subsystem activation.

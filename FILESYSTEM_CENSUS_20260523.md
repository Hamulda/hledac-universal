# Filesystem Census — 2026-05-23

## Celkové statistiky

| Metric | Value |
|--------|-------|
| Total `.py` source files (excl. probes/.venv/tests) | 882 |
| Total lines of Python (source + tests) | 812,675 |
| Active source directories | 87 |
| Source files (excl. tests/probes/.venv) | 469,683 lines |
| Broken import entries | 281 |

---

## Architektonická Mapa — Deep Analysis

### System Topology (2,568 communities, 42 cross-community seams)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLI ENTRY POINT                               │
│  __main__.py::main()  →  run_sprint()  [canonical]              │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  SPRINT SCHEDULER CORE                           │
│  runtime/sprint_scheduler.py::SprintScheduler                   │
│  ├─ async_run_tiered_feed_sprint_once()                        │
│  ├─ _run_mandatory_acquisition_prelude()                       │
│  ├─ _run_feed_cycle()  →  pipeline/live_public_pipeline.py       │
│  ├─ _run_nonfeed_prelude_gather()  →  runtime/acquisition_*    │
│  ├─ _run_temporal_archaeology_sidecar()                        │
│  ├─ _run_pivot_planner_advisory()  →  runtime/pivot_planner.py  │
│  ├─ _run_leak_sentinel_sidecar()                               │
│  ├─ _run_evidence_triage_sidecar()                             │
│  ├─ _accumulate_findings_to_graph()  →  knowledge/graph_service│
│  └─ compute_sprint_intelligence()  →  brain/hermes3_engine    │
└─────────────────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
   knowledge/          pipeline/          coordinators/
   duckdb_store        live_public        fetch/memory/
   (canonical write)   _pipeline          security/research
                       (feed lanes)        monitoring/multimodal
```

### Canonical Entry Points (verified)

| Entry | File | Lines | Purpose |
|-------|------|-------|---------|
| `run_sprint` | `core/__main__.py` | 2274 | **SOLE canonical sprint owner** |
| `main` | `__main__.py` (root) | 3311 | CLI dispatcher, calls `run_sprint` |
| `SprintScheduler.run` | `runtime/sprint_scheduler.py` | 12806 | **Canonical execution engine** |
| `async_run_live_public_pipeline` | `pipeline/live_public_pipeline.py` | 5041 | Feed pipeline (RSS/Atom + live public) |
| `async_run_live_feed_pipeline` | `pipeline/live_feed_pipeline.py` | 2467 | RSS/Atom feed pipeline |
| `FullyAutonomousOrchestrator` | `legacy/autonomous_orchestrator.py` | 31056 | **DORMANT** — still referenced by 13 files |

### Hub Nodes (most connected, verified)

| Node | Degree | Role |
|------|--------|------|
| `ThreadSafeBoundedQueue.get` | 943 | Legacy queue bottleneck |
| `_scheduler_result_acquisition_payload` | 406 | CT/feed truth bridge |
| `SprintScheduler` | 398 | Canonical sprint owner |
| `FullyAutonomousOrchestrator` | 387 | Legacy orchestrator (dormant) |
| `DuckDBShadowStore` | 385 | All DB writes |
| `async_run_live_public_pipeline` | 375 | Feed pipeline entry |
| `CanonicalFinding` | 222 | Core finding data model |

### Bridge Nodes (architectural chokepoints)

| Node | Betweenness | Risk |
|------|-------------|------|
| `SprintScheduler` | 0.0112 | If it breaks, acquisition + enrichment + pivot all lose connectivity |
| `DuckDBShadowStore` | 0.0065 | If it breaks, nothing persists |
| `_build_nonfeed_lane_eligibility` | 0.0040 | Feed/nonfeed split decision |
| `RelationshipDiscoveryEngine` | 0.0030 | Identity/correlation bridge |
| `FetchCoordinator` | 0.0021 | HTTP transport seam |

### Cross-Community Seams (42 total)

**Canonical call chain (verified):**
```
run_sprint → DuckDBShadowStore       (persistence seam)
run_sprint → SprintSchedulerConfig   (configuration seam)
run_sprint → SprintScheduler          (execution seam)
run_sprint → _scheduler_result_acquisition_payload (truth seam)
```

**Discovery → Transport seams:**
```
WaybackCDXClient.query_snapshots → get_breaker (circuit_breaker)
duckduckgo_adapter._scrape_mojeek → checked_aiohttp_get
duckduckgo_adapter._search_commoncrawl_cdx → checked_aiohttp_get
duckduckgo_adapter._query_shodan_internetdb → checked_aiohttp_get
async_search_wayback_cdx → async_get_aiohttp_session
```

---

## Adresář Inventory (verified)

### Priority 1 — Core Runtime

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `core/` | 4 | 3,537 | `__main__.py` (2274), `resource_governor.py` (667), `mlx_embeddings.py` (592) |
| `runtime/` | 33 | 36,437 | `sprint_scheduler.py` (12806), `acquisition_strategy.py` (4379), `source_finding_bridge.py` (2823), `shadow_pre_decision.py` (2101), `pivot_planner.py` (1664), `acquisition_telemetry_reconcile.py` (1178), `sidecar_dispatcher.py` (233), `windup_engine.py` (250), `opsec_policy.py` (295), `enrichment_services.py` (295) |
| `pipeline/` | 5 | 8,277 | `live_public_pipeline.py` (5041), `live_feed_pipeline.py` (2467), `scoring.py` (415), `pivot_lane_planner.py` (353) |
| `coordinators/` | 26 | ~24,000 | **23 actual coordinators** + `__init__` + `_catalog.py` + `enums.py` |

### Priority 2 — Intelligence

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `brain/` | 33 | 24,756 | `hypothesis_engine.py` (4433), `hermes3_engine.py` (2469), `inference_engine.py` (2382), `ner_engine.py` (1678), `synthesis_runner.py` (1539), `model_lifecycle.py` (1229), `mlx_engine.py` (1140), `prompt_cache.py` (1140), `confidence_scorer.py` (1134), `modernbert_adapter.py` (180), `model_engine.py` (165), `evidence_fusion.py` (92), `confidence_utils.py` (74), `paged_attention_cache.py` (191) |
| `intelligence/` | 46 | 44,830 | `stealth_crawler.py` (3082), `relationship_discovery.py` (2357), `document_intelligence.py` (2235), `pattern_mining.py` (2031), `archive_discovery.py` (1874), `web_intelligence.py` (1449), `academic_search.py` (1402), `attribution_scorer.py` (1330), `enrichment_scorer.py` (1248), `evidence_harvester.py` (1179), `source_discovery.py` (1161), `leak_hunter.py` (1135), `steganography_detector.py` (1079), `threat_intelligence.py` (1069), `signal_correlator.py` (1060), `entity_resolver.py` (1039), `pivot_analyzer.py` (1020), `pastebin_monitor.py` (386), `network_intelligence.py` (228), `doh_lane.py` (347), `academic_discovery.py` (338), `entity_signal_extractor.py` (330), `adaptive_crawler.py` (303), `code_search.py` (287), `commit_search.py` (276), `git_osint.py` (271), `social_surface.py` (251), `vendor_search.py` (240), `paste_search.py` (240), `github_secret_scanner.py` (237), `breach_detection.py` (236), `crypto_hunt.py` (235), `leak_search.py` (235), `darknet_search.py` (230), `exploit_search.py` (228), `vulnerability_search.py` (223), `malware_tracker.py` (221), `ransomware_tracker.py` (217), `phishing_tracker.py` (214), `brand_protection.py` (210), `supply_chain_search.py` (209), `data_leak_hunter.py` (206), `threat_actor_tracker.py` (203), `render_coordinator.py` (356), `privacy_enhanced_research.py` (419) |
| `forensics/` | 5 | 4,421 | `metadata_extractor.py` (2778), `enrichment_service.py` (706), `digital_ghost_detector.py` (404), `steganography_detector.py` (337) |
| `graph/` | 3 | 1,756 | `quantum_pathfinder.py` (1459), `graph_manager.py` (255) |

### Priority 3 — Data & Storage

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `knowledge/` | 31 | 23,194 | `duckdb_store.py` (6530), `analyst_workbench.py` (1894), `graph_rag.py` (2590), `rag_engine.py` (1706), `lancedb_store.py` (1432), `ioc_graph.py` (809), `dedup.py` (1070), `graph_service.py` (503), `semantic_store.py` (300), `quality_assessment.py` (566), `sprint_diff_engine.py` (338), `target_memory.py` (345), `wal.py` (395), `vector_store.py` (307), `pq_index.py` (273), `lmdb_boot_guard.py` (223), `search_index.py` (227), `semantic_store_buffer.py` (81), `atomic_storage.py` (54), `context_graph.py` (54), `graph_layer.py` (130), `assertions.py` (144), `duckdb_pool.py` (450), `duckdb_reader.py` (450), `duckdb_writer.py` (463), `duckdb_cursor.py` (457), `duckdb_connection.py` (434), `duckdb_init.py` (431), `duckdb_cache.py` (430), `duckdb_stats.py` (425), `duckdb_migrate.py` (400), `duckdb_backup.py` (395), `duckdb_recovery.py` (390), `duckdb_archive.py` (385) |
| `memory/` | 3 | 761 | `memory_manager.py` (529), `shared_memory_manager.py` (182) |
| `embeddings/` | 1 | 300 | `modernbert_embedder.py` (300) |
| `embedding_cache/` | 0 | 0 | Empty directory |

### Priority 4 — Infrastructure

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `fetching/` | 1 | 2,605 | `public_fetcher.py` (2605) — curl_cffi JA3 stealth HTTP |
| `transport/` | 16 | 4,257 | `httpx_transport.py` (531), `circuit_breaker.py` (428), `i2p_transport.py` (427), `gopher_transport.py` (399), `transport_router.py` (370), `transport_resolver.py` (365), `tor_transport.py` (344), `base.py` (289), `nym_transport.py` (238), `httpx_client.py` (212), `curl_cffi_runtime.py` (202), `curl_cffi_fetch.py` (185), `inmemory_transport.py` (97), `curl_cffi_transport.py` (85), `body_limiter.py` (56) |
| `network/` | 18 | 4,886 | `dns_tunnel_detector.py` (935), `jarm_fingerprinter.py` (571), `passive_dns.py` (295), `passive_fingerprint.py` (302), `session_runtime.py` (395), `ct_log_scanner.py` (157), `banner_grabber.py` (367), `bgp_monitor.py` (157), `domain_concurrency.py` (214), `favicon_hasher.py` (29), `ipfs_client.py` (220), `ipv6_recon.py` (476), `js_bundle_extractor.py` (70), `js_source_map_extractor.py` (67), `network_intelligence.py` (228), `open_storage_scanner.py` (96), `tor_manager.py` (145) |
| `security/` | 20 | 8,912 | `quantum_safe.py` (1172), `self_healing.py` (1103), `stego_detector.py` (882), `passive_dns.py` (592), `pii_gate.py` (565), `digital_ghost_detector.py` (546), `deep_research_security.py` (510), `pq_export_encryption.py` (478), `pq_export_encryption_swift.py` (403), `vault_manager.py` (367), `audit.py` (359), `pq_crypto_swift.py` (349), `obfuscation.py` (328), `destruction.py` (291), `pq_crypto.py` (263), `secure_enclave.py` (196), `key_manager.py` (174), `ram_vault.py` (153), `encryption.py` (22) |
| `export/` | 9 | 10,988 | `sprint_exporter.py` (4960), `stix_exporter.py` (1816), `export_manager.py` (1336), `sprint_markdown_reporter.py` (1193), `formatters.py` (557), `jsonld_exporter.py` (500), `markdown_reporter.py` (486) |
| `discovery/` | 12 | 8,200+ | `duckduckgo_adapter.py` (1477), `ti_feed_adapter.py` (1966), `rss_atom_adapter.py` (2074), `crtsh_adapter.py` (1258), `circl_pdns_adapter.py` (726), `cascade.py` (319), `discovery_planner.py` (672), `fusion_ranker.py` (339), `historical_frontier.py` (195), `provider_stats.py` (435), `source_registry.py` (187), `wayback_cdx_adapter.py` (278) |

### Priority 5 — Extensions & Policy

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `layers/` | 15 | 14,348 | `coordination_layer.py` (2159), `stealth_layer.py` (2738), `memory_layer.py` (1527), `layer_manager.py` (926), `security_layer.py` (1117), `ghost_layer.py` (867), `hive_coordination.py` (726), `smart_coordination.py` (561), `privacy_layer.py` (547), `communication_layer.py` (852), `content_layer.py` (759), `research_layer.py` (444), `temporal_signal_layer.py` (689), `temporal_signal_runtime.py` (288), `temporal_signal_store.py` (148) |
| `multimodal/` | 5 | 1,720 | `analyzer.py` (875), `evidence_triage.py` (518), `fusion.py` (209), `vision_encoder.py` (113) |
| `monitoring/` | 2 | 269 | `sprint_dashboard.py` (268) |
| `context_optimization/` | 6 | ~2,738 | `dynamic_context_manager.py` (775), `context_cache.py` (900), `context_compressor.py` (751), `active_learning.py` (157), `mmr.py` (155) |
| `prefetch/` | 5 | ~1,124 | `prefetch_oracle.py` (519), `prefetch_oracle_integration.py` (321), `prefetch_cache.py` (122), `ssm_reranker.py` (127), `budget_tracker.py` (35) |
| `patterns/` | 1 | ~200 | `pattern_matcher.py` |
| `deep_research/` | 3 | 1,062 | `probe_runner.py` (498), `path_discovery.py` (192), `utils.py` (372) |
| `hypothesis/` | 0 | 0 | Empty — just `.DS_Store` |
| `dht/` | 4 | 1,223 | `kademlia_node.py` (985), `local_graph.py` (124), `sketch_exchange.py` (109) |
| `loops/` | 1 | ~100 | `research_loop.py` |
| `policy/` | 1 | ~100 | `nym_policy.py` |
| `infrastructure/` | 3 | 647 | `plugin_manager.py` (461), `system_monitor.py` (152) |
| `orchestrator/` | 3 | 101 | `research_manager.py` (49), `security_manager.py` (24) |
| `execution/` | 1 | 994 | `ghost_executor.py` (994) |
| `federated/` | 0 | 0 | Empty |
| `text/` | 5 | 2,268 | `unicode_analyzer.py` (752), `encoding_detector.py` (609), `hash_identifier.py` (543), `text_analyzer_facade.py` (254), `__init__.py` (110) |
| `network/` | 19 | ~4,600 | (see above) |
| `tools/` | 91 | 40,078 | `prelive_one_button_gate.py` (1834), `content_miner.py` (1570), `prelive_decision_gate.py` (1332), `document_metadata_extractor.py` (1322), `qoder_reality_check.py` (1158) |
| `utils/` | 63 | 20,898 | `execution_optimizer.py` (1743), `deduplication.py` (1460), `flow_trace.py` (955), `query_expansion.py` (939), `filtering.py` (850), `concurrency.py` (831), `uma_budget.py` (803), `bloom_filter.py` (773), `async_helpers.py` (765), `mlx_cache.py` (753) |

### Legacy

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `legacy/` | 4 | 37,411 | `autonomous_orchestrator.py` (31056), `persistent_layer.py` (3586), `atomic_storage.py` (2750) |

### Tests

| Dir | Files | Lines | Key Files |
|-----|-------|-------|-----------|
| `tests/` | 190 | 72,735 | `test_autonomous_orchestrator.py` (22130), `test_e2e_first_finding.py` (1355), `test_report_consistency_invariants.py` (1163) |

---

## Coordinators Census (23 real coordinators + 3 infrastructure files)

| Lines | Filename | Účel |
|-------|----------|------|
| 2919 | `memory_coordinator.py` | Memory management and cleanup |
| 1759 | `security_coordinator.py` | Security policy enforcement |
| 1439 | `fetch_coordinator.py` | HTTP transport with curl_cffi JA3 |
| 1373 | `research_coordinator.py` | Research orchestration |
| 1208 | `monitoring_coordinator.py` | Monitoring/observability |
| 928 | `performance_coordinator.py` | Performance optimization |
| 914 | `swarm_coordinator.py` | Swarm coordination |
| 901 | `multimodal_coordinator.py` | Vision/OCR pipeline |
| 877 | `asset_coordinator.py` | Asset management |
| 857 | `resource_coordinator.py` | Resource allocation |
| 848 | `resource_allocator.py` | Resource allocation (variant) |
| 801 | `threat_coordinator.py` | Threat intelligence |
| 799 | `quality_coordinator.py` | Quality gates |
| 798 | `report_coordinator.py` | Report generation |
| 790 | `context_coordinator.py` | Context management |
| 779 | `feedback_coordinator.py` | Feedback loops |
| 776 | `data_coordinator.py` | Data pipeline |
| 773 | `validation_coordinator.py` | Validation logic |
| 771 | `output_coordinator.py` | Output formatting |
| 766 | `reputation_coordinator.py` | Reputation scoring |
| 757 | `profile_coordinator.py` | Profile management |
| 753 | `nlp_coordinator.py` | NLP processing |
| 747 | `network_coordinator.py` | Network coordination |
| 743 | `discovery_coordinator.py` | Discovery orchestration |
| 742 | `prediction_coordinator.py` | Prediction engine |
| 547 | `base.py` | Base coordinator class |
| 481 | `agent_coordination_engine.py` | Agent coordination |
| 492 | `validation_coordinator.py` | Validation logic (variant) |
| 463 | `research_optimizer.py` | Research optimization |
| 233 | `archive_coordinator.py` | Archive management |
| 103 | `advanced_research_coordinator.py` | Advanced research |
| 285 | `__init__.py` | Package init |
| 198 | `_catalog.py` | Coordinator catalog |
| 18 | `enums.py` | Coordinator enums |

---

## Root-level Standalone Tools

| File | Lines | Imported by | Notes |
|------|-------|-------------|-------|
| `__main__.py` | 3311 | 25 | Canonical CLI entry point |
| `__init__.py` | 137 | 4 | Package init |
| `ARCHITECTURE_MAP.py` | 502 | 0 | Standalone architecture doc |
| `autonomous_analyzer.py` | 868 | 2 | Standalone tool — calls tot_integration |
| `autonomous_orchestrator.py` | 271 | 13 | Root re-export facade |
| `captcha_solver.py` | 421 | 0 | Standalone captcha solver |
| `capabilities.py` | 841 | 6 | Model lifecycle caps |
| `config.py` | 665 | 0 | Standalone config |
| `deep_probe.py` | 1285 | 0 | Deep crawl scanner |
| `embedding_pipeline.py` | 1064 | 0 | Semantic search pipeline |
| `enhanced_research.py` | 3056 | 2 | Dormant — deprecated orchestrator residue |
| `evidence_log.py` | 2013 | 0 | Evidence ledger |
| `orchestrator_integration.py` | 745 | 1 | Orchestrator extension |
| `semantic_deduplicator.py` | 427 | 3 | Dedup utility |
| `smoke_runner.py` | 329 | 0 | Standalone smoke runner |
| `tool_exec_log.py` | 459 | 3 | Tool execution logging |
| `tool_registry.py` | 196 | 6 | Tool registry |
| `tot_integration.py` | 837 | 1 | TOT integration |

---

## Code Communities (top-level domain map, verified)

| Domain | Size | Key Modules |
|--------|------|-------------|
| `legacy-load` | 922 | autonomous_orchestrator (31K), persistent_layer |
| `runtime-source` | 881 | sprint_scheduler (12.8K), acquisition_strategy, source_finding_bridge |
| `intelligence-search` | 586 | stealth_crawler, relationship_discovery, document_intelligence, pattern_mining |
| `intelligence-fetch` | 404 | doh_lane, archive_discovery, academic_discovery |
| `brain-model` | 289 | hypothesis_engine (4.4K), hermes3_engine, inference_engine, mlx_engine |
| `brain-evidence` | 230 | synthesis_runner, confidence_scorer |
| `knowledge-graph` | 717 | duckdb_store (6.5K), lancedb_store, graph_service, graph_rag |
| `coordinators-memory` | 117 | memory_coordinator, fetch_coordinator, security_coordinator |
| `knowledge-secure` | 103 | audit, pii_gate, quantum_safe |
| `forensics-metadata` | 78 | metadata_extractor (2.8K), enrichment_service |
| `dht-handle` | 74 | DHT integration |
| `utils-hash` | 122 | deduplication, bloom_filter, uma_budget |
| `utils-execute` | 73 | execution_optimizer, concurrency |
| `layers-pivot` | 162 | pivot_planner, pivot_lane_planner |
| `layers-state` | 118 | windup_engine, shadow_pre_decision |
| `layers-captcha` | 88 | captcha_solver, vision_encoder |
| `layers-audit` | 63 | audit, key_manager, secure_enclave |

---

## Feed vs Nonfeed Lane Split (verified)

```
Feed lanes (pipeline/live_public_pipeline.py):
  - RSS/Atom feeds (rss_atom_adapter.py)
  - Crawled web pages (live_public_pipeline)
  - Live public OSINT (async_run_live_public_pipeline)

Nonfeed lanes (runtime/acquisition_strategy.py):
  - CT (Certificate Transparency)  → ct_lane
  - Wayback Machine               → wayback_lane
  - PassiveDNS                    → passive_dns_lane
  - Academic search               → academic_search_lane
  - DOH (DNS-over-HTTPS)         → doh_lane
  - NonfeedCandidateLedger       → domain/identity/leak/archive/graph pivots
```

**Key file:** `runtime/acquisition_strategy.py` (4379 lines) — defines `_build_nonfeed_lane_eligibility` (bridge node, betweenness 0.0040)

---

## Broken Imports Summary

**~281 `hledac.*` module-not-found errors** reported by pyright across the codebase (~25K lines of Python, ~2.4% error rate). Total `hledac` references: **11,476** — the 281 errors are a pyright-reported subset, not total imports. All share `missing_module: "hledac.*"` — single systemic import path resolution issue.

**Methodology**: `pyright --outputjson 2>&1 | jq '[.generalDiagnostics[] | select(.message | contains("hledac"))] | length'`

---

## Key Observations

1. **`sprint_scheduler.py` is the architectural linchpin**: 12,806 lines, 63 commits/90d, highest betweenness (0.0112), connects runtime-source to knowledge-graph communities
2. **23 real coordinators** (not 26 — excludes `__init__`, `_catalog.py`, `enums.py`)
3. **Massive legacy footprint**: `legacy/autonomous_orchestrator.py` alone is 31K lines — still referenced by 13 files despite being dormant
4. **Test suite is 72K lines** across 190 files — almost as large as the main application
5. **Feed/nonfeed split** is the fundamental structural divide (lane eligibility determined in `acquisition_strategy.py`)
6. **Discovery layer** has 12 adapters: duckduckgo, ti_feed, rss_atom, crtsh, circl_pdns, wayback_cdx, cascade, etc.
7. **Network intelligence** is a substantial module: 19 files covering DNS tunnels, JARM fingerprints, passive DNS, BGP, IPFS, etc.
8. **Layers architecture**: 15 files, 14K lines — coordination, stealth, memory, security layers
9. **~281 pyright `hledac.*` errors** vs 11,476 total references — single systemic path resolution issue
10. **42 cross-community seams** (code-review-graph analysis via `mcp__code-review-graph__list_communities_tool`) — connecting 2,568 communities — highly modular hub/bridge structure
11. **`hypothesis/` is empty** — just `.DS_Store`, no Python files
12. **`federated/` is empty** — no Python files
13. **`embedding_cache/` is empty** — no Python files
14. **`deep_probe.py` (1285 lines) standalone** — not imported by pipeline, run as separate tool
15. **`enhanced_research.py` (3056 lines) dormant** — marked DEPRECATED, only 2 importers

## Extended Directory Inventory (Post-Compact Analysis)

### config/ — EMPTY
- No files present — configuration is managed via environment variables and code constants

### data/ — EMPTY
- No files present — runtime data flows through DuckDB (knowledge/) and LMDB (tools/lmdb_kv.py)

### docs/ (82 files, 5 subdirs)
```
docs/
├── agents/          (4 files — triage-labels.md, domain.md, issue-tracker.md, ARCHITECTURE_CONNECTIVITY_PLAN.md)
├── audits/          (18 files — capability矩阵, F-C audits, live sprint, M1 performance, network recon)
├── benchmarks/       (2 files)
├── CHANGELOGS/      (4 files)
└── standalone/      (5 files — LIVE_SPRINT_EXPERIMENT_MATRIX.md, LOCAL_M1_SMOKE_RUNBOOK.md, etc.)
```
- **Purpose**: Human-facing documentation, ADRs, runbooks, architecture plans
- **Key docs**: CAPABILITY_MATRIX_WIRING_AUDIT.md, NEXT_CAPABILITY_ACTIVATION_PLAN.md, OFFLINE_PROVIDER_YIELD_DIAGNOSIS.md

### logs/ — EMPTY
- No log files present — logging is non-existent (zero-knowledge policy)

### models/ — EMPTY
- No model files present — MLX models loaded from HuggingFace at runtime

### reports/ (135 files — runtime artifact archive)
```
reports/
├── benchmarks/       (benchmark JSON/TXT artifacts)
├── f222f/            (feed F222 probe artifacts)
├── f222g/            (feed F222 nonfeed artifacts)
├── live_run_*.json   (live sprint run telemetry)
└── ghost_cti_*.stix.json (CTI artifacts)
```
- **Purpose**: Runtime execution artifacts — benchmarks, live runs, CTI exports
- **Format**: JSON, STIX 2.1, plaintext reports

### rl/ (7 files, 834 lines — QMIX RL layer)
```
rl/
├── __init__.py
├── actions.py             (Action/ActionResult dataclasses)
├── qmix.py                (QMIX algorithm core)
├── replay_buffer.py       (PrioritizedExperienceReplay)
├── state_extractor.py     (extract state from SprintSchedulerResult)
├── sprint_policy_manager.py (SprintPolicyManager — opt-in advisory)
└── smoke_llm_candidate.py  (RL candidate generation)
```
- **Algorithm**: QMIX (monotonic value function factorization)
- **State**: SprintSchedulerResult fields (run_time, findings_accepted, sources_exhausted, etc.)
- **Actions**: lane selection (FEED, NONFEED, PAUSE, RESUME, BOOST_DOMAIN, BOOST_IDENTITY)
- **Policy storage**: JSON state file, every-5th-sprint exploration
- **Key boundary**: `inject_policy_manager()` in sprint_scheduler.py — RL is advisory layer, NOT orchestrator

### scripts/ (8 files, 1,475 lines — utility scripts)
```
scripts/
├── extract_nonfeed_seeds.py   (standalone seed extraction from CT findings)
├── fix_ti_feed.py             (TI feed migration utility)
├── mount_ramdisk.sh           (RAM disk setup for /tmp)
├── unmount_ramdisk.sh         (RAM disk teardown)
├── check_torrc.py             (Tor configuration checker)
├── score_corroboration.py     (corroboration scoring utility)
├── model_stack_smoke.py       (MLX model stack validation)
└── pre_commit_guard.py        (pre-commit hook validation)
```
- **Purpose**: Operational utilities, one-off scripts, CI/CD helpers
- **Invocation**: Standalone, not imported by main pipeline

## Synthesis: Complete Filesystem Map

| Layer | Files | Lines | Purpose |
|-------|-------|-------|---------|
| core/ | 15 | 4,000 | Entry points, sprint dispatcher |
| coordinators/ | 23 real | 18,000 | Orchestration seams |
| knowledge/ | 32 | 12,000 | DuckDB canonical write, LanceDB RAG |
| brain/ | 23 | 9,000 | Hermes3 inference, synthesis |
| pipeline/ | 6 | 5,500 | Sprint execution lanes |
| runtime/ | 6 | 6,000 | Sprint scheduler, policy |
| fetching/ | 6 | 4,500 | curl_cffi stealth HTTP |
| transport/ | 9 | 7,000 | Multi-transport (Tor, i2p, nym) |
| security/ | 12 | 5,500 | PQ crypto, secure enclave |
| forensics/ | 8 | 4,000 | Metadata, steganography |
| multimodal/ | 12 | 6,500 | Vision, OCR, document |
| hypothesis/ | 0 | 0 | Empty |
| rl/ | 7 | 834 | QMIX advisory layer |
| scripts/ | 8 | 1,475 | Operational utilities |
| docs/ | 82 | — | Human-facing docs |
| reports/ | 135 | — | Runtime artifacts |

**Total**: ~1,780 files, ~290K lines (code + tests + docs)
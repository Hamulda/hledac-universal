# Qoder Repowiki Reality Matrix

**Scanned**: 88 documents, 560 references, 518 unique modules (415 exist, 103 missing)

---

## Executive Summary

- **CANONICAL_OWNER**: 3 modules
- **ACTIVE_RUNTIME**: 157 modules
- **ACTIVE_PIPELINE**: 3 modules
- **ACTIVE_SIDECAR**: 21 modules
- **ACTIVE_DIAGNOSTIC**: 3 modules
- **SECURITY_CRITICAL**: 30 modules
- **STORAGE_AUTHORITY**: 38 modules
- **TRANSPORT_AUTHORITY**: 20 modules
- **DONOR**: 32 modules
- **LEGACY**: 6 modules
- **DEPRECATED**: 21 modules
- **TEST_ONLY**: 184 modules
- **DEAD_OR_UNWIRED**: 0 modules
- **MISSING_DOC_TARGET**: 0 modules
- **UNKNOWN_NEEDS_REVIEW**: 0 modules

---

## Canonical Hot Path Map

```
core/__main__.py (CANONICAL_OWNER)
  └── run_sprint()
        ├── runtime/sprint_scheduler.py (ACTIVE_RUNTIME)
        │     ├── runtime/sprint_lifecycle.py
        │     ├── runtime/sprint_lifecycle_runner.py
        │     ├── runtime/sprint_advisory_runner.py (ACTIVE_SIDECAR)
        │     ├── runtime/sidecar_bus.py (ACTIVE_SIDECAR)
        │     ├── runtime/sidecar_dispatcher.py (ACTIVE_SIDECAR)
        │     └── runtime/shadow_*.py (ACTIVE_DIAGNOSTIC)
        ├── knowledge/duckdb_store.py (STORAGE_AUTHORITY)
        ├── knowledge/semantic_store.py (STORAGE_AUTHORITY)
        ├── export/sprint_exporter.py
        └── pipeline/live_public_pipeline.py (ACTIVE_PIPELINE)
              └── pipeline/live_feed_pipeline.py (ACTIVE_PIPELINE)
```

---

## Active Runtime Modules

- `brain/__init__.py` — Project Overview/Architecture Overview.md
- `brain/ane_embedder.py` — Brain Engines/Distillation Engine.md
- `brain/apple_fm_probe.py` — Testing and Quality Assurance/Probe Testing System/Probe Testing System.md
- `brain/decision_engine.py` — API Reference/Brain APIs.md
- `brain/distillation_engine.py` — Brain Engines/Distillation Engine.md
- `brain/dynamic_model_manager.py` — Brain Engines/Brain Engines.md
- `brain/fetch/coordinators` — Testing and Quality Assurance/Integration and End-to-End Testing.md
- `brain/hermes3_engine.py` — API Reference/Brain APIs.md
- `brain/inference_engine.py` — API Reference/Brain APIs.md
- `brain/model_lifecycle.py` — Brain Engines/Brain Engines.md
- `brain/model_manager.py` — API Reference/Brain APIs.md
- `brain/model_swap_manager.py` — Brain Engines/Brain Engines.md
- `brain/prompt_cache.py` — Utilities and Helpers/MLX Integration.md
- `brain/research_flow_decider.py` — API Reference/Brain APIs.md
- `cache/budget_manager.py` — Runtime Management/Resource Governance.md
- `deep_research/probe_runner.py` — Testing and Quality Assurance/Probe Testing System/Probe Testing System.md
- `discovery/circuits.` — Testing and Quality Assurance/Probe Testing System/Specialized Domain Probes.md
- `discovery/duckduckgo_adapter.py` — Pipeline System/Live Public Pipeline.md
- `discovery/fetch/quality` — Project Overview/Project Overview.md
- `discovery/rss_atom_adapter.py` — Pipeline System/Live Feed Pipeline.md
- `fetching/public_fetcher.py` — Pipeline System/Live Feed Pipeline.md
- `forensics/digital_ghost_detector.py` — Security and Privacy/Stealth Operations.md
- `forensics/steganography_detector.py` — Security and Privacy/Stealth Operations.md
- `intelligence/__init__.py` — Intelligence Modules/Document Intelligence.md
- `intelligence/__init__.py#L1-686` — Intelligence Modules/Intelligence Modules.md
- `intelligence/__init__.py#L25-422` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_discovery.py` — Intelligence Modules/Academic Intelligence.md
- `intelligence/academic_discovery.py#L1-301` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_discovery.py#L77-122` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py` — Intelligence Modules/Academic Intelligence.md
- `intelligence/academic_search.py#L1-1369` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L133-159` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L231-273` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L35-48` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L787-1369` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L79-94` — Intelligence Modules/Intelligence Modules.md
- `intelligence/academic_search.py#L795-800` — Intelligence Modules/Intelligence Modules.md
- `intelligence/attribution_scorer.py` — Intelligence Modules/Social Identity Mining.md
- `intelligence/blockchain_analyzer.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/cryptographic_intelligence.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/dark_web_intelligence.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/data_leak_hunter.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/decision_engine.py` — Brain Engines/Decision Engine.md
- `intelligence/decision_engine.py#L1-4` — Intelligence Modules/Intelligence Modules.md
- `intelligence/document_intelligence.py` — Intelligence Modules/Document Intelligence.md
- `intelligence/document_intelligence.py#L1-2125` — Intelligence Modules/Intelligence Modules.md
- `intelligence/document_intelligence.py#L259-599` — Intelligence Modules/Intelligence Modules.md
- `intelligence/document_intelligence.py#L278-351` — Intelligence Modules/Intelligence Modules.md
- `intelligence/document_intelligence.py#L45-113` — Intelligence Modules/Intelligence Modules.md
- `intelligence/document_intelligence.py#L45-94` — Intelligence Modules/Intelligence Modules.md
- `intelligence/entity_signal_extractor.py` — Intelligence Modules/Social Identity Mining.md
- `intelligence/exposed_service_hunter.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/github_secret_scanner.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/identity_stitching.py` — Intelligence Modules/Social Identity Mining.md
- `intelligence/identity_stitching_canonical.py` — Intelligence Modules/Social Identity Mining.md
- `intelligence/input_detector.py` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L1-954` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L162-183` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L190-954` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L23-31` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L241-272` — Intelligence Modules/Intelligence Modules.md
- `intelligence/input_detector.py#L429-544` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py#L1-365` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py#L22-27` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py#L29-151` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py#L29-247` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_intelligence.py#L55-179` — Intelligence Modules/Intelligence Modules.md
- `intelligence/network_reconnaissance.py` — Intelligence Modules/Network Intelligence.md
- `intelligence/onion_seed_manager.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/passive_fingerprint.py` — Intelligence Modules/Network Intelligence.md
- `intelligence/pastebin_monitor.py` — Intelligence Modules/Specialized Intelligence Modules.md
- `intelligence/rir_correlator.py` — Intelligence Modules/Network Intelligence.md
- `intelligence/shodan_wrapper.py` — Intelligence Modules/Network Intelligence.md
- `intelligence/social_identity_miner.py` — Intelligence Modules/Intelligence Modules.md
- `intelligence/social_identity_miner.py#L1-577` — Intelligence Modules/Intelligence Modules.md
- `intelligence/social_identity_miner.py#L187-299` — Intelligence Modules/Intelligence Modules.md
- `intelligence/social_identity_miner.py#L187-577` — Intelligence Modules/Intelligence Modules.md
- `intelligence/social_identity_miner.py#L23-33` — Intelligence Modules/Intelligence Modules.md
- `intelligence/social_identity_miner.py#L36-40` — Intelligence Modules/Intelligence Modules.md
- `intelligence/stealth_crawler.py` — Security and Privacy/Stealth Operations.md
- `intelligence/streaming_embedder.py` — Intelligence Modules/Document Intelligence.md
- `intelligence/temporal_analysis.py` — Intelligence Modules/Intelligence Workflow Orchestration.md
- `intelligence/temporal_archaeologist.py` — Intelligence Modules/Intelligence Workflow Orchestration.md
- `intelligence/temporal_archaeologist_adapter.py` — Intelligence Modules/Intelligence Workflow Orchestration.md
- `intelligence/ti_feed_adapter.py` — Intelligence Modules/Intelligence Workflow Orchestration.md
- `intelligence/timeline_synthesizer.py` — Intelligence Modules/Intelligence Workflow Orchestration.md
- `intelligence/web_intelligence.py` — Intelligence Modules/Intelligence Modules.md
- `intelligence/web_intelligence.py#L1-1075` — Intelligence Modules/Intelligence Modules.md
- `intelligence/web_intelligence.py#L115-800` — Intelligence Modules/Intelligence Modules.md
- `intelligence/web_intelligence.py#L131-199` — Intelligence Modules/Intelligence Modules.md
- `intelligence/web_intelligence.py#L344-427` — Intelligence Modules/Intelligence Modules.md
- `intelligence/web_intelligence.py#L35-47` — Intelligence Modules/Intelligence Modules.md
- `intelligence/workflow_orchestrator.py` — Intelligence Modules/Intelligence Modules.md
- `intelligence/workflow_orchestrator.py#L1-1849` — Intelligence Modules/Intelligence Modules.md
- `intelligence/workflow_orchestrator.py#L315-333` — Intelligence Modules/Intelligence Modules.md
- `intelligence/workflow_orchestrator.py#L335-800` — Intelligence Modules/Intelligence Modules.md
- `intelligence/workflow_orchestrator.py#L385-466` — Intelligence Modules/Intelligence Modules.md
- `memory/memory_manager.py` — Pipeline System/Live Public Pipeline.md
- `memory/performance.` — Testing and Quality Assurance/Probe Testing System/Test Execution and Orchestration.md
- `memory/swap` — Getting Started.md
- `memory/temp` — Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Basic Functionality Probes (0a-1b).md
- `memory/thermal` — Testing and Quality Assurance/Testing and Quality Assurance.md
- `multimodal/analyzer.py` — Intelligence Modules/Document Intelligence.md
- `network/TOR` — API Reference/Core APIs.md
- `network/TOR/proxy` — Deployment and Operations.md
- `network/ct_log_scanner.py` — Pipeline System/Live Public Pipeline.md
- `network/disk` — Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Basic Functionality Probes (0a-1b).md
- `network/model` — Export and Reporting/STIX Export.md
- `network/session_runtime.py` — API Reference/Transport APIs.md
- `network/tor_manager.py` — API Reference/Transport APIs.md
- `patterns/pattern_matcher.py` — Core Architecture/Component Relationships and Data Flow.md
- `research/task_prioritizer.py` — Utilities and Helpers/Execution Optimization.md
- `runtime/hypothesis_feedback.py` — Runtime Management/Advisory Functions.md
- `runtime/memory_authority.py` — Core Architecture/Authority Model and Entry Points/Authority Model and Entry Points.md
- `runtime/opsec_policy.py` — Runtime Management/Runtime Management.md
- `runtime/pivot_executor.py` — Runtime Management/Advisory Functions.md
- `runtime/pivot_planner.py` — Runtime Management/Advisory Functions.md
- `runtime/resource_governor.py` — Runtime Management/Advisory Functions.md
- `runtime/sprint_lifecycle.py` — API Reference/Core APIs.md
- `runtime/sprint_lifecycle_runner.py` — Pipeline System/Pipeline Orchestration.md
- `runtime/sprint_scheduler` — Project Overview/Project Overview.md
- `runtime/sprint_scheduler.py` — API Reference/Core APIs.md
- `runtime/telemetry.py` — Deployment and Operations.md
- `runtime/utils` — Runtime Management/Runtime Management.md
- `utils/__init__.py` — Utilities and Helpers/Async Utilities.md
- `utils/async_helpers.py` — Utilities and Helpers/Async Utilities.md
- `utils/async_utils.py` — Utilities and Helpers/Async Utilities.md
- `utils/capability_prober.py` — Core Architecture/Boot Sequence and Initialization.md
- `utils/concurrency.py` — Runtime Management/Resource Governance.md
- `utils/config.py` — Utilities and Helpers/System Helpers.md
- `utils/deduplication.py` — Intelligence Modules/Academic Intelligence.md
- `utils/encryption.py` — Security and Privacy/Encryption Framework.md
- `utils/exceptions.py` — Utilities and Helpers/Async Utilities.md
- `utils/execution_optimizer.py` — Utilities and Helpers/Execution Optimization.md
- `utils/flow_trace.py` — Utilities and Helpers/Performance Monitoring.md
- `utils/intelligent_cache.py` — Utilities and Helpers/Utilities and Helpers.md
- `utils/lazy_imports.py` — Utilities and Helpers/Execution Optimization.md
- `utils/memory_dashboard.py` — Runtime Management/Resource Governance.md
- `utils/mlx_cache` — Brain Engines/Hermes3 Engine.md
- `utils/mlx_cache.py` — Brain Engines/Hermes3 Engine.md
- `utils/mlx_memory` — Brain Engines/Hermes3 Engine.md
- `utils/mlx_memory.py` — Brain Engines/Hermes3 Engine.md
- `utils/mlx_prompt_cache.py` — Utilities and Helpers/MLX Integration.md
- `utils/mlx_utils.py` — Utilities and Helpers/MLX Integration.md
- `utils/optimize_imports.py` — Utilities and Helpers/Execution Optimization.md
- `utils/performance_monitor.py` — Deployment and Operations.md
- `utils/predictive_planner.py` — Utilities and Helpers/Utilities and Helpers.md
- `utils/query_expansion.py` — Intelligence Modules/Academic Intelligence.md
- `utils/signpost_profiler.py` — Runtime Management/Resource Governance.md
- `utils/sprint_lifecycle.py` — Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Advanced Functionality Probes (4a-5b).md
- `utils/thermal.py` — Runtime Management/Resource Governance.md
- `utils/thread_pools.py` — Utilities and Helpers/System Helpers.md
- `utils/uma_budget.py` — Runtime Management/Resource Governance.md
- `utils/validation.py` — Testing and Quality Assurance/Quality Gates and Validation.md
- `utils/worker_pool.py` — Utilities and Helpers/System Helpers.md
- `utils/workflow_engine.py` — Core Architecture/Design Patterns and Architectural Principles.md

## Active Pipeline Modules

- `pipeline/__init__.py` — 1 doc(s)
- `pipeline/live_feed_pipeline.py` — 7 doc(s)
- `pipeline/live_public_pipeline.py` — 8 doc(s)

## Active Sidecar Modules

- `coordinators/base.py`
- `coordinators/benchmark_coordinator.py`
- `coordinators/execution_coordinator.py`
- `coordinators/fetch_coordinator.py`
- `coordinators/graph_coordinator.py`
- `coordinators/monitoring_coordinator.py`
- `coordinators/performance_coordinator.py`
- `coordinators/privacy_enhanced_research.py`
- `coordinators/research_coordinator.py`
- `coordinators/research_optimizer.py`
- `coordinators/resource_allocator.py`
- `coordinators/security_coordinator.py`
- `coordinators/validation_coordinator.py`
- `monitoring/sprint_dashboard.py`
- `runtime/sidecar_bus.py`
- `runtime/sidecar_dispatcher.py`
- `runtime/sprint_advisory_runner.py`
- `tools/commoncrawl_adapter.py`
- `tools/darknet.py`
- `tools/lmdb_kv.py`
- `tools/metadata_dedup.py`

## Active Diagnostic Modules

- `runtime/shadow_inputs.py`
- `runtime/shadow_parity.py`
- `runtime/shadow_pre_decision.py`

## Security-Critical Modules

- `security/__init__.py` — no risks noted
- `security/audit.py` — no risks noted
- `security/destruction.py` — no risks noted
- `security/digital_ghost_detector.py` — no risks noted
- `security/encryption.py` — no risks noted
- `security/key_manager.py` — PRIVATE_KEY_OR_SECRET_EXPORT_RISK: handles sensitive material in export envelopes
- `security/obfuscation.py` — no risks noted
- `security/passive_dns.py` — no risks noted
- `security/pii_gate.py` — no risks noted
- `security/pq_export_encryption.py` — PRIVATE_KEY_OR_SECRET_EXPORT_RISK: handles sensitive material in export envelopes
- `security/pq_export_encryption_swift.py` — PRIVATE_KEY_OR_SECRET_EXPORT_RISK: handles sensitive material in export envelopes
- `security/privacy` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `security/privacy/stealth` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `security/quantum_safe.py` — no risks noted
- `security/ram_vault.py` — no risks noted
- `security/self_healing.py` — no risks noted
- `security/stego_detector.py` — no risks noted
- `security/vault_manager.py` — no risks noted
- `stealth/anonymize` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/js` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_manager.py` — no risks noted
- `stealth/stealth_manager.py#L103-122` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_manager.py#L147-337` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_manager.py#L261-274` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_manager.py#L85-337` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_session.py` — no risks noted
- `stealth/stealth_session.py#L367-803` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_session.py#L388-448` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_session.py#L500-518` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo
- `stealth/stealth_session.py#L519-684` — MISSING_DOC_TARGET: file referenced in docs but does not exist in repo

## Storage Authority Modules

- `export/COMPAT_DEBT_LEDGER.md`
- `export/COMPAT_HANDOFF.py`
- `export/EXPORT_PLANE_MAP.md`
- `export/__init__.py`
- `export/export`
- `export/export_manager.py`
- `export/import`
- `export/import.`
- `export/jsonld_exporter.py`
- `export/markdown_reporter.py`
- `export/reporting`
- `export/sprint_exporter.py`
- `export/sprint_markdown_reporter.py`
- `export/stix_exporter.py`
- `export/validation`
- `graph/context`
- `graph/graph_manager.py`
- `graph/semantic`
- `knowledge/__init__.py`
- `knowledge/analyst_workbench.py`
- `knowledge/ann_index.py`
- `knowledge/context_graph.py`
- `knowledge/duckdb_store.py`
- `knowledge/entity_linker.py`
- `knowledge/evidence_chain.py`
- `knowledge/graph`
- `knowledge/graph_builder.py`
- `knowledge/graph_layer.py`
- `knowledge/graph_rag.py`
- `knowledge/graph_service.py`
- `knowledge/ioc_graph.py`
- `knowledge/lancedb_store.py`
- `knowledge/lmdb_boot_guard.py`
- `knowledge/pq_index.py`
- `knowledge/rag_engine.py`
- `knowledge/semantic_store.py`
- `knowledge/target_memory.py`
- `knowledge/vector_store.py`

## Transport Authority Modules

- `transport/__init__.py`
- `transport/base.py`
- `transport/base.py#L4-24`
- `transport/circuit_breaker.py`
- `transport/curl_cffi_fetch.py`
- `transport/curl_cffi_runtime.py`
- `transport/curl_cffi_transport.py`
- `transport/httpx_client.py`
- `transport/httpx_transport.py`
- `transport/i2p_transport.py`
- `transport/inmemory_transport.py`
- `transport/nym_transport.py`
- `transport/session`
- `transport/tor_transport.py`
- `transport/transport_resolver.py`
- `transport/transport_resolver.py#L152-175`
- `transport/transport_resolver.py#L187-239`
- `transport/transport_resolver.py#L268-301`
- `transport/transport_resolver.py#L69-85`
- `transport/transport_resolver.py#L95-322`

## Donor / Legacy / Deprecated Modules


### DONOR (32)
- `graph/quantum_pathfinder.py` — 1 doc(s)
- `infrastructure/plugin_manager.py` — 3 doc(s)
- `infrastructure/system_monitor.py` — 2 doc(s)
- `layers/__init__.py` — 2 doc(s)
- `layers/communication_layer.py` — 1 doc(s)
- `layers/content_layer.py` — 1 doc(s)
- `layers/layer_manager` — 1 doc(s)
- `layers/layer_manager.py` — 2 doc(s)
- `layers/memory_layer.py` — 1 doc(s)
- `layers/privacy_layer.py` — 2 doc(s)
- `layers/research_layer.py` — 1 doc(s)
- `layers/security_layer.py` — 2 doc(s)
- `layers/stealth_layer.py` — 2 doc(s)
- `layers/temporal_signal_layer.py` — 3 doc(s)
- `layers/temporal_signal_layer.py#L137-167` — 1 doc(s)
- `layers/temporal_signal_layer.py#L137-691` — 1 doc(s)
- `layers/temporal_signal_layer.py#L170-378` — 1 doc(s)
- `layers/temporal_signal_layer.py#L336-354` — 1 doc(s)
- `layers/temporal_signal_runtime.py` — 1 doc(s)
- `layers/temporal_signal_runtime.py#L134-289` — 1 doc(s)
  ... and 12 more

### LEGACY (6)
- `execution/ghost_executor.py` — 1 doc(s)
- `legacy/persistent_layer.py` — 1 doc(s)
- `orchestrator/global_scheduler.py` — 2 doc(s)
- `orchestrator/memory_pressure_broker.py` — 1 doc(s)
- `orchestrator/phase_controller.py` — 3 doc(s)
- `orchestrator/security_manager.py` — 1 doc(s)

### DEPRECATED (21)
- `GHOST_INVARIANTS.md` — 1 doc(s)
- `LONGTERM_PLAN.md` — 1 doc(s)
- `REAL_ARCHITECTURE.md` — 3 doc(s)
- `__main__.py` — 7 doc(s)
- `autonomous_orchestrator.py` — 2 doc(s)
- `capabilities.py` — 2 doc(s)
- `config.py` — 2 doc(s)
- `deep_probe.py` — 3 doc(s)
- `embedding_pipeline.py` — 3 doc(s)
- `enhanced_research.py` — 2 doc(s)
- `metrics_registry.py` — 1 doc(s)
- `paths.py` — 16 doc(s)
- `project_types.py` — 7 doc(s)
- `pytest.ini` — 4 doc(s)
- `requirements-optional.txt` — 3 doc(s)
- `requirements.txt` — 6 doc(s)
- `research_context.py` — 1 doc(s)
- `semantic_deduplicator.py` — 1 doc(s)
- `smoke_runner.py` — 3 doc(s)
- `tool_registry.py` — 2 doc(s)
  ... and 1 more

## Missing Documentation Targets

- `brain/fetch/coordinators` — referenced by 1 doc(s)
- `discovery/circuits.` — referenced by 1 doc(s)
- `discovery/fetch/quality` — referenced by 1 doc(s)
- `export/export` — referenced by 1 doc(s)
- `export/import` — referenced by 3 doc(s)
- `export/import.` — referenced by 1 doc(s)
- `export/reporting` — referenced by 2 doc(s)
- `export/validation` — referenced by 1 doc(s)
- `graph/context` — referenced by 1 doc(s)
- `graph/semantic` — referenced by 2 doc(s)
- `intelligence/__init__.py#L1-686` — referenced by 1 doc(s)
- `intelligence/__init__.py#L25-422` — referenced by 1 doc(s)
- `intelligence/academic_discovery.py#L1-301` — referenced by 1 doc(s)
- `intelligence/academic_discovery.py#L77-122` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L1-1369` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L133-159` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L231-273` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L35-48` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L787-1369` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L79-94` — referenced by 1 doc(s)
- `intelligence/academic_search.py#L795-800` — referenced by 1 doc(s)
- `intelligence/decision_engine.py#L1-4` — referenced by 1 doc(s)
- `intelligence/document_intelligence.py#L1-2125` — referenced by 1 doc(s)
- `intelligence/document_intelligence.py#L259-599` — referenced by 1 doc(s)
- `intelligence/document_intelligence.py#L278-351` — referenced by 1 doc(s)
- `intelligence/document_intelligence.py#L45-113` — referenced by 1 doc(s)
- `intelligence/document_intelligence.py#L45-94` — referenced by 1 doc(s)
- `intelligence/input_detector.py#L1-954` — referenced by 1 doc(s)
- `intelligence/input_detector.py#L162-183` — referenced by 1 doc(s)
- `intelligence/input_detector.py#L190-954` — referenced by 1 doc(s)

## Unknown / Needs Review


## Overclaims

- **[MEDIUM]** `API Reference/API Reference.md`: Uses 'canonical' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/API Reference.md`: Uses 'production' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/API Reference.md`: Uses 'wired' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Brain APIs.md`: Uses 'canonical' language but module is TEST_ONLY
  → Referenced path: `brain/.` (verdict: TEST_ONLY)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'canonical' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'canonical' language but module is DONOR
  → Referenced path: `orchestrator_integration.py` (verdict: DONOR)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'canonical' language but module is DEPRECATED
  → Referenced path: `autonomous_orchestrator.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'production' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'production' language but module is DONOR
  → Referenced path: `orchestrator_integration.py` (verdict: DONOR)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'production' language but module is DEPRECATED
  → Referenced path: `autonomous_orchestrator.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'wired' language but module is DEPRECATED
  → Referenced path: `__main__.py` (verdict: DEPRECATED)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'wired' language but module is DONOR
  → Referenced path: `orchestrator_integration.py` (verdict: DONOR)
- **[MEDIUM]** `API Reference/Core APIs.md`: Uses 'wired' language but module is DEPRECATED
  → Referenced path: `autonomous_orchestrator.py` (verdict: DEPRECATED)
- **[MEDIUM]** `Brain Engines/Decision Engine.md`: Uses 'canonical' language but module is DONOR
  → Referenced path: `loops/research_loop.py` (verdict: DONOR)
- **[MEDIUM]** `Brain Engines/Distillation Engine.md`: Uses 'production' language but module is DEPRECATED
  → Referenced path: `paths.py` (verdict: DEPRECATED)
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'canonical' language but module is TEST_ONLY
  → Referenced path: `tests/test_sprint75/test_speculative_decoding.py` (verdict: TEST_ONLY)
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'canonical' language but module is TEST_ONLY
  → Referenced path: `tests/test_8c/test_lifecycle_convergence.py` (verdict: TEST_ONLY)
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'canonical' language but module is TEST_ONLY
  → Referenced path: `tests/probe_7i/test_sprint_7i.py` (verdict: TEST_ONLY)
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'canonical' language but module is TEST_ONLY
  → Referenced path: `tests/probe_7e/test_batch_batcher_7e.py` (verdict: TEST_ONLY)
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'production' language but module is TEST_ONLY
  → Referenced path: `tests/test_sprint75/test_speculative_decoding.py` (verdict: TEST_ONLY)

## High-Risk Gaps


### [MEDIUM] MULTIPLE_MEMORY_AUTHORITIES
**Recommended Sprint**: F206AI
**Affected paths**:
  - `knowledge/duckdb_store.py`
  - `utils/memory_dashboard.py`
  - `knowledge/target_memory.py`
  - `layers/memory_layer.py`
  - `memory/memory_manager.py`
  - `memory/performance.`
  - `memory/swap`
  - `memory/temp`
  - `memory/thermal`
  - `orchestrator/memory_pressure_broker.py`

### [MEDIUM] UNDOCUMENTED_WRITE_PATH
**Recommended Sprint**: F206AI
**Affected paths**:
  - `graph/quantum_pathfinder.py`
  - `layers/__init__.py`
  - `layers/layer_manager.py`
  - `infrastructure/plugin_manager.py`
  - `infrastructure/system_monitor.py`

## Verdict Breakdown

- **TEST_ONLY**: 184
- **ACTIVE_RUNTIME**: 157
- **STORAGE_AUTHORITY**: 38
- **DONOR**: 32
- **SECURITY_CRITICAL**: 30
- **DEPRECATED**: 21
- **ACTIVE_SIDECAR**: 21
- **TRANSPORT_AUTHORITY**: 20
- **LEGACY**: 6
- **CANONICAL_OWNER**: 3
- **ACTIVE_PIPELINE**: 3
- **ACTIVE_DIAGNOSTIC**: 3

---

## Recommended Sprint Queue

- **F206AH**: Security-critical gaps: private key export, hardcoded helper paths, subprocess spawn
- **F206AI**: Memory authority audit, undocumented write paths
- **F206AJ**: Legacy/deprecated cleanup, donor module wiring decisions
# Qoder Repowiki Reality Matrix

**Scanned**: 88 documents, 560 references, 518 unique modules (415 exist, 103 missing)

---

## Executive Summary

- **CANONICAL_OWNER**: 1 modules
- **ACTIVE_RUNTIME**: 11 modules
- **ACTIVE_PIPELINE**: 2 modules
- **ACTIVE_SIDECAR**: 30 modules
- **ACTIVE_DIAGNOSTIC**: 3 modules
- **ACTIVE_SUPPORT**: 4 modules
- **ACTIVE_CAPABILITY**: 139 modules
- **ACTIVE_ENTRYPOINT**: 1 modules
- **PATH_AUTHORITY**: 1 modules
- **DONOR_OR_OPTIONAL**: 28 modules
- **SECURITY_CRITICAL**: 30 modules
- **STORAGE_AUTHORITY**: 38 modules
- **TRANSPORT_AUTHORITY**: 20 modules
- **DONOR**: 0 modules
- **LEGACY**: 6 modules
- **DEPRECATED**: 17 modules
- **TEST_ONLY**: 184 modules
- **DEAD_OR_UNWIRED**: 3 modules
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

- `cache/budget_manager.py` — Runtime Management/Resource Governance.md
- `deep_research/probe_runner.py` — Testing and Quality Assurance/Probe Testing System/Probe Testing System.md
- `memory/memory_manager.py` — Pipeline System/Live Public Pipeline.md
- `memory/performance.` — Testing and Quality Assurance/Probe Testing System/Test Execution and Orchestration.md
- `memory/swap` — Getting Started.md
- `memory/temp` — Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Basic Functionality Probes (0a-1b).md
- `memory/thermal` — Testing and Quality Assurance/Testing and Quality Assurance.md
- `research/task_prioritizer.py` — Utilities and Helpers/Execution Optimization.md
- `runtime/sprint_lifecycle.py` — API Reference/Core APIs.md
- `runtime/sprint_lifecycle_runner.py` — Pipeline System/Pipeline Orchestration.md
- `runtime/sprint_scheduler.py` — API Reference/Core APIs.md

## Active Pipeline Modules

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
- `runtime/hypothesis_feedback.py`
- `runtime/memory_authority.py`
- `runtime/opsec_policy.py`
- `runtime/pivot_executor.py`
- `runtime/pivot_planner.py`
- `runtime/resource_governor.py`
- `runtime/sidecar_bus.py`
- `runtime/sidecar_dispatcher.py`
- `runtime/sprint_advisory_runner.py`
- `runtime/sprint_scheduler`
- `runtime/telemetry.py`
- `runtime/utils`
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


### LEGACY (6)
- `execution/ghost_executor.py` — 1 doc(s)
- `legacy/persistent_layer.py` — 1 doc(s)
- `orchestrator/global_scheduler.py` — 2 doc(s)
- `orchestrator/memory_pressure_broker.py` — 1 doc(s)
- `orchestrator/phase_controller.py` — 3 doc(s)
- `orchestrator/security_manager.py` — 1 doc(s)

### DEPRECATED (17)
- `GHOST_INVARIANTS.md` — 1 doc(s)
- `LONGTERM_PLAN.md` — 1 doc(s)
- `REAL_ARCHITECTURE.md` — 3 doc(s)
- `autonomous_orchestrator.py` — 2 doc(s)
- `capabilities.py` — 2 doc(s)
- `deep_probe.py` — 3 doc(s)
- `embedding_pipeline.py` — 3 doc(s)
- `enhanced_research.py` — 2 doc(s)
- `metrics_registry.py` — 1 doc(s)
- `pytest.ini` — 4 doc(s)
- `requirements-optional.txt` — 3 doc(s)
- `requirements.txt` — 6 doc(s)
- `research_context.py` — 1 doc(s)
- `semantic_deduplicator.py` — 1 doc(s)
- `smoke_runner.py` — 3 doc(s)
- `tool_registry.py` — 2 doc(s)
- `tot_integration.py` — 1 doc(s)

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


## Overclaims (Grouped)

**Total overclaims**: 122 groups affecting ~337 module references

- **[HIGH]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md`: Uses 'canonical' language but module is DONOR_OR_OPTIONAL
  → `15` affected modules, examples: `layers/stealth_layer.py`, `layers/temporal_signal_layer.py`, `layers/temporal_signal_store.py`
- **[HIGH]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md`: Uses 'production' language but module is DONOR_OR_OPTIONAL
  → `15` affected modules, examples: `layers/stealth_layer.py`, `layers/temporal_signal_layer.py`, `layers/temporal_signal_store.py`
- **[HIGH]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Benchmark and Performance Probes.md`: Uses 'canonical' language but module is TEST_ONLY
  → `12` affected modules, examples: `benchmarks/benchmark_pipeline.py`, `benchmarks/benchmark_sprint_probe.py`, `benchmarks/e2e_canonical_benchmark.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Capability Probes (6b-7i).md`: Uses 'canonical' language but module is TEST_ONLY
  → `10` affected modules, examples: `tests/probe_6b/test_apple_fm_probe.py`, `tests/probe_6b/test_mlx_cache_limits.py`, `tests/probe_6b/test_qos_constants.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Capability Probes (6b-7i).md`: Uses 'wired' language but module is TEST_ONLY
  → `10` affected modules, examples: `tests/probe_6b/test_apple_fm_probe.py`, `tests/probe_6b/test_mlx_cache_limits.py`, `tests/probe_6b/test_qos_constants.py`
- **[MEDIUM]** `Testing and Quality Assurance/Integration and End-to-End Testing.md`: Uses 'canonical' language but module is TEST_ONLY
  → `9` affected modules, examples: `tests/conftest.py`, `tests/test_e2e_pipeline.py`, `tests/test_e2e_first_finding.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Testing System.md`: Uses 'canonical' language but module is TEST_ONLY
  → `9` affected modules, examples: `tests/probe_0a/test_sprint_0a.py`, `tests/probe_1a/test_sprint_1a.py`, `tests/probe_8a/test_sprint_8a.py`
- **[MEDIUM]** `Testing and Quality Assurance/Testing and Quality Assurance.md`: Uses 'canonical' language but module is TEST_ONLY
  → `9` affected modules, examples: `tests/conftest.py`, `tests/PHASE_GATES.py`, `tests/probe_8ab/conftest.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Basic Functionality Probes (0a-1b).md`: Uses 'canonical' language but module is TEST_ONLY
  → `8` affected modules, examples: `tests/probe_0a/test_sprint_0a.py`, `tests/probe_0a/REPORT_0A.md`, `tests/probe_1a/test_sprint_1a.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md`: Uses 'canonical' language but module is TEST_ONLY
  → `8` affected modules, examples: `tests/probe_temporal_signal_layer/test_temporal_signal_layer.py`, `probe_e2e_readiness/`, `probe_transport_cap_2026/`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md`: Uses 'production' language but module is TEST_ONLY
  → `8` affected modules, examples: `tests/probe_temporal_signal_layer/test_temporal_signal_layer.py`, `probe_e2e_readiness/`, `probe_transport_cap_2026/`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Performance and Benchmark Probes.md`: Uses 'canonical' language but module is TEST_ONLY
  → `7` affected modules, examples: `benchmarks/benchmark_pipeline.py`, `benchmarks/research_effectiveness.py`, `benchmarks/bench_8c0/common_stats.py`
- **[MEDIUM]** `Testing and Quality Assurance/Benchmark and Performance Testing.md`: Uses 'canonical' language but module is TEST_ONLY
  → `6` affected modules, examples: `benchmarks/benchmark_pipeline.py`, `benchmarks/benchmark_sprint_probe.py`, `benchmarks/e2e_canonical_benchmark.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Test Execution and Orchestration.md`: Uses 'canonical' language but module is TEST_ONLY
  → `6` affected modules, examples: `tests/conftest.py`, `tests/PHASE_GATES.py`, `run_baseline.py`
- **[MEDIUM]** `Transport and Networking/Circuit Breaker.md`: Uses 'production' language but module is TEST_ONLY
  → `6` affected modules, examples: `tests/probe_8vb/test_cb_opens_after_threshold.py`, `tests/probe_8vb/test_cb_half_open_after_recovery.py`, `tests/probe_8vb/test_cb_timeout_doubles_recovery.py`
- **[MEDIUM]** `Utilities and Helpers/MLX Integration.md`: Uses 'canonical' language but module is TEST_ONLY
  → `6` affected modules, examples: `benchmarks/m1_embedding_streaming.py`, `benchmarks/m1_phase4_budget.py`, `tests/probe_1b/test_mlx_memory.py`
- **[MEDIUM]** `Utilities and Helpers/MLX Integration.md`: Uses 'wired' language but module is TEST_ONLY
  → `6` affected modules, examples: `benchmarks/m1_embedding_streaming.py`, `benchmarks/m1_phase4_budget.py`, `tests/probe_1b/test_mlx_memory.py`
- **[MEDIUM]** `Core Architecture/Authority Model and Entry Points/Boot Hygiene and Teardown Management.md`: Uses 'canonical' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/test_sprint8an_hygiene.py`, `tests/probe_6a/test_async_hygiene.py`, `tests/probe_8vd/test_preflight_returns_dict.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Integration and End-to-End Probes.md`: Uses 'canonical' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/live_8be/test_live_searxng_8be.py`, `tests/live_8be/FINAL_REPORT_8BE.md`, `probe_e2e_readiness/e2e_signal_fixture_baseline.json`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Integration and End-to-End Probes.md`: Uses 'production' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/live_8be/test_live_searxng_8be.py`, `tests/live_8be/FINAL_REPORT_8BE.md`, `probe_e2e_readiness/e2e_signal_fixture_baseline.json`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Integration and Validation Probes (2a-3d).md`: Uses 'canonical' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/probe_2a/test_sprint_2a.py`, `tests/probe_2b/test_sprint_2b.py`, `tests/probe_3b/probe_3b.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Production Readiness Probes (8a-8z).md`: Uses 'canonical' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/probe_8b/test_sprint_8b.py`, `tests/probe_8az/test_sprint_8az.py`, `tests/probe_8aa/test_sprint_8aa.py`
- **[MEDIUM]** `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Production Readiness Probes (8a-8z).md`: Uses 'production' language but module is TEST_ONLY
  → `5` affected modules, examples: `tests/probe_8b/test_sprint_8b.py`, `tests/probe_8az/test_sprint_8az.py`, `tests/probe_8aa/test_sprint_8aa.py`
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'canonical' language but module is TEST_ONLY
  → `4` affected modules, examples: `tests/test_sprint75/test_speculative_decoding.py`, `tests/test_8c/test_lifecycle_convergence.py`, `tests/probe_7i/test_sprint_7i.py`
- **[MEDIUM]** `Brain Engines/Hermes3 Engine.md`: Uses 'production' language but module is TEST_ONLY
  → `4` affected modules, examples: `tests/test_sprint75/test_speculative_decoding.py`, `tests/test_8c/test_lifecycle_convergence.py`, `tests/probe_7i/test_sprint_7i.py`
- **[MEDIUM]** `Core Architecture/Design Patterns and Architectural Principles.md`: Uses 'canonical' language but module is DONOR_OR_OPTIONAL
  → `4` affected modules, examples: `infrastructure/plugin_manager.py`, `layers/temporal_signal_layer.py`, `infrastructure/system_monitor.py`
- **[MEDIUM]** `Knowledge Layer/DuckDB Shadow Store.md`: Uses 'canonical' language but module is TEST_ONLY
  → `4` affected modules, examples: `tests/test_sprint8ao_duckdb_sidecar.py`, `tests/test_sprint8as_duckdb_async/test_duckdb_async_safety.py`, `tests/probe_7f/test_lmdb_duckdb_dryrun.py`
- **[MEDIUM]** `Project Overview/Introduction.md`: Uses 'canonical' language but module is DEPRECATED
  → `4` affected modules, examples: `REAL_ARCHITECTURE.md`, `LONGTERM_PLAN.md`, `requirements.txt`
- **[MEDIUM]** `Project Overview/Introduction.md`: Uses 'production' language but module is DEPRECATED
  → `4` affected modules, examples: `REAL_ARCHITECTURE.md`, `LONGTERM_PLAN.md`, `requirements.txt`
- **[MEDIUM]** `Testing and Quality Assurance/Quality Gates and Validation.md`: Uses 'production' language but module is TEST_ONLY
  → `4` affected modules, examples: `tests/test_sprint_dashboard.py`, `tests/test_sprint8ap_bounded_live_gate.py`, `tests/probe_8vl/test_lifecycle_gate_truth.py`

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
  - `layers/__init__.py`
  - `infrastructure/plugin_manager.py`
  - `infrastructure/system_monitor.py`
  - `layers/communication_layer.py`
  - `layers/content_layer.py`

## Verdict Breakdown

- **TEST_ONLY**: 184
- **ACTIVE_CAPABILITY**: 139
- **STORAGE_AUTHORITY**: 38
- **ACTIVE_SIDECAR**: 30
- **SECURITY_CRITICAL**: 30
- **DONOR_OR_OPTIONAL**: 28
- **TRANSPORT_AUTHORITY**: 20
- **DEPRECATED**: 17
- **ACTIVE_RUNTIME**: 11
- **LEGACY**: 6
- **ACTIVE_SUPPORT**: 4
- **DEAD_OR_UNWIRED**: 3
- **ACTIVE_DIAGNOSTIC**: 3
- **ACTIVE_PIPELINE**: 2
- **ACTIVE_ENTRYPOINT**: 1
- **CANONICAL_OWNER**: 1
- **PATH_AUTHORITY**: 1

---

## Recommended Sprint Queue

- **F206AH**: Security-critical gaps: private key export, hardcoded helper paths, subprocess spawn
- **F206AI**: Memory authority audit, undocumented write paths
- **F206AJ**: Legacy/deprecated cleanup, donor module wiring decisions
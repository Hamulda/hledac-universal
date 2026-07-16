+++
title = "Getting Started"
template = "section.html"

[extra]
has_mermaid = true
+++

## At a Glance

| Metric | Value |
|---|---|
| Files | 1550 |
| Lines | 548329 |
| Modules | 40 |
| Languages | 2 |

## Entry Points

These are the starting files — where execution begins or where the public API is exposed.

| File | Kind | Key Symbols |
|---|---|---|
| `__init__.py` | Library | `_ensure_bootstrap`, `__getattr__`, `load_optional` |
| `advanced_rag/__init__.py` | Library | — |
| `advanced_web/__init__.py` | Library | — |
| `benchmarks/__init__.py` | Library | — |
| `benchmarks_shadow/__init__.py` | Library | `benchmark_manifest` |
| `brain/__init__.py` | Library | `__getattr__`, `is_brain_engine_available`, `get_available_brain_engines`, `DecisionType` |
| `brain/compiled/__init__.py` | Library | — |
| `brain/hypothesis_engine/__init__.py` | Library | — |
| `cli/__init__.py` | Library | — |
| `compat/__init__.py` | Library | — |
| `config/__init__.py` | Library | `get_preset`, `for_mode`, `_apply_m1_optimizations`, `from_env`, `update`, `to_dict`, `validate`, `create_config` |
| `context_optimization/__init__.py` | Library | — |
| `coordinators/__init__.py` | Library | — |
| `coordinators/memory/__init__.py` | Library | — |
| `coordinators/resource/__init__.py` | Library | — |
| `core/__init__.py` | Library | `__getattr__`, `__dir__` |
| `core/cli/__init__.py` | Library | `_resolve_rl_args`, `_configure_logging`, `filter`, `run`, `_CoremlNativeLibFilter` |
| `core/config/__init__.py` | Library | — |
| `core/embeddings/__init__.py` | Library | — |
| `core/rust_backend/__init__.py` | Library | `is_compatible`, `__new__`, `__init__`, `_ensure_probe`, `is_available`, `info`, `bloom`, `url` |
| `core/telemetry/__init__.py` | Library | — |
| `deep_research/__init__.py` | Library | — |
| `dht/__init__.py` | Library | — |
| `discovery/__init__.py` | Library | — |
| `discovery/academic/__init__.py` | Library | `_lazy_import`, `__getattr__`, `get_all_adapters`, `search_all_academic`, `run_adapter`, `traverse_academic_citations`, `enrich_with_free_pdfs` |
| `embeddings/ane/__init__.py` | Library | `_get_mx`, `_check_ane_available`, `__init__`, `_ensure_ane`, `_ensure_mlx`, `_evict_lru`, `_clear_metal_cache_hook`, `is_loaded` |
| `execution/__init__.py` | Library | — |
| `export/__init__.py` | Library | — |
| `export/components/__init__.py` | Library | — |
| `federated/__init__.py` | Library | `__getattr__` |
| `federated/transports/__init__.py` | Library | — |
| `fetching/__init__.py` | Library | — |
| `forensics/__init__.py` | Library | `_load_metadata_extractor`, `_load_steganography_detector`, `_load_digital_ghost_detector` |
| `graph/__init__.py` | Library | `create_quantum_pathfinder`, `find_best_path` |
| `hledac_hypothesis/__init__.py` | Library | `__getattr__` |
| `hledac_hypothesis/types/__init__.py` | Library | — |
| `infrastructure/__init__.py` | Library | — |
| `intel/__init__.py` | Library | `__getattr__` |
| `intelligence/__init__.py` | Library | — |
| `ipc/__init__.py` | Library | — |
| `knowledge/__init__.py` | Library | `_lazy_legacycompat`, `__init__`, `_ensure_loaded`, `__getattr__`, `__dir__`, `__getattr__`, `_LegacyCompatModule` |
| `knowledge/explainer/__init__.py` | Library | `__getattr__`, `__dir__` |
| `knowledge/graph/__init__.py` | Library | — |
| `knowledge/sprint_facts/__init__.py` | Library | — |
| `layers/__init__.py` | Library | `_stealth_layer_cached`, `get_stealth_layer`, `_content_layer_cached`, `get_content_layer`, `_communication_layer_cached`, `get_communication_layer`, `_ghost_layer_cached`, `get_ghost_layer` |
| `layers/examples/__init__.py` | Library | — |
| `memory/__init__.py` | Library | — |
| `monitoring/__init__.py` | Library | — |
| `multimodal/__init__.py` | Library | — |
| `net/__init__.py` | Library | `is_ipv4`, `is_ipv6`, `is_ip`, `classify_ip`, `is_private_ip`, `is_bogon`, `ip_to_int`, `int_to_ipv4` |
| `network/__init__.py` | Library | `__getattr__` |
| `otel/__init__.py` | Library | `_lazy_import_otel_attr`, `_lazy_import_hledac_otel_attr` |
| `parsing/__init__.py` | Library | — |
| `pipeline/__init__.py` | Library | `__getattr__` |
| `planning/__init__.py` | Library | `__getattr__` |
| `policy/__init__.py` | Library | — |
| `prefetch/__init__.py` | Library | — |
| `probe/probe_f207j_nonfeed_finding_bridge/__init__.py` | Library | — |
| `probe/probe_f207l_bridge_contract/__init__.py` | Library | — |
| `probe/probe_f207q_prewindup_barrier/__init__.py` | Library | — |
| `probe/probe_f208m_predispatch_before_terminality/__init__.py` | Library | — |
| `probe/probe_f208n_scheduler_callback_wiring/__init__.py` | Library | — |
| `probe/probe_f209b_export_prelude_pass_through/__init__.py` | Library | — |
| `probe/probe_f214opt_integration_guard/__init__.py` | Library | — |
| `probe/probe_f226c_ct_acceptance/__init__.py` | Library | — |
| `probe/probe_f228a_policy_feedback/__init__.py` | Library | — |
| `probe/probe_transport_policy_f206ar/__init__.py` | Library | — |
| `recon/__init__.py` | Library | `_load_spec`, `__getattr__`, `__dir__`, `_lazy_stats` |
| `recon/cert/__init__.py` | Library | — |
| `recon/dns/__init__.py` | Library | — |
| `recon/network/__init__.py` | Library | — |
| `recon/protocols/__init__.py` | Library | — |
| `recon/stealth/__init__.py` | Library | — |
| `rendering/__init__.py` | Library | — |
| `report/__init__.py` | Library | — |
| `report/renderers/__init__.py` | Library | — |
| `rl/__init__.py` | Library | — |
| `runtime/__init__.py` | Library | `__getattr__`, `__dir__` |
| `runtime/acquisition/__init__.py` | Library | — |
| `runtime/acquisition_lanes/__init__.py` | Library | — |
| `runtime/adapters/__init__.py` | Library | — |
| `runtime/context/__init__.py` | Library | `__getattr__` |
| `runtime/patterns/__init__.py` | Library | `__getattr__` |
| `runtime/protocols/__init__.py` | Library | — |
| `runtime/scheduler/__init__.py` | Library | — |
| `runtime/scheduler_phases/__init__.py` | Library | `run`, `PhaseRunner` |
| `runtime/scheduler_v2/__init__.py` | Library | `__getattr__` |
| `runtime/sidecars/__init__.py` | Library | `__getattr__` |
| `runtime/state/__init__.py` | Library | `mark_uvloop_installed`, `get_runtime_state`, `mark_uvloop_installed`, `to_correlation_dict`, `to_dict`, `get_sprint_metrics`, `set_sprint_metrics`, `reset_sprint_metrics` |
| `rust_extensions/__init__.py` | Library | — |
| `rust_extensions/hledac_rust_extensions/__init__.py` | Library | — |
| `rust_extensions/src/lib.rs` | Library | `detect_p_core_count`, `detect_p_core_count`, `apply_qos_hint`, `apply_affinity_hint`, `apply_affinity_hint`, `apply_affinity_hint`, `cpu_pool`, `io_pool` |
| `secrets/__init__.py` | Library | — |
| `secrets_vault/__init__.py` | Library | — |
| `security/__init__.py` | Library | — |
| `stealth/__init__.py` | Library | — |
| `storage/__init__.py` | Library | — |
| `tests/__init__.py` | Library | — |
| `tests/cli/__init__.py` | Library | — |
| `tests/test_inference_pipeliner/__init__.py` | Library | — |
| `tests/utils/__init__.py` | Library | — |
| `tools/__init__.py` | Library | — |
| `transport/__init__.py` | Library | `__getattr__` |
| `utils/__init__.py` | Library | `__getattr__`, `__dir__`, `_uuid7_stdlib`, `uuid7`, `get_uuid7_compat_status`, `run_cmd` |
| `utils/coreml/__init__.py` | Library | — |
| `utils/mlx_memory/__init__.py` | Library | — |
| `utils/patterns/__init__.py` | Library | — |
| `utils/text/__init__.py` | Library | — |
| `tests/conftest.py` | Test Runner | — |

## Reading Order

Start at the top and work your way down. Each layer depends on the one below it.

{% mermaid() %}
flowchart TD
    L0["Entry Points: __init__.py, __init__.py, __init__.py, __init__.py, __init__.py, __init__.py +103 more"]
    L1["Direct Dependencies: rag_orchestrator.py, automation_orchestrator.py, stealth_browser.py, structured_extractor.py, deephermes3_engine.py, mlx_batched_executor.py +244 more"]
    L2["Core Infrastructure: dspy_signatures.py, mlx_cache.py, _dto.py, enums.py, neon.rs, index.rs +12 more"]
    L3["Supporting Modules: body_limiter.py, prewarm_pool.py, conditional_cache.py, httpx_client.py"]
    L4["Deep Dependencies: session_pool.py"]
    L5["Periphery: curl_cffi_runtime.py"]
    L0 --> L1
    L1 --> L2
    L2 --> L3
    L3 --> L4
    L4 --> L5
    style L0 fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
{% end %}

### Layer 0: Entry Points

- `__init__.py`
- `advanced_rag/__init__.py`
- `advanced_web/__init__.py`
- `benchmarks/__init__.py`
- `benchmarks_shadow/__init__.py`
- `brain/__init__.py`
- `brain/compiled/__init__.py`
- `brain/hypothesis_engine/__init__.py`
- `cli/__init__.py`
- `compat/__init__.py`
- `config/__init__.py`
- `context_optimization/__init__.py`
- `coordinators/__init__.py`
- `coordinators/memory/__init__.py`
- `coordinators/resource/__init__.py`
- `core/__init__.py`
- `core/cli/__init__.py`
- `core/config/__init__.py`
- `core/embeddings/__init__.py`
- `core/rust_backend/__init__.py`
- `core/telemetry/__init__.py`
- `deep_research/__init__.py`
- `dht/__init__.py`
- `discovery/__init__.py`
- `discovery/academic/__init__.py`
- `embeddings/ane/__init__.py`
- `execution/__init__.py`
- `export/__init__.py`
- `export/components/__init__.py`
- `federated/__init__.py`
- `federated/transports/__init__.py`
- `fetching/__init__.py`
- `forensics/__init__.py`
- `graph/__init__.py`
- `hledac_hypothesis/__init__.py`
- `hledac_hypothesis/types/__init__.py`
- `infrastructure/__init__.py`
- `intel/__init__.py`
- `intelligence/__init__.py`
- `ipc/__init__.py`
- `knowledge/__init__.py`
- `knowledge/explainer/__init__.py`
- `knowledge/graph/__init__.py`
- `knowledge/sprint_facts/__init__.py`
- `layers/__init__.py`
- `layers/examples/__init__.py`
- `memory/__init__.py`
- `monitoring/__init__.py`
- `multimodal/__init__.py`
- `net/__init__.py`
- `network/__init__.py`
- `otel/__init__.py`
- `parsing/__init__.py`
- `pipeline/__init__.py`
- `planning/__init__.py`
- `policy/__init__.py`
- `prefetch/__init__.py`
- `probe/probe_f207j_nonfeed_finding_bridge/__init__.py`
- `probe/probe_f207l_bridge_contract/__init__.py`
- `probe/probe_f207q_prewindup_barrier/__init__.py`
- `probe/probe_f208m_predispatch_before_terminality/__init__.py`
- `probe/probe_f208n_scheduler_callback_wiring/__init__.py`
- `probe/probe_f209b_export_prelude_pass_through/__init__.py`
- `probe/probe_f214opt_integration_guard/__init__.py`
- `probe/probe_f226c_ct_acceptance/__init__.py`
- `probe/probe_f228a_policy_feedback/__init__.py`
- `probe/probe_transport_policy_f206ar/__init__.py`
- `recon/__init__.py`
- `recon/cert/__init__.py`
- `recon/dns/__init__.py`
- `recon/network/__init__.py`
- `recon/protocols/__init__.py`
- `recon/stealth/__init__.py`
- `rendering/__init__.py`
- `report/__init__.py`
- `report/renderers/__init__.py`
- `rl/__init__.py`
- `runtime/__init__.py`
- `runtime/acquisition/__init__.py`
- `runtime/acquisition_lanes/__init__.py`
- `runtime/adapters/__init__.py`
- `runtime/context/__init__.py`
- `runtime/patterns/__init__.py`
- `runtime/protocols/__init__.py`
- `runtime/scheduler/__init__.py`
- `runtime/scheduler_phases/__init__.py`
- `runtime/scheduler_v2/__init__.py`
- `runtime/sidecars/__init__.py`
- `runtime/state/__init__.py`
- `rust_extensions/__init__.py`
- `rust_extensions/hledac_rust_extensions/__init__.py`
- `rust_extensions/src/lib.rs`
- `secrets/__init__.py`
- `secrets_vault/__init__.py`
- `security/__init__.py`
- `stealth/__init__.py`
- `storage/__init__.py`
- `tests/__init__.py`
- `tests/cli/__init__.py`
- `tests/test_inference_pipeliner/__init__.py`
- `tests/utils/__init__.py`
- `tools/__init__.py`
- `transport/__init__.py`
- `utils/__init__.py`
- `utils/coreml/__init__.py`
- `utils/mlx_memory/__init__.py`
- `utils/patterns/__init__.py`
- `utils/text/__init__.py`
- `tests/conftest.py`

### Layer 1: Direct Dependencies

- `advanced_rag/rag_orchestrator.py`
- `advanced_web/automation_orchestrator.py`
- `advanced_web/stealth_browser.py`
- `advanced_web/structured_extractor.py`
- `brain/deephermes3_engine.py`
- `brain/mlx_batched_executor.py`
- `brain/mlx_worker_thread.py`
- `brain/inference_pipeliner.py`
- `brain/research_hypothesis_engine.py`
- `cli/parser.py`
- `config/settings.py`
- `context_optimization/context_cache.py`
- `context_optimization/context_compressor.py`
- `context_optimization/dynamic_context_manager.py`
- `coordinators/_catalog.py`
- `coordinators/agent_coordination_engine.py`
- `coordinators/base.py`
- `coordinators/execution_coordinator.py`
- `coordinators/memory_coordinator.py`
- `coordinators/meta_reasoning_coordinator.py`
- `coordinators/monitoring_coordinator.py`
- `coordinators/performance_coordinator.py`
- `coordinators/privacy_enhanced_research.py`
- `coordinators/research_coordinator.py`
- `coordinators/research_optimizer.py`
- `coordinators/resource_allocator.py`
- `coordinators/security_coordinator.py`
- `coordinators/validation_coordinator.py`
- `coordinators/resource/resource_coordinator.py`
- `core/rust_backend/_prober.py`
- `core/rust_backend/bloom.py`
- `core/rust_backend/hash.py`
- `core/rust_backend/ip.py`
- `core/rust_backend/ioc.py`
- `core/rust_backend/ioc_dedup.py`
- `core/rust_backend/misc.py`
- `core/rust_backend/quality.py`
- `core/rust_backend/rolling_hash.py`
- `core/rust_backend/simhash.py`
- `core/rust_backend/url.py`
- `core/rust_backend/lsh.py`
- `deep_research/path_discovery.py`
- `deep_research/utils.py`
- `dht/kademlia_node.py`
- `dht/local_graph.py`
- `dht/sketch_exchange.py`
- `embeddings/ane/_encoder.py`
- `embeddings/modernbert_embedder.py`
- `execution/ghost_executor.py`
- `export/components/graph_viz_writer.py`
- `export/components/ioc_table_writer.py`
- `export/components/stix_streaming.py`
- `export/components/streaming_exporter.py`
- `federated/bridge.py`
- `federated/coordinator.py`
- `federated/qtable.py`
- `federated/transports/protocol.py`
- `federated/transports/inmemory_peer.py`
- `federated/transports/lane_dispatch.py`
- `federated/transports/peer_node.py`
- `forensics/metadata_extractor.py`
- `forensics/stego_detector.py`
- `forensics/digital_ghost_detector.py`
- `graph/graph_manager.py`
- `graph/quantum_pathfinder.py`
- `hledac_hypothesis/_types.py`
- `hledac_hypothesis/adversarial.py`
- `hledac_hypothesis/causal.py`
- `hledac_hypothesis/explainer.py`
- `hledac_hypothesis/packs.py`
- `hledac_hypothesis/types/evidence.py`
- `hledac_hypothesis/types/test.py`
- `hledac_hypothesis/types/query.py`
- `hledac_hypothesis/types/causal.py`
- `hledac_hypothesis/types/hypothesis.py`
- `hledac_hypothesis/types/anomaly.py`
- `infrastructure/plugin_manager.py`
- `infrastructure/system_monitor.py`
- `ipc/ring_mmap_ipc.py`
- `knowledge/atomic_storage.py`
- `knowledge/sprint_facts/canonical_finding.py`
- `knowledge/sprint_facts/source_attribution.py`
- `layers/communication_layer.py`
- `layers/content_layer.py`
- `layers/ghost_layer.py`
- `layers/hive_coordination.py`
- `layers/layer_manager.py`
- `layers/layer_protocol.py`
- `layers/memory_layer.py`
- `layers/privacy_layer.py`
- `layers/research_layer.py`
- `layers/security_layer.py`
- `layers/stealth_layer.py`
- `layers/temporal_signal_layer.py`
- `layers/temporal_signal_runtime.py`
- `layers/temporal_signal_store.py`
- `layers/ua_rotator.py`
- `layers/examples/demos.py`
- `memory/memory_manager.py`
- `multimodal/analyzer.py`
- `multimodal/fusion.py`
- `multimodal/vision_encoder.py`
- `network/dns_tunnel_detector.py`
- `network/banner_grabber.py`
- `network/ipv6_recon.py`
- `network/ipfs_client.py`
- `pipeline/public_stages.py`
- `pipeline/live_feed_pipeline.py`
- `planning/cost_model.py`
- `planning/htn_planner.py`
- `planning/search.py`
- `planning/slm_decomposer.py`
- `planning/task_cache.py`
- `policy/nym_policy.py`
- `prefetch/budget_tracker.py`
- `prefetch/prefetch_cache.py`
- `prefetch/prefetch_oracle_integration.py`
- `prefetch/ssm_reranker.py`
- `prefetch/temporal_predictor.py`
- `probe/probe_f207j_nonfeed_finding_bridge/nonfeed_finding_bridge.py`
- `recon/stealth/_models.py`
- `recon/stealth/scraper.py`
- `recon/stealth/monitor.py`
- `runtime/adapters/duckdb_adapter.py`
- `runtime/adapters/fetch_adapter.py`
- `runtime/adapters/graph_adapter.py`
- `runtime/protocols/brain_protocol.py`
- `runtime/protocols/cleanup_protocol.py`
- `runtime/protocols/enrichment_protocol.py`
- `runtime/protocols/fetch_protocol.py`
- `runtime/protocols/graph_protocol.py`
- `runtime/protocols/intel_protocol.py`
- `runtime/protocols/lane_protocol.py`
- `runtime/protocols/layers_protocol.py`
- `runtime/protocols/lifecycle_protocol.py`
- `runtime/protocols/metrics_protocol.py`
- `runtime/protocols/pivot_protocol.py`
- `runtime/protocols/prefetch_protocol.py`
- `runtime/protocols/score_protocol.py`
- `runtime/protocols/storage_protocol.py`
- `runtime/protocols/transport_protocol.py`
- `rust_extensions/src/aho_corasick.rs`
- `rust_extensions/src/query_terms.rs`
- `rust_extensions/src/bloom.rs`
- `rust_extensions/src/compress.rs`
- `rust_extensions/src/regex_lz4.rs`
- `rust_extensions/src/content_hasher.rs`
- `rust_extensions/src/crypto_accelerate.rs`
- `rust_extensions/src/adaptive_scheduler.rs`
- `rust_extensions/src/async_query.rs`
- `rust_extensions/src/graph_traverse.rs`
- `rust_extensions/src/hot_edges_rs.rs`
- `rust_extensions/src/html_parse.rs`
- `rust_extensions/src/int_counter_layout.rs`
- `rust_extensions/src/ioc_dedup.rs`
- `rust_extensions/src/ioc_patterns.rs`
- `rust_extensions/src/ioc_patterns_generated.rs`
- `rust_extensions/src/dns_tunnel.rs`
- `rust_extensions/src/ioc_extract.rs`
- `rust_extensions/src/ioc_extract_fast.rs`
- `rust_extensions/src/ioc_extract_simd.rs`
- `rust_extensions/src/ioc_cooccurrence_rs.rs`
- `rust_extensions/src/lmdb_dht.rs`
- `rust_extensions/src/madvise.rs`
- `rust_extensions/src/metal_compute.rs`
- `rust_extensions/src/metal_pattern_matcher.rs`
- `rust_extensions/src/memory.rs`
- `rust_extensions/src/ip_parse.rs`
- `rust_extensions/src/quality_gate.rs`
- `rust_extensions/src/rolling_hash.rs`
- `rust_extensions/src/signal_batch.rs`
- `rust_extensions/src/simd_similarity.rs`
- `rust_extensions/src/simhash_ext.rs`
- `rust_extensions/src/lsh_index.rs`
- `rust_extensions/src/text_norm.rs`
- `rust_extensions/src/feed_decision.rs`
- `rust_extensions/src/feed_pipeline.rs`
- `rust_extensions/src/pipeline_compose.rs`
- `rust_extensions/src/xml_sanitize.rs`
- `rust_extensions/src/url_engine.rs`
- `rust_extensions/src/url_ops.rs`
- `rust_extensions/src/url_set.rs`
- `rust_extensions/src/xxhash_ext.rs`
- `rust_extensions/src/zero_copy.rs`
- `rust_extensions/src/serde_json_rs.rs`
- `rust_extensions/src/arrow_batch_builder.rs`
- `rust_extensions/src/parquet_reader.rs`
- `rust_extensions/src/spsc_queue.rs`
- `rust_extensions/src/mpsc_pool.rs`
- `rust_extensions/src/federated_qtable.rs`
- `rust_extensions/src/simd/mod.rs`
- `rust_extensions/src/hnsw/mod.rs`
- `rust_extensions/src/graph_cache.rs`
- `rust_extensions/src/dedup_bloom.rs`
- `rust_extensions/src/rate_limit.rs`
- `rust_extensions/src/telemetry_agg.rs`
- `rust_extensions/src/health.rs`
- `rust_extensions/src/claims_extraction.rs`
- `rust_extensions/src/sprint_policies.rs`
- `rust_extensions/src/tls_metadata.rs`
- `rust_extensions/src/gil.rs`
- `rust_extensions/src/pool_run.rs`
- `rust_extensions/src/mlx_bridge.rs`
- `rust_extensions/src/collections/mod.rs`
- `rust_extensions/src/data.rs`
- `rust_extensions/src/text_similarity.rs`
- `stealth/stealth_manager.py`
- `stealth/stealth_session.py`
- `tools/commoncrawl_adapter.py`
- `tools/content_miner.py`
- `tools/file_cache.py`
- `tools/lightpanda_manager.py`
- `tools/lightpanda_pool.py`
- `tools/reranker.py`
- `tools/url_dedup.py`
- `tools/wayback_adapter.py`
- `tools/zstd_compressor.py`
- `transport/base.py`
- `transport/gopher_transport.py`
- `transport/http3_lane.py`
- `transport/unified_transport.py`
- `utils/action_result.py`
- `utils/async_utils.py`
- `utils/bloom_filter.py`
- `utils/deduplication.py`
- `utils/encryption.py`
- `utils/entity_extractor.py`
- `utils/execution_optimizer.py`
- `utils/filtering.py`
- `utils/intelligent_cache.py`
- `utils/language.py`
- `utils/lazy_imports.py`
- `utils/patterns/pattern_matcher.py`
- `utils/performance_monitor.py`
- `utils/predictive_planner.py`
- `utils/query_expansion.py`
- `utils/ranking.py`
- `utils/rate_limiter.py`
- `utils/robots_parser.py`
- `utils/semantic.py`
- `utils/tech_detection.py`
- `utils/validation.py`
- `utils/config_introspection.py`
- `utils/workflow_engine.py`
- `utils/coreml/client.py`
- `utils/coreml/manager.py`
- `utils/coreml/models.py`
- `utils/text/unicode_analyzer.py`
- `utils/text/encoding_detector.py`
- `utils/text/hash_identifier.py`

### Layer 2: Core Infrastructure

- `brain/dspy_signatures.py`
- `utils/mlx_cache.py`
- `coordinators/_dto.py`
- `coordinators/enums.py`
- `rust_extensions/src/simd/neon.rs`
- `rust_extensions/src/hnsw/index.rs`
- `rust_extensions/src/hnsw/py_api.rs`
- `rust_extensions/src/collections/ring_buffer.rs`
- `transport/circuit_breaker.py`
- `transport/curl_cffi_fetch.py`
- `transport/curl_cffi_transport.py`
- `transport/httpx_transport.py`
- `transport/transport_router.py`
- `utils/async_helpers.py`
- `utils/memory_dashboard.py`
- `utils/_warnings.py`
- `utils/flow_trace.py`
- `utils/rate_limiters.py`

### Layer 3: Supporting Modules

- `transport/body_limiter.py`
- `transport/prewarm_pool.py`
- `transport/conditional_cache.py`
- `transport/httpx_client.py`

### Layer 4: Deep Dependencies

- `transport/session_pool.py`

### Layer 5: Periphery

- `transport/curl_cffi_runtime.py`


# Review Scope

## Target

Comprehensive code review of Hledac Universal AI research platform (`hledac/universal/`), an autonomous OSINT orchestrator optimized for M1 MacBook 8GB UMA.

## Files

**Core:**
- `autonomous_orchestrator.py` - Main orchestrator
- `__main__.py` - CLI entry point (136KB)
- `tool_registry.py` - Tool execution registry (61KB)
- `config.py` - Configuration

**Coordinators (29 files):**
- `coordinators/fetch_coordinator.py` - HTTP transport seam
- `coordinators/memory_coordinator.py`, `resource_allocator.py`, `research_coordinator.py`
- `coordinators/security_coordinator.py`, `privacy_enhanced_research.py`
- `coordinators/graph_coordinator.py`, `validation_coordinator.py`, `performance_coordinator.py`
- `coordinators/render_coordinator.py`, `multimodal_coordinator.py`, `benchmark_coordinator.py`
- `coordinators/swarm_coordinator.py`, `advanced_research_coordinator.py`, `meta_reasoning_coordinator.py`
- `coordinators/archive_coordinator.py`, `monitoring_coordinator.py`, `execution_coordinator.py`
- `coordinators/claims_coordinator.py`, `research_optimizer.py`, `agent_coordination_engine.py`

**Brain/MLX:**
- `brain/inference_engine.py`, `brain/model_lifecycle.py`, `brain/` (Hermes integration)

**Knowledge:**
- `knowledge/duckdb_store.py`, `knowledge/atomic_storage.py`, `knowledge/graph_service.py`
- `knowledge/lancedb_store.py`, `knowledge/` (17 files total)

**Tools:**
- `tools/http_client.py`, `tools/lmdb_kv.py`, `tools/checkpoint.py`
- `tools/url_dedup.py`, `tools/host_policies.py`, `tools/reputation.py`
- `tools/scoring.py`, `tools/session_manager.py`, `tools/` (44 files)

**Runtime:**
- `runtime/sprint_scheduler.py`, `runtime/sprint_executor.py`, `runtime/memory_governor.py`
- `runtime/` (25 files)

**Pipeline:**
- `pipeline/live_feed_pipeline.py`, `pipeline/live_public_pipeline.py`

**Network:**
- `network/session_runtime.py`, `network/dns.py`, `network/` (HTTP transport, curl_cffi)

**Security:**
- `security/stealth_session.py`, `security/fingerprinting.py`, `security/opsec_policy.py`
- `security/` (18 files)

**Layers:**
- `layers/hypothesis_engine.py`, `layers/evidence_correlator.py`
- `layers/temporal_archaeology.py`, `layers/leak_sentinel.py`, `layers/` (25 files)

**Intelligence:**
- `intelligence/cti_collectors.py`, `intelligence/feed_adapters.py`, `intelligence/` (48 files)

**Utils:**
- `utils/uma_budget.py`, `utils/mlx_cache.py`, `utils/concurrency.py`
- `utils/async_helpers.py`, `utils/` (56 files)

**Other:**
- `core/`, `export/`, `monitoring/`, `graph/`, `forensics/`, `multimodal/`

## Excluded
- `tests/` - Test files (separate review scope)
- `benchmarks/`, `research/` - Benchmark and research code
- `.backup/`, `.venv/`, `__pycache__/` - Non-production

## Flags

- Security Focus: no
- Performance Critical: yes (M1 8GB UMA constraint)
- Strict Mode: no
- Framework: python (asyncio, MLX, DuckDB, LanceDB)

## Review Phases

1. Code Quality & Architecture
2. Security & Performance
3. Testing & Documentation
4. Best Practices & Standards
5. Consolidated Report

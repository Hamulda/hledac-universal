"""  # noqa: N999
Sprint 3A: Phase Gate Manifests
================================
Defines pytest markers for layered test execution.

Usage:
    # Probe gate (fastest, always safe)
    pytest tests/ -m probe_gate -q

    # AO Canary (fast canary, no production risk)
    pytest tests/test_ao_canary.py -q

    # Phase gate (per-sprint focused)
    pytest tests/ -m phase_gate -q

    # Full sprint suite (per-sprint test files)
    pytest tests/test_sprint*.py -q

    # Manual only (heavy/integration - never as default)
    pytest tests/ -m manual_only -q

    # Full AO mega-suite (HEAVY - last resort)
    # pytest tests/test_autonomous_orchestrator.py -q  # NEVER as default
"""

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "probe_gate: Probe tests - instant smoke, no imports"
    )
    config.addinivalue_line(
        "markers", "ao_canary: AO canary tests - fast lifecycle checks (~5-10s)"
    )
    config.addinivalue_line(
        "markers", "phase_gate: Phase gate tests - per-sprint focused"
    )
    config.addinivalue_line(
        "markers", "manual_only: Manual/integration tests - heavy, RAM intensive"
    )


# ============================================================================
# PROBE GATE - Instant smoke tests
# ============================================================================
# Files: probe_*/  - pure Python smoke, no imports
# Duration: <1 second
# Run: pytest tests/probe_*/ -m probe_gate -q


# ============================================================================
# AO CANARY - Fast lifecycle canary
# ============================================================================
# File: test_ao_canary.py
# Duration: ~5-10 seconds (fully mocked)
# Run: pytest tests/test_ao_canary.py -q


# ============================================================================
# PHASE GATE - Per-sprint focused tests
# ============================================================================
# Files: test_sprint*.py (individual sprint test files)
# Duration: varies by sprint, typically 10-60s each
# Run: pytest tests/test_sprint*.py -m phase_gate -q
# Or individual: pytest tests/test_sprint85.py -q


# ============================================================================
# SPRINT TEST FILES (chronological)
# ============================================================================
# test_sprint41.py - Dynamic Batching
# test_sprint42.py - Batch Aging, Predictive RSS, LinUCB
# test_sprint43.py - Distributed Tracing, Geo+Language
# test_sprint44.py - Lightpanda, Deep Forensics, Link Prediction
# test_sprint45.py - Lightpanda Pool, LSH, Stegdetect, MessagePack
# test_sprint46.py - Session Management, Paywall Bypass, OSINT Frameworks
# test_sprint47.py - Stegdetect Pool, Sherlock JSON, Batch Tie-breaker
# test_sprint48.py - MLX Monitor, Holt's double EMA
# test_sprint49.py - ELA Graph Pipeline, PaywallBypass session pool
# test_sprint50.py - HNSW async build, Hermes3 shared KV cache
# test_sprint51_52.py - FlashRank, HTTP/3, GLiNER thread pool
# test_sprint53.py - MPS/Metal for ELA, AMX vectorization
# test_sprint54.py - GlobalPriorityScheduler, ResourceAllocator
# test_sprint55.py - ANE Acceleration, GNN Predictor
# test_sprint56.py - ParallelResearchScheduler, BranchManager
# test_sprint57.py - PQIndex, DynamicModelManager, PagedAttentionCache
# test_sprint58a.py - QMIX Joint Trainer
# test_sprint58b.py - Federated Learning, PQC
# test_sprint59.py - PrefetchOracle, LinUCB contextual bandit
# test_sprint60.py - HTN Planning, AdaptiveCostModel, DeepExplainer
# test_sprint61.py - (exists)
# test_sprint62a.py, test_sprint62b.py, test_sprint62c.py - Transport resolver
# test_sprint64_transport_resolver.py - Transport resolver
# test_sprint65_no_toggles.py - No-toggles refactoring
# test_sprint65_model_store_regression.py - Model store regression
# test_sprint65e_no_available_flags_in_orchestrator.py - No AVAILABLE flags
# test_sprint66/ - Render coordinator, capability prober
# test_sprint67/ - MLX cache, renderer routing, pattern mining
# test_sprint68/ - Action registry, memory pressure
# test_sprint69/ - Structure map engine, scheduling
# test_sprint70a/, test_sprint70b/ - Actions, integration
# test_sprint71/ - ANE pipelines, continuous batching, CoreML
# test_sprint73/ - SimHash, thermal penalty
# test_sprint74/ - MLX cache, async leaks, chaos
# test_sprint75/ - Adaptive profile, energy thermal, speculative decoding
# test_sprint76/ - NER ANE, thermal parallelism, embedding cache
# test_sprint77/ - (exists)
# test_sprint78/ - Metrics, prompt cache, DSPy optimizer
# test_sprint79a/, test_sprint79b/, test_sprint79c/ - Optimizations
# test_sprint80/ - Optimizations


# ============================================================================
# MANUAL ONLY - Heavy tests requiring special conditions
# ============================================================================
# Files: e2e_*.py, test_e2e_pipeline.py
# These tests may:
# - Require actual MLX model loading
# - Need network access
# - Consume significant RAM
# - Take several minutes
#
# Run explicitly when needed:
# pytest tests/test_e2e_pipeline.py -q
# pytest tests/e2e_autonomous_loop.py -q


# ============================================================================
# MEGA SUITE - test_autonomous_orchestrator.py
# ============================================================================
# WARNING: 22,154 lines, 291 test classes
# Duration: 10+ minutes on M1 8GB
# NEVER run as default gate!
#
# Only run when:
# - Specific canary/phase tests pass
# - You have explicit time budget
# - Debugging specific orchestrator behavior
#
# Individual class extraction recommended:
# pytest tests/test_autonomous_orchestrator.py::TestOrchestratorSmoke -q
# pytest tests/test_autonomous_orchestrator.py::TestCapabilitySystem -q
# etc.

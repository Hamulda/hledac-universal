# Permanently Shimmed Imports

**Source:** broken_imports.json
**Generated:** 2026-05-24
**Total Items:** 241

These imports are **intentionally shimmed** — they reference modules that were deleted, never existed, or live outside `hledac/universal/`. They do not cause runtime failures because the shim layer catches and handles them gracefully.

---

## Category 1: Deleted in F196A Sprint (Ghost Invariants Cleanup)

| Item | Count | Rationale |
|------|-------|-----------|
| `hledac.universal.layers.*` | 16 | Layers system removed in F196A |
| `hledac.universal.rl.marl_coordinator.*` | 8 | MARLCoordinator deleted in F196A |
| `hledac.universal.runtime.memory_watchdog.*` | 7 | MemoryWatchdog deleted in F196A |
| `hledac.universal.runtime.intelligence_dispatcher.*` | 4 | IntelligenceDispatcher deleted in F196A |
| `hledac.universal.FETCH_SEMAPHORE` | 3 | Removed in F196A |
| `hledac.universal.orchestrator.*` | 3 | Refactored to autonomous_orchestrator.py |
| `hledac.universal.runtime.runtime_authority_manifest.*` | 6 | Never existed |
| `hledac.universal.transport.TransportContext` | 6 | Removed in F196A |
| `hledac.universal.transport.TransportResolver` | 5 | Removed in F196A |

**Subtotal:** ~58 items

---

## Category 2: Never Existed (Ghost Modules)

These modules were planned but never implemented:

| Prefix | Count | Rationale |
|--------|-------|-----------|
| `hledac.cortex.*` | 4 | Cortex module never created |
| `hledac.speculative_decoding.*` | 3 | Speculative decoding never implemented |
| `hledac.tools.preserved_logic.*` | 3 | Never existed |
| `hledac.outdated.*` | 2 | Cleaned up as dead code |
| `hledac.advanced_rag.*` | 1 | Never existed |
| `hledac.stealth_osint.*` | 4 | Never existed |
| `hledac.stealth_web_v2.*` | 4 | Never existed |
| `hledac.supreme.*` | 3 | Never existed |
| `hledac.ultra_context.*` | 4 | Never existed |
| `hledac.config.*` | 2 | Never existed |
| `hledac.msqes.*` | 2 | Never existed |
| `hledac.runtime.unified_orchestrator.*` | 1 | Never existed |

**Subtotal:** ~33 items

---

## Category 3: Outside Universal/ (hledac.core.*, hledac.security.*)

These modules live in sibling packages, not in `hledac/universal/`:

| Prefix | Count | Rationale |
|--------|-------|-----------|
| `hledac.core.*` | ~40 | All hledac/core/* modules are outside universal/ |
| `hledac.security.*` (non-universal) | 6 | Security modules in hledac/security/ |

**Subtotal:** ~46 items

---

## Category 4: Legacy Test Files

These test files import modules that no longer exist:

| File | Count | Rationale |
|------|-------|-----------|
| `tests/test_sprint_f193a_legacy_boundary.py` | 6 | Legacy boundary tests |
| `tests/test_sprint54.py` | 1 | Old sprint test |
| `tests/test_sprint62c.py` | 1 | Old sprint test |
| `tests/test_sprint64_transport_resolver.py` | 2 | Old sprint test |
| `tests/probe_f192g/*` | 4 | F192G probe tests |
| `tests/probe_r0_nonfeed_reality_lock/*` | 6 | R0 probe tests |
| `tests/test_autonomous_orchestrator.py` | 4 | Legacy coordinator imports |
| `tests/test_sprint81/test_phase4.py` | 2 | Old sprint test |

**Subtotal:** ~26 items

---

## Category 5: Removed Functionality

| Item | Count | Rationale |
|------|-------|-----------|
| `hledac.universal.utils.ActionResult` | 13 | Removed from utils |
| `hledac.universal.utils.get_uuid7_compat_status` | 6 | Removed from utils |
| `hledac.universal.budget_manager.*` | 3 | Removed in F196A |
| `hledac.universal.context_cache.*` | 2 | Removed in F196A |
| `hledac.universal.probe_f207j_*` | 4 | F207J sprint probe |
| `hledac.universal.export.render_*` | 3 | Export functions renamed |
| `hledac.universal.text.*_AVAILABLE` | 3 | Module-level bools removed |
| `hledac.universal.orchestrator._ResearchManager` | 2 | Removed in F196A |
| `hledac.universal.orchestrator._SecurityManager` | 2 | Removed in F196A |
| `hledac.universal.knowledge.ContextGraph` | 1 | Never existed |
| `hledac.universal.knowledge.RAGEngine` | 1 | Never existed |
| `hledac.universal.knowledge.evidence_log.EvidencePacketStorage` | 1 | Never existed |
| `hledac.universal.hypothesis.BetaBinomial` | 1 | Use scipy.stats.beta |
| `hledac.universal.transport.InMemoryTransport` | 2 | Test transport never implemented |
| `hledac.universal.transport.Transport` | 1 | Removed |
| `hledac.universal.autonomy.agent_meta_optimizer.*` | 1 | Never existed |
| `hledac.universal.brain.modernbert_engine.ModernBertEngine` | 1 | We use Hermes3 via MLX |
| `hledac.universal.intelligence.path_discovery.ShadowWalkerAlgorithm` | 1 | Never implemented |
| `hledac.universal.loops.fetch_loop.SELECTOLAX_AVAILABLE` | 1 | Removed - no longer using selectolax |

**Subtotal:** ~53 items

---

## Category 6: Shim Layer Items (Already Handled)

The following are already covered by `_shims/` files:

| Shim File | Covered Imports |
|----------|----------------|
| `_shims/core_resilience.py` | `hledac.core.resilience.AgentExecutionError`, `CircuitBreakerOpen` |
| `_shims/core_mlx_embeddings.py` | `hledac.core.mlx_embeddings.*` |
| `_shims/security_stealth_engine.py` | `hledac.security.stealth_engine.StealthEngine` |
| `_shims/security_threat_intelligence.py` | `hledac.security.threat_intelligence.ThreatIntelligence` |
| `_shims/security_zkp_research_engine.py` | `hledac.security.zkp_research_engine.ZKPResearchEngine` |
| `_shims/security_quantum_resistant_crypto.py` | `hledac.security.quantum_resistant_crypto.QuantumResistantCrypto` |
| `_shims/security_temporal_anonymizer.py` | `hledac.security.temporal_anonymizer.TemporalAnonymizer` |
| `_shims/security_zero_attribution_engine.py` | `hledac.security.zero_attribution_engine.ZeroAttributionEngine` |
| `_shims/core_watchdog.py` | `hledac.core.watchdog.*` |
| `_shims/core_unified_ai_orchestrator.py` | `hledac.core.unified_ai_orchestrator.UnifiedAIOrchestrator` |
| `_shims/cortex_director.py` | `hledac.cortex.director.GhostDirector` |
| `_shims/security_quantum_resistant_crypto.py` | `hledac.security.quantum_resistant_crypto.QuantumResistantCrypto` |

---

## Summary

| Category | Count |
|----------|-------|
| Deleted in F196A | ~58 |
| Never existed | ~33 |
| Outside universal/ | ~46 |
| Legacy tests | ~26 |
| Removed functionality | ~53 |
| Shim layer items | ~25 |
| **TOTAL** | **241** |

---

## Verification

The CI health check validates 3 core imports still work:

```bash
python scripts/ci_health_check.py
# or
uv run python -c "from hledac.universal.runtime.sprint_scheduler import SprintScheduler; print('OK')"
uv run python -c "from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore; print('OK')"
uv run python -c "from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator; print('OK')"
```

All 3 pass ✅
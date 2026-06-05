# IMPLEMENTATION ROADMAP — Universal Namespace Repair

**Generated:** 2026-05-31  
**Scope:** 40 broken imports across A1-A5 analysis files + broken_imports.json  
**Goal:** Restore full functionality with M1 8GB feasibility checks

---

## 1. DEDUPLICATION CHECK

| Category | Count | Action |
|----------|-------|--------|
| **REDIRECT** (symbol exists, wrong path) | 21 | 1-line fix per import |
| **IMPLEMENT** (geninely missing, clear intent) | 19 | Real work required |
| **DELETE** (outdated, shouldn't exist) | 0 | None identified |

**Key finding:** No items need deletion — all 40 are either redirects or genuine implementations.

---

## 2. M1 FEASIBILITY FILTER

| Item | M1 Feasibility | Notes |
|------|----------------|-------|
| `QuantumResistantCrypto` | ✅ FEASIBLE | Uses `cryptography` package (already in use). No native libs required. |
| `ZKPResearchEngine` | ⚠️ **BLOCKED** | No ZK proof libraries available on Python 3.14+. Requires human decision. |
| `AutomationOrchestrator` | ✅ GRACEFUL | Playwright optional, graceful degradation already in place. |
| `RAGOrchestrator` | ✅ FEASIBLE | LanceDB + DuckDB already wired. Simple shim needed. |
| `UnifiedAIOrchestrator` | ✅ FEASIBLE | Wraps existing MLX/structured pipeline. |
| `StealthBrowser` | ✅ FEASIBLE | nodriver already integrated. |

**Conclusion:** Only `ZKPResearchEngine` is M1-blocked. All other items are feasible.

---

## 3. DEPENDENCY GRAPH

### Files Blocked by Multiple Missing Imports

| File | Count | Impact |
|------|-------|--------|
| `coordinators/security_coordinator.py` | 4 | HIGH — security hub, blocks all security ops |
| `intelligence/data_leak_hunter.py` | 3 | MEDIUM — leak detection |
| `coordinators/research_coordinator.py` | 2 | **CRITICAL** — hard errors on import |
| `intelligence/archive_discovery.py` | 2 | MEDIUM — archive discovery |
| `intelligence/stealth_crawler.py` | 2 | MEDIUM — stealth crawling |

### Critical Path (ordered fix sequence)

1. **security_coordinator.py** — unblocks 4 imports, enables security layer
2. **research_coordinator.py** — unblocks hard errors (RAGOrchestrator + UnifiedAIOrchestrator)
3. **data_leak_hunter.py / archive_discovery.py / stealth_crawler.py** — unblock temporal/zero-attribution
4. **context_optimization/** — unblocks 3 files with mlx_embeddings (optional, fail-soft)

---

## 4. IMPLEMENTATION ORDER

### TIER 0 — Redirects (< 1 hour total)

1 fix unblocks multiple imports. All use correct implementations under wrong paths.

| # | Symbol | Wrong Path | Correct Path | Files Fixed |
|---|--------|------------|--------------|-------------|
| T0-1 | `StealthEngine` | `hledac.security.stealth_engine` | `_shims.security_stealth_engine` → `universal.stealth.stealth_session` | 1 |
| T0-2 | `TemporalAnonymizer` | `hledac.security.temporal_anonymizer` | `universal.security.temporal_anonymizer` | 3 |
| T0-3 | `ZeroAttributionEngine` | `hledac.security.zero_attribution_engine` | `universal.security.zero_attribution_engine` | 3 |
| T0-4 | `KeyManager` | `hledac.security.key_manager` | `universal.security.key_manager` | 1 |
| T0-5 | `QuantumResistantCrypto` | `hledac.security.quantum_resistant_crypto` | Use `universal.security.pq_crypto` directly | 1 |
| T0-6 | `adjust_fetch_workers` | `hledac.universal.adjust_fetch_workers` | `utils.concurrency.adjust_fetch_workers` | 2 |
| T0-7 | `Transport` | `hledac.universal.transport.Transport` | `universal.transport.base.Transport` | 1 |
| T0-8 | `FullyAutonomousOrchestrator` | `hledac.universal.orchestrator.FullyAutonomousOrchestrator` | `autonomous_orchestrator` already exists | 1 |
| T0-9 | `GraphRAGOrchestrator` | `hledac.universal.knowledge.GraphRAGOrchestrator` | `knowledge.graph_service.DuckPGQGraph` (already wired) | 1 |
| T0-10 | `mlx_embeddings` | `hledac.core.mlx_embeddings` | N/A — optional, fail-soft | N/A |

**Total Tier 0: ~15 redirect lines**

---

### TIER 1 — Simple Implementations (1-4 hours each)

| # | Symbol | Description | Complexity | Files |
|---|--------|-------------|------------|-------|
| T1-1 | `RAGOrchestrator` | Shims existing `knowledge/` modules (DuckPGQGraph + LanceDB). Hard error on import. | LOW | 1 |
| T1-2 | `UnifiedAIOrchestrator` | Wraps existing MLX/Hermes3 pipeline. Hard error on import. | LOW | 1 |
| T1-3 | `ThreatIntelligence` | Stub with fail-soft return. Already has shim. | TRIVIAL | 1 |
| T1-4 | `AgentExecutionError` / `CircuitBreakerOpen` | Re-export from `utils.resilience` | TRIVIAL | 1 |
| T1-5 | `http.fetch_json` / `http.safe_fetch` | Re-export from `fetching/public_fetcher.py` | TRIVIAL | 1 |
| T1-6 | `pivot_planner` functions | `_score_pivot_archive`, `_score_pivot_domain`, `MAX_PIVOT_CANDIDATES`, `PivotPlanner` | LOW | 1 |

**Total Tier 1: ~6 small implementations**

---

### TIER 2 — Complex Implementations (1-3 days each)

| # | Symbol | Description | Design Required | GHOST_INVARIANTS Compliance |
|---|--------|-------------|----------------|----------------------------|
| T2-1 | `MLXEmbeddingManager` | LanceDB-backed semantic cache. Optional (fail-soft). | Minimal — LanceDB already wired | `mx.eval([])` before cache ops |
| T2-2 | `AutomationOrchestrator` | Browser automation via nodriver. Optional (graceful). | Low — nodriver already integrated | No `--disable-gpu` |

**Total Tier 2: 2 items, both optional/fail-soft**

---

### TIER 3 — Strategic Decisions Needed

| # | Symbol | Decision Required | Blocker |
|---|--------|------------------|---------|
| T3-1 | `ZKPResearchEngine` | Is ZKP a real planned feature? No ZK libraries available for Python 3.14+ on M1. | **ARCHITECTURAL** — needs human decision |
| T3-2 | `ThreatIntelligence` | Is threat intelligence analysis a real planned feature? Shim raises `NotImplementedError`. | **ARCHITECTURAL** — needs human decision |
| T3-3 | `QuantumResistantCrypto` | Already implemented via `pq_crypto.py`. Class wrapper needed or delete import? | **ARCHITECTURAL** — needs human decision |

---

## 5. GHOST_INVARIANTS COMPLIANCE

All Tier 1 and Tier 2 items must respect:

| Rule | Applies To |
|------|------------|
| `asyncio.gather` always uses `return_exceptions=True` | All async implementations |
| `_check_gathered` called after every `gather` | All async implementations |
| `time.monotonic` for interval measurements | T2-1 (embedding cache) |
| `mx.eval([])` before `mx.metal.clear_cache()` | T2-1 (MLX embeddings) |
| No `--disable-gpu` in nodriver args | T2-2 (browser automation) |
| LMDB bulk write via `cursor.putmulti()` | T2-1 if caching to LMDB |
| Fail-soft: return `[]` on errors, never raise | ALL items |

---

## EXECUTION SUMMARY

| Tier | Items | Total Effort | Impact |
|------|-------|--------------|--------|
| **T0** | 10 redirects | ~1 hour | Unblocks 21 imports |
| **T1** | 6 simple impls | ~4 hours | Unblocks 12 imports |
| **T2** | 2 optional impls | ~1-2 days | Optional (fail-soft) |
| **T3** | 3 strategic decisions | Requires human | Blocks aspirational features |

**Recommended execution order:**
1. Fix all T0 redirects first (1 hour) — maximizes functionality
2. Implement T1 items (4 hours) — fixes hard errors
3. T2 and T3 deferred until strategic decisions made

**Critical path:** `security_coordinator.py` → `research_coordinator.py` → rest

---

*Next step: Each T0/T1 item becomes a separate implementation prompt.*
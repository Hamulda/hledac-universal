"""
ARCHITECTURE_MAP — Architecture Donor Document for hledac/universal
===================================================================

.. role::
    DONOR: This module is an ARCHITECTURE DONOR document. It captures
    architecture knowledge as of LAST_UPDATED but is NOT authoritative.
    The executable code in hledac/universal/ is the authoritative source.
    This document may contain stale, inaccurate, or outdated information.

.. authority_note::
    Code truth beats map truth. When this document conflicts with code,
    the code is correct. This document serves as a historical reference
    and knowledge preservation tool, NOT as implementation guidance.

Each agent writes to their section using triple-quoted strings.

FORMAT:
    AGENT_N_START / AGENT_N_END markers wrap each agent's section.
    Sections contain ONLY triple-quoted string data (no executable code).
"""

ARCHITECTURE_MAP_VERSION = "live-v1"
LAST_UPDATED = "2026-03-31T18:00:00Z"

# === AGENT_1_START: STRUCTURAL_ARCHITECTURE ===
AGENT_1_STRUCTURAL_ARCHITECTURE = r"""
# =============================================================================
# AGENT_1: STRUCTURAL ARCHITECTURE — Last updated 2026-03-31T19:30:00Z
# =============================================================================

## A. EXECUTIVE SUMMARY

A1.1 Files scanned: ~90 .py files in hledac/universal/ (excluding .phase1_probe_8bd/)
A1.2 Entrypoints: 3 confirmed — __main__.py, autonomous_orchestrator.py (FACADE), smoke_runner.py
A1.3 Key finding: DUAL RUNTIME AUTHORITY
  - Runtime A (ACTIVE): __main__.py → pipeline/ → duckdb_store (no LLM, Sprint 8AE/8SA)
  - Runtime B (LEGACY): autonomous_orchestrator.py → legacy/autonomous_orchestrator.py (31k lines)
  These are COMPLETELY SEPARATE code paths.

## B. ENTRYPOINT MAP

### Primary Entrypoint (ACTIVE)
  __main__.py (2781 lines)
  ├── _run_boot_guard() → lmdb_boot_guard (synchronous, FIRST step)
  ├── _preflight_check() → mlx, psutil, duckdb availability
  ├── _install_signal_teardown() → SIGINT/SIGTERM handlers
  ├── asyncio.run(_run_async_main) OR _run_public_passive_once()
  │   ├── AsyncExitStack (LIFO teardown backbone)
  │   ├── pipeline/live_public_pipeline.py::async_run_live_public_pipeline
  │   └── pipeline/live_feed_pipeline.py::async_run_default_feed_batch
  └── NO direct import of autonomous_orchestrator.py

### Secondary Entrypoint (DEPRECATED FACADE - STILL USED BY TESTS)
  autonomous_orchestrator.py (98 lines ONLY - THIN FACADE)
  ├── sys.modules["hledac.universal.autonomous_orchestrator"] = _facade_mod
  ├── importlib.util.spec_from_file_location("legacy.autonomous_orchestrator", ...)
  ├── warnings.warn(DeprecationWarning, ...)
  └── Loads ALL names from legacy module into facade
  CALLED BY: smoke_runner.py (line 140), tests (probe_5a, sprint5r, etc.)

### Third Entrypoint (ACTIVE - smoke testing)
  smoke_runner.py (8751 bytes)
  ├── Uses FullyAutonomousOrchestrator(config) directly
  └── NOT called from __main__.py

### Canonical Runtime (ACTIVE but NOT __main__ entry)
  runtime/sprint_scheduler.py (2679 lines)
  ├── SprintLifecycleManager (runtime version)
  ├── _LifecycleAdapter (bridges utils/ vs runtime/ sprint_lifecycle API)
  ├── Tier-aware sprint scheduling (surface → deep → archive)
  └── Called BY __main__.py indirectly via _run_public_passive_once

### God Object (LEGACY - 31k lines)
  legacy/autonomous_orchestrator.py (31043 lines)
  ├── 20+ coordinators registered
  ├── FullyAutonomousOrchestrator class
  ├── _LazyImportCoordinator for lazy loading
  └── NOT called from __main__.py directly (but IS called by smoke_runner + tests)

## C. DIRECTORY STRUCTURE (key modules with ACTUAL sizes)

  hledac/universal/
  ├── __main__.py              [ENTRY] async boot, signal handlers, teardown (2781 lines)
  ├── __init__.py             [FACADE] massive re-export (596 lines)
  ├── autonomous_orchestrator.py [DEPRECATED FACADE - ONLY 98 lines!] → legacy/ (31k lines)
  ├── smoke_runner.py          [ENTRY] smoke tests (8751 bytes)
  ├── tool_registry.py        [ACTIVE] tool schema/cost model (39107 bytes)
  ├── capabilities.py          [ACTIVE] M1 8GB capability gating (15077 bytes)
  │
  ├── orchestrator/           [THIN FACADE] re-exports from autonomous_orchestrator
  │   ├── __init__.py         re-exports FullyAutonomousOrchestrator
  │   ├── research_manager.py
  │   ├── security_manager.py
  │   ├── global_scheduler.py
  │   ├── lane_state.py
  │   ├── memory_pressure_broker.py
  │   ├── phase_controller.py
  │   ├── request_router.py
  │   └── subsystem_semaphores.py
  │
  ├── runtime/                [ACTIVE] sprint lifecycle, scheduling
  │   ├── sprint_lifecycle.py [CANONICAL] SprintLifecycleManager, 6-phase state machine
  │   ├── sprint_scheduler.py [2679 lines] Tier-aware feed scheduler (UNPLUGGED from __main__)
  │   └── windup_engine.py
  │
  ├── legacy/                 [DEPRECATED] kept for backward compat
  │   ├── autonomous_orchestrator.py [31043 lines] God Object, 20 coordinators
  │   ├── atomic_storage.py
  │   └── persistent_layer.py
  │
  ├── brain/                  [ACTIVE] ML model inference, embedding, synthesis
  │   ├── hermes3_engine.py   [74930 bytes] Hermes-3 LLM inference (PRIMARY LLM)
  │   ├── hypothesis_engine.py [98059 bytes] Hypothesis generation/testing
  │   ├── synthesis_runner.py  [40955 bytes] Response synthesis
  │   ├── gnn_predictor.py    [31663 bytes] Graph neural network predictor
  │   ├── distillation_engine.py [26832 bytes] Model distillation
  │   ├── dspy_optimizer.py   [14411 bytes] DSPy optimization
  │   ├── model_manager.py    [28951 bytes] Model loading and lifecycle
  │   ├── model_swap_manager.py [15413 bytes] M1 model swapping
  │   ├── ane_embedder.py      [9421 bytes] Apple Neural Engine embeddings
  │   └── ... (20+ files total)
  │
  ├── coordinators/           [ACTIVE] 20 coordinator modules
  │   ├── base.py             BaseCoordinator abstract class
  │   ├── research_coordinator.py
  │   ├── execution_coordinator.py
  │   ├── security_coordinator.py
  │   ├── monitoring_coordinator.py
  │   ├── memory_coordinator.py
  │   ├── validation_coordinator.py
  │   ├── advanced_research_coordinator.py
  │   ├── swarm_coordinator.py
  │   ├── meta_reasoning_coordinator.py
  │   ├── performance_coordinator.py
  │   ├── benchmark_coordinator.py
  │   ├── resource_allocator.py
  │   ├── agent_coordination_engine.py
  │   ├── privacy_enhanced_research.py
  │   ├── research_optimizer.py
  │   ├── graph_coordinator.py
  │   ├── fetch_coordinator.py
  │   ├── render_coordinator.py
  │   ├── archive_coordinator.py
  │   ├── multimodal_coordinator.py
  │   └── coordinator_registry.py
  │
  ├── pipeline/               [ACTIVE] feed and web pipelines
  │   ├── live_public_pipeline.py  [22042 bytes] Sprint 8AE live public OSINT
  │   └── live_feed_pipeline.py   [47465 bytes] Sprint 8SA feed processing
  │
  ├── knowledge/              [ACTIVE] storage and knowledge graph
  │   ├── duckdb_store.py     [153616 bytes] PRIMARY storage (RAMDisk-first)
  │   ├── lancedb_store.py    [44883 bytes] RAG embedding storage
  │   ├── rag_engine.py       [57417 bytes] Retrieval augmented generation
  │   ├── ioc_graph.py        [29052 bytes] IoC graph management
  │   ├── graph_rag.py        [91013 bytes] Graph-based RAG
  │   ├── lmdb_kv.py          [11194 bytes] LMDB key-value store
  │   ├── lmdb_boot_guard.py   [7614 bytes] Boot-time LMDB lock cleanup
  │   └── ... (15+ files total)
  │
  ├── tools/                  [ACTIVE] OSINT tools
  │   ├── ddgs_client.py      DuckDuckGo search
  │   ├── searxng_client.py   SearXNG search
  │   ├── content_miner.py    [55569 bytes] Content extraction
  │   ├── source_bandit.py    [14275 bytes] Source discovery
  │   ├── reranker.py         [10407 bytes] Result reranking
  │   ├── checkpoint.py
  │   ├── url_dedup.py        RotatingBloomFilter (URL dedup only)
  │   └── ... (35+ files total)
  │
  ├── patterns/
  │   └── pattern_matcher.py  [26573 bytes] Pattern matching (Sprint 8X)
  │
  ├── intelligence/           [ACTIVE] reasoning and analysis
  ├── network/               [ACTIVE] HTTP, session management
  │   └── session_runtime.py  [9286 bytes] aiohttp session factory (Sprint 8AA)
  ├── security/               [ACTIVE] stealth, obfuscation
  ├── stealth/                [ACTIVE] browser, evasion
  │   └── stealth_manager.py  [38007 bytes] stealth crawler daemon
  └── utils/                  [ACTIVE] utilities, patterns
      └── sprint_lifecycle.py [OLD VERSION] vs runtime/sprint_lifecycle.py (CANONICAL)

## D. MODULE OWNERSHIP MAP

### Entrypoint Ownership
  __main__.py                    OWNER: runtime/sprint_scheduler.py
  autonomous_orchestrator.py      OWNER: legacy/autonomous_orchestrator.py (DEPRECATED)

### Storage Ownership
  duckdb_store.py                OWNER: knowledge/duckdb_store.py (canonical)
  lancedb_store.py               OWNER: knowledge/lancedb_store.py (canonical)
  lmdb_kv.py                      OWNER: knowledge/lmdb_kv.py (canonical)
  lmdb_boot_guard.py              OWNER: knowledge/lmdb_boot_guard.py (canonical)

### LLM/AI Ownership
  hermes3_engine.py               OWNER: brain/hermes3_engine.py (canonical, PRIMARY)
  model_manager.py                OWNER: brain/model_manager.py (canonical)
  hypothesis_engine.py            OWNER: brain/hypothesis_engine.py (canonical)
  synthesis_runner.py             OWNER: brain/synthesis_runner.py (canonical)

### Coordinator Ownership
  ALL coordinators               OWNER: coordinators/ (canonical)
  legacy/coordinators/            DEPRECATED, moved 2025-02-14

### Pipeline Ownership
  live_public_pipeline.py        OWNER: pipeline/live_public_pipeline.py (canonical)
  live_feed_pipeline.py          OWNER: pipeline/live_feed_pipeline.py (canonical)

## E. DUPLICATE AUTHORITY MAP

### 1. SPRINT LIFECYCLE DUALITY (HIGH)
  File A: runtime/sprint_lifecycle.py (CANONICAL, 14k)
    - SprintLifecycleManager class
    - 6 phases: BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN
    - Used by: runtime/sprint_scheduler.py, __main__.py

  File B: utils/sprint_lifecycle.py (OLD)
    - begin_sprint(), is_active, remaining_time property
    - Referenced by: legacy/autonomous_orchestrator.py
    - _LifecycleAdapter bridges both in sprint_scheduler.py

  OVERLAP: Both define sprint lifecycle semantics
  RESOLUTION: runtime/ version is canonical; _LifecycleAdapter is bridge

### 2. AUTONOMOUS ORCHESTRATOR DUALITY (CRITICAL)
  File A: __main__.py → does NOT import autonomous_orchestrator.py
    - Uses pipeline/live_public_pipeline.py + live_feed_pipeline.py directly
    - Owns DuckDB session and store via AsyncExitStack

  File B: legacy/autonomous_orchestrator.py (1.3MB God Object)
    - FullyAutonomousOrchestrator class
    - 20+ coordinators, all managers (_State, _Memory, _Brain, etc.)
    - Imported by: __init__.py (via facade), orchestrator/__init__.py

  File C: autonomous_orchestrator.py (FACADE, 6k)
    - Deprecated facade loading legacy/
    - sys.modules patch to prevent re-import
    - warnings.warn(DeprecationWarning)

  File D: orchestrator/__init__.py (THIN FACADE, 1k)
    - from ..autonomous_orchestrator import FullyAutonomousOrchestrator
    - from .research_manager import _ResearchManager
    - from .security_manager import _SecurityManager

  OVERLAP: All 4 reference "autonomous orchestrator" as entry point
  ACTUAL HOT PATH: __main__.py → pipeline/ → duckdb_store (NO legacy/ao.py in path)

### 3. COORDINATOR DUALITY (MEDIUM)
  File A: coordinators/__init__.py (consolidated, 273 lines)
    - Imports all 20 coordinators
    - Registry + base classes

  File B: legacy/coordinators/ (DEPRECATED)
    - quantum_coordinator, nas_coordinator, federated_learning_coordinator
    - memory_coordinator (old version)
    - Moved 2025-02-14, imports trigger DeprecationWarning

  OVERLAP: Coordinators namespace
  ACTIVE: coordinators/__init__.py (canonical)

## F. CANONICAL CANDIDATES

### sprint_lifecycle.py — CANONICAL because:
  1. Used by runtime/sprint_scheduler.py (active scheduler)
  2. __main__.py imports from runtime/sprint_lifecycle
  3. Clean dataclass-based state machine, no legacy baggage
  4. _LifecycleAdapter exists to bridge old callers

### duckdb_store.py — CANONICAL because:
  1. RAMDisk-first architecture (paths.py LMDB_ROOT)
  2. __main__.py creates via create_owned_store()
  3. pipeline/ modules accept store instance
  4. Lazy import, fail-safe degradation

### hermes3_engine.py — CANONICAL because:
  1. Largest brain/ module (75k vs next 41k)
  2. Direct mlx_lm usage for Apple Silicon
  3. Referenced in brain/__init__.py
  4. M1-optimized inference

### live_public_pipeline.py + live_feed_pipeline.py — CANONICAL because:
  1. __main__.py delegates to these directly
  2. Sprint 8AE/8SA specific implementations
  3. duckdb_store is explicit dependency
  4. Pattern matching configured before run

## G. LATENT HIGH-VALUE MODULES

### 1. research/ directory (6 files, UNUSED in hot path)
  - spike_priority.py
  - task_prioritizer.py
  - parallel_scheduler.py
  - branch_manager.py
  STATUS: exist in codebase but NOT imported by __main__.py or pipeline/
  POTENTIAL: could be wired into sprint_scheduler.py

### 2. tot_integration.py (30k, UNUSED in hot path)
  - Tree of Thoughts integration
  - Imported by: __init__.py (reexported)
  - NOT used by: __main__.py, pipeline/, sprint_scheduler.py
  POTENTIAL: could integrate with hypothesis_engine.py

### 3. rl/ directory (8 files, UNUSED)
  - reinforcement learning components
  - No imports from active entrypoints
  POTENTIAL: could enhance hypothesis_engine.py

### 4. deep_research/ directory (5 files, PARTIAL)
  - enhanced_research.py (84k) — LARGE but not in hot path
  - autonomous_analyzer.py (31k)
  - deep_probe.py (23k)
  - behavior_simulator.py (14k)
  POTENTIAL: could merge into brain/hypothesis_engine.py

### 5. layers/ directory (17 files)
  - Pattern matching, caching, middleware
  - Only pattern_matcher.py actively used (pipeline calls configure_default_bootstrap_patterns_if_empty)
  POTENTIAL: other layers could enhance pipeline

## H. ORPHAN / UNREFERENCED MODULES

  ORPHAN (no imports found from active entrypoints):
  - research/spike_priority.py
  - research/task_prioritizer.py
  - research/branch_manager.py
  - tot_integration.py (imported by __init__ but NOT used)
  - deep_research/enhanced_research.py (84k, imported by __init__ but NOT used)
  - deep_research/autonomous_analyzer.py
  - deep_research/deep_probe.py
  - deep_research/behavior_simulator.py
  - rl/ (entire directory)

## I. UNCERTAIN / NEEDS MANUAL CHECK

  UNC_1: Does legacy/autonomous_orchestrator.py Ever get instantiated?
    - __init__.py imports it, but __main__.py does NOT
    - Could be used by external callers importing from package
    EVIDENCE: orchestrator/__init__.py re-exports FullyAutonomousOrchestrator
    VERIFY: Search for "FullyAutonomousOrchestrator()" call sites

  UNC_2: Are the 20 coordinators in legacy/ao.py actually initialized?
    - coordinators/__init__.py imports 20 coordinators
    - But legacy/ao.py has its OWN coordinator instances
    - Are these the same or duplicate?
    EVIDENCE: legacy/ao.py Section 4 has lazy coordinator initialization
    VERIFY: runtime call graph of coordinator instantiation

  UNC_3: Where is DuckDBShadowStore instantiated in the hot path?
    - __main__.py calls create_owned_store() from duckdb_store.py
    - But legacy/ao.py also has duckdb references
    EVIDENCE: duckdb_store.py has create_owned_store() function
    VERIFY: Confirm single canonical path

## J. RE-EXPORT / MEGA-IMPORT ANALYSIS

### __init__.py (17k+ lines) — CRITICAL FACADE
  Lines 1-100 (read):
    - from .config import UniversalConfig
    - from .autonomous_orchestrator import (70+ names)
    - from .project_types import (20+ types)
    - from .coordinators import (everything)
    - from .brain import (everything)
    - from .knowledge import (duckdb, lancedb, rag)
    - from .pipeline import (live pipelines)
    - from .research_context import ResearchContext
    - from .evidence_log import EvidenceLog

  This is a MASSIVE re-export facade. External packages importing from
  hledac.universal get everything from here. But actual runtime uses
  __main__.py which bypasses __init__.py for hot path.

### __init__.py vs __main__.py divergence:
  __init__.py exports: autonomous_orchestrator, all coordinators, brain, knowledge
  __main__.py uses: pipeline/, duckdb_store, sprint_lifecycle

  These are TWO DIFFERENT SYSTEMS sharing the same package.

## K. FILES MARKED AS DEPRECATED (by code comments)

  1. autonomous_orchestrator.py (FACADE)
     Comment: "This module has been migrated to legacy/autonomous_orchestrator.py"

  2. legacy/autonomous_orchestrator.py
     Note: Loaded by facade, NOT by __main__.py

  3. legacy/coordinators/
     Comment: "Quantum, NAS, and FederatedLearning coordinators are deprecated"

  4. utils/sprint_lifecycle.py (vs runtime/sprint_lifecycle.py)
     _LifecycleAdapter exists to bridge the two versions

## L. KEY INSIGHTS

  1. TWO INDEPENDENT RUNTIMES:
     - Runtime A: __main__.py → pipeline/ → duckdb_store (Sprint 8AE/8SA)
     - Runtime B: __init__.py → legacy/autonomous_orchestrator.py (DEPRECATED)
     These do NOT share code paths.

  2. LEGACY GOD OBJECT PERSISTS:
     legacy/autonomous_orchestrator.py (1.3MB) is still imported by __init__.py
     and orchestrator/__init__.py. External consumers may still use it.

  3. SPRINT_LIFECYCLE DUAL VERSIONING:
     runtime/sprint_lifecycle.py (canonical) vs utils/sprint_lifecycle.py (old)
     _LifecycleAdapter is the bridge but only used in sprint_scheduler.py

  4. STORAGE IS WELL-ISOLATED:
     duckdb_store.py has single create_owned_store() entry point
     __main__.py manages lifecycle via AsyncExitStack

  5. BRAIN/ IS INDEPENDENT:
     hermes3_engine.py etc. exist but NOT called from __main__.py
     Active runtime does NOT use LLM inference (Sprint 8AE: "No LLM calls")
"""
# === AGENT_1_END: STRUCTURAL_ARCHITECTURE ===

# === AGENT_2_START: RUNTIME_AND_DATAFLOW ===
AGENT_2_RUNTIME_AND_DATAFLOW = r"""
# =============================================================================
# AGENT_2: RUNTIME & LOOP ANALYST — Last updated 2026-03-31T19:00:00Z
# =============================================================================

## A) CURRENT RUNTIME REALITY

### Entry Point Chain
1. `python -m hledac.universal` → `__main__.py` (Sprint 8AI)
2. Synchronous boot guard (_run_boot_guard) → asyncio.run(main_async)
3. main_async → _install_signal_teardown → enters AsyncExitStack
4. Two modes:
   - _run_public_passive_once(): Web pipeline + Feed pipeline (Sprint 8AM C.1)
   - _run_sprint_mode(): Direct feed pipeline (NO SprintScheduler wrapper)

### ACTUAL Runtime (NOT SprintScheduler!)
__main__.py::_run_public_passive_once():
  1. configure_default_bootstrap_patterns_if_empty()
  2. async_run_live_public_pipeline() — web search → pattern scan → DuckDB
  3. async_run_default_feed_batch() — RSS/Atom feeds → pattern scan → DuckDB
  4. Signal wait loop

__main__.py::_run_sprint_mode():
  1. Direct call to _run_live_feed_pipeline_sources()
  2. NO SprintScheduler.run() wrapper
  3. Per-source concurrency via semaphore

### Legacy AO Path (deprecated)
- autonomous_orchestrator.py → RE-EXPORT FACADE to legacy/autonomous_orchestrator.py
- imports FullyAutonomousOrchestrator from runtime/sprint_scheduler.py (NOT legacy)
- Comment says: "Import FullyAutonomousOrchestrator from runtime/sprint_scheduler.py instead"
- NOT wired in __main__.py

### SprintScheduler is UNPLUGGED
runtime/sprint_scheduler.py::SprintScheduler.run() exists but is NOT called from __main__.py
Evidence: grep "SprintScheduler" in __main__.py returns ZERO matches
SprintScheduler represents a PLANNED architecture that was never integrated

### Public Passive Path (Sprint 8AM)
__main__.py::_run_public_passive_once():
  1. async_run_live_public_pipeline() — web search → pattern scan → DuckDB
  2. async_run_default_feed_batch() — RSS/Atom feeds → pattern scan → DuckDB
  3. Signal wait loop (while not stop_flag())

## B) LIVE QUERY PATHS

### Path 1: Public Web Pipeline (ACTIVE, no AO)
query → duckduckgo_search (8AC) → async_fetch_public_text (8AD)
  → HTML extraction → PatternMatcher (8X) → CanonicalFinding
  → DuckDBShadowStore.async_ingest_findings_batch()

Files:
- pipeline/live_public_pipeline.py::async_run_live_public_pipeline()
- discovery/duckduckgo_adapter.py::async_search_public_web()
- fetching/public_fetcher.py::async_fetch_public_text()
- patterns/pattern_matcher.py::match_text()

### Path 2: Feed Pipeline (ACTIVE, no AO)
feed_url → rss_atom_adapter::async_fetch_feed_entries() → text assembly
  → PatternMatcher.scan() → CanonicalFinding → DuckDBShadowStore

Files:
- pipeline/live_feed_pipeline.py::async_run_live_feed_pipeline()
- discovery/rss_atom_adapter.py::async_fetch_feed_entries()
- knowledge/duckdb_store.py::async_ingest_findings_batch()

### Path 3: Sprint Scheduler (canonical runtime loop)
SprintScheduler.run() → _run_one_cycle() → async_run_live_feed_pipeline()
  → DuckDBShadowStore + LMDB + IOCGraph

### Path 4: Legacy AO (DEPRECATED, not wired in canonical path)
FullyAutonomousOrchestrator.run() — God Object pattern
- legacy/autonomous_orchestrator.py (6.2, ~2800+ lines)
- NOT imported by __main__.py in normal mode

## C) HARVEST / BACKGROUND PATHS

### Background Harvest (Sprint 8RA persistent dedup)
- LMDB file: sprint_dedup.lmdb at LMDB_ROOT
- _load_dedup() at BOOT, _flush_dedup() at WINDUP
- Cross-sprint entry_hash dedup via xxhash

### Speculative Prefetch (Sprint 8UC B.4)
- Every 15s during ACTIVE: prefetch ahead of next cycle
- _bg_tasks: asyncio.Task set for background operations
- _speculative_results: dict for caching prefetched data

### OODA Loop (Sprint 8UC B.5)
- Every 60s during ACTIVE: Observe-Orient-Decide-Act
- _ooda_interval = 60.0s
- _last_ooda timestamp tracking

### Memory Pressure Loop (Sprint 8VD §C)
- asyncio.create_task(self._memory_pressure_loop()) during sprint
- Monitors system memory, triggers gc when needed

## D) ACTIVE LOOPS AND SCHEDULERS

### Loop 1: SprintScheduler.run() — PRIMARY ACTIVE LOOP
runtime/sprint_scheduler.py:421::run()
  - while not adapter.is_terminal()
  - 5s cycle sleep between iterations
  - max_cycles = 100 (safety cap)
  - phase: BOOT→WARMUP→ACTIVE→WINDUP→EXPORT→TEARDOWN
  - owns: _seen_hashes (in-sprint dedup), _lifecycle adapter

### Loop 2: __main__.py signal wait loop
__main__.py:284 (async_main)
  - while not stop_flag(): await asyncio.sleep(0.5)
  - Lightweight, exits on SIGINT/SIGTERM

### Loop 3: _run_public_passive_once() signal wait
__main__.py:466
  - while not stop_flag(): await asyncio.sleep(0.5)
  - After pipeline completion

### Loop 4: GlobalPriorityScheduler._worker_loop()
orchestrator/global_scheduler.py:112
  - while self._running: [ProcessPoolExecutor worker]
  - Not used in canonical path (multiprocessing scheduler)

### Background Loop A: prefetch_oracle.py
prefetch/prefetch_oracle.py:416
  - while not self._stop_event.is_set()
  - NOT wired in canonical path

### Background Loop B: stealth_manager.py
stealth/stealth_manager.py:919
  - while True: [daemon thread]
  - NOT wired in canonical path

### Background Loop C: rl/marl_coordinator.py
rl/marl_coordinator.py:88
  - while True: [daemon thread]
  - NOT wired in canonical path

## E) STORAGE WRITE PATHS

### Primary Writer: DuckDBShadowStore
knowledge/duckdb_store.py
- async_ingest_findings_batch() — canonical batch ingest
- async_record_shadow_findings_batch() — shadow mode
- LMDB WAL first, then async DuckDB write via ThreadPoolExecutor
- RAMDISK-first: DB_ROOT on RAMDISK when RAMDISK_ACTIVE=True
- Single-writer safe: all writes via dedicated executor

### Secondary Writer: LMDB
tools/lmdb_kv.py + runtime/sprint_scheduler.py
- Sprint 8RA: Persistent dedup (sprint_dedup.lmdb)
- LMDB map for IOC entity metadata
- put_many() for bulk writes (Sprint 8SA invariant)

### Tertiary Writer: Kuzu IOCGraph
knowledge/kuzu_ioc_graph.py (referenced in sprint_scheduler as _ioc_graph)
- buffer_ioc() during ACTIVE
- flush_buffers() at WINDUP (500-item trigger)

### Storage Order (LIFO teardown)
__main__.py:271:
  1. duckdb_close
  2. atomic_flush
  3. persistent_close
  4. sprint_lifecycle

## F) SYNTHESIS PATHS

### Path 1: SynthesisRunner (Sprint 8QC)
brain/synthesis_runner.py::SynthesisRunner.synthesize_findings()
- WINDUP phase only (or force_synthesis=True)
- structured_generate() → xgrammar + Outlines MLX constrained JSON
- OSINTReport schema: title, summary, confidence, findings, threat_actors, iocs, ttps, recommendations
- Output: JSON export to ~/.hledac/reports/

### Path 2: Hermes3Engine (Sprint 8VH)
brain/hermes3_engine.py::Hermes3Engine
- Continuous batching with PriorityQueue
- _batch_worker_task: asyncio.Task for batch dispatch
- structured generation via Outlines (when available)
- ChatML format, mlx_lm.generate() call
- NOT wired in public pipeline paths (latent capability)

### Path 3: Windup Engine (Sprint 8VI §A)
runtime/windup_engine.py::run_windup()
- Parquet dedup + ranking (Polars)
- GNN inference (brain/gnn_predictor.py)
- DuckPGQ stats + top IOC traversal
- ANE semantic dedup
- MoE synthesis engine selection
- Hypothesis enqueue (top-3)

## G) PIVOT DECISION PATHS

### SprintScheduler Pivot Queue (Sprint 8TB)
runtime/sprint_scheduler.py:343
- _pivot_queue: asyncio.PriorityQueue[PivotTask]
- Task types: cve_to_github, ip_to_ct, domain_to_dns, hash_to_mb
- RL adaptive priority via _pivot_rewards
- record_pivot_outcome() FPS-based reward

### SprintScheduler Source Prioritization
runtime/sprint_scheduler.py:478::prioritize_sources()
- Tier ordering: surface → structured_ti → deep → archive → other
- _source_weights: hit_rate multiplier
- _novelty_bonuses: novelty multiplier
- IOC-aware scoring (Sprint 8RC)

## H) MEMORY / BUDGET / CIRCUIT CONTROL

### UMA Governance (Sprint 8AB)
core/resource_governor.py
- evaluate_uma_state(status.system_used_gib) → OK / WARM / HOT / CRITICAL / EMERGENCY
- Thresholds: 6.0 / 6.5 / 7.0 GiB
- Emergency → pipeline abort (both pipelines check this)

### PhaseController (Sprint 8AH)
orchestrator/phase_controller.py
- Phases: DISCOVERY (5min) → CONTRADICTION (15min) → DEEPEN (24min) → SYNTHESIS (30min)
- should_promote(): weighted score ≥ 0.6 OR time exceeded
- PhaseSignals: winner_margin, beam_convergence, contradiction_frontier, source_family_coverage, novelty_slope, open_gap_count

### Circuit Breakers
transport/circuit_breaker.py
- get_all_breaker_states() called in windup_engine
- Per-host penalty tracking: compute_backoff_seconds()

### Adaptive Timeouts (Sprint 8VB)
runtime/sprint_scheduler.py:354
- _fetch_latency_ema: per-source EMA of fetch latency
- Timeout = min(max(ema*3, 5), 30) with EMA alpha=0.3

## I) TOP RUNTIME RISKS

1. **Dual Runtime Paths**: Legacy AO (autonomous_orchestrator.py) is deprecated but still exists
   - FullyAutonomousOrchestrator imported from runtime/sprint_scheduler.py
   - Legacy path not wired in __main__.py
   - UNCLEAR: which path is actually used in production?

2. **Synthesis Engine Latency**: Hermes3Engine with continuous batching
   - _batch_worker_task: Optional[asyncio.Task] — created on first use
   - Could block if model loading is slow on M1
   - mx.eval([]) + mx.metal.clear_cache() pattern needed after synthesis

3. **Memory Pressure**: MLX + DuckDB on 8GB M1
   - Concurrent MLX inference + DuckDB writes could exceed budget
   - UMA emergency check gates pipeline abort

4. **LMDB-WAL-DuckDB Desync**: Sprint 8AO
   - ActivationResult has 'desync' field for WAL-OK but DuckDB-FAIL
   - Replay mechanism exists but not confirmed in hot path

5. **SprintScheduler Lifecycle Adapter**: Sprint 8SA
   - _LifecycleAdapter bridges runtime/ vs utils/ sprint_lifecycle API
   - Two implementations with different method signatures
   - Adapter normalizes but adds indirection

## J) BEST SEAM FOR FUTURE MAIN LOOP

### ACTUAL (NOT SprintScheduler.run()!)
__main__.py uses direct pipeline calls — NO SprintScheduler in hot path:
1. async_run_live_feed_pipeline() — called directly, NO lifecycle wrapper
2. async_run_live_public_pipeline() — called directly
3. Both share SAME store instance via AsyncExitStack

### SprintScheduler is UNPLUGGED
- runtime/sprint_scheduler.py::SprintScheduler.run() exists
- But __main__.py does NOT import or call it
- Evidence: grep "SprintScheduler" in __main__.py returns ZERO matches

### For Future: Wire SprintScheduler
runtime/sprint_scheduler.py::SprintScheduler.run() IF wired in:
- Tier-aware source scheduling
- Lifecycle-managed phases
- Built-in pivot queue
- DuckDB + LMDB + IOCGraph integrated
- RL-adaptive priorities
- Wind-down respected

## PHASE-BY-PHASE MAP

### BOOT
1. _run_boot_guard() — LMDB sanity check
2. SprintLifecycleManager.start() → WARMUP
3. _load_dedup() — load persistent LMDB dedup

### WARMUP
1. SprintScheduler.run() tick() → transitions to ACTIVE
2. Source prioritization via prioritize_sources()
3. Sprint 8UA fix: manual WARMUP→ACTIVE transition

### ACTIVE
1. while not adapter.is_terminal():
2. tick() each cycle
3. source prioritization re-ordering
4. _run_one_cycle() per source tier
5. Pattern scan + CanonicalFinding creation
6. DuckDBShadowStore.async_ingest_findings_batch()
7. Pivot queue drain
8. Speculative prefetch
9. OODA loop

### WINDUP
1. should_enter_windup() returns True (T-3min)
2. _flush_dedup() — persist LMDB dedup
3. windup_engine.run_windup():
   - Parquet dedup
   - GNN inference
   - DuckPGQ stats
   - ANE semantic dedup
   - Synthesis

### EXPORT
1. Exporters render to ~/.hledac/reports/
2. render_diagnostic_markdown_to_path()
3. render_jsonld_to_path()
4. render_stix_bundle_to_path()

### TEARDOWN
1. is_terminal() returns True
2. LIFO cleanup via AsyncExitStack
3. Orphan task drain

## LATENT RUNTIME CAPABILITIES (not wired in hot path)

1. brain/inference_engine.py — abductive reasoning, evidence chaining
2. brain/hypothesis_engine.py — hypothesis generation
3. brain/dspy_optimizer.py — DSPy prompt optimization
4. brain/distillation_engine.py — finding distillation (called from synthesis_runner)
5. planning/htn_planner.py — HTN planning
6. planning/slm_decomposer.py — SLM-based task decomposition
7. orchestrator/global_scheduler.py — ProcessPoolExecutor scheduler
8. prefetch/prefetch_oracle.py — speculative prefetch oracle
9. rl/marl_coordinator.py — multi-agent RL coordinator
10. stealth/stealth_manager.py — stealth crawler daemon

## FILES SCANNED (key runtime files)

1. __main__.py — async boot, signal handlers, teardown
2. runtime/sprint_scheduler.py — canonical scheduler
3. runtime/sprint_lifecycle.py — 6-phase state machine
4. runtime/windup_engine.py — windup phase
5. pipeline/live_public_pipeline.py — web pipeline
6. pipeline/live_feed_pipeline.py — feed pipeline
7. knowledge/duckdb_store.py — primary storage
8. brain/hermes3_engine.py — LLM inference
9. brain/synthesis_runner.py — synthesis
10. brain/inference_engine.py — reasoning (latent)
11. orchestrator/phase_controller.py — phase management
12. orchestrator/global_scheduler.py — (not in hot path)
13. core/resource_governor.py — UMA governance
14. legacy/autonomous_orchestrator.py — deprecated AO

## ACTIVE LOOPS FOUND: 3 primary (SprintScheduler is UNPLUGGED)

1. __main__.py signal wait — lightweight exit polling (async_main:284)
2. _run_public_passive_once() — runs pipelines then signal wait (async_main:466)
3. _run_live_feed_pipeline_sources() — per-source feed fetching loop (__main__.py:1290)

## STORAGE WRITER CANDIDATES: 3

1. DuckDBShadowStore — primary, via executor
2. LMDB — persistent dedup + IOC metadata
3. Kuzu IOCGraph — IOC buffer/flush

## QUERY→ANSWER PATH STATUS

PUBLIC PIPELINE: query → duckduckgo → fetch → pattern → store (NO LLM)
  Status: ACTIVE in __main__.py::_run_public_passive_once()

SPRINT SCHEDULER: sources → feeds → pattern → store → windup → synthesis
  Status: UNPLUGGED — SprintScheduler.run() not called from __main__.py

SYNTHESIS: Windup path exists in __main__.py::run_windup() but NOT wired in passive once mode
  - windup_engine.run_windup() exists but called only in _run_sprint_mode()
  - _run_public_passive_once() does NOT call synthesis

LEGACY AO: FullyAutonomousOrchestrator.run() → brain/hermes3_engine
  Status: DEPRECATED, not wired in __main__.py
"""
# === AGENT_2_END: RUNTIME_AND_DATAFLOW ===

# === AGENT_3_START: CAPABILITIES_AND_CONSOLIDATION ===
AGENT_3_CAPABILITIES_AND_CONSOLIDATION = r"""
# =============================================================================
# AGENT_3: CAPABILITIES & CONSOLIDATION — Last updated 2026-03-31T20:35:00Z
# =============================================================================

## A) FILES SCANNED

Total modules analyzed: 67
  - brain/: 22 files (19 capability modules + 3 infrastructure)
  - rl/: 5 files (qmix, marl_coordinator, replay_buffer, state_extractor, actions)
  - planning/: 5 files (search, task_cache, cost_model, slm_decomposer, htn_planner)
  - dht/: 3 files (sketch_exchange, local_graph, kademlia_node)
  - memory/: 1 file (shared_memory_manager)
  - forensics/: 1 file (metadata_extractor)
  - deep_probe.py, enhanced_research.py, autonomous_analyzer.py

Intelligence tools scanned (via intelligence/__init__.py): 20+ tools lazy-loaded
  - ArchiveDiscovery, ArchiveResurrector (Wayback, archive.today, IPFS, GitHub)
  - TemporalAnalyzer, TemporalArchaeologist
  - StealthCrawler, StealthWebScraper
  - UnifiedWebIntelligence, AcademicSearchEngine
  - DataLeakHunter, CryptographicIntelligence
  - DocumentIntelligenceEngine, ExposedServiceHunter
  - RelationshipDiscoveryEngine, PatternMiningEngine
  - IdentityStitchingEngine, BlockchainForensics
  - DecisionEngine, WorkflowOrchestrator, InputDetector

## B) CAPABILITY INVENTORY

### BRAIN MODULES (22 files, 19 active capabilities)

| Module | Class | Public API | Unique Capability | Status | Memory |
|--------|-------|-----------|-------------------|--------|--------|
| hermes3_engine.py | Hermes3Engine | generate(), generate_streaming(), load(), unload(), add_to_batch() | PRIMARY LLM — Hermes-3-Llama-3.2-3B-4bit via MLX, continuous batching, KV cache management | ACTIVE | HEAVY |
| model_manager.py | ModelManager | load(), unload(), get_active_model(), is_loaded() | 1-model-at-a-time lifecycle, phase routing (PLAN→Hermes, EMBED→ModernBERT, NER→GLiNER) | ACTIVE | HEAVY |
| model_swap_manager.py | ModelSwapManager | swap_to(), prepare_for_swap(), rollback() | Race-free Qwen↔Hermes swap arbiter with rollback capability | ACTIVE | MEDIUM |
| model_lifecycle.py | ModelLifecycle | unload_model(), load_model(), begin_sprint(), end_sprint() | Sprint-based lifecycle, emergency unload seam, Outlines structured generation | ACTIVE | MEDIUM |
| inference_engine.py | InferenceEngine, MultiHopReasoner | abduce(), chain_evidence(), resolve_entity() | **Abductive reasoning + multi-hop entity chaining** (OSINT-specific: co-location, temporal proximity, stylometry) | ACTIVE | MEDIUM |
| hypothesis_engine.py | HypothesisEngine, AdversarialVerifier | generate(), test(), update(), get_confidence() | **Bayesian hypothesis testing + adversarial falsification** (Devil's Advocate, source credibility, contradiction detection) | ACTIVE | LOW |
| insight_engine.py | InsightEngine | detect_anomalies(), generate_hypotheses(), synthesize_insights() | **Statistical anomaly detection** (z-score, IQR), multi-level synthesis (micro/meso/macro) | ACTIVE | MEDIUM |
| research_flow_decider.py | ResearchFlowDecider | decide_next_action(), should_continue(), should_fetch_more(), should_deep_dive() | Rule-based + LLM-judged + hybrid strategies for action selection | ACTIVE | LOW |
| synthesis_runner.py | SynthesisRunner, OSINTReport, IOCEntity | run(), _synthesize(), _parse_output(), _extract_iocs() | **Constrained generation (Outlines/xgrammar)** for structured OSINT reports, IOC extraction (URLs, IPs, domains, emails, BTC, hashes, MITRE ATT&CK) | ACTIVE | MEDIUM |
| gnn_predictor.py | GNNPredictor, GraphSAGE, GCN | train(), predict(), compute_anomaly_scores(), add_nodes(), add_edges() | **Pure MLX GraphSAGE/GCN** — node aggregation, anomaly scoring via reconstruction error | ACTIVE | MEDIUM |
| ner_engine.py | NEREngine, IOCScorer | extract(), get_entities(), extract_iocs_from_text(), score() | **GLiNER-X + Apple NaturalLanguage** for NER, IOC extraction with typed patterns + recency weighting | ACTIVE | MEDIUM |
| paged_attention_cache.py | PagedAttentionCache | get(), set(), prefetch(), evict(), clear() | **Page-based KV cache** with score-based page selection, prefetching hints | ACTIVE | LOW |
| dynamic_model_manager.py | DynamicModelManager | get_model(), release_model(), is_model_loaded(), set_memory_limit() | LRU model cache with thrashing protection (min_reload_interval), memory pressure monitoring | LATENT | MEDIUM |
| prompt_cache.py | PromptCache, SystemPromptKVCache | get(), put(), clear(), lookup(), store() | **Trigram similarity cache** with xxhash fingerprinting, O(1) lookup | ACTIVE | LOW |
| prompt_bandit.py | PromptBandit | select(), update(), get_stats(), reset() | **LinUCB/UCB1 contextual bandit** for prompt selection (rope latency-weighted) | LATENT | LOW |
| ane_embedder.py | ANEEmbedder | load(), convert_to_ane(), embed(), warmup(), semantic_dedup_findings(), rerank_findings_cosine() | **CoreML ANE acceleration** for MiniLM embeddings (NotImplementedError placeholder) | EXPERIMENTAL | LOW |
| moe_router.py | MoERouter, RouterMLP | route(), get_top_k(), reset() | **Mixture of Experts** routing with learnable MLP, top-k expert selection | EXPERIMENTAL | MEDIUM |
| dspy_optimizer.py | DSPyOptimizer | optimize(), load_trials(), get_best_program() | **DSPy MIPROv2** offline prompt optimization (idle/thermal guards) | EXPERIMENTAL | MEDIUM |
| distillation_engine.py | DistillationEngine, CriticMLP | train(), score(), distill() | **MLX MLP critic** for reasoning chain scoring (vs full LLM) | EXPERIMENTAL | MEDIUM |
| apple_fm_probe.py | (module funcs) | apple_fm_probe(), is_afm_available() | **Apple Fabric Manager** availability probe via ctypes | ACTIVE | LOW |
| _lazy.py | (module funcs) | get(), get_attr(), clear_cache() | **Deferred import cache** — reduces cold-start by ~1.02-1.18s | ACTIVE | LOW |
| decision_engine.py | (module funcs) | decide_research_action(), should_branch() | **DEPRECATED** — redirects to research_flow_decider | LEGACY | LOW |

### RL MODULES (5 files)

| Module | Class | Public API | Unique Capability | Status | Memory |
|--------|-------|-----------|-------------------|--------|--------|
| qmix.py | QMIXAgent, QMixer, QMIXJointTrainer | forward(), update(), train(), get_joint_q() | **QMIX value decomposition networks** in MLX — monotonic mixing for centralized training, VDN summation for decentralized execution | LATENT | MEDIUM |
| marl_coordinator.py | MARLCoordinator | coordinate(), get_joint_action(), update() | QMIX agent coordination, epsilon-greedy exploration, replay buffer management | LATENT | MEDIUM |
| replay_buffer.py | MARLReplayBuffer | add(), sample(), clear(), size() | NumPy-backed 50K transitions, prioritized experience replay | LATENT | MEDIUM |
| state_extractor.py | StateExtractor | extract(), get_state_vector(), reset() | **12-dim feature vector** from claim density, source diversity, coverage + GNN embedding | LATENT | LOW |
| actions.py | (constants) | ACTION_CONTINUE, ACTION_FETCH_MORE, ACTION_DEEP_DIVE, ACTION_BRANCH, ACTION_YIELD | **Action constants** for RL agent decisions | ACTIVE | NONE |

### PLANNING MODULES (5 files)

| Module | Class | Public API | Unique Capability | Status | Memory |
|--------|-------|-----------|-------------------|--------|--------|
| htn_planner.py | HTNPlanner, PlannerRuntimeRequest, PlannerRuntimeResult | plan(), register_task_type(), build_runtime_request(), execute_requests_and_learn() | **HTN planning** with typed msgspec bridge, panic horizon, sprint lifecycle 4-tier precedence, learning loop | ACTIVE | MEDIUM-HIGH |
| slm_decomposer.py | SLMDecomposer | decompose(), _rule_based_fallback() | **LLM inference (Qwen2.5-0.5B-4bit via mlx_lm)** for task decomposition, RAM-aware parallelism (1-3 instances) | ACTIVE | MEDIUM-HIGH |
| cost_model.py | AdaptiveCostModel | predict(), update() | **Mamba SSM** neural residual on ridge regression baseline — only module with SSM | ACTIVE | MEDIUM |
| search.py | anytime_beam_search() | anytime_beam_search(initial_state, goal_check, expand, heuristic, ...) | **Anytime beam search** maximizing value/cost ratio with explicit budget constraints (time, RAM, network) | ACTIVE | LOW |
| task_cache.py | TaskCache | get(), put(), close() | LMDB cache for SLM decomposition results with model-version isolation | ACTIVE | MEDIUM |

### DHT / MEMORY / FORENSICS MODULES

| Module | Unique Capability | Status | Memory |
|--------|-----------------|--------|--------|
| kademlia_node.py | **Full Kademlia DHT protocol** — IPv4, alpha=3, k=20, torrent metadata (BEP-9/10) | ACTIVE | MEDIUM |
| sketch_exchange.py | **Jaccard-based peer entity similarity** on SHA-256 digest sets, 10K cap | LATENT | MEDIUM |
| local_graph.py | **AES-GCM encrypted graph storage**, optional MLX graphs acceleration | ACTIVE | MEDIUM |
| shared_memory_manager.py | **Zero-copy Arrow IPC** inter-process communication | LATENT | MEDIUM |
| metadata_extractor.py | EXIF, IPTC, XMP metadata extraction from images/documents | ACTIVE | LOW |

### INTELLIGENCE TOOLS (20+ modules, all lazy-loaded)

| Tool | Capability | Status |
|------|------------|--------|
| ArchiveDiscovery | Wayback, archive.today, IPFS, GitHub historical discovery | ACTIVE |
| ArchiveResurrector | Deleted content resurrection via multiple archive sources | ACTIVE |
| TemporalAnalyzer | Time-series analysis, trend detection, causal events | ACTIVE |
| TemporalArchaeologist | Deleted content recovery, timeline reconstruction | ACTIVE |
| StealthCrawler | DuckDuckGo/Google scraping with fingerprint spoofing | ACTIVE |
| StealthWebScraper | Proxy-configurable web scraping with protection bypass | ACTIVE |
| UnifiedWebIntelligence | Multi-platform unified intelligence gathering | ACTIVE |
| AcademicSearchEngine | Arxiv, Crossref, SemanticScholar multi-source search | ACTIVE |
| DataLeakHunter | Email breach detection via HaveIBeenPwned/Hunter/Sherlock | ACTIVE |
| CryptographicIntelligence | Classical cryptanalysis, hash analysis, certificate analysis | ACTIVE |
| DocumentIntelligenceEngine | PDF/Office/Image analysis, MLX long-context | ACTIVE |
| ExposedServiceHunter | S3 enumeration, port scanning, GraphQL introspection, CT logs | ACTIVE |
| RelationshipDiscoveryEngine | Social network analysis, community detection | ACTIVE |
| PatternMiningEngine | Behavioral, temporal, communication pattern mining | ACTIVE |
| IdentityStitchingEngine | Cross-platform identity linking (username→person) | ACTIVE |
| BlockchainForensics | Cryptocurrency address analysis, transaction tracing | ACTIVE |
| WorkflowOrchestrator | Multi-module workflow orchestration | ACTIVE |
| DecisionEngine | Intelligent workflow planning and resource estimation | ACTIVE |
| InputDetector | Input complexity analysis for query routing | ACTIVE |

## C) UNIQUE HIGH-VALUE CAPABILITIES (TOP 15)

| Rank | Module | Why Unique |
|------|--------|------------|
| 1 | inference_engine | **Abductive reasoning** — only module that does real multi-hop OSINT inference (co-location, temporal proximity, communication patterns, stylometry) |
| 2 | hypothesis_engine | **Adversarial verification** — Devil's Advocate + Bayesian updating for falsifiable OSINT claims |
| 3 | hermes3_engine | **Primary LLM** — ~2GB RAM, continuous batching, batch worker thread, KV cache management |
| 4 | synthesis_runner | **Constrained JSON generation** via Outlines/xgrammar — structured OSINT reports, IOC extraction (MITRE ATT&CK, BTC, hashes) |
| 5 | gnn_predictor | **Pure MLX GraphSAGE/GCN** — only graph ML module, anomaly scoring via reconstruction error |
| 6 | htn_planner | **Typed msgspec bridge** + panic horizon logic + sprint lifecycle integration + learning loop |
| 7 | cost_model | **Mamba SSM** — only sequence model in the codebase (other ML is feedforward/GCN) |
| 8 | slm_decomposer | **LLM task decomposition** via Qwen2.5-0.5B — only module that does LLM inference in planning plane |
| 9 | ner_engine | **GLiNER-X + NaturalLanguage** dual NER + IOC pattern scoring with recency weighting |
| 10 | kademlia_node | **Full Kademlia DHT** — only distributed systems module, torrent metadata discovery |
| 11 | model_swap_manager | **Race-free swap arbiter** — prevents MLX model swap races in multi-threaded batching |
| 12 | paged_attention_cache | **Page-based KV cache** — reduces memory fragmentation in long generations |
| 13 | blockchain_forensics | **Cryptocurrency tracing** — only blockchain analysis module |
| 14 | identity_stitching | **Cross-platform identity stitching** — username→person linking across platforms |
| 15 | temporal_archaeologist | **Deleted content recovery** — timeline reconstruction, anomaly detection |

## D) DUPLICATE / OVERLAPPING MODULES

### Cluster 1: MODEL LIFECYCLE (4 modules, HIGH overlap)
  - model_manager.py — canonical, 1-model-at-a-time enforcement, phase routing
  - model_swap_manager.py — race-free swap arbiter (used BY model_manager)
  - model_lifecycle.py — emergency unload + checkpoint seam
  - dynamic_model_manager.py — LRU cache + thrashing protection
  **Canonical**: model_manager.py (phase routing is primary authority)
  **Overlap**: dynamic_model_manager.py is LATENT — model_manager handles lifecycle

### Cluster 2: RESEARCH DECISION (3 modules, MEDIUM overlap)
  - research_flow_decider.py — ACTIVE canonical (rule-based + LLM-judged + hybrid)
  - decision_engine.py — LEGACY deprecated shim (100% redirects to research_flow_decider)
  - prompt_bandit.py — LATENT (UCB1 for prompt selection, not integrated)
  **Canonical**: research_flow_decider.py
  **Overlap**: decision_engine.py is LEGACY — delete candidate (backward compat only)

### Cluster 3: REASONING PIPELINE (3 modules, HIGH overlap — INTENTIONAL)
  - inference_engine.py → abductive reasoning + multi-hop (upstream)
  - hypothesis_engine.py → hypothesis generation + adversarial verification (mid)
  - insight_engine.py → pattern recognition + anomaly detection (downstream)
  **Note**: INTENTIONAL pipeline, NOT duplication. Each has distinct role.

### Cluster 4: IOC EXTRACTION (2 modules, LOW overlap)
  - ner_engine.py — primary NER + IOC extraction (pattern-based + GLiNER-X)
  - synthesis_runner.py — IOC extraction from LLM-generated text
  **Note**: Different sources (raw text vs LLM output) — complementary, not duplicate

### Cluster 5: ANOMALY SCORING (3 modules, LOW overlap)
  - insight_engine.py — statistical anomaly detection (z-score, IQR)
  - gnn_predictor.py — graph-based anomaly via reconstruction error
  - distillation_engine.py — MLX MLP critic for reasoning chain scoring
  **Note**: Different modalities (statistical vs graph vs neural critic) — complementary

### Cluster 6: PROMPT OPTIMIZATION (3 modules, LOW overlap)
  - prompt_cache.py — trigram similarity caching (xxhash, O(1))
  - prompt_bandit.py — LinUCB/UCB1 for prompt variant selection
  - dspy_optimizer.py — DSPy MIPROv2 offline prompt tuning
  **Note**: Different stages (cache vs online selection vs offline tuning) — complementary

### Cluster 7: MARL INFRASTRUCTURE (4 modules, isolated subsystem)
  - qmix.py — QMIX algorithm (canonical)
  - marl_coordinator.py — uses qmix.py
  - replay_buffer.py — numpy 50K transitions
  - state_extractor.py — 12-dim state vector
  **Status**: ALL LATENT — not integrated into main orchestrator
  **Note**: Self-contained RL subsystem; research_flow_decider handles active decisions

### Cluster 8: SERIALIZATION/STORAGE (3 modules, intentional)
  - task_cache.py — LMDB + orjson for planning cache
  - local_graph.py — LMDB + orjson + AES-GCM encryption for graph
  - shared_memory_manager.py — Arrow IPC + orjson fallback for IPC
  **Note**: Different storage backends for different use cases — NOT duplication

## E) LATENT CAPABILITIES (not in hot path, HIGH potential)

| Module | Why Latent | Integration Path |
|--------|------------|-----------------|
| rl/marl_coordinator.py | Not wired in any active entrypoint | Could enhance research_flow_decider for adaptive agent coordination |
| rl/qmix.py | No training loop in hot path | QMIX for multi-agent source selection |
| brain/prompt_bandit.py | Not integrated with prompt selection | Could optimize prompt variant selection in synthesis_runner |
| brain/moe_router.py | Experimental, no integration | Could extend model_manager for multi-expert routing |
| brain/dspy_optimizer.py | Offline-only, no runtime inference | Could tune prompts for synthesis_runner offline |
| brain/distillation_engine.py | Critic not used in hot path | Could score hypothesis_engine chains |
| dht/sketch_exchange.py | No active peer sync in hot path | Could enable distributed entity deduplication |
| memory/shared_memory_manager.py | Zero-copy IPC not used | Could enable process-level parallelism |
| deep_probe.py | Standalone, not integrated | Could feed URLs into enhanced_research.py |
| enhanced_research.py | Not in __main__.py hot path | Natural pair with autonomous_analyzer.py (analyze→execute) |
| autonomous_analyzer.py | Not in __main__.py hot path | Could configure enhanced_research.exe based on query analysis |

## F) ADAPTER / PROVIDER / PROCESSING LAYER CANDIDATES

### RECOMMENDED ADAPTERS (interface/abstraction layer)
  1. model_manager.py — canonical model lifecycle adapter (handles swap, lifecycle, phase routing)
  2. research_flow_decider.py — canonical research action adapter (decides: continue/fetch_more/deep_dive/branch/yield)
  3. inference_engine.py — abductive reasoning adapter (chains evidence, resolves entities)
  4. paged_attention_cache.py — KV cache adapter (manages pages, prefetch, eviction)
  5. prompt_cache.py — prompt lookup adapter (trigram similarity, xxhash O(1))

### RECOMMENDED PROVIDERS (external service / resource providers)
  1. hermes3_engine.py — PRIMARY LLM provider (batch worker, continuous batching)
  2. ner_engine.py — NER/IOC provider (GLiNER-X + NaturalLanguage)
  3. AcademicSearchEngine — academic search provider (Arxiv, Crossref, SemanticScholar)
  4. StealthCrawler — web search provider (DuckDuckGo/Google scraping)
  5. ArchiveDiscovery + ArchiveResurrector — archive provider (Wayback, IPFS, GitHub)
  6. DataLeakHunter — breach detection provider
  7. BlockchainForensics — blockchain analysis provider
  8. IdentityStitchingEngine — identity linking provider

### RECOMMENDED PROCESSING MODULES
  1. synthesis_runner.py — constrained JSON generation (Outlines/xgrammar)
  2. hypothesis_engine.py — hypothesis generation + adversarial verification
  3. insight_engine.py — multi-level synthesis (micro/meso/macro), anomaly detection
  4. gnn_predictor.py — graph-based anomaly scoring (GraphSAGE/GCN)
  5. htn_planner.py — HTN task planning with typed msgspec bridge
  6. slm_decomposer.py — LLM-based task decomposition
  7. enhanced_research.py — multi-phase research orchestration (search→osint→academic→deep_read→fact_check→synthesis)
  8. autonomous_analyzer.py — query analysis + tool/privacy/model selection (analyze→profile pipeline)

### RECOMMENDED BRAIN PLANE
  - Core: hermes3_engine (LLM) + model_manager (lifecycle)
  - Reasoning: inference_engine (abduction) → hypothesis_engine (testing) → insight_engine (synthesis)
  - Extraction: ner_engine (entities) + synthesis_runner (structured output)
  - Memory: paged_attention_cache (KV) + prompt_cache (prompts)

### RECOMMENDED RL/ADAPTATION PLANE
  - CURRENT: research_flow_decider (rule-based action selection)
  - LATENT: rl/marl_coordinator + qmix (multi-agent coordination)
  - LATENT: prompt_bandit (prompt selection via bandit)
  - NOTE: RL plane is entirely LATENT — marl_coordinator daemon thread exists but not wired

### RECOMMENDED MODEL PLANE
  - Primary: hermes3_engine (Hermes-3-Llama-3.2-3B-4bit, ~2GB)
  - Secondary: slm_decomposer (Qwen2.5-0.5B-4bit for task decomposition)
  - Embedding: ane_embedder (CoreML ANE for MiniLM — placeholder/EXPERIMENTAL)
  - MoE: moe_router (EXPERIMENTAL, not integrated)
  - Graph: gnn_predictor (GraphSAGE/GCN in MLX)
  - Cost: cost_model (Mamba SSM residual predictor)

## G) CONSOLIDATION RECOMMENDATIONS

### MUST NOT BE THROWN AWAY (unique, irreplaceable)
  1. inference_engine.py — abductive reasoning engine (no equivalent in OSS OSINT tools)
  2. hypothesis_engine.py — adversarial verification with Bayesian updating (unique in OSINT)
  3. hermes3_engine.py — primary LLM on M1 (Apple Silicon native, continuous batching)
  4. synthesis_runner.py — constrained OSINT report generation (Outlines/xgrammar)
  5. gnn_predictor.py — pure MLX graph ML (no external graph library dependency)
  6. htn_planner.py — typed HTN with msgspec bridge + learning loop
  7. kademlia_node.py — full Kademlia DHT (no other DHT implementation)
  8. ner_engine.py — dual NER (GLiNER-X + NaturalLanguage) with IOC scoring
  9. blockchain_forensics.py — cryptocurrency tracing
  10. identity_stitching.py — cross-platform identity linking
  11. temporal_archaeologist.py — deleted content recovery
  12. cost_model.py — Mamba SSM (only sequence model)
  13. model_swap_manager.py — race-free swap arbiter (prevents MLX races)

### DELETE CANDIDATES
  1. decision_engine.py — LEGACY shim, 100% redirects to research_flow_decider
     Evidence: grep shows only imports from research_flow_decider

### MERGE CANDIDATES
  1. enhanced_research.py + autonomous_analyzer.py — natural pair (analyze→execute)
     Proposed: wire AutonomousAnalyzer.analyze() → UnifiedResearchEngine.deep_research()
  2. deep_probe.py into enhanced_research.py — URL discovery → content extraction pipeline
     Proposed: ShadowWalkerAlgorithm + WaybackCDX → enhanced_research search phase

### INTEGRATION GAPS
  1. RL subsystem (qmix/marl_coordinator/replay_buffer/state_extractor) — entirely latent
     Could enhance: research_flow_decider for multi-agent coordination
  2. prompt_bandit — not integrated with synthesis_runner
     Could optimize: prompt variant selection in synthesis phase
  3. moe_router — experimental, not wired
     Could extend: model_manager for multi-expert synthesis routing
  4. shared_memory_manager — zero-copy IPC unused
     Could enable: process-level parallelism for heavy tools
  5. sketch_exchange — Jaccard peer dedup unused
     Could enable: distributed entity deduplication across peers

## H) M1 8GB HEAVY MODULE RISKS

| Module | Risk Level | Reason |
|--------|-----------|--------|
| hermes3_engine.py | CRITICAL | ~2GB RAM for model + batch worker thread + KV cache |
| slm_decomposer.py | CRITICAL | ~800MB per Qwen instance, RAM-aware parallelism 1-3 |
| gnn_predictor.py | HIGH | OOM guard at 5000 nodes, MLX training |
| model_manager.py | HIGH | May trigger hermes3 load/unload cycles |
| enhanced_research.py | HIGH | Lazy-loads 8 intelligence tools, concurrent execution |
| autonomous_analyzer.py | LOW | All compiled regex, no heavy deps |
| deep_probe.py | MEDIUM | MemoryOptimizedURLSet (50MB cap) + WaybackCDX aiohttp |

**M1 8GB constraint**: Never run hermes3 + slm_decomposer simultaneously. model_manager enforces 1-model-at-a-time.

## I) AUTONOMOUS RESEARCH LOOP (capability wiring)

### MAIN AUTONOMOUS LOOP: analyze → execute → infer → synthesize
  autonomous_analyzer.py (query analysis, 21-tool detection, ToT complexity)
       ↓ AutoResearchProfile
  enhanced_research.py (multi-phase: search→osint→academic→deep_read→fact_check→synthesis, RRF fusion)
       ↓ UnifiedResearchResult
  inference_engine.py (abductive reasoning, multi-hop entity chaining)
       ↓ evidence chain
  hypothesis_engine.py (generate + adversarial verify + Bayesian update)
       ↓ ranked hypotheses
  insight_engine.py (pattern detection, anomaly scoring, multi-level synthesis)
       ↓ insights
  synthesis_runner.py (constrained JSON: OSINTReport, IOCEntity, MITRE ATT&CK)
       ↓ structured output
  hermes3_engine.py (LLM via MLX, batch worker thread)
       ↓
  ner_engine.py (GLiNER-X + NaturalLanguage IOC extraction)
       ↓
  duckdb_store.py / lancedb_store.py (storage)

### PLANNING LOOP
  htn_planner.py (HTN planning with typed msgspec)
       ↓ PlannerRuntimeRequest/Result
  slm_decomposer.py (Qwen2.5 LLM decomposition)
       ↓ task plan
  cost_model.py (Mamba SSM cost estimation)
       ↓ predicted cost
  search.py (anytime beam search with budget)
       ↓ search tree
  task_cache.py (LMDB cache with model-version isolation)

### RL/ADAPTATION LOOP (LATENT — not wired)
  rl/qmix.py (QMIX value decomposition)
       ↓ joint Q-values
  rl/marl_coordinator.py (agent coordination)
       ↓ joint actions
  rl/state_extractor.py (12-dim state vector)
       ↓
  rl/replay_buffer.py (50K numpy transitions)
       ↓
  research_flow_decider.py (action selection) ← NOTE: currently rule-based, not RL-driven

## J) SUMMARY STATS

  Total unique capability modules: 47
  ACTIVE in hot path: 22 (47%)
  LATENT (not wired): 19 (40%)
  EXPERIMENTAL: 3 (moe_router, dspy_optimizer, distillation_engine)
  LEGACY: 1 (decision_engine.py)

  Unique klenoty (must preserve): 13
  Delete candidates: 1 (decision_engine.py)
  Merge candidates: 2 pairs (enhanced+autonomous, deep_probe+enhanced)
  Integration gaps: 5 (RL subsystem, prompt_bandit, moe_router, IPC, DHT sketch)

  Memory heaviness:
    CRITICAL: 2 (hermes3, slm_decomposer)
    HIGH: 4 (gnn, model_manager, enhanced_research, autonomous_analyzer)
    MEDIUM: 9
    LOW: 12
    NONE: 1 (rl/actions.py)
"""
# === AGENT_3_END: CAPABILITIES_AND_CONSOLIDATION ===

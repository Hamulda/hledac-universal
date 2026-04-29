# Architectural Review: Sprint F195 Integration

## Executive Summary

The sprint F195 integration introduces significant architectural complexity to the hledac/universal autonomous OSINT orchestrator. The codebase exhibits a **facade-dominated architecture** where `autonomous_orchestrator.py` acts as a re-export facade delegating to `legacy/autonomous_orchestrator.py`, creating confusion about authority chains. The coordinator/seam pattern is well-implemented for HTTP transport via `FetchCoordinator`, but the memory layer architecture shows signs of drift with multiple overlapping systems. M1 8GB memory constraints are well-handled at the infrastructure level but create tight coupling between components.

**Overall Architectural Health: MEDIUM**

---

## 1. Component Boundaries

### 1.1 Authority Chain Confusion (CRITICAL)

**Finding:** The `autonomous_orchestrator.py` module is explicitly a **non-canonical facade** that re-exports from `legacy/autonomous_orchestrator.py`. This creates a false authority risk where the module appears to be the primary orchestrator but is not.

**Evidence:**
- `autonomous_orchestrator.py:1-44` - Extensive docstring explaining this is a "ROOT_REEXPORT_FACADE" with `canonical_owner` pointing to `legacy/autonomous_orchestrator.py`
- `autonomous_orchestrator.py:93-99` - Deprecation warning issued at import time
- `autonomous_orchestrator.py:80-110` - Complex facade loading mechanism using `sys.modules` manipulation

**Architectural Impact:** HIGH - Multiple tests and smoke runners import directly from this facade, creating hidden dependencies on legacy code that may be removed.

**Recommendation:** 
1. Create a canonical entry point in `core.__main__` that does not rely on facade imports
2. Update all imports to use the legacy module path directly or create a proper alias structure
3. Add a runtime check that fails if the facade is imported by production code

---

### 1.2 Coordinator Pattern - FetchCoordinator (HEALTHY)

**Finding:** `FetchCoordinator` properly implements the `UniversalCoordinator` base class with the stable `start/step/shutdown` interface.

**Evidence:**
- `fetch_coordinator.py:366-376` - `FetchCoordinator` class with proper base inheritance
- `fetch_coordinator.py:810-835` - `_do_start` properly receives context from orchestrator
- `fetch_coordinator.py:854-955` - `_do_step` implements bounded batch processing with AIMD

**Architectural Impact:** LOW - This is a well-designed seam.

**Recommendation:** Continue using this pattern; do not add new methods to the coordinator interface.

---

### 1.3 Memory Layer Fragmentation (HIGH)

**Finding:** There are three overlapping memory management systems:
1. `layers/memory_layer.py` - `_MemoryStateManager`, `_StorageCoordinator`, `_StealthMemoryManager`
2. `coordinators/memory_coordinator.py` - `NeuromorphicMemoryManager`, `MemoryCoordinator`
3. `utils/uma_budget.py` - `UmaWatchdog`, raw UMA sampling

**Evidence:**
- `memory_layer.py:61-224` - Internal classes for system state machine and health monitoring
- `memory_layer.py:226-300` - `_StorageCoordinator` for RAM disk and shared memory
- `memory_coordinator.py:124-401` - `NeuromorphicMemoryManager` with STDP learning (completely different paradigm)
- `uma_budget.py:310-398` - `UmaWatchdog` with async polling

**Architectural Impact:** MEDIUM - The systems address different concerns (system health vs. neuromorphic memory vs. UMA budgeting) but share overlapping functionality (memory pressure detection, cleanup triggers).

**Recommendation:**
1. Define a clear boundary: `uma_budget.py` is the RAW SAMPLER, `memory_layer.py` is the POLICY/GOVERNOR
2. `NeuromorphicMemoryManager` should be extracted to a separate module if genuinely needed; otherwise remove as dead code
3. Ensure `_MemoryStateManager` delegates pressure callbacks to `UmaWatchdog` rather than duplicating monitoring

---

### 1.4 Ghost Layer - Thin Wrapper Pattern (MEDIUM)

**Finding:** `layers/ghost_layer.py` is explicitly a thin wrapper that imports from `hledac.cortex.director` and `hledac.supreme.security`.

**Evidence:**
- `ghost_layer.py:14-16` - Docstring confirms "thin wrapper that imports existing GhostDirector"
- `ghost_layer.py:199-211` - GhostDirector imported from external module
- `ghost_layer.py:216-240` - RamDiskVault and LootManager imported similarly

**Architectural Impact:** LOW - This is an intentional adapter pattern, but it depends on modules outside `hledac/universal/`.

**Recommendation:** Document the external module dependencies explicitly and ensure they are available before the layer initializes.

---

## 2. Dependency Management

### 2.1 Import Dependency Issues (HIGH)

**Finding:** Multiple files use lazy imports to avoid circular dependencies or expensive cold-start costs.

**Evidence:**
- `model_manager.py:28-29` - `MLX_AVAILABLE = False` at module level, populated lazily
- `brain/prompt_cache.py:11-16` - xxhash import wrapped in try/except
- `fetch_coordinator.py:30-55` - Multiple optional imports (zstd, aiohttp, SessionManager)
- `persistent_layer.py:60-98` - Complex lazy import pattern for context_cache

**Architectural Impact:** MEDIUM - Lazy imports hide true dependencies and can cause runtime failures that are hard to diagnose.

**Recommendation:**
1. Create a dependency matrix documenting which features require which imports
2. Use `importlib.util.find_spec()` check at startup rather than try/except on import
3. Ensure all lazy imports have fallbacks that maintain core functionality

---

### 2.2 Cross-Module Coupling (MEDIUM)

**Finding:** `brain/model_manager.py` has extensive comments about cross-module ownership chains that are difficult to verify.

**Evidence:**
- `model_manager.py:200-241` - Large docstring block detailing ownership chain: ModelManager → engine.unload() → _cleanup_memory_async() → mlx_cache
- `model_manager.py:621-622` - Imports `ensure_mlx_runtime_initialized` from `brain.model_lifecycle`
- `model_manager.py:372-376` - Imports from `core.resource_governor`

**Architectural Impact:** MEDIUM - The ownership chain involves 5+ modules, making it fragile to refactoring.

**Recommendation:**
1. Consolidate the model lifecycle ownership into a single module
2. Use a formal protocol/interface for the unload chain rather than implicit method calls
3. Add integration tests that verify the full unload lifecycle

---

## 3. API Design

### 3.1 FetchCoordinator API - Well Designed (HEALTHY)

**Finding:** The FetchCoordinator exposes a clean API with proper timeout/concurrency matrices as constants.

**Evidence:**
- `fetch_coordinator.py:117-153` - Timeout and concurrency constants properly defined
- `fetch_coordinator.py:178-186` - `FetchCoordinatorConfig` dataclass for configuration
- `fetch_coordinator.py:957-980` - `_get_step_result` returns bounded, well-typed responses

**Recommendation:** Continue this pattern; timeout values should remain constants, not config options.

---

### 3.2 PromptCache API (MEDIUM)

**Finding:** `brain/prompt_cache.py` has a simple but effective approximate cache with trigram similarity.

**Evidence:**
- `brain/prompt_cache.py:41-49` - `PromptCache` with LRU eviction
- `brain/prompt_cache.py:64-94` - `_get_embedding` generates trigram-based embeddings
- `brain/prompt_cache.py:118-159` - `get()` method with exact and approximate matching

**Architectural Impact:** LOW - Simple, focused API.

**Issue:** `SystemPromptKVCache` at line 189-241 is a separate class that doesn't use the same interface. It stores tokenized prompts but returns `(None, prefix_token_count)` which is confusing.

**Recommendation:** Unify the cache interfaces or clearly document the difference.

---

### 3.3 ModelManager API - Lifecycle Management (HEALTHY)

**Finding:** Model lifecycle management is well-structured with proper async context manager support.

**Evidence:**
- `model_manager.py:132-172` - `model_lifecycle` async context manager
- `model_manager.py:483-511` - `acquire_model_ctx` guarantees unload via finally block
- `model_manager.py:355-402` - Memory admission gate with hard fail-fast

**Recommendation:** The `_check_memory_admission()` method is well-designed. Ensure it is called before ANY model load, not just in `_load_model_async`.

---

## 4. Data Model

### 4.1 Knowledge Storage - Multiple Backends (HIGH)

**Finding:** `knowledge/atomic_storage.py` is a stub file (pycache marker at line 1), and `persistent_layer.py` has KuzuDB with JSON fallback.

**Evidence:**
- `atomic_storage.py:1` - "Stub for .../atomic_storage.cpython-312.pyc - generováno z bytecode"
- `persistent_layer.py:186-253` - `KuzuDBBackend` with fallback to `JSONBackend`
- `persistent_layer.py:213-224` - `_try_load_kuzu()` with graceful fallback

**Architectural Impact:** HIGH - The stub file suggests atomic_storage.py was compiled from bytecode and the source may be missing. The KuzuDB fallback to JSON is problematic for production.

**Recommendation:**
1. Regenerate `atomic_storage.py` from source or document it as a compiled-only module
2. Replace JSON backend with LMDB for production readiness
3. Ensure the fallback chain is tested: KuzuDB → LMDB → SQLite (not JSON)

---

### 4.2 Memory Budget - UMA Budget Model (HEALTHY)

**Finding:** `uma_budget.py` correctly implements the RAW SAMPLER role with clear authority boundaries.

**Evidence:**
- `uma_budget.py:4-20` - Authority boundary clearly documented: SAMPLER reads raw values, GOVERNOR does policy
- `uma_budget.py:58-62` - Threshold constants: WARN=6GB, CRITICAL=6.5GB, EMERGENCY=7GB
- `uma_budget.py:164-180` - `get_uma_usage_mb()` combines system + MLX active memory

**Recommendation:** The threshold values should be configurable via constructor, not hardcoded constants, to support different M1 configurations (e.g., 16GB model).

---

## 5. Design Patterns

### 5.1 Circuit Breaker Pattern (HEALTHY)

**Finding:** `FetchCoordinator` implements domain-level circuit breaking.

**Evidence:**
- `fetch_coordinator.py:392-396` - Domain failure tracking
- `fetch_coordinator.py:398-409` - `_record_domain_failure()` with exponential backoff
- `fetch_coordinator.py:411-414` - `get_blocked_domains()` for inspection
- `fetch_coordinator.py:1021-1028` - Circuit breaker check before fetch

**Recommendation:** This pattern is well-implemented. Ensure the blocked domain state is persisted so circuit breaker survives restarts.

---

### 5.2 AIMD Concurrency Control (HEALTHY)

**Finding:** Adaptive concurrency via AIMD (Additive Increase Multiplicative Decrease) is properly implemented.

**Evidence:**
- `fetch_coordinator.py:137-144` - AIMD parameters as constants
- `fetch_coordinator.py:456-461` - AIMD state initialization
- `fetch_coordinator.py:596-664` - `_aimd_acquire()`, `_aimd_release_success()`, `_aimd_release_failure()`
- `fetch_coordinator.py:915-916` - AIMD semaphore used in `_do_step`

**Recommendation:** The AIMD parameters should be tuning knobs exposed via config, not constants.

---

### 5.3 Neuromorphic Memory - Potential Over-Engineering (MEDIUM)

**Finding:** `NeuromorphicMemoryManager` in `memory_coordinator.py` implements brain-inspired STDP learning with sparse matrices. This appears to be a research/prototype component.

**Evidence:**
- `memory_coordinator.py:124-401` - Full implementation with STDP parameters, spike traces, synaptic weights
- `memory_coordinator.py:113-122` - `STDPParameters` dataclass with timing constants
- `memory_coordinator.py:202-224` - Sparse synaptic weight matrix initialization

**Architectural Impact:** MEDIUM - This is a complex, specialized component that may not be production-hardened. It uses scipy for sparse matrices which may have memory implications on M1.

**Recommendation:**
1. If this is production code, ensure it has full test coverage and performance benchmarks
2. If prototype/research, move to a separate module or mark as experimental
3. Consider memory constraints: scipy sparse matrices may not be optimal for M1 8GB

---

## 6. Architectural Consistency

### 6.1 Consistency Issues (HIGH)

**Finding:** Several architectural inconsistencies observed:

1. **Multiple memory systems** - `memory_layer.py`, `memory_coordinator.py`, `uma_budget.py` all do memory management differently
2. **Deprecated modules still present** - `legacy/persistent_layer.py` warns it is deprecated but still exists
3. **Security module isolation** - `security/deep_research_security.py` imports from `.quantum_safe`, `.obfuscation`, `.audit` which may not exist in universal/
4. **Ghost layer external dependencies** - GhostDirector from `hledac.cortex.director` not verified

**Evidence:**
- `persistent_layer.py:14-21` - Deprecation warning for `knowledge.persistent_layer`
- `deep_research_security.py:27-30` - Imports from relative security submodules
- `ghost_layer.py:199-211` - External module imports without verification

**Recommendation:**
1. Audit all external module imports and create a dependency manifest
2. Remove or properly deprecate legacy modules with clear migration paths
3. Create integration tests that verify all layers can initialize without errors

---

### 6.2 M1 8GB Memory Constraints (HEALTHY)

**Finding:** Memory constraints are well-handled throughout the codebase with consistent patterns.

**Evidence:**
- `uma_budget.py` - Proper threshold monitoring
- `mlx_cache.py:142-157` - Metal memory limits set to 2.5GB cache + 2.5GB wired
- `model_manager.py:32-37` - Model size estimates for RSS verification
- `model_manager.py:652-653` - `_check_rss_before_load()` and `_verify_rss_after_unload()`

**Recommendation:** These patterns are good. Ensure they are all wired together: if UMA is WARN, model loading should be delayed.

---

## 7. Severity Summary

| Category | Finding | Severity | Impact |
|----------|---------|----------|--------|
| Authority Chain | autonomous_orchestrator facade confusion | CRITICAL | Multiple imports depend on non-canonical facade |
| Data Model | atomic_storage.py is stub/compiled | HIGH | Source may be missing |
| Dependencies | Cross-module coupling in model lifecycle | HIGH | Fragile to refactoring |
| Memory | Multiple overlapping memory systems | HIGH | Maintenance burden |
| API Design | SystemPromptKVCache inconsistent interface | MEDIUM | Confusion about cache behavior |
| Data Model | KuzuDB JSON fallback not production-ready | HIGH | Data loss risk |
| Design Patterns | NeuromorphicMemoryManager potential over-engineering | MEDIUM | Complexity without proven value |
| Consistency | Security module external imports | MEDIUM | Runtime failures possible |

---

## 8. Recommendations (Prioritized)

### P0 - Must Fix

1. **Resolve autonomous_orchestrator authority chain**
   - Identify all consumers of the facade
   - Redirect imports to canonical locations
   - Add runtime assertion if facade is used in production path

2. **Regenerate or document atomic_storage.py**
   - If source is missing, document as compiled-only
   - Add to version control or .gitignore with explanation

3. **Replace KuzuDB JSON fallback with LMDB**
   - JSON backend is not suitable for production knowledge storage
   - Use the established LMDB pattern from other modules

### P1 - Should Fix

4. **Consolidate memory management systems**
   - Define clear ownership: uma_budget = SAMPLER, memory_layer = GOVERNOR
   - Remove duplicate health monitoring

5. **Audit external module dependencies**
   - GhostDirector, quantum_safe, obfuscation, audit
   - Document required vs. optional dependencies

6. **Add integration tests for layer initialization**
   - Verify all layers can start without errors
   - Test fallback paths are exercised

### P2 - Nice to Have

7. **Make AIMD and circuit breaker parameters configurable**
   - Move from constants to config dataclasses
   - Add observability for parameter values

8. **Review NeuromorphicMemoryManager necessity**
   - If not production-critical, mark as experimental
   - Consider removal if not actively used

---

## 9. Trade-offs

| Option | Pros | Cons |
|--------|------|------|
| Keep facade architecture | Backward compatibility for tests | Authority confusion, maintenance burden |
| Remove facade, redirect imports | Clear authority chain | May break existing imports |
| Keep multiple memory systems | Each addresses different concerns | Maintenance burden, potential conflicts |
| Consolidate memory systems | Simplified codebase | May lose specialized functionality |
| Keep NeuromorphicMemoryManager | Research value, potential innovation | Added complexity, unproven at scale |
| Remove NeuromorphicMemoryManager | Reduced complexity | May need to reimplement if research matures |

---

## 10. References

Key files analyzed:

- `autonomous_orchestrator.py:1-272` - Facade architecture
- `coordinators/fetch_coordinator.py:366-1305` - Coordinator implementation
- `coordinators/memory_coordinator.py:1-401` - Memory coordinator with neuromorphic
- `layers/memory_layer.py:1-300` - Memory layer wrapper
- `layers/ghost_layer.py:1-300` - Ghost layer wrapper
- `brain/model_manager.py:1-800` - Model lifecycle management
- `brain/prompt_cache.py:1-241` - Prompt caching
- `utils/uma_budget.py:1-400` - UMA budgeting
- `utils/mlx_cache.py:1-400` - MLX cache management
- `security/deep_research_security.py:1-300` - Security layer
- `knowledge/atomic_storage.py:1-25` - Stub file
- `legacy/persistent_layer.py:1-400` - Deprecated knowledge layer

---

*Review conducted: 2026-04-23*
*Reviewer: Architect (Oracle)*
*Sprint: F195*

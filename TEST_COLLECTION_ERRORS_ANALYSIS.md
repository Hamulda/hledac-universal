# Test Collection Errors Analysis - 2026-05-30

## Overview
73 unique collection errors block pytest from running tests. These are **architectural debt issues** from accumulated refactoring.

---

## Error Categories

### Category 1: Module "is not a package" (19 errors)
**Root Cause:** Missing `__init__.py` in parent directories

```
hledac.universal.coordinators           # __init__.py missing or empty
hledac.universal.knowledge             # __init__.py missing or empty
hledac.universal.tools                 # __init__.py missing or empty
hledac.universal.utils                 # __init__.py missing or empty
```

**Solution:** Add `__init__.py` exports for requested submodules

---

### Category 2: Missing stub modules (14 errors)
**Root Cause:** Deprecated/future modules never created

| Module | Status | Solution |
|--------|---------|----------|
| `brain.causal_engine` | Planned, not implemented | Create stub with logger warning |
| `discovery.circl_pdns_adapter` | Planned, not implemented | Create stub |
| `discovery.duckduckgo_adapter` | Planned, not implemented | Create stub |
| `hledac.universal.brain.llm_candidate_registry` | Planned | Create stub |
| `hledac.universal.federated.sketches` | Planned | Create stub |
| `hledac.universal.runtime.evidence_corroboration` | Planned | Create stub |
| `hledac.universal.runtime.nonfeed_seed_runtime` | Planned | Create stub |
| `hledac.universal.runtime.sidecar_orchestrator` | Planned | Create stub |
| `hledac.universal.runtime.sprint_timer` | Planned | Create stub |
| `hledac.universal.coordinators.render_coordinator` | Planned | Create stub |
| `hledac.universal.knowledge.semantic_store_buffer` | Planned | Create stub |
| `hledac.universal.tools.osint_frameworks` | Planned | Create stub |
| `hledac.universal.tools.replay_research_loop` | Planned | Create stub |
| `hledac.universal.tools.source_bandit` | Planned | Create stub |

**Solution:** Create stub modules with `__all__ = []` and logger warnings

---

### Category 3: Missing functions in live_sprint_measurement.py (4 errors)
**Root Cause:** Functions removed during refactoring, tests still reference them

```
_derive_live_kpi       # Removed - needs re-export or stub
_derive_next_action    # Removed - needs re-export or stub
_was_family_attempted  # Removed - needs re-export or stub
get_acquisition_profile_reality  # Removed - needs re-export
```

**Solution:**
1. Check if functions exist elsewhere (grep)
2. Add stubs in `live_sprint_measurement.py` if truly missing
3. Update test imports if function moved

---

### Category 4: Missing acquisition_strategy exports (12 errors)
**Root Cause:** Massive refactoring of `acquisition_strategy.py`

```
ACQUISITION_REPORT_SCHEMA_VERSION   # Missing constant
AcquisitionLane                    # Missing class
AcquisitionProfile                 # Missing class
build_acquisition_plan              # Missing function
build_acquisition_report            # Missing function
canonicalize_source_family_outcomes # Missing function
canonicalize_source_family_name     # Missing function
canonicalize_source_family_outcome  # Missing function
FeedDominanceBudget                # Missing class
LANE_RULES                         # Missing constant
NonfeedSeedContext                # Missing class
normalize_source_family_name       # Missing function (duplicate)
normalize_source_family_outcome    # Missing function (duplicate)
```

**Solution:** Check `acquisition_strategy.py` exports and add missing ones

---

### Category 5: Missing sprint_scheduler exports (6 errors)
**Root Cause:** API changed, tests not updated

```
_PublicStage              # Missing enum/class
CTLossStage               # Missing enum/class
SPRINT_TIERS              # Missing constant
SprintScheduler          # Class moved/renamed
SprintSchedulerConfig     # Class moved/renamed
SprintSchedulerResult     # Class moved/renamed
```

**Solution:** Update test imports to current `sprint_scheduler.py` API

---

### Category 6: Missing pivot_planner.py (5 errors)
**Root Cause:** File referenced but path is wrong

```
/hledac/runtime/pivot_planner.py           # Wrong path (should be universal/)
/hledac/universal/runtime/pivot_planner.py # Correct path exists
_score_pivot_archive                       # Missing function
_score_pivot_domain                        # Missing function
MAX_PIVOT_CANDIDATES                      # Missing constant
PivotPlanner                              # Missing class
```

**Solution:** Fix imports in tests (use correct path)

---

### Category 7: Missing utils/core/knowledge exports (6 errors)
**Root Cause:** Refactoring moved/removed exports

```
ActionResult                # Missing - should be from utils?
CanonicalFinding            # Missing re-export
DuckDBShadowStore           # Missing re-export
get_sprint_next_seeds_path # Missing from paths.py
get_swap_policy_tier       # Missing from resource_governor.py
Priority                   # Missing enum/class
ResourceGovernor           # Missing class
graph_service              # Missing from knowledge
```

**Solution:** Add missing exports to source modules

---

### Category 8: Missing coordinators/transport exports (3 errors)
**Root Cause:** API refactored

```
LightpandaManager    # Missing from fetch_coordinator
ZstdCompressor       # Missing from fetch_coordinator
checked_aiohttp_get  # Missing from circuit_breaker
```

**Solution:** Add stubs or update test imports

---

### Category 9: Missing tools/research_quality_score (1 error)
```
normalize_benchmark_json  # Missing from research_quality_score
```

**Solution:** Add stub function

---

### Category 10: File not found (4 errors)
**Root Cause:** Tests referencing non-existent files

```
/hledac/runtime/pivot_planner.py                  # Wrong path
/tests/hledac/universal/benchmarks/live_measurement_schema.py  # Nested test dir
/docs/LOCAL_M1_SMOKE_RUNBOOK.md                    # Doc doesn't exist
/benchmarks/llm_reasoner_benchmark.py            # File doesn't exist
```

**Solution:** Fix test file paths or remove references

---

### Category 11: Missing httpx_transport (1 error)
```
should_use_httpx_h2  # Missing from httpx_transport
```

**Solution:** Add stub function

---

### Category 12: Other (2 errors)
```
AssertionError: Live network module urllib3.exceptions imported in test scope
ImportError: attempted relative import beyond top-level package
```

**Solution:** Fix import structure in tests

---

## Strategic Resolution Plan

### Phase 1: Quick Wins (30 min)
1. Add `__init__.py` with exports for coordinators/knowledge/tools/utils
2. Create stub modules for planned-but-not-implemented modules
3. Add stub functions for clearly missing functions

### Phase 2: Import Fixes (1 hour)
1. Update test imports to match current API
2. Fix wrong paths in test files
3. Remove references to deleted files

### Phase 3: Deep Dive (if needed)
1. Analyze which tests are still relevant
2. Archive/delete tests for deprecated features
3. Create integration test layer

---

## Recommended Approach for M1 8GB

Given M1 constraints:
1. **Use stubs liberally** - avoid building unused code
2. **Skip non-critical tests** - focus on smoke tests
3. **Batch fixes** - fix by category, not individually
4. **Lazy loading** - stubs with `__getattr__` to avoid import overhead

---

## Implementation Strategy

Create a script that:
1. Generates stub modules for missing packages
2. Adds missing exports to existing modules
3. Updates test imports

```python
# Stub generator pattern
STUBS = {
    "brain.causal_engine": """
from typing import Any
import logging
logger = logging.getLogger(__name__)
__all__ = []
def causal_inference(*args, **kwargs) -> Any:
    logger.warning("causal_engine stub called")
    return None
""",
    # ... more stubs
}
```

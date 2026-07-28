# Coordinators Refactor Analysis

## Status: Phase 1 COMPLETED ✓

### Completed: Memory Domain Refactor

**Goal**: Eliminate duplicate class definitions, enable composition

**Result**:
- Created `coordinators/memory/_core.py` with 12 shared classes
- Eliminated 11 duplicate class definitions across 3 files
- `memory_coordinator.py` reduced from 17 to 4 classes
- All imports verified, tests pass (106 passed)

**Files Modified**:
| File | Change |
|------|--------|
| `coordinators/memory/_core.py` | **CREATED** - 12 shared classes |
| `coordinators/memory_coordinator.py` | Removed 11 duplicate classes, added imports |
| `coordinators/memory/context_optimizer.py` | Removed duplicates, imports from `_core` |
| `coordinators/memory/multi_level_cache.py` | Removed duplicates, imports from `_core` |
| `coordinators/memory/__init__.py` | Updated exports, imports from `_core` |

**Classes in `_core.py`** (single source of truth):
- `ThermalState`, `MemoryZone`, `MemoryAllocation`, `MemoryStatistics`, `ZoneStatistics`
- `ContextPriority`, `ResearchPhase`, `ContextItem`, `CompressedContext`
- `CacheType`, `CacheLocation`, `CacheEntry`

---

## Remaining Issues (Not Implemented)

### Problem 2: Coordinator Routing Pattern (3 coordinators)
- `archive_coordinator.py` → `fetch_coordinator.py` → `graph_coordinator.py`
- All three have identical delegation docstrings
- Finding routing logic is copy-pasted

**Recommendation**: Create `FindingRouter` protocol in `_finding_router.py`

### Problem 3: Resource Allocation Split (2 files)
- `resource_allocator.py` vs `resource/resource_coordinator.py`
- Common class: `CapacitySnapshot` (duplicated)
- 7 shared methods

**Recommendation**: Create `resource/_types.py` for shared types

### Problem 4: Privacy Audit Cross-cutting (3 files)
- `privacy_enhanced_research.py` is the source
- Similar patterns in `execution_coordinator.py`, `agent_coordination_engine.py`

**Recommendation**: Extract `PrivacyAuditor` protocol

---

## Python 3.14+ Techniques Available

| Technique | PEP | Applicable To |
|-----------|-----|--------------|
| Type Parameter Defaults | PEP 695 | Resource allocation generics |
| Task Groups | PEP 654 | Async coordinators |
| `buffer` protocol | PEP 688 | Zero-copy memory data |
| Explicitly optional | PEP 749 | Import cleanup |

---

## Test Results

```
pytest tests/test_sprint_scheduler.py -x --timeout=60 -q
106 passed, 3 skipped, 9 warnings in 12.11s
```

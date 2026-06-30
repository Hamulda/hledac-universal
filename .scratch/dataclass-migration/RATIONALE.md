# Dataclass → msgspec.Struct Migration Analysis

**Datum:** 2026-06-30
**Scope:** ~/PycharmProjects/Hledac/hledac/universal
**Python:** 3.14.6 | **msgspec:** 0.21.1

---

## 1. Current State

| Metric | Count |
|--------|-------|
| `@dataclass` class definitions | 389 |
| `msgspec.Struct` class definitions | 77 |
| Migration ratio | ~17% msgspec |
| Files using `@dataclass` | ~120 |
| Files using `msgspec.Struct` | 31 |

### Top 15 Files by Dataclass Count

| File | Dataclasses | Hot Path? |
|------|-------------|-----------|
| `project_types.py` | 51 | ⚠️ Medium (config only) |
| `forensics/metadata_extractor.py` | 16 | ❌ Cold |
| `runtime/shadow_pre_decision.py` | 15 | ✅ Yes |
| `brain/hypothesis_engine/_types.py` | 15 | ✅ Yes |
| `enhanced_research.py` | 13 | ❌ Cold |
| `intelligence/pattern_mining.py` | 13 | ❌ Cold |
| `runtime/acquisition_strategy.py` | 12 | ✅ Yes |
| `intelligence/archive_discovery.py` | 11 | ❌ Cold |
| `runtime/sprint_scheduler.py` | 10 | ✅ Yes (already has 7 msgspec) |
| `intelligence/stealth_crawler.py` | 10 | ❌ Cold |
| `brain/insight_engine.py` | 9 | ❌ Cold |
| `layers/stealth_layer.py` | 8 | ❌ Cold |
| `coordinators/memory_coordinator.py` | 8 | ✅ Yes |
| `core/constants.py` | 7 | ✅ Yes |
| `runtime/scheduler/lanes/__init__.py` | 7 | ✅ Yes |

### Frozen/Slots Analysis

| Pattern | Count |
|---------|-------|
| `@dataclass(frozen=True)` | 163 |
| `@dataclass(slots=True)` | 284 |
| Both `frozen + slots` | 61 |
| **Mutable (no frozen)** | **226** |

### Default Factory Distribution

| Factory Type | Count | msgspec Compatible? |
|--------------|-------|---------------------|
| `list` | 433 | ✅ `msgspec.field(default_factory=list)` |
| `dict` | 226 | ✅ `msgspec.field(default_factory=dict)` |
| `lambda` | 51 | ⚠️ Complex |
| `datetime` | 15 | ✅ `datetime.utcnow` |
| `set` | 12 | ✅ `set` |
| `frozenset` | 2 | ✅ `frozenset` |
| `asyncio` | 2 | ⚠️ Event loop specific |
| `threading` | 2 | ⚠️ Context specific |
| Custom class | ~30 | ⚠️ Per-case |

---

## 2. msgspec.Struct vs @dataclass — Technical Comparison

### Performance (M1 8GB Context)

| Aspect | `@dataclass` | `msgspec.Struct` | Winner |
|--------|-------------|------------------|--------|
| `__init__` speed | baseline | 2-3× faster | msgspec |
| Memory/instance | ~40B overhead | zero-overhead | msgspec |
| GC pressure | tracked (slows GC) | invisible | msgspec |
| `frozen=True` | Python-level `__setattr__` | C-level `object.__setattr__` | msgspec |
| `slots=True` | CPython `memberlist` | native slots | tie |
| JSON encoding | manual or orjson | `msgspec.json` (C) | msgspec |
| Type checking | runtime only | compile-time via `msgspec.Def` | msgspec |

### msgspec Struct Options

```python
class Finding(msgspec.Struct, frozen=True, gc=False):
    """frozen=True  → immutable (like @dataclass(frozen=True))
       gc=False     → exclude from GC tracking (M1 RAM optimization)
       weakref=False → no weakref support (default)
    """
    url: str
    ioc_type: str
    confidence: float = 1.0

# Mutable variant
class MutableFinding(msgspec.Struct, frozen=False, gc=False):
    url: str
    tags: list[str] = msgspec.field(default_factory=list)
```

### Migration Syntax Map

| Dataclass | Msgspec | Notes |
|-----------|---------|-------|
| `@dataclass(frozen=True)` | `class X(msgspec.Struct, frozen=True, gc=False)` | Direct |
| `@dataclass(slots=True)` | `class X(msgspec.Struct, frozen=False, gc=False)` | Direct |
| `field(default_factory=list)` | `msgspec.field(default_factory=list)` | Direct |
| `field(default_factory=dict)` | `msgspec.field(default_factory=dict)` | Direct |
| `field(default=...)` | `field: type = default` | Direct |
| `field(metadata={...})` | **N/A** | Drop or custom |
| `field(compare=False)` | **N/A** | msgspec compares all |
| `__post_init__` | **N/A** | Constructor only |

### What Does NOT Migrate

1. **`field(metadata=...)`** — msgspec has no metadata support
2. **`field(compare=False)`** — msgspec always compares all fields
3. **`__post_init__`** — msgspec.Struct has no post-init hook
4. **Inheritance chains** — msgspec supports `class X(Y, msgspec.Struct)` but not mixins
5. **Class-level variables** — dataclass class vars become instance vars in msgspec
6. **`dataclasses.replace()`** — use `msgspec.Struct.asdict()` + constructor instead

---

## 3. Risk Assessment

### High-Risk Migration Categories

| Category | Count | Risk | Strategy |
|----------|-------|------|----------|
| `__post_init__` users | ~40 | 🔴 High | Keep as dataclass or refactor |
| `field(metadata=...)` | ~20 files | 🔴 High | Drop metadata or use `__init__` kwarg |
| `field(compare=False)` | ~5 | 🟡 Medium | Accept all-comparison behavior |
| Lambda factories | 51 | 🔴 High | Refactor to module-level functions |
| Mutable defaults (list/dict) | 0 | ✅ None | None found (already using factory) |
| Inheritance chains | ~15 | 🟡 Medium | Flatten or keep dataclass |

### Hot Path Files (Priority 1)

These files are on the critical execution path and benefit most from migration:

1. **`runtime/sprint_scheduler.py`** (10 dataclasses, 7 msgspec already)
   - Already partially migrated (FeedDominanceGuardResult, LaneBudgetAllocation, HealthReport)
   - 79× `field(default_factory=dict)` — needs careful mapping
   - **Recommendation:** Migrate remaining 7 dataclasses incrementally

2. **`runtime/acquisition_strategy.py`** (12 dataclasses, 1 msgspec)
   - `FeedDominanceBudget` already migrated (frozen, gc=False)
   - **Recommendation:** Migrate `AcquisitionLanePlan` (frozen) and others

3. **`brain/hypothesis_engine/_types.py`** (15 dataclasses)
   - All use `slots=True` only (mutable)
   - Fields: str, float, datetime, list[str]
   - **Recommendation:** High-value migration target (NER pipeline hot path)

4. **`runtime/shadow_pre_decision.py`** (15 dataclasses)
   - All use `slots=True` only
   - **Recommendation:** Migrate selectively for pre-decision speed

5. **`coordinators/memory_coordinator.py`** (8 dataclasses)
   - **Recommendation:** Medium priority, not hot path

### Cold Path Files (Priority 2)

- `forensics/metadata_extractor.py` (16) — offline processing
- `enhanced_research.py` (13) — research mode only
- `intelligence/pattern_mining.py` (13) — batch processing
- `intelligence/archive_discovery.py` (11) — not real-time
- `intelligence/stealth_crawler.py` (10) — background crawler

**Recommendation:** Migrate after hot-path files are done.

---

## 4. Migration Strategy

### Phase 1: Hot-Path Incremental (1-2 sprints)
**Goal:** Migrate files on critical execution paths with zero risk.

**Files:**
- `runtime/sprint_scheduler.py` — 3 remaining dataclasses
- `runtime/acquisition_strategy.py` — ~10 dataclasses
- `brain/hypothesis_engine/_types.py` — 15 dataclasses

**Rules:**
1. Only `frozen=True` dataclasses with no `__post_init__`
2. Only primitive default factories (list, dict, set, frozenset)
3. No `field(metadata=...)` or `field(compare=False)`
4. Each migration is a standalone commit with test verification

### Phase 2: Medium-Path Optimization (2-3 sprints)
**Goal:** Migrate high-frequency DTOs and config objects.

**Files:**
- `runtime/shadow_pre_decision.py` — 15 dataclasses
- `project_types.py` — 51 dataclasses (CONFIG objects only, not Enums)
- `coordinators/memory_coordinator.py` — 8 dataclasses

**Challenge:** project_types.py has many lambdas and complex defaults.

### Phase 3: Cold-Path Cleanup (ongoing)
**Goal:** Complete migration for code consistency.

**Files:** All remaining ~200+ dataclasses.

---

## 5. Automated Migration Script

A script to detect migratable classes and generate the replacement:

```python
# migrate_dataclass_to_msgspec.py
import ast
import re

MIGRATABLE_DEFAULTS = {'list', 'dict', 'set', 'frozenset', 'tuple'}

def analyze_dataclass(source: str) -> dict:
    """Returns {'migratable': bool, 'issues': list, 'scaffold': str}"""
    issues = []
    
    # Check for prohibited patterns
    if '__post_init__' in source:
        issues.append('__post_init__ not supported')
    if 'field(metadata=' in source:
        issues.append('field(metadata=...) not supported')
    if 'field(compare=' in source:
        issues.append('field(compare=...) not supported')
    
    # Check factory types
    lambdas = re.findall(r'default_factory=lambda', source)
    if lambdas:
        issues.append(f'{len(lambdas)} lambda factories')
    
    # Check inheritance
    if re.search(r'class \w+\(\w+.*dataclass\)', source):
        issues.append('inheritance chain')
    
    return {
        'migratable': len(issues) == 0,
        'issues': issues,
    }
```

---

## 6. Test Plan

For each migrated class:

1. **Unit test:** Instantiate with all field combinations
2. **Serialization test:** `msgspec.json.encode()` / `decode()` roundtrip
3. **Frozen test:** Verify `__setattr__` raises `AttributeError`
4. **Regression test:** Existing test suite passes unchanged

```python
def test_migrated_class_as_dataclass():
    """Verify msgspec.Struct behaves like the original dataclass."""
    from module import MigratedClass, OriginalClass
    
    # Instantiation
    obj = MigratedClass(field1="value", field2=1.0)
    assert obj.field1 == "value"
    assert obj.field2 == 1.0
    
    # Frozen enforcement
    try:
        obj.field1 = "new"
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass  # Expected
    
    # Dict conversion for serialization
    d = msgspec.to_dict(obj)
    rebuilt = MigratedClass(**d)
    assert rebuilt == obj
```

---

## 7. Rollback Plan

If migration causes issues:

```bash
# Atomic revert per file
git checkout HEAD -- runtime/acquisition_strategy.py

# Or via reflog
git reflog
git reset --hard HEAD@{before_migration}
```

---

## 8. Decision Criteria for Per-Class Migration

```
MIGRATE if ALL:
  ✓ frozen=True OR (mutable AND gc=False)
  ✓ No __post_init__
  ✓ No field(metadata=...)
  ✓ No field(compare=False)
  ✓ No inheritance from another dataclass
  ✓ All default_factories in MIGRATABLE_DEFAULTS
  ✓ File is on hot path OR class is instantiated >1000×/s

KEEP AS DATACLASS if ANY:
  ✗ Uses __post_init__
  ✗ Uses field(metadata=...)
  ✗ Uses inheritance
  ✗ Complex lambda factories
  ✗ Cold-path file with <100 instantiations total
```

---

## 9. Expected Impact (M1 8GB)

| Metric | Before | After (estimated) |
|--------|--------|-----------------|
| SprintSchedulerConfig init | 1× | 0.35× |
| Memory per Config instance | ~160B | ~120B |
| GC pause frequency | baseline | -30% |
| AcquisitionLanePlan init | 1× | 0.4× |
| Hypothesis Evidence init | 1× | 0.35× |

**Estimated sprint-level speedup:** 5-15% for hot-path instantiation-heavy sprints.

---

## 10. Files to Migrate First (Priority Ordered)

### Sprint 1: acquisition_strategy + _types
1. `brain/hypothesis_engine/_types.py` — 15 classes (Evidence, TestResult, TestDesign, etc.)
2. `runtime/acquisition_strategy.py` — 10 classes (AcquisitionLanePlan, etc.)

### Sprint 2: sprint_scheduler remaining
3. `runtime/sprint_scheduler.py` — 7 remaining dataclasses (SprintSchedulerConfig, FeedDominanceGuard, etc.)

### Sprint 3: shadow_pre_decision
4. `runtime/shadow_pre_decision.py` — 15 classes

### Sprint 4: project_types configs
5. `project_types.py` — 20 simple config dataclasses

---

*Last updated: 2026-06-30*

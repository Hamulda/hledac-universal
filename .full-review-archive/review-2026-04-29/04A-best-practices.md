# Best Practices Review — Python/Asyncio/MLX Patterns
**Date:** 2026-04-29
**Review Scope:** hledac/universal/

---

## Summary

| Category | Finding Count | Critical | High | Medium | Low |
|----------|---------------|----------|------|--------|-----|
| Async Execution | 2 | 0 | 1 | 1 | 0 |
| Data Structures | 3 | 0 | 1 | 2 | 0 |
| MLX Usage | 1 | 0 | 0 | 1 | 0 |
| Modern Python | 3 | 0 | 0 | 3 | 0 |
| Dependencies | 2 | 0 | 0 | 2 | 0 |
| **Total** | **11** | **0** | **2** | **9** | **0** |

---

## 1. Async Execution Patterns

### 1.1 HIGH: execution_optimizer._run_in_executor_safe — Fallback asyncio.run() Creates Nested Loop

**File:** `utils/execution_optimizer.py:404-406`

**Current Pattern:**
```python
try:
    asyncio.get_running_loop()
except RuntimeError:
    # No running loop in this thread - create one with asyncio.run()
    return asyncio.run(func())  # ← Creates new nested loop
# A loop is running in this thread...
loop = asyncio.get_running_loop()
return loop.run_until_complete(coro)
```

**Issue:** The `asyncio.run(func())` fallback creates a nested event loop when no loop exists. While this doesn't crash in the normal path (only in the fallback), it's architecturally inconsistent with the M1-safety pattern used in `brain/inference_engine.py:435-446`.

**Recommended Pattern (per inference_engine.py):**
```python
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    # Create event loop in current thread for async execution
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
loop = asyncio.get_running_loop()
return loop.run_until_complete(coro)
```

**Migration:** Low priority — current code is M1-safe for the normal path (the fallback only triggers when no loop exists, which is safe).

---

### 1.2 MEDIUM: asyncio.run() at Entry Points — Documented but Scattered

**Files:** Multiple `core/__main__.py`, `smoke_runner.py`, etc.

**Finding:** `asyncio.run()` used at application entry points (correct) but no central documentation that this is the only approved pattern.

**Status:** ✅ **COMPLIANT** — Entry point `asyncio.run()` is correct. No issues.

---

## 2. Data Structures — Unbounded Growth

### 2.1 HIGH: defaultdict(list) Without maxlen in self_healing.py

**File:** `security/self_healing.py:183-187`

**Current Pattern:**
```python
self.circuit_breakers = defaultdict(CircuitBreaker)
self.health_history = defaultdict(deque)  # ← No maxlen
self.component_status = defaultdict(dict)
self.active_healing = defaultdict(bool)
```

**Issue:** `health_history` uses `deque` without `maxlen`, allowing unbounded memory growth on long-running processes.

**Recommended Pattern:**
```python
from collections import deque

self.health_history = defaultdict(lambda: deque(maxlen=1000))
```

**Bounds Table:**

| Collection | Current | Recommended | Risk |
|------------|---------|-------------|------|
| `health_history` | unbounded | `maxlen=1000` | Memory exhaustion |

---

### 2.2 MEDIUM: rag_engine.py defaultdict(lambda: defaultdict(int)) — Double Nesting

**File:** `knowledge/rag_engine.py:116`

**Current Pattern:**
```python
self.term_doc_freqs: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
```

**Issue:** Double-nested defaultdict without bounds. If `term_doc_freqs` grows large, memory usage is unbounded.

**Recommended Pattern:**
```python
from collections import defaultdict

# Add bound on outer dict
MAX_TERMS = 50000
self.term_doc_freqs: Dict[str, Dict[int, int]] = defaultdict(
    lambda: defaultdict(int)
)
# Or use bounded dict wrapper
```

**Bounds Table:**

| Collection | Current | Recommended | Risk |
|------------|---------|-------------|------|
| `term_doc_freqs` | unbounded | `MAX_TERMS=50000` + eviction | Memory exhaustion |

---

### 2.3 MEDIUM: duckdb_store — Bounded Pending Sync (FIXED in F205J)

**File:** `knowledge/duckdb_store.py:1317-1318`

**Current Pattern:**
```python
MAX_PENDING_SYNC_MARKERS: int = 10000  # max pending markers before oldest eviction
```

**Status:** ✅ **COMPLIANT** — P0-9 fix already applied. Bounded to 10000 markers.

---

## 3. MLX Usage Patterns

### 3.1 MEDIUM: mlx_lm.generate() vs load() — Verify kv_bits/max_kv_size Placement

**File:** `brain/inference_engine.py`

**Finding:** Per project CLAUDE.md, `kv_bits=4` and `max_kv_size=8192` should be in `mlx_lm.generate()`, **not** `load()`.

**CLAUDE.md Requirement:**
```
kv_bits=4 a max_kv_size=8192 patří do `mlx_lm.generate()`, NE do `load()`
```

**Recommended Verification:**
```python
# WRONG — these should not be in load()
# model = mlx_lm.load(path, kv_bits=4, max_kv_size=8192)

# CORRECT — these go in generate()
response = mlx_lm.generate(
    model,
    prompt,
    kv_bits=4,
    max_kv_size=8192,
    ...
)
```

**Status:** Not confirmed via grep — needs direct verification in inference_engine.py. Medium priority.

---

## 4. Modern Python Patterns

### 4.1 MEDIUM: dataclass Without slots=True (Memory Waste)

**Files:** `planning/cost_model.py`, `forensics/metadata_extractor.py`, etc. (~40+ dataclasses)

**Current Pattern:**
```python
@dataclass
class Finding:
    finding_id: str
    confidence: float
    # ... 10+ fields
```

**Issue:** Without `slots=True`, each dataclass instance has `__dict__` overhead (~56 bytes per instance). With thousands of findings, this adds up.

**Recommended Pattern:**
```python
@dataclass(slots=True)
class Finding:
    finding_id: str
    confidence: float
    # ...

# For frozen=True (immutable), combine both:
@dataclass(frozen=True, slots=True)
class ImmutableFinding:
    finding_id: str
    confidence: float
```

**Already Done:** Some dataclasses use slots=True:
- `utils/shadow_dtos.py:76,86` — `@dataclasses.dataclass(slots=True)`
- `utils/platform_info.py:28,39` — `@dataclass(frozen=True, slots=True)`
- `project_types.py:1368,1399,1449` — `@dataclass(slots=True)`
- `layers/temporal_signal_layer.py:36,46,63` — `@dataclass(frozen=True, slots=True)`

**Bounds Table:**

| Pattern | Current | Recommended | Impact |
|---------|---------|-------------|--------|
| `@dataclass` without slots | ~40+ instances | `@dataclass(slots=True)` | ~56 bytes saved per instance |

---

### 4.2 MEDIUM: typing.List, typing.Dict — Deprecated in Python 3.9+

**Files:** Multiple (~50+ imports)

**Current Pattern:**
```python
from typing import List, Dict, Tuple, Optional, Union
```

**Issue:** Since Python 3.9, `list`, `dict`, `tuple`, `set`, `frozenset` can be used directly as type hints. `typing.List`, `typing.Dict` are deprecated.

**Recommended Pattern:**
```python
# Python 3.9+ (preferred)
def process(items: list[str], mapping: dict[str, int]) -> tuple[str, ...]:
    ...

# With collections.abc for generics
from collections.abc import Sequence, Mapping
def process(items: Sequence[str], mapping: Mapping[str, int]) -> tuple[str, ...]:
    ...
```

**Note:** Using `from __future__ import annotations` (PEP 563) enables string-based type hints which defer evaluation, but has performance implications for large codebases.

**Migration Priority:** Low — functional equivalence, cosmetic improvement only.

---

### 4.3 MEDIUM: from __future__ import annotations — Python 3.10+ Deprecated

**Files:** `scripts/check_torrc.py`, `discovery/source_registry.py`, `fetching/public_fetcher.py`, `layers/ghost_layer.py`, `export/markdown_reporter.py`, etc. (~15 files)

**Current Pattern:**
```python
from __future__ import annotations  # PEP 563
```

**Issue:** In Python 3.10+, `from __future__ import annotations` is deprecated in favor of `type_params` (PEP 649). However, Python 3.10 is not yet minimum supported, and this is low risk until then.

**Recommended:** Monitor Python 3.13 adoption; migration path届时 will be `type_params` or explicit string annotations.

**Status:** ⚠️ **WATCH** — Not critical until Python 3.13 adoption.

---

## 5. Dependencies

### 5.1 MEDIUM: requirements.txt — Duplicate Entry

**File:** `requirements.txt`

**Issue:** `aiohttp-socks>=0.8.0` appears twice:
```
aiohttp-socks>=0.8.0
...
aiohttp-socks>=0.8.0  # duplicate
```

**Recommended Fix:**
```bash
# Remove duplicate from requirements.txt
aiohttp-socks>=0.8.0  # keep one
```

---

### 5.2 MEDIUM: httpx — Inconsistent Import Pattern

**Files:** `intelligence/blockchain_analyzer.py:61`, `intelligence/rir_correlator.py:38`

**Current Pattern:**
```python
import httpx  # Direct httpx import
```

**Issue:** Project uses `curl_cffi` as primary HTTP transport (per architecture), but `httpx` is imported directly in two intelligence modules outside the transport seam.

**Recommended Pattern:**
```python
# If HTTP fetch needed, use project's transport layer:
from hledac.universal.transport.httpx_transport import fetch_via_httpx_h2

# Or if aiohttp is needed:
from hledac.universal.tools.http_client import HttpClient
```

**Status:** ⚠️ **INVESTIGATE** — May be intentional for module-specific needs. Verify if these modules need httpx-specific features not available in transport layer.

---

## 6. Prior Issues Status

| Issue | Status | Evidence |
|-------|--------|----------|
| asyncio.run() M1 crash vectors (4 sites) | ✅ FIXED | inference_engine.py:435-446 correct pattern; other entry points are entry-only |
| Unbounded DuckDB collections | ✅ FIXED | `MAX_PENDING_SYNC_MARKERS=10000` applied |
| Lightpanda hash verification bypass | Not reviewed | Out of scope for best-practices review |
| SprintScheduler 15+ injected dependencies | Not reviewed | Out of scope for best-practices review |
| httpx_transport not documented | ✅ DOCUMENTED | `transport/httpx_transport.py:1-20` has Sprint F206K header |
| DuckDB/LanceDB/Kuzu 4-system complexity | Not reviewed | Architectural concern, not pattern |
| deque.remove() O(n) performance | Not reviewed | Out of scope |

---

## 7. Positive Findings

| Pattern | File | Evidence |
|---------|------|----------|
| ✅ `slots=True` dataclasses | `utils/shadow_dtos.py`, `utils/platform_info.py`, `layers/temporal_signal_layer.py`, `project_types.py` | Already using modern pattern |
| ✅ `frozen=True` dataclasses | Multiple files | Immutable data classes for thread safety |
| ✅ Bounded collections | `duckdb_store.py`, `url_dedup.py` | `RotatingBloomFilter`, `MAX_PENDING_SYNC_MARKERS` |
| ✅ M1-safe asyncio pattern | `brain/inference_engine.py:435-446` | Proper `get_running_loop()` + `run_until_complete()` |
| ✅ `from __future__ import annotations` | ~15 files | Using deferred evaluation where appropriate |
| ✅ Proper `__slots__` | `fetching/public_fetcher.py:93` | `TransportCounters` uses `__slots__` for memory efficiency |

---

## 8. Recommendations Priority Matrix

| Priority | Issue | Fix Effort | Impact |
|----------|-------|------------|--------|
| **P1** | `self_healing.py` unbounded `deque` | 10 min | Memory safety |
| **P2** | `rag_engine.py` unbounded `term_doc_freqs` | 30 min | Memory safety |
| **P3** | `execution_optimizer.py` asyncio.run fallback | 15 min | Consistency |
| **P3** | `httpx` imports investigation | 20 min | Architecture compliance |
| **P4** | `requirements.txt` duplicate entry | 2 min | Cleanup |
| **P5** | Dataclass `slots=True` migration | 4+ hours | Memory optimization |
| **P5** | `typing.List` → `list` migration | 2+ hours | Modernization only |

---

## 9. Test Commands

```bash
# Verify asyncio patterns
rg -n 'asyncio\.run\(' hledac/universal --type py | grep -v 'if __name__'

# Verify bounded collections
rg -n 'defaultdict.*deque|maxlen' hledac/universal --type py

# Verify slots=True usage
rg -n '@dataclass.*slots=True' hledac/universal --type py

# Run tests
pytest hledac/universal/ -q --tb=short
```

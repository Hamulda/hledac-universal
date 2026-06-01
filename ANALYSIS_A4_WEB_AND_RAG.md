# ANALYSIS_A4: Web Automation & RAG Orchestrator Gaps

## 1. Missing Classes Summary

| Class | Import Path | Status | Evidence |
|-------|-------------|--------|----------|
| `AutomationOrchestrator` | `hledac.advanced_web.automation_orchestrator` | **MISSING** | No class definition found, `hledac.advanced_web` is a STUB module |
| `RAGOrchestrator` | `hledac.advanced_rag.rag_orchestrator` | **MISSING** | No class definition found, `hledac.advanced_rag` doesn't exist |
| `UnifiedAIOrchestrator` | `hledac.core.unified_ai_orchestrator` | **STUB ONLY** | `_shims/core_unified_ai_orchestrator.py` contains stub that raises `NotImplementedError` |

---

## 2. AutomationOrchestrator Analysis (`intelligence/web_intelligence.py`)

### 2.1 Import Location
```python
# Line ~30: NOT a static import — uses __import__ with truthiness check
AutomationOrchestrator = __import__(
    'hledac.advanced_web.automation_orchestrator',
    fromlist=['AutomationOrchestrator']
).AutomationOrchestrator
```

### 2.2 Usage Pattern
| Line | Usage | Context |
|------|-------|---------|
| 133 | `self.automation_orchestrator: AutomationOrchestrator \| None = None` | Type annotation, initialized to None |
| 302-304 | Instantiation in `_initialize_components()` | `if AutomationOrchestrator:` check before instantiation |
| 942 | `self.automation_orchestrator is not None` | Metrics reporting |
| 1364-1365 | `await self.automation_orchestrator.cleanup()` | Cleanup in `async def cleanup()` |

### 2.3 Fallback Behavior
**YES — graceful degradation exists:**
```python
if AutomationOrchestrator:
    self.automation_orchestrator = AutomationOrchestrator(...)
else:
    self.automation_orchestrator = None  # remains None, fails silently
```

### 2.4 Browser Automation Context
- **nodriver** is listed in `pyproject.toml` (line 230) as fallback CDP browser
- `web_intelligence.py` does **NOT** directly reference `nodriver`, `playwright`, or `selenium`
- `hledac.advanced_web` is marked as STUB — no real implementation

### 2.5 Functionality Blocked Estimate

| Component | Status | Lines of Code | % Blocked |
|-----------|--------|---------------|-----------|
| `UnifiedWebIntelligence` core (OSINT ops) | FUNCTIONAL | ~1200 | 0% |
| `AutomationOrchestrator` initialization | MISSING | ~5 | 0% (graceful fallback) |
| `automation_orchestrator.cleanup()` | N/A | ~2 | 0% (guarded) |
| **Overall web_intelligence.py** | **GRACEFUL DEG** | **~1380** | **~1-2%** |

**Conclusion:** `web_intelligence.py` has **graceful degradation** — AutomationOrchestrator is optional and the module remains functional without it. No hard dependency.

---

## 3. RAGOrchestrator Analysis (`coordinators/research_coordinator.py`)

### 3.1 Import Location
```python
# Line 293: Lazy import inside __init__
from hledac.advanced_rag.rag_orchestrator import RAGOrchestrator
```

### 3.2 Usage Pattern
| Line | Usage | Method |
|------|-------|--------|
| 195 | `self._rag_orchestrator: Any \| None = None` | Instance variable |
| 291-299 | Initialization with try/except + warning | `__init__` |
| 319-321 | `await self._rag_orchestrator.cleanup()` | `async def cleanup()` |
| 521-523 | `await self._rag_orchestrator.research_and_answer(query, ...)` | `_execute_rag_research()` |

### 3.3 RAG Interface Expected
```python
class RAGOrchestrator:
    async def research_and_answer(
        self,
        query: str,
        confidence_threshold: float,
        priority: int
    ) -> dict[str, Any]:  # Returns {'sources': [...], ...}
```

### 3.4 Knowledge/ Directory Contents
Existing RAG components in `knowledge/`:
- `duckdb_store.py` — DuckDB canonical store (wired)
- `graph_service.py` — DuckPGQ entity graph (wired)
- `graph_rag.py` — `GraphRAGOrchestrator` (different class, EXISTS in universal)

**Key distinction:** `knowledge/graph_rag.py` contains `GraphRAGOrchestrator` which IS implemented. `RAGOrchestrator` (from `hledac.advanced_rag`) does NOT exist.

---

## 4. UnifiedAIOrchestrator Analysis

### 4.1 Import Location
```python
# Line 267: Uses _shims path (not hledac.core)
from _shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator
```

### 4.2 Shim Implementation (STUB)
```python
# _shims/core_unified_ai_orchestrator.py
class UnifiedAIOrchestrator:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("UnifiedAIOrchestrator stub — real implementation missing")
```

### 4.3 Usage Pattern
| Line | Usage | Method |
|------|-------|--------|
| 193 | `self._unified_orchestrator: Any \| None = None` | Instance variable |
| 265-275 | Initialization with try/except + warning | `__init__` |
| 307-309 | `await self._unified_orchestrator.cleanup()` | `async def cleanup()` |
| 460 | `raise RuntimeError("UnifiedAIOrchestrator not available")` | `_execute_research()` |
| 470 | `await self._unified_orchestrator.process_request(research_request)` | `_execute_research()` |

### 4.4 Expected Interface
```python
class UnifiedAIOrchestrator:
    def __init__(self, *args, **kwargs)
    async def initialize()
    async def process_request(request: dict) -> dict  # {'summary': str, 'confidence': float, ...}
    async def cleanup()
```

---

## 5. Architectural Relationship: UnifiedResearchEngine vs UnifiedAIOrchestrator

### 5.1 Two Separate Classes
| Class | File | Purpose |
|-------|------|---------|
| `UnifiedResearchEngine` | `enhanced_research.py` | **EXISTS** — full implementation (90KB+) |
| `UnifiedAIOrchestrator` | `_shims/core_unified_ai_orchestrator.py` | **STUB ONLY** — raises NotImplementedError |

### 5.2 Relationship Analysis
**These are NOT the same class with naming drift.**

- `UnifiedResearchEngine` (enhanced_research.py):
  - Real implementation, fully wired
  - Uses: `intelligence.*` (lazy), `knowledge.rag_engine`, `layers.stealth_layer`
  - Methods: `deep_research(query, depth, query_type, max_results)`

- `UnifiedAIOrchestrator` (research_coordinator.py):
  - Expected to be a general AI research orchestrator
  - Stub implementation
  - Used for: `process_request(research_request)`

**Hypothesis:** `UnifiedAIOrchestrator` is meant to be a higher-level orchestration layer that could potentially use `UnifiedResearchEngine` internally. The stub suggests it was planned but never implemented.

### 5.3 ResearchCoordinator Integration Points
```python
# research_coordinator.py architecture:
ResearchCoordinator
├── _unified_orchestrator  → UnifiedAIOrchestrator (STUB, raises errors)
├── _rag_orchestrator       → RAGOrchestrator (MISSING)
├── _evidence_analyzer      → EvidenceNetworkAnalyzer (MISSING)
└── _stealth_browser        → StealthBrowser (MISSING from hledac.advanced_web)

execute_research_plan()
└── _execute_research_decision()
    ├── _execute_unified_ai_research()    → calls STUB
    ├── _execute_rag_research()           → calls MISSING
    └── _execute_evidence_analysis()      → calls MISSING
```

---

## 6. StealthBrowser Usage

### 6.1 Location
- `coordinators/research_coordinator.py` line 847
- Import: `from hledac.advanced_web.stealth_browser import StealthBrowser`

### 6.2 Usage
```python
async def crawl_url(self, url: str, depth: int = 1) -> dict[str, Any]:
    browser = StealthBrowser()
    content = await browser.fetch(url, depth=depth)
```

### 6.3 Status
`hledac.advanced_web.stealth_browser` — **MISSING** (same module as AutomationOrchestrator)

---

## 7. Summary Findings

### 7.1 Blocked Functionality

| Module | Missing Class | Impact | Degradation |
|--------|---------------|--------|-------------|
| `intelligence/web_intelligence.py` | `AutomationOrchestrator` | ~1-2% | Graceful (None check) |
| `coordinators/research_coordinator.py` | `UnifiedAIOrchestrator` | **30-40%** | **Hard errors** (`raise RuntimeError`) |
| `coordinators/research_coordinator.py` | `RAGOrchestrator` | **30-40%** | **Hard errors** (`raise RuntimeError`) |
| `coordinators/research_coordinator.py` | `StealthBrowser` | **5-10%** | Soft (try/except) |
| `coordinators/research_coordinator.py` | `EvidenceNetworkAnalyzer` | **5-10%** | Hard errors |

### 7.2 Key Observations

1. **`web_intelligence.py` is NOT critical** — has graceful fallback
2. **`research_coordinator.py` has multiple hard dependencies** on missing classes:
   - `UnifiedAIOrchestrator` → raises `RuntimeError` if not available
   - `RAGOrchestrator` → raises `RuntimeError` if not available
   - These are REQUIRED for `execute_research_plan()` to function

3. **`hledac.advanced_web` is a ghost module** — both `AutomationOrchestrator` and `StealthBrowser` expected here but neither exists

4. **`hledac.advanced_rag` doesn't exist at all** — no stub file for `RAGOrchestrator`

5. **`UnifiedAIOrchestrator` has a shim** (vs. no shim for RAGOrchestrator) — suggests it was closer to implementation

6. **`UnifiedResearchEngine` in `enhanced_research.py`** — IS the real research engine, completely separate from the stub `UnifiedAIOrchestrator`

### 7.3 Recommendations (Analysis Only)

| Priority | Action | Complexity |
|----------|--------|------------|
| HIGH | `research_coordinator.py` — either implement `UnifiedAIOrchestrator` or wire to `UnifiedResearchEngine` | Medium |
| HIGH | `research_coordinator.py` — implement `RAGOrchestrator` or integrate `GraphRAGOrchestrator` | Medium |
| LOW | `web_intelligence.py` — consider adding actual `AutomationOrchestrator` integration point | Low |

---

## 8. File Locations Summary

```
EXISTING (real implementations):
├── enhanced_research.py           → UnifiedResearchEngine (WORKING)
├── knowledge/graph_rag.py          → GraphRAGOrchestrator (WORKING)
├── _shims/core_unified_ai_orchestrator.py → UnifiedAIOrchestrator (STUB)

MISSING (no implementation):
├── hledac/advanced_web/automation_orchestrator.py  → AutomationOrchestrator
├── hledac/advanced_web/stealth_browser.py          → StealthBrowser
├── hledac/advanced_rag/rag_orchestrator.py          → RAGOrchestrator
```

---

*Generated: 2026-05-30 | Analysis Type: PURE RESEARCH*
# IMPORT_FIX_KNOWLEDGE_COORD_PIPELINE.md

## Summary

Systematic import audit of `knowledge/`, `coordinators/`, `pipeline/` directories.

## broken_imports.json — 10 filtered records

All 10 records already wrapped in `try/except ImportError` + `logger.warning` fallback. **No additional fix needed.**

| File | Import | Status |
|------|--------|--------|
| `coordinators/monitoring_coordinator.py:194` | `from hledac.core.watchdog import Watchdog` | Already guarded |
| `coordinators/performance_coordinator.py:41` | `AgentExecutionError, CircuitBreakerOpen` | Already guarded |
| `coordinators/research_coordinator.py:273,299` | `UnifiedAIOrchestrator`, `RAGOrchestrator` | Already guarded |
| `coordinators/security_coordinator.py:139,153,167,181` | `StealthEngine, ThreatIntelligence, QuantumResistantCrypto, ZKPResearchEngine` | Already guarded |
| `knowledge/lancedb_store.py:270` | `get_embedding_manager` | Already guarded |

## Actual Fix: embedding_pipeline.py SyntaxError

**Problem:** `SyntaxError: name '_embedding_depth' is assigned to before global declaration` at line 1010.

**Root cause:** `load_embedding_model()` function had two `global _embedding_depth` declarations:
- Line 993: `with _embedding_depth_lock: global _embedding_depth` (correct, at function start)
- Line 1010: Inside `except MemoryPressureError` block (redundant)

Python sees `global` at line 993, then `_embedding_depth += 1` at line 994, then `global` again at line 1010 → "assigned to before global declaration."

**Fix:** Removed redundant `global _embedding_depth` declaration at line 1010.

**Result:** `verify_imports.py` → 2634/2634 files OK

## MIXIN_LOCATIONS Audit (_catalog.py)

All 6 `MIXIN_LOCATIONS` entries reference valid coordinator files:

```python
'FetchCoordinator': '.fetch_coordinator',      # ✅ exists
'GraphCoordinator': '.graph_coordinator',      # ✅ exists
'ArchiveCoordinator': '.archive_coordinator',   # ✅ exists
'ClaimsCoordinator': '.claims_coordinator',     # ✅ exists
'MultimodalCoordinator': '.multimodal_coordinator',  # ✅ exists
'RenderCoordinator': '.render_coordinator',     # ✅ exists
```

No stale references after prior refactoring.
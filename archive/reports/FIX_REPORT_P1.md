# FIX_REPORT_P1.md — Universal Namespace Redirects

**Date**: 2026-05-31
**Scope**: 4 broken imports in `hledac.universal.*` namespace

---

## Summary

| Symbol | Status | Fix |
|--------|--------|-----|
| `Transport` | ✅ FIXED | Added to `_LAZY_EXPORTS` + `__all__` |
| `GraphRAGOrchestrator` | ✅ FIXED | Added to `_LAZY_EXPORTS` + `__all__` |
| `adjust_fetch_workers` | ✅ ALREADY OK | Verified — entry existed in `_LAZY_EXPORTS` |
| `FullyAutonomousOrchestrator` | ✅ FIXED | Added to `_LAZY_EXPORTS` + `__all__` |

**Zero new implementation — all 4 symbols already existed at their expected locations.**

---

## FIX1: Transport — ADDED to `__init__.py`

**File**: `hledac/universal/__init__.py`

**Change1** — `_LAZY_EXPORTS` dict (line ~57):
```python
# BEFORE (line55-58):
    # Transport
    "TransportContext": "hledac.universal.transport.transport_resolver",
    "TransportResolver": "hledac.universal.transport.transport_resolver",
    # Layers

# AFTER:
    # Transport
    "TransportContext": "hledac.universal.transport.transport_resolver",
    "TransportResolver": "hledac.universal.transport.transport_resolver",
    "Transport": "hledac.universal.transport.transport_resolver",
    # Layers
```

**Change 2** — `__all__` list (line ~189-191):
```python
# BEFORE:
    # Transport
    "TransportContext",
    "TransportResolver",
    # Layers

# AFTER:
    # Transport
    "TransportContext",
    "TransportResolver",
    "Transport",
    # Layers
```

**Rationale**: `Transport` enum exists at `transport/transport_resolver.py:40`. Analysis A1 confirmed call sites use `from hledac.universal.transport.transport_resolver import Transport` — top-level export was missing.

---

## FIX 2: GraphRAGOrchestrator — ADDED to `__init__.py`

**File**: `hledac/universal/__init__.py`

**Change 1** — `_LAZY_EXPORTS` dict (after DuckDB store entries):
```python
# BEFORE:
    "create_owned_store": "hledac.universal.knowledge.duckdb_store",

    # Resource allocator

# AFTER:
    "create_owned_store": "hledac.universal.knowledge.duckdb_store",
    # Graph RAG
    "GraphRAGOrchestrator": "hledac.universal.knowledge.graph_rag",

    # Resource allocator
```

**Change 2** — `__all__` list (after DuckDB store entries):
```python
# BEFORE:
    "create_owned_store",
    # Concurrency

# AFTER:
    "create_owned_store",
    # Graph RAG
    "GraphRAGOrchestrator",
    # Concurrency
```

**Rationale**: `GraphRAGOrchestrator` exists at `knowledge/graph_rag.py:92`. Already exported in `knowledge/__init__.py` via `_LAZY_EXPORT_MAP`, but NOT in top-level `__init__.py`. Added to complete the cross-namespace export chain.

---

## FIX 3: adjust_fetch_workers — VERIFIED OK

**File**: `hledac/universal/__init__.py`

**Status**: ALREADY EXISTS — no changes needed.

- Entry existed at `_LAZY_EXPORTS["adjust_fetch_workers"]` → `"hledac.universal.utils.concurrency"` (line 51)
- `adjust_fetch_workers` function exists at `utils/concurrency.py:62`
- Smoke test: `from hledac.universal import adjust_fetch_workers` → ✅ resolves without error

---

## FIX 4: FullyAutonomousOrchestrator — ADDED to `__init__.py`

**File**: `hledac/universal/__init__.py`

**Change1** — `_LAZY_EXPORTS` dict (after AdaptiveSemaphore):
```python
# BEFORE:
    "AdaptiveSemaphore": "hledac.universal.resource_allocator",

    # Concurrency utilities

# AFTER:
    "AdaptiveSemaphore": "hledac.universal.resource_allocator",

    # Orchestrator
    "FullyAutonomousOrchestrator": "hledac.universal.autonomous_orchestrator",

    # Concurrency utilities
```

**Change 2** — `__all__` list (after AdaptiveSemaphore):
```python
# BEFORE:
    "AdaptiveSemaphore",
    # Loader

# AFTER:
    "AdaptiveSemaphore",
    # Orchestrator
    "FullyAutonomousOrchestrator",
    # Loader
```

**Rationale**: `FullyAutonomousOrchestrator` exists via facade chain: `autonomous_orchestrator.py` (root) → `legacy/autonomous_orchestrator.py`. Existing call sites use `from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator` — top-level export was missing.

---

## Final Verification

```bash
$ uv run python -c "
from hledac.universal import Transport, GraphRAGOrchestrator, adjust_fetch_workers, FullyAutonomousOrchestrator
print('Transport OK:', Transport)
print('GraphRAGOrchestrator OK:', GraphRAGOrchestrator)
print('adjust_fetch_workers OK:', adjust_fetch_workers)
print('FullyAutonomousOrchestrator OK:', FullyAutonomousOrchestrator)
print()
print('ALL 4 IMPORTS SUCCESSFUL — no errors')
"

Transport OK: <enum 'Transport'>
GraphRAGOrchestrator OK: <class 'hledac.universal.knowledge.graph_rag.GraphRAGOrchestrator'>
adjust_fetch_workers OK: <function adjust_fetch_workers at 0x10bfbc7d0>
FullyAutonomousOrchestrator OK: <class 'legacy.autonomous_orchestrator.FullyAutonomousOrchestrator'>

ALL 4 IMPORTS SUCCESSFUL — no errors
```

---

## Files Modified

| File | Changes |
|------|---------|
| `hledac/universal/__init__.py` | +3 entries to `_LAZY_EXPORTS`, +3 entries to `__all__` |

**No implementation changes. No new files. Minimal surgical diff.**

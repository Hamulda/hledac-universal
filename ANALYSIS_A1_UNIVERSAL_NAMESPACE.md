# ANALYSIS_A1_UNIVERSAL_NAMESPACE.md

**Scope**: `hledac.universal.*` broken imports — 4 symbols, 7 call sites

---

## Summary

| Symbol | Status | Fix Type |
|--------|--------|----------|
| `adjust_fetch_workers` | **WRONG PATH** | Correct path exists: `hledac.universal.utils.concurrency` |
| `Transport` (enum) | **WRONG PATH** | Exists in `transport.transport_resolver`, not exported from `__init__.py` |
| `FullyAutonomousOrchestrator` | **WRONG PATH** | Exists via facade chain, callers use outdated import path |
| `GraphRAGOrchestrator` | **WRONG PATH** | Exists in `knowledge.graph_rag`, not in `knowledge.__init__` |

**Conclusion**: 4/4 are "wrong path to existing code" — no new implementation needed.

---

## Per-Symbol Analysis

### `adjust_fetch_workers`

| Field | Value |
|-------|-------|
| **Likely location** | `hledac.universal.utils.concurrency:62` (ALREADY EXISTS) |
| **What it does** | Async function that sets `FETCH_SEMAPHORE` limit for M1 resource governor |
| **Signature** | `async def adjust_fetch_workers(new_limit: int) -> None` |
| **Canonical export** | `__init__.py:_LAZY_EXPORTS["adjust_fetch_workers"]` → `hledac.universal.utils.concurrency` |
| **Implementation complexity** | Trivial (already implemented) |
| **Blocker if missing** | CRITICAL — `brain/model_manager.py` and `smoke_runner.py` cannot import |

**Call sites verified:**
- `brain/model_manager.py:26` → `from hledac.universal.utils.concurrency import adjust_fetch_workers` ✅ (correct)
- `smoke_runner.py:77` → `from hledac.universal import adjust_fetch_workers` (uses lazy export via `__init__.py`)
- `tests/probe_f201a/test_smoke_concurrency_contract.py` → `from hledac.universal import ... adjust_fetch_workers` (uses lazy export)

**Problem**: `broken_imports.json` flags this as broken, but `__init__.py` already has the correct lazy export mapping. If the import fails, the issue is in `_LAZY_EXPORTS` lookup or `__getattr__` implementation, not a missing symbol.

**Recommended action**: Verify `__init__.py` lazy export is working. No new code needed.

---

### `Transport` (hledac.universal.transport)

| Field | Value |
|-------|-------|
| **Likely location** | `hledac.universal.transport.transport_resolver:40` (ALREADY EXISTS) |
| **What it does** | Enum: `DIRECT`, `TOR`, `I2P`, `FREENET`, `INMEMORY`, `GOPHER` — transport type classification |
| **Signature** | `class Transport(Enum)` |
| **Canonical export** | NOT in `__init__.py` — only `TransportContext` and `TransportResolver` are exported |
| **Implementation complexity** | N/A — already implemented |
| **Blocker if missing** | HIGH — `policy/nym_policy.py:11` requires this for LinUCB bandit selection |

**Call site verified:**
```
policy/nym_policy.py:11: from hledac.universal.transport.transport_resolver import Transport
```

The import path is explicit and correct — `Transport` is defined in `transport_resolver.py`. The `broken_imports.json` likely flags this because `Transport` is not in the `__all__` or lazy export map of the `transport` package's `__init__.py`.

**Note**: There are TWO `Transport` definitions:
1. `transport/transport_resolver.py:40` — `class Transport(Enum)` (what callers need)
2. `transport/base.py:189` — `class Transport(ABC)` (abstract base for node transports)

Callers use the enum from `transport_resolver.py`.

**Recommended action**: Add `Transport` to `_LAZY_EXPORTS` in `__init__.py`:
```python
"Transport": "hledac.universal.transport.transport_resolver",
```

Or update `policy/nym_policy.py:11` to use the full import path (already correct).

---

### `FullyAutonomousOrchestrator`

| Field | Value |
|-------|-------|
| **Likely location** | `legacy/autonomous_orchestrator.py` (ALREADY EXISTS) |
| **What it does** | Legacy orchestrator — ~31KB implementation in `legacy/` directory |
| **Facade chain** | `__init__.py` → `orchestrator/__init__.py` → `autonomous_orchestrator.py` (root) → `legacy/autonomous_orchestrator.py` |
| **Implementation complexity** | N/A — already implemented |
| **Blocker if missing** | MEDIUM — tests use it, but production uses `SprintScheduler` instead |

**Call sites verified:**
- `tests/test_autonomous_orchestrator.py:71,79,327,383,489` → `from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator`
- `tests/probe_f205d/test_dead_code_archive_manifest.py:144` — uses facade chain
- `tests/test_sprint_f193a_legacy_boundary.py` — references but no explicit import at line 138

**Facade chain exists:**
```
autonomous_orchestrator.py (root) [line 137-143]
  → _FullyAutonomousOrchestrator = getattr(_legacy_mod, "FullyAutonomousOrchestrator", None)
  → _register_facade_export("FullyAutonomousOrchestrator", ...)

orchestrator/__init__.py
  → from ..autonomous_orchestrator import FullyAutonomousOrchestrator
```

**Problem**: Callers import from `hledac.universal.autonomous_orchestrator` (root module), not `hledac.universal.orchestrator`. The root `autonomous_orchestrator.py` IS the facade that re-exports from `legacy/`.

**Recommended action**: Verify the root facade is working. The import path `hledac.universal.autonomous_orchestrator` should resolve via `autonomous_orchestrator.py` (root) → `legacy/autonomous_orchestrator.py`.

---

### `GraphRAGOrchestrator`

| Field | Value |
|-------|-------|
| **Likely location** | `knowledge/graph_rag.py:92` (ALREADY EXISTS) |
| **What it does** | Multi-hop reasoning for KuzuDB — graph-based RAG orchestrator |
| **Signature** | `class GraphRAGOrchestrator` (no explicit base class) |
| **Canonical export** | NOT in `knowledge/__init__.py` |
| **Implementation complexity** | N/A — already implemented |
| **Blocker if missing** | LOW — only referenced in test (`tests/test_sprint_f193a_legacy_boundary.py:40`) |

**Call site verified:**
- `tests/test_sprint_f193a_legacy_boundary.py:40` — TYPE_CHECKING import for `KnowledgeNode`, not `GraphRAGOrchestrator` directly

**Problem**: `GraphRAGOrchestrator` is not exported from `knowledge/__init__.py`. Callers would need full path: `from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator`.

**Recommended action**: Either:
1. Add `GraphRAGOrchestrator` to `knowledge/__init__.py` exports, OR
2. Update callers to use full path `from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator`

---

## Recommended Actions Summary

| Symbol | Action | File to Modify |
|--------|--------|----------------|
| `adjust_fetch_workers` | Verify lazy export works | `__init__.py` (already has mapping) |
| `Transport` | Add to lazy exports OR confirm import path works | `__init__.py` OR `policy/nym_policy.py` |
| `FullyAutonomousOrchestrator` | Verify facade chain | `autonomous_orchestrator.py` (facade is there) |
| `GraphRAGOrchestrator` | Add to knowledge exports OR update callers | `knowledge/__init__.py` OR `tests/test_sprint_f193a_legacy_boundary.py` |

**No new implementations required. All symbols exist at their expected locations.**

---

## Test Verification Evidence

```
# Transport exists in transport_resolver.py:
transport/transport_resolver.py:40: class Transport(Enum):

# adjust_fetch_workers exists in utils/concurrency.py:
utils/concurrency.py:62: async def adjust_fetch_workers(new_limit: int) -> None:

# FullyAutonomousOrchestrator exists via facade chain:
orchestrator/__init__.py: from ..autonomous_orchestrator import FullyAutonomousOrchestrator
autonomous_orchestrator.py:137: _FullyAutonomousOrchestrator = getattr(_legacy_mod, "FullyAutonomousOrchestrator", None)

# GraphRAGOrchestrator exists in knowledge/graph_rag.py:
knowledge/graph_rag.py:92: class GraphRAGOrchestrator:
```

---

*Generated: 2026-05-30*
*Role: PURE ANALYSIS — no implementation*
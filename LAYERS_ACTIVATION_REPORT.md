# LAYERS_ACTIVATION_REPORT.md — Sprint F26X-GC

**Scope:** `hledac/universal/layers/` (15 files, 12 581 LOC)
**Generated:** 2026-06-04
**Sprint goal:** fix broken import chain, wire GhostCoordinator, surface temporal signal runtime end-to-end.

---

## TL;DR

- **15/15** layer files pass `py_compile` after fixes.
- **15/15** layer modules import cleanly through `hledac.universal.layers.X`.
- **8 missing public symbols** added to `layers/__init__.py` re-exports.
- **1 new class** (`GhostCoordinator`) created in `ghost_layer.py` with dry-run-safe `activate()`.
- **1 new factory** (`get_ghost_coordinator()`) added to `layers/__init__.py`.
- **1 smoke test** (`tests/test_temporal_signal_smoke.py`) — **PASSED** in 1.11s.

---

## What Was Actually Broken

The user's prompt claimed a "missing export in `__init__.py`". Audit found:

| # | Missing symbol | Source | Impact |
|---|---|---|---|
| 1 | `GhostConfig` | `hledac.universal.project_types` | Cannot construct `GhostLayer(config=...)` cleanly from layers namespace |
| 2 | `ActionResult` | `hledac.universal.project_types` | Used in ghost_layer.py, unreachable from layers.* |
| 3 | `ActionType` | `hledac.universal.project_types` | Used in ghost_layer.py, unreachable from layers.* |
| 4 | `StagnationError` | `hledac.universal.project_types` | Raised by GhostLayer, unreachable for callers to catch |
| 5 | `ProcessInfo` | defined in `ghost_layer.py:555` | Dataclass unreachable from layers.* |
| 6 | `SecurityEvent` | defined in `ghost_layer.py:570` | Dataclass unreachable from layers.* |
| 7 | `GhostCoordinator` | **did not exist** | No canonical high-level Ghost facade; callers had to construct GhostLayer directly |
| 8 | `get_ghost_coordinator()` | **did not exist** | No factory accessor (parallel to `get_stealth_layer()` etc.) |

**What was NOT broken:**
- All 15 layer module files import cleanly through the package path (verified with `__import__` of every module).
- All internal `hledac.*` imports use proper package paths or are inside fail-soft `try/except` blocks.
- All `from .X` (relative) imports in `__init__.py` and `communication_layer.py` work as part of the package.

**Misconceptions in original spec corrected:**
- ❌ "GhostConfig from `hledac/config/settings.py`" → ✅ GhostConfig is in `hledac.universal.project_types` (re-exported via `hledac.universal.config.__init__`)
- ❌ "TemporalSignalRuntime" class → ✅ it's the **module** `temporal_signal_runtime.py`; the runtime is exposed via factory functions like `get_temporal_signal_layer()`, `reset_temporal_signal_layer()`, etc.
- ❌ ".tick()" → ✅ the actual event surface is `TemporalSignalLayer.observe(TemporalEvent)`

---

## Readiness Matrix

| # | File | LOC | Assessment | Mount point in `runtime/sprint_scheduler.py` or `core/__main__.py` |
|---|---|---|---|---|
| 1 | `__init__.py` | 269 | **READY_TO_WIRE** | n/a (package facade) |
| 2 | `communication_layer.py` | 842 | **READY_TO_WIRE** | `core/__main__.py:1446` — `_comm_layer = get_communication_layer()` |
| 3 | `content_layer.py` | 757 | **READY_TO_WIRE** | (used in fetch pipeline via `get_content_cleaner()`) |
| 4 | `ghost_layer.py` | 868+137 (`GhostCoordinator`) | **READY_TO_WIRE** | `core/__main__.py:1471` — `_ghost_layer = get_ghost_layer()` (recommend swap to `get_ghost_coordinator()`) |
| 5 | `hive_coordination.py` | 726 | **EXPERIMENTAL** | (no current caller; used by `smart_coordination.py`) |
| 6 | `layer_manager.py` | 913 | **READY_TO_WIRE** | `runtime/sprint_scheduler.py:5292`, `5641` — `LayerManager` import + use |
| 7 | `memory_layer.py` | 1538 | **READY_TO_WIRE** | (M1 invariant — wired via `M1ResourceGovernor`) |
| 8 | `privacy_layer.py` | 548 | **NEEDS_FURTHER_WORK** | (separate `PRIVACY_GATE_AUDIT` track) |
| 9 | `research_layer.py` | 441 | **EXPERIMENTAL** | (no current caller; uses legacy `hledac.research.depth_maximizer`, `hledac.cortex.hunter` — fail-soft) |
| 10 | `security_layer.py` | 1217 | **READY_TO_WIRE** | (audit/mission audit, used by `audit_log.py` consumer) |
| 11 | `smart_coordination.py` | 561 | **NEEDS_FURTHER_WORK** | (depends on `hive_coordination` — gating on hive assessment) |
| 12 | `stealth_layer.py` | 2775 | **READY_TO_WIRE** | `core/__main__.py:1458` — `_stealth_layer = get_stealth_layer()` |
| 13 | `temporal_signal_layer.py` | 690 | **READY_TO_WIRE** | `runtime/sprint_scheduler.py:15962` — `get_temporal_signal_layer()` |
| 14 | `temporal_signal_runtime.py` | 288 | **READY_TO_WIRE** | `runtime/sprint_scheduler.py:15962-15964` — `get_temporal_signal_layer` + `event_from_finding_like` |
| 15 | `temporal_signal_store.py` | 148 | **READY_TO_WIRE** | (env-gated via `HLEDAC_ENABLE_TEMPORAL_STORE=1`, no hard mount needed) |

---

## GhostCoordinator Design (Sprint F26X-GC)

```python
class GhostCoordinator:
    """High-level wrapper over GhostLayer with safe dry-run activation."""

    def __init__(self, config: GhostConfig | None = None, *, ghost_layer: GhostLayer | None = None): ...
    def activate(self, *, dry_run: bool = True) -> bool: ...
    def is_activated(self) -> bool: ...
    def activation_mode(self) -> str: ...           # "uninitialized" | "dry_run" | "live"
    def get_layer(self) -> GhostLayer | None: ...
    def get_status(self) -> dict: ...                # read-only, never raises
    async def shutdown(self) -> None: ...
```

**Activation modes:**

| Mode | Trigger | Side effects |
|---|---|---|
| `dry_run` (default) | `activate(dry_run=True)` | None beyond in-memory state. Safe to call any time. |
| `live` | `activate(dry_run=False)` | Calls `GhostLayer.initialize()` (may attempt real I/O). Fail-soft. |
| `uninitialized` | before `activate()` | No state. |

**Verified:**
```python
gc = GhostCoordinator()                                  # uninitialized
gc.activate(dry_run=True)                               # True
gc.get_status()                                         # {'activated': True, 'mode': 'dry_run', ...}
```

**Why dry-run is the default:**
SprintScheduler hot-path can pre-warm the coordinator without paying the cost of a full GhostDirector init. Live activation is explicit (`dry_run=False`) — opt-in only.

---

## Wiring Plan

### Already Wired (READY_TO_WIRE, no action needed)

| Layer | Existing call site | Notes |
|---|---|---|
| `get_communication_layer` | `core/__main__.py:1446-1447` | Already imported, returns singleton |
| `get_stealth_layer` | `core/__main__.py:1458-1459` | Already imported, returns singleton |
| `get_ghost_layer` | `core/__main__.py:1471-1472` | Already imported, returns singleton |
| `get_temporal_signal_layer` | `runtime/sprint_scheduler.py:15962` | Already imported + used |
| `event_from_finding_like` | `runtime/sprint_scheduler.py:15964` | Already imported + used |
| `LayerManager` | `runtime/sprint_scheduler.py:5292`, `5641` | Already imported + used |

### Recommended Next Mount

| Layer | Recommended mount point | Why |
|---|---|---|
| `get_ghost_coordinator()` | Add to `core/__main__.py:1471` (replace or complement `get_ghost_layer`) | SprintScheduler should talk to GhostCoordinator (canonical facade), not GhostLayer directly |
| `GhostConfig` (from `hledac.universal.layers`) | `runtime/sprint_scheduler.py` constructor section | Already imported via `project_types`; now also exposed at layers.* for symmetry |

### Gated / Optional

| Layer | Gate | Action |
|---|---|---|
| `get_temporal_signal_store()` | `HLEDAC_ENABLE_TEMPORAL_STORE=1` | Env-gated; no hard mount. Optional cross-run persistence. |
| `MemoryLayer`, `SharedMemoryManager` | M1 ResourceGovernor | Already wired through `core/resource_governor.py` (per CLAUDE.md) |

### Hold / Experimental

| Layer | Why hold |
|---|---|
| `hive_coordination.py` | SmartSpawnedCoordinationIntegration depends on it; gated on the SmartCoordination assessment outcome |
| `research_layer.py` | Legacy imports (`hledac.cortex.hunter`, `hledac.research.depth_maximizer`) — fail-soft, no production caller |
| `smart_coordination.py` | `NEEDS_FURTHER_WORK` — depends on hive_coordination + ResearchLayer maturity |
| `privacy_layer.py` | `NEEDS_FURTHER_WORK` — separate `PRIVACY_GATE_AUDIT` track; not blocking this sprint |

---

## Verification Log

| Check | Command | Result |
|---|---|---|
| py_compile all 15 files | `uv run python -m py_compile layers/*.py` | **15/15 OK** |
| Real package import | `__import__('hledac.universal.layers.X')` for X in 14 modules | **14/14 OK** |
| New symbol surface | `dir(hledac.universal.layers)` | **49 public symbols** (was 41) |
| `GhostConfig` import | `from hledac.universal.layers import GhostConfig` | **OK** |
| `GhostCoordinator` import | `from hledac.universal.layers import GhostCoordinator` | **OK** |
| `GhostCoordinator().activate(dry_run=True)` | no network, no I/O | **OK** (returns True, mode=`dry_run`) |
| `get_ghost_coordinator()` factory | returns `GhostCoordinator` in dry-run | **OK** |
| Smoke test | `uv run pytest tests/test_temporal_signal_smoke.py -v` | **PASSED in 1.11s** |

---

## Files Modified

| File | Change |
|---|---|
| `layers/__init__.py` | Added 7 new re-exports (`GhostConfig`, `GhostCoordinator`, `ProcessInfo`, `SecurityEvent`, `ActionResult`, `ActionType`, `StagnationError`); added `get_ghost_coordinator()` factory; updated `__all__` |
| `layers/ghost_layer.py` | Added `GhostCoordinator` class with dry-run-safe `activate()`; extended `__all__` |
| `tests/test_temporal_signal_smoke.py` | **NEW** — 10-line end-to-end smoke test (verified PASS) |

## Files NOT Modified

- All other 13 layer files: import chains confirmed live; no edits needed.
- `runtime/sprint_scheduler.py`, `core/__main__.py`: existing mount points confirmed; no edits needed for this sprint.

---

*End of report.*

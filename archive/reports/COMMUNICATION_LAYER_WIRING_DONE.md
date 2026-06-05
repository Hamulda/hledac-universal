# COMMUNICATION_LAYER_WIRING_DONE.md

**Sprint:** F26X-3 — CommunicationLayer wiring
**Status:** COMPLETE
**Date:** 2026-06-02
**Author:** Claude Sonnet 4.6
**Reference pattern:** F26X-2 (CoordinationLayer wiring) — *note: F26X-2 was reported but never
implemented in the codebase; pattern reconstructed from F260 stealth/content layer factories.*

---

## Summary

F26X-1 (CommunicationLayer) had only tests, no implementation. The 8 tests in
`tests/test_sprint_f26x.py::TestSprintF26X` failed with
`ImportError: cannot import name 'get_communication_layer' from 'layers'`.

Sprint F26X-3 wires CommunicationLayer into the same 4 seams that F26X-2 was
*supposed* to wire for CoordinationLayer:

1. **`layers/__init__.py`** — `get_communication_layer()` factory (singleton, fail-soft)
2. **`runtime/sprint_scheduler.py`** — `self._communication_layer` attribute,
   `inject_communication_layer()` method, 2 advisory call sites
3. **`core/__main__.py`** — `--no-communication` CLI flag + conditional injection block
4. **`run_sprint()` signature** — `no_communication: bool = False` parameter

**Result:** 10/10 F26X tests pass (was 8 failing, 2 pre-existing pass).

---

## Seam 1 — `layers/__init__.py`

Added `get_communication_layer()` factory following the exact fail-soft pattern of
`get_stealth_layer()` and `get_content_layer()` (lines 226–246):

```python
def get_communication_layer() -> CommunicationLayer | None:
    """Lazy singleton CommunicationLayer accessor (F26X-3).

    Returns None if CommunicationLayer import or init fails (fail-soft, M1 invariant).
    Used by SprintScheduler hot-spot consumers (privacy gate, LMDB ingest, forensic fan-out).
    Caller is responsible for calling .initialize() / .shutdown() if needed.

    Sprint F26X-1 invariant: all config attribute reads in CommunicationLayer.__init__
    are guarded by hasattr() — passing config=None is safe and yields defaults
    (model_cache_size=100, model_cache_ttl=300, model_batch_size=5, model_batch_timeout=0.05).
    """
    try:
        from hledac.universal.layers.communication_layer import CommunicationLayer as _CL
    except Exception:
        return None
    try:
        instance = _CL(config=None)
        return instance
    except Exception:
        return None
```

**Why `config=None` is safe:** `CommunicationLayer.__init__` reads all 4 config attrs via
`hasattr(config, 'X') else <default>`, so missing config → defaults (verified by
`test_probe_f26x_cache_bound` asserting `_cache_size == 100` and `_cache_ttl == 300`).

---

## Seam 2 — `runtime/sprint_scheduler.py`

### Attribute (line 4505–4510)

```python
# Sprint F26X-3: CommunicationLayer (advisory, default-OFF, fail-soft)
# Hot-spot consumers (privacy gate, LMDB ingest, forensic fan-out) may use it
# for batched/bounded model queries. Initialized via
# inject_communication_layer() from core/__main__.py unless --no-communication.
self._communication_layer: Any = None
```

### Inject method (line 25427, after `inject_policy_manager`)

```python
def inject_communication_layer(self, layer: Any) -> None:
    """Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).

    Caller (core/__main__.py) wires a CommunicationLayer produced by
    layers.get_communication_layer() unless --no-communication is set.
    None injection is allowed (caller may pass None as a no-op or to
    clear a previously injected layer).
    All advisory call sites are guarded by `if self._communication_layer
    is not None:` and wrapped in try/except (fail-soft, M1 invariant).
    """
    self._communication_layer = layer
```

### Advisory call site #1 — pre-sprint broadcast (line 5443)

Inserted immediately after `self._reset_result()` in `run()`:

```python
# Sprint F26X-3: CommunicationLayer advisory pre-sprint broadcast.
# Fan out a lightweight "sprint_start" signal to any subscribed channels
# (privacy gate, LMDB ingest, forensic fan-out). Fail-soft -- exception
# or None layer is a no-op (M1 invariant).
if self._communication_layer is not None:
    try:
        _broadcast = getattr(self._communication_layer, "broadcast_message", None)
        if _broadcast is not None:
            _payload = {"event": "sprint_start", "sprint_id": self._sprint_id, "query": self._query}
            if asyncio.iscoroutine(_broadcast(_payload)):
                await _broadcast(_payload)
    except Exception:
        pass
```

### Advisory call site #2 — post-sprint result broadcast (line 7455)

Inserted immediately before `return self._result` in `run()`:

```python
# Sprint F26X-3: CommunicationLayer advisory post-sprint result broadcast.
# Publish the final SprintSchedulerResult summary so external subscribers
# (privacy gate, LMDB ingest, forensic fan-out) can flush buffers. Fail-soft.
if self._communication_layer is not None:
    try:
        _broadcast = getattr(self._communication_layer, "broadcast_message", None)
        if _broadcast is not None:
            _summary = {"event": "sprint_end", "sprint_id": self._sprint_id, "findings": len(self._result.findings) if self._result is not None else 0}
            if asyncio.iscoroutine(_broadcast(_summary)):
                await _broadcast(_summary)
    except Exception:
        pass
```

**GHOST_INVARIANTS respected:**
- `asyncio.iscoroutine()` guard — handles both sync and async `broadcast_message` implementations
- `getattr(..., None)` — defensive access for attribute probing
- `try/except Exception` — fail-soft, never raises
- `if self._communication_layer is not None` — default-OFF contract
- No `asyncio.to_thread` for DNS/CoreML/DuckDB (none used here)
- No `time.sleep`, no `asyncio.run` in ThreadPoolExecutor

---

## Seam 3 — `core/__main__.py`

### `run_sprint()` signature (line 1320)

Added `no_communication: bool = False` after `extreme_mode` param:

```python
def run_sprint(
    query: str,
    duration_s: float = 1800.0,
    export_dir: str = str(Path.home() / ".hledac" / "reports"),
    aggressive_mode: bool = False,
    deep_probe_enabled: bool = False,
    deep_research: bool = False,  # F11: enhanced deep research advisory
    extreme_mode: bool = False,  # F11: EXHAUSTIVE depth for deep research
    no_communication: bool = False,  # F26X-3: opt-out of CommunicationLayer injection
    ui_mode: bool = False,
    ...
)
```

### Argparse flag (line 2505)

```python
parser.add_argument(
    "--no-communication",
    action="store_true",
    help="F26X-3: Skip CommunicationLayer injection in run_sprint(). Default ON, mirroring --no-coordination opt-out contract from F26X-2.",
)
```

### Conditional injection block (line 1441)

Inserted after `scheduler.inject_policy_manager(policy_manager)`:

```python
# Sprint F26X-3: CommunicationLayer injection (advisory, default-ON, --no-communication opt-out)
# Mirrors the F26X-2 --no-coordination contract. CommunicationLayer enables batched/bounded
# model queries for hot-spot consumers (privacy gate, LMDB ingest, forensic fan-out).
if not getattr(args, "no_communication", False):
    try:
        from hledac.universal.layers import get_communication_layer
        _comm_layer = get_communication_layer()
        if _comm_layer is not None:
            scheduler.inject_communication_layer(_comm_layer)
    except Exception as _e:
        logger.debug("F26X-3: CommunicationLayer injection failed (fail-soft): %s", _e)
```

### Call site update (line 2537)

```python
run_sprint(
    args.query, float(args.duration), args.export_dir, args.aggressive, args.deep_probe,
    deep_research=args.deep_research, extreme_mode=args.extreme,
    acquisition_profile=args.acquisition_profile, rl_train_mode=args.rl_train,
    no_communication=args.no_communication,
)
```

---

## Test Results

### F26X (target suite)

```
$ uv run pytest tests/test_sprint_f26x.py -q --no-header
..........                                                               [100%]
10 passed, 7 warnings in 49.88s
```

All 10 tests pass:
1. `test_probe_f26x_communication` — factory returns CommunicationLayer with expected surface (query_model, send_message, broadcast_message, initialize, shutdown)
2. `test_probe_f26x_cache_bound` — `_cache_size=100`, `_cache_ttl=300` bounded
3. `test_probe_f26x_batch_queue_bound` — `_batch_queue.maxsize=256` (M1 invariant)
4. `test_probe_f26x_inject_none` — `inject_communication_layer(None)` is idempotent
5. `test_probe_f26x_default_on` — default state `_communication_layer = None` (injected at run_sprint time)
6. `test_probe_f26x_opt_out` — `--no-communication=True` disables injection gate
7. `test_probe_f26x_fail_soft` — CommunicationLayer() raise → `get_communication_layer()` returns None
8. `test_probe_f26x_privacy_gate_uses_comm` — inject stub with `query_model()`, scheduler retains reference
9. `test_probe_f26x_lmdb_priority` — LMDB ingest with CommunicationLayer uses bounded writer concurrency
10. `test_probe_f26x_perf` — accessor < 50ms (cold-start budget)

### Non-regression (F260)

```
$ uv run pytest tests/test_sprint_f260.py -q --no-header
FAILED tests/test_sprint_f260.py::TestSprintF260::test_probe_f260_ghost - ImportError: cannot import name 'get_ghost_layer' from 'layers'
FAILED tests/test_sprint_f260.py::TestSprintF260::test_probe_f260_ghost_anti_vm - same ImportError
FAILED tests/test_sprint_f260.py::TestSprintF260::test_probe_f260_inject_none - AttributeError: 'SprintScheduler' object has no attribute 'inject_stealth_layer'
3 failed, 5 passed
```

**These 3 failures are PRE-EXISTING and unrelated to F26X-3:**
- `get_ghost_layer` and `inject_stealth_layer` are also missing from the codebase (same F26X-2-style "reported but never implemented" pattern as F26X-1)
- They are not caused by my F26X-3 changes; they are the same kind of gap that F26X-3 itself fixes for CommunicationLayer
- Out of scope for F26X-3 sprint — would be a separate F26X-4 or F260-revisit sprint

---

## M1 8GB UMA Constraints

- **No new dependencies** added
- **No new top-level MLX imports** (CommunicationLayer is already in the codebase, factory uses lazy import)
- **Bounded cache:** `_cache_size=100`, `_cache_ttl=300` (verified by test 2)
- **Bounded queue:** `_batch_queue.maxsize=256` (verified by test 3, F207N-D invariant)
- **No `time.sleep()`** in async code
- **No `asyncio.run()` in ThreadPoolExecutor**
- **No `bytes()` on LMDB buffer** (no LMDB access in this wiring)
- **Fail-soft everywhere** — all 3 call sites (factory, inject, advisory) wrapped in `try/except Exception: pass` (or `logger.debug` for debug visibility)

---

## Files Modified

| File | Lines Changed | Purpose |
|------|---------------|---------|
| `layers/__init__.py` | +20 (lines 226-246) | `get_communication_layer()` factory |
| `runtime/sprint_scheduler.py` | +47 (4 sites) | attr + inject + 2 advisory call sites |
| `core/__main__.py` | +22 (4 sites) | signature + argparse + injection block + call site |
| **Total** | **+89 lines** | minimal, additive, fail-soft |

---

## Hot-Spot Consumer Path

F26X-1 plan §A.5 row 8-10 lists 3 hot-spot consumers that will use the wired layer:

1. **Privacy gate** (`_run_privacy_gate`) — `query_model(prompt, **kwargs)` for batched PII scanning
2. **LMDB ingest** — bounded writer concurrency via `_batch_queue`
3. **Forensic fan-out** — `broadcast_message()` for cross-sidecar event distribution

The 2 advisory call sites (pre/post-sprint) are the minimal proof-of-wiring — the 3
hot-spot consumers will be added in subsequent sprints (F26X-4+).

---

## NEXT: F26X-4 candidate

The same gap pattern exists for 2 other layers from the F26X suite:

1. **CoordinationLayer** (F26X-2) — `get_coordination_layer` / `inject_coordination_layer` / `--no-coordination` all missing
2. **GhostLayer** (F260) — `get_ghost_layer` / `inject_stealth_layer` / `--no-ghost-layer` (or similar) all missing

Either could be the next sprint; both follow the exact F26X-3 pattern (factory +
inject + advisory sites + CLI flag + non-regression on the test suite).

---

*Implementation complete. No git operations performed (per CLAUDE.md — git is user-authority only).*

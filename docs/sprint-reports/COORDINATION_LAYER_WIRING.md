# COORDINATION_LAYER_WIRING — Sprint Planning Doc

**Status:** Audit complete. Wiring NOT yet implemented.
**Sprint scope:** Wire a coherent `CoordinationLayer` API into the sprint lifecycle. The audit found that `layers/coordination_layer.py` **does not exist** — the F260 design doc reference was aspirational. The actual coordination surface is split across 3 stub/inconsistent modules that the next sprint must reconcile.

---

## 0. CRITICAL AUDIT FINDING

The prompt assumed `coordination_layer.py` (2159L) exists. **It does not.**

```
$ ls layers/coordination_layer.py
ls: layers/coordination_layer.py: No such file or directory
```

`layers/__init__.py:8` (docstring) and `STEALTH_LAYER_WIRING.md:3` both claim `coordination_layer.py` exists, but the file is missing from the working tree. The coordination surface is fragmented across three real files:

| File | Size | Status | Purpose |
|------|------|--------|---------|
| `layers/hive_coordination.py` | 726 L | **DEPRECATED STUB** (docstring L1-21) | "Integrated into coordination_layer.py" — but that file doesn't exist |
| `layers/smart_coordination.py` | 561 L | **DEPRECATED STUB** (docstring L1-24) | "Integrated into coordination_layer.py" — hardcoded test-session agent_ids |
| `layers/communication_layer.py` | 840 L | **PRODUCTION-READY** (async, bounded, fail-soft) | Real pub/sub + model bridge + semantic routing + A2A |

**Implication for next sprint:** the wiring must (a) decide whether to **create** `coordination_layer.py` as a thin facade over the three real modules, or (b) **delete** the two deprecation stubs and re-export from `hive_coordination.py` only. Section 4 proposes option (a) — the lower-risk path.

---

## 1. File & Line Inventory

| File | Size | Public class | Line | Async init? | SprintScheduler wired? |
|------|------|--------------|------|-------------|------------------------|
| `layers/coordination_layer.py` | **MISSING** | — | — | — | — |
| `layers/hive_coordination.py` | 726 L | `ConnectedCoordinationSystem` | L75 | ❌ no — sync init | ❌ **zero references in SS** |
| `layers/hive_coordination.py` | (same) | `CoordinationLayer` (Enum) | L36 | n/a (Enum) | ❌ re-exported as `HiveCoordinationLayer` in `__init__.py:43` |
| `layers/hive_coordination.py` | (same) | `CoordinationNode`, `CoordinationTask` | L53, L64 | n/a (dataclass) | — |
| `layers/smart_coordination.py` | 561 L | `SmartSpawnedCoordinationIntegration` | L60 | ❌ constructor is sync; `process_task_with_smart_coordination` is async | ❌ zero references in SS |
| `layers/communication_layer.py` | 840 L | `CommunicationLayer` | L99 | ✅ `async initialize()` (L166) + `async shutdown()` (L220) | ❌ zero references in SS |
| `layers/communication_layer.py` | (same) | `MessageContext`, `ModelQuery`, `CacheEntry`, `_BatchItem` | L90, L49, L60, L38 | n/a (dataclasses) | — |
| `layers/__init__.py` | 240+ L | `get_ghost_layer()` (added F260) | L210 | n/a | wired F260 |
| `layers/__init__.py` | (same) | **No** `get_communication_layer()`, `get_coordination_layer()`, `get_hive_coordination()` | — | — | must add in wiring sprint |

**The 3 coordination modules have zero integration into `SprintScheduler`** (verified via `rg "HiveCoordinationLayer|CommunicationLayer|SmartSpawnedCoordinationIntegration|ConnectedCoordinationSystem" runtime/sprint_scheduler.py core/__main__.py` → 0 matches).

---

## 2. What `CommunicationLayer` Actually Adds vs Existing Stack

`CommunicationLayer` is the only production-ready of the three. Its capabilities and how they relate to the existing stack:

### 2.1 Existing stack (SprintScheduler today)
- **Multi-source fetch fan-out**: `coordinators/fetch_coordinator.py` + `runtime/sprint_lifecycle.py` (bounded by `M1_FETCH_SOFT_CEILING_GB`)
- **IOC graph upsert**: `_accumulate_findings_to_graph()` (L17544), currently **serial** per finding
- **DuckDB canonical write**: `async_ingest_findings_batch()` (17 call sites, L9014-L17532) — **each call is one batch**
- **Hermes3 LLM routing**: `brain/inference_engine.py::Hermes3Engine.generate()` — direct calls, no batching
- **Sidecar fan-out**: `run_advisory_runner()` (L6956) — sequential awaits
- **Evidence log writes**: `_accumulate_findings_to_graph` + sidecar bus writes — currently per-finding

### 2.2 CommunicationLayer capabilities (`communication_layer.py:99-680`)

| Capability | Method | Existing | New |
|---|---|---|---|
| **Pub/sub agent messaging** | `send_message` / `broadcast_message` (L263, L309) | ❌ | ✅ bridges `AgentMessagingSystem` from `hledac.communication.*` |
| **LLM model bridge** | `query_model` (L337) | partial (Hermes3 only) | ✅ routes to multiple models by complexity (`hermes-3-4b` for complex, `hermes-3-1.7b` for simple, L424-427) |
| **Response caching with TTL** | `_check_cache` / `_add_to_cache` (L631, L649) | ❌ | ✅ SHA256-keyed, `model_cache_size=100`, `model_cache_ttl=300` (configurable) |
| **Priority batch queue with aging** | `_batch_heap` + `_batch_processor` (L150, L511) | ❌ | ✅ heapq min-heap, **aging rate 0.01/s, cap MAX_PRIORITY_CAP=-0.01** (anti-starvation, Sprint 42) |
| **Dynamic max_batch on M1** | `_update_max_batch` (L448) | partial (M1ResourceGovernor) | ✅ `psutil.virtual_memory().available > 4GB → max_batch=8 else 4` |
| **Bounded asyncio.Queue** | `self._batch_queue` (L144) | ❌ | ✅ `maxsize=256` — F207N-D memory guard |
| **Semantic message routing** | `route_semantically` (L685) | ❌ | ✅ `SemanticMessageRouter` from `emergent_communication.*` |
| **A2A protocol (Google)** | `_a2a_adapter` (L130, L210) | ❌ | ✅ `A2AProtocolAdapter` from `emergent_communication.a2a_protocol_adapter` |
| **Distributed tracing** | `trace_id` propagation (L483) | partial (OpenTelemetry elsewhere) | ✅ per-batch `_current_trace_id` |
| **Fail-soft init** | `initialize()` returns `bool` (L214) | ✅ in all layers | ✅ same pattern, plus 4 graceful-degradation paths (`HAS_COMM_MODULES`, `HAS_EMERGENT`, `enable_batching`, `enable_a2a_protocol`) |
| **Tie-breaker for equal VoI** | `_counter` (L35, L486) | ❌ | ✅ `itertools.count()` stable insertion order |

### 2.3 CommunicationConfig (`project_types.py:1080`)

```python
enable_batching: bool = True
enable_compression: bool = True
enable_agent_messaging: bool = True
enable_model_bridge: bool = True
enable_emergent_comm: bool = True
enable_a2a_protocol: bool = False   # disabled by default — needs Google A2A peer
model_cache_size: int = 100
model_cache_ttl: int = 300
model_batch_size: int = 5
model_batch_timeout: float = 0.05
```

**Init surface (L119):** `def __init__(self, config: CommunicationConfig)` — required, no I/O, all subsystems lazy. `initialize()` is async, fail-soft, returns `bool`.

### 2.4 Memory footprint estimate

| Component | RAM |
|---|---|
| `_cache` dict (model_cache_size=100) | ~50 MB max (cache_entry with response text) |
| `_batch_queue` (maxsize=256, _BatchItem) | ~1 MB |
| `_batch_heap` (max_batch=8 typically) | ~negligible |
| `_latency_history` (deque maxlen=100) | ~negligible |
| AgentMessagingSystem (if loaded) | 5–20 MB (lazy) |
| SemanticMessageRouter + TopicChannelOrganizer (if loaded) | 10–30 MB (lazy) |
| A2AProtocolAdapter (if enabled) | 5 MB |
| **All combined worst case** | **~120 MB** (well within M1 8 GB UMA) |

---

## 3. What `HiveCoordinationSystem` and `SmartSpawnedCoordinationIntegration` Are (And Why They Are NOT Production-Ready)

### 3.1 `ConnectedCoordinationSystem` (`hive_coordination.py:75`)

A **simulated** multi-agent coordination system. Real work happens via `process_task` (L253) which is async, but the storage layer is **synchronous sqlite3** — calling `.commit()` (L619, L629, L640) inside async paths is an M1 crash vector (GHOST_INVARIANT #1 violation: sync I/O in async).

**Capabilities (and their value):**
- ✅ Adaptive topology switching (HIERARCHICAL/MESH/HYBRID/ADAPTIVE, L405-466) — potentially useful for fault tolerance
- ✅ Task distribution via `CoordinationNode.capabilities` matching (L322-339) — matches `required_capabilities` against `node.capabilities`
- ❌ **All storage is fake** — `_store_unified_memory`, `_store_coordination_event`, `_record_topology_change` (L612-640) write to a **synchronous sqlite3** DB at `.hive-mind/connected_memory.db` — **the path is relative to CWD, not `paths.DB_ROOT`** (CLAUDE.md invariant violation)
- ❌ All "intelligence" is keyword-matched: `_extract_capabilities` (L494) does `"research" in description.lower()` — this is a string match, not a model call
- ❌ `_generate_collective_insights` (L551), `_generate_consensus` (L559) return **hardcoded strings** — no LLM call, no actual consensus algorithm
- ❌ Hardcoded `agent_ids` in `__main__` block (L686) and in `smart_coordination.py:79-90` (e.g., `agent_1762976821473_w4tl18` — these are timestamp-based IDs from a prior test session, not production identifiers)

**Verdict:** `ConnectedCoordinationSystem` is a **simulation framework** that demonstrates coordination patterns, but provides zero production value. The only salvageable idea is the `TopologyType` enum and the `process_task` orchestration shape.

### 3.2 `SmartSpawnedCoordinationIntegration` (`smart_coordination.py:60`)

A wrapper around `ConnectedCoordinationSystem` for "smart-spawned" agents. Same problems as §3.1, plus:
- Hardcoded agent_ids (L79-90)
- `process_task_with_smart_coordination` (L129) does **5 sequential awaits** with **simulated 0.1s sleeps** (L241) — this is a demo, not production
- "Performance metrics" are hardcoded values (L181: `coordination_efficiency = 0.92`)

**Verdict:** Zero production value. Safe to **delete** in the wiring sprint (after grep confirms no production caller — already done, 0 references in `runtime/sprint_scheduler.py` / `core/__main__.py`).

### 3.3 Conflict / complement analysis

| Dimension | `CommunicationLayer` (production) | `HiveCoordinationSystem` (stub) | `SmartSpawnedCoordinationIntegration` (stub) |
|---|---|---|---|
| Layer | Communication (messaging + LLM) | Coordination (task distribution) | Coordination (multi-agent spawning) |
| Targets | Inter-agent pub/sub, LLM batching, A2A | Task routing, topology, self-healing | Agent spawning, task execution |
| Async safety | ✅ all `await` paths | ❌ sync sqlite3 in async | ❌ sync storage in `process_*` |
| M1 RAM impact | ~120 MB worst | unbounded (sqlite DB grows) | ~30 MB (in-memory dicts) |
| Production callers in SS | 0 | 0 | 0 |
| Worth wiring? | ✅ **YES** | ❌ NO (rewrite first) | ❌ NO (delete) |

**Cross-cutting interaction:** `CommunicationLayer.query_model()` is the **one** coordination capability that would actually improve SprintScheduler today. It batches Hermes3 calls (currently each LLM call is independent), caches responses, and adapts max_batch to M1 RAM.

---

## 4. Exact Integration Seam (Proposed)

### 4.1 Strategy: thin facade module

**Create `layers/coordination_layer.py`** (the file that F260 deferred) as a **thin facade** that re-exports the production-ready `CommunicationLayer` and provides a unified `get_coordination_layer()` accessor. The 2 stub modules stay (backward compat) but get a `__all__` warning that they are simulated.

```python
# layers/coordination_layer.py — NEW
"""
Coordination Layer — unified facade (Sprint F26X).

Wraps the production-ready CommunicationLayer and exposes a single
get_coordination_layer() accessor for SprintScheduler injection.

NOTE: HiveCoordinationSystem and SmartSpawnedCoordinationIntegration
remain in hive_coordination.py / smart_coordination.py for backward
compatibility but are deprecated simulations — DO NOT use for new code.
"""

from .communication_layer import (
    CommunicationLayer,
    CommunicationConfig,  # re-export
    MessageContext,
    ModelQuery,
)


def get_coordination_layer() -> CommunicationLayer | None:
    """Lazy singleton CommunicationLayer accessor (fail-soft)."""
    try:
        from hledac.universal.project_types import CommunicationConfig as _CC
        cfg = _CC()
        inst = CommunicationLayer(cfg)
        return inst
    except Exception:
        return None
```

### 4.2 Concrete wiring seams (parallel to F260 STEALTH_GHOST_WIRING.md §5.3)

| Seam | File | Method | Type | Purpose |
|------|------|--------|------|---------|
| **A. Singleton accessor** | `layers/coordination_layer.py` (NEW, ~30 L) | `get_coordination_layer()` | lazy singleton | injectable handle for SprintScheduler |
| **B. `__init__.py` re-export** | `layers/__init__.py` L107+ | NEW `get_coordination_layer` in `__all__` | public API | mirror `get_stealth_layer` / `get_ghost_layer` pattern |
| **C. Coordinator injection** | `runtime/sprint_scheduler.py:25446+` (after F260 `inject_ghost_layer`) | NEW `inject_coordination_layer(coord: Any)` | DI seam | add `_coordination_layer` attr |
| **D. Initialization** | `core/__main__.py:1438+` (after F260 stealth/ghost block) | inline call | bootstrap | call `get_coordination_layer()` + inject |
| **E. Mode gate** | `core/__main__.py` (new CLI flag `--coordination` OR reuse `--extreme`) | gate | gate | only inject if `args.extreme or args.coordination` |
| **F. First consumer** | `runtime/sprint_scheduler.py` — LLM call sites (e.g., `brain/inference_engine` invocations in `_run_synthesis_sidecar`, `Brain` synthesis lanes) | wrap in `await self._coordination_layer.query_model()` if injected | advisory (existing direct call is the fallback) | real M1 RAM savings through batch + cache |

### 4.3 Recommended wire-up (concrete diff sketch)

**A. NEW `layers/coordination_layer.py`** — thin facade:
```python
"""Coordination Layer — unified facade (Sprint F26X)."""
from .communication_layer import (
    CommunicationLayer,
    MessageContext,
    ModelQuery,
)


def get_coordination_layer() -> CommunicationLayer | None:
    """Lazy singleton accessor. Returns None on init failure (fail-soft)."""
    try:
        from hledac.universal.project_types import CommunicationConfig
        return CommunicationLayer(CommunicationConfig())
    except Exception:
        return None
```

**B. `layers/__init__.py`** — add re-export:
```python
from .coordination_layer import get_coordination_layer
# + add to __all__: "get_coordination_layer", "CommunicationLayer"
```

**C. `runtime/sprint_scheduler.py`** — after F260 `inject_ghost_layer`:
```python
def inject_coordination_layer(self, coord: Any) -> None:
    """
    F26X: Inject CoordinationLayer (LLM batching, pub/sub, model bridge).

    OWNERSHIP: caller owns coordination lifecycle. Scheduler invokes
    coord.query_model() instead of direct Hermes3.generate() for advisory
    LLM calls — saves RAM through batching + cache hits.

    All calls are fail-soft — exception or None coord → no-op.
    """
    self._coordination_layer = coord
```
Add `self._coordination_layer: Any = None` to `__init__` (next to F260 `_stealth_layer` / `_ghost_layer`).

**D. `core/__main__.py`** — injection block (after F260 stealth/ghost):
```python
# F26X: CoordinationLayer (LLM batching, default ON for non-degraded mode)
# Distinct from F260: coordination is on by default; stealth/ghost are off.
if args.extreme or getattr(args, "coordination", True):
    try:
        from layers import get_coordination_layer
        cl = get_coordination_layer()
        if cl:
            scheduler.inject_coordination_layer(cl)
    except Exception as e:
        logger.warning(f"[F26X] Coordination layer injection failed (non-fatal): {e}")
```

**E. CLI flag** (optional, near `--extreme`):
```python
parser.add_argument(
    "--no-coordination",
    action="store_true",
    help="F26X: Disable CoordinationLayer injection (bypass LLM batching)",
)
```

**F. First consumer** — LLM call site in synthesis lane (pseudocode):
```python
# Before (direct call):
result = await self._hermes3.generate(prompt, ...)

# After (advisory — falls back to direct if not injected):
if self._coordination_layer is not None:
    result = await self._coordination_layer.query_model(
        prompt=prompt, complexity="medium", use_cache=True,
    )
else:
    result = await self._hermes3.generate(prompt, ...)
```

### 4.4 Why NOT a transport wrapper (alternative rejected)

Same reasoning as F260 §5.4: `inject_*` is the canonical SprintScheduler seam. CoordinationLayer is a **coordinator**, not a transport — it does not replace `FetchCoordinator` or `SidecarOrchestrator`. It sits **above** them, batching LLM calls that today are serial per sidecar invocation.

### 4.5 What to do with the 2 stub modules

`hive_coordination.py` and `smart_coordination.py` are deprecation stubs. Two options:

**Option A (recommended — minimal scope):** Leave them as-is, add a `# DEPRECATED — use CoordinationLayer from layers.coordination_layer` warning at the top of each. The thin facade in §4.3 A is the production path.

**Option B (cleaner — defer to follow-up sprint):** Delete both files in a dedicated cleanup sprint. **NOT recommended for F26X** because:
- They are in `__all__` and exported — backward-compat break
- Any external test/importer may reference them
- Cleanup sprint should run as F26X+1 with its own audit and 0-references verification

---

## 5. Performance Impact Estimate

### 5.1 Per-operation overhead (CommunicationLayer only — stubs excluded)

| Operation | Cost | Latency | M1 RAM impact |
|---|---|---|---|
| `get_coordination_layer()` constructor | `CommunicationConfig()` + field setup | **<1 ms** | <1 MB |
| `await initialize()` (no subsystems enabled) | bool return | **<5 ms** | <1 MB |
| `await initialize()` (all subsystems) | 4× import + 3× `.start()` | **~150–300 ms one-shot** | ~80 MB |
| `query_model()` cache hit | SHA256 + dict lookup | **<0.1 ms** | 0 |
| `query_model()` cache miss, direct path | `_execute_query()` → `model_bridge.send_to_model()` | **500–1500 ms** (LLM inference) | 0 transient |
| `query_model()` cache miss, batched path | await `future` from heap → batched `_process_batch_parallel` | **10 ms queue + 500–1500 ms LLM** | +1 MB per queued item |
| `send_message()` direct | `messaging.send_message()` | **<1 ms** | 0 |
| `send_message()` semantic routing | `semantic_router.route_message()` | **5–20 ms** (vocabulary lookup) | 0 |
| `clear_cache()` | dict.clear() | **<1 ms** | frees up to 50 MB |

### 5.2 Sprint-level cost (1 sprint, 30 min, ~200 fetches with coordination ON)

**Without coordination (today's baseline):**
- ~5–15 LLM calls per sprint (synthesis, brief, hypothesis) — each independent, 500–1500 ms
- Total LLM time: **~5–20 s**

**With coordination (F26X target):**
- Same 5–15 LLM calls, but:
  - **Cache hit rate** estimated 20–40% (similar prompts for synthesis across sprints) → saves **1–8 calls** → **0.5–12 s**
  - **Batching** (priority > 2 only) → if 2–4 calls queue together, 1 LLM call can serve multiple → saves **1–3 calls** → **0.5–4.5 s**
  - **Pub/sub** for sidecar events: **negligible** (<10 ms total)
- Net sprint overhead: **−1 to −16 s (savings, not cost)**
- `initialize()` cost (one-shot): **~150–300 ms**

**Total sprint impact: 1–16 s SAVED through cache + batching.** This is a **win**, unlike F260 stealth jitter which was a +100 s cost.

### 5.3 M1 RAM budget check

| Component | Steady-state RAM | Peak RAM |
|---|---|---|
| `CommunicationConfig` | <1 KB | <1 KB |
| `_cache` dict (100 entries × ~500 KB response) | 0–50 MB | 50 MB |
| `_batch_queue` + `_batch_heap` | <1 MB | 5 MB (256 items) |
| AgentMessagingSystem (lazy) | 0 | 20 MB |
| SemanticMessageRouter + TopicChannelOrganizer | 0 | 30 MB |
| A2AProtocolAdapter (off by default) | 0 | 5 MB |
| **All combined worst case** | **~5 MB** | **~110 MB** |

**Within M1 8GB UMA budget** (per CLAUDE.md: macOS 2.5 GB + orchestrator 1 GB + LLM 2 GB + KV 0.75 GB + F260 stealth 0.7 GB + F26X coordination 0.11 GB = **7.06 GB max**, below 8 GB).

### 5.4 Failure-mode cost

- `get_coordination_layer()` raises → `None` returned → SprintScheduler `_coordination_layer is None` check → **0 ms added**
- `await coord.query_model()` raises → caller catches in `except Exception` → falls back to direct `Hermes3.generate()` → **0 ms added** (transparency through `inject_*` None-safety)
- Subsystem missing (`HAS_COMM_MODULES = False`) → `_model_bridge = None` → `query_model` returns `{"success": False, "error": "model_bridge_unavailable"}` → caller falls back → **0 ms added**
- Cache corruption (any key) → SHA256 collision astronomically unlikely → **0 ms added**
- `_batch_task` dies → next `query_model` restarts it via `if not self._batch_task or self._batch_task.done():` (L502) → **0 ms added** (self-healing on next call)

---

## 6. Invariants & Safety Properties

| # | Invariant | Verification |
|---|-----------|--------------|
| 1 | CoordinationLayer opt-in, default ON for non-degraded sprints | `core/__main__.py` gate `if args.extreme or getattr(args, "coordination", True):` — default True |
| 2 | Fail-soft: any `CommunicationLayer` exception → no-op | `get_coordination_layer()` returns `None` on import or ctor failure (mirrors F260 `get_stealth_layer`) |
| 3 | `query_model()` returns `dict` (never raises) | `try/except Exception` wrap at L407-413 in `communication_layer.py` |
| 4 | `query_model()` cache hit is non-blocking | SHA256 + dict lookup — O(1), no I/O |
| 5 | `query_model()` cache hit NEVER hits the model | `_check_cache` returns `cached` early at L368-377 — no `await model_bridge` |
| 6 | `SprintScheduler` never breaks if `_coordination_layer` is `None` | All consumers must check `if self._coordination_layer is not None:` (mirrors F260 invariant #8) |
| 7 | `_batch_queue` bounded for M1 8GB | `asyncio.Queue(maxsize=256)` (L144) — F207N-D memory guard |
| 8 | `_max_batch` adapts to M1 RAM | `psutil.virtual_memory().available > 4GB → 8 else 4` (L452-453) |
| 9 | `_BatchItem` tie-breaker stable (no priority inversion) | `itertools.count()` monotonic counter (L35, L486) |
| 10 | Aging prevents starvation (no query waits forever) | `AGING_RATE=0.01/s`, cap at `MAX_PRIORITY_CAP=-0.01` (L514-515) |
| 11 | `trace_id` propagation for distributed tracing | `getattr(self, '_current_trace_id', None)` (L483) — set by caller if available |
| 12 | No top-level MLX imports in `core/__main__.py` | `get_coordination_layer()` returns instance; MLX only loads on `model_bridge.send_to_model()` call (which is in `hledac.communication.agent_model_bridge`, not in our seam) |
| 13 | HiveCoordinationSystem and SmartSpawnedCoordinationIntegration NOT in production path | 0 references in `runtime/sprint_scheduler.py` / `core/__main__.py` (verified) |
| 14 | `hive_coordination.py` sync sqlite NOT called from async path | Module-level `_store_unified_memory` etc. are sync. **STUBS UNUSED** — guaranteed by invariant #13. If wired in future, must wrap in `await asyncio.to_thread()`. |

---

## 7. Test Plan (to be implemented in wiring sprint)

| Test ID | Module | Verifies |
|---------|--------|----------|
| `probe_f26x_coordination` | NEW | `get_coordination_layer()` returns non-`None` instance with `CommunicationConfig` |
| `probe_f26x_config_defaults` | NEW | `CommunicationConfig()` has expected defaults (`enable_batching=True`, `model_cache_size=100`, `model_cache_ttl=300`) |
| `probe_f26x_init_async` | NEW | `await coord.initialize()` returns `bool`, no raise even when subsystems missing (fail-soft) |
| `probe_f26x_query_cache_hit` | NEW | 1st `query_model()` miss → 2nd identical → cache hit, `_metrics["cache_hits"] += 1` |
| `probe_f26x_query_fail_soft` | NEW | When `model_bridge=None`, `query_model()` returns `{"success": False, "error": "model_bridge_unavailable"}` (NOT raise, NOT fake success) |
| `probe_f26x_inject_none` | NEW | `SprintScheduler.inject_coordination_layer(None)` does not raise |
| `probe_f26x_consumer_none` | NEW | When `_coordination_layer=None`, `query_model` caller path falls back to direct Hermes3 (no exception, no hang) |
| `probe_f26x_batch_bounded` | NEW | `_batch_queue.maxsize == 256` (F207N-D invariant) |
| `probe_f26x_aging` | NEW | Item with VoI=0.1 waiting 1s gets priority boost (AGING_RATE=0.01) — does not surpass VoI=1.0 (MAX_PRIORITY_CAP invariant) |
| `probe_f26x_perf` | NEW | Median `query_model()` cache-hit call < 0.5 ms (perf bound from §5.1) |
| `probe_f26x_shutdown` | NEW | `await coord.shutdown()` returns None, `_initialized` set to False |
| `probe_f26x_no_hive_in_sprint` | NEW | `ConnectedCoordinationSystem` is NOT referenced anywhere in `runtime/sprint_scheduler.py` / `core/__main__.py` (regression guard against accidental wiring of the stubs) |

All tests in `tests/test_sprint_f26x.py` class `TestSprintF26X`. No new public APIs beyond the `inject_coordination_layer` method.

---

## 8. Out of Scope (deferred)

- **`hive_coordination.py` rewrite** — turn simulation into production (real LLM-backed consensus, real sqlite via `paths.open_lmdb()` or DuckDB instead of sync sqlite3). **Defer** to a separate sprint. Current stubs are unused.
- **`smart_coordination.py` deletion** — same rationale. **Defer** to F26X+1 cleanup sprint.
- **Brain synthesis migration to `query_model()`** — the actual LLM call sites that would benefit from batching (Hermes3 synthesis, brief generation) should be migrated in a follow-up sprint with proper attribution tracking.
- **A2A protocol production enablement** — `enable_a2a_protocol=False` by default. Enabling requires a Google A2A peer endpoint — out of scope for OSINT sprint.
- **`emergent_communication` module deep integration** — vocab_manager, topic_organizer are loaded by CommunicationLayer but not used by any sprint consumer. **Verify all subsystems are fail-soft** before removing.
- **Distributed tracing wire-up** — `trace_id` plumbing exists in CommunicationLayer but no caller sets `_current_trace_id`. Wire to OpenTelemetry span context in a follow-up.

---

## 9. Summary

| Question | Answer |
|----------|--------|
| Does `layers/coordination_layer.py` exist? | **NO.** F260 reference was aspirational. Real coordination is split across 3 modules. |
| Which module is production-ready? | **`CommunicationLayer` (840L)** — async, bounded, fail-soft, M1-aware. |
| Which modules are stubs? | `ConnectedCoordinationSystem` (726L, sync sqlite3, hardcoded intelligence), `SmartSpawnedCoordinationIntegration` (561L, hardcoded agent_ids, simulated work) — both marked DEPRECATED. |
| Is there existing wiring to SprintScheduler? | **None.** All 3 modules have 0 references in `runtime/sprint_scheduler.py` / `core/__main__.py`. |
| Exact integration seam? | NEW `layers/coordination_layer.py` (thin facade) + `get_coordination_layer()` + `inject_coordination_layer()` + `--no-coordination` CLI flag. |
| Performance impact? | **−1 to −16 s SAVED per sprint** (cache + batching of LLM calls), +110 MB worst-case RAM, all fail-soft. |
| What about the stub modules? | Leave in place (option A) — minimal scope, no backward-compat break. Cleanup sprint follows F26X. |
| Mode gate? | **Default ON for non-degraded sprints**, opt-out via `--no-coordination`. Distinct from F260 stealth/ghost (default OFF). |

---

*Audit complete. Wiring sprint scope: ~80–120 lines (facade + 2 inject edits + 12 tests). No production code in `hive_coordination.py` / `smart_coordination.py` is touched.*

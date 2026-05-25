# Layers Fix Report — Sprint F206K

## Executive Summary

**16 layer files** in `layers/`. Import chain broken at `layers/__init__.py` line 18:
`from .communication_layer import CommunicationLayer` — cascades through all 12 module-level imports because each layer uses absolute `hledac.universal.*` paths that fail outside the installed package.

---

## 1. `layers/__init__.py` — Missing Exports

**Problem**: `__all__` lists 67 names but only **61 are imported** (6 missing).

### Missing from `temporal_signal_runtime` import block:

| Missing Name | Source | Kind |
|---|---|---|
| `TemporalSignalStore` | `temporal_signal_store.py` | class |
| `TemporalSignalLayer` | `temporal_signal_layer.py` | class |
| `TemporalEvent` | `temporal_signal_layer.py` | class |
| `TemporalScore` | `temporal_signal_layer.py` | class |
| `TemporalEdgeCandidate` | `temporal_signal_layer.py` | class |
| `_KeyState` | `temporal_signal_layer.py` | internal class |

### Also missing from `temporal_signal_layer.py` (top-level functions, not imported):

| Missing Name | Note |
|---|---|
| `event_from_finding_like` | public factory function |
| `_clamp`, `_compute_cv`, `_compute_autocorr_lag1` | private helpers |

### Fix to `layers/__init__.py`:

```python
# Add after line 84 (temporal_signal_runtime import block):
from .temporal_signal_layer import (
    TemporalEvent,
    TemporalScore,
    TemporalEdgeCandidate,
    _KeyState,
    TemporalSignalLayer,
    event_from_finding_like,
)
from .temporal_signal_store import TemporalSignalStore
```

And add to `__all__` list (around line 162):
```python
    "TemporalSignalStore",
    "TemporalSignalLayer",
    "TemporalEvent",
    "TemporalScore",
    "TemporalEdgeCandidate",
    "event_from_finding_like",
```

---

## 2. All 12 Layer Files — Broken Absolute Imports

Every layer file opens with absolute imports that only work when `hledac` is installed as a site package:

```
from hledac.universal.project_types import GhostConfig, ActionResult, ...
from hledac.universal.utils.uuid7 import new_runtime_id
from hledac.cortex.director import GhostDirector          # ghost_layer
from hledac.supreme.security.ram_disk_vault import ...   # ghost_layer
from hledac.advanced_web.stealth_browser import ...      # stealth_layer
from hledac.neuromorphic.common.neural_events import ... # coordination_layer
from hledac.hermes3.context_manager import ...          # coordination_layer
from communication.agent_messaging import ...           # communication_layer
from emergent_communication.a2a_protocol_adapter import ... # communication_layer
from mlx_lm import ...                                  # memory_layer
from privacy_protection.personal_privacy_manager import ... # privacy_layer
```

**These are architectural failures, not simple fixes.** They require either:
- (A) Convert all absolute `hledac.universal.*` imports to relative `./..` paths — **creates import chaos**
- (B) Keep layers as a **higher-layer wrapper** that imports from the installed `hledac` package — **architecturally correct**

Option B is the right model: layers are above the `hledac.universal` package boundary and should be imported as a standalone module after the core package is installed.

---

## 3. `ghost_layer.py` — GhostCoordinator Does NOT Exist

`GhostCoordinator` is referenced in docs but **does not exist** in `ghost_layer.py`.

### Actual classes in `ghost_layer.py`:
- `GhostConfig` (imported from `project_types.py`)
- `GhostLayer` (main class — wraps GhostDirector)
- `SystemContext`
- `VMThreatLevel`
- `ProcessType`

### GhostConfig Wiring

`GhostConfig` is already correctly wired:
```
ghost_layer.py:28   from hledac.universal.project_types import GhostConfig  ✅
ghost_layer.py:75   self.config = config or GhostConfig()                   ✅
config.py:247       ghost: GhostConfig = field(default_factory=GhostConfig) ✅
```

**No additional wiring needed.** `GhostLayer` already accepts a `GhostConfig` in `__init__` and falls back to `GhostConfig()` defaults.

### GhostDirector Integration

`GhostLayer` dynamically imports `GhostDirector`:
```
ghost_layer.py:199-210   from hledac.cortex.director import GhostDirector
                          self._ghost_director = GhostDirector(...)
```

Fail-soft pattern: if `GhostDirector` unavailable, uses local simulation. ✅

---

## 4. Stealth/Anonymity Capabilities — Already Implemented

### `stealth_layer.py` — Full stealth stack (2738 lines):

| Class | Capability |
|---|---|
| `StealthLayer` | Main coordinator (line 1926) |
| `AdvancedCaptchaSolver` | CAPTCHA solving (line 71) |
| `JavaScriptEvasion` | JS detection evasion (line 482) |
| `BehaviorSimulator` | Human-like behavior (line 1151) |
| `MouseMovement`, `ScrollAction` | Action simulation |
| `FingerprintRandomizer` | Canvas/WebGL fingerprint randomization (line 1568) |
| `FingerprintConfig` | Fingerprint config (line 1535) |
| `BrowserProfile` | Profile management (line 1550) |
| `Chameleon` | Chrome profile mimicry (line 2503) |

**TOR/I2P routing**: `stealth_layer.py` does NOT implement TOR/I2P proxy layers — those live in `transport/` (TorTransport, I2PTransport). `StealthLayer` focuses on **browser-level stealth** (fingerprint, behavior, captcha, JS evasion).

For **network anonymity routing** (TOR, I2P, Nym), the canonical wiring path is via `transport/` modules, not `stealth_layer`.

---

## 5. Layer Readiness Matrix

| Layer | File | Lines | Status | Notes |
|---|---|---|---|---|
| Ghost | `ghost_layer.py` | 867 | **READY** (with fail-soft) | GhostDirector optional; uses simulation if unavailable |
| Stealth | `stealth_layer.py` | 2738 | **READY** | Full browser stealth; CaptchaSolver, FingerprintRandomizer, BehaviorSimulator |
| Temporal Signal Store | `temporal_signal_store.py` | 148 | **READY** | SQLite WAL persistence; env-gated |
| Temporal Signal Layer | `temporal_signal_layer.py` | 689 | **READY** | Pure Python; M1 8GB safe |
| Temporal Signal Runtime | `temporal_signal_runtime.py` | 290 | **READY** | Lazy singleton; fail-soft |
| Memory | `memory_layer.py` | 1527 | **READY** (env-degraded) | mlx_lm import fail-soft |
| Security | `security_layer.py` | 1117 | **READY** | Cryptography, secure destruction |
| Coordination | `coordination_layer.py` | 2159 | **BLOCKED** | Neuromorphic dep (hledac.neuromorphic) |
| Hive Coordination | `hive_coordination.py` | 726 | **BLOCKED** | emergent_communication dep |
| Smart Coordination | `smart_coordination.py` | ? | **BLOCKED** | emergent_communication dep |
| Communication | `communication_layer.py` | 852 | **BLOCKED** | multiple hledac sub-module deps |
| Research | `research_layer.py` | 759 | **BLOCKED** | hledac.cortex.director dep |
| Privacy | `privacy_layer.py` | ? | **BLOCKED** | privacy_protection dep |
| Content | `content_layer.py` | 759 | **READY** | pure utility; ContentCleaner, SearchResult parsers |
| Layer Manager | `layer_manager.py` | ? | **READY** | creates layers dynamically; lazy imports |

### BLOCKED layers — root causes:

| Dep | Used by | Fix |
|---|---|---|
| `hledac.neuromorphic.common.*` | `coordination_layer` | neuromorphic package not available |
| `emergent_communication.*` | `hive_coordination`, `smart_coordination`, `communication_layer` | separate package |
| `hledac.cortex.director` | `ghost_layer`, `research_layer` | optional (fail-soft ✅) |
| `hledac.supreme.security.*` | `ghost_layer` | optional (fail-soft ✅) |
| `mlx_lm` | `memory_layer` | optional (fail-soft ✅) |
| `privacy_protection.*` | `privacy_layer` | separate package |

---

## 6. Wiring Plan (Not Implemented — Per Task)

### Phase 1: Fix `__init__.py` exports (30 min)
Add missing temporal signal imports to `layers/__init__.py`. 10-line change.

### Phase 2: Enable stealth in sprint pipeline
`StealthLayer` + `GhostLayer` are the priority pair for pipeline wiring.

**StealthLayer wiring** candidate path:
```
sprint_scheduler.py → inject StealthLayer via coordinator registry
                  → FetchCoordinator gets stealth session
                  → JA3 fingerprint + behavior simulation active
```

**GhostLayer wiring** candidate path:
```
config.py:247 (SprintConfig.ghost: GhostConfig) → GhostLayer(GhostConfig)
GhostLayer.initialize() → RamDiskVault + LootManager
GhostLayer.activate_stealth_mode() → anti-loop protection active
```

### Phase 3: Temporal signal chain
```
fetch_coordinator.py → temporal_signal_runtime events
                     → TemporalSignalLayer scoring
                     → build_temporal_priority_hints() advisory
                     → duckdb_store enriched
```

### Not recommended for wired integration:
- `coordination_layer` — neuromorphic dep unresolved
- `communication_layer` — emergent_communication dep unresolved  
- `hive_coordination` / `smart_coordination` — same
- `privacy_layer` — privacy_protection dep unresolved
- `research_layer` — cortex.director dep (even though ghost_layer works)

---

## 7. Before/After Import Error Summary

### BEFORE (current state)
```
$ python3 -c "from layers import get_temporal_signal_layer"
Traceback:
  File "layers/__init__.py", line 18, in <module>
    from .communication_layer import CommunicationLayer
  File "layers/communication_layer.py", line 25, in <module>
    from hledac.universal.project_types import CommunicationConfig
ModuleNotFoundError: No module named 'hledac'
```

### AFTER (after Phase 1 fix only — layer-level imports remain broken)
Same error persists for direct layer import. The cascade means **all** layers fail through `__init__.py`.

**True fix** requires either installing `hledac` as a package, or restructuring imports.

---

## 8. Immediate Action Items

1. **Fix `layers/__init__.py`** — add 6 missing temporal signal exports (10 lines)
2. **Create package install path** — `pip install -e hledac/universal` or add to `pyproject.toml`
3. **Verify stealth layer** — `StealthLayer` + `BehaviorSimulator` are strongest candidates for pipeline wiring (anonymity/stealth capability)
4. **GhostLayer** — already correctly wired to `GhostConfig`; no changes needed

**No architectural changes to layers themselves required.** The broken import chain is a packaging issue, not a code issue.
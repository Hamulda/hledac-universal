# Layers Readiness Matrix — 2026-05-23

## Import Status Summary
- All 15 layers: `import *` from `layers/__init__.py` → ✅ OK
- PYTHONPATH required: `/Users/vojtechhamada/PycharmProjects/Hledac`

## Per-File Readiness

| filename | import OK | public classes | wired to sprint | blocker |
|---|---|---|---|---|
| `communication_layer.py` | ✅ | `A2AAgentCard`, `A2AProtocolAdapter`, `AgentRelevanceScorer`, `CommunicationLayer`, `CommunicationConfig`, `MessagePriority` | ❌ standalone | None — A2A protocol layer, no sprint call |
| `content_layer.py` | ✅ | `ContentCleaner`, `ResiliparseCleaner`, `CleaningResult`, `SearchResultItem` | ❌ standalone | None — content extraction, no sprint call |
| `coordination_layer.py` | ✅ | `CoordinationLayer`, `GhostWatchdog`, `DriverStatus`, `CircularBuffer` | ❌ standalone | None — coordination engine, no sprint call |
| `ghost_layer.py` | ✅ | `GhostLayer`, `SystemContext`, `VMThreatLevel`, `ProcessType` | ❌ standalone | None — ghost director wrapper, no sprint call |
| `hive_coordination.py` | ✅ | `HiveCoordination`, `CoordinationNode`, `ConnectedCoordinationSystem` | ❌ standalone | None — distributed coordination, no sprint call |
| `layer_manager.py` | ✅ | `LayerManager`, `LayerHealth` | ✅ via `live_public_pipeline.py:277,279,302` | None |
| `memory_layer.py` | ✅ | `EntropyMaskingManager`, `MemoryLayer` (inferred) | ❌ standalone | None — memory management, no sprint call |
| `privacy_layer.py` | ✅ | `AnonymizationLevel`, `GeneratedProtocol`, `PrivacyLayer` | ❌ standalone | None — privacy engine, no sprint call |
| `research_layer.py` | ✅ | `DeepResearchConfig`, `ResearchLayer`, `ExplorationNode` | ❌ standalone | None — research engine, no sprint call |
| `security_layer.py` | ✅ | `SecurityLayer`, `MissionAudit`, `AuditEntry`, `DestructionResult` | ❌ standalone | None — security/mission layer, no sprint call |
| `smart_coordination.py` | ✅ | `SmartCoordination`, `CoordinationTask` | ❌ standalone | None — smart routing, no sprint call |
| `stealth_layer.py` | ✅ | `StealthLayer`, `BehaviorSimulator`, `FingerprintRandomizer`, `BrowserProfile`, `Chameleon` | ❌ standalone | None — stealth browser layer, no sprint call |
| `temporal_signal_layer.py` | ✅ | `TemporalSignalLayer`, `TemporalEvent`, `TemporalScore`, `TemporalEdgeCandidate`, `event_from_finding_like` | ✅ via `live_public_pipeline.py:2208-2209,3131,3146,4242,4249,4257` | None |
| `temporal_signal_runtime.py` | ✅ | `get_temporal_signal_layer`, `reset_temporal_signal_layer`, `get_temporal_signal_summary`, `is_temporal_store_enabled`, `get_temporal_signal_store`, `load_temporal_signal_snapshot`, `save_temporal_signal_snapshot`, `close_temporal_signal_store`, `build_temporal_priority_hints` | ✅ via `live_public_pipeline.py` (same as temporal_signal_layer) | None |
| `temporal_signal_store.py` | ✅ | `TemporalSignalStore`, `SCHEMA_SQL` | ✅ via temporal_signal_runtime (singleton) | None |

## Integration Verification

### 1. `stealth_layer.rotate_fingerprint()` — ZeroAttribution Integration
**Status**: ✅ Fixed (was missing, now added)

`rotate_fingerprint()` at line 2426 previously only called `FingerprintRandomizer.rotate()`
for JA3/browser profile. **The ZeroAttributionEngine header rotation was NOT being called.**

Fix applied: creates a local `ZeroAttributionEngine()` instance and calls
`fingerprint_rotate_headers()` — same pattern used in `data_leak_hunter.py:234`.
No singleton needed — fail-soft local instantiation keeps the rotation advisory.

### 2. `ghost_layer.GhostConfig` — Wired to `project_types`
**Status**: ✅ Correctly wired

`GhostConfig` imported from `hledac.universal.project_types` (line 28).
`GhostLayer.__init__()` accepts `config: GhostConfig | None` and defaults to `GhostConfig()`.
No additional wiring needed — this is a self-contained layer.

### 3. `layers/__init__.py` — Exports
**Status**: ✅ Correct

All 15 layer modules' public classes are exported. `TemporalSignalRuntime` (the module name)
was mis-asked — the actual singleton accessor is `get_temporal_signal_layer()` and the store
is `TemporalSignalStore`. The __init__.py correctly exports all actual public APIs.

## Sprint Wiring Summary

| Layer | Wired? | Callers |
|---|---|---|
| `layer_manager.py` | ✅ | `live_public_pipeline.py` — LayerManager singleton |
| `temporal_signal_layer.py` | ✅ | `live_public_pipeline.py` — temporal hints, snapshots, reset |
| `temporal_signal_runtime.py` | ✅ | `live_public_pipeline.py` — via temporal_signal_layer |
| `temporal_signal_store.py` | ✅ | `temporal_signal_runtime.py` — singleton via `get_temporal_signal_store()` |
| All others | ❌ | Standalone / not yet integrated |

**Note**: 11 of 15 layers are standalone. This is likely intentional — layers provide
capability gems (stealth, security, privacy, coordination) that may be wired in future
sprints. The 4 already-wired layers (layer_manager, temporal_*) form the active sprint path.

## Syntax Health
All 15 layer files: `ast.parse()` ✅ (no syntax errors)
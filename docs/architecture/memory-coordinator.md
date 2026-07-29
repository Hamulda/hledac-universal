# Universal Memory Coordinator — Architecture Reference

> **Source:** `coordinators/memory_coordinator.py` module docstring (extracted 2026-07-29).
> Extracted from `"""..."""` block to keep in-file docstring ≤ 30 lines.

## Role

Memory management combining:
- Priority-based zones (CRITICAL, HIGH, MEDIUM, LOW)
- Aggressive garbage collection with MLX cache clearing via `mlx_memory` adapter
- Thread-safe operations with locks
- Memory pressure callbacks

## Class Index

### Memory Allocation & Zones (~lines 145–220)

| Class | Description |
|-------|-------------|
| `MemoryPressureLevel` | Enum: NORMAL, ELEVATED, HIGH, CRITICAL |
| `ThermalState` | IntEnum: NORMAL, WARM, HOT, CRITICAL |
| `MemoryZone` | Enum: CRITICAL, HIGH, MEDIUM, LOW (priority-based memory zones) |
| `MemoryAllocation` | Dataclass: per-zone allocation entry with used/peak/frag |
| `MemoryStatistics` | Dataclass: global memory stats |
| `ZoneStatistics` | Dataclass: per-zone memory stats |

### Core Coordinator (~lines 220+)

`UniversalMemoryCoordinator` — main facade; thread-safe zone management,
MLX coupling is lazy/fail-soft.

### Neuromorphic STDP Layer (F320-10)

> Moved to `knowledge/neuromorphic.py` behind `HLEDAC_ENABLE_NEURO=1` (default OFF).

| Class | Description |
|-------|-------------|
| `NeuromorphicMemoryZone` | Enum: WORKING_MEMORY, LONG_TERM_MEMORY, EPISODIC_BUFFER |
| `NeuromorphicMemoryManager` | STDP-based neuromorphic memory with zone transitions |
| `MemoryPattern` | Dataclass: temporal pattern with timestamp, intensity, frequency |
| `STDPParameters` | Dataclass: spike-timing-dependent plasticity config |

### Context Optimization (F320) — MOVED

Use: `from coordinators.memory import ContextOptimizationManager`

Classes: `ContextOptimizationManager`, `ContextPriority`, `ResearchPhase`,
`ContextItem`, `CompressedContext`

### Multi-Level Cache (F320) — MOVED

Use: `from coordinators.memory import MultiLevelContextCache`

Classes: `CacheType`, `CacheLocation`, `CacheEntry`

### Memory Pressure Polling (~lines 700+)

`MemoryPressurePoller` — background poller with callbacks on pressure transitions.

## Notes

- MLX memory coupling is **lazy and fail-soft**: MLX is not loaded or initialized by this module
- Neuromorphic subsystem (F320-10) is gated behind `HLEDAC_ENABLE_NEURO=1` (default OFF)
- `get_reranking_context()` is the narrow seam for Lancedb/reranking with thermal/battery awareness

## See Also

- `utils/mlx_memory.py` — MLX cache management
- `knowledge/neuromorphic.py` — Neuromorphic memory (behind feature flag)
- `coordinators.memory` — Context optimization and multi-level cache

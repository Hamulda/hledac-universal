# SWARM_COORDINATOR_DECOMPOSITION_AUDIT.md
**Audit Date:** 2026-05-18
**Scope:** `coordinators/swarm_coordinator.py`
**Goal:** Identify smallest safe extraction seams without behavior change.

---

## 1. Current File Overview

**File:** `coordinators/swarm_coordinator.py` (~915 lines)
**Classes defined (8):** `SwarmState`, `SwarmMetrics`, `AdaptiveStrategy`, `SwarmAgent`, `SwarmNode`, `SwarmTask`, `ConsensusProposal`, `UniversalSwarmCoordinator`

**Exported at:** `coordinators/__init__.py` (lines 70–75) and `coordinators/_catalog.py`

**External callers:**
- `orchestrator_integration.py:77` — conditional import, line 119: `UniversalSwarmCoordinator()`
- `coordinator_registry.py:531` — import, line 585: instantiation

---

## 2. Usage Matrix

| Class | Type | Behavior Methods | Only In swarm_coordinator? | External Callers | Tests |
|-------|------|------------------|----------------------------|------------------|-------|
| `SwarmState` | Enum | `.value` | ✅ Yes | `coordinators/__init__.py` (re-export) | ❌ |
| `SwarmMetrics` | Dataclass | fields only | ✅ Yes | `coordinators/__init__.py` (re-export) | ❌ |
| `AdaptiveStrategy` | Dataclass | fields only | ✅ Yes | `_initialize_adaptive_strategies`, `_execute_adaptive_strategies` | ❌ |
| `SwarmAgent` | Dataclass | fields only | ✅ Yes | `add_agent`, `remove_agent`, `_monitor_swarm` | ❌ |
| `SwarmNode` | Dataclass | `update_reputation`, `heartbeat`, `check_health` | ✅ Yes | `register_node`, `assign_task`, `run_heartbeat_monitor`, `submit_task_result`, `get_p2p_status` | ❌ |
| `SwarmTask` | Dataclass | `__lt__` (priority queue) | ✅ Yes | `submit_task`, `assign_task`, `submit_task_result`, `_reassign_node_tasks` | ❌ |
| `ConsensusProposal` | Dataclass | `add_vote`, `get_result` | ✅ Yes | `create_proposal`, `vote_on_proposal`, `get_p2p_status` | ❌ |
| `UniversalSwarmCoordinator` | Class | All coordinator methods | ✅ Yes | `orchestrator_integration`, `coordinator_registry` | ❌ |

**Finding:** Zero test coverage for any swarm class. All 8 types are purely internal with zero independent callers.

---

## 3. Domain Decomposition

The file mixes 5 distinct domains:

### Domain A — Swarm State & Metrics (`SwarmState`, `SwarmMetrics`)
- **Type:** Pure data enums + dataclasses
- **Behavior:** None (metrics) / enum access (state)
- **Used by:** `_monitor_swarm`, `_analyze_swarm_state`, `get_swarm_status`
- **Suggested extraction:** `coordinators/swarm/types.py`

### Domain B — Particle Swarm (`SwarmAgent`)
- **Type:** Pure data dataclass (PSO-inspired)
- **Behavior:** Fields only
- **Used by:** `add_agent`, `remove_agent`, `_monitor_swarm`, `_execute_strategy_actions`, `_check_fault_tolerance`
- **Suggested extraction:** `coordinators/swarm/types.py`

### Domain C — P2P Node Reputation + Heartbeat (`SwarmNode`)
- **Type:** Dataclass with behavior
- **Behavior methods:** `update_reputation`, `heartbeat`, `check_health`
- **Used by:** `register_node`, `assign_task`, `run_heartbeat_monitor`, `submit_task_result`, `get_p2p_status`
- **Suggested extraction:** `coordinators/swarm/node.py`

### Domain D — Task Priority + Consensus (`SwarmTask`, `ConsensusProposal`)
- **Type:** Dataclass with behavior
- **Task behavior:** `__lt__` (priority queue)
- **Consensus behavior:** `add_vote`, `get_result`
- **Used by:** `submit_task`, `assign_task`, `submit_task_result`, `create_proposal`, `vote_on_proposal`
- **Suggested extraction:** `coordinators/swarm/consensus.py`

### Domain E — Adaptive Strategies (`AdaptiveStrategy`)
- **Type:** Pure data dataclass
- **Behavior:** Fields only
- **Used by:** `_initialize_adaptive_strategies`, `_execute_adaptive_strategies`, `_execute_strategy_actions`
- **Suggested extraction:** `coordinators/swarm/types.py`

### Coordinator (thin owner)
- **UniversalSwarmCoordinator** remains in `coordinators/swarm_coordinator.py`
- Acts as owner/facade for all 5 domains
- Imports types from submodules after split

---

## 4. Proposed Extraction Path (Future Sprint)

```
coordinators/swarm/
├── __init__.py          # Re-export all types + UniversalSwarmCoordinator
├── types.py             # SwarmState, SwarmMetrics, AdaptiveStrategy, SwarmAgent
├── node.py              # SwarmNode (update_reputation, heartbeat, check_health)
├── consensus.py         # SwarmTask (with __lt__), ConsensusProposal
└── swarm_coordinator.py # UniversalSwarmCoordinator (imports from submodules)
```

**Backward compatibility:**
- `coordinators/__init__.py` re-exports unchanged → `UniversalSwarmCoordinator`, `SwarmState`, `SwarmMetrics`, `AdaptiveStrategy`, `SwarmAgent` remain at original import paths
- No change to `orchestrator_integration.py` or `coordinator_registry.py`

---

## 5. Characterization Tests (Needed — Currently Zero)

| Method | Class | Test Scenario | Expected |
|--------|-------|---------------|----------|
| `SwarmNode.update_reputation(True, 1.0)` | `SwarmNode` | Success call | reputation += 0.1, tasks_completed += 1 |
| `SwarmNode.update_reputation(False, 1.0)` | `SwarmNode` | Failure call | reputation -= 0.2, tasks_failed += 1 |
| `SwarmNode.check_health(30.0)` (fresh node) | `SwarmNode` | Within timeout | True, is_online=True |
| `SwarmNode.check_health(0.0)` (stale node) | `SwarmNode` | Past timeout | False, is_online=False |
| `ConsensusProposal.get_result()` (no votes) | `ConsensusProposal` | Empty votes | (False, 0.0) |
| `ConsensusProposal.get_result()` (majority yes) | `ConsensusProposal` | 3 yes / 2 no, equal weights | (True, 0.6) |
| `ConsensusProposal.get_result()` (majority no) | `ConsensusProposal` | 2 yes / 3 no | (False, 1.0) |
| `SwarmTask.__lt__` | `SwarmTask` | priority 1 < priority 5 | True |

**Constraints for characterization tests:**
- No async
- No network
- No external dependencies (numpy caught at import, mocked)
- Pure unit tests, no integration

---

## 6. This Commit Scope (Audit Only — No Split)

✅ This commit:
- Writes this audit document
- Adds characterization tests for `SwarmNode` and `ConsensusProposal` behavior methods
- Verifies import smoke

❌ This commit does NOT:
- Create `coordinators/swarm/` package
- Move any types to submodules
- Change any import paths
- Modify `UniversalSwarmCoordinator` behavior

---

## 7. Invariants to Preserve in Future Split

1. **Import path stability:** `from coordinators import UniversalSwarmCoordinator` must continue to work
2. **Re-export parity:** `coordinators/__init__.py` re-exports must match after split
3. **No behavior change:** All methods on all 8 types must behave identically before/after split
4. **Test coverage gap:** Characterization tests in this audit prevent regression during future split
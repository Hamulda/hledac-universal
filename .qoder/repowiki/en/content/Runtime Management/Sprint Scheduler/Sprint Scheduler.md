# Sprint Scheduler

<cite>
**Referenced Files in This Document**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document describes the Sprint Scheduler system, a tier-aware feed scheduling mechanism that orchestrates bounded, concurrent acquisition across multiple data sources and branches (public, CT logs, nonfeed). It documents the sprint lifecycle phases (BOOT, WARMUP, ACTIVE, WINDUP, EXPORT, TEARDOWN), phase transitions, and timing controls. It also covers source priority management, concurrent execution strategies, acquisition strategy implementation, source tier mapping, budget allocation algorithms, configuration options, performance tuning, debugging techniques, and recovery mechanisms.

## Project Structure
The Sprint Scheduler resides in the runtime layer and coordinates with lifecycle managers, acquisition strategies, exporters, and sidecar systems. Supporting components include:
- Runtime lifecycle manager controlling phase transitions and timing
- Global scheduler for distributed, priority-based task execution
- Phase controller for orchestration windows and promotion gates
- Windup engine for post-WINDUP cleanup and diagnostics

```mermaid
graph TB
subgraph "Runtime"
SS["SprintScheduler<br/>Tier-aware feed scheduling"]
SL["SprintLifecycleManager<br/>Phase control"]
WE["WindupEngine<br/>Post-WINDUP cleanup"]
end
subgraph "Orchestrator"
GS["GlobalPriorityScheduler<br/>ProcessPoolExecutor"]
PC["PhaseController<br/>Evidence-driven promotion"]
end
subgraph "Integration"
ACQ["AcquisitionStrategy<br/>Lane planning & gating"]
EXP["Exporters<br/>Markdown/JSON/STIX"]
BUS["SidecarBus<br/>Accepted-finding sidecars"]
end
SS --> SL
SS --> ACQ
SS --> BUS
SS --> EXP
GS --> SS
PC --> SL
WE --> SS
```

**Diagram sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)

## Core Components
- SprintScheduler: Tier-aware scheduler that manages feed acquisition, concurrency, and lifecycle integration. It builds acquisition plans, enforces budgets, and coordinates exports.
- SprintLifecycleManager: Canonical state machine governing phases, timing, and transitions.
- GlobalPriorityScheduler: Distributed task execution with priority queues and CPU affinity.
- PhaseController: Evidence-driven phase windows and promotion logic.
- WindupEngine: Post-WINDUP diagnostics and cleanup (currently dormant in production path).

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)

## Architecture Overview
The Sprint Scheduler operates as a sidecar to the lifecycle manager. It:
- Builds an acquisition plan at start
- Executes bounded feed cycles under ACTIVE phase
- Enforces hard deadlines and per-branch timeouts
- Coordinates nonfeed pre-dispatch and return guards
- Exports results and tears down resources

```mermaid
sequenceDiagram
participant Owner as "SprintOwner"
participant Runner as "SprintLifecycleRunner"
participant Scheduler as "SprintScheduler"
participant Plan as "AcquisitionStrategy"
participant Branch as "Branches (Public/CT/Nonfeed)"
participant Export as "Exporters"
Owner->>Runner : start()
Runner->>Runner : tick() → ACTIVE
Scheduler->>Plan : build_acquisition_plan()
loop ACTIVE cycles
Scheduler->>Runner : tick()
Scheduler->>Branch : dispatch per-lane with budgets
Branch-->>Scheduler : outcomes
Scheduler->>Scheduler : enforce budgets & timeouts
alt windup guard satisfied
Runner->>Runner : tick() → WINDUP
Scheduler->>Export : partial export
break
end
end
Runner->>Runner : tick() → EXPORT → TEARDOWN
Scheduler->>Export : finalize export
```

**Diagram sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)

## Detailed Component Analysis

### Sprint Scheduler
The Sprint Scheduler is the operational backbone for 30-minute bounded sprints. It:
- Maintains in-sprint deduplication and per-source counters
- Implements source economics and optional prefetch oracle advice
- Enforces hard deadlines and per-branch timeout budgets
- Coordinates acquisition prelude, nonfeed pre-dispatch, and return guards
- Manages sidecars, metrics, and partial exports

Key responsibilities:
- Tier-aware source prioritization and per-source budgeting
- Concurrent execution with controlled parallelism and timeouts
- Early exit conditions and canonical exit classification
- Integration with acquisition strategy and export pipeline

```mermaid
classDiagram
class SprintScheduler {
+run(lifecycle, sources, now_monotonic, query, ...)
+prioritize_sources(sources, graph_stats)
+record_pivot_outcome(task_type, found_count, elapsed_s)
+_run_one_cycle(...)
+_ensure_mandatory_nonfeed_before_return(...)
+_finalize_result_truth(exit_path, reason, phase, query)
}
class SprintSchedulerConfig {
+float sprint_duration_s
+float windup_lead_s
+float cycle_sleep_s
+int max_cycles
+int max_parallel_sources
+bool stop_on_first_accepted
+dict source_tier_map
+bool aggressive_mode
+float aggressive_branch_timeout_s
+float branch_timeout_budget_s
}
class SourceTier {
<<enum>>
SURFACE
STRUCTURED_TI
DEEP
ARCHIVE
OTHER
}
class SprintSchedulerResult {
+int cycles_started
+int cycles_completed
+int accepted_findings
+dict entries_per_source
+dict hits_per_source
+str final_phase
+list export_paths
+bool aborted
+str abort_reason
+bool stop_requested
+dict public_* telemetry
+dict ct_* telemetry
+dict ct_loss_stage telemetry
+dict acquisition_* telemetry
+dict prewindup_* telemetry
+dict return_guard_* telemetry
+dict branch_timeout_* telemetry
+dict feed_budget_* telemetry
+dict nonfeed_* telemetry
+dict source_family_events
+dict public_terminal_stage + public_stage_counters
}
SprintScheduler --> SprintSchedulerConfig : "configured by"
SprintScheduler --> SourceTier : "maps sources to tiers"
SprintScheduler --> SprintSchedulerResult : "accumulates"
```

**Diagram sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)

### Sprint Lifecycle Manager
The lifecycle manager defines the canonical phases and timing:
- Phases: BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN
- Hard invariant: T-3 minute wind-down
- Timing uses monotonic time; supports recommended tool modes (normal/prune/panic)
- Provides tick-based automatic wind-up when remaining time drops below windup lead

```mermaid
stateDiagram-v2
[*] --> BOOT
BOOT --> WARMUP : "start()"
WARMUP --> ACTIVE : "mark_warmup_done()/transition_to(ACTIVE)"
ACTIVE --> WINDUP : "should_enter_windup() or windup_guard"
WINDUP --> EXPORT : "mark_export_started()"
EXPORT --> TEARDOWN : "mark_teardown_started()"
TEARDOWN --> [*]
```

**Diagram sources**
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)

**Section sources**
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)

### Global Priority Scheduler
A ProcessPoolExecutor-based scheduler with:
- Priority queue for ordered insertion
- CPU affinity to performance cores
- Work stealing with bounded affinity tracking
- Dead-letter queue and idempotency keys
- Timeout checker and result collector threads

```mermaid
flowchart TD
Start(["Schedule Task"]) --> Insert["Insert into PriorityQueue"]
Insert --> WorkerLoop["Worker processes item"]
WorkerLoop --> Running["Report RUNNING"]
WorkerLoop --> Success{"Succeeded?"}
Success --> |Yes| Succeeded["Report SUCCEEDED"]
Success --> |No| Failed{"Retries left?"}
Failed --> |Yes| Retry["Reschedule with same priority"]
Failed --> |No| DLQ["Move to Dead Letter Queue"]
Succeeded --> End(["Done"])
Retry --> WorkerLoop
DLQ --> End
```

**Diagram sources**
- [global_scheduler.py](file://orchestrator/global_scheduler.py)

**Section sources**
- [global_scheduler.py](file://orchestrator/global_scheduler.py)

### Phase Controller
Provides evidence-driven phase windows and promotion:
- Four-phase orchestration with max time windows
- Weighted score promotion based on signals (winner margin, beam convergence, contradiction frontier, etc.)
- Thermal-aware beam width and priority modifiers
- Time-pressure thresholds and plateau detection via novelty EMA

```mermaid
flowchart TD
Start(["Start"]) --> Phase1["DISCOVERY (≤5m)"]
Phase1 --> Signals1{"Promotion signals?"}
Signals1 --> |Time ceiling| Phase2["CONTRADICTION (≤15m)"]
Signals1 --> |Score ≥ threshold| Phase2
Phase2 --> Signals2{"Promotion signals?"}
Signals2 --> |Time ceiling| Phase3["DEEPEN (≤24m)"]
Signals2 --> |Score ≥ threshold| Phase3
Phase3 --> Signals3{"Promotion signals?"}
Signals3 --> |Time ceiling| Phase4["SYNTHESIS (≤30m)"]
Signals3 --> |Score ≥ threshold| Phase4
Phase4 --> End(["Continue until time or terminal"])
```

**Diagram sources**
- [phase_controller.py](file://orchestrator/phase_controller.py)

**Section sources**
- [phase_controller.py](file://orchestrator/phase_controller.py)

### Windup Engine
A post-WINDUP cleanup and diagnostics module (currently dormant in production). It performs:
- Deduplication and ranking
- Graph statistics and anomaly detection
- Semantic deduplication and synthesis routing
- Hypothesis enqueue and checkpointing

```mermaid
flowchart TD
Start(["run_windup()"]) --> Dedup["Parquet dedup + ranking"]
Dedup --> GNN["GNN inference + anomalies"]
GNN --> Graph["DuckPGQ stats + top nodes"]
Graph --> ANE["ANE semantic dedup"]
ANE --> Synthesis["MoE synthesis routing + synthesis"]
Synthesis --> Hypotheses["Top-3 hypothesis enqueue"]
Hypotheses --> Checkpoint["DuckPGQ checkpoint"]
Checkpoint --> Scorecard["Build scorecard"]
Scorecard --> End(["Return diagnostics"])
```

**Diagram sources**
- [windup_engine.py](file://runtime/windup_engine.py)

**Section sources**
- [windup_engine.py](file://runtime/windup_engine.py)

## Dependency Analysis
- SprintScheduler depends on:
  - SprintLifecycleManager for phase control and timing
  - AcquisitionStrategy for lane planning and terminality enforcement
  - SidecarBus for accepted-finding sidecars
  - Exporters for markdown/json/stix outputs
  - ResourceGovernor for advisory memory management
- GlobalPriorityScheduler is used by higher-level orchestration for distributed task execution
- PhaseController informs lifecycle timing and promotion gates

```mermaid
graph LR
SL["SprintLifecycleManager"] --> SS["SprintScheduler"]
ACQ["AcquisitionStrategy"] --> SS
BUS["SidecarBus"] --> SS
EXP["Exporters"] --> SS
GS["GlobalPriorityScheduler"] --> SS
PC["PhaseController"] --> SL
WE["WindupEngine"] --> SS
```

**Diagram sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [sprint_lifecycle.py](file://runtime/sprint_lifecycle.py)
- [global_scheduler.py](file://orchestrator/global_scheduler.py)
- [phase_controller.py](file://orchestrator/phase_controller.py)
- [windup_engine.py](file://runtime/windup_engine.py)

## Performance Considerations
- Concurrency limits:
  - max_parallel_sources controls concurrent source fetches
  - aggressive_mode enables concurrent branch execution with per-branch timeouts
  - branch_timeout_budget_s and aggressive_branch_timeout_s bound per-branch costs
- Memory governance:
  - Resource governor advisory for thermal and memory states
  - Peak RSS tracking and mission budget violations
- Budget enforcement:
  - Feed dominance budget per source to prevent over-reliance on single sources
  - Nonfeed budget telemetry for diagnostics
- Prefetch and adaptive timeouts:
  - Speculative prefetch every 15s
  - Adaptive timeout EMA per source with bounds
- Export cadence:
  - Partial exports every N findings in aggressive mode

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- Early exits:
  - stop_on_first_accepted triggers immediate return after first accepted finding
  - hard_deadline_exceeded forces termination when wall-clock exceeds sprint duration
  - max_cycles reached triggers finalization with mandatory nonfeed terminalization
- Windup gating:
  - pre-windup barrier ensures required nonfeed lanes reach terminal state before windup
  - windup_guard telemetry helps diagnose callback execution and reasons
- Branch timeouts:
  - branch_timeout_count increments when public/CT branches exceed timeout budget
  - aggressive_mode uses shorter per-branch timeouts to maintain responsiveness
- Memory pressure:
  - peak_rss_gib and budget_violations indicate mission budget breaches
  - sidecars_skipped indicates heavy sidecars disabled under RAM pressure
- CT pipeline losses:
  - ct_loss_stage and ct_bridge_invocation telemetry help locate where raw CT evidence is lost
- Dedup and preload:
  - dedup_preload_count and dedup_preload_elapsed_s show cross-sprint dedup initialization cost

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)

## Conclusion
The Sprint Scheduler integrates tier-aware feed scheduling, acquisition strategy enforcement, and lifecycle-driven timing to deliver robust, bounded research sprints. Its design emphasizes deterministic phase transitions, strict budgeting, and comprehensive diagnostics for performance tuning and recovery. By leveraging priority-based distribution, adaptive timeouts, and nonfeed gating, it maintains reliability under varying resource conditions while supporting export-driven closure and teardown.
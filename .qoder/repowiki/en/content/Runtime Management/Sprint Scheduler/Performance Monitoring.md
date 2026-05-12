# Performance Monitoring

<cite>
**Referenced Files in This Document**
- [telemetry.py](file://runtime/telemetry.py)
- [metrics_registry.py](file://metrics_registry.py)
- [sprint_dashboard.py](file://monitoring/sprint_dashboard.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [nonfeed_candidate_ledger.py](file://runtime/nonfeed_candidate_ledger.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [resource_governor.py](file://runtime/resource_governor.py)
- [performance_coordinator.py](file://coordinators/performance_coordinator.py)
- [bench_gc_314_runtime.py](file://tools/bench_gc_314_runtime.py)
- [research_quality_score.py](file://tools/research_quality_score.py)
- [monitoring_coordinator.py](file://coordinators/monitoring_coordinator.py)
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
10. [Appendices](#appendices)

## Introduction
This document describes the Performance Monitoring system that powers observability, diagnostics, and optimization across the Hledac agent ecosystem. It covers:
- GC telemetry collection and memory statistics tracking
- Performance metrics gathering and reporting
- Public stage computation and terminal stage derivation
- Diagnostic reporting, stage counters, rejection ledgers, and quality tracking
- Examples of setup, metric interpretation, troubleshooting, memory pressure detection, timeout handling, and optimization strategies

## Project Structure
The Performance Monitoring system spans runtime telemetry, metrics registries, dashboards, schedulers, ledgers, and governors. The following diagram maps major components and their relationships.

```mermaid
graph TB
subgraph "Runtime"
T["TelemetryLogger<br/>SprintMetrics"]
MR["MetricsRegistry"]
RG["M1ResourceGovernor"]
MPB["MemoryPressureBroker"]
SC["SprintScheduler"]
NCL["NonfeedCandidateLedger"]
end
subgraph "Tooling"
BENCH["bench_gc_314_runtime.py"]
MQS["research_quality_score.py"]
MC["monitoring_coordinator.py"]
end
subgraph "UI"
SD["SprintDashboard"]
end
T --> MR
SC --> NCL
SC --> MPB
SC --> RG
SD --> SC
BENCH --> T
MQS --> SC
MC --> MR
```

**Diagram sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)
- [memory_pressure_broker.py:79-323](file://orchestrator/memory_pressure_broker.py#L79-L323)
- [resource_governor.py:116-353](file://runtime/resource_governor.py#L116-L353)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [bench_gc_314_runtime.py:38-158](file://tools/bench_gc_314_runtime.py#L38-L158)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)
- [monitoring_coordinator.py:468-509](file://coordinators/monitoring_coordinator.py#L468-L509)

**Section sources**
- [telemetry.py:1-370](file://runtime/telemetry.py#L1-L370)
- [metrics_registry.py:1-388](file://metrics_registry.py#L1-L388)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [nonfeed_candidate_ledger.py:1-398](file://runtime/nonfeed_candidate_ledger.py#L1-L398)
- [memory_pressure_broker.py:1-323](file://orchestrator/memory_pressure_broker.py#L1-L323)
- [resource_governor.py:1-353](file://runtime/resource_governor.py#L1-L353)
- [sprint_dashboard.py:1-269](file://monitoring/sprint_dashboard.py#L1-L269)
- [bench_gc_314_runtime.py:38-158](file://tools/bench_gc_314_runtime.py#L38-L158)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)
- [monitoring_coordinator.py:468-509](file://coordinators/monitoring_coordinator.py#L468-L509)

## Core Components
- Runtime Telemetry: Structured, fail-soft logging of phase transitions and events with JSON emission and bounded history.
- Metrics Registry: Lightweight, bounded counters/gauges with periodic flush to disk and ring-buffer snapshots.
- Sprint Scheduler: Orchestrates public and CT pipelines, computes public stage and terminal stage, aggregates counters and rejections.
- Nonfeed Candidate Ledger: Bounded in-memory ledger for candidate lifecycle events across families (PUBLIC, CT, PIVOT, etc.).
- Resource Governor: Advisory safety layer enforcing concurrency and admission rules under memory pressure.
- Memory Pressure Broker: Polling-based memory pressure detection with callback hooks and admission states.
- Dashboard: Live terminal view of phases, findings, cycle progress, branch health, and governor state.
- Tooling: Benchmarks for GC and memory snapshots, quality scoring, and monitoring coordinator for performance benchmarks.

**Section sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)
- [resource_governor.py:116-353](file://runtime/resource_governor.py#L116-L353)
- [memory_pressure_broker.py:79-323](file://orchestrator/memory_pressure_broker.py#L79-L323)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [bench_gc_314_runtime.py:38-158](file://tools/bench_gc_314_runtime.py#L38-L158)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)
- [monitoring_coordinator.py:468-509](file://coordinators/monitoring_coordinator.py#L468-L509)

## Architecture Overview
The system integrates telemetry and metrics with scheduler-driven pipelines and diagnostic ledgers. The sequence below shows how telemetry and metrics are emitted and consumed.

```mermaid
sequenceDiagram
participant Sched as "SprintScheduler"
participant TL as "TelemetryLogger"
participant SM as "SprintMetrics"
participant MR as "MetricsRegistry"
participant SD as "SprintDashboard"
Sched->>SM : record_phase()/record_transition()/record_event()
SM->>TL : log_event()/log_phase_transition()
TL-->>Sched : emit structured JSON log
Sched->>MR : ingest_sprint_event(event)
MR-->>Sched : ring-buffer snapshot
SD->>Sched : update(result, phase, elapsed)
SD-->>SD : render dashboard
```

**Diagram sources**
- [telemetry.py:153-370](file://runtime/telemetry.py#L153-L370)
- [metrics_registry.py:337-357](file://metrics_registry.py#L337-L357)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [sprint_dashboard.py:109-137](file://monitoring/sprint_dashboard.py#L109-L137)

## Detailed Component Analysis

### Runtime Telemetry and Metrics Collection
- TelemetryLogger: Fail-soft JSON formatter emitting structured logs with session_id, phase, component, event, elapsed_ms, and timestamp. Maintains bounded event history.
- SprintMetrics: Wrapper around TelemetryLogger to record phase enter/transition, named events, start, and finalize with total elapsed time.
- MetricsRegistry: Bounded counters/gauges with periodic flush to disk JSONL, ring-buffer snapshots, and ingestion of sprint events from TelemetryLogger.

```mermaid
classDiagram
class TelemetryLogger {
+log_phase_transition(from_phase, to_phase, component, elapsed_ms) void
+log_event(phase, component, event, elapsed_ms) void
+log_sprint_finalize(final_phase, component, total_elapsed_ms) void
+get_events() dict[]
}
class SprintMetrics {
+start() void
+record_phase(phase, component) void
+record_transition(from_phase, to_phase, component) void
+record_event(phase, component, event) void
+finalize(final_phase) void
+get_telemetry_events() dict[]
}
class MetricsRegistry {
+inc(name, delta) void
+set_gauge(name, value) void
+tick() void
+flush(force) void
+ingest_sprint_event(event) void
+get_summary() dict
+close() void
}
SprintMetrics --> TelemetryLogger : "uses"
MetricsRegistry --> TelemetryLogger : "ingests events"
```

**Diagram sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)

**Section sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)

### Public Stage Computation and Terminal Stage Derivation
- Public stage machine defines deterministic stages from bootstrap to terminal, including timeouts and zero-success conditions.
- Terminal stage is derived from the public outcome, emitting timeouts and outcomes for diagnostics.

```mermaid
flowchart TD
Start(["Start PUBLIC branch"]) --> Bootstrap["Bootstrap attempted / zero success"]
Bootstrap --> |Zero candidates & timeout| ZeroCandidates["BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT"]
Bootstrap --> |Attempted| Discovery["Discovery attempted / zero results / timeout / error"]
Discovery --> Fetch["Fetch attempted / zero success / timeout / error"]
Fetch --> Parse["Parse attempted / zero text"]
Parse --> Quality["Quality rejected"]
Quality --> Storage["Storage rejected"]
Storage --> Accept["Accepted"]
Accept --> Terminal["TERMINAL"]
Discovery --> |Timeout| DiscTimeout["DISCOVERY_TIMEOUT"]
Fetch --> |Timeout| FetchTimeout["FETCH_TIMEOUT"]
DiscTimeout --> Terminal
FetchTimeout --> Terminal
ZeroCandidates --> Terminal
```

**Diagram sources**
- [sprint_scheduler.py:163-189](file://runtime/sprint_scheduler.py#L163-L189)
- [sprint_scheduler.py:191-4873](file://runtime/sprint_scheduler.py#L191-L4873)

**Section sources**
- [sprint_scheduler.py:163-189](file://runtime/sprint_scheduler.py#L163-L189)
- [sprint_scheduler.py:191-4873](file://runtime/sprint_scheduler.py#L191-L4873)

### Diagnostic Reporting, Stage Counters, and Rejection Ledgers
- Stage counters: Public acceptance attempted/accepted/rejected, cycle counts, dedup counts, and source telemetry.
- Rejection ledgers: Quality, duplicate, and low-information rejections summarized by family.
- Nonfeed Candidate Ledger: Bounded evidence ledger capturing lifecycle events across families with bounded samples.

```mermaid
classDiagram
class NonfeedCandidateLedger {
+add(family, stage, candidate_id, source, reason, ...) void
+add_ct_quarantine(domain, reject_reason, ...) void
+add_public_event(stage, candidate_id, reason, ...) void
+add_pivot_discovered(...) void
+add_quality_rejection(...) void
+add_provider_failed(...) void
+records() tuple~LedgerRecord~
+summary() dict
}
class LedgerRecord {
+family : string
+stage : string
+candidate_id : string
+source : string
+reason : string
+accepted : bool
+quarantine : bool
+stale : bool
+sample_url : string
+sample_value : string
+ts_monotonic : float
}
NonfeedCandidateLedger --> LedgerRecord : "stores"
```

**Diagram sources**
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)

**Section sources**
- [sprint_scheduler.py:4123-11087](file://runtime/sprint_scheduler.py#L4123-L11087)
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)

### Memory Pressure Detection and Timeout Handling
- MemoryPressureBroker: Polling-based detection using psutil and vm_stat; triggers callbacks for WARN/CRITICAL/NORMAL with admission states and budget throttle factors.
- ResourceGovernor: Advisory safety layer evaluating UMA state, model load status, and fetch limits; sidecar admission checks with mission budget constraints.

```mermaid
sequenceDiagram
participant MPB as "MemoryPressureBroker"
participant RG as "M1ResourceGovernor"
participant Sched as "SprintScheduler"
MPB->>MPB : check() polls system
alt WARN
MPB->>MPB : throttle_factor=0.5, admission=THROTTLED
MPB-->>Sched : on_warn() callback
else CRITICAL
MPB->>MPB : throttle_factor=0.25, admission=EMERGENCY_CLEANUP_REQUESTED
MPB-->>Sched : on_critical() callback
else NORMAL
MPB->>MPB : reset throttle, admission=NORMAL
MPB-->>Sched : on_normal() callback
end
Sched->>RG : evaluate()
RG-->>Sched : GovernorDecision(fetch_limit, allow_renderer, allow_model_load, branch_concurrency)
```

**Diagram sources**
- [memory_pressure_broker.py:223-291](file://orchestrator/memory_pressure_broker.py#L223-L291)
- [resource_governor.py:137-217](file://runtime/resource_governor.py#L137-L217)
- [sprint_scheduler.py:4848-4873](file://runtime/sprint_scheduler.py#L4848-L4873)

**Section sources**
- [memory_pressure_broker.py:79-323](file://orchestrator/memory_pressure_broker.py#L79-L323)
- [resource_governor.py:116-353](file://runtime/resource_governor.py#L116-L353)
- [sprint_scheduler.py:4848-4873](file://runtime/sprint_scheduler.py#L4848-L4873)

### GC Telemetry and Memory Statistics Tracking
- Benchmarks collect GC snapshots (thresholds, counts, stats), memory snapshots (RSS, swap, virtual memory), and compute deltas for collections and peaks.
- These measurements support performance profiling and memory pressure diagnostics.

```mermaid
flowchart TD
Start(["Start benchmark"]) --> GCInit["Collect GC before snapshot"]
GCInit --> MemBefore["Collect memory before snapshot"]
Work["Execute workload"] --> GCAfter["Collect GC after snapshot"]
GCAfter --> MemAfter["Collect memory after snapshot"]
MemAfter --> Delta["Compute GC collections delta<br/>and memory peak"]
Delta --> Report["Report wall-clock, deltas, and errors"]
```

**Diagram sources**
- [bench_gc_314_runtime.py:61-158](file://tools/bench_gc_314_runtime.py#L61-L158)

**Section sources**
- [bench_gc_314_runtime.py:61-158](file://tools/bench_gc_314_runtime.py#L61-L158)

### Performance Optimization Coordinator
- Agent pooling, load balancing, and async execution optimization with memory-awareness and circuit-breaker logic.
- Periodic optimization cycles identify bottlenecks (high memory, slow agents, circuit breakers) and apply targeted fixes.

```mermaid
classDiagram
class AgentPool {
+initialize() void
+shutdown() void
+get_agent(agent_name, agent_factory) AsyncContext
+get_metrics() dict
+get_pool_stats() dict
}
class IntelligentLoadBalancer {
+select_agent(available_agents, strategy, metrics) string
+record_execution(agent_name) void
}
class AsyncExecutionOptimizer {
+execute_with_limits(agent_name, timeout) AsyncContext
+get_active_tasks_count() int
+get_execution_stats(agent_name) dict
}
class AgentPerformanceOptimizer {
+initialize() void
+shutdown() void
+execute_agent(agent_name, factory, query, ...) AsyncContext
+select_best_agent(available_agents, query, metrics) string
+optimize_performance() OptimizationReport
}
AgentPerformanceOptimizer --> AgentPool : "uses"
AgentPerformanceOptimizer --> IntelligentLoadBalancer : "uses"
AgentPerformanceOptimizer --> AsyncExecutionOptimizer : "uses"
```

**Diagram sources**
- [performance_coordinator.py:116-800](file://coordinators/performance_coordinator.py#L116-L800)

**Section sources**
- [performance_coordinator.py:116-800](file://coordinators/performance_coordinator.py#L116-L800)

### Live Dashboard and Quality Scoring
- SprintDashboard provides a live terminal view of phases, findings, cycle progress, branch status, and governor state.
- research_quality_score extracts normalized metrics including acceptance counts, branch mix, and swap indicators for scoring.

**Section sources**
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)

## Dependency Analysis
The following diagram highlights key dependencies among components.

```mermaid
graph TB
TL["TelemetryLogger"] --> MR["MetricsRegistry"]
SM["SprintMetrics"] --> TL
SC["SprintScheduler"] --> NCL["NonfeedCandidateLedger"]
SC --> MPB["MemoryPressureBroker"]
SC --> RG["M1ResourceGovernor"]
SD["SprintDashboard"] --> SC
BENCH["bench_gc_314_runtime.py"] --> TL
MQS["research_quality_score.py"] --> SC
PC["performance_coordinator.py"] --> MR
```

**Diagram sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)
- [memory_pressure_broker.py:79-323](file://orchestrator/memory_pressure_broker.py#L79-L323)
- [resource_governor.py:116-353](file://runtime/resource_governor.py#L116-L353)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [bench_gc_314_runtime.py:38-158](file://tools/bench_gc_314_runtime.py#L38-L158)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)
- [performance_coordinator.py:116-800](file://coordinators/performance_coordinator.py#L116-L800)

**Section sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:86-388](file://metrics_registry.py#L86-L388)
- [sprint_scheduler.py:163-4873](file://runtime/sprint_scheduler.py#L163-L4873)
- [nonfeed_candidate_ledger.py:130-398](file://runtime/nonfeed_candidate_ledger.py#L130-L398)
- [memory_pressure_broker.py:79-323](file://orchestrator/memory_pressure_broker.py#L79-L323)
- [resource_governor.py:116-353](file://runtime/resource_governor.py#L116-L353)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [bench_gc_314_runtime.py:38-158](file://tools/bench_gc_314_runtime.py#L38-L158)
- [research_quality_score.py:318-469](file://tools/research_quality_score.py#L318-L469)
- [performance_coordinator.py:116-800](file://coordinators/performance_coordinator.py#L116-L800)

## Performance Considerations
- Telemetry and metrics are fail-soft and bounded to avoid overhead and preserve runtime stability.
- Memory pressure detection uses polling to remain portable and robust; callbacks must avoid heavy work.
- Resource Governor applies advisory limits to fetch concurrency and renderer/model admission under UMA warnings/emergency states.
- Benchmarks provide controlled GC and memory sampling for profiling without disrupting production.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- High memory usage: Trigger emergency cleanup in agent pools; reduce concurrency; monitor swap and governor snapshots.
- Memory pressure callbacks failing: Ensure callbacks are lightweight; heavy work should be scheduled asynchronously.
- Circuit breaker open: Inspect recent execution stats; consider resetting after cooldown; avoid stacking model loads.
- PUBLIC branch timeouts: Review timeouts and zero-candidate conditions; check dominant branch blockers and error messages.
- Metrics flush failures: Degraded mode is indicated by registry summary; investigate filesystem permissions and disk availability.

**Section sources**
- [performance_coordinator.py:303-321](file://coordinators/performance_coordinator.py#L303-L321)
- [memory_pressure_broker.py:269-291](file://orchestrator/memory_pressure_broker.py#L269-L291)
- [resource_governor.py:137-217](file://runtime/resource_governor.py#L137-L217)
- [metrics_registry.py:312-336](file://metrics_registry.py#L312-L336)
- [sprint_scheduler.py:4848-4873](file://runtime/sprint_scheduler.py#L4848-L4873)

## Conclusion
The Performance Monitoring system combines structured telemetry, bounded metrics, scheduler-driven diagnostics, and adaptive resource governance to sustain performance under memory constraints. Together with ledgers and dashboards, it enables actionable insights, timely interventions, and continuous optimization across the agent ecosystem.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Setup Examples
- Enable telemetry and metrics:
  - Initialize TelemetryLogger and SprintMetrics for a session.
  - Use MetricsRegistry to periodically flush counters/gauges to disk.
- Monitor live sprints:
  - Instantiate SprintDashboard and call start/update/finish during scheduling.
- Track GC and memory:
  - Use bench_gc_314_runtime.py to collect GC snapshots and memory deltas.
- Benchmark performance:
  - Use monitoring_coordinator.py to run CPU/memory/general benchmarks and inspect throughput.

**Section sources**
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [metrics_registry.py:251-311](file://metrics_registry.py#L251-L311)
- [sprint_dashboard.py:96-137](file://monitoring/sprint_dashboard.py#L96-L137)
- [bench_gc_314_runtime.py:139-158](file://tools/bench_gc_314_runtime.py#L139-L158)
- [monitoring_coordinator.py:468-509](file://coordinators/monitoring_coordinator.py#L468-L509)

### Metric Interpretation
- Telemetry events: Use session_id, phase, component, event, elapsed_ms to trace timing and ownership.
- MetricsRegistry: Inspect counters and gauges for RAM/VMS/FDS; confirm flush cadence and persistence status.
- Public stage counters: Track acceptance attempts, accepted, and rejected counts; timeouts and blockers.
- Rejection summaries: Group by family to identify dominant reasons (quality, duplicates, low information).
- Nonfeed ledger: Review stage and family distributions; examine bounded samples for diagnosis.

**Section sources**
- [telemetry.py:37-70](file://runtime/telemetry.py#L37-L70)
- [metrics_registry.py:312-336](file://metrics_registry.py#L312-L336)
- [sprint_scheduler.py:4123-11087](file://runtime/sprint_scheduler.py#L4123-L11087)
- [nonfeed_candidate_ledger.py:335-378](file://runtime/nonfeed_candidate_ledger.py#L335-L378)

### Optimization Strategies
- Reduce concurrency under memory pressure; throttle budgets; suspend low-priority work.
- Apply governor decisions to fetch limits and admission controls; monitor denied counts.
- Use agent pooling and load balancing to stabilize latency and utilization.
- Periodically optimize memory by clearing pools and forcing garbage collection.
- Benchmark and profile to identify hotspots; adjust timeouts and circuit breaker thresholds.

**Section sources**
- [memory_pressure_broker.py:242-267](file://orchestrator/memory_pressure_broker.py#L242-L267)
- [resource_governor.py:137-217](file://runtime/resource_governor.py#L137-L217)
- [performance_coordinator.py:674-722](file://coordinators/performance_coordinator.py#L674-L722)
- [bench_gc_314_runtime.py:154-158](file://tools/bench_gc_314_runtime.py#L154-L158)